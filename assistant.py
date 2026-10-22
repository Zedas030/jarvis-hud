"""
Organisation and research.

  mail_draft     a draft in the mailbox's Drafts folder (IMAP APPEND, flag \\Draft) —
                 never sent; always a confirmation card (policy CONFIRM)
  day_prep       "Was muss ich für morgen wissen?": appointments with travel time,
                 matching notes and mails, people notes; work appointments only counted
  research_save  a researched answer with sources and date as a note under Recherchen/
  watchlist      "Sag Bescheid, wenn es Neues zu X gibt": up to JARVIS_WATCH_MAX (5)
                 topics, each checked every few days by a background call with web
                 search (Claude only, at most JARVIS_WATCH_PER_DAY (2) checks a day);
                 Jarvis only speaks up when the model finds something new
  plans          "Plan mir den Umzug über zwei Wochen": a note Projekte/<title> with a
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

ROOT = pathlib.Path(__file__).resolve().parent
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


def mail_draft(to, subject, body):
    import jarvis_mcp as j
    if not (j.MAIL_HOST and j.MAIL_USER and j.MAIL_PASSWORD):
        return "Postfach nicht verbunden (MAIL_IMAP_HOST, MAIL_USER, MAIL_PASSWORD)."
    to, subject, body = (to or "").strip(), " ".join((subject or "").split()), (body or "").strip()
    if not (to and subject and body):
        return "Für einen Entwurf brauche ich Empfänger, Betreff und Text."
    if not re.fullmatch(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+(\s*,\s*[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+)*", to):
        return f"„{to}“ ist keine gültige Mail-Adresse."
    box = imaplib.IMAP4_SSL(j.MAIL_HOST, 993, timeout=20)
    try:
        box.login(j.MAIL_USER, j.MAIL_PASSWORD)
        folder = _drafts_folder(box)
        if not folder:
            return "Ich finde den Entwürfe-Ordner im Postfach nicht."
        msg = build_draft(to, subject, body, j.MAIL_USER)
        quoted = '"' + folder.replace('"', '\\"') + '"'
        typ, _ = box.append(quoted, "(\\Draft \\Seen)", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        if typ != "OK":
            return "Das Postfach hat den Entwurf abgelehnt."
    finally:
        try:
            box.logout()
        except Exception:  # noqa: BLE001
            pass
    return f"Entwurf an {to} liegt im Ordner {folder}. Gesendet habe ich nichts."


# ── day preparation ──────────────────────────────────────────

def day_prep(day=None, events=None):
    import icloud_calendar as cal
    day = day or (dt.date.today() + dt.timedelta(days=1))
    if isinstance(day, str):
        day = dt.date.fromisoformat(day[:10])
    try:
        evts = cal.events(day, 1) if events is None else events
    except Exception as e:  # noqa: BLE001
        return f"Kalender nicht lesbar: {e}"
    private = [e for e in evts if not privacy.is_work(e.get("title", ""), e.get("location", ""))]
    work = len(evts) - len(private)
    lines = [f"{day.strftime('%A, %d.%m.')}: {len(private)} private Termin(e)"
             + (f", dazu {work} Arbeitstermin(e) (Details lasse ich weg)" if work else "") + "."]
    for e in private:
        when = "ganztägig" if e.get("allday") else e["start"].strftime("%H:%M")
        row = f"- {when} {e.get('title', '?')}" + (f" in {e['location']}" if e.get("location") else "")
        if e.get("location") and not e.get("allday"):
            try:
                import commute
                dest = commute.geocode(e["location"])
                mins = commute.drive_minutes(dest) if dest else None
                if mins:
                    row += f" — {mins} Min. Fahrt, los um {commute.leave_at(e['start'], mins).strftime('%H:%M')}"
            except Exception:  # noqa: BLE001
                pass
        words = [w for w in re.split(r"\W+", e.get("title", "")) if len(w) > 3][:3]
        notes = []
        for w in words:
            for hit in memory.search(w, limit=2):
                if hit["title"] not in notes and not hit["title"].startswith("Gespräch"):
                    notes.append(hit["title"])
        if notes:
            row += f" · Notizen: {', '.join(notes[:3])}"
        lines.append(row)
    if not private and not work:
        lines.append("Keine Termine.")
    return "\n".join(lines)


# ── research notes ───────────────────────────────────────────

def research_save(title, summary, sources=None):
    title, summary = _slug(title), (summary or "").strip()
    if not title or not summary:
        return "Titel und Zusammenfassung fehlen."
    if privacy.is_work(title, summary):
        return "Nicht gespeichert: Arbeitsthemen merke ich mir nicht."
    folder = memory.MEMORY_DIR / "Recherchen"
    folder.mkdir(parents=True, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    src = "\n".join(f"- {s}" for s in (sources or []) if str(s).strip()) or "- (keine Quellen angegeben)"
    path = folder / f"{title} {today}.md"
    path.write_text(f"---\ntype: recherche\nwichtigkeit: niedrig\nupdated: {today}\n"
                    f"ablauf: {(dt.date.today() + dt.timedelta(days=365)).isoformat()}\n---\n\n# {title}\n\n"
                    f"Recherche vom {dt.date.today().strftime('%d.%m.%Y')}. Gehört zu [[Wissen]].\n\n{summary}\n\n"
                    f"## Quellen\n\n{src}\n", encoding="utf-8")
    return f"Gespeichert als Recherchen/{path.stem}."


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
        return "Welches Thema?"
    if privacy.is_work(topic):
        return "Arbeitsthemen beobachte ich nicht."
    items = _watch_load()
    if any(i["topic"].lower() == topic.lower() for i in items):
        return f"„{topic}“ beobachte ich schon."
    if len(items) >= WATCH_MAX:
        return f"Ich beobachte schon {WATCH_MAX} Themen — bitte erst eines streichen."
    items.append({"topic": topic, "every_days": max(1, min(int(every_days or 7), 30)), "last_run": 0, "last": ""})
    _watch_save(items)
    return f"Ich behalte „{topic}“ im Auge und melde mich, wenn es Neues gibt (Prüfung alle {items[-1]['every_days']} Tage)."


def watch_list():
    items = _watch_load()
    if not items:
        return "Ich beobachte gerade keine Themen."
    return "\n".join(f"- {i['topic']} (alle {i['every_days']} Tage" +
                     (f", zuletzt {time.strftime('%d.%m.', time.localtime(i['last_run']))}" if i["last_run"] else "")
                     + ")" for i in items)


def watch_remove(topic):
    items = _watch_load()
    keep = [i for i in items if topic.lower() not in i["topic"].lower()]
    if len(keep) == len(items):
        return "Dieses Thema beobachte ich nicht."
    _watch_save(keep)
    return f"Nicht mehr beobachtet: {', '.join(i['topic'] for i in items if i not in keep)}."


WATCH_SYSTEM = ("Du bist JARVIS und prüfst mit der Websuche, ob es zu einem Thema Neues gibt. Nur belastbare Quellen, "
                "mit Datum. Antworte ausschließlich mit JSON.")


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
        prompt = (f"Thema: {item['topic']}\nHeute: {time.strftime('%d.%m.%Y', time.localtime(now))}\n"
                  f"Stand der letzten Prüfung: {item['last'] or '(erste Prüfung)'}\n\n"
                  "Suche nach Neuem seit der letzten Prüfung. JSON: {\"neu\": true|false, \"zusammenfassung\": "
                  "\"zwei bis drei gesprochene Sätze\", \"quellen\": [\"Titel – URL (Datum)\"]}. "
                  "Nichts wirklich Neues = neu false.")
        result = background.parse_json(background.ask("watch", WATCH_SYSTEM, prompt, tools="WebSearch")) or {}
        summary = " ".join(str(result.get("zusammenfassung") or "").split())[:600]

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
        if result.get("neu") and summary and not privacy.is_work(summary):
            research_save(f"Beobachtung {item['topic']}", summary, result.get("quellen") or [])
            events.publish("say", f"Sir, Neues zu {item['topic']}: {summary}", kind="research")
    return checked


# ── multi-day plans ──────────────────────────────────────────

def plan_create(title, steps, internal=None):
    """steps: [{"text", "date": "YYYY-MM-DD"}]. Writes Projekte/<title> and one reminder per step."""
    title = _slug(title)
    if not title or not steps:
        return "Titel und Schritte fehlen."
    if privacy.is_work(title, json.dumps(steps, ensure_ascii=False)):
        return "Arbeitsvorhaben plane ich nicht."
    clean = []
    for s in steps[:30]:
        text = " ".join(str((s or {}).get("text", "")).split())[:120]
        date = str((s or {}).get("date", ""))[:10]
        if text and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            clean.append((date, text))
    if not clean:
        return "Die Schritte brauchen Text und Datum (YYYY-MM-DD)."
    clean.sort()
    folder = memory.MEMORY_DIR / "Projekte"
    folder.mkdir(parents=True, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    path = folder / f"{title}.md"
    path.write_text(f"---\ntype: projekt\nwichtigkeit: mittel\nupdated: {today}\nablauf: "
                    f"{(dt.date.fromisoformat(clean[-1][0]) + dt.timedelta(days=30)).isoformat()}\n---\n\n# {title}\n\n"
                    "Plan von Jarvis. Gehört zu [[Profil]].\n\n" +
                    "\n".join(f"- [ ] {d}: {t}" for d, t in clean) + "\n", encoding="utf-8")
    reminders = 0
    if internal:
        for d, t in clean:
            if d >= today:
                res = internal("reminder_add", {"kind": "reminder", "at": f"{d} {PLAN_REMIND}", "text": f"{title}: {t}"})
                reminders += 0 if str(res).startswith(("Jarvis-Server nicht", "Fehler")) else 1
    return f"Plan „{title}“ mit {len(clean)} Schritten angelegt" + (f", {reminders} Erinnerungen gesetzt." if internal else ".")


def plan_list():
    folder = memory.MEMORY_DIR / "Projekte"
    rows = []
    for path in sorted(folder.glob("*.md")) if folder.exists() else []:
        open_steps = re.findall(r"(?m)^- \[ \] (\d{4}-\d{2}-\d{2}): (.+)$", path.read_text(encoding="utf-8"))
        if open_steps:
            rows.append(f"{path.stem}: " + "; ".join(f"{dt.date.fromisoformat(d).strftime('%d.%m.')} {t}"
                                                     for d, t in open_steps[:5]))
    return "\n".join(rows) or "Keine offenen Pläne."


def plan_check(title, step):
    path = memory.MEMORY_DIR / "Projekte" / f"{_slug(title)}.md"
    if not path.exists():
        return "Diesen Plan gibt es nicht."
    body = path.read_text(encoding="utf-8")
    want = (step or "").lower().strip()
    lines = body.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("- [ ] ") and want and want in ln.lower():
            lines[i] = ln.replace("- [ ] ", "- [x] ", 1)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            left = sum(1 for x in lines if x.startswith("- [ ] "))
            return f"Abgehakt. Noch {left} Schritt(e) offen." if left else "Abgehakt — der Plan ist komplett erledigt."
    return "Diesen offenen Schritt finde ich nicht."


def start():
    import background
    background.loop("watchlist", 1800, watch_step)
