"""T5 — api.py : API REST FastAPI (§7 PLAN.md).

Endpoints (§7.2) :
- GET /v1/stations/search?q=&limit=  → autocomplete gares
- GET /v1/journeys                  → itinéraires TER (moteur McRAPTOR T3)
- GET /v1/health                    → état

Contrats d'erreur (§7.3) : gare introuvable → 404 STATION_NOT_FOUND (avec
suggestions), date/heure/paramètres invalides → 400, aucun trajet → 200 avec
`{ journeys: [] }`.

Démarrage :  uvicorn src.api:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Scope, Receive, Send

from src.graph import Graph, ALIASES
from src.raptor import RaptorEngine
from src import gtfs_rt, trainline

DEFAULT_GRAPH = Path(__file__).resolve().parents[1] / "data" / "graph.bin"
WEB_DIR = Path(__file__).resolve().parents[1] / "web"

# Route d'une ouverture/départ de tous les jours de l'application.
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_COORDS_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")

_engine: RaptorEngine | None = None
_poller: gtfs_rt.RealtimePoller | None = None


def get_engine(graph_path: Path = DEFAULT_GRAPH) -> RaptorEngine:
    """Charge le graphe une seule fois (module-level), puis le partage."""
    global _engine, _poller
    if _engine is None:
        _engine = RaptorEngine(Graph.load(graph_path))
        # T8 — temps réel GTFS-RT en arrière-plan ; l'échec de démarrage ne
        # bloque pas l'API (le poller retentera au prochain intervalle).
        try:
            _poller = gtfs_rt.RealtimePoller(_engine.graph)
            _poller.start()
        except Exception:
            _poller = None
    return _engine


def _lifespan(app: FastAPI):
    yield
    # Arrêt propre du poller GTFS-RT à la fermeture (uvicorn --reload).
    global _poller
    if _poller is not None:
        _poller.stop()
        _poller = None


# ---------------------------------------------------------------- helpers
def _error(status: int, code: str, message: str, suggestions: list[str] | None = None) -> HTTPException:
    return HTTPException(
        status_code=status, detail={"error": {"code": code, "message": message, "suggestions": suggestions or []}}
    )


def _parse_date(value: str, g: Graph) -> _dt.date:
    try:
        d = _dt.date.fromisoformat(value)
    except ValueError:
        raise _error(400, "INVALID_DATE", f"date invalide : {value!r} (attendu YYYY-MM-DD)")
    ymd = int(d.strftime("%Y%m%d"))
    if not (g.date_min <= ymd <= g.date_max):
        raise _error(
            400,
            "INVALID_DATE",
            f"date hors plage de données : {value} (couverture {g.date_min} → {g.date_max})",
        )
    return d


def _parse_time(value: str) -> int:
    m = _HHMM_RE.match(value)
    if not m:
        raise _error(400, "INVALID_TIME", f"heure invalide : {value!r} (attendu HH:MM)")
    return int(m.group(1)) * 60 + int(m.group(2))


def _nearest_stops(g: Graph, lat: float, lon: float, n: int = 3) -> list[int]:
    """Indices des gares les plus proches des coordonnées (distance euclidienne
    sur latitude/longitude, suffisante pour une recherche de proximité)."""
    scored = []
    for i, s in enumerate(g.stops):
        d = (s.lat - lat) ** 2 + (s.lon - lon) ** 2
        scored.append((d, i))
    scored.sort(key=lambda it: it[0])
    return [i for _, i in scored[:n]]


def _resolve_place(g: Graph, value: str) -> list[int]:
    """stop_area_id exact (avec ou sans préfixe « StopArea: »), coordonnées
    « lat,lon », nom/groupe ou autocomplete."""
    value = value.strip()
    if value in g.stop_index:
        return [g.stop_index[value]]
    if value.startswith("OCE") and f"StopArea:{value}" in g.stop_index:
        return [g.stop_index[f"StopArea:{value}"]]
    m = _COORDS_RE.match(value)
    if m:
        return _nearest_stops(g, float(m.group(1)), float(m.group(2)))
    idxs = g.resolve_place(value)
    if not idxs:
        suggestions = [name for _, name in g.find_stops(value)[:3]]
        raise _error(
            404,
            "STATION_NOT_FOUND",
            f"gare ou lieu introuvable : {value!r}",
            suggestions=suggestions,
        )
    return idxs


def _stop_aliases(g: Graph, stop_idx: int) -> list[str]:
    """Alias d'usage qui désignent cette gare (miroir du tableau ALIASES)."""
    name = g.stops[stop_idx].name
    norm = _normalize(name)
    out = []
    for alias, targets in ALIASES.items():
        if norm in (_normalize(t) for t in targets):
            out.append(alias)
    return sorted(out)


