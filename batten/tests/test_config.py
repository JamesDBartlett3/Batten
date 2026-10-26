"""Config defaults, persistence, and timezone handling."""

import unittest

from . import temp_home, write_config
from .. import config


class TestConfig(unittest.TestCase):
    def test_defaults_present(self):
        cfg = config.load_config()
        self.assertIsNone(cfg["lat"])
        self.assertEqual(cfg["units"], "imperial")
        self.assertEqual(cfg["alert_providers"], [])
        self.assertEqual(cfg["web_port"], 8787)
        self.assertEqual(cfg["outage_worstcase_hours"], 6)

    def test_save_load_roundtrip(self):
        with temp_home():
            cfg = write_config(units="metric", household=5)
            loaded = config.load_config()
            self.assertEqual(loaded["units"], "metric")
            self.assertEqual(loaded["household"], 5)
            self.assertEqual(loaded["lat"], cfg["lat"])
            self.assertTrue(config.config_exists())

    def test_broken_config_falls_back_to_defaults(self):
        with temp_home():
            from pathlib import Path
            config.home_dir().mkdir(parents=True, exist_ok=True)
            config.config_path().write_text("{not json")
            cfg = config.load_config()
            self.assertEqual(cfg["units"], "imperial")

    def test_tzinfo_known_and_unknown(self):
        self.assertIsNotNone(config.tzinfo_for({"timezone": "America/Chicago"}))
        self.assertIsNotNone(config.tzinfo_for({"timezone": "Not/AZone"}))  # falls back
        self.assertIsNotNone(config.tzinfo_for({}))                         # "local"


if __name__ == "__main__":
    unittest.main()
