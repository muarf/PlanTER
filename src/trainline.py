"""T9 — Liens Trainline (monétisation v1, PoC).

Cartographie `uic8` (stop_area_id « OCE<uic> ») -> **slug** Trainline (ex.
« dijon-ville »), source officielle : repo public `trainline-eu/stations-studio`
(`public/stations.csv`). L'URL de réservation pré-remplie est
`/book/results?origin={slug}&destination={slug}&outbound_date=…&outbound_time=…`
(validé en headless : les codes `sncf_id` type FRABA et les ids numériques ne
sont PAS acceptés par le moteur de réservation, seul le slug l'est).
"""
from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
DEFAULT_CSV = CONFIG_DIR / "trainline_stations.csv"
BOOKING_BASE = "https://www.thetrainline.com/book/results"

# Préfixe interne des stop_area dans le graphe : « StopArea:OCE87686006 ».
_OCE = "OCE"


@lru_cache(maxsize=1)
def _uic_to_slug(csv_path: Path = DEFAULT_CSV) -> dict[str, str]:
    """uic8 (str) -> slug Trainline (ex. « dijon-ville »). Privilégie la gare principale."""
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


def slug_for(stop_area_id: str) -> str | None:
    """Slug Trainline d'une gare (accepte « StopArea:OCE87686006 » ou « OCE87686006 »)."""
    uid = stop_area_id.removeprefix("StopArea:")
    if not uid.startswith(_OCE):
        return None
    return _uic_to_slug().get(uid[len(_OCE):])


def booking_url(from_stop_area_id: str, to_stop_area_id: str, date: str, time_hhmm: str | None = None) -> str | None:
    """URL Trainline pré-remplie, ou None si l'une des gares n'est pas mappée.

    `date` au format « YYYY-MM-DD ». `time_hhmm` optionnel (ex. « 07:34 ») —
    omis si absent pour laisser Trainline choisir.
    """
    origin = slug_for(from_stop_area_id)
    dest = slug_for(to_stop_area_id)
    if not origin or not dest:
        return None
    params = f"origin={origin}&destination={dest}&outbound_date={date}"
    if time_hhmm:
        params += f"&outbound_time={time_hhmm}"
    return f"{BOOKING_BASE}?{params}"
