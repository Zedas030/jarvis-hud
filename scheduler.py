"""
Timers, alarm clock and reminders — parsed and run locally, 0 tokens.

    "Timer 10 Minuten", "Stell einen Timer auf eine halbe Stunde für die Nudeln"
    "Weck mich morgen um halb sieben", "Wecker auf 7:15", "Schlummern"
    "Erinnere mich in 20 Minuten an die Wäsche"
    "Erinnere mich morgen um 9 daran, Anna anzurufen"
    "Welche Timer laufen?", "Wie lange noch?", "Timer stoppen", "Wecker aus"

Items live in state.json ("timers") and survive restarts. A due item is
published on the event bus (events.py); the HUD rings and speaks it. Anything
the rules cannot read goes to the LLM, which has the same functions as tools
(reminder_add & co. in jarvis_mcp.py, via /api/internal).
"""
import datetime as dt
import re
import secrets
import threading
import time

import commands
import events

WEEKDAYS = ("montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag")
WEEKDAY_NAMES = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
MONTHS = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September",
          "Oktober", "November", "Dezember")
SNOOZE_MIN = 9
MISSED_KEEP_H = 6                   # after a longer downtime an old alarm is dropped, not rung

# ── numbers in words ─────────────────────────────────────────
_UNITS = {"null": 0, "ein": 1, "eins": 1, "eine": 1, "einen": 1, "einer": 1, "einem": 1, "zwei": 2, "drei": 3,
          "vier": 4, "fünf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11,
          "zwölf": 12, "dreizehn": 13, "vierzehn": 14, "fünfzehn": 15, "sechzehn": 16, "siebzehn": 17,
          "achtzehn": 18, "neunzehn": 19}
_TENS = {"zwanzig": 20, "dreißig": 30, "dreissig": 30, "vierzig": 40, "fünfzig": 50, "sechzig": 60,
         "siebzig": 70, "achtzig": 80, "neunzig": 90}
_COMPOUND = re.compile(r"^(ein|zwei|drei|vier|fünf|sechs|sieben|acht|neun)und(" + "|".join(_TENS) + r")$")
_PHRASES = [
    (r"\b(?:eine[rn]?\s+)?halbe[n]?\s+stunde\b", "30 minuten"),
    (r"\b(?:eine[rn]?\s+)?dreiviertel\s*stunde\b", "45 minuten"),
    (r"\b(?:eine[rn]?\s+)?viertel\s*stunde\b", "15 minuten"),
    (r"\b(?:anderthalb|eineinhalb)\s+stunden?\b", "90 minuten"),
    (r"\bzweieinhalb\s+stunden?\b", "150 minuten"),
]
UNIT_WORDS = r"(?:sekunden?|sek|minuten?|min|stunden?|std)"


def word_number(w):
    if w.isdigit():
        return int(w)
    if w in _UNITS:
        return _UNITS[w]
    if w in _TENS:
        return _TENS[w]
    m = _COMPOUND.match(w)
    if m:
        return _UNITS[m.group(1)] + _TENS[m.group(2)]
    return None


def numify(n):
    """Normalised text (router._norm) with number words as digits."""
    for pat, rep in _PHRASES:
        n = re.sub(pat, rep, n)
    out = []
    for w in n.split():
        v = word_number(w)
        out.append(str(v) if v is not None and not w.isdigit() else w)
    return " " + " ".join(out) + " "


# ── reading times ────────────────────────────────────────────
def duration(t):
    """Seconds in "10 minuten", "1 stunde 30 minuten", "90 sekunden" (numified text) — or None."""
    total = 0
    for num, unit in re.findall(r"\b(\d+)\s+(" + UNIT_WORDS + r")\b", t):
        k = 1 if unit.startswith("sek") else 3600 if unit.startswith(("stund", "std")) else 60
        total += int(num) * k
    return total or None


