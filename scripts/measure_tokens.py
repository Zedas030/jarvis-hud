"""
Token measurement for Jarvis: calls `claude -p` the way the server does and counts per
API call input / cache_read / cache_write / output. Variants switch single parts
off, so their weight can be read off as a difference.

    .venv\\Scripts\\python.exe scripts\\measure_tokens.py components
    .venv\\Scripts\\python.exe scripts\\measure_tokens.py requests
    .venv\\Scripts\\python.exe scripts\\measure_tokens.py components --new   (after the rebuild)

Every run starts a fresh process without --resume (history = 0).
"""
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import paths  # noqa: E402,F401
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
os.environ.setdefault("JARVIS_PERM_TOKEN", "measure")
os.environ["JARVIS_PORT"] = "8799"   # nothing listens: permission requests are simply denied

import runtime   # noqa: E402
import commands  # noqa: E402

OUT = ROOT / "logs" / "token_measure.jsonl"


def run(cmd, label):
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=runtime._env_for_child(), cwd=runtime.WORKDIR, timeout=300,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    calls, seen, tools, result = [], set(), [], {}
    for line in p.stdout.splitlines():
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            init = {"tools": len(ev.get("tools") or []), "mcp": [s.get("name") for s in ev.get("mcp_servers") or []],
                    "model": ev.get("model")}
        if ev.get("type") == "assistant":
            m = ev.get("message") or {}
            for b in m.get("content") or []:
                if b.get("type") == "tool_use":
                    tools.append(b.get("name"))
            if m.get("id") and m["id"] not in seen and m.get("usage"):
                seen.add(m["id"])
                u = m["usage"]
                calls.append({k: u.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                        "cache_creation_input_tokens", "output_tokens")})
        if ev.get("type") == "result":
            result = ev
    u = result.get("usage") or {}
    row = {
        "label": label, "secs": round(time.time() - t0, 1),
        "model": (locals().get("init") or {}).get("model"),
        "n_tools": (locals().get("init") or {}).get("tools"),
        "mcp": (locals().get("init") or {}).get("mcp"),
        "api_calls": len(calls), "tool_uses": tools,
        "input": u.get("input_tokens", 0), "cache_read": u.get("cache_read_input_tokens", 0),
        "cache_write": u.get("cache_creation_input_tokens", 0), "output": u.get("output_tokens", 0),
        "first_call_context": (calls[0]["input_tokens"] + calls[0]["cache_read_input_tokens"]
                               + calls[0]["cache_creation_input_tokens"]) if calls else 0,
        "cost_usd": result.get("total_cost_usd"),
        "reply": (result.get("result") or "")[:120],
        "error": None if result else (p.stderr or p.stdout)[-300:],
    }
    row["total_in"] = row["input"] + row["cache_read"] + row["cache_write"]
    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(row, at=time.strftime("%Y-%m-%d %H:%M:%S")), ensure_ascii=False) + "\n")
    print(f"{label:46s} ctx1={row['first_call_context']:>6} calls={row['api_calls']} "
          f"in={row['input']:>5} cr={row['cache_read']:>6} cw={row['cache_write']:>6} out={row['output']:>4} "
          f"total_in={row['total_in']:>6} tools={len(row['n_tools'] or []) if isinstance(row['n_tools'], list) else row['n_tools']} "
          f"{row['secs']}s {('ERR ' + row['error'][:120]) if row['error'] else ''}", flush=True)
    return row


def current_cmd(message, ha=False):
    """Exactly what the server sends for the first turn of a fresh session."""
    runtime.HA["on"] = ha
    system = commands.system_block(fresh=True)
    prefix = commands.turn_prefix(message)
    return runtime.build_command(prefix + "\n\n" + message, None, system)


def strip(cmd, flag, has_value=True):
    out, skip = [], False
    for i, a in enumerate(cmd):
        if skip:
            skip = False
            continue
        if a == flag:
            skip = has_value
            continue
        out.append(a)
    return out


