"""
Weekly review: logging, Garmin fetch (faked), patterns and the weekly note.

    .venv\\Scripts\\python.exe -m unittest tests.test_weekly_review -v
"""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import background      # noqa: E402
import events          # noqa: E402
import memory          # noqa: E402
import weekly_review as wr   # noqa: E402

END = dt.date(2026, 10, 25)          # a Sunday


class FakeGarmin:
    def __init__(self):
        self.calls = []

    def get_sleep_data(self, day):
        self.calls.append(day)
        return {"dailySleepDTO": {"sleepTimeSeconds": 7 * 3600, "deepSleepSeconds": 5400, "remSleepSeconds": 6000,
                                  "awakeSleepSeconds": 600, "sleepScores": {"overall": {"value": 80}},
                                  "sleepStartTimestampLocal": 1761346800000}}

    def get_stats(self, day):
        return {"totalSteps": 9000, "restingHeartRate": 52, "averageStressLevel": 30,
                "bodyBatteryLowestValue": 20, "bodyBatteryHighestValue": 85}

    def get_activities_by_date(self, start, end):
        return [{"activityType": {"typeKey": "golf"}, "duration": 14400}] if start.endswith("-24") else []


def synthetic(days=35, late_every=3):
    """Late dinner every third day → the next night scores 15 points lower."""
    d = {}
    for i in range(days):
        day = END - dt.timedelta(days=days - 1 - i)
        rec = {"steps": 8000 + (i % 5) * 1000, "stress": 30, "bb_high": 80, "sleep_s": 7 * 3600,
               "deep_s": 5400, "sleep_score": 80, "golf": {"rounds": 0, "minutes": 0},
               "log": [{"kind": "meal", "what": "Dinner", "at": "22:15" if i % late_every == 0 else "19:00"}]}
        d[day.isoformat()] = rec
    for k in list(d):
        prev = (dt.date.fromisoformat(k) - dt.timedelta(days=1)).isoformat()
        if prev in d and d[prev]["log"][0]["at"] >= "21:00":
            d[k].update(sleep_score=65, deep_s=3600)
    return d


class Review(unittest.TestCase):
    def setUp(self):
        if wr.DATA.exists():
            wr.DATA.unlink()
        self._ask = background.ask

    def tearDown(self):
        background.ask = self._ask

    def test_log_entry(self):
        now = dt.datetime(2026, 10, 22, 23, 0)
        self.assertIn("Noted", wr.log_entry("Dinner", "22:10", now=now))
        wr.log_entry("two beers", "00:30", now=dt.datetime(2026, 10, 23, 1, 0))
        d = wr.load()
        self.assertEqual(d["2026-10-22"]["log"][0], {"kind": "meal", "what": "Dinner", "at": "22:10"})
        self.assertEqual(d["2026-10-22"]["log"][1]["kind"], "alcohol")
        self.assertEqual(d["2026-10-22"]["log"][1]["at"], "24:30")        # counts for the evening before
        wr.log_entry("Coffee", "yesterday 15:00", now=now)
        self.assertEqual(wr.load()["2026-10-21"]["log"][0]["kind"], "caffeine")

    def test_fetch_backfills_once(self):
        g = FakeGarmin()
        self.assertEqual(wr.fetch(END + dt.timedelta(days=1), g=g), wr.BACKFILL_DAYS)
        rec = wr.load()["2026-10-24"]
        self.assertEqual(rec["sleep_score"], 80)
        self.assertEqual(rec["golf"], {"rounds": 1, "minutes": 240})
        self.assertEqual(wr.fetch(END + dt.timedelta(days=1), g=g), 0)    # nothing missing any more

    def test_late_dinner_pattern(self):
        found = wr.patterns(synthetic(), END)
        late = [p for p in found if "late meal" in p and "sleep score" in p]
        self.assertEqual(len(late), 1, found)
        self.assertIn("65 instead of 80", late[0])
        self.assertFalse(any("Golf" in p for p in found))                # never played: no pattern

    def test_rooms_line_from_home_assistant(self):
        states = {"sensor.time_living_room_7_days": {"state": "23.6"}, "sensor.time_bedroom_7_days": {"state": "61.2"}}
        self.assertEqual(wr.rooms_line(states), "Time at home by room (presence sensors, last 7 days): Living Room 24 h, Bedroom 61 h.")
        self.assertEqual(wr.rooms_line({"sensor.time_living_room_7_days": {"state": "unavailable"}}), "")
        self.assertEqual(wr.rooms_line(), "")                       # no Home Assistant in tests
        _, _, text = wr.facts(synthetic(), END, states)
        self.assertIn("Living Room 24 h", text)

    def test_too_little_data_says_so(self):
        d = {k: v for k, v in synthetic().items() if k >= "2026-10-21"}
        _, found, text = wr.facts(d, END)
        self.assertEqual(found, [])
        self.assertIn("not enough data yet", text)

    def test_review_note_and_message(self):
        wr._save(synthetic())
        background.ask = lambda job, system, prompt: "Your week, Sir: you slept decently."
        spoken = wr.review(END + dt.timedelta(days=1))
        self.assertEqual(spoken, "Your week, Sir: you slept decently.")
        note = memory.MEMORY_DIR / "Health" / "Weekly review 2026-W43.md"
        text = note.read_text(encoding="utf-8")
        self.assertIn("sleep score", text)
        self.assertIn("expires:", text)
        self.assertEqual(events._LOG[-1]["text"], spoken)

    def test_review_without_model_uses_facts(self):
        wr._save(synthetic())
        background.ask = lambda *a: None
        spoken = wr.review(END + dt.timedelta(days=1), speak=False)
        self.assertIn("Sleep on average", spoken)

    def test_no_data_no_review(self):
        self.assertIsNone(wr.review(END + dt.timedelta(days=1), speak=False))


if __name__ == "__main__":
    unittest.main()
