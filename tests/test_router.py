"""
Router, timers, lists and routines — without Home Assistant, Claude or a mic.

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v

Home Assistant is faked (a few devices, every switch call is recorded), state
and vault live in a temp folder, so the tests never touch the real house or
the real state.json.
"""
import datetime as dt
import json
import os
import pathlib
import sys
import time
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402  (must come before any Jarvis module)

import commands       # noqa: E402
import events         # noqa: E402
import fallback_llm   # noqa: E402
import jarvis_mcp     # noqa: E402
import lists          # noqa: E402
import router         # noqa: E402
import routines       # noqa: E402
import scheduler      # noqa: E402
import briefing       # noqa: E402

CALLS = []
DEVICES = [("light.living_room", "Living Room"), ("light.kitchen", "Kitchen"), ("switch.router", "Router"),
           ("switch.fan", "Fan"), ("switch.fridge", "Fridge"),
           ("light.led_living_room", "LED Living Room"),                     # a real lamp (e.g. Tuya)
           ("switch.tv_led", "TV LED"),                                      # status LED of a socket
           ("switch.ledlivingroom_internet_access", "ledlivingroom Internet Access"),   # FRITZ!Box
           ("switch.fritz_box_wifi_main", "FRITZ!Box 6660 Cable Wi-Fi Main 5Ghz"),
           ("light.led_pc", "LED PC"), ("switch.pc", "PC")]                  # a lamp at the PC vs. the PC socket
STATES = {"light.living_room": "on", "light.kitchen": "off", "switch.router": "on", "switch.fan": "off",
          "switch.fridge": "on", "light.led_living_room": "on", "switch.tv_led": "on",
          "switch.ledlivingroom_internet_access": "on", "switch.fritz_box_wifi_main": "on",
          "light.led_pc": "on", "switch.pc": "on"}


def fake_home(name, args):
    CALLS.append((args["entity_id"], args["service"], args.get("data") or {}))
    return f"Done: {args['service']} for {args['entity_id']}."


def fake_ha(path, body=None):
    CALLS.append((body.get("entity_id"), path.rsplit("/", 1)[-1], {}))
    return []


jarvis_mcp._home = fake_home
jarvis_mcp._ha = fake_ha
briefing._ha_states = lambda: [{"entity_id": e, "state": STATES[e], "attributes": {"friendly_name": n}}
                               for e, n in DEVICES]
router._DEV["at"] = 0
router._devices()                                  # the real filter runs over the fake house once
router._DEV["at"] = time.time() + 10 ** 9

# The PC is never really touched: volume, apps and the lock screen are recorded instead.
import answer_cache   # noqa: E402
import commute        # noqa: E402
import learning       # noqa: E402
import parcels        # noqa: E402
import pc             # noqa: E402
import pronounce      # noqa: E402

PC = []
pc.set_volume = lambda p: PC.append(("volume", p)) or True
pc.step = lambda d: PC.append(("step", d)) or 50 + d
pc.mute = lambda on=True: PC.append(("mute", on)) or True
pc.lock = lambda: PC.append(("lock",)) or True
pc.open_app = lambda name: PC.append(("open", name.lower())) or name
pc.volume = lambda: 42
learning.USAGE = TMP / "usage.jsonl"


def route(text):
    return router.route(text)


class Base(unittest.TestCase):
    def setUp(self):
        router.forget_topic()
        CALLS.clear()
        PC.clear()
        learning.PATH.unlink(missing_ok=True)
        learning._CACHE["mtime"] = None
        commands.update_state(lambda d: d.update(timers=[], notified={}))
        for p in (TMP / "vault").rglob("*.md"):
            p.unlink()
        routines.PATH.unlink(missing_ok=True)
        routines._CACHE["mtime"] = None

    def local(self, text, handler=None):
        r = route(text)
        self.assertEqual(r["kind"], "local", f"{text!r} went to the LLM: {r}")
        if handler:
            self.assertEqual(r["handler"], handler, text)
        return r["reply"]

    def llm(self, text, *groups):
        r = route(text)
        self.assertEqual(r["kind"], "llm", f"{text!r} was handled locally: {r.get('reply')}")
        for g in groups:
            self.assertIn(g, r["groups"], text)
        return r


