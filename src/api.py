"""T5 — api.py : API REST FastAPI (§7 PLAN.md).

Endpoints (§7.2) :
- POST /v1/stations/search {q, limit}  → autocomplete (gares, communes, bus)
  — GET conservé pour les clients historiques
- POST /v1/journeys                  → itinéraires TER (moteur McRAPTOR T3)
- GET /v1/health                    → état

Contrats d'erreur (§7.3) : gare introuvable → 404 STATION_NOT_FOUND (avec
suggestions), date/heure/paramètres invalides → 400, aucun trajet → 200 avec
`{ journeys: [] }`.

Démarrage :  uvicorn src.api:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeout
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Body
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Scope, Receive, Send

from src.graph import (
    MAX_GARES_PER_SIDE,
    approx_distance_km,
    Graph,
    ALIASES,
    normalize,
)
from src.raptor import RaptorEngine, _iso as _iso_min, _vehicle_label
from src import gtfs_rt, tictactrip, trainline, trainline_cards
from src.pricing import PricingEngine
from src.pow import PoWEngine
from src.crypto import CryptoEngine

DEFAULT_GRAPH = Path(__file__).resolve().parents[1] / "data" / "graph.bin"
WEB_DIR = Path(__file__).resolve().parents[1] / "web"

# Route d'une ouverture/départ de tous les jours de l'application.
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_COORDS_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")

_engine: RaptorEngine | None = None
_poller: gtfs_rt.RealtimePoller | None = None
_pricing: PricingEngine | None = None
_tt: tictactrip.TictactripClient | None = None
_pow = PoWEngine()
_crypto = CryptoEngine()
_raptor_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="raptor")
RAPTOR_TIMEOUT_S = 35


def _load_place_groups() -> dict:
    """Config des groupes « toutes gares » (§5.5) ; [] si fichier absent."""
    path = Path(__file__).resolve().parents[1] / "config" / "place_groups.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def get_engine(graph_path: Path = DEFAULT_GRAPH) -> RaptorEngine:
    """Charge le graphe une seule fois (module-level), puis le partage."""
    global _engine, _poller, _pricing
    if _engine is None:
        _engine = RaptorEngine(Graph.load(graph_path))
        _pricing = PricingEngine(_engine.graph)
        # Compute route_region for each route (used for region-based filtering)
        g = _engine.graph
        for ridx in range(len(g.routes)):
            if ridx in g.route_region:
                continue
            trips = g.trips_by_route[ridx]
            if not trips:
                g.route_region[ridx] = "INCONNUE"
                continue
            t = g.trips[trips[0]]
            if t.stop_times:
                g.route_region[ridx] = _pricing.stop_region(t.stop_times[0].stop)
            else:
                g.route_region[ridx] = "INCONNUE"
        # T8 — temps réel GTFS-RT en arrière-plan ; l'échec de démarrage ne
        # bloque pas l'API (le poller retentera au prochain intervalle).
        try:
            _poller = gtfs_rt.RealtimePoller(
                _engine.graph, extra_trip_urls=[gtfs_rt.LIO_URL]
            )
            _poller.start()
        except Exception:
            _poller = None
    return _engine


from contextlib import asynccontextmanager

@asynccontextmanager
async def _lifespan(app: FastAPI):
    import sys, time as _t
    _t0 = _t.monotonic()
    eng = get_engine()
    g = eng.graph
    # Pre-warm RAPTOR views cache for all vehicle types
    # Pick a central station (Paris) to trigger _views() cache population
    paris_ids = g.resolve_place("Paris")
    if paris_ids:
        for v in ("bus_train", "train_only", "all"):
            eng._views(20260825, v)
    _t1 = _t.monotonic()
    print("[startup] Graph + views preloaded in %.1fs (%d stops, %d trips)" % (
        _t1 - _t0, len(g.stops), len(g.trips)), file=sys.stderr)
    yield
    # Arrêt propre du poller GTFS-RT à la fermeture (uvicorn --reload).
    global _poller
    if _poller is not None:
        _poller.stop()
        _poller = None


# ---------------------------------------------------------------- helpers
def _error(status: int, code: str, message: str, suggestions: list[str] | None = None) -> HTTPException:
    return HTTPException(
        status_code=status, detail={"error": {"code": code, "message": message, "suggestions": suggestions or []}}
    )


def _parse_date(value: str, g: Graph) -> _dt.date:
    try:
        d = _dt.date.fromisoformat(value)
    except ValueError:
        raise _error(400, "INVALID_DATE", f"date invalide : {value!r} (attendu YYYY-MM-DD)")
    ymd = int(d.strftime("%Y%m%d"))
    if not (g.date_min <= ymd <= g.date_max):
        raise _error(
            400,
            "INVALID_DATE",
            f"date hors plage de données : {value} (couverture {g.date_min} → {g.date_max})",
        )
    return d


def _parse_time(value: str) -> int:
    m = _HHMM_RE.match(value)
    if not m:
        raise _error(400, "INVALID_TIME", f"heure invalide : {value!r} (attendu HH:MM)")
    return int(m.group(1)) * 60 + int(m.group(2))


def _nearest_stops(
    g: Graph,
    lat: float,
    lon: float,
    min_km: float = 0.0,
    max_km: float = 0.0,
    n: int = MAX_GARES_PER_SIDE,
) -> list[int]:
    """Gares les plus proches de coordonnées « lat,lon » (distance réelle
    corrigée cos(latitude), cf. graph.approx_distance_km). Mêmes conventions
    que Graph.nearest_gares : max_km == 0 → la gare la plus proche seule ;
    max_km > 0 → intervalle [min_km, max_km] en km réels, plafonné à n."""
    scored = []
    for i, s in enumerate(g.stops):
        if s.id.startswith("BusStop:"):
            continue
        scored.append((approx_distance_km(lat, lon, s.lat, s.lon), i))
    scored.sort(key=lambda it: it[0])
    if max_km > 0:
        scored = [(d, i) for d, i in scored if min_km <= d <= max_km]
    else:
        n = 1
    return [i for _, i in scored[:n]]


GPS_FAIRNESS_KM = 5.0
"""Fenêtre « équité » autour de la gare la plus proche pour une résolution
GPS sans rayon explicite : on retient aussi les gares jusqu'à GPS_FAIRNESS_KM
km AU-DELÀ de la plus proche (plafonné à MAX_GARES_PER_SIDE).

