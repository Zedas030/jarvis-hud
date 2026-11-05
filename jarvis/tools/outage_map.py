"""
Outage map — asks Blast-Radius (dependency map on the Pi, :8098) "what fails if X fails?"; read-only, 0 tokens.

    ask(question)       raw answer of GET /api/ask (matched node, failed/degraded functions)
    spoken(answer)      English sentence for the voice output
    describe(question)  text for the LLM tool `outage_impact`

Blast-Radius labels are English; its optional `say:` names (overrides.yaml) may be in another language, so the
English edition prefers the label. BLAST_RADIUS_URL in .env, default http://<JARVIS_PI_HOST>:8098.
"""
import json
import os
import urllib.parse
import urllib.request

_PI = os.environ.get("JARVIS_PI_HOST", "").strip()
URL = os.environ.get("BLAST_RADIUS_URL", "").strip().rstrip("/") or (f"http://{_PI}:8098" if _PI else "")
TIMEOUT = float(os.environ.get("BLAST_RADIUS_TIMEOUT", "4"))
MAX_NAMES = 6        # read aloud: more names than this are counted, not listed


def ask(question):
    if not URL:
        raise RuntimeError("Blast-Radius is not set up (BLAST_RADIUS_URL or JARVIS_PI_HOST in .env)")
    with urllib.request.urlopen(f"{URL}/api/ask?{urllib.parse.urlencode({'q': question})}", timeout=TIMEOUT) as r:
        return json.loads(r.read())


def _name(item):
    return (item.get("label") or item.get("say") or "").strip()


def _join(names):
    names = [n for n in names if n]
    if len(names) > MAX_NAMES:
        rest = len(names) - MAX_NAMES
        return ", ".join(names[:MAX_NAMES]) + f" and {rest} more"
    if len(names) > 1:
        return ", ".join(names[:-1]) + " and " + names[-1]
    return names[0] if names else ""


def spoken(answer):
    """English sentence, or None when Blast-Radius did not know which device was meant."""
    m = answer.get("matched")
    if not m:
        return None
    who = _name(m)
    fn = answer.get("functions") or {}
    failed, degraded = fn.get("failed") or [], fn.get("degraded") or []
    total = fn.get("total") or 0
    if failed:
        s = f"If {who} fails, {len(failed)} of {total} functions stop working: {_join([_name(f) for f in failed])}."
    else:
        s = f"If {who} fails, no function stops working completely."
    if degraded:
        s += f" Degraded: {_join([_name(f) for f in degraded])}."
    return s


def describe(question):
    a = ask(question)
    if not a.get("matched"):
        return "Blast-Radius found no matching device or service. " \
               "Ask with the name, e.g. Pi, FRITZ!Box, Zigbee coordinator, NAS."
    lines = [spoken(a), f"(node: {a['matched'].get('label')}; technical: {a.get('summary', '')})"]
    alts = [x.get("label") for x in a.get("alternatives") or []][:3]
    if alts:
        lines.append("Other matches: " + ", ".join(alts))
    return "\n".join(lines)
