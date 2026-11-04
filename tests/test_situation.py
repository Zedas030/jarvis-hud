"""
Situation report: Uptime Kuma, the security centre and the energy loop. No network needed.

    .venv\\Scripts\\python.exe -m unittest tests.test_situation -v
"""
import datetime as dt
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import commands          # noqa: E402
import homelab_watch as hw   # noqa: E402
import router            # noqa: E402
import situation         # noqa: E402

NOW = dt.datetime(2026, 10, 28, 12, 0, tzinfo=dt.timezone.utc)

PAGE = {"publicGroupList": [{"name": "Home network", "monitorList": [
    {"id": 1, "name": "DNS · Pi-hole"}, {"id": 2, "name": "Home Assistant"}, {"id": 3, "name": "MQTT · Mosquitto"},
    {"id": 4, "name": "No data"}]}]}
BEATS = {"heartbeatList": {
    "1": [{"status": 1, "time": "2026-10-28 11:58:00.000"}, {"status": 1, "time": "2026-10-28 11:59:00.000"}],
    "2": [{"status": 1, "time": "2026-10-28 11:59:00.000"}, {"status": 0, "time": "2026-10-28 11:40:00.000"}],
    "3": [{"status": 1, "time": "2026-10-28 11:30:00.000"}, {"status": 0, "time": "2026-10-28 11:45:00.000"},
          {"status": 0, "time": "2026-10-28 11:50:00.000"}]}}

REPORT = {"generated": "2026-10-28T12:45:35+01:00", "score": 87, "counts": {"high": 1, "medium": 2, "low": 1},
          "findings": [{"sev": "medium", "title": "Unknown device on the network: 10.0.0.159"},
                       {"sev": "low", "title": "Old key"},
                       {"sev": "high", "title": "SSH open from outside"},
                       {"sev": "medium", "title": "WPS on"},
                       {"sev": "info", "title": "for information only"}]}

ENERGY = {"entities": {"sensor.house_consumption": "House consumption", "sensor.soc": "Battery level"},
          "online": {"binary_sensor.noah": "The battery"},
          "automations": {"automation.power": 0, "automation.mqtt": 300}}


def states(**over):
    s = {"sensor.house_consumption": {"state": "428"}, "sensor.soc": {"state": "40"},
         "binary_sensor.noah": {"state": "on"},
         "automation.power": {"state": "on", "attributes": {"friendly_name": "PowerAutomation",
                                                             "last_triggered": "2026-10-28T07:25:00+00:00"}},
         "automation.mqtt": {"state": "on", "attributes": {"friendly_name": "House consumption → MQTT",
                                                            "last_triggered": "2026-10-28T11:58:00+00:00"}}}
    for k, v in over.items():
        s[k.replace("__", ".")] = v
    return s


class Kuma(unittest.TestCase):
    def test_down_since_first_beat_of_streak(self):
        mons = situation.parse_kuma(PAGE, BEATS, now=NOW)
        self.assertEqual([m["name"] for m in mons], ["DNS · Pi-hole", "Home Assistant", "MQTT · Mosquitto"])
        self.assertTrue(mons[0]["up"])
        self.assertTrue(mons[1]["up"])                  # newest beat counts, not list order
        self.assertFalse(mons[2]["up"])
        self.assertEqual(mons[2]["minutes"], 15)

    def test_spoken(self):
        mons = situation.parse_kuma(PAGE, BEATS, now=NOW)
        self.assertEqual(situation.say_kuma(mons),
                         "Uptime Kuma: 1 of 3 services down — MQTT · Mosquitto for 15 minutes.")
        self.assertEqual(situation.say_kuma(mons[:2]), "Uptime Kuma: all 2 services reachable.")

    def test_findings_skip_and_critical(self):
        mons = situation.parse_kuma(PAGE, BEATS, now=NOW)
        f = situation.kuma_findings(mons, {"kuma_skip": ["home assistant"], "kuma_critical": ["DNS"]})
        self.assertEqual([x["key"] for x in f], ["kuma:DNS · Pi-hole", "kuma:MQTT · Mosquitto"])
        self.assertEqual(f[0]["severity"], "critical")
        self.assertFalse(f[1]["ok"])

    def test_missing_status_page_is_not_an_outage(self):
        import urllib.error
        err = urllib.error.HTTPError("u", 404, "nf", {}, None)
        with mock.patch.object(situation, "KUMA", "http://pi:3001"), mock.patch.object(situation, "_get", side_effect=err):
            with self.assertRaises(situation.NotSetUp):
                situation.kuma()
            self.assertEqual(situation.findings({}), [])

    def test_unknown_slug_never_asks_for_the_page(self):
        # Kuma 1.23 answers an unknown slug's heartbeats with an empty list and hangs on the page itself
        calls = []
        fake = lambda url, timeout=8: calls.append(url) or {"heartbeatList": {}, "uptimeList": {}}  # noqa: E731
        with mock.patch.object(situation, "KUMA", "http://pi:3001"), mock.patch.object(situation, "_get", fake):
            with self.assertRaises(situation.NotSetUp):
                situation.kuma()
        self.assertEqual(calls, ["http://pi:3001/api/status-page/heartbeat/jarvis"])


