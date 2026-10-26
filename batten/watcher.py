"""The watcher: poll NOAA, remember everything, speak only when it matters.

Behavior inherited from the original storm watcher (all field-proven):
  * Persist a snapshot to the weather log for EVERY poll before any
    notification decision.
  * Quiet hours: full updates 07:00-23:00 local; approved triggers bypass.
  * Connectivity loss warns immediately, with the cause (DNS vs timeout vs
    HTTP) — "cannot reach NOAA" is actionable, "unknown error" is not.
  * After OUTAGE_WORSTCASE_HOURS (default 6) offline, fire the worst-case
    48-hour outlook built from last-good data, once per outage.
  * Adaptive cadence: 30 min when alerts are active or a storm is within
    notify radius; 10 min while offline (cheap poll to catch restoration).

Clock, sleep, fetcher, and notifier are injectable for the demo and tests.
"""

from __future__ import annotations

import hashlib
import time as _time
from datetime import datetime

from . import brain, config, geo, noaa, notify, providers, state, supplies, units, worstcase


# --- real fetcher -------------------------------------------------------------

def real_fetch(cfg: dict, st: dict) -> dict:
    """Fetch storms (primary connectivity signal) + alerts/forecast (US only).

    Raises noaa.FetchError only when the PRIMARY source fails. NWS coverage
    limits and secondary failures are reported as notes, not outages.
    """
    storms = noaa.fetch_current_storms(cfg)

    alerts = []
    alerts_note = ""
    forecast = None
    if cfg.get("alert_providers"):
        # User-configured providers (nws/cap/generic...) are authoritative.
        alerts, notes = providers.fetch_all(cfg)
        alerts_note = "; ".join(notes)
    elif cfg.get("lat") is not None and cfg.get("lon") is not None:
        try:
            alerts = noaa.fetch_alerts(cfg["lat"], cfg["lon"], cfg)
        except noaa.FetchError as exc:
            if exc.kind == "unsupported":
                alerts_note = "NWS alert coverage does not include this location (US only); " \
                              "relying on NHC products + local official channels."
            else:
                alerts_note = f"NWS alerts unreachable ({exc.kind}); NHC still reachable."
        if not alerts_note:
            try:
                forecast = noaa.fetch_point_forecast(cfg["lat"], cfg["lon"], cfg)
            except noaa.FetchError:
                forecast = None
    else:
        alerts_note = "run `batten init` to set your coordinates for alerts"
    return {"storms": storms, "alerts": alerts, "alerts_note": alerts_note,
            "forecast": forecast}


# --- triggers ------------------------------------------------------------------

class Trigger:
    def __init__(self, kind: str, text: str, loud: bool = True):
        self.kind = kind
        self.text = text
        self.loud = loud  # loud triggers bypass quiet hours and ring NTFY-style


def _storm_line(s: dict, cfg: dict) -> str:
    u = units.get_units(cfg)
    lat, lon = cfg.get("lat"), cfg.get("lon")
    rel = ""
    if lat is not None and lon is not None:
        d = geo.haversine_nm(s["lat"], s["lon"], lat, lon)
        b = geo.compass_point(geo.bearing_deg(lat, lon, s["lat"], s["lon"]))
        rel = f" — {u.distance(d)} {b} of {cfg.get('place') or 'you'}"
        ap = geo.approach(s, lat, lon)
        if ap["relation"] == "toward":
            rel += ", heading **toward you**"
        elif ap["relation"] == "away":
            rel += ", moving **away from you**"
        elif ap["relation"] == "passing":
            rel += f", passing to your {b.lower()}"
    return (f"**{s['name']}** ({geo.category_label(geo.saffir(s['kt']))}, {u.speed(s['kt'])}, "
            f"{s['pressure']} mb) at {s['lat']:.1f},{s['lon']:.1f}{rel}; "
            f"advisory #{s['advisory']}")


