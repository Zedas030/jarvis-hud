"""
Episodic memory: what happened on a day, from every dated source Jarvis keeps — the
conversation log, the Garmin days, the daily metrics, his own notices and the homelab
incidents — joined by date. "What did I ask you about the printer last week?" or
"How did I sleep the last time I was ill?" read these, nothing else.

  timeline(start, end, query)  [{day, talk, health, metrics, notices, incidents}] — one entry per
                               day that has anything; `query` keeps only exchanges that mention it
  describe(...)                the same as text for the tool memory_timeline (read-only)
  handle(n)                    local router: "What did I ask you yesterday/last week/on Monday
                               about X?", "What did we talk about the other day?" — 0 tokens

Work topics never show: the log already holds "(work topic, not logged)" for them,
and nothing here is stored — every call reads the files again.
"""
import datetime as dt
import re

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
               "October", "November", "December")
MAX_DAYS = 62
MAX_CHARS = 3500


# ── sources ──────────────────────────────────────────────────

def _talk(day, words):
    import memory
    import reflection
    path = memory.DIARY_DIR / f"Conversation {day.isoformat()}.md"
    if not path.exists():
        return []
    try:
        rows = reflection.exchanges(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []
    if words:
        rows = [r for r in rows if _mentions(words, r[1] + " " + r[2])]
    return [(t, u, a) for t, u, a in rows if "not logged" not in u]


def _health(day, data):
    rec = data.get(day.isoformat()) or {}
    parts = []
    if rec.get("sleep_s"):
        s = int(rec["sleep_s"])
        parts.append(f"sleep {s // 3600}:{s % 3600 // 60:02d} h"
                     + (f" (score {rec['sleep_score']})" if rec.get("sleep_score") else ""))
    if rec.get("steps") is not None:
        parts.append(f"{int(rec['steps']):,} steps")
    if rec.get("stress") is not None:
        parts.append(f"stress {rec['stress']}")
    if rec.get("bb_high") is not None:
        parts.append(f"Body Battery up to {rec['bb_high']}")
    if (rec.get("golf") or {}).get("rounds"):
        parts.append(f"golf {rec['golf']['rounds']} round(s)")
    for e in rec.get("log") or []:
        parts.append(f"{e.get('what')} at {e.get('at')}")
    return parts


def _metrics(day, data):
    import monitoring
    out = []
    for key, label in monitoring.LABEL.items():
        v = (data.get(key) or {}).get(day.isoformat())
        if v is not None:
            out.append(f"{label} {v:.0f} %")
    return out


def _notices(day, rows):
    return [r["text"] for r in rows
            if dt.date.fromtimestamp(r.get("at", 0)) == day and r.get("kind") not in ("timers", "wake")]


def _incidents(day, rows):
    return [f"{r['at'][11:]} {r['text']}" for r in rows
            if r.get("at", "").startswith(day.isoformat()) and r.get("state") == "bad"]


def _mentions(words, text):
    t = text.lower()
    return any(w in t for w in words)


_STOP = {"did", "have", "has", "had", "you", "me", "about", "asked", "ask", "said", "say", "last", "week",
         "yesterday", "today", "recently", "back", "then", "and", "with", "the", "this", "that", "for", "from",
         "what", "how", "when", "was", "were", "my", "regarding", "tell", "told"}


def _words(query):
    return [w for w in re.findall(r"\w{3,}", (query or "").lower()) if w not in _STOP]


def timeline(start, end, query=""):
    """One entry per day between start and end (dates, inclusive) that has anything."""
    import events
    import homelab_watch
    import monitoring
    import weekly_review
    end = min(end, dt.date.today())
    start = max(start, end - dt.timedelta(days=MAX_DAYS))
    words = _words(query)
    health, metrics = weekly_review.load(), monitoring.load()
    notes = events.notices_since(dt.datetime.combine(start, dt.time()).timestamp())
    incidents = homelab_watch.history(500)
    out = []
    day = start
    while day <= end:
        entry = dict(day=day, talk=_talk(day, words), health=_health(day, health), metrics=_metrics(day, metrics),
                     notices=_notices(day, notes), incidents=_incidents(day, incidents))
        if words:                                    # a search: only days the conversation mentions it
            if entry["talk"]:
                out.append(entry)
        elif any(entry[k] for k in ("talk", "health", "notices", "incidents")):
            out.append(entry)
        day += dt.timedelta(days=1)
    return out


def _label(day):
    return f"{WEEKDAY_NAMES[day.weekday()]}, {MONTH_NAMES[day.month - 1]} {day.day}"


def describe(start=None, end=None, query="", days=None):
    """Tool text. `start`/`end` ISO dates; without them the last `days` (default 7) days."""
    today = dt.date.today()
    try:
        end_d = dt.date.fromisoformat(end[:10]) if end else today
        start_d = dt.date.fromisoformat(start[:10]) if start else end_d - dt.timedelta(days=(days or 7) - 1)
    except ValueError:
        return "Please give the date as YYYY-MM-DD."
    rows = timeline(start_d, end_d, query)
    if not rows:
        what = f" about “{query}”" if query else ""
        return f"Between {_label(start_d)} and {_label(end_d)} I have recorded nothing{what}."
    lines = []
    for e in rows:
        parts = []
        if e["health"]:
            parts.append("Garmin: " + ", ".join(e["health"]))
        if e["talk"]:
            parts.append("Conversations: " + " | ".join(f"{t} “{u[:90]}” → “{a[:110]}”" for t, u, a in e["talk"][:6])
                         + (f" (+{len(e['talk']) - 6} more)" if len(e["talk"]) > 6 else ""))
        if e["notices"]:
            parts.append("Notices: " + " | ".join(n[:80] for n in e["notices"][:5]))
        if e["incidents"]:
            parts.append("Incidents: " + " | ".join(e["incidents"][:4]))
        if e["metrics"]:
            parts.append("Storage: " + ", ".join(e["metrics"]))
        lines.append(f"{e['day'].isoformat()} ({_label(e['day'])}): " + " · ".join(parts))
    text = "\n".join(lines)
    return text[:MAX_CHARS] + (" … [shortened]" if len(text) > MAX_CHARS else "")


# ── local router ─────────────────────────────────────────────

_ASKED = re.compile(r"\bwhat did i ask you (?P<when>.{0,40}?)\s*(?:about|regarding|on the subject of) "
                    r"(?:the |my )?(?P<topic>[\w -]+?)(?:\s+(?P<when2>yesterday|today|last week|the week before|"
                    r"recently|the other day|lately|on \w+day|\w+ days ago))?\s*$")
_TALKED = re.compile(r"\bwhat did we (?:talk|speak|chat) about(?P<when>.{0,40})")


def period(text, today=None):
    """(start, end) for 'yesterday', 'the day before yesterday', 'last week', 'on Monday', 'recently',
    '3 days ago' — or None."""
    today = today or dt.date.today()
    t = (text or "").lower()
    if re.search(r"\bday before yesterday\b", t):
        d = today - dt.timedelta(days=2)
        return d, d
    if re.search(r"\byesterday\b", t):
        d = today - dt.timedelta(days=1)
        return d, d
    if re.search(r"\btoday\b", t):
        return today, today
    if re.search(r"\b(the week before last|the week before)\b", t):
        monday = today - dt.timedelta(days=today.weekday() + 14)
        return monday, monday + dt.timedelta(days=6)
    if re.search(r"\blast week\b", t):
        monday = today - dt.timedelta(days=today.weekday() + 7)
        return monday, monday + dt.timedelta(days=6)
    if re.search(r"\bthis week\b", t):
        return today - dt.timedelta(days=today.weekday()), today
    m = re.search(r"\b(\d+|two|three|four|five|six|seven) days ago\b", t)
    if m:
        n = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}.get(m.group(1)) or int(m.group(1))
        d = today - dt.timedelta(days=n)
        return d, d
    m = re.search(r"\b(?:on |last )?(" + "|".join(WEEKDAYS) + r")\b", t)
    if m:
        back = (today.weekday() - WEEKDAYS.index(m.group(1))) % 7 or 7
        d = today - dt.timedelta(days=back)
        return d, d
    if re.search(r"\b(the other day|recently|lately|of late)\b", t):
        return today - dt.timedelta(days=14), today
    return None