class Security(unittest.TestCase):
    def test_spoken_orders_by_severity_and_skips_low(self):
        text = situation.say_security(REPORT, now=NOW)
        self.assertTrue(text.startswith("Security: 87 of 100 points, 1 urgent finding, 2 medium and 1 low."))
        self.assertIn("High: SSH open from outside.", text)
        self.assertNotIn("Old key", text)
        self.assertNotIn("Note:", text)

    def test_german_severities_from_the_api_are_read(self):
        german = dict(REPORT, counts={"hoch": 1, "mittel": 2, "niedrig": 1},
                      findings=[dict(f, sev={"high": "hoch", "medium": "mittel", "low": "niedrig"}.get(f["sev"], f["sev"]))
                                for f in REPORT["findings"]])
        self.assertEqual(situation.say_security(german, now=NOW), situation.say_security(REPORT, now=NOW))

    def test_stale_scan_is_named(self):
        old = dict(REPORT, generated="2026-10-28T06:00:00+01:00")
        self.assertIn("Note: the last check is 7 hours old.", situation.say_security(old, now=NOW))

    def test_only_announced_severities_and_resolved_come_back(self):
        f = situation.security_findings(REPORT, {}, known=["security:Old problem", "pi"], now=NOW)
        bad = [x["key"] for x in f if not x["ok"]]
        self.assertEqual(bad, ["security:SSH open from outside"])
        gone = next(x for x in f if x["key"] == "security:Old problem")
        self.assertTrue(gone["ok"])
        self.assertEqual(gone["up"], "Resolved according to the security centre: Old problem.")


class Energy(unittest.TestCase):
    def test_all_fine(self):
        self.assertEqual(situation.energy_problems(ENERGY, states(), now=NOW), [])
        self.assertEqual(situation.say_energy([]), "Energy loop: running.")

    def test_problems(self):
        st = states(sensor__soc={"state": "unavailable"}, binary_sensor__noah={"state": "off"},
                    automation__power={"state": "off", "attributes": {"friendly_name": "PowerAutomation"}},
                    automation__mqtt={"state": "on", "attributes": {"friendly_name": "House consumption → MQTT",
                                                                     "last_triggered": "2026-10-28T11:40:00+00:00"}})
        texts = [t for _, t in situation.energy_problems(ENERGY, st, now=NOW)]
        self.assertEqual(texts, ["The energy loop is missing values: Battery level.",
                                 "The battery has no connection.",
                                 "The automation “PowerAutomation” is off.",
                                 "The automation “House consumption → MQTT” has not run for 20 minutes."])

    def test_findings_cover_every_part(self):
        f = situation.energy_findings(ENERGY, states(sensor__soc={"state": "unknown"}), now=NOW)
        self.assertEqual(len(f), 4)
        self.assertEqual([x["key"] for x in f if not x["ok"]], ["energy:data"])


class Watch(unittest.TestCase):
    def setUp(self):
        commands.update_state(lambda d: d.pop("homelab", None))

    def test_resolved_security_finding_is_announced_once(self):
        f = situation.security_findings(REPORT, {}, known=[], now=NOW)
        hw.process(f, now=1, quiet=False)
        out = hw.process(f, now=301, quiet=False)
        self.assertEqual(out, ["The security centre reports: SSH open from outside."])
        fixed = dict(REPORT, findings=[])
        known = list(commands.load()["homelab"])
        out = hw.process(situation.security_findings(fixed, {}, known, now=NOW), now=601, quiet=False)
        self.assertEqual(out, ["Resolved according to the security centre: SSH open from outside."])


class Router(unittest.TestCase):
    def test_routes(self):
        with mock.patch.object(situation, "report", side_effect=lambda topic="all": topic):
            self.assertEqual(router._situation(router._norm("Jarvis, situation report please")), "all")
            self.assertEqual(router._situation(router._norm("Is everything running?")), "all")
            self.assertEqual(router._situation(router._norm("What does Uptime Kuma say?")), "kuma")
            self.assertEqual(router._situation(router._norm("How is the security situation?")), "security")
            self.assertEqual(router._situation(router._norm("Is the zero export running?")), "energy")
            self.assertIsNone(router._situation(router._norm("Why does the security centre report that?")))
            self.assertIsNone(router._situation(router._norm("Turn off the light")))


if __name__ == "__main__":
    unittest.main()
