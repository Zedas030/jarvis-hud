"""
Before leaving: the calendar says where the user has to be, commute.py says when to leave —
PREP_MIN minutes before that, Jarvis says what still runs at home and prepares the
"switch off" proposal, so one "switch off" (and the card) does it. Nothing is switched here.

  check(events, states, now)   called by watchers.calendar with the cached appointments; once per
                               appointment (watchers.once). Lights, media and the plugs from
                               config/home.json → away_check (away_check.still_on), plus the
                               thermostats in config/home.json → "departure":
                                 {"climate": ["climate.living_room"], "climate_away": 17}
                               which the proposal sets to climate_away degrees; climate_away 0
                               switches them off instead (hvac_mode off).
  With a phone set up the notice goes there too, with yes/no buttons (phone_actions.py).
"""
import datetime as dt
import os

PREP_MIN = int(os.environ.get("JARVIS_DEPARTURE_PREP_MIN", "30"))


def _cfg():
    import jarvis_profile as profile
    return profile.ha("departure", {}) or {}


def plan(states, cfg_away, cfg_dep):
    """(found [(entity, label)], climate {entity: temp}) — what the proposal would do."""
    import away_check
    found = away_check.still_on(states, cfg_away)
    away_t = cfg_dep.get("climate_away")
    climate = {}
    if away_t is not None:
        for eid in cfg_dep.get("climate") or []:
            s = states.get(eid) or {}
            try:
                current = float((s.get("attributes") or {}).get("temperature"))
            except (TypeError, ValueError):
                current = None
            if not s:
                continue
            if float(away_t) <= 0:                      # 0 = switch off: only thermostats that still heat
                if s.get("state") not in ("off", "unavailable", "unknown"):
                    climate[eid] = 0.0
            elif current is None or current > float(away_t):
                climate[eid] = float(away_t)
    return found, climate


def climate_label(name, temp):
    return f"{name} off" if temp <= 0 else f"{name} to {temp:g} degrees"


def message(event, leave_min, drive, found, climate, names):
    title = event.get("title", "your appointment")
    head = (f"Sir, in about {max(1, round(leave_min))} minutes you should leave for {title}, about {drive} minutes by car."
            if leave_min > 1 else f"Sir, time to leave for {title}, about {drive} minutes by car.")
    bits = [label for _, label in found] + [climate_label(names.get(e, e), t) for e, t in climate.items()]
    if not bits:
        return head + " Everything at home is off."
    return head + " Still running at home: " + ", ".join(bits) + ". Say “switch off” and the card comes up."


def check(events_list, states_list, now, once):
    """once(key, text, **extra) is watchers.once. Returns the notice text or None."""
    import jarvis_profile as profile
    import proposals
    states = {s["entity_id"]: s for s in states_list}
    cfg_dep = _cfg()
    for e in events_list:
        if e.get("allday") or not e.get("location") or e["start"] <= now:
            continue
        import watchers
        drive = watchers._drive(e)
        if drive is None:
            continue
        leave_min = (e["start"] - now).total_seconds() / 60 - drive - watchers.commute_buffer()
        if not (0 < leave_min <= PREP_MIN):
            continue
        key = f"prep:{e.get('title')}:{e['start']:%Y%m%d%H%M}"
        found, climate = plan(states, profile.ha("away_check", {}) or {}, cfg_dep)
        names = {eid: ((s.get("attributes") or {}).get("friendly_name") or eid) for eid, s in states.items()}
        text = message(e, leave_min, drive, found, climate, names)
        if not once(key, text, kind="calendar"):
            continue
        if found or climate:
            labels = [label for _, label in found] + [climate_label(names.get(x, x), t) for x, t in climate.items()]
            pid = proposals.add("switch_off", "Switch off before leaving: " + ", ".join(labels)[:90],
                                f"You are about to leave for {e.get('title', 'an appointment')} and this is still running.",
                                {"entities": [x for x, _ in found], "labels": labels, "climate": climate},
                                source="departure")
            try:
                import phone_actions
                phone_actions.push_proposal(pid, text, title="Jarvis · leaving soon")
            except Exception:  # noqa: BLE001 — the phone is a bonus
                pass
        return text
    return None
