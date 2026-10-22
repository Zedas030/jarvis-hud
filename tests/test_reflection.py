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
    path = memory.DIARY_DIR / f"Gespräch {day}.md"
    text = f"---\ntype: gespraech\nupdated: {day}\n---\n\n# Gespräch {day}\n\nAutomatisches Protokoll.\n"
    for t, u, a in rows:
        text += f"\n**{t} · User:** {u}\n\n**Jarvis:** {a}\n"
    path.write_text(text, encoding="utf-8")
    return path


class Base(unittest.TestCase):
    def setUp(self):
        PROMPTS.clear()
        for name in ("Lernprotokoll", "Vorlieben"):
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
        self.assertTrue(privacy.is_work("Wie lief das Meeting mit dem Chef?"))
        self.assertTrue(privacy.is_work("Schick das an Contoso"))          # JARVIS_WORK_TERMS
        self.assertTrue(privacy.is_work("Ich muss morgen ins Büro"))
        self.assertFalse(privacy.is_work("Die Gartenarbeit war anstrengend"))
        self.assertFalse(privacy.is_work("Mach das Licht im Wohnzimmer aus"))

    def test_conversation_log_skips_work(self):
        memory.log_turn("Erinnere mich an das Meeting mit den Kollegen", "Gern, Sir.")
        memory.log_turn("Wie wird das Wetter?", "Sonnig, Sir.")
        text = (memory.DIARY_DIR / f"Gespräch {time.strftime('%Y-%m-%d')}.md").read_text(encoding="utf-8")
        self.assertNotIn("Kollegen", text)
        self.assertIn("Arbeitsthema, nicht protokolliert", text)
        self.assertIn("Wie wird das Wetter?", text)

    def test_memory_save_refuses_work(self):
        out = jarvis_mcp._memory("memory_save", {"note": "Wissen", "text": "Der Kunde Contoso zahlt spät"})
        self.assertIn("Arbeitsthemen", out)


class Loop(Base):
    def test_parse_exchanges(self):
        p = diary("2026-10-20", [("10:00", "Licht aus", "Erledigt."), ("10:05", "Nein, ich meinte die Küche", "Ah.")])
        ex = reflection.exchanges(p.read_text(encoding="utf-8"))
        self.assertEqual(len(ex), 2)
        self.assertEqual(ex[1][1], "Nein, ich meinte die Küche")

    def test_reflect_writes_log_without_work(self):
        today = time.strftime("%Y-%m-%d")
        diary(today, [("09:00", "Mach das Licht im Wohnzimmer aus", "Erledigt, Sir."),
                      ("09:01", "Nein, ich meinte die Küche", "Verzeihung, Küche ist aus."),
                      ("09:05", "Was steht beim Kunden Contoso an?", "Das weiß ich nicht.")])
        background.ask = fake_ask({"gut": ["Schnell geschaltet"],
                                   "korrigiert": ["Wohnzimmer statt Küche geschaltet — erst nachfragen"],
                                   "lehren": ["Bei mehrdeutigen Räumen kurz nachfragen", "Termine beim Kunden vorbereiten"]})
        n = reflection.reflect()
        self.assertEqual(n, 3)                                 # the work lesson was dropped
        self.assertNotIn("Contoso", PROMPTS[0][1])             # and the model never saw the work exchange
        self.assertIn("Nein, ich meinte", PROMPTS[0][1])
        log = (memory.MEMORY_DIR / "Lernprotokoll.md").read_text(encoding="utf-8")
        self.assertIn("- Korrigiert: Wohnzimmer statt Küche", log)
        self.assertNotIn("Kunde", log)
        self.assertEqual(reflection.reflect(), 0)               # nothing new: no second call
        self.assertEqual(len(PROMPTS), 1)

    def test_waits_for_the_conversation_to_end(self):
        diary(time.strftime("%Y-%m-%d"), [("09:00", "a", "b"), ("09:01", "c", "d")])
        commands.update_state(lambda d: d.__setitem__("last_turn_at", time.time()))
        background.ask = fake_ask({"gut": ["x"]})
        self.assertEqual(reflection.reflect(), 0)
        self.assertEqual(PROMPTS, [])

    def test_no_model_keeps_position(self):
        diary(time.strftime("%Y-%m-%d"), [("09:00", "a", "b"), ("09:01", "c", "d")])
        background.ask = lambda *a: None
        self.assertEqual(reflection.reflect(), 0)
        self.assertFalse(commands.load().get("reflection_pos"))


