#!/usr/bin/env python3
import urllib.request
import urllib.parse
import json
import sys
import os
import datetime

TELEGRAM_TOKEN = os.environ.get("TER_FINDER_TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TER_FINDER_TELEGRAM_CHAT_ID", "")
STATE_FILE = "/tmp/ter_finder_alert_sent"

def send_alert(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram alert skipped (TER_FINDER_TELEGRAM_TOKEN/CHAT_ID not set)", file=sys.stderr)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": msg}).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as response:
            response.read()
    except Exception as e:
        print(f"Failed to send telegram alert: {e}", file=sys.stderr)

def notify(msg, is_error=True):
    if is_error:
        if os.path.exists(STATE_FILE):
            # Already alerted, don't spam
            return
        try:
            with open(STATE_FILE, "w") as f:
                f.write(msg)
        except Exception:
            pass
        send_alert(msg)
    else:
        if os.path.exists(STATE_FILE):
            try:
                os.remove(STATE_FILE)
            except Exception:
                pass
            send_alert("✅ [PlanTER] L'API est de nouveau opérationnelle et saine.")

def check_health():
    # We check the local API endpoint (directly on the loopback) to bypass external network issues if any,
    # or the public URL to ensure Nginx + TLS works. Checking the local one is most reliable to check the app process.
    # Let's check both or check the local one. Let's check the local one http://127.0.0.1:8000/v1/health.
    url = "http://127.0.0.1:8000/v1/health"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PlanTER-Monitor"})
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status != 200:
                notify(f"❌ [PlanTER] L'API renvoie un code HTTP {response.status} sur {url} !")
                return
            content = response.read().decode("utf-8")
    except Exception as e:
        notify(f"❌ [PlanTER] L'API est injoignable sur {url} !\nErreur : {e}")
        return

    try:
        data = json.loads(content)
    except Exception as e:
        notify(f"❌ [PlanTER] Impossible de parser la réponse JSON de {url} !\nErreur : {e}")
        return

    # Check status
    if data.get("status") != "ok":
        notify(f"⚠️ [PlanTER] Statut anormal de l'API : {data.get('status')}")
        return

    # Check last refresh status
    last_refresh = data.get("last_refresh")
    if last_refresh:
        status = last_refresh.get("status")
        if status in ("error", "degraded"):
            notify(f"⚠️ [PlanTER] Le dernier refresh hebdomadaire a échoué (statut: {status}).")
            return

    # Check realtime
    realtime = data.get("realtime")
    if realtime:
        if not realtime.get("polling"):
            notify("⚠️ [PlanTER] Le polling temps réel GTFS-RT n'est pas actif !")
            return
        elif not realtime.get("fresh"):
            notify(f"⚠️ [PlanTER] Le flux temps réel GTFS-RT n'est plus frais (âge: {realtime.get('age_s')}s) !")
            return

        alerts = realtime.get("alerts")
        if alerts and not alerts.get("fresh") and alerts.get("count", 0) > 0:
            notify(f"⚠️ [PlanTER] Le flux d'alertes de service n'est plus frais (âge: {alerts.get('age_s')}s) !")
            return
            
    # Check coverage end date
    coverage_end = data.get("coverage_end")
    if coverage_end:
        try:
            end_date = datetime.date.fromisoformat(coverage_end)
            days_left = (end_date - datetime.date.today()).days
            if days_left <= 7:
                notify(f"⚠️ [PlanTER] Les données GTFS expirent bientôt ! Fin de couverture : {coverage_end} (dans {days_left} jours).")
                return
        except Exception:
            pass

    # If all is well and we had a previous alert, clear it and notify recovery
    notify("", is_error=False)

if __name__ == "__main__":
    check_health()
