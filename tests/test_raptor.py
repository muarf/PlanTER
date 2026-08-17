"""Tests du moteur McRAPTOR (T3) — validation sur le graphe réel.

Exécution : python3 -m unittest tests.test_raptor -v
(Oracle de référence : connectivity_check — parité vérifiée sur 16 couples.)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.graph import Graph
from src.raptor import RaptorEngine
from src import gtfs_rt

DATA = Path(__file__).resolve().parents[1] / "data" / "graph.bin"
DATE = 20260810  # un lundi


def _m(hh, mm):
    return hh * 60 + mm


class RaptorTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = Graph.load(DATA)
        cls.e = RaptorEngine(cls.g)

    def resolve(self, q):
        idx = self.g.resolve_place(q)
        self.assertTrue(idx, f"gare introuvable : {q!r}")
        return idx

    # -------------------------------------------------------------- cas réels
    def test_0_chgt_besancon_dijon(self):
        j = self.e.depart_after(DATE, self.resolve("Besançon Viotte"), self.resolve("Dijon"), _m(12, 0), 3)
        self.assertTrue(j)
        j = j[0]
        self.assertEqual(j.transfers, 0)
        self.assertEqual(j.departure, _m(12, 6))
        self.assertEqual(j.arrival, _m(13, 2))
        self.assertEqual(len(j.legs), 1)
        self.assertEqual(j.legs[0].line, "C11")
        self.assertIn("894212", j.legs[0].trip_id)

    def test_1_chgt_paris_besancon(self):
        j = self.e.depart_after(DATE, self.resolve("Paris Gare de Lyon"), self.resolve("Besançon Viotte"), _m(7, 0), 3)
        self.assertTrue(j)
        j = j[0]
        self.assertEqual(j.transfers, 1)
        self.assertEqual(j.departure, _m(7, 34))
        self.assertEqual(j.arrival, _m(12, 4))
        self.assertEqual([leg.line for leg in j.legs], ["K7", "C11"])
        self.assertIn("17769", j.legs[0].trip_id)
        self.assertIn("894213", j.legs[1].trip_id)
        # correspondance à Dijon
        self.assertEqual(j.legs[0].to_name, "Dijon")
        self.assertEqual(j.legs[1].from_name, "Dijon")

    def test_retour_soir_besancon_paris(self):
        # Itinéraire réel utilisateur : C1 (MOBIGO 894264) puis K7 17764 -> Bercy 22:37
        j = self.e.depart_after(DATE, self.resolve("Besançon Viotte"), self.resolve("Paris"), _m(18, 0), 3)
        self.assertTrue(j)
        j = j[0]
        self.assertEqual(j.transfers, 1)
        self.assertEqual(j.departure, _m(18, 16))
        self.assertEqual(j.arrival, _m(22, 37))
        self.assertEqual([leg.line for leg in j.legs], ["C1", "K7"])
        self.assertIn("894264", j.legs[0].trip_id)
        self.assertIn("17764", j.legs[1].trip_id)
        self.assertEqual(j.legs[1].to_name, "Paris Bercy Bourg. Pays d'Auv.")

    def test_paris_groupe_mulhouse_0_chgt(self):
        # « Paris toutes gares » : K4 direct Paris Est -> Mulhouse
        j = self.e.depart_after(DATE, self.resolve("Paris"), self.resolve("Mulhouse"), _m(6, 0), 3)
        self.assertTrue(j)
        j = j[0]
        self.assertEqual(j.transfers, 0)
        self.assertEqual(j.departure, _m(6, 5))
        self.assertEqual(j.arrival, _m(11, 13))
        self.assertEqual(j.legs[0].line, "K4")
        self.assertEqual(j.legs[0].from_name, "Paris Est")

    def test_2_chgt_paris_grenoble(self):
        j = self.e.depart_after(DATE, self.resolve("Paris"), self.resolve("Grenoble"), _m(7, 0), 3)
        self.assertTrue(j)
        self.assertLessEqual(j[0].transfers, 2)

    def test_aucun_trajet(self):
        # Lyon -> Lille exige du TGV : AUCUN TER à <= 3 correspondances
        self.assertEqual(self.e.depart_after(DATE, self.resolve("Lyon Part Dieu"), self.resolve("Lille Flandres"), _m(7, 0), 3), [])

    def test_marche_inter_gares(self):
        # Arc piéton Bercy -> Gare de Lyon (10 min, paris_links) puis K7 07:34 -> Dijon
        j = self.e.depart_after(DATE, self.resolve("Paris Bercy"), self.resolve("Dijon"), _m(7, 0), 3)
        self.assertTrue(j)
        j = j[0]
        self.assertEqual(j.transfers, 1)
        self.assertEqual(j.departure, _m(7, 0))
        self.assertEqual(j.arrival, _m(10, 33))
        self.assertEqual(j.legs[0].type, "walk")
        self.assertEqual(j.legs[0].from_name, "Paris Bercy Bourg. Pays d'Auv.")
        self.assertEqual(j.legs[0].to_name, "Paris Gare de Lyon Hall 1 - 2")
        self.assertEqual(j.legs[1].line, "K7")

    def test_marche_inter_gares_puis_3_chgt(self):
        # Bercy -> Mulhouse via marches intra-Paris + K4 (Belfort) + C13 : 3 chgt
        j = self.e.depart_after(DATE, self.resolve("Paris Bercy"), self.resolve("Mulhouse"), _m(7, 0), 3)
        self.assertTrue(j)
        j = j[0]
        self.assertLessEqual(j.transfers, 3)
        self.assertIn("walk", [leg.type for leg in j.legs])
        self.assertEqual(j.legs[-1].to_name, "Mulhouse")

    def test_wide_revele_le_depart_qui_rattrape_la_meme_correspondance(self):
        """Recherche large (T3bis) : à 11:00, le 12:17 Saint-Vit rejoint le
        même K7 (N17758) que le 11:06 → même arrivée 17:06. RAPTOR simple le
        jette comme dominé ; la révélation le fait apparaître."""
        js = self.e.depart_after_wide(DATE, self.resolve("Saint-Vit"), self.resolve("Paris Bercy"), _m(11, 0), 6, "train_only")
        dep12 = [j for j in js if j.departure == _m(12, 17)]
        self.assertEqual(len(dep12), 1)
        j = dep12[0]
        self.assertEqual(j.arrival, _m(17, 6))
        self.assertEqual(j.transfers, 1)
        # même second leg (le K7 N17758) que le 11:06 (qui reste en tête de liste)
        self.assertEqual(js[0].departure, _m(11, 6))
        self.assertEqual(js[0].legs[1].trip_id, j.legs[1].trip_id)
        self.assertEqual(js[0].arrival, j.arrival)

    def test_wide_renvoie_les_departs_intermediaires(self):
        """Recherche large (T3bis) : Dijon -> Besançon Viotte renvoie tous les
        directs horaires (et pas seulement le 1er de chaque tranche de 3 h)."""
        js = self.e.depart_after_wide(DATE, self.resolve("Dijon"), self.resolve("Besançon Viotte"), _m(7, 0), 6, "train_only")
        deps = sorted({j.departure for j in js})
        self.assertIn(_m(8, 8), deps)  # entre 07:09 (tranche 07:00) et 10:12 (tranche 10:00)
        self.assertIn(_m(9, 9), deps)
        self.assertIn(_m(10, 12), deps)

    def test_wide_arrive_by_reste_sans_revelation(self):
        """Recherche large ArriveBy (T3bis) : la révélation est désactivée —
        le meilleur « départ le plus tardif » reste en tête, sans départs plus
        tôt qui n'apporteraient rien."""
        js = self.e.arrive_by_wide(DATE, self.resolve("Paris Gare de Lyon"), self.resolve("Besançon Viotte"), _m(13, 0), 6, "train_only")
        self.assertTrue(js)
        self.assertEqual(js[0].arrival, _m(12, 4))
        self.assertEqual(js[0].departure, _m(7, 34))

    # -------------------------------------------------------------- modes
    def test_nuit_depart_apres_0300(self):
        j = self.e.depart_after(DATE, self.resolve("Paris Gare de Lyon"), self.resolve("Besançon Viotte"), _m(3, 0), 3)
        self.assertTrue(j)
        self.assertEqual(j[0].departure, _m(7, 34))

    def test_arrive_by(self):
        j = self.e.arrive_by(DATE, self.resolve("Paris Gare de Lyon"), self.resolve("Besançon Viotte"), _m(13, 0), 3)
        self.assertTrue(j)
        j = j[0]
        self.assertLessEqual(j.arrival, _m(13, 0))
        self.assertEqual(j.arrival, _m(12, 4))
        self.assertEqual(j.departure, _m(7, 34))
        self.assertEqual(j.transfers, 1)

    def test_train_only(self):
        j = self.e.depart_after(DATE, self.resolve("Paris Gare de Lyon"), self.resolve("Besançon Viotte"), _m(7, 0), 3, vehicle="train_only")
        self.assertTrue(j)
        self.assertEqual(j[0].transfers, 1)
        self.assertTrue(all(leg.type == "train" for leg in j[0].legs))

    # -------------------------------------------------------------- regression T2
    def test_routes_by_stop_couvre_tous_les_arrêts(self):
        """Bug T2 : routes_by_stop ne listait la route que pour le 1er arrêt
        du premier trip la desservant. Chaque arrêt d'un trip doit référencer
        la route."""
        g = self.g
        for tidx, trip in enumerate(g.trips):
            for st in trip.stop_times:
                self.assertIn(trip.route, g.routes_by_stop[st.stop])

    def test_ligne_c23_dessert_lyon_part_dieu(self):
        g = self.g
        pd = g.resolve_place("Lyon Part Dieu")[0]
        c23 = next(r for r in g.routes if r.short_name == "C23" and "Lyon" in (r.long_name or ""))
        self.assertIn(g.routes.index(c23), g.routes_by_stop[pd])

    # -------------------------------------------------------------- T8 temps réel
    def test_rt_delay_decale_le_depart(self):
        """T8 — un retard GTFS-RT décale le départ/arrivée du leg d'autant."""
        j = self.e.depart_after(DATE, self.resolve("Dijon"), self.resolve("Besançon Viotte"), _m(7, 0), 3)[0]
        trip_id = j.legs[0].trip_id
        trip = self.g.trips[self.g.trip_index[trip_id]]
        delays = {st.stop: 15 for st in trip.stop_times}
        feed = gtfs_rt.RealtimeFeed(trip_delays={(trip_id, DATE): delays})
        jr = self.e.depart_after(DATE, self.resolve("Dijon"), self.resolve("Besançon Viotte"), _m(7, 0), 3, realtime=feed)[0]
        self.assertEqual(jr.departure, j.departure + 15)
        self.assertEqual(jr.arrival, j.arrival + 15)
        self.assertEqual(jr.legs[0].delay_min, 15)

    def test_rt_cancel_bascule_sur_alternative(self):
        """T8 — un train supprimé disparaît du calcul (bascule sur alternative)."""
        j = self.e.depart_after(DATE, self.resolve("Dijon"), self.resolve("Besançon Viotte"), _m(7, 0), 3)[0]
        trip_id = j.legs[0].trip_id
        feed = gtfs_rt.RealtimeFeed(cancelled={(trip_id, DATE)})
        jr = self.e.depart_after(DATE, self.resolve("Dijon"), self.resolve("Besançon Viotte"), _m(7, 0), 3, realtime=feed)
        self.assertTrue(jr)
        for jt in jr:
            for leg in jt.legs:
                self.assertNotEqual(leg.trip_id, trip_id)

    def test_rt_arrive_by_miroir(self):
        """T8 — le retard s'applique aussi en mode ArriveBy (miroir)."""
        j = self.e.arrive_by(DATE, self.resolve("Paris Gare de Lyon"), self.resolve("Besançon Viotte"), _m(13, 0), 3)[0]
        trip_id = j.legs[-1].trip_id
        trip = self.g.trips[self.g.trip_index[trip_id]]
        delays = {st.stop: 12 for st in trip.stop_times}
        feed = gtfs_rt.RealtimeFeed(trip_delays={(trip_id, DATE): delays})
        jr = self.e.arrive_by(DATE, self.resolve("Paris Gare de Lyon"), self.resolve("Besançon Viotte"), _m(13, 0), 3, realtime=feed)[0]
        self.assertLessEqual(jr.arrival, _m(13, 0))
        self.assertEqual(jr.arrival, j.arrival + 12)
        self.assertEqual(jr.legs[-1].delay_min, 12)

    # -------------------------------------------------------------- json
    def test_to_json(self):
        import datetime

        j = self.e.depart_after(DATE, self.resolve("Paris Gare de Lyon"), self.resolve("Besançon Viotte"), _m(7, 0), 3)[0]
        d = j.to_json(datetime.date(2026, 8, 10))
        self.assertEqual(d["transfers"], 1)
        self.assertEqual(d["legs"][0]["line"], "K7")
        self.assertIn("T", d["departure"])
        self.assertIn("+02:00", d["departure"])

    def test_direct_marseille_nice_trsi(self):
        # Régression : le feed national SNCF ne couvre pas les trains ZOU!
        # Transdev RSI (Marseille <-> Nice directs, ex. 17481) ; ils sont
        # fusionnés depuis data/ter/gtfs_trsi.zip. Un direct doit exister
        # sans correspondance, avec son numéro de train et la ligne SUD_IV15.
        mrs = self.resolve("Marseille Saint-Charles")
        nce = self.resolve("Nice-Ville")
        js = self.e.depart_after_wide(DATE, mrs, nce, _m(9, 0), 6, "train_only", None)
        directs = [j for j in js if j.transfers == 0]
        self.assertTrue(directs, "aucun direct Marseille -> Nice (TRSI absent ?)")
        j = directs[0]
        self.assertEqual(len(j.legs), 1)
        leg = j.legs[0]
        self.assertEqual(leg.line, "SUD_IV15")
        self.assertTrue(leg.vehicle_label.isdigit())
        self.assertEqual(leg.from_name, "Marseille Saint-Charles")
        self.assertEqual(leg.to_name, "Nice-Ville")


if __name__ == "__main__":
    unittest.main()