class HomeRules(Base):
    def test_switch_on(self):
        self.local("Turn on the light in the living room", "home")
        self.assertEqual(CALLS, [("light.living_room", "turn_on", {})])

    def test_dim(self):
        self.local("Living room light to 40 percent", "home")
        self.assertEqual(CALLS, [("light.living_room", "turn_on", {"brightness_pct": 40})])

    def test_question_never_switches(self):
        for text in ("Is the light in the living room on?", "Living room on?", "Is the fan off?"):
            route(text)
        self.assertEqual(CALLS, [], "a question switched something")

    def test_state_answer(self):
        self.assertIn("on", self.local("Is the light in the living room on?", "home_state"))

    def test_led_lamp_is_a_lamp(self):
        self.local("LED living room off", "home")
        self.assertEqual(CALLS, [("light.led_living_room", "turn_off", {})])

    def test_indicator_leds_and_internet_switches_are_never_targets(self):
        eids = {d["eid"] for d in router._devices()}
        self.assertNotIn("switch.tv_led", eids)
        self.assertNotIn("switch.ledlivingroom_internet_access", eids)
        route("Switch TV LED off")
        route("ledlivingroom internet access off")
        self.assertFalse([c for c in CALLS if c[0].startswith("switch.")])

    def test_all_lights(self):
        self.local("Turn off all the lights", "home")
        self.assertEqual(sorted(c[0] for c in CALLS),
                         ["light.kitchen", "light.led_living_room", "light.led_pc", "light.living_room"])
        self.assertTrue(all(c[1] == "turn_off" for c in CALLS))

    def test_lamp_named_pc_is_not_the_pc(self):
        self.local("LED PC off", "home")
        self.assertEqual(CALLS, [("light.led_pc", "turn_off", {})])
        CALLS.clear()
        self.llm("PC off")                               # the PC socket stays protected
        self.assertEqual(CALLS, [])

    def test_protected_devices_go_to_llm(self):
        self.llm("Switch off the router")
        self.llm("Turn the fridge off")
        self.llm("Fritz Box off")
        self.llm("Switch the WiFi Main off")
        self.assertEqual(CALLS, [])

    def test_device_aliases(self):
        # The living-room lamp HA calls just "LED", next to a dead lamp; the user says light, lamp, socket.
        house = [("light.light", "LED", "on"), ("light.lamp_living_room", "Dead", "unavailable"),
                 ("light.kitchen", "Kitchen", "off"), ("switch.pc", "PC", "on")]
        states, aliases = briefing._ha_states, router._aliases
        briefing._ha_states = lambda: [{"entity_id": e, "state": s, "attributes": {"friendly_name": n}}
                                       for e, n, s in house]
        router._aliases = lambda: {"light.light": ["Living Room", "Light", "Lamp", "Socket"]}
        router._DEV["at"] = 0
        try:
            self.assertNotIn("light.lamp_living_room", {d["eid"] for d in router._devices()})
            for text in ("Living room, light off", "Living room off", "Light off", "Lamp on",
                         "Turn off the socket in the living room", "LED on"):
                CALLS.clear()
                self.local(text, "home")
                self.assertEqual([c[0] for c in CALLS], ["light.light"], text)
            CALLS.clear()
            self.local("Light kitchen on", "home")           # a real name beats the generic alias word
            self.assertEqual(CALLS, [("light.kitchen", "turn_on", {})])
            CALLS.clear()
            self.local("Turn off all the lights", "home")    # the dead lamp is left out
            self.assertEqual(sorted(c[0] for c in CALLS), ["light.kitchen", "light.light"])
            self.assertIn("on", self.local("Is the light in the living room on?", "home_state"))
        finally:
            briefing._ha_states, router._aliases = states, aliases
            router._DEV["at"] = 0
            router._devices()
            router._DEV["at"] = time.time() + 10 ** 9

    def test_conditions_are_not_switched_now(self):
        self.llm("When I say good night, turn off all the lights", "routines", "home")
        self.assertEqual(CALLS, [])

    def test_delayed_switch_is_scheduled_not_done(self):
        reply = self.local("Turn the light in the living room off in 10 minutes", "timers")
        self.assertEqual(CALLS, [])
        (item,) = scheduler.items()
        self.assertEqual(item["kind"], "action")
        self.assertAlmostEqual(item["at"] - time.time(), 600, delta=5)
        self.assertIn("off", reply)
        self.assertEqual(router.run_local(item["text"]) is not None, True)
        self.assertEqual(CALLS, [("light.living_room", "turn_off", {})])


