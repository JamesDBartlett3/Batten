"""Supply checklist: scaling, statuses, rendering."""

import unittest

from . import temp_home, write_config
from .. import supplies


class TestScaling(unittest.TestCase):
    def test_water_scales_by_household(self):
        item = next(i for i in supplies.DEFAULT_SUPPLIES if i["id"] == "water")
        self.assertIn("12", supplies.scaled_quantity(item, 4))     # 1 gal x 3 days x 4
        self.assertIn("6", supplies.scaled_quantity(item, 2))

    def test_food_in_person_days(self):
        item = next(i for i in supplies.DEFAULT_SUPPLIES if i["id"] == "food")
        self.assertIn("12 person-days", supplies.scaled_quantity(item, 4))

    def test_metric_water_in_liters(self):
        from ..units import Units
        item = next(i for i in supplies.DEFAULT_SUPPLIES if i["id"] == "water")
        qty = supplies.scaled_quantity(item, 4, Units("metric"))
        self.assertIn("45 L", qty)          # 12 gal ~ 45 L


class TestStatuses(unittest.TestCase):
    def test_set_and_missing(self):
        sup = {}
        self.assertTrue(supplies.set_item(sup, "water", "have", True))
        self.assertTrue(supplies.set_item(sup, "food", "na", True))
        self.assertFalse(supplies.set_item(sup, "nope", "have", True))
        missing = supplies.missing_essentials(4, sup)
        self.assertFalse(any("Drinking water" in m for m in missing))
        self.assertFalse(any("Non-perishable" in m for m in missing))
        self.assertTrue(any("First-aid" in m for m in missing))

    def test_need_clears_have(self):
        sup = {}
        supplies.set_item(sup, "water", "have", True)
        supplies.set_item(sup, "water", "have", False)
        self.assertTrue(any("Drinking water" in m
                            for m in supplies.missing_essentials(2, sup)))

    def test_na_and_have_are_exclusive(self):
        sup = {}
        supplies.set_item(sup, "water", "have", True)
        supplies.set_item(sup, "water", "na", True)
        self.assertNotIn("have", sup["water"])
        supplies.set_item(sup, "water", "have", True)
        self.assertNotIn("na", sup["water"])

    def test_note(self):
        sup = {}
        supplies.set_item(sup, "radio", "note", "garage shelf")
        self.assertEqual(sup["radio"]["note"], "garage shelf")


class TestRender(unittest.TestCase):
    def test_render_counts_and_categories(self):
        sup = {}
        for iid in ("water", "food", "canopener"):
            supplies.set_item(sup, iid, "have", True)
        text, have, missing = supplies.render_supplies(3, sup)
        self.assertEqual((have, missing), (3, len(supplies.DEFAULT_SUPPLIES) - 3))
        for cat in ("Water:", "Food:", "Power & light:"):
            self.assertIn(cat, text)

    def test_essential_marked_missing(self):
        text, _, _ = supplies.render_supplies(2, {})
        self.assertIn("❌", text)

    def test_persistence_roundtrip(self):
        with temp_home():
            sup = {}
            supplies.set_item(sup, "water", "have", True)
            supplies.set_item(sup, "water", "note", "pantry")
            supplies.save_supplies(sup)
            loaded = supplies.load_supplies()
            self.assertTrue(loaded["water"]["have"])
            self.assertEqual(loaded["water"]["note"], "pantry")


if __name__ == "__main__":
    unittest.main()
