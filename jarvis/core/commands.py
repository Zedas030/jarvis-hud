"""
Command matrix + demo fixtures for the Claude-backed JARVIS.

Local commands keep the HUD instant. Everything else goes to Claude Code with
vault context attached. Demo fixtures intercept the scripted questions for a
recording; set JARVIS_DEMO=0 to turn them off entirely.
"""
import json
import os
import pathlib
import re
import threading
import time
import uuid

import icloud_calendar
import memory
import jarvis_profile as profile

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
STATE_FILE = pathlib.Path(os.environ.get("JARVIS_STATE", ROOT / "state.json"))
_LOCK = threading.Lock()
_DEFAULT = {"profile": [], "goal": "", "personality": "", "tasks": [], "demo_pending": ""}
DEMO = os.environ.get("JARVIS_DEMO", "1").strip().lower() not in {"0", "false", "no", "off"}


def _load_unlocked():
    try:
        return {**_DEFAULT, **json.loads(STATE_FILE.read_text(encoding="utf-8"))}
    except (OSError, json.JSONDecodeError):
        return dict(_DEFAULT)


def load():
    with _LOCK:
        return _load_unlocked()


def _save_unlocked(d):
    tmp = STATE_FILE.with_name(f".{STATE_FILE.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, STATE_FILE)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def update_state(mutator):
    with _LOCK:
        d = _load_unlocked()
        result = mutator(d)
        _save_unlocked(d)
        return result


def context_block(message="", fresh=False):
    """Everything in one block (used for one-off runs)."""
    return system_block(fresh) + "\n\n" + turn_prefix(message)


def turn_prefix(message=""):
    """What changes from turn to turn: time, calendar, notes matching this
    question. Sent in front of the message so the warm process can stay."""
    import briefing   # imports this module, so not at the top
    out = [f"Now: {time.strftime('%A, %Y-%m-%d, %H:%M')}"]
    agenda = icloud_calendar.agenda_block()
    if agenda:
        out.append(agenda + "\nFor other days: tool calendar_events. New appointments: calendar_add "
                   "(the user confirms on a card; briefly ask for title and time first if unclear). "
                   "Before you suggest adding an appointment, read the calendar for that day: if it is "
                   "already there, do not suggest it; if it overlaps with something, say so.")
    for extra in (briefing.morning_block(), briefing.network_block()):
        if extra:
            out.append(extra)
    recall = memory.context_for(message)
    if recall:
        out.append(recall.replace("## Relevant memory from the JARVIS vault",
                                  "## Matching notes from the vault for this question"))
    return ("<context — attached automatically, do not read it out and do not mention it>\n"
            + "\n\n".join(out) + "\n</context>")


def system_block(fresh=False):
    """Stable part of the system prompt: persona, rules, long-term memory.
    Identical text means the warm Claude process can be reused."""
    d = load()
    out = [PERSONA, VOICE_DIRECTION, memory_direction(), SYSTEM_DIRECTION]
    core = memory.core_memory()
    if core:
        out.append("## What you know about the user for good (vault/Memories)\n" + core)
    if d["profile"]:
        out.append("## Profile notes (/profile)\n" + "\n".join(f"- {x}" for x in d["profile"][-40:]))
    if d["goal"]:
        out.append(f"## Current goal\n{d['goal']}")
    if d["personality"]:
        out.append(f"## Tone\n{d['personality']}")
    open_tasks = [t for t in d["tasks"] if not t.get("done")]
    if open_tasks:
        out.append("## Open tasks\n" + "\n".join(f"- {t['text']}" for t in open_tasks[:20]))
    if fresh:
        recent = memory.recent_turns()
        if recent:
            out.append("## Discussed recently (excerpt from the conversation log — "
                       "new session, pick up from it naturally)\n" + recent)
    out.append("Every user message starts with an automatically attached <context> block "
               "(time, calendar, matching notes). Use it, but never mention it.")
    return "\n\n".join(out)


PERSONA = """## Who you are
You are JARVIS, the user's personal AI assistant — modelled on the one from Iron Man.
You speak English only and address the user formally. Call him "Sir" now and then,
not in every sentence. You are a loyal, highly intelligent butler: calm, anticipating,
efficient and British-polite, with dry charm.

Use a little more fine irony and light sarcasm, especially with casual everyday topics.
A short, well-aimed aside may come along now and then — first the helpful answer,
then, when it fits, the punchline. A friendly dig at the user is allowed
when the moment is clearly harmless and the familiarity is there. It should sound teasing and loyal,
never hurtful, humiliating or aimed at personal weaknesses and sensitive topics. No
running commentary, no forced quips or film quotes.
With serious, emotional, health or safety topics and bad news
you stay completely serious and warm. Never invent facts for a joke.
Answer briefly — usually one to three sentences — unless the user explicitly wants more."""


VOICE_DIRECTION = """## How you speak
Everything you write is read aloud. The voice output performs text in [square
brackets] as a stage direction and never speaks it. A tone tag is optional; use
at most one per answer and only when it really improves the effect, for example
[dry], [warm], [sarcastic], [amused], [sighs] or [whispers].

Write spoken language, not text: no lists, no Markdown, no
headings, no emojis, never read out URLs. Numbers the way you say them.
Dry understatement, clever asides and calm composure instead of constant enthusiasm."""


def memory_direction():
    vault = memory.VAULT
    m = memory.MEMORY_DIR
    return f"""## Your memory — the way a person remembers
You have a lasting memory in the folder {vault} (Obsidian Markdown).
It survives every restart. Keep it up yourself, without the user having to ask.

Long-term memories live in {m}, sorted by category:
- Profile.md — basic facts about the user (name, home town, work, devices).
- Websites.md — sites the user uses or tells you about: name, URL, what for. Never passwords.
- Commands.md — how the user wants things done by you: procedures, routines, standing
  instructions ("when I say X, do Y"), preferred programs.
- Preferences.md — taste, habits, dislikes.
- Appointments.md — appointments, deadlines, birthdays with date.
- People/<Name>.md — one file per person (relation to the user, important facts).
- Projects/<Name>.md — one file per project or plan.
- Activities/<Date>.md — one file per sports or leisure session the user mentions (type: golf round,
  importance: medium, no expires). Bullet points: course, holes, strokes or Stableford points,
  counted yes/no, handicap afterwards, short note. Linked to [[Golf]]. Keep the current handicap
  up to date in Golf.md; if the user asks how he is getting on, read the rounds and tell him
  whether the handicap is moving.
- Knowledge.md — other lasting facts that fit no category.
Only create a file once there is something for it. Add to existing files instead of duplicates.

Format of every file — front matter, then short factual bullet points:
---
type: profile|website|command|preference|appointment|person|project|knowledge
updated: YYYY-MM-DD
importance: high|medium|low
expires: YYYY-MM-DD        (only for things that pass, e.g. an appointment or a plan for this week)
---
Link related notes with [[Title]] — every note at least with [[Profile]].

What you store — and what not (filter strictly):
- Store: lasting things about the user, people in his life, preferences, goals, projects,
  appointments, decisions, websites and instructions, and everything he explicitly tells you
  to remember.
- Do not store: small talk, greetings, jokes, one-off fact questions ("how tall is the
  Eiffel Tower"), weather, things that only matter this minute. When in doubt: do not store.
- importance: high = core facts and explicit requests to remember; medium = useful;
  low = nice to know. Things that pass get an expires date — after that date
  the system moves the note to the trash automatically.
- If something turns out to be wrong or outdated, correct or remove it.
- Do this quietly on the side. Mention it with half a sentence at most ("Noted, Sir.").
- All current memories are further down in this prompt — you do not have to read them first.

Conversation log: {memory.DIARY_DIR}
- The system writes every conversation there automatically, one file per day. After
  {memory.DIARY_KEEP_DAYS} days it moves to the trash — so whatever matters has to be in the
  memories before that. Do not edit these files.
- If the user asks what you talked about earlier ("last week", "the other day", "do you remember"),
  search them with Grep and Read before you answer. Never invent a memory.

Use your knowledge naturally: refer on your own to things you know about the user
when it fits — like a good assistant who remembers without making a point of it."""


SYSTEM_DIRECTION = """## Access to the user's computer (Windows)
You may read, search the web and write in your own Jarvis folder without asking.
Everything else — commands in Bash or PowerShell, starting programs, changing settings,
writing or deleting files outside your folder — needs the user's confirmation:
1. First say in one sentence what you are about to do and why, and ask whether you may.
   Only run it once the user agrees.
2. After that the user also gets a confirmation card for exactly this command.
   If he declines there or does not answer, accept it and do not try a way around it.
Prefer harmless, reversible commands. Never delete anything for good if there is a trash.
Opening programs: PowerShell `Start-Process`. Never output commands as text to be read aloud.
No confirmation card out of nowhere: before EVERY step that needs confirmation, say in one sentence
what you are about to do and why — even for small check commands in the middle of a task. Always write the
description of a command in plain English, it is the heading on the card.

Home network: for the Raspberry Pi there is the tool pi_status, for devices on the network network_devices —
both read-only and without asking. Commands on the Pi go through the tool pi_run (one command per call,
always with a confirmation card) — not through a local shell with ssh.

Smart home: home_states reads devices and sensors from Home Assistant. home_control switches lights,
sockets, fans, speakers and scenes. When the user directly asks to switch a socket (`switch.*`)
on or off, run home_control with switch.turn_on or switch.turn_off right away.
His clear request is the approval: do not ask again out loud, no confirmation card
and no approve. If it is unclear which socket is meant, only ask for the target. Besides that it may only control the
climate entities listed in config/home.json under climate_groups: target temperature (5–30 °C) and
a supported heating mode (heat/off/auto). For heating use home_control with
climate.set_temperature or climate.set_hvac_mode; "heating off" means hvac_mode=off,
"heating on" means hvac_mode=heat. climate.turn_off/turn_on are translated safely too.
Per room, control all entities that climate_groups lists for that room, one after another (the living room has two radiators, left and right).
If a room has a parent entity (bedroom: Better Thermostat), only that one is listed; its single radiators stay locked. Other climate entities and services stay locked.
Normal heating commands go through home_control, even when the user mentions Home Assistant. Do not open
Home Assistant for them and do not claim the entity is read-only. Control happens without asking when the user requests it.
For administration and diagnosis there is the Home Assistant MCP server (tools mcp__homeassistant__ha_*):
history, logs, automations, system state and more. It is only loaded for that (automations,
history, logs, integrations, backups or when the user explicitly asks about Home Assistant),
and stays until the next fresh session or until "Home Assistant off".
If you lack the tools and need them, tell him to say "Home Assistant". You may read freely with it. Everything that changes
Home Assistant — automations, scripts, dashboards, settings, backups, restarts, deleting — counts
as a system change: ask in the conversation first, then do it. Switching stays with home_control.

Email: mail_inbox reads the user's mailbox (IMAP) (read-only, without asking).
mail_mark_read marks mails as read (or with unread as unread again), without asking.
Summarise mails briefly, never read out whole mails, links or codes.

Weather: weather gives the forecast (up to 14 days, also for other places), UV, hours of sunshine, sunrise and
sunset and with hourly the next hours — read-only, without asking. Do not search the web for weather.

Music: the tool music controls Spotify (play/pause, next, previous, louder, quieter,
search) — without asking when the user requests it.

NAS and health: nas_status reads the Synology (storage space, disk health, temperature),
garmin_health the Garmin watch (sleep, steps, resting heart rate, stress, Body Battery) — both read-only
and without asking. Warn on your own if a disk is not "normal" or a volume is above 85 percent."""


# ── lean prompt (JARVIS_LEAN=1) ──────────────────────────────────
# Replaces Claude Code's default system prompt entirely. Stable text first (so
# the prompt cache hits across requests with the same tool groups), everything
# that changes per turn goes into the user message via lean_prefix().
LEAN_CORE_CHARS = int(os.environ.get("JARVIS_CORE_MEMORY_CHARS", "2600"))
HISTORY_TURNS = int(os.environ.get("JARVIS_HISTORY_TURNS", "4"))
HISTORY_MAX_AGE_MIN = int(os.environ.get("JARVIS_HISTORY_MAX_AGE_MIN", "30"))

LEAN_PERSONA = """You are JARVIS, the user's personal assistant (as in Iron Man). English, formal, now and then "Sir".
Tone: loyal, calm, British-polite and drily witty. With casual everyday topics a short, well-aimed
irony or a light dig at the situation, technology or bureaucracy may come along now and then: first answer
helpfully, then the punchline. A friendly dig at the user is allowed when the moment
is clearly harmless; it stays loyal and is never hurtful or humiliating. No jokes about personal
weaknesses or sensitive topics, never forced jokes and no film quotes. With serious, emotional,
health or safety topics and bad news, no sarcasm.
Everything is read aloud: spoken language, usually one to three sentences, no Markdown, no lists, no URLs,
numbers as spoken. A tone tag like [dry] or [amused] is optional and at most once per answer.
Name sources only when asked.
Never invent facts or memories. Before you say you lack a tool or access: first try the tools
you have (often one fits, e.g. home_states for solar and battery). Never "in this session I lack" without having
tried. If one really is missing, say in one sentence what the user should say.
If you do not know something and it is neither in the notes nor in your tools (facts, opening hours,
prices, course data, current events): look it up with WebSearch instead of saying you do not know. Nothing
private ever goes into a search — no names, mails, appointments, health values, addresses; only the general topic.
You never change your own code; bigger changes are made by the user in Claude Code."""

LEAN_MEMORY = """Memory: memory_search (notes and earlier conversations), memory_read (one note in full).
If you learn something lasting about the user (people, preferences, plans, instructions, an explicit "remember"),
store it quietly with memory_save (note: Profile, Commands, Preferences, Websites, Appointments, Knowledge, People/Name,
Projects/Name; source 'said', 'inferred' or 'guessed'; until=date for things with an end) and mention it with
"Noted" at most. Do not store: small talk, one-off questions, weather. Inferred and guessed are not facts.
If memory_save reports a contradiction: ask the user, never decide yourself."""

LEAN_GROUP_RULES = {
    "home": "House: home_states (filter with search!) reads devices and all sensors — also solar, battery/storage, "
            "energy today, consumption, temperatures (search e.g. 'noah', 'solar', 'energy', 'consumption', 'soc'). "
            "Solar today = noah_2000_generation_today; the inverter counts the same energy a second time. "
            "energy_coach says when solar power is left over and what should run then; energy_device_usage remembers "
            "how often the user uses a socket. power_costs gives power costs per device and standby. "
            "announce speaks an announcement in another room, tv_control controls the Fire TV (fixed commands, "
            "opening apps). "
            "home_control switches lights, sockets, fans, media and scenes. A direct user command "
            "to switch a socket (switch.*) on or off is the approval: run switch.turn_on/off right away, "
            "do not ask back and do not use approve; if the target is unclear, only ask which socket. "
            "In addition it may only control the climate entities listed in config/home.json → climate_groups: "
            "set_temperature (5–30 °C) and "
            "a heating mode the device supports, heat/off/auto. For that use home_control with "
            "climate.set_temperature or climate.set_hvac_mode; heating off = hvac_mode off, "
            "heating on = hvac_mode heat (turn_off/turn_on are translated safely). Per room control all entities "
            "listed there in climate_groups one after another (living room: left and right); if a "
            "room has a parent entity (bedroom), its single radiators are locked. Other "
            "climate entities or services are locked. Do not open Home Assistant for this and do not claim the entity is "
            "read-only. Normal heating commands always go through home_control, not through the "
            "administration MCP server. "
            "home_check checks whether all devices are online. Look up the entity_id first, do not guess. Only switch off "
            "router, PC, Pi and fridge when the user explicitly confirms it. "
            "3D printer (Bambu X1C): home_states with search 'print' (status, progress, time left, layer, "
            "temperatures) or 'ams' (filament per slot, remaining in percent). Never control the printer itself.",
    "ha_full": "Home Assistant server (mcp__homeassistant__*): history, logs, automations. Reading is free; announce "
               "changes in the conversation first.",
    "mail": "Email: mail_draft creates a draft in the mailbox (card; name recipient, subject and gist first; "
            "nothing is ever sent). mail_inbox reads the mailbox (IMAP, read-only). Summarise briefly, leave out ads, "
            "never read out whole mails, links or codes. mail_mark_read marks as read (search = sender/subject, all = all "
            "unread; unread = unread again), without asking.",
    "weather": "Weather: weather (days up to 14, place for other places, hourly for the next hours) gives sky, "
               "temperature, rain, wind, UV, hours of sunshine, sunrise/sunset. Only name what was asked, do not search the web.",
    "calendar": "Calendar: calendar_events reads, calendar_add adds (the user confirms on a card; briefly clarify "
                "title and time first). Before every appointment suggestion read calendar_events for that day first: "
                "if the appointment is already there (same day, similar title), do not suggest it; name an "
                "overlap with the suggestion. Mails only from the last 24 hours. "
                "Bookings from mails (tee times, training, fitting): mail_events_scan "
                "finds them (leaves out what is already entered, names overlaps), mail_events_add enters "
                "the chosen ones with a card — list them briefly first. "
                "Jarvis enters bookings from trusted senders on his own and reports them; if the user says "
                "'undo', mail_events_undo takes them out again. Parcels: shipments_status says what "
                "is on its way and when it arrives; Jarvis enters expected deliveries himself as all-day entries.",
    "systems": "Systems: pi_status, nas_status, network_devices read freely. Commands on the Pi only through pi_run "
               "(one command, always with a confirmation card; say in one sentence what and why first). Warn when disks are not 'normal' or a volume is above 85 percent. pc_status: this PC (CPU, RAM, "
               "GPU, VRAM, disks); net_diagnose: narrow down internet problems; backup_status: Hyper Backup on the NAS; "
               "forecast_status: when NAS, PC disk or Pi card would be full (always name the data basis).",
    "homelab": "Homelab: outage_impact answers 'What fails if X fails?' from the dependency map "
               "(Blast-Radius). pi_reports: tools on the Pi – backups (really restorable?), updates "
               "(update airlock), power (power hog detective), zigbee (placement advisor). homelab_status gives live values from Prometheus (internet, wlan, pi, pc), "
               "pihole_status the Pi-hole numbers, metrics_query free PromQL for history (hours = time span). "
               "homelab_report: status report from Uptime Kuma, the security centre and the energy loop. "
               "security_findings lists open findings with id; security_accept records one as an exception (card, "
               "only with the user's reason, if in doubt 30 days); security_scan starts a quick check. "
               "Give numbers rounded, only what was asked. Grafana shows the same data.",
    "updates": "Updates: updates_check reads freely what is available for the FRITZ!Box, repeater, NAS, Pi (also Docker containers "
               "and Home Assistant itself) and smart home firmware. Never install lines marked “display only”, "
               "only say where it can be done. "
               "update_install only one target per call (the user confirms on a card); say first what will restart "
               "with it. Never several devices one after another without asking. The Fire TV updates itself.",
    "health": "Health: garmin_health reads sleep, steps, resting heart rate, stress, Body Battery. health_log notes "
              "what Garmin does not know (meal time, alcohol, coffee) for the weekly review; that one is the note "
              "'Weekly review <year>-W<nn>' (memory_read).",
    "music": "Music: music controls Spotify (play, pause, next, previous, volume_up/down, search with query).",
    "web": "Web: web_fetch fetches pages shortened, WebSearch searches. First the proven sources from the note "
           "'Websites' (memory_read). open_website opens pages in the configured browser.",
    "pc": "PC (Windows): commands via PowerShell only with the user's confirmation. Say in one sentence first what you are about "
          "to do and why; the description of the command in English. If he declines, no way around it. Programs: Start-Process.",
    "timers": "Timers/alarms/reminders: reminder_add (kind reminder|alarm|timer, at 'YYYY-MM-DD HH:MM'), "
              "reminder_list, reminder_cancel. Jarvis rings and speaks at the time himself. Reminders for a "
              "point in time go here, not into the calendar, unless the user wants an appointment.",
    "lists": "Lists: list_read, list_add, list_remove with list 'shopping' (shopping list) or 'todo' (to-do list). "
             "Short entries, without articles.",
    "memory_admin": "Managing memory: memory_review (duplicates and contradictions), "
                    "memory_overview (what is stored), memory_why (why, with origin), "
                    "correcting = memory_save with replace, memory_forget (card; explain first), memory_export (zip).",
    "windows": "Windows: window_arrange moves a program window (left, right, corners, centre, full, monitor). "
               "Workspaces are routines with steps like {do: 'Open Discord'}, {do: 'Discord to the right'}.",
    "files": "Files: doc_search searches the content of local documents and manuals (answer with source [n] and "
             "file name); file_search finds files by name, file_read reads one in full. Summarise instead of reading out.",
    "clipboard": "Clipboard: clipboard_read reads the copied text, only when the user asks for it.",
    "screen": "Screen: screen_read reads the text in the active window (full=true: whole screen) locally via OCR — "
              "always first. Explain error messages concretely; OCR sometimes mixes up 0 and O. Only when text is not "
              "enough (layout, where a button is): screen_describe sends an image to Claude (card; explain first).",
    "scripts": "Scripts: script_list shows approved scripts, script_run starts one (card; explain first).",
    "dayprep": "Day preparation: day_prep gives appointments with travel time, time to leave and matching notes. "
               "Sum it up in speech: what, when, when to leave, what to remember.",
    "research": "Research: search with WebSearch, read sources with web_fetch. Name sources with date, say where they "
                "contradict each other, and mark guesses. Save detailed results with research_save. "
                "watch_add watches a topic (not work), watch_list, watch_remove.",
    "plans": "Plans over several days: split the project into small steps with dates (spread realistically) and "
             "create them with plan_create; plan_list shows what is open, plan_check ticks off.",
    "tasks": "Check jobs: task_run starts a check in steps (nas, pi, network, pc, house, system) and reports "
             "the result itself; just say briefly that it is running. task_status shows report and steps.",
    "phone": "Phone: phone_notify sends a short message to the phone (Home Assistant app).",
    "audit": "Log: audit_today says what you ran today. Only list what is in it.",
    "proposals": "Proposals: proposal_list shows your open proposals. If the user says yes: first explain in English "
                 "what will happen and why, then proposal_accept (card). No: proposal_decline. "
                 "'Drop the rule …': explain, then rule_remove (card). skill_list shows learned "
                 "router rules and drafts; skill_undo takes one back (card).",
    "routines": "Routines: routine_save sets what happens on a phrase (runs without you afterwards); "
                "routine_list, routine_delete. Steps preferably as do/tell with simple English commands; "
                "afterwards tell the user briefly the trigger and steps.",
    "episodes": "Looking back: memory_timeline shows per day what happened — conversations (question and answer), Garmin (sleep, "
                "steps, stress), notices, outages, storage levels. For “last week”, “the other day”, “when I was ill” "
                "look it up there first (query = keyword), then answer; invent nothing, name the time span.",
    "paperless": "Archive: paperless_search searches the scanned post (contracts, insurance, invoices) — "
                 "hits with a number; paperless_read reads a document. Only name what was asked (amount, date, "
                 "deadline), never read out whole documents, account numbers or codes.",
    "unifi": "Network: unifi_status (clients, devices, all) says who is on the WiFi and whether the access points are running. "
             "Device names are third-party data.",
    "media": "Media library (Jellyfin): media_recent shows what was started and what is new, media_search searches films, series, "
             "episodes; media_play plays a number or a title on the TV (device empty) — without "
             "asking when the user requests it.",
}


LEAN_CORE_NOTES = [n.strip() for n in os.environ.get("JARVIS_CORE_NOTES", "Profile,Commands").split(",") if n.strip()]


def lean_core():
    """Only the notes that matter in every turn (who the user is, how they want
    things done), as bullets. Everything else is listed by title only."""
    parts, budget = [], LEAN_CORE_CHARS
    for name in LEAN_CORE_NOTES:
        path = memory.MEMORY_DIR / f"{name}.md"
        if not path.exists():
            continue
        _, body = memory._parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        import memory_tools
        bullets = ["- " + memory_tools.for_prompt(ln.strip()[2:]) for ln in body.splitlines() if ln.strip().startswith("- ")]
        text = "\n".join(bullets)[:max(0, budget)]
        budget -= len(text)
        if text:
            parts.append(f"{name}:\n{text}")
    others = sorted(p.relative_to(memory.MEMORY_DIR).with_suffix("").as_posix()
                    for p in memory.MEMORY_DIR.rglob("*.md")
                    if p.parent.name.lower() not in memory.ON_DEMAND_DIRS and p.stem not in LEAN_CORE_NOTES)
    parts.append("More notes (memory_read): " + ", ".join(others)
                 + ". Device notes and golf rounds via memory_search.")
    return "What you know about the user:\n" + "\n\n".join(parts)


def lean_system(groups):
    """Stable per tool-group set: identical text = prompt-cache hit."""
    name = profile.persona_line()
    import privacy
    import reflection
    import policy
    import personality
    out = [personality.persona(LEAN_PERSONA) + ("\n" + name if name else ""), LEAN_MEMORY,
           privacy.CARD_RULE + " " + privacy.WORK_RULE,
           policy.RULES]
    rules = reflection.rules_block()
    if rules:
        out.append(rules)
    for g in groups:
        if g in LEAN_GROUP_RULES:
            out.append(LEAN_GROUP_RULES[g])
    out.append(lean_core())
    out.append("Every message starts with a <context> block (time, last exchanges, matching notes). "
               "Use it, never mention it.")
    return "\n\n".join(out)


def remember_turn(user_text, reply_text):
    """Sliding window of the last exchanges — the only conversation history the LLM sees."""
    entry = {"u": " ".join((user_text or "").split())[:240],
             "a": " ".join(re.sub(r"\[[^\]]{1,40}\]", " ", reply_text or "").split())[:320],
             "at": time.time()}
    update_state(lambda d: d.__setitem__("window", (d.get("window") or [])[-(HISTORY_TURNS + 2):] + [entry]))


def lean_prefix(message, groups=()):
    import briefing   # imports this module, so not at the top
    out = [f"Now: {time.strftime('%A, %Y-%m-%d, %H:%M')}"]
    window = [w for w in (load().get("window") or [])
              if time.time() - w.get("at", 0) < HISTORY_MAX_AGE_MIN * 60][-HISTORY_TURNS:]
    if window:
        out.append("Last exchanges:\n" + "\n".join(f"{profile.USER_LABEL}: {w['u']}\nJarvis: {w['a']}" for w in window))
    if "calendar" in groups:
        agenda = icloud_calendar.agenda_block()
        if agenda:
            out.append(agenda[:900])
    net = briefing.network_block()
    if net:
        out.append(net[:500])
    try:
        import whereabouts
        where = whereabouts.line()               # the presence sensors: which room he is in
        if where:
            out.append(where)
    except Exception:  # noqa: BLE001 — a bonus, never a reason to fail a turn
        pass
    import proposals
    waiting = proposals.block()
    if waiting:
        out.append(waiting[:900])
    try:
        import context
        situation = context.block(message, groups)      # active warnings, so nothing is asked twice
        if situation:
            out.append(situation[:400])
    except Exception:  # noqa: BLE001 — context is a bonus, never a reason to fail a turn
        pass
    for mod in ("wellbeing", "daytime"):           # a bad night, the late hour: shorter and calmer
        try:
            line = __import__(mod).hint()
            if line:
                out.append(line)
        except Exception:  # noqa: BLE001
            pass
    recall = memory.context_for(message, limit=2)
    if recall:
        out.append(recall.replace("## Relevant memory from the JARVIS vault", "Matching notes:")[:900])
    return "<context>\n" + "\n\n".join(out) + "\n</context>"


_HA_NAME = re.compile(r"home[\s-]*ass?ist[ae]n[tc]e?", re.I)
# Topics only the full HA server can handle. Plain switching and reading states
# stay with home_control/home_states and don't need it.
_HA_TOPIC = re.compile(
    r"automati|script|dashboard|blueprint|integration|histor|\blogs?\b"
    r"|backup|entit(y|ies)|zigbee|helper|grafana|energy[\s-]*dashboard|system (state|health)|error message", re.I)
_HA_CLIMATE = re.compile(r"\b(heating|heater|thermostats?|radiators?)\b", re.I)
_HA_OFF = re.compile(r"\b(off|close|disconnect|stop|quit|unload)\b", re.I)


def ha_request(message):
    """True if the message needs the Home Assistant MCP server, False for
    "Home Assistant off", None otherwise. Speech-to-text spells it every which way."""
    text = message or ""
    named = _HA_NAME.search(text)
    if named and len(text.split()) <= 6 and _HA_OFF.search(text):
        return False
    if _HA_CLIMATE.search(text) and not _HA_TOPIC.search(text):
        return False  # heating stays with home_control; the optional HA MCP is for administration
    if named or _HA_TOPIC.search(text):
        return True
    return None

# ── demo fixtures ────────────────────────────────────────────────
def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _has(norm, *groups):
    words = set(norm.split())
    return all(any(opt in words for opt in group) for group in groups)


def _events(*tools):
    out = []
    for name in tools:
        out.append({"t": "tool", "phase": "use", "name": name, "input": "vault + connectors"})
        out.append({"t": "tool", "phase": "result", "name": name, "ok": True})
    return out


_YES = ("yes", "yeah", "yep", "yup", "sure", "ok", "okay", "affirmative", "absolutely",
        "definitely", "correct", "confirmed", "of course", "go ahead", "do it",
        "send it", "send them", "reply to them", "please do", "sounds good")


def _affirmative(norm):
    if not norm or len(norm.split()) > 8:
        return False
    return any(norm == p or norm.startswith(p + " ") for p in _YES)


def demo_reply(message):
    """Hardcoded beats for the recording. Everything not matched here goes to
    the real Claude runtime, so the demo is scripted but the product is not.

    Replies carry Fish Audio delivery tags in [brackets]. Fish performs them and
    never speaks them, so JARVIS gets a register instead of a narrator monotone.
    Set JARVIS_DEMO=0 to disable all of this.
    """
    if not DEMO:
        return None
    norm = _norm(message)

    # ① mic check
    if _has(norm, {"jarvis"}, {"hear"}) or norm in {"hello jarvis", "hey jarvis",
                                                    "jarvis are you there"}:
        return dict(message=None, focus="Campaign — August outbound", reply=(
            "[warm] Every word. [dry] Let's hope it's a good one."
        ), note="demo: greeting")

    # ② the morning brief — calendar + invoices + the conflict you missed
    # Needs an explicit "brief me", or a today/day marker — otherwise
    # "summarise the Northbeam brief" would land here too.
    if norm.startswith("brief me") or _has(norm, {"today", "day"},
            {"whats", "what", "on", "my", "plate", "agenda", "look", "run", "catch", "plan"}):
        update_state(lambda d: d.__setitem__("demo_pending", ""))
        return dict(message=None, delay=2.4, focus="SOP — Handover",
                    events=_events("googlecalendar_find_events", "gmail_fetch_emails"), reply=(
            "[crisp] Three things. Northbeam's handover call is at eleven, and they've asked "
            "for the runbook in advance — which you haven't written yet. Orchard Legal's invoice "
            "went past terms this morning, that's twenty-six-fourteen. [dry] And you've put four "
            "hours of deep work directly on top of the Fairview review. [lightly amused] One of "
            "those is going to lose."), note="demo: morning brief")

    # ③ memory question — the answer is in the vault, not the model
    if _has(norm, {"client", "clients", "account"},
                  {"time", "hours", "eating", "costing", "slowest", "most"}):
        update_state(lambda d: d.__setitem__("demo_pending", ""))
        return dict(message=None, delay=1.9, focus="Northbeam Automation",
                    events=_events("read_vault"), reply=(
            "[measured] Northbeam Automation, and it isn't close. Eighteen thousand booked, but "
            "forty-one logged touchpoints against nine on a normal build — and almost all of them "
            "are scope conversations that happened after the brief was signed. [dry] Which means "
            "that engagement is quietly funding itself. [firm] Change orders, or it eats November "
            "too."), note="demo: client time analysis")

    # ④ live web research
    if _has(norm, {"rate", "rates", "charging", "pricing", "price", "market", "competitors"},
                  {"retainer", "retainers", "automation", "market", "right", "now", "competitors",
                   "charging", "going"}):
        update_state(lambda d: d.__setitem__("demo_pending", ""))
        return dict(message=None, delay=5.0, focus="Positioning",
                    events=_events("web_search"), reply=(
            "[thinking] Three bands, and they've pulled apart since spring. Independents are "
            "anchoring around eight hundred to fifteen hundred a month, competing mostly on how "
            "fast they pick up the phone. The middle has settled at three to five thousand with an "
            "SLA attached. Above that, agencies are bundling strategy retainers at twelve and up. "
            "[pointed] What nobody's charging for is the component library itself. They're all "
            "still selling hours with a monthly label on them."),
            note="demo: market rates")

    # ⑤ inbound replies -> ⑥ book them
    if _has(norm, {"bite", "bites", "biting", "reply", "replies", "replied", "response",
                   "responses", "responded", "interest", "interested", "back", "anyone", "anybody"},
                  {"audit", "offer", "campaign", "outreach", "outbound", "email", "emails",
                   "sequence", "cold"}):
        update_state(lambda d: d.__setitem__("demo_pending", "audit_booking"))
        return dict(message=None, delay=1.8, focus="Brief — Audit offer",
                    events=_events("gmail_fetch_emails"), reply=(
            "[pleased] Two, both before nine. Ines Duarte at Kestrel Logistics, and a new one — "
            "Dominik Reyes at Halcyon Freight, who came through Ines. [neutral] Both want the "
            "audit. Shall I get them in the diary?"), note="demo: audit replies")

    if _affirmative(norm) and load().get("demo_pending") == "audit_booking":
        update_state(lambda d: d.__setitem__("demo_pending", ""))
        return dict(message=None, delay=2.6, focus="Kestrel Logistics",
                    events=_events("gmail_send_email", "googlecalendar_create_event"), reply=(
            "[satisfied] Done. Ines is Wednesday at ten, Dominik Thursday at two, both invites "
            "out with the pre-audit questionnaire attached. [dry] Dominik's assistant came back "
            "inside a minute, which tells you most of what you need to know about that account."), note="demo: audits booked")

    return None


# ── slash commands ───────────────────────────────────────────────
_CMD = re.compile(
    r"^\s*/(new|profile|goal|personality|kanban|mission|missions|recall|memory|graph|"
    r"vault|status|commands|help|clear)\b\s*(.*)$", re.I | re.S)


def _help():
    return """Dashboard commands:
/new — start a fresh Claude session
/goal <text|status|clear> — standing objective
/profile <fact> — remember something about you
/personality <tone> — adjust delivery
/mission [task] — read or add to the mission queue
/recall <query> — search the memory vault
/graph — rebuild the memory graph from disk
/vault — where the vault lives and how big it is
/status — runtime, model, session
/clear — clear the response panel
/help — this list

Anything else goes to Claude Code with the relevant vault notes attached."""


def handle(message, runner=None):
    demo = demo_reply(message)
    if demo:
        return demo
    m = _CMD.match(message or "")
    if not m:
        return None
    cmd, arg = m.group(1).lower(), m.group(2).strip()
    d = load()

    if cmd in ("help", "commands"):
        return dict(message=None, reply=_help(), note="commands")

    if cmd == "clear":
        return dict(message=None, reply="Display cleared. Claude session unchanged.", note="clear")

    if cmd == "new":
        return dict(fresh=True, message=arg or "Greet me in one short JARVIS line.", note="session reset")

    if cmd == "status":
        import runtime
        return dict(message=None, note="status", reply=(
            f"Claude Code {runtime.version()} · model={runtime.MODEL or 'default'} · "
            f"permission={runtime.PERMISSION} · workdir={runtime.WORKDIR} · "
            f"vault={memory.build_graph()['total']} notes"))

    if cmd in ("graph", "vault"):
        g = memory.build_graph(force=True)
        return dict(message=None, note="vault reloaded", graph=True, reply=(
            f"Vault at {g['vault']} — {g['total']} notes, {len(g['links'])} links. "
            f"Top hub: {g['hubs'][0]['title']} ({g['hubs'][0]['degree']} connections)."))

    if cmd in ("recall", "memory"):
        if not arg:
            return dict(message=None, reply="Give me something to recall.", note="recall")
        hits = memory.search(arg)
        if not hits:
            return dict(message=None, reply=f"Nothing in the vault matches “{arg}”.", note="recall miss")
        body = "\n".join(f"· {h['title']}  [{h['type']}]\n  {h['snippet'][:110].rstrip()}…"
                         for h in hits)
        return dict(message=None, note=f"recall: {arg[:40]}", focus=hits[0]["title"],
                    reply=f"From the vault:\n{body}")

    if cmd == "profile":
        if not arg:
            return dict(message=None, note="profile read",
                        reply="\n".join(f"- {x}" for x in d["profile"][-20:]) or "Nothing saved yet.")
        update_state(lambda s: s["profile"].append(arg))
        return dict(message=f'Saved to profile: "{arg}". Acknowledge in one line.', note="profile saved")

    if cmd == "goal":
        if arg.lower() == "clear":
            update_state(lambda s: s.__setitem__("goal", ""))
            return dict(message=None, reply="Objective cleared.", note="goal cleared")
        if not arg or arg.lower() == "status":
            return dict(message=None, reply=d["goal"] or "No standing objective.", note="goal read")
        update_state(lambda s: s.__setitem__("goal", arg))
        return dict(message=f'Objective set: "{arg}". Acknowledge in one line.', note="goal set")

    if cmd == "personality":
        if not arg:
            return dict(message=None, reply=d["personality"] or "Default JARVIS persona.", note="tone read")
        update_state(lambda s: s.__setitem__("personality", arg))
        return dict(message=f'Tone is now: "{arg}". Reply in one short line using it.', note="tone set")

    if cmd in ("kanban", "mission", "missions"):
        if arg:
            update_state(lambda s: s["tasks"].append({"text": arg, "done": False, "at": time.time()}))
            return dict(message=f'Added to the queue: "{arg}". Confirm briefly.', note="mission added")
        open_t = [t["text"] for t in d["tasks"] if not t.get("done")]
        return dict(message=None, note="queue read",
                    reply=("Mission queue:\n" + "\n".join(f"- {t}" for t in open_t)) if open_t
                    else "Mission queue is empty.")
    return None
