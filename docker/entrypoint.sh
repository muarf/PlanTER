#!/usr/bin/env bash
# Entrypoint du conteneur PlanTER.
# Prépare data/graph.bin si nécessaire puis lance l'API (site inclus).
#
# REFRESH_ON_START :
#   auto   (défaut) construit le graphe uniquement s'il est absent
#   force           re-télécharge et reconstruit à chaque démarrage
#   never           ne touche à rien ; échoue si data/graph.bin manque
set -euo pipefail
cd /app

MODE="${REFRESH_ON_START:-auto}"
PORT="${PORT:-8000}"

log() { echo "[entrypoint $(date '+%F %T')] $*"; }

download_bus_feeds() {
  # Les feeds bus régionaux sont optionnels : en cas d'échec d'un feed,
  # le zip précédent (ou l'absence) est toléré par build_graph.
  python - <<'PYEOF' || true
import json, sys, urllib.request
from pathlib import Path
cfg = json.loads(Path("config/bus_feeds.json").read_text(encoding="utf-8"))
for feed in cfg.get("feeds", []):
    fid, url = feed.get("id"), feed.get("url")
    if not fid or not url:
        continue
    dest = Path("data/bus") / f"gtfs_{fid}.zip"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        print(f"[bus] {fid} <- {url}", flush=True)
        req = urllib.request.Request(url, headers={"User-Agent": "ter-finder/1.0"})
        with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as f:
            f.write(r.read())
    except Exception as exc:
        print(f"[bus] ⚠ {fid} : {exc} — ignoré.", file=sys.stderr)
PYEOF
}

build_graph() {
  mkdir -p data reports
  log "téléchargement GTFS SNCF"
  python -m src.download

  log "téléchargement des feeds bus régionaux"
  download_bus_feeds

  log "filtrage TER"
  python -m src.filter_ter

  log "validation"
  python -m src.validate_ter

  EXTRA_ARGS=""
  [ -f data/ter/gtfs_trsi.zip ] && EXTRA_ARGS="--extra-input data/ter/gtfs_trsi.zip"

  cp -f data/graph.bin data/graph.bin.prev 2>/dev/null || true
  log "construction du graphe de routage (plusieurs minutes)"
  python -m src.build_graph \
    --input data/ter/gtfs_ter.zip --output data/graph.bin \
    --interchange config/interchange.yaml --paris-links config/paris_links.yaml $EXTRA_ARGS
}

case "$MODE" in
  never)
    [ -f data/graph.bin ] || { log "ERREUR : data/graph.bin manquant et REFRESH_ON_START=never"; exit 1; }
    ;;
  force)
    build_graph
    ;;
  *)
    if [ ! -f data/graph.bin ]; then
      build_graph
    else
      log "graphe existant conservé (REFRESH_ON_START=force pour reconstruire)"
    fi
    ;;
esac

UVICORN_ARGS=""
[ "${ACCESS_LOG:-0}" = "1" ] || UVICORN_ARGS="--no-access-log"

log "démarrage API sur le port ${PORT}"
exec python -m uvicorn src.api:app --host 0.0.0.0 --port "$PORT" $UVICORN_ARGS
