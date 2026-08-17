"""Tests chiffrement — src/crypto.py."""
import base64
import unittest

from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes, serialization

from src.crypto import CryptoEngine


class CryptoTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ce = CryptoEngine()

    def test_pubkey_is_pem(self):
        pk = self.ce.pubkey_pem()
        self.assertIn("BEGIN PUBLIC KEY", pk)
        self.assertIn("END PUBLIC KEY", pk)

    def test_roundtrip(self):
        data = {"from": "Paris", "to": "Lyon", "date": "2026-08-10"}
        payload = self.ce.encrypt_b64(data)
        result = self.ce.decrypt_b64(payload)
        self.assertEqual(result, data)

    def test_roundtrip_empty_dict(self):
        payload = self.ce.encrypt_b64({})
        self.assertEqual(self.ce.decrypt_b64(payload), {})

    def test_roundtrip_unicode(self):
        data = {"from": "Strasbourg", "to": "Mulhouse-Ville"}
        payload = self.ce.encrypt_b64(data)
        self.assertEqual(self.ce.decrypt_b64(payload), data)

    def test_different_payloads_different_ciphertext(self):
        data1 = {"from": "A"}
        data2 = {"from": "B"}
        p1 = self.ce.encrypt_b64(data1)
        p2 = self.ce.encrypt_b64(data2)
        self.assertNotEqual(p1, p2)

    def test_decrypt_invalid_base64(self):
        with self.assertRaises(Exception):
            self.ce.decrypt_b64("not-valid-base64!!!")

    def test_decrypt_garbage(self):
        garbage = base64.b64encode(b"garbage data that is not encrypted").decode()
        with self.assertRaises(Exception):
            self.ce.decrypt_b64(garbage)

    def test_different_engine_cannot_decrypt(self):
        ce2 = CryptoEngine()
        data = {"from": "Paris"}
        payload = self.ce.encrypt_b64(data)
        with self.assertRaises(Exception):
            ce2.decrypt_b64(payload)

    def test_full_journey_params(self):
        data = {
            "from": "Paris Bercy",
            "to": "Dijon",
            "date": "2026-08-10",
            "time": "07:00",
            "datetime_represents": "departure",
            "max_transfers": 6,
            "vehicle": "train_only",
            "count": 5,
            "sort": "transfers",
            "use_realtime": True,
            "cards": "",
        }
        payload = self.ce.encrypt_b64(data)
        result = self.ce.decrypt_b64(payload)
        self.assertEqual(result, data)


if __name__ == "__main__":
    unittest.main()
