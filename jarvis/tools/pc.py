"""
PC control without the LLM: system volume, opening apps, locking the screen,
and "ducking" — Spotify gets quieter while Jarvis speaks.

    "Lautstärke auf 30 Prozent", "PC lauter", "Ton aus", "Wie laut ist der PC?"
    "Öffne Spotify", "Starte den Rechner", "Sperr den Bildschirm"

Volume uses Windows Core Audio through pycaw (pip install pycaw); without it,
only the media keys are left (louder/quieter in 2 % steps). Apps come from a
small whitelist plus config/apps.json ({"Steuer": "C:\\\\Programme\\\\...exe"});
anything else goes to the LLM, which asks before running commands.
"""
import json
import os
import pathlib
import re
import threading

import hostos

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
APPS_FILE = pathlib.Path(os.environ.get("JARVIS_APPS", ROOT / "config" / "apps.json"))
DUCK = float(os.environ.get("JARVIS_DUCK", "0.3"))           # Spotify volume factor while Jarvis talks, 0 = off
DUCK_APPS = {a.strip().lower() for a in os.environ.get("JARVIS_DUCK_APPS", "spotify.exe").split(",") if a.strip()}
APPS = {
    "spotify": "spotify:", "rechner": "calc", "taschenrechner": "calc", "editor": "notepad", "notepad": "notepad",
    "explorer": "explorer", "datei explorer": "explorer", "chrome": "chrome", "edge": "msedge", "firefox": "firefox",
    "outlook": "outlook", "einstellungen": "ms-settings:", "discord": "discord:", "steam": "steam:",
    "task manager": "taskmgr", "taskmanager": "taskmgr", "paint": "mspaint", "word": "winword", "excel": "excel",
    "terminal": "wt", "kalender": "outlookcal:", "uhr": "ms-clock:", "kamera": "microsoft.windows.camera:",
}
_LOCK = threading.Lock()
_DUCKED = {}


def _com():
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:  # noqa: BLE001 — already initialised in this thread
        pass


def _endpoint():
    _com()
    from pycaw.pycaw import AudioUtilities
    dev = AudioUtilities.GetSpeakers()
    vol = getattr(dev, "EndpointVolume", None)
    if vol is None:                                    # older pycaw
        from ctypes import POINTER, cast
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import IAudioEndpointVolume
        vol = cast(dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None), POINTER(IAudioEndpointVolume))
    return vol


def volume():
    """0..100, or None without pycaw."""
    try:
        return round(_endpoint().GetMasterVolumeLevelScalar() * 100)
    except Exception:  # noqa: BLE001
        return None


def set_volume(pct):
    pct = max(0, min(100, int(pct)))
    try:
        v = _endpoint()
        v.SetMute(0, None)
        v.SetMasterVolumeLevelScalar(pct / 100, None)
        return True
    except Exception:  # noqa: BLE001
        return False


def step(delta):
    now = volume()
    if now is not None and set_volume(now + delta):
        return now + delta
    import jarvis_mcp                                   # no pycaw: media keys
    jarvis_mcp._music({"action": "volume_up" if delta > 0 else "volume_down"})
    return None


def mute(on=True):
    try:
        _endpoint().SetMute(1 if on else 0, None)
        return True
    except Exception:  # noqa: BLE001
        if on:
            import jarvis_mcp
            jarvis_mcp._music({"action": "mute"})
            return True
        return False


def duck(on):
    """Spotify (JARVIS_DUCK_APPS) quieter while Jarvis speaks, then back to where it was."""
    if not DUCK:
        return False
    with _LOCK:
        try:
            _com()
            from pycaw.pycaw import AudioUtilities
            for s in AudioUtilities.GetAllSessions():
                name = (s.Process.name() if s.Process else "").lower()
                if name not in DUCK_APPS:
                    continue
                vol = s.SimpleAudioVolume
                if on and s.ProcessId not in _DUCKED:
                    _DUCKED[s.ProcessId] = vol.GetMasterVolume()
                    vol.SetMasterVolume(_DUCKED[s.ProcessId] * DUCK, None)
                elif not on and s.ProcessId in _DUCKED:
                    vol.SetMasterVolume(_DUCKED.pop(s.ProcessId), None)
            if not on:
                _DUCKED.clear()
            return True
        except Exception:  # noqa: BLE001 — no pycaw or no Spotify: nothing to duck
            return False