class Timers(Base):
    def test_timer(self):
        self.assertIn("10 minutes", self.local("Timer 10 minutes", "timers"))
        (item,) = scheduler.items()
        self.assertEqual(item["kind"], "timer")
        self.assertAlmostEqual(item["at"] - time.time(), 600, delta=3)

    def test_timer_words_and_label(self):
        reply = self.local("Set a timer for half an hour for the pasta")
        (item,) = scheduler.items()
        self.assertAlmostEqual(item["at"] - time.time(), 1800, delta=3)
        self.assertEqual(item["text"], "pasta")
        self.assertIn("pasta", reply)

    def test_timer_compound_number(self):
        self.local("Timer for twenty-five minutes")
        self.assertAlmostEqual(scheduler.items()[0]["at"] - time.time(), 1500, delta=3)

    def test_alarm(self):
        self.local("Wake me up tomorrow at half past six", "timers")
        (item,) = scheduler.items()
        when = dt.datetime.fromtimestamp(item["at"])
        self.assertEqual((when.date() - dt.date.today()).days, 1)
        self.assertEqual((when.hour, when.minute), (6, 30))

    def test_reminder_relative(self):
        reply = self.local("Remind me in 20 minutes about the laundry", "timers")
        (item,) = scheduler.items()
        self.assertEqual(item["text"], "the laundry")
        self.assertAlmostEqual(item["at"] - time.time(), 1200, delta=3)
        self.assertIn("20 minutes", reply)

    def test_reminder_absolute(self):
        self.local("Remind me tomorrow at 9 to call Anna")
        (item,) = scheduler.items()
        when = dt.datetime.fromtimestamp(item["at"])
        self.assertEqual((when.hour, when.minute), (9, 0))
        self.assertEqual(item["text"], "call Anna")

    def test_reminder_without_time_goes_to_llm(self):
        self.llm("Remind me about the tax return", "timers")

    def test_list_and_cancel(self):
        self.local("Timer 5 minutes")
        self.assertIn("left", self.local("Which timers are running?"))
        self.assertIn("deleted", self.local("Stop the timer"))
        self.assertEqual(scheduler.items(), [])
        self.assertIn("no", self.local("Delete the alarm"))

    def test_snooze(self):
        self.assertIn("9 minutes", self.local("Snooze"))
        self.assertEqual(scheduler.items()[0]["kind"], "alarm")

    def test_ringing(self):
        scheduler.add("timer", time.time() - 1, "Pizza", 300)
        before = events._LOG[-1]["id"] if events._LOG else 0
        scheduler._due(time.time())
        rung = [e for e in events._LOG if e["id"] > before and e["t"] == "alarm"]
        self.assertEqual(len(rung), 1)
        self.assertIn("Pizza", rung[0]["text"])
        self.assertEqual(scheduler.items(), [])


