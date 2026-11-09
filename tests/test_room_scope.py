"""
Room-aware commands (room_scope.py): "light on", "warmer", "TV on" without a room mean the room the presence
sensors see the user in; unsure -> "Living room or Bedroom?"; Home Assistant down -> as before, and the
device that answers instead of the room's speaker says why, once. No Home Assistant, no Sonos, no Claude.

    .venv\\Scripts\\python.exe -m unittest tests.test_room_scope -v
"""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import briefing       # noqa: E402
import jarvis_mcp     # noqa: E402
import jarvis_profile  # noqa: E402
import learning       # noqa: E402
import room_scope     # noqa: E402
import router         # noqa: E402
import sonos          # noqa: E402
import whereabouts    # noqa: E402

DEVICES = [("light.light", "Light"), ("switch.tv", "TV"), ("switch.tv_bedroom", "TV Bedroom"),
           ("switch.air_purifier", "Air purifier"), ("switch.tv_led", "TV LED"), ("switch.pc", "PC"),
           ("switch.bedroom_bedroom_tv_autoplay", "Bedroom TV Autoplay")]     # a Sonos setting
AREAS = {"light.light": "Living room", "switch.tv": "Living room", "switch.pc": "Living room",
         "climate.living_room_left": "Living room", "switch.tv_bedroom": "Bedroom",
         "switch.air_purifier": "Bedroom", "switch.bedroom_bedroom_tv_autoplay": "Bedroom", "climate.bedroom_left": "Bedroom",
         "binary_sensor.bathroom_presence_bathroom": "Bathroom"}
HOME = {"device_aliases": {"light.light": ["Light", "Lamp"]},
        "climate_groups": {"Living room": ["climate.living_room_left"], "Bedroom": ["climate.bedroom_left"]},
        "room_scope": {"areas": {"Bathroom": "Bedroom"},
                       "sensors": {"Living room": ["binary_sensor.lr"], "Bedroom": ["binary_sensor.br",
                                                                                     "binary_sensor.bath"]}}}
CALLS = []
CLIMATE = {}             # entity -> {"state": ..., "attributes": {...}}


def fake_home(name, args):
    CALLS.append((args["entity_id"], args["service"], args.get("data") or {}))
    return f"Done: {args['service']} for {args['entity_id']}."


class Base(unittest.TestCase):
    room, busy, offline = "Living room", {"Living room"}, False

    def setUp(self):
        CALLS.clear()
        CLIMATE.clear()
        CLIMATE.update({"climate.living_room_left": {"state": "heat", "attributes": {"temperature": 20}},
                        "climate.bedroom_left": {"state": "heat", "attributes": {"temperature": 20}}})
        self.saved_dev = dict(router._DEV)
        states = [{"entity_id": e, "state": "off", "attributes": {"friendly_name": n}} for e, n in DEVICES]
        self.patches = [
            mock.patch.object(briefing, "_ha_states", return_value=states),
            mock.patch.object(jarvis_mcp, "_home", side_effect=fake_home),
            mock.patch.object(jarvis_mcp, "_ha", side_effect=lambda path, body=None: CLIMATE.get(path.rsplit("/", 1)[-1], {})),
            mock.patch.object(jarvis_profile, "_load_home", return_value=HOME),
            mock.patch.object(jarvis_profile, "ha", side_effect=lambda k, d=None: HOME.get(k, d)),
            mock.patch.object(room_scope, "ENABLED", True),
            mock.patch.object(whereabouts, "configured", return_value=True),
            mock.patch.object(whereabouts, "room", side_effect=lambda now=None: self.room),
            mock.patch.object(whereabouts, "offline", side_effect=lambda: self.offline),
            mock.patch.object(room_scope, "_busy", side_effect=lambda now=None: self.busy),
            mock.patch.object(room_scope, "_areas", return_value={"map": AREAS, "names": {}}),
        ]
        for p in self.patches:
            p.start()
        router._DEV["at"] = 0
        router._CHOICE.update(n="", devs=[], at=0.0, yes=False)
        router._ROOMQ.update(n="", rooms=[], handler=None, at=0.0)
        learning.PATH.unlink(missing_ok=True)
        learning._CACHE["mtime"] = None

    def tearDown(self):
        for p in self.patches:
            p.stop()
        router._DEV.clear()
        router._DEV.update(self.saved_dev)

    def say(self, text):
        r = router.route(text)
        self.assertEqual(r["kind"], "local", f"{text!r} went to the LLM")
        return r["reply"]


