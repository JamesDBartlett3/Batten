"""NOAA client: parsing, storm normalization, cone URL resolution, geocoding."""

import json
import unittest

from . import respond
from .. import noaa
from ..errors import FetchError

CURRENT_STORMS = json.dumps({"activeStorms": [{
    "id": "ep222026", "binNumber": "EP2", "name": "VANCE", "classification": "TS",
    "intensity": "55", "pressure": "995", "latitude": "15.0N", "longitude": "104.7W",
    "latitudeNumeric": 15.0, "longitudeNumeric": -104.7,
    "movementDir": 305, "movementSpeed": 7, "lastUpdate": "2026-10-26T03:00:00.000Z",
    "publicAdvisory": {"advNum": "019", "issuance": "2026-10-26T03:00:00.000Z",
                       "url": "https://www.nhc.noaa.gov/text/MIATCPEP2.shtml"},
    "forecastDiscussion": {"url": "https://www.nhc.noaa.gov/text/MIATCDEP2.shtml"},
    "forecastGraphics": {"url": "https://www.nhc.noaa.gov/graphics_ep2.shtml"},
}]}).encode()

GRAPHICS_PAGE = b"""<html>
<img src="/storm_graphics/EP22/refresh/EP222026_5day_cone_sm+png/260236_5day_cone_sm.png">
<img src="/storm_graphics/EP22/refresh/EP222026_3day_cone_sm+png/260236_3day_cone_sm.png">
</html>"""


class HttpGetContext:
    """Patch noaa.http_get for the block; map URL prefix -> body."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.old = noaa.http_get

    def __enter__(self):
        def fake(url, timeout, headers):
            for prefix, body in self.routes.items():
                if url.startswith(prefix):
                    return 200, body
            raise FetchError("http", f"no route for {url}", 404)
        noaa.http_get = fake
        return self

    def __exit__(self, *exc):
        noaa.http_get = self.old
        return False


class TestParsing(unittest.TestCase):
    def test_parse_latlon(self):
        self.assertEqual(noaa.parse_latlon("15.0N", "104.7W"), (15.0, -104.7))
        self.assertEqual(noaa.parse_latlon("33.9S", "151.2E"), (-33.9, 151.2))
        with self.assertRaises(ValueError):
            noaa.parse_latlon("nowhere", "0E")

    def test_normalize_storm_uses_numerics(self):
        raw = json.loads(CURRENT_STORMS)["activeStorms"][0]
        s = noaa.normalize_storm(raw)
        self.assertEqual(s["name"], "Vance")          # title-cased
        self.assertEqual(s["kt"], 55.0)
        self.assertEqual(s["mph"], 63)
        self.assertEqual((s["lat"], s["lon"]), (15.0, -104.7))
        self.assertEqual(s["advisory"], "019")
        self.assertIn("MIATCPEP2", s["advisory_url"])

    def test_normalize_storm_falls_back_to_strings(self):
        raw = {"name": "AL", "classification": "HU", "intensity": "64",
               "pressure": "980", "latitude": "20.5N", "longitude": "50.0W"}
        s = noaa.normalize_storm(raw)
        self.assertAlmostEqual(s["lat"], 20.5)
        self.assertAlmostEqual(s["lon"], -50.0)


class TestFetch(unittest.TestCase):
    def test_fetch_current_storms(self):
        with HttpGetContext({"https://www.nhc.noaa.gov": CURRENT_STORMS}):
            storms = noaa.fetch_current_storms({})
        self.assertEqual(len(storms), 1)
        self.assertEqual(storms[0]["id"], "ep222026")

    def test_fetch_current_storms_bad_shape(self):
        with HttpGetContext({"https://www.nhc.noaa.gov": b'{"nope": 1}'}):
            with self.assertRaises(FetchError) as cm:
                noaa.fetch_current_storms({})
        self.assertEqual(cm.exception.kind, "parse")

    def test_fetch_alerts_delegates_to_provider(self):
        alerts_body = json.dumps({"features": [{"properties": {
            "id": "x1", "event": "E", "severity": "Minor", "headline": "",
            "areaDesc": "A", "sent": "s", "ends": None,
            "description": "d", "instruction": "i"}}]}).encode()
        with HttpGetContext({"https://api.weather.gov": alerts_body}):
            alerts = noaa.fetch_alerts(25.0, -80.0, {})
        self.assertEqual(alerts[0]["id"], "x1")
        self.assertEqual(alerts[0]["ends"], "")

    def test_user_providers_take_over_via_fetch_all(self):
        # config providers present -> NHC/NWS URL never hit; cap feed served instead
        from .. import providers
        with HttpGetContext({"https://feeds.example": CAP_MINI}):
            cfg = {"alert_providers": [{"type": "cap", "url": "https://feeds.example/a.atom"}]}
            alerts, notes = providers.fetch_all(cfg)
        self.assertEqual(alerts[0]["event"], "Flood Warning")
        self.assertEqual(notes, [])

    def test_cone_url_fullres_transform(self):
        with HttpGetContext({"https://www.nhc.noaa.gov/graphics": GRAPHICS_PAGE}):
            storm = {"id": "ep222026", "graphics_url": "https://www.nhc.noaa.gov/graphics_ep2.shtml"}
            url = noaa.cone_image_url(storm, {})
        self.assertIsNotNone(url)
        self.assertIn("/EP22/refresh/EP222026_5day_cone+png/260236_5day_cone.png", url)
        self.assertNotIn("_sm", url)

    def test_cone_url_none_when_no_match(self):
        with HttpGetContext({"https://www.nhc.noaa.gov/graphics": b"<html/>"}):
            storm = {"id": "ep999999", "graphics_url": "https://www.nhc.noaa.gov/graphics_ep9.shtml"}
            self.assertIsNone(noaa.cone_image_url(storm, {}))


CAP_MINI = b"""<?xml version="1.0"?><feed xmlns="urn:x" xmlns:cap="urn:oasis:names:tc:emergency:cap:1.2">
<entry><id>c1</id><cap:event>Flood Warning</cap:event><cap:severity>Severe</cap:severity>
<cap:areaDesc>River Valley</cap:areaDesc><cap:effective>2026-10-17T10:00:00Z</cap:effective></entry>
</feed>"""


class TestZipGeocode(unittest.TestCase):
    def test_geocode(self):
        body = json.dumps({"places": [{"place name": "Houston",
                                       "state abbreviation": "TX",
                                       "latitude": "29.76", "longitude": "-95.37"}]}).encode()
        with HttpGetContext({"https://api.zippopotam.us": body}):
            got = noaa.zip_geocode("77002", {})
        self.assertEqual(got, (29.76, -95.37, "Houston, TX"))

    def test_rejects_bad_zip(self):
        self.assertIsNone(noaa.zip_geocode("abc", {}))


if __name__ == "__main__":
    unittest.main()
