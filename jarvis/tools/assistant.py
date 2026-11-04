"""
Organisation and research.

  mail_draft     a draft in the mailbox's Drafts folder (IMAP APPEND, flag \\Draft) —
                 never sent; always a confirmation card (policy CONFIRM)
  day_prep       "What do I need to know for tomorrow?": appointments with travel time,
                 matching notes and mails, people notes; work appointments only counted
  research_save  a researched answer with sources and date as a note under Research/
  watchlist      "Let me know when there is news about X": up to JARVIS_WATCH_MAX (5)
                 topics, each checked every few days by a background call with web
                 search (Claude only, at most JARVIS_WATCH_PER_DAY (2) checks a day);
                 Jarvis only speaks up when the model finds something new
  plans          "Plan my move over two weeks": a note Projects/<title> with a
                 dated checklist and a reminder per step (default 9:00 that day)

Work stays out (privacy.py): no work topics watched, saved or planned; work
appointments are counted in day_prep but not detailed.
"""
import datetime as dt
import email.message
import email.utils
import imaplib
import json
import os
import pathlib
import re
import time

import memory
import privacy

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
WATCH = pathlib.Path(os.environ.get("JARVIS_WATCHLIST", ROOT / "logs" / "watchlist.json"))
WATCH_MAX = int(os.environ.get("JARVIS_WATCH_MAX", "5"))
WATCH_PER_DAY = int(os.environ.get("JARVIS_WATCH_PER_DAY", "2"))
PLAN_REMIND = os.environ.get("JARVIS_PLAN_REMIND", "09:00").strip()


def _slug(name):
    return re.sub(r'[\\/:*?"<>|\[\]#]', "-", " ".join((name or "").split())).strip(" .-")[:80]


# ── mail draft ───────────────────────────────────────────────

def _drafts_folder(box):
    typ, rows = box.list()
    names = []
    for raw in rows or []:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        m = re.search(r'\((?P<flags>[^)]*)\) "?(?P<sep>[^" ]*)"? (?P<name>.+)$', line)
        if not m:
            continue
        name = m.group("name").strip().strip('"')
        if "\\Drafts" in m.group("flags"):
            return name
        names.append(name)
    # folder names as mail providers use them, English and German (IMAP UTF-7 for "Entwürfe")
    for cand in ("Drafts", "Entwürfe", "Entw&APw-rfe", "INBOX.Drafts", "INBOX/Drafts", "[Gmail]/Drafts", "[Gmail]/Entw&APw-rfe"):
        if cand in names:
            return cand
    return None


def build_draft(to, subject, body, sender):
    msg = email.message.EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg.set_content(body)
    return msg


