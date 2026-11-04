"""
Resource manager: who gives back graphics memory, when, and never while busy.
GPU, Ollama, the GPU voice and Whisper are faked.

    .venv\\Scripts\\python.exe -m unittest tests.test_resources -v
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import resources as rm   # noqa: E402
import tts_chain         # noqa: E402
import voice             # noqa: E402

NOW = 1_000_000.0


def snap(free, ollama=(), tts=True, tts_use=0.0, whisper_gpu=True, whisper_use=0.0):
    return {"vram_free_mb": free, "vram_total_mb": 16000, "vram_used_mb": 16000 - free,
            "ollama": list(ollama), "tts_gpu": {"running": tts, "paused": False, "last_use": tts_use},
            "whisper": {"name": "large-v3-turbo, Grafikkarte" if whisper_gpu else "small, CPU",
                        "gpu": whisper_gpu, "last_use": whisper_use}}


class Patch:
    def __init__(self, obj, **kw):
        self.obj, self.kw, self.old = obj, kw, {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = getattr(self.obj, k)
            setattr(self.obj, k, v)

    def __exit__(self, *x):
        for k, v in self.old.items():
            setattr(self.obj, k, v)


class Plan(unittest.TestCase):
    def setUp(self):
        rm.STATE.update(low=0, ok=0, shed=[], last={}, ollama_used=0.0, log=[])

    def test_plenty_of_room_does_nothing(self):
        self.assertEqual(rm.plan(snap(9000), NOW), [])

    def test_pressure_needs_two_checks_and_sheds_in_order(self):
        rm.STATE["ollama_used"] = NOW - rm.IDLE_S - 10     # used a minute ago: not idle by the 3-minute rule
        s = snap(800, ollama=[("qwen2.5:7b-instruct", 4500)])
        self.assertEqual(rm.plan(s, NOW), [])
        self.assertEqual(rm.plan(s, NOW), ["unload_ollama"])
        s = snap(800)
        self.assertEqual(rm.plan(s, NOW), ["stop_tts_gpu"])
        s = snap(800, tts=False)
        self.assertEqual(rm.plan(s, NOW), ["whisper_to_cpu"])

    def test_never_while_busy(self):
        s = snap(800, tts_use=NOW - 5, whisper_use=NOW - 5)
        rm.plan(s, NOW)
        self.assertEqual(rm.plan(s, NOW), [])          # voice spoke 5 s ago, Whisper listened 5 s ago
        rm.STATE["ollama_used"] = NOW - 10
        self.assertEqual(rm.plan(snap(800, ollama=[("m", 1)], tts=False, whisper_gpu=False), NOW), [])

    def test_idle_ollama_unloaded_without_pressure(self):
        rm.STATE["ollama_used"] = NOW - rm.OLLAMA_IDLE_S - 1
        self.assertEqual(rm.plan(snap(9000, ollama=[("m", 4500)]), NOW), ["unload_ollama"])

    def test_takes_back_after_three_good_checks(self):
        rm.STATE["shed"] = ["stop_tts_gpu", "whisper_to_cpu"]
        s = snap(rm.RESERVE + rm.TTS_NEED + 100, tts=False, whisper_gpu=False)
        self.assertEqual(rm.plan(s, NOW), [])
        self.assertEqual(rm.plan(s, NOW), [])
        self.assertEqual(rm.plan(s, NOW), ["start_tts_gpu", "whisper_back"])

    def test_gpu_allowed(self):
        rm.STATE["shed"] = ["whisper_to_cpu"]
        self.assertFalse(rm.gpu_allowed("whisper"))
        rm.STATE["shed"] = []
        rm.STATE["last"] = snap(500)
        self.assertFalse(rm.gpu_allowed("whisper"))
        rm.STATE["last"] = snap(9000)
        self.assertTrue(rm.gpu_allowed("whisper"))
        rm.STATE["last"] = {}
        self.assertTrue(rm.gpu_allowed("tts"))         # no measurement: do not block


class Act(unittest.TestCase):
    def setUp(self):
        rm.STATE.update(low=0, ok=0, shed=[], last={}, ollama_used=0.0, log=[])

    def test_unload_ollama_calls_keep_alive_zero(self):
        calls = []
        with Patch(rm, _ollama=lambda path, body=None, timeout=3: calls.append((path, body)) or {}):
            rm.act("unload_ollama", snap(800, ollama=[("qwen2.5:7b-instruct", 4500)]))
        self.assertEqual(calls, [("/api/generate", {"model": "qwen2.5:7b-instruct", "keep_alive": 0})])
        self.assertIn("unloaded", rm.STATE["log"][-1])

    def test_stop_and_restart_voice(self):
        with Patch(tts_chain, gpu_pause=lambda: True, gpu_resume=lambda: None):
            rm.act("stop_tts_gpu", snap(800))
            self.assertIn("stop_tts_gpu", rm.STATE["shed"])
            rm.act("start_tts_gpu", snap(9000))
            self.assertNotIn("stop_tts_gpu", rm.STATE["shed"])

    def test_voice_refuses_when_not_ours_or_busy(self):
        old = dict(tts_chain._GPU)
        try:
            tts_chain._GPU.update(proc=None)
            self.assertFalse(tts_chain.gpu_pause())

            class Proc:
                def poll(self):
                    return None
            tts_chain._GPU.update(proc=Proc(), last_use=time.time())
            self.assertFalse(tts_chain.gpu_pause())     # spoke just now
        finally:
            tts_chain._GPU.clear()
            tts_chain._GPU.update(old)

    def test_whisper_unload_guards(self):
        old = dict(voice._WHISPER)
        try:
            voice._WHISPER.update(model=object(), name="large-v3-turbo, Grafikkarte", busy=1, last_use=0)
            self.assertFalse(voice.unload_stt())       # transcribing
            voice._WHISPER.update(busy=0, last_use=time.time())
            self.assertFalse(voice.unload_stt())       # listened just now
            voice._WHISPER.update(last_use=0)
            self.assertTrue(voice.unload_stt())
            self.assertIsNone(voice._WHISPER["model"])
            voice._WHISPER.update(model=object(), name="small, CPU")
            self.assertFalse(voice.unload_stt())       # CPU copy holds no VRAM
        finally:
            voice._WHISPER.clear()
            voice._WHISPER.update(old)


class Ollama(unittest.TestCase):
    def setUp(self):
        rm.STATE.update(low=0, ok=0, shed=[], last={}, ollama_used=0.0, log=[])

    def choose(self, free, loaded=(), small=""):
        rm.STATE["last"] = snap(free)
        with Patch(rm, ollama_loaded=lambda: list(loaded),
                   ollama_sizes=lambda: {"qwen2.5:7b-instruct": 4466, "qwen2.5:3b": 1900}, SMALL_MODEL=small):
            return rm.choose_ollama_model("qwen2.5:7b-instruct")

    def test_fits(self):
        self.assertEqual(self.choose(9000), ("qwen2.5:7b-instruct", ""))

    def test_already_loaded(self):
        self.assertEqual(self.choose(500, loaded=[("qwen2.5:7b-instruct", 4400)]), ("qwen2.5:7b-instruct", ""))

    def test_smaller_model(self):
        model, why = self.choose(4500, small="qwen2.5:3b")
        self.assertEqual(model, "qwen2.5:3b")
        self.assertIn("not enough graphics memory", why)

    def test_no_small_model_says_so(self):
        model, why = self.choose(2000)
        self.assertEqual(model, "qwen2.5:7b-instruct")
        self.assertIn("partly on the CPU", why)


class Missing(unittest.TestCase):
    def test_no_gpu_no_crash(self):
        import infra
        with Patch(infra, gpu=lambda: []), Patch(rm, ollama_loaded=lambda: []):
            s = rm.snapshot()
        self.assertNotIn("vram_free_mb", s)
        self.assertEqual(rm.plan(s, NOW), [])
        self.assertIn("Reserve", rm.describe())


if __name__ == "__main__":
    unittest.main()
