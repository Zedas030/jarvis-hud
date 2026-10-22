"""
Routines: one phrase, several actions — run locally, 0 tokens.

    "Gute Nacht"   -> all lights off, say when the alarm rings
    "Ich bin weg"  -> switch off the TV and the fan, "Bis später, Sir."

They live in config/routines.json (format in config/routines.example.json):

    [{"name": "Gute Nacht", "triggers": ["gute nacht", "ich geh schlafen"],
      "steps": [{"ha": "light.turn_off", "entity_id": "all"},
                {"do": "Steckdose Ventilator aus"},
                {"tell": "Wann klingelt mein Wecker?"},
                {"say": "Schlafen Sie gut, Sir."}]}]

Step kinds:
  say   fixed text Jarvis says
  do    a sentence for the local router, its answer is NOT spoken (switching)
  tell  a sentence for the local router, its answer IS spoken (weather, alarm, list)
  ha    a Home Assistant service (light / switch / fan / media_player / scene only);
        "entity_id": "all" is allowed for lights and media players only
  wait  seconds to pause (max 30)

Spoken creation ("Wenn ich Gute Nacht sage, mach alle Lichter aus und sag mir den
Wecker") goes through the LLM, which writes the routine with routine_save.
"""
import json
import os
import pathlib
import re
import time

ROOT = pathlib.Path(__file__).resolve().parent
PATH = pathlib.Path(os.environ.get("JARVIS_ROUTINES", ROOT / "config" / "routines.json"))
HA_DOMAINS = {"light", "switch", "fan", "media_player", "scene"}
ALL_OK = {"light", "media_player"}     # "all" sockets would include router and fridge
PROTECTED = re.compile(r"router|\bpc\b|raspi|\bpi\b|kühlschrank|fridge|smart_?hub|nas", re.I)
_FILLER = r"(?:jarvis|bitte|sir|mal|hey|ok|okay)"
_CACHE = {"mtime": None, "data": []}


def _norm(text):
    t = re.sub(r"[^\wäöüß]+", " ", (text or "").lower())
    t = re.sub(r"\b" + _FILLER + r"\b", " ", t)
    return " ".join(t.split())


def load():
    try:
        mtime = PATH.stat().st_mtime
    except OSError:
        return []
    if _CACHE["mtime"] != mtime:
        try:
            data = json.loads(PATH.read_text(encoding="utf-8"))
            _CACHE.update(mtime=mtime, data=data if isinstance(data, list) else [])
        except (OSError, ValueError):
            _CACHE.update(mtime=mtime, data=[])
    return _CACHE["data"]


def _save(data):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, PATH)
    _CACHE["mtime"] = None


def find(text):
    """The routine this sentence triggers — only on (nearly) the exact phrase."""
    n = _norm(text)
    if not n:
        return None
    m = re.fullmatch(r"(?:starte?|führ\w*|mach\w*)?\s*(?:die\s+)?routine\s+(.+?)(?:\s+(?:aus|an|starten|los))?", n)
    for r in load():
        if m and _norm(r.get("name")) == m.group(1):
            return r
        if any(_norm(t) == n for t in r.get("triggers") or []):
            return r
    return None


def validate(steps):
    """Error text, or '' if every step is allowed."""
    if not isinstance(steps, list) or not steps:
        return "steps muss eine nicht-leere Liste sein."
    for s in steps:
        if not isinstance(s, dict) or len({"say", "do", "tell", "ha", "wait"} & set(s)) != 1:
            return f"Ungültiger Schritt: {s}"
        if "ha" in s:
            dom, _, svc = str(s["ha"]).partition(".")
            eid = str(s.get("entity_id") or "")
            if dom not in HA_DOMAINS or not svc.replace("_", "").isalnum():
                return f"Nicht erlaubt: {s['ha']} (nur {', '.join(sorted(HA_DOMAINS))})."
            if eid == "all" and dom not in ALL_OK:
                return f"'all' geht nur bei Licht und Medien, nicht bei {dom}."
            if eid != "all" and not re.fullmatch(dom + r"\.[\w]+", eid):
                return f"entity_id {eid!r} passt nicht zu {dom}."
            if svc == "turn_off" and PROTECTED.search(eid):
                return f"{eid} schaltet eine Routine nicht aus."
    return ""


def _ha_step(s):
    import jarvis_mcp
    dom, _, svc = s["ha"].partition(".")
    body = dict(s.get("data") or {}, entity_id=s.get("entity_id"))
    jarvis_mcp._ha(f"/api/services/{dom}/{svc}", body)


def run(routine, local):
    """Execute the steps. `local(sentence)` is the router's local path (reply or None)."""
    spoken, failed = [], []
    for s in routine.get("steps") or []:
        try:
            if "say" in s:
                spoken.append(str(s["say"]))
            elif "wait" in s:
                time.sleep(max(0, min(float(s["wait"]), 30)))
            elif "ha" in s:
                if validate([s]):
                    failed.append(s["ha"])
                else:
                    _ha_step(s)
            else:
                text = str(s.get("do") or s.get("tell"))
                reply = local(text)
                if reply is None:
                    failed.append(text)
                elif "tell" in s:
                    spoken.append(reply)
        except Exception:  # noqa: BLE001 — one dead device must not stop the rest
            failed.append(str(s.get("ha") or s.get("do") or s.get("tell")))
    if failed:
        spoken.append("Nicht geklappt hat: " + ", ".join(failed) + ".")
    return " ".join(spoken) or f"{routine.get('name', 'Routine')} erledigt, Sir."


def describe():
    rs = load()
    if not rs:
        return "Noch keine Routinen, Sir. Sagen Sie zum Beispiel: Wenn ich Gute Nacht sage, mach alle Lichter aus."
    return "Routinen: " + "; ".join(f"{r.get('name')} (sagen Sie: {(r.get('triggers') or ['?'])[0]})" for r in rs) + "."


# ── for the LLM tools ────────────────────────────────────────
def tool(name, args):
    data = list(load())
    if name == "routine_list":
        return json.dumps(data, ensure_ascii=False)[:3000] if data else "Keine Routinen."
    title = str(args.get("name") or "").strip()[:60]
    if not title:
        return "name fehlt."
    rest = [r for r in data if _norm(r.get("name")) != _norm(title)]
    if name == "routine_delete":
        if len(rest) == len(data):
            return "Keine Routine mit diesem Namen."
        _save(rest)
        return f"Routine {title} gelöscht."
    triggers = [str(t).strip()[:80] for t in (args.get("triggers") or []) if str(t).strip()]
    steps = args.get("steps") or []
    if not triggers:
        return "triggers fehlt (die Sätze, die die Routine starten)."
    err = validate(steps)
    if err:
        return err
    taken = {_norm(t) for r in rest for t in r.get("triggers") or []}
    clash = [t for t in triggers if _norm(t) in taken]
    if clash:
        return f"Auslöser schon vergeben: {', '.join(clash)}."
    rest.append({"name": title, "triggers": triggers, "steps": steps})
    _save(rest)
    return f"Routine {title} gespeichert, Auslöser: {', '.join(triggers)}."
