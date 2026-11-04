"""
Emergency stop: "Jarvis, stop everything" / "emergency stop".

  - answers every open confirmation card with "no"
  - stops a running Claude process
  - pauses for JARVIS_PAUSE_HOURS (default 2): proactive messages, the
    background jobs of the learning modules, the homelab observer and planned
    commands ("light off in an hour" is dropped while paused)

Alarms, timers and reminders still ring — they are what the user asked for.
"Jarvis, carry on" / "cancel the emergency stop" ends the pause early. Handled by
the local router: no LLM, works even when Claude is down.
"""
import os
import re
import time

import commands

HOURS = float(os.environ.get("JARVIS_PAUSE_HOURS", "2"))
STOP = re.compile(r"^\W*(jarvis\W+)?(emergency stop|stop everything|stop it all|halt everything|stop all of it)\W*$")
RESUME = re.compile(r"^\W*(jarvis\W+)?((cancel|end|lift) (the )?emergency stop|carry on|keep going|(end|lift) (the )?pause|pause (is )?over)\W*$")


def paused(now=None):
    until = commands.load().get("paused_until") or 0
    return until > (now or time.time())


def pause(hours=HOURS, cancel=None):
    """Sets the pause and denies open cards. cancel() stops a running LLM process (server)."""
    until = time.time() + hours * 3600
    commands.update_state(lambda d: d.__setitem__("paused_until", until))
    if cancel:
        try:
            cancel()
        except Exception:  # noqa: BLE001
            pass
    import audit
    audit.write("pause", what=f"emergency stop for {hours:g} h")
    return until


def resume():
    commands.update_state(lambda d: d.pop("paused_until", None))
    import audit
    audit.write("pause", what="emergency stop lifted")


def handle(n, deny_cards=None, cancel=None):
    """Local router hook: reply text or None."""
    if STOP.search(n):
        denied = deny_cards() if deny_cards else 0
        until = pause(cancel=cancel)
        return (f"Emergency stop, Sir. Everything halted" + (f", {denied} open confirmation(s) declined" if denied else "")
                + f". Until {time.strftime('%H:%M', time.localtime(until))} I will not speak up on my own and "
                "will not run anything scheduled. Alarms and reminders still ring. "
                "Say “carry on” when it is over.")
    if RESUME.search(n):
        if not paused():
            return "Nothing was halted, Sir."
        resume()
        return "Carrying on, Sir. Notices and scheduled commands are running again."
    return None
