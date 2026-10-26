"""NWS alerts provider (US states + territories, api.weather.gov).

Moved out of noaa.py when alerts became pluggable; behavior is unchanged.
Raises FetchError("unsupported", ...) for points outside coverage — that is
a coverage limit, NOT an outage, and callers depend on the distinction.
"""

from __future__ import annotations

from ..errors import FetchError
from . import register, AlertProvider

NWS_API = "https://api.weather.gov"


@register("nws")
class NWSProvider(AlertProvider):
    name = "nws"

    def _point(self) -> tuple[float, float]:
        lat = self.spec.get("lat")
        lon = self.spec.get("lon")
        if lat is None or lon is None:
            raise FetchError("parse", "nws provider needs lat/lon (set them via `batten init`)")
        return float(lat), float(lon)

    def fetch(self, cfg: dict) -> list[dict]:
        from .. import noaa  # lazy: avoids circular import at module load

        lat, lon = self._point()
        url = f"{NWS_API}/alerts/active?point={lat:.4f},{lon:.4f}"
        try:
            data = noaa.http_get_json(url, cfg)
        except FetchError as exc:
            if exc.status == 400 and "out of bounds" in exc.detail.lower():
                raise FetchError(
                    "unsupported",
                    "NWS alerts do not cover this location (US + territories only)",
                    400,
                ) from exc
            raise
        events = []
        for feat in data.get("features", []):
            props = feat.get("properties", {})
            events.append({
                "id": props.get("id", ""),
                "event": props.get("event", ""),
                "severity": props.get("severity", ""),
                "headline": props.get("headline", ""),
                "area": props.get("areaDesc", ""),
                "sent": props.get("sent", ""),
                "ends": props.get("ends") or props.get("effective") or "",
                "description": (props.get("description") or "")[:1500],
                "instruction": (props.get("instruction") or "")[:800],
            })
        return events