Motivation : la gare la plus proche à vol d'oiseau n'est pas forcément la mieux
desservie (ex. Mesves-sur-Loire : Pouilly-sur-Loire, 12 trains/jour et 1 seul
direct Paris, à 6,7 km ; La Charité-sur-Loire, 22 trains/jour dont 12 directs,
à 10 km). En laissant concourir les gares voisines, RAPTOR propose le meilleur
itinéraire réel — comportement SNCF Connect."""


def _nearest_stops_window(g: Graph, lat: float, lon: float) -> list[int]:
    """Gare la plus proche + gares dans la fenêtre d'équité (cf.
    GPS_FAIRNESS_KM), plafonné à MAX_GARES_PER_SIDE."""
    scored = []
    for i, s in enumerate(g.stops):
        if s.id.startswith("BusStop:"):
            continue
        scored.append((approx_distance_km(lat, lon, s.lat, s.lon), i))
    scored.sort(key=lambda it: it[0])
    window = scored[0][0] + GPS_FAIRNESS_KM
    return [i for d, i in scored if d <= window][:MAX_GARES_PER_SIDE]


def _fmt_km(km: float) -> str:
    """« 7.9 » → « 7,9 » ; « 12.0 » → « 12 » (messages utilisateur)."""
    s = f"{km:.1f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def _no_gare_in_radius(rmin: float, rmax: float, label: str) -> HTTPException:
    return _error(
        404,
        "NO_GARE_IN_RADIUS",
        f"Aucune gare entre {_fmt_km(rmin)} et {_fmt_km(rmax)} km de {label}. "
        "Élargissez le rayon de recherche.",
    )


def _resolve_place(
    g: Graph,
    value: str,
    radius_min_km: float = 0.0,
    radius_max_km: float = 0.0,
) -> tuple[list[int], dict | None]:
    """Résout un lieu → (gares, contexte de provenance).

    Le contexte (`ctx`) est renseigné quand le lieu a été résolu
    GÉOGRAPHIQUEMENT (commune ou coordonnées GPS) : il sert aux notes
    « Départ/Arrivée à X — N km de Y » et au message d'intervalle vide.
    None pour une résolution par nom exact / groupe (rien à préciser).

    Le rayon ne s'applique qu'à ces résolutions géographiques : une requête
    qui désigne exactement une gare n'est jamais filtrée.
    """
    value = value.strip()
    if value in g.stop_index:
        return [g.stop_index[value]], None
    if value.startswith("OCE") and f"StopArea:{value}" in g.stop_index:
        return [g.stop_index[f"StopArea:{value}"]], None
    m = _COORDS_RE.match(value)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if radius_max_km > 0:
            idxs = _nearest_stops(g, lat, lon, min_km=radius_min_km, max_km=radius_max_km)
            if not idxs:
                raise _no_gare_in_radius(radius_min_km, radius_max_km, "votre position")
        else:
            idxs = _nearest_stops_window(g, lat, lon)
        return idxs, {"kind": "gps", "label": "votre position", "lat": lat, "lon": lon}
    if value.startswith("place_group:"):
        key = value.removeprefix("place_group:")
        if key in g.place_groups:
            return list(g.place_groups[key]), None
    # Priorité §5.5 complète (alias/groupe → gares exactes → commune → bus →
    # autocomplete) ; seul un chemin géographique (commune) renvoie un ctx.
    idxs, ctx = g.resolve_place_ctx(value, radius_min_km, radius_max_km)
    if not idxs:
        if ctx is not None and radius_max_km > 0:
            raise _no_gare_in_radius(radius_min_km, radius_max_km, ctx["label"])
        suggestions = [name for _, name in g.find_stops(value)[:3]]
        raise _error(
            404,
            "STATION_NOT_FOUND",
            f"gare ou lieu introuvable : {value!r}",
            suggestions=suggestions,
        )
    return idxs, ctx


def _stop_aliases(g: Graph, stop_idx: int) -> list[str]:
    """Alias d'usage qui désignent cette gare (miroir du tableau ALIASES)."""
    name = g.stops[stop_idx].name
    norm = _normalize(name)
    out = []
    for alias, targets in ALIASES.items():
        if norm in (_normalize(t) for t in targets):
            out.append(alias)
    return sorted(out)


def _normalize(text: str) -> str:
    import unicodedata

    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return " ".join(text.lower().replace("-", " ").split())


def _bare(stop_area_id: str) -> str:
    """§6.5/§7.2 : l'API expose les ids sans le préfixe interne « StopArea: »."""
    return stop_area_id.removeprefix("StopArea:")


