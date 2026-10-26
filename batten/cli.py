"""Command-line interface: batten init|status|brief|chat|watch|worstcase|cone|
supplies|doctor|selftest|demo"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__, brain, config, geo, noaa, notify, providers, state, supplies, units, watcher, worstcase


def _need_config() -> dict:
    if not config.config_exists():
        sys.exit("batten is not configured yet. Run: batten init --lat .. --lon .. --place ..")
    return config.load_config()


# --- init ---------------------------------------------------------------------

def cmd_init(args) -> None:
    cfg = config.load_config()
    if args.zipcode and (args.lat is None or args.lon is None):
        try:
            lat, lon, label = noaa.zip_geocode(args.zipcode, cfg)
            args.lat, args.lon = lat, lon
            args.place = args.place or label
        except noaa.FetchError as exc:
            sys.exit(f"ZIP lookup failed ({exc.kind}): set --lat/--lon manually instead.")
    # Re-running init to change preferences shouldn't demand coordinates again.
    if args.lat is None and cfg.get("lat") is not None:
        args.lat = cfg["lat"]
    if args.lon is None and cfg.get("lon") is not None:
        args.lon = cfg["lon"]
    if args.lat is None:
        if sys.stdin.isatty():
            args.lat = float(input("Latitude (decimal degrees, north positive): ").strip())
        else:
            sys.exit("--lat is required (or --zip while online).")
    if args.lon is None:
        if sys.stdin.isatty():
            args.lon = float(input("Longitude (decimal degrees, east positive): ").strip())
        else:
            sys.exit("--lon is required (or --zip while online).")

    cfg.update({
        "lat": float(args.lat), "lon": float(args.lon),
        "place": args.place or cfg.get("place") or "home",
        "timezone": args.tz or cfg.get("timezone", "local"),
        "household": args.household or int(cfg.get("household") or 2),
        "model": args.model or cfg.get("model", "llama3.2:3b"),
        "ollama_url": args.ollama_url or cfg.get("ollama_url", "http://127.0.0.1:11434"),
        "poll_minutes": args.poll_minutes or int(cfg.get("poll_minutes") or 120),
        "units": args.units or cfg.get("units", "imperial"),
        "web_port": args.web_port or int(cfg.get("web_port") or 8787),
        "discord_webhook": args.webhook if args.webhook is not None else cfg.get("discord_webhook", ""),
        "user_agent_contact": args.contact or cfg.get("user_agent_contact", ""),
    })
    config.save_config(cfg)
    print("Saved configuration:")
    print(json.dumps({k: cfg[k] for k in
                      ("place", "lat", "lon", "timezone", "household", "model",
                       "ollama_url", "poll_minutes", "units", "web_port")},
                     indent=2))
    print("\nNext steps:")
    print("  1. Install Ollama and pull a model:  ollama pull " + cfg["model"])
    print("  2. Check everything:                 batten doctor")
    print("  3. Fill your supply list:            batten supplies")
    print("  4. Start the watcher:                batten watch")


# --- helpers shared by status/brief/chat ---------------------------------------

def _fresh_or_cached(cfg: dict, st: dict):
    """Try a live read-only refresh; fall back to last-known snapshots."""
    alerts_note = ""
    try:
        storms = noaa.fetch_current_storms(cfg)
        online, cause = True, None
    except noaa.FetchError as exc:
        storms = [s for s in (st.get("storms") or {}).values()]
        online, cause = False, exc
    alerts = []
    if cfg.get("alert_providers"):
        alerts, notes = providers.fetch_all(cfg)
        alerts_note = "; ".join(notes)
    elif cfg.get("lat") is not None and cfg.get("lon") is not None:
        try:
            alerts = noaa.fetch_alerts(cfg["lat"], cfg["lon"], cfg)
        except noaa.FetchError as exc:
            alerts_note = ("NWS alerts do not cover this location (US only)."
                           if exc.kind == "unsupported"
                           else f"NWS alerts unreachable ({exc.kind}).")
    return storms, alerts, alerts_note, online, cause


def _worstcase_text(cfg: dict, st: dict, storms: list[dict], simulate_hours: float | None) -> str:
    lat, lon = cfg.get("lat"), cfg.get("lon")
    if lat is None or lon is None:
        return "Set coordinates with `batten init` first."
    nearest = None
    if storms:
        nearest = min(storms, key=lambda s: geo.haversine_nm(s["lat"], s["lon"], lat, lon))
    outage_h = simulate_hours if simulate_hours is not None \
        else (state.outage_seconds(st) or 0) / 3600
    wc = worstcase.build_worst_case(nearest, lat, lon,
                                    st.get("last_success") or "no polls yet",
                                    outage_hours=outage_h)
    return worstcase.render_worst_case(wc, cfg)


# --- status / brief --------------------------------------------------------------

def cmd_status(args) -> None:
    cfg = _need_config()
    st = state.load_state()
    storms, alerts, alerts_note, online, cause = _fresh_or_cached(cfg, st)
    if not online:
        print(f"⚠️  NOAA unreachable ({cause.kind}): {_cause_note(cause.kind)} "
              f"— showing last-known data.\n")
    print(brain.status_snapshot(cfg, st, storms, alerts, alerts_note))
    ok, msg = brain.model_status(cfg)
    print(f"\nLocal model: {'✅' if ok else '❌'} {msg}")
    if state.outage_seconds(st) is not None or not online:
        print("\nWorst-case preview (if this outage persists):\n")
        print(_worstcase_text(cfg, st, storms, simulate_hours=None))


def _cause_note(kind: str) -> str:
    return {
        "dns": "DNS failing — internet is down or a captive portal took over",
        "timeout": "connections timing out — weak signal, congestion, or ISP outage",
        "http": "NOAA servers erroring",
        "parse": "NOAA responses unreadable",
    }.get(kind, kind)


def cmd_brief(args) -> None:
    cfg = _need_config()
    st = state.load_state()
    storms, alerts, alerts_note, online, cause = _fresh_or_cached(cfg, st)
    snap = brain.status_snapshot(cfg, st, storms, alerts, alerts_note)
    if not online:
        print(f"⚠️  OFFLINE from NOAA ({cause.kind}): {_cause_note(cause.kind)} — "
              f"brief built from last-known data.\n")
    print(snap)
    if brain.model_status(cfg)[0]:
        b = brain.get_brain(cfg)
        prompt = ("Write a 5-bullet briefing for this household from the snapshot "
                  "below: what matters right now, any deadlines, and the single most "
                  "valuable next action. Plain language, no tables.\n\n" + snap)
        try:
            text = b.complete([{"role": "system", "content": brain.SYSTEM_RULES},
                               {"role": "user", "content": prompt}])
            print("\n--- local model briefing ---\n" + text.strip())
        except (urllib.error.URLError, OSError) as exc:
            print(f"\n(local model unavailable for narrative: {exc.__class__.__name__}; "
                  f"the snapshot above is the deterministic brief)")


# --- chat ------------------------------------------------------------------------

def _fallback_reply(user: str, cfg: dict, st: dict, storms, alerts, alerts_note) -> str | None:
    """Deterministic answers when the model is down. Returns None → generic help."""
    u = user.lower()
    if any(k in u for k in ("status", "estado", "what's happening", "update")):
        return brain.status_snapshot(cfg, st, storms, alerts, alerts_note)
    if any(k in u for k in ("suppl", "suministro", "missing", "falta")):
        sup = supplies.load_supplies()
        text, have, missing = supplies.render_supplies(int(cfg.get("household") or 2), sup, cfg)
        return text
    if any(k in u for k in ("worst", "peor", "48")):
        return _worstcase_text(cfg, st, storms, simulate_hours=None)
    return None


def cmd_chat(args) -> None:
    cfg = _need_config()
    st = state.load_state()
    storms, alerts, alerts_note, online, cause = _fresh_or_cached(cfg, st)
    ok, msg = brain.model_status(cfg)
    model_ok = ok and "not pulled" not in msg and "no models" not in msg

    if model_ok:
        b = brain.get_brain(cfg)
        print(f"🥥 batten chat — local model '{cfg.get('model')}' "
              f"({'NOAA online' if online else 'NOAA OFFLINE — working from last-known data'})")
        print("Type /status /brief /worstcase /supplies or just talk. /quit to exit.\n")
    else:
        print(f"⚠️  Local model unavailable: {msg}")
        print("Install Ollama (https://ollama.com), then: ollama pull " + cfg.get("model", ""))
        print("Deterministic mode still answers /status /worstcase /supplies. "
              "Type /quit to exit.\n")

    history: list[dict] = []
    while True:
        try:
            user = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user:
            continue
        if user in ("/quit", "/exit", "quit", "exit"):
            break
        try:
            if user == "/status":
                storms, alerts, alerts_note, online, cause = _fresh_or_cached(cfg, st)
                print(brain.status_snapshot(cfg, st, storms, alerts, alerts_note))
                continue
            if user == "/supplies":
                sup = supplies.load_supplies()
                text, _, _ = supplies.render_supplies(int(cfg.get("household") or 2), sup, cfg)
                print(text)
                continue
            if user == "/worstcase":
                print(_worstcase_text(cfg, st, storms, simulate_hours=None))
                continue
            if user == "/brief":
                cmd_brief(args)
                continue
        except KeyboardInterrupt:
            print()
            continue

        if not model_ok:
            reply = _fallback_reply(user, cfg, st, storms, alerts, alerts_note)
            if reply:
                print(reply)
            else:
                print("Chat needs the local model running. Meanwhile I can answer /status, "
                      "/worstcase, /supplies — and the watcher keeps logging storms offline.")
            continue

        system = brain.build_system_prompt(cfg, st, storms, alerts, alerts_note)
        messages = [{"role": "system", "content": system}] + history[-12:] + \
                   [{"role": "user", "content": user}]
        print("batten> ", end="", flush=True)
        acc = ""
        try:
            for chunk in b.chat(messages):
                acc += chunk
                print(chunk, end="", flush=True)
            print("\n")
        except (urllib.error.URLError, OSError) as exc:
            print(f"\n(model error: {exc}; try `batten doctor`)")
            continue
        history += [{"role": "user", "content": user},
                    {"role": "assistant", "content": acc}]


# --- watch / worstcase / cone ------------------------------------------------------

def cmd_watch(args) -> None:
    cfg = _need_config()
    st = state.load_state()
    narrate = _make_narrator(cfg)
    w = watcher.Watcher(cfg, st, narrate=narrate)
    print(f"🥥 batten watch — {cfg.get('place')} ({cfg.get('lat')}, {cfg.get('lon')})")
    print(f"   base every {cfg.get('poll_minutes')} min; {cfg.get('tight_poll_minutes')} min when "
          f"alerted; {cfg.get('outage_poll_minutes')} min while NOAA is unreachable.")
    print(f"   worst-case outlook fires after {cfg.get('outage_worstcase_hours')} h offline. "
          f"Ctrl-C to stop.\n")
    try:
        w.run(once=args.once, max_cycles=args.max_cycles)
    except KeyboardInterrupt:
        print("\nwatcher stopped. state saved; run `batten status` anytime.")


def cmd_worstcase(args) -> None:
    cfg = _need_config()
    st = state.load_state()
    storms = [s for s in (st.get("storms") or {}).values()]
    if not storms and not args.offline_only:
        try:
            storms = noaa.fetch_current_storms(cfg)
        except noaa.FetchError:
            pass
    text = _worstcase_text(cfg, st, storms, simulate_hours=args.outage_hours)
    print(text + "\n")
    if args.narrate and brain.model_status(cfg)[0]:
        b = brain.get_brain(cfg)
        try:
            para = b.complete([
                {"role": "system", "content": brain.SYSTEM_RULES},
                {"role": "user", "content": "Write a short, calm action plan for this household "
                 "based ONLY on the outlook below. 6 bullets max: what to finish NOW, what to "
                 "move indoors, water/food/power priorities, and when to shelter.\n\n" + text}])
            print("--- local model plan ---\n" + para.strip())
        except (urllib.error.URLError, OSError) as exc:
            print(f"(local model unavailable: {exc.__class__.__name__})")


def cmd_cone(args) -> None:
    cfg = _need_config()
    st = state.load_state()
    lat, lon = cfg.get("lat"), cfg.get("lon")
    try:
        storms = noaa.fetch_current_storms(cfg)
    except noaa.FetchError as exc:
        storms = [s for s in (st.get("storms") or {}).values()]
        if not storms:
            sys.exit(f"cannot reach NOAA ({exc.kind}) and no last-known storms; try later.")
    if not storms:
        sys.exit("no active storms — no cone to fetch.")
    storm = (min(storms, key=lambda s: geo.haversine_nm(s["lat"], s["lon"], lat, lon))
             if lat is not None else storms[0])
    url = noaa.cone_image_url(storm, cfg)
    if not url:
        sys.exit("could not resolve the current cone image URL from NHC graphics page.")
    dest = Path(args.out or f"{storm['id']}-cone.png")
    if noaa.download_cone(url, dest):
        print(f"saved {storm['name']} 5-day cone (full res) → {dest}\n{url}")
    else:
        sys.exit("cone download failed (NHC may be rate-limiting; retry in a minute).")


# --- supplies -----------------------------------------------------------------------

def cmd_supplies(args) -> None:
    cfg = _need_config()
    sup = supplies.load_supplies()
    household = int(cfg.get("household") or 2)
    if args.action in ("have", "need", "na", "note") and args.item:
        if args.action == "have":
            ok = supplies.set_item(sup, args.item, "have", True)
            if ok and args.extra:
                supplies.set_item(sup, args.item, "note", " ".join(args.extra))
        elif args.action == "need":
            ok = supplies.set_item(sup, args.item, "have", False)
        elif args.action == "na":
            ok = supplies.set_item(sup, args.item, "na", True)
        else:
            ok = supplies.set_item(sup, args.item, "note", " ".join(args.extra or []))
        if not ok:
            sys.exit(f"unknown item id: {args.item} (run `batten supplies --list-ids`)")
        supplies.save_supplies(sup)
    if args.list_ids:
        print(" ".join(i["id"] for i in supplies.DEFAULT_SUPPLIES))
        return
    text, have, missing = supplies.render_supplies(household, sup, cfg)
    print(text)


# --- doctor --------------------------------------------------------------------------

def cmd_doctor(args) -> None:
    cfg = config.load_config()
    results: list[tuple[str, bool, str]] = []

    configured = config.config_exists() and cfg.get("lat") is not None
    results.append(("config (place + coordinates)", configured,
                    f"{cfg.get('place')} ({cfg.get('lat')}, {cfg.get('lon')})"
                    if configured else "run `batten init`"))

    try:
        config.home_dir().mkdir(parents=True, exist_ok=True)
        probe = config.home_dir() / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        results.append(("state directory writable", True, str(config.home_dir())))
    except OSError as exc:
        results.append(("state directory writable", False, str(exc)))

    try:
        noaa.http_get_json(noaa.NHC_CURRENT, cfg, timeout=8)
        results.append(("NOAA NHC reachable", True, "CurrentStorms.json OK"))
    except noaa.FetchError as exc:
        results.append(("NOAA NHC reachable", False, f"{exc.kind}: {exc.detail}"))

    if cfg.get("alert_providers"):
        for prov in providers.get_providers(cfg):
            ok, detail = prov.test(cfg)
            results.append((f"alert provider: {prov.name}", ok, detail))
    elif cfg.get("lat") is not None:
        try:
            n = len(noaa.fetch_alerts(cfg["lat"], cfg["lon"], cfg))
            results.append(("NWS alerts (US + territories)", True, f"reachable, {n} active"))
        except noaa.FetchError as exc:
            supported = exc.kind != "unsupported"
            results.append(("NWS alerts (US + territories)", supported,
                            "not covered for this location — NHC only (expected outside the US); "
                            "configure a CAP/generic provider for local alerts"
                            if not supported else f"{exc.kind}: {exc.detail}"))

    ok, msg = brain.model_status(cfg)
    results.append(("local model (Ollama)", ok, msg))

    results.append(("Discord webhook", bool(cfg.get("discord_webhook")),
                    "configured" if cfg.get("discord_webhook")
                    else "not set (console-only notifications)"))

    print("batten doctor")
    fails = 0
    for name, good, detail in results:
        mark = "✅" if good else ("⚠️ " if name.startswith("Discord") else "❌")
        if not good and not name.startswith("Discord"):
            fails += 1
        print(f"  {mark} {name}: {detail}")
    if fails:
        print(f"\n{fails} required check(s) failing — see above.")
        sys.exit(1)
    print("\nAll required checks passed.")


# --- narrator -------------------------------------------------------------------------

def _make_narrator(cfg: dict):
    if os.environ.get("BATTON_NO_MODEL"):
        return None
    b = brain.get_brain(cfg)

    def narrate(prompt: str) -> str | None:
        try:
            return b.complete([
                {"role": "system", "content": brain.SYSTEM_RULES},
                {"role": "user", "content": prompt}]).strip()
        except (urllib.error.URLError, OSError) as exc:
            print(f"(local model unavailable for narration: {exc.__class__.__name__}; "
                  f"sending deterministic outlook)", file=sys.stderr)
            return None
    return narrate


# --- demo --------------------------------------------------------------------------

def cmd_demo(args) -> None:
    """Replay the Trudy fixture with a simulated outage — fully offline."""
    os.environ.setdefault("BATTON_HOME", str(Path.home() / ".batten-demo"))
    if Path(os.environ["BATTON_HOME"]).exists() and not args.keep:
        import shutil
        shutil.rmtree(os.environ["BATTON_HOME"])

    from . import replay
    fixture = replay.load_fixture()
    user = fixture["user"]
    cfg, sim_now = replay.replay_config()

    def clock():
        return sim_now["t"]

    fetcher = replay.ReplayFetcher(fixture["entries"], sim_now)

    print(f"=== batten demo — replaying '{fixture['name']}' offline ===")
    print(f"    user at {user['place']} ({user['lat']}, {user['lon']}), sim clock starts "
          f"{fixture['clock_start']}, 1 cycle ≈ 2 storm-hours, state isolated in "
          f"{os.environ['BATTON_HOME']}\n")
    w = watcher.Watcher(cfg, state.load_state(), fetcher=fetcher,
                        clock=clock, sleep=lambda _s: time.sleep(args.cycle_seconds))
    w.run(max_cycles=args.cycles or len(fixture["entries"]))
    print(f"\n=== demo done — weather log: {config.weather_log_path()} ===")
    if not args.keep:
        import shutil
        print("(demo state left in place; rerun with --keep to preserve, or it resets next time)")


# --- TUI / web -------------------------------------------------------------------

def cmd_tui(args) -> None:
    from . import tui
    if args.source == "demo":
        cfg = config.load_config()  # the replay carries its own demo location
    else:
        cfg = _need_config()
    if args.units:
        cfg["units"] = args.units
    if args.headless:
        paths = tui.render_headless(cfg, source=args.source, out_dir=args.headless)
        print(f"rendered {len(paths)} frame(s):")
        for p in paths:
            print(f"  {p}")
        return
    tui.run(cfg)


def cmd_serve(args) -> None:
    from . import web
    cfg = _need_config()
    web.serve(cfg, host=args.host, port=args.port)


# --- selftest -------------------------------------------------------------------------

def cmd_selftest(args) -> None:
    from . import selftest
    sys.exit(0 if selftest.run() else 1)


# --- parser ----------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="batten",
        description="Offline-first severe weather copilot: NOAA tracking + local open-weight model.")
    p.add_argument("--version", action="version", version=f"batten {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="configure location and preferences")
    sp.add_argument("--lat", type=float)
    sp.add_argument("--lon", type=float)
    sp.add_argument("--zip", dest="zipcode", help="US ZIP — geocoded when online")
    sp.add_argument("--place", help="label, e.g. 'Northside, Houston'")
    sp.add_argument("--tz", help="IANA timezone, e.g. America/Chicago")
    sp.add_argument("--household", type=int, help="people in the household")
    sp.add_argument("--model", help="Ollama model tag (open-weight), e.g. qwen2.5:7b")
    sp.add_argument("--ollama-url", dest="ollama_url")
    sp.add_argument("--poll-minutes", dest="poll_minutes", type=int)
    sp.add_argument("--units", choices=["imperial", "metric"],
                    help="display units (default imperial; NHC-native)")
    sp.add_argument("--web-port", dest="web_port", type=int,
                    help="port for `batten serve` (default 8787)")
    sp.add_argument("--webhook", help="Discord webhook URL for notifications ('' to clear)")
    sp.add_argument("--contact", help="contact added to the NWS User-Agent (courtesy)")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("status", help="current picture: storms, alerts, connectivity, supplies")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("brief", help="one-shot briefing (narrated by the local model if up)")
    sp.set_defaults(func=cmd_brief)

    sp = sub.add_parser("chat", help="chat with the local model about your situation")
    sp.set_defaults(func=cmd_chat)

    sp = sub.add_parser("watch", help="poll NOAA and alert on triggers (runs forever)")
    sp.add_argument("--once", action="store_true", help="single poll then exit (cron mode)")
    sp.add_argument("--max-cycles", type=int, default=None)
    sp.set_defaults(func=cmd_watch)

    sp = sub.add_parser("worstcase", help="worst-case 48h outlook from last-known data")
    sp.add_argument("--outage-hours", type=float, default=None,
                    help="pretend the outage is this many hours old")
    sp.add_argument("--narrate", action="store_true", help="also ask the local model for a plan")
    sp.add_argument("--offline-only", action="store_true",
                    help="never refresh; strictly use last-known state")
    sp.set_defaults(func=cmd_worstcase)

    sp = sub.add_parser("cone", help="download the active storm's full-res forecast cone")
    sp.add_argument("--out", help="output file (default <stormid>-cone.png)")
    sp.set_defaults(func=cmd_cone)

    sp = sub.add_parser("supplies", help="household supply readiness checklist")
    sp.add_argument("action", nargs="?", choices=["have", "need", "na", "note"])
    sp.add_argument("item", nargs="?", help="item id")
    sp.add_argument("extra", nargs="*", help="note text")
    sp.add_argument("--list-ids", action="store_true")
    sp.set_defaults(func=cmd_supplies)

    sub.add_parser("doctor", help="check config, network, model, notification targets").set_defaults(
        func=cmd_doctor)
    sub.add_parser("selftest", help="run offline unit tests").set_defaults(func=cmd_selftest)

    sp = sub.add_parser("demo", help="replay a real storm lifecycle offline (with outage)")
    sp.add_argument("--cycles", type=int, default=None, help="max cycles (default: all)")
    sp.add_argument("--cycle-seconds", type=float, default=0.8)
    sp.add_argument("--keep", action="store_true", help="do not reset demo state")
    sp.set_defaults(func=cmd_demo)

    sp = sub.add_parser("tui", help="full-screen terminal dashboard (PC/Mac/Linux)")
    sp.add_argument("--headless", nargs="?", const="frames", default=None, metavar="DIR",
                    help="render dashboard frames to text files instead of running "
                         "interactively (screenshots/tests); no TTY needed")
    sp.add_argument("--source", choices=["live", "demo"], default="live",
                    help="data source: live NOAA or the offline storm replay")
    sp.add_argument("--units", choices=["imperial", "metric"], default=None)
    sp.set_defaults(func=cmd_tui)

    sp = sub.add_parser("serve", help="serve the web dashboard on your LAN (phones included)")
    sp.add_argument("--host", default=None, help="bind address (default from config: 0.0.0.0)")
    sp.add_argument("--port", type=int, default=None, help="port (default from config: 8787)")
    sp.set_defaults(func=cmd_serve)

    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
