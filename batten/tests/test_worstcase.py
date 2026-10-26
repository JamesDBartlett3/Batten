"""Worst-case engine: arrival math, intensity assumptions, rendering."""

import unittest

from . import sample_storm
from .. import worstcase


def make(**over):
    s = sample_storm(kt=80, lat=10.0, lon=0.0, movement_dir=0, movement_kt=10,
                     pressure=980, name="Test")
    if over:
        s.update(over)
    return s


class TestEngine(unittest.TestCase):
    def test_arrival_and_worst_intensity(self):
        wc = worstcase.build_worst_case(make(), 10 + 200 / 60, 0.0, "t0")
        self.assertEqual(wc.worst_kt, 100.0)
        self.assertEqual(wc.worst_cat, "CAT3")              # 100 kt = CAT3
        self.assertIsNotNone(wc.ts_arrival_h)
        self.assertLessEqual(wc.ts_arrival_h, 12)
        self.assertLess(wc.closest_nm, 200)
        self.assertEqual((wc.rain_low_in, wc.rain_high_in), (6.0, 12.0))

    def test_intensity_capped_at_cat5(self):
        wc = worstcase.build_worst_case(make(kt=140), 40.0, -50.0, "t0")
        self.assertEqual(wc.worst_kt, 150.0)
        self.assertEqual(wc.worst_cat, "CAT5")

    def test_stationary_storm(self):
        wc = worstcase.build_worst_case(make(**{'movement_dir': None, 'movement_kt': 0}),
                                        10.05, 0.0, "t0")
        self.assertFalse(wc.moving)
        self.assertEqual(wc.ts_arrival_h, 0.0)
        self.assertTrue(any("stationary" in n.lower() for n in wc.notes))

    def test_far_storm_no_arrival(self):
        wc = worstcase.build_worst_case(make(lat=0.0, lon=50.0, movement_dir=90,
                                             movement_kt=12, kt=100),
                                        20.0, -105.0, "t0")
        self.assertIsNone(wc.ts_arrival_h)

    def test_no_storm_generic_case(self):
        wc = worstcase.build_worst_case(None, 20.0, -105.0, "t0", outage_hours=7)
        self.assertIsNone(wc.storm_name)
        self.assertTrue(any("No tropical cyclone" in n for n in wc.notes))

    def test_timeline_has_error_growth(self):
        wc = worstcase.build_worst_case(make(), 13.0, 0.0, "t0")
        by_h = {t["h"]: t for t in wc.timeline}
        self.assertLess(by_h[6]["error_nm"], by_h[48]["error_nm"])
        self.assertEqual(by_h[48]["error_nm"], 90)


class TestRendering(unittest.TestCase):
    def test_imperial_defaults_and_labels(self):
        wc = worstcase.build_worst_case(make(), 13.333, 0.0, "2026-01-01T00:00:00Z")
        text = worstcase.render_worst_case(wc)
        self.assertIn("ESTIMATE", text)
        self.assertIn("mph", text)
        self.assertIn("in —", text)
        self.assertIn("2026-01-01T00:00:00Z", text)

    def test_metric_rendering(self):
        wc = worstcase.build_worst_case(make(), 13.333, 0.0, "t0")
        text = worstcase.render_worst_case(wc, {"units": "metric"})
        self.assertIn("km/h", text)
        self.assertIn("mm", text)
        self.assertNotIn(" mph", text)

    def test_relation_lines(self):
        wc = worstcase.build_worst_case(make(movement_dir=0), 13.333, 0.0, "t0")
        self.assertIn("TOWARD your position", worstcase.render_worst_case(wc))
        wc2 = worstcase.build_worst_case(make(movement_dir=0, lat=16.0), 13.333, 0.0, "t0")
        self.assertIn("away from your position", worstcase.render_worst_case(wc2))

    def test_far_storm_gets_offshore_note_no_rain(self):
        wc = worstcase.build_worst_case(make(lat=0.0, lon=50.0, movement_dir=90,
                                             movement_kt=12, kt=100),
                                        20.0, -105.0, "t0")
        text = worstcase.render_worst_case(wc)
        self.assertIn("offshore", text)
        self.assertNotIn("Rainfall worst case", text)

    def test_no_storm_render_mentions_outage(self):
        wc = worstcase.build_worst_case(None, 20.0, -105.0, "t0", outage_hours=6.5)
        self.assertIn("6.5 h", worstcase.render_worst_case(wc))


if __name__ == "__main__":
    unittest.main()
