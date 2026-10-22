"""
Personality profiles, switched by voice without the LLM:

  standard   the usual Jarvis: dry, British-polite, a little sarcastic, "Sir"
  profi      factual and precise, no jokes — e.g. when guests are around
  technisch  detailed, technical terms, exact numbers, may run a little longer
  locker     first name, casual, more humour

"Jarvis, Profi-Modus" · "sei mal locker" · "technischer Modus für zwei Stunden" ·
"zurück zum Normalen" · "welcher Modus?". The choice lasts until changed (or the
given time ends). Profiles can be changed or added in config/profiles.json
(JARVIS_PROFILES): {"name": {"label", "anrede", "ton", "extra", "triggers": [...]}}.

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
    "standard": {"label": "Standard", "anrede": None, "ton": None, "extra": "",
                 "triggers": [r"(standard|normal)[- ]?modus", r"zurück zum normalen", r"sei wieder normal",
                              r"normaler jarvis"]},
    "profi": {"label": "Profi", "anrede": 'Deutsch, du siezt ihn, "Sir" nur selten.',
              "ton": "Ton: sachlich, präzise, höflich, ohne Witze und ohne Sarkasmus.", "extra": "",
              "triggers": [r"(profi|business|seriös)[- ]?modus", r"sei (mal )?(seriös|sachlich|professionell)"]},
    "technisch": {"label": "Technisch", "anrede": "Deutsch, du siezt ihn.",
                  "ton": ("Ton: technisch genau, Fachbegriffe erlaubt, konkrete Zahlen mit Einheiten, kurze "
                          "Begründungen; trocken, ohne Witzeleien."),
                  "extra": "Hier darfst du bis zu fünf Sätze nutzen, wenn es der Sache dient.",
                  "triggers": [r"technisch(er|en)?[- ]?modus", r"technisch bitte", r"sei (mal )?technisch"]},
    "locker": {"label": "Locker", "anrede": 'Deutsch, du duzt ihn und sprichst ihn mit Vornamen an statt mit "Sir".',
               "ton": "Ton: locker, freundlich, mehr Humor, Umgangssprache erlaubt, aber nie albern.", "extra": "",
               "triggers": [r"locker(er|en)?[- ]?modus", r"sei (mal )?(locker|entspannt)", r"du kannst mich duzen",
                            r"duz mich"]},
}

COMMON = ("Humor nur, wenn er passt: kein Witz bei Ausfällen, Fehlern, Gesundheitssorgen oder schlechten Nachrichten. "
          "Bist du unsicher, sag es offen, statt zu raten.")
TYPED_HINT = ("Diese Frage wurde getippt, nicht gesprochen: Erster Absatz ein bis zwei Sätze, die vorgelesen werden. "
              "Danach, nach einer Leerzeile, gern ausführlich und strukturiert als Text (Listen erlaubt, keine Tabellen).")

_WHICH = re.compile(r"\b(welcher modus|welches profil|wie bist du (gerade )?eingestellt|in welchem modus)\b")
_HOURS = re.compile(r"\bfür (eine|einen|1|zwei|2|drei|3|vier|4|\d{1,2}) stunden?\b")
_WORDS = {"eine": 1, "einen": 1, "zwei": 2, "drei": 3, "vier": 4}


def profiles():
    out = {k: dict(v) for k, v in DEFAULTS.items()}
    try:
        custom = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        custom = {}
    for name, p in custom.items():
        if isinstance(p, dict) and not name.startswith("_"):
            out[name.lower()] = dict(out.get(name.lower(), {"label": name, "anrede": None, "ton": None,
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
    if p.get("anrede"):
        text = re.sub(r'Deutsch, du siezt ihn, gelegentlich "Sir"\.', lambda m: p["anrede"], text, count=1)
    if p.get("ton"):
        text = re.sub(r"Ton: [^.]*\.", lambda m: p["ton"], text, count=1)
    if p.get("extra"):
        text += "\n" + p["extra"]
    return text + "\n" + COMMON


_SAY = {"standard": "Wie gewohnt, Sir.", "profi": "Profi-Modus, Sir. Ab jetzt sachlich.",
        "technisch": "Technischer Modus, Sir. Ab jetzt mit Zahlen und Einheiten.",
        "locker": "Alles klar, ab jetzt locker."}


def handle(n):
    """Local router hook: reply text or None."""
    if _WHICH.search(n):
        p = commands.load().get("persona") or {}
        until = p.get("until")
        rest = f", noch bis {time.strftime('%H:%M', time.localtime(until))}" if until and until > time.time() else ""
        return f"Gerade im Modus {label()}{rest}."
    for name, p in profiles().items():
        if any(re.search(r"\b" + t + r"\b", n) for t in p.get("triggers") or []):
            m = _HOURS.search(n)
            hours = (int(m.group(1)) if m.group(1).isdigit() else _WORDS[m.group(1)]) if m else None
            set_profile(name, hours)
            reply = _SAY.get(name, f"Modus {p['label']}.")
            if hours:
                reply += f" Für {hours} Stunde{'n' if hours > 1 else ''}, dann wieder wie gewohnt."
            return reply
    return None
