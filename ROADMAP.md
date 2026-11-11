# Roadmap: growing into a complete assistant

Status per phase of the brief from 2026-11-02. The analysis is in `INVENTORY.md` (phase 0), the voice
pipeline in `VOICE-ANALYSIS.md`, the server move in `SERVER-REBUILD.md`. The checklist in
`docs/features.md` stays the feature list; this page holds the plan.

Legend: ✅ done · 🔄 in progress · ⏸ waiting for approval · ⬜ open

| Phase | Status | State |
|---|---|---|
| 0 Inventory | ✅ | Report `INVENTORY.md`, approved 2026-11-02. The seven decisions (section 11) are still open |
| P0 Foundation | ✅ except the Linux run | 2026-11-02: tests green (637), dependencies split, `jarvis_log.py` (JSON log), `metrics.py` (`/metrics` for all modules), non-UTF-8 requests → 400. Open: test run on Linux (no WSL on the PC), rotate keys (user) |
| 1 Voice pipeline | ✅ core · ⬜ rest | Engine on the server since 2026-11-02 (commit `b85eb0d`). Since 2026-11-10: iPad/iPhone on the engine (tunnel `/voice-ws`). Open: room satellite, Sonos AEC trial, RNNoise, tuning with a real microphone |
| 2 Agent core | 🔄 | Present: router, Haiku/Sonnet, fallback mode, action classes, audit, personality, vault, episodes. Since 2026-11-10: SQLite FTS5 index over the memory (`memory_index.py`), saving facts by himself after a conversation, store on the NAS. Missing: plugin registry, YAML configuration, short-term summary, speaker recognition, separation of private and work |
| 3 Server & operations | ⬜ | Plan and hardware in `SERVER-REBUILD.md`; `hostos.py` started. Missing: Linux test run, PC agent, systemd, secrets, backups to the NAS, self-monitoring. Since 2026-11-10: crash watchdog in HA (`heartbeat.py`, push when the PC runs without a heartbeat) |
| 4 HA & energy | ⬜ | Present: rooms/aliases, climate groups, own routines, energy coach, electricity costs, budget. Missing: HA automations by voice, PV forecast, Noah SOC, PromQL from speech with a chart |
| 5 Proactivity | 🔄 | 2026-11-05: proactive mode (docs/proactive.md): triggers arrival/morning/instant/good night, levels, output by presence, quiet rules, feedback by voice, history. Since 2026-11-10: HA WebSocket (`ha_events.py`) instead of the 30-s poll. Missing: Alertmanager |
| 6 Presence & multiroom | ⬜ | Present: speaker per room, Sonos announcements, moving music. Missing: mmWave/CSI fusion, arbitration of several mics, volume per room and time of day |
| 7 Work assistant | ⬜ | Present: reading IMAP, drafts, CalDAV, research, plans. Missing: M365 Graph, deadline watcher, dictation → document, work memory (decision 1 needed) |
| 8 HUD | ⬜ | Present: 13 layouts, iPad pairing, state display, graph. Missing: camera streams, Prometheus charts, splitting `app.js` |
| 9 Network & IT | 🔄 | Present: Pi/NAS/UniFi/Pi-hole, new devices, situation report, updates with a card, `pi_run`. Since 2026-11-10: Kuma alarms at once via HA (`kuma_alerts.py`), restarting a container as a proposal with a card. Missing: Grafana alerts, asking back about causes |

## Cross-cutting

| Topic | Status | State |
|---|---|---|
| Tests | 🔄 | 664 tests green; `coverage` in `requirements-dev.txt`, no measurement yet; never run on Linux |
| Observability | 🔄 | `/metrics` for requests, duration, tokens, cards, jobs, fallback mode, tool calls, log errors and the voice engine; `logs/jarvis.log` as JSON. Prometheus on the Pi does not reach the PC; no Grafana dashboard "Jarvis" |
| Cost control | ✅ | Tokens per route in the log, electricity budget; since 2026-11-10 a weekly token budget announced at 80/100 % (`token_budget.py`) |
| Privacy | 🔄 | Audio local, raw audio only in debug mode; an overview "which data goes to which cloud" is missing as a document |
| Configuration | 🔄 | `voice.yaml` as the model; the rest in `.env` (333 variables) and 7 JSON files. New: `JARVIS_LOG_LEVEL`, `JARVIS_LOG_DEBUG`, `JARVIS_LOG_FILE` |
| Documentation | 🔄 | README, ARCHITECTURE, SECURITY, PORTING, SERVER-REBUILD, VOICE-ANALYSIS; a README per module and a "new ability" guide are missing |
| Language | ✅ | English edition (this repository); the German edition is [jarvis-hud-de](https://github.com/Zedas030/jarvis-hud-de) |

## Next steps (after approval of phase 0)

1. Decisions 1–7 from `INVENTORY.md`; rotate the keys from commit `f9f794f` (user); connect Prometheus on the Pi to `/metrics` as soon as the Jarvis host is reachable.
2. Phase 2 agent core with registry, YAML configuration, SQLite memory.
3. Phase 3 server move according to `SERVER-REBUILD.md`.

## History

- 2026-11-10 evening: "Why is … on?" from the HA logbook, "Where is …?", birthdays the day before, "What did you do today?", night report of the presence sensors, airing and mould, print ideas with MakerWorld.
- 2026-11-10 afternoon: weekly review of what was saved, check of the NAS store, noise filter, "What's on tomorrow?" in good night, Kuma alarms with a restart proposal.
- 2026-11-10 HA events in real time, weekly token budget, memory with an FTS5 index, saving facts by himself and the NAS store, iPad/iPhone on the voice engine.
- 2026-11-10 False-trigger fix (TV, YouTube, own echo, one Jarvis per PC, one voice per browser), crash watchdog in Home Assistant (`heartbeat.py`, `scripts/heartbeat_ha_setup.py`).
- 2026-11-05 Proactive mode built and accepted (`docs/acceptance-proactive.md`); room-aware commands (`docs/acceptance-room-commands.md`).
- 2026-11-04 English edition: speech, prompts, intents, UI, docs and tests translated; German edition split off.
- 2026-11-02 Phase 0 approved, P0 foundation built (logging, metrics, dependencies, tests).
- 2026-11-02 Phase 0 report written; phase 1 core done (voice engine on the server).
- 2026-10-29 Server rebuild plan (`SERVER-REBUILD.md`).
- 2026-10-24 Portability review (`PORTING.md`), `hostos.py`.
- 2026-10-14 First commit.
