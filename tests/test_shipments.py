"""
Shipment tracking — mails, DHL and the calendar faked; no network.

    .venv\\Scripts\\python.exe -m unittest tests.test_shipments -v
"""
import isolate  # noqa: F401  (must come first)
import datetime as dt
import unittest
from unittest import mock

import shipments

SENT = dt.datetime(2026, 10, 23, 10, 0)
FAIRWAY = {"shop": "Fairway.de", "addr": "shop@fairway.de", "subject": "Eine Sendung aus der Bestellung #100001 ist unterwegs",
         "date": SENT, "account": "privat",
         "text": "Deine Bestellung ist auf dem Weg Bestellung #100001 DHL Germany Trackingnummer: 00340400000000000017"}


def mail(subject, text, shop="Shop", date=SENT):
    return {"shop": shop, "addr": "x@shop.de", "subject": subject, "date": date, "text": text, "account": "privat"}


class Parse(unittest.TestCase):
    def test_shopify_shipping_mail(self):
        p = shipments.parse(FAIRWAY)
        self.assertEqual((p["carrier"], p["tracking"], p["order"], p["status"]),
                         ("DHL", "00340400000000000017", "100001", "unterwegs"))

    def test_newsletter_with_sendung_is_no_parcel(self):
        self.assertIsNone(shipments.parse(mail("Der 7,5-kg-Erfolg aus der Sendung - 50% Rabatt", "Jetzt Rabatt sichern")))

    def test_same_day_services_are_ignored(self):
        self.assertTrue(shipments.ignored("noreply@uber.com"))
        self.assertTrue(shipments.ignored("hello@mail.goflink.com"))
        self.assertFalse(shipments.ignored("shop@fairway.de"))

    def test_expected_day_from_the_text(self):
        p = shipments.parse(mail("Ihre Bestellung wurde versendet", "Mit DHL, voraussichtliche Zustellung am 27.10."))
        self.assertEqual(p["eta"], dt.date(2026, 10, 27))

    def test_will_be_delivered_is_not_delivered(self):
        p = shipments.parse(mail("Ihr Paket ist unterwegs", "DHL: Ihr Paket wird morgen zugestellt."))
        self.assertEqual(p["status"], "unterwegs")
        p = shipments.parse(mail("Ihr Paket wurde zugestellt", "DHL Sendung 00340400000000000017"))
        self.assertEqual(p["status"], "zugestellt")

    def test_work_stays_out(self):
        self.assertIsNone(shipments.parse(mail("Versand Ihrer Firmen-Bestellung", "DHL 00340400000000000017",
                                               shop="Contoso Einkauf")))


SUN = dt.datetime(2026, 10, 25, 18, 0)     # a Sunday


def notice(subject, text, addr, date=SUN):
    return {"shop": "Paketdienst", "addr": addr, "subject": subject, "date": date, "text": text, "account": "privat"}


