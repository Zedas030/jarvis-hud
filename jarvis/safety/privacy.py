"""
Privacy rules shared by the learning modules (and the conversation log).

- Work stays out: anything that looks like the user's job is not logged,
  not evaluated, not turned into rules or suggestions, not saved as a note.
  Built-in keywords plus your own in JARVIS_WORK_TERMS (employer,
  colleagues, customers, project names), comma-separated. When in doubt the
  text counts as work — losing a harmless line is better than keeping a work one.
- Cards are explained: before every confirmation card Jarvis says in plain words
  what he is about to do and why (CARD_RULE goes into the system prompt).
"""
import os
import re

_BUILTIN = (
    "work", "working", "workplace", "working hours", "employer", "my job", "job", "the office", "at the office",
    "home office", "colleague", "colleagues", "coworker", "co-worker", "my boss", "manager", "line manager",
    "customer", "customers", "client", "clients", "meeting", "jour fixe", "deadline",
    "presentation", "project plan", "sprint", "jira", "confluence", "ticket",
    "teams call", "teams-call", "shift", "rota", "overtime", "company", "business trip", "on business",
    "quote for", "requirements spec", "specification",
)
_EXTRA = tuple(t.strip().lower() for t in os.environ.get("JARVIS_WORK_TERMS", "").split(",") if t.strip())
_PATTERN = re.compile(r"(?<![\w])(" + "|".join(re.escape(t) for t in _BUILTIN + _EXTRA) + r")", re.I)

CARD_RULE = ("Confirmation cards: before you call a tool that shows a card (calendar entry, command, "
             "proposal_accept, rule_remove), say in English in one or two sentences what you are about to do and why. "
             "Only then the call.")
WORK_RULE = ("You never store work topics (job, colleagues, customers, work projects): no memory_save, "
             "no rules, no suggestions from them.")


def is_work(*texts):
    """True if any of the texts touches the user's work."""
    return any(_PATTERN.search(t or "") for t in texts)


def without_work(items, key=None):
    """Drops every item whose text (or key(item)) looks like work."""
    return [i for i in items if not is_work(key(i) if key else i)]
