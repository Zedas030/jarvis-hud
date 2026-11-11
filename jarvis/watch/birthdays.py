"""
Birthdays from the memory, the day before — with what Jarvis knows the person likes. Local, 0 tokens.

    "Tomorrow is Anna's birthday, Anna turns 34. I have noted: likes tulips, enjoys crime novels."

Found in the vault notes under Memories (People/<Name> first, then any bullet like
"Anna's birthday is on May 3" or "Birthday: 1992-05-03") — also the ones the learning loop saved by
itself (reflection.remember). The gift idea is no model call: the bullets of the person's note that say what
they like (likes, loves, wishes for, collects, hobby …).

  JARVIS_BIRTHDAYS        1 / 0 = off
  JARVIS_BIRTHDAY_TIME    when the day-before notice comes (18:00)

  upcoming(days, today)   [(date, name, age|None)] sorted
  step(now)               background: the day-before notice, once per person and year
  handle(n)               router: "Who has a birthday soon?", "When is Anna's birthday?"
"""
import datetime as dt
import os
import re

import events
import memory

ENABLED = os.environ.get("JARVIS_BIRTHDAYS", "1").strip().lower() not in ("0", "false", "no", "off")
AT = os.environ.get("JARVIS_BIRTHDAY_TIME", "18:00").strip()
MONTHS = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"), 1)}
_M = "(" + "|".join(MONTHS) + ")"
_DATE_MD = re.compile(_M + r"\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+(\d{4}))?", re.I)          # May 3, 1992
_DATE_DM = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + _M + r"(?:,?\s+(\d{4}))?", re.I)   # 3 May 1992
_DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_NAME_IN = re.compile(r"\b([A-Z][\w-]+)(?:(?:'s|’s|')\s+birthday\b|\s+(?:has|celebrates|was born)\b)")
_LIKES = re.compile(r"\b(likes|loves|wishes for|would like|collects|is into|hobby|hobbies|interested in|enjoys|"
                    r"fan of|favourite|favorite)\b", re.I)
_TAG = re.compile(r"\s*_\([^)]*\)_\s*$")


def _plain(text):
    return _TAG.sub("", re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]", r"\1", text)).strip()


def _date_of(text):
    """(day, month, year|None) or None."""
    m = _DATE_ISO.search(text)
    if m:
        return int(m.group(3)), int(m.group(2)), int(m.group(1))
    m = _DATE_MD.search(text)
    if m:
        return int(m.group(2)), MONTHS[m.group(1).lower()], int(m.group(3)) if m.group(3) else None
    m = _DATE_DM.search(text)
    if m:
        return int(m.group(1)), MONTHS[m.group(2).lower()], int(m.group(3)) if m.group(3) else None
    return None


def people():
    """{name: {"day", "month", "year", "likes": [...]}} from the vault."""
    found = {}
    likes = {}
    root = memory.MEMORY_DIR
    if not root.exists():
        return {}
    for path in root.rglob("*.md"):
        person = path.stem if "People" in path.parts else None
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for ln in lines:
            if not ln.startswith("- "):
                continue
            text = _plain(ln[2:])
            if person and _LIKES.search(text):
                likes.setdefault(person, []).append(text)
            if not re.search(r"birthday|born", text, re.I):
                continue
            d = _date_of(text)
            if not d:
                continue
            name = person
            if not name:
                m = _NAME_IN.search(text)
                name = m.group(1) if m else None
            if not name or name in found and person is None:
                continue
            try:
                dt.date(2000, d[1], d[0])          # leap-year safe check of day/month
            except ValueError:
                continue
            found[name] = {"day": d[0], "month": d[1], "year": d[2]}
    for name, info in found.items():
        info["likes"] = likes.get(name, [])[:3]
    return found


def _next(day, month, today):
    for year in (today.year, today.year + 1):
        try:
            d = dt.date(year, month, day)
        except ValueError:                          # Feb 29 outside a leap year
            d = dt.date(year, 3, 1)
        if d >= today:
            return d
    return None


def upcoming(days=30, today=None):
    today = today or dt.date.today()
    out = []
    for name, p in people().items():
        d = _next(p["day"], p["month"], today)
        if d and (d - today).days <= days:
            out.append((d, name, d.year - p["year"] if p.get("year") else None))
    return sorted(out)


def _lower_first(text):
    return text[:1].lower() + text[1:] if text[1:2].islower() else text


def _likes_text(name, info):
    likes = [_lower_first(re.sub(rf"^{re.escape(name)}\s+", "", x)) for x in info.get("likes") or []]
    return (" I have noted: " + ", ".join(likes) + ".") if likes else ""


def _date_text(month, day):
    return f"{dt.date(2000, month, day):%B} {day}"


def notice(name, info, age):
    who = f"Tomorrow is {name}'s birthday"
    return who + (f", {name} turns {age}." if age else ".") + _likes_text(name, info)


def step(now=None):
    import background
    now = now or dt.datetime.now()
    if not ENABLED or now.strftime("%H:%M") < AT:
        return []
    tomorrow = now.date() + dt.timedelta(days=1)
    said = []
    ppl = people()
    for d, name, age in upcoming(1, now.date()):
        if d != tomorrow or not background.claim(f"birthday:{name}", str(d.year)):
            continue
        text = notice(name, ppl[name], age)
        events.publish("say", text, kind="birthday")
        said.append(text)
    return said


def handle(n):
    if re.search(r"\bwho has (a |their )?birthday (soon|coming up|next|this month)\b|\bwhose birthday is (soon|coming up|next)\b|"
                 r"\b(upcoming|next) birthdays?\b", n):
        soon = upcoming(30)
        if not soon:
            return "Nobody I know has a birthday in the next 30 days, Sir."
        return "Birthdays soon: " + "; ".join(
            f"{name} on {_date_text(d.month, d.day)}" + (f" ({age})" if age else "") for d, name, age in soon[:5]) + "."
    m = (re.search(r"\bwhen is ([a-z-]+) birthday\b", n)
         or re.search(r"\bwhen does ([a-z-]+) have (?:a |her |his |their )?birthday\b", n)
         or re.search(r"\bwhen was ([a-z-]+) born\b", n))
    if m:
        want = m.group(1).lower()
        for name, p in people().items():
            if want in (name.lower(), name.lower() + "s"):     # "when is annas birthday" (apostrophe removed)
                return f"{name}'s birthday is on {_date_text(p['month'], p['day'])}." + _likes_text(name, p)
        return None                                 # unknown here: let the model look further
    return None


def start():
    import background
    if ENABLED:
        background.loop("birthdays", 900, step)