class TimeParsing(unittest.TestCase):
    NOW = dt.datetime(2026, 10, 14, 14, 0)        # a Wednesday afternoon

    def at(self, text, kind="alarm"):
        return scheduler.resolve(scheduler.numify(router._norm(text)), kind, self.NOW)

    def test_clock_words(self):
        self.assertEqual(self.at("at quarter to eight"), dt.datetime(2026, 10, 15, 7, 45))
        self.assertEqual(self.at("at half past seven in the evening"), dt.datetime(2026, 10, 14, 19, 30))
        self.assertEqual(self.at("at 7:15"), dt.datetime(2026, 10, 15, 7, 15))
        self.assertEqual(self.at("at 4:30 pm"), dt.datetime(2026, 10, 14, 16, 30))

    def test_reminder_means_the_coming_six(self):
        self.assertEqual(self.at("at 6", "reminder"), dt.datetime(2026, 10, 14, 18, 0))
        self.assertEqual(self.at("at 6", "alarm"), dt.datetime(2026, 10, 15, 6, 0))

    def test_weekday(self):
        self.assertEqual(self.at("on Friday at 9"), dt.datetime(2026, 10, 16, 9, 0))
        self.assertEqual(self.at("on Wednesday at 9"), dt.datetime(2026, 10, 21, 9, 0))

    def test_minutes_are_not_a_clock(self):
        self.assertIsNone(scheduler.clock(scheduler.numify(router._norm("Timer for 10 minutes"))))

    def test_durations(self):
        d = lambda s: scheduler.duration(scheduler.numify(router._norm(s)))  # noqa: E731
        self.assertEqual(d("an hour and 30 minutes"), 5400)
        self.assertEqual(d("one and a half hours"), 5400)
        self.assertEqual(d("90 seconds"), 90)
        self.assertEqual(d("a quarter of an hour"), 900)

    def test_spoken(self):
        self.assertEqual(scheduler.fmt_dur(90), "one minute and 30 seconds")
        self.assertEqual(scheduler.fmt_dur(7200), "2 hours")


class Lists(Base):
    def test_add_read_remove(self):
        self.assertIn("Milk and Eggs", self.local("Put milk and eggs on the shopping list", "lists"))
        self.assertIn("Butter", self.local("Add butter to the shopping list"))
        self.assertEqual(self.local("What is on the shopping list?"),
                         "On the shopping list: Milk, Eggs and Butter.")
        self.assertIn("crossed off", self.local("Cross the milk off the shopping list"))
        self.assertEqual(lists.entries("shopping"), ["Eggs", "Butter"])

    def test_no_duplicates(self):
        self.local("Milk on the shopping list")
        self.assertIn("Already", self.local("Put milk on the shopping list"))

    def test_todo_and_clear(self):
        self.local("Write tax return on my to-do list")
        self.assertEqual(lists.entries("todo"), ["Tax return"])
        self.assertEqual(lists.entries("shopping"), [])
        self.assertIn("cleared", self.local("Clear the to-do list"))
        self.assertEqual(lists.entries("todo"), [])

    def test_note_is_a_checklist(self):
        self.local("Milk on the shopping list")
        self.assertIn("- [ ] Milk", (lists.LIST_DIR / "Shopping list.md").read_text(encoding="utf-8"))


class Routines(Base):
    def save(self, data):
        routines.PATH.write_text(json.dumps(data), encoding="utf-8")
        routines._CACHE["mtime"] = None

    def test_trigger_runs_steps(self):
        self.save([{"name": "Good night", "triggers": ["good night"],
                    "steps": [{"do": "Living room light off"}, {"say": "Sleep well."}]}])
        self.assertEqual(self.local("Good night, Jarvis", "routine"), "Sleep well.")
        self.assertEqual(CALLS, [("light.living_room", "turn_off", {})])

    def test_only_exact_phrase(self):
        self.save([{"name": "Good night", "triggers": ["good night"], "steps": [{"say": "x"}]}])
        self.assertNotEqual(route("I wish Anna a good night").get("handler"), "routine")

    def test_tell_and_failures(self):
        self.save([{"name": "Test", "triggers": ["test routine"],
                    "steps": [{"tell": "What time is it?"}, {"do": "Explain the world to me"}]}])
        reply = self.local("test routine")
        self.assertRegex(reply, r"\d{1,2}:\d{2}")
        self.assertIn("Did not work", reply)

    def test_validation(self):
        self.assertTrue(routines.validate([{"ha": "switch.turn_off", "entity_id": "all"}]))
        self.assertTrue(routines.validate([{"ha": "switch.turn_off", "entity_id": "switch.router"}]))
        self.assertTrue(routines.validate([{"ha": "lock.unlock", "entity_id": "lock.door"}]))
        self.assertEqual(routines.validate([{"ha": "light.turn_off", "entity_id": "all"}]), "")

    def test_tool_save(self):
        res = routines.tool("routine_save", {"name": "Cinema", "triggers": ["movie night"],
                                             "steps": [{"ha": "light.turn_on", "entity_id": "light.kitchen",
                                                        "data": {"brightness_pct": 10}}]})
        self.assertIn("saved", res)
        self.local("Movie night")
        self.assertEqual(CALLS, [("light.kitchen", "turn_on", {})])


