[← Back to the README](../README.md)

# Architecture and efficiency

The components, why a call costs so few tokens, and the project structure.

## Architecture

The diagram is in the [README](../README.md#architecture). Here are the components in detail:

| Component | Job |
|---|---|
| `server.py` | HTTP server (`127.0.0.1` only), HUD, API endpoints, confirmation cards, usage log, self-restart on code changes |
| `router.py` | Intent router: local handlers, choice of tool groups and model |
| `runtime.py` | Starts `claude -p` lean (`--system-prompt`, `--tools`, `--strict-mcp-config`, `--setting-sources ""`, `--no-session-persistence`, `--effort`) with a pre-warmed process pool |
| `commands.py` | Prompts, conversation window, state file, slash commands |
| `jarvis_mcp.py` | MCP server with all tools in groups, confirmation gate, result size limit |
| `memory.py` | Vault → knowledge graph, core memories, log, tidying up |
| `voice.py` · `tts_chain.py` | Speech recognition and the speech output chain with adapters |
| `local_tts_server.py` | Local GPU voice (Chatterbox Multilingual) in its own environment |
| `briefing.py` · `dashboard.py` | Daily briefing, network watcher, HUD data |
| `icloud_calendar.py` · `billing.py` | CalDAV calendar, API credit (Cost API) |
| `jarvis_profile.py` | Profile and device mapping from `.env` and `config/home.json` |
| `scheduler.py` | Timers, alarms, reminders and scheduled commands: reading English time expressions, ringing |
| `lists.py` · `routines.py` | Shopping/to-do list (HA to-do or vault note), routines from `config/routines.json` |
| `events.py` · `watchers.py` | Push channel to the HUD (server-sent events, quiet hours) and the rules for notices by himself |
| `wakeword.py` | Wake word: text detection or openWakeWord on the PC's microphone |
| `voice_engine.py` · `voice_ws.py` | Full-duplex voice engine: state machine, Silero VAD, adaptive end of sentence, barge-in, resuming; WebSocket for browsers and satellites (`config/voice.yaml`) |
| `fallback_llm.py` | Fallback mode: replacement chain Groq → Gemini → Ollama with the Jarvis tools |
| `learning.py` · `answer_cache.py` | Learned wordings (`config/learned.json`), usage report, answer cache |
| `commute.py` · `parcels.py` | Travel time to appointments (Nominatim + OSRM), parcel status from shipping mails |
| `pc.py` · `pronounce.py` | Volume, apps, screen lock, Spotify ducking (pycaw); pronunciation lexicon |
| `spotify.py` | Spotify Web API: pause on the wake word, then on; play/pause/next on every device |
| `ui/` | HUD (vanilla JS, 3D knowledge graph without dependencies) |

---

## Efficiency: router and lean mode

A standard Claude Code call sends a base load of ~35,000 tokens with every request:

| Part | Tokens |
|---|---|
| Built-in tools | ~19,700 |
| Default prompt | ~3,400 |
| Connectors | ~2,000 |
| Project prompt | (variable) |

On top of that every tool adds an extra round trip.

**Measurement** (input tokens per request, `scripts/measure_tokens.py`):

| Request | Standard | JARVIS | Path |
|---|---:|---:|---|
| Time | 34,575 | **0** | local |
| Energy status | 110,515 | **0** | local |
| Weather | 194,243 | **0** | local |
| Question to the memory | 34,999 | **3,981** | Haiku |
| Mailbox | 110,851 | **9,591** | Haiku |
| Web research | 151,973 | **10,827** | Sonnet |

**Building blocks:**
- **Intent router** with rules instead of an LLM. Questions and commands are kept apart, protected devices are never switched off automatically.
- **Own prompt** (~1,300 tokens): the stable part comes first so that the prompt cache applies. The dynamic part (time, the last exchanges, matching notes) goes into the message.
- **Tool groups** (`memory`, `web`, `home`, `mail`, `calendar`, `systems`, `health`, `music`, `pc`): per request only the matching ones are loaded, directly and without tool search.
- **Stateless:** no growing session history, but a sliding window of the last exchanges.
- **Result size limit** per tool, Haiku/Sonnet choice, `--effort low`, capped answer length.
- **Background jobs without an LLM:** dashboard, network watcher, briefing facts.

Every request lands in `logs/usage.jsonl` (route, model, groups, input/cache/output, API calls).

---

## Project structure

```
├─ server.py           start (supervisor + HTTP server), loads paths.py
├─ paths.py            makes the folders under jarvis/ importable (import names stay short)
├─ jarvis/
│  ├─ core/            router · runtime · commands · events · scheduler · background · jarvis_profile ·
│  │                   personality · context · tasks · focus
│  ├─ safety/          policy · privacy · audit · emergency · proposals
│  ├─ tools/           jarvis_mcp (tool server) · desktop · infra · assistant · pc · lists · routines ·
│  │                   devices · dashboard · commute · parcels · icloud_calendar · billing · paperless ·
│  │                   unifi · jellyfin · phone_actions · power_costs
│  ├─ memory/          memory · memory_tools · memory_intel · learning · skill_factory · answer_cache ·
│  │                   reflection · docsearch · episodes · selfcheck
│  ├─ voice/           voice · tts_chain · local_tts_server · wakeword · pronounce · vision · daytime ·
│  │                   voice_engine · voice_ws · voice_vad · voice_endpoint · voice_stt · voice_tts ·
│  │                   voice_dsp · voice_wake · voice_state · voice_metrics · voice_config
│  ├─ llm/             fallback_llm · resources
│  └─ watch/           watchers · homelab_watch · monitoring · briefing · weekly_review · suggestions ·
│                      user_habits · wellbeing · departure · maintenance
├─ ui/                 index.html · app.js · voice-client.js · graph.js · styles.css · pair.html
├─ scripts/            measure_tokens.py · lean_probe.py · mail_to_trash.py · router_report.py ·
│                      install-autostart.ps1 · ocr_screen.ps1
├─ tests/              one test module per area, isolate.py first (without house, Claude, network and sound)
├─ config/             *.example.json (your own files stay local) · voice.yaml (voice engine)
├─ docs/               handbook in chapters (features · installation · usage · voice · memory ·
│                      architecture · security) · screenshots/
├─ CLAUDE.md · ARCHITECTURE.md · SECURITY.md · ROADMAP.md · INVENTORY.md
├─ requirements.txt · requirements-gpu.txt · requirements-windows.txt · requirements-tts.txt · requirements-dev.txt
└─ start-jarvis.bat
```
