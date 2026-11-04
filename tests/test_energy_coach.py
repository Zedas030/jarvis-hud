"""
Energy coach: learning the plugs (off at first sight = rarely used), the surplus window,
what is said and when he speaks up. No network, no house.

    .venv\\Scripts\\python.exe -m unittest tests.test_energy_coach -v
"""
import datetime as dt
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import energy_coach as ec    # noqa: E402
import router                # noqa: E402

NOW = dt.datetime(2026, 6, 10, 9, 0)
CFG = dict(ec.DEFAULTS, shiftable={"switch.spulmaschine": "die Spülmaschine", "switch.kuche_boiler": "den Boiler"},
           battery_kwh=4.0)


def plug(name, on, watts=0.0, kwh=0.0):
    base = name.lower().replace(" ", "_")
    return {f"switch.{base}": {"state": "on" if on else "off", "attributes": {"friendly_name": name}},
            f"sensor.{base}{ec.POWER_SUFFIX}": {"state": str(watts)},
            f"sensor.{base}{ec.DAY_SUFFIX}": {"state": str(kwh)}}


def house(soc=50, solar=0, load=300, **plugs_):
    s = {"sensor.soc": {"state": str(soc)}, "sensor.solar": {"state": str(solar)}, ec.HOUSE: {"state": str(load)}}
    s.update(plug("Spulmaschine", True, **plugs_.get("spul", {})))
    s.update(plug("Kuche Boiler", False))
    return s


def sunny(day, peak=800):
    """A clear day: radiation rises to `peak` W/m² at 13 h."""
    return [(dt.datetime.combine(day, dt.time(h)), max(0, peak - abs(13 - h) * 140)) for h in range(24)]


PROFILE = {"storage_soc": "sensor.soc", "solar_power": ["sensor.solar"], "generation_today": ["sensor.gen"]}


class Learn(unittest.TestCase):
    def test_off_at_first_sight_is_rare_and_reuse_brings_it_back(self):
        mem = ec.load()
        mem["devices"].clear()
        ec.learn(mem, ec.plugs(house()), NOW)
        self.assertTrue(mem["devices"]["switch.kuche_boiler"]["rare"])
        self.assertFalse(mem["devices"]["switch.spulmaschine"]["rare"])
        boiler_on = dict(house(), **plug("Kuche Boiler", True, watts=2000, kwh=0.4))
        self.assertEqual(ec.learn(mem, ec.plugs(boiler_on), NOW), [])               # one day is not enough
        changed = ec.learn(mem, ec.plugs(boiler_on), NOW + dt.timedelta(days=3))
        self.assertEqual(changed, [("Kuche Boiler", "genutzt")])
        self.assertFalse(mem["devices"]["switch.kuche_boiler"]["rare"])

    def test_idle_for_two_weeks_becomes_rare(self):
        mem = ec.load()
        mem["devices"].clear()
        ec.learn(mem, ec.plugs(house()), NOW)
        off = dict(house(), **plug("Spulmaschine", False))
        self.assertEqual(ec.learn(mem, ec.plugs(off), NOW + dt.timedelta(days=10)), [])
        self.assertEqual(ec.learn(mem, ec.plugs(off), NOW + dt.timedelta(days=15)), [("Spulmaschine", "selten")])

    def test_what_the_user_says_stays(self):
        mem = ec.load()
        mem["devices"].clear()
        ec.learn(mem, ec.plugs(house()), NOW)
        ec.save(mem)
        self.assertIn("nutzt du oft", ec.set_usage("Boiler", True))
        mem = ec.load()
        self.assertFalse(mem["devices"]["switch.kuche_boiler"]["rare"])
        ec.learn(mem, ec.plugs(house()), NOW + dt.timedelta(days=30))              # off for a month: still not rare
        self.assertFalse(mem["devices"]["switch.kuche_boiler"]["rare"])
        self.assertIn("Welche Steckdose", ec.set_usage("Toaster", False))

    def test_typical_values(self):
        d = {"watts": [1800, 2000, 2100], "day_kwh": {"a": 0.01, "b": 0.9, "c": 1.1}}
        self.assertEqual(ec.typical(d), (2000, 1.0))


