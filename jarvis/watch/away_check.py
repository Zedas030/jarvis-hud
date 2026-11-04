"""
Leaving home: once the user's person entity in Home Assistant has been away for a few minutes,
Jarvis looks at what is still on and tells the phone. He switches nothing himself — what is on
becomes one proposal ("Switch off: …"), which goes through the confirmation card in Jarvis.

  what counts    lights that are on, media players that play, and the plugs listed in
                 config/home.json → "away_check" → "switches" that draw more than their
                 threshold (a TV plug is "on" all day; only a running TV draws 50 W)
  when           the person entity (config/home.json → "person") changes from home to
                 anything else and stays there JARVIS_AWAY_MINUTES (3); once per leaving
  what is kept   only the times of the last handled leaving and coming back (state.json →
                 away_checked, away_since, welcomed), no record of comings and goings

Coming back: after at least JARVIS_WELCOME_MINUTES (20) away, "welcome back" with what
Jarvis announced in the meantime (events.notices_since) — the package, the dishwasher, an
outage. An open "switch off" proposal from the leaving is dropped: the user is home now.

config/home.json:
  "away_check": {"switches": {"switch.tv": 30, "switch.kitchen_boiler": 20}, "lights": true, "media": true}
"""
import datetime as dt
import os
import re

MINUTES = float(os.environ.get("JARVIS_AWAY_MINUTES", "3"))
WELCOME_MIN = float(os.environ.get("JARVIS_WELCOME_MINUTES", "20"))
SKIP_KINDS = {"alarm", "timers", "welcome", "wake"}
ENABLED = os.environ.get("JARVIS_AWAY_CHECK", "1").strip() not in ("0", "false", "no", "off")
POWER_SUFFIX = os.environ.get("JARVIS_PLUG_POWER_SUFFIX", "_current_consumption")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _name(s):
    return ((s.get("attributes") or {}).get("friendly_name") or s["entity_id"]).strip()


def still_on(states, cfg):
    """[(entity, label)] of what runs although nobody is home."""
    out = []
    for eid, s in sorted(states.items()):
        dom = eid.split(".")[0]
        if dom == "light" and cfg.get("lights", True) and s.get("state") == "on":
            out.append((eid, _name(s)))
        elif dom == "media_player" and cfg.get("media", True) and s.get("state") == "playing":
            out.append((eid, _name(s)))
    for eid, min_w in (cfg.get("switches") or {}).items():
        s = states.get(eid) or {}
        if s.get("state") != "on":
            continue
        watts = _num((states.get(f"sensor.{eid.split('.', 1)[1]}{POWER_SUFFIX}") or {}).get("state"))
        if watts is None or watts >= float(min_w):
            out.append((eid, _name(s) + (f" ({round(watts)} W)" if watts is not None else "")))
    return out


def left_home(person, handled, now):
    """The last_changed of a leaving to handle now, or None."""
    if not person or person.get("state") in ("home", "unknown", "unavailable", None):
        return None
    changed = person.get("last_changed") or ""
    try:
        since = dt.datetime.fromisoformat(changed)
    except ValueError:
        return None
    if changed == handled or (now - since).total_seconds() < MINUTES * 60:
        return None
    if (now - since).total_seconds() > 3600:          # Jarvis was off when the user left: too late to help
        return None
    return changed


def message(found):
    names = ", ".join(label for _, label in found)
    return (f"You are out, at home this is still running: {names}. "
            "Tell Jarvis “switch off” and a card comes up to confirm.")


def came_back(person, away_since, welcomed, now):
    """The last_changed of a coming back worth a welcome, or None."""
    if not person or person.get("state") != "home" or not away_since:
        return None
    changed = person.get("last_changed") or ""
    try:
        back = dt.datetime.fromisoformat(changed)
    except ValueError:
        return None
    if changed == welcomed or (now - back).total_seconds() > 1800:      # Jarvis was off when the user came home
        return None
    if back.timestamp() - away_since < WELCOME_MIN * 60:
        return None                                   # only out for the bins
    return changed


def welcome(notes):
    """The spoken welcome from the notices of the absence (each once, newest last)."""
    seen, items = set(), []
    for n in notes:
        if n.get("kind") in SKIP_KINDS:
            continue
        text = re.sub(r"^Sir,\s*", "", n.get("text", "")).strip()
        text = text[:1].upper() + text[1:]
        if text and text not in seen:
            seen.add(text)
            items.append(text)
    if not items:
        return "Welcome back, Sir. While you were out, everything was quiet."
    more = f" And {len(items) - 4} more notices in the bell." if len(items) > 4 else ""
    return "Welcome back, Sir. While you were out: " + " ".join(items[-4:]) + more


def check(states_list, now=None):
    if not ENABLED:
        return None
    import commands
    import jarvis_profile as profile
    states = {s["entity_id"]: s for s in states_list}
    person = states.get(profile.ha("person") or "")
    now = now or dt.datetime.now(dt.timezone.utc)
    st = commands.load()
    back = came_back(person, st.get("away_since"), st.get("welcomed", ""), now)
    if back:
        return _welcome(back, st["away_since"])
    handled = st.get("away_checked", "")
    changed = left_home(person, handled, now)
    if not changed:
        return None
    since = dt.datetime.fromisoformat(changed).timestamp()
    commands.update_state(lambda d: d.update(away_checked=changed, away_since=since))
    found = still_on(states, profile.ha("away_check", {}) or {})
    if not found:
        return None
    import phone_actions
    import proposals
    text = message(found)
    pid = proposals.add("switch_off", "Switch off: " + ", ".join(label for _, label in found)[:100],
                        "You are not at home and this is still running.", {"entities": [e for e, _ in found],
                                                                     "labels": [label for _, label in found]},
                        source="away_check")
    phone_actions.push_proposal(pid, text, title="Jarvis · out")     # yes/no on the phone = the card
    return text


def _welcome(back, away_since):
    import commands
    import events
    import proposals
    commands.update_state(lambda d: d.update(welcomed=back, away_since=None))
    for p in proposals.pending():
        if p["kind"] == "switch_off" and p.get("source") == "away_check":
            proposals.resolve(p["id"], "obsolete")         # the user is home and can switch himself
    text = welcome(events.notices_since(away_since))
    events.publish("say", text, kind="welcome")
    return text