_NOT_UNIT = r"(?!\s+" + UNIT_WORDS + r"\b)"
_CLOCK = [
    (r"\bum\s+viertel\s+(?:nach|über)\s+(\d{1,2})\b", lambda h, m: (h, 15)),
    (r"\bum\s+viertel\s+vor\s+(\d{1,2})\b", lambda h, m: (h - 1, 45)),
    (r"\bum\s+drei\s?viertel\s+(\d{1,2})\b", lambda h, m: (h - 1, 45)),
    (r"\b(?:um|auf|für|gegen|ab)\s+halb\s+(\d{1,2})\b", lambda h, m: (h - 1, 30)),
    (r"\b(?:um|auf|für|gegen|ab)\s+(\d{1,2})(?:\s+uhr)?(?:\s+(\d{2}))?\b" + _NOT_UNIT, lambda h, m: (h, m)),
    (r"\b(\d{1,2})\s+uhr(?:\s+(\d{1,2}))?\b", lambda h, m: (h, m)),
]


def clock(t):
    """(hour, minute) of the first clock time in numified text, or None."""
    for pat, fn in _CLOCK:
        m = re.search(pat, t)
        if m:
            h = int(m.group(1))
            mins = int(m.group(2)) if m.lastindex and m.lastindex >= 2 and m.group(2) else 0
            h, mins = fn(h, mins)
            if 0 <= h <= 24 and 0 <= mins < 60:
                return h % 24, mins
    return None


def resolve(t, kind="alarm", now=None):
    """Datetime for a clock time + day words ("morgen", "am freitag", "abends"), or None."""
    now = now or dt.datetime.now()
    hm = clock(t)
    if not hm:
        return None
    h, m = hm
    if re.search(r"\b(abends?|nachmittags?)\b", t) and h < 12:
        h += 12
    elif re.search(r"\bnachts\b", t) and 6 <= h < 12:
        h += 12
    elif re.search(r"\bmittags\b", t) and h <= 5:
        h += 12
    day = now.date()
    explicit = True
    if re.search(r"\bübermorgen\b", t):
        day += dt.timedelta(days=2)
    elif re.search(r"\bmorgen\b", t) and not re.search(r"\bheute\s+morgen\b", t):
        day += dt.timedelta(days=1)
    else:
        wd = re.search(r"\b(" + "|".join(WEEKDAYS) + r")\b", t)
        if wd:
            ahead = (WEEKDAYS.index(wd.group(1)) - now.weekday()) % 7
            day += dt.timedelta(days=ahead)
            if ahead == 0 and dt.datetime.combine(day, dt.time(h, m)) <= now:
                day += dt.timedelta(days=7)
        else:
            explicit = False
    when = dt.datetime.combine(day, dt.time(h, m))
    if not explicit and when <= now:
        later = when + dt.timedelta(hours=12)
        am = re.search(r"\b(früh|morgens|vormittags?)\b", t)
        if kind == "reminder" and h < 12 and not am and later > now:
            when = later                  # "um 6" at two in the afternoon means six in the evening
        else:
            when += dt.timedelta(days=1)
    return when


# ── speaking times ───────────────────────────────────────────
def fmt_dur(sec):
    sec = int(round(sec))
    h, rest = divmod(sec, 3600)
    m, s = divmod(rest, 60)
    word = lambda n, one, many, fem=True: (("eine " if fem else "ein ") + one) if n == 1 else f"{n} {many}"  # noqa: E731
    parts = []
    if h:
        parts.append(word(h, "Stunde", "Stunden"))
    if m:
        parts.append(word(m, "Minute", "Minuten"))
    if s and sec < 600:
        parts.append(word(s, "Sekunde", "Sekunden"))
    return " und ".join(parts) if len(parts) == 2 else " ".join(parts) or "null Sekunden"


