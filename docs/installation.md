[← Back to the README](../README.md)

# Installation and configuration

Requirements, quick start, local voices, all settings from `.env` and the tools for development and diagnosis.

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

**Starting with the PC (optional):** a shortcut to `start-jarvis.bat` in the startup folder (`shell:startup`, working folder the repo, window minimised) starts Jarvis when you sign in. To switch it off use Task Manager under "Startup apps" or delete the shortcut.

**Local voices (optional)**

```powershell
# Piper (CPU, ready right away)
mkdir models\piper
curl -L -o models\piper\en_GB-alan-medium.onnx      https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/alan/medium/en_GB-alan-medium.onnx
curl -L -o models\piper\en_GB-alan-medium.onnx.json https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/alan/medium/en_GB-alan-medium.onnx.json

# Chatterbox Multilingual (GPU, ~5 GB) — details in requirements-tts.txt
py -3.12 -m venv .venv-tts
.venv-tts\Scripts\python.exe -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
.venv-tts\Scripts\python.exe -m pip install --no-deps chatterbox-tts
.venv-tts\Scripts\python.exe -m pip install -r requirements-tts.txt
# voice sample (~10 s, your own rights): models\voice\reference.wav
```

The server starts the GPU voice automatically as soon as `.venv-tts` exists. Chatterbox speaks English (`language_id` `en`).

## Configuration

