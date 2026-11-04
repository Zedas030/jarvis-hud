"""
Skill factory: what the router learns becomes a draft first, never an active rule.

  1. learning.observe stores a wording Claude had to resolve ("Leselampe aus" →
     light.leselampe) as a draft in config/learned.json → "drafts".
  2. Seen JARVIS_SKILL_MIN_SEEN (2) times, the draft becomes a proposal
     (proposals.py, kind "skill") — the user accepts it on a card.
  3. Accepting runs checks first (check): the devices exist and may be switched,
     no routine trigger uses the wording, an existing rule with other devices is
     reported as a replacement. Checks only read — they never switch anything.
  4. Only then the rule goes live (learning "aliases"). Every activation, change
     and removal is versioned in config/learned.history.jsonl; undo() restores the
     previous version or removes the rule (card, policy CONFIRM).

Learned rules never bypass protections: the router still refuses to switch off
router, PC, server or fridge, and only switches light/switch/fan/media_player/scene.
No code is written or changed at runtime — rules are data.
"""
import json
import os
import pathlib
import time

import learning

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
HISTORY = pathlib.Path(os.environ.get("JARVIS_LEARNED_HISTORY", str(learning.PATH).replace(".json", ".history.jsonl")))
MIN_SEEN = int(os.environ.get("JARVIS_SKILL_MIN_SEEN", "2"))
SWITCHABLE = {"light", "switch", "fan", "media_player", "scene"}


def _history(action, key, before, after, note=""):
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "action": action, "key": key,
                             "before": before, "after": after, "note": note}, ensure_ascii=False) + "\n")


def history(key=None):
    try:
        rows = [json.loads(ln) for ln in HISTORY.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except (OSError, ValueError):
        return []
    return [r for r in rows if key is None or r["key"] == key]


def draft_seen(key):
    """Called by learning.observe after storing a draft: propose it once it recurs. Returns the proposal id or None."""
    import proposals
    d = (learning.load().get("drafts") or {}).get(key)
    if not d or d.get("seen", 0) < MIN_SEEN or d.get("proposed"):
        return None
    pid = proposals.add("skill", f"Gelernte Formulierung „{d['example']}“",
                        f"{d['seen']}× musste Claude das auflösen; künftig ginge es lokal ohne Tokens.",
                        {"key": key}, source="skill_factory")
    data = dict(learning.load())
    drafts = dict(data.get("drafts") or {})
    drafts[key] = dict(drafts[key], proposed=pid)
    data["drafts"] = drafts
    learning._save(data)
    return pid


def _ha_entities():
    try:
        import briefing
        return {s["entity_id"] for s in briefing._ha_states()}
    except Exception:  # noqa: BLE001
        return None


def check(key, entities=None):
    """(problems, notes) for activating this draft. Problems block; notes are shown on the card."""
    import router
    import routines
    data = learning.load()
    d = (data.get("drafts") or {}).get(key) or {}
    ents = entities or d.get("entities") or []
    problems, notes = [], []
    if not ents:
        problems.append("Entwurf ohne Geräte.")
    for e in ents:
        if e.split(".")[0] not in SWITCHABLE:
            problems.append(f"{e}: diese Geräteart schalte ich nicht.")
        if router.PROTECTED_OFF.search(e):
            notes.append(f"{e} ist geschützt — ausschalten bleibt trotzdem gesperrt.")
    known = _ha_entities()
    if known is None:
        notes.append("Home Assistant gerade nicht erreichbar — Geräte nicht geprüft.")
    else:
        missing = [e for e in ents if e not in known]
        if missing:
            problems.append("Gibt es nicht (mehr): " + ", ".join(missing))
    for r in routines.load():
        if any(learning.key(t) == key for t in r.get("triggers") or []):
            problems.append(f"Kollidiert mit dem Auslöser der Routine „{r.get('name')}“.")
    current = (data.get("aliases") or {}).get(key)
    if current and sorted(current.get("entities", [])) != sorted(ents):
        notes.append("Ersetzt die bisherige Regel (" + ", ".join(current.get("entities", [])) + ") — rücknehmbar.")
    return problems, notes


def activate(key):
    """Turns a draft into a live rule after the checks. Returns German text."""
    data = dict(learning.load())
    drafts = dict(data.get("drafts") or {})
    d = drafts.get(key)
    if not d:
        return "Diesen Entwurf gibt es nicht (mehr)."
    problems, notes = check(key)
    if problems:
        return "Nicht aktiviert: " + " ".join(problems)
    aliases = dict(data.get("aliases") or {})
    before = aliases.get(key)
    version = (before or {}).get("version", 0) + 1
    aliases[key] = {"entities": d["entities"], "learned": time.strftime("%Y-%m-%d"), "example": d["example"],
                    "version": version}
    drafts.pop(key)
    data.update(aliases=aliases, drafts=drafts)
    learning._save(data)
    _history("activate", key, before, aliases[key], " ".join(notes))
    return f"Aktiv: „{d['example']}“ geht jetzt lokal (Version {version})." + (" " + " ".join(notes) if notes else "")


def undo(text):
    """Restores the previous version of a rule, or removes it. `text`: words of the wording."""
    want = learning.key(text or "")
    data = dict(learning.load())
    aliases = dict(data.get("aliases") or {})
    keys = [k for k in aliases if k == want or (want and want in k)]
    if not keys:
        return "Keine aktive gelernte Formulierung passt dazu."
    if len(keys) > 1:
        return "Mehrere passen: " + ", ".join(keys[:5]) + " — bitte genauer."
    key = keys[0]
    current = aliases[key]
    prev = next((r["before"] for r in reversed(history(key)) if r["action"] == "activate"
                 and r["after"] and r["after"].get("version") == current.get("version")), None)
    if prev:
        aliases[key] = prev
        msg = f"Zurück auf Version {prev.get('version', 1)}: " + ", ".join(prev.get("entities", []))
    else:
        aliases.pop(key)
        msg = f"Gelernte Formulierung „{current.get('example', key)}“ entfernt."
    data["aliases"] = aliases
    learning._save(data)
    _history("undo", key, current, aliases.get(key))
    return msg


def listing():
    data = learning.load()
    out = []
    for k, v in sorted((data.get("aliases") or {}).items()):
        out.append(f"aktiv v{v.get('version', 1)}: „{v.get('example', k)}“ → {', '.join(v.get('entities', []))}")
    for k, v in sorted((data.get("drafts") or {}).items()):
        out.append(f"Entwurf ({v.get('seen', 1)}×{', vorgeschlagen' if v.get('proposed') else ''}): "
                   f"„{v.get('example', k)}“ → {', '.join(v.get('entities', []))}")
    for k, v in sorted((data.get("choices") or {}).items()):
        out.append(f"Rückfrage beantwortet ({v.get('at', '?')}): „{v.get('example', k)}“ → {', '.join(v.get('entities', []))}")
    return "\n".join(out) or "Noch nichts gelernt."
