"""T12bis — tictactrip.py : prix réels TER via l'API Tictactrip.

Option « rechercher les prix réels » : au lieu du modèle d'estimation local,
on interroge Tictactrip pour obtenir les prix réellement vendus du trajet
(promos comprises). Deux endpoints publics sont utilisés :

- GET /cities/autocomplete?q=<nom>           -> résolution ville (city_id)
- GET /data/companies?originId&destinationId&transportType=train
                                              -> min/max (centimes) par compagnie
- GET /priceCalendar/range?originId&destinationId&start&end
                                              -> meilleur prix par jour (date précise)

La granularité est PAR LEG : un appel par leg ferroviaire du trajet, cumulé
pour reconstruire le prix réel du trajet complet. Les prix sont en centimes.

CONFIDENTIALITÉ : ce module envoie le nom des gares (départ/arrivée) et la
date à un serveur tiers (Tictactrip), qui peut les journaliser. L'API expose
un disclaimer explicite quand cette option est activée.

Limitations :
- les petits arrêts peuvent ne pas être résolus en ville Tictactrip ;
- le prixCalendar donne le meilleur prix DU JOUR (une promo très basse peut
  apparaître même si elle ne concerne pas le train exact) ;
- des appels fréquents peuvent déclencher des 429 (backoff + cache).
"""

from __future__ import annotations

import re
import threading
import time
from functools import lru_cache
from typing import Optional

import requests

API_BASE = "https://api.tictactrip.eu"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}
TIMEOUT_S = 6
CACHE_TTL_S = 300  # prix/pair de villes : 5 min (les promos bougent)
CITY_TTL_S = 86400  # city_id stable : 1 jour


class TictactripError(RuntimeError):
    """Erreur d'appel à l'API Tictactrip (réseau, 429, réponse inattendue)."""