class LivingRoom(Base):
    def test_light_on(self):
        self.say("Light on")
        self.assertEqual(CALLS, [("light.light", "turn_on", {})])

    def test_tv_is_the_living_room_tv(self):
        self.say("TV on")
        self.assertEqual(CALLS, [("switch.tv", "turn_on", {})])

    def test_heating_without_room(self):
        self.say("Heating off")
        self.assertEqual(CALLS, [("climate.living_room_left", "set_hvac_mode", {"hvac_mode": "off"})])


class Bedroom(Base):
    room, busy = "Bedroom", {"Bedroom"}

    def test_tv_is_the_bedroom_tv(self):
        self.say("Turn the TV on")
        self.assertEqual(CALLS, [("switch.tv_bedroom", "turn_on", {})])

    def test_named_room_wins(self):
        self.say("Turn on the TV in the living room")
        self.assertEqual(CALLS, [("switch.tv", "turn_on", {})])

    def test_no_light_here_asks_then_yes(self):
        reply = self.say("Light on")
        self.assertEqual(CALLS, [])
        self.assertIn("There is no light in the Bedroom", reply)
        self.assertIn("turn on the one in the Living room?", reply)
        self.say("Yes")
        self.assertEqual(CALLS, [("light.light", "turn_on", {})])

    def test_a_learned_wording_still_asks(self):
        with mock.patch.object(learning, "alias", return_value=["light.light"]):
            self.assertIn("no light", self.say("Light on"))
        self.assertEqual(CALLS, [])

    def test_no_light_here_asks_then_no(self):
        self.say("Light on")
        self.assertIn("never mind", self.say("No"))
        self.assertEqual(CALLS, [])

    def test_light_off_from_bed_just_goes(self):
        self.say("Light off")
        self.assertEqual(CALLS, [("light.light", "turn_off", {})])

    def test_warmer(self):
        self.assertIn("set to 21 degrees", self.say("Make it warmer"))
        self.assertEqual(CALLS, [("climate.bedroom_left", "set_temperature", {"temperature": 21.0})])

    def test_warmer_turns_an_off_heating_on(self):
        CLIMATE["climate.bedroom_left"] = {"state": "off", "attributes": {"temperature": 5}}
        self.assertIn("on at 20 degrees", self.say("warmer"))
        self.assertEqual(CALLS, [("climate.bedroom_left", "set_hvac_mode", {"hvac_mode": "heat"}),
                                 ("climate.bedroom_left", "set_temperature", {"temperature": 20.0})])

    def test_colder_when_off(self):
        CLIMATE["climate.bedroom_left"] = {"state": "off", "attributes": {"temperature": 5}}
        self.assertIn("already off", self.say("colder please"))
        self.assertEqual(CALLS, [])

    def test_a_sentence_about_the_weather_is_no_command(self):
        self.assertIsNone(router._climate(router._norm("It is getting colder outside today than yesterday")))


class Unsure(Base):
    room, busy = "Living room", {"Bedroom"}   # the helper still says living room, the bedroom sensor sees him

    def test_asks_when_it_matters(self):
        reply = self.say("TV on")
        self.assertEqual(CALLS, [])
        self.assertIn("Living room or Bedroom", reply)
        self.say("Bedroom")
        self.assertEqual(CALLS, [("switch.tv_bedroom", "turn_on", {})])

    def test_no_question_when_both_rooms_mean_the_same(self):
        self.say("Light off")
        self.assertEqual(CALLS, [("light.light", "turn_off", {})])

    def test_learned_wording_just_goes(self):
        with mock.patch.object(learning, "alias", return_value=["light.light"]):
            self.say("Light on")
        self.assertEqual(CALLS, [("light.light", "turn_on", {})])

    def test_heating_asks(self):
        self.assertIn(" or ", self.say("warmer"))
        self.say("in the living room")
        self.assertEqual(CALLS, [("climate.living_room_left", "set_temperature", {"temperature": 21.0})])

    def test_cancel(self):
        self.say("TV on")
        self.assertIn("never mind", self.say("forget it"))
        self.assertEqual(CALLS, [])

    def test_current(self):
        self.assertEqual(room_scope.current(), {"room": "Living room", "sure": False,
                                                "candidates": ["Living room", "Bedroom"], "status": "ok"})


class Fallbacks(Base):
    room, busy = "Bedroom", {"Bedroom"}

    def test_switched_off_behaves_as_before(self):
        with mock.patch.object(room_scope, "ENABLED", False):
            self.say("TV on")
            self.assertIsNone(router._climate(router._norm("Heating on")))
            self.assertEqual(room_scope.current()["status"], "off")
        self.assertEqual(CALLS, [("switch.tv", "turn_on", {})])

    def test_home_assistant_down_behaves_as_before(self):
        self.offline = True
        self.say("TV on")
        self.assertEqual(CALLS, [("switch.tv", "turn_on", {})])
        self.assertIn("not reachable", room_scope.line())

    def test_nobody_seen_keeps_the_last_room(self):
        self.busy = set()                          # asleep: no sensor sees him, the helper keeps the room
        self.assertTrue(room_scope.current()["sure"])

    def test_bathroom_counts_as_bedroom(self):
        self.assertEqual(room_scope.room_of("binary_sensor.bathroom_presence_bathroom"), "Bedroom")


