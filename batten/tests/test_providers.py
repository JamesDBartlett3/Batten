"""Alert providers: registry, NWS, CAP, generic JSON, aggregation."""

import io
import json
import unittest
import urllib.error

from . import respond, sample_alert, raise_fetch
from ..errors import FetchError
from .. import noaa, providers
from ..providers import cap as cap_mod
from ..providers import generic_json as gen_mod
from ..providers import nws as nws_mod

NWS_BODY = json.dumps({"features": [
    {"properties": {"id": "w1", "event": "Coastal Flood Warning", "severity": "Severe",
                    "headline": "Coastal Flood Warning issued", "areaDesc": "Coastal Miami",
                    "sent": "2026-10-25T07:43:00-04:00", "ends": "2026-10-27T20:00:00-04:00",
                    "description": "Water levels high.", "instruction": "Do not drive."}},
    {"properties": {"id": "w2", "event": "Rip Current Statement", "severity": "Moderate",
                    "headline": "", "areaDesc": "Beaches", "sent": "2026-10-25T07:43:00-04:00",
                    "ends": None, "description": "Rip currents.", "instruction": ""}},
]}).encode()

CAP_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:cap="urn:oasis:names:tc:emergency:cap:1.2">
  <title>Test Met Service</title>
  <entry>
    <id>cap-001</id>
    <title>Tropical Warning for Coast Region</title>
    <cap:event>Hurricane Warning</cap:event>
    <cap:severity>Severe</cap:severity>
    <cap:areaDesc>Coast Region, Bay District</cap:areaDesc>
    <cap:effective>2026-10-17T21:00:00Z</cap:effective>
    <cap:expires>2026-10-18T21:00:00Z</cap:expires>
    <cap:headline>Hurricane Warning issued for Coast Region</cap:headline>
    <cap:description>Dangerous winds expected.</cap:description>
    <cap:instruction>Complete preparations and shelter inland.</cap:instruction>
  </entry>
  <entry>
    <id>cap-002</id>
    <title>Heat advisory</title>
    <cap:event>Heat Advisory</cap:event>
    <cap:severity>Minor</cap:severity>
    <cap:areaDesc>Interior</cap:areaDesc>
    <cap:effective>2026-10-17T15:00:00Z</cap:effective>
  </entry>
  <entry>
    <id>cap-003</id>
    <title>No event tag — must be skipped</title>
    <cap:severity>Unknown</cap:severity>
  </entry>
