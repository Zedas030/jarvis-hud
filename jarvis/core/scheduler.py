"""
Timers, alarm clock and reminders — parsed and run locally, 0 tokens.

    "Timer 10 minutes", "Set a timer for half an hour for the pasta"
    "Wake me up tomorrow at half past six", "Alarm for 7:15", "Snooze"
    "Remind me in 20 minutes about the laundry"
    "Remind me tomorrow at 9 to call Anna"
    "Which timers are running?", "How long left?", "Stop the timer", "Alarm off"

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

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December")
SNOOZE_MIN = 9
MISSED_KEEP_H = 6                   # after a longer downtime an old alarm is dropped, not rung

# ── numbers in words ─────────────────────────────────────────
_UNITS = {"zero": 0, "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
          "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
          "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
         "ninety": 90}
_DIGITS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
_PHRASES = [
    (r"\b(?:an?\s+|one\s+|1\s+)?hour\s+and\s+(?:a\s+)?half\b", "90 minutes"),
    (r"\b(?:one|1)\s+and\s+(?:a\s+)?half\s+hours?\b", "90 minutes"),
    (r"\b(?:two|2)\s+and\s+(?:a\s+)?half\s+hours?\b", "150 minutes"),
    (r"\b(?:a\s+)?half\s+(?:an\s+)?hour\b", "30 minutes"),
    (r"\b(?:three|3)\s+quarters\s+of\s+an\s+hour\b", "45 minutes"),
    (r"\b(?:a\s+)?quarter\s+(?:of\s+an\s+)?hour\b", "15 minutes"),
]
UNIT_WORDS = r"(?:seconds?|secs?|minutes?|mins?|hours?|hrs?)"


def word_number(w):
    if w.isdigit():
        return int(w)
    if w in _UNITS:
        return _UNITS[w]
    if w in _TENS:
        return _TENS[w]
    return None


def numify(n):
    """Normalised text (router._norm) with number words as digits ("twenty five" -> 25)."""
    for pat, rep in _PHRASES:
        n = re.sub(pat, rep, n)
    out, tens = [], False
    for w in n.split():
        if tens and w in _DIGITS:
            out[-1] = str(int(out[-1]) + _UNITS[w])
            tens = False
            continue
        v = word_number(w)
        tens = w in _TENS
        out.append(str(v) if v is not None and not w.isdigit() else w)
    return " " + " ".join(out) + " "


# ── reading times ────────────────────────────────────────────
def duration(t):
    """Seconds in "10 minutes", "1 hour 30 minutes", "90 seconds" (numified text) — or None."""
    total = 0
    for num, unit in re.findall(r"\b(\d+)\s+(" + UNIT_WORDS + r")\b", t):
        k = 1 if unit.startswith("sec") else 3600 if unit.startswith(("hour", "hr")) else 60
        total += int(num) * k
    return total or None


_NOT_UNIT = r"(?!\s+(?:" + UNIT_WORDS + r"|%|percent)\b)"
_AMPM = r"(?:\s+(?:oclock|am|pm|a m|p m))?"
_CLOCK = [
    (r"\b(?:at|for|around|from)\s+(?:1\s+)?quarter\s+past\s+(\d{1,2})\b", lambda h, m: (h, 15)),
    (r"\b(?:at|for|around|from)\s+(?:1\s+)?quarter\s+to\s+(\d{1,2})\b", lambda h, m: (h - 1, 45)),
    (r"\b(?:at|for|around|from)\s+half\s+past\s+(\d{1,2})\b", lambda h, m: (h, 30)),
    (r"\b(?:at|for|to|around|from)\s+(\d{1,2})(?:\s+(\d{2}))?" + _AMPM + r"\b" + _NOT_UNIT, lambda h, m: (h, m)),
    (r"\b(\d{1,2})(?:\s+(\d{2}))?\s+(?:oclock|am|pm|a m|p m)\b", lambda h, m: (h, m)),
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
    """Datetime for a clock time + day words ("tomorrow", "on friday", "in the evening"), or None."""
    now = now or dt.datetime.now()
    hm = clock(t)
    if not hm:
        return None
    h, m = hm
    if re.search(r"\b(evening|tonight|afternoon|pm|p m)\b", t) and h < 12:
        h += 12
    elif re.search(r"\bat night\b", t) and 6 <= h < 12:
        h += 12
    elif re.search(r"\b(noon|midday|lunchtime)\b", t) and h <= 5:
        h += 12
    elif re.search(r"\b(am|a m)\b", t) and h == 12:
        h = 0
    day = now.date()
    explicit = True
    if re.search(r"\bday after tomorrow\b", t):
        day += dt.timedelta(days=2)
    elif re.search(r"\btomorrow\b", t):
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
        am = re.search(r"\b(early|morning|am|a m)\b", t)
        if kind == "reminder" and h < 12 and not am and later > now:
            when = later                  # "at 6" at two in the afternoon means six in the evening
        else:
            when += dt.timedelta(days=1)
    return when


# ── speaking times ───────────────────────────────────────────
def fmt_dur(sec):
    sec = int(round(sec))
    h, rest = divmod(sec, 3600)
    m, s = divmod(rest, 60)
    word = lambda n, one, many: f"one {one}" if n == 1 else f"{n} {many}"  # noqa: E731
    parts = []
    if h:
        parts.append(word(h, "hour", "hours"))
    if m:
        parts.append(word(m, "minute", "minutes"))
    if s and sec < 600:
        parts.append(word(s, "second", "seconds"))
    return " and ".join(parts) if len(parts) == 2 else " ".join(parts) or "zero seconds"


def fmt_when(when, now=None):
    now = now or dt.datetime.now()
    days = (when.date() - now.date()).days
    day = ("today" if days == 0 else "tomorrow" if days == 1 else "the day after tomorrow" if days == 2 else
           f"on {WEEKDAY_NAMES[when.weekday()]}" if 0 < days < 7 else f"on {MONTHS[when.month - 1]} {when.day}")
    return f"{day} at {when.hour}:{when.minute:02d}"


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


KIND_WORD = {"timer": "Timer", "alarm": "Alarm", "reminder": "Reminder", "action": "Scheduled command"}


def describe(kind=None):
    now = time.time()
    lst = [i for i in items() if kind in (None, i["kind"])]
    if not lst:
        return {"timer": "No timer is running, Sir.", "alarm": "No alarm set, Sir.",
                "reminder": "No open reminders, Sir."}.get(kind, "Nothing set, Sir. Neither timers nor alarms.")
    parts = []
    for i in lst[:5]:
        if i["kind"] == "timer":
            label = f" for {i['text']}" if i.get("text") else ""
            parts.append(f"Timer{label}: {fmt_dur(max(0, i['at'] - now))} left")
        elif i["kind"] == "alarm":
            parts.append(f"Alarm {fmt_when(dt.datetime.fromtimestamp(i['at']))}")
        elif i["kind"] == "action":
            parts.append(f"{fmt_when(dt.datetime.fromtimestamp(i['at']))}: {i.get('text')}")
        else:
            parts.append(f"Reminder {fmt_when(dt.datetime.fromtimestamp(i['at']))}: {i.get('text') or 'no text'}")
    more = f" And {len(lst) - 5} more." if len(lst) > 5 else ""
    return ". ".join(parts) + "." + more


# ── the voice side ───────────────────────────────────────────
_SET_TIMER = re.compile(r"\b(timers?|egg timers?|kitchen timers?|countdowns?)\b")
_ALARM = re.compile(r"\b(alarms?|wake\s+(?:me|us)(?:\s+up)?|wake-?up call)\b")
_REMIND = re.compile(r"\bremind\w*\s+(?:me|us)\b")
_CANCEL = re.compile(r"\b(stop\w*|cancel\w*|delete|remove|clear|end|forget|abort|kill|turn\s+\S+(?:\s+\S+)?\s+off|"
                     r"turn off|switch off|off)\b")
_LIST = re.compile(r"\b(how\s+long\s+(?:left|to go|until|is left)|how\s+much\s+(?:time|longer)|time\s+left|remaining|"
                   r"which|what|are\s+there|is\s+there|any|running|when\s+does\s+.{0,20}ring|is\s+(?:an|the|my)\s+alarm|"
                   r"do\s+i\s+have)\b")
_SNOOZE = re.compile(r"\b(snooze\w*)\b")
_LABEL_WORDS = {"the", "a", "an", "me", "us", "my", "timer", "oclock", "minutes", "hours", "seconds", "later",
                "half", "quarter"}


def _timer_label(raw):
    m = re.search(r"\b([A-Za-z]{3,})\s*-?timer\b", raw, re.I)
    if m and m.group(1).lower() not in ("set", "start", "the", "kitchen", "egg", "my", "your", "new", "one", "that",
                                        "this", "any", "stop", "cancel", "delete"):
        return m.group(1).capitalize()
    for m in re.finditer(r"\bfor\s+(?:the|my)?\s*([A-Za-z][\w-]*)", raw, re.I):
        w = m.group(1)
        if word_number(w.lower()) is None and w.lower() not in _LABEL_WORDS and not re.match(UNIT_WORDS, w.lower()):
            return w
    return ""


_TIME_PHRASES = [
    r"\b(?:the\s+day\s+after\s+tomorrow|today|tomorrow|tonight)(?:\s+(?:morning|evening|afternoon|night|at\s+noon))?\b",
    r"\b(?:on\s+)?(?:next\s+|this\s+|coming\s+)?(?:" + "|".join(WEEKDAYS) + r")\b",
    r"\bin\s+(?:half\s+an\s+hour|a\s+quarter\s+of\s+an\s+hour)\b",
    r"\bin\s+(?:a|an|one|\w+)\s+(?:and\s+a\s+half\s+)?(?:seconds?|minutes?|hours?|days?)\b",
    r"\b(?:and\s+)?\d+\s+(?:seconds?|minutes?)\b",
    r"\b(?:at|around|from)\s+(?:half\s+past\s+|(?:a\s+)?quarter\s+(?:past|to)\s+)?\w+(?:[:.]\d{2})?"
    r"(?:\s+o['’]?clock)?(?:\s*(?:am|pm|a\.m\.|p\.m\.))?(?:\s+\d{2}\b)?",
    r"\b\d{1,2}[:.]\d{2}(?:\s*(?:am|pm))?\b",
    r"\b\d{1,2}\s*(?:o['’]?clock|am|pm)\b",
    r"\b(?:in\s+the\s+(?:morning|evening|afternoon)|at\s+night|at\s+noon|early)\b",
]


def reminder_text(raw):
    """What to remind of, from the original wording: "about the laundry" -> "the laundry"."""
    m = re.search(r"remind\w*\s+(?:me|us)\b[\s,]*(.*)$", raw or "", re.I | re.S)
    rest = m.group(1) if m else ""
    for pat in _TIME_PHRASES:
        rest = re.sub(pat, " ", rest, flags=re.I)
    rest = re.sub(r"\s+", " ", rest).strip(" ,.!?")
    rest = re.sub(r"^(?:please\s+)?(?:again\s+)?(?:about\s+|of\s+|that\s+|to\s+)", "", rest, flags=re.I)
    rest = re.sub(r"\s*,?\s*(?:please|jarvis|sir)$", "", rest, flags=re.I).strip(" ,.!?")
    return rest


def handle(n, raw=""):
    """Router handler: spoken reply, or None if this is not about timers or too unclear."""
    t = numify(n)
    now = dt.datetime.now()
    timerish = _SET_TIMER.search(t) or _ALARM.search(t) or _REMIND.search(t) or \
        re.search(r"\breminders?\b", t)

    if _SNOOZE.search(t) or (re.search(r"\b(\d+)\s+more\s+minutes?\b", t) and not timerish
                             and time.time() - LAST_RING["at"] < 900 and LAST_RING["kind"] == "alarm"):
        mins = duration(t)
        mins = mins // 60 if mins else SNOOZE_MIN
        add("alarm", time.time() + mins * 60, "Snooze")
        return f"Very well. Another {fmt_dur(mins * 60)}, Sir."

    if not timerish:
        if re.search(r"\b(let\s+me\s+know|tell\s+me|give\s+me\s+a\s+shout|ping\s+me)\b", t) and duration(t):
            dur = duration(t)
            add("timer", time.time() + dur, "", dur)
            return f"I will let you know in {fmt_dur(dur)}, Sir."
        if re.search(r"\bhow\s+(?:long\s+(?:left|to go)|much\s+longer)\b", t) and items():
            return describe("timer" if any(i["kind"] == "timer" for i in items()) else None)
        return _later(t, raw or n, now)
    return _timer_or_alarm(t, raw, n, now)


# "Turn the light off in ten minutes", "At 11 pm switch the fan socket off": the
# command itself runs later through the local router.
_ACTION_END = re.compile(r"\b(on|off|activate\w*|deactivate\w*|pause\w*)\s*$|\bto\s+\d+\s*(?:%|percent)\s*$|"
                         r"^\s*(?:please\s+)?(?:turn|switch|power|shut)\s+(?:on|off)\b|"
                         r"^\s*(?:please\s+)?(?:activate|deactivate|pause)\b")


def _later(t, raw, now):
    rel = re.search(r"\bin\s+\d+\s+" + UNIT_WORDS + r"\b", t)
    when = now + dt.timedelta(seconds=duration(t)) if rel and duration(t) else (
        resolve(t, "action", now) if re.search(r"\bat\s+(?:1\s+)?(?:half|quarter|\d)", t) else None)
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
    return f"Fine, {fmt_when(when, now) if when - now > dt.timedelta(hours=2) else 'in ' + fmt_dur((when - now).total_seconds() // 60 * 60 or 60)}: {command}."


def _timer_or_alarm(t, raw, n, now):

    kind = "alarm" if _ALARM.search(t) else "reminder" if (_REMIND.search(t) or "reminder" in t) else "timer"

    if _CANCEL.search(t) and not _REMIND.search(t):
        every = bool(re.search(r"\ball\b", t))
        gone = cancel(kind, every)
        if not gone:
            return {"timer": "No timer is running, Sir.", "alarm": "There was no alarm set, Sir.",
                    "reminder": "There was no reminder, Sir."}[kind]
        if len(gone) > 1:
            return f"All {len(gone)} deleted, Sir."
        return f"{KIND_WORD[kind]} deleted, Sir."

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
        hint = f", that is in {fmt_dur(rest.total_seconds() // 60 * 60)}" if rest < dt.timedelta(hours=12) else ""
        return f"Alarm set for {fmt_when(when, now)}{hint}, Sir."

    if kind == "reminder":
        when = resolve(t, "reminder", now)
        if not when and duration(t):
            when = now + dt.timedelta(seconds=duration(t))
        what = reminder_text(raw or n)
        if not when or not what:
            return None                     # the LLM asks back or uses reminder_add
        add("reminder", when.timestamp(), what)
        if when - now < dt.timedelta(hours=2):
            return f"I will remind you in {fmt_dur((when - now).total_seconds() // 60 * 60 or 60)}: {what}."
        return f"I will remind you {fmt_when(when, now)}: {what}."

    dur = duration(t)
    if not dur:
        return None
    label = _timer_label(raw or n)
    add("timer", time.time() + dur, label, dur)
    return f"Timer {'for ' + label + ' ' if label else ''}set: {fmt_dur(dur)}, Sir."


# ── ringing ──────────────────────────────────────────────────
def _ring_text(i, late=False):
    now = dt.datetime.now()
    if i["kind"] == "timer":
        text = (f"Sir, the timer for {i['text']} is up." if i.get("text")
                else f"Sir, your {fmt_dur(i.get('dur') or 0)} timer is up." if i.get("dur")
                else "Sir, your timer is up.")
    elif i["kind"] == "alarm":
        text = (f"Good morning, Sir. It is {now.hour}:{now.minute:02d}." if 4 <= now.hour < 11
                else f"Sir, your alarm. It is {now.hour}:{now.minute:02d}.")
    else:
        text = f"Sir, your reminder: {i.get('text') or 'no text'}."
    if late:
        text = "Late, I was not reachable. " + text
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
            import emergency
            if late or emergency.paused():
                continue                     # switching lights an hour late helps nobody; nor during an emergency stop
            import router
            reply = router.run_local(i.get("text", ""))
            events.publish("say", reply or f"I could not run the scheduled command: {i.get('text')}.",
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
            return "Nothing set."
        return "\n".join(f"{i['id']} · {i['kind']} · {dt.datetime.fromtimestamp(i['at']):%Y-%m-%d %H:%M} · "
                         f"{i.get('text', '')}" for i in lst)
    if op == "reminder_cancel":
        which = (args.get("id") or "").strip()
        if which == "all":
            gone = cancel(None, every=True)
        else:
            gone = cancel(item_id=which)
        return f"{len(gone)} deleted." if gone else "Nothing found — call reminder_list first."
    if op == "reminder_add":
        kind = (args.get("kind") or "reminder").strip()
        if kind not in KIND_WORD:
            return "kind must be timer, alarm or reminder."
        try:
            when = dt.datetime.strptime((args.get("at") or "").strip()[:16].replace("T", " "), "%Y-%m-%d %H:%M")
        except ValueError:
            return "Please give at as 'YYYY-MM-DD HH:MM' (local time)."
        if when <= dt.datetime.now():
            return "That time is in the past."
        dur = (when - dt.datetime.now()).total_seconds() if kind == "timer" else None
        add(kind, when.timestamp(), args.get("text") or "", dur)
        return f"Set: {KIND_WORD[kind]} {fmt_when(when)}" + (f": {args.get('text')}" if args.get("text") else ".")
    return "unknown operation"
