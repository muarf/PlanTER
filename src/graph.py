"""T2 — graph.py : modèle de données du graphe TER en mémoire.

Structures construites par `build_graph.py` à partir du GTFS-TER, sérialisées
en cache binaire (`data/graph.bin`, pickle) pour un chargement rapide, puis
consommées par le moteur de la Tâche T3 (McRAPTOR).

Points clés (cf. walkthrough.md §4) :
- L'unité de routage est la **StopArea** (la gare) ; les StopPoint sont ramenés
  à leur `parent_station`.
- Un **Trip** est une instance datée : il circule le jour J si
  `J ∈ service_dates[service_id]`.
- Les horaires sont en **minutes depuis minuit**, pouvant dépasser 1440
  (service de nuit).
- Les lignes sont identifiées par **route_id** (le `route_short_name` peut être
  partagé entre plusieurs lignes physiques).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MIN_TRANSFER_MIN = 5  # §5.3 : défaut de correspondance


def approx_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance à vol d'oiseau (km) avec correction de latitude : chaque degré
    de longitude vaut 111,32 × cos(φ) km, pas 111,32 partout. L'ancienne
    formule sans cos classait Byans (12,3 km réels) devant Arc-et-Senans
    (9,5 km) pour un point du Doubs — suffisant à ces échelles, pas besoin
    de haversine complet."""
    import math

    coslat = math.cos(math.radians((lat1 + lat2) / 2.0))
    dx = (lon2 - lon1) * 111_320.0 * coslat
    dy = (lat2 - lat1) * 110_574.0
    return math.sqrt(dx * dx + dy * dy) / 1000.0


# Plafond du nombre de gares retenues par côté lors d'une résolution
# « commune / GPS » : le rayon dit jusqu'où chercher, pas combien de gares
# alimenter au moteur (temps de réponse stable même à 100 km autour de Paris).
MAX_GARES_PER_SIDE = 8


@dataclass
class StopArea:
    """Une gare (StopArea, location_type=1)."""

    id: str
    name: str
    lat: float
    lon: float


@dataclass
class Route:
    id: str
    short_name: str
    long_name: str


@dataclass
class StopTime:
    """Passage d'un trip à un arrêt (indices : stop = StopArea index)."""

    stop: int
    arr: int  # minutes depuis minuit (peut dépasser 1440)
    dep: int


@dataclass
class Trip:
    id: str
    route: int  # index dans Graph.routes
    service_id: str
    vehicle: str  # "train" | "car" | "tram_train"
    stop_times: list[StopTime] = field(default_factory=list)

    @property
    def dep(self) -> int:
        return self.stop_times[0].dep if self.stop_times else 0

    @property
    def arr(self) -> int:
        return self.stop_times[-1].arr if self.stop_times else 0


