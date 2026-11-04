"""
Episodic memory: what happened on a day, from every dated source Jarvis keeps — the
conversation log, the Garmin days, the daily metrics, his own notices and the homelab
incidents — joined by date. "Was habe ich dich letzte Woche zum Drucker gefragt?" or
"Wie war mein Schlaf, als ich das letzte Mal krank war?" read these, nothing else.

  timeline(start, end, query)  [{day, talk, health, metrics, notices, incidents}] — one entry per
                               day that has anything; `query` keeps only exchanges that mention it
  describe(...)                the same as text for the tool memory_timeline (read-only)
  handle(n)                    local router: "Was habe ich dich gestern/letzte Woche/am Montag
                               zu X gefragt?", "Worüber haben wir neulich gesprochen?" — 0 tokens

Work topics never show: the log already holds "(Arbeitsthema, nicht protokolliert)" for them,
and nothing here is stored — every call reads the files again.
"""
import datetime as dt
import re

WEEKDAYS = ("montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag")
WEEKDAY_NAMES = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
MAX_DAYS = 62
MAX_CHARS = 3500


# ── sources ──────────────────────────────────────────────────

def _talk(day, words):
    import memory
    import reflection
    path = memory.DIARY_DIR / f"Gespräch {day.isoformat()}.md"
    if not path.exists():
        return []
    try:
        rows = reflection.exchanges(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []
    if words:
        rows = [r for r in rows if _mentions(words, r[1] + " " + r[2])]
    return [(t, u, a) for t, u, a in rows if "nicht protokolliert" not in u]


def _health(day, data):
    rec = data.get(day.isoformat()) or {}
    parts = []
    if rec.get("sleep_s"):
        s = int(rec["sleep_s"])
        parts.append(f"Schlaf {s // 3600}:{s % 3600 // 60:02d} h"
                     + (f" (Score {rec['sleep_score']})" if rec.get("sleep_score") else ""))
    if rec.get("steps") is not None:
        parts.append(f"{int(rec['steps']):,} Schritte".replace(",", "."))
    if rec.get("stress") is not None:
        parts.append(f"Stress {rec['stress']}")
    if rec.get("bb_high") is not None:
        parts.append(f"Body Battery bis {rec['bb_high']}")
    if (rec.get("golf") or {}).get("rounds"):
        parts.append(f"Golf {rec['golf']['rounds']} Runde(n)")
    for e in rec.get("log") or []:
        parts.append(f"{e.get('what')} um {e.get('at')}")
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


_STOP = {"habe", "hast", "haben", "hatte", "dich", "mich", "über", "wegen", "gefragt", "gesagt", "letzte", "woche",
         "gestern", "heute", "neulich", "damals", "dann", "oder", "und", "mit", "das", "der", "die", "den", "dem",
         "ein", "eine", "einen", "zum", "zur", "beim", "nach", "was", "wie", "wann", "als", "war", "meine", "mein"}


def _words(query):
    return [w for w in re.findall(r"[\wäöüß]{3,}", (query or "").lower()) if w not in _STOP]


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
    return f"{WEEKDAY_NAMES[day.weekday()]}, {day.day}.{day.month}."


def describe(start=None, end=None, query="", days=None):
    """Tool text. `start`/`end` ISO dates; without them the last `days` (default 7) days."""
    today = dt.date.today()
    try:
        end_d = dt.date.fromisoformat(end[:10]) if end else today
        start_d = dt.date.fromisoformat(start[:10]) if start else end_d - dt.timedelta(days=(days or 7) - 1)
    except ValueError:
        return "Datum bitte als JJJJ-MM-TT."
    rows = timeline(start_d, end_d, query)
    if not rows:
        what = f" zu „{query}“" if query else ""
        return f"Zwischen {_label(start_d)} und {_label(end_d)} habe ich nichts{what} aufgezeichnet."
    lines = []
    for e in rows:
        parts = []
        if e["health"]:
            parts.append("Garmin: " + ", ".join(e["health"]))
        if e["talk"]:
            parts.append("Gespräche: " + " | ".join(f"{t} „{u[:90]}“ → „{a[:110]}“" for t, u, a in e["talk"][:6])
                         + (f" (+{len(e['talk']) - 6} weitere)" if len(e["talk"]) > 6 else ""))
        if e["notices"]:
            parts.append("Meldungen: " + " | ".join(n[:80] for n in e["notices"][:5]))
        if e["incidents"]:
            parts.append("Vorfälle: " + " | ".join(e["incidents"][:4]))
        if e["metrics"]:
            parts.append("Speicher: " + ", ".join(e["metrics"]))
        lines.append(f"{e['day'].isoformat()} ({_label(e['day'])}): " + " · ".join(parts))
    text = "\n".join(lines)
    return text[:MAX_CHARS] + (" … [gekürzt]" if len(text) > MAX_CHARS else "")


# ── local router ─────────────────────────────────────────────

_ASKED = re.compile(r"\b(?:was|wonach) (?:habe|hab) ich dich (?P<when>.{0,40}?)\s*(?:zu[mr]?|über|wegen|nach|bezüglich) "
                    r"(?:dem |der |den |meine[mnr]? )?(?P<topic>[\wäöüß -]+?)\s*(?:gefragt|wissen wollen)\b")
_TALKED = re.compile(r"\b(?:worüber|über was|wovon) haben wir (?P<when>.{0,40}?)\s*(?:gesprochen|geredet|gequatscht)\b")


def period(text, today=None):
    """(start, end) for 'gestern', 'vorgestern', 'letzte Woche', 'am Montag', 'neulich', 'vor 3 Tagen' — or None."""
    today = today or dt.date.today()
    t = (text or "").lower()
    if re.search(r"\bvorgestern\b", t):
        d = today - dt.timedelta(days=2)
        return d, d
    if re.search(r"\bgestern\b", t):
        d = today - dt.timedelta(days=1)
        return d, d
    if re.search(r"\bheute\b", t):
        return today, today
    if re.search(r"\bvorletzte woche\b", t):
        monday = today - dt.timedelta(days=today.weekday() + 14)
        return monday, monday + dt.timedelta(days=6)
    if re.search(r"\bletzte woche\b", t):
        monday = today - dt.timedelta(days=today.weekday() + 7)
        return monday, monday + dt.timedelta(days=6)
    if re.search(r"\bdiese woche\b", t):
        return today - dt.timedelta(days=today.weekday()), today
    m = re.search(r"\bvor (\d+|zwei|drei|vier|fünf|sechs|sieben) tagen\b", t)
    if m:
        n = {"zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6, "sieben": 7}.get(m.group(1)) or int(m.group(1))
        d = today - dt.timedelta(days=n)
        return d, d
    m = re.search(r"\b(?:am |letzten )?(" + "|".join(WEEKDAYS) + r")\b", t)
    if m:
        back = (today.weekday() - WEEKDAYS.index(m.group(1))) % 7 or 7
        d = today - dt.timedelta(days=back)
        return d, d
    if re.search(r"\b(neulich|kürzlich|letztens|in letzter zeit)\b", t):
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
            return f"Zu „{m.group('topic').strip()}“ habe ich in dem Zeitraum keine Frage von Ihnen im Protokoll, Sir."
        parts = [f"{_label(d)} um {t}: „{u[:100]}“ — ich antwortete: „{a[:120]}“" for d, t, u, a in hits[-3:]]
        more = f" Dazu {len(hits) - 3} weitere Stellen." if len(hits) > 3 else ""
        return " ".join(parts) + more
    m = _TALKED.search(n)
    if m:
        when = period(m.group("when") + " " + n)
        if not when:
            return None
        rows = timeline(when[0], when[1])
        talk = [(e["day"], u) for e in rows for _, u, _ in e["talk"]]
        if not talk:
            return "In dem Zeitraum steht nichts im Gesprächsprotokoll, Sir."
        topics = []
        for d, u in talk:
            short = " ".join(u.split()[:7])
            if short not in topics:
                topics.append(short)
        return (f"{len(talk)} Wortwechsel, zum Beispiel: " + "; ".join(f"„{t}“" for t in topics[:6])
                + (f" und {len(topics) - 6} weitere Themen." if len(topics) > 6 else "."))
    return None
