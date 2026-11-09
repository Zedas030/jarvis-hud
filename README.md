> 🇩🇪 German version: [jarvis-hud-de](https://github.com/Zedas030/jarvis-hud-de)

<div align="center">

# J.A.R.V.I.S.

**Personal voice assistant with memory, smart home control and a HUD — powered by Claude Code**

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows%2011-0078D6?logo=windows&logoColor=white)
![Claude Code](https://img.shields.io/badge/Brain-Claude%20Code%20CLI-D97757)
![Home Assistant](https://img.shields.io/badge/Smart%20Home-Home%20Assistant-41BDF5?logo=homeassistant&logoColor=white)
![Local first](https://img.shields.io/badge/Voice-local%20(Whisper%20%2B%20GPU--TTS)-2ea44f)

</div>

---

JARVIS is a self-hosted voice assistant in the style of the Iron Man computer. You talk freely in the browser, he answers with a natural voice, controls the smart home, knows your calendar, mailbox and home network, and builds up a **lasting memory** in an Obsidian vault.

The language model is the **Claude Code CLI** in subscription mode, so there is no API billing per token. To make the allowance last, a **local intent router** answers simple commands entirely without an LLM. Everything else runs as a **lean, stateless call** with its own short prompt and only the tools the request really needs.

<div align="center">

![Jarvis HUD, "Sun path" view](docs/screenshots/hud.png)

<sub>HUD on the PC, "Sun path" view · instance with the example configuration, no personal data</sub>

</div>

## Contents

- [Highlights](#highlights)
- [What it can do](#what-it-can-do)
- [Usage](#usage)
- [Voice: provider chain, voice engine, fallback mode](#voice-provider-chain-voice-engine-fallback-mode)
- [Language and voice settings](#language-and-voice-settings)
- [Proactive mode](#proactive-mode)
- [Memory and learning](#memory-and-learning)
- [Security model](#security-model)
- [Architecture](#architecture)
- [Quick start](#quick-start)
- [Secrets](#secrets)
- [On the go: iPhone, iPad, phone](#on-the-go-iphone-ipad-phone)
- [Project structure](#project-structure)
- [Documentation](#documentation)
- [Raspberry Pi: monitoring and docs](#raspberry-pi-monitoring-and-docs)

---

## Highlights

<table>
<tr>
<td width="50%" valign="top">🎙️ <b>Hands-free talking</b><br>Live microphone, end-of-sentence detection, talking over him interrupts him. Wake word "Jarvis", optionally local with openWakeWord.</td>
<td width="50%" valign="top">⚡ <b>0 tokens for everyday things</b><br>Lights, sockets, music, timers, alarms, time, weather: a local intent router answers in milliseconds, without an LLM.</td>
</tr>
<tr>
<td valign="top">🪶 <b>Lean Claude calls</b><br>Its own short prompt, only the tools needed, automatic model choice. Measured at over 25× fewer tokens than a standard call.</td>
<td valign="top">🧠 <b>Real memory</b><br>Obsidian vault with topic notes, conversation log, episodes per day, expiry dates and a trash folder. Every memory with a source you can check.</td>
</tr>
<tr>
<td valign="top">🏠 <b>Knows where you are</b><br>Presence sensors per room: the answer comes from that room's speaker, "light on" and "warmer" mean that room, music moves with you, the heating follows, the morning briefing starts on the first trip to the bathroom.</td>
<td valign="top">📣 <b>Speaks up by himself</b><br>Washing machine done, time to leave, parcel arriving, battery full, severe weather, new device on the WiFi. With quiet hours, never without a card.</td>
</tr>
<tr>
<td valign="top">🛟 <b>Fallback mode</b><br>If Claude is down, a free chain Groq → Gemini → Ollama takes over with the same tools. The voice has its own chain of cloud, GPU and free providers.</td>
<td valign="top">🛡️ <b>Controlled and honest</b><br>Action classes in code instead of the prompt, confirmation cards, protection against third-party instructions in mails and web pages, audit log, emergency stop, <code>localhost</code> only.</td>
</tr>
<tr>
<td valign="top">🖥️ <b>PC and Windows</b><br>Volume, apps, screen lock, window arranging, files and clipboard, screen text through local OCR. Spotify gets quieter while Jarvis listens or speaks.</td>
<td valign="top">🛰️ <b>Home network</b><br>Pi, NAS, FRITZ!Box, UniFi, updates with an approval card, a watcher for new devices. <i>"Situation report"</i> sums up Uptime Kuma, the security centre and the energy loop.</td>
</tr>
</table>

---

## What it can do

The architecture separates the **router** (rules), the **tools** (MCP server) and the **memory** (Markdown). New abilities usually take only a few lines. A selection; the full list is in [Features](docs/features.md):

**Everyday life**
- *"Good morning"* gives a daily briefing with weather, appointments, energy, nearly empty batteries and new mails.
- *"Can I still go for a run / play golf today?"* gives a traffic light from weather, wind, daylight and working hours.
- *"Put the dentist in for Friday at ten"* (with confirmation), *"Do I have important mails?"*, *"When do I have to leave?"* (travel time to the next appointment), *"Is a parcel coming today?"*
- *"Timer 8 minutes for the pizza"*, *"Wake me at 6:45"*, *"Remind me tomorrow at 9 to call Anna"*, *"Put milk on the shopping list"*. Local, no tokens, survives restarts.
- *"When I say good night, turn off all the lights and tell me when the alarm rings"* creates a routine. From then on *"Good night"* runs without an LLM.

**At home**
- *"Set the living room light to 40 percent"*, *"Is the washing machine still on?"*, *"Cinema scene"*, then for two minutes *"brighter"*, *"off again"*, *"and in the bedroom too"*.
- Jarvis knows from presence sensors which room you are in: answers come from the speaker there, *"light on"*, *"TV on"* and *"warmer"* without a room mean that room (when it is unclear he asks *"Living room or Bedroom?"*), *"bring the music to me"* moves Spotify to the room's speaker, the heating follows you, at night the Sonos says good night.
- *"Say in the living room: dinner is ready"* speaks through the Echo Dot, in the bedroom through the Sonos. Answers optionally from the Echo, in Alexa's voice.
- *"How much is the solar system delivering?"*, *"What does the PC cost me per month?"*, *"Who is drawing standby power?"*, *"When should I turn on the dishwasher?"* (energy coach from battery level, consumption and the solar forecast).
- If you leave the flat and something is still running, a "switch off" suggestion comes to your phone. When you come home: *"Welcome back. While you were out: …"*
- Severe weather warnings from the German weather service (DWD) by themselves, routine suggestions from your habits, *"Open Netflix on the TV"* through Alexa on the Fire TV.

**Tech and network**
- *"How is the Pi / the NAS doing?"*, *"Is there an unknown device on the WiFi?"*, *"Who is on the WiFi right now?"* (UniFi), *"Are there any updates?"* (FRITZ!Box, NAS, Pi; installs only with a card).
- *"Situation report"*, *"Is the zero export running?"*, *"Which security findings are open?"* locally from Uptime Kuma, the ORBIT security centre and Home Assistant.
- *"Restart the containers on the Pi"* (one command over SSH, always with a confirmation card), *"How far along is the printer?"* (Bambu Lab), *"What is in the maintenance calendar?"*

**Smarter every day**
- *"What did I ask you about the printer last week?"* is answered by the router from the conversation log. *"How did I sleep the last time I was ill?"* from the episodic memory.
- If you ask the same thing at the same time on several days, Jarvis suggests on Saturday to tell you by himself. A poor sleep score or high stress (Garmin) makes the morning "gentle".
- *"Focus 45 minutes"* collects notices instead of speaking them. *"What do I pay for liability insurance?"* searches the scanned post (Paperless). *"Continue watching the series on the TV"* (Jellyfin).
- From 9 pm he speaks more quietly and slowly, during quiet hours he whispers. With *"Turn on the living room"* and both a lamp and a socket, he asks once and remembers the answer.

---

## Usage

| Action | How |
|---|---|
| Live mode | Click **Live**. The button shows the state: *Listening*, *Hearing you*, *Thinking*, *Speaking*. With the wake word: *Say "Jarvis"*. |
| Push-to-talk | Hold the Live button, speak, let go. |
| Interrupting | Just talk over him: after 150 ms he stops and listens. If you say something, that is the new question; "Stop" ends the answer. If you say nothing or just "mhm", he carries on with the sentence he started. |
| Timers and alarms | Countdown at the top of the bar. It rings, then he speaks. Talking over it, `Esc` or **Stop** ends the ringing, *"Snooze"* gives 9 minutes. |
| Notices | Appear with a yellow border in the conversation and are read out; during quiet hours they are only shown. |
| Confirming | Click the card or say "confirmed" / "no". A plain "yes" deliberately does not count. |
| Mini window | When you leave the Jarvis tab, a small robot panda sits at the bottom right of the desktop with the state, the last line, **■ Stop** and the microphone. |
| Choosing the output | 🔈 **Sound here**, 🔊 **Where I am**, **Echo living room** or **Sonos bedroom**: per device, where the answers can be heard. |
| Typing and commands | Input field, `Enter`. `/new`, `/status`, `/recall <term>`, `/graph`, `/help` |
| Looking things up | If Jarvis does not know something and it is neither in the notes nor in the tools, he searches the web. Private data never goes into the search. |

More, including personality profiles, Windows control, 3D printer, updates, parcels and mailboxes: [Usage](docs/usage.md).

---

## Voice: provider chain, voice engine, fallback mode

```
Fish Audio ─▶ ElevenLabs ─▶ local (Chatterbox GPU · Piper CPU) ─▶ Azure ─▶ Google ─▶ Edge
```

- **Fail-safe voice:** every provider is an adapter. If one is used up (credit, quota, invalid key), it is skipped until its reset, across restarts too. On a temporary error only the current sentence switches provider, without an audible break. The HUD shows the active provider with its remaining credit.
- **Voice engine on the server:** Silero VAD, adaptive end of sentence with Whisper partial results, interrupting in 150 ms, carrying on at the start of the sentence when only "mhm" came. Metrics per turn for Grafana, tuning in `config/voice.yaml`.
- **Local first:** speech recognition with Whisper (`large-v3-turbo` on the GPU) and the GPU voice run offline. Quieter from 9 pm, whispered during quiet hours.
- **Fallback mode:** if the Claude allowance is used up or a call breaks off, the first available stage of **Groq → Gemini → Ollama** answers, with the same Jarvis tools and confirmation cards. Gemini never sees mail, calendar or health.

Details, measurements and the tuning log: [Voice and voice engine](docs/voice.md).

---

## Language and voice settings

This repository is the English edition: Jarvis understands and speaks English, and all prompts, intent phrases, replies, logs and UI texts are English. The German edition lives in [jarvis-hud-de](https://github.com/Zedas030/jarvis-hud-de). Compared with it, these defaults changed:

| Setting | German edition | This edition |
|---|---|---|
| `WHISPER_LOCAL_LANG` (local speech recognition, also the warm-up clip) | `de` | `en` |
| `ELEVENLABS_STT_LANG` / `ELEVENLABS_LANG` | `deu` / `de` | `eng` / `en` |
| `EDGE_VOICE` | `de-DE-FlorianMultilingualNeural` | `en-GB-RyanNeural` |
| `AZURE_VOICE` (SSML `xml:lang`) | `de-DE-FlorianMultilingualNeural` (`de-DE`) | `en-GB-RyanNeural` (`en-GB`) |
| `GOOGLE_TTS_VOICE` (`languageCode`) | `de-DE-Chirp3-HD-Charon` (`de-DE`) | `en-GB-Chirp3-HD-Charon` (`en-GB`) |
| Piper model (CPU voice) | `de_DE-thorsten-high.onnx` | `en_GB-alan-medium.onnx` |
| Chatterbox (local GPU voice) `language_id` | `de` | `en` |
| Home Assistant `tts.speak` language | `de` | `en` |
| HUD locale, browser speech synthesis | `de-DE` | `en-GB` |
| Whisper ghost/noise phrases, backchannel and stop words (`config/voice.yaml`) | German | English |
| Default Home Assistant entity ids (e.g. `sensor.house_consumption`, plug suffix `_current_consumption`) | German entity names | English entity names; the old German suffixes can still be set through the `JARVIS_PLUG_*` variables |

The voice test clips in `tests/fixtures/voice/` were spoken anew in English. Carrier and booking mails from German shops are still parsed in both languages, since they arrive in German regardless of the assistant's language.

---

## Proactive mode

Jarvis speaks up by himself – triggered by events, not by fixed times:

- **Coming home:** a short briefing in the room the presence sensors see you in – with what happened while you
  were away.
- **Morning:** on the first entry into the bathroom after the night (weather, warnings, appointments, parcels).
- **Instant:** water, smoke, severe weather, NOAH batteries drifting apart, a device offline.
- **"Jarvis, good night":** devices still on, open windows, an early appointment tomorrow.

Every notice is *critical*, *important* or *nice to know*. A briefing is at most 25 seconds long, the iPad shows
the rest. He repeats what was already said only when something changes; "Jarvis, tell me more" gives details.

**He keeps quiet** – only critical notices get through – during the quiet hours, after "quiet for an hour" or "no
announcements today", in guest mode, during a phone or video call (PC microphone, iPhone focus) and when you are
not at home. Say "not important" a few times and that kind of notice drops a level; this can be seen and reset in
the bell. Home Assistant gets the switches *Jarvis Proactive*, *Jarvis Guest Mode* and *Jarvis Quiet*.

Setup, all rules, sources and secrets: [Proactive mode](docs/proactive.md). On and off:
`config/proactive.yaml` → `enabled` (template `config/proactive.example.yaml`).

---

## Memory and learning

The vault is an Obsidian folder with Markdown notes by topic (`People/Anna`, `Projects/…`, `Devices/…`), an automatic conversation log and episodes per day (conversations, Garmin, notices, outages). Jarvis remembers and forgets on his own (expiry dates, trash folder), stores nothing twice and never overwrites a contradiction — he asks instead. Every memory has a source you can check.

- **Learns:** wordings that Claude had to resolve once ("reading lamp off") he switches himself from then on. The same questions within 5 minutes come from the cache. *"Usage report"* tells how much ran locally.
- **Learning loop:** rates his own conversations, suggests fixed rules on Sundays, does a weekly review with patterns from sleep, steps, stress and golf, and suggests a new ability twice a week.
- **No work topics:** whatever looks like work is neither logged nor stored (`privacy.py`).
- **Changes only with a card:** background jobs change nothing themselves. They file a suggestion, Jarvis explains it, and only the card puts it into effect.

Resource manager, context engine, task engine, document search and the learning modules in detail: [Memory and learning](docs/memory.md).

---

## Security model

- **Local only:** the server binds to `127.0.0.1`. Every request needs a token created at each start; host and origin are checked.
- **Remote access only when paired:** the device port can only be reached through `tailscale serve` and shows only the pairing page without a paired device.
- **Action classes in code:** every tool has a class (read, limited write, confirm, blocked); the test fails if one is missing. Cards are enforced in code, never only in the prompt.
- **Confirmation cards:** shell commands, calendar entries, writing outside the vault. Declined after 75 s without an answer.
- **Third-party content:** mail, web, calendar, files, clipboard and screen are marked as untrusted data and cannot give instructions.
- **Protected devices:** the router never switches off the router, PC, servers or the fridge by itself. Questions never switch anything.
- **No self-modification:** at runtime there are no tools for editing code. Audit log and emergency stop.
- **Data stays local:** `.env`, `config/*.json`, `vault/`, `logs/`, `state.json` and models are in `.gitignore`.

The rules in detail: [Security model](docs/security.md) and [SECURITY.md](SECURITY.md).

---

## Architecture

```mermaid
flowchart LR
    subgraph Browser["Browser (HUD)"]
        MIC[Microphone + VAD] --> STT
        SPK[Playback sentence by sentence]
    end
    subgraph Server["server.py (127.0.0.1)"]
        STT["/api/listen<br/>Whisper local"] --> RUN["/api/run"]
        RUN --> R{"Intent router<br/>router.py"}
        R -->|simple command| LOCAL[Code handler<br/>0 tokens]
        R -->|real question| LEAN["Lean Claude call<br/>runtime.py"]
        LEAN <--> MCP["Tools<br/>jarvis_mcp.py"]
        SPEAK["/api/speak<br/>tts_chain.py"]
    end
    MCP --> HA[(Home Assistant)]
    MCP --> NET[(Pi · NAS · network)]
    MCP --> PIM[(Calendar · mail · Garmin)]
    MCP --> VAULT[(Obsidian vault)]
    LOCAL --> SPEAK
    LEAN --> SPEAK
    SPEAK --> SPK
```

| Component | Job |
|---|---|
| `server.py` | HTTP server (`127.0.0.1` only), HUD, API endpoints, confirmation cards, usage log, self-restart on code changes |
| `router.py` | Intent router: local handlers, choice of tool groups and model |
| `runtime.py` | Starts `claude -p` lean (`--system-prompt`, `--tools`, `--strict-mcp-config`, `--no-session-persistence`) with a pre-warmed process pool |
| `jarvis_mcp.py` | MCP server with all tools in groups, confirmation gate, result size limit |
| `memory.py` · `episodes.py` | Vault → knowledge graph, core memories, log, episodes, tidying up |
| `voice_engine.py` · `voice_ws.py` | Full-duplex voice engine: state machine, Silero VAD, end of sentence, barge-in, resuming; WebSocket for browsers and satellites |
| `tts_chain.py` · `local_tts_server.py` | Speech output chain with adapters, local GPU voice in its own environment |
| `scheduler.py` · `watchers.py` · `events.py` | Timers, alarms, reminders; rules for notices by himself; push channel to the HUD |
| `fallback_llm.py` | Fallback mode: replacement chain Groq → Gemini → Ollama with the Jarvis tools |
| `policy.py` · `privacy.py` · `proposals.py` | Action classes, work topic filter, suggestions with a card |
| `ui/` | HUD (vanilla JS, 3D knowledge graph without dependencies), 19 layouts per device |

**Efficiency:** its own short prompt instead of the Claude Code default, tool groups as needed, automatic model choice (Haiku/Sonnet), answer cache, process pool. Measured, a call needs **over 25× fewer tokens** than a standard call; whatever the router can do costs nothing. All components and the measurement: [Architecture and efficiency](docs/architecture.md).

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
# with an NVIDIA GPU also: -r requirements-gpu.txt · on the Windows PC: -r requirements-windows.txt

# 3  Configuration
copy .env.example .env                      # fill in the values
copy config\home.example.json config\home.json   # fill in the HA entity ids (optional)
mkdir vault\Memories

# 4  Start
start-jarvis.bat
```

All settings are commented in [`.env.example`](.env.example); personal data belongs only in `.env` and `config/*.json`, both in `.gitignore`. Autostart, local voices (Piper, Chatterbox on the GPU), the table of settings, and development and diagnosis: [Installation and configuration](docs/installation.md).

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests      # all tests: without house, Claude, network and sound
```

---

## Secrets

The repo contains no real keys, passwords or addresses. All access is optional. Secrets go into `.env` (template
[`.env.example`](.env.example)), devices into `config/*.json` (templates `config/*.example.json`). Both are in
`.gitignore`. The full table of all settings is in [Installation and configuration](docs/installation.md#configuration).

| What | Where the real value goes |
|---|---|
| Voice: `FISH_AUDIO_API_KEY`, `ELEVENLABS_API_KEY`, `AZURE_SPEECH_KEY`, `GOOGLE_TTS_API_KEY` | `.env` |
| Fallback mode: `GROQ_API_KEY`, `GEMINI_API_KEY`, `OLLAMA_URL` | `.env` |
| Home Assistant: `HA_URL`, `HA_TOKEN`, `HA_MCP_URL` | `.env`; entities for dashboard, rooms and devices in `config/home.json`, the proactive mode's switches and sources in `config/proactive.yaml` (no secrets there) |
| Mail and calendar: `ICLOUD_USER`, `ICLOUD_APP_PASSWORD`, `MAIL_IMAP_HOST`, `MAIL_USER`, `MAIL_PASSWORD` | `.env`, for iCloud an app-specific password |
| Home network: `PIHOLE_URL`, `PIHOLE_PASSWORD`, `PROMETHEUS_URL`, `KUMA_URL`, `SYNOLOGY_*`, `UNIFI_*`, `JELLYFIN_*`, `PAPERLESS_*`, `JARVIS_PI_HOST`, `JARVIS_PI_USER` | `.env` |
| Other: `DHL_API_KEY`, `GARMIN_EMAIL`, `GARMIN_PASSWORD`, `JARVIS_SPOTIFY_CLIENT_ID` | `.env`; then sign in to Spotify once with `scripts\spotify_login.py` |

**Changing or rotating a secret:** create the new key or password at the provider (or a new long-lived token in Home Assistant under *Profile → Security*), replace the value in `.env`, restart Jarvis (**⟳ Restart** in the HUD reads `.env` again), then revoke the old key at the provider. The Spotify refresh token is stored in `config/spotify_token.json` after the login; delete that file and run `scripts\spotify_login.py` again to replace it. The HUD token for the browser is created anew at every start and needs no rotation; paired iPhones and iPads can be removed under **📱 Devices**.

For publication, personal values were replaced like this:

| Anonymised | In the repo | Your own value |
|---|---|---|
| Name | "User" | `JARVIS_USER_NAME` in `.env` |
| Home town, coordinates, warning cell | "Sampletown", `000000000` | `JARVIS_PLACE`, `JARVIS_WEATHER_LAT`, `JARVIS_WEATHER_LON` in `.env` |
| Addresses in the home network | `10.0.0.x` as examples in comments and defaults | the matching `*_URL` or `*_HOST` variable in `.env`. The links to monitoring and docs are fixed in `ui/index.html` and are changed there. |
| Tailscale names | `…tailxxxx.ts.net` | `JARVIS_REMOTE_HOSTS` in `.env` |
| Spotify account, client id | `user`, `xxxxxxxx` | your own app on developer.spotify.com, id in `.env` |
| Device and room names | examples in `config/*.example.json` | your own copies without `.example` |

A snapshot of the monitoring page and the Pi docs (German, with the same placeholders) is in the German edition under
[`jarvis-hud-de/pi/`](https://github.com/Zedas030/jarvis-hud-de/tree/main/pi). It is there for illustration and is not deployed automatically.

## On the go: iPhone, iPad, phone

The PC stays the brain. iPhone and iPad connect through **Tailscale** over HTTPS, from anywhere and without an open port in the router: `JARVIS_REMOTE=1`, once `tailscale serve --bg 8721`, then add the address to the Home Screen on the device and pair it with a six-digit code. The iPad shows its own room display with tiles (weather, house, energy, tokens), the iPhone the HUD in portrait; tapping the microphone switches live on, holding it is push-to-talk. Suggestions when you leave the flat and before you set off come to the phone with **yes/no buttons** (Home Assistant app), and the answer counts like the card. Step-by-step setup: [Usage → On the go](docs/usage.md#on-the-go-iphone-and-ipad).

---

## Project structure

```
├─ server.py           start (supervisor + HTTP server), loads paths.py
├─ jarvis/
│  ├─ core/            router · runtime · commands · events · scheduler · context · tasks · focus · whereabouts
│  ├─ safety/          policy · privacy · audit · emergency · proposals
│  ├─ tools/           jarvis_mcp (tool server) · desktop · infra · pc · lists · routines · devices ·
│  │                   commute · parcels · icloud_calendar · spotify · speakers · paperless · unifi · jellyfin …
│  ├─ memory/          memory · memory_intel · learning · skill_factory · answer_cache · episodes · docsearch
│  ├─ voice/           voice_engine · voice_ws · voice_vad · tts_chain · local_tts_server · wakeword · sonos · alexa_voice
│  ├─ llm/             fallback_llm · resources
│  └─ watch/           watchers · briefing · weekly_review · energy_coach · departure · weather_alerts · wellbeing …
├─ ui/                 HUD: index.html · app.js · voice-client.js · graph.js · styles.css · pair.html
├─ config/             *.example.json (your own files stay local) · voice.yaml
├─ docs/               handbook in chapters · screenshots/
├─ tests/              one test module per area, isolate.py first
└─ scripts/            measure_tokens · router_report · fetch_voice_models · install-ha-remote.ps1 …
```

---

## Documentation

| Page | Content |
|---|---|
| [Features](docs/features.md) | All highlights, everything that is possible, feature list |
| [Installation and configuration](docs/installation.md) | Quick start, local voices, all `.env` settings, development and diagnosis |
| [Usage](docs/usage.md) | Speaking and typing, cards, personality, everyday life, Windows, iPhone/iPad, phone |
| [Voice and voice engine](docs/voice.md) | Provider chain, listening/interrupting/resuming with measurements, fallback mode |
| [Proactive mode](docs/proactive.md) | Triggers, quiet rules, sources, voice commands, feedback, secrets, Linux |
| [Memory and learning](docs/memory.md) | Resources, context, tasks, learning modules, vault |
| [Architecture and efficiency](docs/architecture.md) | All components, router and lean mode, project structure |
| [Security model](docs/security.md) | The rules in code; details in [SECURITY.md](SECURITY.md) |
| [ARCHITECTURE.md](ARCHITECTURE.md) · [SECURITY.md](SECURITY.md) · [ROADMAP.md](ROADMAP.md) | Module map, security rules and the build-out plan for working on the code |
| [INVENTORY.md](INVENTORY.md) · [SERVER-REBUILD.md](SERVER-REBUILD.md) · [VOICE-ANALYSIS.md](VOICE-ANALYSIS.md) · [PORTING.md](PORTING.md) | Inventory, the server rebuild, the voice analysis and porting notes |
| [jarvis-hud-de/pi/](https://github.com/Zedas030/jarvis-hud-de/tree/main/pi) | Monitoring and docs of the Raspberry Pi that gives Jarvis the house, the network and DNS (German snapshot) |

---

## Raspberry Pi: monitoring and docs

The Pi in the home network gives Jarvis Home Assistant, DNS and the monitoring; the HUD links both pages at the top right. Anonymised copies of both pages (German) are in the German edition under [`jarvis-hud-de/pi/`](https://github.com/Zedas030/jarvis-hud-de/tree/main/pi).

<table>
<tr>
<td width="50%"><img src="docs/screenshots/monitoring.png" alt="ORBIT Monitor, device page"></td>
<td width="50%"><img src="docs/screenshots/pi-docs.png" alt="Pi documentation, overview"></td>
</tr>
<tr>
<td align="center"><sub>ORBIT Monitor (port 8092): device inventory with live status</sub></td>
<td align="center"><sub>Pi documentation (port 8091): hardware, services, Jarvis section</sub></td>
</tr>
</table>

---

<sub>Originally inspired by <a href="https://github.com/Itsme23476/jarvis-claude-code">jarvis-claude-code</a>. Before passing it on independently, clarify the licence situation of the original.</sub>
