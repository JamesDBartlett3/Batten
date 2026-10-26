"""TUI: pure frame builders, headless rendering, demo gather."""

import os
import re
import unittest

from . import sample_alert, sample_storm, temp_home, write_config
from .. import replay, tui


def cfg(**over):
    base = {"place": "Testville", "lat": 25.76, "lon": -80.19, "household": 3,
            "timezone": "America/New_York", "units": "imperial",
            "ollama_url": "http://127.0.0.1:9", "model": "m"}
    base.update(over)
    return base


def data(**over):
    d = {
        "cfg": cfg(), "place": "Testville", "coords": "25.76, -80.19",
        "units_pref": "imperial", "local_time": "2026-10-17 12:00",
        "connectivity": {"online": True, "detail": "online"},
        "model": {"ok": False, "detail": "down"},
        "storms": [{"name": "Trudy", "cat": "Category 2 hurricane", "speed": "97 mph",
                    "pressure": "970", "movement": "moving NW at 9 mph",
                    "dist_bearing": "142 nm SSW of Testville",
                    "relation": "HEADING TOWARD YOU", "relation_kind": "toward",
                    "advisory": "20"}],
        "alerts": [sample_alert()],
        "alerts_note": "",
        "supplies_text": "\nSupply readiness: 0 ready, 27 to get",
        "supplies_have": 0, "supplies_missing": 27, "supplies_missing_essential": 16,
        "source": "demo",
    }
    d.update(over)
    return d


class TestDashboardLines(unittest.TestCase):
    def test_contains_key_sections(self):
        lines = tui.dashboard_lines(data(), 100)
        text = tui.strip_ansi("\n".join(lines))
        self.assertIn("BATTEN — Testville", text)
        self.assertIn("STORMS (1)", text)
        self.assertIn("Trudy", text)
        self.assertIn("HEADING TOWARD YOU", text)
        self.assertIn("ALERTS (1)", text)
        self.assertIn("Hurricane Warning", text)
        self.assertIn("SUPPLIES", text)

    def test_offline_connectivity_marked(self):
        d = data(connectivity={"online": False, "detail": "OFFLINE 6.0 h"})
        text = tui.strip_ansi("\n".join(tui.dashboard_lines(d, 100)))
        self.assertIn("OFFLINE 6.0 h", text)

    def test_lines_clipped_to_width(self):
        lines = tui.dashboard_lines(data(), 40)
        for ln in lines:
            self.assertLessEqual(len(tui.strip_ansi(ln)), 40)

    def test_metric_units(self):
        d = data(units_pref="metric",
                 storms=[{"name": "T", "cat": "Tropical Storm", "speed": "102 km/h",
                          "pressure": "995", "movement": "moving NW at 13 km/h",
                          "dist_bearing": "263 km WSW of X", "relation": "",
                          "relation_kind": "", "advisory": "19"}])
        text = tui.strip_ansi("\n".join(tui.dashboard_lines(d, 100)))
        self.assertIn("km/h", text)


class TestOverlayLines(unittest.TestCase):
    def test_title_and_body(self):
        lines = tui.overlay_lines("HELP", "line one\nline two", 80)
        text = tui.strip_ansi("\n".join(lines))
        self.assertIn("HELP", text)
        self.assertIn("line one", text)
        self.assertIn("line two", text)


class TestStripAnsi(unittest.TestCase):
    def test_strips_colors(self):
        raw = tui.paint("hello", "red", bold=True)
        self.assertEqual(tui.strip_ansi(raw), "hello")


class TestGather(unittest.TestCase):
    def test_demo_gather_sim_time(self):
        cfg_d, st = replay.replay_all(8)
        d = tui.gather(cfg_d, st, source="demo")
        self.assertEqual(len(d["storms"]), 1)
        self.assertIn("2026-10-17", d["local_time"])
        self.assertFalse(d["connectivity"]["online"])

    def test_offline_gather_uses_last_known(self):
        st = {"storms": {"x": {"name": "Old", "kt": 50, "cat": "TS", "lat": 20, "lon": -78,
                               "pressure": "1000", "movement_dir": None,
                               "movement_kt": 0, "advisory": "3", "issued": "",
                               "dist_nm": None, "_flags": {}}},
              "outage_started": "2026-10-17T00:00:00Z", "outage_kind": "dns",
              "last_success": "2026-10-17T00:00:00Z",
              "_sim_now_iso": "2026-10-17T03:00:00Z"}
        d = tui.gather(cfg(), st, source="demo")
        self.assertEqual(d["storms"][0]["name"], "Old")
        self.assertIn("OFFLINE 3.0 h", d["connectivity"]["detail"])


class TestHeadlessRender(unittest.TestCase):
    def test_demo_frames_written(self):
        out = "/tmp/batten-test-frames"
        paths = tui.render_headless(cfg(), source="demo", out_dir=out)
        self.assertEqual(len(paths), 6)     # dashboard, alerts, supplies, worstcase, log, dashboard-outage
        for p in paths:
            self.assertTrue(os.path.exists(p), p)
        dash = open(os.path.join(out, "demo", "dashboard.txt")).read()
        self.assertIn("Trudy", dash)
        outage = open(os.path.join(out, "demo", "dashboard-outage.txt")).read()
        self.assertIn("OFFLINE 6.0 h", tui.strip_ansi(outage))
        log = open(os.path.join(out, "demo", "log.txt")).read()
        self.assertIn("Trudy", tui.strip_ansi(log))

    def test_live_source_smoke(self):
        with temp_home():
            write_config()
            paths = tui.render_headless(cfg(), source="live", out_dir="/tmp/batten-test-frames")
            self.assertTrue(any(p.endswith("dashboard.txt") for p in paths))


if __name__ == "__main__":
    unittest.main()
