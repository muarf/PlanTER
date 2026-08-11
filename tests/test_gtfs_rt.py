"""T8 — Tests du module GTFS-RT (parsing, mapping stop, filtres).

Exécution : .venv/bin/python -m unittest tests.test_gtfs_rt -v

Construit un FeedMessage GTFS-RT en mémoire avec un retard et une
suppression, puis vérifie la réduction du flux (retards par stop_idx,
trains supprimés, trips hors graphe ignorés).
"""
import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.transit import gtfs_realtime_pb2 as _pb2

from src import gtfs_rt
from src.graph import Graph

DATA = Path(__file__).resolve().parents[1] / "data" / "graph.bin"


def _feed_message() -> _pb2.FeedMessage:
    fm = _pb2.FeedMessage()
    fm.header.gtfs_realtime_version = "2.0"
    fm.header.incrementality = _pb2.FeedHeader.FULL_DATASET
    fm.header.timestamp = int(dt.datetime.now(dt.timezone.utc).timestamp())
    return fm


class GtfsRtTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = Graph.load(DATA)

    def _real_trip(self):
        """Un trip réel du graphe (Dijon -> Besançon, C1) et ses arrêts."""
        idx = self.g.resolve_place("Dijon")[0]
        trip_idx = None
        for tidx in self.g.active_trip_indices(20260810):
            t = self.g.trips[tidx]
            if t.stop_times and t.stop_times[0].stop == idx and len(t.stop_times) > 2:
                trip_idx = tidx
                break
        return self.g.trips[trip_idx]

    def test_retard_applique_par_stop(self):
        trip = self._real_trip()
        stops = trip.stop_times
        # Retard de 10 min au 1er arrêt, 15 min au 2e.
        stop1 = self.g.stops[stops[0].stop]
        stop2 = self.g.stops[stops[1].stop]
        uic1 = stop1.id.removeprefix("StopArea:OCE")
        uic2 = stop2.id.removeprefix("StopArea:OCE")

        fm = _feed_message()
        e = fm.entity.add()
        e.id = "1"
        e.trip_update.trip.trip_id = trip.id
        e.trip_update.trip.start_date = "20260810"
        u1 = e.trip_update.stop_time_update.add()
        u1.stop_id = f"StopPoint:OCETrain TER-{uic1}"
        u1.arrival.delay = 600
        u2 = e.trip_update.stop_time_update.add()
        u2.stop_id = f"StopPoint:OCETrain TER-{uic2}"
        u2.departure.delay = 900

        feed = gtfs_rt.parse_trip_updates(fm.SerializeToString(), self.g)
        key = (trip.id, 20260810)
        self.assertIn(key, feed.trip_delays)
        delays = feed.trip_delays[key]
        self.assertEqual(delays[stops[0].stop], 10)
        self.assertEqual(delays[stops[1].stop], 15)

    def test_trip_annule(self):
        trip = self._real_trip()
        fm = _feed_message()
        e = fm.entity.add()
        e.id = "1"
        e.trip_update.trip.trip_id = trip.id
        e.trip_update.trip.start_date = "20260810"
        e.trip_update.trip.schedule_relationship = _pb2.TripDescriptor.CANCELED
        feed = gtfs_rt.parse_trip_updates(fm.SerializeToString(), self.g)
        key = (trip.id, 20260810)
        self.assertIn(key, feed.cancelled)
        self.assertNotIn(key, feed.trip_delays)

    def test_trip_hors_graphe_ignore(self):
        """Un trip non présent dans le graphe (ex. TER d'une autre période)
        est ignoré : pas de crash, pas d'entrée."""
        fm = _feed_message()
        e = fm.entity.add()
        e.id = "1"
        e.trip_update.trip.trip_id = "OCEX:TER:FR:Line::ZZZ::0:0:0:0:0:19990101"
        e.trip_update.stop_time_update.add().stop_id = "StopPoint:OCETrain TER-00000000"
        feed = gtfs_rt.parse_trip_updates(fm.SerializeToString(), self.g)
        self.assertNotIn(e.trip_update.trip.trip_id, feed.trip_delays)
        self.assertNotIn(e.trip_update.trip.trip_id, feed.cancelled)

    def test_sans_ter_ignore(self):
        """Les entrées hors TER (ex. Intercités, id sans ':TER:') sont ignorées."""
        fm = _feed_message()
        e = fm.entity.add()
        e.id = "1"
        e.trip_update.trip.trip_id = "OCEIC123:Intercites:FR::ZZZ::0:0:0:0:0:20260810"
        e.trip_update.stop_time_update.add().arrival.delay = 60
        feed = gtfs_rt.parse_trip_updates(fm.SerializeToString(), self.g)
        self.assertEqual(feed.trip_delays, {})

    def test_stop_non_mappe_ignore(self):
        """Un stop_id du flux non mappé sur le graphe est ignoré (pas de clé)."""
        trip = self._real_trip()
        fm = _feed_message()
        e = fm.entity.add()
        e.id = "1"
        e.trip_update.trip.trip_id = trip.id
        e.trip_update.trip.start_date = "20260810"
        u = e.trip_update.stop_time_update.add()
        u.stop_id = "StopPoint:OCETrain TER-00000000"  # UIC inexistant
        u.arrival.delay = 600
        feed = gtfs_rt.parse_trip_updates(fm.SerializeToString(), self.g)
        # trip connu mais aucun retard mappable -> absent de trip_delays
        self.assertNotIn((trip.id, 20260810), feed.trip_delays)

    def test_snapshot_isole(self):
        feed = gtfs_rt.RealtimeFeed(trip_delays={("t1", 20260810): {1: 5}}, cancelled={("t2", 20260810)})
        snap = feed.snapshot()
        snap.trip_delays[("t1", 20260810)][1] = 99
        snap.cancelled.add(("t3", 20260810))
        self.assertEqual(feed.trip_delays[("t1", 20260810)][1], 5)
        self.assertNotIn(("t3", 20260810), feed.cancelled)

    def test_age_fraicheur(self):
        import time

        feed = gtfs_rt.RealtimeFeed(fetched_at=time.time())
        self.assertTrue(feed.is_fresh)
        feed.fetched_at = time.time() - 10 * 60
        self.assertFalse(feed.is_fresh)


