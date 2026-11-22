"""
Routines: one phrase, several actions — run locally, 0 tokens.

    "Good night"   -> all lights off, say when the alarm rings
    "I am off"     -> switch off the TV and the fan, "See you later, Sir."

They live in config/routines.json (format in config/routines.example.json):

    [{"name": "Good night", "triggers": ["good night", "i am going to bed"],
      "steps": [{"ha": "light.turn_off", "entity_id": "all"},
                {"do": "fan socket off"},
                {"tell": "When does my alarm ring?"},
                {"say": "Sleep well, Sir."}]}]

Step kinds:
  say   fixed text Jarvis says; a list of texts: one of them, never the same twice in a row
  do    a sentence for the local router, its answer is NOT spoken (switching)
  tell  a sentence for the local router, its answer IS spoken (weather, alarm, list)
  ha    a Home Assistant service (light / switch / fan / media_player / scene only);
        "entity_id": "all" is allowed for lights and media players only
  wait  seconds to pause (max 30)

Spoken creation ("When I say good night, turn off all the lights and tell me the
alarm") goes through the LLM, which writes the routine with routine_save.
"""
import json
import os
import pathlib
import random
import re
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
PATH = pathlib.Path(os.environ.get("JARVIS_ROUTINES", ROOT / "config" / "routines.json"))
HA_DOMAINS = {"light", "switch", "fan", "media_player", "scene"}
ALL_OK = {"light", "media_player"}     # "all" sockets would include router and fridge
PROTECTED = re.compile(r"router|\bpc\b|raspi|\bpi\b|kuehlschrank|fridge|smart_?hub|nas", re.I)
_FILLER = r"(?:jarvis|please|sir|hey|ok|okay)"
_CACHE = {"mtime": None, "data": []}


def _norm(text):
    t = re.sub(r"[^\w]+", " ", re.sub(r"['’]", "", (text or "").lower()))
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


# Speech recognition bends the phrase: "Jarvis, good night" came out as "Ciao, what is good night." and went
# to Claude, who told the user to say "good night" (2026-11-19). A trigger of two words or more still counts at
# the start or the end of a short sentence. Not when the sentence is about the phrase or for someone else.
LOOSE_EXTRA = 3
_NOT_LOOSE = re.compile(r"\b(?:when|if|say\w*|said|tell\w*|routine\w*|happens?|wish\w*|not|no|dont|never|why|how come|"
                        r"means?|meaning|german|translat\w*|writ\w*|send\w*|message)\b")


def loose(text, phrase):
    """True when the (normalised) sentence is `phrase` give or take a few misheard words around it."""
    n, p = _norm(text), _norm(phrase)
    if not n or not p:
        return False
    if n == p:
        return True
    words, pw = n.split(), p.split()
    if len(pw) < 2 or len(words) - len(pw) > LOOSE_EXTRA or _NOT_LOOSE.search(n):
        return False
    return words[:len(pw)] == pw or words[-len(pw):] == pw


def find(text):
    """The routine this sentence triggers — the exact phrase, or a short sentence that starts or ends with it."""
    n = _norm(text)
    if not n:
        return None
    m = re.fullmatch(r"(?:start|run|do|play)?\s*(?:the\s+)?(?:routine\s+(.+?)|(.+?)\s+routine)(?:\s+(?:now|please))?", n)
    for r in load():
        if m and _norm(r.get("name")) == (m.group(1) or m.group(2)):
            return r
        if any(_norm(t) == n for t in r.get("triggers") or []):
            return r
    for r in load():
        if any(loose(n, t) for t in r.get("triggers") or []):
            return r
    return None


def validate(steps):
    """Error text, or '' if every step is allowed."""
    if not isinstance(steps, list) or not steps:
        return "steps must be a non-empty list."
    for s in steps:
        if not isinstance(s, dict) or len({"say", "do", "tell", "ha", "wait"} & set(s)) != 1:
            return f"Invalid step: {s}"
        if "say" in s and not (isinstance(s["say"], str) and s["say"].strip() or isinstance(s["say"], list)
                               and s["say"] and all(isinstance(x, str) and x.strip() for x in s["say"])):
            return f"say must be a text or a list of texts: {s}"
        if "ha" in s:
            dom, _, svc = str(s["ha"]).partition(".")
            eid = str(s.get("entity_id") or "")
            if dom not in HA_DOMAINS or not svc.replace("_", "").isalnum():
                return f"Not allowed: {s['ha']} (only {', '.join(sorted(HA_DOMAINS))})."
            if eid == "all" and dom not in ALL_OK:
                return f"'all' only works for lights and media, not for {dom}."
            if eid != "all" and not re.fullmatch(dom + r"\.[\w]+", eid):
                return f"entity_id {eid!r} does not fit {dom}."
            if svc == "turn_off" and PROTECTED.search(eid):
                return f"A routine does not switch {eid} off."
    return ""


def _ha_step(s):
    import jarvis_mcp
    dom, _, svc = s["ha"].partition(".")
    body = dict(s.get("data") or {}, entity_id=s.get("entity_id"))
    jarvis_mcp._ha(f"/api/services/{dom}/{svc}", body)


_LAST_SAID = {}          # routine name -> the text picked last time from a list of sayings


