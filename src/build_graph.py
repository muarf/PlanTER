#!/usr/bin/env python3
"""T2 — build_graph.py : transforme le GTFS-TER en graphe routable + cache binaire.

Alimente le moteur McRAPTOR (T3). Voir PLAN.md §5 et walkthrough.md §4 pour les
conventions de données (StopArea, trip = instance datée, validité par
calendar_dates, horaires en minutes > 24h, ligne identifiée par route_id).

Usage :
    python -m src.build_graph \
        --input data/ter/gtfs_ter.zip --output data/graph.bin \
        [--interchange config/interchange.yaml] \
        [--paris-links config/paris_links.yaml]

Acceptation T2 :
- le graphe se construit et se charge en < 2 s ;
- le 10/08/2026, un trip de la ligne K7 (route B10C45A0) dessert
  Paris Gare de Lyon -> Dijon, et un C11 dessert Dijon -> Besançon Viotte ;
- les correspondances à Dijon sont calculables (même StopArea, temps >= config).
"""

from __future__ import annotations

import argparse
import sys
import time
import zipfile
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config as cfg  # noqa: E402
from src.graph import (  # noqa: E402
    DEFAULT_MIN_TRANSFER_MIN,
    ALIASES,
    PARIS_STATION_NAMES,
    Graph,
    Route,
    StopArea,
    StopTime,
    Trip,
    normalize,
)
from src.gtfs import extract_mode_from_stop_id, read_zip_file  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "data" / "ter" / "gtfs_ter.zip"
DEFAULT_OUTPUT = ROOT / "data" / "graph.bin"
DEFAULT_INTERCHANGE = ROOT / "config" / "interchange.yaml"
DEFAULT_PARIS_LINKS = ROOT / "config" / "paris_links.yaml"


def _hhmm_to_min(hhmm: str) -> int:
    parts = hhmm.split(":")
    return int(parts[0]) * 60 + int(parts[1])


def parse_mini_yaml(path: Path) -> dict[str, str]:
    """Parser YAML *très réduit* : lignes `cle: valeur` (+ commentaires #).

    Suffisant pour les fichiers de config plats interchange.yaml et
    paris_links.yaml (pas de listes imbriquées).
    """
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip()
    return result


