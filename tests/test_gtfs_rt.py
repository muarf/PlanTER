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
        u1 = e.trip_update.stop_time_update.add()
        u1.stop_id = f"StopPoint:OCETrain TER-{uic1}"
        u1.arrival.delay = 600
        u2 = e.trip_update.stop_time_update.add()
        u2.stop_id = f"StopPoint:OCETrain TER-{uic2}"
        u2.departure.delay = 900

        feed = gtfs_rt.parse_trip_updates(fm.SerializeToString(), self.g)
        self.assertIn(trip.id, feed.trip_delays)
        delays = feed.trip_delays[trip.id]
        self.assertEqual(delays[stops[0].stop], 10)
        self.assertEqual(delays[stops[1].stop], 15)

    def test_trip_annule(self):
        trip = self._real_trip()
        fm = _feed_message()
        e = fm.entity.add()
        e.id = "1"
        e.trip_update.trip.trip_id = trip.id
        e.trip_update.trip.schedule_relationship = _pb2.TripDescriptor.CANCELED
        feed = gtfs_rt.parse_trip_updates(fm.SerializeToString(), self.g)
        self.assertIn(trip.id, feed.cancelled)
        self.assertNotIn(trip.id, feed.trip_delays)

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
        u = e.trip_update.stop_time_update.add()
        u.stop_id = "StopPoint:OCETrain TER-00000000"  # UIC inexistant
        u.arrival.delay = 600
        feed = gtfs_rt.parse_trip_updates(fm.SerializeToString(), self.g)
        # trip connu mais aucun retard mappable -> absent de trip_delays
        self.assertNotIn(trip.id, feed.trip_delays)

    def test_snapshot_isole(self):
        feed = gtfs_rt.RealtimeFeed(trip_delays={"t1": {1: 5}}, cancelled={"t2"})
        snap = feed.snapshot()
        snap.trip_delays["t1"][1] = 99
        snap.cancelled.add("t3")
        self.assertEqual(feed.trip_delays["t1"][1], 5)
        self.assertNotIn("t3", feed.cancelled)

    def test_age_fraicheur(self):
        import time

        feed = gtfs_rt.RealtimeFeed(fetched_at=time.time())
        self.assertTrue(feed.is_fresh)
        feed.fetched_at = time.time() - 10 * 60
        self.assertFalse(feed.is_fresh)


if __name__ == "__main__":
    unittest.main()
