"""
Learning loop, privacy filter and proposals — without Claude (background.ask is faked).

    .venv\\Scripts\\python.exe -m unittest tests.test_reflection -v
"""
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import background   # noqa: E402
import commands     # noqa: E402
import events       # noqa: E402
import jarvis_mcp   # noqa: E402
import memory       # noqa: E402
import privacy      # noqa: E402
import proposals    # noqa: E402
import reflection   # noqa: E402

PROMPTS = []


def fake_ask(answer):
    def ask(job, system, prompt):
        PROMPTS.append((job, prompt))
        return json.dumps(answer) if not isinstance(answer, str) else answer
    return ask


def diary(day, rows):
    memory.DIARY_DIR.mkdir(parents=True, exist_ok=True)
    path = memory.DIARY_DIR / f"Conversation {day}.md"
    text = f"---\ntype: conversation\nupdated: {day}\n---\n\n# Conversation {day}\n\nAutomatic log.\n"
    for t, u, a in rows:
        text += f"\n**{t} · User:** {u}\n\n**Jarvis:** {a}\n"
    path.write_text(text, encoding="utf-8")
    return path


class Base(unittest.TestCase):
    def setUp(self):
        PROMPTS.clear()
        for name in ("Learning log", "Preferences"):
            p = memory.MEMORY_DIR / f"{name}.md"
            if p.exists():
                p.unlink()
        if proposals.PATH.exists():
            proposals.PATH.unlink()
        commands.update_state(lambda d: [d.pop(k, None) for k in ("reflection_pos", "last_turn_at", "jobs")])
        self._ask = background.ask

    def tearDown(self):
        background.ask = self._ask


class Privacy(Base):
    def test_work_terms(self):
        self.assertTrue(privacy.is_work("How did the meeting with the boss go?"))
        self.assertTrue(privacy.is_work("Send that to Contoso"))          # JARVIS_WORK_TERMS
        self.assertTrue(privacy.is_work("I have to go to the office tomorrow"))
        self.assertFalse(privacy.is_work("The gardening was exhausting"))
        self.assertFalse(privacy.is_work("Turn off the light in the living room"))

    def test_conversation_log_skips_work(self):
        memory.log_turn("Remind me about the meeting with the colleagues", "Gladly, Sir.")
        memory.log_turn("What will the weather be like?", "Sunny, Sir.")
        text = (memory.DIARY_DIR / f"Conversation {time.strftime('%Y-%m-%d')}.md").read_text(encoding="utf-8")
        self.assertNotIn("colleagues", text)
        self.assertIn("work topic, not logged", text)
        self.assertIn("What will the weather be like?", text)

    def test_memory_save_refuses_work(self):
        out = jarvis_mcp._memory("memory_save", {"note": "Knowledge", "text": "The customer Contoso pays late"})
        self.assertIn("work topics", out)


class Loop(Base):
    def test_parse_exchanges(self):
        p = diary("2026-10-20", [("10:00", "Light off", "Done."), ("10:05", "No, I meant the kitchen", "Ah.")])
        ex = reflection.exchanges(p.read_text(encoding="utf-8"))
        self.assertEqual(len(ex), 2)
        self.assertEqual(ex[1][1], "No, I meant the kitchen")

    def test_reflect_writes_log_without_work(self):
        today = time.strftime("%Y-%m-%d")
        diary(today, [("09:00", "Turn off the light in the living room", "Done, Sir."),
                      ("09:01", "No, I meant the kitchen", "Sorry, the kitchen is off."),
                      ("09:05", "What is coming up with the customer Contoso?", "I do not know.")])
        background.ask = fake_ask({"good": ["Switched quickly"],
                                   "corrected": ["Switched the living room instead of the kitchen — ask first"],
                                   "lessons": ["Ask briefly when the room is ambiguous", "Prepare appointments with the customer"]})
        n = reflection.reflect()
        self.assertEqual(n, 3)                                 # the work lesson was dropped
        self.assertNotIn("Contoso", PROMPTS[0][1])             # and the model never saw the work exchange
        self.assertIn("No, I meant", PROMPTS[0][1])
        log = (memory.MEMORY_DIR / "Learning log.md").read_text(encoding="utf-8")
        self.assertIn("- Corrected: Switched the living room instead of the kitchen", log)
        self.assertNotIn("customer", log)
        self.assertEqual(reflection.reflect(), 0)               # nothing new: no second call
        self.assertEqual(len(PROMPTS), 1)

    def test_waits_for_the_conversation_to_end(self):
        diary(time.strftime("%Y-%m-%d"), [("09:00", "a", "b"), ("09:01", "c", "d")])
        commands.update_state(lambda d: d.__setitem__("last_turn_at", time.time()))
        background.ask = fake_ask({"good": ["x"]})
        self.assertEqual(reflection.reflect(), 0)
        self.assertEqual(PROMPTS, [])

    def test_no_model_keeps_position(self):
        diary(time.strftime("%Y-%m-%d"), [("09:00", "a", "b"), ("09:01", "c", "d")])
        background.ask = lambda *a: None
        self.assertEqual(reflection.reflect(), 0)
        self.assertFalse(commands.load().get("reflection_pos"))