def apps():
    table = dict(APPS)
    try:
        table.update({k.lower(): v for k, v in json.loads(APPS_FILE.read_text(encoding="utf-8")).items()})
    except (OSError, ValueError, AttributeError):
        pass
    return table


def open_app(name):
    target = apps().get(name.lower().strip())
    if not target:
        return None
    hostos.open_target(target)
    return target


def lock():
    import ctypes
    return bool(ctypes.windll.user32.LockWorkStation())


# ── the voice side ───────────────────────────────────────────
_SYS = r"\b(pc|computer|rechner|system|windows|gesamt|lautsprecher)\b"
_MUSIC = re.compile(r"\b(musik|spotify|song|lied|titel|track)\b")
# "Lautstärke auf 80", but also "stell den PC auf 100 % (Lautstärke)" — there the
# number comes first, and the bare "lautstärke" at the end used to read the volume out.
_SET = re.compile(r"\blautstärke\b.*?\bauf (\d{1,3})\s*(%|prozent)?|\bauf (\d{1,3})\s*(%|prozent)")
_OPEN = re.compile(r"^\W*(?:jarvis\W+)?(?:bitte\s+)?(?:öffne|starte?|mach)\s+(?:mir\s+)?(?:bitte\s+)?(?:mal\s+)?"
                   r"(?:den|die|das)?\s*(?P<app>[\wäöüß ]+?)(?:\s+auf)?(?:\s+bitte)?\W*$", re.I)
_LOCKRX = re.compile(r"\b(sperr\w*|lock)\b.*\b(pc|computer|rechner|bildschirm|windows)\b|"
                     r"\b(bildschirm|pc|computer|rechner)\s+sperren\b")


def handle(n, raw=""):
    if _LOCKRX.search(n):
        lock()
        return "Gesperrt, Sir."
    m = _OPEN.match(raw or "")
    if m and m.group("app").lower().strip() in apps():
        open_app(m.group("app"))
        return f"{m.group('app').strip().capitalize()} kommt, Sir."
    if _MUSIC.search(n):
        return None                                   # music volume stays with the music handler
    # "Lautspecke", "Lautstärker": how Whisper hears "Lautstärke" now and then
    systemish = re.search(_SYS, n) or re.search(r"\b(lautstärke|lauts[pt]\w*|ton|stumm\w*|mute)\b", n)
    if not systemish:
        return None
    s = _SET.search(n)
    if s:
        pct = min(100, int(s.group(1) or s.group(3)))
        if set_volume(pct):
            return f"Lautstärke {pct} Prozent."
        return None
    if re.search(r"\b(ton an|stumm aus|laut schalten|stummschaltung (aus|aufheben)|ton wieder|nicht mehr stumm)\b", n):
        mute(False)
        return "Ton ist wieder an."
    if re.search(r"\b(stumm|ton aus|mute)\b", n):
        mute(True)
        return "Stumm, Sir."
    if re.search(r"\blauter\b", n):
        v = step(+10)
        return f"Lauter, jetzt {v} Prozent." if v is not None else "Lauter."
    if re.search(r"\bleiser\b", n):
        v = step(-10)
        return f"Leiser, jetzt {v} Prozent." if v is not None else "Leiser."
    if re.search(r"\b(wie laut|welche lautstärke|lautstärke\s*$)", n):
        v = volume()
        return f"Die Lautstärke steht auf {v} Prozent, Sir." if v is not None else None
    return None