class Misc(Base):
    def test_time(self):
        self.assertRegex(self.local("What time is it?", "time"), r"\d{1,2}:\d{2}")

    def test_smart_question(self):
        r = self.llm("Explain to me why the sky is blue")
        self.assertEqual(r["model"], router.MODEL_SMART)

    def test_quiet_hours(self):
        self.assertTrue(events.quiet_now(dt.datetime(2026, 1, 1, 23)))
        self.assertTrue(events.quiet_now(dt.datetime(2026, 1, 1, 6)))
        self.assertFalse(events.quiet_now(dt.datetime(2026, 1, 1, 12)))

    def test_claude_blocked(self):
        self.assertTrue(fallback_llm.claude_blocked({"status": "rejected", "resets": time.time() + 600}))
        self.assertFalse(fallback_llm.claude_blocked({"status": "rejected", "resets": time.time() - 600}))
        self.assertFalse(fallback_llm.claude_blocked({"status": "allowed"}))


class PcControl(Base):
    def test_volume(self):
        self.assertIn("30", self.local("Volume to 30 percent", "pc"))
        self.local("PC louder")
        self.local("Sound off")
        self.local("Mute off")
        self.assertEqual(PC, [("volume", 30), ("step", 10), ("mute", True), ("mute", False)])

    def test_volume_misheard(self):
        # Heard like this at 11:54: no handler matched, and Jarvis said his volume tool was missing.
        self.assertIn("100", self.local("set the volumn to 100%.", "pc"))
        self.assertEqual(PC, [("volume", 100)])

    def test_follow_up_keeps_the_last_tools(self):
        # "So what." after a mail question used to get memory only — "I lack the mail tool".
        self.assertIn("mail", route("What is in my mails?")["groups"])
        follow = route("Then everything again, but you have already said most of it.")
        self.assertIn("mail", follow["groups"])
        self.assertEqual(follow["reason"], "follow-up")
        self.assertIn("mail", route("So what.")["groups"], "a chain of vague turns keeps the topic")
        self.assertNotIn("mail", route("What will the weather be like tomorrow in Hamburg?").get("groups", []),
                         "a new topic brings its own tools only")

    def test_follow_up_with_own_topic_keeps_the_full_ha_server(self):
        # 13:12: "at what percentage ..." brought "home" itself and lost ha_full from the automation question.
        self.assertIn("ha_full", router.route("Show me the power automation in Home Assistant", ha_full=True)["groups"])
        r = route("I want to know at what percentage it stops drawing power from the battery.")
        self.assertIn("home", r["groups"])
        self.assertIn("ha_full", r["groups"])

    def test_joined_groups_do_not_pile_up(self):
        route("What is in my mails?")
        route("Please research thoroughly which golf courses there are in Brandenburg")   # takes mail along, only itself
        self.assertNotIn("mail", route("Explain to me why the lamp in the living room sometimes flickers")["groups"])

    def test_follow_up_carry_expires(self):
        route("What is in my mails?")
        router._LAST["at"] -= router.CARRY_S + 1
        self.assertEqual(route("So what.")["groups"], ["memory", "web"], "only the search that rides along")

    def test_web_search_rides_along_without_the_big_model(self):
        # "please find out yourself what course rating the course has" got memory only and no search.
        r = route("How long is the outdoor pool in Pankow open today?")
        self.assertIn("web", r["groups"])
        r = route("i have a handicap of 48 and please find out yourself what course rating the course has.")
        self.assertIn("web", r["groups"])
        self.assertEqual(r["model"], router.MODEL_SMART, "an explicit search request gets the bigger model")
        router.forget_topic()
        self.assertEqual(route("Tell me a nice saying").get("model"), router.MODEL_SIMPLE,
                         "the search riding along does not make every turn expensive")

    def test_web_search_is_no_topic_for_follow_ups(self):
        route("What is in my mails?")
        self.assertIn("mail", route("So what.")["groups"], "riding-along web must not end the follow-up carry")

    def test_volume_number_first(self):
        # Heard like this: the number before "volume" must set, not read the volume out.
        self.assertIn("100", self.local("set my PC to 100% volume.", "pc"))
        self.assertIn("100", self.local("A PC Jarvis to 100% volume.", "pc"))
        self.assertIn("40", self.local("Set the computer to 40 percent", "pc"))
        self.assertEqual(PC, [("volume", 100), ("volume", 100), ("volume", 40)])

    def test_music_volume_stays_with_music(self):
        r = route("Music quieter")
        self.assertNotEqual(r.get("handler"), "pc")

    def test_apps_and_lock(self):
        self.local("Open Spotify", "pc")
        self.local("Lock the screen", "pc")
        self.assertEqual(PC, [("open", "spotify"), ("lock",)])

    def test_unknown_app_and_light_are_not_pc(self):
        self.assertNotEqual(route("Open the tax return from 2019").get("handler"), "pc")
        self.local("Turn on the light in the living room", "home")
        self.assertEqual(PC, [])


