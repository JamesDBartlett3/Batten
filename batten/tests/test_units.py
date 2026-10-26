"""Unit formatting (imperial/metric)."""

import unittest

from ..units import Units


class TestImperial(unittest.TestCase):
    def setUp(self):
        self.u = Units("imperial")

    def test_speed(self):
        self.assertEqual(self.u.speed(64.3), "74 mph")

    def test_distance(self):
        self.assertEqual(self.u.distance(126), "126 nm (233 km)")
        self.assertEqual(self.u.distance(5), "5.0 nm (9.3 km)")

    def test_rain_range(self):
        self.assertEqual(self.u.rain_range(6, 12), "6-12 in")
        self.assertEqual(self.u.rain(2.4), "2 in")   # rounds to int

    def test_temp(self):
        self.assertEqual(self.u.temp_f(85), "85°F")
        self.assertEqual(self.u.temp_f(None), "?")

    def test_volume(self):
        self.assertEqual(self.u.volume(9), "9 gal (34 L)")


class TestMetric(unittest.TestCase):
    def setUp(self):
        self.u = Units("metric")

    def test_speed(self):
        self.assertEqual(self.u.speed(64.3), "119 km/h")

    def test_distance(self):
        self.assertEqual(self.u.distance(126), "233 km (126 nm)")
        self.assertEqual(self.u.distance(5), "9.3 km (5.0 nm)")

    def test_rain_range(self):
        self.assertEqual(self.u.rain_range(6, 12), "152-305 mm")
        self.assertEqual(self.u.rain(2), "51 mm")

    def test_temp(self):
        self.assertEqual(self.u.temp_f(85), "29°C")
        self.assertEqual(self.u.temp_f(32), "0°C")
        self.assertEqual(self.u.temp_f(None), "?")

    def test_volume(self):
        self.assertEqual(self.u.volume(9), "34 L")


class TestFallbacks(unittest.TestCase):
    def test_bad_pref_falls_back_to_imperial(self):
        for bad in (None, "", "bogus", "IMPERIAL!!"):
            self.assertFalse(Units(bad).metric, f"pref={bad!r}")

    def test_case_insensitive_metric(self):
        self.assertTrue(Units("Metric").metric)
        self.assertTrue(Units("SI").metric)

    def test_pref_property(self):
        self.assertEqual(Units("metric").pref, "metric")
        self.assertEqual(Units(None).pref, "imperial")


if __name__ == "__main__":
    unittest.main()
