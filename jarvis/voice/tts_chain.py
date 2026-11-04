"""
Text-to-speech provider chain.

Every provider is an adapter with the same interface:

    configured()  -> bool              key/model present?
    synth(text)   -> (bytes, mime)     raise Exhausted / Transient on failure
    balance()     -> dict | None       remaining quota, if the provider can tell
    supports_tags                      performs [dry]-style delivery tags (else stripped)

The order comes from TTS_CHAIN in .env. On each sentence the first usable
provider speaks:

  - Exhausted (402, quota/credit errors, 401/403, a balance API saying "empty"):
    the provider is blocked until its reset date — persisted in state.json, so a
    restart does not hammer an empty account — and skipped until then. After the
    reset it is tried first again automatically.
  - Transient (network, timeout, 5xx, plain rate limit): only this sentence moves
    on to the next provider; nothing is blocked.

The same sentence is re-synthesised by the next provider, so nothing is cut off.
Pure code — no LLM tokens involved.
"""
import base64
import calendar
import datetime as dt
import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from xml.sax.saxutils import escape

import commands
import hostos

CHAIN = [p.strip().lower() for p in os.environ.get(
    "TTS_CHAIN", "fish,elevenlabs,local,azure,google,edge").split(",") if p.strip()]
ANNOUNCE = os.environ.get("TTS_ANNOUNCE_SWITCH", "1").strip() not in ("0", "false", "no", "off")
LABELS = {"fish": "Fish Audio", "elevenlabs": "ElevenLabs", "local": "lokale Stimme",
          "azure": "Azure", "google": "Google", "edge": "Microsoft Edge"}
AUTH_BLOCK_S = 6 * 3600          # wrong/expired key: retry a few hours later
UNKNOWN_RESET_S = 24 * 3600      # empty, but the provider gives no reset date
_LOCK = threading.Lock()
_TAG = re.compile(r"\[[^\][]{1,90}\]")


class Exhausted(Exception):
    def __init__(self, reason, until=None):
        super().__init__(reason)
        self.reason, self.until = reason, until


class Transient(Exception):
    pass


def _strip_tags(text):
    return " ".join(_TAG.sub(" ", text).split())


def _next_month():
    now = dt.datetime.now()
    y, m = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return dt.datetime(y, m, 1).timestamp()


def _http(req, timeout=30):
    """Run a request; turn HTTP problems into Exhausted / Transient."""
    r = _open(req, timeout)
    try:
        return r.read(), r.headers
    except (TimeoutError, OSError) as e:
        raise Transient(f"Netzwerk: {e}")
    finally:
        r.close()