class Learning(Base):
    def test_learns_a_switch_and_does_it_alone_next_time(self):
        self.llm("Turn off the reading lamp")              # unknown name: the LLM has to find it
        calls = [("mcp__jarvis__home_states", {"search": "reading"}),
                 ("mcp__jarvis__home_control", {"entity_id": "light.kitchen", "service": "turn_off"})]
        learned = learning.observe("Turn off the reading lamp", calls)
        self.assertEqual(learned, "lamp reading")
        self.llm("Turn off the reading lamp")              # only a draft: still the LLM
        import skill_factory
        skill_factory._ha_entities = lambda: {"light.kitchen"}
        self.assertIn("Active", skill_factory.activate("lamp reading"))   # what the card's yes does
        self.local("Turn off the reading lamp", "home")
        self.assertEqual(CALLS, [("light.kitchen", "turn_off", {})])
        self.local("Reading lamp on")
        self.assertEqual(CALLS[-1], ("light.kitchen", "turn_on", {}))

    def test_does_not_learn_questions_or_values(self):
        self.assertIsNone(learning.observe("Is the reading lamp on?",
                                           [("home_control", {"entity_id": "light.x", "service": "turn_on"})]))
        self.assertIsNone(learning.observe("Reading lamp to 30 percent",
                                           [("home_control", {"entity_id": "light.x", "service": "turn_on",
                                                              "data": {"brightness_pct": 30}})]))
        self.assertIsNone(learning.observe("Turn off the reading lamp", [("web_fetch", {"url": "x"})]))

    def test_learned_protected_device_still_protected(self):
        learning.observe("Turn off the box", [("home_control", {"entity_id": "switch.router",
                                                               "service": "turn_off"})])
        import skill_factory
        skill_factory._ha_entities = lambda: {"switch.router"}
        skill_factory.activate("box")
        self.llm("Turn off the box")
        self.assertEqual(CALLS, [])

    def test_report(self):
        rows = [dict(at=time.strftime("%Y-%m-%d %H:%M:%S"), route="llm:simple", message="Do I have mails?",
                     total_in=9000, output=100)] * 3 + [dict(at=time.strftime("%Y-%m-%d %H:%M:%S"),
                                                             route="local:home", message="light on")]
        learning.USAGE.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        reply = self.local("Usage report", "report")
        self.assertIn("25 percent", reply)
        self.assertIn("do i have mails", reply)


