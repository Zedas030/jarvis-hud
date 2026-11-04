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

With JARVIS_FOLLOWUP_S > 0 the HUD keeps listening for a moment after every answer without the wake word
(follow-up window), so "And tomorrow?" just works. Off by default: nothing counts without "Jarvis".
"""
import os
import pathlib
import threading
import time

import events

MODE = os.environ.get("JARVIS_WAKE", "off").strip().lower()
if MODE in ("", "0", "false", "no", "none"):
    MODE = "off"
elif MODE not in ("off", "text", "oww"):
    MODE = "text"            # "JARVIS_WAKE=Jarvis", "=on", "=1": the obvious wish is the wake word on
WORD_MODEL = os.environ.get("JARVIS_WAKE_MODEL", "hey_jarvis").strip()
THRESHOLD = float(os.environ.get("JARVIS_WAKE_THRESHOLD", "0.5"))
DEVICE = os.environ.get("JARVIS_WAKE_DEVICE", "").strip() or None     # sounddevice name or index
WINDOW_S = float(os.environ.get("JARVIS_WAKE_WINDOW_S", "7"))
FOLLOWUP_S = float(os.environ.get("JARVIS_FOLLOWUP_S", "0"))   # 0 = every request needs "Jarvis"
# The bar for the wake word on the iPad (ui/wakeword.js). Low on purpose: the model knows the
# English "Hey Jarvis" (~0.99), a German "Jarvis" in a sentence ~0.04, alone 0.005-0.06 (seen in
# logs/wake_device.log), talk without it 0.000; the PC checks the words after. 0.05 and 0.03 dropped
# every second "Jarvis". A short clip only needs to clear 0.004 (OnDevice.need in ui/app.js).
LOCAL_THRESHOLD = float(os.environ.get("JARVIS_WAKE_LOCAL_THRESHOLD", "0.02"))
# Which model decides on the iPad. The others that are there run alongside and only show up in
# logs/wake_device.log, so one evening of talking tells which hears this "Jarvis" best.
LOCAL_FILES = {"hey_jarvis": "hey_jarvis_v0.1.onnx", "jarvis_v1": "jarvis_v1.onnx", "jarvis_v2": "jarvis_v2.onnx"}
LOCAL_MODEL = os.environ.get("JARVIS_WAKE_LOCAL_MODEL", "hey_jarvis").strip().lower()
if LOCAL_MODEL not in LOCAL_FILES:
    LOCAL_MODEL = "hey_jarvis"
VENDOR_OWW = pathlib.Path(__file__).resolve().parents[2] / "ui" / "vendor" / "oww-v0.5.1"
STATUS = {"mode": MODE, "listening": False, "error": "", "hits": 0}
# What the wake word on a phone or iPad did (scores, lengths, never words) — its own HUD log is
# out of reach when "Jarvis" is not heard. Kept short: the newest DEVICE_LOG_KEEP lines.
DEVICE_LOG = pathlib.Path(os.environ.get("JARVIS_WAKE_LOG",
                                         pathlib.Path(__file__).resolve().parents[2] / "logs" / "wake_device.log"))
DEVICE_LOG_KEEP = 500
_LOG_LOCK = threading.Lock()


def device_log(device, text):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {str(device or '?')[:30]}  {' '.join(str(text).split())[:200]}\n"
    with _LOG_LOCK:
        DEVICE_LOG.parent.mkdir(parents=True, exist_ok=True)
        try:
            lines = DEVICE_LOG.read_text(encoding="utf-8").splitlines(keepends=True)[-(DEVICE_LOG_KEEP - 1):]
        except FileNotFoundError:
            lines = []
        DEVICE_LOG.write_text("".join(lines) + line, encoding="utf-8")


def local_models():
    """[(name, file)] the iPad loads: the deciding one first, then every other one that was fetched."""
    names = [LOCAL_MODEL] + [n for n in LOCAL_FILES if n != LOCAL_MODEL]
    return [(n, LOCAL_FILES[n]) for n in names if n == LOCAL_MODEL or (VENDOR_OWW / LOCAL_FILES[n]).exists()]


def status():
    return dict(STATUS, window_s=WINDOW_S, followup_s=FOLLOWUP_S, local_threshold=LOCAL_THRESHOLD,
                local_models=[n for n, _ in local_models()])


def _fallback(why):
    STATUS.update(mode="text", listening=False, error=why)
    print(f"  wake word: {why} — carrying on with text detection (“Jarvis …”).", flush=True)


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
        return _fallback(f"model not loadable ({str(e)[:120]})")
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
            STATUS.update(listening=False, error=f"microphone: {str(e)[:120]}")
            time.sleep(10)


def start():
    if MODE == "oww":
        threading.Thread(target=_listen, daemon=True).start()
