"""
Garmin as a trigger, not only as an answer: a bad night or a high stress level changes how
Jarvis behaves for the day — plain thresholds, 0 tokens, nothing is switched.

  morning   from FROM_HOUR, once a day, from the dashboard's Garmin reading (one login, cached):
            sleep score below SLEEP_LOW, stress at or above STRESS_HIGH or Body Battery below
            BATTERY_LOW → the day is "schonend" (gentle) until GENTLE_UNTIL o'clock:
              - the briefings stay short and calm (briefing.py, morning_echo.py get a hint)
              - the model is told so in the turn's context (commands.lean_prefix)
              - suggestions, habits, learning and energy tips are shown, not spoken (events.py)
              - the HUD speaks a little softer (server → voice_factor)
  evening   from EVENING_HOUR: Body Battery below BATTERY_EVENING → one notice: an early night
            would do good, and how the "Gute Nacht" routine helps, if there is one

state.json → wellbeing: {day, mode, reason, sleep_score, stress, battery} — today only.
"""
import datetime as dt
import os
import re

import commands
import events

ENABLED = os.environ.get("JARVIS_WELLBEING", "1").strip() not in ("0", "false", "no", "off")
FROM_HOUR = int(os.environ.get("JARVIS_WB_FROM", "6"))
GENTLE_UNTIL = int(os.environ.get("JARVIS_WB_UNTIL", "12"))
EVENING_HOUR = int(os.environ.get("JARVIS_WB_EVENING", "21"))
SLEEP_LOW = int(os.environ.get("JARVIS_WB_SLEEP_LOW", "60"))
STRESS_HIGH = int(os.environ.get("JARVIS_WB_STRESS_HIGH", "50"))
BATTERY_LOW = int(os.environ.get("JARVIS_WB_BATTERY_LOW", "30"))
BATTERY_EVENING = int(os.environ.get("JARVIS_WB_BATTERY_EVENING", "25"))
VOLUME = float(os.environ.get("JARVIS_WB_VOLUME", "0.75"))
SOFT_KINDS = {"suggestion", "habit", "learning", "energy", "homelab_review"}


def _state():
    return commands.load().get("wellbeing") or {}


def judge(me):
    """(mode, reason) from a Garmin reading {sleep_score, stress, battery}."""
    reasons = []
    if me.get("sleep_score") is not None and me["sleep_score"] < SLEEP_LOW:
        reasons.append(f"Schlaf-Score {me['sleep_score']}")
    if me.get("stress") is not None and me["stress"] >= STRESS_HIGH:
        reasons.append(f"Stress {me['stress']}")
    if me.get("battery") is not None and me["battery"] < BATTERY_LOW:
        reasons.append(f"Body Battery {me['battery']}")
    return ("schonend", ", ".join(reasons)) if reasons else ("normal", "")


def gentle(now=None):
    """True while today is a gentle day and it is still morning."""
    now = now or dt.datetime.now()
    st = _state()
    return st.get("day") == now.date().isoformat() and st.get("mode") == "schonend" and now.hour < GENTLE_UNTIL


def soft(ev, now=None):
    """events.publish: a notice that should be shown, not spoken, on a gentle morning."""
    return ev.get("kind") in SOFT_KINDS and gentle(now)


def volume_factor(now=None):
    return VOLUME if gentle(now) else 1.0


def hint(now=None):
    """One German line for the prompts and briefings on a gentle morning, else ''."""
    if not gentle(now):
        return ""
    return (f"Der User hat eine schlechte Nacht hinter sich ({_state().get('reason', '')}): "
            "antworte kurz und ruhig, keine Pointen, keine Vorschläge, nichts Drängendes.")


def _reading():
    import dashboard
    me = dashboard._cached("garmin", 900, dashboard._garmin)
    return me if isinstance(me, dict) and not me.get("error") else None


def morning(now=None):
    now = now or dt.datetime.now()
    today = now.date().isoformat()
    if not FROM_HOUR <= now.hour < GENTLE_UNTIL or _state().get("day") == today:
        return None
    me = _reading()
    if not me or (me.get("sleep_score") is None and me.get("battery") is None):
        return None
    mode, reason = judge(me)
    commands.update_state(lambda d: d.__setitem__("wellbeing", dict(
        day=today, mode=mode, reason=reason, sleep_score=me.get("sleep_score"), stress=me.get("stress"),
        battery=me.get("battery"), evening=False)))
    return mode


def evening(now=None):
    now = now or dt.datetime.now()
    st = _state()
    if now.hour < EVENING_HOUR or st.get("day") != now.date().isoformat() or st.get("evening"):
        return None
    me = _reading()
    if not me or me.get("battery") is None:
        return None
    commands.update_state(lambda d: d.setdefault("wellbeing", {}).__setitem__("evening", True))
    if me["battery"] >= BATTERY_EVENING:
        return None
    import routines
    night = next((r for r in routines.load() if re.search(r"nacht|schlaf", str(r.get("name", "")), re.I)), None)
    tail = (f" Sagen Sie „{(night.get('triggers') or ['Gute Nacht'])[0]}“, dann übernehme ich den Rest."
            if night else "")
    text = f"Sir, Ihre Body Battery steht bei {me['battery']}. Ein früher Abend täte gut.{tail}"
    events.publish("say", text, kind="wellbeing", key="wellbeing:evening")
    return text


def step(now=None):
    now = now or dt.datetime.now()
    morning(now)
    evening(now)


def describe(now=None):
    now = now or dt.datetime.now()
    st = _state()
    if st.get("day") != now.date().isoformat():
        return "Für heute habe ich noch keine Garmin-Werte gelesen, Sir."
    bits = [f"Schlaf-Score {st['sleep_score']}" if st.get("sleep_score") is not None else "",
            f"Stress {st['stress']}" if st.get("stress") is not None else "",
            f"Body Battery {st['battery']}" if st.get("battery") is not None else ""]
    values = ", ".join(b for b in bits if b)
    if st.get("mode") == "schonend":
        return (f"Heute fahre ich Sie schonend: {st.get('reason')}. Bis {GENTLE_UNTIL} Uhr halte ich mich mit "
                f"Vorschlägen zurück und bleibe leise. Werte: {values}.")
    return f"Heute sieht es gut aus, Sir: {values}. Ganz normaler Betrieb."


_ASK = re.compile(r"\b(wie (geht es|gehts|geht's) mir|wie bin ich (heute )?drauf|schonmodus|schonend|"
                  r"wie war meine nacht|wie (gut )?habe ich geschlafen)\b")


def handle(n):
    return describe() if _ASK.search(n) else None


def start():
    if ENABLED:
        import background
        background.loop("wellbeing", 300, step)
