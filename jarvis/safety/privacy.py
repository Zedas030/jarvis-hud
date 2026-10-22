"""
Privacy rules shared by the learning modules (and the conversation log).

- Work stays out: anything that looks like the user's job is not logged,
  not evaluated, not turned into rules or suggestions, not saved as a note.
  Built-in German keywords plus your own in JARVIS_WORK_TERMS (employer,
  colleagues, customers, project names), comma-separated. When in doubt the
  text counts as work — losing a harmless line is better than keeping a work one.
- Cards are explained: before every confirmation card Jarvis says in German
  what he is about to do and why (CARD_RULE goes into the system prompt).
"""
import os
import re

_BUILTIN = (
    "arbeit", "arbeite", "arbeitest", "arbeitszeit", "arbeitgeber", "arbeitsplatz", "job", "büro", "buero",
    "homeoffice", "home office", "kollege", "kollegen", "kollegin", "kolleginnen", "chef", "chefin",
    "vorgesetzt", "kunde", "kunden", "kundin", "mandant", "meeting", "besprechung", "jour fixe", "deadline",
    "abgabe", "präsentation", "praesentation", "projektplan", "sprint", "jira", "confluence", "ticket",
    "teams-call", "teams call", "schicht", "dienstplan", "überstunden", "ueberstunden", "firma", "firmen",
    "geschäftlich", "geschaeftlich", "dienstlich", "dienstreise", "angebot für", "lastenheft", "pflichtenheft",
)
_EXTRA = tuple(t.strip().lower() for t in os.environ.get("JARVIS_WORK_TERMS", "").split(",") if t.strip())
_PATTERN = re.compile(r"(?<![\wäöüß])(" + "|".join(re.escape(t) for t in _BUILTIN + _EXTRA) + r")", re.I)

CARD_RULE = ("Bestätigungskarten: Bevor du ein Werkzeug aufrufst, das eine Karte zeigt (Kalendereintrag, Befehl, "
             "proposal_accept, rule_remove), sagst du auf Deutsch in ein, zwei Sätzen, was du vorhast und warum. "
             "Erst danach der Aufruf.")
WORK_RULE = ("Arbeitsthemen (Job, Kollegen, Kunden, Projekte der Arbeit) speicherst du nie: kein memory_save, "
             "keine Regeln, keine Vorschläge daraus.")


def is_work(*texts):
    """True if any of the texts touches the user's work."""
    return any(_PATTERN.search(t or "") for t in texts)


def without_work(items, key=None):
    """Drops every item whose text (or key(item)) looks like work."""
    return [i for i in items if not is_work(key(i) if key else i)]
