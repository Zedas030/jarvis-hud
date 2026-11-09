"""
Live check of the room awareness against the real Home Assistant — reads only, switches nothing.

    .venv\\Scripts\\python.exe scripts\\room_probe.py           # once
    .venv\\Scripts\\python.exe scripts\\room_probe.py --watch   # every 3 s, for a walk through the flat

Prints the helper's room, the busy sensors, how sure Jarvis is, the speaker an answer would go to and the
context line the model gets.
"""
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
env = ROOT / ".env"
if env.exists():
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

import paths  # noqa: E402,F401
import room_scope  # noqa: E402
import sonos  # noqa: E402
import whereabouts  # noqa: E402


def once():
    whereabouts.forget()
    room_scope._SENSORS["at"] = 0.0
    cur = room_scope.current()
    speaker = sonos.resolve(sonos.AUTO)
    print(time.strftime("%H:%M:%S"), f"room={cur['room'] or '-'}", f"sure={cur['sure']}",
          f"candidates={cur['candidates']}", f"status={cur['status']}", f"busy={sorted(room_scope._busy() or [])}",
          f"output={sonos.label(speaker) or speaker or 'the device itself'}", flush=True)
    return cur


if __name__ == "__main__":
    if "--watch" in sys.argv:
        while True:
            once()
            time.sleep(3)
    else:
        once()
        print("Devices in the room:", sorted(room_scope.eids(room_scope.current()["room"]))[:12])
        print("Context:", room_scope.line())
