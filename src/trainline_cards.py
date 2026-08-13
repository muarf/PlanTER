"""T11 — Cartes de réduction TER (Trainline).

Liste des cartes TER régionales (`displayGroup=sncf_regional`) extraites de
l'API publique `GET /api/discount-cards` de Trainline (headers `x-app-version`,
`x-client-name`, ...). Chaque carte est identifiée par son hash Trainline (id
40-hex, ex. `2a730e22…` pour la carte BFC solidaire), envoyé en query param
`passengerDiscountCards[]` dans l'URL de réservation.

Voir `walkthrough.md` §T11. Les données sont statiques (config/), rafraîchies
à la main quand Trainline ajoute/modifie une carte.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
DEFAULT_JSON = CONFIG_DIR / "trainline_cards.json"

# DOB par défaut du passager si une carte est sélectionnée (33 ans avant
# aujourd'hui, cf. §T11 — Trainline exige `passengers[]=DOB|pid-0`).
DEFAULT_PASSENGER_DOB = "1993-08-12"

# Pid Trainline généré par le front (1er passager).
_PID = "pid-0"

# Les cartes TER de l'API Trainline sont identifiées par un hash 40-hex.
# Les cartes sociales absentes du catalogue Trainline (SolidariO', illico
# SOLIDAIRE…) portent un id local non hexadécimal : le calcul de réduction
# reste possible, mais elles ne peuvent pas être pré-remplies dans l'URL de
# réservation (Trainline ne les connaît pas et ignore de toute façon les
# `passengerDiscountCards[]` d'une URL, cf. walkthrough.md §T11).
_TRAINLINE_HASH = re.compile(r"^[0-9a-f]{40}$")


@lru_cache(maxsize=1)
def _cards_json(path: Path = DEFAULT_JSON) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def cards() -> list[dict]:
    """Toutes les cartes TER (id, name, shortName, ageRange optionnel)."""
    return _cards_json()


def card_by_id(card_id: str) -> dict | None:
    """Carte TER par son hash Trainline, ou None si inconnue."""
    for c in _cards_json():
        if c["id"] == card_id:
            return c
    return None


def valid_ids(card_ids: list[str]) -> list[str]:
    """Filtre les ids : ne garde que les cartes TER connues, sans doublons."""
    known = {c["id"] for c in _cards_json()}
    seen: set[str] = set()
    out: list[str] = []
    for cid in card_ids:
        if cid in known and cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


def booking_url(base_url: str, card_ids: list[str], passenger_dob: str | None = None) -> str:
    """Ajoute carte(s) de réduction et passager à une URL de réservation
    Trainline (l'URL d'un leg ou d'un trajet total, déjà pré-remplie).

    `passengers[]=<DOB>|pid-0` est requis dès qu'une carte est sélectionnée
    (Trainline calcule l'âge). Sans DOB explicite, on utilise la DOB par défaut.
    Sans carte, l'URL est retournée inchangée. Seules les cartes portant un
    hash Trainline réel (id 40-hex) sont ajoutées à l'URL ; les cartes locales
    sans hash Trainline (SolidariO', illico SOLIDAIRE…) n'y figurent pas.
    """
    hash_ids = [cid for cid in card_ids if _TRAINLINE_HASH.match(cid)]
    if not hash_ids:
        return base_url
    dob = passenger_dob or DEFAULT_PASSENGER_DOB
    sep = "&" if "?" in base_url else "?"
    parts = [base_url]
    for cid in hash_ids:
        parts.append(f"passengerDiscountCards[]={cid}")
    parts.append(f"passengers[]={dob}|{_PID}")
    return sep.join(parts)
