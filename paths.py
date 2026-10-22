"""
Where the code lives. The modules are sorted into folders under jarvis/ by topic,
but keep their short import names (import memory, import router): importing this
module puts every jarvis/<group> folder on sys.path. Entry points (server.py,
jarvis_mcp.py as its own process, tests/isolate.py, scripts/) import it first.

  jarvis/core     request path: router, runtime (Claude), commands (prompts), events, scheduler,
                  background jobs, profile, personality, context engine, task engine
  jarvis/safety   action classes, privacy, audit log, emergency stop, proposals
  jarvis/tools    MCP tool server and the integrations behind it (Windows, PC/network, mail and
                  calendar helpers, lists, routines, devices, dashboard data)
  jarvis/memory   vault, memory tools and intelligence, learning, skill factory, reflection,
                  answer cache, document search
  jarvis/voice    speech in and out, wake word, pronunciation, vision
  jarvis/llm      stand-in model chain, GPU/RAM resource manager
  jarvis/watch    proactive watchers, homelab observer, forecasts, briefing, weekly review, suggestions
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
GROUPS = ("core", "safety", "tools", "memory", "voice", "llm", "watch")
for _g in reversed(GROUPS):
    _p = os.path.join(ROOT, "jarvis", _g)
    if _p not in sys.path:
        sys.path.insert(0, _p)
