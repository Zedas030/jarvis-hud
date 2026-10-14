"""
Local GPU voice for JARVIS: Chatterbox Multilingual on a local NVIDIA GPU.

Runs in its own environment (.venv-tts, PyTorch for CUDA 12.8) so it cannot
break Whisper or the main server. tts_chain.py starts it on demand and talks
to it over HTTP:

    POST /tts   {"text": "..."}  -> audio/wav
    GET  /health                 -> {"ok": true, "ready": bool}

The voice comes from models/voice/reference.wav (default: a clip of the Piper
"Thorsten" voice, whose recordings are public domain). Replace that file with
another ~10 s clean recording you have the rights to, to change the voice.
"""
import io
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("LOCAL_TTS_PORT", "8730"))
REFERENCE = os.environ.get("LOCAL_TTS_REFERENCE", os.path.join(ROOT, "models", "voice", "reference.wav"))
EXAGGERATION = float(os.environ.get("LOCAL_TTS_EXAGGERATION", "0.4"))   # 0.5 = neutral, lower = calmer
CFG = float(os.environ.get("LOCAL_TTS_CFG", "0.5"))

STATE = {"model": None, "ready": False, "error": None}
GPU = threading.Lock()


def load():
    try:
        import torch
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        device = "cuda" if torch.cuda.is_available() else "cpu"
        t = time.time()
        m = ChatterboxMultilingualTTS.from_pretrained(device=device)
        m.prepare_conditionals(REFERENCE, exaggeration=EXAGGERATION)
        STATE.update(model=m, ready=True)
        print(f"[local-tts] Chatterbox bereit auf {device} ({time.time() - t:.1f} s)", flush=True)
    except Exception as e:  # noqa: BLE001
        STATE["error"] = str(e)[:300]
        print(f"[local-tts] Laden fehlgeschlagen: {e}", flush=True)


def synth(text):
    import soundfile as sf
    with GPU:
        wav = STATE["model"].generate(text, language_id="de", exaggeration=EXAGGERATION, cfg_weight=CFG)
    buf = io.BytesIO()
    sf.write(buf, wav.squeeze(0).cpu().numpy(), STATE["model"].sr, format="WAV")
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, json.dumps({"ok": True, "ready": STATE["ready"], "error": STATE["error"]}).encode(),
                              "application/json")
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path != "/tts" or self.client_address[0] != "127.0.0.1":
            return self._send(404, b"not found", "text/plain")
        if not STATE["ready"]:
            return self._send(503, b"not ready", "text/plain")
        try:
            n = int(self.headers.get("Content-Length", 0))
            text = (json.loads(self.rfile.read(n) or b"{}").get("text") or "").strip()[:600]
            if not text:
                return self._send(400, b"empty", "text/plain")
            self._send(200, synth(text), "audio/wav")
        except Exception as e:  # noqa: BLE001
            self._send(500, str(e)[:300].encode(), "text/plain")


if __name__ == "__main__":
    threading.Thread(target=load, daemon=True).start()
    print(f"[local-tts] lauscht auf 127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
