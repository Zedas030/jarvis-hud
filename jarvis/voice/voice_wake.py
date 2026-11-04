"""
Was the utterance meant for Jarvis? The text-mode wake word check of the HUD (ui/app.js → Wake),
now on the server so the engine can decide before the model is called.

  check(text, awake, strict) -> (ok, rest)
      ok: the words count; rest: the request without the name.
      awake: a conversation window is open (after an answer, after a bare "Jarvis", push-to-talk);
      strict: TV or music in the room — only a name at the start or the end counts.
  is_bare(text)  the utterance is nothing but the name ("Jarvis?") — he should open the window and listen
  names(text)    the name appears anywhere (barge-in by name)
"""
import re

# spellings Whisper produces for "Jarvis"; "arvis" lost its first sound
NAME = (r"(?:jarvis|jarviz|jarves|javis|jarwis|jervis|yarvis|garvis|tscharvis|tschawis|dschawis|dschavis|"
        r"charvis|chavis|arvis|jarvi)")
START = re.compile(r"^\W*(?:(?:hey|hi|hallo|ok|okay|he|ey|du)\W+)?" + NAME + r"\b[\s,.!?:;-]*", re.I)
END = re.compile(r"[\s,;-]+(?:bitte\W+)?" + NAME + r"\W*$", re.I)
MID = re.compile(r"(^|[\s,.!?;:-]+)" + NAME + r"(?![^\W\d_])[\s,.!?:;-]*", re.I)
ANY = re.compile(r"(^|[^^\w])" + NAME + r"(?![^\W\d_])", re.I)
WORD = re.compile(r"[^\W\d_]|\d", re.U)


def names(text):
    return bool(ANY.search(text or ""))


def is_bare(text):
    m = START.match(text or "")
    if not m:
        return False
    return len(WORD.findall((text or "")[m.end():])) < 2


def check(text, awake=False, strict=False):
    text = (text or "").strip()
    m = START.match(text)
    if m:
        return True, text[m.end():].strip()
    e = END.search(text)
    if e:
        punct = re.search(r"[.!?]\s*$", text)
        return True, (text[:e.start()].strip() + (punct.group(0).strip() if punct else ""))
    if not strict:
        mid = MID.search(text)
        if mid:
            return True, re.sub(r"\s+", " ", text[:mid.start()] + " " + text[mid.end():]).strip()
    if awake:
        return True, text
    return False, text
