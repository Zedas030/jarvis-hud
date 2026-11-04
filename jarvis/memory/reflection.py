"""
Learning loop: Jarvis reviews his own conversations and learns lasting rules.

After a conversation (JARVIS_REFLECT_IDLE_MIN, default 30 minutes of silence) the
new exchanges from the conversation log (vault/Conversations) are evaluated with one
small background call: what went well, what the user corrected, what to do
differently. The result is appended to the note Memories/Learning log.

Once a week (Sunday from JARVIS_REFLECT_WEEK_HOUR, default 19 h) the week's
entries are condensed into a few fixed rules. They are NOT written directly:
they become a proposal (proposals.py) that the user confirms on a card. Accepted
rules live in the note Preferences, in the section "Learned rules" between two
markers, and go into the system prompt of every turn (rules_block). "Drop the
rule …" removes one again (tool rule_remove, also with a card).

Work topics never get in: exchanges that look like work are skipped before the
model sees them, and anything work-like in its answer is dropped (privacy.py).
"""
import datetime as dt
import os
import re
import time

import background
import commands
import events
import memory
import privacy
import proposals

IDLE_MIN = int(os.environ.get("JARVIS_REFLECT_IDLE_MIN", "30"))
MIN_EXCHANGES = int(os.environ.get("JARVIS_REFLECT_MIN_EXCHANGES", "2"))
WEEK_HOUR = int(os.environ.get("JARVIS_REFLECT_WEEK_HOUR", "19"))
MAX_RULES = int(os.environ.get("JARVIS_MAX_RULES", "20"))
ENABLED = os.environ.get("JARVIS_REFLECT", "1").strip() not in ("0", "false", "no", "off")

LOG_NOTE = "Learning log"
RULES_NOTE = "Preferences"
START = "<!-- jarvis:learned-rules -->"
END = "<!-- /jarvis:learned-rules -->"

_EXCHANGE = re.compile(r"\*\*(\d{1,2}:\d{2}) · [^*]+:\*\* (.*?)\n\n\*\*Jarvis:\*\* (.*?)(?=\n\*\*\d{1,2}:\d{2} · |\Z)", re.S)
_CORRECTION = re.compile(
    r"\b(no|wrong|not right|thats not right|i meant|not that|not like that|leave it|stop it|stop|too long|"
    r"too loud|too quiet|again|once more|i already said|actually i wanted|you should|please dont|"
    r"that was not|that wasnt)\b", re.I)

SYSTEM = ("You are JARVIS and evaluate your own conversations with the user in order to get better. "
          "Honest, concrete, brief. You leave out work topics (job, colleagues, customers, work projects) completely. "
          "Answer with a JSON object only.")


# ── conversation log → exchanges ─────────────────────────────

def exchanges(text):
    """[(HH:MM, user, jarvis)] from a conversation-log note."""
    return [(t, " ".join(u.split()), " ".join(a.split())) for t, u, a in _EXCHANGE.findall(text)]


def _new_exchanges():
    """Exchanges not evaluated yet, from today's and yesterday's log, plus the new read positions."""
    seen = commands.load().get("reflection_pos") or {}
    out, pos = [], {}
    for back in (1, 0):
        day = (dt.date.today() - dt.timedelta(days=back)).isoformat()
        path = memory.DIARY_DIR / f"Conversation {day}.md"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        start = seen.get(path.name, 0)
        if start > len(text):
            start = 0
        out += [(day,) + e for e in exchanges(text[start:])]
        pos[path.name] = len(text)
    return out, pos


def _prompt(items):
    lines, hints = [], []
    for day, t, user, reply in items:
        lines.append(f"[{day} {t}] User: {user[:300]}\nJarvis: {reply[:400]}")
        if _CORRECTION.search(re.sub(r"['’]", "", user)):
            hints.append(f"[{day} {t}] {user[:120]}")
    try:
        import selfcheck
        misses = selfcheck.hints()
    except Exception:  # noqa: BLE001
        misses = []
    return ("Conversations since the last evaluation:\n\n" + "\n\n".join(lines) +
            ("\n\nPossible corrections by the user:\n" + "\n".join(hints) if hints else "") +
            ("\n\nPossible misses (the user asked again right away, the first answer probably did not fit):\n"
             + "\n".join(misses) if misses else "") +
            "\n\nAnswer with JSON: {\"good\": [...], \"corrected\": [...], \"lessons\": [...]}\n"
            "- good: what went well (3 at most)\n"
            "- corrected: what the user corrected and what would have been right (3 at most)\n"
            "- lessons: a general lesson for future conversations, as an instruction to yourself (3 at most)\n"
            "Each entry one short English sentence (under 140 characters). Invent nothing; nothing notable = empty lists.")


