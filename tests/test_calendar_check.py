"""
Calendar read before every proposal, and mail never further back than 24 hours —
calendar, mailbox and HUD faked; no iCloud, no IMAP.

    .venv\\Scripts\\python.exe -m unittest tests.test_calendar_check -v
"""
import isolate  # noqa: F401  (must come first)
import datetime as dt
import imaplib
import time
import unittest

import commands
import icloud_calendar as cal
import jarvis_mcp
import mail_events
import mailboxes
import shipments

DAY = dt.date(2026, 10, 27)
DENTIST = {"start": dt.datetime(2026, 10, 27, 10, 0), "end": dt.datetime(2026, 10, 27, 11, 0),
           "title": "Zahnarzt Dr. Krause", "calendar": "Privat"}
HAIRCUT = {"start": dt.datetime(2026, 10, 27, 16, 0), "end": dt.datetime(2026, 10, 27, 17, 0),
           "title": "Friseur", "calendar": "Privat"}
HOLIDAY = {"start": dt.datetime(2026, 10, 27), "end": dt.datetime(2026, 10, 28), "title": "Urlaub",
           "allday": True, "calendar": "Privat"}


class Similar(unittest.TestCase):
    def test_same_appointment(self):
        self.assertTrue(cal.similar("Zahnarzt", "Zahnarzt Dr. Krause"))
        self.assertTrue(cal.similar("Golfspiel: Ost Tee 10", "golfspiel ost tee 10"))
        self.assertTrue(cal.similar("Training bei Max", "Golftraining Max"))

    def test_different_appointment(self):
        self.assertFalse(cal.similar("Zahnarzt", "Friseur"))
        self.assertFalse(cal.similar("Termin bei Anna", "Termin bei Ben"))
        self.assertFalse(cal.similar("", "Friseur"))


class Check(unittest.TestCase):
    def test_already_there_same_day_similar_title(self):
        found = cal.check("Zahnarzt", "2026-10-27 14:00", existing=[DENTIST, HAIRCUT])
        self.assertEqual(found["same"], [DENTIST])
        self.assertIn("Steht schon im Kalender", cal.clash_text(found))

    def test_other_day_is_not_the_same(self):
        found = cal.check("Zahnarzt", "2026-10-28 10:00", existing=[DENTIST])
        self.assertEqual(found, {"same": [], "overlap": []})
        self.assertEqual(cal.clash_text(found), "")

    def test_overlap_is_named(self):
        found = cal.check("Physiotherapie", "2026-10-27 10:30", "2026-10-27 11:30", existing=[DENTIST, HAIRCUT, HOLIDAY])
        self.assertEqual((found["same"], found["overlap"]), ([], [DENTIST]))
        self.assertIn("Überschneidet sich mit: 27.10. 10:00–11:00 Zahnarzt Dr. Krause", cal.clash_text(found))

    def test_back_to_back_and_all_day_do_not_overlap(self):
        self.assertEqual(cal.check("Physio", "2026-10-27 11:00", existing=[DENTIST, HOLIDAY])["overlap"], [])
        self.assertEqual(cal.check("Paket", "2026-10-27", existing=[DENTIST])["overlap"], [])

    def test_reads_the_calendar_itself(self):
        asked = []
        real = cal.events
        try:
            cal.events = lambda start, days: (asked.append((start, days)), [DENTIST])[1]
            self.assertEqual(cal.check("Zahnarzt", dt.datetime(2026, 10, 27, 9, 0))["same"], [DENTIST])
        finally:
            cal.events = real
        self.assertEqual(asked, [(DAY, 1)])


def ics(start, end, summary, uid):
    return ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\n"
            f"UID:{uid}\r\nSUMMARY:{summary}\r\nDTSTART;TZID=Europe/Berlin:{start}\r\n"
            f"DTEND;TZID=Europe/Berlin:{end}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


def mail(summary, start, end, uid):
    return {"from": "Praxis", "addr": "info@praxis.example", "subject": f"Ihr Termin: {summary}",
            "date": "24.10.2026", "text": summary, "ics": [ics(start, end, summary, uid)]}


