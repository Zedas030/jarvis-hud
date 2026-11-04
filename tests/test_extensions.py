"""
Electricity costs, the check when leaving home, the weekly homelab report and working
through security findings. No network, no house.

    .venv\\Scripts\\python.exe -m unittest tests.test_extensions -v
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
            f"sensor.{base}_current_consumption": {"entity_id": f"sensor.{base}_current_consumption", "state": str(watts)},
            f"sensor.{base}_this_month_s_consumption": {"entity_id": "x", "state": str(month)}}


class Costs(unittest.TestCase):
    def setUp(self):
        self.st = {**plug("pc", "PC", month=80), **plug("tv_bedroom", "TV bedroom", month=15),
                   **plug("kitchen_boiler", "Kitchen boiler", on=False, month=0.2),
                   "sensor.house_consumption_this_month": {"state": "171"}}
        self.now = dt.datetime(2026, 10, 28, 12)

    def test_month_and_projection(self):
        text = power_costs.say_month(self.st, self.now)
        self.assertIn("The house has used 171 kilowatt hours so far this month, 51.30 euros", text)
        self.assertIn("PC 80.0 kWh (24.00 euros), TV bedroom 15.0 kWh (4.50 euros)", text)
        self.assertIn("30 cents per kilowatt hour", text)

    def test_one_device(self):
        text = power_costs.say_month(self.st, self.now, "pc")
        self.assertTrue(text.startswith("PC: so far this month 80.0 kilowatt hours, that is 24.00 euros."))
        self.assertIn("I cannot find a power meter for “Toaster”", power_costs.say_month(self.st, self.now, "Toaster"))

    def test_standby_needs_days_and_skips_always_on(self):
        mem = {"devices": {"switch.tv_bedroom": {"name": "TV bedroom", "min_w": {"a": 18, "b": 20, "c": 19}},
                           "switch.fridge": {"name": "Fridge", "min_w": {"a": 1, "b": 60, "c": 50}},
                           "switch.pc": {"name": "PC", "min_w": {"a": 0.5, "b": 0.4, "c": 0.6}}}}
        text = power_costs.say_standby(mem, {"always_on": ["switch.fridge"]}, {})
        self.assertEqual(text, "Drawing power while idle: TV bedroom with about 19 watts (49.93 euros a year). "
                               "Switching the socket off saves exactly that.")
        young = {"devices": {"switch.tv": {"name": "TV", "min_w": {"a": 20}}}}
        self.assertIn("at least 3 days", power_costs.say_standby(young, {}, {}))

    def test_route(self):
        with mock.patch.object(power_costs, "report", side_effect=lambda device="", standby=False: f"{device}|{standby}"):
            r = lambda s: router._costs(router._norm(s))  # noqa: E731
            self.assertEqual(r("What does the PC cost me per month?"), "pc|False")
            self.assertEqual(r("Who is the power hog in standby here?"), "|True")
            self.assertEqual(r("How high are the electricity costs?"), "|False")
            self.assertIsNone(r("What does a kebab cost?"))


class Away(unittest.TestCase):
    CFG = {"switches": {"switch.tv": 30, "switch.kitchen_boiler": 20}}

    def states(self):
        s = {**plug("tv", "TV", watts=85), **plug("kitchen_boiler", "Kitchen boiler", on=True, watts=0.5),
             "light.lamp": {"entity_id": "light.lamp", "state": "on", "attributes": {"friendly_name": "Lamp"}},
             "light.hallway": {"entity_id": "light.hallway", "state": "off", "attributes": {}},
             "media_player.sonos": {"entity_id": "media_player.sonos", "state": "playing",
                                    "attributes": {"friendly_name": "Sonos"}}}
        return s

    def test_what_still_runs(self):
        found = away_check.still_on(self.states(), self.CFG)
        self.assertEqual([e for e, _ in found], ["light.lamp", "media_player.sonos", "switch.tv"])
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
        st = list(self.states().values()) + [{"entity_id": "person.user", "state": "not_home",
                                              "last_changed": "2026-10-28T11:50:00+00:00"}]
        prof = {"person": "person.user", "away_check": self.CFG}
        with mock.patch("jarvis_profile.ha", side_effect=lambda k, d=None: prof.get(k, d)), \
                mock.patch("infra.phone_notify") as push, mock.patch("jarvis_mcp._home") as switch:
            text = away_check.check(st, now=dt.datetime(2026, 10, 28, 12, tzinfo=UTC))
            self.assertIn("at home this is still running: Lamp, Sonos, TV (85 W)", text)
            push.assert_called_once()
            switch.assert_not_called()
            self.assertIsNone(away_check.check(st, now=dt.datetime(2026, 10, 28, 12, 5, tzinfo=UTC)))
        p = [x for x in proposals.pending() if x["kind"] == "switch_off"][-1]
        self.assertEqual(p["payload"]["entities"], ["light.lamp", "media_player.sonos", "switch.tv"])
        self.assertIn("• TV (85 W)", proposals.describe(p))
        with mock.patch("jarvis_mcp._home", return_value="Done.") as switch:
            proposals.accept(p["id"])
            self.assertEqual([c.args[1]["service"] for c in switch.call_args_list], ["turn_off"] * 3)


class Review(unittest.TestCase):
    def test_facts(self):
        now = dt.datetime(2026, 11, 1, 18)
        hist = [{"at": "2026-10-31 09:00", "key": "nas", "state": "bad", "text": "The NAS is gone."},
                {"at": "2026-10-30 10:00", "key": "pi", "state": "bad", "text": "The Pi was gone."},
                {"at": "2026-10-30 10:20", "key": "pi", "state": "ok", "text": "The Pi is back."},
                {"at": "2026-10-29 10:00", "key": "nas", "state": "bad", "text": "The NAS is gone."},
                {"at": "2026-10-20 10:00", "key": "ha", "state": "bad", "text": "Too old."}]
        rep = {"score": 96, "counts": {"medium": 1}, "history": [{"t": "2026-10-26T10:00:00+01:00", "score": 87}],
               "resolved": [{"at": "2026-10-28T13:23:12+01:00"}], "router": {"inet_fails": 5}}
        lines = homelab_review.facts(now, hist, rep, 16.5, 0.30, {"2026-10-29": 1.2, "2026-10-31": 2.4, "2026-10-01": 9})
        self.assertEqual(lines[0], "Outages: 3 — The NAS is gone. (2 times) The Pi was gone.")
        self.assertEqual(lines[1], "Security: now 96 of 100 points (start of the week 87), open findings: 1 medium. "
                                   "Resolved this week: 1. The FRITZ!Box log shows 5 internet drops.")
        self.assertEqual(lines[2], "Energy: The house used 16.5 kilowatt hours this week, about 4.95 euros, "
                                   "the sun delivered 3.6 kilowatt hours, the most on 2026-10-31 (2.4).")

    def test_quiet_week_and_missing_sources(self):
        lines = homelab_review.facts(dt.datetime(2026, 11, 1, 18), [], None, None, 0.3, {})
        self.assertEqual(lines, ["Outages: 0, the week ran without a disturbance."])


class Findings(unittest.TestCase):
    REP = {"findings": [{"id": "ssh-password:nas", "sev": "medium", "title": "SSH with password – NAS",
                         "why": "Passwords can be guessed.", "fix": ["Turn SSH off in DSM."], "effort": "5 min"}],
           "accepted": [{"title": "SOCKS – Echo", "accepted": {"until": "2027-10-27T18:45:26+01:00"}},
                        {"title": "Old", "accepted": {"until": None}}]}

    def test_list(self):
        with mock.patch.object(situation, "security", return_value=self.REP):
            text = situation.open_findings()
        self.assertIn("[ssh-password:nas] medium: SSH with password – NAS. Why: Passwords can be guessed. "
                      "What to do: Turn SSH off in DSM. (5 min)", text)
        self.assertIn("Accepted as exception: SOCKS – Echo (until 2027-10-27); Old (no end date)", text)

    def test_accept_checks_before_it_posts(self):
        with mock.patch.object(situation, "security", return_value=self.REP), \
                mock.patch.object(situation, "_post", return_value={"ok": True}) as post:
            self.assertIn("30, 90 or 365", situation.accept("ssh-password:nas", "Only reachable in the home network.", 7))
            self.assertIn("reason", situation.accept("ssh-password:nas", "ok", 30))
            self.assertIn("There is no open finding", situation.accept("does-not-exist", "Only reachable in the home network.", 30))
            post.assert_not_called()
            self.assertIn("exception for 30 days", situation.accept("ssh-password:nas", "Only reachable in the home network.", 30))
        body = post.call_args_list[0].args[1]
        self.assertEqual(body, {"id": "ssh-password:nas", "days": 30, "reason": "Accepted via Jarvis: Only reachable in the home network."})
        self.assertEqual(post.call_args_list[1].args[0], "/api/security/run/quick")

    def test_classes(self):
        self.assertEqual(policy.CLASSES["security_accept"], policy.CONFIRM)
        self.assertEqual(policy.CLASSES["security_findings"], policy.READ)
        self.assertIn("security_findings", policy.UNTRUSTED)
        self.assertEqual(policy.CLASSES["power_costs"], policy.READ)

    def test_route_scan(self):
        with mock.patch.object(situation, "run_scan", return_value="S"):
            self.assertEqual(router._situation(router._norm("Start a new security check")), "S")
            self.assertEqual(router._situation(router._norm("Run the security check now")), "S")


if __name__ == "__main__":
    unittest.main()