All settings are commented in [`.env.example`](../.env.example). Personal data belongs only in `.env` and `config/home.json`; both are in `.gitignore`. Where to enter API keys, passwords and tokens and how to rotate them: README, section [Secrets](../README.md#secrets).

| Area | Variables | Purpose |
|---|---|---|
| Profile | `JARVIS_USER_NAME`, `JARVIS_PLACE`, `JARVIS_WEATHER_LAT/LON`, `JARVIS_TIMEZONE`, `JARVIS_BROWSER` | Form of address, place, browser |
| Brain | `JARVIS_LEAN`, `JARVIS_LOCAL_ROUTER`, `JARVIS_MODEL_SIMPLE/SMART`, `JARVIS_EFFORT`, `JARVIS_MAX_OUTPUT_TOKENS`, `JARVIS_HISTORY_TURNS` | Router, model choice, context size |
| Voice | `TTS_CHAIN`, `TTS_ANNOUNCE_SWITCH`, `FISH_AUDIO_*`, `ELEVENLABS_*` (incl. `ELEVENLABS_LANG`, default `en`), `AZURE_*`, `GOOGLE_TTS_*`, `EDGE_*`, `LOCAL_TTS_URL` | Provider order and access |
| Listening | `JARVIS_STT`, `WHISPER_LOCAL_*` (incl. `WHISPER_LOCAL_LANG`, default `en`), `ELEVENLABS_STT_LANG` (default `eng`) | Speech recognition |
| Wake word | `JARVIS_WAKE` (`off`/`text`/`oww`), `JARVIS_WAKE_*`, `JARVIS_FOLLOWUP_S` | "Jarvis …" instead of an always-on microphone. Without "Jarvis" nothing is processed or logged; `JARVIS_FOLLOWUP_S` > 0 briefly allows follow-up questions without "Jarvis" (default: off) |
| Speaking up by himself | `JARVIS_PROACTIVE`, `JARVIS_QUIET_HOURS`, `JARVIS_EVENT_LEAD_MIN`, `JARVIS_STORAGE_FULL` | Notices and quiet hours (alarms always ring) |
| Lists | `JARVIS_SHOPPING_LIST`, `JARVIS_TODO_LIST` | HA to-do entity, empty = note under `vault/Memories/Lists/` |
| Fallback mode | `JARVIS_FALLBACK` (`groq,gemini,ollama`), `GROQ_API_KEY`, `GROQ_MODEL`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `OLLAMA_URL`, `OLLAMA_MODEL`, `JARVIS_FALLBACK_PRIVATE` | Replacement chain when Claude fails; see [Fallback mode](voice.md#fallback-mode-replacement-chain) |
| Saving | `JARVIS_CACHE_S` (default 300, 0 = off) | The same read questions come from the cache for this long |
| Commuting | `JARVIS_HOME_LAT/LON` (otherwise the weather coordinates), `JARVIS_TRAFFIC_FACTOR` (1.25), `JARVIS_COMMUTE_BUFFER_MIN` (5), `JARVIS_LEAVE_WARN_MIN` (5) | Starting point, traffic surcharge, buffer, advance warning |
| PC and volume | `JARVIS_DUCK` (0.3, 0 = off), `JARVIS_DUCK_APPS` (spotify.exe), `JARVIS_NIGHT_VOLUME` (0.5) | Music quieter while speaking, the voice during quiet hours; own apps in `config/apps.json` |
| Spotify | `JARVIS_SPOTIFY_CLIENT_ID`, `JARVIS_SPOTIFY_HOLD` (1), `JARVIS_SPOTIFY_HOLD_MAX_S` (120), `JARVIS_SPOTIFY_HOLD_MODE` (quiet), `JARVIS_SPOTIFY_QUIET_FACTOR` (0.3), `JARVIS_SPOTIFY_QUIET_PCT` (20) | On "Jarvis" and on every announcement, also when he speaks up by himself, the music on the account's device that is playing (also the phone on the stereo) goes to a third of its volume, at most 20 %, and then comes back to the old volume; if you changed it by hand in between, it stays that way. With `JARVIS_SPOTIFY_HOLD_MODE=pause` it stops instead, as it does on devices whose volume Spotify cannot set; music commands go through the account. Once: an app on developer.spotify.com (redirect URI `http://127.0.0.1:8722/callback`), then `scripts\spotify_login.py` |
| Pronunciation | `config/pronunciation.json` | Own entries, created by voice |
| Services | `ICLOUD_*`, `MAIL_*`, `HA_*`, `SYNOLOGY_*`, `JARVIS_PI_*`, `GARMIN_*`, `PAPERLESS_*`, `UNIFI_*`, `JELLYFIN_*` | Integrations; each one is optional |
| Smarter | `JARVIS_USER_HABITS` (1), `JARVIS_WELLBEING` (1), `JARVIS_WB_SLEEP_LOW/STRESS_HIGH/BATTERY_LOW` (60/50/30), `JARVIS_WB_BATTERY_EVENING` (25), `JARVIS_DEPARTURE_PREP_MIN` (30), `JARVIS_POWER_BUDGET_EUR` (0 = off), `JARVIS_MAINTENANCE` (1), `JARVIS_WHISPER_HOURS` (= quiet hours), `JARVIS_LATE_FROM` (21), `JARVIS_LATE_VOLUME` (0.8), `JARVIS_FOCUS_WARN_MIN` (10), `JARVIS_FOCUS_DISTRACT`, `JARVIS_SELFCHECK_SECONDS` (25), `JARVIS_PHONE_ACTION_ENTITY` | Habits, Garmin triggers, setting off, budget, maintenance, voice by time of day, focus, self-check, phone return channel |
| Devices | `config/home.json` | Mapping of the dashboard fields (solar, battery, rooms, router, air) to Home Assistant entities, plus `appliances` (power meters for "… is done") and `device_aliases` (more words per device, e.g. "living room" and "lamp" for a light that is only called "LED" in Home Assistant; applies without a restart after 5 minutes at the latest) |
| Home Assistant entity names | `JARVIS_PLUG_POWER_SUFFIX`, `JARVIS_PLUG_MONTH_SUFFIX`, `JARVIS_HOUSE_MONTH` | The defaults follow English Home Assistant names (`_current_consumption`, `_this_month_s_consumption`); set these for a Home Assistant in another language |
| Routines | `config/routines.json` | Created by voice; format in `config/routines.example.json` |

> **Important:** never set `ANTHROPIC_API_KEY`. Claude Code would then bill through the API instead of the subscription. As a precaution the server removes the variable from the environment of its child processes.

## Development and diagnosis

```powershell
# Tests: router, timers, lists, routines (Home Assistant is simulated, nothing is switched)
.venv\Scripts\python.exe -m unittest discover -s tests -v

# Token measurement: parts, standard requests, the lean path
.venv\Scripts\python.exe scripts\measure_tokens.py components
.venv\Scripts\python.exe scripts\measure_tokens.py new

# What still goes to the LLM, what has the router learned?
.venv\Scripts\python.exe scripts\router_report.py 30

# Autostart at sign-in (Task Scheduler, without admin rights); -Remove removes it
powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1

# Test router + lean call without the server
.venv\Scripts\python.exe scripts\lean_probe.py "How is the NAS doing?"

# Test instance next to the running one (own port, own state file)
set JARVIS_PORT=8721& set JARVIS_STATE=%TEMP%\state_test.json& .venv\Scripts\python.exe server.py
```

| Symptom | Cause / fix |
|---|---|
| The Live button does nothing | `F5`. On "Microphone blocked" allow the microphone in the address bar. |
| Gets cut off in the middle of a sentence | Raise `endpoint.base_ms` / `unfinished_extra_ms` in `config/voice.yaml` (see the tuning log); without the engine: `SILENCE_MS` in `ui/app.js` |
| Stutters while speaking without anyone talking over him | Raise `vad.barge_threshold` in `config/voice.yaml`; without the engine: `BARGE_FACTOR` in `ui/app.js` |
| The HUD reports no engine ("voice engine off" in the start banner) | Port 8724 in use or `websockets` missing: `pip install websockets pyyaml`; `engine.enabled` in `config/voice.yaml` |
| No voice | The **VOICE** field in the dashboard shows blocked providers and the reason |
| The GPU voice does not start | `logs/local_tts.log`; the first start downloads ~3 GB |
| High usage | Evaluate `logs/usage.jsonl` by route, model and groups |
| What went wrong? | `logs/jarvis.log` (JSON lines, warnings and errors of all modules; `JARVIS_LOG_DEBUG=router` for one module on DEBUG) and `http://localhost:8720/metrics` (counters per route, card, job, module) |
| Does not react to "Jarvis" | Check `JARVIS_WAKE`. With `oww` the start banner shows whether openWakeWord runs; otherwise text detection applies. Lower `JARVIS_WAKE_THRESHOLD`. |
| Notices only shown, not spoken | Quiet hours (`JARVIS_QUIET_HOURS`) |
| Fallback mode does not answer | The tooltip on "Brain" in the header shows the reason per provider (key missing, limit until …, Ollama off). Ollama: `ollama pull qwen2.5:7b-instruct` |
| Jarvis keeps restarting | `logs/crash.log`; after five crashes in ten minutes the supervisor stops |
| Wrong travel time | Set `JARVIS_HOME_LAT/LON` to the home address (the default is the weather coordinates) |
| A learned wording is wrong | Delete the entry in `config/learned.json` |
| Speech recognition writes German | `WHISPER_LOCAL_LANG` must be `en` (an `.env` taken over from the German edition still says `de`) |
