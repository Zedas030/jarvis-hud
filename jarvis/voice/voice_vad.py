"""
Voice activity detection for the engine: Silero VAD v5 (MIT) through the onnxruntime that is
already installed, on 16 kHz mono frames. Without the model file (models/silero_vad.onnx, see
scripts/fetch_voice_models.py) an energy detector with a calibrated floor takes over, so the
engine always runs — only less sure of itself in a noisy room.

    vad = Vad(cfg.vad)
    p = vad.probability(frame_int16_bytes)   # 0..1 per 20-ms frame (Silero needs 32 ms: internal buffer)
    vad.reset()                              # between utterances / sessions

Silero's model wants 512-sample windows plus a 64-sample context from the window before, and a
recurrent state; the class keeps both. A 20-ms frame (320 samples) therefore yields a probability
every 32 ms, and in between the last value is repeated — fine for a 150-ms decision.
"""
import array
import math
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
WINDOW = 512          # samples per Silero step at 16 kHz
CONTEXT = 64


def _to_floats(frame):
    a = array.array("h")
    a.frombytes(frame[: len(frame) - len(frame) % 2])
    return a


def rms(frame):
    a = _to_floats(frame)
    if not a:
        return 0.0
    return math.sqrt(sum(x * x for x in a) / len(a)) / 32768.0


class EnergyVad:
    """Fallback: speech = level clearly above a slowly adapting floor."""
    kind = "energy"

    def __init__(self, factor=3.0, floor=0.004, rate=0.01):
        self.factor, self.floor, self.rate = factor, floor, rate
        self.ambient = floor

    def probability(self, frame):
        level = rms(frame)
        # the floor follows quiet moments only, so speech itself does not lift it
        if level < self.ambient * 1.5:
            self.ambient += (level - self.ambient) * self.rate
        bar = max(self.ambient * self.factor, self.floor * 2)
        if level <= bar * 0.5:
            return 0.0
        return max(0.0, min(1.0, (level - bar * 0.5) / (bar * 1.5)))

    def reset(self):
        pass


class SileroVad:
    kind = "silero"

    def __init__(self, model_path):
        import numpy as np
        import onnxruntime as ort
        self.np = np
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 3
        self.sess = ort.InferenceSession(str(model_path), opts, providers=["CPUExecutionProvider"])
        self.sr = np.array(16000, dtype=np.int64)
        self.reset()

    def reset(self):
        np = self.np
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros(CONTEXT, dtype=np.float32)
        self.buf = np.zeros(0, dtype=np.float32)
        self.last = 0.0

    def probability(self, frame):
        np = self.np
        pcm = np.frombuffer(frame[: len(frame) - len(frame) % 2], dtype=np.int16).astype(np.float32) / 32768.0
        self.buf = np.concatenate([self.buf, pcm]) if self.buf.size else pcm
        while self.buf.size >= WINDOW:
            chunk, self.buf = self.buf[:WINDOW], self.buf[WINDOW:]
            x = np.concatenate([self.context, chunk])[None, :]
            out, self.state = self.sess.run(None, {"input": x, "state": self.state, "sr": self.sr})
            self.context = chunk[-CONTEXT:]
            self.last = float(out[0][0])
        return self.last


class Vad:
    """The detector the engine uses, Silero when the model is there."""
    def __init__(self, cfg=None, model_path=None):
        path = pathlib.Path(model_path or (cfg or {}).get("model", "models/silero_vad.onnx"))
        if not path.is_absolute():
            path = ROOT / path
        self.impl = None
        if path.exists():
            try:
                self.impl = SileroVad(path)
            except Exception as e:  # noqa: BLE001 — onnxruntime missing or a broken file
                print(f"  voice: Silero VAD nicht ladbar ({str(e)[:80]}) — Pegel-VAD", flush=True)
        if self.impl is None:
            self.impl = EnergyVad()
        self.kind = self.impl.kind

    def probability(self, frame):
        return self.impl.probability(frame)

    def reset(self):
        self.impl.reset()


class Tracker:
    """Turns per-frame probabilities into speech/silence with hysteresis and timing.

        tr = Tracker(start=0.55, stop=0.35, frame_ms=20)
        ev = tr.feed(p)     # None | "start" | "stop"
        tr.speaking, tr.speech_ms, tr.silence_ms
    """
    def __init__(self, start, stop, frame_ms=20, min_speech_ms=0):
        self.start_t, self.stop_t, self.frame_ms = start, stop, frame_ms
        self.min_speech_ms = min_speech_ms
        self.reset()

    def reset(self):
        self.speaking = False
        self.speech_ms = 0          # speech so far in this utterance
        self.silence_ms = 0         # silence since the last speech frame
        self.run_ms = 0             # consecutive frames over the start threshold

    def feed(self, p):
        if not self.speaking:
            if p >= self.start_t:
                self.run_ms += self.frame_ms
                if self.run_ms >= max(self.frame_ms, self.min_speech_ms):
                    self.speaking = True
                    self.speech_ms = self.run_ms
                    self.silence_ms = 0
                    return "start"
            else:
                self.run_ms = 0
            return None
        if p >= self.stop_t:
            self.speech_ms += self.frame_ms
            self.silence_ms = 0
        else:
            self.silence_ms += self.frame_ms
        return None

    def end(self):
        """The caller decided the utterance is over."""
        self.reset()
        return "stop"