def diff_storms(prev: dict, current: list[dict], cfg: dict) -> list[Trigger]:
    """Compare last-known storms with the fresh poll; return triggers."""
    trig: list[Trigger] = []
    radius = float(cfg.get("notify_radius_nm") or 600)

    def dist_of(s):
        lat, lon = cfg.get("lat"), cfg.get("lon")
        if lat is None or lon is None:
            return float("inf")
        return geo.haversine_nm(s["lat"], s["lon"], lat, lon)

    for s in current:
        p = prev.get(s["id"])
        if p is None:
            d = dist_of(s)
            if d <= radius:
                trig.append(Trigger("NEW_STORM",
                                    f"🌪️ New storm on the board: {_storm_line(s, cfg)}"))
            continue
        old_cat = geo.saffir(float(p.get("kt") or 0))
        new_cat = geo.saffir(s["kt"])
        if new_cat != old_cat:
            verb = "intensified to" if s["kt"] > float(p.get("kt") or 0) else "weakened to"
            trig.append(Trigger("CATEGORY_CHANGE",
                                f"⚡ {s['name']} {verb} **{geo.category_label(new_cat)}** "
                                f"({units.get_units(cfg).speed(s['kt'])}). {_storm_line(s, cfg)}"))
        elif s["kt"] - float(p.get("kt") or 0) >= 10:
            trig.append(Trigger("RAPID_INTENSIFICATION",
                                f"⚡ {s['name']} winds up {s['kt'] - float(p.get('kt') or 0):.0f} kt "
                                f"since last check — rapid intensification trend. {_storm_line(s, cfg)}"))
        if str(p.get("advisory")) != str(s["advisory"]):
            trig.append(Trigger("ADVISORY_UPDATE",
                                f"📋 {s['name']} advisory #{p.get('advisory')} → #{s['advisory']}. "
                                f"{_storm_line(s, cfg)}"))
        # distance threshold crossings, once each
        d = dist_of(s)
        entry = p.setdefault("_flags", {})
        for limit, label, message in (
            (300, "prep_warned",
             "🧭 %s is now within 300 nm — finish outdoor prep and fuel/charge everything."),
            (150, "act_warned",
             "🚨 %s is within 150 nm — move to indoor sheltering; stay off roads once winds pick up."),
        ):
            if d <= limit and not entry.get(label):
                entry[label] = True
                trig.append(Trigger("PROXIMITY", message % s["name"]))

    for sid, p in prev.items():
        if sid not in {s["id"] for s in current} and sid in prev:
            flags = p.get("_flags") or {}
            if flags.get("prep_warned") or flags.get("act_warned") or \
                    float(p.get("dist_nm") or 0) <= radius:
                trig.append(Trigger("STORM_GONE",
                                    f"🥥 {p.get('name', sid)} is no longer on the NHC board. "
                                    f"Standing down; keep supplies current."))
    return trig


def diff_alerts(prev_hash: str, alerts: list[dict]) -> tuple[str, list[Trigger]]:
    h = hashlib.md5("\n".join(sorted(a["id"] for a in alerts)).encode()).hexdigest()[:10]
    if h == prev_hash:
        return h, []
    if not prev_hash:
        return h, []  # first observation of this install: don't cry wolf
    events = {a["id"]: a["event"] for a in alerts}
    return h, [Trigger("ALERTS_CHANGED",
                       f"⚠️ NWS alerts changed for your area: {len(alerts)} active.")]


# --- the watcher ----------------------------------------------------------------

