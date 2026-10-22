# Architecture

```
Browser HUD (ui/)  ──HTTP/NDJSON──▶  server.py (127.0.0.1:8720)
   mic, speech, graph                 │
   bell, views          ◀──SSE──  events.py  ◀── watchers, homelab_watch, scheduler, learning modules
                                      │
                         router.py ───┼──▶ local handlers (0 tokens): home, timers, lists, routines,
                                      │      windows, personality, emergency stop, weather, …
                                      └──▶ lean Claude call (runtime.py)  ──MCP stdio──▶ jarvis_mcp.py
                                            └─ fallback_llm.py (Groq → Gemini → Ollama) ──▶ jarvis_mcp.py
```

## Request path

1. `server.py` receives `/api/run` (typed flag from the input field).
2. `router.route` tries the local handlers in order (`LOCAL_HANDLERS`). A hit answers immediately.
3. Otherwise `router.llm_route` picks tool groups by regex and the model (Haiku/Sonnet).
4. `commands.lean_system` builds a short system prompt (persona from `personality.py`, rules from
   `policy.py`/`privacy.py`, learned rules from `reflection.py`, core notes); `commands.lean_prefix`
   adds time, the last exchanges, matching notes, open proposals.
5. `runtime.run_lean` runs `claude -p` with only those tool groups (`jarvis_mcp.py` as MCP server).
   If Claude is blocked or fails before speaking, `fallback_llm.run` uses the free chain with the
   same tools.
6. Every tool call goes through `jarvis_mcp.handle_call`: action class, block list, taint check,
   audit log, size cap, untrusted wrapping. CONFIRM tools reach the HUD card via `/api/permission/request`.

## Modules

| Area | Modules |
|---|---|
| Core | `server.py`, `router.py`, `runtime.py`, `commands.py`, `events.py` |
| Tools | `jarvis_mcp.py` (MCP server; `call_tool` = dispatch, `handle_call` = policy wrapper) |
| Safety | `policy.py`, `audit.py`, `emergency.py`, `privacy.py`, `proposals.py` |
| Memory | `memory.py` (vault, graph, diary), `memory_tools.py` (provenance, forget, export, backup), `answer_cache.py`, `learning.py` |
| Voice | `voice.py`, `tts_chain.py`, `local_tts_server.py`, `wakeword.py`, `pronounce.py` |
| Personality | `personality.py` |
| Proactive | `watchers.py`, `scheduler.py`, `homelab_watch.py`, `briefing.py` |
| Learning | `reflection.py`, `weekly_review.py`, `suggestions.py`, `background.py` |
| Windows | `pc.py` (volume, apps, ducking), `desktop.py` (windows, files, clipboard, OCR, scripts) |
| Infrastructure | `infra.py`, `dashboard.py`, `jarvis_profile.py` |
| Organisation | `assistant.py` (drafts, day prep, research, watched topics, plans), `lists.py`, `routines.py`, `commute.py`, `parcels.py`, `icloud_calendar.py` |
| Stand-ins | `fallback_llm.py` |

## Data

| Path | What |
|---|---|
| `vault/Erinnerungen/` | facts (bullets with provenance), Lernprotokoll, Vorlieben (with "Gelernte Regeln"), Vorschläge, Recherchen, Projekte, Gesundheit |
| `vault/Gespraeche/` | daily conversation log (work topics redacted) |
| `vault/Papierkorb/` | expired and forgotten entries, deleted after 30 days |
| `state.json` | window of recent exchanges, timers, persona, pause, job stamps, incident state |
| `logs/` | `usage.jsonl`, `audit.jsonl`, `proposals.json`, `health_daily.json`, `homelab_*`, `watchlist.json` |
| `backups/` | daily vault zip (14 kept) and exports |
| `config/` | `home.json`, `routines.json`, `homelab.json`, `profiles.json`, `scripts.json` (+ `.example.json`) |

## Background threads

All via `background.loop` (paused by the emergency stop unless `pausable=False`):
reflection (5 min), weekly_review (10 min), homelab_watch (5 min), suggestions (10 min),
vault backup (1 h, not pausable), watchlist (30 min). Plus `scheduler` (1 s), `watchers` (30 s),
`briefing` network watchdog.
