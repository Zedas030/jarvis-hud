"""
Bookings from mail -> calendar — mails and calendar faked; no IMAP, no iCloud.

    .venv\\Scripts\\python.exe -m unittest tests.test_mail_events -v
"""
import isolate  # noqa: F401  (must come first)
import datetime as dt
import unittest

import mail_events
import policy
import proposals
import router

NOW = dt.datetime(2026, 10, 23, 12, 0)


def ics(start, end, summary, uid, location="", description="", extra=""):
    return ("BEGIN:VCALENDAR\r\nMETHOD:PUBLISH\r\n" + extra + "BEGIN:VEVENT\r\n"
            f"UID:{uid}\r\nSUMMARY:{summary}\r\nDTSTART;TZID=Europe/Berlin:{start}\r\n"
            f"DTEND;TZID=Europe/Berlin:{end}\r\nLOCATION:{location}\r\nDESCRIPTION:{description}\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n")


def mail(subject, text="", attachments=(), sender="PC CADDIE", date="22.10.2026"):
    return {"from": sender, "subject": subject, "date": date, "text": text or subject, "ics": list(attachments)}


TEE = mail("Ihre Buchung | 25.10.2026 09:20 Uhr, Ost Tee 10",
           attachments=[ics("20261025T092000", "20261025T115000", "Golfspiel: Ost Tee 10", "PCCO-1",
                            "Golfclub Musterheide\\, Am Golfplatz 1", "Startzeit 09:20")])
FITTING = mail("Reservierungsbestätigung", sender="Golf House",
               text="Ihre Reservierungs-Nr.: 100.200 Buchung: 60-minütiges Fitting Datum: 27.10.2026 Zeit von: 16:30h",
               attachments=[ics("20261027T163000", "20261027T173000", "Golf House Berlin Kleinmachnow", "gh-1",
                                "Musterstraße 1 12345 Musterstadt")])
PAST = mail("Ihre Buchung | 19.10.2026 08:50 Uhr", attachments=[ics("20261019T085000", "20261019T110000",
                                                                    "Golfspiel: Tee 1", "PCCO-2")])
BOOKED_THEN_CANCELLED = mail("Ihre Buchung | 30.10.2026 08:30 Uhr, Ostplatz von Tee 1",
                             attachments=[ics("20261030T083000", "20261030T110000", "Golfspiel: Tee 1", "PCCO-3")])
CANCELLATION = mail("Ihre Stornierung | 30.10.2026 08:30 Uhr, Ostplatz von Tee 1",
                    text="Folgende Startzeiten wurden storniert: Nr. 2979603 Fr, 30.10.2026, 08:30 Uhr Ostplatz")


