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
    build_place_groups,
    Graph,
    Route,
    StopArea,
    StopTime,
    Trip,
    normalize,
)
from src.gtfs import extract_mode_from_stop_id, read_zip_file  # noqa: E402
from src.rfn import RfnIndex, uic_from_stop_id  # noqa: E402
from src.bus_stop_align import align_bus_stops  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "data" / "ter" / "gtfs_ter.zip"
DEFAULT_OUTPUT = ROOT / "data" / "graph.bin"
DEFAULT_INTERCHANGE = ROOT / "config" / "interchange.yaml"
DEFAULT_PARIS_LINKS = ROOT / "config" / "paris_links.yaml"
DEFAULT_PLACE_GROUPS = ROOT / "config" / "place_groups.json"
DEFAULT_BUS_FEEDS = ROOT / "config" / "bus_feeds.json"
DEFAULT_COMMUNES_URL = (
    "https://geo.api.gouv.fr/communes?fields=nom,code,centre,population&format=json"
)
DEFAULT_COMMUNES_CACHE = ROOT / "data" / "raw" / "communes.json"


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


def _merge_extra_feed(graph: Graph, zip_path: Path) -> None:
    """Fusionne un GTFS complémentaire dans le graphe déjà rempli.

    Le feed national SNCF ne contient pas les trains ZOU! Transdev RSI
    (directs Marseille <-> Nice). Ce feed utilise des stop_id = UIC nus
    (87751008) et des trips datés (17481@2026-08-10). On aligne chaque UIC
    sur la StopArea existante `StopArea:OCE<uic>` ; les trips dont un arrêt
    est inconnu sont ignorés.
    """
    with zipfile.ZipFile(zip_path) as zf:
        _, routes_rows = read_zip_file(zf, "routes.txt")
        _, trips_rows = read_zip_file(zf, "trips.txt")
        _, stop_times = read_zip_file(zf, "stop_times.txt")
        _, cdates = read_zip_file(zf, "calendar_dates.txt")

    # --- Routes (dédup sur route_id) ------------------------------------
    for r in routes_rows:
        rid = r["route_id"]
        if rid in graph.route_index:
            continue
        graph.route_index[rid] = len(graph.routes)
        graph.routes.append(
            Route(id=rid, short_name=r.get("route_short_name", ""), long_name=r.get("route_long_name", ""))
        )

    # --- Circulation (service_id -> dates) ------------------------------
    # Même schéma que le feed principal : chaque service pointe des dates.
    for c in cdates:
        dates = set(graph.service_dates.get(c["service_id"], ()))
        dates.add(int(c["date"]))
        graph.service_dates[c["service_id"]] = frozenset(dates)
    for svc, dates in graph.service_dates.items():
        if dates:
            if graph.date_min == 0 or min(dates) < graph.date_min:
                graph.date_min = min(dates)
            if max(dates) > graph.date_max:
                graph.date_max = max(dates)

    # --- Trips + StopTimes (alignés sur les StopArea existantes) ---------
    st_by_trip: dict[str, list[dict]] = defaultdict(list)
    for s in stop_times:
        st_by_trip[s["trip_id"]].append(s)

    skipped = 0
    added = 0
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
            vehicle="train",
        )
        if trip.route == -1:
            skipped += 1
            continue
        ok = True
        for r in rows:
            uic = r["stop_id"]
            area_id = f"StopArea:OCE{uic}"
            area_idx = graph.stop_index.get(area_id)
            if area_idx is None:
                area_idx = graph.stop_index.get(uic)
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
        added += 1

    if skipped:
        print(f"[build] ⚠ extra {zip_path.name} : {skipped} stop_times/trips ignorés (arrêt inconnu).", file=sys.stderr)
    print(f"[build] extra {zip_path.name} : {added} trips, {len(routes_rows)} ligne(s).", file=sys.stderr)



