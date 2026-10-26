"""Offline replay: fixture integrity, state isolation, sim clock."""

import os
import unittest
from pathlib import Path

from .. import config, replay, state


class TestFixture(unittest.TestCase):
    def test_fixture_loads_and_shapes(self):
        fx = replay.load_fixture()
        self.assertIn("entries", fx)
        self.assertIn("user", fx)
        for e in fx["entries"]:
            self.assertIn("advance_minutes", e)
            self.assertTrue(e.get("fail") or "storm" in e)
        fails = [e for e in fx["entries"] if e.get("fail")]
        self.assertEqual(len(fails), 4)          # 4 failed cycles = 6 sim hours

    def test_replay_config_carries_user(self):
        cfg, sim = replay.replay_config()
        self.assertIsNotNone(cfg["lat"])
        self.assertTrue(sim["t"].year == 2026)


class TestReplayAll(unittest.TestCase):
    def test_home_isolation(self):
        # whatever BATTON_HOME is, running the replay must not write storm
        # state into it (it uses a throwaway dir internally)
        home_before = os.environ.get("BATTON_HOME")
        cfg, st = replay.replay_all(max_cycles=6)
        if home_before is None:
            self.assertIsNone(os.environ.get("BATTON_HOME"))
        else:
            self.assertEqual(os.environ.get("BATTON_HOME"), home_before)
        self.assertIn("_sim_now_iso", st)
        self.assertIn("_log_tail", st)

    def test_mid_replay_has_active_storm(self):
        cfg, st = replay.replay_all(max_cycles=6)
        storms = st.get("storms") or {}
        self.assertEqual(len(storms), 1)
        only = next(iter(storms.values()))
        self.assertEqual(only["name"], "Trudy")
        self.assertEqual(only["advisory"], "20")
        self.assertIsNotNone(storms["ep212026"]["dist_nm"])

    def test_outage_state_at_cycle_10(self):
        cfg, st = replay.replay_all(max_cycles=10)
        self.assertIsNotNone(st.get("outage_started"))
        now = state.parse_iso(st["_sim_now_iso"])
        secs = state.outage_seconds(st, now=now)
        self.assertAlmostEqual(secs, 6 * 3600, delta=60)
        self.assertTrue(st.get("worstcase_sent_for_outage"))

    def test_end_of_replay_storm_gone(self):
        cfg, st = replay.replay_all()
        self.assertEqual(st.get("storms"), {})
        self.assertIn("Trudy", st.get("_log_tail", ""))

    def test_sim_clock_advances(self):
        cfg, st = replay.replay_all(max_cycles=3)
        t0 = state.parse_iso(replay.load_fixture()["clock_start"])
        t3 = state.parse_iso(st["_sim_now_iso"])
        self.assertGreater(t3, t0)


if __name__ == "__main__":
    unittest.main()
