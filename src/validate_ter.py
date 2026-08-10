#!/usr/bin/env python3
"""T1 — validate_ter.py : tests de régression sur le GTFS-TER filtré.

Tests (PLAN.md, Tâche T1) :
  T1  Aucun stop_id de la blacklist (TGV/Intercités/…) dans le résultat.
  T2  La route "K7 | Paris - Lyon P D" (cas de référence, TER) est bien présente.
  T3  La route "611A | Paris - Besançon Viotte" est bien absente (c'est un TGV).
  T4  Toutes les routes conservées ont au moins un trip de la whitelist TER.
  T5  Le rapport reports/filter_report.json existe, est valide et cohérent
      avec le contenu du zip de sortie.

Usage :
    python -m src.validate_ter
    python -m src.validate_ter --input <gtfs_ter.zip>
    python -m src.validate_ter --report <filter_report.json>
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
from src.gtfs import extract_mode_from_stop_id, read_zip_file  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "data" / "ter" / "gtfs_ter.zip"
DEFAULT_REPORT = ROOT / "reports" / "filter_report.json"

GOLDEN_K7_ROUTE = ("K7", "Paris - Lyon P D")
GOLDEN_TGV_ROUTE = ("611A", "Paris - Besançon Viotte")


def _load(zip_path: Path):
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        required = config.REQUIRED_GTFS_FILES
        missing = [n for n in required if n not in names]
        if missing:
            raise AssertionError(f"Zip GTFS incomplet : fichiers manquants {missing}")
        return {
            name: read_zip_file(zf, name)
            for name in required
            if name in names
        }


def run_tests(zip_path: Path, report_path: Path) -> int:
    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        status = "OK  " if ok else "FAIL"
        print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))
        if not ok:
            failures.append(label)

    print(f"[validate] input  : {zip_path}")
    print(f"[validate] report : {report_path}")

    files = _load(zip_path)

    # --- T1 : aucune blacklist dans stop_times ------------------------------
    st_headers, stop_times = files["stop_times.txt"]
    blacklisted = Counter()
    for row in stop_times:
        mode = extract_mode_from_stop_id(row.get("stop_id", ""))
        if mode in config.BLACKLIST_MODES:
            blacklisted[mode] += 1
    check(
        "T1 aucun mode blacklisté (TGV/Intercités/ICE/Lyria/OUIGO)",
        len(blacklisted) == 0,
        dict(blacklisted) or "0 occurrence",
    )

    # --- T2 : la route K7 Paris - Lyon P D (TER) est présente -----------------
    rt_headers, routes = files["routes.txt"]
    k7 = [
        r
        for r in routes
        if r.get("route_short_name", "") == GOLDEN_K7_ROUTE[0]
        and GOLDEN_K7_ROUTE[1].replace(" ", "").lower()
        in r.get("route_long_name", "").replace(" ", "").lower()
    ]
    check(
        f"T2 route présente {GOLDEN_K7_ROUTE[0]} {GOLDEN_K7_ROUTE[1]}",
        len(k7) == 1,
        k7[0]["route_id"] if k7 else "introuvable",
    )

    # --- T3 : la route 611A (TGV) est absente -------------------------------
    tgv = [
        r
        for r in routes
        if r.get("route_short_name", "") == GOLDEN_TGV_ROUTE[0]
        and "besançon" in r.get("route_long_name", "").lower()
    ]
    check(
        f"T3 route absente {GOLDEN_TGV_ROUTE[0]} {GOLDEN_TGV_ROUTE[1]}",
        len(tgv) == 0,
        "absente" if not tgv else f"trouvée : {tgv[0]['route_id']}",
    )

    # --- T4 : chaque route conservée a un trip de la whitelist ---------------
    tr_headers, trips = files["trips.txt"]
    # recompute trip -> mode depuis le stop_times filtré (indépendant)
    trip_modes: dict[str, set] = {}
    for row in stop_times:
        mode = extract_mode_from_stop_id(row.get("stop_id", ""))
        trip_modes.setdefault(row.get("trip_id", ""), set()).add(mode)
    kept_trip_ids = {t.get("trip_id", "") for t in trips}
    trip_route: dict[str, str] = {t.get("trip_id", ""): t.get("route_id", "") for t in trips}

    bad_routes: list[str] = []
    for route in routes:
        rid = route.get("route_id", "")
        has_ter_trip = False
        for tid in kept_trip_ids:
            if trip_route.get(tid) != rid:
                continue
            modes = trip_modes.get(tid, set())
            if modes & config.WHITELIST_MODES:
                has_ter_trip = True
                break
        if not has_ter_trip:
            bad_routes.append(
                f"{route.get('route_short_name', '')} | {route.get('route_long_name', '')}"
            )
    check(
        "T4 toute route conservée a un trip TER (whitelist)",
        len(bad_routes) == 0,
        f"{len(bad_routes)} routes sans trip TER : {bad_routes[:5]}",
    )

    # --- T5 : rapport présent, valide, cohérent ------------------------------
    ok_report = report_path.exists()
    if ok_report:
        report = json.loads(report_path.read_text())
        ok_report = (
            report.get("trips_kept", 0) == len(kept_trip_ids)
            and report.get("routes_kept", 0) == len(routes)
            and report.get("stop_times_kept", 0) == len(stop_times)
        )
        detail = (
            f"trips_kept={report.get('trips_kept')}, "
            f"routes_kept={report.get('routes_kept')}, "
            f"stop_times_kept={report.get('stop_times_kept')}"
        )
    else:
        detail = "fichier introuvable"
    check(
        "T5 rapport filter_report.json présent et cohérent",
        ok_report,
        detail,
    )

    print()
    if failures:
        print(f"[validate] ÉCHEC : {len(failures)} test(s) en échec -> {failures}")
        return 1
    print("[validate] SUCCÈS : tous les tests passent.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tests de régression GTFS-TER")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)

    if not args.input.exists():
        print(
            f"[validate] ERREUR : {args.input} introuvable. "
            "Lancez d'abord : python -m src.filter_ter",
            file=sys.stderr,
        )
        return 1
    return run_tests(args.input, args.report)


if __name__ == "__main__":
    raise SystemExit(main())