def fmt_when(when, now=None):
    now = now or dt.datetime.now()
    days = (when.date() - now.date()).days
    day = ("heute" if days == 0 else "morgen" if days == 1 else "übermorgen" if days == 2 else
           f"am {WEEKDAY_NAMES[when.weekday()]}" if 0 < days < 7 else f"am {when.day}. {MONTHS[when.month - 1]}")
    return f"{day} um {when.hour}:{when.minute:02d} Uhr"


# ── store ────────────────────────────────────────────────────
LAST_RING = {"at": 0.0, "kind": ""}


def items():
    return sorted(commands.load().get("timers") or [], key=lambda i: i["at"])


def public():
    """For the HUD countdown."""
    return [dict(id=i["id"], kind=i["kind"], at=i["at"], text=i.get("text", "")) for i in items()]


def _changed():
    events.publish("timers", items=public())


def add(kind, at, text="", dur=None):
    item = dict(id=secrets.token_hex(3), kind=kind, at=float(at), text=(text or "").strip()[:200],
                dur=dur, created=time.time())
    commands.update_state(lambda d: d.__setitem__("timers", (d.get("timers") or []) + [item]))
    _changed()
    return item


def cancel(kind=None, every=False, item_id=None):
    """Remove the next item of `kind` (or all of them). Returns the removed items."""
    def mut(d):
        lst = sorted(d.get("timers") or [], key=lambda i: i["at"])
        if item_id:
            gone = [i for i in lst if i["id"] == item_id]
        else:
            match = [i for i in lst if kind in (None, i["kind"])]
            gone = match if every else match[:1]
        d["timers"] = [i for i in lst if i not in gone]
        return gone
    gone = commands.update_state(mut)
    if gone:
        _changed()
    return gone


KIND_WORD = {"timer": "Timer", "alarm": "Wecker", "reminder": "Erinnerung", "action": "Geplanter Befehl"}


def describe(kind=None):
    now = time.time()
    lst = [i for i in items() if kind in (None, i["kind"])]
    if not lst:
        return {"timer": "Es läuft kein Timer, Sir.", "alarm": "Kein Wecker gestellt, Sir.",
                "reminder": "Keine Erinnerungen offen, Sir."}.get(kind, "Nichts gestellt, Sir. Weder Timer noch Wecker.")
    parts = []
    for i in lst[:5]:
        if i["kind"] == "timer":
            label = f" für {i['text']}" if i.get("text") else ""
            parts.append(f"Timer{label}: noch {fmt_dur(max(0, i['at'] - now))}")
        elif i["kind"] == "alarm":
            parts.append(f"Wecker {fmt_when(dt.datetime.fromtimestamp(i['at']))}")
        elif i["kind"] == "action":
            parts.append(f"{fmt_when(dt.datetime.fromtimestamp(i['at']))}: {i.get('text')}")
        else:
            parts.append(f"Erinnerung {fmt_when(dt.datetime.fromtimestamp(i['at']))}: {i.get('text') or 'ohne Text'}")
    more = f" Und {len(lst) - 5} weitere." if len(lst) > 5 else ""
    return ". ".join(parts) + "." + more


# ── the voice side ───────────────────────────────────────────
_SET_TIMER = re.compile(r"\b(timer|eieruhr|kurzzeitwecker)\b")
_ALARM = re.compile(r"\b(wecker|weck\w*\s+(?:mich|uns)|aufwecken|wach\s+machen|alarm)\b")
_REMIND = re.compile(r"\berinner\w*\s+(?:mich|uns)\b")
_CANCEL = re.compile(r"\b(stopp\w*|stop|lösch\w*|abbrech\w*|brich\s+\S+(?:\s+\S+)?\s+ab|beend\w*|entfern\w*|vergiss|"
                     r"cancel\w*|abstellen|stell\w*\s+\S+(?:\s+\S+)?\s+ab|aus|ausschalten|ausmachen|abschalten|weg)\b")
