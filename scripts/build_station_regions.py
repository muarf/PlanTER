#!/usr/bin/env python3
"""T12 — build_station_regions.py : carte gare -> région organisatrice.

Télécharge (ou lit en cache) l'export SNCF Open Data « liste-des-gares »,
qui contient pour chaque gare le code UIC (à 7 chiffres), le libellé, la
commune et le département. Le département est converti en région
administrative (mapping INSEE), puis le tout est écrit dans
`config/station_regions.json`, indexé par code UIC.

Le fichier est commité : le runtime n'a besoin d'aucun appel réseau.

Usage :
    python -m scripts.build_station_regions            # télécharge l'export
    python -m scripts.build_station_regions --input /tmp/gares.json
    python -m scripts.build_station_regions --graph data/graph.bin  # rapport couverture
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "config" / "station_regions.json"
API_URL = "https://ressources.data.sncf.com/api/records/1.0/search/"
DATASET = "liste-des-gares"

# Département (nom, champ `departemen` de l'export SNCF) -> région administrative.
DEPT_TO_REGION: dict[str, str] = {
    # Auvergne-Rhône-Alpes
    "AIN": "Auvergne-Rhône-Alpes", "ALLIER": "Auvergne-Rhône-Alpes",
    "ARDECHE": "Auvergne-Rhône-Alpes", "CANTAL": "Auvergne-Rhône-Alpes",
    "DROME": "Auvergne-Rhône-Alpes", "HAUTE-LOIRE": "Auvergne-Rhône-Alpes",
    "HAUTE-SAVOIE": "Auvergne-Rhône-Alpes", "ISERE": "Auvergne-Rhône-Alpes",
    "LOIRE": "Auvergne-Rhône-Alpes", "PUY-DE-DOME": "Auvergne-Rhône-Alpes",
    "RHONE": "Auvergne-Rhône-Alpes", "SAVOIE": "Auvergne-Rhône-Alpes",
    # Bourgogne-Franche-Comté
    "COTE-D'OR": "Bourgogne-Franche-Comté", "DOUBS": "Bourgogne-Franche-Comté",
    "HAUTE-SAONE": "Bourgogne-Franche-Comté", "JURA": "Bourgogne-Franche-Comté",
    "NIEVRE": "Bourgogne-Franche-Comté", "SAONE-ET-LOIRE": "Bourgogne-Franche-Comté",
    "TERRITOIRE-DE-BELFORT": "Bourgogne-Franche-Comté", "YONNE": "Bourgogne-Franche-Comté",
    # Bretagne
    "COTES-D'ARMOR": "Bretagne", "FINISTERE": "Bretagne",
    "ILLE-ET-VILAINE": "Bretagne", "MORBIHAN": "Bretagne",
    # Centre-Val de Loire
    "CHER": "Centre-Val de Loire", "EURE-ET-LOIR": "Centre-Val de Loire",
    "INDRE": "Centre-Val de Loire", "INDRE-ET-LOIRE": "Centre-Val de Loire",
    "LOIR-ET-CHER": "Centre-Val de Loire", "LOIRET": "Centre-Val de Loire",
    # Corse
    "CORSE-DU-SUD": "Corse", "HAUTE-CORSE": "Corse",
    # Grand Est
    "ARDENNES": "Grand Est", "AUBE": "Grand Est", "BAS-RHIN": "Grand Est",
    "HAUT-RHIN": "Grand Est", "HAUTE-MARNE": "Grand Est", "MARNE": "Grand Est",
    "MEURTHE-ET-MOSELLE": "Grand Est", "MEUSE": "Grand Est",
    "MOSELLE": "Grand Est", "VOSGES": "Grand Est",
    # Hauts-de-France
    "AISNE": "Hauts-de-France", "NORD": "Hauts-de-France",
    "OISE": "Hauts-de-France", "PAS-DE-CALAIS": "Hauts-de-France",
    "SOMME": "Hauts-de-France",
    # Île-de-France
    "ESSONNE": "Île-de-France", "HAUTS-DE-SEINE": "Île-de-France",
    "PARIS": "Île-de-France", "SEINE-ET-MARNE": "Île-de-France",
    "SEINE-SAINT-DENIS": "Île-de-France", "VAL-D'OISE": "Île-de-France",
    "VAL-DE-MARNE": "Île-de-France", "YVELINES": "Île-de-France",
    # Normandie
    "CALVADOS": "Normandie", "EURE": "Normandie", "MANCHE": "Normandie",
    "ORNE": "Normandie", "SEINE-MARITIME": "Normandie",
    # Nouvelle-Aquitaine
    "CHARENTE": "Nouvelle-Aquitaine", "CHARENTE-MARITIME": "Nouvelle-Aquitaine",
    "CORREZE": "Nouvelle-Aquitaine", "CREUSE": "Nouvelle-Aquitaine",
    "DEUX-SEVRES": "Nouvelle-Aquitaine", "DORDOGNE": "Nouvelle-Aquitaine",
    "GIRONDE": "Nouvelle-Aquitaine", "HAUTE-VIENNE": "Nouvelle-Aquitaine",
    "LANDES": "Nouvelle-Aquitaine", "LOT-ET-GARONNE": "Nouvelle-Aquitaine",
    "PYRENEES-ATLANTIQUES": "Nouvelle-Aquitaine", "VIENNE": "Nouvelle-Aquitaine",
    # Occitanie
    "ARIEGE": "Occitanie", "AUDE": "Occitanie", "AVEYRON": "Occitanie",
    "GARD": "Occitanie", "GERS": "Occitanie", "HAUTE-GARONNE": "Occitanie",
    "HAUTES-PYRENEES": "Occitanie", "HERAULT": "Occitanie", "LOT": "Occitanie",
    "LOZERE": "Occitanie", "PYRENEES-ORIENTALES": "Occitanie",
    "TARN": "Occitanie", "TARN-ET-GARONNE": "Occitanie",
    # Pays de la Loire
    "LOIRE-ATLANTIQUE": "Pays de la Loire", "MAINE-ET-LOIRE": "Pays de la Loire",
    "MAYENNE": "Pays de la Loire", "SARTHE": "Pays de la Loire",
    "VENDEE": "Pays de la Loire",
    # Provence-Alpes-Côte d'Azur
    "ALPES-DE-HAUTE-PROVENCE": "Provence-Alpes-Côte d'Azur",
    "ALPES-MARITIMES": "Provence-Alpes-Côte d'Azur",
    "BOUCHES-DU-RHONE": "Provence-Alpes-Côte d'Azur",
    "HAUTES-ALPES": "Provence-Alpes-Côte d'Azur",
    "VAR": "Provence-Alpes-Côte d'Azur", "VAUCLUSE": "Provence-Alpes-Côte d'Azur",
}


def fetch() -> dict:
    """Télécharge l'export complet « liste-des-gares » (rows=7000 couvre les 6469)."""
    resp = requests.get(API_URL, params={"dataset": DATASET, "rows": 7000, "format": "json"}, timeout=120)
    resp.raise_for_status()
    return resp.json()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build(records: list[dict]) -> dict[str, dict]:
    """records -> {uic8: {"name", "dept", "region"}} (dédupliqué par uic)."""
    out: dict[str, dict] = {}
    for rec in records:
        f = rec.get("fields") or {}
        uic = str(f.get("code_uic", "")).strip()
        if not uic:
            continue
        dept = (f.get("departemen") or "").strip()
        region = DEPT_TO_REGION.get(dept.upper())
        if region is None:
            print(f"  [build] ⚠ département inconnu '{dept}' (uic {uic})", file=sys.stderr)
        out[uic] = {
            "name": (f.get("libelle") or "").strip(),
            "dept": dept,
            "region": region or "INCONNUE",
        }
    return out


