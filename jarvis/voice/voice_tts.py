"""
From the model's token stream to playable sentences, with the audio kept per sentence so a pause
can resume at a sentence start without asking the voice provider again.

  Splitter(cfg.tts)      feed(text_so_far, final) -> new chunks (sentences; the first one may be a
                         clause cut at a comma so the voice starts early)
  Sentence               id, text, pcm (24 kHz int16 for the clients), raw provider bytes + mime (for
                         a Sonos), done flag; wait_first() blocks until the first audio exists
  Synth(cfg, out_rate)   one worker: synthesises queued sentences in order through tts_chain
                         (streamed where the provider can), decodes to PCM as it arrives and calls
                         on_audio(sentence, pcm_chunk) / on_done(sentence); cancel() drops the queue

The tone tags ("[dry]") stay in the text handed to the provider — tts_chain strips them for voices
that cannot perform them. pronounce.apply runs here ("NAS" -> "Nass"), as /api/speak did.
"""
import re
import threading
import time

import voice
import voice_dsp
import jarvis_log

log = jarvis_log.get(__name__)

SENTENCE_RE = re.compile(r"[\s\S]*?[.!?…:](?=\s|$)")
TONE_RE = re.compile(r"\[[^\][]{1,90}\]")
CLAUSE_RE = re.compile(r"[\s\S]*?[,;](?=\s)")


def plain(text):
    return " ".join(TONE_RE.sub(" ", text or "").split())


class Splitter:
    def __init__(self, cfg=None):
        c = cfg or {}
        self.first_min = int(c.get("first_chunk_min_chars", 12))
        self.first_max = int(c.get("first_chunk_max_chars", 70))
        self.min_chunk = int(c.get("min_chunk_chars", 28))
        self.upto = 0
        self.first_done = False

    def reset(self):
        self.upto = 0
        self.first_done = False

    def feed(self, full, final=False):
        """Sentences finished since the last call. The first piece of an answer may stop at a comma
        once the sentence is long, so the first sound comes after a clause, not a paragraph."""
        rest = full[self.upto:]
        out, take = [], 0
        for m in SENTENCE_RE.finditer(rest):
            chunk = rest[take:m.end()]
            need = self.first_min if not self.first_done else self.min_chunk
            if len(plain(chunk)) >= need:
                out.append(chunk.strip())
                take = m.end()
                self.first_done = True
        if not self.first_done and not out and len(plain(rest)) >= self.first_max:
            # the longest run of clauses that still fits: "Guten Abend, Sir, die Solaranlage liefert gerade zwei Komma vier Kilowatt,"
            cut = 0
            for m in CLAUSE_RE.finditer(rest):
                if m.start() != cut:
                    break
                if len(plain(rest[:m.end()])) > self.first_max:
                    break
                cut = m.end()
            if cut and len(plain(rest[:cut])) >= self.first_min:
                out.append(rest[:cut].strip())
                take = cut
                self.first_done = True
        if final and plain(rest[take:]).strip():
            out.append(rest[take:].strip())
            take = len(rest)
        self.upto += take
        return [c for c in out if plain(c)]


class Sentence:
    def __init__(self, sid, text):
        self.id, self.text = sid, text
        self.pcm = bytearray()
        self.raw = bytearray()
        self.mime = ""
        self.provider = ""
        self.done = False
        self.error = ""
        self.started_at = None
        self.first_audio_at = None
        self.ev = threading.Event()        # set on the first audio or on error/done
        self.ev_done = threading.Event()

    def wait_first(self, timeout):
        return self.ev.wait(timeout)

    def duration_s(self, rate):
        return len(self.pcm) / 2 / rate


class Synth:
    def __init__(self, cfg=None, out_rate=24000, on_audio=None, on_done=None, speak_stream=None):
        c = cfg or {}
        self.out_rate = out_rate
        self.timeout = float(c.get("provider_timeout_s", 20))
        self.on_audio = on_audio or (lambda s, pcm: None)
        self.on_done = on_done or (lambda s: None)
        self.speak_stream = speak_stream or self._chain_stream
        self.queue = []
        self.cond = threading.Condition()
        self.generation = 0
        self.alive = True
        self.current = None
        threading.Thread(target=self._loop, daemon=True, name="voice-tts").start()

    @staticmethod
    def _chain_stream(text):
        """-> (chunks, mime, provider) from the configured voice chain."""
        import pronounce
        text = pronounce.apply(text)
        if __import__("os").environ.get("TTS_CHAIN", "").strip():
            import tts_chain
            return tts_chain.speak_stream(text)
        return voice.speak_stream(text)

    def submit(self, sentence):
        with self.cond:
            sentence.generation = self.generation
            self.queue.append(sentence)
            self.cond.notify()
        return sentence

    def cancel(self):
        """Drop everything queued; the sentence being synthesised finishes quietly in the background."""
        with self.cond:
            self.generation += 1
            self.queue.clear()
            cur = self.current
        if cur is not None:
            cur.cancelled = True

    def stop(self):
        with self.cond:
            self.alive = False
            self.cond.notify()

    def _loop(self):
        while True:
            with self.cond:
                while self.alive and not self.queue:
                    self.cond.wait(0.2)
                if not self.alive:
                    return
                s = self.queue.pop(0)
                self.current = s
            try:
                self._one(s)
            except Exception as e:  # noqa: BLE001
                s.error = str(e)[:160]
                log.warning("Stimme fehlgeschlagen: %s", s.error)
            finally:
                s.done = True
                s.ev.set()
                s.ev_done.set()
                with self.cond:
                    self.current = None
                try:
                    self.on_done(s)
                except Exception as e:  # noqa: BLE001
                    log.warning("on_done failed: %s", str(e)[:100])

    def _one(self, s):
        s.started_at = time.time()
        chunks, mime, provider = self.speak_stream(s.text)
        s.mime, s.provider = mime, provider
        dec = voice_dsp.Decoder(mime, self.out_rate)
        for chunk in chunks:
            if getattr(s, "cancelled", False):
                close = getattr(chunks, "close", None)
                if close:
                    close()
                return
            if not chunk:
                continue
            s.raw += chunk
            pcm = dec.feed(chunk)
            if pcm:
                self._emit(s, pcm)
        tail = dec.flush()
        if tail:
            self._emit(s, tail)

    def _emit(self, s, pcm):
        if s.first_audio_at is None:
            s.first_audio_at = time.time()
        s.pcm += pcm
        s.ev.set()
        if not getattr(s, "cancelled", False):
            self.on_audio(s, pcm)
