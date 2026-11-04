"""
Focus sessions: "focus 45 minutes" — Jarvis holds his notices, starts the focus playlist if one
is set, mentions a distraction once, and sums the session up at the end. 0 tokens.

  while a session runs
    notices    every `say` is shown, not spoken (events.publish asks hold()); alarms still ring
    music      config/home.json → "focus": {"playlist": "spotify:playlist:…"} starts on Spotify
    distraction  the active window's title is checked every EVERY seconds (desktop.py); time in
               a window matching DISTRACT (YouTube, Netflix, Reddit …) adds up — at WARN_MIN
               minutes one spoken hint, no more
  at the end   "45 minutes of focus, 8 of them on YouTube. Meanwhile: …" with up to three
               held notices (the rest stays in the bell)

state.json → focus: {since, until, held: [text], distract_s, warned, app}
"""
import datetime as dt
import os
import re
import time

import commands
import events

EVERY = 30
WARN_MIN = int(os.environ.get("JARVIS_FOCUS_WARN_MIN", "10"))
DEFAULT_MIN = int(os.environ.get("JARVIS_FOCUS_DEFAULT_MIN", "45"))
DISTRACT = re.compile(os.environ.get("JARVIS_FOCUS_DISTRACT", r"youtube|netflix|twitch|reddit|twitter|\bx\.com|instagram|tiktok|"
                                                              r"prime video|disney\+|9gag|facebook"), re.I)


def state():
    return commands.load().get("focus") or {}


def active(now=None):
    st = state()
    return bool(st.get("until")) and (now or time.time()) < st["until"]


def hold(text):
    """events.publish: a notice during a session is kept for the summary (and not spoken)."""
    if not active():
        return False
    commands.update_state(lambda d: d.setdefault("focus", {}).setdefault("held", []).append(text[:160]))
    return True


def _music():
    import jarvis_profile as profile
    uri = (profile.ha("focus", {}) or {}).get("playlist")
    if not uri:
        return ""
    try:
        import spotify
        return "The focus playlist is playing." if spotify.play_context(uri) else ""
    except Exception:  # noqa: BLE001 — music is a bonus
        return ""


def start(minutes=None, now=None):
    now = now or time.time()
    minutes = max(5, min(int(minutes or DEFAULT_MIN), 240))
    commands.update_state(lambda d: d.__setitem__("focus", dict(since=now, until=now + minutes * 60, held=[],
                                                                 distract_s=0, warned=False, app="")))
    music = _music()
    until = time.strftime("%H:%M", time.localtime(now + minutes * 60))
    return f"Focus until {until}, Sir. I will hold back everything that does not have to ring. {music}".strip()


def summary(st, now=None):
    now = now or time.time()
    mins = max(1, round((min(now, st.get("until", now)) - st.get("since", now)) / 60))
    lost = round(st.get("distract_s", 0) / 60)
    text = f"{mins} minutes of focus" + (f", {lost} of them in {st.get('app') or 'distractions'}" if lost else ", without distraction") + "."
    held = st.get("held") or []
    if held:
        text += " Meanwhile: " + " ".join(h.removeprefix("Sir, ").rstrip(".") + "." for h in held[:3])
        if len(held) > 3:
            text += f" And {len(held) - 3} more notices in the bell."
    return text


def end(now=None):
    st = state()
    if not st.get("until"):
        return "No focus session is running, Sir."
    commands.update_state(lambda d: d.__setitem__("focus", {}))
    return "Focus ended. " + summary(st, now)


def _foreground():
    try:
        import desktop
        return desktop._foreground_title()[1] or ""
    except Exception:  # noqa: BLE001 — not on Windows, or no desktop
        return ""


def step(now=None):
    now = now or time.time()
    st = state()
    if not st.get("until"):
        return
    if now >= st["until"]:
        commands.update_state(lambda d: d.__setitem__("focus", {}))
        events.publish("say", "Sir, the focus time is up. " + summary(st, now), kind="focus", priority="critical")
        return
    title = _foreground()
    m = DISTRACT.search(title)
    if not m:
        return
    app = m.group(0).capitalize()

    def mut(d):
        f = d.setdefault("focus", {})
        f["distract_s"] = f.get("distract_s", 0) + EVERY
        f["app"] = app
        if f["distract_s"] >= WARN_MIN * 60 and not f.get("warned"):
            f["warned"] = True
            return True
        return False
    if commands.update_state(mut):
        events.publish("say", f"Sir, {WARN_MIN} minutes of {app}. Just a reminder — you wanted to stay focused.",
                       kind="focus", priority="critical")


_START = re.compile(r"\b(focus( session| mode| time| phase)?|deep ?work)\b")
_END = re.compile(r"\b(end\w*|off|stop\w*|over|done|finish\w*|cancel)\b")
_HOWLONG = re.compile(r"\bhow (long|much longer|much time)\b")


def handle(n):
    if not _START.search(n):
        return None
    if _HOWLONG.search(n):
        st = state()
        if not active():
            return "No focus session is running, Sir."
        return f"{max(1, round((st['until'] - time.time()) / 60))} more minutes of focus, Sir."
    if _END.search(n):
        return end()
    import scheduler
    mins = scheduler.duration(scheduler.numify(n))
    return start((mins // 60) if mins else None)


def start_loop():
    import background
    background.loop("focus", EVERY, step, pausable=False)