def _normalize(text: str) -> str:
    import unicodedata

    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return " ".join(text.lower().replace("-", " ").split())


def _bare(stop_area_id: str) -> str:
    """§6.5/§7.2 : l'API expose les ids sans le préfixe interne « StopArea: »."""
    return stop_area_id.removeprefix("StopArea:")


def _bare_journey(j: dict) -> dict:
    for leg in j["legs"]:
        leg["from"]["stop_area_id"] = _bare(leg["from"]["stop_area_id"])
        leg["to"]["stop_area_id"] = _bare(leg["to"]["stop_area_id"])
    return j


# T8 — une correspondance est « risquée » si le train entrant est en retard et
# que la marge réelle jusqu'au départ suivant (marche comprise) reste faible.
CONNECTION_SLACK_MIN = 5  # minutes


def _connection_risks(jd: dict) -> list[dict]:
    """Correspondances à risque sur un trajet temps réel (T8).

    Une correspondance manquée devient possible quand le leg entrant est en
    retard (`delay_min > 0`) et que l'écart réel au départ du leg suivant est
    trop court pour absorber une aggravation du retard. On signale la gare,
    les lignes concernées, le retard et la marge réelle restante."""
    rail = [l for l in jd["legs"] if l["type"] != "walk"]
    risks = []
    for a, b in zip(rail, rail[1:]):
        if a["delay_min"] <= 0:
            continue
        margin = _minutes(b["from"]["time"]) - _minutes(a["to"]["time"])
        # Le retard a déjà consommé une partie de la marge planifiée : si la
        # marge restante (au-delà du retard) est inférieure au seuil, une
        # aggravation fait rater la correspondance.
        if margin - a["delay_min"] < CONNECTION_SLACK_MIN:
            risks.append(
                {
                    "at_station": b["from"]["name"],
                    "from_line": a["line"],
                    "to_line": b["line"],
                    "delay_min": a["delay_min"],
                    "margin_min": margin,
                }
            )
    return risks


def _minutes(iso: str) -> int:
    """ISO « YYYY-MM-DDTHH:MM:SS+hh:mm » → minutes depuis l'époque (legs de nuit
    et changements de jour gérés naturellement par le timestamp absolu)."""
    return int(_dt.datetime.fromisoformat(iso).timestamp()) // 60


# ---------------------------------------------------------------- application
app = FastAPI(
    title="TER Finder API",
    description="Recherche d'itinéraires 100% TER (moteur McRAPTOR).",
    version="0.1.0",
    lifespan=_lifespan,
)


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request, exc: StarletteHTTPException) -> JSONResponse:
    # §7.3 : le corps d'erreur est exactement `{ error: {...} }` (sans wrapper
    # FastAPI `{"detail": ...}`).
    return JSONResponse(status_code=exc.status_code, content=exc.detail)


@app.get("/v1/health", tags=["santé"])
def health() -> dict:
    g = get_engine().graph
    return {
        "status": "ok",
        "data_date": f"{g.date_max // 10000:04d}-{g.date_max % 10000 // 100:02d}-{g.date_max % 100:02d}",
        "coverage_start": f"{g.date_min // 10000:04d}-{g.date_min % 10000 // 100:02d}-{g.date_min % 100:02d}",
        "coverage_end": f"{g.date_max // 10000:04d}-{g.date_max % 10000 // 100:02d}-{g.date_max % 100:02d}",
        "stations": len(g.stops),
        "last_refresh": _last_refresh(),
        "realtime": _realtime_health(),
    }