class Context(Base):
    room, busy = "Living room", {"Living room"}

    def test_line_lists_the_devices_of_the_room(self):
        line = room_scope.line()
        self.assertIn("in the Living room", line)
        self.assertIn("light.light", line)
        self.assertIn("climate.living_room_left", line)
        self.assertNotIn("switch.pc", line)        # keeps things alive: never meant by "off"
        self.assertNotIn("bedroom", line.split("Devices there:")[1])

    def test_unsure_line_says_so(self):
        self.busy = {"Bedroom"}
        self.assertIn("unsure", room_scope.line())


class Presence(unittest.TestCase):
    """whereabouts.room(): a hiccup keeps the last room for STALE_S, then it is no longer trusted."""

    def setUp(self):
        whereabouts.forget()
        self.p = [mock.patch.object(whereabouts, "configured", return_value=True)]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()
        whereabouts.forget()

    def _answer(self, state):
        class R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return ('{"state": "%s"}' % state).encode()
        return mock.patch.object(whereabouts.urllib.request, "urlopen", return_value=R())

    def test_hiccup_then_stale(self):
        with self._answer("Bedroom"):
            self.assertEqual(whereabouts.room(1000.0), "Bedroom")
        self.assertFalse(whereabouts.offline())
        with mock.patch.object(whereabouts.urllib.request, "urlopen", side_effect=OSError("down")):
            self.assertEqual(whereabouts.room(1100.0), "Bedroom")
            self.assertTrue(whereabouts.offline())
            self.assertEqual(whereabouts.room(1000.0 + whereabouts.STALE_S + 50), "")
        with self._answer("Living room"):
            self.assertEqual(whereabouts.room(2000.0), "Living room")
        self.assertFalse(whereabouts.offline())

    def test_unavailable_helper_counts_as_offline(self):
        with self._answer("unavailable"):
            self.assertEqual(whereabouts.room(1000.0), "")
        self.assertTrue(whereabouts.offline())


class Output(unittest.TestCase):
    """"Where I am" without presence or with a dead Sonos: the device answers and says why, once."""
    SZ = "media_player.bedroom_bedroom"

    def setUp(self):
        sonos._NOTE.update(why="", told="")
        sonos._PLAYER.clear()
        self.offline, self.state = False, "playing"
        self.p = [mock.patch.object(whereabouts, "room", return_value="Bedroom"),
                  mock.patch.object(whereabouts, "offline", side_effect=lambda: self.offline),
                  mock.patch.object(whereabouts, "speaker", return_value=self.SZ),
                  mock.patch.object(sonos, "label", side_effect=lambda e: "Sonos Bedroom" if e == self.SZ else ""),
                  mock.patch.object(sonos, "_ha", side_effect=lambda path, body=None:
                                    ('{"state": "%s"}' % self.state).encode())]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()
        sonos._NOTE.update(why="", told="")
        sonos._PLAYER.clear()

    def test_room_speaker(self):
        self.assertEqual(sonos.resolve(sonos.AUTO), self.SZ)
        self.assertEqual(sonos.fallback_note(), "")

    def test_presence_down_told_once(self):
        self.offline = True
        self.assertEqual(sonos.resolve(sonos.AUTO), "")
        self.assertIn("which room", sonos.fallback_note())
        sonos.resolve(sonos.AUTO)
        self.assertEqual(sonos.fallback_note(), "")
        self.offline = False                        # back: the next outage is told again
        self.assertEqual(sonos.resolve(sonos.AUTO), self.SZ)
        self.offline = True
        sonos.resolve(sonos.AUTO)
        self.assertIn("which room", sonos.fallback_note())

    def test_unavailable_sonos(self):
        self.state = "unavailable"
        self.assertEqual(sonos.resolve(sonos.AUTO), "")
        self.assertIn("Sonos Bedroom cannot be reached right now", sonos.fallback_note())

    def test_failed_announcement(self):
        self.assertIn("does not answer", sonos.failed(self.SZ))
        self.assertEqual(sonos.resolve(sonos.AUTO), "")      # remembered as dead for PLAYER_TTL_S
        sonos._PLAYER[self.SZ] = (time.time() - sonos.PLAYER_TTL_S - 1, False)
        self.assertEqual(sonos.resolve(sonos.AUTO), self.SZ)  # alive again after the next look


if __name__ == "__main__":
    unittest.main()