_LIST = re.compile(r"\b(wie\s+lange\s+(?:noch|läuft|dauert)|wie\s+viel\s+zeit|restzeit|welche|was\s+für|"
                   r"gibt\s+es|läuft\s+(?:noch\s+)?(?:ein|der|mein)|laufen|wann\s+klingelt|ist\s+(?:ein|der|mein)\s+wecker|"
                   r"hab\w*\s+ich\s+(?:einen|ein|noch))\b")
_SNOOZE = re.compile(r"\b(schlummer\w*|snooze)\b")
_LABEL_WORDS = {"die", "den", "das", "mich", "uns", "timer", "uhr", "minuten", "stunden", "sekunden", "später"}


def _timer_label(raw):
    m = re.search(r"\b([A-Za-zÄÖÜäöüß]{3,})-?timer\b", raw, re.I)
    if m and m.group(1).lower() not in ("ein", "einen", "den", "der", "kurzzeit", "mein", "meinen", "dein"):
        return m.group(1).capitalize()
    for m in re.finditer(r"\bfür\s+(?:die|den|das|meine?n?)?\s*([A-Za-zÄÖÜäöüß][\wäöüßÄÖÜ-]*)", raw, re.I):
        w = m.group(1)
        if word_number(w.lower()) is None and w.lower() not in _LABEL_WORDS and not re.match(UNIT_WORDS, w.lower()):
            return w
    return ""


_TIME_PHRASES = [
    r"\b(?:heute|morgen|übermorgen)(?:\s+(?:früh|abend|nachmittag|vormittag|mittag|morgen))?\b",
    r"\b(?:am\s+)?(?:nächsten\s+|kommenden\s+)?(?:" + "|".join(WEEKDAYS) + r")\b",
    r"\bin\s+(?:einer|einem|eine|\w+)\s+(?:halben\s+)?(?:sekunden?|minuten?|stunden?|viertelstunde|tag(?:en)?)\b",
    r"\b(?:und\s+)?\d+\s+(?:sekunden?|minuten?)\b",
    r"\b(?:um|gegen|ab)\s+(?:halb\s+|viertel\s+(?:nach|vor)\s+|drei\s?viertel\s+)?[\w]+(?:[:.]\d{2})?(?:\s+uhr)?(?:\s+\d{2}\b)?",
    r"\b\d{1,2}[:.]\d{2}(?:\s+uhr)?\b",
    r"\b\d{1,2}\s+uhr\b",
    r"\b(?:abends|morgens|nachmittags|vormittags|mittags|nachts|früh)\b",
]


def reminder_text(raw):
    """What to remind of, from the original wording: "an die Wäsche" -> "die Wäsche"."""
    m = re.search(r"erinner\w*\s+(?:mich|uns)\b[\s,]*(.*)$", raw or "", re.I | re.S)
    rest = m.group(1) if m else ""
    for pat in _TIME_PHRASES:
        rest = re.sub(pat, " ", rest, flags=re.I)
    rest = re.sub(r"\s+", " ", rest).strip(" ,.!?")
    rest = re.sub(r"^(?:bitte\s+)?(?:noch\s+)?(?:an\s+|daran,?\s+|zu\s+)", "", rest, flags=re.I)
    rest = re.sub(r"\s*,?\s*(?:bitte|jarvis|sir)$", "", rest, flags=re.I).strip(" ,.!?")
    return rest


def handle(n, raw=""):
    """Router handler: spoken reply, or None if this is not about timers or too unclear."""
    t = numify(n)
    now = dt.datetime.now()
    timerish = _SET_TIMER.search(t) or _ALARM.search(t) or _REMIND.search(t) or \
        re.search(r"\berinnerung\w*\b", t)

    if _SNOOZE.search(t) or (re.search(r"\bnoch\s+(\d+)\s+minuten?\b", t) and not timerish
                             and time.time() - LAST_RING["at"] < 900 and LAST_RING["kind"] == "alarm"):
        mins = duration(t)
        mins = mins // 60 if mins else SNOOZE_MIN
        add("alarm", time.time() + mins * 60, "Schlummern")
        return f"Na gut. Noch {fmt_dur(mins * 60)}, Sir."

    if not timerish:
        if re.search(r"\b(sag|gib)\s+(?:mir\s+)?bescheid\b", t) and duration(t):
            dur = duration(t)
            add("timer", time.time() + dur, "", dur)
            return f"Ich sage in {fmt_dur(dur)} Bescheid, Sir."
        if re.search(r"\bwie\s+lange\s+noch\b", t) and items():
            return describe("timer" if any(i["kind"] == "timer" for i in items()) else None)
        return _later(t, raw or n, now)
    return _timer_or_alarm(t, raw, n, now)


