"""Full-screen terminal dashboard — PC/Mac/Linux, stdlib only.

Two layers:
  * Pure frame builders (dashboard_lines, alerts_lines, supplies_lines, ...)
    that turn a plain data dict into colored text — unit-tested without a
    terminal, and reused by `--headless` to render screenshot frames.
  * A minimal ANSI screen + key reader (alternate buffer, raw input via
    termios on POSIX / msvcrt on Windows) driving an interactive loop.

Keys: r poll now · w worst-case · a alerts · p supplies · l log · h help · q quit
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from collections import deque

from . import brain, config, geo, noaa, replay, state, supplies, units, watcher, worstcase

# --- tiny ANSI helpers (basic SGR only — keeps screenshots trivial) -----------

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
FG = {"black": 30, "red": 31, "green": 32, "yellow": 33,
      "blue": 34, "magenta": 35, "cyan": 36, "white": 37}


def paint(text: str, color: str | None = None, bold: bool = False) -> str:
    out = ""
    if bold:
        out += BOLD
    if color:
        out += f"\x1b[{FG[color]}m"
    return out + text + RESET if out else text


def strip_ansi(text: str) -> str:
    import re
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


# --- data gathering ------------------------------------------------------------

def gather(cfg: dict, st: dict, source: str = "live") -> dict:
    """Build the render-ready data dict (shared by interactive + headless)."""
    u = units.get_units(cfg)
    lat, lon = cfg.get("lat"), cfg.get("lon")

    online, cause, storms, alerts, alerts_note = True, None, [], [], ""
    if source == "demo":
        try:
            storms = [s for s in (st.get("storms") or {}).values()]
            alerts_note = "demo replay (no NWS)"
        except Exception:
            storms = []
    else:
        try:
            data = watcher.real_fetch(cfg, st)
            storms, alerts, alerts_note = data["storms"], data["alerts"], data["alerts_note"]
        except noaa.FetchError as exc:
            storms = [s for s in (st.get("storms") or {}).values()]
            online, cause = False, exc

    storm_rows = []
    for s in storms:
        row = {
            "name": s["name"],
            "cat": geo.category_label(geo.saffir(s["kt"])),
            "speed": u.speed(s["kt"]),
            "pressure": s.get("pressure"),
            "movement": "",
            "dist_bearing": "",
            "relation": "",
            "relation_kind": "",
            "advisory": s.get("advisory", "?"),
        }
        if s.get("movement_dir") is not None:
            row["movement"] = (f"moving {geo.compass_point(s['movement_dir'])} "
                               f"at {u.speed(s['movement_kt'])}")
        if lat is not None and lon is not None:
            d = geo.haversine_nm(s["lat"], s["lon"], lat, lon)
            b = geo.compass_point(geo.bearing_deg(lat, lon, s["lat"], s["lon"]))
            row["dist_bearing"] = f"{u.distance(d)} {b} of {cfg.get('place') or 'you'}"
            ap = geo.approach(s, lat, lon)
            row["relation_kind"] = ap["relation"]
            row["relation"] = {
                "toward": "HEADING TOWARD YOU",
                "away": "moving away from you",
                "passing": f"passing to your {b.lower()}",
            }.get(ap["relation"], "")
        storm_rows.append(row)
    storm_rows.sort(key=lambda r: r["dist_bearing"] == "")  # closest (known) first

    tz = config.tzinfo_for(cfg)
    # Replay frames carry a sim clock; use it for outage/local-time math.
    sim_now = state.parse_iso(st.get("_sim_now_iso") or "")
    now = sim_now or state.utcnow()
    outage_secs = state.outage_seconds(st, now=now)
    if outage_secs is not None:
        conn = {"online": False,
                "detail": f"OFFLINE {outage_secs / 3600:.1f} h ({st.get('outage_kind')}) — "
                          f"last good {st.get('last_success')}"}
    elif online:
        conn = {"online": True, "detail": f"online · last good {st.get('last_success') or 'now'}"}
    else:
        conn = {"online": False, "detail": f"unreachable ({cause.kind}) — showing last-known"}

    model_ok, model_msg = brain.model_status(cfg)

    sup = supplies.load_supplies()
    sup_text, have_n, missing_n = supplies.render_supplies(
        int(cfg.get("household") or 2), sup, cfg)
    missing_ess = supplies.missing_essentials(int(cfg.get("household") or 2), sup)

    return {
        "cfg": cfg,
        "place": cfg.get("place") or "unset",
        "coords": f"{lat}, {lon}" if lat is not None else "not configured",
        "units_pref": u.pref,
        "local_time": now.astimezone(tz).strftime("%Y-%m-%d %H:%M"),
        "connectivity": conn,
        "model": {"ok": model_ok, "detail": model_msg},
        "storms": storm_rows,
        "alerts": alerts,
        "alerts_note": alerts_note,
        "supplies_text": sup_text,
        "supplies_have": have_n,
        "supplies_missing": missing_n,
        "supplies_missing_essential": len(missing_ess),
        "source": source,
    }


# --- pure frame builders ---------------------------------------------------------

def _clip(lines: list[str], width: int) -> list[str]:
    out = []
    for ln in lines:
        visible = 0
        cut = len(ln)
        i = 0
        while i < len(ln):
            if ln[i] == "\x1b":
                j = ln.find("m", i)
                i = (j + 1) if j != -1 else len(ln)
                continue
            if visible >= width:
                cut = i
                break
            visible += 1
            i += 1
        out.append(ln[:cut] + RESET + "\x1b[K")
    return out


def dashboard_lines(d: dict, width: int) -> list[str]:
    lines = []
    title = f" 🥥 BATTEN — {d['place']} ({d['coords']}) "
    lines.append(paint(title, "cyan", bold=True))
    conn = d["connectivity"]
    mark = paint("●", "green") if conn["online"] else paint("●", "red", bold=True)
    lines.append(f" NOAA {mark} {conn['detail']}")
    model = d["model"]
    mm = paint("●", "green" if model["ok"] else "red")
    lines.append(f" model {mm} {model['detail']}  ·  units: {d['units_pref']}  ·  {d['local_time']}")

    lines.append("")
    lines.append(paint(f" STORMS ({len(d['storms'])})", "cyan", bold=True))
    if not d["storms"]:
        lines.append("  No active storms at last good poll.")
    for s in d["storms"]:
        rel_color = {"toward": "red", "away": "green", "passing": "yellow"}.get(s["relation_kind"])
        head = f" ▸ {paint(s['name'], bold=True)} — {s['cat']} ({s['speed']})"
        if s["pressure"]:
            head += f" · {s['pressure']} mb"
        if s["movement"]:
            head += f" · {s['movement']}"
        lines.append(head)
        sub = f"     {s['dist_bearing']}" if s["dist_bearing"] else "     (set coordinates for range)"
        if s["relation"]:
            sub += " · " + paint(s["relation"], rel_color, bold=s["relation_kind"] == "toward")
        sub += f" · advisory #{s['advisory']}"
        lines.append(sub)

    lines.append("")
    n_alerts = len(d["alerts"])
    lines.append(paint(f" ALERTS ({n_alerts})" + ("  [a] details" if n_alerts else ""),
                       "cyan", bold=True))
    if d["alerts_note"]:
        lines.append(f"  {d['alerts_note']}")
    for a in d["alerts"][:4]:
        sev = a.get("severity", "")
        color = {"Extreme": "magenta", "Severe": "red", "Moderate": "yellow"}.get(sev, "white")
        lines.append(f"  • {paint(a['event'], color)} ({sev}) — {a['area'][:60]}")

    lines.append("")
    lines.append(paint(" SUPPLIES", "cyan", bold=True) +
                 f"  {d['supplies_have']} ready · {d['supplies_missing']} to get "
                 f"({d['supplies_missing_essential']} essential missing)   [p] list")
    return _clip(lines, width)


def overlay_lines(title: str, body: str, width: int) -> list[str]:
    lines = [paint(f" {title} ", "cyan", bold=True), ""]
    lines += [f" {ln}" for ln in body.splitlines()]
    return _clip(lines, width)


HELP_TEXT = """\
 keys
 ────
 r  poll NOAA now
 w  worst-case 48-h outlook (offline engine)
 a  alert details
 p  supply checklist
 l  weather log (recent)
 h  this help
 q  quit

 The watcher polls in the background on its normal cadence
 (2h base, 30 min when alerted, 10 min while offline).
