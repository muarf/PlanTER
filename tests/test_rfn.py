import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.rfn import uic_from_stop_id

class TestUicFromStopId(unittest.TestCase):
    def test_happy_paths(self):
        self.assertEqual(uic_from_stop_id("StopArea:OCE87734004"), "87734004")
        self.assertEqual(uic_from_stop_id("StopPoint:OCETrainTER-87734004"), "87734004")
        self.assertEqual(uic_from_stop_id("StopPoint:OCETrainTER-87734004:some_suffix"), "87734004")
        self.assertEqual(uic_from_stop_id("OCE87734004"), "87734004")

    def test_edge_cases(self):
        self.assertIsNone(uic_from_stop_id("StopArea:SNCF87734004"))
        self.assertIsNone(uic_from_stop_id(""))
        self.assertIsNone(uic_from_stop_id("87734004"))
        self.assertIsNone(uic_from_stop_id("stoparea:oce87734004"))
        self.assertIsNone(uic_from_stop_id("oce87734004"))

if __name__ == "__main__":
    unittest.main()
