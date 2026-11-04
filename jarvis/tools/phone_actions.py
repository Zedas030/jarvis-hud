"""
The phone as the card: a proposal goes to the phone with Ja/Nein buttons (Home Assistant
companion app), and the answer comes back through Home Assistant — so a proposal can be
confirmed on the way, not only at the HUD.

  push_proposal(pid, text, title)   push with two actions: JARVIS_YES_<id> and JARVIS_NO_<id>
  poll(states)                      every watcher round: reads the helper entity ACTION_ENTITY
                                    (default input_text.jarvis_phone_action). A new value
                                    JARVIS_YES_<id> accepts that proposal exactly like the card
                                    (audit "card", source phone), JARVIS_NO_<id> declines it;
                                    then the helper is emptied again.

Home Assistant needs one helper and one automation (README, "Handy"):
  input_text.jarvis_phone_action
  automation: trigger event mobile_app_notification_action →
              input_text.set_value entity jarvis_phone_action value "{{ trigger.event.data.action }}"

Only proposals Jarvis himself created can be accepted this way; the id in the action must
match an open proposal. Any other value in the helper is ignored and cleared.
"""
import os
import re
import time

ACTION_ENTITY = os.environ.get("JARVIS_PHONE_ACTION_ENTITY", "input_text.jarvis_phone_action").strip()
_ACTION = re.compile(r"^JARVIS_(YES|NO)_([0-9a-f]{6})$")


def push_proposal(pid, text, title="Jarvis"):
    """Push with Ja/Nein; without a phone set up infra says so and nothing else happens."""
    import infra
    actions = [{"action": f"JARVIS_YES_{pid}", "title": "Ja, machen"}, {"action": f"JARVIS_NO_{pid}", "title": "Nein"}]
    return infra.phone_notify(text, title=title, actions=actions)


def _clear():
    import jarvis_mcp as j
    try:
        j._ha(f"/api/services/{ACTION_ENTITY.split('.')[0]}/set_value", {"entity_id": ACTION_ENTITY, "value": ""})
    except Exception:  # noqa: BLE001 — the seen-stamp keeps it from running twice anyway
        pass


def handle_action(value, stamp):
    """Carries out one action value. Returns the German result or None if it was no action."""
    import audit
    import commands
    import events
    import infra
    import proposals
    m = _ACTION.match((value or "").strip())
    if not m:
        return None
    verdict, pid = m.group(1), m.group(2)
    p = proposals.get(pid)
    if not p or p["status"] != "open":
        text = "Dieser Vorschlag ist nicht mehr offen."
    elif verdict == "NO":
        proposals.decline(pid)
        audit.write("card", tool="proposal_decline", allow=False, reason="phone", proposal=pid)
        text = f"Verworfen: {p['title']}."
    else:
        import jarvis_mcp as j
        result = proposals.accept(pid, internal=j._internal)
        audit.write("card", tool="proposal_accept", allow=True, reason="phone", proposal=pid)
        text = f"Vom Handy bestätigt: {p['title']}. {result}"
    commands.update_state(lambda d: d.__setitem__("phone_action_seen", stamp))
    events.publish("say", "Sir, " + text[0].lower() + text[1:], kind="phone")
    infra.phone_notify(text, title="Jarvis")
    return text


def poll(states_list):
    import commands
    s = next((s for s in states_list if s.get("entity_id") == ACTION_ENTITY), None)
    if not s:
        return None
    value = (s.get("state") or "").strip()
    if not value or value in ("unknown", "unavailable"):
        return None
    stamp = f"{value}@{s.get('last_changed', '')}"
    if commands.load().get("phone_action_seen") == stamp:
        return None
    out = handle_action(value, stamp)
    if out is None:                                 # not ours: forget it, but only once
        commands.update_state(lambda d: d.__setitem__("phone_action_seen", stamp))
    _clear()
    return out
