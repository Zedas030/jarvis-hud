"""
Wake word — "Jarvis" instead of an always-open microphone.

JARVIS_WAKE selects the mode:
  off    (default) live mode as before: every utterance is a request
  text   the HUD still hears everything, but only acts when the transcript starts
         with "Jarvis" (Whisper runs on every utterance; nothing to install)
  oww    openWakeWord listens on this PC's microphone for "Hey Jarvis" — fully
         local and light on CPU. On a hit the HUD opens the mic for a few seconds.
         Needs:  .venv\\Scripts\\python.exe -m pip install openwakeword sounddevice
         If that is missing or the mic cannot be opened, it falls back to "text".

After every answer the HUD keeps listening for a moment without the wake word
(follow-up window, JARVIS_FOLLOWUP_S), so "Und morgen?" just works.
"""
import os
import threading
import time

import events

MODE = os.environ.get("JARVIS_WAKE", "off").strip().lower()
if MODE in ("", "0", "aus", "false", "no", "nein", "none"):
    MODE = "off"
elif MODE not in ("off", "text", "oww"):
    MODE = "text"            # "JARVIS_WAKE=Jarvis", "=an", "=1": the obvious wish is the wake word on
WORD_MODEL = os.environ.get("JARVIS_WAKE_MODEL", "hey_jarvis").strip()
THRESHOLD = float(os.environ.get("JARVIS_WAKE_THRESHOLD", "0.5"))
DEVICE = os.environ.get("JARVIS_WAKE_DEVICE", "").strip() or None     # sounddevice name or index
WINDOW_S = float(os.environ.get("JARVIS_WAKE_WINDOW_S", "7"))
FOLLOWUP_S = float(os.environ.get("JARVIS_FOLLOWUP_S", "8"))
STATUS = {"mode": MODE, "listening": False, "error": "", "hits": 0}


def status():
    return dict(STATUS, window_s=WINDOW_S, followup_s=FOLLOWUP_S)


def _fallback(why):
    STATUS.update(mode="text", listening=False, error=why)
    print(f"  Aktivierungswort: {why} — weiter mit Text-Erkennung („Jarvis …“).", flush=True)


def _listen():
    try:
        import numpy as np
        import sounddevice as sd
        import openwakeword
        from openwakeword.model import Model
    except ImportError as e:
        return _fallback(f"Paket fehlt ({e.name}); pip install openwakeword sounddevice")
    try:
        try:
            openwakeword.utils.download_models(model_names=[WORD_MODEL])
        except Exception:  # noqa: BLE001 — already there, or offline with a cached copy
            pass
        model = Model(wakeword_models=[WORD_MODEL], inference_framework="onnx")
    except Exception as e:  # noqa: BLE001
        return _fallback(f"Modell nicht ladbar ({str(e)[:120]})")
    device = int(DEVICE) if DEVICE and DEVICE.isdigit() else DEVICE
    last = 0.0
    while True:
        try:
            with sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=1280, device=device) as stream:
                STATUS.update(listening=True, error="")
                while True:
                    frame, _ = stream.read(1280)
                    scores = model.predict(np.asarray(frame[:, 0], dtype=np.int16))
                    if max(scores.values() or [0]) >= THRESHOLD and time.time() - last > 2:
                        last = time.time()
                        STATUS["hits"] += 1
                        events.publish("wake")
                        if hasattr(model, "reset"):
                            model.reset()
        except Exception as e:  # noqa: BLE001 — mic unplugged or busy: retry, text mode meanwhile
            STATUS.update(listening=False, error=f"Mikrofon: {str(e)[:120]}")
            time.sleep(10)


def start():
    if MODE == "oww":
        threading.Thread(target=_listen, daemon=True).start()
