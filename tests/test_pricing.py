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
DATE = 20260914

# Ids Trainline (config/trainline_cards.json).
BFC_26 = "5be729fcfc26caa921c53f6d836175d832c288ca"
BFC_SOLIDAIRE = "2a730e22c0be4cf0030f89205f540fe39e8dca6b"
HDF = "7d76cf68a01468562cde7088753e8a4673fcda30"
ZOU_ETUDES = "d55f73482d9d2d0f7ccc92b83a13cc0a9b1d788d"
ZOU_SOLIDAIRE = "50a5c534742f0416d10ffc7081f48fb6f4a537bc"
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
        # 112 km (bande 110-149) : 4,506 + 0,1572×112 = 22,11 → ×1,0425 = 23,05 -> 23,10 €.
        self.assertEqual(self.pe.fare(112, "Hauts-de-France"), 23.1)

    def test_fare_ancre_normandie(self):
        # Grille CGV Nomad 30/03/2026 : 112 km -> bande 101-125 = 22,00 €.
        self.assertEqual(self.pe.fare(112, "Normandie"), 22.0)
        # Bornes de paliers : 25 km -> 8,80 ; 26 km -> 8,80 ; 51 km -> 13,20.
        self.assertEqual(self.pe.fare(25, "Normandie"), 4.4)
        self.assertEqual(self.pe.fare(26, "Normandie"), 8.8)
        self.assertEqual(self.pe.fare(50, "Normandie"), 8.8)
        self.assertEqual(self.pe.fare(51, "Normandie"), 13.2)
        # 131 km -> bande 126-150 = 26,40 € (Caen-Cherbourg).
        self.assertEqual(self.pe.fare(131.4, "Normandie"), 26.4)

    def test_fare_plancher_et_arrondi(self):
        # région sans règle -> formule de repli, plancher min_eur (3,00 €).
        self.assertGreaterEqual(self.pe.fare(1, "INCONNUE"), 3.0)
        self.assertEqual(self.pe.fare(1, "INCONNUE"), 3.0)

    # ------------------------------------------------- règles régionales
    def test_fare_escalier_bfc(self):
        # Mobigo (validé 14/08, 6 points à 0,00 €) : 6/13/19/31 €.
        self.assertEqual(self.pe.fare(23, "Bourgogne-Franche-Comté"), 6.0)
        self.assertEqual(self.pe.fare(46, "Bourgogne-Franche-Comté"), 13.0)
        self.assertEqual(self.pe.fare(93, "Bourgogne-Franche-Comté"), 19.0)
        self.assertEqual(self.pe.fare(159, "Bourgogne-Franche-Comté"), 31.0)

    def test_fare_escalier_cvl(self):
        # Rémi : escalier 3,40/6,80/10,30/13,70/17,10/20,50/24,00.
        self.assertEqual(self.pe.fare(59, "Centre-Val de Loire"), 13.7)
        self.assertEqual(self.pe.fare(96, "Centre-Val de Loire"), 20.5)
        self.assertEqual(self.pe.fare(115, "Centre-Val de Loire"), 24.0)

    def test_fare_escalier_bretagne(self):
        self.assertEqual(self.pe.fare(60, "Bretagne"), 12.0)
        self.assertEqual(self.pe.fare(101, "Bretagne"), 17.0)
        self.assertEqual(self.pe.fare(240, "Bretagne"), 30.0)

    def test_fare_affine_paca(self):
        # ZOU! : bande 65-109 -> 4,939 + 0,1741×66,9 = 16,59 -> 16,60 €.
        self.assertEqual(self.pe.fare(66.9, "Provence-Alpes-Côte d'Azur"), 16.6)
        self.assertEqual(self.pe.fare(15.9, "Provence-Alpes-Côte d'Azur"), 6.1)

    def test_fare_affine_grand_est(self):
        # Barème CGV V38 (01/01/2026) : 3,605 + 0,1859×87,7 = 19,91 -> 20,00 €.
        self.assertEqual(self.pe.fare(87.7, "Grand Est"), 20.0)
        # palier fixe 1-10 km = 3,20 € ; bande 1-11 semi-ouverte.
        self.assertEqual(self.pe.fare(8, "Grand Est"), 3.2)
        # bande 65-110 : 3,605 + 0,1859×65,8 = 15,84 -> 15,90 €.
        self.assertEqual(self.pe.fare(65.8, "Grand Est"), 15.9)
        # bande 150-200 : 10,093 + 0,1488×150 = 32,41 -> 32,50 €.
        self.assertEqual(self.pe.fare(150, "Grand Est"), 32.5)

    def test_fare_affine_pays_de_la_loire(self):
        # Barème CGV 25/06/2026 (plein tarif) : 3,6096 + 0,1859×87,4 = 19,86 -> 19,90 €.
        self.assertEqual(self.pe.fare(87.4, "Pays de la Loire"), 19.9)
        # bande 33-65 : 2,5865 + 0,1996×63,6 = 15,28 -> 15,30 €.
        self.assertEqual(self.pe.fare(63.6, "Pays de la Loire"), 15.3)

    def test_fare_affine_occitanie(self):
        # Barème BKN lu par l'utilisateur : 3,246 + 0,1673×73,0 = 15,46 -> 15,50 €.
        self.assertEqual(self.pe.fare(73.0, "Occitanie"), 15.5)
        # bande 33-65 : 2,327 + 0,1794×50,5 = 11,39 -> 11,40 € (réel 11,70).
        self.assertEqual(self.pe.fare(50.5, "Occitanie"), 11.4)
        # bande 301-500 : 15,339 + 0,1157×308,2 = 51,00 -> 51,00 € (réel 50,10).
        self.assertEqual(self.pe.fare(308.2, "Occitanie"), 51.0)

    def test_fare_rule_repli_sur_formule(self):
        # Au-delà de max_km (159,3) la BFC retombe sur la formule (405 km -> ~41 €).
        self.assertEqual(self.pe.fare(405, "Bourgogne-Franche-Comté"), 41.0)

    def test_fare_region_inconnue_utilise_defaut(self):
        # région absente de la config -> default_scale (courbe nationale) ;
        # la Bourgogne (scale 0.9865) est calibrée pour coller à 41,00 €.
        self.assertEqual(self.pe.fare(405, "INCONNUE"), self.pe.fare(405, "Région bidon"))
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
        # Carte BFC 26+ : -60% we / -30% semaine -> pay_we=0.40, pay_wd=0.70 ;
        # carte solidaire BFC : -75% -> 0.25 (dérogation by_id).
        info = self.pe.card_info({"id": BFC_26, "name": "Carte Bourgogne-Franche-Comté 26+"})
        self.assertEqual(info["region"], "Bourgogne-Franche-Comté")
        self.assertIsNone(info["pay"])
        self.assertEqual(info["pay_we"], 0.4)
        self.assertEqual(info["pay_wd"], 0.7)
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
        # Dijon -> Besançon : 19,00 € plein tarif (escalier Mobigo, validé
        # 14/08) ; solidaire BFC (-75%) -> 4,75 €.
        js = self.e.depart_after_wide(
            DATE, self.resolve("Dijon"), self.resolve("Besançon Viotte"),
            7 * 60, 2, "train_only", None,
        )
        self.assertTrue(js)
        info = self.pe.journey_price(js[0])
        self.assertEqual(info["price_normal_eur"], 19.0)
        red = self.pe.journey_price(js[0], cards=[BFC_SOLIDAIRE])
        self.assertEqual(red["price_reduced_eur"], 4.75)
        self.assertEqual([c["shortName"] for c in red["cards"]], ["Tarif réduit solidaire"])

    def test_meilleure_carte_gagne(self):
        # Parmi deux cartes BFC, la plus avantageuse s'applique (solidaire < 26+).
        js = self.e.depart_after_wide(
            DATE, self.resolve("Dijon"), self.resolve("Besançon Viotte"),
            7 * 60, 2, "train_only", None,
        )
        red = self.pe.journey_price(js[0], cards=[BFC_26, BFC_SOLIDAIRE])
        self.assertEqual(red["price_reduced_eur"], 4.75)

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
        # prix réduit = somme sur TOUS les legs : les trains découpés
        # intra-train paient par segment régional, les autres legs un billet
        # unitaire ; chaque portion Hauts-de-France est à -50 %, les portions
        # Normandie restent pleines. (Le train Amiens->Rouen traverse la
        # frontière : il est découpé à Formerie ; le Lille->Amiens est un
        # billet HdF à part.)
        expected = 0.0
        for leg in base["legs"]:
            if leg.get("segments"):
                for seg in leg["segments"]:
                    fare = self.pe.fare(seg["km"], seg["region"])
                    expected += self.pe._discount(fare, 0.5) if seg["region"] == "Hauts-de-France" else fare
            else:
                fare = self.pe.fare(leg["km"], leg["region"])
                expected += self.pe._discount(fare, 0.5) if leg["region"] == "Hauts-de-France" else fare
        self.assertAlmostEqual(info["price_reduced_eur"], round(expected, 2), delta=0.01)

    # ------------------------------------------------------- §32 split intra-train
    ILLICO_SOLIDAIRE = "illico-solidaire"

    def _k7_paris_lyon(self):
        """Trajet Paris Bercy -> Lyon Part Dieu sur le TER K7 de 15h35.

        (Depuis l'horaire sept. 2026, le K7 ne part plus de Gare de Lyon le
        matin ; les directs Paris -> Lyon partent de Bercy l'après-midi.)"""
        js = self.e.depart_after_wide(
            DATE, self.resolve("Paris Bercy"), self.resolve("Lyon Part Dieu"),
            14 * 60, 2, "train_only", None,
        )
        return next(
            (j for j in js if j.legs and j.legs[0].line == "K7" and j.legs[0].from_time == 15 * 60 + 35),
            None,
        )

    def test_k7_paris_lyon_jonction_macon(self):
        # Le TER K7 de 15h35 traverse la BFC puis l'ARA : la jonction se situe
        # à Mâcon (dernier arrêt BFC avant Belleville-sur-Saône, Rhône).
        j = self._k7_paris_lyon()
        self.assertIsNotNone(j, "TER K7 15:35 Paris Bercy -> Lyon introuvable")
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
        j = self._k7_paris_lyon()
        self.assertIsNotNone(j, "TER K7 15:35 introuvable")
        info = self.pe.journey_price(j, cards=[BFC_SOLIDAIRE, self.ILLICO_SOLIDAIRE])
        self.assertEqual(info["rule"], "mono_region")
        split = info["split"]
        self.assertIsNotNone(split)
        self.assertEqual(split["junction_stations"], ["Mâcon"])
        self.assertEqual(len(split["segments"]), 2)
        seg1, seg2 = split["segments"]
        self.assertEqual(seg1["region"], "Bourgogne-Franche-Comté")
        self.assertEqual(seg1["from"]["name"], "Paris Bercy Bourg. Pays d'Auv.")
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
        # le prix affiché devient le total découpé (normal et réduit) ;
        # le billet unique dégressif est conservé en référence dans split.
        self.assertEqual(info["price_reduced_eur"], split["price_reduced_split_eur"])
        self.assertEqual(info["price_normal_eur"], split["price_split_eur"])
        # la référence « billet unique » conserve le prix direct de référence (tarif d'axe 65 €)
        self.assertEqual(split["single_ticket_eur"], 65.00)
        self.assertEqual(split["single_ticket_reduced_eur"], self.pe._discount(split["single_ticket_eur"], 0.25))
        # le découpage est annoncé même sans cartes (mais sans réduction)
        base = self.pe.journey_price(j)
        self.assertIsNotNone(base["split"])
        self.assertEqual(base["price_reduced_eur"], base["price_normal_eur"])

    def test_paris_dijon_meme_train_pas_de_split(self):
        # Paris -> Dijon sur le même K7 ne traverse qu'une région utile (BFC) :
        # aucun découpage annoncé. (Depuis sept. 2026 le direct part de Bercy.)
        js = self.e.depart_after_wide(
            DATE, self.resolve("Paris Bercy"), self.resolve("Dijon"),
            14 * 60, 1, "train_only", None,
        )
        j = next((j for j in js if j.legs and j.legs[0].line == "K7"), None)
        self.assertIsNotNone(j, "K7 Paris -> Dijon introuvable")
        info = self.pe.journey_price(j)
        self.assertIsNotNone(info)
        self.assertIsNone(info["split"])

    # ---------------------------------------------- §33 pas d'accord AURA↔PACA
    def _k14_0640_lyon_marseille(self):
        """TER K14 06:40 Lyon Part Dieu -> Marseille Saint-Charles (via
        Pierrelatte puis Bollène la Croisière)."""
        js = self.e.depart_after_wide(
            DATE, self.resolve("Lyon Part Dieu"), self.resolve("Marseille Saint-Charles"),
            6 * 60, 1, "train_only", None,
        )
        return next(
            (j for j in js if j.legs and j.legs[0].line == "K14" and j.legs[0].from_time == 440),
            None,
        )

    def test_lyon_marseille_gap_pierrelatte_bollene(self):
        # Le K14 06:40 traverse l'ARA puis la PACA : pas d'accord interrégional
        # (§33/§34), découpage en 3 billets — ARA jusqu'à Pierrelatte, PLEIN
        # TARIF Pierrelatte -> Bollène la Croisière, puis PACA depuis Bollène.
        j = self._k14_0640_lyon_marseille()
        self.assertIsNotNone(j, "TER K14 06:40 Lyon -> Marseille introuvable")
        leg = j.legs[0]
        tidx = self.g.trip_index[leg.trip_id]
        segs = self.pe.trip_region_segments(
            tidx, self.g.stop_index[leg.from_id], self.g.stop_index[leg.to_id]
        )
        self.assertEqual(len(segs), 3)
        self.assertEqual(segs[0]["region"], "Auvergne-Rhône-Alpes")
        self.assertEqual(segs[0]["from"]["name"], "Lyon Part Dieu")
        self.assertEqual(segs[0]["to"]["name"], "Pierrelatte")
        self.assertIs(segs[1].get("gap"), True)
        self.assertEqual(segs[1]["region"], "Auvergne-Rhône-Alpes")
        self.assertEqual(segs[1]["from"]["name"], "Pierrelatte")
        self.assertEqual(segs[1]["to"]["name"], "Bollène la Croisière")
        self.assertEqual(segs[2]["region"], "Provence-Alpes-Côte d'Azur")
        self.assertEqual(segs[2]["from"]["name"], "Bollène la Croisière")
        self.assertEqual(segs[2]["to"]["name"], "Marseille Saint-Charles")

    def test_lyon_marseille_zou_seulement_cote_paca(self):
        # Avec ZOU! Solidaire : la réduction ne s'applique QUE sur le segment
        # PACA (Bollène -> Marseille) ; le segment ARA et le plein tarif
        # interrégional restent au tarif plein.
        j = self._k14_0640_lyon_marseille()
        self.assertIsNotNone(j, "TER K14 06:40 introuvable")
        info = self.pe.journey_price(j, cards=[ZOU_SOLIDAIRE])
        split = info["split"]
        self.assertIsNotNone(split)
        self.assertEqual(split["junction_stations"], [])
        self.assertEqual(len(split["segments"]), 3)
        seg_ara, seg_gap, seg_paca = split["segments"]
        # segments ARA et interrégional : plein tarif, pas de réduction
        self.assertEqual(seg_ara["fare_reduced_eur"], seg_ara["fare_eur"])
        self.assertEqual(seg_gap["fare_reduced_eur"], seg_gap["fare_eur"])
        self.assertIs(seg_gap.get("gap"), True)
        # segment PACA : -50 % (ZOU! Solidaire)
        self.assertAlmostEqual(
            seg_paca["fare_reduced_eur"], round(seg_paca["fare_eur"] * 0.5, 2), delta=0.05
        )
        # le total découpé = ARA + plein tarif + PACA réduit
        expected = round(seg_ara["fare_eur"] + seg_gap["fare_eur"] + seg_paca["fare_reduced_eur"], 2)
        self.assertEqual(split["price_reduced_split_eur"], expected)
        # billets contigus : la somme des km des segments couvre le trajet
        self.assertAlmostEqual(
            round(seg_ara["km"] + seg_gap["km"] + seg_paca["km"], 1), info["km"], delta=0.5
        )
        # seule la carte ZOU est annoncée, et uniquement pour la PACA
        applied = {c["region"] for c in info["cards"]}
        self.assertEqual(applied, {"Provence-Alpes-Côte d'Azur"})
        # le prix affiché devient le total découpé
        self.assertEqual(info["price_reduced_eur"], split["price_reduced_split_eur"])
        self.assertEqual(info["price_normal_eur"], split["price_split_eur"])

    def test_distance_pk_jonction_angers(self):
        # Régression 15/08 : la jonction Angers (515000 ↔ 450000) était ancrée
        # au même PK des deux côtés (342,95), mais le PK est spécifique à chaque
        # ligne → Nantes-Le Mans surestimé à 219,4 km (réel ~185).
        # K15 Nantes -> Le Mans doit valoir ~183 km et ~37,4 € plein tarif.
        from src.pricing import PricingEngine  # noqa: F811
        pe = self.pe
        for tid, ti in self.g.trip_index.items():
            t = self.g.trips[ti]
            if t.route is not None and self.g.routes[t.route].short_name == "K15":
                sts = [self.g.stops[st.stop].name for st in t.stop_times]
                if sts and sts[0] == "Nantes":
                    nm = self.g.stop_index.get("StopArea:OCE87481002")
                    lm = self.g.stop_index.get("StopArea:OCE87396002")
                    km = pe.leg_km(ti, nm, lm)
                    self.assertAlmostEqual(km, 183.0, delta=4.0)
                    fare = pe.fare(km, "Pays de la Loire")
                    self.assertAlmostEqual(fare, 37.4, delta=1.0)
                    break

    def test_multi_leg_journey_with_split(self):
        # T12 — Trajet multi-legs où au moins un leg est split et l'autre non.
        # Le split doit contenir la totalité des segments du trajet de bout en bout.
        j_k7 = self._k7_paris_lyon()
        self.assertIsNotNone(j_k7, "TER K7 15:35 introuvable")
        leg1 = j_k7.legs[0]

        js_lg = self.e.depart_after_wide(
            DATE, self.resolve("Lyon Part Dieu"), self.resolve("Grenoble"),
            13 * 60, 1, "train_only", None,
        )
        self.assertTrue(js_lg)
        leg2 = js_lg[0].legs[0]

        class FakeJourney:
            def __init__(self, legs):
                self.legs = legs

        fj = FakeJourney([leg1, leg2])
        info = self.pe.journey_price(fj)
        self.assertIsNotNone(info)

        split = info["split"]
        self.assertIsNotNone(split)
        self.assertEqual(len(split["segments"]), 3)
        self.assertEqual(split["segments"][0]["from"]["name"], "Paris Bercy Bourg. Pays d'Auv.")
        self.assertEqual(split["segments"][0]["to"]["name"], "Mâcon")
        self.assertEqual(split["segments"][1]["from"]["name"], "Mâcon")
        self.assertEqual(split["segments"][1]["to"]["name"], "Lyon Part Dieu")
        self.assertEqual(split["segments"][2]["from"]["name"], "Lyon Part Dieu")
        self.assertEqual(split["segments"][2]["to"]["name"], "Grenoble")


if __name__ == "__main__":
    unittest.main()