def mail_draft(to, subject, body, account=""):
    import mailboxes
    acc = mailboxes.find(account)
    if not acc:
        return "Mailbox not connected (MAIL_IMAP_HOST, MAIL_USER, MAIL_PASSWORD)."
    to, subject, body = (to or "").strip(), " ".join((subject or "").split()), (body or "").strip()
    if not (to and subject and body):
        return "For a draft I need recipient, subject and text."
    if not re.fullmatch(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+(\s*,\s*[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+)*", to):
        return f"“{to}” is not a valid mail address."
    box = imaplib.IMAP4_SSL(acc["host"], acc["port"], timeout=20)
    try:
        box.login(acc["user"], acc["password"])
        folder = _drafts_folder(box)
        if not folder:
            return "I cannot find the drafts folder in the mailbox."
        msg = build_draft(to, subject, body, acc["user"])
        quoted = '"' + folder.replace('"', '\\"') + '"'
        typ, _ = box.append(quoted, "(\\Draft \\Seen)", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        if typ != "OK":
            return "The mailbox refused the draft."
    finally:
        try:
            box.logout()
        except Exception:  # noqa: BLE001
            pass
    where = f" of {acc['name']}" if len(mailboxes.accounts()) > 1 else ""
    return f"The draft to {to} is in the folder {folder}{where}. I have not sent anything."


# ── day preparation ──────────────────────────────────────────

def day_prep(day=None, events=None):
    import icloud_calendar as cal
    day = day or (dt.date.today() + dt.timedelta(days=1))
    if isinstance(day, str):
        day = dt.date.fromisoformat(day[:10])
    try:
        evts = cal.events(day, 1) if events is None else events
    except Exception as e:  # noqa: BLE001
        return f"Calendar not readable: {e}"
    private = [e for e in evts if not privacy.is_work(e.get("title", ""), e.get("location", ""))]
    work = len(evts) - len(private)
    lines = [f"{day.strftime('%A, %Y-%m-%d')}: {len(private)} private appointment(s)"
             + (f", plus {work} work appointment(s) (I leave out the details)" if work else "") + "."]
    for e in private:
        when = "all day" if e.get("allday") else e["start"].strftime("%H:%M")
        row = f"- {when} {e.get('title', '?')}" + (f" in {e['location']}" if e.get("location") else "")
        if e.get("location") and not e.get("allday"):
            try:
                import commute
                dest = commute.geocode(e["location"])
                mins = commute.drive_minutes(dest) if dest else None
                if mins:
                    row += f" — {mins} min drive, leave at {commute.leave_at(e['start'], mins).strftime('%H:%M')}"
            except Exception:  # noqa: BLE001
                pass
        words = [w for w in re.split(r"\W+", e.get("title", "")) if len(w) > 3][:3]
        notes = []
        for w in words:
            for hit in memory.search(w, limit=2):
                if hit["title"] not in notes and not hit["title"].startswith("Conversation"):
                    notes.append(hit["title"])
        if notes:
            row += f" · notes: {', '.join(notes[:3])}"
        lines.append(row)
    if not private and not work:
        lines.append("No appointments.")
    return "\n".join(lines)


# ── research notes ───────────────────────────────────────────

def research_save(title, summary, sources=None):
    title, summary = _slug(title), (summary or "").strip()
    if not title or not summary:
        return "Title and summary are missing."
    if privacy.is_work(title, summary):
        return "Not saved: I do not remember work topics."
    folder = memory.MEMORY_DIR / "Research"
    folder.mkdir(parents=True, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    src = "\n".join(f"- {s}" for s in (sources or []) if str(s).strip()) or "- (no sources given)"
    path = folder / f"{title} {today}.md"
    path.write_text(f"---\ntype: research\nimportance: low\nupdated: {today}\n"
                    f"expires: {(dt.date.today() + dt.timedelta(days=365)).isoformat()}\n---\n\n# {title}\n\n"
                    f"Research of {dt.date.today().isoformat()}. Belongs to [[Knowledge]].\n\n{summary}\n\n"
                    f"## Sources\n\n{src}\n", encoding="utf-8")
    return f"Saved as Research/{path.stem}."


# ── watched topics ───────────────────────────────────────────

def _watch_load():
    try:
        return json.loads(WATCH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _watch_save(items):
    WATCH.parent.mkdir(parents=True, exist_ok=True)
    WATCH.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def watch_add(topic, every_days=7):
    topic = " ".join((topic or "").split())[:100]
    if not topic:
        return "Which topic?"
    if privacy.is_work(topic):
        return "I do not watch work topics."
    items = _watch_load()
    if any(i["topic"].lower() == topic.lower() for i in items):
        return f"I am already watching “{topic}”."
    if len(items) >= WATCH_MAX:
        return f"I am already watching {WATCH_MAX} topics — please drop one first."
    items.append({"topic": topic, "every_days": max(1, min(int(every_days or 7), 30)), "last_run": 0, "last": ""})
    _watch_save(items)
    return f"I will keep an eye on “{topic}” and speak up when there is news (checked every {items[-1]['every_days']} days)."


def watch_list():
    items = _watch_load()
    if not items:
        return "I am not watching any topics right now."
    return "\n".join(f"- {i['topic']} (every {i['every_days']} days" +
                     (f", last on {time.strftime('%Y-%m-%d', time.localtime(i['last_run']))}" if i["last_run"] else "")
                     + ")" for i in items)


def watch_remove(topic):
    items = _watch_load()
    keep = [i for i in items if topic.lower() not in i["topic"].lower()]
    if len(keep) == len(items):
        return "I am not watching this topic."
    _watch_save(keep)
    return f"No longer watched: {', '.join(i['topic'] for i in items if i not in keep)}."


WATCH_SYSTEM = ("You are JARVIS and check with web search whether there is news on a topic. Only solid sources, "
                "with dates. Answer with JSON only.")


def watch_step(now=None):
    """Checks due topics (oldest first, at most WATCH_PER_DAY a day). Returns the topics checked."""
    import background
    import events
    import fallback_llm
    import runtime
    import commands
    now = now or time.time()
    if runtime.runtime_kind() != "claude" or fallback_llm.claude_blocked(commands.load().get("usage")):
        return []                                   # web search needs Claude
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    done_today = sum(1 for i in _watch_load() if time.strftime("%Y-%m-%d", time.localtime(i["last_run"])) == today)
    checked = []
    for item in sorted(_watch_load(), key=lambda i: i["last_run"]):
        if done_today + len(checked) >= WATCH_PER_DAY:
            break
        if now - item["last_run"] < item["every_days"] * 86400:
            continue
        prompt = (f"Topic: {item['topic']}\nToday: {time.strftime('%Y-%m-%d', time.localtime(now))}\n"
                  f"State at the last check: {item['last'] or '(first check)'}\n\n"
                  "Search for news since the last check. JSON: {\"new\": true|false, \"summary\": "
                  "\"two to three spoken sentences\", \"sources\": [\"Title – URL (date)\"]}. "
                  "Nothing really new = new false.")
        result = background.parse_json(background.ask("watch", WATCH_SYSTEM, prompt, tools="WebSearch")) or {}
        summary = " ".join(str(result.get("summary") or "").split())[:600]

        def mark(items, t=item["topic"], s=summary):
            for i in items:
                if i["topic"] == t:
                    i["last_run"] = now
                    if s:
                        i["last"] = s
        items = _watch_load()
        mark(items)
        _watch_save(items)
        checked.append(item["topic"])
        if result.get("new") and summary and not privacy.is_work(summary):
            research_save(f"Watch {item['topic']}", summary, result.get("sources") or [])
            events.publish("say", f"Sir, news about {item['topic']}: {summary}", kind="research")
    return checked


# ── multi-day plans ──────────────────────────────────────────

def plan_create(title, steps, internal=None):
    """steps: [{"text", "date": "YYYY-MM-DD"}]. Writes Projects/<title> and one reminder per step."""
    title = _slug(title)
    if not title or not steps:
        return "Title and steps are missing."
    if privacy.is_work(title, json.dumps(steps, ensure_ascii=False)):
        return "I do not plan work projects."
    clean = []
    for s in steps[:30]:
        text = " ".join(str((s or {}).get("text", "")).split())[:120]
        date = str((s or {}).get("date", ""))[:10]
        if text and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            clean.append((date, text))
    if not clean:
        return "The steps need text and date (YYYY-MM-DD)."
    clean.sort()
    folder = memory.MEMORY_DIR / "Projects"
    folder.mkdir(parents=True, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    path = memory.note_path(f"Projects/{title}")
    path.write_text(f"---\ntype: project\nimportance: medium\nupdated: {today}\nexpires: "
                    f"{(dt.date.fromisoformat(clean[-1][0]) + dt.timedelta(days=30)).isoformat()}\n---\n\n# {title}\n\n"
                    "Plan by Jarvis. Belongs to [[Profile]].\n\n" +
                    "\n".join(f"- [ ] {d}: {t}" for d, t in clean) + "\n", encoding="utf-8")
    reminders = 0
    if internal:
        for d, t in clean:
            if d >= today:
                res = internal("reminder_add", {"kind": "reminder", "at": f"{d} {PLAN_REMIND}", "text": f"{title}: {t}"})
                reminders += 0 if str(res).startswith(("Jarvis server not", "Error")) else 1
    return f"Plan “{title}” created with {len(clean)} steps" + (f", {reminders} reminders set." if internal else ".")


def plan_list():
    folder = memory.MEMORY_DIR / "Projects"
    rows = []
    for path in sorted(folder.glob("*.md")) if folder.exists() else []:
        open_steps = re.findall(r"(?m)^- \[ \] (\d{4}-\d{2}-\d{2}): (.+)$", path.read_text(encoding="utf-8"))
        if open_steps:
            rows.append(f"{path.stem}: " + "; ".join(f"{dt.date.fromisoformat(d).strftime('%b %d')} {t}"
                                                     for d, t in open_steps[:5]))
    return "\n".join(rows) or "No open plans."


def plan_check(title, step):
    path = memory.note_path(f"Projects/{_slug(title)}")
    if not path.exists():
        return "This plan does not exist."
    body = path.read_text(encoding="utf-8")
    want = (step or "").lower().strip()
    lines = body.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("- [ ] ") and want and want in ln.lower():
            lines[i] = ln.replace("- [ ] ", "- [x] ", 1)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            left = sum(1 for x in lines if x.startswith("- [ ] "))
            return f"Ticked off. {left} step(s) still open." if left else "Ticked off — the plan is completely done."
    return "I cannot find this open step."


def start():
    import background
    background.loop("watchlist", 1800, watch_step)
