"""
Ideas for the 3D printer: "Remember as a print idea: cable holder for the desk". Local, 0 tokens.

A third list next to shopping and to-do (lists.py, key "print_ideas", always the vault note
Memories/Lists/Print ideas — Home Assistant has no use for it):

  "Remember as a print idea …", "New print idea: …"                 add
  "What did I want to print?", "Which print ideas …"                 read
  "The cable holder is printed", "Strike the print idea …"           remove
  "Search for a cable holder on MakerWorld"                          opens MakerWorld's search in the browser (open_website)
"""
import re
import urllib.parse

KEY = "print_ideas"
_IDEA = r"print ?ideas?"
_ADD = re.compile(rf"^\s*(?:jarvis\s+)?(?:please\s+)?(?:remember\s+|note\s+|note down\s+|write down\s+|add\s+|save\s+)?"
                  rf"(?:as\s+)?(?:a\s+|another\s+|one more\s+)?(?:new\s+)?{_IDEA}\s+(?:is\s+|would be\s+)?"
                  rf"(?P<item>.{{3,80}}?)\s*$")
_ADD2 = re.compile(rf"^\s*(?:jarvis\s+)?(?:remember|note|add|save)\s+(?P<item>.{{3,80}}?)\s+"
                   rf"(?:as\s+(?:a\s+)?{_IDEA}|to\s+(?:my\s+|the\s+)?{_IDEA})\s*$")
_READ = re.compile(rf"^\s*(my |the )?{_IDEA}\s*$|"
                   rf"\b(what (did|do) i (still )?want to print|what was i going to print|which {_IDEA}|"
                   rf"what {_IDEA}|show (me )?(the |my )?{_IDEA}|"
                   rf"whats (on )?(my |the )?{_IDEA}|what is (on )?(my |the )?{_IDEA})")
_DONE = re.compile(rf"^\s*(?:jarvis\s+)?(?:strike|delete|remove|cross off|cross out)\s+(?:the\s+)?{_IDEA}\s+"
                   rf"(?P<item>.{{2,80}}?)\s*$"
                   r"|^\s*(?:jarvis\s+)?(?:the|my)\s+(?P<item2>.{2,60}?)\s+(?:is|are|has been|have been)\s+"
                   r"(?:now\s+)?(?:done\s+)?printed\s*$")
_MAKER = re.compile(r"^\s*(?:jarvis\s+)?(?:search|look|find|show|open)\w*\s+(?:up\s+)?(?:me\s+)?(?:for\s+)?"
                    r"(?:the\s+print\s?idea\s+)?(?:a\s+|an\s+|the\s+)?(?P<item>.{2,60}?)\s+(?:on|at|in)\s+maker ?world\s*$")


def cased(fragment):
    """The user's own spelling of a fragment the router lower-cased ("cable holder for the ikea desk" -> "cable holder for the IKEA desk")."""
    import router
    raw = " ".join(re.sub(r"[^\w%]+", " ", re.sub(r"['’]", "", router._raw() or "")).split())
    frag = " ".join((fragment or "").split())
    i = raw.lower().find(frag.lower())
    return raw[i:i + len(frag)] if frag and i >= 0 else frag


def _lists():
    import lists
    lists.LISTS.setdefault(KEY, {"title": "Print ideas", "entity": ""})
    return lists


def handle(n):
    m = _MAKER.match(n)
    if m:
        import jarvis_mcp
        item = cased(m.group("item").strip())
        url = "https://makerworld.com/en/search/models?keyword=" + urllib.parse.quote(item)
        jarvis_mcp._open(url)
        return f"I have opened MakerWorld with “{item}”, Sir."
    lists = _lists()
    m = _DONE.match(n)
    if m:
        item = (m.group("item") or m.group("item2") or "").strip()
        gone = lists.remove(KEY, item)
        if gone:
            return f"“{gone}” crossed off the print ideas, Sir."
        return None if m.group("item2") else f"“{item}” is not on the print ideas, Sir."
    m = _ADD2.match(n) or _ADD.match(n)
    if m and not _READ.search(n):
        item = cased(re.sub(r"^(?:for\s+|a\s+|an\s+)?", "", m.group("item").strip(" :,.")))
        new = lists.add(KEY, [item[:1].upper() + item[1:]])
        return f"Print idea noted: {new[0]}, Sir." if new else "That idea is already on it, Sir."
    if _READ.search(n):
        ideas = lists.entries(KEY)
        if not ideas:
            return "No print ideas yet, Sir. Say for example: remember as a print idea a cable holder."
        return f"Your print ideas ({len(ideas)}): " + lists.say_list(ideas[:8]) + "."
    return None