# "Mach das Licht in zehn Minuten aus", "Um 23 Uhr Steckdose Ventilator aus": the
# command itself runs later through the local router.
_ACTION_END = re.compile(r"\b(an|aus|ein|einschalten|ausschalten|anmachen|ausmachen|abschalten|anschalten|"
                         r"aktivier\w*|deaktivier\w*|pausier\w*)\s*$|\bauf\s+\d+\s*(?:%|prozent)\s*$")


def _later(t, raw, now):
    rel = re.search(r"\bin\s+\d+\s+" + UNIT_WORDS + r"\b", t)
    when = now + dt.timedelta(seconds=duration(t)) if rel and duration(t) else (
        resolve(t, "action", now) if re.search(r"\bum\s+(?:halb\s+)?\d", t) else None)
    if not when:
        return None
    command = raw
    for pat in _TIME_PHRASES:
        command = re.sub(pat, " ", command, flags=re.I)
    command = re.sub(r"\s+", " ", command).strip(" ,.!?")
    import router                            # router imports this module
    if not command or not _ACTION_END.search(router._norm(command).strip()):
        return None
    add("action", when.timestamp(), command)
    return f"Gut, {fmt_when(when, now) if when - now > dt.timedelta(hours=2) else 'in ' + fmt_dur((when - now).total_seconds() // 60 * 60 or 60)}: {command}."


def _timer_or_alarm(t, raw, n, now):

    kind = "alarm" if _ALARM.search(t) else "reminder" if (_REMIND.search(t) or "erinnerung" in t) else "timer"

    if _CANCEL.search(t) and not _REMIND.search(t):
        every = bool(re.search(r"\balle\b", t))
        gone = cancel(kind, every)
        if not gone:
            return {"timer": "Es läuft kein Timer, Sir.", "alarm": "Es war kein Wecker gestellt, Sir.",
                    "reminder": "Da war keine Erinnerung, Sir."}[kind]
        if len(gone) > 1:
            return f"Alle {len(gone)} gelöscht, Sir."
        return f"{KIND_WORD[kind]} gelöscht, Sir."

    if _LIST.search(t) and not (duration(t) or clock(t)):
        return describe(kind)

    if kind == "alarm":
        when = resolve(t, "alarm", now)
        if not when and duration(t):
            when = now + dt.timedelta(seconds=duration(t))
        if not when:
            return None
        add("alarm", when.timestamp(), "")
        rest = when - now
        hint = f", also in {fmt_dur(rest.total_seconds() // 60 * 60)}" if rest < dt.timedelta(hours=12) else ""
        return f"Wecker steht auf {fmt_when(when, now)}{hint}, Sir."

    if kind == "reminder":
        when = resolve(t, "reminder", now)
        if not when and duration(t):
            when = now + dt.timedelta(seconds=duration(t))
        what = reminder_text(raw or n)
        if not when or not what:
            return None                     # the LLM asks back or uses reminder_add
        add("reminder", when.timestamp(), what)
        if when - now < dt.timedelta(hours=2):
            return f"Ich erinnere Sie in {fmt_dur((when - now).total_seconds() // 60 * 60 or 60)}: {what}."
        return f"Ich erinnere Sie {fmt_when(when, now)}: {what}."

    dur = duration(t)
    if not dur:
        return None
    label = _timer_label(raw or n)
    add("timer", time.time() + dur, label, dur)
    return f"Timer {'für ' + label + ' ' if label else ''}über {fmt_dur(dur)} läuft, Sir."


