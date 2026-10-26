"""Worst-case 48-hour outlook — the offline engine.

When NOAA cannot be reached for a while, this module does the best it can
with the LAST GOOD DATA: persistence extrapolation (storm keeps its last
heading and speed), deliberately pessimistic intensity and wind-field
assumptions, and uncertainty that grows with lead time.

Everything it produces is labeled ESTIMATE. It is a planning tool for
"how bad could this get while I can't see the data", not a forecast product.
The deterministic core needs no model and no network; the local LLM only
narrates the numbers.

Assumptions (documented, deliberately conservative):
  * Track   : straight-line persistence of last heading + forward speed.
  * Intensity: last known + 20 kt (~one Saffir-Simpson category; the NHC
               official average 48h intensity error is about one category).
               Capped at 150 kt / CAT5.
  * Track error (inflated NHC average-official-error climatology, nm):
               6h:20  12h:30  24h:50  36h:70  48h:90
  * Wind radii worst case: tropical-storm-force ~100 nm (120 nm if the
    storm is major), hurricane-force ~35 nm (50 nm if major).
  * Rainfall: slow movers (<10 kt) dump far more — rule-of-thumb bands below.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import geo
from .units import Units, get_units

TRACK_ERROR_NM = {6: 20, 12: 30, 24: 50, 36: 70, 48: 90}

HORIZON_H = 48


@dataclass
class WorstCase:
    generated_from: str            # timestamp of last good NOAA data
    outage_hours: float | None
    storm_name: str | None = None
    last_cat: str | None = None
    worst_cat: str | None = None
    last_kt: float | None = None
    worst_kt: float | None = None
    last_pos: tuple[float, float] | None = None
    user_lat: float | None = None
    user_lon: float | None = None
    track_deg: float | None = None       # last-known heading, deg (None if unknown)
    closest_nm: float | None = None
    closest_eta_h: float | None = None
    ts_arrival_h: float | None = None
    ts_radius_nm: float | None = None
    hurricane_arrival_h: float | None = None
    hurricane_radius_nm: float | None = None
    rain_low_in: float | None = None
    rain_high_in: float | None = None
    moving: bool = True
    notes: list[str] = field(default_factory=list)
    # per-lead-time worst positions with error radius, for the table
    timeline: list[dict] = field(default_factory=list)


def _worst_wind_radii(kt: float) -> tuple[float, float]:
    """(ts_radius_nm, hurricane_radius_nm) worst case."""
    if kt >= 96:  # major
        return 120.0, 50.0
    return 100.0, 35.0


def build_worst_case(storm: dict | None, user_lat: float, user_lon: float,
                     last_good_iso: str, outage_hours: float | None = None) -> WorstCase:
    if not storm:
        return _no_storm_case(last_good_iso, outage_hours)

    kt = float(storm.get("kt") or 0)
    worst_kt = min(150.0, kt + 20.0)
    dir_deg = storm.get("movement_dir")
    spd_kt = float(storm.get("movement_kt") or 0)
    lat, lon = storm["lat"], storm["lon"]

    wc = WorstCase(
        generated_from=last_good_iso,
        outage_hours=outage_hours,
        storm_name=storm["name"],
        last_cat=geo.saffir(kt),
        worst_cat=geo.saffir(worst_kt),
        last_kt=kt,
        worst_kt=worst_kt,
        last_pos=(lat, lon),
        user_lat=user_lat,
        user_lon=user_lon,
    )

    if dir_deg is None or spd_kt <= 0.5:
        wc.moving = False
        wc.notes.append(
            "Storm was nearly stationary or heading unknown at last fix — "
            "stationary systems park over one area and maximize rainfall; "
            "assume the worst for flooding."
        )
        dist = geo.haversine_nm(lat, lon, user_lat, user_lon)
        wc.closest_nm = dist
        wc.closest_eta_h = 0.0
        wc.ts_radius_nm, wc.hurricane_radius_nm = _worst_wind_radii(worst_kt)
        if dist <= wc.ts_radius_nm:
            wc.ts_arrival_h = 0.0
        if dist <= wc.hurricane_radius_nm:
            wc.hurricane_arrival_h = 0.0
        _rain_from_speed(wc, 0.0)
        return wc

    # Persistence extrapolation, hourly.
    wc.track_deg = float(dir_deg)
    ts_radius_nm, hurr_radius_nm = _worst_wind_radii(worst_kt)
    wc.ts_radius_nm = ts_radius_nm
    wc.hurricane_radius_nm = hurr_radius_nm
    best = None
    for h in range(0, HORIZON_H + 1):
        plat, plon = geo.destination(lat, lon, float(dir_deg), spd_kt * h)
        d = geo.haversine_nm(plat, plon, user_lat, user_lon)
        radius = TRACK_ERROR_NM[min(TRACK_ERROR_NM, key=lambda k: abs(k - h))]
        wc.timeline.append({
            "h": h, "lat": round(plat, 1), "lon": round(plon, 1),
            "dist_nm": round(d), "error_nm": radius,
        })
        if best is None or d < best[1]:
            best = (h, d)
        if wc.ts_arrival_h is None and d - radius <= ts_radius_nm:
            wc.ts_arrival_h = float(h)
        if wc.hurricane_arrival_h is None and d - radius <= hurr_radius_nm:
            wc.hurricane_arrival_h = float(h)

    wc.closest_eta_h, wc.closest_nm = float(best[0]), float(best[1])
    _rain_from_speed(wc, spd_kt)
    return wc


def _rain_from_speed(wc: WorstCase, spd_kt: float) -> None:
    if spd_kt < 10:
        wc.rain_low_in, wc.rain_high_in = 10.0, 20.0
    elif spd_kt < 15:
        wc.rain_low_in, wc.rain_high_in = 6.0, 12.0
    else:
        wc.rain_low_in, wc.rain_high_in = 4.0, 8.0


def _no_storm_case(last_good_iso: str, outage_hours: float | None) -> WorstCase:
    wc = WorstCase(generated_from=last_good_iso, outage_hours=outage_hours)
    wc.notes.append(
        "No tropical cyclone was active in the NHC basins at last contact. "
        "The worst case while offline is unforecast severe weather: "
        "thunderstorms, tornadoes, flash flooding, or a new storm forming "
        "undetected. New Atlantic/E-Pacific cyclones typically give 2-5 days "
        "of advisory warning once named."
    )
    return wc


# --- rendering ---------------------------------------------------------------

def _fmt_hours(h: float | None) -> str:
    if h is None:
        return "not within 48h"
    if h <= 0:
        return "NOW / already inside the zone"
    return f"~{h:.0f} h from last good data"


def _fmt_closest_eta(h: float | None) -> str:
    if h is None:
        return "not within 48h"
    if h <= 0:
        return "now — already as close as it gets per last-known track (moving away)"
    return f"~{h:.0f} h from last good data"


def _heading_line(wc: WorstCase) -> str | None:
    """Heading-relation sentence, or None when position/track is unknown."""
    if wc.user_lat is None or wc.user_lon is None or not wc.last_pos:
        return None
    ap = geo.approach({"lat": wc.last_pos[0], "lon": wc.last_pos[1],
                       "movement_dir": wc.track_deg}, wc.user_lat, wc.user_lon)
    if ap["relation"] == "toward":
        return "Per last-known track the storm is moving TOWARD your position."
    if ap["relation"] == "away":
        return "Per last-known track the storm is moving away from your position."
    if ap["relation"] == "passing":
        brg = geo.compass_point(geo.bearing_deg(wc.user_lat, wc.user_lon,
                                                wc.last_pos[0], wc.last_pos[1]))
        return f"Per last-known track the storm is passing to your {brg}."
    return None


def render_worst_case(wc: WorstCase, cfg: dict | None = None) -> str:
    u: Units = get_units(cfg)
    lines: list[str] = []
    hdr = "⚠️  WORST-CASE 48-HOUR OUTLOOK (ESTIMATE — generated offline)"
    if wc.outage_hours is not None:
        hdr += f" — NOAA unreachable for {wc.outage_hours:.1f} h"
    lines.append(hdr)
    lines.append(f"Based on last good NOAA data: {wc.generated_from}")

    if wc.storm_name:
        lines.append("")
        lines.append(
            f"**{wc.storm_name}** — last seen {geo.category_label(wc.last_cat)} "
            f"({u.speed(wc.last_kt)}) at "
            f"{wc.last_pos[0]:.1f}, {wc.last_pos[1]:.1f}."
        )
        heading = _heading_line(wc)
        if heading:
            lines.append(heading)
        lines.append(
            f"Worst case by +48h: **{geo.category_label(wc.worst_cat)} "
            f"({u.speed(wc.worst_kt)})** assuming one category of "
            f"intensification and no weakening (persistence)."
        )
        lines.append(f"Closest approach to you: {u.distance(wc.closest_nm)} "
                     f"({_fmt_closest_eta(wc.closest_eta_h)})")
        lines.append(f"Tropical-storm-force winds (~{u.distance(wc.ts_radius_nm)} field): "
                     f"{_fmt_hours(wc.ts_arrival_h)}")
        lines.append(f"Hurricane-force winds (~{u.distance(wc.hurricane_radius_nm)} field): "
                     f"{_fmt_hours(wc.hurricane_arrival_h)}")
        if wc.rain_low_in is not None and (wc.closest_nm or 0) <= 500:
            lines.append(f"Rainfall worst case: "
                         f"{u.rain_range(wc.rain_low_in, wc.rain_high_in)} — "
                         "flash flooding and landslides possible, worse if the storm stalls.")
        elif wc.closest_nm and wc.closest_nm > 500:
            lines.append(f"Per last-known track the center stays well offshore "
                         f"(>{u.distance(500)}) — direct wind impact unlikely, but "
                         "bands/swells/marine effects still reach the coast, and the "
                         "track may have changed while offline.")
        lines.append(f"Track uncertainty at +48h is roughly ±{u.distance(90)} "
                     "(estimate) — a small bend left or right changes everything.")
    else:
        lines.append("No storm in last data — see notes.")

    for note in wc.notes:
        lines.append(f"- {note}")

    lines.append("")
    lines.append("Act on the earliest arrival time above, not the peak. "
                 "Finish outdoor prep before tropical-storm-force winds arrive; "
                 "after that, shelter and stay off the roads.")
    lines.append("This is a persistence-based estimate from stale data — "
                 "treat as a floor for preparation, not a prediction. "
                 "Re-check NOAA the moment connectivity returns.")
    return "\n".join(lines)
