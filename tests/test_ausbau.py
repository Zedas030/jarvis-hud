"""
Electricity costs, the check when leaving home, the weekly homelab report and working
through security findings. No network, no house.

    .venv\\Scripts\\python.exe -m unittest tests.test_ausbau -v
"""
import datetime as dt
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import away_check        # noqa: E402
import commands          # noqa: E402
import homelab_review    # noqa: E402
import policy            # noqa: E402
import power_costs       # noqa: E402
import proposals         # noqa: E402
import router            # noqa: E402
import situation         # noqa: E402

UTC = dt.timezone.utc


def plug(base, name, on=True, watts=0.0, month=0.0):
    return {f"switch.{base}": {"entity_id": f"switch.{base}", "state": "on" if on else "off",
                               "attributes": {"friendly_name": name}},
            f"sensor.{base}_derzeitiger_verbrauch": {"entity_id": f"sensor.{base}_derzeitiger_verbrauch", "state": str(watts)},
            f"sensor.{base}_der_verbrauch_dieses_monats": {"entity_id": "x", "state": str(month)}}


class Costs(unittest.TestCase):
    def setUp(self):
        self.st = {**plug("pc", "PC", month=80), **plug("tv_schlafzimmer", "TV Schlafzimmer", month=15),
                   **plug("kuche_boiler", "Küche Boiler", on=False, month=0.2),
                   "sensor.hausverbrauch_diesen_monat": {"state": "171"}}
        self.now = dt.datetime(2026, 10, 28, 12)

    def test_month_and_projection(self):
        text = power_costs.say_month(self.st, self.now)
        self.assertIn("Das Haus hat diesen Monat bisher 171 Kilowattstunden verbraucht, 51,30 Euro", text)
        self.assertIn("PC 80,0 kWh (24,00 Euro), TV Schlafzimmer 15,0 kWh (4,50 Euro)", text)
        self.assertIn("30 Cent pro Kilowattstunde", text)

    def test_one_device(self):
        text = power_costs.say_month(self.st, self.now, "pc")
        self.assertTrue(text.startswith("PC: diesen Monat bisher 80,0 Kilowattstunden, also 24,00 Euro."))
        self.assertIn("Zu „Toaster“ finde ich keinen Stromzähler", power_costs.say_month(self.st, self.now, "Toaster"))

    def test_standby_needs_days_and_skips_always_on(self):
        mem = {"devices": {"switch.tv_schlafzimmer": {"name": "TV Schlafzimmer", "min_w": {"a": 18, "b": 20, "c": 19}},
                           "switch.kuhlschrank": {"name": "Kühlschrank", "min_w": {"a": 1, "b": 60, "c": 50}},
                           "switch.pc": {"name": "PC", "min_w": {"a": 0.5, "b": 0.4, "c": 0.6}}}}
        text = power_costs.say_standby(mem, {"always_on": ["switch.kuhlschrank"]}, {})
        self.assertEqual(text, "Im Leerlauf ziehen Strom: TV Schlafzimmer mit etwa 19 Watt (49,93 Euro im Jahr). "
                               "Eine Steckdose aus spart genau das.")
        young = {"devices": {"switch.tv": {"name": "TV", "min_w": {"a": 20}}}}
        self.assertIn("mindestens 3 Tage", power_costs.say_standby(young, {}, {}))

    def test_route(self):
        with mock.patch.object(power_costs, "report", side_effect=lambda device="", standby=False: f"{device}|{standby}"):
            r = lambda s: router._costs(router._norm(s))  # noqa: E731
            self.assertEqual(r("Was kostet mich der PC im Monat?"), "pc|False")
            self.assertEqual(r("Wer ist hier der Stromfresser im Standby?"), "|True")
            self.assertEqual(r("Wie hoch sind die Stromkosten?"), "|False")
            self.assertIsNone(r("Was kostet ein Döner?"))


