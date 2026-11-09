"""
Creates the three Home Assistant switches of the proactive mode (once; running it again changes nothing):

  input_boolean.jarvis_proactive    "Jarvis Proactive"    on  = Jarvis speaks up by himself
  input_boolean.jarvis_guest_mode   "Jarvis Guest Mode"   on  = guests are here, only critical notices
  input_boolean.jarvis_quiet        "Jarvis Quiet"        on  = quiet; "Jarvis, quiet for an hour" sets it too

Uses HA_URL and HA_TOKEN from .env (the same long-lived token Jarvis uses). The entity ids must match
config/proactive.yaml → ha. Run from the repository root:

    .venv\\Scripts\\python.exe scripts\\proactive_ha_setup.py
"""
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
HELPERS = [("Jarvis Proactive", "mdi:bullhorn", True), ("Jarvis Guest Mode", "mdi:account-group", False),
           ("Jarvis Quiet", "mdi:volume-off", False)]


def env():
    dotenv = pathlib.Path(os.environ.get("JARVIS_ENV_FILE", ROOT / ".env"))
    for line in (dotenv.read_text(encoding="utf-8").splitlines() if dotenv.exists() else []):
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"'))
    return os.environ.get("HA_URL", "").rstrip("/"), os.environ.get("HA_TOKEN", "")


def main():
    from websockets.sync.client import connect
    url, token = env()
    if not url or not token:
        sys.exit("HA_URL / HA_TOKEN missing in .env")
    ws_url = url.replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"
    with connect(ws_url, open_timeout=10) as ws:
        json.loads(ws.recv())
        ws.send(json.dumps({"type": "auth", "access_token": token}))
        if json.loads(ws.recv()).get("type") != "auth_ok":
            sys.exit("Login to Home Assistant failed")
        ws.send(json.dumps({"id": 1, "type": "input_boolean/list"}))
        have = {h["name"] for h in json.loads(ws.recv()).get("result") or []}
        n = 2
        for name, icon, initial in HELPERS:
            if name in have:
                print(f"exists: {name}")
                continue
            ws.send(json.dumps({"id": n, "type": "input_boolean/create", "name": name, "icon": icon}))
            r = json.loads(ws.recv())
            n += 1
            print(("created: " if r.get("success") else "ERROR: ") + name, r.get("error") or "")
            if r.get("success") and initial:
                ws.send(json.dumps({"id": n, "type": "call_service", "domain": "input_boolean", "service": "turn_on",
                                    "target": {"entity_id": "input_boolean." + r["result"]["id"]}}))
                json.loads(ws.recv())
                n += 1


if __name__ == "__main__":
    main()
