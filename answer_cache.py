"""
Short-lived answer cache for the LLM path.

"Hab ich neue Mails?" twice in five minutes costs one call, not two. Only
read-only questions are cached (groups mail, calendar, systems, health, web,
weather, memory), never anything that changes something. A turn in which the LLM saved
a note or marked mails read empties the cache. JARVIS_CACHE_S (default 300, 0 = off).
"""
import os
import re
import threading
import time

TTL = int(os.environ.get("JARVIS_CACHE_S", "300"))
READ_ONLY = {"memory", "mail", "calendar", "systems", "health", "web", "weather"}
_WRITES = ("memory_save", "mail_mark_read")   # tools that change what later answers would say
_CHANGES = re.compile(r"\b(merk|markier|speicher|notier|erinner|trag\w*|schreib|lösch|setz|schalt|mach|öffne|spiel|"
                      r"start|kauf|bestell|sende|schick|änder|stell|füg|nimm|streich|vergiss)", re.I)
_DATA = {}
_LOCK = threading.Lock()


def _key(message, groups):
    return (" ".join(re.sub(r"[^\wäöüß]+", " ", (message or "").lower()).split()), tuple(sorted(groups)))


def cacheable(message, groups):
    return bool(TTL) and set(groups) <= READ_ONLY and not _CHANGES.search(message or "") and len(message or "") < 200


def get(message, groups):
    if not cacheable(message, groups):
        return None
    with _LOCK:
        hit = _DATA.get(_key(message, groups))
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    return None


def put(message, groups, reply, tools_used=()):
    if any(t.endswith(_WRITES) for t in tools_used):
        clear()          # e.g. "Hab ich neue Mails?" is stale once mails were marked read
        return
    if not reply or not cacheable(message, groups):
        return
    with _LOCK:
        _DATA[_key(message, groups)] = (time.time(), reply)
        for k in [k for k, v in _DATA.items() if time.time() - v[0] > TTL]:
            _DATA.pop(k, None)


def clear():
    with _LOCK:
        _DATA.clear()
