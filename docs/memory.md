[← Back to the README](../README.md)

# Memory and learning

Resources, context and tasks, the learning modules and the lasting memory in the Obsidian vault.

## Resources, context, tasks and memory

Further build-out stages on the existing architecture. All of them run without an LLM unless noted, and each can be switched off on its own.

| Module | File | What it does | Commands |
|---|---|---|---|
| **Resource manager** | `resources.py` | Measures VRAM, RAM, Ollama, the GPU voice and Whisper with real values. Keeps a reserve free (`JARVIS_VRAM_RESERVE_MB`). When memory gets tight, it only gives memory back while idle: first unload Ollama, then pause the GPU voice (Piper speaks), then Whisper to the CPU. Later it brings everything back. It never interrupts a conversation. | *"How full is the graphics card?"* · HUD: the "GPU budget" line |
| **Skill factory** | `skill_factory.py` | Learned wordings are a **draft** at first. From the 2nd repetition a suggestion comes; it is only activated after a check (device present and switchable, no collision with routines) and a confirmation card. Every version is stored and can be taken back. | *"What have you learned?"*, *"Take back the reading lamp rule"* |
| **Context engine** | `context.py` | Queries time, calendar, weather, timers, due plans, warnings, PC and house in parallel, with a time limit and a cache. Stale or missing sources are marked. Every LLM request gets a short "situation". | *"What does my day look like?"* (local) |
| **Task engine** | `tasks.py` | Checks in steps (NAS, Pi, network, PC, smart home, everything). Every step has a time limit, retries and its own result check; cancelling is possible at any time. At the end there is a report with a recovery plan. Nothing is **ever** repaired automatically. | *"Check my NAS"*, *"System check"*, *"Cancel the check"* |
| **Memory intelligence** | `memory_intel.py` | Duplicates are not stored. A contradiction (e.g. a different place of residence, a different date) is **never** overwritten; Jarvis asks. Names of people, projects and devices become links in the graph. A "guessed" source and an expiry date per entry are possible. | *"Check my memory"* |
| **Monitoring** | `monitoring.py` | Storage forecast for the NAS, system disk and Pi card with the data basis and a confidence. Recurring outages are named. The same notices from several sources are merged, at most 6 spoken notices per hour. | *"When will the NAS be full?"* |
| **Document search** | `docsearch.py` | A local full-text index (SQLite FTS5) over the shared folders and `JARVIS_DOC_ROOTS`, with the place found and its position (for a PDF the page). | *"What does the manual say about the lint filter?"* |
| **Vision** | `vision.py` | Describes the screen with Claude when local text recognition is not enough (layout, where a button is). Only on request, with a **card per picture**. Blocked for password, bank and chat windows. The picture goes to Claude without tools and is deleted afterwards. Without Claude, vision stays off. | *"Where do I find the setting in this window?"* |

| Variable | Default | Purpose |
|---|---|---|
| `JARVIS_RESOURCES`, `JARVIS_VRAM_RESERVE_MB`, `JARVIS_VRAM_HYSTERESIS_MB` | `1`, `1500`, `1000` | Resource manager, free reserve, margin for bringing things back |
| `JARVIS_TTS_VRAM_MB`, `JARVIS_WHISPER_VRAM_MB` | `4000`, `1800` | Needs of the GPU voice and of Whisper (a setting, not a measurement) |
| `JARVIS_OLLAMA_IDLE_S`, `JARVIS_OLLAMA_SMALL_MODEL` | `180`, empty | Unload Ollama after idling; a smaller model when memory is tight |
| `JARVIS_SKILL_MIN_SEEN` | `2` | From this many repetitions a draft is suggested |
| `JARVIS_CONTEXT_TIMEOUT` | `4` | Time limit per context source in seconds |
| `JARVIS_TASK_STEP_TIMEOUT`, `JARVIS_TASK_MAX_RUNNING` | `45`, `2` | Time limit per check step, simultaneous checks |
| `JARVIS_FORECAST_WINDOW`, `JARVIS_FORECAST_WARN_DAYS` | `30`, `60` | Forecast window, warning threshold in days |
| `JARVIS_DEDUP_SECONDS`, `JARVIS_SAY_PER_HOUR` | `1800`, `6` | Merge identical notices, spoken notices per hour |
| `JARVIS_HOME_IGNORE` | inverters, internet access switches | Entities the smart home check skips (regex) |
| `JARVIS_DOCSEARCH`, `JARVIS_DOC_ROOTS`, `JARVIS_DOC_MAX_FILES`, `JARVIS_DOC_MAX_MB` | `1`, empty, `5000`, `20` | Document search, more folders, limits |
| `JARVIS_VISION`, `JARVIS_VISION_MODEL`, `JARVIS_VISION_MAX_WIDTH` | `1`, `sonnet`, `1600` | Image description on/off, model, maximum picture width |

