"""The brain: a local open-weight model via Ollama, with deterministic
fallbacks so the safety loop never depends on the AI being up.

Design rules (inherited from the original storm bot's soul):
  * Safety ordering in every answer: person first, home second, tasks third.
  * Facts over vibes: every fact carries source + timestamp; estimates are
    labeled estimates. The model gets ONLY data we fetched or computed.
  * Assume the grid is failing: short, mobile-friendly, no tables.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import geo, state, supplies, units
from .config import tzinfo_for
from .noaa import FetchError
from .worstcase import build_worst_case

SYSTEM_RULES = """You are Batten, an offline-first severe weather copilot. You help one
household prepare for and ride out a severe weather event.

Rules:
- Safety ordering in every answer: the person first, the home second, the task list third.
- Be calm, terse and practical. Short paragraphs or bullet lists. No tables. No drama, no fear-mongering.
- Only use the data in this context (status snapshot + weather log). Never invent numbers, dates, or sources.
- Data from NOAA carries its advisory timestamp; say when it was last updated.
- Anything labeled ESTIMATE is your own reasoning from last-good data — say so plainly when you rely on it.
- If we are offline from NOAA, remind the user what stale means for the answer.
- Give concrete actions with rough deadlines ("before tropical-storm-force winds arrive").
- If the user asks in Spanish (or any language), reply in that language.
- Never tell the user to ignore official evacuation orders; evacuation decisions belong to authorities.
- One nudge per concern: state the action once, do not nag.
"""

PERSONA_NOTE = """You keep a field-operations tone: built for bad Wi-Fi, high humidity, and busy hands.
When the user asks a question you cannot answer from the data, say what you don't know and what single
check would answer it."""


# --- status snapshot (shared by chat, brief, watcher) ------------------------

def status_snapshot(cfg: dict, st: dict, storms: list[dict] | None = None,
                    alerts: list[dict] | None = None, alerts_note: str = "",
                    supply_note: str | None = None, now_iso: str | None = None) -> str:
    """Current-state text for the model and for `batten status`/`brief`."""
    tz = tzinfo_for(cfg)
    u = units.get_units(cfg)
    now_iso = now_iso or state.iso(state.utcnow())
    lines = [f"STATUS SNAPSHOT (generated {now_iso}; local zone: {tz})"]

    # Connectivity (replay/demo callers pass now_iso; honor it for outage math)
    outage_secs = state.outage_seconds(
        st, now=state.parse_iso(now_iso) if now_iso else None)
    last_good = st.get("last_success")
    if outage_secs is not None:
        kind = st.get("outage_kind", "unknown")
        human_kind = {
            "dns": "DNS resolution failing (internet down or captive portal)",
            "timeout": "connections timing out (weak signal / congestion / ISP outage)",
            "http": "NOAA servers reachable but erroring",
            "parse": "NOAA responses unreadable",
        }.get(kind, kind)
        lines.append(
            f"NOAA CONNECTIVITY: OFFLINE for {outage_secs / 3600:.1f} h — {human_kind}. "
            f"Last good data: {last_good or 'never'}."
        )
    else:
        poll_note = "live just now" if last_good in (None, "n/a") else f"last good poll {last_good}"
        lines.append(f"NOAA CONNECTIVITY: online ({poll_note})")

    # Storms
    lines.append("")
    if storms:
        lat, lon = cfg.get("lat"), cfg.get("lon")
        ranked = []
        for s in storms:
            if lat is None:
                ranked.append((float("inf"), s))
            else:
                ranked.append((geo.haversine_nm(s["lat"], s["lon"], lat, lon), s))
        ranked.sort(key=lambda t: t[0])
        lines.append(f"ACTIVE NHC STORMS ({len(storms)}):")
        for dist, s in ranked:
            loc = f"{s['lat']:.1f}, {s['lon']:.1f}"
            move = ""
            if s.get("movement_dir") is not None:
                move = (f", moving {geo.compass_point(s['movement_dir'])} "
                        f"at {s['movement_kt']} kt")
            rel = ""
            if dist != float("inf"):
                brg = geo.bearing_deg(lat, lon, s["lat"], s["lon"])
                rel = (f" — {u.distance(dist)} {geo.compass_point(brg)} "
                       f"of {cfg.get('place') or 'you'}")
                relation = geo.approach(s, lat, lon)["relation"]
                if relation == "toward":
                    rel += ", on a heading **toward you**"
                elif relation == "away":
                    rel += ", **moving away from you**"
                elif relation == "passing":
                    rel += f", passing to your {geo.compass_point(brg)}"
            lines.append(
                f"- {s['name']} ({geo.category_label(geo.saffir(s['kt']))}, "
                f"{u.speed(s['kt'])}), "
                f"pressure {s['pressure']} mb, at {loc}{move}{rel}; "
                f"advisory #{s['advisory']} issued {s['issued'] or 'unknown'}"
            )
        if lat is not None and ranked:
            nearest = ranked[0][1]
            wc = build_worst_case(nearest, lat, lon, last_good or now_iso)
            if wc.ts_arrival_h is not None and wc.ts_arrival_h <= 48:
                lines.append(
                    f"ESTIMATE: worst-case tropical-storm-force winds could reach you "
                    f"in ~{wc.ts_arrival_h:.0f} h if the storm holds course and intensifies "
                    f"one category."
                )
    else:
        lines.append("ACTIVE NHC STORMS: none at last good poll.")

    # Alerts
    lines.append("")
    if alerts:
        lines.append(f"NWS ALERTS for your location ({len(alerts)}):")
        for a in alerts:
            lines.append(f"- {a['event']} ({a['severity']}) — {a['area']}; sent {a['sent']}")
            if a.get("headline"):
                lines.append(f"  {a['headline']}")
    elif alerts_note:
        lines.append(f"NWS ALERTS: {alerts_note}")
    else:
        lines.append("NWS ALERTS: none active at last good poll.")

    # Supplies
    lines.append("")
    if supply_note is None:
        sup = supplies.load_supplies()
        missing = supplies.missing_essentials(int(cfg.get("household") or 2), sup)
        supply_note = (f"{len(missing)} essential items still missing: " + "; ".join(missing)
                       if missing else "all essential supplies marked ready.")
    lines.append(f"SUPPLIES: {supply_note}")
    return "\n".join(lines)


def build_system_prompt(cfg: dict, st: dict, storms, alerts, alerts_note: str = "") -> str:
    return (
        SYSTEM_RULES
        + "\n" + PERSONA_NOTE
        + "\n\nHousehold: "
        + json.dumps({k: cfg.get(k) for k in ("place", "lat", "lon", "household", "timezone")})
        + "\n\n" + status_snapshot(cfg, st, storms, alerts, alerts_note)
        + "\n\nRECENT WEATHER LOG (oldest first, most recent last):\n"
        + state.tail_weather_log(3500)
        + "\n\nAnswer the user using this context. If the context is stale, say how stale."
    )


# --- Ollama client -----------------------------------------------------------

class OllamaBrain:
    """Thin stdlib client for Ollama's REST API (no SDK dependency)."""

    def __init__(self, base_url: str, model: str, timeout: int = 300):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def available(self) -> tuple[bool, str]:
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags",
                                         headers={"User-Agent": "batten"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            return False, f"Ollama not reachable at {self.base_url} ({exc.__class__.__name__})"
        names = [m.get("name", "") for m in data.get("models", [])]
        if not names:
            return False, "Ollama is up but no models are pulled (ollama pull <model>)"
        if self.model in names:
            return True, self.model
        return True, f"model '{self.model}' not pulled yet; available: {', '.join(names[:8])}"

    def chat(self, messages: list[dict]):
        """Yield assistant text chunks (streaming). Raises FetchError-ish OSErrors."""
        payload = json.dumps({
            "model": self.model,
            "messages": messages,
            "stream": True,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/chat", data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "batten"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            for raw in resp:
                try:
                    obj = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                chunk = obj.get("message", {}).get("content", "")
                if chunk:
                    yield chunk
                if obj.get("done"):
                    break

    def complete(self, messages: list[dict]) -> str:
        return "".join(self.chat(messages))


def get_brain(cfg: dict) -> OllamaBrain:
    return OllamaBrain(cfg.get("ollama_url", "http://127.0.0.1:11434"),
                       cfg.get("model", "llama3.2:3b"))


def model_status(cfg: dict) -> tuple[bool, str]:
    import os
    if os.environ.get("BATTON_NO_MODEL"):
        return False, "BATTON_NO_MODEL is set — using template brain"
    try:
        return get_brain(cfg).available()
    except Exception as exc:  # defensive: availability check must never crash
        return False, f"model check failed: {exc}"
