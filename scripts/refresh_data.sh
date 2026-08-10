#!/usr/bin/env bash
# Re-télécharge le GTFS SNCF, re-filtre TER, reconstruit le graphe puis
# redémarre le service API. En cas d'échec à n'importe quelle étape, on
# conserve les données actuelles et le service reste en l'état (exit 1).
set -uo pipefail
cd "$(dirname "$0")/.."
PY=python3
LOG() { echo "[refresh] $*"; }

LOG "1/5 téléchargement GTFS SNCF"
$PY -m src.download || { LOG "ÉCHEC download — on garde les données actuelles"; exit 1; }

LOG "2/5 filtrage TER"
$PY -m src.filter_ter || { LOG "ÉCHEC filter — on garde les données actuelles"; exit 1; }

LOG "3/5 validation"
$PY -m src.validate_ter || { LOG "ÉCHEC validation — on garde les données actuelles"; exit 1; }

LOG "4/5 build du graphe (sauvegarde de l'ancien)"
cp -f data/graph.bin data/graph.bin.prev 2>/dev/null || true
$PY -m src.build_graph \
    --input data/ter/gtfs_ter.zip --output data/graph.bin \
    --interchange config/interchange.yaml --paris-links config/paris_links.yaml \
    || { LOG "ÉCHEC build — rollback du graphe précédent"; cp -f data/graph.bin.prev data/graph.bin 2>/dev/null; exit 1; }

chown -R ubuntu:ubuntu data reports 2>/dev/null || true

LOG "5/5 redémarrage du service API"
systemctl restart ter-finder.service

COV=$($PY -c 'from pathlib import Path; from src.graph import Graph; g=Graph.load(Path("data/graph.bin")); print(f"{g.date_min} → {g.date_max}")' 2>/dev/null) || COV="?"
LOG "OK — nouvelle couverture : $COV"
