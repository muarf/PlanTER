"""T4 — Golden tests de bout en bout (table datée + contrôles croisés GTFS).

Exécution : python3 -m unittest tests.golden_tests -v

Table de cas vérifiés manuellement (parité 16/16 avec l'oracle connectivity_check,
2026-08-10). Les cas sont DATÉS : le direct Paris→Vittel (K6) ne circule que les
week-ends — vérifié le lundi 10/08 (aucun direct) et le dimanche 16/08 (direct).

Contrôles de cohérence appliqués à chaque itinéraire retourné :
- heures strictement croissantes (from_time >= to_time du leg précédent) ;
- continuité des gares entre legs consécutifs (from_id == to_id précédent) ;
- correspondance minimum respectée (min_transfer §5.3) à chaque embarquement train ;
- `transfers == len(legs) - 1` et horaires cohérents avec la structure du Journey ;
- legs de marche bien formés (type "walk", champs ligne vides).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.graph import Graph
from src.raptor import RaptorEngine

DATA = Path(__file__).resolve().parents[1] / "data" / "graph.bin"

LUNDI = 20260810
DIMANCHE = 20260816


def _m(hh, mm):
    return hh * 60 + mm


# ------------------------------------------------------------------ table dorée
# (date, origine, destination, t0, attendu)
# attendu : ("none",)                    → aucun trajet
#           ("exact", tr, dep, arr)      → meilleur trajet exact
#           ("min_tr", n)                → trajet trouvé, >= n correspondances
GOLDEN = [
    # date 2026-08-10 (lundi)
    (LUNDI, "Besançon Viotte", "Dijon", _m(12, 0), ("exact", 0, _m(12, 6), _m(13, 2))),
    (LUNDI, "Paris Gare de Lyon", "Besançon Viotte", _m(7, 0), ("exact", 1, _m(7, 34), _m(12, 4))),
    (LUNDI, "Besançon Viotte", "Paris", _m(18, 0), ("exact", 1, _m(18, 16), _m(22, 37))),
    (LUNDI, "Paris", "Mulhouse", _m(6, 0), ("exact", 0, _m(6, 5), _m(11, 13))),
    (LUNDI, "Paris", "Grenoble", _m(7, 0), ("exact", 2, _m(7, 34), _m(24, 31))),
    (LUNDI, "Paris Bercy", "Dijon", _m(7, 0), ("exact", 1, _m(7, 0), _m(10, 33))),
    (LUNDI, "Paris Gare de Lyon", "Nevers", _m(8, 0), ("exact", 1, _m(8, 0), _m(11, 37))),
    (LUNDI, "Toulouse Matabiau", "Clermont-Ferrand", _m(8, 0), ("exact", 0, _m(13, 3), _m(19, 4))),
    (LUNDI, "Lyon Part Dieu", "Lille Flandres", _m(7, 0), ("none",)),
    (LUNDI, "Paris", "Nice", _m(8, 0), ("none",)),
    # le direct Paris→Vittel (K6) ne circule PAS le lundi
    (LUNDI, "Paris", "Vittel", _m(8, 0), ("min_tr", 1)),
    # date 2026-08-16 (dimanche) : direct K6 N840451 08:21 -> 12:42
    (DIMANCHE, "Paris", "Vittel", _m(8, 0), ("exact", 0, _m(8, 21), _m(12, 42))),
    # ArriveBy : même itinéraire que DepartAfter (partir au plus tard reste 07:34)
    (LUNDI, "Paris Gare de Lyon", "Besançon Viotte", _m(13, 0), ("arrive_exact", 1, _m(7, 34), _m(12, 4))),
]


class GoldenRaptorCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = Graph.load(DATA)
        cls.e = RaptorEngine(cls.g)

    def resolve(self, q):
        idx = self.g.resolve_place(q)
        self.assertTrue(idx, f"gare introuvable : {q!r}")
        return idx

    def run_case(self, date, orig, dest, t0, expected):
        o = self.resolve(orig)
        d = self.resolve(dest)
        if expected[0] == "arrive_exact":
            journeys = self.e.arrive_by(date, o, d, t0, 3)
        else:
            journeys = self.e.depart_after(date, o, d, t0, 3)

        kind = expected[0]
        if kind == "none":
            self.assertEqual(journeys, [], f"{orig}->{dest} {date} : trajet inattendu")
            return

        self.assertTrue(journeys, f"{orig}->{dest} {date} : aucun trajet trouvé")
        j = journeys[0]
        self._check_journey(j, date, orig, dest)

        if kind == "min_tr":
            self.assertGreaterEqual(j.transfers, expected[1],
                                    f"{orig}->{dest} {date} : direct inattendu (service week-end seulement)")
            return

        _, tr, dep, arr = expected
        self.assertEqual((j.transfers, j.departure, j.arrival), (tr, dep, arr),
                         f"{orig}->{dest} {date} : {j}")

        if kind == "exact" and expected == (LUNDI, "Paris Bercy", "Dijon", _m(7, 0), ("exact", 1, _m(7, 0), _m(10, 33))):
            self.assertEqual(j.legs[0].type, "walk")
        if kind == "exact" and expected == (LUNDI, "Paris Gare de Lyon", "Nevers", _m(8, 0), ("exact", 1, _m(8, 0), _m(11, 37))):
            self.assertEqual(j.legs[0].type, "walk")

    # ------------------------------------------------------------- cohérence
    def _check_journey(self, j, date, orig, dest):
        g, e = self.g, self.e
        self.assertGreaterEqual(len(j.legs), 1)
        self.assertEqual(j.transfers, len(j.legs) - 1)
        self.assertEqual(j.departure, j.legs[0].from_time)
        self.assertEqual(j.arrival, j.legs[-1].to_time)
        prev_to = -1
        prev_to_id = None
        for leg in j.legs:
            self.assertIsNotNone(leg.from_id)
            self.assertIsNotNone(leg.to_id)
            if prev_to_id is not None:
                self.assertEqual(leg.from_id, prev_to_id,
                                 "rupture de gare entre legs consécutifs")
            # heures croissantes
            self.assertGreaterEqual(leg.from_time, prev_to,
                                    f"leg {leg} part avant l'arrivée précédente")
            self.assertGreaterEqual(leg.to_time, leg.from_time)
            # correspondance minimum à l'embarquement d'un train
            if leg.type == "train":
                gap = leg.from_time - prev_to
                mt = g.min_transfer[g.stop_by_id(leg.from_id)]
                self.assertGreaterEqual(gap, mt,
                                        f"correspondance trop courte à {leg.from_name} ({gap}<{mt})")
            if leg.type == "walk":
                self.assertEqual(leg.route_id, "")
                self.assertEqual(leg.line, "")
                self.assertEqual(leg.trip_id, "")
            prev_to = leg.to_time
            prev_to_id = leg.to_id

    # ----------------------------------------------------------- table dorée
    def test_golden_table(self):
        for case in GOLDEN:
            with self.subTest(case=case):
                date, orig, dest, t0, expected = case
                self.run_case(date, orig, dest, t0, expected)

    # ------------------------------------------------- sweeps de cohérence
    SWEEP = [
        (LUNDI, "Paris Gare de Lyon", "Besançon Viotte", _m(7, 0)),
        (LUNDI, "Besançon Viotte", "Paris", _m(18, 0)),
        (LUNDI, "Paris", "Grenoble", _m(7, 0)),
        (LUNDI, "Paris Bercy", "Mulhouse", _m(7, 0)),
        (LUNDI, "Dijon", "Avignon Centre", _m(8, 0)),
        (LUNDI, "Paris", "Lyon Perrache", _m(8, 0)),
        (DIMANCHE, "Paris", "Vittel", _m(8, 0)),
        (DIMANCHE, "Paris Gare de Lyon", "Lyon Perrache", _m(9, 0)),
    ]

    def test_coherence_sweep(self):
        for date, orig, dest, t0 in self.SWEEP:
            with self.subTest(case=(date, orig, dest)):
                journeys = self.e.depart_after(date, self.resolve(orig), self.resolve(dest), t0, 3)
                for j in journeys:
                    self._check_journey(j, date, orig, dest)


if __name__ == "__main__":
    unittest.main()