def _bare_journey(j: dict) -> dict:
    for leg in j["legs"]:
        leg["from"]["stop_area_id"] = _bare(leg["from"]["stop_area_id"])
        leg["to"]["stop_area_id"] = _bare(leg["to"]["stop_area_id"])
    return j


_ARTICLE_PREFIXES = {"saint", "sainte", "st", "ste", "le", "la", "les", "sur", "sous", "grand", "petit"}


def _city_prefix(name: str) -> str:
    tokens = _normalize(name).split()
    if not tokens:
        return ""
    if len(tokens) >= 2 and tokens[0] in _ARTICLE_PREFIXES:
        return f"{tokens[0]} {tokens[1]}"
    return tokens[0]


def _is_same_city(name1: str, name2: str) -> bool:
    c1 = _city_prefix(name1)
    c2 = _city_prefix(name2)
    return bool(c1 and c2 and c1 == c2)


# T8 — une correspondance est « risquée » si le train entrant est en retard et
# que la marge réelle jusqu'au départ suivant (marche comprise) reste faible.
CONNECTION_SLACK_MIN = 5  # minutes


def _connection_risks(jd: dict) -> list[dict]:
    """Correspondances à risque sur un trajet temps réel (T8).

    Une correspondance manquée devient possible quand le leg entrant est en
    retard (`delay_min > 0`) et que l'écart réel au départ du leg suivant est
    trop court pour absorber une aggravation du retard. On signale la gare,
    les lignes concernées, le retard et la marge réelle restante."""
    rail = [l for l in jd["legs"] if l["type"] != "walk"]
    risks = []
    for a, b in zip(rail, rail[1:]):
        if a["delay_min"] <= 0:
            continue
        margin = _minutes(b["from"]["time"]) - _minutes(a["to"]["time"])
        # Le retard a déjà consommé une partie de la marge planifiée : si la
        # marge restante (au-delà du retard) est inférieure au seuil, une
        # aggravation fait rater la correspondance.
        if margin - a["delay_min"] < CONNECTION_SLACK_MIN:
            risks.append(
                {
                    "at_station": b["from"]["name"],
                    "from_line": a["line"],
                    "to_line": b["line"],
                    "delay_min": a["delay_min"],
                    "margin_min": margin,
                }
            )
    return risks


def _minutes(iso: str) -> int:
    """ISO « YYYY-MM-DDTHH:MM:SS+hh:mm » → minutes depuis l'époque (legs de nuit
    et changements de jour gérés naturellement par le timestamp absolu)."""
    return int(_dt.datetime.fromisoformat(iso).timestamp()) // 60


# ---------------------------------------------------------------- application
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="PlanTER API",
    description="Recherche d'itinéraires 100% TER (moteur McRAPTOR).",
    version="0.1.0",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request, exc: StarletteHTTPException) -> JSONResponse:
    # §7.3 : le corps d'erreur est exactement `{ error: {...} }` (sans wrapper
    # FastAPI `{"detail": ...}`).
    return JSONResponse(status_code=exc.status_code, content=exc.detail)


@app.get("/v1/health", tags=["santé"])
def health() -> dict:
    g = get_engine().graph
    return {
        "status": "ok",
        "data_date": f"{g.date_max // 10000:04d}-{g.date_max % 10000 // 100:02d}-{g.date_max % 100:02d}",
        "coverage_start": f"{g.date_min // 10000:04d}-{g.date_min % 10000 // 100:02d}-{g.date_min % 100:02d}",
        "coverage_end": f"{g.date_max // 10000:04d}-{g.date_max % 10000 // 100:02d}-{g.date_max % 100:02d}",
        "stations": len(g.stops),
        "last_refresh": _last_refresh(),
        "realtime": _realtime_health(),
    }


@app.get("/v1/challenge", tags=["anti-abus"])
def challenge() -> dict:
    """Proof-of-work : défi aléatoire (TTL 60s, RAM only)."""
    return _pow.generate_challenge()


@app.get("/v1/crypto/pubkey", tags=["crypto"])
def crypto_pubkey() -> dict:
    """Clé publique RSA pour le chiffrement des requêtes (hybride AES-GCM + RSA-OAEP)."""
    return {"public_key": _crypto.pubkey_pem()}


def _realtime_health() -> dict | None:
    """T8 — état des flux GTFS-RT : fraîcheur (âge en s), nombre de trips
    retardés/supprimés, alertes, horodatages. None si le poller n'est pas actif."""
    global _poller
    if _poller is None:
        return None
    feed = _poller.snapshot()
    alerts = _poller.alerts_snapshot()
    return {
        "polling": feed.fetched_at > 0,
        "age_s": feed.age_s(),
        "fresh": feed.is_fresh,
        "delayed_trips": len(feed.trip_delays),
        "cancelled_trips": len(feed.cancelled),
        "gtfs_rt_timestamp": _dt.datetime.fromtimestamp(feed.updated_at, tz=_dt.timezone.utc).isoformat()
        if feed.updated_at
        else None,
        "alerts": {
            "count": len(alerts.alerts),
            "fresh": 0 <= alerts.age_s() <= 6 * 60 if alerts.fetched_at else False,
            "age_s": alerts.age_s(),
        },
    }


def _last_refresh() -> dict | None:
    """Dernier refresh hebdomadaire (scripts/refresh_data.sh) : statut, date,
    couverture — None si jamais exécuté. Journal lisible par l'humain dans
    reports/refresh.log."""
    f = Path(__file__).resolve().parents[1] / "data" / "refresh_status.json"
    try:
        return json.loads(f.read_text())
    except (FileNotFoundError, ValueError):
        return None


