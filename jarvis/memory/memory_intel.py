"""
Memory intelligence: before a fact is saved, compare it with what is there.

  duplicate   "Mag kurze Antworten" vs "Mag kurze Antworten, Sir"    → not saved twice
  conflict    "Wohnt in Köln" vs "Wohnt in Bonn"                      → NOT saved, Jarvis asks
              (single-valued things: where someone lives, birthday, height, …;
              different numbers or a flipped negation on the same topic)
  related     "Mag Kaffee" vs "Mag Kaffee schwarz"                    → saved, mentioned
  —           "Mag Jazz" vs "Mag Golf"                                → simply saved (liking is not exclusive)

Nothing is ever overwritten because of a conflict; correcting stays memory_save
with `replace` after the user said what is right.

Linking: names of people, projects and devices that have their own note
(Personen/, Projekte/, Netzwerk-Geräte/) become [[links]] in a new entry, so the
knowledge graph in the HUD connects them.

review() lists duplicates and conflicts inside every note for "Prüf mein Gedächtnis".
"""
import re

import memory
import memory_tools as mt

_STOP = {"der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer", "und", "oder", "in", "im",
         "am", "an", "auf", "mit", "zu", "zum", "zur", "von", "vom", "für", "bei", "ist", "sind", "hat", "haben", "er",
         "sie", "es", "sein", "seine", "seinen", "ihr", "ihre", "user", "sir", "gern", "gerne", "sehr", "auch", "noch",
         "immer", "oft", "mal", "so", "wie", "als", "aber", "dass", "nach", "aus", "bis", "um", "über"}
_NEG = re.compile(r"\b(nicht|kein|keine|keinen|keinem|keiner|nie|niemals)\b")
SINGLE = re.compile(r"^(wohnt|wohnort|heißt|heisst|name|geboren|geburtstag|geburtsdatum|fährt|auto|größe|groß|alter|"
                    r"jahre|adresse|telefon|handynummer|nummer|email|mail|verheiratet|partner|partnerin|freundin|freund|"
                    r"lieblings\w*|handicap|kleidergröße|schuhgröße|blutgruppe|zimmer|raum)$")
LINK_DIRS = ("Personen", "Projekte", "Netzwerk-Geräte")


def words(text):
    text = re.sub(r"\[\[([^\]|]+)(\|[^\]]*)?\]\]", r"\1", text or "")
    return {w for w in re.findall(r"[a-zäöüß0-9]+", text.lower()) if w not in _STOP and len(w) > 1}


def numbers(text):
    return set(re.findall(r"\d+(?:[.,]\d+)?", text or ""))


def compare(new, bullets):
    """('duplicate'|'conflict'|'related', bullet, reason) for the strongest match, or None."""
    a = words(new)
    if not a:
        return None
    best = None
    for b_raw in bullets:
        b_text = mt.split_tag(b_raw)[0]
        b = words(b_text)
        if not b:
            continue
        shared = a & b
        jacc = len(shared) / len(a | b)
        cover = len(shared) / min(len(a), len(b))
        if jacc >= 0.8 and numbers(new) == numbers(b_text) and bool(_NEG.search(new.lower())) == bool(_NEG.search(b_text.lower())):
            return ("duplicate", b_raw, "fast gleich")
        if not shared:
            continue
        reason = None
        plain_a = {w for w in a if not w.isdigit()}
        plain_b = {w for w in b if not w.isdigit()}
        if numbers(new) and numbers(b_text) and numbers(new) != numbers(b_text) and plain_a and plain_a == plain_b:
            reason = "andere Zahl zum selben Thema"     # same words, only the number differs
        elif bool(_NEG.search(new.lower())) != bool(_NEG.search(b_text.lower())) and cover >= 0.6:
            reason = "Verneinung umgedreht"
        elif any(SINGLE.match(w) for w in shared) and (a - b) and (b - a) and cover >= 0.5:
            reason = "Eigenschaft, die es nur einmal gibt"
        if reason:
            return ("conflict", b_raw, reason)
        if (a <= b or b <= a) and cover >= 0.6 and best is None:
            best = ("related", b_raw, "ergänzt")
    return best


def link_entities(text, own_note=""):
    """Adds [[links]] to people, projects and devices that have their own note."""
    titles = []
    for d in LINK_DIRS:
        folder = memory.MEMORY_DIR / d
        if folder.exists():
            titles += [p.stem for p in folder.glob("*.md")]
    own = own_note.split("/")[-1].lower()
    for title in sorted(set(titles), key=len, reverse=True):
        if len(title) < 3 or title.lower() == own or f"[[{title}" in text:
            continue
        m = re.search(r"(?<![\w\[])" + re.escape(title) + r"(?![\w\]])", text, re.I)
        if m:
            found = m.group(0)
            link = f"[[{title}]]" if found == title else f"[[{title}|{found}]]"
            text = text[:m.start()] + link + text[m.end():]
    return text


def review():
    """Duplicates and conflicts inside each fact note."""
    rows = []
    for path in sorted(memory.MEMORY_DIR.rglob("*.md")) if memory.MEMORY_DIR.exists() else []:
        bullets = mt._bullets(path)
        for i, b in enumerate(bullets):
            hit = compare(mt.split_tag(b)[0], bullets[:i])
            if hit and hit[0] in ("duplicate", "conflict"):
                kind = "Doppelt" if hit[0] == "duplicate" else f"Widerspruch ({hit[2]})"
                rows.append(f"- {mt._title(path)}: {kind}: „{mt.split_tag(b)[0]}“ ↔ „{mt.split_tag(hit[1])[0]}“")
    return "\n".join(rows[:30]) or "Keine Doppelungen oder Widersprüche gefunden."
