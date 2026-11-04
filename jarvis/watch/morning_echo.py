"""
Morning briefing in the living room: the first time in the morning something there goes on (the LED,
the TV, or someone talks to the Echo), the Echo says once what matters today. Fixed text, 0 tokens.

  Weather and DWD warnings, today's appointments, the energy coach's tip, what Jarvis reported
  in the night, unread mail. It only speaks — nothing is switched.

The briefing counts as the day's briefing (state.json → briefing_day), so the first conversation
does not repeat it. config/home.json → "morning_briefing":
  {"room": "Living Room", "triggers": ["binary_sensor.bathroom_presence", "light.light", "switch.tv",
                                       "media_player.user_echo_dot"]}
  The briefing is spoken in the room the presence sensors see the user in (whereabouts.py); "room" is the
  fallback when nobody is seen. A presence sensor as trigger: the first visit to the bathroom in the morning.
A media_player counts when it was talked to (Alexa Media Player's last_called_timestamp).
"""
import datetime as dt
import os
import re
import threading
import time

import commands

ENABLED = os.environ.get("JARVIS_MORNING_ECHO", "1").strip().lower() not in ("0", "false", "no", "off")
FROM_HOUR = int(os.environ.get("JARVIS_MORNING_FROM", "5"))
UNTIL_HOUR = int(os.environ.get("JARVIS_BRIEFING_UNTIL", "13"))
ECHO_WAIT_S = 8              # after "Alexa, ..." Alexa answers first
_LAST = {}                   # entity -> what it was in the previous round ("on", a timestamp)


def config():
    import jarvis_profile as profile
    cfg = profile.ha("morning_briefing", {}) or {}
    return {"room": cfg.get("room", "Living Room"), "triggers": list(cfg.get("triggers") or [])}


def _mark(s):
    if s["entity_id"].startswith("media_player."):
        return str((s.get("attributes") or {}).get("last_called_timestamp") or "")
    return s.get("state")


def triggered(states, triggers, last):
    """The trigger that fired since the last round, or None; updates `last`. The first round only looks."""
    by = {s["entity_id"]: s for s in states}
    fired = None
    for eid in triggers:
        s = by.get(eid)
        if not s:
            continue
        now, before = _mark(s), last.get(eid)
        last[eid] = now
        if before is None:
            continue
        if eid.startswith("media_player."):
            if now and now != before:
                fired = fired or eid
        elif now == "on" and before == "off":
            fired = fired or eid
    return fired


# ── what is said ─────────────────────────────────────────────
def _weather():
    import briefing
    text = briefing._weather().split(": ", 1)[-1]
    text = re.sub(r"\s*\([^)]*\)", "", text).replace("°C", "degrees")
    return "Today " + text


def _alerts():
    import weather_alerts
    alerts = weather_alerts.fetch()
    return ("Note: " + " ".join(weather_alerts.say(a) for a in alerts[:2])) if alerts else ""


def _agenda():
    import icloud_calendar
    if not icloud_calendar.configured():
        return ""
    evts = [e for e in icloud_calendar.events(dt.date.today(), 1) if e["start"].date() == dt.date.today()]
    # expected deliveries are all-day entries "📦 Amazon: ... · DHL" (shipments.py)
    parcels = [e for e in evts if e.get("title", "").startswith("📦")]
    evts = [e for e in evts if e not in parcels]
    out = []
    if parcels:
        senders = [e["title"].lstrip("📦 ").split(":", 1)[0].strip() for e in parcels]
        out.append(f"Today {'a parcel arrives' if len(parcels) == 1 else f'{len(parcels)} parcels arrive'} "
                   f"from {' and '.join(dict.fromkeys(senders))}.")
    if not evts:
        return " ".join(out) or "There are no appointments today."
    parts = [("all day " if e.get("allday") else f"at {e['start'].hour}:{e['start'].minute:02d} ")
             + e.get("title", "an appointment") for e in evts[:3]]
    more = f", and {len(evts) - 3} more" if len(evts) > 3 else ""
    return " ".join(out + ["Appointments: " + ", ".join(parts) + more + "."])


def _coach():
    import energy_coach
    return energy_coach.morning_line().replace("Energy coach: today", "The sun brings today")


def _night(now):
    import events
    since = (now - dt.timedelta(days=1)).replace(hour=22, minute=0, second=0).timestamp()
    rows = [r for r in events.notices_since(since) if r.get("kind") != "alarm"]
    if not rows:
        return ""
    first = rows[-1]["text"].removeprefix("Sir, ")
    return (f"There were {len(rows)} notices during the night, the last: {first}" if len(rows) > 1
            else f"During the night: {first}")


def _mail():
    import briefing
    m = re.search(r"(\d+) unread", briefing._mail_count() or "")
    return f"You have {m.group(1)} unread mails." if m and m.group(1) != "0" else ""


def text(now=None):
    now = now or dt.datetime.now()
    import jarvis_profile as profile
    name = getattr(profile, "USER_NAME", "") or ""
    out = [f"Good morning, {name or 'Sir'}."]
    parts = (_weather, _alerts, _agenda, _coach, lambda: _night(now), _mail)
    try:
        import wellbeing
        if wellbeing.gentle(now):              # a bad night: only the weather and the day, said quietly
            parts = (_weather, _alerts, _agenda)
    except Exception:  # noqa: BLE001
        pass
    for part in parts:
        try:
            line = part()
        except Exception:  # noqa: BLE001 — a dead source must not cost the rest
            line = ""
        if line:
            out.append(line.rstrip(".") + ".")
    return " ".join(out)


# ── background (watchers.py, every round) ────────────────────
def _speak(room, words):
    import alexa_voice
    import speakers
    try:
        import whereabouts
        room = whereabouts.room() or room          # the presence sensors know where he is; else the configured room
    except Exception:  # noqa: BLE001
        pass
    sp = (speakers._cfg()[0] or {}).get(room) or {}
    if alexa_voice.is_alexa(sp.get("notify")):
        alexa_voice.say(sp["notify"], words)
    else:
        speakers.announce(room, words)


def check(states, now=None):
    if not ENABLED:
        return
    cfg = config()
    fired = triggered(states, cfg["triggers"], _LAST)
    now = now or dt.datetime.now()
    if not fired or not FROM_HOUR <= now.hour < UNTIL_HOUR:
        return
    today = now.strftime("%Y-%m-%d")
    if not commands.update_state(lambda d: d.get("briefing_day") != today and not d.update(briefing_day=today)):
        return                     # already briefed today, here or in the first conversation

    def run():
        words = text(now)
        print(f"  morning briefing ({fired}): {words[:120]}", flush=True)
        _speak(cfg["room"], words)
    wait = ECHO_WAIT_S if fired.startswith("media_player.") else 0
    t = threading.Timer(wait, run)
    t.daemon = True
    t.start()
