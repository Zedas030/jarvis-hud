"""
Background jobs of the learning modules: one small LLM call without tools, and
"run once per day/week" bookkeeping.

ask() uses Claude Haiku through the CLI (subscription, JARVIS_BACKGROUND_MODEL).
It runs as its own short `claude -p` process, outside the turn lock, so a job
never makes the user wait. If Claude is down or at its limit, the free chain
from fallback_llm.py answers — always as a private request (Groq or Ollama,
never Gemini), because these jobs read conversations and health data.

Every call lands in logs/usage.jsonl as route "background:<job>".
"""
import datetime as dt
import json
import os
import pathlib
import subprocess
import threading
import time

import commands

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
MODEL = os.environ.get("JARVIS_BACKGROUND_MODEL", "haiku").strip()
USAGE_LOG = ROOT / "logs" / "usage.jsonl"
TIMEOUT = int(os.environ.get("JARVIS_BACKGROUND_TIMEOUT", "180"))
_ONE_AT_A_TIME = threading.Lock()


def _log(job, model, usage, prompt, reply, ms):
    u = usage or {}
    row = dict(at=time.strftime("%Y-%m-%d %H:%M:%S"), route=f"background:{job}", model=model, groups=[],
               input=u.get("input_tokens", 0), cache_read=u.get("cache_read_input_tokens", 0),
               cache_write=u.get("cache_creation_input_tokens", 0), output=u.get("output_tokens", 0),
               api_calls=1, total_in=u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
               + u.get("cache_creation_input_tokens", 0),
               est=dict(system=0, context=len(prompt) // 3, tool_results=0, reply=len(reply or "") // 3),
               ms=ms, message=f"({job})")
    try:
        USAGE_LOG.parent.mkdir(exist_ok=True)
        with USAGE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _claude(system, prompt, tools=""):
    import runtime
    cmd = runtime._claude_base() + [
        "-p", "--output-format", "json", "--model", MODEL, "--system-prompt", system,
        "--tools", tools, "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence",
        "--disable-slash-commands"]
    if tools:                                   # e.g. WebSearch for watched topics — pre-allowed, nothing else
        cmd += ["--allowedTools", tools]
    p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=runtime._env_for_child(), cwd=runtime.WORKDIR, timeout=TIMEOUT,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        out = json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise RuntimeError((p.stderr or p.stdout or "keine Antwort").strip()[:200]) from None
    if out.get("is_error") or out.get("subtype") != "success":
        raise RuntimeError(str(out.get("result") or out.get("subtype"))[:200])
    return out.get("result") or "", out.get("usage") or {}


def _chain(system, prompt):
    import fallback_llm
    text, usage, who = [], {}, "?"
    for ev in fallback_llm.run(system, "", prompt, [], private=True):
        if ev["t"] == "delta":
            text.append(ev["text"])
        elif ev["t"] == "usage":
            usage = ev
        elif ev["t"] == "provider":
            who = ev["name"]
        elif ev["t"] == "error":
            raise RuntimeError(ev["message"])
    return "".join(text), usage, who


def ask(job, system, prompt, tools=""):
    """One answer as text, or None if no model could answer. tools: Claude built-ins (e.g. "WebSearch");
    jobs that need them get no stand-in model."""
    import fallback_llm
    import runtime
    t0 = time.time()
    with _ONE_AT_A_TIME:
        if runtime.runtime_kind() == "claude" and not fallback_llm.claude_blocked(commands.load().get("usage")):
            try:
                text, usage = _claude(system, prompt, tools)
                _log(job, MODEL, usage, prompt, text, int((time.time() - t0) * 1000))
                return text
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
                print(f"  {job}: Claude nicht verfügbar ({e})" + ("" if tools else ", versuche Ersatz"), flush=True)
        if tools:
            return None
        try:
            text, usage, who = _chain(system, prompt)
        except Exception as e:  # noqa: BLE001
            print(f"  {job}: kein Modell verfügbar ({e})", flush=True)
            return None
        _log(job, f"fallback:{who}", usage, prompt, text, int((time.time() - t0) * 1000))
        return text


def parse_json(text):
    """The first JSON object in a model answer (models like to wrap it in prose or fences)."""
    if not text:
        return None
    start = text.find("{")
    while start >= 0:
        depth = 0
        for i in range(start, len(text)):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except ValueError:
                    break
        start = text.find("{", start + 1)
    return None


def week_key(day=None):
    y, w, _ = (day or dt.date.today()).isocalendar()
    return f"{y}-KW{w:02d}"


def claim(job, key):
    """True exactly once per (job, key) — e.g. once per day or ISO week, across restarts."""
    def mut(d):
        jobs = d.setdefault("jobs", {})
        if jobs.get(job) == key:
            return False
        jobs[job] = key
        return True
    return commands.update_state(mut)


def last(job):
    return (commands.load().get("jobs") or {}).get(job)


def loop(name, every, step, pausable=True):
    """Runs step() every `every` seconds in a daemon thread; one failure never stops the loop.
    pausable: skipped while the emergency stop is active (emergency.py)."""
    def run():
        time.sleep(30)
        while True:
            try:
                import emergency
                if not (pausable and emergency.paused()):
                    step()
            except Exception as e:  # noqa: BLE001
                print(f"  {name}: {e}", flush=True)
            time.sleep(every)
    threading.Thread(target=run, daemon=True, name=name).start()
