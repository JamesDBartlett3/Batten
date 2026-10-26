"""Web layer: API functions with faked transports + real HTTP smoke test."""

import json
import os
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from . import respond, sample_storm, temp_home, write_config
from .. import noaa, web


def cfg(**over):
    base = {"place": "Testville", "lat": 25.76, "lon": -80.19, "household": 3,
            "timezone": "America/New_York", "units": "metric",
            "ollama_url": "http://127.0.0.1:9", "model": "m",
            "discord_webhook": ""}
    base.update(over)
    return base


class ApiUnitTest(unittest.TestCase):
    def setUp(self):
        web._CACHE.clear()

    def test_status_offline_uses_last_known(self):
        with temp_home():
            write_config()
            old = noaa.http_get

            def fake(url, timeout, headers):
                raise noaa.FetchError("timeout", "down")

            noaa.http_get = fake
            try:
                out = web.api_status(cfg())
            finally:
                noaa.http_get = old
            self.assertFalse(out["connectivity"]["online"])
            self.assertIn("unreachable", out["connectivity"]["detail"])
            self.assertIn("unreachable (timeout)", out["alerts_note"])

    def test_status_live_with_storms(self):
        with temp_home():
            write_config()
            body = json.dumps({"activeStorms": [{
                "id": "ep222026", "binNumber": "EP2", "name": "VANCE",
                "classification": "TS", "intensity": "55", "pressure": "995",
                "latitudeNumeric": 15.0, "longitudeNumeric": -104.7,
                "movementDir": 305, "movementSpeed": 7,
                "publicAdvisory": {"advNum": "19", "issuance": "", "url": ""},
            }]}).encode()
            old = noaa.http_get
            noaa.http_get = respond(body)
            try:
                out = web.api_status(cfg())
            finally:
                noaa.http_get = old
            self.assertTrue(out["connectivity"]["online"])
            self.assertEqual(out["storms"][0]["name"], "Vance")
            self.assertIn("km/h", out["storms"][0]["speed"])

    def test_supplies_post_cycle(self):
        with temp_home():
            write_config()
            out = web.api_supplies_post(cfg(), {"action": "have", "item": "water"})
            water = next(i for i in out["items"] if i["id"] == "water")
            self.assertEqual(water["status"], "have")
            out = web.api_supplies_post(cfg(), {"action": "need", "item": "water"})
            water = next(i for i in out["items"] if i["id"] == "water")
            self.assertEqual(water["status"], "need")

    def test_supplies_post_errors(self):
        with temp_home():
            write_config()
            out = web.api_supplies_post(cfg(), {"action": "have", "item": "unicorn"})
            self.assertIsInstance(out, tuple)
            self.assertEqual(out[1], 400)

    def test_worstcase_text(self):
        with temp_home():
            write_config()
            from .. import state as state_mod
            state_mod.save_state({"storms": {
                "x": {"name": "Zed", "kt": 80, "cat": "CAT1", "lat": 24.0, "lon": -80.0,
                      "pressure": 980, "movement_dir": 15, "movement_kt": 8,
                      "advisory": "9", "issued": "", "dist_nm": 60, "_flags": {}}},
                "last_success": "2026-10-17T12:00:00Z"})
            out = web.api_worstcase(cfg())
            self.assertIn("WORST-CASE", out["text"])
            self.assertIn("Zed", out["text"])

    def test_chat_without_model_is_clean_error(self):
        with temp_home():
            write_config()
            old = noaa.http_get
            noaa.http_get = respond(b'{"activeStorms": []}')
            try:
                out = web.api_chat(cfg(), {"message": "hi"})
            finally:
                noaa.http_get = old
            self.assertIn("error", out)


class TestLanUrls(unittest.TestCase):
    def test_returns_non_loopback_list(self):
        urls = web._lan_urls(8787)
        self.assertIsInstance(urls, list)
        for u in urls:
            self.assertTrue(u.startswith("http://"))
            self.assertNotIn("127.0.0.1", u)


class TestHttpSmoke(unittest.TestCase):
    """Full stack: real HTTP server on an ephemeral port."""

    def setUp(self):
        self._home = temp_home()
        self._home.__enter__()
        write_config()
        web._CACHE.clear()
        old_fetch = noaa.fetch_current_storms
        self._old_fetch = old_fetch
        noaa.fetch_current_storms = lambda cfg=None: [sample_storm()]
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.BattenHandler)
        web.BattenHandler.cfg = cfg()
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        noaa.fetch_current_storms = self._old_fetch
        self.httpd.shutdown()
        self.httpd.server_close()
        self._home.__exit__(None, None, None)

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
            return r.status, r.read()

    def test_index_served(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"batten", body)
        self.assertIn(b"/api/status", body)

    def test_status_endpoint(self):
        status, body = self.get("/api/status")
        data = json.loads(body)
        self.assertEqual(data["place"], "Testville")
        self.assertEqual(data["storms"][0]["name"], "Test")

    def test_unknown_route_404(self):
        try:
            self.get("/api/nope")
            self.fail("expected 404")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)

    def test_supplies_post_roundtrip(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/supplies",
            data=json.dumps({"action": "have", "item": "radio"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        radio = next(i for i in data["items"] if i["id"] == "radio")
        self.assertEqual(radio["status"], "have")


if __name__ == "__main__":
    unittest.main()
