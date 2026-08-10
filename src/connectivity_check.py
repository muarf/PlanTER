#!/usr/bin/env python3
"""T1 — connectivity_check.py : vérifie la faisabilité de trajets 100% TER.

Outil de validation des données (complément de validate_ter.py). Pour un couple
de gares et une date donnée, il calcule les trajets TER possibles avec au plus
`--max-transfers` correspondances (recherche "départ au plus tôt / arrivée au
plus tôt", par rounds — une implémentation simplifiée de RAPTOR).

C'est un OUTIL DE VALIDATION : il sera remplacé par le moteur McRAPTOR de la
Tâche T3 pour la production.

Usage :
    python -m src.connectivity_check \
        --from "Paris Est" --to "Besançon Viotte" \
        --date 2026-09-15 --time 08:00 [--max-transfers 3]

    python -m src.connectivity_check --pairs "Paris Est|Besançon Viotte;Lille Flandres|Lyon Perrache"
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.gtfs import read_zip_file  # noqa: E402
from src.graph import normalize  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "data" / "ter" / "gtfs_ter.zip"

MIN_TRANSFER_MIN = 5  # temps minimum de correspondance dans une gare (minutes)
DAY_MIN = 24 * 60

# Gares principales de Paris (groupe « Paris toutes gares » §5.5) : atteindre
# l'une d'elles compte comme « arrivé à Paris ».
PARIS_STATION_NAMES: frozenset[str] = frozenset(
    {
        "paris est",
        "paris gare du nord",
        "paris saint lazare",
        "paris montparnasse hall 1 2",
        "paris austerlitz",
        "paris gare de lyon hall 1 2",
        "paris bercy bourg pays d auv",
    }
)
PARIS_QUERIES: frozenset[str] = frozenset(
    {"paris", "paris toutes gares", "paris tous", "toutes gares"}
)


def _hhmm_to_min(hhmm: str) -> int:
    parts = hhmm.split(":")
    h, m = parts[0], parts[1]
    return int(h) * 60 + int(m)


class Trip:
    __slots__ = ("id", "route_id", "route_short", "route_long", "areas", "arrs", "deps", "vehicle")

    def __init__(self, tid, route_id, route_short, route_long, vehicle):
        self.id = tid
        self.route_id = route_id
        self.route_short = route_short
        self.route_long = route_long
        self.vehicle = vehicle
        self.areas: list[str] = []
        self.arrs: list[int] = []
        self.deps: list[int] = []


def load(zip_path: Path, date: str):
    """Charge le GTFS-TER et retourne la structure de routage pour `date`."""
    date = date.replace("-", "")
    with zipfile.ZipFile(zip_path) as zf:
        _, stops = read_zip_file(zf, "stops.txt")
        _, trips = read_zip_file(zf, "trips.txt")
        _, routes = read_zip_file(zf, "routes.txt")
        _, stop_times = read_zip_file(zf, "stop_times.txt")
        _, cdates = read_zip_file(zf, "calendar_dates.txt")

    # services actifs à la date demandée
    active = {
        c["service_id"]
        for c in cdates
        if c["date"] == date and c.get("exception_type", "1") == "1"
    }

    # stop_id (StopPoint) -> StopArea (parent_station)
    sp_parent = {s["stop_id"]: s.get("parent_station", "") for s in stops}
    area_name = {
        s["stop_id"]: s.get("stop_name", "")
        for s in stops
        if s.get("location_type", "0") == "1"
    }

    route_info = {
        r["route_id"]: (r.get("route_short_name", ""), r.get("route_long_name", ""))
        for r in routes
    }

    # trips valides pour cette date
    trip_rows = [t for t in trips if t.get("service_id", "") in active]
    trips_by_id = {}
    for t in trip_rows:
        rid = t["route_id"]
        short, long_ = route_info.get(rid, (rid, ""))
        trips_by_id[t["trip_id"]] = Trip(t["trip_id"], rid, short, long_, "")

    # remplissage des stop_times
    st_by_trip: dict[str, list] = {}
    for s in stop_times:
        if s["trip_id"] not in trips_by_id:
            continue
        st_by_trip.setdefault(s["trip_id"], []).append(s)

    for tid, rows in st_by_trip.items():
        rows.sort(key=lambda r: int(r["stop_sequence"]))
        trip = trips_by_id[tid]
        for r in rows:
            area = sp_parent.get(r["stop_id"], r["stop_id"])
            if not area:
                area = r["stop_id"]
            trip.areas.append(area)
            trip.arrs.append(_hhmm_to_min(r["arrival_time"]))
            trip.deps.append(_hhmm_to_min(r["departure_time"]))

    return trips_by_id, area_name


def search(trips: dict, area_name: dict, origins: list[str], dests: list[str], t0: int, max_transfers: int):
    """Recherche "arrivée au plus tôt" avec <= max_transfers changements.

    `origins`/`dests` sont des listes de gares : la recherche démarre depuis
    n'importe quelle origine et s'arrête dès qu'une destination est atteinte
    (groupes « toutes gares » §5.5).

    Retourne une liste de (nb_changements, heure_arrivée, legs) pour chaque
    nombre de changements où une destination est atteignable.
    """
    # index : area -> [(trip, position)]
    area_trips: dict[str, list[tuple[str, int]]] = {}
    for tid, trip in trips.items():
        for pos, area in enumerate(trip.areas):
            area_trips.setdefault(area, []).append((tid, pos))

    INF = float("inf")
    origin_set = set(origins)
    dest_set = set(dests)
    best: dict[str, float] = {o: float(t0) for o in origins}
    prev: dict[str, tuple[str, str]] = {}  # area -> (area embarquement, trip_id)

    results: list[tuple[int, float, list[dict]]] = []
    frontier: dict[str, float] = dict(best)

    for transfers in range(max_transfers + 1):
        new_frontier: dict[str, float] = {}
        for area, arr in frontier.items():
            board_time = arr if transfers == 0 else arr + MIN_TRANSFER_MIN
            for tid, pos in area_trips.get(area, []):
                trip = trips[tid]
                dep = trip.deps[pos]
                if dep < board_time:
                    continue
                for j in range(pos, len(trip.areas)):
                    a2 = trip.areas[j]
                    if a2 == area:  # pas d'auto-boucle (gare d'embarquement)
                        continue
                    if trip.arrs[j] < best.get(a2, INF):
                        best[a2] = trip.arrs[j]
                        prev[a2] = (area, tid)
                        new_frontier[a2] = trip.arrs[j]
        reached = dest_set & new_frontier.keys()
        if reached:
            best_dest = min(reached, key=new_frontier.get)
            results.append((transfers, new_frontier[best_dest], _reconstruct(prev, trips, origin_set, best_dest)))
        if not new_frontier:
            break
        frontier = new_frontier

    return results, area_name


def _reconstruct(prev, trips, origins: set[str], dest) -> list[dict]:
    """Reconstruit la séquence de legs depuis les backpointers."""
    legs: list[dict] = []
    cur = dest
    seen: set[str] = set()
    while cur not in origins:
        if cur in seen:  # garde-fou anti-cycle (routes circulaires)
            break
        seen.add(cur)
        board_area, tid = prev.get(cur, (None, None))
        if tid is None:
            break
        trip = trips[tid]
        legs.append({"trip": tid, "route": trip.route_short, "name": trip.route_long, "to": cur, "board_at": board_area})
        cur = board_area
    legs.reverse()
    return legs


def _find_area(area_name: dict, query: str) -> str | None:
    """Trouve un StopArea par nom (insensible accents/casse)."""
    q = normalize(query)
    best: tuple[int, str] | None = None
    for aid, name in area_name.items():
        n = normalize(name)
        if n == q:
            return aid
        if q in n:
            score = len(q) / len(n)
            if best is None or score > best[0]:
                best = (score, aid)
    return best[1] if best else None


def _find_areas(area_name: dict, query: str) -> list[str]:
    """Résout une requête en une liste de gares (« Paris » → toutes les gares
    principales de Paris, sinon la meilleure gare unique)."""
    if normalize(query) in PARIS_QUERIES:
        return [aid for aid, name in area_name.items() if normalize(name) in PARIS_STATION_NAMES]
    single = _find_area(area_name, query)
    return [single] if single else []


def format_time(minutes: float) -> str:
    minutes = int(minutes) % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def check_pair(trips, area_name, origin_q, dest_q, date, time, max_transfers, verbose=True) -> bool:
    o_list = _find_areas(area_name, origin_q)
    d_list = _find_areas(area_name, dest_q)
    if not o_list or not d_list:
        print(f"  ! Gare introuvable : {'De ' + origin_q if not o_list else 'À ' + dest_q}")
        return False

    def label(q: str, areas: list[str]) -> str:
        if len(areas) > 1:
            return f"{q} (toutes gares)"
        return area_name[areas[0]]

    t0 = _hhmm_to_min(time)
    results, _ = search(trips, area_name, o_list, d_list, t0, max_transfers)
    if not results:
        print(f"  ✗ {label(origin_q, o_list)} -> {label(dest_q, d_list)} : AUCUN trajet TER avec ≤{max_transfers} changement(s)")
        return False
    print(f"  ✓ {label(origin_q, o_list)} -> {label(dest_q, d_list)} :")
    for transfers, arr, legs in results:
        if not legs:
            continue
        seq = " -> ".join(
            f"{area_name[l['board_at']]} [{l['route']}] {area_name[l['to']]}"
            for l in legs
        )
        print(
            f"      {transfers} changement(s), arrivée {format_time(arr)} "
            f"({len(legs)} trains : {seq})"
        )
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vérifie la connectivité TER entre deux gares")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--date", default="2026-09-15", help="Date de voyage (YYYY-MM-DD)")
    parser.add_argument("--time", default="08:00", help="Heure de départ (HH:MM)")
    parser.add_argument("--max-transfers", type=int, default=3)
    parser.add_argument("--from", dest="from_")
    parser.add_argument("--to")
    parser.add_argument("--pairs", help="Liste 'De1|À1;De2|À2;...'")
    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"[check] ERREUR : {args.input} introuvable. Lancez d'abord filter_ter.", file=sys.stderr)
        return 1

    trips, area_name = load(args.input, args.date)
    pairs = []
    if args.pairs:
        for chunk in args.pairs.split(";"):
            if "|" in chunk:
                a, b = chunk.split("|", 1)
                pairs.append((a.strip(), b.strip()))
    elif args.from_ and args.to:
        pairs = [(args.from_, args.to)]
    if not pairs:
        parser.error("Fournissez --from/--to ou --pairs")

    print(f"[check] date={args.date} départ={args.time} max_changements={args.max_transfers}")
    ok = True
    for a, b in pairs:
        ok = check_pair(trips, area_name, a, b, args.date, args.time, args.max_transfers) and ok
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
