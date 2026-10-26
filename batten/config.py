"""Configuration and home-directory handling.

Default home: ~/.batten (override with BATTON_HOME env var; the demo uses
this to keep replay state out of your real home).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DEFAULTS = {
    "place": "",
    "lat": None,
    "lon": None,
    "timezone": "local",           # IANA name; "local" = system zone
    "household": 2,                # people in the household (supplies scaling)
    "model": "llama3.2:3b",        # any open-weight model Ollama has pulled
    "ollama_url": "http://127.0.0.1:11434",
    "poll_minutes": 120,           # base cadence; tightens automatically
    "tight_poll_minutes": 30,      # when alerts active or storm nearby
    "outage_poll_minutes": 10,     # cheap polls while NOAA is unreachable
    "outage_worstcase_hours": 6,   # fire the 48h worst-case forecast after this
    "notify_radius_nm": 600,       # storms closer than this are "relevant"
    "units": "imperial",           # "imperial" | "metric"
    "alert_providers": [],         # [] -> default NWS for cfg's coordinates;
                                   # or [{"type": "nws"|"cap"|"generic", ...}]
    "web_host": "0.0.0.0",         # serve the web UI on the LAN (0.0.0.0)
    "web_port": 8787,              # http://<your-ip>:8787 from any LAN device
    "discord_webhook": "",         # optional; empty = console only
    "user_agent_contact": "",      # NWS asks for a contact in the UA string
}


def home_dir() -> Path:
    return Path(os.environ.get("BATTON_HOME", str(Path.home() / ".batten")))


def config_path() -> Path:
    return home_dir() / "config.json"


def state_path() -> Path:
    return home_dir() / "state.json"


def supplies_path() -> Path:
    return home_dir() / "supplies.json"


def weather_log_path() -> Path:
    return home_dir() / "weather-log.md"


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    p = config_path()
    if p.exists():
        try:
            cfg.update(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"warning: could not read {p} ({exc}); using defaults", file=sys.stderr)
    return cfg


def save_config(cfg: dict) -> None:
    home_dir().mkdir(parents=True, exist_ok=True)
    merged = dict(DEFAULTS)
    merged.update(cfg)
    tmp = config_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(config_path())


def config_exists() -> bool:
    return config_path().exists()


def tzinfo_for(cfg: dict):
    """Zone info for the configured timezone; 'local' uses the system zone."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    name = (cfg.get("timezone") or "local").strip()
    if name.lower() == "local":
        try:
            local = datetime.now().astimezone().tzinfo
            return local if local is not None else timezone.utc
        except OSError:
            return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception:
        print(f"warning: unknown timezone {name!r}; using UTC", file=sys.stderr)
        return timezone.utc
