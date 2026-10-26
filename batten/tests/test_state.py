"""Connectivity state machine + weather log."""

import unittest
from datetime import datetime, timezone

from . import temp_home
from .. import state


def t(h):
    return datetime(2026, 10, 17, h, 0, tzinfo=timezone.utc)


class TestConnectivity(unittest.TestCase):
    def test_first_failure_starts_outage(self):
        st = {}
        info = state.record_poll_failure(st, "dns", "boom", t(0))
        self.assertTrue(info["went_down"])
        self.assertIsNone(info["outage_seconds"])
        self.assertEqual(st["outage_kind"], "dns")

    def test_outage_accumulates(self):
        st = {}
        state.record_poll_failure(st, "timeout", "x", t(0))
        info = state.record_poll_failure(st, "timeout", "x", t(6))
        self.assertFalse(info["went_down"])
        self.assertAlmostEqual(info["outage_seconds"], 21600, delta=1)

    def test_recovery_records_outage_and_resets(self):
        st = {}
        state.record_poll_failure(st, "dns", "x", t(0))
        done = state.record_poll_success(st, t(3))
        self.assertTrue(done["came_back"])
        self.assertIsNone(st["outage_started"])
        self.assertEqual(st["last_outage"]["started"], state.iso(t(0)))
        self.assertFalse(st["worstcase_sent_for_outage"])

    def test_outage_seconds_none_when_online(self):
        st = {"outage_started": None}
        self.assertIsNone(state.outage_seconds(st, t(5)))


class TestWeatherLog(unittest.TestCase):
    def test_append_and_tail(self):
        with temp_home():
            state.append_weather_log("first", ["a", "b"], t(1))
            state.append_weather_log("second", ["c"], t(2))
            tail = state.tail_weather_log()
            self.assertIn("first", tail)
            self.assertIn("second", tail)
            self.assertIn("- c", tail)

    def test_tail_when_missing(self):
        with temp_home():
            self.assertEqual(state.tail_weather_log(), "(no weather log yet)")

    def test_iso_roundtrip(self):
        s = state.iso(t(5))
        self.assertTrue(s.endswith("Z"))
        self.assertEqual(state.parse_iso(s), t(5))
        self.assertIsNone(state.parse_iso(""))


if __name__ == "__main__":
    unittest.main()