class Sun(Base):
    def setUp(self):
        super().setUp()
        router._WX.update(at=time.time(), d={"daily": {
            "sunrise": ["2026-10-15T07:32", "2026-10-16T07:34", "2026-10-17T07:36"],
            "sunset": ["2026-10-15T18:12", "2026-10-16T18:10", "2026-10-17T18:08"]}})

    def tearDown(self):
        router._WX.clear()
        super().tearDown()

    def test_sunset_today(self):
        self.assertIn("18:12", self.local("When does the sun set?", "sun"))

    def test_sunrise_tomorrow(self):
        reply = self.local("When is sunrise tomorrow?", "sun")
        self.assertIn("07:34", reply)
        self.assertNotIn("18:10", reply)

    def test_both_with_daylight(self):
        reply = self.local("How long does it stay light the day after tomorrow?", "sun")
        self.assertIn("07:36", reply)
        self.assertIn("10 hours 32 minutes", reply)

    def test_rest_goes_to_weather_tool(self):
        for text in ("How high is the UV index?", "Will the sun shine tomorrow?", "How many hours of sunshine today?",
                     "What will the weather be like at the weekend?", "How warm will it be in Madrid?"):
            self.llm(text, "weather")
            # the search that rides along in every turn is fine; no search *request* though
            self.assertNotIn("web", router.llm_route(text)["groups"], text)

    def test_no_weather_for_unrelated(self):
        for text in ("Restart Windows", "Make the light brighter", "What is the temperature in the living room?"):
            self.assertNotIn("weather", router.llm_route(text)["groups"], text)


class MailRoute(Base):
    def test_mark_read_goes_to_mail(self):
        self.llm("Mark all mails as read", "mail")
        self.llm("Mark the ones from Amazon as read", "mail")


class CalendarRoute(Base):
    def test_day_plan_questions_get_the_calendar(self):
        # "what else is on today?" got memory only, and Jarvis said he could not see the calendar
        for text in ("what else is on today?", "What do I have planned today?", "Do I have anything on today?",
                     "What is on this week", "Am I free on Saturday?"):
            self.llm(text, "calendar")

    def test_switches_stay_with_the_house(self):
        self.assertNotIn("calendar", route("Is the TV on?").get("groups", []))


class Cache(unittest.TestCase):
    def setUp(self):
        answer_cache.clear()

    def test_read_only_is_cached(self):
        answer_cache.put("Do I have new mails?", ["memory", "mail"], "Two, Sir.")
        self.assertEqual(answer_cache.get("do i have new mails", ["mail", "memory"]), "Two, Sir.")

    def test_changes_and_saves_are_not(self):
        answer_cache.put("Switch the light on", ["memory", "home"], "x")
        answer_cache.put("Remember that Anna likes tea", ["memory"], "Noted.")
        answer_cache.put("What do you know about Anna?", ["memory"], "y", tools_used=["mcp__jarvis__memory_save"])
        self.assertIsNone(answer_cache.get("Switch the light on", ["memory", "home"]))
        self.assertIsNone(answer_cache.get("Remember that Anna likes tea", ["memory"]))
        self.assertIsNone(answer_cache.get("What do you know about Anna?", ["memory"]))

    def test_marking_mails_read_empties_cache(self):
        answer_cache.put("Do I have new mails?", ["mail"], "Two, Sir.")
        answer_cache.put("Mark the mails from Amazon as read", ["mail"], "Done.")
        self.assertEqual(answer_cache.get("Do I have new mails?", ["mail"]), "Two, Sir.")
        answer_cache.put("The ones from Amazon are read", ["mail"], "Done.",
                         tools_used=["mcp__jarvis__mail_mark_read"])
        self.assertIsNone(answer_cache.get("Do I have new mails?", ["mail"]))
        self.assertIsNone(answer_cache.get("Mark the mails from Amazon as read", ["mail"]))

    def test_expires(self):
        answer_cache.put("How is the NAS?", ["systems"], "Fine.")
        key = answer_cache._key("How is the NAS?", ["systems"])
        answer_cache._DATA[key] = (time.time() - answer_cache.TTL - 1, "Fine.")
        self.assertIsNone(answer_cache.get("How is the NAS?", ["systems"]))


class Pronounce(Base):
    def test_defaults_whole_words(self):
        self.assertEqual(pronounce.apply("The NAS has 3 kWh."), "The naz has 3 kilowatt hours.")
        self.assertEqual(pronounce.apply("HA and HAL"), "Home Assistant and HAL")

    def test_learn_by_voice(self):
        self.assertIn("Too-ya", self.local("Pronounce Tuya as Too-ya", "pronounce"))
        self.assertEqual(pronounce.apply("Tuya lamp"), "Too-ya lamp")
        pronounce.PATH.unlink()
        pronounce._CACHE["mtime"] = "reset"


