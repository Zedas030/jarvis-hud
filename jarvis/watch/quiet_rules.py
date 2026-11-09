"""
When the proactive mode speaks, shows or keeps a notice back. Pure functions, no I/O: the caller
(proactive.py) collects the situation once and passes it in, so every rule is testable on its own.

Levels, most urgent first: critical, important, nice — and "muted" below them, which only feedback reaches
("not important" three times). Critical is never lowered.

Holds (any of them = only critical is spoken):
  off          the proactive switch in Home Assistant is off
  estop        the emergency stop is active
  away         the user is not at home (nobody to hear it; the arrival briefing brings it, critical also goes to the phone)
  quiet        "quiet for an hour" / "no announcements today" / the quiet switch in Home Assistant
  guests       guest mode in Home Assistant
  call         the PC microphone is in use by a call app, or a do-not-disturb entity is on
  quiet_hours  inside the quiet hours (the morning briefing ends the night, so it is not held by them)

Actions: "speak"; "defer" = shown now and spoken later while it is still valid (only important, only after
a hold the user ends during the day: quiet, guests, call); "show" = only on the dashboard.
A request by the user ("good night", "tell me more") is an answer, not an interruption: it is always spoken.
"""
import datetime as dt
import re

LEVELS = ("critical", "important", "nice", "muted")
RETRY_AFTER = ("away", "quiet", "guests", "call")       # holds that end during the day: important is said afterwards
REASONS = {"off": "proactive mode off", "estop": "emergency stop", "away": "not at home", "quiet": "quiet",
           "guests": "guest mode", "call": "on a call", "quiet_hours": "quiet hours", "muted": "lowered by feedback",
           "nice": "only nice to know"}


class Decision(tuple):
    """(action, reason, retry) with names."""
    __slots__ = ()

    def __new__(cls, action, reason="", retry=False):
        return super().__new__(cls, (action, reason, retry))

    action = property(lambda s: s[0])
    reason = property(lambda s: s[1])
    retry = property(lambda s: s[2])


def in_hours(spec, now):
    """True inside '22-7' (wraps over midnight). '' / 'off' / garbage = never."""
    spec = str(spec or "").strip().lower()
    if not spec or spec in ("0", "off", "none", "no"):
        return False
    try:
        start, end = (int(x) for x in spec.split("-", 1))
    except ValueError:
        return False
    if start == end:
        return False
    h = now.hour
    return start <= h < end if start < end else h >= start or h < end


def demote(level, steps):
    """`level` lowered by `steps` (feedback); critical stays critical, nothing goes below muted."""
    level = level if level in LEVELS else "nice"
    if level == "critical" or steps <= 0:
        return level
    return LEVELS[min(LEVELS.index(level) + steps, len(LEVELS) - 1)]


def holds(ctx):
    """The active holds as codes, most binding first. ctx keys (all optional):
    now, quiet_hours, proactive_switch (False = off; None = unknown counts as on), emergency, quiet_until (epoch),
    away, quiet_switch, guests, call, night_ended (the morning briefing)."""
    now = ctx.get("now") or dt.datetime.now()
    out = []
    if ctx.get("proactive_switch") is False:
        out.append("off")
    if ctx.get("emergency"):
        out.append("estop")
    if ctx.get("away"):
        out.append("away")
    if ctx.get("quiet_switch") or (ctx.get("quiet_until") or 0) > now.timestamp():
        out.append("quiet")
    if ctx.get("guests"):
        out.append("guests")
    if ctx.get("call"):
        out.append("call")
    if not ctx.get("night_ended") and in_hours(ctx.get("quiet_hours", ""), now):
        out.append("quiet_hours")
    return out


def decide(level, ctx, mode="instant", critical_always=True):
    """What to do with one notice. mode: "instant" (it just happened), "briefing" (arrival, morning),
    "requested" (the user asked)."""
    if level == "muted":
        return Decision("show", "muted")
    if mode == "requested":
        return Decision("speak", "requested")
    active = holds(ctx)
    if level == "critical" and (critical_always or not active):
        return Decision("speak", "critical")
    if active:
        first = active[0]
        if level == "important":
            return Decision("defer", first, retry=all(h in RETRY_AFTER for h in active))
        return Decision("show", first)
    if mode == "instant" and level == "nice":
        return Decision("show", "nice")            # nice to know never interrupts; it waits for a briefing
    return Decision("speak", "")


def briefing_allowed(ctx):
    """A whole briefing (arrival, morning) is spoken only without holds; critical inside it still speaks."""
    return not holds(ctx)


# ── voice commands for quiet ─────────────────────────────────
_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
        "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
        "fifty": 50, "ninety": 90}
_FOR = re.compile(r"\b(quiet|silence|be still|no announcements|no notices|do not disturb|dont disturb)\b.*?\bfor\s+"
                  r"(\d+|[a-z]+)\s*(hours?|hrs?|minutes?|mins?)\b")
_TODAY = re.compile(r"\bno (more )?(announcements|notices)\b.*\b(today|for today|until tomorrow)\b|"
                    r"\b(today|for today)\b.*\bno (more )?(announcements|notices)\b|"
                    r"\bquiet until tomorrow\b")
_END = re.compile(r"\b(end (the )?quiet|quiet (is )?over|cancel (the )?quiet|announcements (back )?on( again)?|"
                  r"you (may|can) (speak|talk) again|no more quiet)\b")


_SPOKEN_TIMES = ((re.compile(r"\b(an hour and a half|one and a half hours?)\b"), "90 minutes"),
                 (re.compile(r"\b(a )?half (an )?hour\b"), "30 minutes"),
                 (re.compile(r"\b(a )?quarter (of an )?hour\b"), "15 minutes"))


def _amount(word):
    return (int(word) if word.isdigit() else _NUM.get(word)), None


def parse_quiet(n, now, today_until_hour=5):
    """A quiet command in the normalised text `n`: ("set", until_datetime) / ("end", None) / None."""
    n = " ".join((n or "").lower().replace("'", "").replace("’", "").split())
    for pattern, plain in _SPOKEN_TIMES:
        n = pattern.sub(plain, n)
    if _END.search(n):
        return ("end", None)
    m = _FOR.search(n)
    if m:
        value, unit = _amount(m.group(2))
        if value:
            unit = unit or ("min" if m.group(3).startswith("m") else "h")
            minutes = value if unit == "min" else value * 60
            if 0 < minutes <= 24 * 60:
                return ("set", now + dt.timedelta(minutes=minutes))
    if _TODAY.search(n):
        return ("set", until_morning(now, today_until_hour))
    return None


def until_morning(now, hour=5):
    """The next morning at `hour` (still today when it is past midnight and before that hour)."""
    day = now.date() if now.hour < hour else now.date() + dt.timedelta(days=1)
    return dt.datetime.combine(day, dt.time(hour))