class MailEventsCheck(unittest.TestCase):
    NOW = dt.datetime(2026, 10, 24, 12, 0)

    def test_already_entered_by_hand_is_not_offered(self):
        # the user entered it themselves at another hour: same day and title is enough
        m = mail("Zahnarzt", "20261027T140000", "20261027T150000", "z-1")
        self.assertEqual(mail_events.candidates([m], [DENTIST], now=self.NOW), [])

    def test_overlap_goes_with_the_offer(self):
        m = mail("Physiotherapie", "20261027T103000", "20261027T113000", "p-1")
        (c,) = mail_events.candidates([m], [DENTIST, HAIRCUT], now=self.NOW)
        self.assertIn("Zahnarzt Dr. Krause", c["clash"])
        self.assertIn("Überschneidet sich mit", mail_events.describe([c]))

    def test_proposal_names_the_overlap(self):
        import events
        import proposals
        m = mail("Physiotherapie", "20261027T103000", "20261027T113000", "p-2")
        real = (cal.configured, cal.events, events.publish)
        said = []
        try:
            cal.configured = lambda: True
            cal.events = lambda start, days: [DENTIST]
            events.publish = lambda kind, text, **k: said.append(text)
            mail_events.check(now=self.NOW, mails=[m])
        finally:
            cal.configured, cal.events, events.publish = real
        (p,) = [p for p in proposals.pending() if p["source"] == "mail_events"]
        self.addCleanup(proposals.resolve, p["id"], "declined")   # other tests expect no open proposal
        self.assertIn("Überschneidet sich mit", p["why"])
        self.assertIn("Überschneidet sich mit", said[-1])
        self.assertIn("Überschneidet sich mit", proposals.describe(p))


class ShipmentsCheck(unittest.TestCase):
    def test_own_entry_that_day_means_no_second_one(self):
        added = []
        real = (cal.events, cal.add_event)
        d = {"items": {"a1": {"key": "a1", "shop": "Amazon", "carrier": "DHL", "status": "unterwegs",
                              "eta": DAY.isoformat()}}}
        try:
            cal.events = lambda start, days: [{"start": dt.datetime(2026, 10, 27), "title": "Paket Amazon",
                                               "allday": True, "calendar": "Privat"}]
            cal.add_event = lambda *a, **k: (added.append(a), ("ok", "https://cal/x@jarvis-hud.ics"))[1]
            shipments.sync(d, now=dt.datetime(2026, 10, 24, 12, 0))
            shipments.sync(d, now=dt.datetime(2026, 10, 24, 12, 30))
        finally:
            cal.events, cal.add_event = real
        self.assertEqual(added, [])
        self.assertEqual(d["items"]["a1"]["cal_there"], DAY.isoformat())

    def test_free_day_gets_its_entry(self):
        added = []
        real = (cal.events, cal.add_event)
        d = {"items": {"b1": {"key": "b1", "shop": "Zalando", "carrier": "Hermes", "status": "unterwegs",
                              "eta": DAY.isoformat()}}}
        try:
            cal.events = lambda start, days: [DENTIST]
            cal.add_event = lambda *a, **k: (added.append(a), ("ok", "https://cal/y@jarvis-hud.ics"))[1]
            shipments.sync(d, now=dt.datetime(2026, 10, 24, 12, 0))
        finally:
            cal.events, cal.add_event = real
        self.assertEqual(len(added), 1)


