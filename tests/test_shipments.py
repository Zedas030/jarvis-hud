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
FAIRWAY = {"shop": "Fairway.de", "addr": "shop@fairway.de", "subject": "A shipment from order #100001 is on its way",
           "date": SENT, "account": "private",
           "text": "Your order is on its way Order #100001 DHL Germany Tracking number: 00340400000000000017"}


def mail(subject, text, shop="Shop", date=SENT):
    return {"shop": shop, "addr": "x@shop.de", "subject": subject, "date": date, "text": text, "account": "private"}


class Parse(unittest.TestCase):
    def test_shopify_shipping_mail(self):
        p = shipments.parse(FAIRWAY)
        self.assertEqual((p["carrier"], p["tracking"], p["order"], p["status"]),
                         ("DHL", "00340400000000000017", "100001", "on the way"))

    def test_newsletter_with_shipment_is_no_parcel(self):
        self.assertIsNone(shipments.parse(mail("The shipment of savings: 50% off everything", "Grab the discount now")))

    def test_same_day_services_are_ignored(self):
        self.assertTrue(shipments.ignored("noreply@uber.com"))
        self.assertTrue(shipments.ignored("hello@mail.goflink.com"))
        self.assertFalse(shipments.ignored("shop@fairway.de"))

    def test_expected_day_from_the_text(self):
        p = shipments.parse(mail("Your order has been dispatched", "With DHL, estimated delivery: October 27"))
        self.assertEqual(p["eta"], dt.date(2026, 10, 27))

    def test_will_be_delivered_is_not_delivered(self):
        p = shipments.parse(mail("Your parcel is on its way", "DHL: your parcel will be delivered tomorrow."))
        self.assertEqual(p["status"], "on the way")
        p = shipments.parse(mail("Your parcel has been delivered", "DHL shipment 00340400000000000017"))
        self.assertEqual(p["status"], "delivered")

    def test_german_carrier_mails_still_parse(self):
        # carriers in Germany write German whatever language Jarvis speaks
        p = shipments.parse(mail("Ihr Paket wurde zugestellt", "DHL Sendung 00340400000000000017"))
        self.assertEqual(p["status"], "delivered")
        p = shipments.parse(mail("Ihre Bestellung wurde versendet", "Mit DHL, voraussichtliche Zustellung am 27.10."))
        self.assertEqual((p["status"], p["eta"]), ("on the way", dt.date(2026, 10, 27)))

    def test_work_stays_out(self):
        self.assertIsNone(shipments.parse(mail("Shipping of your company order", "DHL 00340400000000000017",
                                               shop="Contoso Purchasing")))


SUN = dt.datetime(2026, 10, 25, 18, 0)     # a Sunday


def notice(subject, text, addr, date=SUN):
    return {"shop": "Parcel service", "addr": addr, "subject": subject, "date": date, "text": text, "account": "private"}


class CarrierNotices(unittest.TestCase):
    def test_dhl_tomorrow_and_the_shop_from_the_text(self):
        p = shipments.parse(notice("Your parcel arrives tomorrow", "Your parcel from Fairway arrives tomorrow. "
                                   "Tracking number 00340400000000000017", "noreply@dhl.de"))
        self.assertEqual((p["eta"], p["shop"], p["carrier"]), (dt.date(2026, 10, 26), "Fairway", "DHL"))

    def test_dpd_predict_with_month_name_and_window(self):
        p = shipments.parse(notice("Your DPD parcel delivery", "Your parcel from Zalando will be delivered on Tuesday, "
                                   "27 October 2026 between 10:15 and 11:15. Parcel number: 01234567890123", "info@dpd.de"))
        self.assertEqual((p["eta"], p["window"], p["carrier"], p["tracking"]),
                         (dt.date(2026, 10, 27), "10:15–11:15", "DPD", "01234567890123"))

    def test_hermes_numeric_date_without_year(self):
        p = shipments.parse(notice("Your shipment is on its way", "Hermes: your shipment H1001234567890123456 is "
                                   "expected to be delivered on 28.10.", "noreply@hermesworld.com"))
        self.assertEqual(p["eta"], dt.date(2026, 10, 28))

    def test_gls_today_with_whole_hours(self):
        p = shipments.parse(notice("Delivery today", "GLS: your parcel 12345678901 will be delivered today between 13 "
                                   "and 15 h.", "noreply@gls-pakete.de"))
        self.assertEqual((p["eta"], p["window"]), (dt.date(2026, 10, 25), "13:00–15:00"))

    def test_ups(self):
        p = shipments.parse(notice("UPS Update: Scheduled Delivery", "Scheduled delivery: Thursday, October 29 between "
                                   "9:00 and 12:00 h. Tracking Number: 1Z999AA10123456784", "mcinfo@ups.com"))
        self.assertEqual((p["eta"], p["window"], p["tracking"]), (dt.date(2026, 10, 29), "09:00–12:00", "1Z999AA10123456784"))

    def test_weekday_only(self):
        self.assertEqual(shipments.expected_day("DHL: your parcel arrives on Wednesday.", SUN), dt.date(2026, 10, 28))

    def test_dates_without_a_delivery_word_do_not_count(self):
        self.assertIsNone(shipments.expected_day("Your order of 20.10.2026 has been received.", SUN))
        self.assertIsNone(shipments.expected_day("Good morning! New offers for you.", SUN))
        self.assertEqual(shipments.time_window("Our shop is open from 10 to 18 h."), "")

    def test_relative_days_count_from_the_mail_not_today(self):
        friday = dt.datetime(2026, 10, 23, 20, 0)
        self.assertEqual(shipments.expected_day("Your parcel will be delivered tomorrow.", friday), dt.date(2026, 10, 24))

    def test_december_mail_about_january(self):
        self.assertEqual(shipments.expected_day("Delivery expected on 02.01.", dt.datetime(2026, 12, 28)),
                         dt.date(2027, 1, 2))

    def test_shop_mail_and_carrier_notice_become_one(self):
        items = shipments.merge({}, [shipments.parse(FAIRWAY)])
        dhl = shipments.parse(notice("Your parcel arrives tomorrow", "Your DHL parcel arrives tomorrow between 10 and 13 h. "
                                     "Tracking number 00340400000000000017", "noreply@dhl.de"))
        shipments.merge(items, [dhl])
        self.assertEqual(len(items), 1)
        it = items["00340400000000000017"]
        self.assertEqual((it["shop"], it["eta"], it["window"]), ("Fairway.de", "2026-10-26", "10:00–13:00"),
                         "the carrier's notice brings the day, the shop's name stays")
        self.assertEqual(shipments._title(it), "📦 Fairway.de · DHL 10:00–13:00")


