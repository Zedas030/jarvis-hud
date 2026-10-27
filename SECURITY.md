# Security

The model is never the security boundary. Permissions are enforced by code in `jarvis_mcp.handle_call`,
`policy.py` and the HUD confirmation gate; prompts only explain them.

## Action classes (`policy.py`)

| Class | Meaning | Gate |
|---|---|---|
| READ | reads, changes nothing | none |
| LIMITED_WRITE | small, reversible change (lamp, list entry, note bullet, timer, window) | none — but a card once untrusted content was read in the same request |
| CONFIRM | external effect, deletion, calendar, mail draft, scripts, rules | always a confirmation card |
| BLOCKED | never | refused in code |

Unknown tools default to CONFIRM. Blocked Home Assistant domains: `lock`, `alarm_control_panel`,
`camera`, `siren`, `valve`, `update`, `button`. `light`, `switch`, `fan`, `media_player` and `scene`
can be switched. Climate control is limited to the `climate.*` entities listed in `config/home.json`
under `climate_groups`; only `set_temperature` from 5–30 °C and a supported `set_hvac_mode` of `heat`, `off`
or `auto` are allowed.

Bookings from mail (`mail_events.py`): from senders whose address is at a domain in
`JARVIS_MAIL_EVENTS_TRUSTED` (default PC CADDIE, golftimer, Golf House) they are entered without a
card — the one exception to "background jobs only propose", chosen by the user. Fixed code reads
the calendar attachment, the model plays no part; links are stripped; every entry is announced and
`mail_events_undo` removes it (only entries Jarvis created, recognised by their own UID). Everything
else, and every other sender, goes through one confirmation card. A forged sender is the remaining
risk; the mail provider's spam filter guards against it.

Deliveries (`shipments.py`) are part of the same exception: an all-day entry "📦 Shop · Carrier"
on the expected day, from shipping mails of any sender (shops change too often for a list), with
the tracking number but never a link; announced, moved when the day changes, and removed by the
same `mail_events_undo`. Worst case of a forged shipping mail is one such note in the calendar.
Same-day services and work mails stay out. With `TRACK17_API_KEY`, 17TRACK (a third party) is told
each tracking number to follow it across carriers — nothing else (no address, no shop, no mail
content); with only `DHL_API_KEY`, DHL itself is asked about DHL numbers.

Several mailboxes (`mailboxes.py`, `MAIL2_…`): all read-only except drafts (APPEND) and
"mark as read"; one failing account never blocks the others. Every module that reads mail over a
period (inbox, bookings, deliveries, parcels) goes through `mailboxes.recent` and never looks back
more than `JARVIS_MAIL_LOOKBACK_HOURS` (default 24) — less foreign content read, fewer old mails
to act on.

Before a calendar entry is offered or entered — the model's `calendar_add`, bookings from mail,
deliveries — code reads that day first (`icloud_calendar.check`): an entry with a similar title on
the same day means nothing is offered (for `calendar_add`, the request is refused before any card);
an overlap with another entry is named on the card and in the announcement.

Updates go only through `update_install` (CONFIRM, `updates.py`): one target per card, fixed commands
(the model picks the target, never what runs), nothing in the background. DSM itself and Docker images
are reported, not installed; so are FRITZ! repeaters (version read without login, compared with
download.avm.de) and smart-home firmware HA lists without an update entity (compared with the newest
device of the same model in the house). Checking talks to version.home-assistant.io, download.avm.de and
the image registries anonymously — it sends image names, nothing about the house.

## Confirmation cards

- Shown in the HUD via Claude Code's `--permission-prompt-tool` (or `fallback_llm.run_tool` for the
  stand-ins). No answer within `JARVIS_PERM_TIMEOUT` (75 s) means no.
- The card shows Jarvis' German explanation (`begruendung`), the action, the target, the details
  and the consequence (`policy.describe`). Only "bestätigt"/"nein" by voice — a bare "ja" does not count.
- Each card covers exactly one call; an earlier yes never authorises a later action.
- Without an open HUD nobody can confirm, so the answer is no.

## Untrusted content

Mail, web pages, calendar entries, files, clipboard, screen text and day preparation are wrapped in
«FREMDE DATEN» markers (fake end markers inside are defused). After such a tool, every
LIMITED_WRITE call in the same request needs a card ("taint"). One MCP process per request in lean mode.

## Privacy

- Work topics are not logged, evaluated, saved, planned or watched (`privacy.py`, `JARVIS_WORK_TERMS`).
- Screen reading only on request, local Windows OCR; refused while a password manager, banking or
  messenger window is visible. The image never leaves the PC.
- Screen description (`vision.py`) only on request with a card for every picture; same window block; the
  picture goes to Claude in a call without any tools and is deleted afterwards. No mouse or keyboard control.
- Files only below `JARVIS_FILE_ROOTS`; path traversal is resolved and refused.
- The Gemini free tier may train on prompts, so it never sees mail, calendar, health or briefing turns.
- Background jobs that read conversations or health data use Claude or Groq/Ollama only.

## Learning without self-modification

- Router rules learned from Claude are drafts. They become active only after checks (devices exist and are
  switchable, no clash with routine triggers) and a confirmation card; every version is kept in
  `config/learned.history.jsonl` and can be undone (card). Checks only read — they never switch anything.
- A memory entry that contradicts an existing one is never overwritten; Jarvis asks the user.
- Check tasks (`tasks.py`) only read; findings come with a recovery plan in words, nothing is repaired automatically.
- The resource manager only unloads or pauses Jarvis' own components when they are idle; it never touches other programs.

## Emergency stop

"Jarvis, stopp alles" / "Not-Aus" (local, no LLM): open cards are denied, a running Claude process
is stopped, proactive messages, background jobs and planned commands pause for `JARVIS_PAUSE_HOURS`.
Alarms still ring. "weitermachen" resumes.

## Audit

`logs/audit.jsonl`: every tool call (class, shortened arguments with secrets masked, success),
every card decision, local actions, pauses. 90 days. "Was hast du heute gemacht?" reads it.

## Network

The server binds to `127.0.0.1` with a per-launch token, host and origin checks. Remote devices only
via `tailscale serve` on a second local port, paired with one-time codes, keys stored as hashes.

## Reporting

Private project. Problems go to the owner directly; never paste `.env` content into issues or logs.