Local data (in `.gitignore`): `logs/tasks.json`, `logs/metrics_daily.json`, `logs/docindex.sqlite`, `config/learned.history.jsonl`.

## Learning modules

Four modules run in the background, each in its own file. For all of them:
- **No work topics:** whatever looks like work is neither logged nor evaluated nor stored (`privacy.py`, own terms in `JARVIS_WORK_TERMS`). That also applies to the conversation log and to `memory_save`.
- **Changes only with a card:** background jobs change nothing themselves. They file a suggestion (`proposals.py`), Jarvis mentions it in the next conversation, explains what happens and why, and only the confirmation card (`proposal_accept`) carries it out. The explanation is on the card as well.
- **First the calendar, then the suggestion:** before Jarvis suggests an appointment for entering or enters one himself (`calendar_add`, appointments from mails, deliveries), the code reads the calendar for that day (`icloud_calendar.check`). If there already is an appointment with a similar title, no suggestion and no card come; if it overlaps with another one, that is on the card and in the announcement.

| Module | File | What it does | When |
|---|---|---|---|
| **Learning loop** | `reflection.py` | After every conversation rates what went well and what was corrected, and writes that into the note **Learning log**. Condenses the week into rules; confirmed rules are in **Preferences** under "Learned rules" and apply in every conversation from then on. *"Drop the rule …"* removes one again. | 30 min after the last exchange; rules on Sundays from 19 h |
| **Weekly review** | `weekly_review.py` | Fetches sleep, steps, stress, Body Battery and golf rounds from Garmin every day, on Sundays a review with a comparison to the week before and patterns (calculated in code, with the number of days). Meal times, alcohol and coffee Jarvis notes down when you say them (*"I ate at 10 pm"*). Note: **Health/Weekly review YYYY-Wnn** | fetch from 9 h; review on Sundays from 18 h |
| **Homelab watcher** | `homelab_watch.py` | Home Assistant, Pi (services, containers, temperature, memory card, load), NAS and important devices from `config/homelab.json`. Reports outages only after two checks, once per incident plus the all-clear, at night only critical ones, conspicuous values against fixed limits and, after a learning week, also against its own normal values. New WiFi devices are still reported by the existing watcher. 0 tokens. | every 5 minutes |
| **Suggestions** | `suggestions.py` | Suggests a new ability that fits the habits of the last two weeks, with a reason. Routines and reminders it sets up itself after the card; what would need new code is only noted. A list with status in the note **Suggestions**. | Tuesdays and Fridays 18:30 |

Background jobs use Claude Haiku (`JARVIS_BACKGROUND_MODEL`) as their own short call, without tools and without blocking the conversation; if Claude is at its limit, only Groq or Ollama (never Gemini). Usage is in `logs/usage.jsonl` as `background:…`, estimated at 30–60k tokens per week.

| Variable | Default | Purpose |
|---|---|---|
| `JARVIS_WORK_TERMS` | empty | Own work terms (company, colleagues, customers, projects), comma-separated |
| `JARVIS_REFLECT`, `JARVIS_REVIEW`, `JARVIS_HOMELAB`, `JARVIS_SUGGEST` | `1` | Module on/off |
| `JARVIS_REFLECT_IDLE_MIN`, `JARVIS_REFLECT_WEEK_HOUR`, `JARVIS_MAX_RULES` | `30`, `19`, `20` | Learning loop |
| `JARVIS_REVIEW_HOUR`, `JARVIS_LATE_MEAL`, `JARVIS_PATTERN_DAYS` | `18`, `21:00`, `42` | Weekly review |
| `JARVIS_HOMELAB_SECONDS`, `JARVIS_HOMELAB_CONFIRM`, `JARVIS_HOMELAB_MAX_PER_DAY` | `300`, `2`, `2` | Watcher; devices and limits in `config/homelab.json` (template `config/homelab.example.json`) |
| `JARVIS_SUGGEST_DAYS`, `JARVIS_SUGGEST_TIME` | `1,4`, `18:30` | Suggestions (Monday = 0) |
| `JARVIS_BACKGROUND_MODEL`, `JARVIS_PROPOSAL_DAYS` | `haiku`, `7` | Model of the background jobs, expiry of open suggestions |

