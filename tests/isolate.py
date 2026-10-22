"""
Import this first in every test file: state, vault, lists, routines and the
learned files go to a temp folder, Home Assistant is switched off.

Modules read their paths once at import. If one of them was already imported
with the real paths (another test file imported it first), the tests would
write into the real state.json and vault — so that is refused outright.
"""
import os
import pathlib
import sys
import tempfile

_GUARDED = ("commands", "memory", "lists", "routines", "learning", "pronounce", "pc", "scheduler", "server",
            "proposals", "weekly_review", "homelab_watch", "suggestions", "reflection", "audit", "memory_tools",
            "emergency", "personality", "desktop", "infra", "assistant")
_early = [m for m in _GUARDED if m in sys.modules and "isolate" not in sys.modules]
if _early:
    raise RuntimeError(f"tests/isolate.py must be imported before {', '.join(_early)} — real data at risk")

TMP = pathlib.Path(tempfile.mkdtemp(prefix="jarvis-test-"))
os.environ.update(JARVIS_STATE=str(TMP / "state.json"), JARVIS_VAULT=str(TMP / "vault"),
                  JARVIS_ROUTINES=str(TMP / "routines.json"), HA_TOKEN="", JARVIS_SHOPPING_LIST="",
                  JARVIS_TODO_LIST="", JARVIS_LOCAL_ROUTER="1", JARVIS_QUIET_HOURS="22-7",
                  JARVIS_LEARNED=str(TMP / "learned.json"), JARVIS_PRONOUNCE=str(TMP / "aussprache.json"),
                  JARVIS_APPS=str(TMP / "apps.json"), JARVIS_PROPOSALS=str(TMP / "proposals.json"),
                  JARVIS_HEALTH_DATA=str(TMP / "health.json"), JARVIS_HOMELAB_CONFIG=str(TMP / "homelab.json"),
                  JARVIS_HOMELAB_BASELINE=str(TMP / "baseline.json"), JARVIS_WORK_TERMS="Contoso",
                  JARVIS_AUDIT_LOG=str(TMP / "audit.jsonl"), JARVIS_BACKUP_DIR=str(TMP / "backups"),
                  JARVIS_PROFILES=str(TMP / "profiles.json"), JARVIS_WORKSPACES=str(TMP / "workspaces.json"),
                  JARVIS_SCRIPTS=str(TMP / "scripts.json"), JARVIS_FILE_ROOTS=str(TMP / "files"),
                  JARVIS_PERM_URL="http://127.0.0.1:9/api/permission/request",
                  JARVIS_HOMELAB_HISTORY=str(TMP / "homelab_events.jsonl"), JARVIS_PHONE_NOTIFY="")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
