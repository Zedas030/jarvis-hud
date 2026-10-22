"""
Everything that describes the person and the home Jarvis runs for.

Nothing personal lives in the code: names, places and device IDs come from
.env (JARVIS_USER_*, JARVIS_PLACE, ...) and from config/home.json, which
maps the generic dashboard fields to your Home Assistant entities. Both stay
local (.gitignore); config/home.example.json shows the format.
"""
import json
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)

# How Jarvis addresses the user. USER_LABEL is used in logs and transcripts.
USER_NAME = os.environ.get("JARVIS_USER_NAME", "").strip()
USER_LABEL = USER_NAME or "User"
PLACE = os.environ.get("JARVIS_PLACE", "").strip()          # e.g. a city or "Golfplatz XY"
TIMEZONE = os.environ.get("JARVIS_TIMEZONE", "Europe/Berlin").strip()


def place_suffix():
    return f" ({PLACE})" if PLACE else ""


def persona_line():
    """One line for the system prompt — empty if no name is configured."""
    return f"Der User heißt {USER_NAME}." if USER_NAME else ""


def _load_home():
    for name in ("home.json", "home.example.json"):
        p = ROOT / "config" / name
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                pass
    return {}


# Home Assistant entity mapping for the dashboard and the briefing.
HOME = _load_home()


def ha(key, default=None):
    """A configured entity ID (or list of IDs) from config/home.json."""
    return HOME.get(key, default)
