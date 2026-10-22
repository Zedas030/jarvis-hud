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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import assistant as a    # noqa: E402
import background        # noqa: E402
import events            # noqa: E402
import fallback_llm      # noqa: E402
import jarvis_mcp        # noqa: E402
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
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Drafts) "/" "Entw&APw-rfe"',
                      b'(\\HasNoChildren \\Sent) "/" "Gesendet"']

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
        args = dict(to="anna@example.org", subject="Samstag", body="Hallo Anna,\nbleibt es bei Samstag?")
        args.update(kw)
        with Patch(a.imaplib, IMAP4_SSL=FakeIMAP), \
             Patch(jarvis_mcp, MAIL_HOST="imap.example.org", MAIL_USER="me@example.org", MAIL_PASSWORD="x"):
            return a.mail_draft(**args)

    def test_draft_goes_to_drafts_only(self):
        out = self.run_draft()
        self.assertIn("Gesendet habe ich nichts", out)
        folder, flags, data = FakeIMAP.instances[0].appended[0]
        self.assertEqual(folder, '"Entw&APw-rfe"')
        self.assertIn("\\Draft", flags)
        self.assertIn(b"Subject: Samstag", data)
        self.assertIn(b"To: anna@example.org", data)

    def test_bad_address(self):
        self.assertIn("keine gültige Mail-Adresse", self.run_draft(to="anna"))
        self.assertEqual(FakeIMAP.instances, [])

    def test_needs_card(self):
        self.assertEqual(policy.action_class("mail_draft"), policy.CONFIRM)
        self.assertNotIn("mail_draft", runtime.NO_CONFIRM)


class DayPrep(unittest.TestCase):
    def test_private_detailed_work_counted(self):
        (memory.MEMORY_DIR / "Personen").mkdir(parents=True, exist_ok=True)
        (memory.MEMORY_DIR / "Personen" / "Zahnarzt Müller.md").write_text(
            "---\ntype: person\n---\n\n# Zahnarzt Müller\n\n- Praxis in Bonn\n", encoding="utf-8")
        day = dt.date(2026, 10, 23)
        evts = [{"title": "Zahnarzt Müller", "start": dt.datetime(2026, 10, 23, 10), "allday": False,
                 "location": "Bonn"},
                {"title": "Meeting mit Kunde Contoso", "start": dt.datetime(2026, 10, 23, 14), "allday": False}]
        import commute
        with Patch(commute, geocode=lambda p: (50.7, 7.1), drive_minutes=lambda d: 25):
            out = a.day_prep(day, events=evts)
        self.assertIn("1 private Termin(e), dazu 1 Arbeitstermin(e)", out)
        self.assertIn("10:00 Zahnarzt Müller in Bonn — 25 Min. Fahrt, los um 09:30", out)
        self.assertIn("Notizen: Zahnarzt Müller", out)
        self.assertNotIn("Contoso", out)

    def test_untrusted(self):
        self.assertIn("day_prep", policy.UNTRUSTED)


class Research(unittest.TestCase):
    def test_save(self):
        out = a.research_save("Golfschläger für Anfänger", "Ein Halbsatz reicht für den Anfang (Annahme: Budget 500 €).",
                              ["Golf Post – https://example.org/a (10.10.2026)"])
        self.assertIn("Recherchen/Golfschläger für Anfänger", out)
        note = next((memory.MEMORY_DIR / "Recherchen").glob("Golfschläger*.md")).read_text(encoding="utf-8")
        self.assertIn("## Quellen", note)
        self.assertIn("ablauf:", note)
        self.assertIn("Arbeitsthemen", a.research_save("Angebot für Kunde", "x"))


class Watch(unittest.TestCase):
    def setUp(self):
        if a.WATCH.exists():
            a.WATCH.unlink()
        self._ask = background.ask

    def tearDown(self):
        background.ask = self._ask

    def test_add_list_remove(self):
        self.assertIn("im Auge", a.watch_add("RTX 5080 Treiber", 3))
        self.assertIn("schon", a.watch_add("rtx 5080 treiber"))
        self.assertIn("Arbeitsthemen", a.watch_add("Projektplan beim Kunden"))
        self.assertIn("RTX 5080 Treiber (alle 3 Tage)", a.watch_list())
        self.assertIn("Nicht mehr beobachtet", a.watch_remove("rtx"))
        for i in range(a.WATCH_MAX):
            a.watch_add(f"Thema {i}")
        self.assertIn("schon", a.watch_add("eins zu viel"))

    def test_step_announces_only_news(self):
        a.watch_add("Golfplatz Öffnungszeiten")
        a.watch_add("Pi-hole Version")
        a.watch_add("Hyper Backup")
        answers = iter([{"neu": True, "zusammenfassung": "Ab November schließt der Platz um 16 Uhr.",
                         "quellen": ["Club – https://example.org (20.10.2026)"]},
                        {"neu": False, "zusammenfassung": "Nichts Neues."}])
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
            self.assertIn("schließt der Platz", events._LOG[-1]["text"])
            self.assertEqual(a.watch_step(now=time.time()), [])       # nothing more today
        self.assertTrue(list((memory.MEMORY_DIR / "Recherchen").glob("Beobachtung Golfplatz*.md")))

    def test_no_claude_no_check(self):
        a.watch_add("Irgendwas")
        with Patch(runtime, runtime_kind=lambda: "mock"):
            self.assertEqual(a.watch_step(), [])


class Plans(unittest.TestCase):
    def test_create_list_check(self):
        reminders = []
        steps = [{"text": "Kartons besorgen", "date": "2099-11-01"}, {"text": "Küche packen", "date": "2099-11-03"},
                 {"text": "kaputt", "date": "irgendwann"}]
        out = a.plan_create("Umzug", steps, internal=lambda op, args: reminders.append(args) or "Erinnerung gesetzt.")
        self.assertIn("2 Schritten", out)
        self.assertIn("2 Erinnerungen", out)
        self.assertEqual(reminders[0], {"kind": "reminder", "at": "2099-11-01 09:00", "text": "Umzug: Kartons besorgen"})
        self.assertIn("Umzug: 01.11. Kartons besorgen; 03.11. Küche packen", a.plan_list())
        self.assertIn("Noch 1 Schritt", a.plan_check("Umzug", "kartons"))
        self.assertIn("komplett erledigt", a.plan_check("Umzug", "küche"))
        self.assertEqual(a.plan_list(), "Keine offenen Pläne.")

    def test_no_work_plans(self):
        self.assertIn("Arbeitsvorhaben", a.plan_create("Präsentation für den Chef", [{"text": "x", "date": "2099-01-01"}]))


if __name__ == "__main__":
    unittest.main()
