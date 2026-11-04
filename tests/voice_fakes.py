"""
Stand-ins for the voice engine tests and the scenario script: a recogniser that answers from a
list, a model run that streams a fixed answer, a voice that renders silence of a sentence's length,
and a client that records everything the engine sends. No network, no Whisper, no Piper.
"""
import io
import json
import math
import struct
import threading
import time

import voice_config
import voice_engine
import voice_tts


def tone(ms, rate=16000, freq=220.0, amp=0.5):
    """Loud periodic sound: the energy VAD takes it for speech."""
    n = int(rate * ms / 1000)
    return struct.pack(f"<{n}h", *(int(amp * 32767 * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)))


def noise_burst(ms, rate=16000, amp=0.4):
    import random
    rnd = random.Random(7)
    n = int(rate * ms / 1000)
    return struct.pack(f"<{n}h", *(int(amp * 32767 * (rnd.random() * 2 - 1)) for i in range(n)))


def silence(ms, rate=16000):
    return bytes(2 * int(rate * ms / 1000))


class FakeRecognizer:
    available = True

    def __init__(self, finals=(), partials=(), partials_enabled=False):
        self.finals = list(finals)
        self.partials = list(partials)
        self.partials_enabled = partials_enabled
        self.calls = []
        self.last_partial = ""

    def final(self, pcm):
        self.calls.append(("final", len(pcm)))
        return self.finals.pop(0) if self.finals else ""

    def partial(self, pcm):
        if self.partials:
            self.last_partial = self.partials.pop(0)
        return self.last_partial


class FakeResponse:
    """What urllib would hand back for /api/run: iterable NDJSON lines."""
    def __init__(self, events, delay=0.0):
        self.lines = [json.dumps(e) + "\n" for e in events]
        self.delay = delay
        self.closed = False

    def __iter__(self):
        for line in self.lines:
            if self.closed:
                return
            if self.delay:
                time.sleep(self.delay)
            yield line

    def read(self):
        return b"{}"

    def close(self):
        self.closed = True


def answer_events(text, chunk=12, latency_ms=0):
    """A model answer as deltas, like /api/run streams it."""
    evs = [{"t": "route", "model": "fake", "groups": [], "reason": "test"}]
    for i in range(0, len(text), chunk):
        evs.append({"t": "delta", "text": text[i:i + chunk]})
    evs.append({"t": "complete", "ms": latency_ms})
    return evs


def fake_speak_stream(seconds_per_char=0.04, rate=24000):
    def speak(text):
        n = int(rate * max(0.3, len(voice_tts.plain(text)) * seconds_per_char))
        pcm = bytes(2 * n)
        half = len(pcm) // 2 - (len(pcm) // 2) % 2
        return iter([pcm[:half], pcm[half:]]), f"audio/pcm;rate={rate}", "fake"
    return speak


class Client:
    """Records the engine's output and plays the audio 'instantly': every audio_end is reported
    back as ended, so the server sees the sentence as spoken. `hold` keeps a sentence 'playing'."""
    def __init__(self):
        self.events = []
        self.audio = []
        self.session = None
        self.hold = False
        self.lock = threading.Lock()
        self.current = None          # the sentence 'playing' right now
        self.queue = []

    def send(self, obj):
        with self.lock:
            self.events.append(obj)
        t = obj.get("t")
        # like the worklet: a sentence is 'playing' once the one before has ended, not when its bytes arrive
        if t == "audio_begin":
            if self.current is None:
                self.current = obj["id"]
                if self.session:
                    self.session.client_event({"t": "playing", "id": obj["id"]})
            elif obj["id"] not in self.queue:
                self.queue.append(obj["id"])
        elif t == "audio_end" and self.session and not self.hold:
            self.session.client_event({"t": "ended", "id": obj["id"]})
            if self.current == obj["id"]:
                self.current = self.queue.pop(0) if self.queue else None
                if self.current is not None:
                    self.session.client_event({"t": "playing", "id": self.current})
        elif t == "flush":
            self.current, self.queue = None, []

    def send_audio(self, data):
        with self.lock:
            self.audio.append(len(data))

    def of(self, t):
        with self.lock:
            return [e for e in self.events if e.get("t") == t]

    def states(self):
        return [e["state"] for e in self.of("state")]

    def wait_state(self, state, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            if self.session and self.session.machine.state == state:
                return True
            time.sleep(0.01)
        return False

    def wait_event(self, t, timeout=5.0, n=1):
        end = time.time() + timeout
        while time.time() < end:
            if len(self.of(t)) >= n:
                return self.of(t)
            time.sleep(0.01)
        return self.of(t)


TEST_OVERRIDES = {
    "engine": {"state_log": "", "preroll_ms": 100, "debug_audio": False},
    "vad": {"model": "models/does-not-exist.onnx", "min_speech_ms": 100, "start_threshold": 0.5, "stop_threshold": 0.3},
    "endpoint": {"base_ms": 300, "unfinished_extra_ms": 600, "no_text_ms": 400, "max_ms": 1200,
                 "continue_window_ms": 1500, "partial_every_ms": 50},
    "barge": {"duck_ms": 40, "confirm_ms": 120, "clip_silence_ms": 200, "clip_max_ms": 3000, "resume_after_ms": 500},
    "tts": {"first_chunk_min_chars": 12, "first_chunk_max_chars": 70, "min_chunk_chars": 20, "gap_ms": 0},
    "llm": {"thinking_cue_after_ms": 0},
    "metrics": {"file": ""},
}


def make_session(finals=(), partials=(), answer="The light is off. Anything else?", partials_enabled=False,
                 speak_stream=None, overrides=None, run_delay=0.0):
    """A Session wired to the fakes. Returns (session, client, shared)."""
    cfg = voice_config.make({**TEST_OVERRIDES, **(overrides or {})})
    shared = voice_engine.Shared(port=1, token="t", cfg=cfg)
    shared.rec = FakeRecognizer(finals, partials, partials_enabled)
    client = Client()
    session = voice_engine.Session(shared, client.send, client.send_audio, device="test")
    client.session = session
    session.synth.speak_stream = speak_stream or fake_speak_stream()
    session.posts = []

    def fake_post(path, body, stream=False, timeout=30):
        session.posts.append((path, body))
        if path == "/api/run":
            text = answer if isinstance(answer, str) else answer.pop(0)
            return FakeResponse(answer_events(text), delay=run_delay)
        return {}
    session._post = fake_post
    return session, client, shared


def feed(session, pcm, frame_ms=20, realtime=False, rate=16000):
    """Hands PCM to the session frame by frame; realtime=True sleeps like a microphone would."""
    n = int(rate * frame_ms / 1000) * 2
    for i in range(0, len(pcm) - n + 1, n):
        session.feed(pcm[i:i + n])
        if realtime:
            time.sleep(frame_ms / 1000.0)


def feed_ms(session, pcm, frame_ms=20):
    """Feed with real time passing (the engine's timers use the clock)."""
    feed(session, pcm, frame_ms, realtime=True)
