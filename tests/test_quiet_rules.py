"""
The quiet rules of the proactive mode — the heart of "when does Jarvis keep his mouth shut".

    .venv\\Scripts\\python.exe -m unittest tests.test_quiet_rules -v
"""
import datetime as dt
import itertools
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import proactive_config as pc  # noqa: E402
import quiet_rules as qr       # noqa: E402

DAY = dt.datetime(2026, 11, 5, 14, 0)
NIGHT = dt.datetime(2026, 11, 5, 23, 30)
EARLY = dt.datetime(2026, 11, 6, 6, 15)


def ctx(now=DAY, **kw):
    return dict(now=now, quiet_hours="22-7", **kw)


class Levels(unittest.TestCase):
    def test_demote(self):
        self.assertEqual(qr.demote("important", 1), "nice")
        self.assertEqual(qr.demote("important", 2), "muted")
        self.assertEqual(qr.demote("nice", 5), "muted")
        self.assertEqual(qr.demote("critical", 3), "critical")       # never lowered
        self.assertEqual(qr.demote("important", 0), "important")
        self.assertEqual(qr.demote("nonsense", 0), "nice")

    def test_hours(self):
        self.assertTrue(qr.in_hours("22-7", NIGHT))
        self.assertTrue(qr.in_hours("22-7", EARLY))
        self.assertFalse(qr.in_hours("22-7", DAY))
        self.assertTrue(qr.in_hours("13-15", DAY))
        for off in ("", "off", "none", "x-y", "5-5", None):
            self.assertFalse(qr.in_hours(off, NIGHT), off)


class Holds(unittest.TestCase):
    def test_none_by_day(self):
        self.assertEqual(qr.holds(ctx()), [])
        self.assertEqual(qr.holds(ctx(proactive_switch=None)), [])      # unknown switch = on

    def test_each_hold(self):
        cases = {"off": dict(proactive_switch=False), "estop": dict(emergency=True), "away": dict(away=True),
                 "quiet": dict(quiet_until=DAY.timestamp() + 60), "guests": dict(guests=True),
                 "call": dict(call=True)}
        for code, kw in cases.items():
            self.assertEqual(qr.holds(ctx(**kw)), [code], code)
        self.assertEqual(qr.holds(ctx(quiet_switch=True)), ["quiet"])
        self.assertEqual(qr.holds(ctx(NIGHT)), ["quiet_hours"])

    def test_expired_quiet_is_no_hold(self):
        self.assertEqual(qr.holds(ctx(quiet_until=DAY.timestamp() - 1)), [])

    def test_morning_ends_the_night(self):
        self.assertEqual(qr.holds(ctx(EARLY)), ["quiet_hours"])
        self.assertEqual(qr.holds(ctx(EARLY, night_ended=True)), [])


class Decide(unittest.TestCase):
    """Every hold against every level and mode: the full table."""
    HOLDS = {None: {}, "off": dict(proactive_switch=False), "estop": dict(emergency=True), "away": dict(away=True),
             "quiet": dict(quiet_until=DAY.timestamp() + 3600), "guests": dict(guests=True),
             "call": dict(call=True), "quiet_hours": {}}

    def expected(self, level, hold, mode):
        if level == "muted":
            return "show"
        if mode == "requested":
            return "speak"
        if level == "critical":
            return "speak"
        if hold:
            return "defer" if level == "important" else "show"
        if mode == "instant" and level == "nice":
            return "show"
        return "speak"

    def test_table(self):
        for level, hold, mode in itertools.product(qr.LEVELS, self.HOLDS, ("instant", "briefing", "requested")):
            now = NIGHT if hold == "quiet_hours" else DAY
            d = qr.decide(level, ctx(now, **self.HOLDS[hold]), mode)
            self.assertEqual(d.action, self.expected(level, hold, mode), (level, hold, mode, d))

    def test_retry_only_after_holds_that_end_during_the_day(self):
        for hold in ("away", "quiet", "guests", "call"):
            self.assertTrue(qr.decide("important", ctx(**self.HOLDS[hold])).retry, hold)
        for hold in ("off", "estop"):
            self.assertFalse(qr.decide("important", ctx(**self.HOLDS[hold])).retry, hold)
        self.assertFalse(qr.decide("important", ctx(NIGHT)).retry)            # quiet hours: wait for the morning
        self.assertFalse(qr.decide("important", ctx(NIGHT, call=True)).retry)  # a call at night: still the night

    def test_reason_is_the_first_hold(self):
        d = qr.decide("important", ctx(NIGHT, guests=True))
        self.assertEqual((d.action, d.reason), ("defer", "guests"))
        self.assertEqual(qr.decide("critical", ctx(NIGHT)).reason, "critical")

    def test_critical_can_be_held_if_configured(self):
        self.assertEqual(qr.decide("critical", ctx(NIGHT), critical_always=False).action, "show")
        self.assertEqual(qr.decide("critical", ctx(), critical_always=False).action, "speak")

    def test_briefing_allowed(self):
        self.assertTrue(qr.briefing_allowed(ctx()))
        self.assertFalse(qr.briefing_allowed(ctx(guests=True)))
        self.assertFalse(qr.briefing_allowed(ctx(NIGHT)))
        self.assertTrue(qr.briefing_allowed(ctx(EARLY, night_ended=True)))