class Rules(Base):
    def test_consolidate_makes_a_proposal_not_a_rule(self):
        reflection.append_log({"corrected": ["Answer too long"], "lessons": ["Answer more briefly"]})
        reflection.append_log({"lessons": ["Answer more briefly, two sentences at most"]})
        background.ask = fake_ask({"rules": [{"rule": "Answer in two sentences at most.", "why": "Too long twice"},
                                             {"rule": "Prepare customer appointments.", "why": "Work"}]})
        before = len(events._LOG)
        pid = reflection.consolidate()
        p = proposals.get(pid)
        self.assertEqual(p["kind"], "rules")
        self.assertEqual([r["rule"] for r in p["payload"]["rules"]], ["Answer in two sentences at most."])
        self.assertEqual(reflection.rules_list(), [])            # nothing written before the card
        self.assertIn("accept the rules", events._LOG[-1]["text"])
        self.assertGreater(len(events._LOG), before)

    def test_accept_writes_section_and_prompt(self):
        pid = proposals.add("rules", "1 rule", "Test", {"rules": [{"rule": "Ask about rooms.", "why": "x"}]},
                            source="reflection")
        self.assertIn(pid, proposals.block())
        self.assertIn("1 rule(s)", proposals.accept(pid))
        self.assertEqual(reflection.rules_list(), ["Ask about rooms."])
        self.assertIn("Ask about rooms.", reflection.rules_block())
        self.assertIn("Ask about rooms.", commands.lean_system([]))
        self.assertEqual(proposals.accept(pid), "This proposal is no longer open.")
        self.assertEqual(proposals.block(), "")

    def test_memory_save_after_section_stays_outside(self):
        reflection.apply_rules(["Rule one."])
        jarvis_mcp._memory("memory_save", {"note": "Preferences", "text": "Likes short answers"})
        self.assertEqual(reflection.rules_list(), ["Rule one."])
        self.assertIn("Likes short answers", (memory.MEMORY_DIR / "Preferences.md").read_text(encoding="utf-8"))

    def test_duplicates_cap_and_remove(self):
        reflection.apply_rules(["Rule one."])
        self.assertIn("Nothing new", reflection.apply_rules(["rule ONE"]))
        reflection.apply_rules([f"Rule {i}." for i in range(30)])
        self.assertEqual(len(reflection.rules_list()), reflection.MAX_RULES)
        self.assertIn("dropped", reflection.remove_rule("Rule 29"))
        self.assertNotIn("Rule 29.", reflection.rules_list())
        self.assertIn("No learned rule", reflection.remove_rule("does not exist"))

    def test_decline_and_expiry(self):
        pid = proposals.add("idea", "Idea", "because", source="suggestions")
        self.assertEqual(proposals.decline(pid), "Discarded.")
        self.assertEqual(proposals.pending(), [])
        old = proposals.add("idea", "Old", "because")
        proposals._update(lambda items: [p.__setitem__("expires", 0) for p in items if p["id"] == old])
        self.assertEqual(proposals.pending(), [])

    def test_week_claim_once(self):
        self.assertTrue(background.claim("reflection-week", "2026-W43"))
        self.assertFalse(background.claim("reflection-week", "2026-W43"))
        self.assertTrue(background.claim("reflection-week", "2026-W44"))

    def test_parse_json_from_prose(self):
        self.assertEqual(background.parse_json('Sure! ```json\n{"a": {"b": 1}}\n``` done'), {"a": {"b": 1}})
        self.assertIsNone(background.parse_json("no json"))


if __name__ == "__main__":
    unittest.main()