class CarrierNotices(unittest.TestCase):
    def test_dhl_tomorrow_and_the_shop_from_the_text(self):
        p = shipments.parse(notice("Ihr Paket kommt morgen", "Ihr Paket von Fairway kommt morgen. "
                                   "Sendungsnummer 00340400000000000017", "noreply@dhl.de"))
        self.assertEqual((p["eta"], p["shop"], p["carrier"]), (dt.date(2026, 10, 26), "Fairway", "DHL"))

    def test_dpd_predict_with_month_name_and_window(self):
        p = shipments.parse(notice("Ihre DPD Paketzustellung", "Ihr Paket von Zalando wird am Dienstag, 27. Oktober 2026 "
                                   "zwischen 10:15 und 11:15 Uhr zugestellt. Paketnummer: 01234567890123", "info@dpd.de"))
        self.assertEqual((p["eta"], p["window"], p["carrier"], p["tracking"]),
                         (dt.date(2026, 10, 27), "10:15–11:15", "DPD", "01234567890123"))

    def test_hermes_numeric_date_without_year(self):
        p = shipments.parse(notice("Ihre Sendung ist unterwegs", "Hermes: Ihre Sendung H1001234567890123456 wird "
                                   "voraussichtlich am 28.10. zugestellt.", "noreply@hermesworld.com"))
        self.assertEqual(p["eta"], dt.date(2026, 10, 28))

    def test_gls_today_with_whole_hours(self):
        p = shipments.parse(notice("Zustellung heute", "GLS: Ihr Paket 12345678901 wird heute zwischen 13 und 15 Uhr "
                                   "zugestellt.", "noreply@gls-pakete.de"))
        self.assertEqual((p["eta"], p["window"]), (dt.date(2026, 10, 25), "13:00–15:00"))

    def test_ups_in_english(self):
        p = shipments.parse(notice("UPS Update: Scheduled Delivery", "Scheduled delivery: Thursday, October 29 between "
                                   "9:00 and 12:00 h. Tracking Number: 1Z999AA10123456784", "mcinfo@ups.com"))
        self.assertEqual((p["eta"], p["window"], p["tracking"]), (dt.date(2026, 10, 29), "09:00–12:00", "1Z999AA10123456784"))

    def test_weekday_only(self):
        self.assertEqual(shipments.expected_day("DHL: Ihr Paket kommt am Mittwoch.", SUN), dt.date(2026, 10, 28))

    def test_dates_without_a_delivery_word_do_not_count(self):
        self.assertIsNone(shipments.expected_day("Ihre Bestellung vom 20.10.2026 ist eingegangen.", SUN))
        self.assertIsNone(shipments.expected_day("Guten Morgen! Neue Angebote für Sie.", SUN))
        self.assertEqual(shipments.time_window("Unser Laden hat von 10 bis 18 Uhr geöffnet."), "")

    def test_relative_days_count_from_the_mail_not_today(self):
        friday = dt.datetime(2026, 10, 23, 20, 0)
        self.assertEqual(shipments.expected_day("Ihr Paket wird morgen zugestellt.", friday), dt.date(2026, 10, 24))

    def test_december_mail_about_january(self):
        self.assertEqual(shipments.expected_day("Lieferung voraussichtlich am 02.01.", dt.datetime(2026, 12, 28)),
                         dt.date(2027, 1, 2))

    def test_shop_mail_and_carrier_notice_become_one(self):
        items = shipments.merge({}, [shipments.parse(FAIRWAY)])
        dhl = shipments.parse(notice("Ihr Paket kommt morgen", "Ihr DHL Paket kommt morgen zwischen 10 und 13 Uhr. "
                                     "Sendungsnummer 00340400000000000017", "noreply@dhl.de"))
        shipments.merge(items, [dhl])
        self.assertEqual(len(items), 1)
        it = items["00340400000000000017"]
        self.assertEqual((it["shop"], it["eta"], it["window"]), ("Fairway.de", "2026-10-26", "10:00–13:00"),
                         "the carrier's notice brings the day, the shop's name stays")
        self.assertEqual(shipments._title(it), "📦 Fairway.de · DHL 10:00–13:00")


def amazon(subject, text, date=SUN, addr="shipment-tracking@amazon.de"):
    return {"shop": "Amazon.de", "addr": addr, "subject": subject, "date": date, "text": text, "account": "privat"}


ORDER_TEXT = ("Bestellt Versendet In Zustellung Zugestellt Zustellung: Mittwoch DHL Packstation 101, Musterstadt "
              "Bestellnr. 303-0000001-0000001 Bestellung ansehen")


class Amazon(unittest.TestCase):
    def test_order_confirmation_names_the_day(self):
        p = shipments.parse(amazon("Bestellt: 3 „Wilson Golfbälle, Ultra...“", ORDER_TEXT, addr="bestellbestaetigung@amazon.de"))
        self.assertEqual((p["shop"], p["order"], p["eta"]),
                         ("Amazon: Wilson Golfbälle, Ultra", "303-0000001-0000001", dt.date(2026, 10, 28)))

    def test_packstation_address_is_not_ready_for_pickup(self):
        p = shipments.parse(amazon("Bestellt: „Wilson Golfbälle...“", ORDER_TEXT, addr="bestellbestaetigung@amazon.de"))
        self.assertEqual(p["status"], "unterwegs")

    def test_button_is_no_order_number(self):
        p = shipments.parse(amazon("Bestellt: „Schloss...“", "Zustellung: Freitag Bestellung ansehen",
                                   addr="bestellbestaetigung@amazon.de"))
        self.assertNotEqual(p["order"], "ansehen")

    def test_out_for_delivery_with_window_without_uhr(self):
        p = shipments.parse(amazon("In Zustellung: „Callaway Golf Syntech...“",
                                   "In Zustellung Zustellung heute 14:15 - 17:15 Bestellnr. 303-0000002-0000002"))
        self.assertEqual((p["status"], p["eta"], p["window"]), ("heute", dt.date(2026, 10, 25), "14:15–17:15"))

    def test_attempted_delivery(self):
        p = shipments.parse(amazon("Versuchte Zustellung: „BioBizz All-Mix...“",
                                   "Zustellung wurde versucht heute um 11:37 Bestellnr. 303-0000003-0000003",
                                   addr="order-update@amazon.de"))
        self.assertEqual(p["status"], "zustellversuch")

    def test_one_order_from_ordered_to_delivered(self):
        mails = [amazon("Bestellt: „Callaway Golf Syntech...“", "Zustellung: Montag Bestellnr. 303-0000002-0000002",
                        date=dt.datetime(2026, 9, 5), addr="bestellbestaetigung@amazon.de"),
                 amazon("Geliefert: \"Callaway Golf Syntech...“", "Zugestellt Bestellnr. 303-0000002-0000002",
                        date=dt.datetime(2026, 9, 7), addr="order-update@amazon.de")]
        items = shipments.merge({}, [shipments.parse(m) for m in mails])
        self.assertEqual(len(items), 1)
        self.assertEqual(list(items.values())[0]["status"], "zugestellt")

    def test_digital_purchases_and_account_mails_stay_out(self):
        self.assertIsNone(shipments.parse(amazon("Ihre Amazon.de Bestellung von Shazam! [dt./OV]",
                                                 "Vielen Dank für Ihren Kauf bei Prime Video.")))
        self.assertIsNone(shipments.parse(amazon("Amazon Passwort Unterstützung", "Ihr Bestätigungscode")))

    def test_title_says_amazon_once(self):
        self.assertEqual(shipments._title({"shop": "Amazon: BioBizz All-Mix", "carrier": "Amazon", "status": "unterwegs"}),
                         "📦 Amazon: BioBizz All-Mix")


