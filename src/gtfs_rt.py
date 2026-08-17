"""T8 — gtfs_rt.py : flux temps réel SNCF (retards/suppressions/alertes).

Polling des flux GTFS-RT SNCF :
- « Trip Updates » (retards/suppressions) :
  https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-trip-updates
- « Service Alerts » (perturbations) :
  https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-service-alerts

mis à jour toutes les ~2 min, horizon ~60 min.

Structure retenue (partagée avec le moteur) :
- `RealtimeFeed.trip_delays[(trip_id, date_ymd)][stop_area_id] = retard en
  minutes (>=0)`
- `RealtimeFeed.cancelled[(trip_id, date_ymd)] = train supprimé ce jour-là`

Clé datée par la date de service réelle (`TripDescriptor.start_date`). Le
suffixe daté du trip_id SNCF (NewTripId) n'est PAS la date de service : un
même trip_id circule sur plusieurs jours et le flux n'annonce le retard que
pour son `start_date` — sans datation, un retard d'aujourd'hui fuirait sur
toutes les dates du trip (ex. « +10 min dans deux jours »).
- `RealtimeAlerts.alerts = [RealtimeAlert, ...]` (perturbations, §10)

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
ALERTS_URL = "https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-service-alerts"
POLL_INTERVAL_S = 120  # toutes les 2 min
HTTP_TIMEOUT_S = 30

# « StopPoint:OCETrain TER-87723197 » -> « 87723197 »
_STOPPOINT_RE = re.compile(r"(\d{8})$")
# numéro de train d'un trip_id (graphe et flux Service Alerts) : OCESN117760F…
# ou TRSI (Transdev) : 17481@2026-08-10
_TRAIN_NO_RE = re.compile(r"OCES(N?\d+)F|^(\d+)@")

_MAX_ALERT_LEN = 200  # description tronquée exposée (titre complet conservé)


@dataclass
class RealtimeFeed:
    """État temps réel en mémoire : retards par trip/arrêt et trains supprimés."""

    trip_delays: dict[tuple[str, int], dict[int, int]] = field(default_factory=dict)
    cancelled: set[tuple[str, int]] = field(default_factory=set)
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


def _ts_to_ymd(ts: int) -> int | None:
    """Date (YYYYMMDD int) d'un horodatage absolu dans le fuseau Europe/Paris."""
    import datetime as _dt
    from zoneinfo import ZoneInfo

    return int(
        _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc)
        .astimezone(ZoneInfo("Europe/Paris"))
        .strftime("%Y%m%d")
    )


def _service_date(tue, header_ts: int) -> int | None:
    """Date de service (YYYYMMDD int) d'un TripUpdate : `TripDescriptor.start_date`
    si présent, sinon date Europe/Paris de l'horaire absolu (`time`) le plus
    précoce, sinon celle du header du flux. Renvoie None si indatable : on
    refuse alors de l'appliquer (ne pas fuiter sur toutes les dates)."""
    sd = tue.trip.start_date
    if sd:
        try:
            return int(sd)
        except ValueError:
            pass
    times = [
        getattr(getattr(stu, f), "time")
        for stu in tue.stop_time_update
        for f in ("arrival", "departure")
        if getattr(stu, f).HasField("time")
    ]
    if times:
        return _ts_to_ymd(min(times))
    return _ts_to_ymd(header_ts) if header_ts else None


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

        sdate = _service_date(tue, fm.header.timestamp)
        if sdate is None:
            continue  # indatable : on ne l'applique à aucune date
        key = (trip_id, sdate)

        rel = tue.trip.schedule_relationship
        if rel == _pb2.TripDescriptor.CANCELED:
            feed.cancelled.add(key)
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
            feed.trip_delays[key] = delays

    return feed


def fetch_trip_updates(graph: Graph, url: str = DEFAULT_URL) -> RealtimeFeed:
    """Télécharge et décode le flux GTFS-RT courant. En cas d'échec, renvoie un
    feed vide (on conserve l'état précédent côté appelant)."""
    import requests

    r = requests.get(url, timeout=HTTP_TIMEOUT_S)
    r.raise_for_status()
    return parse_trip_updates(r.content, graph)


# ------------------------------------------------------------- Service Alerts
@dataclass
class RealtimeAlert:
    """Perturbation GTFS-RT Service Alerts réduite aux cibles du graphe."""

    id: str
    header: str  # titre (fr si dispo)
    description: str  # description (fr), texte brut
    cause: str = ""  # MAINTENANCE / UNKNOWN_CAUSE / OTHER_CAUSE …
    effect: str = ""
    general: bool = False  # toute la circulation concernée (pas de cible précise)
    stops: set[int] = field(default_factory=set)  # stop_idx (graphe)
    train_numbers: set[str] = field(default_factory=set)  # « N17810 » (graphe)

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "header": self.header,
            "description": self.description,
            "cause": self.cause,
            "effect": self.effect,
        }


