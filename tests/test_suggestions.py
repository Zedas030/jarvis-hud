"""
Suggestion module: habits from the usage log, one proposal, card-gated setup.

    .venv\\Scripts\\python.exe -m unittest tests.test_suggestions -v
"""
import datetime as dt
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402  (must come before any Jarvis module)

import background    # noqa: E402
import events        # noqa: E402
import memory        # noqa: E402
import proposals     # noqa: E402
import routines      # noqa: E402
import suggestions   # noqa: E402

NOW = dt.datetime(2026, 10, 27, 18, 45)     # a Tuesday


def usage_rows():
    rows = []
    for i in range(6):
        rows.append(dict(at=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - i * 86400)),
                         route="llm:simple", groups=["memory", "home"], message="Turn off all the lights and the music down"))
    rows.append(dict(at=time.strftime("%Y-%m-%d %H:%M:%S"), route="llm:smart", groups=["memory"],
                     message="Prepare the meeting with the customer"))
    rows.append(dict(at=time.strftime("%Y-%m-%d %H:%M:%S"), route="local:light", groups=[], message="Light on"))
    rows.append(dict(at=time.strftime("%Y-%m-%d %H:%M:%S"), route="background:reflection", groups=[], message="x"))
    return rows


class Suggest(unittest.TestCase):
    def setUp(self):
        suggestions.USAGE_LOG = TMP / "usage.jsonl"
        suggestions.USAGE_LOG.write_text("\n".join(json.dumps(r) for r in usage_rows()), encoding="utf-8")
        for p in (memory.MEMORY_DIR / "Suggestions.md", proposals.PATH, routines.PATH):
            if p.exists():
                p.unlink()
        self._ask = background.ask
        self.prompts = []

    def tearDown(self):
        background.ask = self._ask

    def answer(self, data):
        def ask(job, system, prompt):
            self.prompts.append(prompt)
            return json.dumps(data, ensure_ascii=False)
        background.ask = ask

    def test_habits_without_work_and_background(self):
        h = suggestions.habits()
        self.assertIn("turn off all the lights and the music down (6×)", h)
        self.assertNotIn("Meeting", h)
        self.assertNotIn("background", h)

    def test_routine_suggestion_needs_card(self):
        self.answer({"title": "Evening routine", "reason": "You often switch light and music together in the evening.",
                     "kind": "routine", "routine": {"name": "Evening", "triggers": ["wind down"],
                                                    "steps": [{"do": "All lights off"}, {"say": "Have a nice evening."}]}})
        pid = suggestions.suggest(NOW)
        p = proposals.get(pid)
        self.assertEqual(p["kind"], "routine")
        self.assertEqual(routines.load(), [])                         # nothing set up yet
        self.assertIn("confirmation card", events._LOG[-1]["text"])
        note = (memory.MEMORY_DIR / "Suggestions.md").read_text(encoding="utf-8")
        self.assertIn("· open · Evening routine", note)
        self.assertNotIn("Meeting", self.prompts[0])
        proposals.accept(pid)                                         # what the card's yes does
        self.assertEqual(routines.load()[0]["triggers"], ["wind down"])
        self.assertIn("· accepted · Evening routine",
                      (memory.MEMORY_DIR / "Suggestions.md").read_text(encoding="utf-8"))

    def test_invalid_routine_becomes_idea(self):
        self.answer({"title": "Door open", "reason": "x", "kind": "routine",
                     "routine": {"name": "k", "triggers": ["save"],
                                 "steps": [{"ha": "lock.unlock", "entity_id": "lock.front_door"}]}})
        self.assertEqual(proposals.get(suggestions.suggest(NOW))["kind"], "idea")

    def test_reminder_and_idea(self):
        self.answer({"title": "Golf weather on Friday", "reason": "You ask about the weather on Fridays.",
                     "kind": "reminder", "reminder": {"at": "2026-10-30 17:00", "text": "Check the golf weather"}})
        p = proposals.get(suggestions.suggest(NOW))
        self.assertEqual((p["kind"], p["payload"]["at"]), ("reminder", "2026-10-30 17:00"))
        self.answer({"title": "Sleep traffic light", "reason": "You often ask about sleep.", "kind": "idea"})
        self.assertIn("Claude Code", events._LOG[-1]["text"] if suggestions.suggest(NOW) else "")

    def test_no_work_and_no_repeats(self):
        self.answer({"title": "Customer briefing", "reason": "For your meetings.", "kind": "idea"})
        self.assertIsNone(suggestions.suggest(NOW))
        self.answer({"title": "Sleep traffic light", "reason": "x", "kind": "idea"})
        self.assertIsNotNone(suggestions.suggest(NOW))
        self.assertIsNone(suggestions.suggest(NOW))                  # same title again

    def test_when(self):
        self.assertTrue(suggestions.due(NOW))
        self.assertFalse(suggestions.due(NOW.replace(hour=18, minute=0)))
        self.assertFalse(suggestions.due(NOW - dt.timedelta(days=1)))   # Monday
        self.assertTrue(suggestions.due(dt.datetime(2026, 10, 30, 19, 0)))  # Friday
        old = suggestions.AT
        suggestions.AT = "23:00"
        try:
            self.assertFalse(suggestions.due(NOW.replace(hour=23, minute=5)))   # quiet hours
        finally:
            suggestions.AT = old


if __name__ == "__main__":
    unittest.main()
