"""T5 — Tests d'intégration de l'API REST FastAPI (§7 PLAN.md).

Exécution : .venv/bin/python -m unittest tests.test_api -v

Couvre les contrats §7.2/§7.3 : /v1/health, /v1/stations/search,
/v1/journeys (départ/arrivée, count, max_transfers, vehicle, marche
inter-gares) et les erreurs (404 gare introuvable, 400 date hors plage /
heure invalide / paramètres invalides, 200 avec journeys vide).
"""
import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from src.api import app

DATE = "2026-08-10"
DATE_YM = 20260810


class ApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    # ---------------------------------------------------------------- health
    def test_health(self):
        r = self.client.get("/v1/health")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["data_date"], "2026-12-19")
        # T8 — section temps réel (peut être None si le poller est désactivé)
        self.assertIn("realtime", body)

    # ------------------------------------------------------------- stations
    def test_stations_search(self):
        r = self.client.get("/v1/stations/search", params={"q": "dijon"})
        self.assertEqual(r.status_code, 200)
        stations = r.json()["stations"]
        self.assertTrue(stations)
        self.assertEqual(stations[0]["stop_area_id"], "OCE87713040")
        self.assertIn("lat", stations[0])
        self.assertIn("lon", stations[0])

    def test_stations_search_limit(self):
        r = self.client.get("/v1/stations/search", params={"q": "paris", "limit": 3})
        self.assertEqual(r.status_code, 200)
        self.assertLessEqual(len(r.json()["stations"]), 3)

    def test_stations_search_empty(self):
        r = self.client.get("/v1/stations/search", params={"q": "zzzzxq"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["stations"], [])

    # ------------------------------------------------------------- journeys
    def test_journeys_canonique(self):
        # §7.4 : Paris Gare de Lyon -> Besançon Viotte, 1 correspondance à Dijon
        r = self.client.get(
            "/v1/journeys",
            params={"from": "OCE87686006", "to": "OCE87718007", "date": DATE, "time": "07:00"},
        )
        self.assertEqual(r.status_code, 200)
        journeys = r.json()["journeys"]
        self.assertTrue(journeys)
        j = journeys[0]
        self.assertEqual(j["transfers"], 1)
        self.assertEqual(j["departure"], "2026-08-10T07:34:00+02:00")
        self.assertEqual(j["arrival"], "2026-08-10T12:04:00+02:00")
        self.assertEqual(j["duration_min"], 270)
        self.assertEqual([leg["line"] for leg in j["legs"]], ["K7", "C11"])

    def test_journeys_by_nom_et_arrival(self):
        r = self.client.get(
            "/v1/journeys",
            params={
                "from": "Paris Gare de Lyon",
                "to": "Besançon Viotte",
                "date": DATE,
                "time": "13:00",
                "datetime_represents": "arrival",
            },
        )
        self.assertEqual(r.status_code, 200)
        j = r.json()["journeys"][0]
        self.assertEqual(j["arrival"], "2026-08-10T12:04:00+02:00")

    def test_journeys_groupe_paris(self):
        r = self.client.get(
            "/v1/journeys",
            params={"from": "Paris", "to": "Mulhouse", "date": DATE, "time": "06:00"},
        )
        self.assertEqual(r.status_code, 200)
        j = r.json()["journeys"][0]
        self.assertEqual(j["transfers"], 0)
        self.assertEqual(j["legs"][0]["line"], "K4")
        self.assertEqual(j["legs"][0]["from"]["name"], "Paris Est")

    def test_journeys_aucun_resultat(self):
        # Aucun trajet TER en ≤3 correspondances -> 200 avec journeys vide (pas une erreur)
        r = self.client.get(
            "/v1/journeys",
            params={"from": "Hendaye", "to": "Strasbourg", "date": DATE, "time": "07:00", "max_transfers": 3},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["journeys"], [])

    def test_journeys_plus_de_3_correspondances(self):
        # Le défaut max_transfers=6 permet au moteur de proposer des trajets à
        # 4+ correspondances quand c'est la seule option à cette heure (retour
        # utilisateur : plus de limite 0-3, le moteur choisit lui-même).
        r = self.client.get(
            "/v1/journeys",
            params={"from": "Lyon Part Dieu", "to": "Lille Flandres", "date": DATE, "time": "07:00"},
        )
        self.assertEqual(r.status_code, 200)
        j = r.json()["journeys"][0]
        self.assertGreater(j["transfers"], 3)

    def test_journeys_marche_inter_gares(self):
        r = self.client.get(
            "/v1/journeys",
            params={"from": "Paris Bercy", "to": "Dijon", "date": DATE, "time": "07:00"},
        )
        self.assertEqual(r.status_code, 200)
        j = r.json()["journeys"][0]
        self.assertEqual(j["legs"][0]["type"], "walk")
        self.assertEqual(j["arrival"], "2026-08-10T10:33:00+02:00")

    def test_journeys_coordonnees(self):
        # Coordonnées de la gare de Dijon (47.323, 5.027) -> Besançon Viotte
        r = self.client.get(
            "/v1/journeys",
            params={"from": "47.3231,5.0271", "to": "Besançon Viotte", "date": DATE, "time": "12:00"},
        )
        self.assertEqual(r.status_code, 200)
        j = r.json()["journeys"][0]
        self.assertEqual(j["departure"], "2026-08-10T12:11:00+02:00")
        self.assertEqual(j["legs"][0]["from"]["name"], "Dijon")

    def test_journeys_arrivee_jour_suivant(self):
        # Paris -> Grenoble 07:00 : arrivée le lendemain (24:31) datée jour+1
        r = self.client.get(
            "/v1/journeys",
            params={"from": "Paris", "to": "Grenoble", "date": DATE, "time": "07:00"},
        )
        self.assertEqual(r.status_code, 200)
        j = r.json()["journeys"][0]
        self.assertEqual(j["transfers"], 2)
        self.assertEqual(j["arrival"], "2026-08-11T00:31:00+02:00")

    def test_journeys_count_et_max_transfers(self):
        r = self.client.get(
            "/v1/journeys",
            params={"from": "Paris", "to": "Lyon Perrache", "date": DATE, "time": "08:00", "count": 1, "max_transfers": 3},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["journeys"]), 1)

    # ------------------------------------------------------------- T8 temps réel
    def test_journeys_use_realtime_delay_min_present(self):
        """T8 — use_realtime=true : chaque leg expose delay_min (0 par défaut)."""
        r = self.client.get(
            "/v1/journeys",
            params={"from": "Dijon", "to": "Besançon Viotte", "date": DATE, "time": "07:00", "use_realtime": "true"},
        )
        self.assertEqual(r.status_code, 200)
        j = r.json()["journeys"][0]
        for leg in j["legs"]:
            self.assertIn("delay_min", leg)
            self.assertGreaterEqual(leg["delay_min"], 0)

    def test_journeys_sans_realtime_zero(self):
        """T8 — sans use_realtime, delay_min vaut 0 (pas de temps réel appliqué)."""
        r = self.client.get(
            "/v1/journeys",
            params={"from": "Dijon", "to": "Besançon Viotte", "date": DATE, "time": "07:00"},
        )
        self.assertEqual(r.status_code, 200)
        j = r.json()["journeys"][0]
        self.assertEqual(j["legs"][0]["delay_min"], 0)

    # --------------------------------------------------- tri par durée (§20)
    def test_sort_duration_le_plus_court_en_premier(self):
        """sort=duration : la durée ne décroît pas, et la liste n'est pas triée
        par départ (le plus court de la journée peut partir plus tard)."""
        q = {"from": "Paris Est", "to": "Strasbourg", "date": DATE, "time": "08:00", "count": "5"}
        by_dep = self.client.get("/v1/journeys", params={**q, "sort": "departure"}).json()["journeys"]
        by_dur = self.client.get("/v1/journeys", params={**q, "sort": "duration"}).json()["journeys"]
        self.assertEqual(by_dur, sorted(by_dur, key=lambda j: j["duration_min"]))
        # le premier par durée est au moins aussi rapide que le premier par départ
        self.assertLessEqual(by_dur[0]["duration_min"], by_dep[0]["duration_min"])

    def test_sort_duration_reste_ordonne_par_egalite(self):
        """sort=duration : à durée égale, les départs restent triés."""
        q = {"from": "Dijon", "to": "Besançon Viotte", "date": DATE, "time": "07:00", "count": "5",
             "sort": "duration"}
        js = self.client.get("/v1/journeys", params=q).json()["journeys"]
        durs = [j["duration_min"] for j in js]
        self.assertEqual(durs, sorted(durs))

    # ---------------------------------------------------- recherche large (§20)
    def test_recherche_large_trajets_de_fin_de_journee(self):
        """RAPTOR simple ne garde que l'arrivée la plus tôt par nombre de
        correspondances : à 08:00, un trajet rapide de 15h38 (5h) était masqué
        par un plus long de 08:03. La recherche large le fait apparaître."""
        q = {"from": "Mouchard", "to": "Paris Bercy", "date": DATE, "time": "08:00", "count": "20"}
        js = self.client.get("/v1/journeys", params=q).json()["journeys"]
        durs = sorted(j["duration_min"] for j in js)
        # le trajet le plus rapide de la journée doit être < 5h30
        self.assertLess(durs[0], 330)
        # et il faut voir plus de 2 trajets (dont des départs de l'après-midi)
        self.assertGreater(len(js), 2)
        self.assertTrue(any(j["departure"][11:13] >= "14" for j in js))

    def test_connection_risks_detecte_retard_rongeant_la_marge(self):
        """T8 — une correspondance dont le retard a consommé la marge planifiée
        est signalée (connection_risks) ; absente sans temps réel."""
        from src import api, gtfs_rt
        from src.raptor import RaptorEngine

        engine = api.get_engine()
        g = engine.graph
        j_theo = engine.depart_after(
            DATE_YM, g.resolve_place("Paris Gare de Lyon"),
            g.resolve_place("Besançon Viotte"), 7 * 60, 3,
        )[0]
        k7 = j_theo.legs[0]
        trip = g.trips[g.trip_index[k7.trip_id]]
        feed = gtfs_rt.RealtimeFeed(
            trip_delays={k7.trip_id: {st.stop: 20 for st in trip.stop_times}}
        )
        # injecte le feed dans le poller (le moteur partage l'instantané)
        saved = api._poller
        api._poller = gtfs_rt.RealtimePoller(g)
        api._poller.feed = feed
        try:
            r = self.client.get(
                "/v1/journeys",
                params={"from": "Paris Gare de Lyon", "to": "Besançon Viotte",
                        "date": DATE, "time": "07:00", "use_realtime": "true"},
            )
        finally:
            api._poller = saved
        self.assertEqual(r.status_code, 200)
        j = r.json()["journeys"][0]
        risks = j.get("connection_risks", [])
        self.assertTrue(risks, "le retard K7 20 min doit signaler une correspondance risquée")
        self.assertEqual(risks[0]["at_station"], "Dijon")
        self.assertEqual(risks[0]["from_line"], "K7")
        self.assertEqual(risks[0]["delay_min"], 20)
        # sans temps réel, aucune section risks
        r0 = self.client.get(
            "/v1/journeys",
            params={"from": "Paris Gare de Lyon", "to": "Besançon Viotte",
                    "date": DATE, "time": "07:00"},
        )
        self.assertNotIn("connection_risks", r0.json()["journeys"][0])

    # ----------------------------------------------------------------- errors
    def test_gare_introuvable_404(self):
        r = self.client.get(
            "/v1/journeys",
            params={"from": "Zzqq", "to": "Dijon", "date": DATE, "time": "07:00"},
        )
        self.assertEqual(r.status_code, 404)
        err = r.json()["error"]
        self.assertEqual(err["code"], "STATION_NOT_FOUND")
        self.assertIn("suggestions", err)

    def test_date_invalide_400(self):
        for bad in ("2026-13-01", "08/08/2026", "2026-08-10T07:00"):
            r = self.client.get(
                "/v1/journeys",
                params={"from": "Dijon", "to": "Besançon Viotte", "date": bad, "time": "07:00"},
            )
            self.assertEqual(r.status_code, 400, f"date {bad}")
            self.assertEqual(r.json()["error"]["code"], "INVALID_DATE")

    def test_date_hors_plage_400(self):
        r = self.client.get(
            "/v1/journeys",
            params={"from": "Dijon", "to": "Besançon Viotte", "date": "2027-01-01", "time": "07:00"},
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "INVALID_DATE")

    def test_heure_invalide_400(self):
        r = self.client.get(
            "/v1/journeys",
            params={"from": "Dijon", "to": "Besançon Viotte", "date": DATE, "time": "25:00"},
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "INVALID_TIME")

    def test_parametres_invalides_422(self):
        # max_transfers hors 0..6 et vehicle invalide sont rejetés par FastAPI
        r = self.client.get(
            "/v1/journeys",
            params={"from": "Dijon", "to": "Besançon Viotte", "date": DATE, "time": "07:00", "max_transfers": 9},
        )
        self.assertEqual(r.status_code, 422)
        r = self.client.get(
            "/v1/journeys",
            params={"from": "Dijon", "to": "Besançon Viotte", "date": DATE, "time": "07:00", "vehicle": "tgv"},
        )
        self.assertEqual(r.status_code, 422)


class WebTestCase(unittest.TestCase):
    """T6 — la SPA statique est servie par l'API (§8) et référence les assets."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_page_daccueil_servie(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])
        html = r.text
        self.assertIn("TER Finder", html)
        self.assertIn('id="search-form"', html)
        self.assertIn('id="from"', html)
        self.assertIn('id="to"', html)
        # T8 — option temps réel dans le formulaire
        self.assertIn('name="realtime"', html)

    def test_assets_servis(self):
        self.assertEqual(self.client.get("/styles.css").status_code, 200)
        self.assertEqual(self.client.get("/app.js").status_code, 200)

    def test_attribution_odbl(self):
        html = self.client.get("/").text
        self.assertIn("ODbL", html)
        self.assertIn("SNCF Open Data", html)

    def test_aucun_tgv(self):
        # §8.2 : le mot TGV n'apparaît que pour le revendiquer, jamais comme trajet
        html = self.client.get("/").text
        self.assertIn("Jamais de TGV", html)


if __name__ == "__main__":
    unittest.main()
