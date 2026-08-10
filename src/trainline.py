"""T9 — Liens Trainline (monétisation v1, PoC).

Cartographie `uic8` (stop_area_id « OCE<uic> ») -> code `sncf_id` Trainline
(source officielle : repo public `trainline-eu/stations-studio`,
`public/stations.csv`) et génération d'URL de recherche pré-remplies
(`/book/results?origin=…&destination=…&outbound_date=…&outbound_time=…`).
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
def _uic_to_code(csv_path: Path = DEFAULT_CSV) -> dict[str, str]:
    """uic8 (str) -> sncf_id Trainline (ex. « FRPLY »). Privilégie la gare principale."""
    best: dict[str, str] = {}
    with csv_path.open(newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            uic = (row.get("uic8_sncf") or "").strip()
            code = (row.get("sncf_id") or "").strip()
            if not uic or not code:
                continue
            if uic not in best or row.get("is_main_station") == "t":
                best[uic] = code
    return best


def code_for(stop_area_id: str) -> str | None:
    """Retourne le code Trainline d'une gare (accepte « StopArea:OCE87686006 » ou « OCE87686006 »)."""
    uid = stop_area_id.removeprefix("StopArea:")
    if not uid.startswith(_OCE):
        return None
    return _uic_to_code().get(uid[len(_OCE):])


def booking_url(from_stop_area_id: str, to_stop_area_id: str, date: str, time_hhmm: str | None = None) -> str | None:
    """URL Trainline pré-remplie, ou None si l'une des gares n'est pas mappée.

    `date` au format « YYYY-MM-DD » (départ le jour demandé). `time_hhmm` optionnel
    (ex. « 07:34 ») — omis si absent pour laisser Trainline choisir.
    """
    origin = code_for(from_stop_area_id)
    dest = code_for(to_stop_area_id)
    if not origin or not dest:
        return None
    params = f"origin={origin}&destination={dest}&outbound_date={date}"
    if time_hhmm:
        params += f"&outbound_time={time_hhmm}"
    return f"{BOOKING_BASE}?{params}"
