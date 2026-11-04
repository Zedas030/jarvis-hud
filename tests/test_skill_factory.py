"""
Skill factory: learned router rules are drafts until accepted on a card;
checks, versions and undo. No Home Assistant, nothing is switched.

    .venv\\Scripts\\python.exe -m unittest tests.test_skill_factory -v
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import learning        # noqa: E402
import policy          # noqa: E402
import proposals       # noqa: E402
import routines        # noqa: E402
import runtime         # noqa: E402
import skill_factory as sf   # noqa: E402

SWITCH = [("mcp__jarvis__home_control", {"entity_id": "light.reading_lamp", "service": "turn_off"})]
KEY = "lamp reading"                 # learning.key("Turn off the reading lamp")


class Factory(unittest.TestCase):
    def setUp(self):
        for p in (learning.PATH, sf.HISTORY, proposals.PATH, routines.PATH):
            if p.exists():
                p.unlink()
        learning._CACHE["mtime"] = None
        self._ents = sf._ha_entities
        sf._ha_entities = lambda: {"light.reading_lamp", "light.floor_lamp", "lock.front_door"}

    def tearDown(self):
        sf._ha_entities = self._ents

    def test_draft_not_active(self):
        self.assertEqual(learning.observe("Turn off the reading lamp", SWITCH), KEY)
        self.assertEqual(learning.alias("reading lamp off"), [])
        self.assertEqual(proposals.pending(), [])            # once is not yet a habit

    def test_recurring_becomes_proposal_and_card_activates(self):
        learning.observe("Turn off the reading lamp", SWITCH)
        learning.observe("Reading lamp off please", SWITCH)
        p = proposals.pending()[0]
        self.assertEqual(p["kind"], "skill")
        self.assertIn("Check passed", proposals.describe(p))
        self.assertIn("version 1", proposals.accept(p["id"]))
        self.assertEqual(learning.alias("reading lamp"), ["light.reading_lamp"])
        self.assertEqual(sf.history(KEY)[-1]["action"], "activate")
        self.assertIsNone(learning.observe("Reading lamp off", SWITCH))     # already live: nothing new

    def test_checks_block(self):
        learning.observe("Turn the thing off", [("home_control", {"entity_id": "light.gone", "service": "turn_off"})])
        self.assertIn("Does not exist", sf.activate("thing"))
        learning.observe("Door open", [("home_control", {"entity_id": "lock.front_door", "service": "turn_off"})])
        self.assertIn("I do not switch", sf.activate("door open"))
        routines.tool("routine_save", {"name": "Reading", "triggers": ["reading lamp"], "steps": [{"say": "x"}]})
        learning.observe("Reading lamp off", SWITCH)
        self.assertIn("routine “Reading”", sf.activate(KEY))
        self.assertEqual(learning.alias("reading lamp"), [])

    def test_versions_and_undo(self):
        learning.observe("Reading lamp off", SWITCH)
        sf.activate(KEY)
        learning.observe("Reading lamp off", [("home_control", {"entity_id": "light.floor_lamp", "service": "turn_off"})])
        problems, notes = sf.check(KEY)
        self.assertIn("Replaces the previous rule", " ".join(notes))
        self.assertIn("version 2", sf.activate(KEY))
        self.assertEqual(learning.alias("reading lamp"), ["light.floor_lamp"])
        self.assertIn("version 1", sf.undo("Reading lamp"))
        self.assertEqual(learning.alias("reading lamp"), ["light.reading_lamp"])
        self.assertIn("removed", sf.undo("Reading lamp"))
        self.assertEqual(learning.alias("reading lamp"), [])
        self.assertIn("No active", sf.undo("Reading lamp"))

    def test_old_rules_stay_and_can_be_removed(self):
        learning._save({"aliases": {"cinema": {"entities": ["scene.cinema"], "example": "Cinema on"}}})
        self.assertEqual(learning.alias("cinema"), ["scene.cinema"])
        self.assertIn("active v1", sf.listing())
        self.assertIn("removed", sf.undo("cinema"))

    def test_undo_needs_card(self):
        self.assertEqual(policy.action_class("skill_undo"), policy.CONFIRM)
        self.assertNotIn("skill_undo", runtime.NO_CONFIRM)
        self.assertIn("skill_list", runtime.NO_CONFIRM)

    def test_history_is_json_lines(self):
        learning.observe("Reading lamp off", SWITCH)
        sf.activate(KEY)
        for line in sf.HISTORY.read_text(encoding="utf-8").splitlines():
            json.loads(line)


if __name__ == "__main__":
    unittest.main()
