"""
Voice for JARVIS.

Step 1 of the build ships deliberately mute: the browser's own speechSynthesis
voice. That is the robotic default the Fish Audio segment replaces.

Step 2 drops a FISH_AUDIO_API_KEY into .env and this module starts serving real
audio from /api/speak instead. The key stays server-side — the browser only ever
receives mp3 bytes, so nothing leaks into devtools or a screen recording.

Contract verified against the live API: TTS is POST /v1/tts with the model in a
header and reference_id choosing the voice; ASR is POST /v1/asr with a multipart
field named `audio`. See .agents/skills/fish-audio-api for the full spec.
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request

TTS_URL = os.environ.get("FISH_AUDIO_TTS_URL", "https://api.fish.audio/v1/tts")
ASR_URL = os.environ.get("FISH_AUDIO_ASR_URL", "https://api.fish.audio/v1/asr")
MODEL = os.environ.get("FISH_AUDIO_MODEL", "s2.1-pro-free")
VOICE_ID = os.environ.get("FISH_AUDIO_VOICE_ID", "").strip()

# ── ElevenLabs (alternative provider) ───────────────────────────
# Used for speech when no Fish Audio key is set, and for listening (Scribe).
# eleven_v3 performs [square bracket] audio tags like Fish does, so the
# delivery-tag style of the replies carries over unchanged.
EL_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
EL_VOICE = os.environ.get("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb").strip()
EL_MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_v3").strip()
EL_STT_MODEL = os.environ.get("ELEVENLABS_STT_MODEL", "scribe_v1").strip()
EL_LANG = os.environ.get("ELEVENLABS_STT_LANG", "deu").strip()

# ── local speech-to-text ────────────────────────────────────────
# Listening is the one thing Fish Audio charges for, and the browser's own
# recogniser needs Google's service — which Brave ships without, so it fails
# with a bare "network". whisper.cpp sidesteps both: offline, free, ~1s for a
# short clip, and it works in any browser.
WHISPER_BIN = os.environ.get("WHISPER_BIN", "").strip() or shutil.which("whisper-cli") \
    or shutil.which("whisper-cpp") or ""
FFMPEG = os.environ.get("FFMPEG_BIN", "").strip() or shutil.which("ffmpeg") or ""


def _whisper_model():
    explicit = os.environ.get("WHISPER_MODEL", "").strip()
    if explicit and os.path.exists(os.path.expanduser(explicit)):
        return os.path.expanduser(explicit)
    for guess in ("~/.clawbot/models/ggml-base.bin",
                  "~/.cache/whisper/ggml-base.bin",
                  "/opt/homebrew/share/whisper-cpp/ggml-base.bin"):
        path = os.path.expanduser(guess)
        if os.path.exists(path):
            return path
    return ""


def whisper_ready():
    return bool(WHISPER_BIN and FFMPEG and _whisper_model())


def stt_kind():
    """Which listener is actually usable right now."""
    forced = os.environ.get("JARVIS_STT", "auto").strip().lower()
    if forced in ("local", "auto") and local_stt_available():
        return "local"
    if forced == "fish":
        return "fish" if fish_available() else "browser"
    if forced == "elevenlabs":
        return "elevenlabs" if EL_KEY else "browser"
    if whisper_ready():
        return "whisper"
    if EL_KEY:
        return "elevenlabs"
    return "fish" if fish_available() else "browser"


def describe_stt():
    kind = stt_kind()
    if kind == "whisper":
        return f"whisper.cpp local ({pathlib.Path(_whisper_model()).name})"
    if kind == "fish":
        return f"Fish Audio ASR ({MODEL})"
    if kind == "elevenlabs":
        return f"ElevenLabs Scribe ({EL_STT_MODEL})"
    if kind == "local":
        return f"Whisper lokal ({_WHISPER.get('name') or LOCAL_STT_MODEL}, kostenlos, offline)"
    return "browser speech recognition"


# Whisper narrates silence. On a near-silent clip the base model reliably
# invents one of these, which would fire a phantom turn in live mode. Deliberately
# NOT blocking yes/yeah/ok/sure — those are real confirmations in normal use.
_GHOSTS = {
    "you", "thank you", "thanks", "thanks for watching", "thank you for watching",
    "thanks for watching everyone", "bye", "goodbye", "bye bye", "please subscribe",
    "subscribe", "the", "uh", "um", "hmm", "mm", "mhm", "ah", "oh", "so", "and",
    "i", "a", "it", "he", "we", "yes yes yes",
}


def transcribe_local(audio, mime="audio/webm"):
    """Browser clip -> 16 kHz mono wav -> whisper.cpp -> text. Never leaves the machine."""
    model = _whisper_model()
    if not (WHISPER_BIN and FFMPEG and model):
        raise RuntimeError("whisper.cpp not available")
    ext = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".mp4",
           "audio/mpeg": ".mp3", "audio/wav": ".wav"}.get(mime.split(";")[0], ".webm")
    with tempfile.TemporaryDirectory() as tmp:
        raw = os.path.join(tmp, "turn" + ext)
        wav = os.path.join(tmp, "turn.wav")
        with open(raw, "wb") as fh:
            fh.write(audio)
        conv = subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", raw,
                               "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav],
                              capture_output=True, text=True, timeout=60)
        if conv.returncode != 0 or not os.path.exists(wav):
            raise RuntimeError(f"audio convert failed: {(conv.stderr or '')[:160]}")
        out = subprocess.run([WHISPER_BIN, "-m", model, "-f", wav, "-nt", "-np",
                              "-t", "4", "-sns",
                              "-l", os.environ.get("WHISPER_LANG", "auto")],
                             capture_output=True, text=True, timeout=180)
        if out.returncode != 0:
            raise RuntimeError(f"whisper failed: {(out.stderr or '')[-160:]}")
        text = " ".join(out.stdout.split()).strip()
        # whisper narrates silence as [BLANK_AUDIO], [Music], (clicking) and
        # friends. Strip every such marker; if nothing real is left, say so with
        # an empty string so live mode simply keeps listening.
        text = re.sub(r"[\[(][^\])]{0,40}[\])]", " ", text)
        text = " ".join(text.split()).strip()
        if len(re.sub(r"[^A-Za-z0-9]", "", text)) < 2:
            return ""
        if re.sub(r"[^a-z ]", "", text.lower()).strip() in _GHOSTS:
            return ""
        return text


def fish_available():
    return bool(os.environ.get("FISH_AUDIO_API_KEY"))


def tts_kind():
    choice = os.environ.get("JARVIS_TTS", "auto").strip().lower()
    if choice == "elevenlabs" and EL_KEY:
        return "elevenlabs"
    if choice == "fish" and fish_available():
        return "fish"
    if choice in ("auto", "edge") and edge_available():
        return "edge"
    if fish_available():
        return "fish"
    return "elevenlabs" if EL_KEY else "browser"


def available():
    if os.environ.get("TTS_CHAIN", "").strip():
        import tts_chain
        return any(a.configured() for a in tts_chain.ADAPTERS.values())
    return tts_kind() != "browser"


def describe():
    if os.environ.get("TTS_CHAIN", "").strip():
        import tts_chain
        usable = [tts_chain.LABELS.get(n, n) for n in tts_chain.CHAIN
                  if n in tts_chain.ADAPTERS and tts_chain.ADAPTERS[n].configured()]
        return "Kette: " + " → ".join(usable)
    kind = tts_kind()
    if kind == "fish":
        return f"Fish Audio · {MODEL}" + (f" · {VOICE_ID[:8]}…" if VOICE_ID else " · default voice")
    if kind == "elevenlabs":
        return f"ElevenLabs · {EL_MODEL} · {EL_VOICE[:8]}…"
    if kind == "edge":
        return f"Microsoft Neural · {EDGE_VOICE} (kostenlos)"
    return "browser speechSynthesis (add FISH_AUDIO_API_KEY or ELEVENLABS_API_KEY for real voice)"


def short_name():
    if os.environ.get("TTS_CHAIN", "").strip():
        import tts_chain
        return tts_chain.status().get("active_label") or "Kette"
    return {"fish": "Fish Audio", "elevenlabs": "ElevenLabs", "edge": "Microsoft"}.get(tts_kind(), "browser")


def _strip_tags(text):
    return " ".join(re.sub(r"\[[^\][]{1,90}\]", " ", text).split())


def speak_elevenlabs(text):
    model = EL_MODEL
    if not model.startswith("eleven_v3"):
        text = _strip_tags(text)   # older models would read the tags aloud
    payload = {"text": text[:2500], "model_id": model,
               # Lower stability = livelier intonation; a little style keeps the dry butler tone.
               "voice_settings": {"stability": float(os.environ.get("ELEVENLABS_STABILITY", "0.4")),
                                  "similarity_boost": 0.8, "style": 0.25, "use_speaker_boost": True}}
    if not model.startswith(("eleven_v3", "eleven_multilingual")):
        payload["language_code"] = os.environ.get("ELEVENLABS_LANG", "de")
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{EL_VOICE}?output_format=mp3_44100_128",
        data=json.dumps(payload).encode(), method="POST",
        headers={"content-type": "application/json", "accept": "audio/mpeg", "xi-api-key": EL_KEY})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ElevenLabs TTS HTTP {e.code}: {e.read(400).decode('utf-8','replace')}") from e


def transcribe_elevenlabs(audio, mime="audio/webm"):
    boundary = "----jarvis" + os.urandom(8).hex()
    b = boundary.encode()
    ext = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "mp4",
           "audio/mpeg": "mp3", "audio/wav": "wav"}.get(mime.split(";")[0], "webm")
    fields = {"model_id": EL_STT_MODEL, "tag_audio_events": "false"}
    if EL_LANG:
        fields["language_code"] = EL_LANG
    parts = []
    for k, v in fields.items():
        parts += [b"--", b, b"\r\n", f'Content-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()]
    parts += [b"--", b, b"\r\n",
              f'Content-Disposition: form-data; name="file"; filename="turn.{ext}"\r\n'.encode(),
              f"Content-Type: {mime}\r\n\r\n".encode(), audio, b"\r\n", b"--", b, b"--\r\n"]
    req = urllib.request.Request(
        "https://api.elevenlabs.io/v1/speech-to-text", data=b"".join(parts), method="POST",
        headers={"content-type": f"multipart/form-data; boundary={boundary}", "xi-api-key": EL_KEY})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            text = (json.loads(r.read()).get("text") or "").strip()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ElevenLabs STT HTTP {e.code}: {e.read(400).decode('utf-8','replace')}") from e
    text = " ".join(re.sub(r"[\[(][^\])]{0,40}[\])]", " ", text).split())
    return "" if len(re.sub(r"\W", "", text)) < 2 else text


def speak_full(text):
    """-> (audio bytes, mime, provider). Uses the provider chain (tts_chain.py)
    when TTS_CHAIN is set, otherwise the single-provider behaviour below."""
    import pronounce
    text = pronounce.apply(text)          # "NAS" -> "Nass", "kWh" -> "Kilowattstunden" ...
    if os.environ.get("TTS_CHAIN", "").strip():
        import tts_chain
        return tts_chain.speak(text)
    return speak(text), "audio/mpeg", tts_kind()


def speak(text):
    """Returns mp3 bytes, or raises. The caller decides what to do on failure."""
    if os.environ.get("TTS_CHAIN", "").strip():
        return speak_full(text)[0]
    if not available():
        raise RuntimeError("no voice key — browser voice is handling this")
    text = (text or "").strip()
    if not text:
        raise ValueError("empty text")
    if tts_kind() == "edge":
        return speak_edge(text)
    if tts_kind() == "elevenlabs":
        try:
            return speak_elevenlabs(text)
        except Exception as e:  # noqa: BLE001
            # Free plan out of credits or service down: keep talking with the free voice.
            if not edge_available():
                raise
            print(f"  ElevenLabs failed, using Microsoft voice: {str(e)[:120]}", flush=True)
            return speak_edge(text)

    payload = {"text": text[:2500], "format": "mp3", "latency": "normal"}
    if VOICE_ID:
        payload["reference_id"] = VOICE_ID

    req = urllib.request.Request(
        TTS_URL, data=json.dumps(payload).encode(), method="POST",
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {os.environ['FISH_AUDIO_API_KEY']}",
            "model": MODEL,
        })
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.read()
    except Exception as e:  # noqa: BLE001
        detail = e.read(400).decode("utf-8", "replace") if isinstance(e, urllib.error.HTTPError) else str(e)
        if not edge_available():
            raise RuntimeError(f"Fish Audio TTS failed: {detail[:200]}") from e
        print(f"  Fish Audio failed, using Microsoft voice: {detail[:120]}", flush=True)
        return speak_edge(text)


def transcribe(audio, mime="audio/webm"):
    """Speech to text. Prefers local whisper.cpp, falls back to Fish Audio ASR."""
    if not audio:
        raise ValueError("empty audio")
    if stt_kind() == "whisper":
        return transcribe_local(audio, mime)
    if stt_kind() == "local":
        return transcribe_whisper_local(audio)
    if stt_kind() == "elevenlabs":
        return transcribe_elevenlabs(audio, mime)
    if not fish_available():
        raise RuntimeError("no local whisper and no FISH_AUDIO_API_KEY")

    boundary = "----jarvis" + os.urandom(8).hex()
    b = boundary.encode()
    ext = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "mp4",
           "audio/mpeg": "mp3", "audio/wav": "wav"}.get(mime.split(";")[0], "webm")
    body = b"".join([
        b"--", b, b"\r\n",
        f'Content-Disposition: form-data; name="audio"; filename="turn.{ext}"\r\n'.encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(), audio, b"\r\n",
        b"--", b, b"--\r\n",
    ])
    req = urllib.request.Request(
        ASR_URL, data=body, method="POST",
        headers={"content-type": f"multipart/form-data; boundary={boundary}",
                 "authorization": f"Bearer {os.environ['FISH_AUDIO_API_KEY']}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return (json.loads(r.read()).get("text") or "").strip()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Fish Audio ASR HTTP {e.code}: {e.read(400).decode('utf-8','replace')}") from e


# ── free providers: Microsoft neural voices + local Whisper ─────
# Speaking via edge-tts (the read-aloud voices of Microsoft Edge): free, no
# account, ~1 s. Listening via faster-whisper locally on the CPU: free,
# offline, ~1 s for a short sentence, no audio leaves the machine.
TTS_CHOICE = os.environ.get("JARVIS_TTS", "auto").strip().lower()      # auto|edge|elevenlabs|fish
EDGE_VOICE = os.environ.get("EDGE_VOICE", "de-DE-ConradNeural").strip()
EDGE_RATE = os.environ.get("EDGE_RATE", "+5%").strip()
LOCAL_STT_MODEL = os.environ.get("WHISPER_LOCAL_MODEL", "small").strip()
LOCAL_STT_LANG = os.environ.get("WHISPER_LOCAL_LANG", "de").strip() or None
# With an NVIDIA card the large model runs faster than "small" on the CPU.
LOCAL_STT_DEVICE = os.environ.get("WHISPER_LOCAL_DEVICE", "auto").strip().lower()   # auto|cuda|cpu
LOCAL_STT_GPU_MODEL = os.environ.get("WHISPER_LOCAL_GPU_MODEL", "large-v3-turbo").strip()
# Beam 5 costs a little time but gets noticeably more words right than greedy.
LOCAL_STT_BEAM = int(os.environ.get("WHISPER_LOCAL_BEAM", "5"))
# Words the user actually uses — Whisper spells names it has been primed with
# far more reliably ("Jarvis" instead of "Javas", "Spotify" instead of "Spottifei").
LOCAL_STT_PROMPT = os.environ.get(
    "WHISPER_LOCAL_PROMPT",
    "Jarvis, Sir, Spotify, Home Assistant, Raspberry Pi, Pi-hole, NAS, ElevenLabs, "
    "Claude, Briefing, Solaranlage.").strip()

_WHISPER = {"model": None}
_WHISPER_LOCK = __import__("threading").Lock()

# Whisper "hears" these in silence or noise (German subtitle credits it was trained on).
_GHOSTS_DE = re.compile(
    r"untertitel|amara\.org|vielen dank (fürs|für's|für das) zuschauen|bis zum nächsten mal|"
    r"swr \d{4}|zdf|copyright|^(danke|tschüss|ja|äh|ähm|hm+)\W*$", re.I)


def edge_available():
    try:
        import edge_tts  # noqa: F401
        return True
    except ImportError:
        return False


def local_stt_available():
    try:
        import faster_whisper  # noqa: F401
        import av  # noqa: F401
        return True
    except ImportError:
        return False


def _cuda_dlls():
    """pip's nvidia-cublas/cudnn wheels put their DLLs in site-packages/nvidia/*/bin,
    where Windows does not look on its own."""
    import glob
    import site
    for sp in site.getsitepackages():
        for d in glob.glob(os.path.join(sp, "nvidia", "*", "bin")):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(d)


def _fw_model():
    with _WHISPER_LOCK:
        if _WHISPER["model"] is None:
            from faster_whisper import WhisperModel
            if LOCAL_STT_DEVICE in ("auto", "cuda"):
                try:
                    _cuda_dlls()
                    import numpy as np
                    m = WhisperModel(LOCAL_STT_GPU_MODEL, device="cuda", compute_type="float16")
                    list(m.transcribe(np.zeros(16000, dtype=np.float32), language="de")[0])  # proves cuBLAS loads
                    _WHISPER.update(model=m, name=f"{LOCAL_STT_GPU_MODEL}, Grafikkarte")
                    return m
                except Exception as e:  # noqa: BLE001
                    print(f"  whisper on GPU failed, using CPU: {str(e)[:120]}", flush=True)
            _WHISPER.update(model=WhisperModel(LOCAL_STT_MODEL, device="cpu", compute_type="int8"),
                            name=f"{LOCAL_STT_MODEL}, CPU")
        return _WHISPER["model"]


def preload_local_stt():
    """Load the Whisper model in the background at startup so the first
    sentence is not slow."""
    if stt_kind() == "local":
        try:
            _fw_model()
        except Exception as e:  # noqa: BLE001
            print(f"  whisper preload failed: {e}", flush=True)


def _decode_16k(audio):
    import io
    import av
    import numpy as np
    out = []
    rs = av.AudioResampler(format="s16", layout="mono", rate=16000)
    with av.open(io.BytesIO(audio)) as c:
        for frame in c.decode(audio=0):
            for f in rs.resample(frame):
                out.append(f.to_ndarray().reshape(-1))
        for f in rs.resample(None):
            out.append(f.to_ndarray().reshape(-1))
    if not out:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(out).astype(np.float32) / 32768.0


def transcribe_whisper_local(audio):
    pcm = _decode_16k(audio)
    if pcm.size < 16000 * 0.3:
        return ""
    segs, _ = _fw_model().transcribe(pcm, language=LOCAL_STT_LANG, beam_size=LOCAL_STT_BEAM,
                                          vad_filter=True, condition_on_previous_text=False,
                                          initial_prompt=LOCAL_STT_PROMPT or None)
    text = " ".join(s.text.strip() for s in segs).strip()
    if not text or _GHOSTS_DE.search(text) and len(text) < 60:
        return ""
    return text if len(re.sub(r"\W", "", text)) >= 2 else ""


def speak_edge(text):
    import asyncio
    import edge_tts
    text = _strip_tags(text)
    if not text:
        raise ValueError("empty text")

    async def run():
        data = bytearray()
        async for chunk in edge_tts.Communicate(text[:3000], EDGE_VOICE, rate=EDGE_RATE).stream():
            if chunk["type"] == "audio":
                data += chunk["data"]
        return bytes(data)

    # Microsoft's endpoint occasionally answers 403 for a single request; a
    # quick retry almost always goes through.
    last = None
    for attempt in range(3):
        try:
            data = asyncio.run(run())
            if data:
                return data
        except Exception as e:  # noqa: BLE001
            last = e
        __import__("time").sleep(0.25 * (attempt + 1))
    raise RuntimeError(f"Microsoft-Stimme nicht erreichbar: {last}")
