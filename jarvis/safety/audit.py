"""
Audit log: what Jarvis did, when, and who allowed it.

One JSON line per event in logs/audit.jsonl (JARVIS_AUDIT_LOG):
  tool      a Jarvis tool ran (from jarvis_mcp.py — Claude and the stand-ins alike)
  card      a confirmation card was answered (allowed / denied / timed out)
  local     the local router did something without an LLM ("light off")
  pause     emergency stop / resume

Arguments are shortened and secrets masked (policy.redact); tool results are
not stored, only whether they looked like success. "What did you do today?"
reads this file (today_summary). Lines older than JARVIS_AUDIT_DAYS (90) are
dropped once a day.
"""
import json
import os
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
PATH = pathlib.Path(os.environ.get("JARVIS_AUDIT_LOG", ROOT / "logs" / "audit.jsonl"))
KEEP_DAYS = int(os.environ.get("JARVIS_AUDIT_DAYS", "90"))
_FAIL = ("refused", "error", "not reachable", "not confirmed", "invalid", "does not exist", "not saved", "failed")


def write(kind, **fields):
    row = dict(at=time.strftime("%Y-%m-%d %H:%M:%S"), kind=kind, **fields)
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        with PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return row


def tool(name, args, result, action_class):
    import policy
    head = (result or "").strip().split("\n", 1)[0][:100]
    ok = not any(f in head.lower() for f in _FAIL)
    return write("tool", tool=name, cls=action_class, args=policy.redact(args), ok=ok, result=head if not ok else "")


def read(day=None):
    day = day or time.strftime("%Y-%m-%d")
    rows = []
    try:
        with PATH.open(encoding="utf-8") as fh:
            for line in fh:
                if line.startswith('{"at": "' + day):
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass
    except OSError:
        pass
    return rows


def today_summary(day=None):
    """Plain words for 'What did you do today?': actions first, reading only counted."""
    rows = read(day)
    if not rows:
        return "I have not run anything today yet."
    acts, reads, cards = [], 0, []
    for r in rows:
        if r["kind"] == "tool" and r.get("cls") == "READ":
            reads += 1
        elif r["kind"] == "tool":
            what = ", ".join(f"{v}" for v in r.get("args", {}).values())[:80]
            acts.append(f"{r['at'][11:16]} {r['tool']}" + (f" ({what})" if what else "") + ("" if r.get("ok") else " — failed"))
        elif r["kind"] == "local":
            acts.append(f"{r['at'][11:16]} local: {r.get('message', '')[:60]}")
        elif r["kind"] == "card":
            cards.append(f"{r['at'][11:16]} {r.get('tool')}: {'allowed' if r.get('allow') else 'declined'}")
        elif r["kind"] == "pause":
            acts.append(f"{r['at'][11:16]} {r.get('what')}")
    out = [f"Today {len(acts)} action(s), {reads} times only read."]
    if acts:
        out.append("Actions:\n" + "\n".join(f"- {a}" for a in acts[-25:]))
    if cards:
        out.append("Confirmation cards:\n" + "\n".join(f"- {c}" for c in cards[-10:]))
    return "\n".join(out)


def trim():
    """Drops lines older than KEEP_DAYS."""
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - KEEP_DAYS * 86400))
    try:
        lines = PATH.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return 0
    keep = [ln for ln in lines if ln[9:19] >= cutoff]
    if len(keep) != len(lines):
        PATH.write_text("".join(keep), encoding="utf-8")
    return len(lines) - len(keep)