def handle(n):
    m = _ASKED.search(n)
    if m:
        when = period(m.group("when") + " " + n)
        if not when:
            return None                              # no time named: the model may still search
        rows = timeline(when[0], when[1], m.group("topic"))
        hits = [(e["day"], t, u, a) for e in rows for t, u, a in e["talk"]]
        if not hits:
            return f"I have no question of yours about “{m.group('topic').strip()}” in the log for that period, Sir."
        parts = [f"{_label(d)} at {t}: “{u[:100]}” — I answered: “{a[:120]}”" for d, t, u, a in hits[-3:]]
        more = f" Plus {len(hits) - 3} more places." if len(hits) > 3 else ""
        return " ".join(parts) + more
    m = _TALKED.search(n)
    if m:
        when = period(m.group("when") + " " + n)
        if not when:
            return None
        rows = timeline(when[0], when[1])
        talk = [(e["day"], u) for e in rows for _, u, _ in e["talk"]]
        if not talk:
            return "There is nothing in the conversation log for that period, Sir."
        topics = []
        for d, u in talk:
            short = " ".join(u.split()[:7])
            if short not in topics:
                topics.append(short)
        return (f"{len(talk)} exchanges, for example: " + "; ".join(f"“{t}”" for t in topics[:6])
                + (f" and {len(topics) - 6} more topics." if len(topics) > 6 else "."))
    return None
