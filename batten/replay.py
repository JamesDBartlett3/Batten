"""Offline storm replay — shared by `batten demo` and the TUI/web demo views.

Replays batten/fixtures/trudy.json (a real hurricane's advisory lifecycle,
including a simulated outage) through the normal Watcher pipeline with a
simulated clock, so triggers, outage handling, and the worst-case fire all
exercise the same code paths they would against live NOAA.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from . import config, geo, noaa, state

FIXTURE = Path(__file__).parent / "fixtures" / "trudy.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class ReplayFetcher:
    """Watcher.fetcher that walks the fixture, advancing a sim clock.

    Mirrors the real fetcher's contract: raises noaa.FetchError on failed
    cycles, returns {"storms", "alerts", "alerts_note"} otherwise.
    """

    def __init__(self, entries: list[dict], sim_now: dict):
        self.entries = entries
        self.sim_now = sim_now          # {"t": datetime} — advanced per poll
        self.i = 0

    def __call__(self, cfg: dict, st: dict) -> dict:
        if self.i >= len(self.entries):           # past the end: board clear
            return {"storms": [], "alerts": [], "alerts_note": "replay finished"}
        entry = self.entries[self.i]
        self.sim_now["t"] += timedelta(minutes=entry.get("advance_minutes", 0))
        self.i += 1
        if entry.get("fail"):
            raise noaa.FetchError(entry["fail"]["kind"], entry["fail"]["detail"])
        s = entry.get("storm")
        if not s:
            return {"storms": [], "alerts": [], "alerts_note": "replay (no NWS)"}
        out = dict(s)
        out.setdefault("mph", geo.kt_to_mph(out["kt"]))
        for k, v in (("bin", "EP2"), ("classification", "HU"), ("advisory_url", ""),
                     ("discussion_url", ""), ("graphics_url", ""), ("last_update", "")):
            out.setdefault(k, v)
        return {"storms": [out], "alerts": [], "alerts_note": "replay (no NWS)"}


def replay_config() -> tuple[dict, dict]:
    """(cfg, sim_now) for the fixture's demo user."""
    fx = load_fixture()
    user = fx["user"]
    from . import config
    cfg = dict(config.DEFAULTS)
    cfg.update({"place": user["place"], "lat": user["lat"], "lon": user["lon"],
                "timezone": "America/Mexico_City", "discord_webhook": ""})
    return cfg, {"t": state.parse_iso(fx["clock_start"])}


def replay_all(max_cycles: int | None = None) -> tuple[dict, dict]:
    """Run the full replay through a real Watcher. Returns (cfg, final_state).

    Runs with BATTON_HOME pointed at a throwaway dir so poll()'s state
    writes never touch the user's real home, and the returned state carries
    the final sim clock under "_sim_now_iso" so renderers can compute outage
    durations in replay time.
    """
    import os
    import tempfile
    from . import watcher as watcher_mod

    old_home = os.environ.get("BATTON_HOME")
    os.environ["BATTON_HOME"] = tempfile.mkdtemp(prefix="batten-replay-")
    try:
        cfg, sim_now = replay_config()
        st = {"storms": {}, "alerts_hash": "", "alert_events": []}
        fx = load_fixture()
        fetcher = ReplayFetcher(fx["entries"], sim_now)
        w = watcher_mod.Watcher(cfg, st, fetcher=fetcher, clock=lambda: sim_now["t"],
                                sleep=lambda _s: None, say=lambda _t: None,
                                send=lambda _t: None)
        w.run(max_cycles=max_cycles or len(fx["entries"]))
        st["_sim_now_iso"] = state.iso(sim_now["t"])
        log_path = config.weather_log_path()
        st["_log_tail"] = log_path.read_text(encoding="utf-8")[-4000:] if log_path.exists() else ""
        return cfg, st
    finally:
        if old_home is None:
            os.environ.pop("BATTON_HOME", None)
        else:
            os.environ["BATTON_HOME"] = old_home
