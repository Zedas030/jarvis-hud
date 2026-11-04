"""
When is the user done? Not after a fixed silence, but after a wait that depends on what was said.

    ep = Endpointer(cfg.endpoint)
    wait_ms = ep.wait_for(partial_text)       # how long to wait after the last speech frame
    ep.learn(user_continued=True)             # the turn was closed too early: wait longer next time

Rules (English, from the old HUD's experience plus a few more):
  - unfinished: trailing comma, dash, ellipsis, a connector or filler as the last word
    ("and", "but", "because", "um", "so"), an article or preposition without its noun, a modal
    verb without its verb ("I want", "can you"), fewer than three words and no full stop
  - question: ends with "?" or starts with a question word and has no full stop
  - no text (CPU mode has no partials): a plain middle wait
An optional turn model (smart-turn as ONNX) can replace the rules: set endpoint.turn_model; it gets
the last seconds of audio and says "finished" with a probability. Not shipped, hook only.
"""
import re

UNFINISHED_END = re.compile(
    r"(?:^|\s)(um|uh|erm|er|hm+|mhm|and|or|but|so|that|because|if|when|whether|the|a|an|my|your|his|her|their|"
    r"with|for|from|to|of|in|on|at|by|into|about|like|basically|kind of|sort of|i mean|then|"
    r"also|just|i|you|we|they|it|is|are|was|will|can|could|would|should|want|wanna|gonna|"
    r"must|please\s*,|what|how|where|when|why|which)\s*[,.…\-–]*\s*$", re.I)
QUESTION_START = re.compile(r"^\W*(what|whats|how|where|when|why|which|who|whom|whose|is|are|was|were|do|does|did|"
                            r"have|has|can|could|would|will|should|shall|am|tell|show)\b", re.I)
TRAILING_PUNCT = re.compile(r"(\.\.\.|…|,|-|–|;|:)\s*$")
SENTENCE_END = re.compile(r"[.!?][\"”'»)]?\s*$")


def looks_unfinished(text):
    t = (text or "").strip()
    if not t:
        return True
    if TRAILING_PUNCT.search(t):
        return True
    if UNFINISHED_END.search(t):
        return True
    words = t.split()
    if len(words) <= 2 and not SENTENCE_END.search(t):
        return True
    return False


def looks_like_question(text):
    t = (text or "").strip()
    if not t:
        return False
    if t.endswith("?"):
        return True
    return bool(QUESTION_START.match(t)) and not t.endswith((".", "!"))


class Endpointer:
    def __init__(self, cfg=None):
        c = cfg or {}
        self.base = int(c.get("base_ms", 450))
        self.unfinished_extra = int(c.get("unfinished_extra_ms", 900))
        self.question_bonus = int(c.get("question_bonus_ms", -100))
        self.no_text = int(c.get("no_text_ms", 700))
        self.max = int(c.get("max_ms", 1800))
        self.learn_step = int(c.get("learn_step_ms", 100))
        self.learn_max = int(c.get("learn_max_ms", 600))
        self.learned = 0
        self.turn_model = None
        path = c.get("turn_model") or ""
        if path:
            try:
                self.turn_model = TurnModel(path)
            except Exception as e:  # noqa: BLE001
                print(f"  voice: turn model not loadable ({str(e)[:80]}) — rules", flush=True)

    def wait_for(self, partial_text=None, audio=None):
        """ms to wait after the last speech frame before the utterance is final."""
        if partial_text is None or not str(partial_text).strip():
            wait = self.no_text
        else:
            wait = self.base
            if looks_unfinished(partial_text):
                wait += self.unfinished_extra
            elif looks_like_question(partial_text):
                wait += self.question_bonus
        if self.turn_model is not None and audio is not None:
            p_done = self.turn_model.finished(audio)
            if p_done is not None:
                # confident "finished" shortens, confident "not finished" lengthens
                wait = int(wait * (1.6 - p_done))
        wait += self.learned
        return max(150, min(self.max, wait))

    def learn(self, user_continued):
        """The user went on talking after we closed the turn → wait longer; a clean turn relaxes it."""
        if user_continued:
            self.learned = min(self.learn_max, self.learned + self.learn_step)
        else:
            self.learned = max(0, self.learned - self.learn_step // 2)
        return self.learned

    def reasons(self, partial_text):
        """For the state log: why this wait."""
        if partial_text is None or not str(partial_text).strip():
            return "no text"
        if looks_unfinished(partial_text):
            return "sounds unfinished"
        if looks_like_question(partial_text):
            return "question"
        return "sentence done"


class TurnModel:
    """Hook for an ONNX turn-detection model (e.g. pipecat smart-turn v3): takes up to 8 s of
    16-kHz float audio, returns P(turn finished). Loaded lazily; input names are read from the
    model so a different export still works as long as it is audio in, one probability out."""
    def __init__(self, path):
        import onnxruntime as ort
        import numpy as np
        from voice_config import resolve
        self.np = np
        self.sess = ort.InferenceSession(str(resolve(path)), providers=["CPUExecutionProvider"])
        self.input = self.sess.get_inputs()[0].name

    def finished(self, pcm16k_bytes):
        np = self.np
        x = np.frombuffer(pcm16k_bytes[-16000 * 8:], dtype=np.int16).astype(np.float32) / 32768.0
        try:
            out = self.sess.run(None, {self.input: x[None, :]})[0]
            p = float(np.ravel(out)[-1])
            return max(0.0, min(1.0, p))
        except Exception:  # noqa: BLE001
            return None
