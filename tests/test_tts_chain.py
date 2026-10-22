"""
Voice provider chain — fake providers, no network, no audio.

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""
import os
import pathlib
import sys
import tempfile
import time
import unittest

TMP = pathlib.Path(tempfile.mkdtemp(prefix="jarvis-tts-test-"))
os.environ.update(JARVIS_STATE=str(TMP / "state.json"), TTS_CHAIN="a,b,c", TTS_ANNOUNCE_SWITCH="1")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import paths  # noqa: E402,F401

import commands    # noqa: E402
import tts_chain   # noqa: E402


class Fake:
    def __init__(self, name, tags=False, fail=None, until=None):
        self.name, self.supports_tags, self.fail, self.until = name, tags, fail, until
        self.said = []

    def configured(self):
        return True

    def synth(self, text):
        self.said.append(text)
        if self.fail == "exhausted":
            raise tts_chain.Exhausted("Guthaben leer", self.until)
        if self.fail == "transient":
            raise tts_chain.Transient("Timeout")
        return b"audio-" + self.name.encode(), "audio/mpeg"

    def balance(self):
        return None


class Chain(unittest.TestCase):
    def setup(self, **fails):
        tts_chain.CHAIN = ["a", "b", "c"]
        tts_chain.ADAPTERS = {n: Fake(n, tags=(n == "a"), fail=fails.get(n), until=fails.get(n + "_until"))
                              for n in ("a", "b", "c")}
        commands.update_state(lambda d: d.update(tts={}))
        return tts_chain.ADAPTERS

    def test_first_provider_speaks_with_tags(self):
        a = self.setup()
        data, mime, name = tts_chain.speak("[dry] Guten Abend.")
        self.assertEqual(name, "a")
        self.assertEqual(a["a"].said, ["[dry] Guten Abend."])

    def test_exhausted_is_blocked_until_reset(self):
        until = time.time() + 3600
        a = self.setup(a="exhausted", a_until=until)
        _, _, name = tts_chain.speak("[dry] Hallo.")
        self.assertEqual(name, "b")
        self.assertEqual(a["b"].said, ["Hallo."], "tags must be stripped for providers without tag support")
        self.assertAlmostEqual(commands.load()["tts"]["blocked"]["a"]["until"], until, delta=1)
        a["a"].said.clear()
        tts_chain.speak("Noch einmal.")
        self.assertEqual(a["a"].said, [], "a blocked provider must be skipped")

    def test_block_ends_after_reset(self):
        a = self.setup()
        tts_chain._block("a", "leer", time.time() - 1)
        _, _, name = tts_chain.speak("Test.")
        self.assertEqual(name, "a")
        self.assertNotIn("a", commands.load()["tts"].get("blocked", {}))

    def test_transient_only_moves_this_sentence(self):
        a = self.setup(a="transient")
        _, _, name = tts_chain.speak("Satz eins.")
        self.assertEqual(name, "b")
        self.assertNotIn("a", (commands.load()["tts"].get("blocked") or {}))

    def test_switch_is_announced_once(self):
        a = self.setup()
        tts_chain.speak("Eins.")
        a["a"].fail = "exhausted"
        tts_chain.speak("Zwei.")
        self.assertTrue(a["b"].said[-1].startswith("Wechsle auf"))
        tts_chain.speak("Drei.")
        self.assertEqual(a["b"].said[-1], "Drei.")

    def test_all_failing_raises(self):
        self.setup(a="transient", b="exhausted", c="transient")
        with self.assertRaises(RuntimeError):
            tts_chain.speak("Hallo?")


if __name__ == "__main__":
    unittest.main()
