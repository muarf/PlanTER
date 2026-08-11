"""T3 — raptor.py : moteur de calcul d'itinéraires McRAPTOR (§6 PLAN.md).

Implémentation RAPTOR par rounds (Delling/Pajor/Werneck, ALENEX 2012) sur le
graphe construit par T2 (`data/graph.bin`). Multi-critères Pareto :
- heure d'arrivée la plus précoce (DepartAfter) ;
- nombre de correspondances minimal (limite 0–3 = nombre de rounds) ;
- heure de départ la plus tardive (ArriveBy, par renversement du temps).

Contraintes du plan :
- tous les legs sont des trips TER par construction (graphe déjà filtré) ;
- correspondance minimum par gare (§5.3, `Graph.min_transfer`) ;
- arcs de marche inter-gares (`Graph.transfer_edges`, ex. Paris) appliqués
  entre les rounds (un déplacement à pied ne compte pas comme correspondance) ;
- groupes de gares (« Paris » toutes gares, §5.5) : origines/destinations = listes.

Un même cœur de balayage (`_rounds`) traite DepartAfter (trips directs) et
ArriveBy (trips miroirs, temps renversés autour de MAXT puis re-renversés).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date as _date
from typing import Optional

from src.graph import Graph

# Fenêtre de recherche : on ne scanne pas au-delà de t0 + HORIZON_MIN (§6.4).
HORIZON_MIN = 36 * 60

# Plage de renversement du temps pour ArriveBy (couvre les services de nuit
# jusqu'à ~48 h). MAXT = borne haute des horaires normalisés (> 24 h).
MAXT = 48 * 60

# Heures d'été/hiver (Europe/Paris) en 2026.
_DST_START = (3, 29)
_DST_END = (10, 25)

# Nombre maximal de legs de marche (inter-gares) par itinéraire : au plus un
# côté origine + un côté destination (ex. Paris GDL -> Bercy puis retour).
MAX_WALK_LEGS = 2


def _is_dst(d: _date) -> bool:
    return (_DST_START[0], _DST_START[1]) <= (d.month, d.day) < (_DST_END[0], _DST_END[1])


def _iso(d: _date, minutes: int) -> str:
    day_shift, rem = divmod(int(minutes), 24 * 60)
    hh, mm = divmod(rem, 60)
    dd = _date.fromordinal(d.toordinal() + day_shift)
    off = "+02:00" if _is_dst(dd) else "+01:00"
    return f"{dd.isoformat()}T{hh:02d}:{mm:02d}:00{off}"


# ---------------------------------------------------------------- types
@dataclass
class Leg:
    type: str  # "train" | "car" | "tram_train" | "walk" (arc inter-gares)
    route_id: str
    line: str  # route_short_name
    line_name: str  # route_long_name
    vehicle_label: str  # numéro de train si disponible
    trip_id: str
    from_id: str
    from_name: str
    from_time: int  # minutes depuis minuit
    to_id: str
    to_name: str
    to_time: int
    delay_min: int = 0  # T8 : retard GTFS-RT à l'embarquement (minutes)

    def to_json(self, d: _date) -> dict:
        return {
            "type": self.type,
            "line": self.line,
            "line_name": self.line_name,
            "vehicle_label": self.vehicle_label,
            "delay_min": self.delay_min,
            "from": {"stop_area_id": self.from_id, "name": self.from_name, "time": _iso(d, self.from_time)},
            "to": {"stop_area_id": self.to_id, "name": self.to_name, "time": _iso(d, self.to_time)},
        }


@dataclass
class Journey:
    departure: int  # minutes (départ réel du 1er train)
    arrival: int  # minutes
    transfers: int  # correspondances (legs - 1)
    legs: list[Leg] = field(default_factory=list)

    @property
    def duration_min(self) -> int:
        return self.arrival - self.departure

    def to_json(self, d: _date) -> dict:
        return {
            "departure": _iso(d, self.departure),
            "arrival": _iso(d, self.arrival),
            "duration_min": self.duration_min,
            "transfers": self.transfers,
            "legs": [leg.to_json(d) for leg in self.legs],
        }


_VEHICLE_LABEL_RE = re.compile(r"OCES(N?\d+)F")


def _vehicle_label(trip_id: str) -> str:
    m = _VEHICLE_LABEL_RE.search(trip_id)
    return m.group(1) if m else ""


# ---------------------------------------------------------------- engine
class RaptorEngine:
    def __init__(self, graph: Graph):
        self.graph = graph
        # ensemble des arrêts par route = UNION sur tous les trips de la route
        # (une même ligne peut avoir des terminus différents selon les trips,
        # ex. K7 Paris GDL->Lyon vs Lyon->Paris Bercy).
        self._route_stops: list[list[int]] = [[] for _ in graph.routes]
        self._route_stop_sets: list[frozenset[int]] = [frozenset() for _ in graph.routes]
        for ridx, trips in enumerate(graph.trips_by_route):
            if trips:
                stops: set[int] = set()
                for tidx in trips:
                    stops.update(st.stop for st in graph.trips[tidx].stop_times)
                self._route_stops[ridx] = sorted(stops)
                self._route_stop_sets[ridx] = frozenset(stops)
        self._active_cache: dict[int, list[int]] = {}
        self._views_cache: dict[tuple[int, str], dict[int, list[tuple[int, list]]]] = {}

    # ------------------------------------------------------------- vues
    def active_trips(self, date: int) -> list[int]:
        if date not in self._active_cache:
            self._active_cache[date] = self.graph.active_trip_indices(date)
        return self._active_cache[date]

    def _views(
        self,
        date: int,
        vehicle: str,
        mirror: bool = False,
        realtime: Optional[object] = None,
    ) -> dict[int, list[tuple[int, list]]]:
        """Trips actifs filtrés par mode, indexés par route.

        Chaque vue = (trip_idx, [(stop, arr, dep), ...]) triée par premier
        départ. `mirror=True` renverse les temps pour ArriveBy.

        T8 — `realtime` (flux GTFS-RT, optionnel) : applique les retards aux
        horaires et exclut les trains supprimés. Les retards > 0 sont ajoutés à
        chaque arrêt concerné du trip (un train en avance n'est pas avancé).
        """
        key = (date, vehicle, mirror, bool(realtime))
        if realtime is None and key in self._views_cache:
            return self._views_cache[key]
        rt = realtime.snapshot() if realtime is not None else None
        views: dict[int, list[tuple[int, list]]] = {}
        for trip_idx in self.active_trips(date):
            trip = self.graph.trips[trip_idx]
            if vehicle == "train_only" and trip.vehicle != "train":
                continue
            if rt is not None:
                if trip.id in rt.cancelled:
                    continue
                delays = rt.trip_delays.get(trip.id)
            else:
                delays = None
            if mirror:
                if delays is None:
                    view = [(st.stop, MAXT - st.dep, MAXT - st.arr) for st in reversed(trip.stop_times)]
                else:
                    view = [
                        (st.stop, MAXT - (st.dep + delays.get(st.stop, 0)),
                         MAXT - (st.arr + delays.get(st.stop, 0)))
                        for st in reversed(trip.stop_times)
                    ]
            else:
                if delays is None:
                    view = [(st.stop, st.arr, st.dep) for st in trip.stop_times]
                else:
                    view = [
                        (st.stop, st.arr + delays.get(st.stop, 0),
                         st.dep + delays.get(st.stop, 0))
                        for st in trip.stop_times
                    ]
            if not view:
                continue
            views.setdefault(trip.route, []).append((trip_idx, view))
        for r in views:
            views[r].sort(key=lambda tv: tv[1][0][2])
        if realtime is None:
            self._views_cache[key] = views
        return views

    # ------------------------------------------------------------- cœur
    def _rounds(
        self,
        views: dict[int, list[tuple[int, list]]],
        origins: list[int],
        dests: list[int],
        t0: int,
        max_transfers: int,
        horizon: int,
    ):
        """Balayage par rounds.

        Retourne (arr_by_round, round_parents, transfer_walk) où
        `transfer_walk[b] = (a, heure_arrivée_en_b)` signifie que `b` a été
        atteint à pied depuis `a` (arc inter-gares §5.3) — la marche ne consomme
        pas de round et doit produire un leg "walk" à la reconstruction.
        """
        graph = self.graph
        n = len(graph.stops)
        INF = float("inf")
        arr = [INF] * n
        for o in origins:
            arr[o] = float(t0)

        origin_set = set(origins)
        marked: dict[int, int] = {o: t0 for o in origins}
        transfer_walk: dict[int, tuple[int, int]] = {}
        for (a, b), minutes in graph.transfer_edges.items():
            if a in origin_set and arr[b] > t0 + minutes:
                arr[b] = t0 + minutes
                transfer_walk[b] = (a, t0 + minutes)
                marked[b] = t0 + minutes

        arr_by_round: list[list[float]] = []
        round_parents: list[dict[int, tuple[int, int]]] = []

        for _k in range(1, max_transfers + 2):
            parents: dict[int, tuple[int, int]] = {}
            new_marked: dict[int, int] = {}

            routes = set()
            for s in marked:
                routes.update(graph.routes_by_stop[s])

            for route in routes:
                on_route = self._route_stop_sets[route]
                marked_on_route = [(s, bt) for s, bt in marked.items() if s in on_route]
                if not marked_on_route:
                    continue
                for trip_idx, view in views.get(route, ()):
                    if view[0][2] > horizon:  # vues triées par 1er départ
                        break
                    # positions des arrêts marqués dans cette vue
                    for s, bt in marked_on_route:
                        for i in range(len(view)):
                            if view[i][0] == s:
                                break
                        else:
                            continue
                        if view[i][2] < bt:
                            continue
                        for j in range(i + 1, len(view)):
                            a2 = view[j][0]
                            at = view[j][1]
                            if at < arr[a2]:
                                arr[a2] = at
                                parents[a2] = (trip_idx, s)
                                # le temps d'embarquement doit suivre CHAQUE amélioration
                                # (sinon une arrivée précédente plus tardive verrouille le round suivant)
                                new_marked[a2] = at + graph.min_transfer[a2]

            # arcs de marche inter-gares (ne consomment pas de round)
            for (a, b), minutes in graph.transfer_edges.items():
                cand = arr[a] + minutes
                if cand < arr[b]:
                    arr[b] = cand
                    transfer_walk[b] = (a, cand)
                    new_marked[b] = cand + graph.min_transfer[b]

            arr_by_round.append(list(arr))
            round_parents.append(parents)
            if not new_marked:
                break
            marked = new_marked

        return arr_by_round, round_parents, transfer_walk

    # ------------------------------------------------------------- DepartAfter
    def depart_after(
        self,
        date: int,
        origins: list[int],
        dests: list[int],
        t0: int,
        max_transfers: int,
        vehicle: str = "all",
        realtime: Optional[object] = None,
    ) -> list[Journey]:
        views = self._views(date, vehicle, mirror=False, realtime=realtime)
        arr_by_round, round_parents, transfer_walk = self._rounds(
            views, origins, dests, t0, max_transfers, t0 + HORIZON_MIN
        )
        return self._pareto_journeys(
            arr_by_round, round_parents, transfer_walk, origins, dests, t0,
            mirror=False, realtime=realtime,
        )

    # ------------------------------------------------------------- DepartAfter (large)
    def depart_after_wide(
        self,
        date: int,
        origins: list[int],
        dests: list[int],
        t0: int,
        max_transfers: int,
        vehicle: str = "all",
        realtime: Optional[object] = None,
        slice_min: int = 180,
    ) -> list[Journey]:
        """RAPTOR « large » : RAPTOR classique ne garde que l'arrivée la plus
        tôt par nombre de correspondances — un trajet rapide qui part tard est
        éliminé comme dominé (départ+arrivée plus tardifs qu'un autre). En
        relançant le balayage par tranches horaires depuis t0 (ici toutes les
        `slice_min` min, horizon 36 h), on récupère les meilleurs trajets de
        chaque tranche (y compris les rapides de fin de journée), dédupliqués.
        Coût ~2-3× une passe simple ; résultats triés par (départ, arrivée)."""
        views = self._views(date, vehicle, mirror=False, realtime=realtime)
        rt = realtime.snapshot() if realtime is not None else None
        seen: set[tuple] = set()
        out: list[Journey] = []
        for start in range(t0, t0 + HORIZON_MIN + 1, slice_min):
            arr_by_round, round_parents, transfer_walk = self._rounds(
                views, origins, dests, start, max_transfers, start + HORIZON_MIN
            )
            for j in self._pareto_journeys(
                arr_by_round, round_parents, transfer_walk, origins, dests, start,
                mirror=False, realtime=rt,
            ):
                key = (j.departure, j.arrival, j.transfers,
                       tuple((l.trip_id, l.from_id, l.to_id) for l in j.legs))
                if key in seen:
                    continue
                seen.add(key)
                out.append(j)
        return sorted(out, key=lambda j: (j.departure, j.arrival))

    # ------------------------------------------------------------- ArriveBy
    def arrive_by(
        self,
        date: int,
        origins: list[int],
        dests: list[int],
        deadline: int,
        max_transfers: int,
        vehicle: str = "all",
        realtime: Optional[object] = None,
    ) -> list[Journey]:
        """Arrivée au plus tard `deadline` : part le plus tard possible.

        Renversement du temps : on cherche un trajet "miroir" depuis `dests`
        (miroir de l'origine) jusqu'à `origins` (miroir de la destination),
        partant au plus tôt à MAXT - deadline dans le temps renversé.
        """
        t0 = MAXT - deadline
        views = self._views(date, vehicle, mirror=True, realtime=realtime)
        arr_by_round, round_parents, transfer_walk = self._rounds(
            views, dests, origins, t0, max_transfers, MAXT
        )
        return self._pareto_journeys(
            arr_by_round, round_parents, transfer_walk, dests, origins, t0,
            mirror=True, realtime=realtime,
        )

    # ------------------------------------------------------------- ArriveBy (large)
    def arrive_by_wide(
        self,
        date: int,
        origins: list[int],
        dests: list[int],
        deadline: int,
        max_transfers: int,
        vehicle: str = "all",
        realtime: Optional[object] = None,
        slice_min: int = 180,
    ) -> list[Journey]:
        """ArriveBy « large » : tranches horaires du temps renversé, équivalent
        miroir de `depart_after_wide` (récupère les trajets rapides arrivant
        avant la limite, mêmes si leur départ est tôt)."""
        t0 = MAXT - deadline
        views = self._views(date, vehicle, mirror=True, realtime=realtime)
        rt = realtime.snapshot() if realtime is not None else None
        seen: set[tuple] = set()
        out: list[Journey] = []
        # temps renversé : on scanne du plus tard (départ miroir le plus tôt)
        # vers le plus tôt, en tranches.
        for start in range(t0, MAXT + 1, slice_min):
            arr_by_round, round_parents, transfer_walk = self._rounds(
                views, dests, origins, start, max_transfers, MAXT
            )
            for j in self._pareto_journeys(
                arr_by_round, round_parents, transfer_walk, dests, origins, start,
                mirror=True, realtime=rt,
            ):
                key = (j.departure, j.arrival, j.transfers,
                       tuple((l.trip_id, l.from_id, l.to_id) for l in j.legs))
                if key in seen:
                    continue
                seen.add(key)
                out.append(j)
        return sorted(out, key=lambda j: (j.departure, j.arrival))

    # ------------------------------------------------------------- Pareto
    def _pareto_journeys(
        self,
        arr_by_round: list[list[float]],
        round_parents: list[dict[int, tuple[int, int]]],
        transfer_walk: dict[int, tuple[int, int]],
        origins: list[int],
        dests: list[int],
        t0: int,
        mirror: bool,
        realtime: Optional[object] = None,
    ) -> list[Journey]:
        dest_set = set(dests)
        journeys: list[Journey] = []
        best_so_far = float("inf")
        rt = realtime.snapshot() if realtime is not None else None
        for idx, k_arr in enumerate(arr_by_round):
            rides = idx + 1
            best_dest = min(
                (d for d in dest_set if k_arr[d] < best_so_far),
                key=lambda d: k_arr[d],
                default=None,
            )
            if best_dest is None:
                continue
            journey = self._reconstruct(
                best_dest, rides, arr_by_round, round_parents, transfer_walk, origins, mirror, rt
            )
            if journey is None:
                continue
            best_so_far = min(best_so_far, k_arr[best_dest])
            journeys.append(journey)
        return journeys

    # ------------------------------------------------------------- reconstruct
    def _best_parent(self, stop, k, arr_by_round, round_parents, transfer_walk):
        """Meilleur "parent" d'un arrêt au round k (0-based).

        Retourne ("train", trip_idx, board) ou ("walk", a) — ou None. Priorité au
        mode dont l'heure d'arrivée coïncide avec l'arrivée optimale (arr_by_round),
        pour ne pas reconstruire une arrivée différente de celle annoncée (cas où
        marche et train améliorent tous deux le même arrêt dans le même round).
        """
        best = arr_by_round[k][stop]
        p = round_parents[k].get(stop)
        train = None
        if p is not None:
            trip_idx, board = p
            tarr = None
            for st in self.graph.trips[trip_idx].stop_times:
                if st.stop == stop:
                    tarr = st.arr
                    break
            train = ("train", trip_idx, board)
            if tarr is not None and abs(tarr - best) < 0.5:
                return train
        w = transfer_walk.get(stop)
        walk = None
        if w is not None:
            a, w_arr = w
            walk = ("walk", a)
            if abs(w_arr - best) < 0.5:
                return walk
        return train or walk

    def _reconstruct(
        self,
        dest: int,
        rides: int,
        arr_by_round: list[list[float]],
        round_parents: list[dict[int, tuple[int, int]]],
        transfer_walk: dict[int, tuple[int, int]],
        origins: list[int],
        mirror: bool,
        realtime: Optional[object] = None,
    ) -> Journey | None:
        graph = self.graph
        origin_set = set(origins)
        rt_delays = None
        if realtime is not None:
            rt_delays = {tid: dict(rt) for tid, rt in realtime.trip_delays.items()}
        # segs collectés en remontant de `dest` vers l'origine.
        # ('train', trip_idx, board, alight) ou ('walk', a, b, heure_arrivée_b).
        segs: list[tuple] = []
        stop = dest
        k = rides
        guard = 0
        while stop not in origin_set:
            guard += 1
            if guard > rides + len(transfer_walk) + 2:
                return None
            if k - 1 >= 0:
                parent = self._best_parent(stop, k - 1, arr_by_round, round_parents, transfer_walk)
                if parent is not None:
                    if parent[0] == "train":
                        _, trip_idx, board = parent
                        segs.append(("train", trip_idx, board, stop))
                        stop = board
                        k -= 1
                    else:
                        _, a = parent
                        w_arr = transfer_walk[stop][1]
                        segs.append(("walk", a, stop, w_arr))
                        stop = a  # la marche ne consomme pas de round
                    continue
            # k == 0 (plus de trains à consommer) : seule une marche d'origine
            # peut encore relier au point de départ.
            w = transfer_walk.get(stop)
            if w is None:
                return None
            a, w_arr = w
            segs.append(("walk", a, stop, w_arr))
            stop = a

        legs: list[Leg] = []
        walk_count = 0
        travel = segs if mirror else segs[::-1]
        for seg in travel:
            if seg[0] == "train":
                _, trip_idx, board, alight = seg
                trip_id = graph.trips[trip_idx].id
                delays = (rt_delays or {}).get(trip_id, {})
                leg = self._leg(trip_idx, alight, board) if mirror else self._leg(trip_idx, board, alight)
                if leg is not None and delays:
                    leg = self._shift_leg(leg, delays, mirror)
            else:
                walk_count += 1
                _, a, b, to_time = seg
                leg = self._walk_leg(a, b, to_time, mirror)
            if leg is None:
                return None
            legs.append(leg)

        if walk_count > MAX_WALK_LEGS:
            return None
        # Cohérence temporelle stricte (heures réelles) : `transfer_walk` étant
        # global, une chaîne de marche peut mélanger des rounds différents —
        # rejeter toute rupture (un leg qui "partirait" avant l'arrivée précédente).
        for i in range(1, len(legs)):
            if legs[i].from_time < legs[i - 1].to_time:
                return None

        departure = legs[0].from_time if legs else MAXT
        arrival = legs[-1].to_time if legs else MAXT
        return Journey(departure=departure, arrival=arrival, transfers=len(legs) - 1, legs=legs)

    def _shift_leg(self, leg: Leg, delays: dict[int, int], mirror: bool) -> Leg:
        """T8 — applique les retards GTFS-RT à un leg.

        `delays` est indexé par stop_idx (int). Le départ du leg prend le retard
        à l'arrêt d'embarquement, l'arrivée celui à l'arrêt de débarquement
        (identique à la construction des vues, pour la cohérence des horaires)."""
        from_idx = self.graph.stop_index.get(leg.from_id)
        to_idx = self.graph.stop_index.get(leg.to_id)
        if from_idx is not None:
            d_from = delays.get(from_idx, 0)
            leg.from_time += d_from
            leg.delay_min = max(leg.delay_min, d_from)
        if to_idx is not None:
            d_to = delays.get(to_idx, 0)
            leg.to_time += d_to
        return leg

    def _walk_leg(self, a: int, b: int, to_time: int, mirror: bool) -> Leg:
        graph = self.graph
        minutes = graph.transfer_edges[(a, b)]
        if mirror:
            to_time_real = MAXT - to_time
        else:
            to_time_real = to_time
        return Leg(
            type="walk",
            route_id="",
            line="",
            line_name="",
            vehicle_label="",
            trip_id="",
            from_id=graph.stops[a].id,
            from_name=graph.stops[a].name,
            from_time=to_time_real - minutes,
            to_id=graph.stops[b].id,
            to_name=graph.stops[b].name,
            to_time=to_time_real,
        )

    def _leg(self, trip_idx: int, from_stop: int, to_stop: int) -> Leg | None:
        graph = self.graph
        trip = graph.trips[trip_idx]
        route = graph.routes[trip.route]
        pb = pb2 = -1
        st = trip.stop_times
        for i, s in enumerate(st):
            if s.stop == from_stop:
                pb = i
            if s.stop == to_stop:
                pb2 = i
        if pb < 0 or pb2 < 0 or pb2 < pb:
            return None
        return Leg(
            type=trip.vehicle,
            route_id=route.id,
            line=route.short_name,
            line_name=route.long_name,
            vehicle_label=_vehicle_label(trip.id),
            trip_id=trip.id,
            from_id=graph.stops[from_stop].id,
            from_name=graph.stops[from_stop].name,
            from_time=st[pb].dep,
            to_id=graph.stops[to_stop].id,
            to_name=graph.stops[to_stop].name,
            to_time=st[pb2].arr,
        )


# ---------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import time
    from datetime import datetime
    from pathlib import Path

    from src.graph import Graph

    parser = argparse.ArgumentParser(description="Moteur McRAPTOR TER (T3)")
    parser.add_argument("--graph", type=Path, default=Path("data/graph.bin"))
    parser.add_argument("--from")
    parser.add_argument("--to")
    parser.add_argument("--date", required=True)
    parser.add_argument("--time", default="08:00")
    parser.add_argument("--mode", choices=["depart", "arrive"], default="depart")
    parser.add_argument("--max-transfers", type=int, default=6)
    parser.add_argument("--vehicle", choices=["all", "train_only"], default="all")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    import sys

    if not getattr(args, 'from') or not getattr(args, 'to'):
        parser.error("--from et --to sont requis")

    graph = Graph.load(args.graph.resolve())
    engine = RaptorEngine(graph)

    origins = graph.resolve_place(getattr(args, 'from'))
    dests = graph.resolve_place(getattr(args, 'to'))
    if not origins or not dests:
        print(f"Gare introuvable : {'de ' + getattr(args, 'from') if not origins else 'à ' + getattr(args, 'to')}")
        return 1

    d = datetime.strptime(args.date, "%Y-%m-%d").date()
    date_int = int(args.date.replace("-", ""))
    hh, mm = map(int, args.time.split(":"))
    t = hh * 60 + mm

    t0 = time.perf_counter()
    if args.mode == "depart":
        journeys = engine.depart_after(date_int, origins, dests, t, args.max_transfers, args.vehicle)
    else:
        journeys = engine.arrive_by(date_int, origins, dests, t, args.max_transfers, args.vehicle)
    dt_ms = (time.perf_counter() - t0) * 1000

    if args.json:
        query = {k: (str(v) if isinstance(v, (Path,)) else v) for k, v in vars(args).items()}
        print(json.dumps({"query": query, "engine_ms": round(dt_ms, 1), "journeys": [j.to_json(d) for j in journeys]}, ensure_ascii=False, indent=2))
        return 0

    print(f"[raptor] {getattr(args, 'from')} -> {getattr(args, 'to')} | {args.date} {args.time} | mode={args.mode} | max_transfers={args.max_transfers} | {dt_ms:.0f} ms")
    if not journeys:
        print("  Aucun trajet trouvé.")
        return 0
    for j in journeys:
        print(f"  ✓ {j.transfers} correspondance(s) : départ {_fmt(j.departure)} -> arrivée {_fmt(j.arrival)} (durée {j.duration_min} min)")
        for leg in j.legs:
            print(f"      {_fmt(leg.from_time)} {leg.from_name} [{leg.line} {leg.vehicle_label}] -> {_fmt(leg.to_time)} {leg.to_name}")
    return 0


def _fmt(minutes: int) -> str:
    return f"{int(minutes) // 60:02d}:{int(minutes) % 60:02d}"


if __name__ == "__main__":
    raise SystemExit(main())
