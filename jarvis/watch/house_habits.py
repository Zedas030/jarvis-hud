"""
House habits ("Hunches"): what the user does by hand at the same time of day, learned from the
Home Assistant history (the recorder keeps about ten days) — plain rules, 0 tokens.

  habits      a light or plug switched on or off within ±TOLERANCE minutes of the same time of
              day on at least MIN_DAYS different days ("Licht aus gegen 22:45, an 5 von 10 Tagen")
  routine     habits of one part of the day (morning, evening, night) become one routine
              proposal — "Gute Nacht: Licht aus, TV Schlafzimmer aus" — at most one a week, only
              if no routine of that name exists; it is created only after the card (proposals.py)
  long on     a light that has been on for much longer than it usually is (twice its usual
              longest, at least two hours) is mentioned once, outside the quiet hours

Watched: every light, plus the plugs in config/home.json → away_check → switches; not the
entities in "habits_ignore" (those an automation switches, like the iPad station).
Jarvis never switches anything here.
"""
import datetime as dt
import os
import statistics
import urllib.parse

import background
import commands
import events

ENABLED = os.environ.get("JARVIS_HABITS", "1").strip() not in ("0", "false", "no", "off")
EVERY = int(os.environ.get("JARVIS_HABITS_SECONDS", "900"))
DAYS = 10
MIN_DAYS = int(os.environ.get("JARVIS_HABITS_MIN_DAYS", "4"))
TOLERANCE = 45                 # minutes around the usual time
LONG_MIN_H = 2.0
PHASES = [("Guten Morgen", ["guten morgen", "ich bin wach"], 5, 10),
          ("Feierabend", ["feierabend", "ich bin zu hause"], 16, 21),
          ("Gute Nacht", ["gute nacht", "ich geh schlafen"], 21, 27)]       # 27 = 3 h after midnight
_CACHE = {"day": "", "history": {}}


# ── reading the history ──────────────────────────────────────

def watched(states, cfg):
    ignore = set(cfg.get("habits_ignore") or [])
    plugs = set((cfg.get("away_check") or {}).get("switches") or {})
    return sorted(e for e in states if (e.startswith("light.") or e in plugs) and e not in ignore)


def fetch(entities, now):
    """{entity: [(local datetime, state)]} for the last DAYS days."""
    import briefing
    if not (briefing.HA_TOKEN and entities):
        return {}
    start = (now - dt.timedelta(days=DAYS)).astimezone().isoformat()
    q = urllib.parse.urlencode({"filter_entity_id": ",".join(entities), "end_time": now.astimezone().isoformat()})
    d = briefing._get(f"{briefing.HA_URL}/api/history/period/{urllib.parse.quote(start)}?{q}"
                      "&minimal_response&no_attributes", {"Authorization": f"Bearer {briefing.HA_TOKEN}"}, timeout=30)
    out = {}
    for rows in d or []:
        if not rows:
            continue
        eid = rows[0].get("entity_id")
        out[eid] = [(dt.datetime.fromisoformat(r["last_changed"]).astimezone().replace(tzinfo=None), r["state"])
                    for r in rows if r.get("last_changed")]
    return out


def switches(rows):
    """[(time, 'on'|'off')] of real changes — a plug coming back from 'unavailable' is no action."""
    out, prev = [], None
    for t, s in rows:
        if s not in ("on", "off"):
            continue                                   # unavailable/unknown: keep the last real state
        if prev is not None and s != prev:
            out.append((t, s))
        prev = s
    return out


# ── habits ───────────────────────────────────────────────────

def _minute(t):
    return t.hour * 60 + t.minute


def _dist(a, b):
    d = abs(a - b) % 1440
    return min(d, 1440 - d)


def habits(history):
    """[{entity, state, at: 'HH:MM', minute, days}] — one per entity and state, the strongest time."""
    out = []
    for eid, rows in history.items():
        for state in ("on", "off"):
            times = [t for t, s in switches(rows) if s == state]
            best = None
            for t in times:
                near = [u for u in times if _dist(_minute(u), _minute(t)) <= TOLERANCE]
                days = len({u.date() for u in near})
                if days >= MIN_DAYS and (not best or days > best[1]):
                    mins = sorted((_minute(u) - _minute(t) + 720) % 1440 - 720 for u in near)
                    center = (_minute(t) + int(statistics.median(mins))) % 1440
                    best = (center, days)
            if best:
                out.append(dict(entity=eid, state=state, minute=best[0], days=best[1],
                                at=f"{best[0] // 60:02d}:{best[0] % 60:02d}"))
    return out


