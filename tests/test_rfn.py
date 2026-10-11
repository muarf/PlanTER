import sys
import unittest
from pathlib import Path
import math

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.rfn import _haversine, EARTH_R_KM

class TestRfn(unittest.TestCase):
    def test_haversine_identity(self):
        self.assertEqual(_haversine(48.8566, 2.3522, 48.8566, 2.3522), 0.0)

    def test_haversine_symmetry(self):
        d1 = _haversine(48.8566, 2.3522, 45.7640, 4.8357)
        d2 = _haversine(45.7640, 4.8357, 48.8566, 2.3522)
        self.assertEqual(d1, d2)

    def test_haversine_known_distance(self):
        # Paris (48.8566, 2.3522) to Lyon (45.7640, 4.8357) is roughly 392 km.
        dist = _haversine(48.8566, 2.3522, 45.7640, 4.8357)
        self.assertTrue(385 < dist < 395)

    def test_haversine_equator(self):
        # 1 degree longitude difference at the equator
        dist = _haversine(0.0, 0.0, 0.0, 1.0)
        expected_dist = (math.pi / 180.0) * EARTH_R_KM
        self.assertAlmostEqual(dist, expected_dist, places=5)

if __name__ == "__main__":
    unittest.main()
