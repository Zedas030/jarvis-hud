"""
Memory intelligence: before a fact is saved, compare it with what is there.

  duplicate   "Likes short answers" vs "Likes short answers, Sir"    → not saved twice
  conflict    "Lives in Cologne" vs "Lives in Bonn"                    → NOT saved, Jarvis asks
              (single-valued things: where someone lives, birthday, height, …;
              different numbers or a flipped negation on the same topic)
  related     "Likes coffee" vs "Likes coffee black"                  → saved, mentioned
  —           "Likes jazz" vs "Likes golf"                            → simply saved (liking is not exclusive)

Nothing is ever overwritten because of a conflict; correcting stays memory_save
with `replace` after the user said what is right.

Linking: names of people, projects and devices that have their own note
(People/, Projects/, Network devices/) become [[links]] in a new entry, so the
knowledge graph in the HUD connects them.

review() lists duplicates and conflicts inside every note for "Check my memory".
"""
import re

import memory
import memory_tools as mt

_STOP = {"the", "a", "an", "and", "or", "in", "on", "at", "with", "to", "of", "from", "for", "by", "is", "are",
         "has", "have", "he", "she", "it", "his", "her", "their", "user", "sir", "likes", "like", "very", "also",
         "still", "always", "often", "so", "as", "but", "that", "after", "until", "about", "be", "was", "were"}
_NEG = re.compile(r"\b(not|no|never|dont|doesnt|isnt|wont|nobody|none)\b")
SINGLE = re.compile(r"^(lives|home|hometown|called|name|born|birthday|birth|drives|car|height|tall|age|"
                    r"years|address|phone|mobile|number|email|mail|married|partner|wife|husband|girlfriend|boyfriend|"
                    r"favou?rite\w*|handicap|size|shoe|blood|room)$")
LINK_DIRS = ("People", "Projects", "Network devices")


def words(text):
    text = re.sub(r"\[\[([^\]|]+)(\|[^\]]*)?\]\]", r"\1", text or "")
    return {w for w in re.findall(r"[a-z0-9]+", re.sub(r"['’]", "", text.lower())) if w not in _STOP and len(w) > 1}


def _flat(text):
    return re.sub(r"['’]", "", (text or "").lower())


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
        if jacc >= 0.8 and numbers(new) == numbers(b_text) and bool(_NEG.search(_flat(new))) == bool(_NEG.search(_flat(b_text))):
            return ("duplicate", b_raw, "nearly the same")
        if not shared:
            continue
        reason = None
        plain_a = {w for w in a if not w.isdigit()}
        plain_b = {w for w in b if not w.isdigit()}
        if numbers(new) and numbers(b_text) and numbers(new) != numbers(b_text) and plain_a and plain_a == plain_b:
            reason = "a different number on the same topic"     # same words, only the number differs
        elif bool(_NEG.search(_flat(new))) != bool(_NEG.search(_flat(b_text))) and cover >= 0.6:
            reason = "negation flipped"
        elif any(SINGLE.match(w) for w in shared) and (a - b) and (b - a) and cover >= 0.5:
            reason = "a property there is only one of"
        if reason:
            return ("conflict", b_raw, reason)
        if (a <= b or b <= a) and cover >= 0.6 and best is None:
            best = ("related", b_raw, "adds to it")
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
                kind = "Duplicate" if hit[0] == "duplicate" else f"Contradiction ({hit[2]})"
                rows.append(f"- {mt._title(path)}: {kind}: “{mt.split_tag(b)[0]}” ↔ “{mt.split_tag(hit[1])[0]}”")
    return "\n".join(rows[:30]) or "No duplicates or contradictions found."
