"""Quick probe of the lean pipeline without the server:
    .venv\\Scripts\\python.exe scripts\\lean_probe.py "Which car do I drive?"
"""
import os
import pathlib
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
os.environ.setdefault("JARVIS_PERM_TOKEN", "probe")
os.environ["JARVIS_PORT"] = "8799"

import commands  # noqa: E402
import router    # noqa: E402
import runtime   # noqa: E402

for msg in sys.argv[1:]:
    r = router.route(msg)
    if r["kind"] == "local":
        print(f"\n> {msg}\n  LOCAL[{r['handler']}] {r['reply']}")
        continue
    system = commands.lean_system(r["groups"])
    prefix = commands.lean_prefix(msg, r["groups"])
    t0 = time.time()
    text, usage, tools = "", {}, []
    for ev in runtime.run_lean(msg, r["model"], r["groups"], system, prefix):
        if ev["t"] == "delta":
            text += ev["text"]
        elif ev["t"] == "usage":
            usage = ev
        elif ev["t"] == "tool" and ev.get("phase") == "use":
            tools.append(ev["name"])
        elif ev["t"] == "error":
            print("  ERROR", ev["message"])
    tot = usage.get("input_tokens", 0) + usage.get("cache_read", 0) + usage.get("cache_write", 0)
    print(f"\n> {msg}\n  {r['model']} {r['groups']} tools={tools} {time.time() - t0:.1f}s\n"
          f"  in={usage.get('input_tokens')} cr={usage.get('cache_read')} cw={usage.get('cache_write')} "
          f"out={usage.get('output_tokens')} calls={usage.get('api_calls')} total_in={tot} "
          f"tool_chars={usage.get('tool_result_chars')}\n  → {text.strip()[:200]}")
runtime.lean_shutdown()
