"""
Skill factory: what the router learns becomes a draft first, never an active rule.

  1. learning.observe stores a wording Claude had to resolve ("reading lamp off" →
     light.reading_lamp) as a draft in config/learned.json → "drafts".
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
    pid = proposals.add("skill", f"Learned wording “{d['example']}”",
                        f"Claude had to resolve this {d['seen']}×; from now on it could run locally without tokens.",
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
        problems.append("Draft without devices.")
    for e in ents:
        if e.split(".")[0] not in SWITCHABLE:
            problems.append(f"{e}: I do not switch this kind of device.")
        if router.PROTECTED_OFF.search(e):
            notes.append(f"{e} is protected — switching it off stays locked anyway.")
    known = _ha_entities()
    if known is None:
        notes.append("Home Assistant not reachable right now — devices not checked.")
    else:
        missing = [e for e in ents if e not in known]
        if missing:
            problems.append("Does not exist (any more): " + ", ".join(missing))
    for r in routines.load():
        if any(learning.key(t) == key for t in r.get("triggers") or []):
            problems.append(f"Collides with the trigger of the routine “{r.get('name')}”.")
    current = (data.get("aliases") or {}).get(key)
    if current and sorted(current.get("entities", [])) != sorted(ents):
        notes.append("Replaces the previous rule (" + ", ".join(current.get("entities", [])) + ") — can be undone.")
    return problems, notes


def activate(key):
    """Turns a draft into a live rule after the checks. Returns spoken text."""
    data = dict(learning.load())
    drafts = dict(data.get("drafts") or {})
    d = drafts.get(key)
    if not d:
        return "This draft does not exist (any more)."
    problems, notes = check(key)
    if problems:
        return "Not activated: " + " ".join(problems)
    aliases = dict(data.get("aliases") or {})
    before = aliases.get(key)
    version = (before or {}).get("version", 0) + 1
    aliases[key] = {"entities": d["entities"], "learned": time.strftime("%Y-%m-%d"), "example": d["example"],
                    "version": version}
    drafts.pop(key)
    data.update(aliases=aliases, drafts=drafts)
    learning._save(data)
    _history("activate", key, before, aliases[key], " ".join(notes))
    return f"Active: “{d['example']}” now runs locally (version {version})." + (" " + " ".join(notes) if notes else "")


def undo(text):
    """Restores the previous version of a rule, or removes it. `text`: words of the wording."""
    want = learning.key(text or "")
    data = dict(learning.load())
    aliases = dict(data.get("aliases") or {})
    keys = [k for k in aliases if k == want or (want and want in k)]
    if not keys:
        return "No active learned wording matches that."
    if len(keys) > 1:
        return "Several match: " + ", ".join(keys[:5]) + " — please be more precise."
    key = keys[0]
    current = aliases[key]
    prev = next((r["before"] for r in reversed(history(key)) if r["action"] == "activate"
                 and r["after"] and r["after"].get("version") == current.get("version")), None)
    if prev:
        aliases[key] = prev
        msg = f"Back to version {prev.get('version', 1)}: " + ", ".join(prev.get("entities", []))
    else:
        aliases.pop(key)
        msg = f"Learned wording “{current.get('example', key)}” removed."
    data["aliases"] = aliases
    learning._save(data)
    _history("undo", key, current, aliases.get(key))
    return msg


def listing():
    data = learning.load()
    out = []
    for k, v in sorted((data.get("aliases") or {}).items()):
        out.append(f"active v{v.get('version', 1)}: “{v.get('example', k)}” → {', '.join(v.get('entities', []))}")
    for k, v in sorted((data.get("drafts") or {}).items()):
        out.append(f"draft ({v.get('seen', 1)}×{', proposed' if v.get('proposed') else ''}): "
                   f"“{v.get('example', k)}” → {', '.join(v.get('entities', []))}")
    for k, v in sorted((data.get("choices") or {}).items()):
        out.append(f"question answered ({v.get('at', '?')}): “{v.get('example', k)}” → {', '.join(v.get('entities', []))}")
    return "\n".join(out) or "Nothing learned yet."
