"""
Personality profiles: switching by voice (no LLM), prompt text, custom profiles.

    .venv\\Scripts\\python.exe -m unittest tests.test_personality -v
"""
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import commands      # noqa: E402
import personality   # noqa: E402
import router        # noqa: E402


class Profiles(unittest.TestCase):
    def setUp(self):
        commands.update_state(lambda d: d.pop("persona", None))
        if personality.PATH.exists():
            personality.PATH.unlink()

    def say(self, text):
        r = router.route(text)
        self.assertEqual(r["kind"], "local", text)
        return r["reply"]

    def test_switch_by_voice(self):
        self.assertIn("Profi-Modus", self.say("Jarvis, Profi-Modus"))
        self.assertEqual(personality.current(), "profi")
        self.assertIn("locker", self.say("sei mal locker"))
        self.assertEqual(personality.current(), "locker")
        self.say("technischer Modus")
        self.assertEqual(personality.current(), "technisch")
        self.say("zurück zum Normalen")
        self.assertEqual(personality.current(), "standard")
        self.assertIn("Standard", self.say("Welcher Modus?"))

    def test_for_some_hours(self):
        self.assertIn("Für 2 Stunden", self.say("Profi-Modus für zwei Stunden"))
        self.assertIn("noch bis", self.say("welcher Modus"))
        commands.update_state(lambda d: d["persona"].__setitem__("until", time.time() - 1))
        self.assertEqual(personality.current(), "standard")

    def test_no_false_trigger(self):
        self.assertNotEqual(router.route("Spiel lockere Musik").get("handler"), "personality")
        self.assertEqual(personality.current(), "standard")

    def test_prompt_text(self):
        base = commands.LEAN_PERSONA
        std = personality.persona(base)
        self.assertIn('gelegentlich "Sir"', std)
        self.assertIn("Humor nur, wenn er passt", std)
        personality.set_profile("locker")
        loose = commands.lean_system([])
        self.assertIn("du duzt ihn", loose)
        self.assertNotIn("britisch-höflich", loose)
        self.assertIn("Humor nur, wenn er passt", loose)
        personality.set_profile("technisch")
        self.assertIn("bis zu fünf Sätze", personality.persona(base))

    def test_custom_profile(self):
        personality.PATH.write_text(json.dumps({"abend": {"label": "Abend", "ton": "Ton: ruhig und leise.",
                                                          "triggers": ["abend[- ]?modus"]}}), encoding="utf-8")
        self.say("Abendmodus")
        self.assertEqual(personality.current(), "abend")
        self.assertIn("Ton: ruhig und leise.", personality.persona(commands.LEAN_PERSONA))
        self.assertIn("Abend", self.say("welcher modus"))

    def test_typed_hint(self):
        self.assertIn("getippt", personality.TYPED_HINT)


if __name__ == "__main__":
    unittest.main()
