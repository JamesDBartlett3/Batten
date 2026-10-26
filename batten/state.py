"""Runtime state (connectivity tracking + last-known storm snapshots) and the
human-readable weather log.

The state file is the watcher's memory. Everything in it must be readable
offline by the local model, which is why snapshots keep source + timestamp.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import config


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_state() -> dict:
    p = config.state_path()
    if not p.exists():
        return {"storms": {}, "alerts_hash": "", "alert_events": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"storms": {}, "alerts_hash": "", "alert_events": []}


def save_state(state: dict) -> None:
    config.home_dir().mkdir(parents=True, exist_ok=True)
    tmp = config.state_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(config.state_path())


# --- connectivity -----------------------------------------------------------

def record_poll_success(state: dict, now: datetime | None = None) -> dict:
    """Record a good NOAA poll. Returns {'came_back': bool} on outage end."""
    now = now or utcnow()
    went_down_at = state.get("outage_started")
    came_back = bool(went_down_at)
    state["last_success"] = iso(now)
    state["last_error"] = None
    state["consecutive_failures"] = 0
    state["outage_started"] = None
    state["outage_kind"] = None
    state["worstcase_sent_for_outage"] = False
    if came_back:
        state["last_outage"] = {
            "started": went_down_at,
            "ended": iso(now),
        }
    return {"came_back": came_back}


def record_poll_failure(state: dict, kind: str, detail: str, now: datetime | None = None) -> dict:
    """Record a failed poll. Returns {'went_down': bool, 'outage_seconds': int|None}."""
    now = now or utcnow()
    state["last_poll"] = iso(now)
    state["last_error"] = {"kind": kind, "detail": detail, "at": iso(now)}
    state["consecutive_failures"] = int(state.get("consecutive_failures") or 0) + 1
    went_down = False
    outage_seconds = None
    if not state.get("outage_started"):
        state["outage_started"] = iso(now)
        went_down = True
    else:
        started = parse_iso(state["outage_started"])
        if started:
            outage_seconds = (now - started).total_seconds()
    state["outage_kind"] = kind
    return {"went_down": went_down, "outage_seconds": outage_seconds}


def outage_seconds(state: dict, now: datetime | None = None) -> float | None:
    started = parse_iso(state.get("outage_started") or "")
    if not started:
        return None
    now = now or utcnow()
    return max(0.0, (now - started).total_seconds())


# --- weather log ------------------------------------------------------------

def append_weather_log(title: str, lines: list[str], when: datetime | None = None) -> None:
    """Markdown log — human-readable and model-readable. Newest at the bottom.

    A snapshot is appended for EVERY poll before any notification decision,
    so during an outage the model can reason over the last-known history.
    """
    config.home_dir().mkdir(parents=True, exist_ok=True)
    p = config.weather_log_path()
    when = when or utcnow()
    stamp = when.strftime("%Y-%m-%d %H:%M UTC")
    with p.open("a", encoding="utf-8") as fh:
        fh.write(f"\n## {stamp} — {title}\n")
        for line in lines:
            fh.write(f"- {line}\n")


def tail_weather_log(max_chars: int = 4000) -> str:
    p = config.weather_log_path()
    if not p.exists():
        return "(no weather log yet)"
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return "(weather log unreadable)"
    return text[-max_chars:]