def _stations_search_impl(q: str, limit: int) -> dict:
    """Logique partagée GET/POST de l'autocomplete. Réponse en 4 blocs :
    place_groups (« toutes gares »), stations (gares), communes (villes sans
    gare, résolues offline), bus_stops. Les gares passent avant les arrêts
    bus (tri dans Graph.find_stops)."""
    g = get_engine().graph
    hits = g.find_stops(q)[:limit]
    qn = normalize(q)
    groups = []
    for key, spec in _load_place_groups().items():
        aliases = spec.get("aliases", [])
        if qn and (qn == normalize(key) or any(qn in normalize(a) for a in aliases)):
            groups.append(
                {
                    "kind": "place_group",
                    "place_group": key,
                    "name": spec.get("label", key),
                    "station_count": len(g.place_groups.get(key, [])),
                    "stop_area_ids": [_bare(g.stops[i].id) for i in g.place_groups.get(key, [])],
                }
            )
    gares: list[dict] = []
    bus_stops: list[dict] = []
    for idx, _name in hits:
        stop = g.stops[idx]
        item = {
            "stop_area_id": _bare(stop.id),
            "name": stop.name,
            "lat": stop.lat,
            "lon": stop.lon,
        }
        if stop.id.startswith("BusStop:"):
            bus_stops.append(item)
        else:
            item["aliases"] = _stop_aliases(g, idx)
            item["trainline_slug"] = trainline.slug_for(stop.id)
            gares.append(item)
    return {
        "stations": gares,
        "communes": g.find_communes(q, limit=min(5, limit)),
        "bus_stops": bus_stops,
        "place_groups": groups,
    }


@app.get("/v1/stations/search", tags=["gares"])
def stations_search(
    q: str = Query(..., description="Texte de recherche (nom, alias, partie de nom)"),
    limit: int = Query(10, ge=1, le=50),
) -> dict:
    """Autocomplete en GET — conservé pour les clients historiques (app
    Android embarquée). Le client web utilise POST (sans trace de log)."""
    return _stations_search_impl(q, limit)


class SearchRequest(BaseModel):
    """Body du POST /v1/stations/search (le client web n'envoie plus la
    requête en GET : ni log nginx, ni cache navigateur)."""

    q: str = Field(..., min_length=1, description="Texte de recherche")
    limit: int = Field(10, ge=1, le=50)


@app.post("/v1/stations/search", tags=["gares"])
def stations_search_post(body: SearchRequest) -> dict:
    return _stations_search_impl(body.q, body.limit)


@app.get("/v1/cards", tags=["itinéraires"])
def cards_list() -> dict:
    """T11 — cartes de réduction TER (Trainline, displayGroup=sncf_regional).

    T12 — chaque carte est enrichie de sa région d'application et de son taux
    de réduction estimé (`pay` = fraction du plein tarif payée, `discount_pct`
    = % affiché ; None pour abonnements/pass sans réduction par billet)."""
    get_engine()  # garantit `_pricing` chargé (carte -> région/taux)
    cards = []
    for c in trainline_cards.cards():
        info = _pricing.card_info(c) if _pricing is not None else {}
        c = dict(c)
        c["region"] = info.get("region", "INCONNUE")
        pay = info.get("pay")
        c["pay"] = pay
        c["discount_pct"] = round((1 - pay) * 100) if pay is not None else None
        cards.append(c)
    return {"cards": cards}


# T12bis — prix réels : envoi du trajet (gares + date) à un serveur tiers
# (Tictactrip) qui peut journaliser la requête. Ce disclaimer est exposé à
# l'utilisateur quand l'option « prix réels » est activée.
REAL_PRICES_DISCLAIMER = (
    "Votre recherche (gares de départ et d'arrivée, date) est envoyée au "
    "service Tictactrip, un serveur tiers qui peut journaliser votre requête. "
    "Les prix affichés sont les prix réellement vendus (promotions comprises), "
    "ils ne sont pas calculés localement."
)


def _get_tictactrip() -> tictactrip.TictactripClient | None:
    """Client Tictactrip partagé (créé une seule fois, échec silencieux)."""
    global _tt
    if _tt is None:
        try:
            _tt = tictactrip.TictactripClient()
        except Exception:
            _tt = None
    return _tt


def _real_prices_for(journey, d: _dt.date, tt) -> dict:
    """Prix réels d'un trajet, leg par leg (legs ferroviaires uniquement).

    Retourne `{"real_price_eur", "real_price_min_eur", "real_price_max_eur",
    "legs": {leg_idx: {"line", "from", "to", "min_eur", "max_eur", "day_eur",
    "day_company", "ok"}}}`. `day_eur` est le meilleur prix du jour UNIQUEMENT
    si le trip du jour est un TER (priceCalendar peut sinon renvoyer un OUIGO/TGV
    moins cher, hors périmètre) ; `day_company` indique alors la compagnie.
    Si un leg n'a pas de prix Tictactrip (ville introuvable, 429…),
    `ok` est False et les totaux agrègent uniquement les legs résolus ; si
    AUCUN leg n'est résolu, tous les totaux sont None.
    """
    total_min = total_max = total_day = 0.0
    n_ok = 0
    any_min = any_max = any_day = True
    legs_out: dict[int, dict] = {}
    for li, leg in enumerate(journey.legs):
        if leg.type == "walk":
            continue
        leg_date = _iso_min(d, leg.from_time)[:10]
        try:
            p = tt.leg_prices(leg.from_name, leg.to_name, leg_date)
        except tictactrip.TictactripError:
            p = None
        if not p:
            legs_out[li] = {"line": leg.line, "from": leg.from_name, "to": leg.to_name,
                            "min_eur": None, "max_eur": None, "day_eur": None, "day_company": None, "ok": False}
            continue
        fmin = tt.fare_eur(p["min"])
        fmax = tt.fare_eur(p["max"])
        fday = tt.fare_eur(p["day"])
        legs_out[li] = {"line": leg.line, "from": leg.from_name, "to": leg.to_name,
                        "min_eur": fmin, "max_eur": fmax, "day_eur": fday,
                        "day_company": p.get("day_company"), "ok": True}
        if fday is not None:
            total_day += fday
        else:
            any_day = False
        if fmin is not None:
            total_min += fmin
        else:
            any_min = False
        if fmax is not None:
            total_max += fmax
        else:
            any_max = False
        n_ok += 1
    if n_ok == 0:
        return {"real_price_eur": None, "real_price_min_eur": None,
                "real_price_max_eur": None, "legs": legs_out}
    return {
        "real_price_eur": round(total_day, 2) if any_day else None,
        "real_price_min_eur": round(total_min, 2) if any_min else None,
        "real_price_max_eur": round(total_max, 2) if any_max else None,
        "legs": legs_out,
    }


