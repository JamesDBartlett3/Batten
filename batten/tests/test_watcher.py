"""Watcher: trigger diffs, poll cycles, outage→worst-case fire, cadence."""

import unittest
from datetime import datetime, timedelta, timezone

from . import sample_storm, temp_home, write_config
from .. import watcher
from ..errors import FetchError


def cfg(**over):
    base = {"place": "Testville", "lat": 25.0, "lon": -80.0, "units": "imperial",
            "notify_radius_nm": 600, "poll_minutes": 120, "tight_poll_minutes": 30,
            "outage_poll_minutes": 10, "outage_worstcase_hours": 6,
            "timezone": "America/New_York", "discord_webhook": ""}
    base.update(over)
    return base


class TestDiffStorms(unittest.TestCase):
    def test_new_nearby_storm_triggers(self):
        trig = watcher.diff_storms({}, [sample_storm(lat=24.6, lon=-80.2)], cfg())
        self.assertTrue(any(t.kind == "NEW_STORM" for t in trig))

    def test_far_new_storm_silent(self):
        c = cfg(notify_radius_nm=50)
        trig = watcher.diff_storms({}, [sample_storm(lat=20.0, lon=-95.0)], c)
        self.assertFalse(any(t.kind == "NEW_STORM" for t in trig))

    def test_category_change_and_advisory(self):
        prev = {"al912026": {"name": "Test", "kt": 70, "advisory": "11", "_flags": {},
                             "dist_nm": 30, "lat": 20.0, "lon": -105.0}}
        trig = watcher.diff_storms(prev, [sample_storm(kt=90, advisory="12")], cfg())
        kinds = {t.kind for t in trig}
        self.assertIn("CATEGORY_CHANGE", kinds)
        self.assertIn("ADVISORY_UPDATE", kinds)

    def test_rapid_intensification_same_category(self):
        prev = {"al912026": {"name": "Test", "kt": 84, "advisory": "12", "_flags": {},
                             "dist_nm": 30, "lat": 20.0, "lon": -105.0}}
        trig = watcher.diff_storms(prev, [sample_storm(kt=97)], cfg())  # 84->97 same CAT2? 97=CAT2,84=CAT1
        self.assertTrue(any(t.kind in ("RAPID_INTENSIFICATION", "CATEGORY_CHANGE") for t in trig))

    def test_proximity_fires_once_per_threshold(self):
        c = cfg()
        cur = [sample_storm(lat=25.05, lon=-80.05)]        # ~7 nm
        prev = {"al912026": {"name": "Test", "kt": 80, "advisory": "12", "_flags": {},
                             "dist_nm": 400, "lat": 20.0, "lon": -105.0}}
        first = watcher.diff_storms(prev, cur, c)
        self.assertTrue(any(t.kind == "PROXIMITY" for t in first))
        prev["al912026"]["_flags"] = {"prep_warned": True, "act_warned": True}
        second = watcher.diff_storms({"al912026": prev["al912026"]}, cur, c)
        self.assertFalse(any(t.kind == "PROXIMITY" for t in second))

    def test_storm_gone_after_relevance(self):
        prev = {"al912026": {"name": "Test", "kt": 80, "advisory": "12", "_flags": {},
                             "dist_nm": 200, "lat": 20.0, "lon": -105.0}}
        trig = watcher.diff_storms(prev, [], cfg())
        self.assertTrue(any(t.kind == "STORM_GONE" for t in trig))

    def test_storm_line_includes_units_and_relation(self):
        line = watcher._storm_line(sample_storm(), cfg(units="metric"))
        self.assertIn("km/h", line)
        self.assertIn("nm", line)
        self.assertRegex(line.replace("**", ""), r"(toward you|away from you|passing to your)")