def _build_graph(zip_path: Path, interchange: dict[str, str], links: dict[str, str]) -> Graph:
    with zipfile.ZipFile(zip_path) as zf:
        _, stops = read_zip_file(zf, "stops.txt")
        _, routes_rows = read_zip_file(zf, "routes.txt")
        _, trips_rows = read_zip_file(zf, "trips.txt")
        _, stop_times = read_zip_file(zf, "stop_times.txt")
        _, cdates = read_zip_file(zf, "calendar_dates.txt")

    graph = Graph()

    # --- StopAreas + StopPoint -> StopArea ------------------------------
    stop_parent: dict[str, str] = {}
    for s in stops:
        stype = s.get("location_type", "0")
        sid = s["stop_id"]
        if stype == "1":
            idx = len(graph.stops)
            graph.stops.append(
                StopArea(
                    id=sid,
                    name=s.get("stop_name", ""),
                    lat=float(s.get("stop_lat", 0) or 0),
                    lon=float(s.get("stop_lon", 0) or 0),
                )
            )
            graph.stop_index[sid] = idx
        elif s.get("parent_station"):
            stop_parent[sid] = s["parent_station"]

    # --- Index de recherche de gares + alias -----------------------------
    # Construit tôt : utilisé par la résolution des configs (interchange,
    # paris_links) et par l'API (autocomplete). On déduplique via des sets :
    # un alias qui reproduit exactement le nom normalisé (ex. "dijon") ne
    # doit pas dupliquer l'entrée.
    from collections import defaultdict as _dd

    index: dict[str, set[int]] = _dd(set)
    for idx, stop in enumerate(graph.stops):
        norm = normalize(stop.name)
        if norm:
            index[norm].add(idx)
    for alias_norm, targets in ALIASES.items():
        for target in targets:
            for idx in index[normalize(target)]:
                index[alias_norm].add(idx)
    graph.search_index = {k: sorted(v) for k, v in index.items()}

    # --- Groupes de gares (« Paris toutes gares » §5.5) -----------------
    paris_targets = {normalize(n) for n in PARIS_STATION_NAMES}
    graph.place_groups["paris"] = sorted(
        idx for idx, stop in enumerate(graph.stops) if normalize(stop.name) in paris_targets
    )

    # --- Routes ---------------------------------------------------------
    for r in routes_rows:
        idx = len(graph.routes)
        graph.routes.append(
            Route(id=r["route_id"], short_name=r.get("route_short_name", ""), long_name=r.get("route_long_name", ""))
        )
        graph.route_index[r["route_id"]] = idx

    # --- Circulation (calendar_dates) -----------------------------------
    dates_by_service: dict[str, set[int]] = defaultdict(set)
    for c in cdates:
        dates_by_service[c["service_id"]].add(int(c["date"]))
    graph.service_dates = {svc: frozenset(ds) for svc, ds in dates_by_service.items()}
    all_dates = sorted({d for ds in dates_by_service.values() for d in ds})
    graph.date_min = all_dates[0] if all_dates else 0
    graph.date_max = all_dates[-1] if all_dates else 0

    # --- Trips + StopTimes ----------------------------------------------
    # mode commercial par trip (tous les arrêts partagent le même mode)
    trip_mode: dict[str, str] = {}
    for s in stop_times:
        mode = extract_mode_from_stop_id(s.get("stop_id", ""))
        trip_mode[s["trip_id"]] = cfg.MODE_VEHICLE_TYPE.get(mode, "train")

    st_by_trip: dict[str, list[dict]] = defaultdict(list)
    for s in stop_times:
        st_by_trip[s["trip_id"]].append(s)

    skipped = 0
    for t in trips_rows:
        tid = t["trip_id"]
        rows = st_by_trip.get(tid)
        if not rows:
            continue
        rows.sort(key=lambda r: int(r["stop_sequence"]))
        trip = Trip(
            id=tid,
            route=graph.route_index.get(t["route_id"], -1),
            service_id=t["service_id"],
            vehicle=trip_mode.get(tid, "train"),
        )
        if trip.route == -1:
            skipped += 1
            continue
        ok = True
        for r in rows:
            sid = r["stop_id"]
            area_id = stop_parent.get(sid) or (sid if sid in graph.stop_index else "")
            area_idx = graph.stop_index.get(area_id)
            if area_idx is None:
                skipped += 1
                ok = False
                break
            trip.stop_times.append(
                StopTime(
                    stop=area_idx,
                    arr=_hhmm_to_min(r["arrival_time"]),
                    dep=_hhmm_to_min(r["departure_time"]),
                )
            )
        if not ok or not trip.stop_times:
            continue
        graph.trips.append(trip)

    if skipped:
        print(f"[build] ⚠ {skipped} stop_times/trips ignorés (arrêt inconnu).", file=sys.stderr)

    # --- Index de routage -----------------------------------------------
    n_stops = len(graph.stops)
    graph.trips_by_route = [[] for _ in graph.routes]
    graph.routes_by_stop = [[] for _ in range(n_stops)]

    for tidx, trip in enumerate(graph.trips):
        graph.trips_by_route[trip.route].append(tidx)
        graph.trip_index[trip.id] = tidx
        seen = set()
        for st in trip.stop_times:
            if st.stop not in seen:  # dédup (trip, arrêt) — pas (trip, route) !
                graph.routes_by_stop[st.stop].append(trip.route)
                seen.add(st.stop)

    for route_trips in graph.trips_by_route:
        route_trips.sort(key=lambda i: graph.trips[i].dep)

    graph.routes_by_stop = [sorted(set(routes)) for routes in graph.routes_by_stop]

    # --- Temps de correspondance par gare --------------------------------
    default = int(interchange.pop("default", str(DEFAULT_MIN_TRANSFER_MIN)))
    graph.min_transfer = [default] * n_stops
    for key, value in interchange.items():
        value = int(value)
        resolved = _resolve_by_name(graph, key) or graph.stop_index.get(key)
        if resolved is None:
            print(f"[build] ⚠ interchange : '{key}' introuvable.", file=sys.stderr)
            continue
        graph.min_transfer[resolved] = value

    # --- Arcs de correspondance explicites (paris_links) ----------------
    for key, value in links.items():
        if "->" not in key:
            continue
        a_name, b_name = (p.strip() for p in key.split("->", 1))
        minutes = int(value)
        a = _resolve_by_name(graph, a_name)
        b = _resolve_by_name(graph, b_name)
        if a is None or b is None:
            print(f"[build] ⚠ paris_links : '{key}' gare introuvable.", file=sys.stderr)
            continue
        graph.transfer_edges[(a, b)] = minutes
        graph.transfer_edges[(b, a)] = minutes

    return graph


def _resolve_by_name(graph: Graph, name: str) -> int | None:
    """Résout un nom de gare (config) vers un stop_idx, unique et normalisé."""
    hits = graph.search_index.get(normalize(name), [])
    if len(hits) == 1:
        return hits[0]
    return None


