<div align="center">

# J.A.R.V.I.S.

**Personal voice assistant with memory, smart home control and an HUD — powered by Claude Code**

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows%2011-0078D6?logo=windows&logoColor=white)
![Claude Code](https://img.shields.io/badge/Brain-Claude%20Code%20CLI-D97757)
![Home Assistant](https://img.shields.io/badge/Smart%20Home-Home%20Assistant-41BDF5?logo=homeassistant&logoColor=white)
![Local first](https://img.shields.io/badge/Voice-local%20(Whisper%20%2B%20GPU--TTS)-2ea44f)

</div>

---

JARVIS is a self-hosted voice assistant in the style of the Iron Man computer. You talk to it hands-free in the browser, it answers with a natural voice, controls the smart home, knows your calendar, inbox and home network, and builds up a **persistent memory** in an Obsidian vault.

The language model is the **Claude Code CLI** on a subscription, so there is no per-token API billing. To make the quota last, a **local intent router** answers simple commands without any LLM at all. Everything else runs as a **lean, stateless call** with its own short prompt and only the tools the request actually needs.

> **Language:** Jarvis listens and speaks **German**. The router patterns, time parsing and spoken replies are German, so the example commands below are German with an English translation. Code, comments and docs are English.

## Contents

- [Highlights](#highlights)
- [What it can do](#what-it-can-do)
- [Architecture](#architecture)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Usage](#usage)
- [Speech output: provider chain](#speech-output-provider-chain)
- [Fallback mode: stand-in chain](#fallback-mode-stand-in-chain)
- [Resources, context, tasks and memory](#resources-context-tasks-and-memory)
- [Control, personality and everyday](#control-personality-and-everyday)
- [Learning modules](#learning-modules)
- [Efficiency: router and lean mode](#efficiency-router-and-lean-mode)
- [Memory](#memory)
- [On the go: iPhone and iPad](#on-the-go-iphone-and-ipad)
- [Security model](#security-model)
- [Project structure](#project-structure)
- [Development and diagnostics](#development-and-diagnostics)
- [Roadmap](#roadmap)

---

## Highlights

| | |
|---|---|
| 🎙️ **Hands-free** | Live microphone with pause and end-of-sentence detection, push-to-talk, barge-in. Optional wake word ("Jarvis …" or locally via openWakeWord) and follow-up questions without addressing it again. |
| ⏰ **Timers, alarms, reminders** | "Timer 10 minutes for the pasta", "Wake me tomorrow at 6:30", "Remind me about the laundry in 20 minutes", "Turn the light off in an hour". Local, no tokens, survives restarts. |
| 📣 **Speaks up on its own** | Washing machine done, appointment in 15 minutes or "time to leave" (with travel time), parcel arriving today, storage full, NAS problem, new device on the Wi-Fi. With quiet hours. |
| 📦 **Commute and parcels** | "When do I have to leave?" (travel time to the next appointment via OpenStreetMap), "Is a parcel coming today?" (from shipping mails). Local, no tokens. |
| 🖥️ **PC control** | "Volume to 30 percent", "Mute", "Open Spotify", "Lock the screen". Spotify ducks while Jarvis talks; at night it speaks more quietly. |
| 📉 **Gets cheaper over time** | Remembers phrasings Claude had to resolve once ("reading lamp off") and handles them itself afterwards. Identical questions within 5 minutes come from a cache. A "usage report" says how much ran locally. |
| 🗣️ **Pronunciation** | NAS, Pi, kWh & co. are spoken correctly; add your own words by voice ("pronounce Tuya as Tuja"). |
| 🛒 **Lists and routines** | Shopping and to-do list by voice (Home Assistant or a vault note). Routines like "good night": one phrase, several actions, created by voice. |
| 🛡️ **Controlled and honest** | Rules enforced in code, not in the prompt; protection against instructions hidden in mails and web pages; audit log; emergency stop; traceable source of every memory; honest feedback when switching. |
| 🎭 **Personality** | Standard, professional, technical, casual by voice; typed questions in detail, spoken ones short. |
| 🪟 **Windows** | Arrange windows, workspaces, files and clipboard, screen text via local OCR, allow-listed scripts. |
| 🎓 **Learns** | Rates its own conversations, proposes fixed rules on Sundays, a weekly review with patterns from sleep, steps, stress and golf, reports homelab outages only when relevant, suggests a new ability twice a week. No work topics, changes only by card. |
| 🛟 **Fallback mode** | If Claude is down or the limit is reached, a free chain takes over: Groq → Gemini → Ollama, with the same tools and confirmation cards. |
| 🧠 **Real memory** | Notes by topic, automatic conversation log, remembers and forgets on its own (expiry dates, trash). |
| ⚡ **Local intent router** | Lights, sockets, music, time, weather, energy and system status without an LLM, answered in milliseconds. |
| 🪶 **Lean LLM calls** | Own prompt instead of the Claude Code default, tools on demand, automatic model choice (Haiku/Sonnet). Measured at **more than 25× fewer tokens** than a default call. |
| 🔊 **Fail-safe voice** | A chain of cloud, local GPU and free voices. Exhausted providers are skipped until their reset, without an audible break. |
| 🏠 **Smart home** | Home Assistant: switching, reading states, energy (solar/battery), room climate, air quality. Optionally the full HA MCP server for automations and history. |
| 🛰️ **Home network** | Status of a Raspberry Pi and a Synology NAS, device list, watcher for new devices on the Wi-Fi. |
| 📅 **Everyday** | Calendar (CalDAV/iCloud), inbox (IMAP, read-only), Garmin health data, Spotify, web search. |
| 🖥️ **HUD** | Dashboard with weather and activity traffic light, home, health, systems, mail, 3D knowledge graph and usage meter. |
| 🔐 **Under control** | `localhost` only, confirmation cards for system changes, protected devices, no self-modification at runtime. |

---

## What it can do

The architecture separates the **router** (rules), **tools** (MCP server) and **memory** (Markdown), so new abilities usually take only a few lines.

**Everyday**
- *„Guten Morgen“* (good morning) gives a daily briefing with weather, appointments, energy, nearly empty batteries and new mail.
- *„Kann ich heute noch eine Runde laufen?“* (can I still go for a run today?) gives a traffic light from weather, wind, daylight and working hours.
- *„Was steht morgen an?“* (what's on tomorrow?) or *„Trag mir Freitag um zehn den Zahnarzt ein“* (add the dentist on Friday at ten — with confirmation).
- *„Hab ich wichtige Mails?“* (any important mail?) gives a summary without newsletters.
- *„Merk dir, dass Anna am 3. Mai Geburtstag hat“* (remember that Anna's birthday is on May 3rd) creates a note under `Personen/Anna`.
- *„Timer 8 Minuten für die Pizza“*, *„Wie lange noch?“*, *„Weck mich um 6:45“*, *„Schlummern“* (snooze), *„Wecker aus“* (alarm off)
- *„Erinnere mich morgen um 9 daran, Anna anzurufen“* (remind me to call Anna tomorrow at 9), *„Welche Erinnerungen habe ich?“*
- *„Setz Milch und Eier auf die Einkaufsliste“* (put milk and eggs on the shopping list), *„Was steht auf der Einkaufsliste?“*
- *„Wenn ich Gute Nacht sage, mach alle Lichter aus und sag mir, wann der Wecker klingelt“* (when I say good night, turn off all lights and tell me when the alarm rings) creates a routine. After that, *„Gute Nacht“* runs without an LLM.

**Home**
- *„Mach das Licht im Wohnzimmer auf 40 Prozent“* (living room light to 40 percent), *„Ist die Waschmaschine noch an?“*, *„Szene Kino“* (cinema scene)
- *„Wie viel liefert die Solaranlage, und wie voll ist der Speicher?“* (solar output and battery level)
- *„Welche Batterien muss ich tauschen?“* (which batteries need replacing?)
- With the HA MCP server: *„Warum ist es im Schlafzimmer so kalt? Zeig mir den Verlauf.“* (why is the bedroom so cold? show me the history)

**Tech and network**
- *„Wie geht es dem Pi / dem NAS?“* (how is the Pi / the NAS?) reports temperature, storage and uptime, plus disk health with a warning from 85 %.
- *„Ist ein unbekanntes Gerät im WLAN?“* (unknown device on the Wi-Fi?) The watcher also reports new devices on its own.
- *„Starte die Container auf dem Pi neu“* (restart the containers on the Pi — SSH, with confirmation).

**Ideas for extensions** (see [Roadmap](#roadmap))
- Focus sessions with distraction hints, camera or screen context
- Room microphones (ESP32/satellites), several user profiles via speaker recognition
- More tool groups: Paperless, Jellyfin/Plex, Proxmox, UniFi, vehicle APIs, fitness platforms
- Your own voice from a voice sample (local), more languages

---

## Architecture

```mermaid
flowchart LR
    subgraph Browser["Browser (HUD)"]
        MIC[Microphone + VAD] --> STT
        SPK[Playback sentence by sentence]
    end
    subgraph Server["server.py (127.0.0.1)"]
        STT["/api/listen<br/>local Whisper"] --> RUN["/api/run"]
        RUN --> R{"Intent router<br/>router.py"}
        R -->|simple command| LOCAL[Code handler<br/>0 tokens]
        R -->|real question| LEAN["Lean Claude call<br/>runtime.py"]
        LEAN <--> MCP["Tools<br/>jarvis_mcp.py"]
        SPEAK["/api/speak<br/>tts_chain.py"]
    end
    MCP --> HA[(Home Assistant)]
    MCP --> NET[(Pi · NAS · network)]
    MCP --> PIM[(Calendar · Mail · Garmin)]
    MCP --> VAULT[(Obsidian vault)]
    LOCAL --> SPEAK
    LEAN --> SPEAK
    SPEAK --> SPK
```

| Component | Role |
|---|---|
| `server.py` | HTTP server (`127.0.0.1` only), HUD, API endpoints, confirmation cards, usage log, self-restart on code changes |
| `router.py` | Intent router: local handlers, choice of tool groups and model |
| `runtime.py` | Starts `claude -p` lean (`--system-prompt`, `--tools`, `--strict-mcp-config`, `--setting-sources ""`, `--no-session-persistence`, `--effort`) with a pre-warmed process pool |
| `commands.py` | Prompts, conversation window, state file, slash commands |
| `jarvis_mcp.py` | MCP server with all tools in groups, confirmation gate, result size cap |
| `memory.py` | Vault → knowledge graph, core memories, log, cleanup |
| `voice.py` · `tts_chain.py` | Speech recognition and the speech output chain with adapters |
| `local_tts_server.py` | Local GPU voice (Chatterbox Multilingual) in its own environment |
| `briefing.py` · `dashboard.py` | Daily briefing, network watcher, HUD data |
| `icloud_calendar.py` · `billing.py` | CalDAV calendar, API credit (Cost API) |
| `jarvis_profile.py` | Profile and device mapping from `.env` and `config/home.json` |
| `scheduler.py` | Timers, alarms, reminders and scheduled commands: parses German time expressions, rings |
| `lists.py` · `routines.py` | Shopping/to-do list (HA to-do or vault note), routines from `config/routines.json` |
| `events.py` · `watchers.py` | Push channel to the HUD (server-sent events, quiet hours) and the rules for proactive messages |
| `wakeword.py` | Wake word: text detection or openWakeWord on the PC microphone |
| `fallback_llm.py` | Fallback mode: stand-in chain Groq → Gemini → Ollama with the Jarvis tools |
| `learning.py` · `answer_cache.py` | Learned phrasings (`config/learned.json`), usage report, answer cache |
| `commute.py` · `parcels.py` | Travel time to appointments (Nominatim + OSRM), parcel status from shipping mails |
| `pc.py` · `pronounce.py` | Volume, apps, screen lock, Spotify ducking (pycaw); pronunciation lexicon |
| `ui/` | HUD (vanilla JS, dependency-free 3D knowledge graph) |

---

## Quick start

**Requirements:** Windows 11 · Python 3.12 · Node.js · a Claude subscription. Optional: NVIDIA GPU (CUDA 12.8) for Whisper `large-v3-turbo` and the local voice.

```powershell
# 1  Install Claude Code and sign in (subscription, no API key)
npm.cmd install -g @anthropic-ai/claude-code
claude auth login

# 2  Python environment
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 3  Configuration
copy .env.example .env                           # fill in your values
copy config\home.example.json config\home.json   # HA entity IDs (optional)
mkdir vault\Erinnerungen

# 4  Start
start-jarvis.bat
```

**Local voices (optional)**

```powershell
# Piper (CPU, works right away)
mkdir models\piper
curl -L -o models\piper\de_DE-thorsten-high.onnx      https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/de/de_DE/thorsten/high/de_DE-thorsten-high.onnx
curl -L -o models\piper\de_DE-thorsten-high.onnx.json https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/de/de_DE/thorsten/high/de_DE-thorsten-high.onnx.json

# Chatterbox Multilingual (GPU, ~5 GB) — details in requirements-tts.txt
py -3.12 -m venv .venv-tts
.venv-tts\Scripts\python.exe -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
.venv-tts\Scripts\python.exe -m pip install --no-deps chatterbox-tts
.venv-tts\Scripts\python.exe -m pip install -r requirements-tts.txt
# Voice sample (~10 s, one you have the rights to): models\voice\reference.wav
```

The server starts the GPU voice automatically as soon as `.venv-tts` exists.

---

## Configuration

Every setting is documented in [`.env.example`](.env.example). Personal data belongs only in `.env` and `config/home.json`; both are in `.gitignore`.

| Area | Variables | Purpose |
|---|---|---|
| Profile | `JARVIS_USER_NAME`, `JARVIS_PLACE`, `JARVIS_WEATHER_LAT/LON`, `JARVIS_TIMEZONE`, `JARVIS_BROWSER` | Form of address, location, browser |
| Brain | `JARVIS_LEAN`, `JARVIS_LOCAL_ROUTER`, `JARVIS_MODEL_SIMPLE/SMART`, `JARVIS_EFFORT`, `JARVIS_MAX_OUTPUT_TOKENS`, `JARVIS_HISTORY_TURNS` | Router, model choice, context size |
| Voice | `TTS_CHAIN`, `TTS_ANNOUNCE_SWITCH`, `FISH_AUDIO_*`, `ELEVENLABS_*`, `AZURE_*`, `GOOGLE_TTS_*`, `EDGE_*`, `LOCAL_TTS_URL` | Provider order and credentials |
| Listening | `JARVIS_STT`, `WHISPER_LOCAL_*` | Speech recognition |
| Wake word | `JARVIS_WAKE` (`off`/`text`/`oww`), `JARVIS_WAKE_*`, `JARVIS_FOLLOWUP_S` | "Jarvis …" instead of an always-open mic, follow-up window |
| Proactive | `JARVIS_PROACTIVE`, `JARVIS_QUIET_HOURS`, `JARVIS_EVENT_LEAD_MIN`, `JARVIS_STORAGE_FULL` | Messages and quiet hours (alarms always ring) |
| Lists | `JARVIS_SHOPPING_LIST`, `JARVIS_TODO_LIST` | HA to-do entity; empty = a note under `vault/Erinnerungen/Listen/` |
| Fallback | `JARVIS_FALLBACK` (`groq,gemini,ollama`), `GROQ_API_KEY`, `GROQ_MODEL`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `OLLAMA_URL`, `OLLAMA_MODEL`, `JARVIS_FALLBACK_PRIVATE` | Stand-in chain when Claude is unavailable; see [Fallback mode](#fallback-mode-stand-in-chain) |
| Saving | `JARVIS_CACHE_S` (default 300, 0 = off) | Repeated read-only questions come from the cache for this long |
| Commute | `JARVIS_HOME_LAT/LON` (else the weather coordinates), `JARVIS_TRAFFIC_FACTOR` (1.25), `JARVIS_COMMUTE_BUFFER_MIN` (5), `JARVIS_LEAVE_WARN_MIN` (5) | Starting point, traffic surcharge, buffer, advance warning |
| PC and volume | `JARVIS_DUCK` (0.3, 0 = off), `JARVIS_DUCK_APPS` (spotify.exe), `JARVIS_NIGHT_VOLUME` (0.5) | Music quieter while speaking, voice volume in quiet hours; your own apps in `config/apps.json` |
| Pronunciation | `config/aussprache.json` | Your own entries, created by voice |
| Services | `ICLOUD_*`, `MAIL_*`, `HA_*`, `SYNOLOGY_*`, `JARVIS_PI_*`, `GARMIN_*` | Integrations; each one is optional |
| Devices | `config/home.json` | Maps dashboard fields (solar, battery, rooms, router, air) to Home Assistant entities, plus `appliances` (power meters for "… is done") and `device_aliases` (extra words per device, e.g. "Wohnzimmer" and "Lampe" for a light Home Assistant only calls "LED"; picked up without a restart within 5 minutes) |
| Routines | `config/routines.json` | Created by voice; format in `config/routines.example.json` |

> **Important:** never set `ANTHROPIC_API_KEY`. Claude Code would then bill through the API instead of the subscription. The server removes the variable from the child processes' environment as a precaution.

---

## Usage

| Action | How |
|---|---|
| Live mode | Click **Live**. The button shows the state: listening, hearing you, thinking, speaking, microphone blocked. With a wake word it waits for "Jarvis" and listens briefly after "Jarvis" and after each answer. |
| Timers and alarms | Countdown in the top bar. It rings, then speaks. Talking, `Esc` or **Stop** ends the ringing; *„Schlummern“* snoozes for 9 minutes. |
| Messages | Appear with a yellow border in the conversation and are read aloud; during quiet hours they are only shown. |
| Push-to-talk | Hold the Live button, speak, release. |
| Interrupt | Talk over it, or `Esc` / space bar |
| Confirm | Click the card or say *„bestätigt“* (confirmed) / *„nein“*. A plain *„ja“* deliberately does not count. |
| Typing | Input field, `Enter` |
| Commands | `/new`, `/status`, `/recall <term>`, `/graph`, `/help` |
| API credit | ✎ in the usage panel: enter your balance; spending is deducted via the Cost API |

---

## Speech output: provider chain

```
Fish Audio ─▶ ElevenLabs ─▶ Local (Chatterbox GPU · Piper CPU) ─▶ Azure ─▶ Google ─▶ Edge
```

- **One interface:** every provider is an adapter with `configured()`, `synth()` and `balance()`. The order is set in `TTS_CHAIN`.
- **Exhausted** (402, quota/credit errors, invalid key, balance API reports 0): the provider is blocked until its reset, and that survives restarts. The reset is the date the API reports, the start of the month for free tiers, 6 h for an invalid key, otherwise 24 h. After that it is back in its place automatically.
- **Temporary** (network, timeout, 5xx): only the current sentence switches provider and is regenerated in full. There is no audible break.
- **Transparency:** the dashboard shows the active provider with remaining credit and the blocked providers with their dates. An optional short announcement marks a switch.
- **Tone tags** like `[dry]` are applied by providers that support them and stripped for all others.
- The chain is plain code; it uses no LLM tokens.

---

## Fallback mode: stand-in chain

If the Claude quota is exhausted, the CLI does not start, or a call fails before a single word was said, the first available stage of this chain answers:

| Stage | Model | Cost | Note |
|---|---|---|---|
| **Groq** | gpt-oss-120b (`GROQ_MODEL`) | free tier, key at console.groq.com | very fast, data not used for training |
| **Gemini** | Gemini 3.5 Flash (`GEMINI_MODEL`) | free tier, key at aistudio.google.com | Google may use free-tier requests for training, so it **never** gets mail, calendar, health or the daily briefing |
| **Ollama** | `qwen2.5:7b-instruct`, local | free, offline | last resort |

- The stand-ins get **the same Jarvis tools** as Claude (the groups the router picks). `jarvis_mcp.py` runs as a child process for this, and whatever needs a confirmation card with Claude (e.g. `calendar_add`) needs one here too. Only `pc` (PowerShell) and the full HA MCP server are not available.
- If a provider reports its own limit (HTTP 429), it is skipped until the retry time it gives; an invalid key or a model that is no longer offered blocks it for six hours (reason shown in the tooltip).
- After read-only tools the next provider may take over; once a provider has **changed** something (switching, saving, calendar), no second one takes over, so nothing happens twice.
- Providers without a key are simply skipped. `JARVIS_FALLBACK=ollama` restores the old local-only behaviour, `off` disables fallback mode.
- `JARVIS_FALLBACK_PRIVATE` (default `groq,ollama`) sets which providers may see private requests.
- The usage log records the answers as `fallback:groq`, `fallback:gemini` or `fallback:ollama`.

```
GROQ_API_KEY=...
GEMINI_API_KEY=...
```

---

## Resources, context, tasks and memory

More layers on the existing architecture. All run without an LLM unless noted, and each can be switched off.

| Module | File | What it does | Say |
|---|---|---|---|
| **Resource manager** | `resources.py` | Measures VRAM, RAM, Ollama, the GPU voice and Whisper from real data and keeps a reserve free (`JARVIS_VRAM_RESERVE_MB`). Under pressure it gives memory back, only when idle: unload Ollama, then pause the GPU voice (Piper speaks), then move Whisper to the CPU — and takes it back later. Never interrupts a conversation. | "How full is the GPU?" · HUD row "GPU-Budget" |
| **Skill factory** | `skill_factory.py` | Learned wordings are **drafts**; from the 2nd repetition they become a proposal, activated only after checks (device exists and is switchable, no clash with routines) and a confirmation card. Every version is kept and can be undone. | „Was hast du gelernt?“, „Nimm die Regel … zurück“ |
| **Context engine** | `context.py` | Asks time, calendar, weather, timers, due plan steps, warnings, PC and home in parallel with time limits and a cache; stale or missing sources are marked. Every LLM request gets a short "situation". | „Wie sieht mein Tag aus?“ (local) |
| **Task engine** | `tasks.py` | Checks in steps (NAS, Pi, network, PC, smart home, everything), each with time limit, retry and its own result check; cancel any time; report with a recovery plan. Never repairs anything itself. | „Prüf mein NAS“, „Systemcheck“ |
| **Memory intelligence** | `memory_intel.py` | Duplicates are not saved; a contradiction (another home town, another date) is **never** overwritten — Jarvis asks. Names of people, projects and devices become graph links. Source "vermutet" (assumption) and an expiry date per entry. | „Prüf mein Gedächtnis“ |
| **Monitoring** | `monitoring.py` | Storage forecast for NAS, system disk and Pi SD card with data basis and confidence; recurring outages named; the same notice from several sources merged; at most 6 spoken notices per hour. | „Wann ist das NAS voll?“ |
| **Document search** | `docsearch.py` | Local full-text index (SQLite FTS5) of the shared folders and `JARVIS_DOC_ROOTS`, with file and position (page for PDF, via `pypdf`). | "What does the manual say about …?" |
| **Vision** | `vision.py` | Describes the screen with Claude when local text recognition is not enough (layout, where a button is). Only on request, a **card for every picture**, blocked for password, banking and chat windows; the picture goes to Claude without any tools and is deleted afterwards. Off without Claude. | "Where is the setting in this window?" |

| Variable | Default | Purpose |
|---|---|---|
| `JARVIS_RESOURCES`, `JARVIS_VRAM_RESERVE_MB`, `JARVIS_VRAM_HYSTERESIS_MB` | `1`, `1500`, `1000` | Resource manager, free reserve, margin before taking back |
| `JARVIS_TTS_VRAM_MB`, `JARVIS_WHISPER_VRAM_MB` | `4000`, `1800` | Needs of the GPU voice and Whisper (settings, not measurements) |
| `JARVIS_OLLAMA_IDLE_S`, `JARVIS_OLLAMA_SMALL_MODEL` | `180`, empty | Unload Ollama when idle; smaller model when memory is short |
| `JARVIS_SKILL_MIN_SEEN` | `2` | Repetitions before a draft is proposed |
| `JARVIS_CONTEXT_TIMEOUT` | `4` | Time limit per context source in seconds |
| `JARVIS_TASK_STEP_TIMEOUT`, `JARVIS_TASK_MAX_RUNNING` | `45`, `2` | Time limit per check step, parallel checks |
| `JARVIS_FORECAST_WINDOW`, `JARVIS_FORECAST_WARN_DAYS` | `30`, `60` | Forecast window, warning threshold in days |
| `JARVIS_DEDUP_SECONDS`, `JARVIS_SAY_PER_HOUR` | `1800`, `6` | Merge identical notices, spoken notices per hour |
| `JARVIS_HOME_IGNORE` | inverters, internet-access switches | Entities the smart-home check skips (regex) |
| `JARVIS_DOCSEARCH`, `JARVIS_DOC_ROOTS`, `JARVIS_DOC_MAX_FILES`, `JARVIS_DOC_MAX_MB` | `1`, empty, `5000`, `20` | Document search, extra folders, limits |
| `JARVIS_VISION`, `JARVIS_VISION_MODEL`, `JARVIS_VISION_MAX_WIDTH` | `1`, `sonnet`, `1600` | Screen description on/off, model, max picture width |

The stand-in providers that may train on prompts (Gemini free tier) no longer see the user profile, name or memory notes at all.

---

## Control, personality and everyday

Six expansion packages, each with its own tests. The rules behind them are in [SECURITY.md](SECURITY.md), the structure in [ARCHITECTURE.md](ARCHITECTURE.md). Spoken commands are German; translations in brackets.

**Trust and safety**
- Every tool has a fixed class in code (`policy.py`): read, small change, card required, blocked. Locks, alarm systems and cameras are never switched.
- Mails, web pages, calendar entries, files, clipboard and screen text are **untrusted data**: wrapped as such, and afterwards every change in the same request needs a card.
- *„Was hast du heute gemacht?“* (what did you do today?) reads the audit log (`logs/audit.jsonl`).
- *„Jarvis, stopp alles“* / *„Not-Aus“* (emergency stop): open cards are denied, Claude is stopped, messages and planned commands pause for two hours; alarms still ring. *„Weitermachen“* resumes.
- Memory: every new entry carries its source and date (*gesagt* = said, *abgeleitet* = inferred). Plus "what do you know about me?", "why did you save that?", "correct …", "forget …" (with a card, goes to the trash) and "export my memory". A daily backup goes to `backups/`.
- After switching, Jarvis reads the state back and says honestly when a device did not react.

**Personality**
- *„Profi-Modus“* (professional), *„sei mal locker“* (casual), *„technischer Modus für zwei Stunden“* (technical, for two hours), *„zurück zum Normalen“* (back to standard), *„welcher Modus?“* — local, 0 tokens. Own profiles in `config/profiles.json`.
- In every profile: no jokes about outages or bad news, uncertainty is said openly.
- **Typed** questions get detailed, structured answers; only the first paragraph is read aloud.

**Windows and screen**
- *„Spotify nach rechts“*, *„Browser links und Code rechts auf Monitor 2“* — local. A workspace is a routine with steps like „Öffne Discord“ (open Discord) and „Discord nach rechts“.
- Search and read files only in Desktop, Documents and Downloads (`JARVIS_FILE_ROOTS`): text, code and Word, no work files.
- Clipboard and screen only on request; the screen text is recognised by the Windows OCR on the PC, the image never leaves it. Refused while a password manager, banking or messenger window is visible.
- Scripts only from `config/scripts.json`, always with a card.

**Infrastructure and smart home**
- PC status: CPU, RAM, GPU, VRAM (with the programs behind it), disks. Network diagnosis: router, internet, DNS, web — with a verdict where the problem is.
- Hyper Backup status from the Synology (the Jarvis DSM user needs the rights to see the tasks).
- "Are all sensors online?": unavailable devices, low batteries, silent sensors.
- The homelab observer also watches GPU temperature and the system disk, checks backups daily, keeps an incident history and pushes critical events to the phone (`JARVIS_PHONE_NOTIFY`).

**Organisation and research**
- Mail **drafts** in the mailbox's Drafts folder, with a card — nothing is ever sent.
- "What do I need to know for tomorrow?": appointments with travel and leave time and matching notes; work appointments only counted.
- Research mode with dated sources, contradictions named, assumptions marked; saved under `Recherchen/`.
- Watched topics: up to 5, at most 2 checks a day, Jarvis only speaks up when something is new.
- Plans over several days: `Projekte/<title>` with a checklist and a reminder per step.

**HUD**
- Shows who answered last and why (e.g. *Haiku · home · simple* or *lokal · Router, 0 Tokens*).
- A waveform of the real microphone or voice signal — no fake animation.
- 🔔 opens the notification centre: messages, homelab incidents, open proposals, emergency stop.
- **⛶ Zentrale**: full-screen command centre without input (second monitor, `?view=wall`); **▭ Mini**: conversation and voice only (`?view=mini`).
- A row for this PC under "Systeme".

| Variable | Default | Purpose |
|---|---|---|
| `JARVIS_PAUSE_HOURS` | `2` | Length of the emergency stop |
| `JARVIS_AUDIT_DAYS`, `JARVIS_BACKUP_KEEP` | `90`, `14` | Keep audit log and vault backups |
| `JARVIS_VERIFY_SECONDS` | `2.5` | How long Jarvis waits for the new device state |
| `JARVIS_FILE_ROOTS` | Desktop, Documents, Downloads | Shared folders, comma-separated |
| `JARVIS_SCREEN_BLOCK` | empty | More window titles that block screen reading |
| `JARVIS_PHONE_NOTIFY`, `JARVIS_PHONE_PUSH` | empty, `critical` | HA notify service for the phone; what is pushed (`critical`, `all`, `off`) |
| `JARVIS_WATCH_MAX`, `JARVIS_WATCH_PER_DAY` | `5`, `2` | Watched topics and checks per day |
| `JARVIS_PLAN_REMIND` | `09:00` | Time of plan reminders |

---

## Learning modules

Four background modules, each in its own file. For all of them:
- **No work topics:** anything that looks like work is not logged, evaluated or saved (`privacy.py`, own terms in `JARVIS_WORK_TERMS`) — this also covers the conversation log and `memory_save`.
- **Changes only with a card:** background jobs never change anything themselves. They store a proposal (`proposals.py`); in the next conversation Jarvis explains it in German, and only the confirmation card (`proposal_accept`) carries it out.

| Module | File | What it does | When |
|---|---|---|---|
| **Learning loop** | `reflection.py` | After each conversation rates what went well and what was corrected, in the note **Lernprotokoll**. Condenses the week into rules; confirmed rules live in **Vorlieben** under "Gelernte Regeln" and apply to every turn. "Streich die Regel …" removes one. | 30 min after the last exchange; rules Sundays from 19:00 |
| **Weekly review** | `weekly_review.py` | Fetches sleep, steps, stress, Body Battery and golf rounds from Garmin daily; Sunday review with last-week comparison and patterns computed in code (with day counts). Meal times, alcohol and coffee are noted when you say them. Note **Gesundheit/Wochenrückblick YYYY-KWnn**. | fetch from 9:00; review Sundays from 18:00 |
| **Homelab observer** | `homelab_watch.py` | Home Assistant, Pi (services, containers, temperature, SD card, load), NAS and important devices from `config/homelab.json`. Outages only after two checks, one message per incident plus recovery, only critical ones at night, fixed limits and — after a learning week — outliers from the normal range. 0 tokens. | every 5 minutes |
| **Suggestions** | `suggestions.py` | Suggests one new ability that fits the habits of the last two weeks, with a reason. Routines and reminders are set up after the card; anything needing code is only noted. List with status in the note **Vorschläge**. | Tuesday and Friday 18:30 |

Background jobs use Claude Haiku (`JARVIS_BACKGROUND_MODEL`) as their own short call without tools, outside the conversation lock; when Claude is at its limit only Groq or Ollama (never Gemini). Usage is logged as `background:…`, about 30–60k tokens a week.

| Variable | Default | Purpose |
|---|---|---|
| `JARVIS_WORK_TERMS` | empty | Your own work terms (employer, colleagues, customers, projects), comma-separated |
| `JARVIS_REFLECT`, `JARVIS_REVIEW`, `JARVIS_HOMELAB`, `JARVIS_SUGGEST` | `1` | Module on/off |
| `JARVIS_REFLECT_IDLE_MIN`, `JARVIS_REFLECT_WEEK_HOUR`, `JARVIS_MAX_RULES` | `30`, `19`, `20` | Learning loop |
| `JARVIS_REVIEW_HOUR`, `JARVIS_LATE_MEAL`, `JARVIS_PATTERN_DAYS` | `18`, `21:00`, `42` | Weekly review |
| `JARVIS_HOMELAB_SECONDS`, `JARVIS_HOMELAB_CONFIRM`, `JARVIS_HOMELAB_MAX_PER_DAY` | `300`, `2`, `2` | Observer; devices and limits in `config/homelab.json` |
| `JARVIS_SUGGEST_DAYS`, `JARVIS_SUGGEST_TIME` | `1,4`, `18:30` | Suggestions (Monday = 0) |
| `JARVIS_BACKGROUND_MODEL`, `JARVIS_PROPOSAL_DAYS` | `haiku`, `7` | Model of the background jobs, expiry of open proposals |

---

## Efficiency: router and lean mode

A default Claude Code call sends ~35,000 tokens of baseline with every request:

| Part | Tokens |
|---|---|
| Built-in tools | ~19,700 |
| Default prompt | ~3,400 |
| Connectors | ~2,000 |
| Project prompt | (varies) |

On top of that, every tool use adds an extra round trip.

**Measurement** (input tokens per request, `scripts/measure_tokens.py`):

| Request | Default | JARVIS | Path |
|---|---:|---:|---|
| Time | 34,575 | **0** | local |
| Energy status | 110,515 | **0** | local |
| Weather | 194,243 | **0** | local |
| Memory question | 34,999 | **3,981** | Haiku |
| Inbox | 110,851 | **9,591** | Haiku |
| Web research | 151,973 | **10,827** | Sonnet |

**Building blocks:**
- **Intent router** with rules instead of an LLM. Questions and commands are told apart; protected devices are never switched off automatically.
- **Own prompt** (~1,300 tokens): stable parts first so the prompt cache hits. Dynamic parts (time, last exchanges, matching notes) go into the message.
- **Tool groups** (`memory`, `web`, `home`, `mail`, `calendar`, `systems`, `health`, `music`, `pc`): only the matching ones are loaded per request, directly and without tool search.
- **Stateless:** no growing session history, just a sliding window of the last exchanges.
- **Result cap** per tool, Haiku/Sonnet selection, `--effort low`, capped answer length.
- **Background jobs without an LLM:** dashboard, network watcher, briefing facts.

Every request is logged in `logs/usage.jsonl` (route, model, groups, input/cache/output, API calls).

---

## Memory

```
vault/
├─ Erinnerungen/     profile · commands · preferences · websites · knowledge · network · smart home
│  ├─ Personen/      one note per person
│  ├─ Projekte/      one note per project
│  └─ Netzwerk-Geräte/   created automatically by the network watcher
├─ Gespraeche/       daily conversation logs (automatic)
└─ Papierkorb/       expired notes, deleted after 30 days
```

- **Obsidian-compatible:** front matter with `type`, `wichtigkeit` (importance) and optionally `ablauf` (expiry), links via `[[Wikilink]]`. This becomes the 3D graph in the HUD.
- **Targeted recall instead of full context:** only the profile and commands are in the prompt. Everything else Jarvis fetches with `memory_search` (weighted full text) and `memory_read`.
- **Remembers on its own** via `memory_save`, without you having to say "remember".
- **Forgets systematically:** expiry dates, then the trash, then deletion.

---

## On the go: iPhone and iPad

The PC stays the brain. iPhone and iPad connect over [Tailscale](https://tailscale.com) (private VPN, free for personal use) via HTTPS, from anywhere and without an open port on the router.

**One-time setup**

1. Install Tailscale on the PC and sign in, then the Tailscale app on the iPhone/iPad with the same account.
2. In the Tailscale admin console under *DNS*, enable "MagicDNS" and "HTTPS Certificates".
3. In `.env`: `JARVIS_REMOTE=1`, then restart Jarvis. Device access then runs on `127.0.0.1:8721` (`JARVIS_REMOTE_PORT`).
4. Once on the PC: `tailscale serve --bg 8721`. Tailscale shows the address, e.g. `https://my-pc.tailXXXX.ts.net`.

**Pair a device**

1. On the iPhone or iPad, open the address in Safari, then *Share → Add to Home Screen* (iPhone: share at the bottom, iPad: top right).
2. Open Jarvis from the home screen (the web app has its own sign-in, separate from Safari).
3. On the PC, in the HUD click **📱 Devices → New code** and enter the six-digit code on the device. It is valid for five minutes and only once.

**📱 Devices** lists all paired devices; ✕ locks one out immediately.

**On the phone:** tapping the mic button toggles Live, holding it is push-to-talk. The microphone never starts on its own, there is no daily briefing, and messages appear silently (the PC reads them out). The wake word only listens on the PC.

**On the iPad:** the full HUD with the memory graph, just with bigger buttons. Landscape uses three columns like the PC; portrait shows graph and conversation on top and the widgets in two columns below. One finger rotates the graph, two fingers zoom and pan, a tap selects a node. Usage and action log stay on the PC. Microphone, briefing and messages behave like on the phone.

---

## Security model

- **Local only:** the server binds to `127.0.0.1`. Every request needs a per-launch token; host and origin are checked.
- **Remote access only when paired:** the device port (`JARVIS_REMOTE=1`) also binds only to `127.0.0.1` and is reachable only via `tailscale serve`. Without a paired device it serves only the pairing page. A code is discarded after five failed attempts. `config/devices.json` stores only hashes of the device keys. Pairing, device list and the confirmation endpoint are reachable only on the PC. `JARVIS_REMOTE_HOSTS` optionally restricts the allowed host names (default: `*.ts.net`).
- **Confirmation cards:** system changes go through Claude Code's `--permission-prompt-tool` to a card in the HUD. That covers shell commands, calendar entries and writing outside the vault. No answer within 75 s means deny.
- **Protected devices:** the router never switches off the router, PC, server or fridge by itself. Home Assistant only switches `light`, `switch`, `fan`, `media_player` and `scene`.
- **Questions never switch:** "Is … on?" is read, never executed.
- **No self-modification:** at runtime there are no tools for editing code.
- **Action classes, untrusted content, audit log, emergency stop:** see [SECURITY.md](SECURITY.md).
- **Data stays local:** `.env`, `config/home.json`, `vault/`, `logs/`, `state.json` and models are in `.gitignore`. Speech recognition and the GPU voice run offline.

---

## Project structure

```
├─ server.py           entry point (supervisor + HTTP server), loads paths.py
├─ paths.py            makes the folders under jarvis/ importable (short import names stay)
├─ jarvis/
│  ├─ core/            router · runtime · commands · events · scheduler · background · jarvis_profile ·
│  │                   personality · context · tasks
│  ├─ safety/          policy · privacy · audit · emergency · proposals
│  ├─ tools/           jarvis_mcp (tool server) · desktop · infra · assistant · pc · lists · routines ·
│  │                   devices · dashboard · commute · parcels · icloud_calendar · billing
│  ├─ memory/          memory · memory_tools · memory_intel · learning · skill_factory · answer_cache ·
│  │                   reflection · docsearch
│  ├─ voice/           voice · tts_chain · local_tts_server · wakeword · pronounce · vision
│  ├─ llm/             fallback_llm · resources
│  └─ watch/           watchers · homelab_watch · monitoring · briefing · weekly_review · suggestions
├─ ui/                 index.html · app.js · graph.js · styles.css · pair.html
├─ scripts/            measure_tokens.py · lean_probe.py · mail_to_trash.py · router_report.py ·
│                      install-autostart.ps1 · ocr_screen.ps1
├─ tests/              one test module per area, isolate.py first (no house, Claude, network or sound needed)
├─ config/             *.example.json (your own files stay local)
├─ CLAUDE.md · ARCHITECTURE.md · SECURITY.md
├─ requirements.txt · requirements-tts.txt
└─ start-jarvis.bat
```

---

## Development and diagnostics

```powershell
# Tests: router, timers, lists, routines, fallback (Home Assistant is simulated, nothing is switched)
.venv\Scripts\python.exe -m unittest discover -s tests -v

# Token measurement: components, default requests, lean path
.venv\Scripts\python.exe scripts\measure_tokens.py components
.venv\Scripts\python.exe scripts\measure_tokens.py new

# What still goes to the LLM, what has the router learned?
.venv\Scripts\python.exe scripts\router_report.py 30

# Autostart at sign-in (Task Scheduler, no admin rights); -Remove removes it
powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1

# Test router + lean call without the server
.venv\Scripts\python.exe scripts\lean_probe.py "Wie geht es dem NAS?"

# Test instance next to the running one (own port, own state file)
set JARVIS_PORT=8721& set JARVIS_STATE=%TEMP%\state_test.json& .venv\Scripts\python.exe server.py
```

| Symptom | Cause / fix |
|---|---|
| Live button does nothing | `F5`. If "microphone blocked", allow the microphone in the address bar. |
| Cut off mid-sentence | Increase `SILENCE_MS` / `UNFINISHED_HOLD_MS` in `ui/app.js` |
| Interrupts itself | Increase `BARGE_FACTOR` or use headphones |
| No voice | The **VOICE** field on the dashboard shows blocked providers and why |
| GPU voice does not start | `logs/local_tts.log`; the first start downloads ~3 GB |
| High usage | Analyse `logs/usage.jsonl` by route, model and groups |
| Does not react to "Jarvis" | Check `JARVIS_WAKE`. With `oww` the startup banner shows whether openWakeWord is running; otherwise text detection applies. Lower `JARVIS_WAKE_THRESHOLD`. |
| Messages shown but not spoken | Quiet hours (`JARVIS_QUIET_HOURS`) |
| Fallback mode never answers | The tooltip on "Brain" in the header shows each provider's reason (no key, limit until …, Ollama off). Ollama: `ollama pull qwen2.5:7b-instruct` |
| Jarvis keeps restarting | `logs/crash.log`; after five crashes in ten minutes the supervisor gives up |
| Wrong travel time | Set `JARVIS_HOME_LAT/LON` to your home address (default is the weather coordinates) |
| Wrong learned phrasing | Delete the entry in `config/learned.json` |

---

## Roadmap

- [x] Wake word ("Jarvis …") as an alternative to the always-open mic, follow-ups without the wake word
- [x] Timers, alarms, reminders and scheduled commands
- [x] Proactive messages (appliances done, appointments, storage, NAS, new devices) with quiet hours
- [x] Shopping and to-do list, routines by voice
- [x] Fallback mode with a free stand-in chain (Groq, Gemini, Ollama) including tools
- [x] Automated tests for the router
- [x] Commute times, parcel status, PC control, ducking, pronunciation, learning router, answer cache
- [x] Autostart and restart after a crash
- [x] Access from iPhone and iPad (Tailscale, device pairing)
- [x] Automated tests for the voice chain
- [x] Learning loop, weekly review, homelab observer, suggestion module
- [x] Action classes, audit log, emergency stop, memory control, protection against injected instructions
- [x] Personality profiles, windows/files/screen, PC and network diagnosis, mail drafts, research, plans, notification centre
- [x] Resource manager, skill factory with approval, context and task engines, memory intelligence, forecasts
- [x] Document search incl. PDF, screen description on request with a card per picture
- [ ] Mouse and keyboard control (deliberately not built; only as a separate, strictly controlled extension)
- [ ] Central control: screen, room microphones, speaker recognition
- [ ] Focus sessions with distraction hints
- [ ] Screen and camera context on request
- [ ] Several user profiles and room satellites (ESP32)
- [ ] Linux/macOS start scripts
- [ ] More tool groups (media server, documents, hypervisor, network controller)
- [ ] English speech (router patterns and replies)

---

<sub>Originally inspired by <a href="https://github.com/Itsme23476/jarvis-claude-code">jarvis-claude-code</a>. Check the template's licence before redistributing this project on its own.</sub>
