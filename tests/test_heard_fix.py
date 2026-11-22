"""
Known mishearings are replaced in the finished transcript (heard_fix.py, config/heard.json).

    .venv\\Scripts\\python.exe -m unittest tests.test_heard_fix -v
"""
import json
import os
import unittest

import tests.isolate  # noqa: F401
import heard_fix
import voice_stt


class HeardFix(unittest.TestCase):
    def setUp(self):
        heard_fix.PATH.write_text(json.dumps({"fixes": {
            "males": "mails", "sun nails": "some mails", "mark as red": "mark as read", "good knight": "good night"}}),
            encoding="utf-8")
        heard_fix._TABLE.update(mtime=None, rules=[])

    def tearDown(self):
        heard_fix.PATH.unlink(missing_ok=True)

    def test_what_the_user_said_on_2026_11_20(self):
        self.assertEqual(heard_fix.fix("Yes, I mean the males. What came in today?"),
                         "Yes, I mean the mails. What came in today?")
        self.assertEqual(heard_fix.fix("What? Sun nails arrived today?"), "What? some mails arrived today?")
        self.assertEqual(heard_fix.fix("Jarvis, now mark as red all of them."), "Jarvis, now mark as read all of them.")

    def test_whole_words_and_phrases_only(self):
        self.assertEqual(heard_fix.fix("The tamales recipe"), "The tamales recipe")
        self.assertEqual(heard_fix.fix("Good  Knight, Jarvis"), "good night, Jarvis")

    def test_no_file_or_a_broken_one_changes_nothing(self):
        heard_fix.PATH.write_text("{broken", encoding="utf-8")
        heard_fix._TABLE.update(mtime=None, rules=[])
        self.assertEqual(heard_fix.fix("the males"), "the males")
        heard_fix.PATH.unlink()
        self.assertEqual(heard_fix.fix("the males"), "the males")
        self.assertEqual(heard_fix.fix(""), "")

    def test_an_edited_file_is_read_again(self):
        self.assertEqual(heard_fix.fix("the males"), "the mails")
        heard_fix.PATH.write_text(json.dumps({"fixes": {}}), encoding="utf-8")
        os.utime(heard_fix.PATH, (1, 1))
        self.assertEqual(heard_fix.fix("the males"), "the males")

    def test_the_recognizer_applies_it_to_the_final_transcript(self):
        rec = voice_stt.Recognizer({})
        rec._run = lambda pcm, beam: "Read me the males."
        self.assertEqual(rec.final(b"\0" * 32000), "Read me the mails.")


if __name__ == "__main__":
    unittest.main()
