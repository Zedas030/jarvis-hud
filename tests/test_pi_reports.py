"""
Pi reports (Restore Probe, update airlock, power hog detective, Zigbee placement advisor) — fetch is faked; no network.

    .venv\\Scripts\\python.exe -m unittest tests.test_pi_reports -v
"""
import isolate  # noqa: F401  (must come first)
import time
import unittest

import pi_reports
import policy
import router

RESTORE = {"finished": 0, "status": "ok", "error": "",
           "services": [{"name": "homeassistant", "title": "Home Assistant", "status": "ok"},
                        {"name": "grafana", "title": "Grafana", "status": "ok"}]}
AIRLOCK = {"overall": "attention",
           "global": {"last_postponed": {"reason": "postponed: at home and active (binary_sensor.x was on)"}},
           "containers": {"homeassistant": {"status": "held", "available_version": "2026.11.0",
                                            "held_reason": "breaking_change"},
                          "uptime-kuma": {"status": "ready", "available_version": "2@e884"},
                          "fritz_exporter": {"status": "candidate"},
                          "noah-mqtt": {"status": "up_to_date"}}}
POWER = {"status": "auffällig", "lernfortschritt": {"tv": "14/14", "ipad_station": "11/14"},
         "offen": [{"id": 1, "geraet": "tv", "meldung": "TV: standby too high.", "zustand": "offen"},
                   {"id": 2, "geraet": "pc", "meldung": "PC: old.", "zustand": "erledigt"}]}
ZIGBEE = {"recommendation": {"status": "insufficient_data", "candidates": [], "weak": []},
          "text": ["Noch zu wenig Daten.", "Nur 4 Zigbee-Geräte."],
          "basis": "Datengrundlage: 0,1 Tage.",
          "channel": {"tone": "ok", "headline": "No overlap."}, "health": {"ok": True}}


class PiReports(unittest.TestCase):
    def setUp(self):
        self._fetch = pi_reports.fetch
        self.data = {"backups": dict(RESTORE, finished=time.time() - 3600), "updates": AIRLOCK,
                     "power": POWER, "zigbee": ZIGBEE}
        pi_reports.fetch = lambda topic: self.data[topic]
        router.forget_topic()

    def tearDown(self):
        pi_reports.fetch = self._fetch

    def test_tool_only_reads(self):
        self.assertEqual(policy.action_class("pi_reports"), policy.READ)

    def test_backups(self):
        self.assertEqual(pi_reports.report("backups")[0], "ok")
        self.data["backups"] = dict(self.data["backups"], services=[
            {"title": "Home Assistant", "status": "fail"}, {"title": "Grafana", "status": "ok"}])
        sev, text = pi_reports.report("backups")
        self.assertEqual(sev, "attention")
        self.assertIn("Home Assistant could not be restored cleanly", text)
        self.assertIn("1 of 2", text)
        self.data["backups"] = dict(RESTORE, finished=time.time() - 3 * 86400)
        self.assertIn("3 days old", pi_reports.report("backups")[1])

    def test_updates(self):
        sev, text = pi_reports.report("updates")
        self.assertEqual(sev, "attention")
        self.assertIn("Home Assistant 2026.11.0 is waiting for you because it has breaking changes.", text)
        self.assertIn("2 container updates are pending", text)
        self.assertIn("because you were at home", text)
        # routine updates are left out of the briefing, the held one stays
        self.assertNotIn("container updates", pi_reports.report("updates", brief=True)[1])
        self.data["updates"] = {"overall": "ok", "containers": {"a": {"status": "up_to_date"}}}
        self.assertEqual(pi_reports.report("updates"), ("ok", "All containers are up to date."))

    def test_power_reads_only_open_findings(self):
        sev, text = pi_reports.report("power")
        self.assertEqual(sev, "attention")
        self.assertIn("One open finding. TV: standby too high.", text)
        self.assertNotIn("PC: old", text)
        self.data["power"] = dict(POWER, offen=[])
        self.assertEqual(pi_reports.report("power"), ("ok", "Nothing unusual about the power use. Still learning: 1 device."))

    def test_zigbee(self):
        sev, text = pi_reports.report("zigbee")
        self.assertEqual(sev, "ok")
        self.assertTrue(text.startswith("Not enough data yet"))
        self.data["zigbee"] = dict(ZIGBEE, channel={"tone": "warn", "headline": "WLAN 7 overlaps Zigbee 20."})
        self.assertEqual(pi_reports.report("zigbee")[0], "attention")

    def test_unreachable_or_changed_format_never_raises(self):
        def boom(topic):
            raise OSError("down")
        pi_reports.fetch = boom
        self.assertEqual(pi_reports.report("power"), ("unknown", "The Power hog detective cannot be reached right now."))
        self.assertEqual(pi_reports.briefing_line(), "")
        pi_reports.fetch = lambda topic: {"containers": "broken"}
        self.assertEqual(pi_reports.report("updates")[0], "unknown")

    def test_briefing_only_findings(self):
        line = pi_reports.briefing_line()
        self.assertIn("breaking changes", line)
        self.assertIn("standby too high", line)
        self.assertNotIn("Zigbee", line)
        self.data.update(updates={"containers": {}}, power=dict(POWER, offen=[]))
        self.assertEqual(pi_reports.briefing_line(), "")

    def test_router_answers_locally(self):
        cases = {"Are the backups fine?": "The backups are fine",
                 "What does the update airlock say?": "Home Assistant 2026.11.0",
                 "Anything unusual about the power?": "Power hog detective",
                 "What does the power hog detective say?": "Power hog detective",
                 "Where should I put a Zigbee router?": "Not enough data yet",
                 "What do the tools on the Pi say?": "breaking changes"}
        for text, start in cases.items():
            r = router.route(text)
            self.assertEqual(r.get("handler"), "pi_reports", text)
            self.assertIn(start, r["reply"], text)

    def test_other_questions_are_not_taken(self):
        for text in ("What are my power hogs?", "Are there any updates?", "How is the Pi?",
                     "Why was Home Assistant not updated by the update airlock?", "Turn the light off"):
            r = router.route(text)
            self.assertNotEqual(r.get("handler"), "pi_reports", text)

    def test_why_questions_go_to_claude_with_the_tool(self):
        r = router.route("Why is the backup test result unusual?")
        self.assertEqual(r["kind"], "llm")
        self.assertIn("homelab", r["groups"])

    def test_mcp_tool_text(self):
        text = pi_reports.describe("all")
        self.assertIn("[Restore Probe · ok]", text)
        self.assertIn("[Update airlock · attention]", text)


if __name__ == "__main__":
    unittest.main()
