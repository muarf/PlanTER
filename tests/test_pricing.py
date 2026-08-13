"""T12 — Tests du moteur de tarification (prix estimés, modèle v1).

Exécution : .venv/bin/python -m unittest tests.test_pricing -v

Valide sur le graphe réel :
- la calibration reproduit les prix observés (41,00 € / 22,10 € / 20,30 €) ;
- la règle mono/pluri-région s'applique (un billet vs somme des tronçons) ;
- les cartes de réduction réduisent le tarif selon leur région d'application ;
- l'API expose price_normal_eur / price_reduced_eur + métadonnées pricing.
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

# Ids Trainline (config/trainline_cards.json).
BFC_26 = "5be729fcfc26caa921c53f6d836175d832c288ca"
BFC_SOLIDAIRE = "2a730e22c0be4cf0030f89205f540fe39e8dca6b"
HDF = "7d76cf68a01468562cde7088753e8a4673fcda30"
ZOU_ETUDES = "d55f73482d9d2d0f7ccc92b83a13cc0a9b1d788d"
ABO_TEMPO = "8343c7a009b1f83bccd79d21150724227a19e3ad"


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

    # ------------------------------------------------------- cartes réductions
    def test_card_info_bfc(self):
        # Carte BFC 26+ : -60% we / -30% semaine -> pay représentatif 0.40 ;
        # carte solidaire BFC : -75% -> 0.25 (dérogation by_id).
        info = self.pe.card_info({"id": BFC_26, "name": "Carte Bourgogne-Franche-Comté 26+"})
        self.assertEqual(info["region"], "Bourgogne-Franche-Comté")
        self.assertEqual(info["pay"], 0.4)
        info_sol = self.pe.card_info({"id": BFC_SOLIDAIRE, "name": "Carte Bourgogne-Franche-Comté tarif réduit solidaire"})
        self.assertEqual(info_sol["pay"], 0.25)

    def test_card_info_abonnement_sans_reduction(self):
        # Un abonnement n'a pas de réduction par billet unitaire.
        info = self.pe.card_info({"id": ABO_TEMPO, "name": "Abonnement Normandie Tempo +26"})
        self.assertIsNone(info["pay"])

    def test_card_info_zou_etudes_moins_50(self):
        # Carte ZOU! Études : -50 % sur les autres trajets (cohérent cards.html).
        info = self.pe.card_info({"id": ZOU_ETUDES, "name": "Carte Région Sud (PACA) ZOU! Études"})
        self.assertEqual(info["pay"], 0.5)
        self.assertEqual(info["region"], "Provence-Alpes-Côte d'Azur")

    def test_card_info_region_inconnue(self):
        # Sans mot-clé de région, la carte n'est applicable nulle part.
        info = self.pe.card_info({"id": "x" * 40, "name": "Pass Sûreté"})
        self.assertIsNone(info["pay"])
        self.assertEqual(info["region"], "INCONNUE")

    def test_dijon_besancon_carte_bfc(self):
        # Dijon -> Besançon : 20,00 € plein tarif ; solidaire BFC (-75%) -> 5,00 €.
        js = self.e.depart_after_wide(
            DATE, self.resolve("Dijon"), self.resolve("Besançon Viotte"),
            7 * 60, 2, "train_only", None,
        )
        self.assertTrue(js)
        info = self.pe.journey_price(js[0])
        self.assertEqual(info["price_normal_eur"], 20.0)
        red = self.pe.journey_price(js[0], cards=[BFC_SOLIDAIRE])
        self.assertEqual(red["price_reduced_eur"], 5.0)
        self.assertEqual([c["shortName"] for c in red["cards"]], ["Tarif réduit solidaire"])

    def test_meilleure_carte_gagne(self):
        # Parmi deux cartes BFC, la plus avantageuse s'applique (solidaire < 26+).
        js = self.e.depart_after_wide(
            DATE, self.resolve("Dijon"), self.resolve("Besançon Viotte"),
            7 * 60, 2, "train_only", None,
        )
        red = self.pe.journey_price(js[0], cards=[BFC_26, BFC_SOLIDAIRE])
        self.assertEqual(red["price_reduced_eur"], 5.0)

    def test_carte_hors_region_sans_effet(self):
        # Une carte HdF ne réduit pas un trajet bourguignon.
        js = self.e.depart_after_wide(
            DATE, self.resolve("Dijon"), self.resolve("Besançon Viotte"),
            7 * 60, 2, "train_only", None,
        )
        red = self.pe.journey_price(js[0], cards=[HDF])
        self.assertEqual(red["price_reduced_eur"], red["price_normal_eur"])
        self.assertEqual(red["cards"], [])

    def test_pluri_region_carte_reduit_son_troncon_seulement(self):
        # Lille -> Rouen (HdF + Normandie) avec Ma Carte TER HdF : le tronçon
        # haut-de-français est divisé par 2, le tronçon normand reste plein tarif.
        js = self.e.depart_after_wide(
            DATE, self.resolve("Lille Flandres") or self.resolve("Lille"), self.resolve("Rouen Rive-Droite"),
            7 * 60, 4, "train_only", None,
        )
        found = None
        for j in js:
            info = self.pe.journey_price(j, cards=[HDF])
            if info and info["rule"] == "pluri_region" and len(info["cards"]) == 1:
                found = (info, j)
                break
        self.assertIsNotNone(found, "aucun trajet pluri-région Lille->Rouen réductible")
        info, j = found
        base = self.pe.journey_price(j)
        self.assertLess(info["price_reduced_eur"], base["price_normal_eur"])
        # le tronçon normand reste au plein tarif : la réduction ne porte que
        # sur le tronçon haut-de-français (pay 0.50).
        hdf_km = next(l["km"] for l in base["legs"] if l["region"] == "Hauts-de-France")
        norm_km = next(l["km"] for l in base["legs"] if l["region"] == "Normandie")
        expected = round(round(self.pe.fare(hdf_km, "Hauts-de-France") * 0.5 / 0.05) * 0.05, 2) + self.pe.fare(norm_km, "Normandie")
        self.assertAlmostEqual(info["price_reduced_eur"], round(expected, 2), delta=0.01)

    # ------------------------------------------------------- §32 split intra-train
    ILLICO_SOLIDAIRE = "illico-solidaire"

    def _k7_0734(self):
        """Trajet Paris Gare de Lyon -> Lyon Part Dieu sur le TER K7 de 07h34."""
        js = self.e.depart_after_wide(
            DATE, self.resolve("Paris Gare de Lyon"), self.resolve("Lyon Part Dieu"),
            7 * 60, 2, "train_only", None,
        )
        return next(
            (j for j in js if j.legs and j.legs[0].line == "K7" and j.legs[0].from_time == 454),
            None,
        )

    def test_k7_paris_lyon_jonction_macon(self):
        # Le TER K7 de 07h34 traverse la BFC puis l'ARA : la jonction se situe
        # à Mâcon (dernier arrêt BFC avant Belleville-sur-Saône, Rhône).
        j = self._k7_0734()
        self.assertIsNotNone(j, "TER K7 07:34 Paris GDL -> Lyon introuvable")
        leg = j.legs[0]
        tidx = self.g.trip_index[leg.trip_id]
        segs = self.pe.trip_region_segments(tidx, self.g.stop_index[leg.from_id], self.g.stop_index[leg.to_id])
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0]["region"], "Bourgogne-Franche-Comté")
        self.assertEqual(segs[0]["to"]["name"], "Mâcon")
        self.assertEqual(segs[1]["region"], "Auvergne-Rhône-Alpes")
        self.assertEqual(segs[1]["to"]["name"], "Lyon Part Dieu")

    def test_k7_paris_lyon_split_annonce_et_deux_cartes(self):
        # Avec BFC solidaire + illico solidaire : le découpage à Mâcon est
        # annoncé, chaque segment est réduit par la carte de sa région, et le
        # prix mono-région (billet unique dégressif) reste inchangé.
        j = self._k7_0734()
        self.assertIsNotNone(j, "TER K7 07:34 introuvable")
        info = self.pe.journey_price(j, cards=[BFC_SOLIDAIRE, self.ILLICO_SOLIDAIRE])
        self.assertEqual(info["rule"], "mono_region")
        split = info["split"]
        self.assertIsNotNone(split)
        self.assertEqual(split["junction_stations"], ["Mâcon"])
        self.assertEqual(len(split["segments"]), 2)
        seg1, seg2 = split["segments"]
        self.assertEqual(seg1["region"], "Bourgogne-Franche-Comté")
        self.assertEqual(seg1["from"]["name"], "Paris Gare de Lyon Hall 1 - 2")
        self.assertEqual(seg1["to"]["name"], "Mâcon")
        self.assertEqual(seg2["region"], "Auvergne-Rhône-Alpes")
        self.assertEqual(seg2["from"]["name"], "Mâcon")
        self.assertEqual(seg2["to"]["name"], "Lyon Part Dieu")
        # billets contigus : la somme des km des segments vaut le total
        self.assertAlmostEqual(round(seg1["km"] + seg2["km"], 1), info["km"], delta=0.5)
        # les deux cartes sont appliquées (chacune sur son segment)
        applied = {c["region"] for c in info["cards"]}
        self.assertIn("Bourgogne-Franche-Comté", applied)
        self.assertIn("Auvergne-Rhône-Alpes", applied)
        # -75 % sur chaque segment
        self.assertAlmostEqual(seg1["fare_reduced_eur"], round(seg1["fare_eur"] * 0.25, 2), delta=0.05)
        self.assertAlmostEqual(seg2["fare_reduced_eur"], round(seg2["fare_eur"] * 0.25, 2), delta=0.05)
        self.assertAlmostEqual(split["price_reduced_split_eur"], round(seg1["fare_reduced_eur"] + seg2["fare_reduced_eur"], 2), delta=0.01)
        # le prix mono-région (billet unique dégressif) est inchangé
        plain = self.pe.journey_price(j, cards=[BFC_SOLIDAIRE])
        self.assertEqual(info["price_reduced_eur"], plain["price_reduced_eur"])
        # sans cartes, pas de split
        base = self.pe.journey_price(j)
        self.assertEqual(base["price_normal_eur"], info["price_normal_eur"])

    def test_paris_dijon_meme_train_pas_de_split(self):
        # Paris -> Dijon sur le même K7 ne traverse qu'une région utile (BFC) :
        # aucun découpage annoncé.
        js = self.e.depart_after_wide(
            DATE, self.resolve("Paris Gare de Lyon"), self.resolve("Dijon"),
            7 * 60, 1, "train_only", None,
        )
        j = next((j for j in js if j.legs and j.legs[0].line == "K7"), None)
        self.assertIsNotNone(j, "K7 Paris -> Dijon introuvable")
        info = self.pe.journey_price(j)
        self.assertIsNotNone(info)
        self.assertIsNone(info["split"])


if __name__ == "__main__":
    unittest.main()