def _realtime_health() -> dict | None:
    """T8 — état des flux GTFS-RT : fraîcheur (âge en s), nombre de trips
    retardés/supprimés, alertes, horodatages. None si le poller n'est pas actif."""
    global _poller
    if _poller is None:
        return None
    feed = _poller.snapshot()
    alerts = _poller.alerts_snapshot()
    return {
        "polling": feed.fetched_at > 0,
        "age_s": feed.age_s(),
        "fresh": feed.is_fresh,
        "delayed_trips": len(feed.trip_delays),
        "cancelled_trips": len(feed.cancelled),
        "gtfs_rt_timestamp": _dt.datetime.fromtimestamp(feed.updated_at, tz=_dt.timezone.utc).isoformat()
        if feed.updated_at
        else None,
        "alerts": {
            "count": len(alerts.alerts),
            "fresh": 0 <= alerts.age_s() <= 6 * 60 if alerts.fetched_at else False,
            "age_s": alerts.age_s(),
        },
    }


def _last_refresh() -> dict | None:
    """Dernier refresh hebdomadaire (scripts/refresh_data.sh) : statut, date,
    couverture — None si jamais exécuté. Journal lisible par l'humain dans
    reports/refresh.log."""
    f = Path(__file__).resolve().parents[1] / "data" / "refresh_status.json"
    try:
        return json.loads(f.read_text())
    except (FileNotFoundError, ValueError):
        return None


@app.get("/v1/stations/search", tags=["gares"])
def stations_search(
    q: str = Query(..., description="Texte de recherche (nom, alias, partie de nom)"),
    limit: int = Query(10, ge=1, le=50),
) -> dict:
    g = get_engine().graph
    hits = g.find_stops(q)[:limit]
    return {
        "stations": [
            {
                "stop_area_id": _bare(g.stops[idx].id),
                "name": g.stops[idx].name,
                "lat": g.stops[idx].lat,
                "lon": g.stops[idx].lon,
                "aliases": _stop_aliases(g, idx),
                "trainline_slug": trainline.slug_for(g.stops[idx].id),
            }
            for idx, _ in hits
        ]
    }


