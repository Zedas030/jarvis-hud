"""
Lagebericht: Uptime Kuma, the security centre and the energy loop. No network needed.

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

PAGE = {"publicGroupList": [{"name": "Heimnetz", "monitorList": [
    {"id": 1, "name": "DNS · Pi-hole"}, {"id": 2, "name": "Home Assistant"}, {"id": 3, "name": "MQTT · Mosquitto"},
    {"id": 4, "name": "Ohne Daten"}]}]}
BEATS = {"heartbeatList": {
    "1": [{"status": 1, "time": "2026-10-28 11:58:00.000"}, {"status": 1, "time": "2026-10-28 11:59:00.000"}],
    "2": [{"status": 1, "time": "2026-10-28 11:59:00.000"}, {"status": 0, "time": "2026-10-28 11:40:00.000"}],
    "3": [{"status": 1, "time": "2026-10-28 11:30:00.000"}, {"status": 0, "time": "2026-10-28 11:45:00.000"},
          {"status": 0, "time": "2026-10-28 11:50:00.000"}]}}

REPORT = {"generated": "2026-10-28T12:45:35+01:00", "score": 87, "counts": {"hoch": 1, "mittel": 2, "niedrig": 1},
          "findings": [{"sev": "mittel", "title": "Unbekanntes Gerät im Netz: 10.0.0.159"},
                       {"sev": "niedrig", "title": "Alter Schlüssel"},
                       {"sev": "hoch", "title": "SSH von außen offen"},
                       {"sev": "mittel", "title": "WPS an"},
                       {"sev": "info", "title": "nur zur Info"}]}

ENERGY = {"entities": {"sensor.hausverbrauch": "Hausverbrauch", "sensor.soc": "Akkustand"},
          "online": {"binary_sensor.noah": "Der Speicher"},
          "automations": {"automation.strom": 0, "automation.mqtt": 300}}


def states(**over):
    s = {"sensor.hausverbrauch": {"state": "428"}, "sensor.soc": {"state": "40"},
         "binary_sensor.noah": {"state": "on"},
         "automation.strom": {"state": "on", "attributes": {"friendly_name": "StromAutomatisierung",
                                                             "last_triggered": "2026-10-28T07:25:00+00:00"}},
         "automation.mqtt": {"state": "on", "attributes": {"friendly_name": "Hausverbrauch → MQTT",
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
                         "Uptime Kuma: 1 von 3 Diensten ausgefallen — MQTT · Mosquitto seit 15 Minuten.")
        self.assertEqual(situation.say_kuma(mons[:2]), "Uptime Kuma: alle 2 Dienste erreichbar.")

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
        self.assertTrue(text.startswith("Sicherheit: 87 von 100 Punkten, 1 dringender Befund, 2 mittel und 1 niedrig."))
        self.assertIn("Hoch: SSH von außen offen.", text)
        self.assertNotIn("Alter Schlüssel", text)
        self.assertNotIn("Achtung", text)

    def test_stale_scan_is_named(self):
        old = dict(REPORT, generated="2026-10-28T06:00:00+01:00")
        self.assertIn("Achtung, die letzte Prüfung ist 7 Stunden alt.", situation.say_security(old, now=NOW))

    def test_only_announced_severities_and_resolved_come_back(self):
        f = situation.security_findings(REPORT, {}, known=["security:Altes Problem", "pi"], now=NOW)
        bad = [x["key"] for x in f if not x["ok"]]
        self.assertEqual(bad, ["security:SSH von außen offen"])
        gone = next(x for x in f if x["key"] == "security:Altes Problem")
        self.assertTrue(gone["ok"])
        self.assertEqual(gone["up"], "Erledigt laut Sicherheitszentrale: Altes Problem.")


class Energy(unittest.TestCase):
    def test_all_fine(self):
        self.assertEqual(situation.energy_problems(ENERGY, states(), now=NOW), [])
        self.assertEqual(situation.say_energy([]), "Energie-Kreislauf: läuft.")

    def test_problems(self):
        st = states(sensor__soc={"state": "unavailable"}, binary_sensor__noah={"state": "off"},
                    automation__strom={"state": "off", "attributes": {"friendly_name": "StromAutomatisierung"}},
                    automation__mqtt={"state": "on", "attributes": {"friendly_name": "Hausverbrauch → MQTT",
                                                                     "last_triggered": "2026-10-28T11:40:00+00:00"}})
        texts = [t for _, t in situation.energy_problems(ENERGY, st, now=NOW)]
        self.assertEqual(texts, ["Dem Energie-Kreislauf fehlen Werte: Akkustand.",
                                 "Der Speicher hat keine Verbindung.",
                                 "Die Automation „StromAutomatisierung“ ist aus.",
                                 "Die Automation „Hausverbrauch → MQTT“ ist seit 20 Minuten nicht gelaufen."])

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
        self.assertEqual(out, ["Die Sicherheitszentrale meldet: SSH von außen offen."])
        fixed = dict(REPORT, findings=[])
        known = list(commands.load()["homelab"])
        out = hw.process(situation.security_findings(fixed, {}, known, now=NOW), now=601, quiet=False)
        self.assertEqual(out, ["Erledigt laut Sicherheitszentrale: SSH von außen offen."])


class Router(unittest.TestCase):
    def test_routes(self):
        with mock.patch.object(situation, "report", side_effect=lambda topic="alles": topic):
            self.assertEqual(router._situation(router._norm("Jarvis, Lagebericht bitte")), "alles")
            self.assertEqual(router._situation(router._norm("Läuft alles?")), "alles")
            self.assertEqual(router._situation(router._norm("Was sagt Uptime Kuma?")), "kuma")
            self.assertEqual(router._situation(router._norm("Wie ist die Sicherheitslage?")), "sicherheit")
            self.assertEqual(router._situation(router._norm("Läuft die Nulleinspeisung?")), "energie")
            self.assertIsNone(router._situation(router._norm("Warum meldet die Sicherheitszentrale das?")))
            self.assertIsNone(router._situation(router._norm("Mach das Licht aus")))


if __name__ == "__main__":
    unittest.main()
