"""
Run recorded audio through the real voice engine (Silero VAD, local Whisper, the configured
voice) with a fake model, and report what happened — the tuning loop for config/voice.yaml.

    .venv\\Scripts\\python.exe scripts\\voice_scenarios.py              # all scenarios
    .venv\\Scripts\\python.exe scripts\\voice_scenarios.py thinking_pause    # one
    .venv\\Scripts\\python.exe scripts\\voice_scenarios.py --fake-tts   # without Piper/Fish (silence as voice)

Fixtures (tests/fixtures/voice/, 16 kHz mono; regenerate with scripts/make_voice_fixtures.py):
  question_light         one request, finished sentence → wait_ms should be the base wait
  thinking_pause         "Jarvis, and then" … 0.9 s pause … "please turn the music down" → ONE request
  interruption_question  talking over the answer with a question → new turn, spoken part kept
  backchannel_mhm        "Mhm." over the answer → he goes on at the sentence start
  cough                  a cough over the answer → duck, no pause or a short pause, then on
  stop                   "Stop." → answer ends
  name_only              "Jarvis?" over the answer → he stops and listens

State and logs come from the real Session; nothing talks to Claude or the house (JARVIS_STATE
points to a temp file). The barge-in scenarios first play `question_light`, wait for SPEAKING, then
feed the clip.
"""
import json
import os
import pathlib
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ["JARVIS_STATE"] = str(pathlib.Path(tempfile.mkdtemp(prefix="jarvis-voice-")) / "state.json")
os.environ.setdefault("JARVIS_WAKE", "Jarvis")
import paths  # noqa: E402,F401
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")   # Windows console: cp1252 chokes on arrows
    except (AttributeError, ValueError):
        pass

import voice_config  # noqa: E402
import voice_engine  # noqa: E402
import voice_dsp  # noqa: E402
import voice_metrics  # noqa: E402
import voice_state as st  # noqa: E402
from voice_fakes import Client, FakeResponse, answer_events, fake_speak_stream  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "voice"
ANSWER = ("The light in the living room is off, Sir. The storage is at eighty percent and still charging. "
          "The solar system is delivering two point four kilowatts right now. Anything else?")


def load(name):
    pcm, rate = voice_dsp.read_wav((FIX / f"{name}.wav").read_bytes())
    return voice_dsp.resample(pcm, rate, 16000)


def make_session(fake_tts):
    cfg = voice_config.load()
    cfg = voice_config.make({**{k: dict(v) for k, v in cfg.items()},
                             "engine": {**cfg.engine, "state_log": "", "debug_audio": False},
                             "metrics": {"file": ""}})
    shared = voice_engine.Shared(port=1, token="t", cfg=cfg)
    client = Client()
    session = voice_engine.Session(shared, client.send, client.send_audio, device="scenario")
    client.session = session
    if fake_tts:
        session.synth.speak_stream = fake_speak_stream()
    session.posts = []
    answers = [ANSWER, "It is just after ten, Sir."]

    def fake_post(path, body, stream=False, timeout=30):
        session.posts.append((path, body))
        if path == "/api/run":
            return FakeResponse(answer_events(answers[0] if len(answers) == 1 else answers.pop(0)), delay=0.03)
        return {}
    session._post = fake_post
    return session, client


def feed(session, pcm, realtime=True):
    n = 640
    for i in range(0, len(pcm) - n + 1, n):
        session.feed(pcm[i:i + n])
        if realtime:
            time.sleep(0.02)


def wait_for(client, state, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if client.session.machine.state == state:
            return True
        time.sleep(0.01)
    return False


def report(name, session, client, t0, extra=""):
    states = " → ".join(f"{h['to']}({h['why']})" for h in session.machine.history)
    finals = [e["text"] for e in client.of("final")]
    runs = [p[1]["message"] for p in session.posts if p[0] == "/api/run"]
    snap = voice_metrics.snapshot()["last"]
    print(f"\n=== {name}  ({time.time() - t0:.1f} s)")
    print(f"  states    : {states}")
    print(f"  understood: {finals}")
    print(f"  requests  : {runs}")
    print(f"  metrics   : " + ", ".join(f"{k} {v:.0f}" for k, v in snap.items()
                                        if k in ("wait_ms", "stt_ms", "first_word_ms", "barge_react_ms", "tts_first_ms")))
    logs = [e["text"] for e in client.of("log")]
    if logs:
        print(f"  Protokoll: {' | '.join(logs[-6:])}")
    if extra:
        print(f"  {extra}")


def scenario_request(name, fake_tts):
    session, client = make_session(fake_tts)
    t0 = time.time()
    voice_metrics.reset()
    try:
        feed(session, load(name))
        wait_for(client, st.SPEAKING, 8)
        wait_for(client, st.LISTENING, 25)
        report(name, session, client, t0)
        return session
    finally:
        session.close()


def scenario_barge(name, fake_tts):
    session, client = make_session(fake_tts)
    client.hold = True                                   # the client 'plays' until we say so
    t0 = time.time()
    voice_metrics.reset()
    try:
        feed(session, load("question_light"))
        if not wait_for(client, st.SPEAKING, 10):
            report(name, session, client, t0, "NO ANSWER — preparation failed")
            return
        client.wait_event("audio_begin", 10)
        time.sleep(0.4)
        feed(session, load(name))
        time.sleep(1.5)
        client.hold = False
        for e in list(client.events):
            if e.get("t") == "audio_end" and session.machine.state in (st.SPEAKING, st.RESUMING):
                session.client_event({"t": "ended", "id": e["id"]})
        wait_for(client, st.LISTENING, 20)
        outcome = [k for k in voice_metrics.snapshot()["counters"] if k.startswith("barge_ins")]
        report(name, session, client, t0, f"Barge-in: {outcome}")
    finally:
        session.close()


def main(argv):
    fake_tts = "--fake-tts" in argv
    names = [a for a in argv if not a.startswith("--")]
    all_names = ["question_light", "time", "thinking_pause", "interruption_question", "backchannel_mhm", "cough", "stop", "name_only"]
    names = names or all_names
    print(f"Engine scenarios · VAD {voice_engine.voice_vad.Vad(voice_config.load().vad).kind} · "
          f"voice {'silence (fake)' if fake_tts else os.environ.get('TTS_CHAIN', 'auto')}")
    for n in names:
        if n in ("question_light", "time", "thinking_pause"):
            scenario_request(n, fake_tts)
        else:
            scenario_barge(n, fake_tts)
    print("\nfertig.")


if __name__ == "__main__":
    main(sys.argv[1:])
