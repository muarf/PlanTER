"""T11 — Cartes de réduction TER (module src/trainline_cards)."""
import unittest

from src import trainline_cards as tc

BFC_SOLIDAIRE = "2a730e22c0be4cf0030f89205f540fe39e8dca6b"
BFC_26 = "5be729fcfc26caa921c53f6d836175d832c288ca"


class TestCards(unittest.TestCase):
    def test_cards_liste_ter_seulement(self):
        cards = tc.cards()
        self.assertGreaterEqual(len(cards), 30)
        self.assertTrue(all("id" in c and "name" in c and "shortName" in c for c in cards))

    def test_card_by_id(self):
        bfc = tc.card_by_id(BFC_SOLIDAIRE)
        self.assertIsNotNone(bfc)
        self.assertIn("Bourgogne", bfc["name"])
        self.assertIsNone(tc.card_by_id("inconnue"))

    def test_valid_ids_filtre_et_dedupe(self):
        ids = tc.valid_ids([BFC_SOLIDAIRE, "inconnue", BFC_SOLIDAIRE, BFC_26])
        self.assertEqual(ids, [BFC_SOLIDAIRE, BFC_26])

    def test_booking_url_sans_carte_inchangee(self):
        url = "https://www.thetrainline.com/book/results?origin=urn%3Atrainline%3Ageneric%3Aloc%3A38&destination=urn%3Atrainline%3Ageneric%3Aloc%3A45"
        self.assertEqual(tc.booking_url(url, []), url)

    def test_booking_url_avec_carte(self):
        url = "https://www.thetrainline.com/book/results?origin=x&destination=y"
        out = tc.booking_url(url, [BFC_SOLIDAIRE])
        self.assertIn(f"passengerDiscountCards[]={BFC_SOLIDAIRE}", out)
        self.assertIn("passengers[]=1993-08-12|pid-0", out)
        self.assertIn("?", out)

    def test_booking_url_dob_personnalisee(self):
        url = "https://www.thetrainline.com/book/results?origin=x&destination=y"
        out = tc.booking_url(url, [BFC_SOLIDAIRE], passenger_dob="1990-01-01")
        self.assertIn("passengers[]=1990-01-01|pid-0", out)


if __name__ == "__main__":
    unittest.main()
