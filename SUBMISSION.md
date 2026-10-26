<!--
DEV.TO SUBMISSION DRAFT — Batten (Hacktoberfest Open-Source AI Challenge, Week 3)

Before publishing, replace these placeholders:
  2. "published:" front-matter -> true when you publish on dev.to
  3. "## My Agent Session" — save your session with DevRelay and embed it
  4. "## Prize Categories" — check the challenge page's partner categories
Screenshots below are hotlinked from the repo (raw.githubusercontent.com, branch: main) — nothing hosted twice.
-->

---

title: I rode out two hurricanes with a Discord bot. Here's the open-source, offline-first version anyone can run.
published: false
tags: devchallenge, hf26challenge, python, ai

---

_This is a submission for the [Hacktoberfest Open-Source AI Challenge Week 3: Slow and Steady](https://dev.to/challenges/hacktoberfest-week3-2026-10-19)_

## What I Built

Earlier this month I closed on a house in coastal Mexico, got hit by Hurricane Simon before I'd even unpacked, and rode out Hurricane Trudy the following weekend. Between board-up runs I ran a little Discord bot that watched the National Hurricane Center for me every two hours, logged every advisory, and screamed only when something actually mattered. It worked. The house is fine, and I never opened a cone map once.

**Batten** (_verb_: to batten down the hatches) is that bot rebuilt for everyone else — generalized, hardened, and with an open-weight model at its core:

- **Watches NOAA for you.** NHC storm trackers for your part of the world, NWS alerts for your exact coordinates (US), configurable alert feeds for anywhere else. It fires on the things that matter — new storms, category changes, rapid intensification, landfall statements, proximity crossings at 300/150 nm — and stays quiet otherwise. Routine updates respect quiet hours; real triggers break through.
- **Keeps working when the internet dies.** This is the whole point. The first failed check tells you _what broke_ ("DNS failing — internet down or captive portal" vs "connections timing out — weak signal"). It keeps retrying in the background, and if NOAA stays unreachable for 6 hours it builds a **worst-case 48-hour outlook** from the last good advisory — persistence track extrapolation, one category of assumed intensification, wind-field arrival times, rainfall bands — every line labeled ESTIMATE, timestamped to the last good data.
- **Talks to you through a local model.** Chat, briefings, and the worst-case action plan run on an open-weight model via [Ollama](https://ollama.com) on your own machine. When a storm takes your internet down — the exact moment you want a weather assistant — the AI is the part that still works.
- **Three ways to see it:** a plain CLI, a cross-platform **TUI** (PC/Mac/Linux), and a **local web dashboard** served on your LAN — open it on your phone while you're boarding up the windows, no cloud involved.
- **Tracks your supplies** against a ready.gov-derived checklist that scales to your household, so "are we ready?" is one command, not a 2 a.m. inventory.
- **Speaks metric and imperial**, takes your location as GPS coordinates or ZIP code, and frames everything relative to _your_ position: how far, which direction, and whether the storm is headed toward you or moving away.

Who gets to be lazier: anyone in a storm's path who currently has twelve browser tabs of cone maps open and a not-app storm checklist in their head. You set Batten up once; it does the watching, the remembering, and the "what does this advisory actually mean for my address" translation.

## Demo

Batten is deliberately local (that's the thesis — it has to work when the internet doesn't), so instead of a hosted demo the repo ships a **fully offline storm replay**:

```bash
git clone https://github.com/JamesDBartlett3/Batten && cd Batten
python3 -m batten demo
```

This replays a real hurricane's full lifecycle — advisories transcribed from an actual storm watcher's log — including a simulated internet outage. You'll watch it detect the storm, fire category-change and rapid-intensification triggers, warn "cannot reach NOAA" when the outage hits, and at the six-hour mark produce the worst-case 48-hour outlook from stale data. No network needed, runs in about fifteen seconds.

Screenshots (from the repo's `screenshots/` folder):

![TUI dashboard](https://raw.githubusercontent.com/JamesDBartlett3/Batten/main/screenshots/tui-dashboard.png)
![TUI dashboard, 6 hours into a simulated outage](https://raw.githubusercontent.com/JamesDBartlett3/Batten/main/screenshots/tui-dashboard-outage.png)
![Worst-case outlook from stale data](https://raw.githubusercontent.com/JamesDBartlett3/Batten/main/screenshots/worstcase.png)
![Chat on the local model](https://raw.githubusercontent.com/JamesDBartlett3/Batten/main/screenshots/chat.png)
![LAN dashboard on a phone](https://raw.githubusercontent.com/JamesDBartlett3/Batten/main/screenshots/web-phone.png)
![Supply readiness](https://raw.githubusercontent.com/JamesDBartlett3/Batten/main/screenshots/tui-supplies.png)

## Code

Repo: **https://github.com/JamesDBartlett3/Batten**

The entire thing is **Python stdlib — zero pip dependencies** (~4k lines, 155 offline tests):

- `noaa.py` + `providers/` — NHC trackers, and a pluggable alert-provider layer: built-ins for NWS, CAP 1.2 feeds (the format most national weather services publish), and a generic JSON endpoint with field mapping, so you can point it at any alert service with a connection string
- `worstcase.py` — the offline 48-hour extrapolation engine with documented, deliberately pessimistic assumptions
- `brain.py` — the Ollama client (plain `urllib`, ~40 lines) and the prompts
- `watcher.py` — the trigger state machine, adaptive cadence, quiet hours, outage fires
- `tui.py` — ANSI terminal dashboard; `web.py` — stdlib HTTP server + mobile-first single-page dashboard for the LAN
- `tests/` — a full offline suite; nothing in CI touches the network

## How I Built It

**The open-source AI core:** an open-weight model (default `llama3.2:3b`, any Ollama-pulled model works) doing local inference for everything conversational — chat, briefings, and narrating the worst-case plan. It talks to Ollama over plain HTTP; no SDKs, no accounts, no API keys.

The design rule that shaped everything else: **the model is never a single point of failure.** The safety loop — trigger detection, connectivity monitoring, worst-case math — is deterministic Python. The model turns numbers into judgment; if the model is cold or the laptop is struggling, the numbers still get delivered. The model gets _only_ data Batten fetched or computed, with source timestamps, so it can't hallucinate an advisory that doesn't exist. Data in, judgment out, all on one machine.

**The data half is open too:** NOAA/NHC/NWS are public-domain open data. The alert-provider layer means the same binary works in Mexico on a CAP feed or against your municipality's JSON endpoint, not just US NWS.

## Why Does Open Innovation Matter?

Because the failure mode this tool exists for is _the internet being gone_.

- **A closed API assistant goes dark exactly when you need it most.** Storm knocks out connectivity — a cloud-model chat is unreachable, and your conversation history is on someone else's server. Batten's model runs on the laptop next to the flashlights. The chat that answered "should I cover the windows now?" at noon answers it at hour six of the outage, working from the last good advisory and its own log.
- **Your data stays yours.** Coordinates, household size, supply list, and every anxious 3 a.m. question live in `~/.batten/`. There is no telemetry, because there's nowhere to send it.
- **Cost nothing, forever.** No subscription, no per-query meter, no tier where alerts become a paid feature. Set it up once; it runs on a decade-old laptop.
- **Inspectable and swappable.** Worst-case doctrine too conservative for your coast? It's a documented module with the assumptions written at the top. Prefer Qwen to Llama? One flag. Try asking a closed weather assistant to change its forecasting philosophy.
- **Open compounds.** The cone-image download workaround, the trigger thresholds, the quiet-hours rules — all of that came from an open-source storm bot's fix history, and now it's in a generalized tool anyone can fork for their own coast.

## My Agent Session

<!-- Optional, but judges love it: save your ZCode/DevRelay session and embed it here with the agent_session tag (see the challenge page), or paste the share link. This project was built in a long agentic session: history review of the original storm bot, live NOAA schema probing, the full build, an offline test suite, and screenshot generation. -->

## The Lazy Payoff

During the real week this tool is based on, the storm it watched meandered offshore for **five days**. Manually checking the NHC that responsibly means roughly 60+ cone-map refreshes across a work week while also doing actual storm prep — and the checks you skip at 4 a.m. are exactly the ones where rapid intensification happens.

Batten's deal: it checks on cadence (tightening to 30 minutes when things get close), pages you only on triggers that matter, and _stops nagging_ during quiet hours. You get pinged four or five times across a five-day event — new storm, category change, "it's within 300 nm, finish outdoor prep," "it's within 150 nm, go inside" — instead of refreshing a map sixty times. The supply list is built once and re-scores itself every season. And the worst-case outlook means an internet outage during the storm stops being "am I blind now?" and becomes "here's the plausible worst case, here's what to finish first."

The original bot ran through the real Trudy week; zero house damage, and its human spent the whole storm swinging a hammer instead of watching maps. That's the lazy payoff: the bot does the watching so you can do the boarding.

## Prize Categories

<!-- Check the challenge page for the current partner categories and list every one that applies, e.g.:
- <Partner category 1>
- <Partner category 2>
-->

<!-- Thanks for participating! -->
