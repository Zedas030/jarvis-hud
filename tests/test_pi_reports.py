"""
Pi reports (Restore Probe, update airlock, power hog detective, Zigbee placement advisor, battery doctor,
automation checker, Homelab drift) — fetch is faked; no network.

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
BATTERY = {"ha_connected": True, "alerts": [],
           "storages": {"system": {"capacity": {"status": "ok", "soh": 92.7, "cap_kwh": 3.7976, "lo": 3.677, "hi": 3.918},
                                   "warranty": {"status": "ok", "end": "2036-04-17", "notes": []}}},
           "cycles": {"system": 74.09}}
CHECKER = {"status": "ok", "trusted": True, "detail": "", "last_check": 0,
           "findings": [{"severity": "info", "title": "wait_template waits 30 min"}]}
LOOP = {"severity": "critical", "title": "Real loop: Jarvis: bedroom night mode"}
CONFLICT = {"severity": "warning", "title": "Conflict at Hue Bedroom"}
DRIFT_TEXT = """# HELP homelab_drift_scan_ok last scan ok
homelab_drift_last_success_timestamp_seconds {now}
homelab_drift_scan_ok 1
homelab_drift_host_reachable{{host="fritzbox"}} 1
homelab_drift_host_reachable{{host="pi"}} 1
homelab_drift_findings{{severity="instant",status="open"}} 0
homelab_drift_findings{{severity="instant",status="acknowledged"}} 10
homelab_drift_findings{{severity="digest",status="open"}} 7
"""


class PiReports(unittest.TestCase):
    def setUp(self):
        self._fetch = pi_reports.fetch
        self.data = {"backups": dict(RESTORE, finished=time.time() - 3600), "updates": AIRLOCK,
                     "power": POWER, "zigbee": ZIGBEE, "battery": BATTERY,
                     "automations": dict(CHECKER, last_check=time.time() - 60),
                     "drift": pi_reports._prom(DRIFT_TEXT.format(now=time.time() - 600))}
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

    def test_battery(self):
        self.assertEqual(pi_reports.report("battery"), ("ok", "The battery still has about 93 percent of its capacity, around "
                                                        "3.8 kilowatt hours usable (3.7 to 3.9). 74 full cycles so far, warranty until 2036."))
        self.assertEqual(pi_reports.report("battery", brief=True), ("ok", ""))
        self.data["battery"] = dict(BATTERY, ha_connected=False, alerts=[{"text": "Temperature too high"}],
                                    storages={"system": {"capacity": {"status": "uncertain"}}})
        sev, text = pi_reports.report("battery")
        self.assertEqual(sev, "attention")
        self.assertIn("not enough yet", text)
        self.assertIn("no data from Home Assistant", text)
        self.assertIn("Temperature too high.", text)

    def test_double_encoded_text_is_repaired(self):
        broken = "verlässlich – gut".encode("utf-8").decode("cp1252")
        self.assertEqual(pi_reports._fix(broken), "verlässlich – gut")
        self.assertEqual(pi_reports._fix("fine as it is"), "fine as it is")

    def test_automations(self):
        self.assertEqual(pi_reports.report("automations"), ("ok", "The automations are fine. Plus 1 tidiness note."))
        self.data["automations"]["findings"] = [LOOP, CONFLICT]
        sev, text = pi_reports.report("automations")
        self.assertEqual(sev, "attention")
        self.assertIn("One critical finding in the automations: Real loop: Jarvis: bedroom night mode.", text)
        self.assertIn("One warning: Conflict at Hue Bedroom.", text)
        self.assertNotIn("Conflict", pi_reports.report("automations", brief=True)[1], "warnings stay out of the briefing")
        self.data["automations"] = dict(CHECKER, findings=[CONFLICT], last_check=time.time() - 60)
        self.assertEqual(pi_reports.report("automations", brief=True)[0], "ok")
        self.data["automations"] = dict(CHECKER, last_check=time.time() - 5 * 3600)
        self.assertIn("has not checked for 5 hours", pi_reports.report("automations")[1])
        self.data["automations"] = dict(CHECKER, status="error", trusted=False, detail="HA does not answer")
        self.assertIn("properly right now: HA does not answer.", pi_reports.report("automations")[1])

    def test_drift_reads_metrics(self):
        m = self.data["drift"]
        self.assertEqual(m[("homelab_drift_findings", (("severity", "digest"), ("status", "open")))], 7.0)
        self.assertEqual(pi_reports.report("drift"),
                         ("ok", "Nothing urgent in the homelab. 7 smaller changes are waiting for the weekly report."))
        self.assertEqual(pi_reports.report("drift", brief=True), ("ok", "Nothing unexpected has changed in the homelab."))
        text = (DRIFT_TEXT.format(now=time.time() - 5 * 3600).replace('host="pi"} 1', 'host="pi"} 0')
                .replace('"instant",status="open"} 0', '"instant",status="open"} 2'))
        self.data["drift"] = pi_reports._prom(text)
        sev, text = pi_reports.report("drift", brief=True)
        self.assertEqual(sev, "attention")
        self.assertIn("has not scanned for 5 hours", text)
        self.assertIn("cannot reach pi", text)
        self.assertIn("2 important changes", text)
        self.data["drift"] = {}
        self.assertEqual(pi_reports.report("drift")[0], "unknown")

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
                 "What do the tools on the Pi say?": "breaking changes",
                 "How healthy is the battery?": "93 percent",
                 "What does the battery doctor say?": "93 percent",
                 "Are there any broken automations?": "The automations are fine",
                 "What does the automation checker say?": "The automations are fine",
                 "Has anything changed in the network?": "7 smaller changes",
                 "What does Homelab drift say?": "7 smaller changes"}
        for text, start in cases.items():
            r = router.route(text)
            self.assertEqual(r.get("handler"), "pi_reports", text)
            self.assertIn(start, r["reply"], text)

    def test_other_questions_are_not_taken(self):
        for text in ("What are my power hogs?", "Are there any updates?", "How is the Pi?",
                     "How full is the battery?", "Create an automation for the light",
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