class Calendar(unittest.TestCase):
    def run_sync(self, d, now):
        import icloud_calendar as cal
        added, deleted, said = [], [], []

        def add(title, start, end=None, location="", notes="", calendar="", allday=False, return_url=False):
            added.append((title, start, allday, notes))
            return "ok", f"https://cal/{len(added)}@jarvis-hud.ics"
        with mock.patch.object(cal, "add_event", add), mock.patch.object(cal, "delete_event", deleted.append):
            shipments.sync(d, now=now, say=lambda text, key: said.append(text))
        return added, deleted, said

    def state(self, **it):
        base = {"key": "T1", "shop": "Fairway.de", "carrier": "DHL", "tracking": "T1", "status": "unterwegs",
                "subject": "Sendung", "eta": "2026-10-26"}
        return {"items": {"T1": dict(base, **it)}, "auto": []}

    def test_entry_without_links_and_announced(self):
        d = self.state()
        added, _, said = self.run_sync(d, dt.datetime(2026, 10, 23, 9))
        self.assertEqual(added[0][:3], ("📦 Fairway.de · DHL", "2026-10-26", True))
        self.assertNotIn("http", added[0][3])
        self.assertTrue(any("eingetragen" in s for s in said))
        self.assertEqual(self.run_sync(d, dt.datetime(2026, 10, 23, 10))[0], [], "no second entry")

    def test_moves_with_the_day(self):
        d = self.state()
        self.run_sync(d, dt.datetime(2026, 10, 23, 9))
        d["items"]["T1"]["eta"] = "2026-10-27"
        added, deleted, said = self.run_sync(d, dt.datetime(2026, 10, 24, 9))
        self.assertEqual((len(deleted), added[0][1]), (1, "2026-10-27"))
        self.assertTrue(any("verschiebt" in s for s in said))

    def test_delivered_entry_is_marked(self):
        d = self.state()
        self.run_sync(d, dt.datetime(2026, 10, 23, 9))
        d["items"]["T1"]["status"] = "zugestellt"
        added, deleted, _ = self.run_sync(d, dt.datetime(2026, 10, 26, 12))
        self.assertTrue(added[0][0].startswith("✓ zugestellt"))

    def test_reminder_names_the_window(self):
        d = self.state(eta="2026-10-26", window="10:15–11:15")
        _, _, said = self.run_sync(d, dt.datetime(2026, 10, 26, 7, 30))
        self.assertTrue(any("zwischen 10:15 und 11:15 Uhr" in s for s in said), said)

    def test_reminder_on_the_day_once(self):
        d = self.state(eta="2026-10-26")
        self.run_sync(d, dt.datetime(2026, 10, 25, 9))
        _, _, said = self.run_sync(d, dt.datetime(2026, 10, 26, 7, 30))
        self.assertTrue(any("heute kommt" in s for s in said))
        self.assertEqual(self.run_sync(d, dt.datetime(2026, 10, 26, 8))[2], [], "once a day")

    def test_quiet_at_night(self):
        self.assertEqual(self.run_sync(self.state(), dt.datetime(2026, 10, 23, 23)), ([], [], []))

    def test_no_entry_for_a_day_already_past(self):
        # 23.10.: old Amazon orders whose "Geliefert" mail was outside the window got entries for 6.–12.10.
        d = self.state(eta="2026-10-08")
        added, _, said = self.run_sync(d, dt.datetime(2026, 10, 23, 9))
        self.assertEqual((added, said), ([], []))
        self.assertEqual(d["items"], {}, "long past and never entered: forgotten")

    def test_no_day_no_entry(self):
        self.assertEqual(self.run_sync(self.state(eta=None), dt.datetime(2026, 10, 23, 9))[0], [])

    def test_undone_entry_does_not_come_back(self):
        import icloud_calendar as cal
        d = self.state()
        self.run_sync(d, dt.datetime(2026, 10, 23, 9))
        shipments._save(d)
        with mock.patch.object(cal, "delete_event", lambda url: None):
            gone = shipments.undo()
        self.assertEqual(len(gone), 1)
        d = shipments._load()
        self.assertEqual(self.run_sync(d, dt.datetime(2026, 10, 23, 10))[0], [], "the user said no to the entry")


