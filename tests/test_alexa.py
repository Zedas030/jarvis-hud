"""
Welcome back, weather warnings, house habits, announcements and the TV. No network, no house.

    .venv\\Scripts\\python.exe -m unittest tests.test_alexa -v
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
import events            # noqa: E402
import house_habits as hh  # noqa: E402
import policy            # noqa: E402
import proposals         # noqa: E402
import router            # noqa: E402
import speakers          # noqa: E402
import weather_alerts as wa  # noqa: E402

UTC = dt.timezone.utc
PROF = {"person": "person.user", "away_check": {"switches": {}},
        "speakers": {"Living room": {"notify": "notify.alexa_media_user_echo_dot"},
                     "Bedroom": {"tts": "tts.google", "player": "media_player.sonos"}},
        "tv": "media_player.user_fire_tv"}


def prof():
    return mock.patch("jarvis_profile.ha", side_effect=lambda k, d=None: PROF.get(k, d))


class Welcome(unittest.TestCase):
    def setUp(self):
        commands.update_state(lambda d: [d.pop(k, None) for k in ("away_checked", "away_since", "welcomed")])
        if events.NOTICES.exists():
            events.NOTICES.unlink()

    def person(self, state, changed):
        return [{"entity_id": "person.user", "state": state, "last_changed": changed}]

    def test_round_trip(self):
        with prof(), mock.patch("infra.phone_notify"):
            away_check.check(self.person("not_home", "2026-10-28T10:00:00+00:00"),
                             now=dt.datetime(2026, 10, 28, 10, 5, tzinfo=UTC))
        since = commands.load()["away_since"]
        events._note({"at": since + 600, "kind": "appliance", "t": "say", "text": "Sir, the dishwasher is done."})
        events._note({"at": since + 900, "kind": "parcel", "t": "say", "text": "Sir, a parcel was delivered."})
        events._note({"at": since + 950, "kind": "parcel", "t": "say", "text": "Sir, a parcel was delivered."})
        events._note({"at": since + 990, "kind": "alarm", "t": "alarm", "text": "Timer finished."})
        events._note({"at": since - 60, "kind": "habit", "t": "say", "text": "Said before."})
        with prof(), mock.patch.object(events, "publish") as pub:
            text = away_check.check(self.person("home", "2026-10-28T11:30:00+00:00"),
                                    now=dt.datetime(2026, 10, 28, 11, 31, tzinfo=UTC))
        self.assertEqual(text, "Welcome back, Sir. While you were out: The dishwasher is done. "
                               "A parcel was delivered.")
        pub.assert_called_once()
        with prof():                                   # once per coming back
            self.assertIsNone(away_check.check(self.person("home", "2026-10-28T11:30:00+00:00"),
                                               now=dt.datetime(2026, 10, 28, 11, 35, tzinfo=UTC)))

    def test_short_trip_or_late_jarvis_stays_silent(self):
        now = dt.datetime(2026, 10, 28, 12, tzinfo=UTC)
        back = {"state": "home", "last_changed": "2026-10-28T11:59:00+00:00"}
        self.assertIsNone(away_check.came_back(back, now.timestamp() - 600, "", now))       # 9 minutes out
        self.assertTrue(away_check.came_back(back, now.timestamp() - 3600, "", now))
        old = {"state": "home", "last_changed": "2026-10-28T10:00:00+00:00"}
        self.assertIsNone(away_check.came_back(old, now.timestamp() - 9000, "", now))      # Jarvis was off

    def test_quiet_absence(self):
        self.assertEqual(away_check.welcome([]), "Welcome back, Sir. While you were out, everything was quiet.")

    def test_open_switch_off_is_dropped(self):
        pid = proposals.add("switch_off", "Switch off: TV", "away", {"entities": ["switch.tv"]}, source="away_check")
        commands.update_state(lambda d: d.update(away_since=1.0))
        with mock.patch.object(events, "publish"):
            away_check._welcome("2026-10-28T11:30:00+00:00", 1.0)
        self.assertNotIn(pid, [p["id"] for p in proposals.pending()])


class Alerts(unittest.TestCase):
    A = {"event_code": 51, "severity": "moderate", "headline_de": "Amtliche WARNUNG vor STURMBÖEN",
         "headline_en": "Official WARNING of GALE-FORCE GUSTS",
         "onset": "2026-10-28T15:00:00+01:00", "expires": "2026-10-28T22:00:00+01:00",
         "description_de": "Es treten Sturmböen mit Geschwindigkeiten um 70 km/h auf. Achten Sie auf herabstürzende Äste.",
         "description_en": "There is a risk of gale-force gusts of about 70 km/h. Watch out for falling branches."}

    def test_each_once_and_again_only_when_worse(self):
        seen, now = {}, dt.datetime(2026, 10, 28, 13, tzinfo=UTC)
        self.assertEqual(len(wa.new_ones([self.A], seen, now)), 1)
        self.assertEqual(wa.new_ones([self.A], seen, now), [])
        worse = dict(self.A, severity="severe")
        hit = wa.new_ones([worse], seen, now)
        self.assertEqual(len(hit), 1)
        self.assertTrue(hit[0][1])                                   # severe: also at night, also the phone
        self.assertEqual(wa.new_ones([dict(self.A, severity="minor", event_code=22)], seen, now), [])
        wa.new_ones([], seen, dt.datetime(2026, 10, 29, tzinfo=UTC))
        self.assertEqual(seen, {})                                   # expired ones are dropped

    def test_spoken(self):
        text = wa.say(self.A)
        self.assertTrue(text.startswith("Official WARNING of GALE-FORCE GUSTS from "), text)
        self.assertTrue(text.endswith("There is a risk of gale-force gusts of about 70 km/h."), text)

    def test_german_only_warning_is_still_read(self):
        de = {k: v for k, v in self.A.items() if not k.endswith("_en")}
        self.assertTrue(wa.say(de).startswith("Amtliche WARNUNG vor STURMBÖEN from "))   # no English text from the DWD

    def test_route(self):
        with mock.patch.object(wa, "current", return_value="W"):
            self.assertEqual(router._alerts(router._norm("Is there a severe weather warning?")), "W")
            self.assertIsNone(router._alerts(router._norm("What will the weather be like?")))


def day(d, h, m):
    return dt.datetime(2026, 10, d, h, m)


class Habits(unittest.TestCase):
    HIST = {"light.lamp": [(day(18, 17, 0), "on"), (day(18, 22, 40), "off"), (day(19, 17, 30), "on"),
                            (day(19, 23, 5), "off"), (day(20, 18, 0), "on"), (day(20, 22, 50), "off"),
                            (day(21, 16, 0), "on"), (day(21, 22, 30), "off"), (day(22, 12, 0), "on"), (day(22, 13, 0), "off")],
            "switch.tv_bedroom": [(day(18, 6, 0), "unavailable"), (day(18, 6, 1), "on"), (day(19, 6, 0), "unavailable"),
                                       (day(19, 6, 1), "on")]}

    def test_habits_skip_unavailable_flapping(self):
        found = {(h["entity"], h["state"]): h for h in hh.habits(self.HIST)}
        self.assertEqual(set(found), {("light.lamp", "off")})        # on-times spread too wide, plug never changed
        off = found[("light.lamp", "off")]
        self.assertEqual(off["days"], 4)
        self.assertEqual(off["at"], "22:45")

    def test_routine_proposal_only_once_per_name(self):
        found = hh.habits(self.HIST)
        payload, why = hh.routine_proposal(found, {"light.lamp": "Lamp"}, [])
        self.assertEqual(payload, {"name": "Good night", "triggers": ["good night", "i am going to bed"],
                                   "steps": [{"ha": "light.turn_off", "entity_id": "light.lamp"}]})
        self.assertEqual(why, "Lamp off around 22:45 (on 4 of 10 days)")
        self.assertIsNone(hh.routine_proposal(found, {}, [{"name": "Bedtime", "triggers": ["good night"]}]))

    def test_long_on(self):
        usual = hh.usual_hours(self.HIST["light.lamp"])
        self.assertEqual(usual, 6.5)                                   # the usual longest evening
        st = {"state": "on", "last_changed": "2026-10-28T08:00:00"}
        self.assertIsNone(hh.long_on(st, usual, dt.datetime(2026, 10, 28, 20)))   # 12 h: under twice the usual
        self.assertEqual(round(hh.long_on(st, usual, dt.datetime(2026, 10, 28, 22))), 14)
        self.assertIsNone(hh.long_on(st, None, dt.datetime(2026, 10, 28, 20)))


class Speakers(unittest.TestCase):
    def test_announce(self):
        with prof(), mock.patch("jarvis_mcp._ha") as ha:
            self.assertEqual(speakers.announce("living room", "Dinner is ready"), "Announced in the Living room.")
            ha.assert_called_with("/api/services/notify/alexa_media_user_echo_dot",
                                  {"message": "Dinner is ready", "data": {"type": "announce"}})
            speakers.announce("Bedroom", "Time to get up")
            self.assertEqual(ha.call_args.args[0], "/api/services/tts/speak")
            self.assertEqual(ha.call_args.args[1]["language"], "en")
            self.assertIn("I do not know", speakers.announce("Cellar", "Hello"))

    def test_tv_fixed_commands_only(self):
        with prof(), mock.patch("jarvis_mcp._home", return_value="Done: media_player.play_media") as home:
            self.assertEqual(speakers.tv("pause"), "Done.")
            self.assertEqual(home.call_args.args[1]["data"], {"media_content_type": "custom", "media_content_id": "pause"})
            self.assertEqual(speakers.tv("app", "Netflix"), "Opening Netflix.")
            self.assertEqual(home.call_args.args[1]["data"]["media_content_id"], "open Netflix")
            for bad in ("Buy toilet paper", "call mum", "x" * 40, "Netflix; order"):
                self.assertIn("I do not open", speakers.tv("app", bad))
            self.assertIn("I cannot do that on the TV", speakers.tv("buy"))
            self.assertEqual(home.call_count, 2)

    def test_routes(self):
        with prof(), mock.patch.object(speakers, "announce", side_effect=lambda r, t: f"{r}|{t}"), \
                mock.patch.object(speakers, "tv", side_effect=lambda c, a="": f"{c}|{a}"):
            def run(text, fn):
                with mock.patch.object(router, "_raw", return_value=text):
                    return fn(router._norm(text))
            self.assertEqual(run("Say in the living room: Dinner is ready", router._announce), "Living room|Dinner is ready")
            self.assertEqual(run("Announce to the bedroom that I am coming in a minute", router._announce),
                             "Bedroom|I am coming in a minute")
            self.assertIsNone(run("Say in the cellar: Hello", router._announce))
            self.assertEqual(run("Open Netflix on the TV", router._tv), "app|Netflix")
            self.assertEqual(run("YouTube on the TV", router._tv), "app|YouTube")
            self.assertEqual(run("TV pause", router._tv), "pause|")
            self.assertEqual(run("Make the TV quieter", router._tv), "quieter|")
            self.assertIsNone(run("Turn the TV off", router._tv))     # the plug, as before
            self.assertIsNone(run("Open Spotify", router._tv))

    def test_classes(self):
        self.assertEqual(policy.CLASSES["announce"], policy.LIMITED_WRITE)
        self.assertEqual(policy.CLASSES["tv_control"], policy.LIMITED_WRITE)
        self.assertEqual(policy.CLASSES["weather_alerts"], policy.READ)


if __name__ == "__main__":
    unittest.main()
