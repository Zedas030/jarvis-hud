"""
Suggestion module: twice a week Jarvis proposes one new ability that fits the
user's habits, with a reason.

Basis (last 14 days, work topics filtered out before the model sees anything):
the usage log (what still goes to the LLM, which tool groups, at what times),
the routines that exist, the learned rules and the earlier suggestions (so
nothing comes twice). One background call picks a single suggestion:

  routine     something the user does by voice again and again → a routine
  reminder    a recurring moment worth a reminder
  idea        needs new code → only explained and noted; Jarvis never changes
              his own code, that stays with the user in Claude Code

Routines and reminders are only set up after a confirmation card in the next
conversation (proposals.py). Every suggestion is listed in the note
Memories/Suggestions with its status (open, accepted, declined).

When: JARVIS_SUGGEST_DAYS (weekday numbers, Monday = 0; default "1,4" = Tuesday
and Friday) at JARVIS_SUGGEST_TIME (18:30), never in the quiet hours.
"""
import collections
import datetime as dt
import json
import os
import pathlib
import re
import time

import background
import events
import memory
import privacy
import proposals

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
USAGE_LOG = ROOT / "logs" / "usage.jsonl"
DAYS = [int(x) for x in os.environ.get("JARVIS_SUGGEST_DAYS", "1,4").split(",") if x.strip().isdigit()]
AT = os.environ.get("JARVIS_SUGGEST_TIME", "18:30").strip()
ENABLED = os.environ.get("JARVIS_SUGGEST", "1").strip() not in ("0", "false", "no", "off")
NOTE = "Suggestions"
STATUS = {"accepted", "declined", "open"}

CAPABILITIES = ("lights/sockets/scenes (Home Assistant), timers, alarms, reminders, shopping and to-do list, "
                "routines on a phrase, weather, reading/adding calendar entries, reading mails, Spotify, Pi/NAS/network status, "
                "Garmin health data, weekly review, travel time to appointments, parcel status, PC volume/apps, "
                "web search, memory notes, daily briefing")

SYSTEM = ("You are JARVIS and suggest exactly one new ability to the user that fits his observed "
          "habits. Concrete, useful, not already there, nothing from work. Answer with JSON only.")


def _usage(days=14, now=None):
    since = (now or time.time()) - days * 86400
    rows = []
    try:
        with USAGE_LOG.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                    t = time.mktime(time.strptime(r["at"], "%Y-%m-%d %H:%M:%S"))
                except (ValueError, KeyError):
                    continue
                if t >= since and not str(r.get("route", "")).startswith("background"):
                    rows.append(dict(r, t=t))
    except OSError:
        pass
    return rows


def habits(now=None):
    """A compact, work-free picture of the last two weeks."""
    rows = [r for r in _usage(now=now) if not privacy.is_work(r.get("message", ""))]
    if not rows:
        return ""
    routes = collections.Counter(r["route"].split(":")[0] + (":" + r["route"].split(":")[1]
                                                             if r["route"].startswith("local:") else "") for r in rows)
    groups = collections.Counter(g for r in rows for g in r.get("groups") or [] if g != "memory")
    hours = collections.Counter(time.localtime(r["t"]).tm_hour for r in rows)
    llm = collections.Counter(" ".join(r.get("message", "").lower().split())[:70]
                              for r in rows if r["route"].startswith(("llm", "fallback")))
    days = collections.Counter(time.strftime("%a", time.localtime(r["t"])) for r in rows)
    return "\n".join([
        f"Requests in 14 days: {len(rows)}",
        "Routes: " + ", ".join(f"{k} {v}" for k, v in routes.most_common(8)),
        "Tool groups: " + ", ".join(f"{k} {v}" for k, v in groups.most_common(8)),
        "Times (hour: count): " + ", ".join(f"{h}: {n}" for h, n in sorted(hours.items())),
        "Weekdays: " + ", ".join(f"{d} {n}" for d, n in days.most_common()),
        "Frequent questions that still go to the language model:\n" + "\n".join(f"- {m} ({n}×)" for m, n in llm.most_common(12)),
    ])


def _note_path():
    return memory.MEMORY_DIR / f"{NOTE}.md"


def earlier():
    p = _note_path()
    if not p.exists():
        return []
    return [ln[2:].strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.startswith("- ")]


