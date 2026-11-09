"""
Settings of the proactive mode: config/proactive.yaml (JARVIS_PROACTIVE_CONFIG) over the defaults below.

The file is private like config/home.json; config/proactive.example.yaml is the commented template.
Without a file every default applies and the mode is off. JARVIS_PROACTIVE_MODE=1/0 overrides
`enabled`, so a container can switch it without editing the file. The file is re-read when it changes.

Secrets never live here: Home Assistant and the calendar take theirs from .env.
"""
import copy
import os
import pathlib
import threading

import jarvis_log
from voice_config import _merge, _wrap

log = jarvis_log.get(__name__)

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATH = pathlib.Path(os.environ.get("JARVIS_PROACTIVE_CONFIG", ROOT / "config" / "proactive.yaml"))
LEVELS = ("critical", "important", "nice")       # most urgent first; below "nice" a notice is only shown

DEFAULTS = {
    "enabled": False,
    "ha": {"switch_proactive": "", "switch_guests": "", "switch_quiet": ""},
    "output": {"default_room": "", "max_seconds": 25, "words_per_second": 2.5, "formulate_with_llm": True,
               "llm_timeout_s": 25},
    "quiet": {"hours": "22-7", "today_until_hour": 5, "critical_always_speaks": True},
    "calls": {"windows_mic": True, "mic_ignore": ["opera.exe", "chrome.exe", "msedge.exe", "firefox.exe",
                                                   "python.exe", "pythonw.exe"], "entities": []},
    "triggers": {"arrival": {"enabled": True, "min_away_minutes": 20, "room_wait_minutes": 3},
                 "morning": {"enabled": True, "sensors": [], "from_hour": 5, "until_hour": 11},
                 "instant": {"enabled": True},
                 "evening": {"enabled": True, "routine": "Good night"}},
    "feedback": {"threshold": 3, "window_days": 30},
    "sources": {"calendar": {"enabled": True}, "weather": {"enabled": True}, "dwd": {"enabled": True},
                "parcels": {"enabled": True}, "energy": {"enabled": True},
                "noah": {"enabled": False, "switch": "", "master": "", "slave": ""},
                "devices_offline": {"enabled": True, "pattern": "_cloud_connection$", "minutes": 15, "ignore": []},
                "safety": {"enabled": True, "device_classes": ["moisture", "smoke", "gas", "carbon_monoxide"]},
                "devices_on": {"enabled": True},
                "windows": {"enabled": True, "device_classes": ["window", "door", "opening", "garage_door"]},
                "missed": {"enabled": True}},
    "priorities": {"safety": "critical", "dwd_severe": "critical", "dwd": "important", "noah": "important",
                   "device_offline": "important", "calendar_soon": "important", "calendar": "nice",
                   "calendar_tomorrow_early": "important", "parcel": "nice", "weather": "nice",
                   "energy": "nice", "devices_on": "important", "window_open": "important", "missed": "nice"},
}

_LOCK = threading.Lock()
_CACHE = {"cfg": None, "mtime": None}


def _read(path):
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        import yaml
        data = yaml.safe_load(text) or {}
    except ImportError:
        log.warning("PyYAML missing — proactive mode with default settings")
        return {}
    except Exception as e:  # noqa: BLE001 — yaml.YAMLError and friends
        log.warning("%s unreadable (%s) — proactive mode with default settings", pathlib.Path(path).name, str(e)[:80])
        return {}
    return data if isinstance(data, dict) else {}


def _env_flag(cfg):
    flag = os.environ.get("JARVIS_PROACTIVE_MODE", "").strip().lower()
    if flag in ("1", "true", "on", "yes"):
        cfg["enabled"] = True
    elif flag in ("0", "false", "off", "no"):
        cfg["enabled"] = False
    return cfg


def load(path=None):
    """The merged settings (attribute access); re-read when the file changed on disk."""
    p = pathlib.Path(path) if path else PATH
    try:
        mtime = p.stat().st_mtime
    except OSError:
        mtime = None
    with _LOCK:
        if path is None and _CACHE["cfg"] is not None and _CACHE["mtime"] == mtime:
            return _CACHE["cfg"]
        cfg = _wrap(_env_flag(_merge(DEFAULTS, _read(p))))
        if path is None:
            _CACHE.update(cfg=cfg, mtime=mtime)
        return cfg


def make(overrides=None):
    """Defaults plus overrides, without touching the file — for tests."""
    return _wrap(_merge(copy.deepcopy(DEFAULTS), overrides or {}))


def enabled():
    return bool(load().enabled)


def source_on(cfg, name):
    return bool((cfg.sources.get(name) or {}).get("enabled", False))


def level(cfg, kind):
    """The configured level of a kind of notice ("critical", "important", "nice")."""
    lv = str((cfg.priorities or {}).get(kind, "nice")).strip().lower()
    return lv if lv in LEVELS else "nice"