def amazon(subject, text, date=SUN, addr="shipment-tracking@amazon.de"):
    return {"shop": "Amazon.de", "addr": addr, "subject": subject, "date": date, "text": text, "account": "private"}


ORDER_TEXT = "Arriving: Wednesday DHL Parcel locker 101, Sampletown Order # 303-0000001-0000001 View or edit order"


class Amazon(unittest.TestCase):
    def test_order_confirmation_names_the_day(self):
        p = shipments.parse(amazon('Ordered: 3 "Wilson golf balls, Ultra..."', ORDER_TEXT, addr="auto-confirm@amazon.de"))
        self.assertEqual((p["shop"], p["order"], p["eta"]),
                         ("Amazon: Wilson golf balls, Ultra", "303-0000001-0000001", dt.date(2026, 10, 28)))

    def test_parcel_locker_address_is_not_ready_for_pickup(self):
        p = shipments.parse(amazon('Ordered: "Wilson golf balls..."', ORDER_TEXT, addr="auto-confirm@amazon.de"))
        self.assertEqual(p["status"], "on the way")

    def test_button_is_no_order_number(self):
        p = shipments.parse(amazon('Ordered: "Lock..."', "Arriving: Friday View or edit order",
                                   addr="auto-confirm@amazon.de"))
        self.assertNotEqual(p["order"], "edit")

    def test_out_for_delivery_with_window_without_h(self):
        p = shipments.parse(amazon('Out for delivery: "Callaway Golf Syntech..."',
                                   "Out for delivery Arriving today 14:15 - 17:15 Order # 303-0000002-0000002"))
        self.assertEqual((p["status"], p["eta"], p["window"]), ("today", dt.date(2026, 10, 25), "14:15–17:15"))

    def test_attempted_delivery(self):
        p = shipments.parse(amazon('Delivery attempted: "BioBizz All-Mix..."',
                                   "We tried to deliver your package today at 11:37 Order # 303-0000003-0000003",
                                   addr="order-update@amazon.de"))
        self.assertEqual(p["status"], "attempt")

    def test_one_order_from_ordered_to_delivered(self):
        mails = [amazon('Ordered: "Callaway Golf Syntech..."', "Arriving: Monday Order # 303-0000002-0000002",
                        date=dt.datetime(2026, 9, 5), addr="auto-confirm@amazon.de"),
                 amazon('Delivered: "Callaway Golf Syntech..."', "Delivered Order # 303-0000002-0000002",
                        date=dt.datetime(2026, 9, 7), addr="order-update@amazon.de")]
        items = shipments.merge({}, [shipments.parse(m) for m in mails])
        self.assertEqual(len(items), 1)
        self.assertEqual(list(items.values())[0]["status"], "delivered")

    def test_digital_purchases_and_account_mails_stay_out(self):
        self.assertIsNone(shipments.parse(amazon("Your Amazon.de order of Shazam! [OV]",
                                                 "Thank you for your purchase on Prime Video.")))
        self.assertIsNone(shipments.parse(amazon("Amazon password assistance", "Your verification code")))

    def test_title_says_amazon_once(self):
        self.assertEqual(shipments._title({"shop": "Amazon: BioBizz All-Mix", "carrier": "Amazon", "status": "on the way"}),
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
        base = {"key": "T1", "shop": "Fairway.de", "carrier": "DHL", "tracking": "T1", "status": "on the way",
                "subject": "Shipment", "eta": "2026-10-26"}
        return {"items": {"T1": dict(base, **it)}, "auto": []}

    def test_entry_without_links_and_announced(self):
        d = self.state()
        added, _, said = self.run_sync(d, dt.datetime(2026, 10, 23, 9))
        self.assertEqual(added[0][:3], ("📦 Fairway.de · DHL", "2026-10-26", True))
        self.assertNotIn("http", added[0][3])
        self.assertTrue(any("entered" in s for s in said))
        self.assertEqual(self.run_sync(d, dt.datetime(2026, 10, 23, 10))[0], [], "no second entry")

    def test_moves_with_the_day(self):
        d = self.state()
        self.run_sync(d, dt.datetime(2026, 10, 23, 9))
        d["items"]["T1"]["eta"] = "2026-10-27"
        added, deleted, said = self.run_sync(d, dt.datetime(2026, 10, 24, 9))
        self.assertEqual((len(deleted), added[0][1]), (1, "2026-10-27"))
        self.assertTrue(any("moves" in s for s in said))

    def test_delivered_entry_is_marked(self):
        d = self.state()
        self.run_sync(d, dt.datetime(2026, 10, 23, 9))
        d["items"]["T1"]["status"] = "delivered"
        added, deleted, _ = self.run_sync(d, dt.datetime(2026, 10, 26, 12))
        self.assertTrue(added[0][0].startswith("✓ delivered"))

    def test_reminder_names_the_window(self):
        d = self.state(eta="2026-10-26", window="10:15–11:15")
        _, _, said = self.run_sync(d, dt.datetime(2026, 10, 26, 7, 30))
        self.assertTrue(any("between 10:15 and 11:15" in s for s in said), said)

    def test_reminder_on_the_day_once(self):
        d = self.state(eta="2026-10-26")
        self.run_sync(d, dt.datetime(2026, 10, 25, 9))
        _, _, said = self.run_sync(d, dt.datetime(2026, 10, 26, 7, 30))
        self.assertTrue(any("arrives today" in s for s in said))
        self.assertEqual(self.run_sync(d, dt.datetime(2026, 10, 26, 8))[2], [], "once a day")

    def test_quiet_at_night(self):
        self.assertEqual(self.run_sync(self.state(), dt.datetime(2026, 10, 23, 23)), ([], [], []))

    def test_no_entry_for_a_day_already_past(self):
        # 23.10.: old Amazon orders whose "delivered" mail was outside the window got entries for 6.–12.10.
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
        "latest_event": {"description": "The shipment has arrived at the Hermes distribution centre"},
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
        self.assertEqual((r["status"], r["eta"], r["carrier"]), ("on the way", dt.date(2026, 10, 27), "Hermes"))
        self.assertEqual(seen, ["register", "gettrackinfo"])

    def test_registered_once_then_only_asked(self):
        items = {"H1": {"key": "H1", "tracking": "H1", "shop": "Shop", "status": "on the way"}}
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
        self.assertEqual((r["status"], r["eta"]), ("today", dt.date.today()))

    def test_odd_answer_keeps_what_the_mail_said(self):
        items = {"H1": {"key": "H1", "tracking": "H1", "shop": "Shop", "status": "on the way", "eta": "2026-10-28"}}

        def broken(path, body):
            raise ValueError("unexpected")
        with mock.patch.object(shipments, "TRACK17_KEY", "k"), mock.patch.object(shipments, "_t17", broken):
            shipments.refresh(items, now=shipments.REFRESH_S + 1000.0)
        self.assertEqual(items["H1"]["eta"], "2026-10-28")
        self.assertIn("17TRACK", items["H1"]["live_error"])

    def test_without_key_nothing_leaves_the_house(self):
        with mock.patch.object(shipments, "TRACK17_KEY", ""), mock.patch.object(shipments, "_t17") as call:
            self.assertIsNone(shipments.track17("H1"))
            shipments.refresh({"H1": {"tracking": "H1", "status": "on the way", "carrier": "Hermes"}})
        call.assert_not_called()


class Dhl(unittest.TestCase):
    def test_expected_day_and_status(self):
        body = (b'{"shipments":[{"status":{"statusCode":"transit","description":"The shipment has been processed in the '
                b'parcel centre of origin."},"estimatedTimeOfDelivery":"2026-10-26T09:00:00+02:00"}]}')
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = body
        with mock.patch.object(shipments, "DHL_KEY", "k"), mock.patch("urllib.request.urlopen", return_value=resp):
            r = shipments.dhl("00340400000000000017")
        self.assertEqual((r["status"], r["eta"]), ("on the way", dt.date(2026, 10, 26)))

    def test_without_key_nothing_is_asked(self):
        with mock.patch.object(shipments, "DHL_KEY", ""), mock.patch("urllib.request.urlopen") as call:
            self.assertIsNone(shipments.dhl("00340400000000000017"))
        call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