class ModelPath(unittest.TestCase):
    def setUp(self):
        self.real = (cal.configured, cal.events, cal.add_event, jarvis_mcp._ask, jarvis_mcp._calendar_past)
        self.cards = []
        cal.configured = lambda: True
        cal.events = lambda start, days: [DENTIST]
        jarvis_mcp._ask = lambda tool, inp: (self.cards.append(inp), {"behavior": "allow", "updatedInput": inp})[1]
        # the fixed day DAY lies in the past one day; these tests are about the calendar, not the date
        jarvis_mcp._calendar_past = lambda args: False

    def tearDown(self):
        (cal.configured, cal.events, cal.add_event, jarvis_mcp._ask, jarvis_mcp._calendar_past) = self.real

    def test_past_is_refused_before_any_card(self):
        jarvis_mcp._calendar_past = self.real[4]
        yesterday = f"{dt.date.today() - dt.timedelta(days=1):%Y-%m-%d} 10:00"
        verdict = jarvis_mcp._approve("mcp__jarvis__calendar_add", {"title": "Golf", "start": yesterday})
        self.assertEqual(verdict["behavior"], "deny")
        self.assertIn("Vergangenheit", verdict["message"])
        self.assertEqual(self.cards, [])
        cal.add_event = lambda *a, **k: self.fail("a past event is never entered")
        self.assertIn("Vergangenheit", jarvis_mcp._calendar("calendar_add", {"title": "Golf", "start": yesterday}))

    def test_what_counts_as_past(self):
        past, today = self.real[4], dt.date.today()
        self.assertTrue(past({"start": f"{today - dt.timedelta(days=1)}"}))
        self.assertFalse(past({"start": f"{today}"}), "an all-day event today is still on")
        self.assertTrue(past({"start": f"{today - dt.timedelta(days=1)}T09:00"}))
        self.assertFalse(past({"start": f"{today + dt.timedelta(days=1)} 09:00"}))
        self.assertFalse(past({"start": "nächsten Dienstag"}), "an odd date is the calendar's to refuse")

    def test_already_there_is_refused_before_any_card(self):
        verdict = jarvis_mcp._approve("mcp__jarvis__calendar_add", {"title": "Zahnarzt", "start": "2026-10-27 10:00"})
        self.assertEqual(verdict["behavior"], "deny")
        self.assertIn("Steht schon im Kalender", verdict["message"])
        self.assertEqual(self.cards, [])

    def test_overlap_is_on_the_card_not_in_the_entry(self):
        args = {"title": "Physio", "start": "2026-10-27 10:30"}
        verdict = jarvis_mcp._approve("mcp__jarvis__calendar_add", args)
        self.assertIn("Überschneidet sich mit", self.cards[0]["_hinweis"])
        self.assertEqual(verdict["updatedInput"], args)

    def test_free_slot_is_a_plain_card(self):
        jarvis_mcp._approve("mcp__jarvis__calendar_add", {"title": "Physio", "start": "2026-10-27 13:00"})
        self.assertNotIn("_hinweis", self.cards[0])

    def test_add_itself_checks_again(self):
        cal.add_event = lambda *a, **k: self.fail("must not be entered twice")
        text = jarvis_mcp._calendar("calendar_add", {"title": "Zahnarzt", "start": "2026-10-27 10:00"})
        self.assertIn("Nicht eingetragen", text)

    def test_prompt_rules_say_it_too(self):
        rule = commands.LEAN_GROUP_RULES["calendar"]
        self.assertIn("Vor jedem Terminvorschlag erst calendar_events", rule)
        self.assertIn("24 Stunden", rule)


class FakeBox:
    """IMAP with three mails: 2 h, 20 h and 30 h old."""
    def __init__(self):
        now = time.time()
        self.dates = {b"1": now - 30 * 3600, b"2": now - 20 * 3600, b"3": now - 2 * 3600}
        self.searched = []

    def search(self, charset, *criteria):
        self.searched.append(criteria)
        since = time.mktime(time.strptime(criteria[-1], "%d-%b-%Y"))
        return "OK", [b" ".join(i for i, t in self.dates.items() if t >= since)]

    def fetch(self, ids, what):
        assert what == "(INTERNALDATE)"
        return "OK", [i + b" (INTERNALDATE " + imaplib.Time2Internaldate(self.dates[i]).encode() + b")"
                      for i in ids.split(b",")]


class MailLookback(unittest.TestCase):
    def test_default_is_24_hours(self):
        self.assertEqual(mailboxes.LOOKBACK_H, 24)
        self.assertEqual(mailboxes.recent(FakeBox()), [b"2", b"3"])

    def test_never_further_back_and_less_on_request(self):
        self.assertEqual(mailboxes.recent(FakeBox(), hours=24 * 14), [b"2", b"3"])
        self.assertEqual(mailboxes.recent(FakeBox(), hours=6), [b"3"])

    def test_criteria_and_the_date_go_to_the_server(self):
        box = FakeBox()
        mailboxes.recent(box, criteria=("UNSEEN",))
        self.assertEqual(box.searched[0][:2], ("UNSEEN", "SINCE"))

    def test_every_reader_uses_it(self):
        import inspect
        import parcels
        for fn in (mail_events._mails, shipments._mails, parcels.shipments, jarvis_mcp._mail):
            src = inspect.getsource(fn)
            self.assertIn("mailboxes.recent(", src, fn.__qualname__)
            self.assertNotIn('"SINCE"', src, fn.__qualname__)


if __name__ == "__main__":
    unittest.main()