@app.get("/v1/journeys", tags=["itinéraires"])
def journeys(
    from_: str = Query(..., alias="from", description="stop_area_id, « lat,lon » ou nom de gare/groupe"),
    to: str = Query(..., description="stop_area_id, « lat,lon » ou nom de gare/groupe"),
    date: str = Query(..., description="Date du voyage (YYYY-MM-DD)"),
    time: str = Query(..., description="Heure de référence (HH:MM)"),
    datetime_represents: str = Query("departure", pattern="^(departure|arrival)$"),
    max_transfers: int = Query(6, ge=0, le=6),
    vehicle: str = Query("all", pattern="^(all|train_only)$"),
    count: int = Query(5, ge=1, le=20),
    sort: str = Query("departure", pattern="^(departure|duration)$",
                      description="Tri des résultats : departure (heure de départ, défaut) ou duration (le plus court d'abord)"),
    use_realtime: bool = Query(False, description="T8 — appliquer les retards/suppressions GTFS-RT"),
) -> dict:
    engine = get_engine()
    g = engine.graph
    d = _parse_date(date, g)
    t0 = _parse_time(time)

    origins = _resolve_place(g, from_)
    dests = _resolve_place(g, to)

    # T8 — le moteur consomme un instantané du poller (graphe et temps réel
    # sont partagés en lecture seule via snapshot()).
    realtime = _poller.snapshot() if (use_realtime and _poller is not None) else None

    if datetime_represents == "arrival":
        journeys = engine.arrive_by(int(d.strftime("%Y%m%d")), origins, dests, t0, max_transfers, vehicle, realtime)
    else:
        journeys = engine.depart_after(int(d.strftime("%Y%m%d")), origins, dests, t0, max_transfers, vehicle, realtime)

    # Tri : par départ (défaut) ou par durée (« le plus court de la journée » :
    # le moteur couvre un horizon de 36 h, le plus court figure donc parmi les
    # solutions Pareto si on trie par durée avant la troncature à `count`).
    if sort == "duration":
        journeys = sorted(journeys, key=lambda j: (j.duration_min, j.departure, j.arrival))[:count]
    else:
        journeys = sorted(journeys, key=lambda j: (j.departure, j.arrival))[:count]

    # T9 — liens Trainline PAR BILLET (PoC monétisation). Les billets TER se
    # vendent séparément par segment : une URL de réservation est générée pour
    # chaque leg ferroviaire (train/car/tram), avec sa propre date et heure de
    # départ (les legs peuvent passer minuit). Les marches inter-gares n'ont
    # pas de billet.
    def _iso(leg):
        """(date, heure) d'un leg à partir de son `from.time` ISO."""
        t = (leg.get("from") or {}).get("time") or ""
        return t[:10], (t[11:16] or None)

    out = []
    # T8 — alertes Service Alerts : pertinentes si une gare ou un train du trajet
    # est touché. On expose au plus 3 (les alertes générales restent dans health).
    alerts_feed = _poller.alerts_snapshot() if (_poller is not None) else None
    for j in journeys:
        jd = _bare_journey(j.to_json(d))
        bookable = 0
        for leg in jd["legs"]:
            leg["booking"] = None
            if leg["type"] == "walk":
                continue
            leg_date, leg_time = _iso(leg)
            if not leg_date:
                continue
            url = trainline.booking_url(
                leg["from"]["stop_area_id"], leg["to"]["stop_area_id"],
                leg_date, leg_time,
            )
            if url:
                leg["booking"] = {"provider": "trainline", "url": url}
                bookable += 1
        jd["booking"] = {"provider": "trainline", "tickets": bookable}
        # T8 — correspondances à risque (retard réel menaçant la jonction).
        if realtime is not None:
            jd["connection_risks"] = _connection_risks(jd)
        # T8 — perturbations du trajet (Service Alerts).
        if alerts_feed is not None:
            jd["alerts"] = [_alert_json(a) for a in _journey_alerts(alerts_feed, j, g)[:3]]
        out.append(jd)
    return {"journeys": out}


def _journey_alerts(alerts, j, g) -> list:
    """Alertes pertinentes d'un trajet : gares des legs (marche exclue) et
    numéros de train des legs ferroviaires."""
    stop_idxs: list[int] = []
    train_numbers: list[str] = []
    for leg in j.legs:
        if leg.type == "walk":
            continue
        idx = g.stop_index.get(leg.from_id)
        if idx is not None:
            stop_idxs.append(idx)
        idx = g.stop_index.get(leg.to_id)
        if idx is not None:
            stop_idxs.append(idx)
        m = gtfs_rt._TRAIN_NO_RE.match(leg.trip_id)
        if m:
            train_numbers.append(m.group(1))
    return alerts.relevant(stop_idxs, train_numbers)


def _alert_json(a) -> dict:
    return a.to_json()


class _ShellStaticFiles(StaticFiles):
    """Fichiers du shell (html/css/js/sw) : pas de cache navigateur, pour que
    les correctifs (ex. date par défaut) soient pris dès le prochain rechargement
    sans rejouer une ancienne version depuis le cache heuristique."""
    _NO_CACHE = {"/", "/app.js", "/sw.js", "/index.html"}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope["path"]
        if path in self._NO_CACHE:
            async def _send(message: dict) -> None:
                if message["type"] == "http.response.start":
                    headers = [h for h in message.get("headers", []) if h[0].lower() != b"cache-control"]
                    headers.append((b"cache-control", b"no-cache"))
                    message["headers"] = headers
                await send(message)
            await super().__call__(scope, receive, _send)
        else:
            await super().__call__(scope, receive, send)


# SPA T6 (§8) : servie par l'API elle-même — un seul point d'entrée, pas de CORS.
# Les routes /v1/* étant déclarées avant, elles restent prioritaires.
app.mount("/", _ShellStaticFiles(directory=WEB_DIR, html=True), name="web")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
