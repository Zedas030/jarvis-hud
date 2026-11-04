"""
Regenerate the recorded scenarios in tests/fixtures/voice/ (16 kHz mono WAV): Piper speaks the
user's lines — Whisper does not care whose voice it is — and a shaped noise burst stands for a cough.

    .venv\\Scripts\\python.exe scripts\\make_voice_fixtures.py

The committed files were spoken by the Windows voice "Microsoft Zira" (System.Speech, en-US) with the
same lines, pauses and cough; this script makes the Piper versions of them.

Replace a file with a real recording of your own voice to tune against yourself
(16 kHz mono; `ffmpeg -i rec.m4a -ar 16000 -ac 1 tests/fixtures/voice/question_light.wav`).
"""
import io
import math
import pathlib
import random
import struct
import sys
import wave

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import paths  # noqa: E402,F401
import voice_dsp  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "voice"
MODEL = ROOT / "models" / "piper" / "en_GB-alan-medium.onnx"


def main():
    from piper import PiperVoice, SynthesisConfig
    voice = PiperVoice.load(str(MODEL))

    def speak(text):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            voice.synthesize_wav(text, w, syn_config=SynthesisConfig(length_scale=1.0))
        pcm, rate = voice_dsp.read_wav(buf.getvalue())
        return voice_dsp.resample(pcm, rate, 16000)

    def sil(ms):
        return bytes(2 * int(16000 * ms / 1000))

    rnd = random.Random(3)

    def cough(ms=250):
        n = int(16000 * ms / 1000)
        return struct.pack(f"<{n}h", *(int(0.5 * 32767 * (rnd.random() * 2 - 1) * math.sin(math.pi * i / n)) for i in range(n)))

    items = {
        "question_light": sil(300) + speak("Jarvis, turn off the light in the living room.") + sil(1500),
        "time": sil(300) + speak("Jarvis, what time is it?") + sil(2000),
        "thinking_pause": sil(300) + speak("Jarvis, and then") + sil(900) + speak("please turn the music in the bedroom down.") + sil(1500),
        "interruption_question": speak("What time is it, actually?") + sil(800),
        "backchannel_mhm": speak("Mhm.") + sil(800),
        "cough": cough() + sil(800),
        "stop": speak("Stop.") + sil(800),
        "name_only": speak("Jarvis?") + sil(800),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for name, pcm in items.items():
        (OUT / f"{name}.wav").write_bytes(voice_dsp.wav_bytes(pcm, 16000))
        print(f"  {name}: {len(pcm) / 32000:.1f} s")


if __name__ == "__main__":
    main()
