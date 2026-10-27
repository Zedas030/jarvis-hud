"""
Desktop card — when it should show; no window is opened here.

    .venv\\Scripts\\python.exe -m unittest tests.test_overlay -v
"""
import isolate  # noqa: F401  (must come first)
import time
import unittest

import overlay


class Overlay(unittest.TestCase):
    def setUp(self):
        overlay.ENABLED = True
        overlay.STATE.update(visible=True, st="off", detail="", line="", at=0.0)

    def test_shows_only_while_the_tab_is_hidden(self):
        overlay.report({"visible": False, "st": "speaking", "detail": "Spricht", "line": "Guten Tag"})
        self.assertTrue(overlay.wanted())
        overlay.report({"visible": True, "st": "speaking"})
        self.assertFalse(overlay.wanted())

    def test_goes_away_when_the_hud_stops_reporting(self):
        overlay.report({"visible": False, "st": "listening"})
        overlay.STATE["at"] = time.time() - overlay.STALE_S - 1
        self.assertFalse(overlay.wanted(), "tab closed or browser gone: no card left behind")

    def test_switched_off(self):
        overlay.ENABLED = False
        overlay.report({"visible": False, "st": "busy"})
        self.assertFalse(overlay.wanted())

    def test_long_line_starts_at_a_word(self):
        tail = overlay._tail("Golf, Sir, ist ein Sport, bei dem man einen kleinen Ball in möglichst wenigen Schlägen einlocht.")
        self.assertTrue(tail.startswith("… ") and len(tail) <= 82, tail)
        self.assertFalse(tail[2:].startswith("in Sport"), "never in the middle of a word")
        self.assertEqual(overlay._tail("kurz"), "kurz")

    def test_tone_is_kept_for_the_face(self):
        overlay.report({"visible": False, "st": "speaking", "tone": "dry"})
        self.assertEqual(overlay.STATE["tone"], "dry")

    def test_face_from_tone_tag_first(self):
        self.assertEqual(overlay.mood("dry"), "critical")
        self.assertEqual(overlay.mood("amused"), "happy")
        self.assertEqual(overlay.mood("sighs"), "worried")
        self.assertEqual(overlay.mood("firm"), "serious")
        self.assertEqual(overlay.mood("satisfied", "Leider kaputt."), "proud", "the tag wins over the words")

    def test_face_from_words_without_a_tag(self):
        self.assertEqual(overlay.mood("", "Leider ist der Druck fehlgeschlagen, Sir."), "worried")
        self.assertEqual(overlay.mood("", "Erledigt, Sir."), "proud")
        self.assertIsNone(overlay.mood("", "Es sind 21 Grad."))
        self.assertIsNone(overlay.mood(None, None))

    def test_unknown_fields_are_ignored(self):
        overlay.report({"visible": False, "st": "busy", "evil": "x"})
        self.assertNotIn("evil", overlay.STATE)


if __name__ == "__main__":
    unittest.main()
