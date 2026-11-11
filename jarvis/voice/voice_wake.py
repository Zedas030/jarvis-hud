"""
Was the utterance meant for Jarvis? The text-mode wake word check of the HUD (ui/app.js → Wake),
now on the server so the engine can decide before the model is called.

  check(text, awake, strict) -> (ok, rest)
      ok: the words count; rest: the request without the name.
      awake: a conversation window is open (after an answer, after a bare "Jarvis", push-to-talk);
      strict: TV or music in the room — only a name at the start or the end counts.
  is_bare(text)  the utterance is nothing but the name ("Jarvis?") — he should open the window and listen
  names(text)    the name appears anywhere (barge-in by name)
  foreign(text)  mostly another language or script: the TV, a video, not the user (who speaks English)
"""
import re

# spellings Whisper produces for "Jarvis"; "arvis" lost its first sound
NAME = (r"(?:jarvis|jarviz|jarves|javis|jarwis|jervis|yarvis|garvis|charvis|chavis|arvis|jarvi|jarvas)")
START = re.compile(r"^\W*(?:(?:hey|hi|hello|ok|okay|yo|oi)\W+)?" + NAME + r"\b[\s,.!?:;-]*", re.I)
END = re.compile(r"[\s,;-]+(?:please\W+)?" + NAME + r"\W*$", re.I)
MID = re.compile(r"(^|[\s,.!?;:-]+)" + NAME + r"(?![^\W\d_])[\s,.!?:;-]*", re.I)
ANY = re.compile(r"(^|[^^\w])" + NAME + r"(?![^\W\d_])", re.I)
WORD = re.compile(r"[^\W\d_]|\d", re.U)
# Function words only: they say which language a sentence is in, names and loanwords do not.
# Whisper is told "en" but writes German commentary as German ("Jarvis Kelsey" = Travis Kelce).
EN = frozenset("""the and is are was were that this these those with what you your he she they them his her
their it its of to for on in at be been have has had will would can could should going gonna get got there
here from about just like not don't it's that's he's they're we're i'm you're let's i we our me my do does
did so but or if when who which where how why than then very really right now well yeah""".split())
# languages a TV or a video in the room is likely to speak (German, Spanish, French); words that are
# English too ("in", "an", "was", "bad", "gut", "er") are left out
OTHER = frozenset("""der die das den dem des und ist sind ich du sie es wir ihr mir mich dir dich mein
dein nicht kein keine bitte mach mache mal noch schon auch auf aus im zum zur mit vom für bei nach wie wer
wo warum wann ein eine einen einem einer ja nein doch heute jetzt gerade dann aber oder wenn ob soll kannst
kann wird haben hat gesagt el la los las que por para con una está pero muy le les et est pas pour avec
dans une du je vous nous très mais""".split())
TOKENS = re.compile(r"[a-zäöüßáéíóúñàèêçâ']+", re.I)
# Cyrillic, Greek, Hebrew/Arabic, Japanese, Chinese, Korean: Whisper's guesses at foreign speech
SCRIPT = re.compile("[\u0400-\u04ff\u0370-\u03ff\u0590-\u06ff\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af]")


def foreign(text):
    """True when the words are not English: at least three function words of another language and
    clearly more of them than English ones, or letters of another script. "Jarvis, play Ein Kompliment"
    stays English; when the name is in it, the words after it count (the TV before "Jarvis, good night"
    does not)."""
    text = text or ""
    if len(SCRIPT.findall(text)) >= 3:
        return True
    # TV first, then "Jarvis, good night ...": what follows the name decides
    m = ANY.search(text)
    if m and len(TOKENS.findall(text[m.end():])) >= 2:
        text = text[m.end():]
    words = [w.lower() for w in TOKENS.findall(text)]
    en = sum(1 for w in words if w in EN)
    other = sum(1 for w in words if w in OTHER)
    return other >= 3 and other > 2 * en


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
