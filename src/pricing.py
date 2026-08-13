"""T12 — pricing.py : estimation tarifaire TER (MVP).

Modèle (cf. config/pricing.yaml) :
- distance ferroviaire estimée d'un leg = somme des haversine entre les
  arrêts consécutifs du train × `rail_factor` ;
- région d'un train = région majoritaire de ses arrêts (config/station_regions) ;
- prix d'un billet = scale_region × (a·√km + b·km), arrondi aux 5 centimes,
  plancher min_eur ;
- agrégation : trajet mono-région -> un billet sur la distance totale cumulée
  (dégressivité globale) ; pluri-région -> somme des billets par tronçon.

Les prix sont des ESTIMATIONS : le modèle est calibré sur quelques prix
observés (Trainline, 12/08/2026) et les barèmes régionaux réels n'y sont pas
tous disponibles. L'API marque ces prix comme estimés.
"""

from __future__ import annotations

import json
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from typing import TYPE_CHECKING

import yaml  # noqa: E402

if TYPE_CHECKING:
    from src.graph import Graph

ROOT = Path(__file__).resolve().parents[1]
PRICING_FILE = ROOT / "config" / "pricing.yaml"
REGIONS_FILE = ROOT / "config" / "station_regions.json"

EARTH_R_KM = 6371.0088
_STOPAREA = "StopArea:"
_OCE = "OCE"


# -------------------------------------------------------------- distances
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance à vol d'oiseau (km) entre deux points."""
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * EARTH_R_KM * asin(sqrt(a))


class PricingEngine:
    """Tarification d'un trajet à partir du graphe et des configs.

    Caches lazy (à l'exécution d'un même process) : distance par segment de
    trip et région par trip. Les accès concurrents sont bénins (recalculs
    redondants, valeurs identiques).
    """

    def __init__(self, graph: "Graph", pricing: Path = PRICING_FILE, regions: Path = REGIONS_FILE):
        self.graph = graph
        cfg = yaml.safe_load(pricing.read_text(encoding="utf-8"))
        self.rail_factor = float(cfg["rail_factor"])
        self.min_eur = float(cfg["min_eur"])
        self.round_to = float(cfg["round_to"])
        self.a = float(cfg["a"])
        self.b = float(cfg["b"])
        self.default_scale = float(cfg["default_scale"])
        self.region_scale = {name: float(v["scale"]) for name, v in cfg.get("regions", {}).items()}
        self._stations = json.loads(regions.read_text(encoding="utf-8"))
        self._stop_region: dict[int, str] = {}
        self._trip_region: dict[int, str] = {}
        self._leg_km: dict[tuple[int, int, int], float] = {}

    # ------------------------------------------------------ région d'un arrêt
    def _uic(self, stop_idx: int) -> str | None:
        stop_id = self.graph.stops[stop_idx].id
        if stop_id.startswith(_STOPAREA):
            stop_id = stop_id[len(_STOPAREA):]
        if not stop_id.startswith(_OCE):
            return None
        return stop_id[len(_OCE):]

    def stop_region(self, stop_idx: int) -> str:
        if stop_idx not in self._stop_region:
            uic = self._uic(stop_idx)
            region = self._stations.get(uic or "", {}).get("region") if uic else None
            self._stop_region[stop_idx] = region or "INCONNUE"
        return self._stop_region[stop_idx]

    # ------------------------------------------------------ région d'un train
    def trip_region(self, trip_idx: int) -> str:
        if trip_idx not in self._trip_region:
            counts: dict[str, int] = {}
            seen: set[int] = set()
            for st in self.graph.trips[trip_idx].stop_times:
                if st.stop in seen:
                    continue
                seen.add(st.stop)
                r = self.stop_region(st.stop)
                counts[r] = counts.get(r, 0) + 1
            # majorité (à égalité : le premier par ordre d'apparition)
            self._trip_region[trip_idx] = max(counts.items(), key=lambda kv: kv[1])[0]
        return self._trip_region[trip_idx]

    # ------------------------------------------------------ distance d'un leg
    def leg_km(self, trip_idx: int, board_stop: int, alight_stop: int) -> float:
        key = (trip_idx, board_stop, alight_stop)
        if key not in self._leg_km:
            stops = self.graph.trips[trip_idx].stop_times
            bi = ai = -1
            for i, st in enumerate(stops):
                if st.stop == board_stop and bi < 0:
                    bi = i
                if st.stop == alight_stop:
                    ai = i
            km = 0.0
            if 0 <= bi < ai:
                for k in range(bi, ai):
                    s1, s2 = self.graph.stops[stops[k].stop], self.graph.stops[stops[k + 1].stop]
                    km += haversine_km(s1.lat, s1.lon, s2.lat, s2.lon)
            self._leg_km[key] = km * self.rail_factor
        return self._leg_km[key]

    # ---------------------------------------------------------------- tarif
    def fare(self, km: float, region: str) -> float:
        """Prix estimé d'un billet sur `km` km dans `region`."""
        scale = self.region_scale.get(region, self.default_scale)
        raw = scale * (self.a * sqrt(km) + self.b * km) if km > 0 else 0.0
        return max(self.min_eur, round(round(raw / self.round_to) * self.round_to, 2))

    # ------------------------------------------------------------- trajet
    def journey_price(self, journey) -> dict | None:
        """Prix estimé d'un trajet (legs ferroviaires uniquement).

        Retourne None si le trajet n'a aucun leg ferroviaire (tout à pied).
        """
        rail = [l for l in journey.legs if l.type != "walk"]
        if not rail:
            return None

        legs: list[dict] = []
        regions: set[str] = set()
        total_km = 0.0
        for leg in rail:
            trip_idx = self.graph.trip_index.get(leg.trip_id)
            if trip_idx is None:
                # leg sans trip (impossible en pratique) : on s'abstient
                return None
            board = self.graph.stop_index.get(leg.from_id)
            alight = self.graph.stop_index.get(leg.to_id)
            if board is None or alight is None:
                return None
            km = self.leg_km(trip_idx, board, alight)
            region = self.trip_region(trip_idx)
            total_km += km
            regions.add(region)
            legs.append({"line": leg.line, "km": round(km, 1), "region": region})

        rule = "mono_region" if len(regions) == 1 else "pluri_region"
        if rule == "mono_region":
            region = legs[0]["region"]
            total_eur = self.fare(total_km, region)
        else:
            total_eur = round(sum(self.fare(jl["km"], jl["region"]) for jl in legs), 2)

        return {
            "rule": rule,
            "regions": sorted(regions),
            "km": round(total_km, 1),
            "legs": legs,
            "price_normal_eur": total_eur,
            "note": "prix estimés (modèle v1 calibré sur 3 prix observés Trainline, 12/08/2026)",
        }
