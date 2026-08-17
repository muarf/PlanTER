"""Tests PoW — src/pow.py."""
import hashlib
import time
import unittest

from src.pow import PoWEngine, solve


class PoWTestCase(unittest.TestCase):
    def test_challenge_has_salt_and_difficulty(self):
        e = PoWEngine()
        ch = e.generate_challenge()
        self.assertIn("salt", ch)
        self.assertIn("difficulty", ch)
        self.assertIsInstance(ch["salt"], str)
        self.assertEqual(len(ch["salt"]), 32)

    def test_verify_valid(self):
        e = PoWEngine(difficulty=4)
        ch = e.generate_challenge()
        nonce = solve(ch["salt"], ch["difficulty"])
        self.assertIsNotNone(nonce)
        self.assertTrue(e.verify(ch["salt"], nonce))

    def test_verify_invalid_nonce(self):
        e = PoWEngine(difficulty=4)
        ch = e.generate_challenge()
        self.assertFalse(e.verify(ch["salt"], "999999999"))

    def test_verify_expired_salt(self):
        e = PoWEngine(difficulty=4)
        ch = e.generate_challenge()
        e._challenges[ch["salt"]]["ts"] = time.monotonic() - 120
        nonce = solve(ch["salt"], ch["difficulty"])
        self.assertFalse(e.verify(ch["salt"], nonce))

    def test_verify_unknown_salt(self):
        e = PoWEngine()
        self.assertFalse(e.verify("inconnu", "0"))

    def test_solve_finds_nonce(self):
        e = PoWEngine(difficulty=3)
        ch = e.generate_challenge()
        nonce = solve(ch["salt"], 3)
        self.assertIsNotNone(nonce)
        h = hashlib.sha256(f"{ch['salt']}:{nonce}".encode()).hexdigest()
        self.assertTrue(h.startswith("000"))

    def test_difficulty_setter(self):
        e = PoWEngine()
        e.difficulty = 8
        self.assertEqual(e.difficulty, 6)  # capped at max
        e.difficulty = 1
        self.assertEqual(e.difficulty, 2)  # min is 2

    def test_cleanup_removes_expired(self):
        e = PoWEngine()
        ch = e.generate_challenge()
        self.assertEqual(len(e._challenges), 1)
        e._challenges[ch["salt"]]["ts"] = time.monotonic() - 120
        e._last_cleanup = 0
        e._maybe_cleanup()
        self.assertEqual(len(e._challenges), 0)


if __name__ == "__main__":
    unittest.main()
