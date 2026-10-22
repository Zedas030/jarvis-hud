"""
Open proposals from the learning modules — things Jarvis wants to change but
may only change after the user confirms a card.

A background job cannot show a card (cards exist only inside a turn in the
HUD). So it stores a proposal here and mentions it; in the next conversation
the proposal sits in the context block, Jarvis explains it in German and calls
proposal_accept — which always goes through the confirmation card.

Kinds and what accepting does:
  rules     learned rules → section "Gelernte Regeln" in the note Vorlieben (reflection.py)
  routine   a routine → config/routines.json (routines.py)
  reminder  a reminder → the scheduler in the HUD server
  idea      needs new code: only noted as wanted, Jarvis never changes his own code

Stored in its own file (JARVIS_PROPOSALS), not in state.json, because both the
server and jarvis_mcp.py (a separate process) write it. Proposals expire after
JARVIS_PROPOSAL_DAYS (7) days.
"""
import json
import os
import pathlib
import secrets
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent
PATH = pathlib.Path(os.environ.get("JARVIS_PROPOSALS", ROOT / "logs" / "proposals.json"))
DAYS = int(os.environ.get("JARVIS_PROPOSAL_DAYS", "7"))
KINDS = ("rules", "routine", "reminder", "idea")
_LOCK = threading.Lock()


def _load():
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(items):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, PATH)


def _update(fn):
    with _LOCK:
        items = _load()
        out = fn(items)
        _save(items)
        return out


def add(kind, title, why, payload=None, source=""):
    """Stores a proposal; an open one of the same kind and source is replaced. Returns its id."""
    if kind not in KINDS:
        raise ValueError(kind)
    pid = secrets.token_hex(3)

    def fn(items):
        now = time.time()
        for p in items:
            if p["status"] == "open" and p["kind"] == kind and p.get("source") == source:
                p["status"] = "replaced"
        items.append(dict(id=pid, kind=kind, title=title.strip()[:120], why=why.strip()[:400],
                          payload=payload or {}, source=source, status="open", created=now,
                          expires=now + DAYS * 86400))
        del items[:max(0, len(items) - 60)]
    _update(fn)
    return pid


def pending():
    now = time.time()
    return [p for p in _load() if p["status"] == "open" and p.get("expires", 0) > now]


def get(pid):
    return next((p for p in _load() if p["id"] == (pid or "").strip()), None)


def resolve(pid, status):
    def fn(items):
        for p in items:
            if p["id"] == pid:
                p["status"], p["resolved"] = status, time.time()
                return p
        return None
    return _update(fn)


def describe(p):
    """What the card shows: in plain German, what happens on yes."""
    pl = p.get("payload") or {}
    if p["kind"] == "rules":
        body = "\n".join(f"• {r.get('regel', r) if isinstance(r, dict) else r}" for r in pl.get("rules") or [])
        return f"Diese Regeln in die Notiz Vorlieben übernehmen:\n{body}"
    if p["kind"] == "routine":
        steps = "; ".join(next(iter(s.values()), "") if isinstance(s, dict) else str(s) for s in pl.get("steps") or [])
        return f"Routine „{pl.get('name', p['title'])}“ anlegen. Auslöser: {', '.join(pl.get('triggers') or [])}. Schritte: {steps}"
    if p["kind"] == "reminder":
        return f"Erinnerung anlegen: {pl.get('text', p['title'])} um {pl.get('at', '?')}"
    return f"Als gewünschte Fähigkeit vormerken (bauen müssen Sie sie in Claude Code): {p['title']}"


def block():
    """Context for the next turn: what is waiting for the user's yes or no."""
    items = pending()
    if not items:
        return ""
    rows = "\n".join(f"- {p['id']}: {p['title']} — {p['why']}" for p in items[:4])
    return ("Offene Vorschläge von dir (warten auf den User):\n" + rows +
            "\nSagt der User ja oder fragt danach: erst auf Deutsch erklären, was passiert und warum, dann "
            "proposal_accept mit id und begruendung. Bei nein: proposal_decline. Nicht von selbst aufdrängen, "
            "höchstens einmal kurz erwähnen, wenn es passt.")


def accept(pid, internal=None):
    """Carries out an accepted proposal. internal(op, args) reaches the HUD server (reminders)."""
    p = get(pid)
    if not p:
        return "Diesen Vorschlag gibt es nicht."
    if p["status"] != "open" or p.get("expires", 0) < time.time():
        return "Dieser Vorschlag ist nicht mehr offen."
    pl = p.get("payload") or {}
    if p["kind"] == "rules":
        import reflection
        result = reflection.apply_rules(pl.get("rules") or [])
    elif p["kind"] == "routine":
        import routines
        result = routines.tool("routine_save", pl)
    elif p["kind"] == "reminder":
        result = internal("reminder_add", pl) if internal else "Erinnerungen gehen nur über den Server."
    else:
        result = "Vorgemerkt."
    resolve(pid, "accepted")
    try:
        import suggestions
        suggestions.mark(p, "angenommen")
    except Exception:  # noqa: BLE001 — the note is a log, not a requirement
        pass
    return result


def decline(pid):
    p = resolve((pid or "").strip(), "declined")
    if not p:
        return "Diesen Vorschlag gibt es nicht."
    try:
        import suggestions
        suggestions.mark(p, "abgelehnt")
    except Exception:  # noqa: BLE001
        pass
    return "Verworfen."