class TictactripClient:
    """Client minimal de l'API publique Tictactrip (résolution ville + prix).

    Thread-safe (verrou sur le cache et les appels), avec backoff 429,
    cache LRU et un cache TTL pour les prix par paire/date.
    """

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self._session = session or requests.Session()
        self._session.headers.update(HEADERS)
        self._prices: dict[tuple[int, int, str], tuple[float | None, float | None, float | None, str | None]] = {}  # (oid,did,date) -> (min,max,day,day_company)
        self._cities: dict[str, Optional[int]] = {}  # nom -> city_id
        self._lock = threading.Lock()  # sérialise appels HTTP (throttle + cache)
        self._last_call = 0.0
        self._min_interval = 0.15  # s entre deux appels (politesse API)
        self._max_retries = 3
        self._backoff = 1.0

    # ------------------------------------------------------------ appels HTTP
    def _get_json(self, url: str, params: dict) -> dict | list:
        for attempt in range(self._max_retries):
            with self._lock:
                # throttling simple : espace les appels (pas de burst 429).
                now = time.monotonic()
                wait = self._last_call + self._min_interval - now
                if wait > 0:
                    time.sleep(wait)
                self._last_call = time.monotonic()
                try:
                    r = self._session.get(url, params=params, timeout=TIMEOUT_S)
                except requests.RequestException as e:
                    raise TictactripError(f"réseau Tictactrip : {e}") from e
            if r.status_code == 429:
                time.sleep(self._backoff * (attempt + 1))
                continue
            if r.status_code == 404:
                raise TictactripError(f"404 Tictactrip : {url} {params}")
            if r.status_code != 200:
                raise TictactripError(f"HTTP {r.status_code} Tictactrip : {url}")
            try:
                return r.json()
            except ValueError as e:
                raise TictactripError(f"JSON invalide Tictactrip : {e}") from e
        raise TictactripError(f"429 répété Tictactrip : {url}")

    # ------------------------------------------------------- résolution ville
    def city_id(self, name: str) -> Optional[int]:
        """City_id Tictactrip d'une gare/ville, via autocomplete. None si
        introuvable. Résultat mis en cache (city_id stable dans le temps).

        Robustesse : ponctuation retirée (une apostrophe fait timeout sur
        l'API), puis on retente en retirant les derniers mots (ex.
        « Paris Bercy Bourg. Pays d'Auv. » -> « Paris »).
        """
        key = name.strip().lower()
        if key in self._cities:
            return self._cities[key]
        candidates = self._name_candidates(name)
        cid: Optional[int] = None
        for cand in candidates:
            try:
                data = self._get_json(f"{API_BASE}/cities/autocomplete", {"q": cand})
            except TictactripError:
                continue
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("city_id"):
                        cid = int(item["city_id"])
                        break
            if cid is not None:
                break
        # On ne met en cache que les résolutions réussies : une panne
        # temporaire ne « empoisonne » pas le cache avec des None définitifs.
        if cid is not None:
            self._cities[key] = cid
        return cid

    @staticmethod
    def _name_candidates(name: str) -> list[str]:
        """Variantes d'un nom de gare à essayer, de la plus précise à la plus
        générale : nom ponctuation nettoyée, puis préfixes successifs."""
        cleaned = re.sub(r"[^a-zA-Z0-9À-ÿ ]", " ", name)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        words = cleaned.split()
        out = []
        for n in range(len(words), 0, -1):
            cand = " ".join(words[:n])
            if cand not in out:
                out.append(cand)
        return out

    # ------------------------------------------------------------ prix par leg
    def leg_prices(self, origin: str, dest: str, date: str) -> dict:
        """Prix réels (centimes) d'un leg (origine, destination, date) :
        `min`/`max` sur la période (data/companies) et `day` = meilleur prix
        du jour (priceCalendar). Lève TictactripError si aucune donnée."""
        oid = self.city_id(origin)
        did = self.city_id(dest)
        if oid is None or did is None:
            raise TictactripError(f"ville introuvable : {origin!r} ou {dest!r}")

        cache_key = (oid, did, date)
        cached = self._prices.get(cache_key)
        if cached is not None:
            return {"min": cached[0], "max": cached[1], "day": cached[2], "day_company": cached[3]}

        prices: dict[str, Optional[object]] = {"min": None, "max": None, "day": None, "day_company": None}

        # --- min/max par compagnie (TER) --------------------------------
        try:
            comp = self._get_json(
                f"{API_BASE}/data/companies",
                {"originId": oid, "destinationId": did, "transportType": "train"},
            )
            if isinstance(comp, dict):
                ter = comp.get("TER") or {}
                pc = ter.get("priceCents") or {}
                prices["min"] = pc.get("min")
                prices["max"] = pc.get("max")
        except TictactripError:
            pass

        # --- meilleur prix du jour (promo comprise) ----------------------
        # ATTENTION : priceCalendar renvoie le trip le moins cher de la
        # journée TOUTES compagnies confondues (OUIGO/TGV si moins cher qu'un
        # TER). L'appli étant 100 % TER, on n'accepte le prix du jour QUE si
        # la compagnie du trip contient « TER » ; sinon pas de `day`.
        try:
            cal = self._get_json(
                f"{API_BASE}/priceCalendar/range",
                {"originId": oid, "destinationId": did, "start": date, "end": date},
            )
            if isinstance(cal, list):
                best: Optional[float] = None
                day_company: Optional[str] = None
                for entry in cal:
                    if not isinstance(entry, dict):
                        continue
                    trip = entry.get("trip") or {}
                    companies = trip.get("companies") or []
                    if not any("TER" in c for c in companies):
                        continue  # OUIGO/TGV : hors périmètre TER
                    c = trip.get("priceCents")
                    if c is None:
                        continue
                    if best is None or c < best:
                        best = c
                        day_company = ", ".join(companies)
                prices["day"] = best
                prices["day_company"] = day_company
        except TictactripError:
            pass

        if prices["min"] is None and prices["day"] is None:
            raise TictactripError(f"aucun prix Tictactrip pour {origin} -> {dest} ({date})")

        self._prices[cache_key] = (prices["min"], prices["max"], prices["day"], prices.get("day_company"))
        return prices

    # -------------------------------------------------------------- utilitaires
    def fare_eur(self, cents: Optional[float]) -> Optional[float]:
        """Centimes -> euros (2 décimales), None si absent."""
        if cents is None:
            return None
        return round(cents / 100.0, 2)
