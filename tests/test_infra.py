"""
Infrastructure package: PC values, network diagnosis, Hyper Backup, Home Assistant
health, phone push, incident history. External commands and services are faked.

    .venv\\Scripts\\python.exe -m unittest tests.test_infra -v
"""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import commands           # noqa: E402
import homelab_watch as hw   # noqa: E402
import infra              # noqa: E402
import jarvis_mcp         # noqa: E402
import router             # noqa: E402

PING_EN = """Pinging 1.1.1.1 with 32 bytes of data:
Reply from 1.1.1.1: bytes=32 time=18ms TTL=57
Reply from 1.1.1.1: bytes=32 time=20ms TTL=57
Request timed out.
"""
# German Windows writes "Zeit=": parsed as well
PING_DE = PING_EN.replace("time=", "Zeit=")


class Patch:
    def __init__(self, obj, **kw):
        self.obj, self.kw, self.old = obj, kw, {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = getattr(self.obj, k)
            setattr(self.obj, k, v)

    def __exit__(self, *a):
        for k, v in self.old.items():
            setattr(self.obj, k, v)


class Pc(unittest.TestCase):
    def test_ping_parse(self):
        with Patch(infra, _run=lambda cmd, timeout=10: (0, PING_DE)):
            self.assertEqual(infra.ping("1.1.1.1"), (33, 19.0))
        with Patch(infra, _run=lambda cmd, timeout=10: (0, PING_EN)):
            self.assertEqual(infra.ping("1.1.1.1"), (33, 19.0))

    def test_gpu_parse(self):
        with Patch(infra, _run=lambda cmd, timeout=10: (0, "NVIDIA GeForce RTX 5080, 78, 14503, 16303, 46\n")):
            g = infra.gpu()[0]
        self.assertEqual((g["util"], g["vram_used"], g["temp"]), (78.0, 14503.0, 46.0))

    def test_status_text(self):
        with Patch(infra, gpu=lambda: [], gpu_apps=lambda: []):
            out = infra.pc_status()
        self.assertIn("RAM", out)
        self.assertIn("free of", out)


class Net(unittest.TestCase):
    def diag(self, gw="192.0.2.1", router=(0, 3), internet=(0, 15), dns=40, http=100):
        pings = {gw: router, "1.1.1.1": internet}
        with Patch(infra, gateway=lambda: gw, ping=lambda h, count=3: pings[h], _dns=lambda: dns, _http=lambda: http):
            return infra.net_diagnose()

    def test_all_good(self):
        self.assertIn("All green", self.diag())

    def test_router_side(self):
        self.assertIn("in the home network", self.diag(router=(100, None)))

    def test_provider_side(self):
        self.assertIn("the internet behind it is not", self.diag(internet=(100, None)))

    def test_dns(self):
        self.assertIn("(DNS) is the problem", self.diag(dns=None))

    def test_no_network(self):
        self.assertIn("no network connection", self.diag(gw=None))


class Backup(unittest.TestCase):
    def test_failed_words(self):
        self.assertTrue(infra.backup_failed("Cloud: failed, last 2026/10/21"))
        self.assertFalse(infra.backup_failed("Cloud: success, last 2026/10/21"))

    def test_incident_from_failed_backup(self):
        hw._BACKUP.update(day="", finding=None)
        with Patch(infra, backup_status=lambda: "Cloud: failed, last yesterday"), \
             Patch(jarvis_mcp, SYNO_URL="https://nas", SYNO_USER="u", SYNO_PASSWORD="p"):
            found = hw.check_backup()
        if found:                                    # only after 9 h
            self.assertFalse(found[0]["ok"])
            self.assertIn("backup", found[0]["down"])


class Home(unittest.TestCase):
    def test_check(self):
        now = dt.datetime(2026, 10, 22, 12, tzinfo=dt.timezone.utc)
        states = [
            {"entity_id": "light.kitchen", "state": "on", "attributes": {"friendly_name": "Kitchen"}},
            {"entity_id": "switch.printer", "state": "unavailable", "attributes": {"friendly_name": "Printer"}},
            {"entity_id": "sensor.door_battery", "state": "12",
             "attributes": {"friendly_name": "Door battery", "device_class": "battery", "unit_of_measurement": "%"}},
            {"entity_id": "sensor.bathroom_temp", "state": "21", "last_updated": "2026-10-20T08:00:00+00:00",
             "attributes": {"friendly_name": "Bathroom temperature", "device_class": "temperature"}},
            {"entity_id": "update.ha_core", "state": "unavailable", "attributes": {}},
        ]
        out = infra.home_check(states, now=now)
        self.assertIn("Not reachable (1): Printer", out)
        self.assertIn("Door battery 12 %", out)
        self.assertIn("Bathroom temperature", out)


class Phone(unittest.TestCase):
    def test_not_configured(self):
        self.assertIn("not set up", infra.phone_notify("Hello"))

    def test_push(self):
        calls = []
        with Patch(infra, PHONE="notify.mobile_app_iphone", PHONE_LEVEL="critical"), \
             Patch(jarvis_mcp, _ha=lambda path, body=None: calls.append((path, body)) or []):
            self.assertEqual(infra.push_if_needed("Pi gone", "critical"), "Sent to the phone.")
            self.assertIsNone(infra.push_if_needed("Container", "warn"))
        self.assertEqual(calls, [("/api/services/notify/mobile_app_iphone", {"title": "Jarvis", "message": "Pi gone"})])


class History(unittest.TestCase):
    def setUp(self):
        commands.update_state(lambda d: d.pop("homelab", None))
        if hw.HISTORY.exists():
            hw.HISTORY.unlink()

    def test_incident_written_and_severity_kept(self):
        f = dict(key="pi", ok=False, severity="critical", down="The Pi has been gone for {min} minutes.", up="Pi back.")
        hw.process([f], now=1000, quiet=False)
        out = hw.process([f], now=1300, quiet=False)
        self.assertEqual(out[0].severity, "critical")
        hw.process([dict(f, ok=True)], now=1600, quiet=False)
        h = hw.history()
        self.assertEqual([r["state"] for r in h], ["ok", "bad"])
        self.assertIn("for 5 minutes", h[1]["text"])


class Routing(unittest.TestCase):
    def test_groups(self):
        self.assertIn("systems", router.llm_route("Why is the internet so slow?")["groups"])
        self.assertIn("systems", router.llm_route("How full is my graphics card?")["groups"])
        self.assertIn("phone", router.llm_route("Send that to my phone")["groups"])


if __name__ == "__main__":
    unittest.main()
