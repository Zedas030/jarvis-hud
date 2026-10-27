"""
Token-Messung für Jarvis: ruft `claude -p` so auf wie der Server und zählt pro
API-Aufruf input / cache_read / cache_write / output. Varianten schalten
einzelne Bestandteile weg, damit sich ihr Gewicht als Differenz ablesen lässt.

    .venv\\Scripts\\python.exe scripts\\measure_tokens.py components
    .venv\\Scripts\\python.exe scripts\\measure_tokens.py requests
    .venv\\Scripts\\python.exe scripts\\measure_tokens.py components --new   (nach dem Umbau)

Jeder Lauf startet einen frischen Prozess ohne --resume (Verlauf = 0).
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
    msg = "Sag nur: ok."
    runtime.HA["on"] = False
    base = current_cmd(msg)
    c = base + ["--strict-mcp-config"]
    run(c, "C ohne claude.ai-Konnektoren")
    d = c + ["--tools", ""]
    run(d, "D + ohne eingebaute Tools")
    sysp = commands.system_block(fresh=True)
    e = strip(d, "--append-system-prompt") + ["--system-prompt", sysp]
    run(e, "E + eigener Systemprompt statt Standard")
    h = strip(e, "--system-prompt") + ["--system-prompt", "Antworte kurz auf Deutsch."]
    run(h, "H Mini-Prompt + nur Jarvis-Werkzeuge")


def components():
    msg = "Sag nur: ok."
    base = current_cmd(msg)
    run(base, "A aktuell (Standardprompt+alle Tools+Konnektoren)")
    run(current_cmd(msg, ha=True), "B aktuell + Home-Assistant-Server")
    runtime.HA["on"] = False
    runtime._mcp_config()   # B rewrote the shared config file with Home Assistant on
    c = base + ["--strict-mcp-config"]
    run(c, "C ohne claude.ai-Konnektoren")
    d = c + ["--tools", ""]
    run(d, "D + ohne eingebaute Tools")
    sysp = commands.system_block(fresh=True)
    e = strip(d, "--append-system-prompt") + ["--system-prompt", sysp]
    run(e, "E + eigener Systemprompt statt Standard")
    f = strip(strip(e, "--mcp-config"), "--permission-prompt-tool")
    run(f, "F + ohne Jarvis-MCP-Werkzeuge")
    g = strip(f, "--system-prompt") + ["--system-prompt", "Antworte kurz auf Deutsch."]
    run(g, "G nackt (Mini-Prompt, keine Tools)")
    runtime.HA["on"] = True
    runtime._mcp_config()
    h = strip(strip(e, "--system-prompt") + ["--system-prompt", "Antworte kurz auf Deutsch."], "--allowedTools")
    run(h, "H nur Mini-Prompt + Jarvis- und HA-Werkzeuge")
    runtime.HA["on"] = False
    runtime._mcp_config()


def requests():
    for label, msg in [
        ("Smalltalk", "Wie spät ist es?"),
        ("Gedächtnis", "Welches Auto fahre ich?"),
        ("Haus lesen", "Wie viel Strom erzeugt die Solaranlage gerade?"),
        ("Wetter (Web)", "Wie wird das Wetter morgen?"),
    ]:
        run(current_cmd(msg), f"aktuell · {label}")


def requests_old_extra():
    for label, msg in [("Mail", "Hab ich wichtige neue Mails?"),
                       ("Recherche (Web)", "Wer hat das letzte Bundesliga-Spiel von Hertha gewonnen?")]:
        run(current_cmd(msg), f"aktuell · {label}")


def requests_new():
    """Same questions through the new path: router first, else one lean call."""
    import router
    rows = []
    for label, msg in [
        ("Smalltalk", "Wie spät ist es?"),
        ("Gedächtnis", "Welches Auto fahre ich?"),
        ("Haus lesen", "Wie viel Strom erzeugt die Solaranlage gerade?"),
        ("Wetter (Web)", "Wie wird das Wetter morgen?"),
        ("Mail", "Hab ich wichtige neue Mails?"),
        ("Recherche (Web)", "Wer hat das letzte Bundesliga-Spiel von Hertha gewonnen?"),
    ]:
        r = router.route(msg)
        if r["kind"] == "local":
            print(f"neu · {label:40s} LOKAL ({r['handler']}) → 0 Tokens", flush=True)
            continue
        system, prefix = commands.lean_system(r["groups"]), commands.lean_prefix(msg, r["groups"])
        usage, t0 = {}, time.time()
        for ev in runtime.run_lean(msg, r["model"], r["groups"], system, prefix):
            if ev["t"] == "usage":
                usage = ev
        tot = usage.get("input_tokens", 0) + usage.get("cache_read", 0) + usage.get("cache_write", 0)
        row = dict(label="neu · " + label, model=r["model"], groups=r["groups"], total_in=tot,
                   cache_write=usage.get("cache_write"), output=usage.get("output_tokens"),
                   api_calls=usage.get("api_calls"), secs=round(time.time() - t0, 1))
        with OUT.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(row, at=time.strftime("%Y-%m-%d %H:%M:%S")), ensure_ascii=False) + "\n")
        print(f"neu · {label:40s} {r['model']:6s} total_in={tot:>6} cw={usage.get('cache_write'):>5} "
              f"out={usage.get('output_tokens'):>4} calls={usage.get('api_calls')} {row['secs']}s", flush=True)
    runtime.lean_shutdown()


if __name__ == "__main__":
    {"components": components, "cde": components_cde, "requests": requests,
     "old_extra": requests_old_extra, "new": requests_new}[sys.argv[1]]()