class Advice(unittest.TestCase):
    def setUp(self):
        self.mem = ec.load()
        self.mem["devices"].clear()
        ec.learn(self.mem, ec.plugs(house()), NOW)
        self.mem["devices"]["switch.spulmaschine"]["day_kwh"] = {"2026-06-01": 0.9}
        self.p = mock.patch("jarvis_profile.ha", side_effect=lambda k, d=None: PROFILE.get(k, d))
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_window_only_after_the_battery_is_full(self):
        fc = sunny(NOW.date())
        kwh, h1, h2 = ec.window(fc, NOW, NOW + dt.timedelta(hours=15), 1.0, 300, room_kwh=100)
        self.assertEqual(kwh, 0)                                     # a huge battery swallows everything
        kwh, h1, h2 = ec.window(fc, NOW, NOW + dt.timedelta(hours=15), 1.0, 300, room_kwh=0.5)
        self.assertGreater(kwh, 1)
        self.assertGreaterEqual(h1, 11)

    def test_sunny_day_suggests_dishwasher_never_boiler(self):
        fc = sunny(NOW.date(), peak=1000) + sunny(NOW.date() + dt.timedelta(days=1), peak=1000)
        a = ec.advise(house(soc=90, solar=300), self.mem, CFG, fc, NOW)
        text = ec.say(a)
        self.assertIn("Heute zwischen", text)
        self.assertIn("die Spülmaschine", text)
        self.assertNotIn("den Boiler", text)
        self.assertIn("selten genutzt: Kuche Boiler", text)

    def test_grey_day(self):
        fc = sunny(NOW.date(), peak=300) + sunny(NOW.date() + dt.timedelta(days=1), peak=300)
        text = ec.say(ec.advise(house(soc=20, solar=40, load=400), self.mem, CFG, fc, NOW))
        self.assertIn("Akku 20 Prozent, Sonne gerade 40 Watt, das Haus braucht 400.", text)
        self.assertIn("das geht in den Akku, Überschuss gibt es nicht", text)
        self.assertIn("Morgen gibt es voraussichtlich auch keinen Überschuss.", text)

    def test_announce_twice_over_then_once_a_day(self):
        a = ec.advise(house(soc=100, solar=900, load=300), self.mem, CFG, [], NOW.replace(hour=12))
        self.assertEqual(a["spill_now"], 600)
        noon = NOW.replace(hour=12)
        self.assertIsNone(ec.surplus_notice(a, self.mem, noon, False, CFG))
        text = ec.surplus_notice(a, self.mem, noon, False, CFG)
        self.assertIn("600 Watt mehr", text)
        self.assertIn("die Spülmaschine", text)
        self.assertIsNone(ec.surplus_notice(a, self.mem, noon, False, CFG))          # once a day

    def test_quiet_running_or_evening_stays_silent(self):
        noon = NOW.replace(hour=12)
        a = ec.advise(house(soc=100, solar=900, load=300), self.mem, CFG, [], noon)
        self.mem["streak"] = 5
        self.assertIsNone(ec.surplus_notice(a, self.mem, noon, True, CFG))
        self.assertIsNone(ec.surplus_notice(a, self.mem, noon.replace(hour=19), False, CFG))
        busy = ec.advise(house(soc=100, solar=900, load=300, spul=dict(watts=1900)), self.mem, CFG, [], noon)
        self.assertIsNone(ec.surplus_notice(busy, self.mem, noon, False, CFG))      # already running

    def test_calibration(self):
        fc = sunny(NOW.date())
        self.assertFalse(ec.calibrate(self.mem, 3.0, fc, NOW))                    # only in the evening
        evening = NOW.replace(hour=21)
        self.assertTrue(ec.calibrate(self.mem, 3.0, fc, evening))
        self.assertFalse(ec.calibrate(self.mem, 3.0, fc, evening))                # once a day
        self.assertAlmostEqual(ec.factor(self.mem, CFG), 0.64)                          # fewer than 3 days: default


class Route(unittest.TestCase):
    def test_routes(self):
        with mock.patch.object(ec, "report", return_value="R"), \
                mock.patch.object(ec, "set_usage", side_effect=lambda w, o: f"{w}:{o}"):
            r = lambda s: router._coach(router._norm(s))  # noqa: E731
            self.assertEqual(r("Energie-Tipp bitte"), "R")
            self.assertEqual(r("Wann soll ich die Spülmaschine anmachen?"), "R")
            self.assertEqual(r("Lohnt sich jetzt die Waschmaschine?"), "R")
            self.assertEqual(r("Den Boiler nutze ich sehr selten"), "boiler:False")
            self.assertEqual(r("Ich nutze den Drucker doch oft"), "drucker:True")
            self.assertIsNone(r("Mach die Spülmaschine an"))
            self.assertIsNone(r("Wie viel liefert die Solaranlage?"))


if __name__ == "__main__":
    unittest.main()
