"""
Context engine: time limits, cache, stale and missing sources, the local day
overview, work privacy. All sources are faked.

    .venv\\Scripts\\python.exe -m unittest tests.test_context -v
"""
import datetime as dt
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import commands    # noqa: E402
import context     # noqa: E402
import router      # noqa: E402

NOW = dt.datetime(2026, 10, 23, 8, 0)


class Engine(unittest.TestCase):
    def setUp(self):
        context._CACHE.clear()
        self._sources = dict(context.SOURCES)

    def tearDown(self):
        context.SOURCES.clear()
        context.SOURCES.update(self._sources)
        context._CACHE.clear()

    def test_timeout_does_not_block(self):
        context.SOURCES["slow"] = (lambda: time.sleep(3) or "late", 60, "service")
        t = time.time()
        item = context.get(["slow"], timeout=0.3)["slow"]
        self.assertLess(time.time() - t, 1.5)
        self.assertFalse(item["ok"])
        self.assertEqual(item["error"], "time limit")

    def test_stale_value_when_source_fails(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("service gone")
            return {"text": "ok"}
        context.SOURCES["flaky"] = (flaky, 0, "service")       # ttl 0: asked every time
        self.assertFalse(context.get(["flaky"])["flaky"]["stale"])
        item = context.get(["flaky"])["flaky"]
        self.assertTrue(item["ok"] and item["stale"])
        self.assertIn("stale", context.describe(["flaky"]))

    def test_cache_ttl(self):
        calls = {"n": 0}
        context.SOURCES["cached"] = (lambda: calls.__setitem__("n", calls["n"] + 1) or {"text": "x"}, 60, "local")
        context.get(["cached"])
        context.get(["cached"])
        self.assertEqual(calls["n"], 1)

    def test_missing_integration(self):
        context.SOURCES["calendar"] = (lambda: (_ for _ in ()).throw(LookupError("calendar not set up")), 300, "third-party")
        self.assertIn("not available (calendar not set up)", context.describe(["calendar"]))


class Overview(unittest.TestCase):
    def ctx(self, **over):
        base = {
            "time": dict(ok=True, stale=False, data={"now": NOW, "text": "Friday"}),
            "weather": dict(ok=True, stale=False, data={"sky": "sunny", "tmin": 6.2, "tmax": 15.8, "rain": 10}),
            "calendar": dict(ok=True, stale=False, data={"events": [
                {"title": "Dentist", "start": NOW.replace(hour=10), "allday": False},
                {"title": "Golf", "start": NOW.replace(hour=15), "allday": False}], "work": 2}),
            "timers": dict(ok=True, stale=False, data=[{"at": time.mktime(NOW.replace(hour=18).timetuple()),
                                                      "kind": "reminder", "text": "Laundry"}]),
            "plans": dict(ok=True, stale=False, data=[("Move", "Get boxes", True)]),
            "warnings": dict(ok=True, stale=False, data=[{"severity": "warn", "text": "pi disturbed for 12 min."}]),
        }
        base.update(over)
        return base

    def test_everything_together(self):
        out = context.day_overview(self.ctx())
        self.assertIn("sunny, 6 to 16 degrees", out)
        self.assertIn("10:00 Dentist; 15:00 Golf", out)
        self.assertIn("2 work appointment(s)", out)
        self.assertIn("18:00 Laundry", out)
        self.assertIn("Get boxes (overdue)", out)
        self.assertIn("pi disturbed", out)

    def test_marks_missing_and_stale(self):
        out = context.day_overview(self.ctx(calendar=dict(ok=False, stale=False, data=None),
                                            weather=dict(ok=True, stale=True, data={"sky": "rain", "tmin": 3,
                                                                                   "tmax": 9, "rain": 80})))
        self.assertIn("Calendar: not available", out)
        self.assertIn("(stale)", out)

    def test_router_answers_locally(self):
        context.SOURCES_BACKUP = dict(context.SOURCES)
        try:
            for n in ("calendar", "weather"):
                context.SOURCES[n] = (lambda: (_ for _ in ()).throw(LookupError("off")), 0, "service")
            r = router.route("Jarvis, what does my day look like?")
            self.assertEqual(r["kind"], "local")
            self.assertEqual(r["handler"], "context")
        finally:
            context.SOURCES.update(context.SOURCES_BACKUP)
            context._CACHE.clear()


class Privacy(unittest.TestCase):
    def test_work_appointments_only_counted(self):
        import icloud_calendar as cal
        old = (cal.configured, cal.events)
        cal.configured = lambda: True
        cal.events = lambda day, days=1: [{"title": "Meeting with the customer", "start": NOW},
                                         {"title": "Hairdresser", "start": NOW}]
        try:
            data = context._calendar()
        finally:
            cal.configured, cal.events = old
        self.assertEqual([e["title"] for e in data["events"]], ["Hairdresser"])
        self.assertEqual(data["work"], 1)


class Situation(unittest.TestCase):
    def tearDown(self):
        commands.update_state(lambda d: d.pop("homelab", None))
        context._CACHE.clear()

    def test_block_only_when_something_is_wrong(self):
        commands.update_state(lambda d: d.pop("homelab", None))
        self.assertEqual(context.block(), "")
        commands.update_state(lambda d: d.__setitem__("homelab", {"nas": {"bad": 3, "since": time.time() - 600}}))
        self.assertIn("nas disturbed for 10 min.", context.block())
        self.assertIn("nas disturbed", commands.lean_prefix("How is the NAS doing?", ["systems"]))


if __name__ == "__main__":
    unittest.main()