class Track17(unittest.TestCase):
    INFO = {"data": {"accepted": [{"number": "H1", "track_info": {
        "latest_status": {"status": "InTransit"},
        "latest_event": {"description": "Die Sendung ist im Hermes Verteilzentrum eingetroffen"},
        "time_metrics": {"estimated_delivery_date": {"from": "2026-10-27T00:00:00+02:00", "to": None}},
        "tracking": {"providers": [{"provider": {"name": "Hermes"}}]}}}], "rejected": []}}

    def calls(self, responses):
        seen = []

        def fake(path, body):
            seen.append(path)
            return responses[path]
        return seen, fake

    def test_any_carrier_with_expected_day(self):
        seen, fake = self.calls({"register": {"data": {"accepted": [{}], "rejected": []}}, "gettrackinfo": self.INFO})
        with mock.patch.object(shipments, "TRACK17_KEY", "k"), mock.patch.object(shipments, "_t17", fake):
            r = shipments.track17("H1")
        self.assertEqual((r["status"], r["eta"], r["carrier"]), ("unterwegs", dt.date(2026, 10, 27), "Hermes"))
        self.assertEqual(seen, ["register", "gettrackinfo"])

    def test_registered_once_then_only_asked(self):
        items = {"H1": {"key": "H1", "tracking": "H1", "shop": "Shop", "status": "unterwegs"}}
        seen, fake = self.calls({"register": {"data": {"accepted": [{}], "rejected": []}}, "gettrackinfo": self.INFO})
        with mock.patch.object(shipments, "TRACK17_KEY", "k"), mock.patch.object(shipments, "_t17", fake):
            shipments.refresh(items, now=shipments.REFRESH_S + 1000.0)
            shipments.refresh(items, now=2 * shipments.REFRESH_S + 1001.0)
        self.assertEqual(seen.count("register"), 1, "registering costs quota: once per parcel")
        self.assertEqual(items["H1"]["eta"], "2026-10-27")
        self.assertEqual(items["H1"]["carrier"], "Hermes", "17TRACK names the carrier when the mail did not")

    def test_out_for_delivery_is_today(self):
        info = {"data": {"accepted": [{"track_info": {"latest_status": {"status": "OutForDelivery"}}}]}}
        _, fake = self.calls({"gettrackinfo": info})
        with mock.patch.object(shipments, "TRACK17_KEY", "k"), mock.patch.object(shipments, "_t17", fake):
            r = shipments.track17("H1", registered=True)
        self.assertEqual((r["status"], r["eta"]), ("heute", dt.date.today()))

    def test_odd_answer_keeps_what_the_mail_said(self):
        items = {"H1": {"key": "H1", "tracking": "H1", "shop": "Shop", "status": "unterwegs", "eta": "2026-10-28"}}

        def broken(path, body):
            raise ValueError("unexpected")
        with mock.patch.object(shipments, "TRACK17_KEY", "k"), mock.patch.object(shipments, "_t17", broken):
            shipments.refresh(items, now=shipments.REFRESH_S + 1000.0)
        self.assertEqual(items["H1"]["eta"], "2026-10-28")
        self.assertIn("17TRACK", items["H1"]["live_error"])

    def test_without_key_nothing_leaves_the_house(self):
        with mock.patch.object(shipments, "TRACK17_KEY", ""), mock.patch.object(shipments, "_t17") as call:
            self.assertIsNone(shipments.track17("H1"))
            shipments.refresh({"H1": {"tracking": "H1", "status": "unterwegs", "carrier": "Hermes"}})
        call.assert_not_called()


class Dhl(unittest.TestCase):
    def test_expected_day_and_status(self):
        body = (b'{"shipments":[{"status":{"statusCode":"transit","description":"Die Sendung wurde im Start-Paketzentrum '
                b'bearbeitet."},"estimatedTimeOfDelivery":"2026-10-26T09:00:00+02:00"}]}')
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = body
        with mock.patch.object(shipments, "DHL_KEY", "k"), mock.patch("urllib.request.urlopen", return_value=resp):
            r = shipments.dhl("00340400000000000017")
        self.assertEqual((r["status"], r["eta"]), ("unterwegs", dt.date(2026, 10, 26)))

    def test_without_key_nothing_is_asked(self):
        with mock.patch.object(shipments, "DHL_KEY", ""), mock.patch("urllib.request.urlopen") as call:
            self.assertIsNone(shipments.dhl("00340400000000000017"))
        call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
