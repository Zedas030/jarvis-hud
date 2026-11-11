"""
The seven ideas of 10.11.2026 evening: why something switched, where things are, birthdays, what Jarvis did
today, the night's ghosts, airing and print ideas. HA, weather and browser faked.

    .venv\\Scripts\\python.exe -m unittest tests.test_ideas_1110 -v
"""
import isolate  # noqa: F401  (must come first)
import datetime as dt
import shutil
import unittest
from unittest import mock

import activity
import airing
import birthdays
import ha_why
import memory
import night_report
import places
import print_ideas
import router

UTC = dt.timezone.utc


def ask(text):
    return router._local(text, router.LOCAL_HANDLERS)[0]


class Vault(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(memory.VAULT, ignore_errors=True)
        memory.MEMORY_DIR.mkdir(parents=True, exist_ok=True)


class Places(Vault):
    def test_put_and_find_with_original_spelling(self):
        self.assertIn("IKEA box in the hallway", ask("I put the spare key in the IKEA box in the hallway"))
        self.assertIn("in the IKEA box in the hallway", ask("Where is the spare key?"))
        self.assertIn("IKEA box", ask("Where did I put the spare key?"))

    def test_new_place_replaces_old(self):
        ask("Remember, the passport is in the desk")
        ask("The passport is now in the safe")
        self.assertEqual(len(places.find("passport")), 1)
        self.assertIn("safe", ask("Where is the passport?"))

    def test_plain_statements_and_other_questions_stay_out(self):
        self.assertIsNone(places.handle(" the light is on in the living room "))
        self.assertIsNone(places.handle(" the key is in the hallway "))          # no "remember" / "now"
        self.assertIsNone(places.handle(" i put the milk on the shopping list "))
        with mock.patch("jarvis_profile.USER_NAME", "Alex Example"):
            self.assertIsNone(places.handle(" where is alex "))
        self.assertFalse(places._bullets())


class Birthdays(Vault):
    def setUp(self):
        super().setUp()
        p = memory.MEMORY_DIR / "People"
        p.mkdir()
        (p / "Anna.md").write_text("# Anna\n\n- [[Anna]]'s birthday is on May 3 _(inferred, 2026-11-10)_\n"
                                   "- Likes tulips\n- Enjoys crime novels\n- Lives in Springfield\n", encoding="utf-8")
        (memory.MEMORY_DIR / "Knowledge.md").write_text("# Knowledge\n\n- Ben's birthday is on 1990-05-04\n",
                                                        encoding="utf-8")

    def test_people_and_upcoming(self):
        ppl = birthdays.people()
        self.assertEqual((ppl["Anna"]["day"], ppl["Anna"]["month"]), (3, 5))
        self.assertEqual(ppl["Ben"]["year"], 1990)
        soon = birthdays.upcoming(5, dt.date(2027, 5, 1))
        self.assertEqual([(n, a) for _, n, a in soon], [("Anna", None), ("Ben", 37)])

    def test_date_forms(self):
        self.assertEqual(birthdays._date_of("born on 3rd of May 1992"), (3, 5, 1992))
        self.assertEqual(birthdays._date_of("birthday: December 24, 1985"), (24, 12, 1985))

    def test_day_before_once_with_likes(self):
        said = []
        with mock.patch("events.publish", lambda t, text, **kw: said.append(text)):
            out = birthdays.step(dt.datetime(2027, 5, 2, 18, 30))
            birthdays.step(dt.datetime(2027, 5, 2, 19, 30))
        self.assertEqual(len(said), 1)
        self.assertIn("Tomorrow is Anna's birthday", out[0])
        self.assertIn("likes tulips, enjoys crime novels", out[0])
        self.assertNotIn("Springfield", out[0])

    def test_router(self):
        self.assertIn("May 3", birthdays.handle(" when is annas birthday "))
        with mock.patch.object(birthdays, "upcoming", return_value=[(dt.date(2027, 5, 4), "Ben", 37)]):
            self.assertIn("Ben on May 4 (37)", birthdays.handle(" who has a birthday soon "))


class PrintIdeas(Vault):
    def test_add_read_done(self):
        self.assertIn("Cable holder for the desk", ask("Remember as a print idea: cable holder for the desk"))
        self.assertIn("Cable holder", ask("What did I want to print?"))
        self.assertIn("crossed off", ask("The cable holder for the desk is printed"))
        self.assertIn("No print ideas", ask("Which print ideas do I have?"))

    def test_makerworld_opens_search(self):
        with mock.patch("jarvis_mcp._open") as op:
            self.assertIn("MakerWorld", ask("Search for a cable holder on MakerWorld"))
        self.assertIn("makerworld.com/en/search/models?keyword=cable%20holder", op.call_args[0][0])


def entry(when, state=None, **ctx):
    e = {"when": when.isoformat()}
    if state:
        e["state"] = state
    e.update(ctx)
    return e


class Why(unittest.TestCase):
    def test_chain_back_to_the_sensor(self):
        t = dt.datetime(2026, 11, 10, 22, 12, tzinfo=UTC)
        books = {
            "light.led": [entry(t, "on", context_event_type="automation_triggered", context_domain="automation",
                                context_name="Jarvis: living room light follows me",
                                context_source="state of input_select.room")],
            "input_select.room": [entry(t - dt.timedelta(seconds=3), "Living room",
                                        context_event_type="automation_triggered", context_domain="automation",
                                        context_name="Where am I",
                                        context_source="state of binary_sensor.presence_lr")],
            "binary_sensor.presence_lr": [entry(t - dt.timedelta(seconds=5), "on")]}
        states = {"light.led": {"attributes": {"friendly_name": "LED"}},
                  "input_select.room": {"attributes": {"friendly_name": "Room"}},
                  "binary_sensor.presence_lr": {"attributes": {"friendly_name": "Presence living room"}}}
        with mock.patch.object(ha_why, "logbook", lambda eid, start, end=None: books.get(eid, [])):
            text = ha_why.explain("light.led", now=t + dt.timedelta(minutes=5), states=states)
        self.assertIn("LED went on", text)
        self.assertIn("“Jarvis: living room light follows me”, triggered by “Room” (to Living room)", text)
        self.assertIn("“Where am I”, triggered by “Presence living room” (on)", text)

    def test_no_context_means_device(self):
        self.assertIn("directly on the device", ha_why.cause({"state": "on"}, {}))


class Night(unittest.TestCase):
    def test_counts_pulses_in_other_rooms_only(self):
        day = dt.date(2026, 11, 7)
        start = dt.datetime.combine(day, dt.time(0)).astimezone()
        at = lambda m: start + dt.timedelta(minutes=m)     # noqa: E731
        books = {
            night_report.ROOM_ENTITY: [entry(start - dt.timedelta(hours=1), "Bedroom")],
            "binary_sensor.lr": [entry(at(i * 5), "on") for i in range(7)],
            "binary_sensor.br": [entry(at(30), "on")],                      # that is the user, they are in there
            "light.led": [entry(at(40), "on"), entry(at(40), context_name="Jarvis: living room light follows me")]}
        states = {"binary_sensor.lr": {"attributes": {"device_class": "occupancy", "friendly_name": "Presence living room"}},
                  "binary_sensor.br": {"attributes": {"device_class": "occupancy", "friendly_name": "Presence bedroom"}},
                  "light.led": {"attributes": {"friendly_name": "LED"}},
                  night_report.ROOM_ENTITY: {"attributes": {}}}
        with mock.patch.object(ha_why, "logbook", lambda eid, s, e=None: books.get(eid, [])):
            r = night_report.analyse(day, states)
        self.assertEqual(r["ghosts"], [("Presence living room", 7, "living room")])
        text = night_report.spoken(r)
        self.assertIn("7 times", text)
        self.assertIn("LED went on once (Jarvis: living room light follows me)", text)
        self.assertEqual(night_report.spoken({"ghosts": [("X", 2, "bathroom")], "lights": []}), "")   # too little to say


class Airing(unittest.TestCase):
    STATES = {"sensor.h_br": {"state": "66", "attributes": {"device_class": "humidity", "friendly_name": "Bedroom humidity"}},
              "sensor.t_br": {"state": "19", "attributes": {"device_class": "temperature", "friendly_name": "Bedroom temperature"}},
              "sensor.h_tent": {"state": "80", "attributes": {"device_class": "humidity", "friendly_name": "Tent humidity"}},
              "sensor.t_tent": {"state": "25", "attributes": {"device_class": "temperature", "friendly_name": "Tent temperature"}}}

    def test_absolute_humidity(self):
        self.assertAlmostEqual(airing.absolute(20, 50), 8.6, places=1)

    def test_rooms_and_advice(self):
        rooms = airing.rooms(self.STATES)
        self.assertEqual(list(rooms), ["bedroom"])                            # the tent has no room word
        cold = airing.advice(rooms, {"temp": 4, "rh": 90})[0]
        self.assertTrue(cold[4])                                              # 90 % at 4 °C is drier than 66 % at 19 °C
        muggy = airing.advice(rooms, {"temp": 24, "rh": 70})[0]
        self.assertFalse(muggy[4])

    def test_wet_days(self):
        now = dt.datetime(2026, 11, 10, 12, 0).astimezone()
        series = [(now - dt.timedelta(days=4), 70.0)]
        self.assertEqual(airing.wet_days(series, now), 3)
        series.append((now - dt.timedelta(days=2) + dt.timedelta(hours=-6), 55.0))
        self.assertLess(airing.wet_days(series, now), 3)


class Activity(unittest.TestCase):
    def test_quiet_day(self):
        with mock.patch.object(activity, "requests", return_value=(0, 0)), \
                mock.patch.object(activity, "notices", return_value=[]), \
                mock.patch.object(activity, "saved", return_value=[]), \
                mock.patch.object(activity, "proposed", return_value=[]), \
                mock.patch.object(activity, "actions", return_value=[]):
            self.assertIn("quiet", activity.report())

    def test_busy_day(self):
        at = dt.datetime.now().replace(hour=16, minute=40).timestamp()
        with mock.patch.object(activity, "requests", return_value=(14, 9)), \
                mock.patch.object(activity, "notices", return_value=[(at, "appliance", "Sir, the washing machine is done.")]), \
                mock.patch.object(activity, "saved", return_value=["Anna likes tulips"]), \
                mock.patch.object(activity, "proposed", return_value=[{"status": "open", "title": "Restart container x on the Pi"}]), \
                mock.patch.object(activity, "actions", return_value=[{"tool": "home_control"}]):
            text = activity.handle(" what have you done today ")
        self.assertIn("14 requests, 9 of them handled locally", text)
        self.assertIn("16:40 “the washing machine is done.”", text)
        self.assertIn("Remembered: Anna likes tulips", text)
        self.assertIn("open: Restart container x on the Pi", text)
        self.assertIn("home_control", text)


if __name__ == "__main__":
    unittest.main()