def _open(req, timeout=30):
    """Like _http, but hands back the open response for reading as it arrives."""
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        body = e.read(400).decode("utf-8", "replace")
        low = body.lower()
        if e.code == 402 or re.search(r"quota|credit|insufficient|exceed|payment|balance|billing", low):
            raise Exhausted(f"HTTP {e.code}: {body[:120]}")
        if e.code in (401, 403):
            raise Exhausted(f"Zugang abgelehnt (HTTP {e.code}) — Key prüfen", time.time() + AUTH_BLOCK_S)
        raise Transient(f"HTTP {e.code}: {body[:120]}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise Transient(f"Netzwerk: {e}")


# ── adapters ──────────────────────────────────────────────────
class Fish:
    name, supports_tags = "fish", True

    def __init__(self):
        self.key = os.environ.get("FISH_AUDIO_API_KEY", "").strip()
        self.model = os.environ.get("FISH_AUDIO_MODEL", "s2.1-pro-free").strip()
        self.voice = os.environ.get("FISH_AUDIO_VOICE_ID", "").strip()
        self.latency = os.environ.get("FISH_AUDIO_LATENCY", "balanced").strip()   # 'normal' took ~8 s

    def configured(self):
        return bool(self.key)

    def synth(self, text):
        payload = {"text": text[:2500], "format": "mp3", "latency": self.latency}
        if self.voice:
            payload["reference_id"] = self.voice
        req = urllib.request.Request("https://api.fish.audio/v1/tts", data=json.dumps(payload).encode(),
                                     method="POST", headers={"authorization": f"Bearer {self.key}",
                                                             "content-type": "application/json", "model": self.model})
        data, _ = _http(req, timeout=40)
        return data, "audio/mpeg"

    def stream(self, text):
        """Like synth, but yields the audio as Fish produces it: the first bytes come
        after ~0.7 s, the whole clip only after ~2.5 s. The first chunk is read here,
        so a failing request still moves on to the next provider."""
        payload = {"text": text[:2500], "format": "mp3", "latency": self.latency}
        if self.voice:
            payload["reference_id"] = self.voice
        req = urllib.request.Request("https://api.fish.audio/v1/tts", data=json.dumps(payload).encode(),
                                     method="POST", headers={"authorization": f"Bearer {self.key}",
                                                             "content-type": "application/json", "model": self.model})
        r = _open(req, timeout=40)
        try:
            first = r.read1(16384)
        except (TimeoutError, OSError) as e:
            r.close()
            raise Transient(f"Netzwerk: {e}")
        if not first:
            r.close()
            raise Transient("leere Antwort")

        def chunks():
            try:
                yield first
                while True:
                    c = r.read1(16384)
                    if not c:
                        return
                    yield c
            finally:
                r.close()
        return chunks(), "audio/mpeg"

    def balance(self):
        h = {"authorization": f"Bearer {self.key}"}
        out = {}
        for path, key in (("/wallet/self/package", "package"), ("/wallet/self/api-credit", "credit")):
            try:
                data, _ = _http(urllib.request.Request("https://api.fish.audio" + path, headers=h), timeout=40)
                out[key] = json.loads(data)
            except (Exhausted, Transient, ValueError):
                pass
        if not out:
            return None
        pkg, cred = out.get("package") or {}, out.get("credit") or {}
        res = {"package_left": pkg.get("balance"), "package_total": pkg.get("total"),
               "credit_usd": float(cred.get("credit") or 0)}
        # The free model does not draw on either; only a paid model can run "empty".
        res["empty"] = (not self.model.endswith("-free")
                        and (pkg.get("balance") or 0) <= 0 and res["credit_usd"] <= 0)
        res["text"] = (f"Paket {pkg.get('balance')}/{pkg.get('total')}, Guthaben {abs(res['credit_usd']):.2f} $"
                       + (" · Modell kostenlos" if self.model.endswith("-free") else ""))
        return res


class ElevenLabs:
    name, supports_tags = "elevenlabs", True

    def __init__(self):
        self.key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
        self.voice = os.environ.get("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb").strip()
        self.model = os.environ.get("ELEVENLABS_MODEL", "eleven_flash_v2_5").strip()
        if not self.model.startswith("eleven_v3"):
            self.supports_tags = False          # older models would read the tags aloud

    def configured(self):
        return bool(self.key)

    def synth(self, text):
        b = _BAL.get(self.name)
        if b and b.get("left") is not None and b["left"] < len(text):
            raise Exhausted(f"Nur noch {b['left']} Zeichen frei", b.get("reset"))
        req = urllib.request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice}?output_format=mp3_44100_128",
            data=json.dumps({"text": text[:2500], "model_id": self.model}).encode(), method="POST",
            headers={"content-type": "application/json", "accept": "audio/mpeg", "xi-api-key": self.key})
        try:
            data, _ = _http(req, timeout=40)
        except Exhausted as e:
            if e.until is None and b:
                e.until = b.get("reset")
            raise
        if b and b.get("left") is not None:
            b["left"] -= len(text)
        return data, "audio/mpeg"

    def balance(self):
        req = urllib.request.Request("https://api.elevenlabs.io/v1/user/subscription", headers={"xi-api-key": self.key})
        try:
            d = json.loads(_http(req, timeout=20)[0])
        except (Exhausted, Transient, ValueError):
            return None
        left = int(d.get("character_limit", 0)) - int(d.get("character_count", 0))
        reset = d.get("next_character_count_reset_unix")
        return {"left": left, "limit": d.get("character_limit"), "reset": reset, "empty": left <= 0,
                "text": f"{left:,} von {d.get('character_limit', 0):,} Zeichen".replace(",", ".")
                        + (f", Reset {dt.datetime.fromtimestamp(reset):%d.%m.}" if reset else "")}