"""


# --- screen + input ---------------------------------------------------------------

class Screen:
    def __init__(self, out=None):
        self.out = out or sys.stdout

    def enter(self):
        self.out.write("\x1b[?1049h\x1b[H\x1b[2J\x1b[?25l")
        self.out.flush()

    def leave(self):
        self.out.write("\x1b[0m\x1b[?25h\x1b[?1049l")
        self.out.flush()

    def draw(self, lines: list[str]):
        w, h = shutil.get_terminal_size((100, 30))
        lines = list(lines)[: h - 1]
        self.out.write("\x1b[H" + "\n".join(lines) + "\x1b[0m\x1b[J")
        self.out.flush()

    @staticmethod
    def size() -> tuple[int, int]:
        return shutil.get_terminal_size((100, 30))


class KeyReader:
    """Blocking-with-timeout single key reads on POSIX and Windows."""

    def __init__(self):
        self._windows = os.name == "nt"
        self._old = None
        if not self._windows:
            import termios
            import tty
            self._old = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())

    def read_key(self, timeout: float) -> str | None:
        if self._windows:
            import msvcrt
            end = time.time() + timeout
            while time.time() < end:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    return "\x1b" if ch in ("\x1b", "") else ch.lower()
                time.sleep(0.02)
            return None
        import select
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready:
            return None
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            return "\x1b"
        if ch == "\r":
            return "\n"
        return ch.lower()

    def close(self):
        if self._old is not None:
            import termios
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old)


# --- interactive loop ---------------------------------------------------------------

def run(cfg: dict) -> None:
    if not sys.stdin.isatty():
        raise SystemExit("batten tui needs a terminal — try `batten tui --headless DIR` "
                         "to render frames, or `batten serve` for the web UI.")
    st = state.load_state()
    events: deque[str] = deque(maxlen=6)
    w = watcher.Watcher(cfg, st, say=lambda text: events.append(text.splitlines()[0]))

    lock = threading.Lock()
    stop = threading.Event()
    latest: dict = {"data": gather(cfg, st)}

    def background():
        first = True
        while not stop.is_set():
            try:
                w.poll()
            except Exception as exc:  # never kill the UI thread over a poll
                events.append(f"poll error: {exc.__class__.__name__}")
            with lock:
                latest["data"] = gather(cfg, st)
            stop.wait(15 if first else 60)
            first = False

    thread = threading.Thread(target=background, daemon=True)
    thread.start()

    screen = Screen()
    keys = KeyReader()
    view = "dashboard"
    worst_text = ""
    screen.enter()
    try:
        while True:
            with lock:
                d = latest["data"]
            width, _ = screen.size()
            if view == "dashboard":
                lines = dashboard_lines(d, width)
                if events:
                    lines.append("")
                    lines.append(paint(" RECENT EVENTS", "cyan", bold=True))
                    for ev in list(events)[-3:]:
                        lines.append(f"  {ev[:width - 4]}")
            elif view == "alerts":
                body = "\n".join(
                    f"{a['event']} ({a['severity']}) — {a['area']}\n  {a['headline']}\n"
                    f"  sent {a['sent']} · ends {a['ends'] or 'n/a'}\n  {a['description']}\n"
                    for a in d["alerts"]) or (d["alerts_note"] or "No active alerts.")
                lines = overlay_lines("ALERTS  [any key] back", body, width)
            elif view == "supplies":
                lines = overlay_lines("SUPPLIES  [any key] back", d["supplies_text"], width)
            elif view == "worstcase":
                lines = overlay_lines("WORST-CASE 48-HOUR OUTLOOK  [any key] back",
                                      worst_text or "(computing...)", width)
            elif view == "log":
                lines = overlay_lines("WEATHER LOG (recent)  [any key] back",
                                      state.tail_weather_log(2500), width)
            else:
                lines = overlay_lines("HELP  [any key] back", HELP_TEXT, width)
            screen.draw(lines)

            key = keys.read_key(1.0)
            if key is None:
                continue
            if view == "dashboard":
                if key in ("q", "\x1b"):
                    break
                if key == "r":
                    threading.Thread(target=background_once, args=(w, cfg, st, latest, lock),
                                     daemon=True).start()
                elif key == "w":
                    worst_text = _compute_worst(cfg, st, d)
                    view = "worstcase"
                elif key == "a":
                    view = "alerts"
                elif key == "p":
                    view = "supplies"
                elif key == "l":
                    view = "log"
                elif key == "h" or key == "?":
                    view = "help"
            else:
                view = "dashboard"
    finally:
        stop.set()
        screen.leave()
        keys.close()


def background_once(w, cfg, st, latest, lock):
    try:
        w.poll()
    except Exception:
        pass
    with lock:
        latest["data"] = gather(cfg, st)


def _compute_worst(cfg: dict, st: dict, d: dict) -> str:
    storms = [s for s in (st.get("storms") or {}).values()]
    lat, lon = cfg.get("lat"), cfg.get("lon")
    if lat is None or lon is None:
        return "Set coordinates first: batten init --lat .. --lon .."
    nearest = None
    if storms:
        nearest = min(storms, key=lambda s: s.get("dist_nm") if s.get("dist_nm") is not None
                      else geo.haversine_nm(s["lat"], s["lon"], lat, lon))
    outage = (state.outage_seconds(
        st, now=state.parse_iso(st.get("_sim_now_iso") or "")) or 0) / 3600
    wc = worstcase.build_worst_case(nearest, lat, lon,
                                    st.get("last_success") or "no polls yet",
                                    outage_hours=outage)
    return worstcase.render_worst_case(wc, cfg)


# --- headless rendering (screenshots / tests) ---------------------------------------

def render_headless(cfg: dict, source: str = "live", out_dir: str = "frames",
                    width: int = 100, cycles: int | None = None) -> list[str]:
    """Render the standard frame set to text files. No terminal required.

    source=demo replays the bundled storm lifecycle: default frame set is
    captured mid-storm (cycle 8, storm closing) plus a dashboard variant at
    the 6-hour outage mark (cycle 10).
    """
    units_pref = cfg.get("units")
    out = os.path.join(out_dir, source)
    os.makedirs(out, exist_ok=True)

    def frames_for(cfg_r: dict, st_r: dict, extra: dict) -> dict:
        if units_pref:
            cfg_r = dict(cfg_r)
            cfg_r["units"] = units_pref
        d = gather(cfg_r, st_r, source=source)
        return {
            "dashboard.txt": dashboard_lines(d, width),
            "alerts.txt": overlay_lines("ALERTS", "\n".join(
                f"{a['event']} ({a['severity']}) — {a['area']}\n  {a['headline']}\n  {a['description']}"
                for a in d["alerts"]) or (d["alerts_note"] or "No active alerts."), width),
            "supplies.txt": overlay_lines("SUPPLIES", d["supplies_text"], width),
            "worstcase.txt": overlay_lines("WORST-CASE 48-HOUR OUTLOOK",
                                           _compute_worst(cfg_r, st_r, d), width),
            "log.txt": overlay_lines("WEATHER LOG (recent)",
                                     st_r.get("_log_tail") or state.tail_weather_log(2500),
                                     width),
            **extra,
        }

    paths = []
    if source == "demo":
        cfg_d, st_d = replay.replay_all(max_cycles=cycles or 6)
        frames = frames_for(cfg_d, st_d, {})
        _, st_out = replay.replay_all(max_cycles=cycles or 10)
        frames["dashboard-outage.txt"] = frames_for(cfg_d, st_out, {})["dashboard.txt"]
    else:
        frames = frames_for(cfg, state.load_state(), {})
    for name, lines in frames.items():
        p = os.path.join(out, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        paths.append(p)
    return paths
