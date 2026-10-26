"""Geodesy and meteorology helpers. Pure functions, no I/O.

Distances are nautical miles (NHC units), speeds in knots unless noted.
"""

from __future__ import annotations

import math

EARTH_RADIUS_NM = 3440.065
KT_TO_MPH = 1.15078
KT_TO_KMH = 1.852

COMPASS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]


def compass_point(deg: float) -> str:
    """345 degrees -> 'NNW'."""
    return COMPASS[int(((deg % 360) + 360 / 32) // (360 / 16)) % 16]


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_NM * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2, degrees clockwise from north."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlmb = math.radians(lon2 - lon1)
    x = math.sin(dlmb) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlmb)
    return math.degrees(math.atan2(x, y)) % 360


def approach(storm: dict, user_lat: float, user_lon: float) -> dict:
    """Where is the storm headed relative to the user?

    Returns {bearing_storm_to_user_deg, track_deg, angle_diff_deg, relation}
    where relation is "toward" (angle between the storm's track and the
    storm->user bearing <= 45), "away" (>= 135), "passing" otherwise — or
    "unknown" when the storm's movement direction is missing.
    """
    bearing = bearing_deg(storm["lat"], storm["lon"], user_lat, user_lon)
    track = storm.get("movement_dir")
    if track is None:
        return {"bearing_storm_to_user_deg": bearing, "track_deg": None,
                "angle_diff_deg": None, "relation": "unknown"}
    track = float(track) % 360
    diff = abs(bearing - track) % 360
    if diff > 180:
        diff = 360 - diff
    if diff <= 45:
        relation = "toward"
    elif diff >= 135:
        relation = "away"
    else:
        relation = "passing"
    return {"bearing_storm_to_user_deg": bearing, "track_deg": track,
            "angle_diff_deg": diff, "relation": relation}


def destination(lat: float, lon: float, bearing: float, dist_nm: float) -> tuple[float, float]:
    """Move dist_nm from (lat, lon) along bearing. Returns (lat, lon)."""
    delta = dist_nm / EARTH_RADIUS_NM
    theta = math.radians(bearing)
    phi1 = math.radians(lat)
    lmb1 = math.radians(lon)
    phi2 = math.asin(math.sin(phi1) * math.cos(delta) + math.cos(phi1) * math.sin(delta) * math.cos(theta))
    lmb2 = lmb1 + math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(phi1),
        math.cos(delta) - math.sin(phi1) * math.sin(phi2),
    )
    return math.degrees(phi2), (math.degrees(lmb2) + 540) % 360 - 180


def saffir(kt: float) -> str:
    """Saffir-Simpson category from 1-minute sustained winds in knots.

    Returns 'TD', 'TS', 'CAT1'..'CAT5'. 'HU' prefixes in NHC classification
    field; we normalize to category labels so state diffs are stable.
    """
    if kt >= 137:
        return "CAT5"
    if kt >= 113:
        return "CAT4"
    if kt >= 96:
        return "CAT3"
    if kt >= 83:
        return "CAT2"
    if kt >= 64:
        return "CAT1"
    if kt >= 34:
        return "TS"
    return "TD"


CATEGORY_LABEL = {
    "TD": "Tropical Depression",
    "TS": "Tropical Storm",
    "CAT1": "Category 1 hurricane",
    "CAT2": "Category 2 hurricane",
    "CAT3": "Major Category 3 hurricane",
    "CAT4": "Major Category 4 hurricane",
    "CAT5": "Major Category 5 hurricane",
}


def category_label(cat: str) -> str:
    return CATEGORY_LABEL.get(cat, cat)


def kt_to_mph(kt: float) -> int:
    return int(round(kt * KT_TO_MPH))


def fmt_distance(nm: float) -> str:
    km = nm * 1.852
    if nm < 100:
        return f"{nm:.0f} nm ({km:.0f} km)"
    return f"{nm:,.0f} nm ({km:,.0f} km)"
