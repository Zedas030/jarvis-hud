"""
Push channel from the server to the HUD (Server-Sent Events on /api/events).

Timers, reminders, the wake word and the watchers publish here; the HUD keeps
one EventSource open and speaks what arrives. Nothing here costs tokens.

Event kinds:
  say     something Jarvis says by himself ("Die Waschmaschine ist fertig")
  alarm   a timer, alarm clock or reminder — rings, then speaks
  wake    the wake word was heard (wakeword.py)
  timers  the current list of timers, for the countdown in the top bar

Quiet hours (JARVIS_QUIET_HOURS, e.g. 22-7): `say` events are shown but not
spoken. Alarms always ring — that is what they are for.

Events published while no HUD is connected wait a while and are delivered on
the next connect, so an alarm does not vanish because the tab was reloading.
"""
import datetime as dt
import itertools
import os
import threading
import time

QUIET_HOURS = os.environ.get("JARVIS_QUIET_HOURS", "22-7").strip()
KEEP_S = 1800                       # how long an undelivered event waits for a HUD
FRESH_S = {"wake": 3, "timers": 0}  # these are only worth something right away

_COND = threading.Condition()
_LOG = []
_SEQ = itertools.count(1)
CLIENTS = [0]


def quiet_now(now=None):
    """True inside the quiet hours. '22-7' wraps over midnight, '' or 'off' = never."""
    spec = QUIET_HOURS.lower()
    if not spec or spec in ("0", "off", "aus", "none"):
        return False
    try:
        start, end = (int(x) for x in spec.split("-", 1))
    except ValueError:
        return False
    hour = (now or dt.datetime.now()).hour
    if start == end:
        return False
    return start <= hour < end if start < end else hour >= start or hour < end


def publish(etype, text="", priority="normal", **extra):
    """etype: say | alarm | wake | timers. Extra fields (kind, items, ...) travel along."""
    if etype == "say" and priority != "alarm":
        try:
            import emergency
            if emergency.paused():
                return None                 # emergency stop: Jarvis stays quiet (alarms still ring)
        except Exception:  # noqa: BLE001
            pass
    ev = dict(extra, t=etype, text=text, priority=priority, at=time.time())
    if etype == "say" and priority != "alarm" and quiet_now():
        ev["silent"] = True
    with _COND:
        ev["id"] = next(_SEQ)
        _LOG.append(ev)
        cutoff = time.time() - KEEP_S
        del _LOG[:max(0, len(_LOG) - 200)]
        while _LOG and _LOG[0]["at"] < cutoff:
            _LOG.pop(0)
        _COND.notify_all()
    return ev


def _pending(last_id, fresh_connect):
    now = time.time()
    out = []
    for ev in _LOG:
        if ev["id"] <= last_id:
            continue
        if fresh_connect:
            # A new page gets what nobody has heard yet — but no stale wake-ups
            # and no old timer lists (it reads the current one from /api/status).
            if ev.get("delivered") or now - ev["at"] > FRESH_S.get(ev["t"], KEEP_S):
                continue
        out.append(ev)
    return out


def wait(last_id, timeout=15.0):
    """Events after last_id; blocks up to `timeout` seconds when there are none."""
    with _COND:
        evs = _pending(last_id, False)
        if not evs:
            _COND.wait(timeout)
            evs = _pending(last_id, False)
        for ev in evs:
            ev["delivered"] = True
        return [dict(ev) for ev in evs]


def connect():
    """For a new page: (what it should still hear, cursor to continue from)."""
    with _COND:
        evs = _pending(0, True)
        for ev in evs:
            ev["delivered"] = True
        return [dict(ev) for ev in evs], (_LOG[-1]["id"] if _LOG else 0)
