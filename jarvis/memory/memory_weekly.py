"""
Weekly look back at what Jarvis saved by himself — "This week I remembered three things …".

Since 10.11.2026 the learning loop saves lasting facts without a card (reflection.remember). So the user
keeps the overview without opening the learning log, Jarvis lists those saves once a week (Sunday from
JARVIS_MEMORY_WEEKLY_TIME, default 18:30) and listens for two days:

  "fine" / "all correct"               keeps everything, the review is closed
  "strike number 2" / "strike two"     moves that bullet to the Trash (memory_tools.forget)
  "strike the thing about anna"        the same, found by a word of it
  "What did you remember this week?"   the list again, any day

Only bullets Jarvis saved himself this week can be struck this way — undoing his own background save,
like mail_events_undo. Anything else still goes through memory_forget and its card.

  JARVIS_MEMORY_WEEKLY       1 / 0 = off
  JARVIS_MEMORY_WEEKLY_TIME  time on Sunday (18:30)
"""
import datetime as dt
import os
import re
import time

import commands
import events
import memory

ENABLED = os.environ.get("JARVIS_MEMORY_WEEKLY", "1").strip().lower() not in ("0", "false", "no", "off")
AT = os.environ.get("JARVIS_MEMORY_WEEKLY_TIME", "18:30").strip()
OPEN_S = 2 * 86400
_SECTION = re.compile(r"(?m)^## (\d{4}-\d{2}-\d{2}) \d{2}:\d{2}\s*$")
_SAVED = re.compile(r"^- Saved \(\[\[([^\]]+)\]\]\): (.+)$")
_NUMBERS = {"one": 1, "first": 1, "two": 2, "second": 2, "three": 3, "third": 3,
            "four": 4, "fourth": 4, "five": 5, "fifth": 5}
_STOP = {"strike", "cross", "scratch", "delete", "remove", "forget", "number", "item", "point", "entry",
         "that", "this", "thing", "things", "about", "with", "please", "jarvis", "from", "the", "one"}


def saved(days=7, today=None):
    """[{"day", "note", "text"}] of the "Saved" lines in the learning log of the last `days` days."""
    import reflection
    path = memory.MEMORY_DIR / f"{reflection.LOG_NOTE}.md"
    try:
        body = path.read_text(encoding="utf-8")
    except OSError:
        return []
    since = ((today or dt.date.today()) - dt.timedelta(days=days - 1)).isoformat()
    out, day = [], ""
    for line in body.splitlines():
        m = _SECTION.match(line)
        if m:
            day = m.group(1)
            continue
        s = _SAVED.match(line.strip())
        if s and day >= since:
            out.append({"day": day, "note": s.group(1), "text": s.group(2).strip()})
    return out


def _spoken(items):
    if not items:
        return "This week I did not remember anything by myself, Sir."
    n = len(items)
    head = "This week I remembered one thing" if n == 1 else f"This week I remembered {n} things"
    listed = " ".join(f"{i}. {it['text']}." for i, it in enumerate(items, 1))
    return (f"{head}: {listed} Say “fine” or “strike number …” "
            "if something is wrong.").replace("..", ".")


def _open(items, now=None):
    commands.update_state(lambda d: d.__setitem__("memory_weekly", {
        "items": items, "until": (now or time.time()) + OPEN_S}) or True)


def _pending():
    st = commands.load().get("memory_weekly") or {}
    return st.get("items") or [] if st.get("until", 0) > time.time() else []


def _close():
    commands.update_state(lambda d: d.pop("memory_weekly", None) is not None)


def step(now=None):
    """Background (every 10 min): on Sunday after AT, once per ISO week."""
    import background
    now = now or dt.datetime.now()
    if not ENABLED or now.weekday() != 6 or now.strftime("%H:%M") < AT:
        return None
    if not background.claim("memory-weekly", background.week_key(now.date())):
        return None
    items = saved(7, now.date())
    if not items:
        return None                       # nothing saved: no announcement at all
    items = items[:8]
    _open(items, now.timestamp())
    text = _spoken(items)
    events.publish("say", text, kind="memory")
    return text


def _which(n, items):
    # "one" only after "number" ("strike the one about anna" is not number 1)
    m = (re.search(r"\b(?:number|no|item|point|entry)\s+(\d|one|two|three|four|five)\b", n)
         or re.search(r"\b(\d|two|three|four|five|first|second|third|fourth|fifth)\b", n))
    if m:
        w = m.group(1)
        i = int(w) if w.isdigit() else _NUMBERS.get(w, 0)
        if 1 <= i <= len(items):
            return items[i - 1]
    words = [w for w in re.findall(r"[a-z]{4,}", n) if w not in _STOP]
    hits = [it for it in items if any(w in it["text"].lower() for w in words)]
    return hits[0] if len(hits) == 1 else None


def _forget(item):
    """The learning log names the note by its last part ([[Anna]]); the bullet lives in People/Anna."""
    import memory_tools
    titles = {memory_tools._title(p) for p, _ in memory_tools.find(item["text"])
              if memory_tools._title(p).split("/")[-1].lower() == item["note"].lower()}
    return memory_tools.forget(item["text"], titles.pop() if len(titles) == 1 else item["note"])


def handle(n):
    """Local router (0 tokens)."""
    if re.search(r"\bwhat (have|did) you (remember\w*|save\w*|note\w*)( by yourself| on your own)?"
                 r"( this week| lately| recently)?\b(?!\s+about\b)", n):
        items = saved(7)
        if items:
            _open(items[:8])
        return _spoken(items[:8])
    items = _pending()
    if not items:
        return None
    if re.search(r"^\s*(fine|thats fine|thats right|all correct|all good|its all correct|correct|keep it|keep them)\s*$", n):
        _close()
        return "Good, then everything stays as it is, Sir."
    # "strike the milk from the shopping list" stays with the lists: only a number or a word of a saved item
    if (re.search(r"\b(strike|cross out|scratch|delete|remove|forget)\b", n)
            and not re.search(r"\blist\w*\b|\bto ?do\b", n)):
        it = _which(n, items)
        if not it:
            return None
        result = _forget(it)
        rest = [x for x in items if x is not it]
        if rest:
            _open(rest)
        else:
            _close()
        return result
    return None


def start():
    import background
    if ENABLED:
        background.loop("memory_weekly", 600, step)