Local data (all in `.gitignore`): `logs/health_daily.json`, `logs/proposals.json`, `logs/homelab_baseline.json`, `config/homelab.json`.

## Memory

```
vault/
├─ Memories/         Profile · Commands · Preferences · Websites · Knowledge · Network · Smart Home
│  ├─ People/        one note per person
│  ├─ Projects/      one note per project
│  └─ Network devices/   created automatically by the network watcher
├─ Conversations/    daily logs (automatic)
└─ Trash/            expired notes, deleted after 30 days
```

- **Obsidian compatible:** front matter with `type`, `importance` and optionally `expires`, links through `[[wikilinks]]`. The 3D graph in the HUD is built from that.
- **Targeted retrieval instead of the whole context:** the prompt only holds the profile and the commands. Everything else Jarvis fetches through `memory_search` (full text, weighted) and `memory_read`.
- **Remembering on his own** through `memory_save`, without you having to say "remember".
- **Saving after the conversation (since 2026-11-10):** 30 minutes after the last exchange the learning loop (`reflection.py`) picks, in the same call, up to three lasting facts **you yourself** said (people, preferences, appointments, hobbies) and saves them without a card through the same path as `memory_save`: duplicates are skipped, a contradiction is never overwritten, work topics stay out, source "inferred" with the date. Allowed notes: Profile, Preferences, Appointments, Knowledge, Golf, Health, People/…, Projects/… (never Commands, Websites or Smart Home). TV sound (sentences in another language) does not count as conversation. Every entry is listed in the **Learning log** as "Saved" or "Not saved"; *"Forget that …"* takes it out again. Switch off: `JARVIS_REMEMBER=0`.
- **Search index (since 2026-11-10):** `memory_index.py` keeps a SQLite FTS5 index (`logs/memory_index.sqlite`) over all notes and **every single exchange** with its date. `memory_search` asks the index first (bm25, facts before conversations, newer conversations before older ones, accents and word beginnings match). Exchanges stay in the index even when the daily log is deleted after 30 + 30 days; so *"What did I once tell you about …?"* finds things months old.
- **Store on the NAS (since 2026-11-10):** once a day `memory_nas.py` puts the whole vault as `vault-YYYY-MM-DD.zip` and the index on the NAS (`JARVIS_MEMORY_NAS_DIR`, default `/home/Jarvis-Memory`, through the File Station API with `SYNOLOGY_URL`/`SYNOLOGY_USER`/`SYNOLOGY_PASSWORD`); 90 daily copies are kept. The working copy stays on the PC: SQLite over a network share breaks, and Jarvis should remember even when the NAS sleeps. If the NAS cannot be reached, he tries again every hour.
- **Weekly review of what he saved:** Sundays from 18:30 Jarvis lists what he saved by himself during the week (`memory_weekly.py`, only when there is something). For two days *"fine"* or *"strike number 2"* or *"strike the thing about Anna"* is enough; the entry moves to the trash. *"What did you remember this week?"* works any day. Only what he saved himself can be struck this way; everything else stays with *"Forget …"* and its card.
- **Check of the NAS store:** on Wednesdays Jarvis downloads the newest zip and the index again and opens both (zip test, SQLite integrity check, memories present, index not empty). If that fails or the last backup is older than 48 hours (`JARVIS_MEMORY_NAS_STALE_H`), the homelab watcher reports it like an outage, once plus the all-clear.
- **Restoring:** copy `memory_index.sqlite` from the NAS to `logs/` and unpack the newest `vault-….zip` into `vault/`, then restart Jarvis.
- **Forgetting systematically:** expiry dates, then the trash folder, then deletion.
- **Vault from the German edition:** the folder and note names are English here (`Memories`, `Conversations`, `Trash`, `Knowledge`, …). A vault from the German edition uses the German names (`Erinnerungen`, `Gespraeche`, `Papierkorb`, `Wissen`, …); rename the folders and notes before pointing this edition at it.