</feed>"""

GENERIC_BODY = json.dumps({
    "meta": {"ok": True},
    "alerts": [
        {"guid": "g1", "type": {"name": "Flash Flood Warning"},
         "level": "Severe", "title": "Flash Flood Warning downtown",
         "region": {"description": "Downtown Sector"},
         "issued": "2026-10-17T18:00:00Z", "expires": "2026-10-18T00:00:00Z",
         "body": "Flash flooding from overloaded drains.", "action": "Move to high ground."},
        {"guid": "g2", "type": {"name": "All Clear"}},
    ],
}).encode()


def no_transport_reset(fn):
    """Decorator restoring noaa.http_get after a test."""
    def wrapper(self):
        old = noaa.http_get
        try:
            fn(self)
        finally:
            noaa.http_get = old
    return wrapper


class TestRegistry(unittest.TestCase):
    def test_builtins_registered(self):
        for name in ("nws", "cap", "generic"):
            self.assertIn(name, providers._REGISTRY)

    def test_unknown_type_raises_parse(self):
        with self.assertRaises(FetchError) as cm:
            providers.provider_for({"type": "carrier-pigeon"})
        self.assertEqual(cm.exception.kind, "parse")
        self.assertIn("known", cm.exception.detail)

    def test_case_insensitive(self):
        self.assertIs(providers.provider_for({"type": "CAP"}), cap_mod.CAPProvider)

    def test_default_is_single_nws(self):
        provs = providers.get_providers({"lat": 25.0, "lon": -80.0})
        self.assertEqual(len(provs), 1)
        self.assertIsInstance(provs[0], nws_mod.NWSProvider)
        self.assertEqual(provs[0].spec["lat"], 25.0)


class TestNWSProvider(unittest.TestCase):
    @no_transport_reset
    def test_happy_path(self):
        noaa.http_get = respond(NWS_BODY)
        alerts = nws_mod.NWSProvider({"lat": 25.76, "lon": -80.19}).fetch({})
        self.assertEqual(len(alerts), 2)
        self.assertEqual(alerts[0]["event"], "Coastal Flood Warning")
        self.assertEqual(alerts[0]["severity"], "Severe")
        self.assertEqual(alerts[1]["ends"], "")          # None -> ""

    @no_transport_reset
    def test_out_of_bounds_is_unsupported_not_http(self):
        def fake(url, timeout, headers):
            fp = io.BytesIO(b'{"detail": "Parameter \\"point\\" is invalid: out of bounds"}')
            err = urllib.error.HTTPError(url, 400, "Bad", {}, fp)
            err.read = fp.read
            raise err
        noaa.http_get = fake
        with self.assertRaises(FetchError) as cm:
            nws_mod.NWSProvider({"lat": 20.77, "lon": -105.31}).fetch({})
        self.assertEqual(cm.exception.kind, "unsupported")

    @no_transport_reset
    def test_missing_point_is_parse(self):
        with self.assertRaises(FetchError) as cm:
            nws_mod.NWSProvider({}).fetch({})
        self.assertEqual(cm.exception.kind, "parse")

    @no_transport_reset
    def test_test_method_reports(self):
        noaa.http_get = respond(NWS_BODY)
        ok, detail = nws_mod.NWSProvider({"lat": 25.7, "lon": -80.1}).test({})
        self.assertTrue(ok)
        self.assertIn("2 active", detail)


class TestCAPProvider(unittest.TestCase):
    def setUp(self):
        self.old = noaa.http_get
        noaa.http_get = respond(CAP_FEED.encode())

    def tearDown(self):
        noaa.http_get = self.old

    def test_parses_entries(self):
        alerts = cap_mod.CAPProvider({"url": "https://x/feed.atom"}).fetch({})
        self.assertEqual(len(alerts), 2)  # entry without cap:event skipped
        self.assertEqual(alerts[0]["event"], "Hurricane Warning")
        self.assertEqual(alerts[0]["severity"], "Severe")
        self.assertEqual(alerts[0]["area"], "Coast Region, Bay District")
        self.assertEqual(alerts[0]["sent"], "2026-10-17T21:00:00Z")
        self.assertEqual(alerts[0]["ends"], "2026-10-18T21:00:00Z")
        self.assertIn("shelter inland", alerts[0]["instruction"])
        self.assertEqual(alerts[0]["id"], "cap-001")

    def test_area_filter(self):
        alerts = cap_mod.CAPProvider({"url": "https://x/feed.atom",
                                      "area_filter": "interior"}).fetch({})
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["event"], "Heat Advisory")

    def test_area_filter_excludes_all_when_no_match(self):
        alerts = cap_mod.CAPProvider({"url": "https://x/feed.atom",
                                      "area_filter": "nowhere"}).fetch({})
        self.assertEqual(alerts, [])

    def test_malformed_xml_is_parse_error(self):
        noaa.http_get = respond(b"<feed><entry><unclosed>")
        with self.assertRaises(FetchError) as cm:
            cap_mod.CAPProvider({"url": "https://x/feed.atom"}).fetch({})
        self.assertEqual(cm.exception.kind, "parse")

    def test_missing_url(self):
        with self.assertRaises(FetchError) as cm:
            cap_mod.CAPProvider({}).fetch({})
        self.assertEqual(cm.exception.kind, "parse")
        self.assertIn("url", cm.exception.detail)


class TestGenericProvider(unittest.TestCase):
    def setUp(self):
        self.old = noaa.http_get
        noaa.http_get = respond(GENERIC_BODY)

    def tearDown(self):
        noaa.http_get = self.old

    def test_defaults_and_dotted_paths(self):
        alerts = gen_mod.GenericJSONProvider({"url": "https://x/api", "list_path": "alerts"}).fetch({})
        self.assertEqual(len(alerts), 2)
        self.assertEqual(alerts[0]["event"], "Flash Flood Warning")
        self.assertEqual(alerts[0]["area"], "Downtown Sector")
        self.assertEqual(alerts[0]["severity"], "Severe")
        self.assertEqual(alerts[0]["sent"], "2026-10-17T18:00:00Z")
        self.assertIn("high ground", alerts[0]["instruction"])
        self.assertEqual(alerts[0]["id"], "g1")

    def test_custom_fields_override_defaults(self):
        alerts = gen_mod.GenericJSONProvider({
            "url": "https://x/api", "list_path": "alerts",
            "fields": {"area": "region.description", "id": "guid"},
        }).fetch({})
        self.assertEqual(alerts[0]["area"], "Downtown Sector")

    def test_list_path_required_when_wrapped(self):
        with self.assertRaises(FetchError) as cm:
            gen_mod.GenericJSONProvider({"url": "https://x/api", "list_path": "wrong.path"}).fetch({})
        self.assertEqual(cm.exception.kind, "parse")

    def test_root_array_works(self):
        noaa.http_get = respond(json.dumps([{"id": 1, "event": "X"}]).encode())
        alerts = gen_mod.GenericJSONProvider({"url": "https://x/api"}).fetch({})
        self.assertEqual(alerts[0]["event"], "X")


class TestFetchAll(unittest.TestCase):
    def setUp(self):
        self.old = noaa.http_get

    def tearDown(self):
        noaa.http_get = self.old

    def test_merges_and_survives_failures(self):
        calls = {"n": 0}

        def fake(url, timeout, headers):
            calls["n"] += 1
            if "feed.atom" in url:
                return 200, CAP_FEED.encode()
            if url.startswith("https://api.weather.gov"):
                raise urllib.error.HTTPError(url, 500, "oops", {}, b"")
            return 200, GENERIC_BODY

        noaa.http_get = fake
        cfg = {"lat": 25.7, "lon": -80.1, "alert_providers": [
            {"type": "nws"}, {"type": "cap", "url": "https://x/feed.atom"},
            {"type": "generic", "url": "https://x/api", "list_path": "alerts"}]}
        alerts, notes = providers.fetch_all(cfg)
        self.assertEqual(len(alerts), 4)  # 2 NWS + 2 CAP + 2 generic - hmm see below
        self.assertTrue(any("nws provider: http" in n for n in notes))

    def test_dedupes_by_id(self):
        noaa.http_get = respond(NWS_BODY)
        cfg = {"lat": 25.7, "lon": -80.1,
               "alert_providers": [{"type": "nws"}, {"type": "nws"}]}
        alerts, notes = providers.fetch_all(cfg)
        self.assertEqual(len(alerts), 2)
        self.assertEqual(notes, [])

    def test_all_fail_notes_only(self):
        import socket
        import urllib.error
        noaa.http_get = raise_fetch(urllib.error.URLError(socket.gaierror("no dns")))
        alerts, notes = providers.fetch_all({"alert_providers": [
            {"type": "cap", "url": "https://x/feed.atom"}]})
        self.assertEqual(alerts, [])
        self.assertEqual(len(notes), 1)
        self.assertIn("cap provider", notes[0])
        self.assertIn("dns", notes[0])


if __name__ == "__main__":
    unittest.main()
