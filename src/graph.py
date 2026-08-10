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

    # Correspondances : temps par gare (défaut DEFAULT_MIN_TRANSFER_MIN)
    min_transfer: list[int] = field(default_factory=list)  # stop_idx -> minutes

    # Arcs de correspondance explicites (inter-gares, ex. Paris)
    # (stop_a, stop_b) -> minutes
    transfer_edges: dict[tuple[int, int], int] = field(default_factory=dict)

    # Circulation : service_id -> dates (format int YYYYMMDD)
    service_dates: dict[str, frozenset[int]] = field(default_factory=dict)

    # Recherche de gares : nom normalisé -> stop_idx
    search_index: dict[str, list[int]] = field(default_factory=dict)

    # Groupes de gares interchangeables pour la recherche (§5.5 « toutes gares »)
    # ex. place_groups["paris"] = indices des gares principales de Paris.
    # Le moteur de routage considère l'arrivée à N'IMPORTE QUEL membre comme une
    # arrivée au groupe ; les arcs inter-gares (paris_links) gèrent les trajets
    # entre deux gares du groupe.
    place_groups: dict[str, list[int]] = field(default_factory=dict)

    # Couverture du GTFS
    date_min: int = 0
    date_max: int = 0

    # ------------------------------------------------------------------ I/O
    def save(self, path: Path) -> None:
        import pickle

        path.write_bytes(pickle.dumps(self, protocol=pickle.HIGHEST_PROTOCOL))

    @staticmethod
    def load(path: Path) -> "Graph":
        import pickle

        return pickle.loads(path.read_bytes())

    # ------------------------------------------------------ utilitaires
    def is_service_active(self, service_id: str, date: int) -> bool:
        return date in self.service_dates.get(service_id, frozenset())

    def active_trip_indices(self, date: int) -> list[int]:
        """Trips circulant à la date demandée (indices)."""
        return [i for i, t in enumerate(self.trips) if self.is_service_active(t.service_id, date)]

    def stop_by_id(self, stop_area_id: str) -> int:
        return self.stop_index[stop_area_id]

    def find_stops(self, query: str) -> list[tuple[int, str]]:
        """Gares candidates pour une recherche (autocomplete §5.4).

        Correspondance exacte (nom normalisé) d'abord, puis préfixe, puis
        sous-chaîne. Retourne [(stop_idx, nom)], classées par pertinence.
        """
        norm = normalize(query)
        if not norm:
            return []
        results: list[tuple[int, str]] = []
        if norm in self.search_index:
            results = [(idx, self.stops[idx].name) for idx in self.search_index[norm]]
        if not results:
            for key, idxs in self.search_index.items():
                if key.startswith(norm):
                    results.extend((idx, self.stops[idx].name) for idx in idxs)
        if not results:
            for key, idxs in self.search_index.items():
                if norm in key:
                    results.extend((idx, self.stops[idx].name) for idx in idxs)
        # dédup (une gare peut apparaître sous plusieurs clés) + tri
        seen: set[int] = set()
        out: list[tuple[int, str]] = []
        for idx, name in results:
            if idx not in seen:
                seen.add(idx)
                out.append((idx, name))
        out.sort(key=lambda it: it[1])
        return out

    def resolve_place(self, query: str) -> list[int]:
        """Résout un lieu de recherche en une liste de gares (§5.5).

        - Un groupe (« Paris », « Paris toutes gares ») → tous les membres
          (N'importe lequel satisfait la recherche).
        - Sinon → la meilleure gare unique (autocomplete).
        """
        norm = normalize(query)
        if norm in self.place_groups:
            return list(self.place_groups[norm])
        if norm in PARIS_ALIASES:
            return list(self.place_groups.get("paris", []))
        hits = self.find_stops(query)
        return [idx for idx, _ in hits[:1]]


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

# ------------------------------------------------- groupe « toutes gares »
# §5.5 : gares principales d'une ville traitées comme interchangeables pour la
# recherche (« Paris », « Paris toutes gares »). Le build_graph résout ces noms
# en indices réels dans Graph.place_groups.
PARIS_STATION_NAMES: tuple[str, ...] = (
    "Paris Est",
    "Paris Gare du Nord",
    "Paris Saint-Lazare",
    "Paris Montparnasse Hall 1 - 2",
    "Paris Austerlitz",
    "Paris Gare de Lyon Hall 1 - 2",
    "Paris Bercy Bourg. Pays d'Auv.",
)

# Alias de recherche qui désignent le groupe entier (normalisés sans accents).
PARIS_ALIASES: frozenset[str] = frozenset(
    {"paris", "paris toutes gares", "paris tous", "toutes gares"}
)
