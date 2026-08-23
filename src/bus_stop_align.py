"""Alignement géographique arrêts bus ↔ gares SNCF.

Pour chaque arrêt bus, trouve la/les gare(s) SNCF dans un rayon donné
et crée des arcs de correspondance (transfer_edges) entre eux.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt

EARTH_R_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance à vol doiseau (km) entre deux points."""
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * EARTH_R_KM * asin(sqrt(a))


@dataclass
class TransferEdge:
    """Arc de correspondance entre un arrêt bus et une gare SNCF."""

    from_idx: int  # stop_idx de larrêt bus dans le graphe
    to_idx: int  # stop_idx de la gare SNCF dans le graphe
    minutes: int  # temps de correspondance


def align_bus_stops(
    bus_stops: list[tuple[int, float, float]],
    train_stops: list[tuple[int, float, float]],
    max_distance_km: float = 1.0,
    transfer_minutes: int = 8,
) -> list[TransferEdge]:
    """Aligne les arrêts bus sur les gares SNCF proches.

    Args:
        bus_stops: [(stop_idx, lat, lon), ...] arrêts bus déjà dans le graphe
        train_stops: [(stop_idx, lat, lon), ...] gares SNCF déjà dans le graphe
        max_distance_km: rayon de recherche (défaut 1 km)
        transfer_minutes: temps de correspondance fixe (défaut 8 min)

    Returns:
        Liste de TransferEdge (bus -> train) pour les paires proches.
    """
    edges: list[TransferEdge] = []
    for b_idx, b_lat, b_lon in bus_stops:
        if b_lat == 0.0 and b_lon == 0.0:
            continue
        best_dist = float("inf")
        best_t_idx = -1
        for t_idx, t_lat, t_lon in train_stops:
            d = haversine_km(b_lat, b_lon, t_lat, t_lon)
            if d < best_dist:
                best_dist = d
                best_t_idx = t_idx
        if best_t_idx >= 0 and best_dist <= max_distance_km:
            edges.append(TransferEdge(from_idx=b_idx, to_idx=best_t_idx, minutes=transfer_minutes))
    return edges
