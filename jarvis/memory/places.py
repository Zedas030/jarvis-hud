"""
"Where is the spare key?" — where the user put things. Local, 0 tokens, in the vault note Places.

    "I put the spare key in the blue box in the hallway."   -> Places: "Spare key: in the blue box in the hallway"
    "Remember, the passport is in the desk, top left."
    "The winter tyres are now in the basement."
    "Where is the spare key?" / "Where did I put the passport?"

A new place for the same thing replaces the old one (the old bullet goes, with its date, into the history of
memory_save). A plain statement only counts with "remember", "now" or "I put …": otherwise
"the light is on in the living room" or the TV would fill the note. Questions find the bullet by its words; when
the note has nothing, the full-text index (memory_index.py) may know an older conversation about it.
"""
import datetime as dt
import re

NOTE = "Places"
_ART = r"(?:the|my|our|a|an)"
_PREP = (r"(?:in|into|inside|on|onto|on top of|under|underneath|beneath|behind|next to|beside|by|at|near|"
         r"between|in front of|above|below|top|bottom)")
_PUT = re.compile(rf"^\s*(?:jarvis\s+)?(?:remember\s+(?:that\s+)?)?i\s+(?:have\s+|ve\s+|just\s+)?"
                  rf"(?:put|placed|stored|stashed|hid|hidden|keep|kept|hung|packed)\s+{_ART}\s+"
                  rf"(?P<item>.{{2,40}}?)\s+(?P<place>{_PREP}\s+.{{2,60}}?)\s*$")
_IS = re.compile(rf"^\s*(?:jarvis\s+)?(?P<merk>remember\s+(?:that\s+)?)?{_ART}\s+(?P<item>.{{2,40}}?)\s+"
                 rf"(?:is|are|lies|lie|sits|sit|hangs|hang|stays|stay)\s+(?P<now>now\s+)?(?:kept\s+|stored\s+|located\s+)?"
                 rf"(?P<place>{_PREP}\s+.{{2,60}}?)\s*$")
_ASK = re.compile(rf"^\s*(?:jarvis\s+)?(?:do you know\s+)?where\s+(?:is|are|did i put|have i put|ive put|did i leave|"
                  rf"have i left|did i store|did i hide|do i keep|did i keep|was|were)\s+(?:{_ART}\s+)?"
                  rf"(?P<item>.{{2,40}}?)(?:\s+(?:again|now|kept|stored|located))?\s*$"
                  rf"|^\s*(?:jarvis\s+)?do you know where\s+(?:{_ART}\s+)?(?P<item2>.{{2,40}}?)\s+(?:is|are)\s*$")
_NOT_THINGS = re.compile(r"\b(light|lights|lamp|heating|music|tv|television|i|you|he|she|it|they|we)\b")


def _not_a_thing(item):
    """People and devices are other questions ("where is <user name>" belongs to the room tracking)."""
    import jarvis_profile
    name = (jarvis_profile.USER_NAME or "").split(" ")[0].lower()
    return bool(_NOT_THINGS.search(item) or (name and re.search(rf"\b{re.escape(name)}\b", item)))


def cased(fragment):
    """The user's own spelling of a fragment the router lower-cased ("blue box in the hallway" -> "blue Box in the Hallway")."""
    import router
    raw = " ".join(re.sub(r"[^\w%]+", " ", re.sub(r"['’]", "", router._raw() or "")).split())
    frag = " ".join((fragment or "").split())
    i = raw.lower().find(frag.lower())
    return raw[i:i + len(frag)] if frag and i >= 0 else frag


def _norm_item(item):
    item = " ".join(item.split()).strip(" ,.")
    return item[:1].upper() + item[1:]


def _bullets():
    import memory
    path = memory.note_path(NOTE)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [ln[2:].strip() for ln in lines if ln.startswith("- ")]


def _strip_links(text):
    return re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]", r"\1", text)


def _day(iso):
    """"2026-11-10" -> "November 10"."""
    try:
        d = dt.date.fromisoformat(str(iso)[:10])
    except ValueError:
        return ""
    return f"{d:%B} {d.day}"


def remember(item, place):
    import jarvis_mcp
    import memory_tools
    item = _norm_item(item)
    text = f"{item}: {place.strip()}"
    old = next((b for b in _bullets() if _strip_links(memory_tools.split_tag(b)[0]).lower().startswith(item.lower() + ":")), None)
    args = {"note": NOTE, "text": text, "source": "said"}
    if old:
        args["replace"] = memory_tools.split_tag(old)[0]
    res = jarvis_mcp._memory("memory_save", args)
    if res.startswith(("Saved", "Already")):
        return f"Noted, Sir: {item} {place.strip()}."
    return res


def find(item):
    import memory_tools
    words = [w for w in re.findall(r"[a-z0-9]{3,}", item.lower())]
    hits = []
    for b in _bullets():
        plain = _strip_links(memory_tools.split_tag(b)[0])
        head, _, place = plain.partition(":")
        if words and all(w in head.lower() for w in words):
            day = memory_tools.split_tag(b)[2]
            hits.append((head.strip(), place.strip(), day))
    return hits


def handle(n):
    n = re.sub(r"^\s*is\s+(?=(?:jarvis\s+)?where\b)", " ", n)   # router._local puts "is" before a question it does not know
    m = _ASK.match(n)
    if m:
        item = m.group("item") or m.group("item2")
        if _not_a_thing(item):
            return None                     # "where is <name>", "where is the light on" are other questions
        hits = find(item)
        if hits:
            head, place, day = hits[-1]
            when = _day(day) if day else ""
            return f"{head}: {place}" + (f" — that is what you told me on {when}." if when else ".")
        try:
            import memory_index
            turns = memory_index.search(item, limit=1, kind="turn")
        except Exception:  # noqa: BLE001
            turns = []
        if turns:
            return (f"I have nothing noted about it. In our conversation on {_day(turns[0]['day'])} "
                    f"it came up: {turns[0]['snippet'][:160]}")
        return f"You have not told me where “{_norm_item(item)}” is, Sir."
    m = _PUT.match(n)
    if not m:
        m = _IS.match(n)
        if not m or not (m.group("merk") or m.group("now")):
            return None
    if _not_a_thing(m.group("item")):
        return None
    if re.search(r"\b(list|lists|to ?do|calendar)\b", m.group("place")):
        return None                         # "I put the milk on the shopping list" belongs to the lists
    return remember(cased(m.group("item")), cased(m.group("place")))