@app.post("/v1/journeys", tags=["itinéraires"])
def journeys(
    request: Request,
    body: dict = Body(..., description='{"payload": "base64 du chiffré hybride AES-GCM + RSA-OAEP"}'),
) -> dict:
    # PoW : vérification proof-of-work (anti-abus, sans logs)
    if _pow.enabled:
        pow_salt = request.headers.get("x-pow-salt", "")
        pow_nonce = request.headers.get("x-pow-nonce", "")
        pow_diff = request.headers.get("x-pow-difficulty", "")
        if not pow_salt or not pow_nonce or not pow_diff:
            raise _error(403, "POW_REQUIRED", "Proof-of-work requis. Récupérez un défi via /v1/challenge.")
        try:
            diff = int(pow_diff)
        except ValueError:
            raise _error(403, "POW_INVALID", "Difficulty invalide.")
        if not _pow.verify(pow_salt, pow_nonce, diff):
            raise _error(403, "POW_INVALID", "Solution proof-of-work invalide ou expirée.")

    # Déchiffrement du payload
    payload_b64 = body.get("payload")
    if not payload_b64:
        raise _error(400, "MISSING_PAYLOAD", "Champ 'payload' requis (base64 chiffré).")
    try:
        params = _crypto.decrypt_b64(payload_b64)
    except Exception:
        raise _error(400, "INVALID_PAYLOAD", "Payload chiffré invalide ou corrompu.")

    # Extraction des paramètres
    from_ = params.get("from", "").strip()
    to = params.get("to", "").strip()
    date = params.get("date", "").strip()
    time = params.get("time", "").strip()
    if not from_ or not to or not date or not time:
        raise _error(400, "MISSING_PARAMS", "Champs 'from', 'to', 'date', 'time' requis dans le payload.")

    datetime_represents = params.get("datetime_represents", "departure")
    if datetime_represents not in ("departure", "arrival"):
        datetime_represents = "departure"
    max_transfers = params.get("max_transfers", 6)
    if not isinstance(max_transfers, int) or max_transfers < 0 or max_transfers > 6:
        raise _error(400, "INVALID_PARAM", "max_transfers doit être entre 0 et 6.")
    vehicle = params.get("vehicle", "train_only")
    if vehicle not in ("all", "train_only", "bus", "bus_train"):
        raise _error(400, "INVALID_PARAM", "vehicle doit être 'all', 'train_only', 'bus' ou 'bus_train'.")
    count = params.get("count", 5)
    if not isinstance(count, int) or count < 1 or count > 20:
        raise _error(400, "INVALID_PARAM", "count doit être entre 1 et 20.")
    sort = params.get("sort", "transfers")
    if params.get("prioritize_fewer_transfers"):
        sort = "transfers"
    if sort not in ("departure", "duration", "transfers"):
        raise _error(400, "INVALID_PARAM", "sort doit être 'departure', 'duration' ou 'transfers'.")
    use_realtime = params.get("use_realtime", True)
    if not isinstance(use_realtime, bool):
        use_realtime = True
    cards = params.get("cards", "")
    if not isinstance(cards, str):
        cards = ""
    real_prices = params.get("real_prices", False)
    if not isinstance(real_prices, bool):
        real_prices = False
    expand_nearby = params.get("expand_nearby", False)
    if not isinstance(expand_nearby, bool):
        expand_nearby = False

    # Rayon utilisateur (double curseur) pour les résolutions géographiques
    # (commune / GPS) : gares retenues dans [min, max] km réels, plafonnées
    # à MAX_GARES_PER_SIDE par côté. Défaut [0, 0] = la gare la plus proche,
    # sans limite de distance (comportement prévisible, jamais d'erreur).
    def _radius(val) -> float | None:
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            return None
        return min(max(float(val), 0.0), 100.0)

    r_min = _radius(params.get("radius_min_km", 0))
    r_max = _radius(params.get("radius_max_km", 0))
    if r_min is None or r_max is None:
        raise _error(400, "INVALID_PARAM", "radius_min_km / radius_max_km doivent être numériques.")
    if r_min > r_max:
        r_min, r_max = r_max, r_min

    engine = get_engine()
    g = engine.graph
    d = _parse_date(date, g)
    t0 = _parse_time(time)

    origins_raw, origin_ctx = _resolve_place(g, from_, r_min, r_max)
    dests_raw, dest_ctx = _resolve_place(g, to, r_min, r_max)
    req_origin_ids = {g.stops[i].id for i in origins_raw}
    req_dest_ids = {g.stops[i].id for i in dests_raw}

    # Expand origins with nearby stops (< 200m) so RAPTOR can board directly.
    # For destinations: expand within 5km, but by default ONLY within the same
    # city/commune (unless expand_nearby is True, which allows neighboring stations).
    def _expand_stops(stops: list[int], radius_m: float, allow_other_cities: bool) -> list[int]:
        expanded = set(stops)
        for idx in list(stops):
            orig_name = g.stops[idx].name
            for nearby_idx in g.stops_nearby(idx, radius_m):
                nearby_name = g.stops[nearby_idx].name
                # Toujours autoriser si < 200m (arrêt en face / pôle multimodal)
                if approx_distance_km(
                    g.stops[idx].lat, g.stops[idx].lon,
                    g.stops[nearby_idx].lat, g.stops[nearby_idx].lon,
                ) <= 0.2:
                    expanded.add(nearby_idx)
                elif allow_other_cities or _is_same_city(orig_name, nearby_name):
                    expanded.add(nearby_idx)
        return list(expanded)

    origins = _expand_stops(origins_raw, 200.0, allow_other_cities=False)
    dests = _expand_stops(dests_raw, 5000.0, allow_other_cities=expand_nearby)

    # T8 — le moteur consomme un instantané du poller (graphe et temps réel
    # sont partagés en lecture seule via snapshot()). Instantané vide => pas
    # de replanification RT (fast-path), mais `connection_risks` reste exposé
    # (schéma stable pour les clients) via `rt_requested`.
    realtime = None
    rt_requested = False
    if use_realtime and _poller is not None:
        rt_requested = True
        rt_snap = _poller.snapshot()
        if rt_snap.cancelled or rt_snap.trip_delays:
            realtime = rt_snap

    # Route proximity filter: only include routes with stops near origin/dest.
    # Uses geographic distance (5km) + 1-hop expansion via shared stops.
    region_filter = None
    if vehicle in ("bus", "bus_train"):
        # Collect all origin/dest coordinates
        search_stops = set(origins + dests)
        latlons = [(g.stops[idx].lat, g.stops[idx].lon) for idx in search_stops]
        # Find routes with at least one stop within 5km of any origin/dest
        relevant_routes: set[int] = set()
        for stop_idx, route_ids in enumerate(g.routes_by_stop):
            if not route_ids:
                continue
            s = g.stops[stop_idx]
            for slat, slon in latlons:
                dlat = s.lat - slat
                dlon = s.lon - slon
                if abs(dlat) < 0.05 and abs(dlon) < 0.075:
                    km = approx_distance_km(slat, slon, s.lat, s.lon)
                    if km < 5.0:
                        relevant_routes.update(route_ids)
                        break
        # 1-hop expansion: add routes that share a stop with any filtered route.
        # This captures intermediate routes (ex: C2 Nevers→Clermont connecting
        # Paris to bus 283) without blowing up the filter.
        if relevant_routes:
            hop1: set[int] = set()
            for stop_idx, route_ids in enumerate(g.routes_by_stop):
                if route_ids and any(r in relevant_routes for r in route_ids):
                    hop1.update(route_ids)
            relevant_routes.update(hop1)
        if relevant_routes:
            region_filter = relevant_routes

    def _run_raptor():
        if datetime_represents == "arrival":
            return engine.arrive_by_wide(int(d.strftime("%Y%m%d")), origins, dests, t0, max_transfers, vehicle, realtime, region_filter=region_filter)
        else:
            return engine.depart_after_wide(int(d.strftime("%Y%m%d")), origins, dests, t0, max_transfers, vehicle, realtime, region_filter=region_filter)

    future = _raptor_pool.submit(_run_raptor)
    try:
        journeys = future.result(timeout=RAPTOR_TIMEOUT_S)
    except _FutureTimeout:
        future.cancel()
        raise _error(503, "TIMEOUT", "Le calcul d'itinéraire a pris trop de temps. Réessayez avec des critères plus simples.")

    # Pareto dominance filter: remove journeys dominated by another.
    # A is dominated by B when B departs >= as late AND arrives <= as early
    # AND has <= transfers.  Keeps only non-dominated alternatives.
    if journeys:
        journeys.sort(key=lambda j: (-j.departure, j.arrival, j.transfers))
        filtered: list = []
        for j in journeys:
            if not any(
                d.departure >= j.departure
                and d.arrival <= j.arrival
                and d.transfers <= j.transfers
                and (d.departure, d.arrival, d.transfers) != (j.departure, j.arrival, j.transfers)
                for d in filtered
            ):
                filtered.append(j)
        journeys = filtered

    # Tri : §8.2 — par défaut le moins de correspondances d'abord (même trajet
    # plus long) ; sinon par départ ou par durée (« le plus court de la
    # journée » : le moteur couvre un horizon de 36 h, le plus court figure
    # donc parmi les solutions Pareto si on trie par durée avant la troncature
    # à `count`).
    if sort == "transfers":
        journeys = sorted(journeys, key=lambda j: (j.transfers, j.departure, j.arrival))[:count]
    elif sort == "duration":
        journeys = sorted(journeys, key=lambda j: (j.duration_min, j.departure, j.arrival))[:count]
    else:
        journeys = sorted(journeys, key=lambda j: (j.departure, j.arrival))[:count]

    # T9 — liens Trainline PAR BILLET (PoC monétisation). Les billets TER se
    # vendent séparément par segment : une URL de réservation est générée pour
    # chaque leg ferroviaire (train/car/tram), avec sa propre date et heure de
    # départ (les legs peuvent passer minuit). Les marches inter-gares n'ont
    # pas de billet.
    def _iso(leg):
        """(date, heure) d'un leg à partir de son `from.time` ISO."""
        t = (leg.get("from") or {}).get("time") or ""
        return t[:10], (t[11:16] or None)

    out = []
    # T8 — alertes Service Alerts : pertinentes si une gare ou un train du trajet
    # est touché. On expose au plus 3 (les alertes générales restent dans health).
    alerts_feed = _poller.alerts_snapshot() if (_poller is not None) else None
    # T11 — cartes de réduction TER demandées (ids Trainline, validés).
    card_ids = trainline_cards.valid_ids([c for c in cards.split(",") if c])
    for j in journeys:
        jd = _bare_journey(j.to_json(d))
        # T12 — prix estimés (modèle v1, calibré sur prix observés) : le prix
        # n'est jamais nul et reste une ESTIMATION clairement signalée. Les
        # cartes de réduction (param `cards`) réduisent `price_reduced_eur`.
        price_info = _pricing.journey_price(j, cards=card_ids, date=d) if _pricing is not None else None
        if price_info is not None:
            jd["price_normal_eur"] = price_info.pop("price_normal_eur")
            jd["price_reduced_eur"] = price_info.pop("price_reduced_eur")
            jd["pricing"] = price_info
            # §32 — découpage intra-train : ids nus, horodatage ISO et lien de
            # réservation par segment (avec la meilleure carte de sa région).
            split = price_info.get("split")
            if split:
                for seg in split["segments"]:
                    seg["from"]["stop_area_id"] = _bare(seg["from"]["stop_area_id"])
                    seg["to"]["stop_area_id"] = _bare(seg["to"]["stop_area_id"])
                    seg["from"]["time"] = _iso_min(d, seg.pop("departure_min"))
                    seg["to"]["time"] = _iso_min(d, seg.pop("arrival_min"))
        # T8 — correspondances à risque (retard réel menaçant la jonction) ;
        # clé présente (liste vide possible) dès que le RT est demandé
        if rt_requested:
            jd["connection_risks"] = _connection_risks(jd) if realtime is not None else []
        # T8 — perturbations du trajet (Service Alerts).
        if alerts_feed is not None:
            jd["alerts"] = [_alert_json(a) for a in _journey_alerts(alerts_feed, j, g)[:3]]
        # T12bis — prix réels (option) : prix réellement vendus leg par leg
        # (Tictactrip, promos comprises). La requête est envoyée à un serveur
        # tiers (disclaimer exposé ci-dessous). Échec = repli sur l'estimation
        # locale sans faire planter la réponse.
        if real_prices:
            tt = _get_tictactrip()
            if tt is not None:
                try:
                    rp = _real_prices_for(j, d, tt)
                except Exception:
                    rp = None
                if rp:
                    jd["real_price_eur"] = rp["real_price_eur"]
                    jd["real_price_min_eur"] = rp["real_price_min_eur"]
                    jd["real_price_max_eur"] = rp["real_price_max_eur"]
                    for li, info in rp["legs"].items():
                        jd["legs"][li]["real_price"] = {
                            k: info[k] for k in ("line", "from", "to", "min_eur", "max_eur", "day_eur", "day_company", "ok")
                        }
        # Notes de transparence : la résolution géographique (commune/GPS),
        # l'extension « gares voisines » et les groupes multi-gares peuvent
        # faire partir/arriver ailleurs que le lieu tapé. On l'affiche avec la
        # distance réelle (corrigée cos(latitude)) plutôt que de deviner via
        # un préfixe de nom (_is_same_city confondait « Saint-Jean-de-Luz » et
        # « Saint-Jean-de-Bassel », ~900 km).
        rail_legs = [l for l in j.legs if l.type != "walk"]
        if origin_ctx and rail_legs:
            idx = g.stop_index.get(rail_legs[0].from_id)
            if idx is not None:
                km = approx_distance_km(
                    origin_ctx["lat"], origin_ctx["lon"], g.stops[idx].lat, g.stops[idx].lon
                )
                jd["origin_note"] = (
                    f"Départ de {rail_legs[0].from_name} — {_fmt_km(km)} km de {origin_ctx['label']}"
                )
        if j.legs:
            last_leg = j.legs[-1]
            arr_idx = g.stop_index.get(last_leg.to_id)
            if arr_idx is not None:
                if dest_ctx is not None:
                    km = approx_distance_km(
                        dest_ctx["lat"], dest_ctx["lon"], g.stops[arr_idx].lat, g.stops[arr_idx].lon
                    )
                    jd["destination_note"] = (
                        f"Arrivée à {last_leg.to_name} — {_fmt_km(km)} km de {dest_ctx['label']}"
                    )
                elif last_leg.to_id not in req_dest_ids:
                    jd["destination_note"] = f"Arrivée à {last_leg.to_name} (gare voisine)"
                elif dests_raw and last_leg.to_id != g.stops[dests_raw[0]].id:
                    # Membre « secondaire » d'une résolution multi-gares
                    # (ex. groupe besancon → F-C TGV à 8,4 km de Viotte).
                    km = approx_distance_km(
                        g.stops[dests_raw[0]].lat,
                        g.stops[dests_raw[0]].lon,
                        g.stops[arr_idx].lat,
                        g.stops[arr_idx].lon,
                    )
                    if km > 2.0:
                        to_label = to.removeprefix("place_group:")
                        jd["destination_note"] = (
                            f"Arrivée à {last_leg.to_name} — {_fmt_km(km)} km de {to_label}"
                        )
        out.append(jd)
    resp = {"journeys": out}
    if real_prices:
        resp["real_prices"] = {
            "provider": "tictactrip",
            "disclaimer": REAL_PRICES_DISCLAIMER,
        }
        for jd in out:
            jd["real_prices_disclaimer"] = REAL_PRICES_DISCLAIMER
    return resp


