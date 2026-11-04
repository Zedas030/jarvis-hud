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
        self.assertIn("Professional mode", self.say("Jarvis, professional mode"))
        self.assertEqual(personality.current(), "professional")
        self.assertIn("casual", self.say("be casual"))
        self.assertEqual(personality.current(), "casual")
        self.say("technical mode")
        self.assertEqual(personality.current(), "technical")
        self.say("back to normal")
        self.assertEqual(personality.current(), "standard")
        self.assertIn("Standard", self.say("Which mode?"))

    def test_for_some_hours(self):
        self.assertIn("For 2 hours", self.say("Professional mode for two hours"))
        self.assertIn("until", self.say("which mode"))
        commands.update_state(lambda d: d["persona"].__setitem__("until", time.time() - 1))
        self.assertEqual(personality.current(), "standard")

    def test_no_false_trigger(self):
        self.assertNotEqual(router.route("Play some relaxed music").get("handler"), "personality")
        self.assertEqual(personality.current(), "standard")

    def test_prompt_text(self):
        base = commands.LEAN_PERSONA
        std = personality.persona(base)
        self.assertIn('now and then "Sir"', std)
        self.assertIn("Humour only when it fits", std)
        personality.set_profile("casual")
        loose = commands.lean_system([])
        self.assertIn("call him by his first name", loose)
        self.assertNotIn("British-polite", loose)
        self.assertIn("Humour only when it fits", loose)
        personality.set_profile("technical")
        self.assertIn("up to five sentences", personality.persona(base))

    def test_custom_profile(self):
        personality.PATH.write_text(json.dumps({"evening": {"label": "Evening", "tone": "Tone: calm and quiet.",
                                                            "triggers": ["evening[- ]?mode"]}}), encoding="utf-8")
        self.say("Evening mode")
        self.assertEqual(personality.current(), "evening")
        self.assertIn("Tone: calm and quiet.", personality.persona(commands.LEAN_PERSONA))
        self.assertIn("Evening", self.say("which mode"))

    def test_typed_hint(self):
        self.assertIn("typed", personality.TYPED_HINT)


if __name__ == "__main__":
    unittest.main()
