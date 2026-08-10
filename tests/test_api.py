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
        # Aucun trajet TER -> 200 avec journeys vide (pas une erreur)
        r = self.client.get(
            "/v1/journeys",
            params={"from": "Lyon Part Dieu", "to": "Lille Flandres", "date": DATE, "time": "07:00"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["journeys"], [])

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
        # max_transfers hors 0..3 et vehicle invalide sont rejetés par FastAPI
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
