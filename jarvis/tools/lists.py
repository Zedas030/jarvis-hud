"""
Shopping list and to-do list — by voice, without the LLM.

    "Put milk and eggs on the shopping list", "Butter on the shopping list"
    "What is on the shopping list?", "Cross the milk off the shopping list"
    "Write tax return on my to-do list", "Clear the shopping list"

Where a list lives:
  - a Home Assistant to-do entity (JARVIS_SHOPPING_LIST / JARVIS_TODO_LIST, e.g.
    todo.shopping_list) — then it shows up in the HA app on the phone as well;
  - otherwise a checkbox note in the vault (Memories/Lists/<Name>.md),
    which Obsidian shows as a real checklist.
If the HA entity cannot be reached the note is used, so nothing gets lost.
Removing marks an item done in HA (undo in the app), or deletes the line locally.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

import memory

HA_URL = os.environ.get("HA_URL", "http://homeassistant.local:8123").strip().rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "").strip()
LISTS = {
    "shopping": {"title": "Shopping list", "entity": os.environ.get("JARVIS_SHOPPING_LIST", "").strip()},
    "todo": {"title": "To-do list", "entity": os.environ.get("JARVIS_TODO_LIST", "").strip()},
    "print_ideas": {"title": "Print ideas", "entity": ""},      # print_ideas.py, always the vault note
}
LIST_DIR = memory.MEMORY_DIR / "Lists"


def which(text):
    t = (text or "").lower()
    if re.search(r"shopping|grocer", t):
        return "shopping"
    if re.search(r"to[\s-]*do|task\s*list|errands?\s*list", t):
        return "todo"
    return None


# ── backends ─────────────────────────────────────────────────
def _ha(path, body):
    req = urllib.request.Request(HA_URL + path, method="POST", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {HA_TOKEN}", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"null")


def _use_ha(key):
    return bool(HA_TOKEN and LISTS[key]["entity"])


def _note(key):
    return LIST_DIR / f"{LISTS[key]['title']}.md"


def _read_note(key):
    p = _note(key)
    if not p.exists():
        return []
    return [m.group(1).strip() for m in re.finditer(r"(?m)^- \[ \] (.+)$", p.read_text(encoding="utf-8"))]


def _write_note(key, entries):
    p = _note(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = (f"---\ntype: list\nimportance: medium\nupdated: {time.strftime('%Y-%m-%d')}\n---\n\n"
            f"# {LISTS[key]['title']}\n\nBelongs to [[Profile]]. Jarvis keeps this list by voice.\n\n"
            + "".join(f"- [ ] {e}\n" for e in entries))
    tmp = p.with_suffix(".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, p)


def entries(key):
    if _use_ha(key):
        try:
            res = _ha("/api/services/todo/get_items?return_response",
                      {"entity_id": LISTS[key]["entity"], "status": "needs_action"})
            resp = (res or {}).get("service_response") or {}
            return [i["summary"] for i in (resp.get(LISTS[key]["entity"]) or {}).get("items", [])]
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            pass
    return _read_note(key)


def add(key, items):
    items = [i for i in items if i]
    if not items:
        return []
    have = {e.lower() for e in entries(key)}
    new = [i for i in items if i.lower() not in have]
    if _use_ha(key):
        try:
            for i in new:
                _ha("/api/services/todo/add_item", {"entity_id": LISTS[key]["entity"], "item": i})
            return new
        except (urllib.error.URLError, OSError, ValueError):
            pass
    _write_note(key, _read_note(key) + new)
    return new


def _find(item, current):
    want = item.lower().strip()
    exact = [e for e in current if e.lower() == want]
    if exact:
        return exact[0]
    loose = [e for e in current if want in e.lower() or e.lower() in want]
    return loose[0] if len(loose) == 1 else None


def remove(key, item):
    """Returns the entry that was removed, or None."""
    current = entries(key)
    hit = _find(item, current)
    if not hit:
        return None
    if _use_ha(key):
        try:
            _ha("/api/services/todo/update_item", {"entity_id": LISTS[key]["entity"], "item": hit,
                                                   "status": "completed"})
            return hit
        except (urllib.error.URLError, OSError, ValueError):
            pass
    _write_note(key, [e for e in _read_note(key) if e != hit])
    return hit


def clear(key):
    current = entries(key)
    if _use_ha(key):
        try:
            for e in current:
                _ha("/api/services/todo/update_item", {"entity_id": LISTS[key]["entity"], "item": e,
                                                       "status": "completed"})
            return len(current)
        except (urllib.error.URLError, OSError, ValueError):
            pass
    _write_note(key, [])
    return len(current)


# ── the voice side ───────────────────────────────────────────
_LIST_WORD = (r"(?:shopping\s*list|grocery\s*list|groceries\s*list|to[\s-]*do[\s-]*list|todo[\s-]*list|to[\s-]*dos?|"
              r"task\s*list|errands?\s*list)")
_ADD = re.compile(r"^(?:\W*(?:hey\s+)?jarvis\W+)?(?:please\s+)?(?:(?:add|put|write|stick|pop|note|jot|we\s+need|i\s+need)"
                  r"(?:\s+down)?\s+)?(?:please\s+)?(?:some\s+)?"
                  r"(?P<items>.+?)\s*,?\s+(?:on|to|onto|in)\s+(?:the|my|our)?\s*"
                  + _LIST_WORD + r"\W*(?:please|sir)?\W*$", re.I)
_REMOVE = re.compile(r"^(?:\W*jarvis\W+)?(?:please\s+)?(?:remove|take|delete|strike|cross|tick|scratch)\s+(?:off\s+)?(?:please\s+)?"
                     r"(?P<item>.+?)\s+(?:off|from|off of)\s+(?:the|my|our)?\s*" + _LIST_WORD +
                     r"\W*(?:please|sir)?\W*$", re.I)
_DONE = re.compile(r"^(?:\W*jarvis\W+)?(?:tick|cross|check)\s+(?P<item>.+?)\s+off\W*$", re.I)
_READ = re.compile(r"\b(what(?:s|\s+is|\s+do|\s+does|\s+have)?(?:\s+\w+)?\s+on|whats|show|read|tell\s+me|which|"
                   r"how\s+(?:long|many)|is\s+.{0,30}\s+on|are\s+.{0,30}\s+on)\b", re.I)
_CLEAR = re.compile(r"^(?:\W*jarvis\W+)?(?:please\s+)?(?:clear|empty|delete|wipe)\s+(?:the|my|our)?\s*"
                    + _LIST_WORD + r"(?:\s+(?:completely|entirely))?\W*(?:please)?\W*$", re.I)


def split_items(text):
    text = re.sub(r"\s+(?:and|plus|as\s+well\s+as)\s+", ",", text.strip(), flags=re.I)
    out = []
    for part in text.split(","):
        part = re.sub(r"^(?:also\s+|please\s+|some\s+)+", "", part.strip(" .!?"), flags=re.I)
        part = re.sub(r"^(?:the|a|an)\s+", "", part, flags=re.I).strip()
        if part:
            out.append(part[0].upper() + part[1:])
    return out


def say_list(names):
    if not names:
        return ""
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def handle(n, raw=""):
    """Router handler: spoken reply, or None if this is not about a list."""
    raw = (raw or n).strip()
    if not re.search(_LIST_WORD, raw, re.I):
        return None
    key = which(raw)
    if not key:
        return None
    title = LISTS[key]["title"]
    name = title.lower()
    if _CLEAR.match(raw):
        count = clear(key)
        return f"{title} cleared, {count} entries ticked off, Sir." if count else f"The {name} was already empty, Sir."
    m = _REMOVE.match(raw) or _DONE.match(raw)
    if m:
        item = split_items(m.group("item"))
        if not item:
            return None
        gone = [g for g in (remove(key, i) for i in item) if g]
        if not gone:
            return f"{say_list(item)} is not on the {name}, Sir."
        return f"{say_list(gone)} crossed off, Sir."
    m = _ADD.match(raw)
    if m and not _READ.match(raw):
        what = re.sub(r"\s+(?:needs?\s+to\s+go|has\s+to\s+go|should\s+go)$", "", m.group("items"), flags=re.I)
        item = split_items(what)
        if not item or any(len(i) > 60 for i in item):
            return None
        new = add(key, item)
        if not new:
            return "Already on it, Sir."
        return f"{say_list(new)} {'is' if len(new) == 1 else 'are'} on the {name}, Sir."
    if _READ.search(raw) or raw.rstrip().endswith("?"):
        current = entries(key)
        if not current:
            return f"The {name} is empty, Sir."
        if len(current) > 12:
            return f"There are {len(current)} things on the {name}, first: {say_list(current[:8])}."
        return f"On the {name}: {say_list(current)}."
    return None


# ── for the LLM tools ────────────────────────────────────────
def tool(name, args):
    key = which(args.get("list") or "") or ("todo" if "todo" in (args.get("list") or "").lower() else "shopping")
    if name == "list_read":
        return "\n".join(entries(key)) or "The list is empty."
    if name == "list_add":
        items = args.get("items") or []
        if isinstance(items, str):
            items = split_items(items)
        new = add(key, [str(i).strip()[:80] for i in items])
        return f"Added: {', '.join(new)}" if new else "Already on it."
    if name == "list_remove":
        hit = remove(key, str(args.get("item") or ""))
        return f"Ticked off: {hit}" if hit else "Not found — call list_read first."
    return "unknown"