class Local:
    """Piper on this PC (de_DE thorsten) — unlimited, offline. A GPU model can
    later replace it behind the same interface (LOCAL_TTS_ENGINE)."""
    name, supports_tags = "local", False
    _voice, _vlock = None, threading.Lock()

    def __init__(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.model = os.environ.get("LOCAL_TTS_MODEL", os.path.join(root, "models", "piper", "de_DE-thorsten-high.onnx"))
        self.speed = float(os.environ.get("LOCAL_TTS_SPEED", "0.95"))   # <1 = faster speech

    def configured(self):
        try:
            import piper  # noqa: F401
        except ImportError:
            return False
        return os.path.exists(self.model)

    def synth(self, text):
        # 1st choice: Chatterbox on the GPU (local_tts_server.py), if it is up and loaded.
        url = os.environ.get("LOCAL_TTS_URL", "").strip()
        if url and _GPU["ready"] and not _GPU.get("paused"):
            _GPU["last_use"] = time.time()
            try:
                req = urllib.request.Request(url.rstrip("/") + "/tts", data=json.dumps({"text": text}).encode(),
                                             method="POST", headers={"content-type": "application/json"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    return r.read(), "audio/wav"
            except Exception as e:  # noqa: BLE001 — fall back to Piper for this sentence
                print(f"  GPU-Stimme fehlgeschlagen, Piper übernimmt: {str(e)[:100]}", flush=True)
        return self._piper(text)

    def stream(self, text):
        """Piper sentence by sentence as raw PCM while it is still speaking: the first chunk arrives
        after ~0.1 s instead of the whole clip. The GPU voice (Chatterbox) has no stream and goes
        through synth(); the engine then gets the clip as one piece."""
        url = os.environ.get("LOCAL_TTS_URL", "").strip()
        if url and _GPU["ready"] and not _GPU.get("paused"):
            data, mime = self.synth(text)
            return iter([data]), mime
        try:
            from piper import PiperVoice, SynthesisConfig
        except ImportError as e:
            raise Transient(f"Piper: {e}")
        with Local._vlock:
            if Local._voice is None:
                Local._voice = PiperVoice.load(self.model)
        try:
            import daytime
            speed = self.speed * daytime.speed_factor()
        except Exception:  # noqa: BLE001
            speed = self.speed
        rate = int(getattr(getattr(Local._voice, "config", None), "sample_rate", 22050) or 22050)

        def chunks():
            with Local._vlock:
                for c in Local._voice.synthesize(text, syn_config=SynthesisConfig(length_scale=speed)):
                    yield c.audio_int16_bytes
        gen = chunks()
        try:
            first = next(gen)
        except StopIteration:
            raise Transient("Piper: leer")
        except Exception as e:  # noqa: BLE001
            raise Transient(f"Piper: {e}")

        def rest():
            yield first
            yield from gen
        return rest(), f"audio/pcm;rate={rate}"

    def _piper(self, text):
        import wave
        try:
            from piper import PiperVoice, SynthesisConfig
            with Local._vlock:
                if Local._voice is None:
                    Local._voice = PiperVoice.load(self.model)
                buf = io.BytesIO()
                try:
                    import daytime
                    speed = self.speed * daytime.speed_factor()     # a little slower late in the evening
                except Exception:  # noqa: BLE001
                    speed = self.speed
                with wave.open(buf, "wb") as w:
                    Local._voice.synthesize_wav(text, w, syn_config=SynthesisConfig(length_scale=speed))
            return buf.getvalue(), "audio/wav"
        except Exception as e:  # noqa: BLE001
            raise Transient(f"Piper: {e}")

    def balance(self):
        return {"text": ("GPU (Chatterbox)" if _GPU["ready"] else "Piper (CPU)") + ", unbegrenzt", "empty": False}


# ── local GPU voice server (Chatterbox in .venv-tts) ──────────
_GPU = {"ready": False, "proc": None, "paused": False, "last_use": 0.0}


def _gpu_start(root, py):
    import subprocess
    log = open(os.path.join(root, "logs", "local_tts.log"), "a", encoding="utf-8")
    _GPU["proc"] = subprocess.Popen([py, os.path.join(root, "jarvis", "voice", "local_tts_server.py")], cwd=root,
                                    stdout=log, stderr=subprocess.STDOUT,
                                    **hostos.QUIET)


def gpu_pause():
    """Resource manager: stop the GPU voice to free graphics memory (Piper speaks meanwhile).
    Only a process Jarvis started himself, and never while it is speaking. True if stopped."""
    p = _GPU.get("proc")
    if p is None or p.poll() is not None or time.time() - _GPU.get("last_use", 0) < 20:
        return False
    _GPU.update(paused=True, ready=False)
    p.terminate()
    try:
        p.wait(timeout=10)
    except Exception:  # noqa: BLE001
        p.kill()
    return True


def gpu_resume():
    _GPU["paused"] = False                       # _gpu_watch notices and starts it again


def _gpu_watch():
    """Start local_tts_server.py once if its environment exists, then keep
    track of whether the model is loaded (first start downloads ~3 GB)."""
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    py = hostos.venv_python(root, ".venv-tts")
    url = os.environ.get("LOCAL_TTS_URL", "").strip().rstrip("/")
    if not url or not os.path.exists(py):
        return

    def health():
        try:
            with urllib.request.urlopen(url + "/health", timeout=3) as r:
                return json.loads(r.read())
        except Exception:  # noqa: BLE001
            return None

    if health() is None:
        _gpu_start(root, py)
    while True:
        if _GPU.get("paused"):
            _GPU["ready"] = False
            time.sleep(5)
            continue
        h = health()
        if h is None and _GPU.get("proc") is not None and _GPU["proc"].poll() is not None:
            _gpu_start(root, py)                 # resumed after a pause (or it died): start it again
        _GPU["ready"] = bool(h and h.get("ready"))
        time.sleep(5 if not _GPU["ready"] else 60)


class Azure:
    name, supports_tags = "azure", False

    def __init__(self):
        self.key = os.environ.get("AZURE_SPEECH_KEY", "").strip()
        self.region = os.environ.get("AZURE_SPEECH_REGION", "westeurope").strip()
        self.voice = os.environ.get("AZURE_VOICE", "de-DE-FlorianMultilingualNeural").strip()

    def configured(self):
        return bool(self.key)

    def synth(self, text):
        ssml = (f"<speak version='1.0' xml:lang='de-DE'><voice name='{self.voice}'>"
                f"{escape(text[:2500])}</voice></speak>")
        req = urllib.request.Request(
            f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1", data=ssml.encode("utf-8"),
            method="POST", headers={"Ocp-Apim-Subscription-Key": self.key, "Content-Type": "application/ssml+xml",
                                    "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
                                    "User-Agent": "JARVIS-HUD"})
        try:
            data, _ = _http(req)
        except Exhausted as e:
            e.until = e.until or _next_month()     # F0 quota resets monthly
            raise
        return data, "audio/mpeg"

    def balance(self):
        return {"text": "Free Tier 0,5 Mio. Zeichen/Monat (keine Restanzeige)", "empty": False}


class Google:
    name, supports_tags = "google", False

    def __init__(self):
        self.key = os.environ.get("GOOGLE_TTS_API_KEY", "").strip()
        self.voice = os.environ.get("GOOGLE_TTS_VOICE", "de-DE-Chirp3-HD-Charon").strip()

    def configured(self):
        return bool(self.key)

    def synth(self, text):
        body = {"input": {"text": text[:2500]}, "voice": {"languageCode": "de-DE", "name": self.voice},
                "audioConfig": {"audioEncoding": "MP3"}}
        req = urllib.request.Request(f"https://texttospeech.googleapis.com/v1/text:synthesize?key={self.key}",
                                     data=json.dumps(body).encode(), method="POST",
                                     headers={"content-type": "application/json"})
        try:
            data, _ = _http(req)
        except Exhausted as e:
            e.until = e.until or _next_month()
            raise
        return base64.b64decode(json.loads(data)["audioContent"]), "audio/mpeg"

    def balance(self):
        return {"text": "Free Tier (keine Restanzeige)", "empty": False}


class Edge:
    name, supports_tags = "edge", False

    def configured(self):
        import voice
        return voice.edge_available()

    def synth(self, text):
        import voice
        try:
            return voice.speak_edge(text), "audio/mpeg"
        except Exception as e:  # noqa: BLE001
            raise Transient(str(e))

    def balance(self):
        return {"text": "kostenlos", "empty": False}


ADAPTERS = {a.name: a for a in (Fish(), ElevenLabs(), Local(), Azure(), Google(), Edge())}


# ── balances (refreshed in the background, never on the speaking path) ──
_BAL = {}
BAL_TTL = 600


def refresh_balances():
    for name in CHAIN:
        a = ADAPTERS.get(name)
        if not a or not a.configured():
            continue
        try:
            b = a.balance()
        except Exception:  # noqa: BLE001
            b = None
        if b is None:
            continue
        b["at"] = time.time()
        _BAL[name] = b
        if b.get("empty"):
            until = b.get("reset") or time.time() + UNKNOWN_RESET_S
            _block(name, "Guthaben laut Anbieter leer", until)


def _balance_loop():
    while True:
        refresh_balances()
        time.sleep(BAL_TTL)


def start():
    os.makedirs(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "logs"), exist_ok=True)
    threading.Thread(target=_balance_loop, daemon=True).start()
    threading.Thread(target=_gpu_watch, daemon=True).start()


# ── persisted blocks ──────────────────────────────────────────
def _state():
    return commands.load().get("tts") or {}


def _block(name, reason, until):
    until = float(until or (time.time() + UNKNOWN_RESET_S))

    def mut(d):
        t = d.setdefault("tts", {})
        t.setdefault("blocked", {})[name] = {"until": until, "reason": reason[:160]}
    commands.update_state(mut)
    print(f"  Stimme: {LABELS.get(name, name)} gesperrt bis {dt.datetime.fromtimestamp(until):%d.%m. %H:%M} — {reason[:80]}",
          flush=True)


def _unblock(name):
    commands.update_state(lambda d: (d.get("tts") or {}).get("blocked", {}).pop(name, None))


def _blocked(name):
    b = (_state().get("blocked") or {}).get(name)
    if not b:
        return None
    if time.time() >= b["until"]:
        _unblock(name)            # reset date passed: try it first again
        return None
    return b


# ── the chain ─────────────────────────────────────────────────
def speak(text):
    """-> (audio bytes, mime, provider name). Raises only if every provider failed."""
    return _speak(text, False)


def speak_stream(text):
    """-> (iterator of audio chunks, mime, provider name). Providers that can stream
    (Fish) start sending at once; the others deliver their clip as one chunk."""
    return _speak(text, True)


def _speak(text, stream):
    text = (text or "").strip()
    if not text:
        raise ValueError("empty text")
    errors = []
    for name in CHAIN:
        a = ADAPTERS.get(name)
        if not a or not a.configured() or _blocked(name):
            continue
        say = text if a.supports_tags else _strip_tags(text)
        try:
            import daytime
            say = daytime.prepare(say, a.supports_tags)      # "[whispers]" at night
        except Exception:  # noqa: BLE001
            pass
        with _LOCK:
            prev = _state().get("active")
            switching = prev is not None and prev != name
            if switching:
                commands.update_state(lambda d: d.setdefault("tts", {}).__setitem__("active", name))
        if switching and ANNOUNCE:
            say = f"Wechsle auf {LABELS.get(name, name)}. " + say
        try:
            if stream and hasattr(a, "stream"):
                data, mime = a.stream(say)
            else:
                data, mime = a.synth(say)
                if stream:
                    data = iter([data])
        except Exhausted as e:
            _block(name, e.reason, e.until or time.time() + UNKNOWN_RESET_S)
            errors.append(f"{name}: leer ({e.reason[:60]})")
            continue
        except Transient as e:
            errors.append(f"{name}: {str(e)[:60]}")
            continue
        if prev is None:
            commands.update_state(lambda d: d.setdefault("tts", {}).__setitem__("active", name))
        return data, mime, name
    raise RuntimeError("Keine Stimme verfügbar: " + "; ".join(errors))


def status():
    st = _state()
    rows = []
    for name in CHAIN:
        a = ADAPTERS.get(name)
        if not a:
            continue
        blocked = (st.get("blocked") or {}).get(name)
        if blocked and time.time() >= blocked["until"]:
            blocked = None
        rows.append({"name": name, "label": LABELS.get(name, name), "configured": a.configured(),
                     "blocked_until": blocked["until"] if blocked else None,
                     "reason": blocked["reason"] if blocked else None,
                     "balance": a.balance()["text"] if name == "local" else (_BAL.get(name) or {}).get("text")})
    return {"active": st.get("active"), "active_label": LABELS.get(st.get("active"), st.get("active")),
            "chain": rows}