class Rules(Base):
    def test_consolidate_makes_a_proposal_not_a_rule(self):
        reflection.append_log({"korrigiert": ["Zu lange Antwort"], "lehren": ["Kürzer antworten"]})
        reflection.append_log({"lehren": ["Kürzer antworten, höchstens zwei Sätze"]})
        background.ask = fake_ask({"regeln": [{"regel": "Antworte in höchstens zwei Sätzen.", "grund": "Zweimal zu lang"},
                                              {"regel": "Bereite Kundentermine vor.", "grund": "Arbeit"}]})
        before = len(events._LOG)
        pid = reflection.consolidate()
        p = proposals.get(pid)
        self.assertEqual(p["kind"], "rules")
        self.assertEqual([r["regel"] for r in p["payload"]["rules"]], ["Antworte in höchstens zwei Sätzen."])
        self.assertEqual(reflection.rules_list(), [])            # nothing written before the card
        self.assertIn("Regeln übernehmen", events._LOG[-1]["text"])
        self.assertGreater(len(events._LOG), before)

    def test_accept_writes_section_and_prompt(self):
        pid = proposals.add("rules", "1 Regel", "Test", {"rules": [{"regel": "Frag bei Räumen nach.", "grund": "x"}]},
                            source="reflection")
        self.assertIn(pid, proposals.block())
        self.assertIn("1 Regel(n)", proposals.accept(pid))
        self.assertEqual(reflection.rules_list(), ["Frag bei Räumen nach."])
        self.assertIn("Frag bei Räumen nach.", reflection.rules_block())
        self.assertIn("Frag bei Räumen nach.", commands.lean_system([]))
        self.assertEqual(proposals.accept(pid), "Dieser Vorschlag ist nicht mehr offen.")
        self.assertEqual(proposals.block(), "")

    def test_memory_save_after_section_stays_outside(self):
        reflection.apply_rules(["Regel eins."])
        jarvis_mcp._memory("memory_save", {"note": "Vorlieben", "text": "Mag kurze Antworten"})
        self.assertEqual(reflection.rules_list(), ["Regel eins."])
        self.assertIn("Mag kurze Antworten", (memory.MEMORY_DIR / "Vorlieben.md").read_text(encoding="utf-8"))

    def test_duplicates_cap_and_remove(self):
        reflection.apply_rules(["Regel eins."])
        self.assertIn("Nichts Neues", reflection.apply_rules(["regel EINS"]))
        reflection.apply_rules([f"Regel {i}." for i in range(30)])
        self.assertEqual(len(reflection.rules_list()), reflection.MAX_RULES)
        self.assertIn("gestrichen", reflection.remove_rule("Regel 29"))
        self.assertNotIn("Regel 29.", reflection.rules_list())
        self.assertIn("Keine gelernte Regel", reflection.remove_rule("gibt es nicht"))

    def test_decline_and_expiry(self):
        pid = proposals.add("idea", "Idee", "weil", source="suggestions")
        self.assertEqual(proposals.decline(pid), "Verworfen.")
        self.assertEqual(proposals.pending(), [])
        old = proposals.add("idea", "Alt", "weil")
        proposals._update(lambda items: [p.__setitem__("expires", 0) for p in items if p["id"] == old])
        self.assertEqual(proposals.pending(), [])

    def test_week_claim_once(self):
        self.assertTrue(background.claim("reflection-week", "2026-KW43"))
        self.assertFalse(background.claim("reflection-week", "2026-KW43"))
        self.assertTrue(background.claim("reflection-week", "2026-KW44"))

    def test_parse_json_from_prose(self):
        self.assertEqual(background.parse_json('Klar! ```json\n{"a": {"b": 1}}\n``` fertig'), {"a": {"b": 1}})
        self.assertIsNone(background.parse_json("kein json"))


if __name__ == "__main__":
    unittest.main()
