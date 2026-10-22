"""
Emergency stop: "Jarvis, stopp alles" / "Not-Aus".

  - answers every open confirmation card with "no"
  - stops a running Claude process
  - pauses for JARVIS_PAUSE_HOURS (default 2): proactive messages, the
    background jobs of the learning modules, the homelab observer and planned
    commands ("Licht in einer Stunde aus" is dropped while paused)

Alarms, timers and reminders still ring — they are what the user asked for.
"Jarvis, weitermachen" / "Not-Aus aufheben" ends the pause early. Handled by
the local router: no LLM, works even when Claude is down.
"""
import os
import re
import time

import commands

HOURS = float(os.environ.get("JARVIS_PAUSE_HOURS", "2"))
STOP = re.compile(r"^\W*(jarvis\W+)?(not[- ]?aus|stopp? alles|alles stopp(en)?|halt alles an|sofort aufhören)\W*$")
RESUME = re.compile(r"^\W*(jarvis\W+)?(not[- ]?aus (aufheben|beenden|aus)|weitermachen|mach weiter|pause (aufheben|beenden|vorbei))\W*$")


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
    audit.write("pause", what=f"Not-Aus für {hours:g} h")
    return until


def resume():
    commands.update_state(lambda d: d.pop("paused_until", None))
    import audit
    audit.write("pause", what="Not-Aus aufgehoben")


def handle(n, deny_cards=None, cancel=None):
    """Local router hook: reply text or None."""
    if STOP.search(n):
        denied = deny_cards() if deny_cards else 0
        until = pause(cancel=cancel)
        return (f"Not-Aus, Sir. Alles angehalten" + (f", {denied} offene Bestätigung(en) abgelehnt" if denied else "")
                + f". Bis {time.strftime('%H:%M', time.localtime(until))} melde ich mich nicht von selbst und "
                "führe nichts Geplantes aus. Wecker und Erinnerungen klingeln weiter. "
                "Sagen Sie „weitermachen“, wenn es vorbei ist.")
    if RESUME.search(n):
        if not paused():
            return "Es war nichts angehalten, Sir."
        resume()
        return "Weiter geht's, Sir. Meldungen und geplante Befehle laufen wieder."
    return None