@dataclass
class RealtimeAlerts:
    """État Service Alerts en mémoire : alertes actives (période en cours)."""

    alerts: list[RealtimeAlert] = field(default_factory=list)
    updated_at: int = 0
    fetched_at: float = 0.0

    def age_s(self) -> int:
        return int(time.time() - self.fetched_at) if self.fetched_at else -1

    def snapshot(self) -> "RealtimeAlerts":
        return RealtimeAlerts(
            alerts=list(self.alerts),
            updated_at=self.updated_at,
            fetched_at=self.fetched_at,
        )

    def relevant(self, stop_idxs: list[int], train_numbers: list[str], include_general: bool = False) -> list[RealtimeAlert]:
        """Alertes pertinentes pour un trajet : touchant une gare du trajet, ou
        un numéro de train du trajet. Les alertes générales (toutes lignes) sont
        exclues par défaut : trop nombreuses et non ciblables, elles nuiraient
        au bandeau (elles restent comptées dans /v1/health)."""
        stops = set(stop_idxs)
        trains = set(train_numbers)
        out = []
        for a in self.alerts:
            if a.general:
                if include_general:
                    out.append(a)
                continue
            if (a.stops & stops) or (a.train_numbers & trains):
                out.append(a)
        return out


def _clean(text: str) -> str:
    """Nettoie un texte GTFS-RT : balises retirées, entités HTML décodées,
    espaces multiples aplatis. Le texte est conservé EN ENTIER (la troncature
    d'affichage est faite côté client, avec un bouton « voir plus »)."""
    import html
    import re as _re

    text = _re.sub(r"<br\s*/?>", " ", text)
    text = _re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return _re.sub(r"\s+", " ", text).strip()


def _tr(text: str, max_len: int = _MAX_ALERT_LEN) -> str:
    return _clean(text) if len(_clean(text)) <= max_len else _clean(text)[: max_len - 1].rstrip() + "…"


def _fr(translations) -> Optional[str]:
    for tr in translations.translation:
        if tr.language == "fr":
            return tr.text
    if translations.translation:
        return translations.translation[0].text
    return None


def _active(period) -> bool:
    """La période d'activité est-elle en cours (toujours active si non bornée) ?"""
    now = int(time.time())
    if period.start and period.end:
        return period.start <= now <= period.end
    if period.start:
        return now >= period.start
    if period.end:
        return now <= period.end
    return True


def parse_service_alerts(payload: bytes, graph: Graph) -> RealtimeAlerts:
    """Décode le flux Service Alerts et le réduit aux alertes actives ciblées
    sur le réseau TER (graphe) — ou générales."""
    feed = RealtimeAlerts()
    if _pb2 is None:
        return feed
    fm = _pb2.FeedMessage()
    fm.ParseFromString(payload)
    feed.updated_at = fm.header.timestamp
    feed.fetched_at = time.time()

    # index numéro de train -> les trips du graphe ne sont pas nécessaires :
    # on garde le numéro tel quel pour le matching côté moteur.
    for entity in fm.entity:
        sa = entity.alert
        if not sa.HasField("header_text"):
            continue
        periods = list(sa.active_period) or []
        if periods and not any(_active(p) for p in periods):
            continue
        header = _fr(sa.header_text) or ""
        if not header:
            continue
        alert = RealtimeAlert(
            id=entity.id,
            header=header,
            description=_clean(_fr(sa.description_text) or ""),
            cause=_pb2.Alert.Cause.Name(sa.cause) if sa.cause else "",
            effect=_pb2.Alert.Effect.Name(sa.effect) if sa.effect else "",
        )
        seen_general = True
        for ie in sa.informed_entity:
            if ie.stop_id:
                idx = graph.stop_index.get(ie.stop_id)
                if idx is not None:
                    alert.stops.add(idx)
                    seen_general = False
            elif ie.trip.trip_id:
                m = _TRAIN_NO_RE.match(ie.trip.trip_id)
                if m:
                    alert.train_numbers.add(m.group(1) or m.group(2))
                    seen_general = False
        alert.general = seen_general
        feed.alerts.append(alert)
    return feed


def fetch_service_alerts(graph: Graph, url: str = ALERTS_URL) -> RealtimeAlerts:
    import requests

    r = requests.get(url, timeout=HTTP_TIMEOUT_S)
    r.raise_for_status()
    return parse_service_alerts(r.content, graph)


class RealtimePoller:
    """Polling périodique en arrière-plan (thread daemon) des deux flux GTFS-RT :
    Trip Updates (retards/suppressions) et Service Alerts (perturbations).
    Le graphe est consulté pour les index trip_id -> stop_idx et numéro de
    train (indépendants des dates)."""

    def __init__(
        self,
        graph: Graph,
        url: str = DEFAULT_URL,
        alerts_url: str = ALERTS_URL,
        interval_s: int = POLL_INTERVAL_S,
    ):
        self.graph = graph
        self.url = url
        self.alerts_url = alerts_url
        self.interval_s = interval_s
        self.feed = RealtimeFeed()
        self.alerts = RealtimeAlerts()
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

    def alerts_snapshot(self) -> RealtimeAlerts:
        with self._lock:
            return self.alerts.snapshot()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                feed = fetch_trip_updates(self.graph, self.url)
                with self._lock:
                    self.feed = feed
                log.info("GTFS-RT: %d trips retardés, %d supprimés (age %ss)",
                         len(feed.trip_delays), len(feed.cancelled), feed.age_s())
            except Exception:
                log.warning("GTFS-RT: échec du fetch trip-updates (l'état précédent est conservé)", exc_info=True)
            try:
                alerts = fetch_service_alerts(self.graph, self.alerts_url)
                with self._lock:
                    self.alerts = alerts
                log.info("GTFS-RT alerts: %d alertes actives (age %ss)",
                         len(alerts.alerts), alerts.age_s())
            except Exception:
                log.warning("GTFS-RT: échec du fetch service-alerts (l'état précédent est conservé)", exc_info=True)
            self._stop.wait(self.interval_s)
