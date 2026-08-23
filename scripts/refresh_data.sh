#!/usr/bin/env bash
# Re-télécharge le GTFS SNCF, re-filtre TER, reconstruit le graphe puis
# redémarre le service API. En cas d'échec à n'importe quelle étape, on
# conserve les données actuelles et le service reste en l'état (exit 1).
#
# Suivi : journal permanent reports/refresh.log + état machine-readable
# data/refresh_status.json (couverture + statut) exposé par /v1/health.
set -uo pipefail
cd "$(dirname "$0")/.."
PY="$PWD/.venv/bin/python"
LOG() { echo "[$(date '+%F %T')] $*"; }
log_to_journal() { LOG "$*" >> reports/refresh.log 2>/dev/null; }

TELEGRAM_TOKEN="${TER_FINDER_TELEGRAM_TOKEN:-}"
TELEGRAM_CHAT_ID="${TER_FINDER_TELEGRAM_CHAT_ID:-}"

send_telegram_alert() {
  local msg="$1"
  if [ -z "$TELEGRAM_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
    LOG "Alerte Telegram ignorée (TER_FINDER_TELEGRAM_TOKEN/CHAT_ID non définis)"
    return 0
  fi
  curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
       -d "chat_id=${TELEGRAM_CHAT_ID}" \
       -d "text=${msg}" >/dev/null || true
}

write_status() { # status coverage_start coverage_end
  cat > data/refresh_status.json <<EOF
{
  "status": "$1",
  "date": "$(date '+%Y-%m-%dT%H:%M:%S%z')",
  "coverage_start": "$2",
  "coverage_end": "$3"
}
EOF
  chown ubuntu:ubuntu data/refresh_status.json 2>/dev/null || true
}

mkdir -p reports data

COV_BEFORE=$($PY -c 'from pathlib import Path; from src.graph import Graph; g=Graph.load(Path("data/graph.bin")); print(f"{g.date_min} → {g.date_max}")' 2>/dev/null) || COV_BEFORE="?"
log_to_journal "début du refresh (couverture actuelle : $COV_BEFORE)"

LOG "1/6 téléchargement GTFS SNCF"
if ! $PY -m src.download; then
  log_to_journal "ÉCHEC 1/6 download — on garde les données actuelles"
  send_telegram_alert "❌ [PlanTER] Échec 1/6 : Téléchargement du GTFS SNCF."
  write_status "error" "" ""; exit 1
fi

LOG "2/6 téléchargement des feeds bus régionaux"
BUS_FAIL=0
$PY - <<'PYEOF' || BUS_FAIL=1
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
        # un feed manquant n'est pas bloquant : le build réutilise le zip
        # précédent (build_graph ignore les feeds introuvables)
        print(f"[bus] ⚠ {fid} : {exc} — zip précédent conservé.", file=sys.stderr)
print("[bus] terminé.")
PYEOF
if [ "$BUS_FAIL" -ne 0 ]; then
  log_to_journal "ALERTE 2/6 : échec téléchargement feeds bus (zips précédents conservés)"
fi

LOG "3/6 filtrage TER"
if ! $PY -m src.filter_ter; then
  log_to_journal "ÉCHEC 3/6 filter — on garde les données actuelles"
  send_telegram_alert "❌ [PlanTER] Échec 3/6 : Filtrage du GTFS TER."
  write_status "error" "" ""; exit 1
fi

LOG "4/6 validation"
if ! $PY -m src.validate_ter; then
  log_to_journal "ÉCHEC 4/6 validation — on garde les données actuelles"
  send_telegram_alert "❌ [PlanTER] Échec 4/6 : Validation du GTFS-TER."
  write_status "error" "" ""; exit 1
fi

LOG "5/6 build du graphe (sauvegarde de l'ancien)"
cp -f data/graph.bin data/graph.bin.prev 2>/dev/null || true
EXTRA_ARGS=""
[ -f data/ter/gtfs_trsi.zip ] && EXTRA_ARGS="--extra-input data/ter/gtfs_trsi.zip"
if ! $PY -m src.build_graph \
    --input data/ter/gtfs_ter.zip --output data/graph.bin \
    --interchange config/interchange.yaml --paris-links config/paris_links.yaml $EXTRA_ARGS; then
  log_to_journal "ÉCHEC 5/6 build — rollback du graphe précédent"
  send_telegram_alert "❌ [PlanTER] Échec 5/6 : Construction du graphe de routage."
  cp -f data/graph.bin.prev data/graph.bin 2>/dev/null || true
  write_status "error" "" ""; exit 1
fi

chown -R ubuntu:ubuntu data reports 2>/dev/null || true

LOG "6/6 redémarrage du service API"
START_BEFORE=$(systemctl show -p ActiveEnterTimestamp --value ter-finder-pricing.service 2>/dev/null || true)
if ! systemctl restart ter-finder-pricing.service; then
  log_to_journal "ALERTE : systemctl restart ter-finder-pricing.service a échoué"
  send_telegram_alert "❌ [PlanTER] Échec 6/6 : Redémarrage du service API via systemd."
  write_status "degraded" "" ""
  exit 1
fi

# Health-check : l'API doit répondre ET avoir effectivement redémarré (sinon
# l'ancien graphe reste servi en mémoire).
OK=""
for i in 1 2 3 4 5; do
  sleep 2
  START_AFTER=$(systemctl show -p ActiveEnterTimestamp --value ter-finder-pricing.service 2>/dev/null || true)
  if [ -n "$START_AFTER" ] && [ "$START_AFTER" != "$START_BEFORE" ] \
     && curl -sf "http://127.0.0.1:8001/v1/health" >/dev/null 2>&1; then
    OK=1; break
  fi
done
if [ -z "$OK" ]; then
  log_to_journal "ALERTE : service API injoignable ou non redémarré après refresh"
  send_telegram_alert "❌ [PlanTER] Échec : Service API injoignable ou non redémarré après refresh des données."
  write_status "degraded" "" ""
  exit 1
fi

COV=$($PY -c 'from pathlib import Path; from src.graph import Graph; g=Graph.load(Path("data/graph.bin")); print(f"{g.date_min} → {g.date_max}")' 2>/dev/null) || COV="?"
LOG "OK — nouvelle couverture : $COV"
log_to_journal "OK — refresh réussi : $COV_BEFORE → $COV"
write_status "ok" "${COV% → *}" "${COV#*→ }"