class MailEvents(unittest.TestCase):
    def found(self, mails, existing=()):
        return mail_events.candidates(mails, list(existing), now=NOW)

    def test_future_booking_with_details(self):
        (c,) = self.found([TEE])
        self.assertEqual((c["title"], c["start"]), ("Golfspiel: Ost Tee 10", dt.datetime(2026, 10, 25, 9, 20)))
        self.assertIn("Musterheide", c["location"])
        self.assertIn("Startzeit 09:20", c["notes"])
        self.assertIn("Aus der Mail", c["notes"])

    def test_shop_name_gets_the_kind_of_booking(self):
        (c,) = self.found([FITTING])
        self.assertEqual(c["title"], "Golf House Berlin Kleinmachnow · 60-minütiges Fitting")

    def test_time_in_the_title_is_dropped(self):
        m = mail("golftimer - Buchungsbestätigung/29.10.2026, 16:00", sender="golftimer",
                 attachments=[ics("20261029T160000", "20261029T165000", "Training bei Iulius Andreotti (16:00 - 16:50)", "g")])
        self.assertEqual(self.found([m])[0]["title"], "Training bei Iulius Andreotti")

    def test_work_invitations_stay_out(self):
        # isolate.py sets JARVIS_WORK_TERMS=Contoso; "Meeting" is a built-in work word
        invite = mail("Einladung: Jour fixe", sender="Contoso Kalender",
                      attachments=[ics("20261026T100000", "20261026T110000", "Jour fixe Vertrieb", "w1")])
        meeting = mail("Einladung", sender="Anna",
                       attachments=[ics("20261026T140000", "20261026T150000", "Meeting Q4-Planung", "w2")])
        self.assertEqual(self.found([invite, meeting, TEE])[0]["title"], "Golfspiel: Ost Tee 10")
        self.assertEqual(len(self.found([invite, meeting, TEE])), 1)

    def test_any_sender_with_a_calendar_file_counts(self):
        doctor = mail("Ihr Termin ist bestätigt", sender="Praxis Dr. Beispiel",
                      attachments=[ics("20261028T083000", "20261028T090000", "Zahnarzt Kontrolle", "d1")])
        self.assertEqual(self.found([doctor])[0]["title"], "Zahnarzt Kontrolle")

    def test_past_ones_are_left_out(self):
        self.assertEqual(self.found([PAST]), [])

    def test_cancellation_mail_without_attachment_cancels(self):
        self.assertEqual(self.found([BOOKED_THEN_CANCELLED, CANCELLATION]), [])

    def test_cancel_method_in_the_attachment(self):
        m = mail("Absage", attachments=[ics("20261026T100000", "20261026T110000", "Training", "x",
                                            extra="METHOD:CANCEL\r\n").replace("METHOD:PUBLISH\r\n", "")])
        self.assertEqual(self.found([m]), [])

    def test_already_in_the_calendar(self):
        existing = [{"start": dt.datetime(2026, 10, 25, 9, 25), "title": "Golf"}]
        self.assertEqual(self.found([TEE], existing), [])

    def test_same_booking_twice_is_offered_once(self):
        self.assertEqual(len(self.found([TEE, TEE])), 1)

    def test_ids_are_stable(self):
        self.assertEqual(self.found([TEE])[0]["id"], self.found([TEE])[0]["id"])

    def test_scan_add_and_card(self):
        import icloud_calendar as cal
        added = []
        real = (cal.configured, cal.add_event, mail_events.dt)
        try:
            cal.configured = lambda: False
            cal.add_event = lambda *a, **k: (added.append(a), ("ok", f"https://cal/x{len(added)}@jarvis-hud.ics"))[1]
            found = mail_events.scan(14, mails=[TEE, FITTING])
            self.assertEqual(len(found), 2)
            self.assertEqual(len(mail_events.card_lines("alle")), 2)
            text = mail_events.add([found[0]["id"]])
            self.assertIn("1 Termin", text)
            self.assertEqual(added[0][0], found[0]["title"])
            self.assertEqual(added[0][1], found[0]["start"].strftime("%Y-%m-%d %H:%M"))
            self.assertEqual(len(mail_events.card_lines("alle")), 1, "an entered one is not offered again")
        finally:
            cal.configured, cal.add_event, mail_events.dt = real

    def test_trusted_is_the_address_not_the_name(self):
        self.assertTrue(mail_events.trusted("noreply@pccaddie-online.de"))
        self.assertTrue(mail_events.trusted("booking@webshop.golfhouse.de"), "subdomains count")
        self.assertFalse(mail_events.trusted("noreply@pccaddie-online.de.evil.com"))
        self.assertFalse(mail_events.trusted("pccaddie-online.de@evil.com"))
        self.assertFalse(mail_events.trusted(""))

    def test_links_never_reach_the_calendar(self):
        m = mail("Buchung", attachments=[ics("20261026T100000", "20261026T110000", "Kurs", "l1",
                                             description="Absagen hier: https://evil.example/x www.evil.example")])
        notes = self.found([m])[0]["notes"]
        self.assertNotIn("http", notes)
        self.assertNotIn("www.", notes)

    def background(self, mails, hour=9):
        """check() with faked calendar, events and proposals; -> (entered, said, proposals, deleted)"""
        import events
        import icloud_calendar as cal
        entered, said, deleted = [], [], []
        saved = (cal.configured, cal.add_event, cal.delete_event, events.publish)
        try:
            cal.configured = lambda: False
            cal.add_event = lambda *a, **k: (entered.append(a[0]), ("ok", f"https://cal/{len(entered)}@jarvis-hud.ics"))[1]
            cal.delete_event = lambda url: deleted.append(url)
            events.publish = lambda t, text="", **k: said.append(text)
            mail_events.check(now=NOW.replace(hour=hour), mails=mails)
            undo = mail_events.undo()
            mail_events.check(now=NOW.replace(hour=hour), mails=mails)     # the next half hour
            return entered, said, [p for p in proposals.pending() if p["kind"] == "calendar"], deleted, undo
        finally:
            cal.configured, cal.add_event, cal.delete_event, events.publish = saved

    def test_trusted_sender_goes_in_announced_and_undoable(self):
        import os
        os.path.exists(mail_events.PATH) and os.remove(mail_events.PATH)
        tee = dict(TEE, addr="noreply@pccaddie-online.de")
        entered, said, props, deleted, undo = self.background([tee])
        self.assertEqual(entered, ["Golfspiel: Ost Tee 10"], "entered once — not again after undo")
        self.assertTrue(any("eingetragen" in t and "rückgängig" in t for t in said))
        self.assertEqual(props, [], "a trusted booking needs no proposal")
        self.assertEqual(len(deleted), 1)
        self.assertIn("herausgenommen", undo)

    def test_unknown_sender_only_proposed(self):
        import os
        os.path.exists(mail_events.PATH) and os.remove(mail_events.PATH)
        doctor = mail("Termin", sender="Praxis", attachments=[ics("20261028T083000", "20261028T090000", "Zahnarzt", "d9")])
        doctor["addr"] = "praxis@example.org"
        entered, said, props, deleted, undo = self.background([doctor])
        self.assertEqual(entered, [], "nothing written without a card")
        self.assertEqual(len(props), 1)
        self.assertEqual(deleted, [])
        # named explicitly: the sender's address, the appointment, and that it is not on the list
        self.assertTrue(any("praxis@example.org" in t and "Zahnarzt" in t and "Vertrauensliste" in t for t in said), said)
        self.assertIn("NICHT vertraut", mail_events.describe(mail_events._load()["found"]))

    def test_stale_offer_is_withdrawn(self):
        # An offer from yesterday whose events are past or entered by hand by now is no longer
        # in a fresh scan — it is withdrawn instead of being brought up for a week.
        for p in proposals.pending():
            proposals.resolve(p["id"], "test")
        proposals.add("calendar", "2 Termin(e) aus Mails eintragen", "x", payload={"ids": ["aaa111", "aaa222"]},
                      source="mail_events")
        proposals.add("calendar", "1 Termin(e) aus Mails eintragen", "y", payload={"ids": ["bbb333"]},
                      source="other")
        self.assertEqual(mail_events.withdraw_stale([{"id": "bbb333"}]), 1)
        left = [p["payload"]["ids"] for p in proposals.pending() if p["kind"] == "calendar"]
        self.assertEqual(left, [["bbb333"]], "one with an event still to come stays")

    def test_quiet_at_night(self):
        import os
        os.path.exists(mail_events.PATH) and os.remove(mail_events.PATH)
        entered, said, props, deleted, undo = self.background([dict(TEE, addr="noreply@pccaddie-online.de")], hour=23)
        self.assertEqual((entered, said), ([], []))

    def test_policy_and_card(self):
        self.assertEqual(policy.action_class("mail_events_undo"), policy.LIMITED_WRITE)
        self.assertEqual(policy.action_class("mail_events_add"), policy.CONFIRM)
        self.assertEqual(policy.action_class("mail_events_scan"), policy.READ)
        self.assertIn("mail_events_scan", policy.UNTRUSTED, "mail content must taint the request")

    def test_router_brings_both_groups(self):
        router.forget_topic()
        r = router.route("Trag mir bitte alle Buchungen aus meinen Mails in den Kalender ein")
        self.assertIn("mail", r["groups"])
        self.assertIn("calendar", r["groups"])

    def test_proposal_kind_exists(self):
        self.assertIn("calendar", proposals.KINDS)


if __name__ == "__main__":
    unittest.main()