def components_cde():
    msg = "Just say: ok."
    runtime.HA["on"] = False
    base = current_cmd(msg)
    c = base + ["--strict-mcp-config"]
    run(c, "C without claude.ai connectors")
    d = c + ["--tools", ""]
    run(d, "D + without built-in tools")
    sysp = commands.system_block(fresh=True)
    e = strip(d, "--append-system-prompt") + ["--system-prompt", sysp]
    run(e, "E + own system prompt instead of the default")
    h = strip(e, "--system-prompt") + ["--system-prompt", "Answer briefly in English."]
    run(h, "H mini prompt + only Jarvis tools")


def components():
    msg = "Just say: ok."
    base = current_cmd(msg)
    run(base, "A current (default prompt+all tools+connectors)")
    run(current_cmd(msg, ha=True), "B current + Home Assistant server")
    runtime.HA["on"] = False
    runtime._mcp_config()   # B rewrote the shared config file with Home Assistant on
    c = base + ["--strict-mcp-config"]
    run(c, "C without claude.ai connectors")
    d = c + ["--tools", ""]
    run(d, "D + without built-in tools")
    sysp = commands.system_block(fresh=True)
    e = strip(d, "--append-system-prompt") + ["--system-prompt", sysp]
    run(e, "E + own system prompt instead of the default")
    f = strip(strip(e, "--mcp-config"), "--permission-prompt-tool")
    run(f, "F + without Jarvis MCP tools")
    g = strip(f, "--system-prompt") + ["--system-prompt", "Answer briefly in English."]
    run(g, "G bare (mini prompt, no tools)")
    runtime.HA["on"] = True
    runtime._mcp_config()
    h = strip(strip(e, "--system-prompt") + ["--system-prompt", "Answer briefly in English."], "--allowedTools")
    run(h, "H only mini prompt + Jarvis and HA tools")
    runtime.HA["on"] = False
    runtime._mcp_config()


def requests():
    for label, msg in [
        ("small talk", "What time is it?"),
        ("memory", "Which car do I drive?"),
        ("reading the house", "How much power is the solar system generating right now?"),
        ("weather (web)", "What will the weather be like tomorrow?"),
    ]:
        run(current_cmd(msg), f"current · {label}")


def requests_old_extra():
    for label, msg in [("mail", "Do I have important new mails?"),
                       ("research (web)", "Who won Arsenal's last Premier League match?")]:
        run(current_cmd(msg), f"current · {label}")


def requests_new():
    """Same questions through the new path: router first, else one lean call."""
    import router
    rows = []
    for label, msg in [
        ("small talk", "What time is it?"),
        ("memory", "Which car do I drive?"),
        ("reading the house", "How much power is the solar system generating right now?"),
        ("weather (web)", "What will the weather be like tomorrow?"),
        ("mail", "Do I have important new mails?"),
        ("research (web)", "Who won Arsenal's last Premier League match?"),
    ]:
        r = router.route(msg)
        if r["kind"] == "local":
            print(f"new · {label:40s} LOCAL ({r['handler']}) → 0 tokens", flush=True)
            continue
        system, prefix = commands.lean_system(r["groups"]), commands.lean_prefix(msg, r["groups"])
        usage, t0 = {}, time.time()
        for ev in runtime.run_lean(msg, r["model"], r["groups"], system, prefix):
            if ev["t"] == "usage":
                usage = ev
        tot = usage.get("input_tokens", 0) + usage.get("cache_read", 0) + usage.get("cache_write", 0)
        row = dict(label="new · " + label, model=r["model"], groups=r["groups"], total_in=tot,
                   cache_write=usage.get("cache_write"), output=usage.get("output_tokens"),
                   api_calls=usage.get("api_calls"), secs=round(time.time() - t0, 1))
        with OUT.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(row, at=time.strftime("%Y-%m-%d %H:%M:%S")), ensure_ascii=False) + "\n")
        print(f"new · {label:40s} {r['model']:6s} total_in={tot:>6} cw={usage.get('cache_write'):>5} "
              f"out={usage.get('output_tokens'):>4} calls={usage.get('api_calls')} {row['secs']}s", flush=True)
    runtime.lean_shutdown()


if __name__ == "__main__":
    {"components": components, "cde": components_cde, "requests": requests,
     "old_extra": requests_old_extra, "new": requests_new}[sys.argv[1]]()