@dataclass
class Graph:
    """Graphe statique TER prêt pour le routage."""

    stops: list[StopArea] = field(default_factory=list)
    routes: list[Route] = field(default_factory=list)
    trips: list[Trip] = field(default_factory=list)

    stop_index: dict[str, int] = field(default_factory=dict)  # stop_area_id -> idx
    route_index: dict[str, int] = field(default_factory=dict)  # route_id -> idx

    # Index de routage
    trips_by_route: list[list[int]] = field(default_factory=list)  # route_idx -> [trip_idx]
    routes_by_stop: list[list[int]] = field(default_factory=list)  # stop_idx -> [route_idx]
    trip_index: dict[str, int] = field(default_factory=dict)  # trip_id -> trip_idx

    # Correspondances : temps par gare (défaut DEFAULT_MIN_TRANSFER_MIN)
    min_transfer: list[int] = field(default_factory=list)  # stop_idx -> minutes

    # Arcs de correspondance explicites (inter-gares, ex. Paris)
    # (stop_a, stop_b) -> minutes
    transfer_edges: dict[tuple[int, int], int] = field(default_factory=dict)

    # Distances ferroviaires PK par hop : (trip_idx, k) -> km entre les
    # arrêts consécutifs k et k+1 du trip (RfnIndex, build_graph.py). Les hops
    # sans ancres PK sont absents : pricing.py retombe sur haversine × rail_factor.
    hop_km: dict[tuple[int, int], float] = field(default_factory=dict)

    # Circulation : service_id -> dates (format int YYYYMMDD)
    service_dates: dict[str, frozenset[int]] = field(default_factory=dict)

    # Recherche de gares : nom normalisé -> stop_idx
    search_index: dict[str, list[int]] = field(default_factory=dict)

    # Groupes de gares interchangeables pour la recherche (§5.5 « toutes gares »)
    # ex. place_groups["lyon"] = indices des gares de Lyon.
    # Le moteur de routage considère l'arrivée à N'IMPORTE QUEL membre comme une
    # arrivée au groupe ; les arcs inter-gares (paris_links) gèrent les trajets
    # entre deux gares du groupe.
    place_groups: dict[str, list[int]] = field(default_factory=dict)

    # « toutes gares » : alias de recherche (normalisé, ex. « lyon toutes gares »)
    # -> clé du groupe (ex. "lyon"). Construit depuis config/place_groups.json.
    place_group_aliases: dict[str, str] = field(default_factory=dict)

    # Communes (référentiel géographique offline, geo.api.gouv.fr) :
    # insee -> (nom, lat, lon, population)
    communes: dict[str, tuple[str, float, float, int]] = field(default_factory=dict)

    # nom normalisé -> [insee] (recherche/résolution des communes)
    commune_index: dict[str, list[str]] = field(default_factory=dict)

    # Region de chaque arret bus (stop_idx -> region)
    bus_stop_region: dict[int, str] = field(default_factory=dict)

    # Region de chaque route (route_idx -> region)
    route_region: dict[int, str] = field(default_factory=dict)

    # Couverture du GTFS
    date_min: int = 0
    date_max: int = 0

    # Index spatial (built lazily) : cell -> list of stop_idx
    _grid: dict[tuple[int, int], list[int]] | None = field(default=None, repr=False)

    def _build_grid(self, cell_deg: float = 0.002) -> None:
        grid: dict[tuple[int, int], list[int]] = {}
        for i, s in enumerate(self.stops):
            key = (int(s.lat / cell_deg), int(s.lon / cell_deg))
            grid.setdefault(key, []).append(i)
        self._grid = grid

    def stops_nearby(self, stop_idx: int, radius_m: float = 200.0) -> list[int]:
        """Indices de stops à < radius_m de stops[stop_idx] (hors lui-même).

        Grille en degrés mais distances métriques corrigées : la couverture
        de grille est calculée par axe (les cellules de longitude sont plus
        petites en mètres qu'en latitude), et le filtre final utilise
        approx_distance_km. L'ancienne formule ×111 uniforme sous-couvrait
        l'est-ouest (~3,5 km réels pour un « 5 km » à 47°N)."""
        import math

        if self._grid is None:
            self._build_grid()
        s = self.stops[stop_idx]
        cell_deg = 0.002
        coslat = math.cos(math.radians(s.lat))
        cell_m_lat = cell_deg * 110_574.0
        cell_m_lon = max(cell_deg * 111_320.0 * coslat, 1e-9)
        r_lat = int(radius_m / cell_m_lat) + 1
        r_lon = int(radius_m / cell_m_lon) + 1
        c_lat = int(s.lat / cell_deg)
        c_lon = int(s.lon / cell_deg)
        result: list[int] = []
        for dlat in range(-r_lat, r_lat + 1):
            for dlon in range(-r_lon, r_lon + 1):
                for j in self._grid.get((c_lat + dlat, c_lon + dlon), []):
                    if j == stop_idx:
                        continue
                    o = self.stops[j]
                    if approx_distance_km(s.lat, s.lon, o.lat, o.lon) * 1000.0 <= radius_m:
                        result.append(j)
        return result

    # ------------------------------------------------------------------ I/O
    def save(self, path: Path) -> None:
        import pickle

        path.write_bytes(pickle.dumps(self, protocol=pickle.HIGHEST_PROTOCOL))

    @staticmethod
    def load(path: Path) -> "Graph":
        import pickle

        g = pickle.loads(path.read_bytes())
        # backward compat: add fields missing from older pickles
        if not hasattr(g, "bus_stop_region"):
            g.bus_stop_region = {}
        if not hasattr(g, "route_region"):
            g.route_region = {}
        if not hasattr(g, "communes"):
            g.communes = {}
        if not hasattr(g, "commune_index"):
            g.commune_index = {}
        return g

    # ------------------------------------------------------ utilitaires
    def is_service_active(self, service_id: str, date: int) -> bool:
        return date in self.service_dates.get(service_id, frozenset())

    def active_trip_indices(self, date: int) -> list[int]:
        """Trips circulant à la date demandée (indices)."""
        return [i for i, t in enumerate(self.trips) if self.is_service_active(t.service_id, date)]

    def stop_by_id(self, stop_area_id: str) -> int:
        return self.stop_index[stop_area_id]

    def _is_bus(self, idx: int) -> bool:
        return self.stops[idx].id.startswith("BusStop:")

    def find_stops(self, query: str) -> list[tuple[int, str]]:
        """Gares candidates pour une recherche (autocomplete §5.4).

        Correspondance exacte (nom normalisé) d'abord, puis préfixe, puis
        sous-chaîne. Retourne [(stop_idx, nom)], classées par pertinence :
        les gares (StopArea) passent toujours avant les arrêts bus — le tri
        alphabétique pur faisait remonter les « VILLE … » en majuscules des
        feeds bus devant les gares homonymes.
        """
        norm = normalize(query)
        if not norm:
            return []
        results: list[tuple[int, str]] = []
        if norm in self.search_index:
            results = [(idx, self.stops[idx].name) for idx in self.search_index[norm]]
        # La clé exacte peut ne contenir que des arrêts bus (indexation par
        # premier mot des feeds régionaux : « besancon » → « BESANCON … »).
        # On fusionne alors le tier préfixe, sinon les gares homonymes
        # (« Besançon Viotte »…) resteraient introuvables.
        if not results or all(self._is_bus(idx) for idx, _ in results):
            for key, idxs in self.search_index.items():
                if key.startswith(norm):
                    results.extend((idx, self.stops[idx].name) for idx in idxs)
        if not results:
            for key, idxs in self.search_index.items():
                if norm in key:
                    results.extend((idx, self.stops[idx].name) for idx in idxs)
        # dédup : une gare peut apparaître sous plusieurs clés ; deux arrêts
        # bus de feeds régionaux différents peuvent désigner le même point
        # physique (ex. « BESANCON PEM Viotte » UT25/UT70) → on fusionne les
        # doublons (même nom normalisé à < ~300 m).
        seen: set[int] = set()
        seen_bus: dict[str, list[tuple[float, float]]] = {}
        out: list[tuple[int, str]] = []
        for idx, name in results:
            if idx in seen:
                continue
            seen.add(idx)
            if self._is_bus(idx):
                coords = seen_bus.setdefault(normalize(name), [])
                s = self.stops[idx]
                if any((s.lat - la) ** 2 + (s.lon - lo) ** 2 < 0.003 ** 2 for la, lo in coords):
                    continue
                coords.append((s.lat, s.lon))
            out.append((idx, name))
        # gares d'abord, ordre alphabétique dans chaque catégorie
        out.sort(key=lambda it: (1 if self._is_bus(it[0]) else 0, it[1]))
        return out

    # ------------------------------------------------------------- communes
    def find_communes(self, query: str, limit: int = 5) -> list[dict]:
        """Communes candidates pour l'autocomplete : correspondance exacte,
        puis préfixe, puis sous-chaîne sur le nom normalisé. Retourne
        [{id, name, lat, lon}], les plus peuplées d'abord (les homonymes).

        Si le graphe a été construit avec le précalcul « gare la plus proche »
        (tuples à 6 éléments), chaque entrée expose en plus
        `nearest_gare` / `nearest_km` pour l'affichage (« By · Liesle 7,9 km »).
        """
        norm = normalize(query)
        if not norm or not self.commune_index:
            return []
        if norm in self.commune_index:
            keys = [norm]
        else:
            keys = sorted(k for k in self.commune_index if k.startswith(norm))
            if not keys:
                keys = sorted(k for k in self.commune_index if norm in k)
        rows = []
        for k in keys:
            for insee in self.commune_index[k]:
                entry = self.communes[insee]
                # compat anciens pickles : tuple (nom, lat, lon, pop) sans précalcul
                nom, lat, lon, pop = entry[0], entry[1], entry[2], entry[3]
                extra = entry[4:] if len(entry) >= 6 else ()
                rows.append((insee, nom, lat, lon, pop, extra))
        rows.sort(key=lambda r: (-r[4], r[1]))
        out = []
        for code, nom, lat, lon, _pop, extra in rows[:limit]:
            item = {"id": f"commune:{code}", "name": nom, "lat": lat, "lon": lon}
            if extra and extra[0] is not None:
                idx, km = extra
                item["nearest_gare"] = self.stops[idx].name
                item["nearest_km"] = round(float(km), 1)
            out.append(item)
        return out

    def resolve_commune(self, value: str) -> tuple[float, float] | None:
        """Résout « commune:<insee> » ou un nom de commune exact en (lat, lon)."""
        v = value.strip()
        if v.startswith("commune:"):
            entry = self.communes.get(v.removeprefix("commune:"))
            return (entry[1], entry[2]) if entry else None
        hits = self.commune_index.get(normalize(v))
        if hits:
            entry = self.communes[hits[0]]
            return (entry[1], entry[2])
        return None

    def commune_name(self, value: str) -> str | None:
        """Nom de la commune résolue (« commune:<insee> », nom exact ou id),
        pour les messages d'erreur et les notes de trajet."""
        v = value.strip()
        if v.startswith("commune:"):
            entry = self.communes.get(v.removeprefix("commune:"))
            return entry[0] if entry else None
        hits = self.commune_index.get(normalize(v))
        if hits:
            return self.communes[hits[0]][0]
        return None

    def nearest_gares(
        self,
        lat: float,
        lon: float,
        min_km: float = 0.0,
        max_km: float = 0.0,
        n: int = MAX_GARES_PER_SIDE,
    ) -> list[int]:
        """Gares (StopArea, hors arrêts bus) autour d'un point.

        - max_km == 0 : défaut « la gare la plus proche » — UNE seule gare,
          quelle que soit sa distance.
        - max_km > 0 : gares dont la distance RÉELLE est dans [min_km, max_km],
          triées par distance, plafonnées à n.
        """
        scored = []
        for i, s in enumerate(self.stops):
            if s.id.startswith("BusStop:"):
                continue
            d = approx_distance_km(lat, lon, s.lat, s.lon)
            scored.append((d, i))
        scored.sort(key=lambda it: it[0])
        if max_km > 0:
            scored = [(d, i) for d, i in scored if min_km <= d <= max_km]
        else:
            n = 1
        return [i for _, i in scored[:n]]

    def resolve_place(
        self,
        query: str,
        radius_min_km: float = 0.0,
        radius_max_km: float = 0.0,
    ) -> list[int]:
        idxs, _ctx = self.resolve_place_ctx(query, radius_min_km, radius_max_km)
        return idxs

    def resolve_place_ctx(
        self,
        query: str,
        radius_min_km: float = 0.0,
        radius_max_km: float = 0.0,
    ) -> tuple[list[int], dict | None]:
        """Résout un lieu → (gares, contexte de provenance).

        Le contexte est non-None UNIQUEMENT pour une résolution géographique
        (commune) : il alimente les notes « Départ/Arrivée à X — N km de Y ».
        Priorité inchangée (§5.5) : alias groupe → gare(s) exacte(s) → commune
        → arrêt bus exact → autocomplete. La commune ne prend donc JAMAIS le
        dessus sur des gares homonymes (ex. « Lyon » reste multi-gares).

        Le rayon [radius_min_km, radius_max_km] ne s'applique qu'à la branche
        commune ; une requête qui désigne exactement une gare n'est jamais
        filtrée.
        """
        norm = normalize(query)
        aliases = getattr(self, "place_group_aliases", {})
        if norm in aliases:
            group_key = aliases[norm]
            # Ville dont le nom EST une gare (ex. « Dijon ») → la gare ; mais
            # si la clé exacte ne contient que des arrêts bus (ex. « besancon »
            # via les feeds régionaux), on retombe sur le groupe de gares.
            if norm in self.search_index:
                gares = [i for i in self.search_index[norm] if not self._is_bus(i)]
                if gares:
                    return gares, None
            return list(self.place_groups.get(group_key, [])), None
        # correspondance exacte : les gares priment sur les arrêts bus homonymes
        if norm in self.search_index:
            idxs = self.search_index[norm]
            gares = [i for i in idxs if not self._is_bus(i)]
            if gares:
                return gares, None
        # commune exacte (nom tapé ou id) → gares dans l'intervalle de rayon
        coords = self.resolve_commune(query)
        if coords is not None:
            label = self.commune_name(query) or query
            idxs = self.nearest_gares(coords[0], coords[1], min_km=radius_min_km, max_km=radius_max_km)
            return idxs, {"kind": "commune", "label": label, "lat": coords[0], "lon": coords[1]}
        # clé exacte bus uniquement (sélection explicite d'un arrêt)
        if norm in self.search_index:
            return list(self.search_index[norm]), None
        hits = self.find_stops(query)
        if not hits:
            return [], None
        first_name = hits[0][1]
        norm_first = normalize(first_name)
        if norm_first in self.search_index:
            return list(self.search_index[norm_first]), None
        return [idx for idx, _ in hits[:1]], None


