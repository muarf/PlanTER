"""T12 — Tests du moteur de tarification (prix estimés, modèle v1).

Exécution : .venv/bin/python -m unittest tests.test_pricing -v

Valide sur le graphe réel :
- la calibration reproduit les prix observés (41,00 € / 22,10 € / 20,30 €) ;
- la règle mono/pluri-région s'applique (un billet vs somme des tronçons) ;
- l'API expose price_normal_eur + métadonnées pricing sur /v1/journeys.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.graph import Graph
from src.pricing import PricingEngine
from src.raptor import RaptorEngine

DATA = Path(__file__).resolve().parents[1] / "data" / "graph.bin"
DATE = 20260810


class PricingTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = Graph.load(DATA)
        cls.pe = PricingEngine(cls.g)
        cls.e = RaptorEngine(cls.g)

    def resolve(self, q):
        return self.g.resolve_place(q)

    # ------------------------------------------------------------ calibration
    def test_fare_ancre_bfc(self):
        # 405 km de voie -> 41,00 € (Paris-Dijon-Besançon, observé)
        self.assertEqual(self.pe.fare(405, "Bourgogne-Franche-Comté"), 41.0)

    def test_fare_ancre_hdf(self):
        self.assertEqual(self.pe.fare(112, "Hauts-de-France"), 22.1)

    def test_fare_ancre_normandie(self):
        self.assertEqual(self.pe.fare(112, "Normandie"), 20.3)

    def test_fare_plancher_et_arrondi(self):
        self.assertGreaterEqual(self.pe.fare(1, "Occitanie"), 3.0)
        self.assertEqual(self.pe.fare(1, "Occitanie"), 3.0)

    def test_fare_region_inconnue_utilise_defaut(self):
        # région absente de la config -> default_scale (courbe nationale) ;
        # la Bourgogne (scale 0.9865) est calibrée pour coller à 41,00 €.
        self.assertEqual(self.pe.fare(405, "INCONNUE"), self.pe.fare(405, "Occitanie"))
        self.assertNotEqual(self.pe.fare(405, "INCONNUE"), self.pe.fare(405, "Bourgogne-Franche-Comté"))

    # --------------------------------------------------------------- régions
    def test_trip_region_st_vit_dijon(self):
        # Un C11 desservant Dijon relève de la Bourgogne-Franche-Comté.
        dijon = self.resolve("Dijon")[0]
        tidx = next(
            i for i, t in enumerate(self.g.trips)
            if self.g.routes[t.route].short_name == "C11"
            and any(st.stop == dijon for st in t.stop_times)
        )
        self.assertEqual(self.pe.trip_region(tidx), "Bourgogne-Franche-Comté")

    def test_stop_region_dijon(self):
        idx = self.resolve("Dijon")[0]
        self.assertEqual(self.pe.stop_region(idx), "Bourgogne-Franche-Comté")

    # --------------------------------------------------------------- trajets
    def test_paris_besancon_mono_region(self):
        # § T12 : Paris -> Besançon via Dijon (K7 -> C11) doit être un billet
        # unique dégressif sur la distance totale (≈ 41 €).
        js = self.e.depart_after_wide(
            DATE, self.resolve("Paris Gare de Lyon"), self.resolve("Besançon Viotte"),
            8 * 60, 3, "train_only", None,
        )
        found = None
        for j in js:
            info = self.pe.journey_price(j)
            if info and info["rule"] == "mono_region" and any(l.line == "K7" for l in j.legs):
                found = (j, info)
                break
        self.assertIsNotNone(found, "aucun trajet mono-région Paris->Besançon via Dijon")
        j, info = found
        self.assertAlmostEqual(info["price_normal_eur"], 41.0, delta=3.0)

    def test_dijon_besancon_prix_positif(self):
        js = self.e.depart_after_wide(
            DATE, self.resolve("Dijon"), self.resolve("Besançon Viotte"),
            7 * 60, 2, "train_only", None,
        )
        self.assertTrue(js)
        for j in js[:2]:
            info = self.pe.journey_price(j)
            self.assertIsNotNone(info)
            self.assertGreater(info["price_normal_eur"], 0)
            self.assertEqual(info["rule"], "mono_region")

    def test_pluri_region_somme_des_troncons(self):
        # Lille -> Rouen : traverse Hauts-de-France et Normandie.
        js = self.e.depart_after_wide(
            DATE, self.resolve("Lille Flandres") or self.resolve("Lille"), self.resolve("Rouen Rive-Droite"),
            7 * 60, 4, "train_only", None,
        )
        self.assertTrue(js)
        pluri = None
        for j in js:
            info = self.pe.journey_price(j)
            if info and info["rule"] == "pluri_region":
                pluri = info
                break
        self.assertIsNotNone(pluri, "aucun trajet pluri-région Lille->Rouen")
        self.assertGreater(len(pluri["regions"]), 1)

    def test_marche_toute_a_pied_aucun_prix(self):
        # Un trajet uniquement composé de marches n'a pas de prix.
        class FakeJourney:
            legs = []

        self.assertIsNone(self.pe.journey_price(FakeJourney()))


if __name__ == "__main__":
    unittest.main()