class Away(unittest.TestCase):
    CFG = {"switches": {"switch.tv": 30, "switch.kuche_boiler": 20}}

    def states(self):
        s = {**plug("tv", "TV", watts=85), **plug("kuche_boiler", "Küche Boiler", on=True, watts=0.5),
             "light.licht": {"entity_id": "light.licht", "state": "on", "attributes": {"friendly_name": "Licht"}},
             "light.flur": {"entity_id": "light.flur", "state": "off", "attributes": {}},
             "media_player.sonos": {"entity_id": "media_player.sonos", "state": "playing",
                                    "attributes": {"friendly_name": "Sonos"}}}
        return s

    def test_what_still_runs(self):
        found = away_check.still_on(self.states(), self.CFG)
        self.assertEqual([e for e, _ in found], ["light.licht", "media_player.sonos", "switch.tv"])
        self.assertEqual(found[2][1], "TV (85 W)")              # the boiler plug is on but idle: not named

    def test_leaving_once_after_a_few_minutes(self):
        now = dt.datetime(2026, 10, 28, 12, tzinfo=UTC)
        left = {"state": "not_home", "last_changed": "2026-10-28T11:58:00+00:00"}
        self.assertIsNone(away_check.left_home(left, "", now))                       # 2 min: wait
        later = now + dt.timedelta(minutes=2)
        self.assertEqual(away_check.left_home(left, "", later), left["last_changed"])
        self.assertIsNone(away_check.left_home(left, left["last_changed"], later))   # handled
        self.assertIsNone(away_check.left_home({"state": "home"}, "", later))
        self.assertIsNone(away_check.left_home(left, "", now + dt.timedelta(hours=2)))  # Jarvis was off: too late

    def test_check_makes_a_proposal_and_never_switches(self):
        commands.update_state(lambda d: d.pop("away_checked", None))
        st = list(self.states().values()) + [{"entity_id": "person.nutzer", "state": "not_home",
                                              "last_changed": "2026-10-28T11:50:00+00:00"}]
        prof = {"person": "person.nutzer", "away_check": self.CFG}
        with mock.patch("jarvis_profile.ha", side_effect=lambda k, d=None: prof.get(k, d)), \
                mock.patch("infra.phone_notify") as push, mock.patch("jarvis_mcp._home") as switch:
            text = away_check.check(st, now=dt.datetime(2026, 10, 28, 12, tzinfo=UTC))
            self.assertIn("zu Hause läuft noch: Licht, Sonos, TV (85 W)", text)
            push.assert_called_once()
            switch.assert_not_called()
            self.assertIsNone(away_check.check(st, now=dt.datetime(2026, 10, 28, 12, 5, tzinfo=UTC)))
        p = [x for x in proposals.pending() if x["kind"] == "switch_off"][-1]
        self.assertEqual(p["payload"]["entities"], ["light.licht", "media_player.sonos", "switch.tv"])
        self.assertIn("• TV (85 W)", proposals.describe(p))
        with mock.patch("jarvis_mcp._home", return_value="Erledigt.") as switch:
            proposals.accept(p["id"])
            self.assertEqual([c.args[1]["service"] for c in switch.call_args_list], ["turn_off"] * 3)


