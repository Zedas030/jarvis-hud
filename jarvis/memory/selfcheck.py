"""
Jarvis checks his own answers without asking: when the user repeats or rephrases a question
within RETRY_S seconds, the first answer most likely missed. Those moments are logged
(logs/selfcheck.jsonl) with the route that answered first — a local handler that fired
wrongly, or a model turn — and feed two things:

  reflection.py   the next evaluation sees them as "possible misses" (with the wording),
                  so the learned rules and the learning log cover them
  the report      "How well did you do this week?" / the usage report names how often it happened
                  and which local handler misfires most

Follow-ups that are meant as follow-ups ("brighter", "and in the bedroom too", a plain yes)
do not count; nor does anything that looks like work (privacy.py).
"""
import collections
import json
import os
import pathlib
import re
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
PATH = pathlib.Path(os.environ.get("JARVIS_SELFCHECK", ROOT / "logs" / "selfcheck.jsonl"))
RETRY_S = int(os.environ.get("JARVIS_SELFCHECK_SECONDS", "25"))
KEEP = 500
_LAST = {"at": 0.0, "message": "", "route": ""}
_FOLLOW = re.compile(r"^\W*(yes|no|ok(ay)?|thanks|thank you|brighter|darker|louder|quieter|(on|off) again|too|and .{0,25} too|"
                     r"again|once more|next|continue|stop|confirmed)\W*$", re.I)


_STOP = {"jarvis", "please", "can", "could", "you", "how", "will", "the", "this", "that", "is", "are", "was",
         "me", "my", "we", "us", "what", "whats", "who", "when", "and", "or", "still", "with", "for", "on", "from",
         "again", "now", "today", "then", "actually", "just", "tell", "say", "have", "has", "had", "does", "did"}


def _words(text):
    return {w for w in re.findall(r"\w{3,}", (text or "").lower()) if w not in _STOP}


def similar(a, b):
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    if wa <= wb or wb <= wa:
        return True
    return len(wa & wb) / len(wa | wb) >= 0.5


def _route_name(route):
    if route.get("kind") == "local":
        return "local:" + str(route.get("handler", "?"))
    return "model:" + ",".join(g for g in route.get("groups") or [] if g not in ("memory", "web")) or "model"


def note(message, route, now=None):
    """Called once per turn with the user's text and the router's decision. Returns the row logged, or None."""
    now = now or time.time()
    prev = dict(_LAST)
    _LAST.update(at=now, message=message or "", route=_route_name(route or {}))
    if not prev["message"] or now - prev["at"] > RETRY_S or _FOLLOW.match(message or "") or not similar(prev["message"], message):
        return None
    import privacy
    if privacy.is_work(prev["message"], message):
        return None
    row = dict(at=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)), first=prev["message"][:120],
               route=prev["route"], second=(message or "")[:120], gap=round(now - prev["at"]))
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        lines = PATH.read_text(encoding="utf-8").splitlines() if PATH.exists() else []
        lines = (lines + [json.dumps(row, ensure_ascii=False)])[-KEEP:]
        PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass
    return row


def misses(days=7, now=None):
    since = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime((now or time.time()) - days * 86400))
    try:
        rows = [json.loads(ln) for ln in PATH.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except (OSError, ValueError):
        return []
    return [r for r in rows if r.get("at", "") >= since]


def hints(days=2, limit=8):
    """Lines for the reflection prompt."""
    return [f"[{r['at'][:16]}] “{r['first']}” ({r['route']}) → right after: “{r['second']}”" for r in misses(days)[-limit:]]


def spoken_report(days=7):
    rows = misses(days)
    if not rows:
        return f"In the last {days} days you did not have to ask a question twice — good for me."
    by = collections.Counter(r["route"] for r in rows)
    route, n = by.most_common(1)[0]
    kind = "my local handler " + route.split(":", 1)[1] if route.startswith(("local:", "lokal:")) else "the language model"
    return (f"{len(rows)} times you rephrased a question within seconds — my answer probably "
            f"missed. Most often it was {kind} ({n} times), last at “{rows[-1]['first']}”.")


_ASK = re.compile(r"\b(how well did you do|how (often|many times) (were you|did you get it) wrong|self ?check|misses|"
                  r"where were you wrong|what went wrong)\b")


def handle(n):
    return spoken_report() if _ASK.search(n) else None
