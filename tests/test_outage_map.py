"""
Outage map (Blast-Radius) — the HTTP call is faked; no network.

    .venv\\Scripts\\python.exe -m unittest tests.test_outage_map -v
"""
import isolate  # noqa: F401  (must come first)
import unittest

import outage_map
import policy
import router

ANSWER = {
    "matched": {"id": "hw:pi", "label": "the Pi", "say": "der Pi"},
    "alternatives": [{"label": "Raspberry Pi (raspi, OS)"}],
    "summary": "If Raspberry Pi (board) fails, 3 of 17 functions stop working …",
    "functions": {"total": 17,
                  "failed": [{"id": "fn:heating", "label": "Heating", "say": "die Heizung"},
                             {"id": "fn:jarvis", "label": "Jarvis voice assistant"},
                             {"id": "fn:remote", "label": "Remote access via Tailscale"}],
                  "degraded": [{"id": "fn:music", "label": "Music follows me"}]},
}


class OutageMap(unittest.TestCase):
    def setUp(self):
        self._ask = outage_map.ask
        self.asked = []

        def fake(q):
            self.asked.append(q)
            return ANSWER
        outage_map.ask = fake
        router.forget_topic()

    def tearDown(self):
        outage_map.ask = self._ask

    def test_tool_only_reads(self):
        self.assertEqual(policy.action_class("outage_impact"), policy.READ)

    def test_spoken_sentence_uses_english_labels(self):
        self.assertEqual(outage_map.spoken(ANSWER),
                         "If the Pi fails, 3 of 17 functions stop working: Heating, Jarvis voice assistant and "
                         "Remote access via Tailscale. Degraded: Music follows me.")
        none = dict(ANSWER, functions={"total": 17, "failed": [], "degraded": []})
        self.assertEqual(outage_map.spoken(none), "If the Pi fails, no function stops working completely.")
        self.assertIsNone(outage_map.spoken({"matched": None}))

    def test_long_lists_are_counted(self):
        many = dict(ANSWER, functions={"total": 17, "failed": [{"label": f"F{i}"} for i in range(9)], "degraded": []})
        self.assertIn("F5 and 3 more.", outage_map.spoken(many))

    def test_router_answers_outage_questions_locally(self):
        for text in ("What happens if the Pi crashes?", "Jarvis, what fails if the FRITZ!Box goes down?",
                     "What depends on the Zigbee coordinator?", "What breaks when the NAS has no power?"):
            r = router.route(text)
            self.assertEqual(r["kind"], "local", text)
            self.assertEqual(r["handler"], "outage", text)
            self.assertTrue(r["reply"].startswith("If the Pi fails"), text)
        self.assertEqual(self.asked[0], "What happens if the Pi crashes?")  # raw text goes to Blast-Radius

    def test_other_questions_are_not_taken(self):
        for text in ("What happens today?", "How is the Pi?", "Turn the light off",
                     "What happens if I turn the light off?"):
            r = router.route(text)
            self.assertNotEqual(r.get("handler"), "outage", text)

    def test_unknown_device_or_unreachable_falls_back_to_claude(self):
        outage_map.ask = lambda q: {"matched": None}
        r = router.route("What happens if the flux capacitor fails?")
        self.assertNotEqual(r.get("handler"), "outage")

        def boom(q):
            raise OSError("unreachable")
        outage_map.ask = boom
        r = router.route("What happens if the Pi crashes?")
        self.assertNotEqual(r.get("handler"), "outage")
        self.assertIn("homelab", r["groups"])  # Claude then has outage_impact

    def test_mcp_tool_text(self):
        text = outage_map.describe("What happens if the Pi crashes?")
        self.assertIn("Heating", text)
        self.assertIn("Raspberry Pi (raspi, OS)", text)


if __name__ == "__main__":
    unittest.main()