def _clean(items, limit=3):
    out = []
    for i in items or []:
        s = " ".join(str(i).split())[:160]
        if s and not privacy.is_work(s):
            out.append(s)
    return out[:limit]


def _note(name):
    return memory.MEMORY_DIR / f"{name}.md"


def append_log(result, when=None):
    """Writes one evaluation to the learning log note. Returns the number of bullets."""
    rows = ([f"- Good: {s}" for s in _clean(result.get("good"))] +
            [f"- Corrected: {s}" for s in _clean(result.get("corrected"))] +
            [f"- Lesson: {s}" for s in _clean(result.get("lessons"))])
    if not rows:
        return 0
    path = _note(LOG_NOTE)
    path.parent.mkdir(parents=True, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    if not path.exists():
        path.write_text(f"---\ntype: learning\nimportance: low\nupdated: {today}\n---\n\n# {LOG_NOTE}\n\n"
                        "What Jarvis learned about himself after conversations. Every Sunday this turns into "
                        "proposals for [[Preferences]].\n", encoding="utf-8")
    body = path.read_text(encoding="utf-8")
    body = re.sub(r"(?m)^updated: .*$", f"updated: {today}", body, count=1)
    stamp = when or time.strftime("%Y-%m-%d %H:%M")
    path.write_text(body.rstrip() + f"\n\n## {stamp}\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return len(rows)


def reflect(force=False):
    """Evaluates the new exchanges if the conversation is over. Returns the number of bullets written."""
    last_turn = commands.load().get("last_turn_at") or 0
    if not force and time.time() - last_turn < IDLE_MIN * 60:
        return 0
    items, pos = _new_exchanges()
    kept = [e for e in items if not privacy.is_work(e[2], e[3])]
    if len(kept) < (1 if force else MIN_EXCHANGES):
        if pos and not kept:          # only work talk (or nothing): mark as read anyway
            commands.update_state(lambda d: d.__setitem__("reflection_pos", pos))
        return 0
    result = background.parse_json(background.ask("reflection", SYSTEM, _prompt(kept[-30:])))
    if result is None:
        return 0                      # no model right now: try again later, positions unchanged
    commands.update_state(lambda d: d.__setitem__("reflection_pos", pos))
    return append_log(result)


# ── weekly: entries → proposed rules ─────────────────────────

def week_entries(days=7, today=None):
    path = _note(LOG_NOTE)
    if not path.exists():
        return []
    since = (today or dt.date.today()) - dt.timedelta(days=days)
    out = []
    for block in re.split(r"(?m)^## ", path.read_text(encoding="utf-8"))[1:]:
        head, _, rest = block.partition("\n")
        try:
            day = dt.date.fromisoformat(head.strip()[:10])
        except ValueError:
            continue
        if day > since:
            out += [ln[2:].strip() for ln in rest.splitlines() if ln.startswith("- ")]
    return out


def rules_list():
    path = _note(RULES_NOTE)
    if not path.exists():
        return []
    body = path.read_text(encoding="utf-8")
    if START not in body or END not in body:
        return []
    section = body.split(START, 1)[1].split(END, 1)[0]
    return [re.sub(r"\s*\(since [\d-]+\)$", "", ln[2:].strip()) for ln in section.splitlines() if ln.startswith("- ")]


def rules_block():
    rules = rules_list()
    return ("Rules confirmed by the user that you learned from earlier conversations:\n"
            + "\n".join(f"- {r}" for r in rules)) if rules else ""


def _norm(s):
    return re.sub(r"\W+", " ", s.lower()).strip()


def _write_rules(rules_with_dates):
    path = _note(RULES_NOTE)
    path.parent.mkdir(parents=True, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    if path.exists():
        body = path.read_text(encoding="utf-8")
    else:
        body = (f"---\ntype: preference\nimportance: medium\nupdated: {today}\n---\n\n# {RULES_NOTE}\n\n"
                "How the user likes things. Belongs to [[Profile]].\n")
    section = (f"{START}\n## Learned rules\n\nProposed by Jarvis, confirmed by the user on a card.\n\n"
               + "\n".join(f"- {r} (since {d})" for r, d in rules_with_dates) + f"\n{END}")
    if START in body and END in body:
        before, rest = body.split(START, 1)
        body = before + section + rest.split(END, 1)[1]
    else:
        body = body.rstrip() + "\n\n" + section + "\n"
    body = re.sub(r"(?m)^updated: .*$", f"updated: {today}", body, count=1)
    path.write_text(body, encoding="utf-8")


def _dated_rules():
    path = _note(RULES_NOTE)
    if not path.exists() or START not in path.read_text(encoding="utf-8"):
        return []
    section = path.read_text(encoding="utf-8").split(START, 1)[1].split(END, 1)[0]
    out = []
    for ln in section.splitlines():
        if ln.startswith("- "):
            m = re.match(r"- (.*?)\s*\(since ([\d-]+)\)$", ln.strip())
            out.append((m.group(1), m.group(2)) if m else (ln[2:].strip(), time.strftime("%Y-%m-%d")))
    return out


def apply_rules(rules):
    """Adds confirmed rules to the note Preferences (oldest drop out beyond MAX_RULES)."""
    current = _dated_rules()
    known = {_norm(r) for r, _ in current}
    today = time.strftime("%Y-%m-%d")
    added = 0
    for r in rules:
        text = " ".join(str(r.get("rule") if isinstance(r, dict) else r).split())[:200]
        if text and _norm(text) not in known and not privacy.is_work(text):
            current.append((text, today))
            known.add(_norm(text))
            added += 1
    _write_rules(current[-MAX_RULES:])
    return f"{added} rule(s) added to Preferences." if added else "Nothing new — the rules were already in there."


def remove_rule(text):
    want = _norm(text or "")
    if not want:
        return "Which rule?"
    current = _dated_rules()
    keep = [(r, d) for r, d in current if want not in _norm(r)]
    if len(keep) == len(current):
        return "No learned rule matches that."
    _write_rules(keep)
    return f"{len(current) - len(keep)} rule(s) dropped."


def consolidate(today=None):
    """Turns the week's log entries into a rules proposal. Returns the proposal id or None."""
    entries = privacy.without_work(week_entries(today=today))
    if not entries:
        return None
    existing = rules_list()
    prompt = ("Entries from your learning log this week:\n" + "\n".join(f"- {e}" for e in entries[:60]) +
              ("\n\nRules that already exist (do not repeat):\n" + "\n".join(f"- {r}" for r in existing)
               if existing else "") +
              "\n\nDerive at most 5 fixed rules for yourself from them. Only what came up several times or was "
              "explicitly corrected by the user. Each rule a short instruction to yourself (under 140 characters), plus a "
              "reason. JSON: {\"rules\": [{\"rule\": \"...\", \"why\": \"...\"}]}. Nothing solid = empty list.")
    result = background.parse_json(background.ask("reflection-week", SYSTEM, prompt)) or {}
    rules = [{"rule": " ".join(str(r.get("rule", "")).split())[:200],
              "why": " ".join(str(r.get("why", "")).split())[:200]}
             for r in (result.get("rules") or []) if isinstance(r, dict)]
    known = {_norm(r) for r in existing}
    rules = [r for r in rules if r["rule"] and _norm(r["rule"]) not in known
             and not privacy.is_work(r["rule"], r["why"])][:5]
    if not rules:
        return None
    week = background.week_key(today)
    pid = proposals.add("rules", f"{len(rules)} learned rules from {week}",
                        "From this week's learning log: " + rules[0]["why"], {"rules": rules}, source="reflection")
    events.publish("say", f"Sir, from this week I have derived {len(rules)} rule{'s' if len(rules) != 1 else ''} for myself, for example: "
                          f"{rules[0]['rule']} If you like, say “accept the rules” and I will show them to you "
                          "for confirmation.", kind="learning")
    return pid


def step(now=None):
    now = now or dt.datetime.now()
    reflect()
    if now.weekday() == 6 and now.hour >= WEEK_HOUR and background.claim("reflection-week", background.week_key(now.date())):
        consolidate(now.date())


def start():
    if ENABLED:
        background.loop("reflection", 300, step)