# ── ringing ──────────────────────────────────────────────────
def _ring_text(i, late=False):
    now = dt.datetime.now()
    if i["kind"] == "timer":
        text = (f"Sir, der Timer für {i['text']} ist abgelaufen." if i.get("text")
                else f"Sir, Ihr Timer über {fmt_dur(i.get('dur') or 0)} ist abgelaufen." if i.get("dur")
                else "Sir, Ihr Timer ist abgelaufen.")
    elif i["kind"] == "alarm":
        text = (f"Guten Morgen, Sir. Es ist {now.hour}:{now.minute:02d} Uhr." if 4 <= now.hour < 11
                else f"Sir, Ihr Wecker. Es ist {now.hour}:{now.minute:02d} Uhr.")
    else:
        text = f"Sir, Ihre Erinnerung: {i.get('text') or 'ohne Text'}."
    if late:
        text = "Verspätet, ich war nicht erreichbar. " + text
    return text


def _due(start):
    now = time.time()

    def mut(d):
        lst = d.get("timers") or []
        due = [i for i in lst if i["at"] <= now]
        d["timers"] = [i for i in lst if i["at"] > now]
        return due
    for i in commands.update_state(mut):
        if now - i["at"] > MISSED_KEEP_H * 3600:
            continue
        late = i["at"] < start - 60 and now - i["at"] > 120
        if i["kind"] == "action":
            if late:
                continue                     # switching lights an hour late helps nobody
            import router
            reply = router.run_local(i.get("text", ""))
            events.publish("say", reply or f"Den geplanten Befehl konnte ich nicht ausführen: {i.get('text')}.",
                           kind="action")
            _changed()
            continue
        LAST_RING.update(at=now, kind=i["kind"])
        events.publish("alarm", _ring_text(i, late), priority="alarm", kind=i["kind"], item=i["id"])
        _changed()


def start():
    started = time.time()

    def loop():
        while True:
            try:
                if any(i["at"] <= time.time() for i in commands.load().get("timers") or []):
                    _due(started)
            except Exception as e:  # noqa: BLE001 — a broken item must not stop the clock
                print(f"  scheduler: {e}", flush=True)
            time.sleep(1)
    threading.Thread(target=loop, daemon=True).start()


# ── for the LLM tools (via /api/internal) ────────────────────
def tool(op, args):
    if op == "reminder_list":
        lst = items()
        if not lst:
            return "Nichts gestellt."
        return "\n".join(f"{i['id']} · {i['kind']} · {dt.datetime.fromtimestamp(i['at']):%Y-%m-%d %H:%M} · "
                         f"{i.get('text', '')}" for i in lst)
    if op == "reminder_cancel":
        which = (args.get("id") or "").strip()
        if which == "all":
            gone = cancel(None, every=True)
        else:
            gone = cancel(item_id=which)
        return f"{len(gone)} gelöscht." if gone else "Nichts gefunden — erst reminder_list."
    if op == "reminder_add":
        kind = (args.get("kind") or "reminder").strip()
        if kind not in KIND_WORD:
            return "kind muss timer, alarm oder reminder sein."
        try:
            when = dt.datetime.strptime((args.get("at") or "").strip()[:16].replace("T", " "), "%Y-%m-%d %H:%M")
        except ValueError:
            return "at bitte als 'YYYY-MM-DD HH:MM' (Ortszeit)."
        if when <= dt.datetime.now():
            return "Der Zeitpunkt liegt in der Vergangenheit."
        dur = (when - dt.datetime.now()).total_seconds() if kind == "timer" else None
        add(kind, when.timestamp(), args.get("text") or "", dur)
        return f"Gestellt: {KIND_WORD[kind]} {fmt_when(when)}" + (f": {args.get('text')}" if args.get("text") else ".")
    return "unbekannte Operation"