class Watcher:
    def __init__(self, cfg: dict, st: dict, fetcher=None, clock=None, sleep=None,
                 say=None, send=None, narrate=None, quiet_console=False):
        self.cfg = cfg
        self.st = st
        self.fetch = fetcher or (lambda c, s: real_fetch(c, s))
        self.clock = clock or state.utcnow
        self.sleep = sleep or _time.sleep
        self.say = say or notify.console                     # console updates
        self.send = send or (lambda text: notify.send_discord(
            cfg.get("discord_webhook", ""), text, quiet=quiet_console)
            if cfg.get("discord_webhook") else None)
        self.narrate = narrate                                # optional LLM narrator

    # -- notification helpers -------------------------------------------------

    def _emit(self, trig: Trigger, local_now: datetime):
        stamp = local_now.strftime("%m-%d %H:%M")
        text = f"{trig.text}\n_{stamp} local · source NHC/NWS · batten_"
        self.say(text)
        self.send(text)

    def _full_update(self, data: dict, local_now: datetime, note_extra: str = "") -> None:
        snap = brain.status_snapshot(
            self.cfg, self.st, data["storms"], data["alerts"],
            data.get("alerts_note", ""), now_iso=state.iso(self.clock()))
        stamp = local_now.strftime("%m-%d %H:%M")
        self.say(f"🥥 **Batten status** — {stamp} local\n" + snap + note_extra)
        self.send(f"🥥 **Batten status** — {stamp} local\n" + snap + note_extra)

    # -- one poll cycle ---------------------------------------------------------

    def poll(self, quiet_full: bool | None = None) -> list[Trigger]:
        cfg, st = self.cfg, self.st
        trig: list[Trigger] = []

        try:
            data = self.fetch(cfg, st)
        except noaa.FetchError as exc:
            now = self.clock()
            local_now = now.astimezone(config.tzinfo_for(cfg))
            info = state.record_poll_failure(st, exc.kind, exc.detail, now)
            state.append_weather_log(
                f"NOAA UNREACHABLE ({exc.kind})", [exc.detail], now)
            if info["went_down"]:
                trig.append(Trigger(
                    "OUTAGE_START",
                    f"⚠️ **Cannot reach NOAA — operating on last known data.**\n"
                    f"Cause: {_cause(exc.kind)}\n"
                    f"Last good data: {st.get('last_success') or 'never'}. Chat still works "
                    f"(local model); I'll keep retrying every "
                    f"{cfg.get('outage_poll_minutes')} min and build a worst-case "
                    f"outlook if this lasts {cfg.get('outage_worstcase_hours')} h.",
                    loud=True))
            outage = info.get("outage_seconds") or 0.0
            if outage >= float(cfg.get("outage_worstcase_hours") or 6) * 3600 \
                    and not st.get("worstcase_sent_for_outage"):
                trig.append(self._fire_worst_case(now))
            for t in trig:
                self._emit(t, local_now)
            return trig

        now = self.clock()
        local_now = now.astimezone(config.tzinfo_for(cfg))
        info = state.record_poll_success(st, now)
        if info["came_back"]:
            last = st.get("last_outage") or {}
            started = state.parse_iso(last.get("started") or "")
            hours = (now - started).total_seconds() / 3600 if started else 0.0
            trig.append(Trigger("RECONNECTED",
                                f"✅ NOAA is back after {hours:.1f} h offline. "
                                f"Refreshing from live data — worst-case mode off."))

        prev = st.get("storms") or {}
        trig += diff_storms(prev, data["storms"], cfg)
        # landfall check when an advisory number advances (grep the text product)
        for s in data["storms"]:
            p = prev.get(s["id"])
            if p and str(p.get("advisory")) != str(s["advisory"]) and s.get("advisory_url"):
                try:
                    txt = noaa.fetch_advisory_text(s, cfg)
                except noaa.FetchError:
                    txt = ""
                if txt and noaa.LANDFALL_RE.search(txt):
                    trig.append(Trigger(
                        "LANDFALL",
                        f"🚨 NHC advisory #{s['advisory']} reports {s['name']} HAS MADE "
                        f"LANDFALL. Stay sheltered — worst winds/rain come with and after "
                        f"closest approach; conditions can quiet deceptively in the eye."))
        new_hash, alert_trigs = diff_alerts(st.get("alerts_hash", ""), data["alerts"])
        st["alerts_hash"] = new_hash
        st["alert_events"] = [a["event"] for a in data["alerts"]]
        trig += alert_trigs

        st["storms"] = {
            s["id"]: _snapshot_storm(s, cfg,
                                     (prev.get(s["id"]) or {}).get("_flags") or {})
            for s in data["storms"]}
        state.save_state(st)

        state.append_weather_log(
            f"{len(data['storms'])} active storm(s)" if data["storms"] else "no active storms",
            [_storm_line(s, cfg) for s in data["storms"]] or ["NHC board clear."],
            now)

        for t in trig:
            self._emit(t, local_now)

        hour = local_now.hour
        if quiet_full is None:
            quiet_full = hour < 7 or hour >= 23
        loud_trig = any(t.loud for t in trig)
        if not quiet_full or loud_trig:
            self._full_update(data, local_now)
        return trig

    def _fire_worst_case(self, now) -> Trigger:
        cfg, st = self.cfg, self.st
        st["worstcase_sent_for_outage"] = True
        state.save_state(st)
        outage = (state.outage_seconds(st, now) or 0) / 3600
        nearest = _nearest_relevant(st, cfg)
        wc = worstcase.build_worst_case(
            nearest, cfg["lat"], cfg["lon"],
            st.get("last_success") or "unknown",
            outage_hours=outage)
        text = worstcase.render_worst_case(wc)
        if self.narrate:
            para = self.narrate(
                "Write a short, calm action plan for this household based ONLY on the "
                "worst-case outlook below. 6 bullets max: what to finish NOW, what to "
                "move indoors, water/food/power priorities, and when to shelter. "
                "No speculation beyond the data.\n\n" + text)
            if para:
                text += "\n\n**Plan (local model):**\n" + para
        state.append_weather_log("WORST-CASE OUTLOOK FIRED (offline > threshold)",
                                 ["see posted outlook"], now)
        return Trigger("WORSTCASE", text, loud=True)

    # -- main loop ---------------------------------------------------------------

    def run(self, once: bool = False, max_cycles: int | None = None) -> None:
        cfg = self.cfg
        cycles = 0
        while True:
            self.poll()
            cycles += 1
            if once or (max_cycles and cycles >= max_cycles):
                break
            self.sleep(self._current_interval())

    def _current_interval(self) -> float:
        cfg, st = self.cfg, self.st
        if state.outage_seconds(st) is not None:
            return float(cfg.get("outage_poll_minutes") or 10) * 60
        if _alerts_active(st) or _storm_within(st, float(cfg.get("notify_radius_nm") or 600)):
            return float(cfg.get("tight_poll_minutes") or 30) * 60
        return float(cfg.get("poll_minutes") or 120) * 60