def verify(graph: Graph, date: int) -> list[str]:
    """Critères d'acceptation T2 sur la date donnée (ex. 20260810)."""
    problems: list[str] = []

    def area_id_of(name: str) -> int | None:
        hits = graph.search_index.get(normalize(name), [])
        return hits[0] if len(hits) == 1 else None

    paris = area_id_of("Paris Gare de Lyon") or area_id_of("Paris Gare de Lyon Hall 1 - 2")
    dijon = area_id_of("Dijon")
    besancon = area_id_of("Besançon Viotte")
    bercy = area_id_of("Paris Bercy") or area_id_of("Paris Bercy Bourg. Pays d'Auv.")

    def route_serves(short: str, long_sub: str, a: int | None, b: int | None) -> bool:
        if a is None or b is None:
            return False
        for ridx, route in enumerate(graph.routes):
            if route.short_name != short:
                continue
            if long_sub not in normalize(route.long_name):
                continue
            for tidx in graph.trips_by_route[ridx]:
                trip = graph.trips[tidx]
                if not graph.is_service_active(trip.service_id, date):
                    continue
                stops = [st.stop for st in trip.stop_times]
                if a in stops and b in stops and stops.index(a) < stops.index(b):
                    return True
        return False

    if not route_serves("K7", "lyon p d", paris, dijon):
        problems.append(f"K7 Paris Gare de Lyon -> Dijon absent à la date {date}")
    if not route_serves("C11", "besan", dijon, besancon):
        problems.append(f"C11 Dijon -> Besançon Viotte absent à la date {date}")
    if dijon is not None and graph.min_transfer[dijon] < DEFAULT_MIN_TRANSFER_MIN:
        problems.append(f"min_transfer(Dijon)={graph.min_transfer[dijon]} < défaut {DEFAULT_MIN_TRANSFER_MIN}")

    # Retour du soir (itinéraire réel, cf. walkthrough.md §5) : car C1/MOBIGO
    # Besançon Viotte -> Dijon, puis K7 17764 Dijon -> Paris Bercy.
    if not route_serves("C1", "dijon", besancon, dijon):
        problems.append(f"C1 (MOBIGO) Besançon Viotte -> Dijon absent à la date {date}")
    if not route_serves("K7", "lyon p d", dijon, bercy):
        problems.append(f"K7 Dijon -> Paris Bercy absent à la date {date} (retour du soir)")

    # Groupe « Paris toutes gares »
    paris_group = graph.place_groups.get("paris", [])
    if len(paris_group) < 5:
        problems.append(f"groupe Paris (toutes gares) trop petit : {len(paris_group)} gares")
    if paris is not None and paris not in paris_group:
        problems.append("Paris Gare de Lyon absent du groupe Paris")
    if bercy is not None and bercy not in paris_group:
        problems.append("Paris Bercy absent du groupe Paris")

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Builder de graphe GTFS-TER (T2)")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--interchange", type=Path, default=DEFAULT_INTERCHANGE)
    parser.add_argument("--paris-links", type=Path, default=DEFAULT_PARIS_LINKS)
    parser.add_argument("--date", default="2026-08-10", help="Date de vérification (YYYY-MM-DD)")
    parser.add_argument("--no-paris-links", action="store_true", help="Ne pas charger les arcs intra-Paris")
    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"[build] ERREUR : {args.input} introuvable. Lancez d'abord filter_ter.", file=sys.stderr)
        return 1

    interchange = parse_mini_yaml(args.interchange)
    links = {} if args.no_paris_links else parse_mini_yaml(args.paris_links)

    t0 = time.perf_counter()
    print(f"[build] input  : {args.input}")
    graph = _build_graph(args.input, interchange, links)
    print(
        f"[build] graphe : {len(graph.stops)} gares, {len(graph.routes)} lignes, "
        f"{len(graph.trips)} trips, {graph.date_min}-{graph.date_max}"
    )

    date = int(args.date.replace("-", ""))
    problems = verify(graph, date)
    if problems:
        print(f"[build] ✗ Acceptation T2 ({args.date}) : {problems}")
        return 1
    print(f"[build] ✓ Acceptation T2 ({args.date}) : K7 Paris GDL -> Dijon, C11 Dijon -> Besançon, correspondance OK")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    graph.save(args.output)
    size_kb = args.output.stat().st_size / 1024
    print(f"[build] cache  : {args.output} ({size_kb:.0f} Ko, build {time.perf_counter() - t0:.2f} s)")

    t0 = time.perf_counter()
    loaded = Graph.load(args.output)
    dt = time.perf_counter() - t0
    verdict = "✓ < 2 s" if dt < 2 else "⚠ > 2 s"
    print(f"[build] load   : {dt:.2f} s ({verdict}) — {len(loaded.trips)} trips rechargés")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
