"""Geodesy + meteorology math."""

import unittest

from .. import geo
from . import sample_storm


class TestDistanceBearing(unittest.TestCase):
    def test_haversine_known_pair(self):
        # Miami <-> Houston ~ 835 nm
        d = geo.haversine_nm(25.7617, -80.1918, 29.7604, -95.3698)
        self.assertAlmostEqual(d, 835, delta=25)

    def test_haversine_symmetric_and_zero(self):
        self.assertEqual(geo.haversine_nm(10, 20, 10, 20), 0)
        self.assertAlmostEqual(geo.haversine_nm(0, 0, 10, 20),
                               geo.haversine_nm(10, 20, 0, 0), delta=1e-6)

    def test_bearing_cardinals(self):
        self.assertAlmostEqual(geo.bearing_deg(0, 0, 10, 0), 0, delta=1)
        self.assertAlmostEqual(geo.bearing_deg(0, 0, 0, 10), 90, delta=1)
        self.assertAlmostEqual(geo.bearing_deg(10, 0, 0, 0), 180, delta=1)
        self.assertAlmostEqual(geo.bearing_deg(0, 10, 0, 0), 270, delta=1)

    def test_destination_roundtrip(self):
        lat2, lon2 = geo.destination(20.0, -105.0, 325, 120)
        self.assertAlmostEqual(geo.haversine_nm(20, -105, lat2, lon2), 120, delta=0.5)
        delta = abs(geo.bearing_deg(20, -105, lat2, lon2) - 325)
        self.assertLess(min(delta, 360 - delta), 0.5)

    def test_compass_points(self):
        for deg, want in [(0, "N"), (45, "NE"), (90, "E"), (345, "NNW"), (359.9, "N")]:
            self.assertEqual(geo.compass_point(deg), want)

    def test_fmt_distance(self):
        self.assertIn("nm", geo.fmt_distance(50))
        self.assertIn("km", geo.fmt_distance(50))


class TestSaffir(unittest.TestCase):
    def test_boundaries(self):
        cases = {33: "TD", 34: "TS", 63: "TS", 64: "CAT1", 82: "CAT1", 83: "CAT2",
                 95: "CAT2", 96: "CAT3", 112: "CAT3", 113: "CAT4", 136: "CAT4",
                 137: "CAT5", 165: "CAT5"}
        for kt, want in cases.items():
            self.assertEqual(geo.saffir(kt), want, f"kt={kt}")

    def test_labels(self):
        self.assertIn("Tropical Storm", geo.category_label("TS"))
        self.assertIn("Category 5", geo.category_label("CAT5"))


class TestApproach(unittest.TestCase):
    def test_toward(self):
        # storm due south of user, heading north -> toward
        storm = sample_storm(lat=10.0, lon=0.0, movement_dir=0)
        ap = geo.approach(storm, 13.333, 0.0)
        self.assertEqual(ap["relation"], "toward")
        self.assertLessEqual(ap["angle_diff_deg"], 45)

    def test_away(self):
        storm = sample_storm(lat=13.333, lon=0.0, movement_dir=0)  # north of user, going north
        ap = geo.approach(storm, 10.0, 0.0)
        self.assertEqual(ap["relation"], "away")

    def test_passing(self):
        storm = sample_storm(lat=10.0, lon=0.0, movement_dir=90)  # east, user north
        ap = geo.approach(storm, 13.333, 0.0)
        self.assertEqual(ap["relation"], "passing")

    def test_unknown_track(self):
        storm = sample_storm(lat=10.0, lon=0.0, movement_dir=None)
        self.assertEqual(geo.approach(storm, 13.0, 0.0)["relation"], "unknown")

    def test_boundary_45_is_toward(self):
        storm = sample_storm(lat=10.0, lon=0.0, movement_dir=45)
        self.assertEqual(geo.approach(storm, 13.333, 0.0)["relation"], "toward")


if __name__ == "__main__":
    unittest.main()