def _pick(say, key):
    if not isinstance(say, list):
        return str(say)
    options = [str(x) for x in say if str(x).strip()] or [""]
    fresh = [x for x in options if x != _LAST_SAID.get(key)] or options
    _LAST_SAID[key] = random.choice(fresh)
    return _LAST_SAID[key]


def run(routine, local):
    """Execute the steps. `local(sentence)` is the router's local path (reply or None)."""
    spoken, failed = [], []
    for i, s in enumerate(routine.get("steps") or []):
        try:
            if "say" in s:
                spoken.append(_pick(s["say"], f"{routine.get('name')}:{i}"))
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
        spoken.append("Did not work: " + ", ".join(failed) + ".")
    return " ".join(spoken) or f"{routine.get('name', 'Routine')} done, Sir."


def describe():
    rs = load()
    if not rs:
        return "No routines yet, Sir. Say for example: when I say good night, turn off all the lights."
    return "Routines: " + "; ".join(f"{r.get('name')} (say: {(r.get('triggers') or ['?'])[0]})" for r in rs) + "."


# ── dry run: what a routine would do, without doing it ───────
_SERVICE_WORDS = {"turn_on": "on", "turn_off": "off", "toggle": "toggle", "media_play": "play",
                  "media_pause": "pause", "media_stop": "stop"}


def _entity_names():
    try:
        import briefing
        return {s["entity_id"]: ((s.get("attributes") or {}).get("friendly_name") or s["entity_id"])
                for s in briefing._ha_states()}
    except Exception:  # noqa: BLE001 — without the house the ids will do
        return {}


def explain(routine, names=None):
    """Spoken list of the steps, nothing runs. names: entity_id → friendly name."""
    names = _entity_names() if names is None else names
    steps = []
    for s in routine.get("steps") or []:
        if "say" in s and isinstance(s["say"], list):
            steps.append(f"I say one of {len(s['say'])} lines, for example “{s['say'][0]}”")
        elif "say" in s:
            steps.append(f"I say “{s['say']}”")
        elif "wait" in s:
            steps.append(f"I wait {s['wait']} seconds")
        elif "ha" in s:
            dom, _, svc = str(s["ha"]).partition(".")
            what = ("all the lights" if s.get("entity_id") == "all" and dom == "light" else
                    "all the speakers" if s.get("entity_id") == "all" else names.get(s.get("entity_id"), s.get("entity_id")))
            verb = _SERVICE_WORDS.get(svc, svc.replace("_", " "))
            extra = ""
            if (s.get("data") or {}).get("brightness_pct") is not None:
                extra = f" at {s['data']['brightness_pct']} percent"
            steps.append(f"{what} {verb}{extra}" if verb in ("on", "off") else f"{verb} {what}{extra}")
        elif "do" in s:
            steps.append(f"command “{s['do']}”")
        elif "tell" in s:
            steps.append(f"I answer “{s['tell']}”")
    name = routine.get("name", "the routine")
    trig = ", ".join(f"“{t}”" for t in (routine.get("triggers") or [])[:3])
    if not steps:
        return f"{name} has no steps, Sir."
    return (f"On {trig or name} this happens, in this order: " + "; ".join(steps)
            + ". That was only the preview — I have not done any of it just now.")


_EXPLAIN = re.compile(r"\b(?:what (?:happens|do you do|would happen|runs),? (?:when|if) i say (?P<phrase>.+?)|"
                      r"what (?:does|is in|is behind) (?:the )?routine (?P<name>.+?)(?: do| mean)?|"
                      r"(?:explain|show|describe)\w* (?:me )?(?:the )?routine (?P<name2>.+?)|"
                      r"preview (?:for |of )?(?:the )?routine (?P<name3>.+?))\s*[?.!]*$", re.I)


def explain_request(raw):
    """'What happens when I say good night?' → the dry run, or None if this is no such question."""
    m = _EXPLAIN.search((raw or "").strip())
    if not m:
        return None
    phrase = (m.group("phrase") or m.group("name") or m.group("name2") or m.group("name3") or "").strip(" “”\"'")
    r = find(phrase) or next((x for x in load() if _norm(x.get("name")) == _norm(phrase)), None)
    if not r:
        return f"I do not know a routine “{phrase}”, Sir. {describe()}"
    return explain(r)


# ── for the LLM tools ────────────────────────────────────────
def tool(name, args):
    data = list(load())
    if name == "routine_list":
        return json.dumps(data, ensure_ascii=False)[:3000] if data else "No routines."
    title = str(args.get("name") or "").strip()[:60]
    if not title:
        return "name is missing."
    rest = [r for r in data if _norm(r.get("name")) != _norm(title)]
    if name == "routine_delete":
        if len(rest) == len(data):
            return "No routine with this name."
        _save(rest)
        return f"Routine {title} deleted."
    triggers = [str(t).strip()[:80] for t in (args.get("triggers") or []) if str(t).strip()]
    steps = args.get("steps") or []
    if not triggers:
        return "triggers is missing (the phrases that start the routine)."
    err = validate(steps)
    if err:
        return err
    taken = {_norm(t) for r in rest for t in r.get("triggers") or []}
    clash = [t for t in triggers if _norm(t) in taken]
    if clash:
        return f"Trigger already taken: {', '.join(clash)}."
    rest.append({"name": title, "triggers": triggers, "steps": steps})
    _save(rest)
    return f"Routine {title} saved, triggers: {', '.join(triggers)}."