class ServiceAlertsTestCase(unittest.TestCase):
    """T8 — Parsing Service Alerts : cibles (stop/train/général), période,
    traduction, pertinence pour un trajet."""

    @classmethod
    def setUpClass(cls):
        cls.g = Graph.load(DATA)
        cls._trip = cls._real_trip(cls.g)

    @staticmethod
    def _real_trip(g):
        """Un trip réel du graphe (numéro de train exploitable)."""
        for tidx in g.active_trip_indices(20260810):
            t = g.trips[tidx]
            if t.stop_times and len(t.stop_times) > 2:
                return t
        raise AssertionError("aucun trip de test")

    def _alert(self, entity_id="a1", trip_id=None, stop_id=None,
               cause=_pb2.Alert.MAINTENANCE, start=None, end=None):
        fm = _feed_message()
        e = fm.entity.add()
        e.id = entity_id
        e.alert.header_text.translation.add(language="fr", text="Travaux en cours")
        e.alert.description_text.translation.add(language="fr", text="Des travaux ont lieu <b>ici</b>.")
        if trip_id:
            e.alert.informed_entity.add().trip.trip_id = trip_id
        if stop_id:
            e.alert.informed_entity.add().stop_id = stop_id
        e.alert.cause = cause
        if start is not None:
            p = e.alert.active_period.add()
            p.start = start
            if end is not None:
                p.end = end
        elif end is not None:
            e.alert.active_period.add().end = end
        return fm

    def test_alerte_par_train(self):
        """Une alerte ciblée sur un numéro de train (sans date) est retenue,
        avec le numéro extrait du trip_id."""
        trip = self._trip
        m = gtfs_rt._TRAIN_NO_RE.match(trip.id)
        self.assertIsNotNone(m, f"trip_id inattendu : {trip.id}")
        fm = self._alert(trip_id=trip.id)
        feed = gtfs_rt.parse_service_alerts(fm.SerializeToString(), self.g)
        self.assertEqual(len(feed.alerts), 1)
        a = feed.alerts[0]
        self.assertFalse(a.general)
        self.assertIn(m.group(1), a.train_numbers)
        self.assertIn("Travaux", a.header)
        self.assertNotIn("<b>", a.description)

    def test_alerte_par_stop_mappe(self):
        """Une alerte ciblée sur un stop_id du graphe (StopArea:OCE<uic8>)
        est retenue avec son stop_idx."""
        trip = self._trip
        uic = trip.stop_times[0].stop
        stop = self.g.stops[uic]
        fm = self._alert(stop_id=stop.id)
        feed = gtfs_rt.parse_service_alerts(fm.SerializeToString(), self.g)
        self.assertEqual(len(feed.alerts), 1)
        self.assertIn(uic, feed.alerts[0].stops)

    def test_alerte_generale(self):
        """Une alerte sans informed_entity est générale (toutes lignes)."""
        fm = self._alert()
        feed = gtfs_rt.parse_service_alerts(fm.SerializeToString(), self.g)
        self.assertEqual(len(feed.alerts), 1)
        self.assertTrue(feed.alerts[0].general)

    def test_periode_hors_activite_ignoree(self):
        """Une alerte hors de sa période d'activité est ignorée."""
        import time

        now = int(time.time())
        fm = self._alert(start=now - 3600, end=now - 1800)
        feed = gtfs_rt.parse_service_alerts(fm.SerializeToString(), self.g)
        self.assertEqual(len(feed.alerts), 0)

    def test_periode_bornee_active(self):
        """Une alerte dont la période englobe maintenant est retenue."""
        import time

        now = int(time.time())
        fm = self._alert(start=now - 3600, end=now + 3600)
        feed = gtfs_rt.parse_service_alerts(fm.SerializeToString(), self.g)
        self.assertEqual(len(feed.alerts), 1)

    def test_alerte_commence_apres(self):
        """Une alerte à venir (start futur) n'est pas encore active."""
        import time

        now = int(time.time())
        fm = self._alert(start=now + 3600)
        feed = gtfs_rt.parse_service_alerts(fm.SerializeToString(), self.g)
        self.assertEqual(len(feed.alerts), 0)

    def test_sans_header_ignore(self):
        """Une entrée sans header_text est ignorée (feed annexe)."""
        fm = _feed_message()
        e = fm.entity.add()
        e.id = "x"
        feed = gtfs_rt.parse_service_alerts(fm.SerializeToString(), self.g)
        self.assertEqual(len(feed.alerts), 0)

    def test_relevant_par_train_et_gare(self):
        """relevant() croise les gares et numéros de train du trajet."""
        trip = self._trip
        m = gtfs_rt._TRAIN_NO_RE.match(trip.id)
        stop0 = trip.stop_times[0].stop
        # une alerte sur notre train + une sur notre gare + une générale
        fm = _feed_message()
        e = fm.entity.add()
        e.id = "a-train"
        e.alert.header_text.translation.add(language="fr", text="Train")
        e.alert.informed_entity.add().trip.trip_id = trip.id
        e = fm.entity.add()
        e.id = "a-stop"
        e.alert.header_text.translation.add(language="fr", text="Gare")
        e.alert.informed_entity.add().stop_id = self.g.stops[stop0].id
        e = fm.entity.add()
        e.id = "a-general"
        e.alert.header_text.translation.add(language="fr", text="Générale")
        feed = gtfs_rt.parse_service_alerts(fm.SerializeToString(), self.g)

        rel = feed.relevant([stop0], [m.group(1)])
        self.assertEqual({a.id for a in rel}, {"a-train", "a-stop"})
        # sans general, l'alerte générale n'apparaît pas
        with_general = feed.relevant([stop0], [m.group(1)], include_general=True)
        self.assertEqual({a.id for a in with_general}, {"a-train", "a-stop", "a-general"})
        # autre gare / autre train -> rien
        self.assertEqual(feed.relevant([], ["ZZZZ"]), [])


if __name__ == "__main__":
    unittest.main()
