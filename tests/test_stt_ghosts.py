"""voice_stt: room noise that Whisper turns into words is dropped, real short answers stay. No model, no GPU."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import voice_stt  # noqa: E402


def rec():
    r = object.__new__(voice_stt.Recognizer)
    r.min_chars = 2
    return r


def seg(text, no_speech=0.1, logprob=-0.3, ratio=1.2):
    return types.SimpleNamespace(text=text, no_speech_prob=no_speech, avg_logprob=logprob, compression_ratio=ratio)


class FillerRuns(unittest.TestCase):
    def test_noise_from_the_voice_pe(self):
        for text in ("Yes, yes, yes, yes.", "yeah yeah", "Hm hm.", "Uh, um.", "Thank you. Thanks."):
            self.assertEqual(rec().clean(text), "", text)

    def test_real_short_answers_stay(self):
        for text in ("No.", "No, no.", "Yes, do that.", "Yes please", "Lights off", "Okay, thanks a lot"):
            self.assertEqual(rec().clean(text), text, text)


class Segments(unittest.TestCase):
    def test_whisper_says_no_speech(self):
        self.assertTrue(voice_stt._ghost_segment(seg("Yes, yes.", no_speech=0.8, logprob=-1.4)))

    def test_looping_text(self):
        self.assertTrue(voice_stt._ghost_segment(seg("yes " * 30, ratio=6.0)))

    def test_quiet_but_sure_speech_stays(self):
        # a high no-speech guess alone is not enough: Whisper was still sure of the words
        self.assertFalse(voice_stt._ghost_segment(seg("Turn the light off.", no_speech=0.7, logprob=-0.4)))
        self.assertFalse(voice_stt._ghost_segment(seg("What will the weather be like tomorrow?")))

    def test_segment_without_numbers_is_kept(self):
        self.assertFalse(voice_stt._ghost_segment(types.SimpleNamespace(text="Hello")))


if __name__ == "__main__":
    unittest.main()
