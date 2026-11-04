"""
Pronunciation lexicon — applied to every sentence right before it is spoken.

Built-in defaults for the usual stumbling blocks (HA, kWh, °C ...) plus
your own entries in config/pronunciation.json ({"Tuya": "Too-ya"}), which win.
By voice: "Pronounce Tuya as Too-ya" (local, 0 tokens).
Matching is case-sensitive and on whole words, so "HA" does not touch "HAL".
"""
import json
import os
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
PATH = pathlib.Path(os.environ.get("JARVIS_PRONOUNCE", ROOT / "config" / "pronunciation.json"))
DEFAULTS = {
    "NAS": "naz", "Raspi": "Raspberry Pie", "HA": "Home Assistant", "HUD": "hud",
    "kWh": "kilowatt hours", "kW": "kilowatts", "W": "watts", "°C": " degrees", "km/h": "kilometres per hour",
    "LED": "L E D", "PV": "P V", "PC": "P C", "WLAN": "W-LAN", "SSD": "S S D", "CPU": "C P U",
    "GPU": "G P U", "e.g.": "for example", "approx.": "approximately", "etc.": "and so on",
}
_CACHE = {"mtime": None, "user": {}, "re": None, "map": {}}


def user_entries():
    try:
        mtime = PATH.stat().st_mtime
    except OSError:
        mtime = None
    if _CACHE["mtime"] != mtime or _CACHE["re"] is None:
        user = {}
        if mtime is not None:
            try:
                user = json.loads(PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                user = {}
        table = dict(DEFAULTS, **{k: v for k, v in user.items() if isinstance(k, str) and isinstance(v, str)})
        pats = sorted(table, key=len, reverse=True)
        # word boundaries that also work for keys like "°C" or "e.g."
        rx = re.compile("|".join(r"(?<![\w])" + re.escape(p) + r"(?![\w])" for p in pats))
        _CACHE.update(mtime=mtime, user=user, re=rx, map=table)
    return _CACHE["user"]


def apply(text):
    user_entries()
    if not text or _CACHE["re"] is None:
        return text
    return _CACHE["re"].sub(lambda m: _CACHE["map"][m.group(0)], text)


def learn(word, spoken):
    data = dict(user_entries())
    data[word] = spoken
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, PATH)
    _CACHE["mtime"] = None


_SAY_AS = re.compile(r"^\W*(?:jarvis\W+)?(?:pronounce|say)\s+[“\"']?(?P<w>[\w.°/ -]{1,30}?)[”\"']?\s+(?:as|like)\s+"
                     r"[“\"']?(?P<s>[\w .,-]{1,40}?)[”\"']?\W*$", re.I)


def handle(n, raw=""):
    m = _SAY_AS.match(raw or "")
    if not m:
        return None
    word, spoken = m.group("w").strip(), m.group("s").strip()
    learn(word, spoken)
    return f"Very well, from now on I say {spoken}, Sir."