class Commute(Base):
    def test_when_to_leave(self):
        start = dt.datetime.now().replace(second=0, microsecond=0) + dt.timedelta(hours=2)
        orig = commute.next_trip
        commute.next_trip = lambda now=None: ({"title": "Dentist", "start": start}, 20,
                                              commute.leave_at(start, 20))
        try:
            reply = self.local("When do I have to leave?", "commute")
        finally:
            commute.next_trip = orig
        leave = start - dt.timedelta(minutes=20 + commute.BUFFER_MIN)
        self.assertIn("Dentist", reply)
        self.assertIn(f"{leave.hour}:{leave.minute:02d}", reply)

    def test_how_long(self):
        orig = commute.geocode, commute.drive_minutes
        commute.geocode, commute.drive_minutes = (lambda p: ("50.1", "8.6")), (lambda d: 17)
        try:
            self.assertIn("17 minutes", self.local("How long does it take me to get to the central station?", "commute"))
        finally:
            commute.geocode, commute.drive_minutes = orig


class Parcels(Base):
    def setUp(self):
        super().setUp()
        now = time.time()
        self.items = [
            dict(sender="DHL", subject="Your parcel arrives today", status="today", when=now, id="1"),   # now: always today, even just after midnight
            dict(sender="Amazon", subject="Shipped: cable", status="on the way", when=now - 86400, id="2"),
            dict(sender="Hermes", subject="Delivered", status="delivered", when=now - 5 * 86400, id="3"),
        ]
        self.orig = parcels.shipments
        parcels.shipments = lambda days=14, ttl=600: self.items

    def tearDown(self):
        parcels.shipments = self.orig

    def test_classify(self):
        self.assertEqual(parcels.classify("Your shipment is out for delivery"), "today")
        self.assertEqual(parcels.classify("Parcel is ready for pickup at the parcel locker"), "pickup")
        self.assertEqual(parcels.classify("Your parcel has been delivered"), "delivered")
        self.assertEqual(parcels.classify("Order shipped"), "on the way")
        self.assertIsNone(parcels.classify("Newsletter October"))

    def test_questions(self):
        self.assertEqual(self.local("Is a parcel coming today?", "parcels"), "Parcels, out for delivery today: DHL.")
        reply = self.local("Which parcels are on the way?", "parcels")
        self.assertIn("Amazon", reply)
        self.assertNotIn("Hermes", reply)               # delivered five days ago: old news

    def test_other_meanings_of_package(self):
        self.assertNotEqual(route("Install the package on the Pi").get("handler"), "parcels")


if __name__ == "__main__":
    unittest.main()


class MusicRooms(Base):
    """The music into another room goes to the music tool as a transfer, without the LLM."""

    def setUp(self):
        super().setUp()
        import speakers
        self.moved = []
        self.patches = [unittest.mock.patch.object(speakers, "rooms", return_value=["Bedroom", "Living Room"]),
                        unittest.mock.patch.object(jarvis_mcp, "_music_room",
                                                   side_effect=lambda room: self.moved.append(room) or f"The music is now playing in the {room}.")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_target_is_the_room_not_said_as_source(self):
        self.assertIn("Living Room", self.local("Put the music from the bedroom on in the living room", "music"))
        self.local("Move the music from the bedroom to the living room", "music")
        self.local("Put the music from the living room on in the bedroom", "music")
        self.local("Music to the bedroom", "music")
        self.assertEqual(self.moved, ["Living Room", "Living Room", "Bedroom", "Bedroom"])

    def test_plain_music_commands_and_lights_stay_as_they_were(self):
        route("Music quieter")
        self.assertEqual(self.moved, [])
        self.assertNotEqual(route("Turn on the light in the living room").get("handler"), "music")
        self.llm("Play the playlist Chill in the living room", "music")
        self.assertEqual(self.moved, [])