class QuietCommands(unittest.TestCase):
    def test_durations(self):
        cases = {"quiet for an hour": 60, "jarvis quiet for 2 hours": 120, "quiet for two hours": 120,
                 "quiet for half an hour": 30, "be still for 20 minutes": 20,
                 "no announcements for an hour and a half": 90, "please do not disturb for three hours": 180,
                 "quiet for a quarter of an hour": 15, "don't disturb for 45 minutes": 45}
        for text, minutes in cases.items():
            self.assertEqual(qr.parse_quiet(text, DAY), ("set", DAY + dt.timedelta(minutes=minutes)), text)

    def test_today(self):
        tomorrow5 = dt.datetime(2026, 11, 6, 5, 0)
        for text in ("no announcements today", "no more announcements today", "no more announcements for today",
                     "for today no notices", "quiet until tomorrow"):
            self.assertEqual(qr.parse_quiet(text, DAY), ("set", tomorrow5), text)
        self.assertEqual(qr.parse_quiet("no announcements today", dt.datetime(2026, 11, 6, 1, 0)),
                         ("set", tomorrow5))                       # after midnight: this morning

    def test_end(self):
        for text in ("end the quiet", "cancel the quiet", "announcements back on", "you may speak again"):
            self.assertEqual(qr.parse_quiet(text, DAY), ("end", None), text)

    def test_not_a_quiet_command(self):
        for text in ("quiet", "music pause", "how quiet is it in the living room", "quiet forever",
                     "end the pause", "turn the light off for an hour", "quiet for 30 hours"):
            self.assertIsNone(qr.parse_quiet(text, DAY), text)


class Config(unittest.TestCase):
    def test_defaults_off_and_levels(self):
        cfg = pc.make()
        self.assertFalse(cfg.enabled)
        self.assertEqual(pc.level(cfg, "safety"), "critical")
        self.assertEqual(pc.level(cfg, "unknown"), "nice")
        self.assertFalse(pc.source_on(cfg, "noah"))
        self.assertTrue(pc.source_on(pc.make({"sources": {"noah": {"enabled": True}}}), "noah"))

    def test_file_and_env_flag(self):
        path = TMP / "proactive_test.yaml"
        path.write_text("enabled: true\nsources:\n  weather: {enabled: false}\n", encoding="utf-8")
        self.assertTrue(pc.load(path).enabled)
        self.assertFalse(pc.source_on(pc.load(path), "weather"))
        self.assertTrue(pc.source_on(pc.load(path), "calendar"))     # untouched defaults stay
        os.environ["JARVIS_PROACTIVE_MODE"] = "0"
        try:
            self.assertFalse(pc.load(path).enabled)
        finally:
            del os.environ["JARVIS_PROACTIVE_MODE"]

    def test_broken_file_gives_defaults(self):
        path = TMP / "broken.yaml"
        path.write_text("enabled: [unclosed\n", encoding="utf-8")
        self.assertFalse(pc.load(path).enabled)

    def test_example_is_valid_and_complete(self):
        example = pc.ROOT / "config" / "proactive.example.yaml"
        import yaml
        data = yaml.safe_load(example.read_text(encoding="utf-8"))
        self.assertEqual(set(data) - {"enabled"}, set(pc.DEFAULTS) - {"enabled"})
        for name in pc.DEFAULTS["sources"]:
            self.assertIn(name, data["sources"], name)
        for kind, lv in data["priorities"].items():
            self.assertIn(lv, pc.LEVELS, kind)


if __name__ == "__main__":
    unittest.main()