class TestPoll(unittest.TestCase):
    def test_poll_success_records_and_logs(self):
        with temp_home():
            write_config()
            st = {"storms": {}, "alerts_hash": "", "alert_events": []}
            sim = {"t": datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)}
            from batten.tests import sample_alert
            fetcher = lambda c, s: {"storms": [sample_storm()], "alerts": [sample_alert()],
                                    "alerts_note": ""}
            w = watcher.Watcher(cfg(), st, fetcher=fetcher, clock=lambda: sim["t"],
                                sleep=lambda s: None, say=lambda t: None, send=lambda t: None)
            trig = w.poll()
            self.assertEqual(len(st["storms"]), 1)
            self.assertTrue(st["last_success"])
            self.assertEqual(st["alert_events"], ["Hurricane Warning"])
            self.assertEqual(trig, [])

    def test_outage_warns_then_fires_worstcase_at_threshold(self):
        with temp_home():
            write_config()
            st = {"storms": {"al912026": {"name": "Test", "kt": 80, "cat": "CAT1",
                                          "lat": 24.0, "lon": -80.5, "pressure": 980,
                                          "movement_dir": 10, "movement_kt": 8,
                                          "advisory": "12", "issued": "", "dist_nm": 70,
                                          "_flags": {}}},
                  "alerts_hash": "x", "alert_events": [],
                  "last_success": "2026-10-17T12:00:00Z"}
            sim = {"t": datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)}

            def failer(c, s):
                sim["t"] += timedelta(hours=2)
                raise FetchError("timeout", "down")

            sent = []
            w = watcher.Watcher(cfg(), st, fetcher=failer, clock=lambda: sim["t"],
                                sleep=lambda s: None, say=sent.append, send=lambda t: None)
            t1 = w.poll()
            self.assertTrue(any(t.kind == "OUTAGE_START" for t in t1))
            t2 = w.poll()   # +2h -> 2h outage
            t3 = w.poll()   # +4h
            self.assertFalse(any(t.kind == "WORSTCASE" for t in t2 + t3))
            t4 = w.poll()   # +6h -> fires
            self.assertTrue(any(t.kind == "WORSTCASE" for t in t4))
            self.assertTrue(st["worstcase_sent_for_outage"])
            self.assertTrue(any("WORST-CASE" in s for s in sent))
            # does not fire twice
            t5 = w.poll()
            self.assertFalse(any(t.kind == "WORSTCASE" for t in t5))

    def test_reconnect_after_outage(self):
        with temp_home():
            write_config()
            st = {"storms": {}, "alerts_hash": "", "alert_events": [],
                  "outage_started": "2026-10-17T10:00:00Z", "outage_kind": "timeout",
                  "consecutive_failures": 3,
                  "last_success": "2026-10-17T09:00:00Z"}
            sim = {"t": datetime(2026, 10, 17, 16, 0, tzinfo=timezone.utc)}
            fetcher = lambda c, s: {"storms": [], "alerts": [], "alerts_note": ""}
            sent = []
            w = watcher.Watcher(cfg(), st, fetcher=fetcher, clock=lambda: sim["t"],
                                sleep=lambda s: None, say=sent.append, send=lambda t: None)
            trig = w.poll()
            self.assertTrue(any(t.kind == "RECONNECTED" for t in trig))
            self.assertTrue(any("6.0 h" in s for s in sent))
            self.assertIsNone(st["outage_started"])

    def test_provider_alerts_used_when_configured(self):
        with temp_home():
            write_config()
            from . import sample_alert
            fetcher = lambda c, s: {"storms": [], "alerts": [sample_alert()],
                                    "alerts_note": "cap provider: down"}
            st = {"storms": {}, "alerts_hash": "", "alert_events": []}
            w = watcher.Watcher(cfg(alert_providers=[{"type": "nws"}]), st,
                                fetcher=fetcher, clock=lambda: datetime.now(timezone.utc),
                                sleep=lambda s: None, say=lambda t: None, send=lambda t: None)
            w.poll()
            self.assertEqual(st["alert_events"], ["Hurricane Warning"])


class TestCadence(unittest.TestCase):
    def _interval(self, st):
        return watcher.Watcher(cfg(), st)._current_interval()

    def test_base(self):
        self.assertEqual(self._interval({"storms": {}, "alert_events": []}), 120 * 60)

    def test_tight_when_alerts(self):
        self.assertEqual(self._interval({"storms": {}, "alert_events": ["X"]}), 30 * 60)

    def test_tight_when_storm_near(self):
        self.assertEqual(self._interval({"storms": {"a": {"dist_nm": 100}},
                                         "alert_events": []}), 30 * 60)

    def test_outage_poll_fastest(self):
        self.assertEqual(self._interval({"storms": {"a": {"dist_nm": 100}},
                                         "alert_events": ["X"],
                                         "outage_started": "2026-10-17T10:00:00Z"}), 10 * 60)


if __name__ == "__main__":
    unittest.main()
