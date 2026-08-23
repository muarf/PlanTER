"""Rayon utilisateur (double curseur), saisie GPS et distances cos(latitude).

Exécution : .venv/bin/python -m unittest tests.test_geo_radius -v

Couvre :
- approx_distance_km (correction cos latitude : 0,05° de longitude à 47°N
  ≈ 3,8 km, pas 5,6 km) et la borne réelle de Graph.stops_nearby ;
- la sémantique du rayon : défaut [0,0] = la gare la plus proche SEULE,
  [min,max] = intervalle en km réels plafonné à MAX_GARES_PER_SIDE ;
- le classement corrigé du cas « By » (Liesle/Mouchard/Arc-et-Senans devant
  Byans, que l'ancienne formule sans cos remontait à tort) ;
- l'API /v1/journeys : clamp/validation des paramètres de rayon, erreur
  NO_GARE_IN_RADIUS explicite, note origin_note pour les résolutions
  géographiques (commune/GPS).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from src.api import app, _pow, _crypto
from src.graph import StopArea, Graph, approx_distance_km, normalize

DATE = "2026-09-14"
ROOT = Path(__file__).resolve().parents[1]


def _enc(params: dict) -> dict:
    return {"payload": _crypto.encrypt_b64(params)}


class GeoDistanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = Graph.load(ROOT / "data" / "graph.bin")

    def test_approx_distance_est_ouest(self):
        # 0,05° de longitude à 47°N ≈ 3,79 km (l'ancienne formule disait 5,56)
        d = approx_distance_km(47.0, 5.0, 47.0, 5.05)
        self.assertGreater(d, 3.7)
        self.assertLess(d, 3.9)

    def test_viotte_tgv_environ_8_km(self):
        viotte = tgv = None
        for i, s in enumerate(self.g.stops):
            if not s.id.startswith("StopArea"):
                continue
            n = normalize(s.name)
            if n == "besancon viotte":
                viotte = i
            elif "tgv" in n and "besancon" in n:
                tgv = i
        self.assertIsNotNone(viotte)
        self.assertIsNotNone(tgv)
        d = approx_distance_km(
            self.g.stops[viotte].lat, self.g.stops[viotte].lon,
            self.g.stops[tgv].lat, self.g.stops[tgv].lon,
        )
        self.assertGreater(d, 7.5)
        self.assertLess(d, 9.5)

    def test_stops_nearby_borne_reelle(self):
        """Tout voisin retourné est VRAIMENT dans le rayon (échantillon)."""
        sample = [i for i, s in enumerate(self.g.stops) if s.id.startswith("StopArea")][::300]
        for idx in sample:
            s = self.g.stops[idx]
            for j in self.g.stops_nearby(idx, 5000.0):
                o = self.g.stops[j]
                d = approx_distance_km(s.lat, s.lon, o.lat, o.lon)
                self.assertLessEqual(d, 5.001, f"{s.name} ↔ {o.name} = {d:.2f} km")

    def test_stops_nearby_couverture_est(self):
        """Un voisin strictement à l'est ≤ 5 km est trouvé (l'ancienne grille
        sans cos sous-couvrait l'est-ouest ~3,5 km)."""
        g = Graph()
        g.stops = [
            StopArea(id="StopArea:A", name="A", lat=47.0, lon=6.0),
            StopArea(id="StopArea:B", name="B", lat=47.0, lon=6.048),  # ≈ 3,6 km est
            StopArea(id="StopArea:C", name="C", lat=47.0, lon=6.096),  # ≈ 7,3 km
        ]
        near = g.stops_nearby(0, 5000.0)
        self.assertIn(1, near)
        self.assertNotIn(2, near)

    def test_nearest_gares_defaut_plus_proche_seule(self):
        coords = self.g.resolve_commune("by")
        idxs = self.g.nearest_gares(*coords)
        self.assertEqual(len(idxs), 1)
        self.assertEqual(self.g.stops[idxs[0]].name, "Liesle")

    def test_nearest_gares_intervalle_ordre_reel(self):
        coords = self.g.resolve_commune("by")
        idxs = self.g.nearest_gares(*coords, min_km=5, max_km=15)
        names = [self.g.stops[i].name for i in idxs]
        # Régression cos(lat) : Byans (12,3 km) doit rester APRÈS Arc-et-Senans
        self.assertEqual(names[:3], ["Liesle", "Mouchard", "Arc-et-Senans"])
        for i in idxs:
            d = approx_distance_km(*coords, self.g.stops[i].lat, self.g.stops[i].lon)
            self.assertGreaterEqual(d, 4.999)
            self.assertLessEqual(d, 15.001)
        self.assertLessEqual(len(idxs), 8)

    def test_resolve_place_by_classement(self):
        # Défaut [0,0] : la plus proche seule
        names = [self.g.stops[i].name for i in self.g.resolve_place("by")]
        self.assertEqual(names, ["Liesle"])
        # Intervalle large : l'ordre réel place Liesle/Mouchard devant Byans
        names = [self.g.stops[i].name for i in self.g.resolve_place("by", 5, 15)]
        self.assertEqual(names[0], "Liesle")
        self.assertEqual(names[1], "Mouchard")
        self.assertNotIn("Byans", names[:3])


class RadiusApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        _pow.enabled = False

    def _post(self, **params):
        base = {"to": "Besançon Viotte", "date": DATE, "time": "10:00"}
        return self.client.post("/v1/journeys", json=_enc({**base, **params}))

    def test_defaut_plus_proche_seule(self):
        r = self._post(**{"from": "Levier"})
        self.assertEqual(r.status_code, 200)
        from src.api import get_engine
        graph = get_engine().graph
        attendu = {graph.stops[i].id for i in graph.resolve_place("Levier")}
        self.assertEqual(len(attendu), 1)
        for j in r.json()["journeys"]:
            rail = [l for l in j["legs"] if l["type"] != "walk"]
            if rail:
                self.assertIn(f"StopArea:{rail[0]['from']['stop_area_id']}", attendu)

    def test_intervalle_min_max(self):
        r = self._post(**{"from": "commune:25334", "radius_min_km": 5, "radius_max_km": 20})
        self.assertEqual(r.status_code, 200)

    def test_min_max_inverses_acceptes(self):
        r = self._post(**{"from": "Levier", "radius_min_km": 20, "radius_max_km": 5})
        self.assertEqual(r.status_code, 200)

    def test_aucune_gare_dans_intervalle(self):
        r = self._post(**{"from": "by", "radius_min_km": 0, "radius_max_km": 6})
        self.assertEqual(r.status_code, 404)
        err = r.json()["error"]
        self.assertEqual(err["code"], "NO_GARE_IN_RADIUS")
        self.assertIn("By", err["message"])

    def test_type_invalide_rejete(self):
        r = self._post(**{"from": "Levier", "radius_min_km": "abc"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "INVALID_PARAM")

    def test_note_origine_commune(self):
        r = self._post(**{"from": "Levier"})
        self.assertEqual(r.status_code, 200)
        journeys = r.json()["journeys"]
        if journeys:  # dépend des circulations du jour : notons dès qu'il y en a
            notes = [j.get("origin_note", "") for j in journeys if j.get("origin_note")]
            self.assertTrue(notes, "origin_note attendue pour une origine commune")
            self.assertTrue(all(n.startswith("Départ de ") for n in notes))

    def test_note_origine_gps(self):
        r = self._post(**{"from": "46.9655, 6.1196"})  # Levier
        self.assertEqual(r.status_code, 200)
        journeys = r.json()["journeys"]
        if journeys:
            notes = [j.get("origin_note", "") for j in journeys if j.get("origin_note")]
            self.assertTrue(all("votre position" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
