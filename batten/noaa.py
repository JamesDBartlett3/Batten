"""NOAA data clients: NHC (tropical cyclones, global tropics) and NWS
(alerts/forecasts, US + territories only).

Every network call goes through http_get(), which classifies failures:
  dns      — cannot resolve the host (internet down / captive portal)
  timeout  — host unreachable or too slow (weak signal / congestion)
  http     — server answered with an error status
  parse    — response was not the shape we expected
  unsupported — the endpoint does not cover this location (NOT an outage)

Transport is injectable (see http_get) so the demo and self-tests can
replay fixtures or simulate an outage without touching the network.
"""

from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request

from . import __version__, USER_AGENT
from .errors import FetchError  # re-exported for backward compatibility

DEFAULT_TIMEOUT = 20


# --- transport --------------------------------------------------------------

def _urllib_get(url: str, timeout: int, headers: dict) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


# Tests and demo mode patch this.
http_get = _urllib_get


def _headers(cfg: dict | None, extra: dict | None = None) -> dict:
    contact = (cfg or {}).get("user_agent_contact", "").strip()
    ua = USER_AGENT + (f" contact:{contact}" if contact else "")
    h = {"User-Agent": ua}
    if extra:
        h.update(extra)
    return h


def http_get_json(url: str, cfg: dict | None = None, timeout: int = DEFAULT_TIMEOUT,
                  headers: dict | None = None):
    try:
        status, body = http_get(url, timeout, _headers(cfg, headers))
    except urllib.error.HTTPError as exc:
        detail = f"{url} -> HTTP {exc.code}"
        try:  # NWS embeds reasons like "out of bounds" in the response body
            detail += f": {exc.read(300).decode('utf-8', errors='replace').strip()}"
        except Exception:
            pass
        raise FetchError("http", detail, exc.code) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (socket.gaierror, UnicodeError)):
            raise FetchError("dns", f"cannot resolve host for {url}: {reason}") from exc
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise FetchError("timeout", f"connection timed out for {url}") from exc
        if isinstance(reason, ConnectionRefusedError):
            raise FetchError("timeout", f"connection refused for {url}") from exc
        raise FetchError("timeout", f"network error for {url}: {reason}") from exc
    except (socket.timeout, TimeoutError) as exc:
        raise FetchError("timeout", f"connection timed out for {url}") from exc
    try:
        return json.loads(body.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise FetchError("parse", f"non-JSON response from {url}: {exc}") from exc


def http_get_text(url: str, cfg: dict | None = None, timeout: int = DEFAULT_TIMEOUT) -> str:
    try:
        _, body = http_get(url, timeout, _headers(cfg))
    except urllib.error.HTTPError as exc:
        raise FetchError("http", f"{url} -> HTTP {exc.code}", exc.code) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        kind = "dns" if isinstance(reason, socket.gaierror) else "timeout"
        raise FetchError(kind, f"network error for {url}: {reason}") from exc
    except (socket.timeout, TimeoutError) as exc:
        raise FetchError("timeout", f"connection timed out for {url}") from exc
    return body.decode("utf-8", errors="replace")


# --- NHC: CurrentStorms.json ------------------------------------------------

NHC_CURRENT = "https://www.nhc.noaa.gov/CurrentStorms.json"


def parse_latlon(lat_s: str, lon_s: str) -> tuple[float, float]:
    """'15.0N', '104.7W' -> (15.0, -104.7)."""

    def one(s: str) -> float:
        m = re.match(r"^\s*([-\d.]+)\s*([NSEW])\s*$", s.upper())
        if not m:
            raise ValueError(f"bad coordinate {s!r}")
        val = float(m.group(1))
        return -val if m.group(2) in "SW" else val

    return one(lat_s), one(lon_s)


def normalize_storm(raw: dict) -> dict:
    """CurrentStorms entry -> flat snapshot used everywhere else."""
    kt = float(raw.get("intensity") or 0)
    lat = raw.get("latitudeNumeric")
    lon = raw.get("longitudeNumeric")
    if lat is None or lon is None:
        lat, lon = parse_latlon(raw.get("latitude", ""), raw.get("longitude", ""))
    adv = raw.get("publicAdvisory") or {}
    return {
        "id": raw.get("id", ""),
        "name": str(raw.get("name", "?")).title(),
        "bin": raw.get("binNumber", ""),
        "classification": raw.get("classification", ""),
        "kt": kt,
        "mph": int(round(kt * 1.15078)),
        "pressure": raw.get("pressure"),
        "lat": float(lat),
        "lon": float(lon),
        "movement_dir": raw.get("movementDir"),
        "movement_kt": raw.get("movementSpeed"),
        "advisory": str(adv.get("advNum") or "?"),
        "issued": adv.get("issuance") or "",
        "advisory_url": adv.get("url") or "",
        "discussion_url": (raw.get("forecastDiscussion") or {}).get("url", ""),
        "graphics_url": (raw.get("forecastGraphics") or {}).get("url", ""),
        "last_update": raw.get("lastUpdate") or "",
    }


def fetch_current_storms(cfg: dict | None = None) -> list[dict]:
    data = http_get_json(NHC_CURRENT, cfg)
    storms = data.get("activeStorms")
    if not isinstance(storms, list):
        raise FetchError("parse", "CurrentStorms.json missing activeStorms list")
    return [normalize_storm(s) for s in storms]


LANDFALL_RE = re.compile(r"MAKES? LANDFALL|MADE LANDFALL", re.IGNORECASE)
AREA_HINT_RE = re.compile(
    r"hurricane watch|storm surge watch|storm surge warning|tropical storm watch|"
    r"tropical storm warning|hurricane warning",
    re.IGNORECASE,
)


def fetch_advisory_text(storm: dict, cfg: dict | None = None) -> str:
    """Full public advisory text (plain text product)."""
    url = storm.get("advisory_url")
    if not url:
        return ""
    return http_get_text(url, cfg)


# --- NWS: alerts + forecast (US + territories only) --------------------------

NWS_API = "https://api.weather.gov"


def fetch_alerts(lat: float, lon: float, cfg: dict | None = None) -> list[dict]:
    """Active NWS alerts for a point — the legacy single-provider helper.

    Raises FetchError('unsupported') outside the US/territories — that is a
    coverage limit, not an outage. For user-configured multi-provider alert
    setups (CAP feeds, generic endpoints) use batten.providers.fetch_all.
    """
    from .providers.nws import NWSProvider

    return NWSProvider({"lat": lat, "lon": lon}).fetch(cfg or {})


def fetch_point_forecast(lat: float, lon: float, cfg: dict | None = None) -> dict | None:
    """7-day NWS forecast periods. None outside US coverage."""
    try:
        point = http_get_json(f"{NWS_API}/points/{lat:.4f},{lon:.4f}", cfg)
    except FetchError as exc:
        if exc.status in (400, 404):
            raise FetchError(
                "unsupported", "NWS forecast does not cover this location", exc.status
            ) from exc
        raise
    props = point.get("properties", {})
    forecast_url = props.get("forecast")
    if not forecast_url:
        return None
    fc = http_get_json(forecast_url, cfg)
    periods = [
        {
            "name": p.get("name", ""),
            "temp": f"{p.get('temperature', '?')}°{p.get('temperatureUnit', 'F')}",
            "wind": p.get("windSpeed", ""),
            "forecast": p.get("detailedForecast") or p.get("shortForecast", ""),
        }
        for p in (fc.get("properties", {}).get("periods") or [])[:6]
    ]
    return {"periods": periods, "updated": fc.get("properties", {}).get("updateTime", "")}


# --- NHC cone image (proven pattern: NHC 403s Discord/browser-less fetches
# on link previews, so download full-res yourself with a browser UA) ----------

def cone_image_url(storm: dict, cfg: dict | None = None) -> str | None:
    """Resolve the current full-resolution 5-day cone PNG for a storm.

    The graphics page lists the _sm variant; the full-res file is the same
    path with the _sm suffix dropped (pattern verified against NHC).
    """
    graphics = storm.get("graphics_url") or ""
    storm_id = storm.get("id", "")
    if not graphics or not storm_id:
        return None
    try:
        page = http_get_text(graphics, cfg)
    except FetchError:
        return None
    m = re.search(
        re.escape(storm_id.upper()) + r"_5day_cone_sm\+png/(\d+)_5day_cone(_sm)?\.png",
        page,
    )
    if not m:
        return None
    basin_dir = storm_id.upper()[:4]          # e.g. EP22 from ep222026
    path = (m.group(0)
            .replace("_5day_cone_sm+png", "_5day_cone+png")
            .replace("_sm.png", ".png"))
    return f"https://www.nhc.noaa.gov/storm_graphics/{basin_dir}/refresh/{path}"


def download_cone(url: str, dest_path, cfg: dict | None = None) -> bool:
    """Download the cone PNG with a browser UA (NHC blocks bot UAs)."""
    try:
        _, body = http_get(url, 30, {"User-Agent": "Mozilla/5.0 (batten cone fetch)"})
    except (FetchError, urllib.error.URLError):
        return False
    if not body:
        return False
    dest_path = str(dest_path)
    with open(dest_path, "wb") as fh:
        fh.write(body)
    return True


# --- geocoding (US ZIP convenience, cached) -----------------------------------

def zip_geocode(zip_code: str, cfg: dict | None = None) -> tuple[float, float, str] | None:
    """ZIP -> (lat, lon, 'City, ST') via zippopotam.us. None if unknown."""
    z = re.sub(r"\D", "", zip_code)
    if len(z) != 5:
        return None
    data = http_get_json(f"https://api.zippopotam.us/us/{z}", cfg)
    places = data.get("places") or []
    if not places:
        return None
    p = places[0]
    label = f"{p.get('place name', '?')}, {p.get('state abbreviation', '?')}"
    return float(p["latitude"]), float(p["longitude"]), label
