"""
"Why did the light turn on?" — the cause from the Home Assistant logbook, in one sentence. Local, 0 tokens.

    Living room light went on at 23:12. Cause: the automation “Light follows me”, triggered by “Room”
    (to Living room), which was set by: the automation “Where am I”, triggered by “Presence living room” (on).

The logbook stores with every change the context it happened in: which automation, script or person, and
for an automation what triggered it ("state of input_select.room"). This follows that chain up to
two steps back, so the ghost pulses of a presence sensor show up as the real cause. No context = changed
on the device itself or in an app that does not tell HA (Tuya, Hue app).

  explain(entity_id, now)   the sentence, or None when the logbook has nothing for the last 24 hours
  handle(n)                 router: "why is/did … on/off", "who turned on …", "how come … is running"
"""
import datetime as dt
import json
import os
import re
import urllib.parse
import urllib.request

HOURS = int(os.environ.get("JARVIS_WHY_HOURS", "24"))
_ASK = re.compile(r"\b(why|how come|who)\b.*\b(on|off|turned|switched|went|came|running|lit|started|"
                  r"booted|playing)\b")


def _get(path):
    import briefing
    req = urllib.request.Request(briefing.HA_URL + path, headers={"Authorization": f"Bearer {briefing.HA_TOKEN}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read() or b"[]")


def logbook(entity_id, start, end=None):
    q = "?entity=" + urllib.parse.quote(entity_id)
    if end:
        q += "&end_time=" + urllib.parse.quote(end.isoformat())
    return _get("/api/logbook/" + urllib.parse.quote(start.isoformat()) + q)


def _when(iso):
    try:
        t = dt.datetime.fromisoformat(iso).astimezone()
    except (TypeError, ValueError):
        return "at ?"
    today = dt.datetime.now().astimezone().date()
    day = ("" if t.date() == today else "yesterday " if t.date() == today - dt.timedelta(days=1)
           else f"on {t:%B} {t.day} ")
    return f"{day}at {t.hour}:{t.minute:02d}"


def _state_word(state):
    return {"on": "on", "off": "off", "playing": "on", "idle": "off", "unavailable": "unavailable"}.get(state, f"to {state}")


def _source_entity(entry):
    """The entity an automation was triggered by: 'state of input_select.room' -> input_select.room."""
    m = re.search(r"state of ([a-z_]+\.[a-z0-9_]+)", entry.get("context_source") or entry.get("source") or "")
    return m.group(1) if m else None


def _change_at(entity_id, before, states):
    """The last state change of entity_id up to `before` (logbook of the 10 minutes before)."""
    try:
        t = dt.datetime.fromisoformat(before)
    except (TypeError, ValueError):
        return None
    rows = [r for r in logbook(entity_id, t - dt.timedelta(minutes=10), t + dt.timedelta(seconds=2))
            if "state" in r and r.get("when", "") <= before]
    return rows[-1] if rows else None


def _name(eid, states):
    s = states.get(eid) or {}
    return (s.get("attributes") or {}).get("friendly_name") or eid


def cause(entry, states, depth=2):
    """A clause naming what caused one logbook entry, following triggers back up to `depth` steps."""
    who = entry.get("context_name") or entry.get("context_entity_id_name")
    kind = entry.get("context_event_type") or ""
    if kind == "automation_triggered" or entry.get("context_domain") == "automation":
        text = f"the automation “{who}”"
        src = _source_entity(entry)
        if src:
            prev = _change_at(src, entry.get("when", ""), states) if depth > 0 else None
            text += f", triggered by “{_name(src, states)}”"
            if prev and prev.get("state"):
                text += f" ({_state_word(prev['state'])})"
                if depth > 1 and (prev.get("context_event_type") == "automation_triggered"
                                  or prev.get("context_domain") == "automation"):
                    text += ", which was set by: " + cause(prev, states, depth - 1)
        elif (entry.get("context_message") or entry.get("context_source") or "").startswith(("triggered by time", "time")):
            text += " (time-based)"
        return text
    if kind == "script_started" or entry.get("context_domain") == "script":
        return f"the script “{who}”"
    if who:
        return f"{who}"
    if entry.get("context_user_id"):
        return "someone by hand in Home Assistant"
    return "nobody in Home Assistant — switched directly on the device or in its app"


def explain(entity_id, now=None, states=None):
    import briefing
    now = (now or dt.datetime.now()).astimezone()
    states = states if states is not None else {s["entity_id"]: s for s in briefing._ha_states()}
    rows = [r for r in logbook(entity_id, now - dt.timedelta(hours=HOURS)) if "state" in r]
    if not rows:
        return None
    last = rows[-1]
    name = _name(entity_id, states)
    return f"{name} went {_state_word(last.get('state'))} {_when(last.get('when'))}. Cause: {cause(last, states)}."


def handle(n):
    if not _ASK.search(n):
        return None
    import briefing
    import router
    if not briefing.HA_TOKEN:
        return None
    # the zone switches of the presence sensors are settings, not things that "go on"
    n = re.sub(r"\btelevision\b", "tv", n)
    hits = [d for d in router._match_devices(n)
            if not re.search(r"zone|occupancy|presence detection|detection|sensitiv", d["name"], re.I)]
    if len(hits) > 1 and re.search(r"\b(light|lights|lamp|lamps)\b", n):
        hits = [d for d in hits if d["dom"] == "light"] or hits
    if not hits:
        return None
    if len(hits) > 1:
        return "Do you mean " + " or ".join(d["name"] for d in hits[:3]) + ", Sir?"
    try:
        text = explain(hits[0]["eid"])
    except OSError:
        return "I cannot reach the Home Assistant logbook right now, Sir."
    return text or f"The logbook has nothing for {hits[0]['name']} in the last {HOURS} hours, Sir."
