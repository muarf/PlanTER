"""T8 — gtfs_rt.py : flux temps réel SNCF (retards/suppressions).

Polling du flux GTFS-RT « Trip Updates » SNCF
(https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-trip-updates),
mis à jour toutes les ~2 min, horizon ~60 min.

Structure retenue (partagée avec le moteur) :
- `RealtimeFeed.trip_delays[trip_id][stop_area_id] = retard en minutes (>=0)`
- `RealtimeFeed.cancelled[trip_id] = train supprimé`

Le stop_id du flux est « StopPoint:OCETrain TER-<uic8> » ; on le ramène à
« StopArea:OCE<uic8> » (identifiant interne du graphe, §2). Un train en avance
(delay négatif) est ramené à 0 : on ne part jamais avant l'horaire théorique.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

try:
    from google.transit import gtfs_realtime_pb2 as _pb2
except ImportError:  # pragma: no cover
    _pb2 = None

from src.graph import Graph

log = logging.getLogger("gtfs_rt")

DEFAULT_URL = "https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-trip-updates"
POLL_INTERVAL_S = 120  # toutes les 2 min
HTTP_TIMEOUT_S = 30

# « StopPoint:OCETrain TER-87723197 » -> « 87723197 »
_STOPPOINT_RE = re.compile(r"(\d{8})$")


@dataclass
class RealtimeFeed:
    """État temps réel en mémoire : retards par trip/arrêt et trains supprimés."""

    trip_delays: dict[str, dict[int, int]] = field(default_factory=dict)
    cancelled: set[str] = field(default_factory=set)
    updated_at: int = 0  # epoch (timestamp GTFS-RT)
    fetched_at: float = 0.0  # epoch (heure locale du fetch)

    def age_s(self) -> int:
        return int(time.time() - self.fetched_at) if self.fetched_at else -1

    @property
    def is_fresh(self, max_age_s: int = 6 * 60) -> bool:
        """Un flux de plus de ~6 min est obsolète (maj toutes les 2 min)."""
        return 0 <= self.age_s() <= max_age_s

    def snapshot(self) -> "RealtimeFeed":
        """Copie indépendante (le moteur la lit sans verrou)."""
        return RealtimeFeed(
            trip_delays={tid: dict(d) for tid, d in self.trip_delays.items()},
            cancelled=set(self.cancelled),
            updated_at=self.updated_at,
            fetched_at=self.fetched_at,
        )


def _to_stop_idx(graph: Graph, stop_id: str) -> Optional[int]:
    m = _STOPPOINT_RE.search(stop_id)
    if not m:
        return None
    return graph.stop_index.get(f"StopArea:OCE{m.group(1)}")


def parse_trip_updates(payload: bytes, graph: Graph) -> RealtimeFeed:
    """Décode un FeedMessage GTFS-RT et le réduit aux trips TER du graphe."""
    feed = RealtimeFeed()
    if _pb2 is None:
        return feed
    fm = _pb2.FeedMessage()
    fm.ParseFromString(payload)
    feed.updated_at = fm.header.timestamp
    feed.fetched_at = time.time()

    for entity in fm.entity:
        tue = entity.trip_update
        if not tue.HasField("trip"):
            continue
        trip_id = tue.trip.trip_id
        if ":TER:" not in trip_id:
            continue
        # Trip non reconnu du graphe -> ignoré (ex. date hors couverture).
        if trip_id not in graph.trip_index:
            continue

        rel = tue.trip.schedule_relationship
        if rel == _pb2.TripDescriptor.CANCELED:
            feed.cancelled.add(trip_id)
            continue

        delays: dict[int, int] = {}
        for stu in tue.stop_time_update:
            idx = _to_stop_idx(graph, stu.stop_id)
            if idx is None:
                continue
            # Retard le plus défavorable (arrivée OU départ), en minutes, borné à >= 0.
            d = 0
            for field_name in ("arrival", "departure"):
                st = getattr(stu, field_name)
                if st.HasField("delay"):
                    d = max(d, st.delay // 60)
            if d > 0:
                delays[idx] = d
        if delays:
            feed.trip_delays[trip_id] = delays

    return feed


def fetch_trip_updates(graph: Graph, url: str = DEFAULT_URL) -> RealtimeFeed:
    """Télécharge et décode le flux GTFS-RT courant. En cas d'échec, renvoie un
    feed vide (on conserve l'état précédent côté appelant)."""
    import requests

    r = requests.get(url, timeout=HTTP_TIMEOUT_S)
    r.raise_for_status()
    return parse_trip_updates(r.content, graph)


class RealtimePoller:
    """Polling périodique en arrière-plan (thread daemon). Le graphe est consulté
    au premier fetch pour l'index trip_id -> stop_idx (indépendant des dates)."""

    def __init__(self, graph: Graph, url: str = DEFAULT_URL, interval_s: int = POLL_INTERVAL_S):
        self.graph = graph
        self.url = url
        self.interval_s = interval_s
        self.feed = RealtimeFeed()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="gtfs-rt-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def snapshot(self) -> RealtimeFeed:
        with self._lock:
            return self.feed.snapshot()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                feed = fetch_trip_updates(self.graph, self.url)
                with self._lock:
                    self.feed = feed
                log.info("GTFS-RT: %d trips retardés, %d supprimés (age %ss)",
                         len(feed.trip_delays), len(feed.cancelled), feed.age_s())
            except Exception:
                log.warning("GTFS-RT: échec du fetch (l'état précédent est conservé)", exc_info=True)
            self._stop.wait(self.interval_s)
