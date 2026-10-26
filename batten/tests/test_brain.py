"""Brain: status snapshots, prompts, Ollama client (transport-faked)."""

import json
import unittest
import urllib.error

from . import sample_alert, sample_storm, temp_home, write_config
from .. import brain, state


def base_cfg(**over):
    cfg = {"place": "Testville", "lat": 25.76, "lon": -80.19, "household": 3,
           "timezone": "America/New_York", "units": "imperial",
           "ollama_url": "http://127.0.0.1:9", "model": "test-model"}
    cfg.update(over)
    return cfg


class TestStatusSnapshot(unittest.TestCase):
    def test_storm_line_with_relation_and_units(self):
        cfg = base_cfg()
        st = {}
        snap = brain.status_snapshot(cfg, st, [sample_storm()], [sample_alert()])
        self.assertIn("Test (Category 2 hurricane, 96 mph)", snap)
        self.assertIn("of Testville", snap)
        self.assertIn("Coastal County", snap)
        self.assertIn("NWS ALERTS for your location (1)", snap)

    def test_relation_phrase_present(self):
        storm = sample_storm(lat=20.0, lon=-80.3, movement_dir=45)   # NE heading, user NE-ish
        snap = brain.status_snapshot(base_cfg(), {}, [storm], [])
        self.assertRegex(snap.replace("\n", " "), r"(toward you|away from you|passing to your)")

    def test_metric_snapshot(self):
        snap = brain.status_snapshot(base_cfg(units="metric"), {}, [sample_storm()], [])
        self.assertIn("km/h", snap)
        self.assertNotIn(" mph", snap)

    def test_offline_snapshot(self):
        st = {"outage_started": "2026-10-17T00:00:00Z",
              "outage_kind": "timeout", "last_success": "2026-10-17T00:00:00Z"}
        snap = brain.status_snapshot(base_cfg(), st, [], [], now_iso="2026-10-17T06:30:00Z")
        self.assertIn("OFFLINE for 6.5 h", snap)
        self.assertIn("weak signal", snap)
        self.assertIn("2026-10-17T00:00:00Z", snap)

    def test_online_no_storms(self):
        snap = brain.status_snapshot(base_cfg(), {}, [], [])
        self.assertIn("NOAA CONNECTIVITY: online", snap)
        self.assertIn("ACTIVE NHC STORMS: none", snap)

    def test_supplies_note_counts_essentials(self):
        with temp_home():
            write_config()
            snap = brain.status_snapshot(base_cfg(), {}, [], [])
            self.assertIn("SUPPLIES:", snap)
            self.assertIn("essential items still missing", snap)


class TestSystemPrompt(unittest.TestCase):
    def test_includes_rules_snapshot_and_log(self):
        with temp_home():
            state.append_weather_log("test entry", ["hello"])
            prompt = brain.build_system_prompt(base_cfg(), {}, [sample_storm()],
                                               [sample_alert()])
            self.assertIn("Safety ordering", prompt)
            self.assertIn("STATUS SNAPSHOT", prompt)
            self.assertIn("test entry", prompt)
            self.assertIn('"place": "Testville"', prompt)


class TestOllamaBrain(unittest.TestCase):
    def test_available_when_down(self):
        b = brain.OllamaBrain("http://127.0.0.1:9", "x")
        ok, msg = b.available()
        self.assertFalse(ok)
        self.assertIn("not reachable", msg)

    def test_complete_parses_ndjson(self):
        b = brain.OllamaBrain("http://127.0.0.1:9", "m")

        class FakeResp:
            def __init__(self, lines):
                self.lines = lines

            def __iter__(self):
                return iter(self.lines)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        payload = [json.dumps({"message": {"content": "Hel"}, "done": False}),
                   json.dumps({"message": {"content": "lo"}, "done": True})]
        called = {}

        def fake_urlopen(req, timeout):
            called["body"] = json.loads(req.data.decode())
            return FakeResp([ln.encode() + b"\n" for ln in payload])

        old = brain.urllib.request.urlopen
        brain.urllib.request.urlopen = fake_urlopen
        try:
            self.assertEqual(b.complete([{"role": "user", "content": "hi"}]), "Hello")
        finally:
            brain.urllib.request.urlopen = old
        self.assertEqual(called["body"]["model"], "m")
        self.assertTrue(called["body"]["stream"])

    def test_model_status_env_override(self):
        import os
        old = os.environ.get("BATTON_NO_MODEL")
        os.environ["BATTON_NO_MODEL"] = "1"
        try:
            ok, msg = brain.model_status({})
            self.assertFalse(ok)
            self.assertIn("template", msg)
        finally:
            if old is None:
                os.environ.pop("BATTON_NO_MODEL", None)
            else:
                os.environ["BATTON_NO_MODEL"] = old


if __name__ == "__main__":
    unittest.main()