def _merge_bus_feed(graph, zip_path, region, allowed_route_types=None):
    """Fusionne un feed GTFS bus régional dans le graphe."""
    import zipfile
    import sys
    from collections import defaultdict
    from src.graph import StopArea, Route, StopTime, Trip
    from src.gtfs import read_zip_file
    from src import config as _cfg

    with zipfile.ZipFile(zip_path) as zf:
        _, routes_rows = read_zip_file(zf, "routes.txt")
        _, trips_rows = read_zip_file(zf, "trips.txt")
        _, stop_times = read_zip_file(zf, "stop_times.txt")
        _, cdates = read_zip_file(zf, "calendar_dates.txt")
        _, stops_rows = read_zip_file(zf, "stops.txt")
        if "calendar.txt" in zf.namelist():
            _, cal_rows = read_zip_file(zf, "calendar.txt")
        else:
            cal_rows = []

    bus_stop_map = {}
    bus_stops_coords = []
    for s in stops_rows:
        original_id = s["stop_id"]
        if ':ST:' in original_id:
            original_id = original_id.replace(':ST:', ':')
        area_id = _cfg.BUS_STOP_PREFIX + original_id
        try:
            lat = float(s.get("stop_lat", 0) or 0)
            lon = float(s.get("stop_lon", 0) or 0)
        except (ValueError, TypeError):
            lat, lon = 0.0, 0.0
        if area_id in graph.stop_index:
            existing_idx = graph.stop_index[area_id]
            existing = graph.stops[existing_idx]
            if (existing.lat == 0.0 and existing.lon == 0.0) and (lat != 0.0 or lon != 0.0):
                keep_name = existing.name if len(existing.name) > len(s.get("stop_name", "")) else s.get("stop_name", "")
                graph.stops[existing_idx] = StopArea(id=area_id, name=keep_name, lat=lat, lon=lon)
                bus_stops_coords.append((existing_idx, lat, lon))
            continue
        idx = len(graph.stops)
        graph.stops.append(StopArea(id=area_id, name=s.get("stop_name", ""), lat=lat, lon=lon))
        graph.stop_index[area_id] = idx
        bus_stop_map[original_id] = idx
        if lat != 0.0 or lon != 0.0:
            bus_stops_coords.append((idx, lat, lon))

    allowed_set = set(allowed_route_types) if allowed_route_types else None
    filtered_routes = 0
    for r in routes_rows:
        rid = r["route_id"]
        rtype = int(r.get("route_type", 3))
        if allowed_set is not None and rtype not in allowed_set:
            filtered_routes += 1
            continue
        if rid in graph.route_index:
            continue
        graph.route_index[rid] = len(graph.routes)
        graph.routes.append(
            Route(id=rid, short_name=r.get("route_short_name", ""), long_name=r.get("route_long_name", ""))
        )
    if filtered_routes:
        print("[build] bus %s : %d routes filtres (route_type non-bus)." % (zip_path.name, filtered_routes), file=sys.stderr)

    # --- calendar.txt (services hebdo) → dates concrètes --------
    from datetime import date as _dt, timedelta as _td
    for row in cal_rows:
        svc = row["service_id"]
        sd = int(row["start_date"])
        ed = int(row["end_date"])
        wd = [int(row["monday"]), int(row["tuesday"]), int(row["wednesday"]),
              int(row["thursday"]), int(row["friday"]),
              int(row["saturday"]), int(row["sunday"])]
        d = _dt(sd // 10000, (sd % 10000) // 100, sd % 100)
        d_end = _dt(ed // 10000, (ed % 10000) // 100, ed % 100)
        dates = set(graph.service_dates.get(svc, ()))
        while d <= d_end:
            if wd[d.weekday()]:
                dates.add(int(d.strftime("%Y%m%d")))
            d += _td(days=1)
        graph.service_dates[svc] = frozenset(dates)

    # --- calendar_dates.txt (exceptions) --------------------------
    for c in cdates:
        dates = set(graph.service_dates.get(c["service_id"], ()))
        exception_type = int(c.get("exception_type", 1))
        if exception_type == 1:
            dates.add(int(c["date"]))
        elif exception_type == 2:
            dates.discard(int(c["date"]))
        graph.service_dates[c["service_id"]] = frozenset(dates)
    for svc, dates in graph.service_dates.items():
        if dates:
            if graph.date_min == 0 or min(dates) < graph.date_min:
                graph.date_min = min(dates)
            if max(dates) > graph.date_max:
                graph.date_max = max(dates)

    st_by_trip = defaultdict(list)
    for s in stop_times:
        st_by_trip[s["trip_id"]].append(s)

    def _hhmm(h):
        if not h or ":" not in h:
            return 0
        p = h.split(":")
        try:
            return int(p[0]) * 60 + int(p[1])
        except (ValueError, IndexError):
            return 0

    skipped = 0
    added = 0
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
            vehicle="bus",
        )
        if trip.route == -1:
            skipped += 1
            continue
        ok = True
        for r in rows:
            area_id = _cfg.BUS_STOP_PREFIX + r["stop_id"]
            area_idx = graph.stop_index.get(area_id)
            if area_idx is None:
                skipped += 1
                ok = False
                break
            trip.stop_times.append(
                StopTime(stop=area_idx, arr=_hhmm(r["arrival_time"]), dep=_hhmm(r["departure_time"]))
            )
        if not ok or not trip.stop_times:
            continue
        graph.trips.append(trip)
        added += 1

    if skipped:
        print("[build] bus %s : %d stop_times/trips ignores." % (zip_path.name, skipped), file=sys.stderr)
    print("[build] bus %s : %d trips, %d lignes, %d arrets." % (zip_path.name, added, len(routes_rows), len(bus_stop_map)), file=sys.stderr)

    train_stops_coords = []
    for idx, stop in enumerate(graph.stops):
        if not stop.id.startswith(_cfg.BUS_STOP_PREFIX):
            if stop.lat != 0.0 or stop.lon != 0.0:
                train_stops_coords.append((idx, stop.lat, stop.lon))

    edges = align_bus_stops(bus_stops_coords, train_stops_coords, max_distance_km=1.0)
    for e in edges:
        graph.transfer_edges[(e.from_idx, e.to_idx)] = e.minutes
        graph.transfer_edges[(e.to_idx, e.from_idx)] = e.minutes
    print("[build] bus align : %d correspondances bus<->train creees." % len(edges), file=sys.stderr)

    # Propager la region pour les arrets bus (utilise par pricing)
    for idx, _ in bus_stop_map.items():
        area_id = _cfg.BUS_STOP_PREFIX + idx
        g_idx = graph.stop_index.get(area_id)
        if g_idx is not None:
            graph.bus_stop_region[g_idx] = region



def _load_communes(graph: Graph, url: str = DEFAULT_COMMUNES_URL,
                   cache_path: Path = DEFAULT_COMMUNES_CACHE) -> None:
    """Charge le référentiel des communes (nom + centre + population) dans le
    graphe pour la recherche/résolution offline des villes sans gare.

    Téléchargé une fois au build depuis geo.api.gouv.fr (Étalab) ; en cas
    d'échec réseau, retombe sur le cache local data/raw/communes.json. Aucun
    appel réseau au runtime : tout est picklé dans graph.bin.
    """
    import json as _json

    rows = None
    try:
        import urllib.request

        req = urllib.request.Request(url, headers={"User-Agent": "ter-finder/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            rows = _json.loads(resp.read().decode("utf-8"))
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(_json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:  # réseau indisponible, API down…
        if cache_path.exists():
            print(f"[build] ⚠ communes : téléchargement impossible ({exc}) ; "
                  f"usage du cache {cache_path}.", file=sys.stderr)
            rows = _json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            print(f"[build] ⚠ communes indisponibles ({exc}) et aucun cache : "
                  "recherche de communes désactivée.", file=sys.stderr)
            return

    for row in rows:
        code = row.get("code")
        nom = row.get("nom")
        centre = row.get("centre") or {}
        coords = centre.get("coordinates") if isinstance(centre, dict) else None
        if not code or not nom or not coords or len(coords) < 2:
            continue
        try:
            lon, lat = float(coords[0]), float(coords[1])
            pop = int(row.get("population") or 0)
        except (TypeError, ValueError):
            continue
        graph.communes[code] = (nom, lat, lon, pop)
        norm = normalize(nom)
        if norm:
            graph.commune_index.setdefault(norm, []).append(code)
    print(f"[build] communes : {len(graph.communes)} indexées "
          f"({len(graph.commune_index)} noms normalisés).")
    # Stops desservis par des trips bus SANS aucun trip train : à exclure du
    # « gare la plus proche » (les flux bus réutilisent des StopAreas OCE
    # « Halte Routière »). Une vraie gare aussi desservie par un bus reste
    # éligible.
    bus_served = {st.stop for t in graph.trips if t.vehicle == "bus" for st in t.stop_times}
    train_served = {st.stop for t in graph.trips if t.vehicle != "bus" for st in t.stop_times}
    _precompute_commune_nearest(graph, exclude=bus_served - train_served)


def _precompute_commune_nearest(graph: Graph, exclude: set[int] | None = None) -> None:
    """Pour chaque commune : gare OCE la plus proche + distance réelle (km).

    Stocké dans graph.communes (tuples étendus à 6 éléments :
    nom, lat, lon, pop, gare_idx, km) pour l'autocomplete (« By · Liesle
    7,9 km ») et l'aide au choix du rayon. Recherche par grille spatiale sur
    les ~3 500 gares (un scan naïf ferait ~120 M d'opérations Python) ;
    distance via approx_distance_km (correction cos(latitude), cf. graph.py).
    """
    import math
    from src.graph import approx_distance_km

    excl = exclude or set()
    gare_idx = [i for i, s in enumerate(graph.stops) if not s.id.startswith("BusStop:")
                and i not in excl
                and (s.lat != 0.0 or s.lon != 0.0)]
    if not gare_idx or not graph.communes:
        return

    cell = 0.05  # ~5 km ; grille en degrés, distances métriques corrigées
    grid: dict[tuple[int, int], list[int]] = {}
    for i in gare_idx:
        s = graph.stops[i]
        grid.setdefault((int(s.lat / cell), int(s.lon / cell)), []).append(i)

    def nearest(lat: float, lon: float) -> tuple[int, float]:
        cx, cy = int(lat / cell), int(lon / cell)
        best = [-1, math.inf]

        def scan(gx: int, gy: int) -> None:
            for i in grid.get((gx, gy), ()):
                s = graph.stops[i]
                d = approx_distance_km(lat, lon, s.lat, s.lon)
                if d < best[1]:
                    best[0], best[1] = i, d

        # Anneaux de rayon croissant — PÉRIMÈTRE seulement (8r cellules par
        # anneau, pas le carré complet : un scan en carré coûtait O(R³) et
        # bloquait le build sur les communes sans gare proche — DROM).
        for r in range(0, 400):
            if r == 0:
                scan(cx, cy)
            else:
                for gx in range(cx - r, cx + r + 1):
                    scan(gx, cy - r)
                    scan(gx, cy + r)
                for gy in range(cy - r + 1, cy + r):
                    scan(cx - r, gy)
                    scan(cx + r, gy)
            # Rayon km garanti couvert après cet anneau : borne basse du
            # km/cellule (0,05° de longitude ≈ 3,5 km à la latitude max).
            if best[1] <= r * cell * 69.0:
                break
        return best[0], best[1]

    t0 = time.perf_counter()
    updated = 0
    for code, entry in graph.communes.items():
        idx, km = nearest(entry[1], entry[2])
        if idx < 0:
            continue
        graph.communes[code] = (*entry[:4], idx, round(km, 3))
        updated += 1
    print(f"[build] communes : gare la plus proche précalculée pour {updated} communes "
          f"({time.perf_counter() - t0:.1f} s).")


def _build_graph(
    zip_path: Path,
    extra_zips: list[Path],
    interchange: dict[str, str],
    links: dict[str, str],
) -> Graph:
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

    # --- Groupes de gares (« toutes gares » §5.5) -------------------------
    # Config config/place_groups.json : {ville → {label, aliases, stations}}.
    # Les gares absentes du GTFS sont ignorées (build_place_groups), on
    # avertit ici si un groupe prévu est vide ou trop petit.
    import json as _json

    pg_path = ROOT / "config" / "place_groups.json"
    try:
        pg_config = _json.loads(pg_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"[build] ⚠ {pg_path} introuvable : groupes « toutes gares » absents.", file=sys.stderr)
        pg_config = {}
    build_place_groups(graph, pg_config)
    for key, members in graph.place_groups.items():
        if len(members) < 2:
            print(f"[build] ⚠ groupe « {key} » (toutes gares) : {len(members)} gare(s) trouvée(s).",
                  file=sys.stderr)
    missing = [name for spec in pg_config.values() for name in spec.get("stations", [])
               if normalize(name) not in {normalize(s.name) for s in graph.stops}]
    if missing:
        print(f"[build] ⚠ gares du fichier place_groups.json absentes du GTFS : {missing}", file=sys.stderr)

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

    # --- Feeds complémentaires (ex. TRSI Transdev, UIC nus) ---------------
    # Le GTFS national SNCF ne couvre pas les trains ZOU! Transdev
    # (Marseille <-> Nice direct, ex. 17481). On fusionne ici les feeds
    # supplémentaires : leurs stop_id sont des UIC nus (87751008) à aligner
    # sur les StopArea existantes (StopArea:OCE87751008), sinon le trip est
    # ignoré. Les trips sont des instances datées (17481@2026-08-10).
    for extra in extra_zips:
        _merge_extra_feed(graph, extra)

    # --- Feeds bus regionaux interurbains ----------------------------------
    import json as _json_mod
    bus_feeds_path = ROOT / "config" / "bus_feeds.json"
    if bus_feeds_path.exists():
        bus_cfg = _json_mod.loads(bus_feeds_path.read_text(encoding="utf-8"))
        for feed in bus_cfg.get("feeds", []):
            feed_id = feed.get("id", "unknown")
            feed_path = ROOT / "data" / "bus" / ("gtfs_%s.zip" % feed_id)
            if not feed_path.exists():
                print("[build] bus feed %s : %s introuvable, ignore." % (feed_id, feed_path), file=sys.stderr)
                continue
            _merge_bus_feed(graph, feed_path, feed.get("region", ""), feed.get("allowed_route_types"))

    # --- Index de recherche : inclure les arrêts bus ajoutés --------
    from collections import defaultdict as _dd2
    _bus_index: dict[str, set[int]] = _dd2(set)
    for idx, stop in enumerate(graph.stops):
        if stop.id.startswith(cfg.BUS_STOP_PREFIX):
            norm = normalize(stop.name)
            if norm:
                _bus_index[norm].add(idx)
            # Indexer aussi la partie avant " - " (ex: "Mende" pour "MENDE - Gare Sncf")
            if " - " in stop.name:
                prefix = normalize(stop.name.split(" - ", 1)[0])
                if prefix and len(prefix) >= 2:
                    _bus_index[prefix].add(idx)
            # Indexer aussi le premier mot si >= 3 car. (ex: "besancon" pour "BESANCON Chamars")
            parts = stop.name.split()
            if len(parts) >= 2:
                first = normalize(parts[0])
                if first and len(first) >= 3:
                    _bus_index[first].add(idx)
    for norm, idxs in _bus_index.items():
        if norm in graph.search_index:
            graph.search_index[norm] = sorted(set(graph.search_index[norm]) | idxs)
        else:
            graph.search_index[norm] = sorted(idxs)
    for alias_norm, targets in ALIASES.items():
        for target in targets:
            t_norm = normalize(target)
            if t_norm in graph.search_index:
                existing = set(graph.search_index.get(alias_norm, []))
                graph.search_index[alias_norm] = sorted(existing | set(graph.search_index[t_norm]))

    # --- Communes (recherche/résolution offline des villes sans gare) -----
    _load_communes(graph)

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

    # --- Distances PK par hop (rfn.py) ------------------------------------
    # Précalcul : pour chaque paire d'arrêts consécutifs d'un trip, distance
    # ferroviaire le long du réseau (RfnIndex.hop_km) stockée dans
    # Graph.hop_km[(trip_idx, k)]. Les hops sans ancres PK (gares étrangères,
    # etc.) restent absents : pricing.py retombe sur haversine × rail_factor.
    t0_rfn = time.perf_counter()
    rfn = RfnIndex()
    uic_by_stop: list[str | None] = [uic_from_stop_id(st.id) for st in graph.stops]
    total_hops = sum(len(t.stop_times) - 1 for t in graph.trips)
    n_pk = 0
    for tidx, trip in enumerate(graph.trips):
        sts = trip.stop_times
        for k in range(len(sts) - 1):
            d = rfn.hop_km(uic_by_stop[sts[k].stop], uic_by_stop[sts[k + 1].stop])
            if d is not None:
                graph.hop_km[(tidx, k)] = d
                n_pk += 1
    print(
        f"[build] hop_km PK : {n_pk}/{total_hops} hops couverts "
        f"({100 * n_pk / total_hops:.1f} %) en {time.perf_counter() - t0_rfn:.1f} s"
    )

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
    """Résout un nom de gare (config) vers un stop_idx, unique et normalisé.
    Les gares priment : depuis la fusion des feeds bus régionaux, une clé
    exacte comme « dijon » contient aussi des arrêts bus homonymes."""
    hits = graph.search_index.get(normalize(name), [])
    gares = [i for i in hits if not graph.stops[i].id.startswith("BusStop:")]
    if len(gares) == 1:
        return gares[0]
    if not gares and len(hits) == 1:
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

    def route_serves(short: str, long_sub: str, a_list: list[int], b: int | None) -> bool:
        """Un trip de la ligne `short` (long_name contenant `long_sub`) relie
        l'un des stops `a_list` à `b`, avec a avant b (ordre de parcours)."""
        if not a_list or b is None:
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
                if b not in stops:
                    continue
                bpos = stops.index(b)
                for a in a_list:
                    if a in stops and stops.index(a) < bpos:
                        return True
        return False

    def route_serves_rev(short: str, long_sub: str, a: int | None, b_list: list[int]) -> bool:
        """Retour : `a` avant l'un des stops `b_list`."""
        if a is None or not b_list:
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
                if a not in stops:
                    continue
                apos = stops.index(a)
                for b in b_list:
                    if b in stops and stops.index(b) > apos:
                        return True
        return False

    # K7 Paris <-> Dijon : le terminus parisien varie selon le service (Paris
    # Gare de Lyon ou Paris Bercy). On accepte l'un ou l'autre pour l'aller ;
    # le retour du soir arrive classiquement à Paris Bercy.
    if not route_serves("K7", "lyon p d", [p for p in (paris, bercy) if p is not None], dijon):
        problems.append(f"K7 Paris (Gare de Lyon ou Bercy) -> Dijon absent à la date {date}")
    if not route_serves("C11", "besan", [dijon] if dijon is not None else [], besancon):
        problems.append(f"C11 Dijon -> Besançon Viotte absent à la date {date}")
    if dijon is not None and graph.min_transfer[dijon] < DEFAULT_MIN_TRANSFER_MIN:
        problems.append(f"min_transfer(Dijon)={graph.min_transfer[dijon]} < défaut {DEFAULT_MIN_TRANSFER_MIN}")

    # Retour du soir (itinéraire réel, cf. walkthrough.md §5) : car C1/MOBIGO
    # Besançon Viotte -> Dijon, puis K7 17764 Dijon -> Paris Bercy.
    if not route_serves("C1", "dijon", [besancon] if besancon is not None else [], dijon):
        problems.append(f"C1 (MOBIGO) Besançon Viotte -> Dijon absent à la date {date}")
    if not route_serves_rev("K7", "lyon p d", dijon, [b for b in (bercy, paris) if b is not None]):
        problems.append(f"K7 Dijon -> Paris (Bercy ou Gare de Lyon) absent à la date {date} (retour du soir)")

    # Groupes « toutes gares » (§5.5) : Paris doit rester bien fourni, et
    # chaque groupe configuré doit contenir au moins 2 gares.
    paris_group = graph.place_groups.get("paris", [])
    if len(paris_group) < 5:
        problems.append(f"groupe Paris (toutes gares) trop petit : {len(paris_group)} gares")
    if paris is not None and paris not in paris_group:
        problems.append("Paris Gare de Lyon absent du groupe Paris")
    if bercy is not None and bercy not in paris_group:
        problems.append("Paris Bercy absent du groupe Paris")
    for key, members in graph.place_groups.items():
        if len(members) < 2:
            problems.append(f"groupe « {key} » (toutes gares) : {len(members)} gare(s)")

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Builder de graphe GTFS-TER (T2)")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--extra-input", type=Path, action="append", default=[],
                        help="GTFS complémentaire à fusionner (ex. TRSI Transdev). Répétable.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--interchange", type=Path, default=DEFAULT_INTERCHANGE)
    parser.add_argument("--paris-links", type=Path, default=DEFAULT_PARIS_LINKS)
    parser.add_argument("--date", default=None,
                        help="Date de vérification T2 (YYYY-MM-DD). Défaut : début de couverture du graphe construit.")
    parser.add_argument("--no-paris-links", action="store_true", help="Ne pas charger les arcs intra-Paris")
    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"[build] ERREUR : {args.input} introuvable. Lancez d'abord filter_ter.", file=sys.stderr)
        return 1

    interchange = parse_mini_yaml(args.interchange)
    links = {} if args.no_paris_links else parse_mini_yaml(args.paris_links)

    t0 = time.perf_counter()
    print(f"[build] input  : {args.input}")
    graph = _build_graph(args.input, args.extra_input, interchange, links)
    print(
        f"[build] graphe : {len(graph.stops)} gares, {len(graph.routes)} lignes, "
        f"{len(graph.trips)} trips, {graph.date_min}-{graph.date_max}"
    )

    # T2 — la vérification se fait au début de couverture du graphe construit :
    # le GTFS téléchargé peut commencer plus tard que la date historique codée
    # (ex. 2026-08-10) et les lignes K7/C11/C1 ne circulent alors pas ce jour-là.
    date = int((args.date or f"{graph.date_min//10000}-{graph.date_min%10000//100:02d}-{graph.date_min%100:02d}").replace("-", ""))
    problems = verify(graph, date)
    if problems:
        print(f"[build] ✗ Acceptation T2 ({date}) : {problems}")
        print("[build] ⚠ Sauvegarde quand même (vérification non bloquante).", file=sys.stderr)
    else:
        print(f"[build] ✓ Acceptation T2 ({date}) : K7 Paris GDL -> Dijon, C11 Dijon -> Besançon, correspondance OK")

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