# --- helpers -------------------------------------------------------------------

def _cause(kind: str) -> str:
    return {
        "dns": "DNS resolution failing — internet is down or a captive portal took over",
        "timeout": "connections timing out — weak signal, congestion, or ISP outage",
        "http": f"NOAA servers answering with errors",
        "parse": "NOAA responses arrived unreadable",
    }.get(kind, kind)


def _snapshot_storm(s: dict, cfg: dict, prev_flags: dict | None = None) -> dict:
    lat, lon = cfg.get("lat"), cfg.get("lon")
    dist = None
    if lat is not None and lon is not None:
        dist = geo.haversine_nm(s["lat"], s["lon"], lat, lon)
    return {
        "name": s["name"], "kt": s["kt"], "cat": geo.saffir(s["kt"]),
        "lat": s["lat"], "lon": s["lon"], "pressure": s["pressure"],
        "movement_dir": s.get("movement_dir"), "movement_kt": s.get("movement_kt"),
        "advisory": s["advisory"], "issued": s.get("issued", ""),
        "dist_nm": dist, "_flags": dict(prev_flags or {}),
    }


def _nearest_relevant(st: dict, cfg: dict) -> dict | None:
    storms = st.get("storms") or {}
    if not storms:
        return None
    lat, lon = cfg.get("lat"), cfg.get("lon")
    if lat is None or lon is None:
        return next(iter(storms.values()))
    return min(storms.values(),
               key=lambda s: s.get("dist_nm") if s.get("dist_nm") is not None else float("inf"))


def _alerts_active(st: dict) -> bool:
    return bool(st.get("alert_events"))


def _storm_within(st: dict, radius_nm: float) -> bool:
    for s in (st.get("storms") or {}).values():
        d = s.get("dist_nm")
        if d is not None and d <= radius_nm:
            return True
    return False