def _journey_alerts(alerts, j, g) -> list:
    """Alertes pertinentes d'un trajet : gares des legs (marche exclue) et
    numéros de train des legs ferroviaires."""
    stop_idxs: list[int] = []
    train_numbers: list[str] = []
    for leg in j.legs:
        if leg.type == "walk":
            continue
        idx = g.stop_index.get(leg.from_id)
        if idx is not None:
            stop_idxs.append(idx)
        idx = g.stop_index.get(leg.to_id)
        if idx is not None:
            stop_idxs.append(idx)
        m = gtfs_rt._TRAIN_NO_RE.match(leg.trip_id)
        if m:
            train_numbers.append(m.group(1) or m.group(2))
    return alerts.relevant(stop_idxs, train_numbers)


def _alert_json(a) -> dict:
    return a.to_json()


@app.get("/v1/trips/{trip_id:path}/schedule", tags=["horaires"])
def trip_schedule(
    trip_id: str,
    date: str | None = Query(None, description="Date au format YYYY-MM-DD"),
) -> dict:
    engine = get_engine()
    g = engine.graph
    trip_idx = g.trip_index.get(trip_id)
    if trip_idx is None:
        raise _error(404, "TRIP_NOT_FOUND", f"Trip '{trip_id}' introuvable.")

    ref_trip = g.trips[trip_idx]
    route = g.routes[ref_trip.route]

    d = _parse_date(date, g) if date else datetime.now().date()
    date_int = int(d.strftime("%Y%m%d"))

    def _same_dir(t1, t2) -> bool:
        stops1 = [st.stop for st in t1.stop_times]
        pos1 = {s: i for i, s in enumerate(stops1)}
        common = [pos1[st.stop] for st in t2.stop_times if st.stop in pos1]
        if len(common) >= 2:
            return all(common[i] < common[i + 1] for i in range(len(common) - 1))
        return (t1.stop_times[-1].stop == t2.stop_times[-1].stop or
                t1.stop_times[0].stop == t2.stop_times[0].stop)

    matching_trips = []
    for ti, t in enumerate(g.trips):
        if t.route == ref_trip.route:
            services = g.service_dates.get(t.service_id, frozenset())
            if date_int in services and _same_dir(ref_trip, t):
                matching_trips.append(t)

    matching_trips.sort(key=lambda t: t.stop_times[0].dep)

    trips_out = []
    for t in matching_trips:
        st0 = t.stop_times[0]
        st_end = t.stop_times[-1]
        dep_h, dep_m = divmod(st0.dep, 60)
        arr_h, arr_m = divmod(st_end.arr, 60)
        stops_list = []
        for st in t.stop_times:
            s_obj = g.stops[st.stop]
            s_arr_h, s_arr_m = divmod(st.arr, 60)
            s_dep_h, s_dep_m = divmod(st.dep, 60)
            stops_list.append({
                "stop_id": _bare(s_obj.id),
                "name": s_obj.name,
                "arrival_time": f"{s_arr_h % 24:02d}:{s_arr_m:02d}",
                "departure_time": f"{s_dep_h % 24:02d}:{s_dep_m:02d}",
            })
        trips_out.append({
            "trip_id": t.id,
            "vehicle_label": _vehicle_label(t.id),
            "departure_time": f"{dep_h % 24:02d}:{dep_m:02d}",
            "arrival_time": f"{arr_h % 24:02d}:{arr_m:02d}",
            "origin_name": g.stops[st0.stop].name,
            "destination_name": g.stops[st_end.stop].name,
            "stops": stops_list,
        })

    last_stop_name = g.stops[ref_trip.stop_times[-1].stop].name
    return {
        "route_id": route.id,
        "line": route.short_name,
        "line_name": route.long_name,
        "type": ref_trip.vehicle,
        "vehicle_label": _vehicle_label(ref_trip.id),
        "current_trip_id": ref_trip.id,
        "date": d.isoformat(),
        "direction_name": f"Direction {last_stop_name}",
        "trips": trips_out,
    }


class _ShellStaticFiles(StaticFiles):
    """Fichiers du shell (html/css/js/sw) : pas de cache navigateur, pour que
    les correctifs (ex. date par défaut) soient pris dès le prochain rechargement
    sans rejouer une ancienne version depuis le cache heuristique."""
    _NO_CACHE = {"/", "/app.js", "/sw.js", "/index.html"}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope["path"]
        if path in self._NO_CACHE:
            async def _send(message: dict) -> None:
                if message["type"] == "http.response.start":
                    headers = [h for h in message.get("headers", []) if h[0].lower() != b"cache-control"]
                    headers.append((b"cache-control", b"no-cache"))
                    message["headers"] = headers
                await send(message)
            await super().__call__(scope, receive, _send)
        else:
            await super().__call__(scope, receive, send)


# SPA T6 (§8) : servie par l'API elle-même — un seul point d'entrée, pas de CORS.
# Les routes /v1/* étant déclarées avant, elles restent prioritaires.
app.mount("/", _ShellStaticFiles(directory=WEB_DIR, html=True), name="web")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