def coverage_report(graph_path: Path, regions_file: Path) -> None:
    """% des gares du graphe couvertes par la carte des régions."""
    sys.path.insert(0, str(ROOT))
    from src.graph import Graph  # noqa: PLC0415

    regions = json.loads(regions_file.read_text(encoding="utf-8"))
    g = Graph.load(graph_path)
    known = unknown = 0
    by_region: dict[str, int] = {}
    for stop in g.stops:
        uid = stop.id.removeprefix("StopArea:")
        if uid.startswith("OCE"):
            uic = uid[3:]
        elif uid.isdigit():
            uic = uid
        else:
            unknown += 1
            continue
        r = regions.get(uic, {}).get("region")
        if r:
            known += 1
            by_region[r] = by_region.get(r, 0) + 1
        else:
            unknown += 1
    print(f"gares du graphe : {len(g.stops)} — régions connues : {known} ({100 * known / max(len(g.stops), 1):.1f} %)")
    for r, n in sorted(by_region.items(), key=lambda it: -it[1]):
        print(f"  {r:28s} {n}")
    if unknown:
        print(f"  inconnues : {unknown}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Génère config/station_regions.json")
    parser.add_argument("--input", type=Path, help="JSON liste-des-gares en cache (sinon téléchargé)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--graph", type=Path, help="data/graph.bin : imprime un rapport de couverture")
    args = parser.parse_args(argv)

    data = load(args.input) if args.input else fetch()
    stations = build(data.get("records", []))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(stations, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
        encoding="utf-8",
    )
    print(f"[regions] {len(stations)} gares -> {args.out}")

    if args.graph:
        coverage_report(args.graph, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