class Review(unittest.TestCase):
    def test_facts(self):
        now = dt.datetime(2026, 11, 1, 18)
        hist = [{"at": "2026-10-31 09:00", "key": "nas", "state": "bad", "text": "Das NAS ist weg."},
                {"at": "2026-10-30 10:00", "key": "pi", "state": "bad", "text": "Der Pi war weg."},
                {"at": "2026-10-30 10:20", "key": "pi", "state": "ok", "text": "Der Pi ist wieder da."},
                {"at": "2026-10-29 10:00", "key": "nas", "state": "bad", "text": "Das NAS ist weg."},
                {"at": "2026-10-20 10:00", "key": "ha", "state": "bad", "text": "Zu alt."}]
        rep = {"score": 96, "counts": {"mittel": 1}, "history": [{"t": "2026-10-26T10:00:00+01:00", "score": 87}],
               "resolved": [{"at": "2026-10-28T13:23:12+01:00"}], "router": {"inet_fails": 5}}
        lines = homelab_review.facts(now, hist, rep, 16.5, 0.30, {"2026-10-29": 1.2, "2026-10-31": 2.4, "2026-10-01": 9})
        self.assertEqual(lines[0], "Ausfälle: 3 — Das NAS ist weg. (2-mal) Der Pi war weg.")
        self.assertEqual(lines[1], "Sicherheit: jetzt 96 von 100 Punkten (Wochenbeginn 87), offene Befunde: 1 mittel. "
                                   "Diese Woche erledigt: 1. Im Protokoll der FRITZ!Box stehen 5 Internet-Abbrüche.")
        self.assertEqual(lines[2], "Energie: Das Haus hat diese Woche 16,5 Kilowattstunden verbraucht, etwa 4,95 Euro, "
                                   "die Sonne hat 3,6 Kilowattstunden geliefert, am meisten am 31.10. (2,4).")

    def test_quiet_week_and_missing_sources(self):
        lines = homelab_review.facts(dt.datetime(2026, 11, 1, 18), [], None, None, 0.3, {})
        self.assertEqual(lines, ["Ausfälle: 0, die Woche lief ohne Störung."])


class Findings(unittest.TestCase):
    REP = {"findings": [{"id": "ssh-password:nas", "sev": "mittel", "title": "SSH mit Passwort – NAS",
                         "why": "Passwörter lassen sich ausprobieren.", "fix": ["SSH in DSM aus."], "effort": "5 min"}],
           "accepted": [{"title": "SOCKS – Echo", "accepted": {"until": "2027-10-27T18:45:26+01:00"}},
                        {"title": "Alt", "accepted": {"until": None}}]}

    def test_list(self):
        with mock.patch.object(situation, "security", return_value=self.REP):
            text = situation.open_findings()
        self.assertIn("[ssh-password:nas] mittel: SSH mit Passwort – NAS. Warum: Passwörter lassen sich ausprobieren. "
                      "Was tun: SSH in DSM aus. (5 min)", text)
        self.assertIn("Als Ausnahme akzeptiert: SOCKS – Echo (bis 27.10.2027); Alt (unbefristet)", text)

    def test_accept_checks_before_it_posts(self):
        with mock.patch.object(situation, "security", return_value=self.REP), \
                mock.patch.object(situation, "_post", return_value={"ok": True}) as post:
            self.assertIn("30, 90 oder 365", situation.accept("ssh-password:nas", "Nur im Heimnetz erreichbar.", 7))
            self.assertIn("Begründung", situation.accept("ssh-password:nas", "ok", 30))
            self.assertIn("gibt es nicht", situation.accept("gibts-nicht", "Nur im Heimnetz erreichbar.", 30))
            post.assert_not_called()
            self.assertIn("Ausnahme für 30 Tage", situation.accept("ssh-password:nas", "Nur im Heimnetz erreichbar.", 30))
        body = post.call_args_list[0].args[1]
        self.assertEqual(body, {"id": "ssh-password:nas", "days": 30, "reason": "Per Jarvis akzeptiert: Nur im Heimnetz erreichbar."})
        self.assertEqual(post.call_args_list[1].args[0], "/api/security/run/quick")

    def test_classes(self):
        self.assertEqual(policy.CLASSES["security_accept"], policy.CONFIRM)
        self.assertEqual(policy.CLASSES["security_findings"], policy.READ)
        self.assertIn("security_findings", policy.UNTRUSTED)
        self.assertEqual(policy.CLASSES["power_costs"], policy.READ)

    def test_route_scan(self):
        with mock.patch.object(situation, "run_scan", return_value="S"):
            self.assertEqual(router._situation(router._norm("Starte eine neue Sicherheitsprüfung")), "S")
            self.assertEqual(router._situation(router._norm("Sicherheitscheck jetzt laufen lassen")), "S")


if __name__ == "__main__":
    unittest.main()
