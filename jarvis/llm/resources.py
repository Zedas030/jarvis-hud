"""
Resource manager: keeps the GPU voice, Whisper and Ollama from crowding each
other (or a game) out of the graphics memory.

Measures, every JARVIS_RESOURCE_SECONDS (30), from real sources only:
  nvidia-smi       used / total VRAM (infra.gpu)
  psutil           free system RAM
  Ollama /api/ps   which local model is loaded and how much VRAM it holds
  tts_chain        whether the GPU voice (Chatterbox) runs and when it last spoke
  voice            whether Whisper sits on the GPU and when it last listened

Budget: JARVIS_VRAM_RESERVE_MB (1500) must stay free for Windows, browser and
games. If free VRAM stays below the reserve for two checks, Jarvis gives memory
back in this order, each only when idle (never while speaking or listening):
  1. unload the Ollama model (it reloads on the next fallback answer)
  2. stop the GPU voice — Piper on the CPU speaks instead
  3. move Whisper from the GPU to the CPU (small model)
When there is room again (reserve + JARVIS_VRAM_HYSTERESIS_MB for three checks)
the GPU voice is started again; Whisper returns to the GPU on its next load.

choose_ollama_model(): the fallback picks the configured model, or a smaller
installed one (JARVIS_OLLAMA_SMALL_MODEL) when the big one would not fit.
Nothing here is hard-coded hardware; model sizes come from Ollama itself, the
GPU voice and Whisper needs are settings (JARVIS_TTS_VRAM_MB, JARVIS_WHISPER_VRAM_MB).
"""
import json
import os
import threading
import time
import urllib.request

EVERY = int(os.environ.get("JARVIS_RESOURCE_SECONDS", "30"))
RESERVE = int(os.environ.get("JARVIS_VRAM_RESERVE_MB", "1500"))
HYSTERESIS = int(os.environ.get("JARVIS_VRAM_HYSTERESIS_MB", "1000"))
RAM_RESERVE = int(os.environ.get("JARVIS_RAM_RESERVE_MB", "2000"))
TTS_NEED = int(os.environ.get("JARVIS_TTS_VRAM_MB", "4000"))
WHISPER_NEED = int(os.environ.get("JARVIS_WHISPER_VRAM_MB", "1800"))
IDLE_S = int(os.environ.get("JARVIS_RESOURCE_IDLE_S", "60"))
OLLAMA_IDLE_S = int(os.environ.get("JARVIS_OLLAMA_IDLE_S", "180"))
SMALL_MODEL = os.environ.get("JARVIS_OLLAMA_SMALL_MODEL", "").strip()
ENABLED = os.environ.get("JARVIS_RESOURCES", "1").strip() not in ("0", "false", "no", "off")

STATE = {"low": 0, "ok": 0, "shed": [], "last": {}, "ollama_used": 0.0, "log": []}
_LOCK = threading.Lock()


# ── measuring ────────────────────────────────────────────────

