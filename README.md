# 🥥 Batten

**An offline-first severe weather copilot.** Batten watches NOAA's trackers for
your location, keeps a running storm log, tracks your household's supply
readiness, chats with you about all of it — and when the internet dies
mid-storm, it keeps working: it warns you that NOAA is unreachable, tells you
*why*, and after a few hours offline it builds a worst-case 48-hour outlook
from the last good data so you can plan while you're in the dark.

> Batten: *(verb)* — to batten down the hatches; to secure everything before a storm.

It was inspired by a real storm-season Discord bot that spent five days
watching a hurricane for a first-time homeowner — every two hours, day and
night — and then kept chatting while the wind took the neighborhood's internet
down. Batten is the generalized, anyone-can-run-it version: no specific
country, house, or person baked in.

---

## Why the open core matters

Everything that makes Batten *Batten* runs on your machine, with open pieces:

- **The brain is an open-weight model running locally** (via
  [Ollama](https://ollama.com) — Llama, Qwen, Gemma, Mistral, anything you've
  pulled). The chat, briefings, and the worst-case action plan are generated on
  your laptop. **When a storm takes your internet down — the exact moment you
  want a weather assistant — the AI keeps working.** A cloud-model assistant
  goes dark precisely when you need it most. That's not a hypothetical; it's
  the failure mode this tool is built around.
- **Your data stays yours.** Your coordinates, household size, supply list,
  and every "are we going to be okay?" you type live in `~/.batten/` and never
  leave your machine. No account, no telemetry, no prompt logs on someone
  else's server.
- **Zero cost, zero dependencies.** The whole thing is Python stdlib — no pip
  installs at all. Set it up once, run it forever, pay nothing per query. The
  model is the only heavy piece, and you choose it (a 3B model runs fine on a
  laptop).
- **Swappable and inspectable.** Don't like the model? `batten init --model
  qwen2.5:7b`. Don't like how the watcher phrases things? It's ~60 lines of
  prompt. Want a different worst-case doctrine? The engine is a documented,
  pure-Python module you can read and change. Try that with a subscription
  API.

The deterministic safety loop is deliberately *not* LLM-dependent: trigger
detection, connectivity monitoring, and the worst-case math are plain code so
they work under load, on battery, with the model cold. The open model sits on
top and turns numbers into judgment.

## What it does

| When online | When offline |
| --- | --- |
| Polls NHC every 2h (30 min when alerts are active or a storm is near) | Keeps retrying every 10 min |
| Watches NWS alerts/forecasts for your exact point (US + territories) | Marks the gap, keeps NHC history |
| Alerts on: new storms, category changes, rapid intensification, landfall, watch/warning changes, 300/150 nm proximity crossings | **Warns you immediately** that NOAA is unreachable, with the cause (DNS vs timeout vs server error) |
| Logs every poll to a human-readable `weather-log.md` | After **6 h** offline, fires a **worst-case 48-hour outlook** from last-good data |
| Chats via a local open-weight model (streams, offline-capable) | Chat still works — the model is local; it answers from the log |
| Optional Discord webhook for push updates | Console always works |

## Quick start

```bash
# 1. Get this repo, no dependencies to install
git clone <this-repo> && cd Batten
python3 -m batten --help          # runs from the checkout

# 2. Install Ollama (https://ollama.com) and pull an open-weight model
ollama pull llama3.2:3b           # any model works; 3B is laptop-friendly

# 3. Configure your location
python3 -m batten init --lat 29.76 --lon -95.37 --place "Houston, TX" \
    --tz America/Chicago --household 4
# US users can geocode by ZIP instead:  batten init --zip 77002 ...

# 4. Check the whole stack
python3 -m batten doctor

# 5. Fill in your supply list (scales with household size)
python3 -m batten supplies
python3 -m batten supplies have water "12 gallons in the pantry"

# 6. Start watching (run under systemd/tmux/pm2 to keep it alive)
python3 -m batten watch

# 7. Talk to it
python3 -m batten chat
```

Or install it: `pip install .` gives you a `batten` command.

### See it work without a storm

Batten ships with a replay of a **real hurricane lifecycle** (East Pacific,
October 2026 — advisories transcribed from an actual watcher's log), including
a simulated internet outage:

```bash
python3 -m batten demo
```

You'll see it detect the storm, fire the category-change and rapid-
intensification triggers, warn "cannot reach NOAA" when the simulated outage
hits, and at the 6-hour mark produce the worst-case 48-hour outlook from the
last good advisory — then stand down when the storm dies. No network needed.

## The outage playbook (the point of the whole thing)

1. **Detection.** Every poll classifies failure: `dns` (internet down /
   captive portal), `timeout` (weak signal, congestion, ISP outage), `http`,
   or `parse`. NWS not covering your country is *not* an outage — it's
   reported as a coverage note.
2. **Immediate warning.** The first failed poll after a good one emits:
   *"⚠️ Cannot reach NOAA — operating on last known data. Cause: connections
   timing out… I'll keep retrying every 10 min and build a worst-case outlook
   if this lasts 6 h."*
3. **Persistence.** Every poll — good or bad — is written to
   `~/.batten/weather-log.md` with source timestamps. That log is the model's
   memory while offline.
4. **The 6-hour mark.** Still offline after `outage_worstcase_hours` (default
   6), Batten fires the **worst-case 48-hour outlook**: persistence
   extrapolation of the storm's last known track and intensity, deliberately
   pessimistic (+20 kt ≈ one Saffir-Simpson category, no weakening assumed),
   wind-field radii, track-uncertainty growth, rainfall bands by storm speed —
   every line labeled `ESTIMATE` with the timestamp of the last good data. The
   local model then turns it into a short, calm action plan. If the model is
   down too, the numbers go out as-is.
5. **Recovery.** When NOAA answers again you get *"✅ NOAA is back after X h
   offline"*, and the state machine resets.

You can run the engine any time: `batten worstcase --outage-hours 7 --narrate`.

## Commands

```
batten init        configure location (lat/lon or US ZIP), household, model, webhook
batten status      current picture: storms, alerts, connectivity, supplies, model
batten brief       one-shot briefing narrated by the local model
batten chat        REPL with the local model (works fully offline; /status /brief /worstcase /supplies)
batten watch       the watcher loop (also: --once for cron)
batten worstcase   worst-case 48h outlook on demand (--outage-hours N --narrate)
batten cone        download the active storm's full-resolution NHC forecast cone
batten supplies    household supply checklist (have/need/na/note <item>)
batten doctor      check config, NOAA reachability, NWS coverage, Ollama, webhook
batten selftest    17 offline unit tests
batten demo        replay a real storm lifecycle offline, outage included
```

Notifications go to the console always, and to a Discord webhook if you set
one (`batten init --webhook …`) — with retries, rate-limit handling, and a
console fallback, because a silently-dropped storm alert is worse than no
alert. Quiet hours (23:00–07:00 local) suppress routine updates; real triggers
break through.

## Data sources and honest limits

- **[NHC CurrentStorms.json](https://www.nhc.noaa.gov/cyclones/#current-activity)**
  — active tropical cyclones, Atlantic + Eastern/Central Pacific (this covers
  the Caribbean, Gulf, and Mexico coasts). Public advisory text is fetched and
  grepped for landfall statements. The full-res forecast cone is downloaded
  directly (NHC blocks bot user-agents on link previews, so Batten fetches the
  image itself).
- **[api.weather.gov](https://www.weather.gov/documentation/services-web-api)** —
  active alerts and point forecasts for **US states and territories only**.
  Outside that coverage Batten says so explicitly and leans on NHC products
  plus your local official channels. (It will not invent alerts.)
- **The worst-case engine is a planning aid, not a forecast.** It assumes the
  storm keeps its last heading and speed and gets one category stronger — a
  deliberately pessimistic persistence model with inflated error bars. It is
  wrong in the direction of caution. **Always obey official orders from your
  civil-protection/emergency-management authority; Batten never decides
  whether you evacuate.**

## How it's built

```
batten/
├── geo.py        great-circle math, Saffir-Simpson mapping (pure functions)
├── noaa.py       NHC + NWS clients, failure classification, cone fetch, geocoding
├── state.py      connectivity state machine, storm snapshots, weather log
├── worstcase.py  the 48h offline extrapolation engine (documented assumptions)
├── supplies.py   ready.gov-derived checklist, household scaling
├── brain.py      Ollama client (streaming), system prompt, status snapshots
├── watcher.py    poll loop: triggers, adaptive cadence, quiet hours, outage fires
├── notify.py     console + Discord webhook (retry/backoff/429/chunking)
├── selftest.py   offline test battery
└── cli.py        the batten command
```

- **Zero runtime dependencies** — Python ≥ 3.10 stdlib only. The Ollama
  client is ~40 lines of `urllib`.
- Injectable clock/fetcher/transport everywhere, which is how `demo` and
  `selftest` exercise outages and trigger storms without touching the network.
- Suggested systemd unit (runs as your user, survives logins):

```ini
# ~/.config/systemd/user/batten.service
[Unit]
Description=Batten severe weather watcher

[Service]
ExecStart=%h/.local/bin/batten watch
Restart=on-failure

[Install]
WantedBy=default.target
```

`systemctl --user enable --now batten.timer` with an `OnCalendar=*:0/30` unit
works too — `batten watch --once` is cron-safe either way.

## Development

```bash
python3 -m batten selftest     # 17 tests, no network required
python3 -m batten demo         # end-to-end storm replay
BATTON_NO_MODEL=1 batten chat  # force the deterministic fallback brain
BATTON_HOME=/tmp/bt batten ... # isolate state anywhere
```

Contributions welcome — good first issues: more basin coverage (JTWC/CMC
products), international alert sources, a local web UI, voice announcements,
more worst-case doctrines (ensemble spread, surge heuristics by coast).

## License

MIT. Data by NOAA/NWS (US public domain). Not affiliated with NOAA.

*For the two that came through while this was being written — and for whoever
reads their storm log by flashlight.*
