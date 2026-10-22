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

SWITCH = [("mcp__jarvis__home_control", {"entity_id": "light.leselampe", "service": "turn_off"})]


class Factory(unittest.TestCase):
    def setUp(self):
        for p in (learning.PATH, sf.HISTORY, proposals.PATH, routines.PATH):
            if p.exists():
                p.unlink()
        learning._CACHE["mtime"] = None
        self._ents = sf._ha_entities
        sf._ha_entities = lambda: {"light.leselampe", "light.stehlampe", "lock.haustuer"}

    def tearDown(self):
        sf._ha_entities = self._ents

    def test_draft_not_active(self):
        self.assertEqual(learning.observe("Mach die Leselampe aus", SWITCH), "leselampe")
        self.assertEqual(learning.alias("leselampe aus"), [])
        self.assertEqual(proposals.pending(), [])            # once is not yet a habit

    def test_recurring_becomes_proposal_and_card_activates(self):
        learning.observe("Mach die Leselampe aus", SWITCH)
        learning.observe("Leselampe aus bitte", SWITCH)
        p = proposals.pending()[0]
        self.assertEqual(p["kind"], "skill")
        self.assertIn("Prüfung bestanden", proposals.describe(p))
        self.assertIn("Version 1", proposals.accept(p["id"]))
        self.assertEqual(learning.alias("leselampe"), ["light.leselampe"])
        self.assertEqual(sf.history("leselampe")[-1]["action"], "activate")
        self.assertIsNone(learning.observe("Leselampe aus", SWITCH))     # already live: nothing new

    def test_checks_block(self):
        learning.observe("Mach das Ding aus", [("home_control", {"entity_id": "light.weg", "service": "turn_off"})])
        self.assertIn("Gibt es nicht", sf.activate("ding"))
        learning.observe("Tür auf", [("home_control", {"entity_id": "lock.haustuer", "service": "turn_off"})])
        self.assertIn("schalte ich nicht", sf.activate("auf tür"))
        routines.tool("routine_save", {"name": "Lesen", "triggers": ["leselampe"], "steps": [{"say": "x"}]})
        learning.observe("Leselampe aus", SWITCH)
        self.assertIn("Routine „Lesen“", sf.activate("leselampe"))
        self.assertEqual(learning.alias("leselampe"), [])

    def test_versions_and_undo(self):
        learning.observe("Leselampe aus", SWITCH)
        sf.activate("leselampe")
        learning.observe("Leselampe aus", [("home_control", {"entity_id": "light.stehlampe", "service": "turn_off"})])
        problems, notes = sf.check("leselampe")
        self.assertIn("Ersetzt die bisherige Regel", " ".join(notes))
        self.assertIn("Version 2", sf.activate("leselampe"))
        self.assertEqual(learning.alias("leselampe"), ["light.stehlampe"])
        self.assertIn("Version 1", sf.undo("Leselampe"))
        self.assertEqual(learning.alias("leselampe"), ["light.leselampe"])
        self.assertIn("entfernt", sf.undo("Leselampe"))
        self.assertEqual(learning.alias("leselampe"), [])
        self.assertIn("Keine aktive", sf.undo("Leselampe"))

    def test_old_rules_stay_and_can_be_removed(self):
        learning._save({"aliases": {"kino": {"entities": ["scene.kino"], "example": "Kino an"}}})
        self.assertEqual(learning.alias("kino"), ["scene.kino"])
        self.assertIn("aktiv v1", sf.listing())
        self.assertIn("entfernt", sf.undo("kino"))

    def test_undo_needs_card(self):
        self.assertEqual(policy.action_class("skill_undo"), policy.CONFIRM)
        self.assertNotIn("skill_undo", runtime.NO_CONFIRM)
        self.assertIn("skill_list", runtime.NO_CONFIRM)

    def test_history_is_json_lines(self):
        learning.observe("Leselampe aus", SWITCH)
        sf.activate("leselampe")
        for line in sf.HISTORY.read_text(encoding="utf-8").splitlines():
            json.loads(line)


if __name__ == "__main__":
    unittest.main()
