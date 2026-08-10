#!/usr/bin/env python3
"""T1 — filter_ter.py : filtre le GTFS SNCF pour ne garder que l'offre TER.

Principe (PLAN.md §4) : le mode commercial de chaque course est encodé dans
les stop_id de stop_times.txt :
    StopPoint:OCE<MODE>-<code UIC>
On construit la table course -> mode, on ne conserve que les courses dont le
mode appartient à la whitelist (OCETrain TER, OCECar TER, OCETramTrain,
OCETrain), puis on propage le filtre à tous les fichiers GTFS.

Usage :
    python -m src.filter_ter                        # depuis data/raw/latest.zip
    python -m src.filter_ter --input <gtfs.zip>
    python -m src.filter_ter --output <dir>
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.gtfs import (  # noqa: E402
    GTFSRows,
    extract_mode_from_stop_id,
    read_zip_file,
    write_csv_rows,
    write_gtfs_zip,
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "data" / "raw" / "latest.zip"
DEFAULT_TER_DIR = ROOT / "data" / "ter"
DEFAULT_REPORT_DIR = ROOT / "reports"


def trip_modes_from_stop_times(rows: GTFSRows) -> tuple[dict[str, str], Counter]:
    """Construit trip_id -> mode commercial depuis stop_times.

    Pour chaque course, on collecte les modes de tous ses arrêts. En cas de
    conflit (théoriquement impossible), on garde le mode de l'arrêt de plus
    petit stop_sequence et on le logue.

    Retourne (trip_id -> mode, compteur des modes inconnus/non reconnus).
    """
    trip_modes: dict[str, str] = {}
    per_trip: dict[str, Counter] = {}
    unknown: Counter = Counter()

    for row in rows:
        trip_id = row.get("trip_id", "")
        stop_id = row.get("stop_id", "")
        mode = extract_mode_from_stop_id(stop_id)
        if mode is None:
            unknown[stop_id] += 1
            continue
        per_trip.setdefault(trip_id, Counter())[mode] += 1

    for trip_id, counts in per_trip.items():
        most_common = counts.most_common()
        if len(most_common) > 1:
            print(
                f"[filter] AVERTISSEMENT : course {trip_id} avec plusieurs "
                f"modes {dict(counts)} — on garde {most_common[0][0]}"
            )
        trip_modes[trip_id] = most_common[0][0]

    return trip_modes, unknown


def filter_gtfs_ter(input_zip: Path, ter_dir: Path, report_dir: Path) -> dict:
    """Filtre le GTFS et écrit data/ter/gtfs_ter.zip + le rapport JSON."""
    ter_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    stats: dict = {}
    with zipfile.ZipFile(input_zip) as zf:
        # 1) stop_times : table trip_id -> mode
        st_headers, stop_times = read_zip_file(zf, "stop_times.txt")
        stats["input_stop_times"] = len(stop_times)

        trip_modes, unknown_stops = trip_modes_from_stop_times(stop_times)
        stats["unknown_stop_ids"] = len(unknown_stops)

        # 2) décision par trip
        mode_counts = Counter(trip_modes.values())
        stats["modes_input"] = dict(mode_counts)

        allowed_trips = {
            tid for tid, m in trip_modes.items() if m in config.WHITELIST_MODES
        }
        stats["trips_input"] = len(trip_modes)
        stats["trips_kept"] = len(allowed_trips)
        stats["modes_kept"] = dict(
            Counter(m for tid, m in trip_modes.items() if tid in allowed_trips)
        )

        # 3) trips : ne garder que les trips conservés (+ service_id + route_id)
        tr_headers, trips = read_zip_file(zf, "trips.txt")
        stats["trips_rows"] = len(trips)
        kept_trips = [t for t in trips if t.get("trip_id", "") in allowed_trips]
        kept_service_ids = {t.get("service_id", "") for t in kept_trips}
        kept_route_ids = {t.get("route_id", "") for t in kept_trips}

        # 4) routes : ne garder que les routes ayant au moins un trip conservé
        rt_headers, routes = read_zip_file(zf, "routes.txt")
        stats["routes_input"] = len(routes)
        kept_routes = [r for r in routes if r.get("route_id", "") in kept_route_ids]
        stats["routes_kept"] = len(kept_routes)
        excluded_routes = [
            r for r in routes if r.get("route_id", "") not in kept_route_ids
        ]
        stats["excluded_routes"] = [
            {
                "route_id": r.get("route_id", ""),
                "short_name": r.get("route_short_name", ""),
                "long_name": r.get("route_long_name", ""),
            }
            for r in excluded_routes
        ]

        # 5) stop_times : ne garder que les lignes des trips conservés
        kept_stop_times = [
            s for s in stop_times if s.get("trip_id", "") in allowed_trips
        ]
        stats["stop_times_kept"] = len(kept_stop_times)
        kept_stop_ids = {s.get("stop_id", "") for s in kept_stop_times}

        # 6) stops : garder les StopPoint conservés + leurs StopArea parentes
        stp_headers, stops = read_zip_file(zf, "stops.txt")
        stats["stops_input"] = len(stops)
        by_id = {s.get("stop_id", ""): s for s in stops}
        kept_stops: dict[str, dict] = {}
        for sid in kept_stop_ids:
            if sid in by_id:
                kept_stops[sid] = by_id[sid]
                parent = by_id[sid].get("parent_station", "")
                if parent and parent in by_id:
                    kept_stops[parent] = by_id[parent]
        kept_stops_list = [kept_stops[k] for k in sorted(kept_stops)]
        stats["stops_kept"] = len(kept_stops_list)

        # 7) calendar_dates : ne garder que les service_id conservés
        cd_headers, calendar_dates = read_zip_file(zf, "calendar_dates.txt")
        stats["calendar_dates_input"] = len(calendar_dates)
        kept_calendar_dates = [
            c for c in calendar_dates if c.get("service_id", "") in kept_service_ids
        ]
        stats["calendar_dates_kept"] = len(kept_calendar_dates)

        # 8) assemblage du GTFS-TER
        out_files = {
            "agency.txt": zf.read("agency.txt").decode("utf-8-sig"),
            "feed_info.txt": zf.read("feed_info.txt").decode("utf-8-sig"),
            "routes.txt": write_csv_rows(rt_headers, kept_routes),
            "trips.txt": write_csv_rows(tr_headers, kept_trips),
            "stop_times.txt": write_csv_rows(st_headers, kept_stop_times),
            "stops.txt": write_csv_rows(stp_headers, kept_stops_list),
            "calendar_dates.txt": write_csv_rows(cd_headers, kept_calendar_dates),
        }

        output_path = ter_dir / "gtfs_ter.zip"
        write_gtfs_zip(output_path, out_files)
        stats["output_path"] = str(output_path)
        stats["output_size_bytes"] = output_path.stat().st_size

    # 10) rapport
    report_path = report_dir / "filter_report.json"
    report_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2))
    stats["report_path"] = str(report_path)

    print(
        f"[filter] OK : {stats['trips_input']} trips -> {stats['trips_kept']} "
        f"conservés ({stats['routes_input']} routes -> {stats['routes_kept']})"
    )
    print(f"[filter] sortie  -> {output_path}")
    print(f"[filter] rapport -> {report_path}")
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Filtre le GTFS SNCF : TER uniquement")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_TER_DIR)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    args = parser.parse_args(argv)

    if not args.input.exists():
        print(
            f"[filter] ERREUR : {args.input} introuvable. "
            "Lancez d'abord : python -m src.download",
            file=sys.stderr,
        )
        return 1
    try:
        filter_gtfs_ter(args.input, args.output_dir, args.report_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"[filter] ERREUR : {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
