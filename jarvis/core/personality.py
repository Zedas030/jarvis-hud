"""
Personality profiles, switched by voice without the LLM:

  standard      the usual Jarvis: dry, British-polite, a little sarcastic, "Sir"
  professional  factual and precise, no jokes — e.g. when guests are around
  technical     detailed, technical terms, exact numbers, may run a little longer
  casual        first name, casual, more humour

"Jarvis, professional mode" · "be casual" · "technical mode for two hours" ·
"back to normal" · "which mode?". The choice lasts until changed (or the
given time ends). Profiles can be changed or added in config/profiles.json
(JARVIS_PROFILES): {"name": {"label", "address", "tone", "extra", "triggers": [...]}}.

Rules for every profile (COMMON): humour only when it fits — never on outages,
errors, health worries or bad news; say openly when unsure instead of guessing.

Typed vs. spoken: a typed question may get a longer, structured answer; only its
first paragraph is read aloud (TYPED_HINT, used by the server and the HUD).
"""
import json
import os
import pathlib
import re
import time

import commands

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
PATH = pathlib.Path(os.environ.get("JARVIS_PROFILES", ROOT / "config" / "profiles.json"))

DEFAULTS = {
    "standard": {"label": "Standard", "address": None, "tone": None, "extra": "",
                 "triggers": [r"(standard|normal)[- ]?mode", r"back to normal", r"be normal again",
                              r"normal jarvis"]},
    "professional": {"label": "Professional", "address": 'English, formal, "Sir" only rarely.',
                     "tone": "Tone: factual, precise, polite, without jokes and without sarcasm.", "extra": "",
                     "triggers": [r"(professional|business|serious)[- ]?mode", r"be (more )?(serious|factual|professional)"]},
    "technical": {"label": "Technical", "address": "English, formal.",
                  "tone": ("Tone: technically exact, technical terms allowed, concrete numbers with units, short "
                           "reasons; dry, without jokes."),
                  "extra": "Here you may use up to five sentences when it serves the matter.",
                  "triggers": [r"technical[- ]?mode", r"technical please", r"be (more )?technical"]},
    "casual": {"label": "Casual", "address": 'English, casual, call him by his first name instead of "Sir".',
               "tone": "Tone: relaxed, friendly, more humour, colloquial language allowed, but never silly.", "extra": "",
               "triggers": [r"casual[- ]?mode", r"be (more )?(casual|relaxed)", r"you can call me by my (first )?name",
                            r"drop the sir"]},
}

COMMON = ("Humour only when it fits; a friendly dig at the user is allowed in clearly harmless moments. "
          "It stays loyal and is never hurtful or humiliating, never aims at personal weaknesses or sensitive topics. "
          "No jokes about outages, errors, health worries, emotional or safety topics "
          "or bad news. If you are unsure, say so openly instead of guessing.")
TYPED_HINT = ("This question was typed, not spoken: first paragraph one or two sentences, which are read aloud. "
              "After that, following a blank line, feel free to go into detail as structured text (lists allowed, no tables).")

_WHICH = re.compile(r"\b(which mode|what mode|which profile|how are you (currently )?set|what mode are you in)\b")
_HOURS = re.compile(r"\bfor (an|a|one|1|two|2|three|3|four|4|\d{1,2}) hours?\b")
_WORDS = {"an": 1, "a": 1, "one": 1, "two": 2, "three": 3, "four": 4}

def profiles():
    out = {k: dict(v) for k, v in DEFAULTS.items()}
    try:
        custom = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        custom = {}
    for name, p in custom.items():
        if isinstance(p, dict) and not name.startswith("_"):
            out[name.lower()] = dict(out.get(name.lower(), {"label": name, "address": None, "tone": None,
                                                            "extra": "", "triggers": []}), **p)
    return out


def current():
    p = commands.load().get("persona") or {}
    name, until = p.get("name") or "standard", p.get("until")
    if until and until < time.time():
        name = "standard"
    return name if name in profiles() else "standard"


def label():
    return profiles()[current()]["label"]


def set_profile(name, hours=None):
    if name not in profiles():
        return False
    until = time.time() + hours * 3600 if hours else None
    commands.update_state(lambda d: d.__setitem__("persona", {"name": name, "until": until}))
    return True


def persona(base):
    """The persona text for the system prompt: base (the standard persona) adapted to the profile."""
    p = profiles()[current()]
    text = base
    if p.get("address"):
        text = re.sub(r'English, formal, now and then "Sir"\.', lambda m: p["address"], text, count=1)
    if p.get("tone"):
        text = re.sub(r"Tone: [^.]*\.", lambda m: p["tone"], text, count=1)
    if p.get("extra"):
        text += "\n" + p["extra"]
    return text + "\n" + COMMON


_SAY = {"standard": "As usual, Sir.", "professional": "Professional mode, Sir. Strictly factual from now on.",
        "technical": "Technical mode, Sir. With numbers and units from now on.",
        "casual": "Sure thing, casual from now on."}


def handle(n):
    """Local router hook: reply text or None."""
    if _WHICH.search(n):
        p = commands.load().get("persona") or {}
        until = p.get("until")
        rest = f", until {time.strftime('%H:%M', time.localtime(until))}" if until and until > time.time() else ""
        return f"Currently in {label()} mode{rest}."
    for name, p in profiles().items():
        if any(re.search(r"\b" + t + r"\b", n) for t in p.get("triggers") or []):
            m = _HOURS.search(n)
            hours = (int(m.group(1)) if m.group(1).isdigit() else _WORDS[m.group(1)]) if m else None
            set_profile(name, hours)
            reply = _SAY.get(name, f"{p['label']} mode.")
            if hours:
                reply += f" For {hours} hour{'s' if hours > 1 else ''}, then back to usual."
            return reply
    return None
