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
from unittest.mock import patch

from src.api import app, _pow, _crypto


def _enc(params: dict) -> dict:
    """Chiffre les paramètres et retourne le body POST."""
    return {"payload": _crypto.encrypt_b64(params)}

DATE = "2026-09-14"
DATE_YM = 20260914


class ApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        _pow.enabled = False

    # ---------------------------------------------------------------- health
    def test_health(self):
        r = self.client.get("/v1/health")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["data_date"], "2028-06-30")
        # T8 — section temps réel (peut être None si le poller est désactivé)
        self.assertIn("realtime", body)

    # ------------------------------------------------------------- stations
    def test_stations_search(self):
        r = self.client.get("/v1/stations/search", params={"q": "dijon"})
        self.assertEqual(r.status_code, 200)
        stations = r.json()["stations"]
        self.assertTrue(stations)
        self.assertTrue(any(s["stop_area_id"] == "OCE87713040" for s in stations))
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

    def test_stations_search_post(self):
        # Le client web cherche en POST (pas de query string → ni log nginx,
        # ni cache navigateur). Réponse en 4 blocs.
        r = self.client.post("/v1/stations/search", json={"q": "besancon"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        gares = [s["name"] for s in body["stations"]]
        self.assertIn("Besançon Viotte", gares)
        self.assertIn("Besançon Mouillère", gares)
        self.assertIn("Besançon Franche-Comté TGV", gares)
        # les gares ne sont jamais mélangées aux arrêts bus
        self.assertTrue(all(not s["stop_area_id"].startswith("BusStop:") for s in body["stations"]))
        self.assertTrue(body["bus_stops"])
        communes = {c["name"] for c in body["communes"]}
        self.assertIn("Besançon", communes)
        self.assertTrue(all(c["id"].startswith("commune:") for c in body["communes"]))

    def test_stations_search_gares_avant_bus(self):
        # Régression : le tri alphabétique pur faisait remonter les
        # « BESANCON … » (bus, majuscules) devant les gares homonymes.
        r = self.client.get("/v1/stations/search", params={"q": "besancon", "limit": 50})
        self.assertEqual(r.status_code, 200)
        stations = r.json()["stations"]
        self.assertTrue(stations)
        self.assertTrue(all(not s["stop_area_id"].startswith("BusStop:") for s in stations))
        self.assertEqual(stations[0]["name"], "Besançon Franche-Comté TGV")

    def test_stations_search_dedup_bus(self):
        # « BESANCON PEM Viotte » existe dans deux feeds régionaux (UT25/UT70)
        # à quelques mètres d'écart : un seul résultat doit rester.
        r = self.client.get("/v1/stations/search", params={"q": "besancon pem viotte", "limit": 50})
        self.assertEqual(r.status_code, 200)
        bus = [b for b in r.json()["bus_stops"] if b["name"] == "BESANCON PEM Viotte"]
        self.assertLessEqual(len(bus), 1)

    def test_stations_search_commune_sans_gare(self):
        # Levier (Doubs) : pas de gare OCE homonyme → proposée dans « Communes ».
        r = self.client.post("/v1/stations/search", json={"q": "levier"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertFalse(any(s["name"] == "Levier" for s in body["stations"]))
        communes = [c for c in body["communes"] if c["name"] == "Levier"]
        self.assertTrue(communes)
        self.assertIn("lat", communes[0])
        self.assertIn("lon", communes[0])

    def test_journeys_depuis_commune_tapee(self):
        # Un nom de commune tapé à la main résout vers les gares proches.
        r = self.client.post(
            "/v1/journeys",
            json=_enc({"from": "Levier", "to": "Besançon Viotte", "date": DATE, "time": "10:00"}),
        )
        self.assertEqual(r.status_code, 200)

    def test_journeys_depuis_commune_id(self):
        # L'id « commune:<insee> » sélectionné dans l'autocomplete est accepté.
        search = self.client.post("/v1/stations/search", json={"q": "levier"}).json()
        commune_id = next(c["id"] for c in search["communes"] if c["name"] == "Levier")
        r = self.client.post(
            "/v1/journeys",
            json=_enc({"from": commune_id, "to": "Besançon Viotte", "date": DATE, "time": "10:00"}),
        )
        self.assertEqual(r.status_code, 200)

    # ------------------------------------------------------------- journeys
    def test_journeys_canonique(self):
        # §7.4 : Paris Gare de Lyon -> Besançon Viotte ; depuis sept. 2026 le
        # meilleur itinéraire passe par Paris Est -> Belfort (K4) -> C13.
        r = self.client.post(
            "/v1/journeys",
            json=_enc({"from": "OCE87686006", "to": "OCE87718007", "date": DATE, "time": "07:00"}),
        )
        self.assertEqual(r.status_code, 200)
        journeys = r.json()["journeys"]
        self.assertTrue(journeys)
        j = journeys[0]
        self.assertEqual(j["transfers"], 2)
        self.assertEqual(j["departure"], "2026-09-14T07:07:00+02:00")
        self.assertEqual(j["arrival"], "2026-09-14T14:28:00+02:00")
        self.assertEqual(j["duration_min"], 441)
        self.assertEqual([leg["line"] or leg["type"] for leg in j["legs"]], ["walk", "K4", "C13"])

    def test_journeys_by_nom_et_arrival(self):
        r = self.client.post(
            "/v1/journeys",
            json=_enc({
                "from": "Paris Gare de Lyon",
                "to": "Besançon Viotte",
                "date": DATE,
                "time": "15:00",
                "datetime_represents": "arrival",
            }),
        )
        self.assertEqual(r.status_code, 200)
        journeys = r.json()["journeys"]
        if journeys:
            self.assertIn("2026-09-14", journeys[0]["arrival"])

    def test_journeys_groupe_paris(self):
        r = self.client.post(
            "/v1/journeys",
            json=_enc({"from": "Paris", "to": "Mulhouse", "date": DATE, "time": "06:00"}),
        )
        self.assertEqual(r.status_code, 200)
        j = r.json()["journeys"][0]
        self.assertIn(j["legs"][0]["line"], ["K1", "K4"])
        self.assertIn(j["legs"][0]["from"]["name"], ["Paris Est", "Paris Gare de Lyon"])

    def test_journeys_ville_multi_gares_lyon(self):
        # §5.3 — « Lyon » (pas une gare unique) résout le groupe entier : la
        # première solution part d'une gare du groupe (ici direct K14 depuis
        # Lyon Part Dieu).
        r = self.client.post(
            "/v1/journeys",
            json=_enc({"from": "Lyon", "to": "Valence Ville", "date": DATE, "time": "07:00"}),
        )
        self.assertEqual(r.status_code, 200)
        j = r.json()["journeys"][0]
        self.assertEqual(j["transfers"], 0)
        self.assertEqual(j["legs"][0]["from"]["name"], "Lyon Part Dieu")

    def test_journeys_dijon_et_dijon_toutes_gares(self):
        # §5.3 — « Dijon » est une gare unique (recherche ciblée) ; « Dijon
        # toutes gares » étend la recherche à tout le groupe (Dijon Porte
        # Neuve). Le premier résultat d'une gare homonyme reste la gare.
        r1 = self.client.post(
            "/v1/journeys",
            json=_enc({"from": "Dijon", "to": "Mulhouse", "date": DATE, "time": "08:00"}),
        )
        self.assertEqual(r1.status_code, 200)
        j1 = r1.json()["journeys"][0]
        self.assertEqual(j1["legs"][0]["from"]["name"], "Dijon")
        r2 = self.client.post(
            "/v1/journeys",
            json=_enc({"from": "Dijon toutes gares", "to": "Mulhouse", "date": DATE, "time": "08:00"}),
        )
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json()["journeys"])

    def test_stations_search_place_group(self):
        # §5.3 — l'autocomplete expose le groupe « Lyon toutes gares ».
        r = self.client.get("/v1/stations/search", params={"q": "lyon"})
        self.assertEqual(r.status_code, 200)
        groups = r.json().get("place_groups", [])
        names = [g["name"] for g in groups]
        self.assertIn("Lyon", names)

    def test_sort_transfers_par_defaut(self):
        # §8.2 — le tri par défaut favorise le moins de correspondances, même
        # si la solution est plus longue qu'une autre (davantage de
        # correspondances).
        r = self.client.post(
            "/v1/journeys",
            json=_enc({"from": "Lyon Part Dieu", "to": "Lille Flandres", "date": DATE, "time": "07:00", "count": 10}),
        )
        self.assertEqual(r.status_code, 200)
        js = r.json()["journeys"]
        self.assertTrue(js)
        tr = [j["transfers"] for j in js]
        self.assertEqual(tr, sorted(tr), "les trajets ne sont pas triés par nombre de correspondances")
        self.assertEqual(js[0]["transfers"], min(tr))

    def test_sort_transfers_correspondances_avant_depart(self):
        # §8.2 — sur le même couple origine/destination, le tri par
        # correspondances et le tri par départ diffèrent (quand les deux
        # classements ne coïncident pas, c'est le moins de correspondances qui
        # prime par défaut).
        base = {"from": "Lyon Part Dieu", "to": "Lille Flandres", "date": DATE, "time": "07:00", "count": 10}
        r_dep = self.client.post("/v1/journeys", json=_enc({**base, "sort": "departure"}))
        r_tr = self.client.post("/v1/journeys", json=_enc({**base, "sort": "transfers"}))
        self.assertEqual(r_dep.status_code, 200)
        self.assertEqual(r_tr.status_code, 200)
        dep_first = r_dep.json()["journeys"][0]
        tr_first = r_tr.json()["journeys"][0]
        self.assertLessEqual(tr_first["transfers"], dep_first["transfers"])

    def test_journeys_aucun_resultat(self):
        # Aucun trajet TER en ≤3 correspondances -> 200 avec journeys vide (pas une erreur)
        r = self.client.post(
            "/v1/journeys",
            json=_enc({"from": "Hendaye", "to": "Strasbourg", "date": DATE, "time": "07:00", "max_transfers": 3}),
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["journeys"], [])

    def test_journeys_plus_de_3_correspondances(self):
        # Le défaut max_transfers=6 permet au moteur de proposer des trajets à
        # 4+ correspondances quand c'est la seule option à cette heure (retour
        # utilisateur : plus de limite 0-3, le moteur choisit lui-même).
        r = self.client.post(
            "/v1/journeys",
            json=_enc({"from": "Lyon Part Dieu", "to": "Lille Flandres", "date": DATE, "time": "07:00"}),
        )
        self.assertEqual(r.status_code, 200)
        j = r.json()["journeys"][0]
        self.assertGreater(j["transfers"], 3)

    def test_journeys_marche_inter_gares(self):
        # Le tri par défaut (§8.2) favorise le moins de correspondances : la
        # solution avec marche inter-gares existe mais n'est plus forcément la
        # première. On cherche donc une solution avec un premier leg à pied.
        r = self.client.post(
            "/v1/journeys",
            json=_enc({"from": "Paris Bercy", "to": "Dijon", "date": DATE, "time": "07:00", "count": 20}),
        )
        self.assertEqual(r.status_code, 200)
        walked = [j for j in r.json()["journeys"] if j["legs"] and j["legs"][0]["type"] == "walk"]
        self.assertTrue(walked, "aucune solution avec marche inter-gares trouvée")
        j = walked[0]
        self.assertEqual(j["arrival"], "2026-09-14T17:09:00+02:00")

    def test_journeys_coordonnees(self):
        # Coordonnées de la gare de Dijon (47.323, 5.027) -> Besançon Viotte
        r = self.client.post(
            "/v1/journeys",
            json=_enc({"from": "47.3231,5.0271", "to": "Besançon Viotte", "date": DATE, "time": "12:00"}),
        )
        self.assertEqual(r.status_code, 200)
        j = r.json()["journeys"][0]
        self.assertEqual(j["departure"], "2026-09-14T12:11:00+02:00")
        self.assertEqual(j["legs"][0]["from"]["name"], "Dijon")

    @patch("src.raptor.RaptorEngine.depart_after_wide")
    def test_journeys_arrivee_jour_suivant(self, mock_depart):
        # Paris -> Grenoble 07:00 : arrivée le lendemain (24:31) datée jour+1.
        # Trajet de nuit avec car TER : on demande explicitement vehicle=all
        # (le défaut train_only exclut les cars).
        from unittest.mock import patch
        from src.raptor import Journey, Leg
        mock_depart.return_value = [
            Journey(
                departure=454,  # 07:34
                arrival=1471,   # 24:31 (00:31 next day)
                transfers=2,
                legs=[
                    Leg(
                        type="train",
                        route_id="R1",
                        line="L1",
                        line_name="Ligne 1",
                        vehicle_label="1234",
                        trip_id="T1",
                        from_id="OCE1",
                        from_name="Paris Gare de Lyon",
                        from_time=454,
                        to_id="OCE2",
                        to_name="Lyon Part Dieu",
                        to_time=764,
                    ),
                    Leg(
                        type="walk",
                        route_id="",
                        line="",
                        line_name="",
                        vehicle_label="",
                        trip_id="",
                        from_id="OCE2",
                        from_name="Lyon Part Dieu",
                        from_time=764,
                        to_id="OCE3",
                        to_name="Lyon Part Dieu",
                        to_time=774,
                    ),
                    Leg(
                        type="car",
                        route_id="R2",
                        line="L2",
                        line_name="Ligne 2",
                        vehicle_label="5678",
                        trip_id="T2",
                        from_id="OCE3",
                        from_name="Lyon Part Dieu",
                        from_time=1316,
                        to_id="OCE4",
                        to_name="Grenoble",
                        to_time=1471,
                    )
                ]
            )
        ]
        r = self.client.post(
            "/v1/journeys",
            json=_enc({"from": "Paris", "to": "Grenoble", "date": DATE, "time": "07:00", "vehicle": "all"}),
        )
        self.assertEqual(r.status_code, 200)
        j = r.json()["journeys"][0]
        self.assertEqual(j["transfers"], 2)
        self.assertEqual(j["arrival"], "2026-09-15T00:31:00+02:00")

    def test_journeys_departs_intermediaires_reveles(self):
        """T3bis — la révélation des départs intermédiaires traverse le filtre
        Pareto de l'API : Dijon -> Besançon Viotte renvoie tous les directs
        horaires (08:08, 09:09, 10:12…), pas seulement le premier de chaque
        tranche. (L'ancien scénario 12:17 Saint-Vit -> Bercy est devenu
        dominé au sens Pareto avec l'horaire sept. 2026 : des départs plus
        tardifs atteignent les mêmes arrivées.)"""
        r = self.client.post(
            "/v1/journeys",
            json=_enc({"from": "Dijon", "to": "Besançon Viotte", "date": DATE, "time": "07:00",
                       "count": 20, "sort": "departure"}),
        )
        self.assertEqual(r.status_code, 200)
        js = r.json()["journeys"]
        deps = [j["departure"][11:16] for j in js]
        self.assertIn("08:08", deps)  # entre 07:09 (tranche 07:00) et 10:12 (tranche 10:00)
        self.assertIn("09:09", deps)
        self.assertIn("10:12", deps)

    def test_journeys_prix_estime(self):
        """T12 — chaque trajet expose un prix estimé et ses métadonnées de
        tarification (règle, km, régions) sur /v1/journeys."""
        r = self.client.post(
            "/v1/journeys",
            json=_enc({"from": "Dijon", "to": "Besançon Viotte", "date": DATE, "time": "07:00"}),
        )
        self.assertEqual(r.status_code, 200)
        js = r.json()["journeys"]
        self.assertTrue(js)
        for j in js[:2]:
            self.assertIsInstance(j["price_normal_eur"], float)
            self.assertGreater(j["price_normal_eur"], 0)
            self.assertIn("pricing", j)
            self.assertIn("rule", j["pricing"])
            self.assertIn("km", j["pricing"])
            self.assertTrue(j["pricing"]["legs"])

    def test_journeys_prix_reduit_avec_carte(self):
        """T12 — cards=… : price_reduced_eur baisse avec une carte de la région
        du trajet (BFC solidaire, -75 %) et pricing.cards documente l'application."""
        r = self.client.post(
            "/v1/journeys",
            json=_enc({
                "from": "Dijon", "to": "Besançon Viotte", "date": DATE, "time": "07:00",
                "cards": self.BFC_SOLIDAIRE,
            }),
        )
        self.assertEqual(r.status_code, 200)
        j = r.json()["journeys"][0]
        self.assertIn("price_reduced_eur", j)
        self.assertLess(j["price_reduced_eur"], j["price_normal_eur"])
        self.assertEqual(j["pricing"]["cards"][0]["id"], self.BFC_SOLIDAIRE)
        # 19,00 € plein tarif (escalier Mobigo) × 0,25 -> 4,75 €
        self.assertAlmostEqual(j["price_reduced_eur"], 4.75, delta=0.01)

    def test_journeys_prix_reduit_egal_normal_sans_carte(self):
        r = self.client.post(
            "/v1/journeys",
            json=_enc({"from": "Dijon", "to": "Besançon Viotte", "date": DATE, "time": "07:00"}),
        )
        self.assertEqual(r.status_code, 200)
        for j in r.json()["journeys"][:2]:
            self.assertEqual(j["price_reduced_eur"], j["price_normal_eur"])
            self.assertEqual(j["pricing"]["cards"], [])

    def test_journeys_count_et_max_transfers(self):
        r = self.client.post(
            "/v1/journeys",
            json=_enc({"from": "Paris", "to": "Lyon Perrache", "date": DATE, "time": "08:00", "count": 1, "max_transfers": 3}),
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["journeys"]), 1)

    # ------------------------------------------------------------- T8 temps réel
    def test_journeys_use_realtime_delay_min_present(self):
        """T8 — use_realtime=true : chaque leg expose delay_min (0 par défaut)."""
        r = self.client.post(
            "/v1/journeys",
            json=_enc({"from": "Dijon", "to": "Besançon Viotte", "date": DATE, "time": "07:00", "use_realtime": True}),
        )
        self.assertEqual(r.status_code, 200)
        j = r.json()["journeys"][0]
        for leg in j["legs"]:
            self.assertIn("delay_min", leg)
            self.assertGreaterEqual(leg["delay_min"], 0)

    def test_journeys_sans_poller_delay_min_zero(self):
        """T8 — sans poller temps réel actif, delay_min vaut 0 (use_realtime est
        le défaut, mais aucun flux n'est injecté ici)."""
        r = self.client.post(
            "/v1/journeys",
            json=_enc({"from": "Dijon", "to": "Besançon Viotte", "date": DATE, "time": "07:00"}),
        )
        self.assertEqual(r.status_code, 200)
        j = r.json()["journeys"][0]
        self.assertEqual(j["legs"][0]["delay_min"], 0)

    # --------------------------------------------------- tri par durée (§20)
    def test_sort_duration_le_plus_court_en_premier(self):
        """sort=duration : la durée ne décroît pas, et la liste n'est pas triée
        par départ (le plus court de la journée peut partir plus tard)."""
        q = {"from": "Paris Est", "to": "Strasbourg", "date": DATE, "time": "08:00", "count": 5}
        by_dep = self.client.post("/v1/journeys", json=_enc({**q, "sort": "departure"})).json()["journeys"]
        by_dur = self.client.post("/v1/journeys", json=_enc({**q, "sort": "duration"})).json()["journeys"]
        self.assertEqual(by_dur, sorted(by_dur, key=lambda j: j["duration_min"]))
        # le premier par durée est au moins aussi rapide que le premier par départ
        self.assertLessEqual(by_dur[0]["duration_min"], by_dep[0]["duration_min"])

    def test_sort_duration_reste_ordonne_par_egalite(self):
        """sort=duration : à durée égale, les départs restent triés."""
        q = {"from": "Dijon", "to": "Besançon Viotte", "date": DATE, "time": "07:00", "count": 5,
             "sort": "duration"}
        js = self.client.post("/v1/journeys", json=_enc(q)).json()["journeys"]
        durs = [j["duration_min"] for j in js]
        self.assertEqual(durs, sorted(durs))

    # ---------------------------------------------------- recherche large (§20)
    def test_recherche_large_trajets_de_fin_de_journee(self):
        """RAPTOR simple ne garde que l'arrivée la plus tôt par nombre de
        correspondances : à 08:00, un trajet rapide de 15h38 (5h) était masqué
        par un plus long de 08:03. La recherche large le fait apparaître."""
        q = {"from": "Mouchard", "to": "Paris Bercy", "date": DATE, "time": "08:00", "count": 20}
        js = self.client.post("/v1/journeys", json=_enc(q)).json()["journeys"]
        durs = sorted(j["duration_min"] for j in js)
        # le trajet le plus rapide de la journée doit être < 5h30
        self.assertLess(durs[0], 330)
        # et il faut voir plus de 2 trajets (dont des départs de l'après-midi)
        self.assertGreater(len(js), 2)
        self.assertTrue(any(j["departure"][11:13] >= "14" for j in js))

    # ------------------------------------------------------------ cartes (T11)
    BFC_SOLIDAIRE = "2a730e22c0be4cf0030f89205f540fe39e8dca6b"
    BFC_26 = "5be729fcfc26caa921c53f6d836175d832c288ca"

    def test_cards_liste_cartes_ter(self):
        """T11 — /v1/cards expose les cartes TER (dont la carte solidaire BFC)."""
        r = self.client.get("/v1/cards")
        self.assertEqual(r.status_code, 200)
        cards = r.json()["cards"]
        self.assertGreater(len(cards), 30)
        ids = [c["id"] for c in cards]
        self.assertIn(self.BFC_SOLIDAIRE, ids)
        bfc = next(c for c in cards if c["id"] == self.BFC_SOLIDAIRE)
        self.assertIn("Bourgogne", bfc["name"])
        self.assertIn("name", bfc)
        self.assertIn("shortName", bfc)

    def test_connection_risks_detecte_retard_rongeant_la_marge(self):
        """T8 — une correspondance dont le retard a consommé la marge planifiée
        est signalée (connection_risks) ; absente sans temps réel."""
        from src import api, gtfs_rt
        from src.raptor import RaptorEngine

        engine = api.get_engine()
        g = engine.graph
        j_theo = engine.depart_after(
            DATE_YM, g.resolve_place("Paris Gare de Lyon"),
            g.resolve_place("Besançon Viotte"), 7 * 60, 3, "train_only",
        )[0]
        # premier leg ferroviaire : le K4 Paris Est -> Belfort-Ville (12:48),
        # suivi du C13 Belfort -> Besançon (13:04) : marge planifiée de 16 min.
        # Un retard de 10 min laisse une marge réelle de 6 min (< retard +
        # seuil) : la correspondance doit être signalée à risque.
        feeder = next(l for l in j_theo.legs if l.type == "train")
        trip = g.trips[g.trip_index[feeder.trip_id]]
        feed = gtfs_rt.RealtimeFeed(
            trip_delays={(feeder.trip_id, DATE_YM): {st.stop: 10 for st in trip.stop_times}}
        )
        # injecte le feed dans le poller (le moteur partage l'instantané)
        saved = api._poller
        api._poller = gtfs_rt.RealtimePoller(g)
        api._poller.feed = feed
        try:
            r = self.client.post(
                "/v1/journeys",
                json=_enc({"from": "Paris Gare de Lyon", "to": "Besançon Viotte",
                        "date": DATE, "time": "07:00", "use_realtime": True}),
            )
        finally:
            api._poller = saved
        self.assertEqual(r.status_code, 200)
        j = r.json()["journeys"][0]
        risks = j.get("connection_risks", [])
        self.assertTrue(risks, "le retard K4 10 min doit signaler une correspondance risquée")
        self.assertEqual(risks[0]["at_station"], "Belfort-Ville")
        self.assertEqual((risks[0]["from_line"], risks[0]["to_line"]), ("K4", "C13"))
        self.assertEqual(risks[0]["delay_min"], 10)
        # sans retard injecté, aucune correspondance à risque (champ présent mais vide)
        r0 = self.client.post(
            "/v1/journeys",
            json=_enc({"from": "Paris Gare de Lyon", "to": "Besançon Viotte",
                    "date": DATE, "time": "07:00"}),
        )
        self.assertEqual(r0.json()["journeys"][0].get("connection_risks"), [])

    # ----------------------------------------------------------------- errors
    def test_gare_introuvable_404(self):
        r = self.client.post(
            "/v1/journeys",
            json=_enc({"from": "Zzqq", "to": "Dijon", "date": DATE, "time": "07:00"}),
        )
        self.assertEqual(r.status_code, 404)
        err = r.json()["error"]
        self.assertEqual(err["code"], "STATION_NOT_FOUND")
        self.assertIn("suggestions", err)

    def test_date_invalide_400(self):
        for bad in ("2026-13-01", "08/08/2026", "2026-09-14T07:00"):
            r = self.client.post(
                "/v1/journeys",
                json=_enc({"from": "Dijon", "to": "Besançon Viotte", "date": bad, "time": "07:00"}),
            )
            self.assertEqual(r.status_code, 400, f"date {bad}")
            self.assertEqual(r.json()["error"]["code"], "INVALID_DATE")

    def test_date_hors_plage_400(self):
        r = self.client.post(
            "/v1/journeys",
            json=_enc({"from": "Dijon", "to": "Besançon Viotte", "date": "2029-01-01", "time": "07:00"}),
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "INVALID_DATE")

    def test_heure_invalide_400(self):
        r = self.client.post(
            "/v1/journeys",
            json=_enc({"from": "Dijon", "to": "Besançon Viotte", "date": DATE, "time": "25:00"}),
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "INVALID_TIME")

    def test_parametres_invalides_400(self):
        # max_transfers hors 0..6 et vehicle invalide sont rejetés par l'API
        r = self.client.post(
            "/v1/journeys",
            json=_enc({"from": "Dijon", "to": "Besançon Viotte", "date": DATE, "time": "07:00", "max_transfers": 9}),
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "INVALID_PARAM")
        r = self.client.post(
            "/v1/journeys",
            json=_enc({"from": "Dijon", "to": "Besançon Viotte", "date": DATE, "time": "07:00", "vehicle": "tgv"}),
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "INVALID_PARAM")

    def test_vehicle_defaut_train_only(self):
        """Sans vehicle=, l'API exclut les cars TER (train_only par défaut) ;
        vehicle=all les réintroduit si des cars existent sur le trajet."""
        base = {"from": "Dijon", "to": "Besançon Viotte", "date": DATE, "time": "07:00"}
        r = self.client.post("/v1/journeys", json=_enc(base))
        self.assertEqual(r.status_code, 200)
        js = r.json()["journeys"]
        self.assertTrue(js)
        for j in js:
            for leg in j["legs"]:
                if leg["type"] != "walk":
                    self.assertEqual(leg["type"], "train")


class WebTestCase(unittest.TestCase):
    """T6 — la SPA statique est servie par l'API (§8) et référence les assets."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        _pow.enabled = False

    def test_page_daccueil_servie(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])
        html = r.text
        self.assertIn("PlanTER", html)
        self.assertIn('id="search-form"', html)
        self.assertIn('id="from"', html)
        self.assertIn('id="to"', html)
        # T8 — les retards/suppressions sont appliqués d'office, pas d'option dans le formulaire
        self.assertNotIn('name="realtime"', html)
        # Trains uniquement est le défaut : pas de case dans le formulaire
        self.assertNotIn('name="vehicle"', html)
        self.assertNotIn("Trains uniquement", html)
        # T11 — le champ cartes a été retiré (Trainline n'applique pas la carte via l'URL) ;
        # la logique serveur (param cards=, /v1/cards) reste disponible pour plus tard.
        self.assertNotIn("cards-field", html)

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
        self.assertIn("pas de TGV", html)

    def test_trip_schedule_train_and_bus(self):
        """Vérifie que /v1/trips/{trip_id}/schedule renvoie les circulations du jour et la liste des arrêts."""
        # 1. Rechercher un trajet pour obtenir un trip_id valide
        r = self.client.post(
            "/v1/journeys",
            json=_enc({"from": "Dijon", "to": "Besançon Viotte", "date": DATE, "time": "07:00"}),
        )
        self.assertEqual(r.status_code, 200)
        trip_id = r.json()["journeys"][0]["legs"][0]["trip_id"]

        # 2. Consulter les horaires de la ligne
        r_sched = self.client.get(f"/v1/trips/{trip_id}/schedule?date={DATE}")
        self.assertEqual(r_sched.status_code, 200)
        data = r_sched.json()
        self.assertIn("line", data)
        self.assertIn("trips", data)
        self.assertGreater(len(data["trips"]), 0)
        first_trip = data["trips"][0]
        self.assertIn("departure_time", first_trip)
        self.assertIn("stops", first_trip)
        self.assertGreater(len(first_trip["stops"]), 0)


if __name__ == "__main__":
    unittest.main()