def _record(title, why, status="open"):
    p = _note_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    if not p.exists():
        p.write_text(f"---\ntype: memory\nimportance: low\nupdated: {today}\n---\n\n# {NOTE}\n\n"
                     "Abilities Jarvis has suggested. Belongs to [[Profile]].\n", encoding="utf-8")
    body = re.sub(r"(?m)^updated: .*$", f"updated: {today}", p.read_text(encoding="utf-8"), count=1)
    p.write_text(body.rstrip() + f"\n- {today} · {status} · {title} — {why}\n", encoding="utf-8")


def mark(proposal, status):
    """Updates the status of a suggestion in the note (no-op for proposals from other modules)."""
    if proposal.get("source") != "suggestions" or status not in STATUS:
        return
    p = _note_path()
    if not p.exists():
        return
    body = p.read_text(encoding="utf-8")
    pat = re.compile(r"(?m)^(- [\d-]+ · )open( · " + re.escape(proposal["title"]) + r" — )")
    p.write_text(pat.sub(lambda m: m.group(1) + status + m.group(2), body, count=1), encoding="utf-8")


def _prompt(now):
    import reflection
    import routines
    rs = [r.get("name") for r in routines.load()]
    return ("Observed habits:\n" + (habits() or "(hardly any data yet)") +
            f"\n\nNow: {now.strftime('%A, %Y-%m-%d %H:%M')}"
            f"\nAlready there: {CAPABILITIES}"
            f"\nRoutines: {', '.join(rs) or 'none'}"
            f"\nLearned rules: {'; '.join(reflection.rules_list()) or 'none'}"
            "\nEarlier suggestions (do not repeat):\n" + ("\n".join(f"- {e}" for e in earlier()[-15:]) or "- none") +
            "\n\nSuggest exactly one new ability. JSON:\n"
            "{\"title\": \"short\", \"reason\": \"one sentence, which habit it fits\", "
            "\"kind\": \"routine|reminder|idea\", "
            "\"routine\": {\"name\": \"...\", \"triggers\": [\"lower-case phrase\"], \"steps\": "
            "[{\"do\": \"simple English command\"} or {\"tell\": \"English question\"} or {\"say\": \"text\"}]}, "
            "\"reminder\": {\"at\": \"YYYY-MM-DD HH:MM\", \"text\": \"...\"}}\n"
            "routine/reminder only with the matching kind. What would need new code is kind idea.")


def suggest(now=None):
    """Creates one suggestion and announces it. Returns the proposal id or None."""
    import routines
    now = now or dt.datetime.now()
    result = background.parse_json(background.ask("suggestion", SYSTEM, _prompt(now))) or {}
    title = " ".join(str(result.get("title", "")).split())[:100]
    why = " ".join(str(result.get("reason", "")).split())[:300]
    if not title or not why or privacy.is_work(title, why, json.dumps(result, ensure_ascii=False)):
        return None
    if any(title.lower() in e.lower() for e in earlier()):
        return None
    kind, payload = "idea", {}
    if result.get("kind") == "routine" and isinstance(result.get("routine"), dict):
        r = result["routine"]
        payload = {"name": str(r.get("name") or title)[:60],
                   "triggers": [str(t).lower()[:80] for t in r.get("triggers") or [] if str(t).strip()],
                   "steps": r.get("steps") or []}
        kind = "routine" if payload["triggers"] and not routines.validate(payload["steps"]) else "idea"
    elif result.get("kind") == "reminder" and isinstance(result.get("reminder"), dict):
        e = result["reminder"]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", str(e.get("at", ""))) and e.get("text"):
            kind, payload = "reminder", {"kind": "reminder", "at": e["at"], "text": str(e["text"])[:120]}
    pid = proposals.add(kind, title, why, payload if kind != "idea" else {}, source="suggestions")
    _record(title, why)
    tail = ("Shall I set it up? I will show you a confirmation card first." if kind in ("routine", "reminder")
            else "That would have to be built in Claude Code; I have noted it under Suggestions.")
    events.publish("say", f"Sir, a suggestion: {title}. {why} {tail}", kind="suggestion")
    return pid


def due(now):
    hh, mm = (int(x) for x in AT.split(":"))
    return now.weekday() in DAYS and (now.hour, now.minute) >= (hh, mm) and not events.quiet_now(now)


def step(now=None):
    now = now or dt.datetime.now()
    if due(now) and background.claim("suggestion", now.date().isoformat()):
        suggest(now)


def start():
    if ENABLED:
        background.loop("suggestions", 600, step)
