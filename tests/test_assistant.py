"""
Organisation and research: mail drafts (fake IMAP), day preparation, research
notes, watched topics (fake model), multi-day plans. Nothing is sent anywhere.

    .venv\\Scripts\\python.exe -m unittest tests.test_assistant -v
"""
import datetime as dt
import json
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import assistant as a    # noqa: E402
import background        # noqa: E402
import events            # noqa: E402
import fallback_llm      # noqa: E402
import memory            # noqa: E402
import policy            # noqa: E402
import runtime           # noqa: E402

os.environ["JARVIS_WATCHLIST"] = str(TMP / "watch.json")
a.WATCH = TMP / "watch.json"


class FakeIMAP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.appended = []
        FakeIMAP.instances.append(self)

    def login(self, user, pw):
        return "OK", []

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Drafts) "/" "Drafts"',
                      b'(\\HasNoChildren \\Sent) "/" "Sent"']

    def append(self, folder, flags, when, data):
        self.appended.append((folder, flags, data))
        return "OK", []

    def logout(self):
        pass


class Patch:
    def __init__(self, obj, **kw):
        self.obj, self.kw, self.old = obj, kw, {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = getattr(self.obj, k)
            setattr(self.obj, k, v)

    def __exit__(self, *x):
        for k, v in self.old.items():
            setattr(self.obj, k, v)


class Draft(unittest.TestCase):
    def setUp(self):
        FakeIMAP.instances.clear()

    def run_draft(self, **kw):
        args = dict(to="anna@example.org", subject="Saturday", body="Hello Anna,\nare we still on for Saturday?")
        args.update(kw)
        # mailboxes.py reads the accounts from the environment; one fake account here
        fake = {"MAIL_IMAP_HOST": "imap.example.org", "MAIL_USER": "me@example.org", "MAIL_PASSWORD": "x"}
        with Patch(a.imaplib, IMAP4_SSL=FakeIMAP), mock.patch.dict(os.environ, fake):
            return a.mail_draft(**args)

    def test_draft_goes_to_drafts_only(self):
        out = self.run_draft()
        self.assertIn("I have not sent anything", out)
        folder, flags, data = FakeIMAP.instances[0].appended[0]
        self.assertEqual(folder, '"Drafts"')
        self.assertIn("\\Draft", flags)
        self.assertIn(b"Subject: Saturday", data)
        self.assertIn(b"To: anna@example.org", data)

    def test_bad_address(self):
        self.assertIn("is not a valid mail address", self.run_draft(to="anna"))
        self.assertEqual(FakeIMAP.instances, [])

    def test_needs_card(self):
        self.assertEqual(policy.action_class("mail_draft"), policy.CONFIRM)
        self.assertNotIn("mail_draft", runtime.NO_CONFIRM)


class DayPrep(unittest.TestCase):
    def test_private_detailed_work_counted(self):
        (memory.MEMORY_DIR / "People").mkdir(parents=True, exist_ok=True)
        (memory.MEMORY_DIR / "People" / "Dentist Miller.md").write_text(
            "---\ntype: person\n---\n\n# Dentist Miller\n\n- Practice in Bonn\n", encoding="utf-8")
        day = dt.date(2026, 10, 23)
        evts = [{"title": "Dentist Miller", "start": dt.datetime(2026, 10, 23, 10), "allday": False,
                 "location": "Bonn"},
                {"title": "Meeting with customer Contoso", "start": dt.datetime(2026, 10, 23, 14), "allday": False}]
        import commute
        with Patch(commute, geocode=lambda p: (50.7, 7.1), drive_minutes=lambda d: 25):
            out = a.day_prep(day, events=evts)
        self.assertIn("1 private appointment(s), plus 1 work appointment(s)", out)
        self.assertIn("10:00 Dentist Miller in Bonn — 25 min drive, leave at 09:30", out)
        self.assertIn("notes: Dentist Miller", out)
        self.assertNotIn("Contoso", out)

    def test_untrusted(self):
        self.assertIn("day_prep", policy.UNTRUSTED)


class Research(unittest.TestCase):
    def test_save(self):
        out = a.research_save("Golf clubs for beginners", "A half set is enough to start with (assumption: budget 500 €).",
                              ["Golf Post – https://example.org/a (10.10.2026)"])
        self.assertIn("Research/Golf clubs for beginners", out)
        note = next((memory.MEMORY_DIR / "Research").glob("Golf clubs*.md")).read_text(encoding="utf-8")
        self.assertIn("## Sources", note)
        self.assertIn("expires:", note)
        self.assertIn("work topics", a.research_save("Offer for the customer", "x"))


class Watch(unittest.TestCase):
    def setUp(self):
        if a.WATCH.exists():
            a.WATCH.unlink()
        self._ask = background.ask

    def tearDown(self):
        background.ask = self._ask

    def test_add_list_remove(self):
        self.assertIn("keep an eye on", a.watch_add("RTX 5080 driver", 3))
        self.assertIn("already", a.watch_add("rtx 5080 driver"))
        self.assertIn("work topics", a.watch_add("Project plan at the customer"))
        self.assertIn("RTX 5080 driver (every 3 days)", a.watch_list())
        self.assertIn("No longer watched", a.watch_remove("rtx"))
        for i in range(a.WATCH_MAX):
            a.watch_add(f"Topic {i}")
        self.assertIn("already", a.watch_add("one too many"))

    def test_step_announces_only_news(self):
        a.watch_add("Golf course opening hours")
        a.watch_add("Pi-hole Version")
        a.watch_add("Hyper Backup")
        answers = iter([{"new": True, "summary": "From November the course closes at 4 pm.",
                         "sources": ["Club – https://example.org (2026-10-20)"]},
                        {"new": False, "summary": "Nothing new."}])
        calls = []

        def ask(job, system, prompt, tools=""):
            calls.append(tools)
            return json.dumps(next(answers), ensure_ascii=False)
        background.ask = ask
        with Patch(runtime, runtime_kind=lambda: "claude"), Patch(fallback_llm, claude_blocked=lambda u: False):
            before = len(events._LOG)
            checked = a.watch_step(now=time.time())
            self.assertEqual(len(checked), a.WATCH_PER_DAY)          # capped per day
            self.assertEqual(calls, ["WebSearch"] * a.WATCH_PER_DAY)
            self.assertEqual(len(events._LOG) - before, 1)           # only the news is spoken
            self.assertIn("the course closes", events._LOG[-1]["text"])
            self.assertEqual(a.watch_step(now=time.time()), [])       # nothing more today
        self.assertTrue(list((memory.MEMORY_DIR / "Research").glob("Watch Golf course*.md")))

    def test_no_claude_no_check(self):
        a.watch_add("Anything")
        with Patch(runtime, runtime_kind=lambda: "mock"):
            self.assertEqual(a.watch_step(), [])


class Plans(unittest.TestCase):
    def test_create_list_check(self):
        reminders = []
        steps = [{"text": "Get boxes", "date": "2099-11-01"}, {"text": "Pack the kitchen", "date": "2099-11-03"},
                 {"text": "broken", "date": "some time"}]
        out = a.plan_create("Move", steps, internal=lambda op, args: reminders.append(args) or "Reminder set.")
        self.assertIn("2 steps", out)
        self.assertIn("2 reminders", out)
        self.assertEqual(reminders[0], {"kind": "reminder", "at": "2099-11-01 09:00", "text": "Move: Get boxes"})
        self.assertIn("Move: Nov 01 Get boxes; Nov 03 Pack the kitchen", a.plan_list())
        self.assertIn("1 step(s) still open", a.plan_check("Move", "boxes"))
        self.assertIn("completely done", a.plan_check("Move", "kitchen"))
        self.assertEqual(a.plan_list(), "No open plans.")

    def test_no_work_plans(self):
        self.assertIn("work projects", a.plan_create("Presentation for the boss", [{"text": "x", "date": "2099-01-01"}]))


if __name__ == "__main__":
    unittest.main()
