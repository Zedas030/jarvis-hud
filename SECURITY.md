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
`camera`, `siren`, `valve`, `update`, `button`. Only `light`, `switch`, `fan`, `media_player` and
`scene` can be switched at all.

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
- Files only below `JARVIS_FILE_ROOTS`; path traversal is resolved and refused.
- The Gemini free tier may train on prompts, so it never sees mail, calendar, health or briefing turns.
- Background jobs that read conversations or health data use Claude or Groq/Ollama only.

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