def _ollama(path, body=None, timeout=3):
    import fallback_llm
    req = urllib.request.Request(fallback_llm.OLLAMA_URL + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def ollama_loaded():
    """[(name, vram_mb)] of models Ollama holds right now, or [] if it is not running."""
    try:
        return [(m.get("name", "?"), round((m.get("size_vram") or 0) / 2**20)) for m in _ollama("/api/ps").get("models", [])]
    except Exception:  # noqa: BLE001
        return []


def ollama_sizes():
    """{name: file size MB} of installed models — the best estimate of what loading costs."""
    try:
        return {m["name"]: round(m["size"] / 2**20) for m in _ollama("/api/tags").get("models", [])}
    except Exception:  # noqa: BLE001
        return {}


def snapshot():
    import infra
    import psutil
    g = (infra.gpu() or [None])[0]
    snap = {"at": time.time(), "ram_free_mb": round(psutil.virtual_memory().available / 2**20)}
    if g:
        snap.update(gpu=g["name"], vram_total_mb=round(g["vram_total"]), vram_used_mb=round(g["vram_used"]),
                    vram_free_mb=round(g["vram_total"] - g["vram_used"]))
    snap["ollama"] = ollama_loaded()
    snap["tts_gpu"] = _tts_state()
    snap["whisper"] = _whisper_state()
    STATE["last"] = snap
    return snap


def _tts_state():
    try:
        import tts_chain
        g = tts_chain._GPU
        return {"running": bool(g.get("ready")), "paused": bool(g.get("paused")), "last_use": g.get("last_use", 0)}
    except Exception:  # noqa: BLE001
        return {"running": False, "paused": False, "last_use": 0}


def _whisper_state():
    try:
        import voice
        w = voice._WHISPER
        return {"name": w.get("name") or "", "gpu": "Grafikkarte" in (w.get("name") or ""),
                "last_use": w.get("last_use", 0)}
    except Exception:  # noqa: BLE001
        return {"name": "", "gpu": False, "last_use": 0}


# ── decisions ────────────────────────────────────────────────

def _note(text):
    STATE["log"] = (STATE["log"] + [f"{time.strftime('%H:%M')} {text}"])[-20:]
    try:
        import audit
        audit.write("resource", what=text)
    except Exception:  # noqa: BLE001
        pass
    print(f"  resources: {text}", flush=True)


def plan(snap, now=None):
    """What to give back or take again: list of action names. Pure function of the snapshot and STATE."""
    now = now or time.time()
    free = snap.get("vram_free_mb")
    if free is None:
        return []
    if free < RESERVE:
        STATE["low"] += 1
        STATE["ok"] = 0
    elif free >= RESERVE + HYSTERESIS:
        STATE["ok"] += 1
        STATE["low"] = 0
    else:
        STATE["low"] = STATE["ok"] = 0
    actions = []
    if STATE["low"] >= 2:
        if snap["ollama"] and now - STATE["ollama_used"] > IDLE_S:
            actions.append("unload_ollama")
        elif snap["tts_gpu"]["running"] and now - snap["tts_gpu"]["last_use"] > IDLE_S:
            actions.append("stop_tts_gpu")
        elif snap["whisper"]["gpu"] and now - snap["whisper"]["last_use"] > IDLE_S:
            actions.append("whisper_to_cpu")
    elif snap["ollama"] and now - STATE["ollama_used"] > OLLAMA_IDLE_S:
        actions.append("unload_ollama")                 # idle rule, regardless of pressure
    if STATE["ok"] >= 3 and "stop_tts_gpu" in STATE["shed"] and free >= RESERVE + TTS_NEED:
        actions.append("start_tts_gpu")
    if STATE["ok"] >= 3 and "whisper_to_cpu" in STATE["shed"] and free >= RESERVE + WHISPER_NEED:
        actions.append("whisper_back")
    return actions


def act(action, snap):
    if action == "unload_ollama":
        for name, mb in snap["ollama"]:
            try:
                _ollama("/api/generate", {"model": name, "keep_alive": 0}, timeout=10)
                _note(f"Ollama-Modell {name} entladen ({mb} MB VRAM frei)")
            except Exception as e:  # noqa: BLE001
                _note(f"Ollama entladen fehlgeschlagen: {e}")
    elif action == "stop_tts_gpu":
        import tts_chain
        if tts_chain.gpu_pause():
            STATE["shed"].append(action)
            _note("GPU-Stimme pausiert, Piper spricht (Grafikspeicher knapp)")
    elif action == "start_tts_gpu":
        import tts_chain
        tts_chain.gpu_resume()
        STATE["shed"].remove("stop_tts_gpu")
        _note("GPU-Stimme wieder gestartet")
    elif action == "whisper_to_cpu":
        import voice
        if voice.unload_stt():
            STATE["shed"].append(action)
            _note("Whisper von der Grafikkarte genommen, hört jetzt auf der CPU")
    elif action == "whisper_back":
        STATE["shed"].remove("whisper_to_cpu")
        _note("Whisper darf wieder auf die Grafikkarte (beim nächsten Laden)")


def gpu_allowed(kind):
    """For loaders: may this component use the GPU now? kind: whisper | tts"""
    if kind == "whisper" and "whisper_to_cpu" in STATE["shed"]:
        return False
    if kind == "tts" and "stop_tts_gpu" in STATE["shed"]:
        return False
    last = STATE.get("last") or {}
    free = last.get("vram_free_mb")
    need = WHISPER_NEED if kind == "whisper" else TTS_NEED
    return free is None or free >= RESERVE + need


def choose_ollama_model(configured):
    """(model, reason) for the next fallback answer."""
    STATE["ollama_used"] = time.time()
    loaded = dict(ollama_loaded())
    if configured in loaded:
        return configured, ""
    sizes = ollama_sizes()
    snap = STATE.get("last") or {}
    free = snap.get("vram_free_mb")
    need = sizes.get(configured)
    if free is None or need is None or free - RESERVE >= need * 1.15:
        return configured, ""
    if SMALL_MODEL and SMALL_MODEL in sizes and free - RESERVE >= sizes[SMALL_MODEL] * 1.15:
        return SMALL_MODEL, f"zu wenig Grafikspeicher für {configured}, nehme {SMALL_MODEL}"
    return configured, f"zu wenig Grafikspeicher — {configured} läuft teilweise auf der CPU (langsamer)"


def status():
    s = STATE.get("last") or {}
    if not s:
        return {"ok": None, "text": "noch keine Messung"}
    parts = []
    if "vram_total_mb" in s:
        parts.append(f"VRAM {s['vram_used_mb'] / 1024:.1f}/{s['vram_total_mb'] / 1024:.0f} GB frei {s['vram_free_mb'] / 1024:.1f}")
    parts.append("Stimme " + ("GPU" if s["tts_gpu"]["running"] else "pausiert" if s["tts_gpu"]["paused"] else "CPU"))
    parts.append("Whisper " + ("GPU" if s["whisper"]["gpu"] else "CPU" if s["whisper"]["name"] else "–"))
    parts.append("Ollama " + (", ".join(n for n, _ in s["ollama"]) or "leer"))
    return {"ok": s.get("vram_free_mb", RESERVE) >= RESERVE, "text": " · ".join(parts), "shed": list(STATE["shed"]),
            "log": STATE["log"][-8:], "snapshot": s}


def describe():
    """German text for the resource_status tool."""
    st = status()
    if st["ok"] is None:
        snapshot()
        st = status()
    lines = [st["text"], f"Reserve {RESERVE} MB, frei gehalten für Windows, Browser und Spiele."]
    if st["shed"]:
        lines.append("Gerade abgegeben: " + ", ".join(st["shed"]))
    if st["log"]:
        lines.append("Zuletzt: " + "; ".join(st["log"][-4:]))
    return "\n".join(lines)


def step():
    with _LOCK:
        snap = snapshot()
        for a in plan(snap):
            act(a, snap)


def start():
    if not ENABLED:
        return
    import background
    background.loop("resources", EVERY, step, pausable=False)
