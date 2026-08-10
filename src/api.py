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
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.graph import Graph, ALIASES
from src.raptor import RaptorEngine
from src import trainline

DEFAULT_GRAPH = Path(__file__).resolve().parents[1] / "data" / "graph.bin"
WEB_DIR = Path(__file__).resolve().parents[1] / "web"

# Route d'une ouverture/départ de tous les jours de l'application.
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_COORDS_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")

_engine: RaptorEngine | None = None


def get_engine(graph_path: Path = DEFAULT_GRAPH) -> RaptorEngine:
    """Charge le graphe une seule fois (module-level), puis le partage."""
    global _engine
    if _engine is None:
        _engine = RaptorEngine(Graph.load(graph_path))
    return _engine


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


# ---------------------------------------------------------------- application
app = FastAPI(
    title="TER Finder API",
    description="Recherche d'itinéraires 100% TER (moteur McRAPTOR).",
    version="0.1.0",
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
    }


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
                "trainline_code": trainline.code_for(g.stops[idx].id),
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
    max_transfers: int = Query(3, ge=0, le=3),
    vehicle: str = Query("all", pattern="^(all|train_only)$"),
    count: int = Query(5, ge=1, le=20),
) -> dict:
    engine = get_engine()
    g = engine.graph
    d = _parse_date(date, g)
    t0 = _parse_time(time)

    origins = _resolve_place(g, from_)
    dests = _resolve_place(g, to)

    if datetime_represents == "arrival":
        journeys = engine.arrive_by(int(d.strftime("%Y%m%d")), origins, dests, t0, max_transfers, vehicle)
    else:
        journeys = engine.depart_after(int(d.strftime("%Y%m%d")), origins, dests, t0, max_transfers, vehicle)

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
        out.append(jd)
    return {"journeys": out}


# SPA T6 (§8) : servie par l'API elle-même — un seul point d'entrée, pas de CORS.
# Les routes /v1/* étant déclarées avant, elles restent prioritaires.
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