def _phase(minute):
    h = minute / 60
    for name, triggers, h0, h1 in PHASES:
        if h0 <= h < h1 or h0 <= h + 24 < h1:
            return name, triggers
    return None, None


def routine_proposal(found, names, existing):
    """(payload, why) for the first part of the day that has habits and no routine yet, or None."""
    have = {r.get("name", "").strip().lower() for r in existing} | \
        {t.lower() for r in existing for t in r.get("triggers") or []}
    by_phase = {}
    for h in found:
        name, triggers = _phase(h["minute"])
        if name:
            by_phase.setdefault(name, (triggers, []))[1].append(h)
    for name, _t, _h0, _h1 in PHASES[::-1]:                 # the night first: the classic
        if name not in by_phase or name.lower() in have or any(t in have for t in by_phase[name][0]):
            continue
        triggers, hs = by_phase[name]
        steps = [{"ha": f"{h['entity'].split('.')[0]}.turn_{h['state']}", "entity_id": h["entity"]} for h in hs]
        why = "; ".join(f"{names.get(h['entity'], h['entity'])} {'an' if h['state'] == 'on' else 'aus'} "
                        f"gegen {h['at']} (an {h['days']} von {DAYS} Tagen)" for h in hs)
        return {"name": name, "triggers": triggers, "steps": steps}, why
    return None


# ── long on ──────────────────────────────────────────────────

def usual_hours(rows):
    """The usual longest on-time in hours (90th percentile), or None with fewer than four."""
    durs, start = [], None
    for t, s in switches(rows):
        if s == "on":
            start = t
        elif start:
            durs.append((t - start).total_seconds() / 3600)
            start = None
    if len(durs) < 4:
        return None
    durs.sort()
    return durs[min(len(durs) - 1, int(len(durs) * 0.9))]


def long_on(state, usual, now):
    """Hours on, if that is unusually long now; else None."""
    if not state or state.get("state") != "on" or usual is None:
        return None
    try:
        since = dt.datetime.fromisoformat(state["last_changed"]).astimezone().replace(tzinfo=None)
    except (KeyError, ValueError):
        return None
    hours = (now - since).total_seconds() / 3600
    return hours if hours >= max(LONG_MIN_H, 2 * usual) else None


# ── background ───────────────────────────────────────────────

def _names(states):
    return {e: ((s.get("attributes") or {}).get("friendly_name") or e).strip() for e, s in states.items()}


def step(now=None):
    import briefing
    import jarvis_profile as profile
    now = now or dt.datetime.now()
    states = {s["entity_id"]: s for s in briefing._ha_states()}
    if not states:
        return
    cfg = {"habits_ignore": profile.ha("habits_ignore", []), "away_check": profile.ha("away_check", {})}
    today = now.date().isoformat()
    if _CACHE["day"] != today and now.hour >= 10:
        _CACHE.update(day=today, history=fetch(watched(states, cfg), now))
    names = _names(states)
    if _CACHE["history"] and now.weekday() == 5 and now.hour >= 11 and not events.quiet_now() \
            and background.claim("habits-routine", background.week_key(now.date())):
        _propose(habits(_CACHE["history"]), names)
    if events.quiet_now():
        return
    for eid, rows in _CACHE["history"].items():
        if not eid.startswith("light."):
            continue
        hours = long_on(states.get(eid), usual_hours(rows), now)
        if hours:
            key = f"longon:{eid}:{states[eid].get('last_changed', '')}"
            if commands.update_state(lambda d, k=key: d.setdefault("habit_notes", {}).get(k) is None
                                     and not d["habit_notes"].update({k: today})):
                events.publish("say", f"Sir, {names.get(eid, eid)} ist seit {round(hours)} Stunden an — "
                                      f"sonst selten länger als {max(1, round(usual_hours(rows)))}.", kind="habit")
    commands.update_state(lambda d: d.update(habit_notes={k: v for k, v in (d.get("habit_notes") or {}).items()
                                                          if v >= (now.date() - dt.timedelta(days=3)).isoformat()}))


def _propose(found, names):
    import proposals
    import routines
    hit = routine_proposal(found, names, routines.load())
    if not hit:
        return None
    payload, why = hit
    proposals.add("routine", f"Routine „{payload['name']}“", "Gewohnheit im Haus: " + why, payload, source="house_habits")
    events.publish("say", f"Sir, mir ist etwas aufgefallen: {why}. Soll ich daraus die Routine „{payload['name']}“ machen? "
                          "Sag einfach ja.", kind="habit")
    return payload


def start():
    if ENABLED:
        background.loop("house_habits", EVERY, step)
