"""T9 — Liens Trainline (monétisation v1, PoC).

Cartographie `uic8` (stop_area_id « OCE<uic> ») -> **id numérique Trainline**
(colonne `id` de `public/stations.csv`, repo `trainline-eu/stations-studio`).
L'URL de réservation pré-remplie est
`/book/results?journeySearchType=single&origin=urn:trainline:generic:loc:{id}&…`
Le slug (ex. « dijon-ville ») n'est PAS accepté par le moteur de réservation
(« no journeys available ») : seul l'id URN l'est (validé en HTTP réel).
"""
from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
DEFAULT_CSV = CONFIG_DIR / "trainline_stations.csv"
BOOKING_BASE = "https://www.thetrainline.com/book/results"

# Préfixe interne des stop_area dans le graphe : « StopArea:OCE87686006 ».
_OCE = "OCE"


@lru_cache(maxsize=1)
def _uic_to_loc(csv_path: Path = DEFAULT_CSV) -> dict[str, str]:
    """uic8 (str) -> id numérique Trainline (ex. « 3616 » pour St-Vit). Privilégie la gare principale."""
    best: dict[str, str] = {}
    with csv_path.open(newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            uic = (row.get("uic8_sncf") or "").strip()
            loc = (row.get("id") or "").strip()
            if not uic or not loc:
                continue
            if uic not in best or row.get("is_main_station") == "t":
                best[uic] = loc
    return best


@lru_cache(maxsize=1)
def _uic_to_slug(csv_path: Path = DEFAULT_CSV) -> dict[str, str]:
    """uic8 (str) -> slug Trainline (ex. « dijon-ville »), pour l'API stations."""
    best: dict[str, str] = {}
    with csv_path.open(newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            uic = (row.get("uic8_sncf") or "").strip()
            slug = (row.get("slug") or "").strip()
            if not uic or not slug:
                continue
            if uic not in best or row.get("is_main_station") == "t":
                best[uic] = slug
    return best


def loc_for(stop_area_id: str) -> str | None:
    """Id numérique Trainline d'une gare (URN loc), ou None si non mappée."""
    uid = stop_area_id.removeprefix("StopArea:")
    if not uid.startswith(_OCE):
        return None
    return _uic_to_loc().get(uid[len(_OCE):])


def slug_for(stop_area_id: str) -> str | None:
    """Slug Trainline d'une gare (ex. « dijon-ville »), ou None si non mappée."""
    uid = stop_area_id.removeprefix("StopArea:")
    if not uid.startswith(_OCE):
        return None
    return _uic_to_slug().get(uid[len(_OCE):])


def booking_url(from_stop_area_id: str, to_stop_area_id: str, date: str, time_hhmm: str | None = None) -> str | None:
    """URL Trainline pré-remplie (format URN loc, validé en HTTP), ou None si
    l'une des gares n'est pas mappée.

    `date` au format « YYYY-MM-DD ». `time_hhmm` optionnel (ex. « 07:34 ») —
    remplacé par 12:00 s'il est absent.
    """
    origin = loc_for(from_stop_area_id)
    dest = loc_for(to_stop_area_id)
    if not origin or not dest:
        return None
    params = {
        "journeySearchType": "single",
        "origin": f"urn:trainline:generic:loc:{origin}",
        "destination": f"urn:trainline:generic:loc:{dest}",
        "outwardDate": f"{date}T{time_hhmm}:00" if time_hhmm else f"{date}T12:00:00",
        "outwardDateType": "departAfter",
        "selectedTab": "train",
        "splitSave": "true",
        "lang": "fr",
        "transportModes[]": "mixed",
    }
    return f"{BOOKING_BASE}?{urlencode(params)}"