# ------------------------------------------------------------ normalisation
def normalize(text: str) -> str:
    """Normalisation pour la recherche de gares (§5.4) : minuscules, sans
    accents, caractères non alphanumériques → espaces, espaces resserrés."""
    import re
    import unicodedata

    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


# ------------------------------------------------------------ alias de gares
# §5.4 : alias courants (orthographes d'usage), au format nom normalisé -> idx
# (résolus par le builder à partir des vrais noms de gares).
ALIASES: dict[str, list[str]] = {
    "paris gare de lyon": ["paris gare de lyon hall 1 2"],
    "paris lyon": ["paris gare de lyon hall 1 2"],
    "paris bercy": ["paris bercy bourg pays d auv"],
    "bordeaux st jean": ["bordeaux saint jean"],
    "bordeaux saint jean": ["bordeaux saint jean"],
    "marseille st charles": ["marseille saint charles"],
    "marseille saint charles": ["marseille saint charles"],
    "lyon part dieu": ["lyon part dieu"],
    "lyon perrache": ["lyon perrache"],
    "besancon viotte": ["besancon viotte"],
    "dijon": ["dijon"],
    "paris est": ["paris est"],
}


def build_place_groups(g: Graph, config: dict) -> None:
    """Remplit `g.place_groups` / `g.place_group_aliases` depuis
    config/place_groups.json : `{clé_ville: {label, aliases, stations}}`.

    Les gares listées mais absentes du GTFS sont ignorées (l'appelant peut
    vérifier les tailles). La gare homonyme de la ville (ex. « Dijon ») est
    placée en tête du groupe pour être le premier résultat affiché.
    """
    g.place_groups = {}
    g.place_group_aliases = {}
    index = {normalize(s.name): idx for idx, s in enumerate(g.stops)}
    for key, spec in config.items():
        found = []
        for name in spec.get("stations", []):
            idx = index.get(normalize(name))
            if idx is not None:
                found.append(idx)
        if not found:
            continue
        main = index.get(normalize(key))
        if main is not None and main in found:
            found = [main] + sorted(i for i in found if i != main)
        else:
            found = sorted(found)
        g.place_groups[key] = found
        for alias in spec.get("aliases", []):
            g.place_group_aliases[normalize(alias)] = key
