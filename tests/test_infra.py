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

PING_DE = """Ping wird ausgeführt für 1.1.1.1 mit 32 Bytes Daten:
Antwort von 1.1.1.1: Bytes=32 Zeit=18ms TTL=57
Antwort von 1.1.1.1: Bytes=32 Zeit=20ms TTL=57
Zeitüberschreitung der Anforderung.
"""


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

    def test_gpu_parse(self):
        with Patch(infra, _run=lambda cmd, timeout=10: (0, "NVIDIA GeForce RTX 5080, 78, 14503, 16303, 46\n")):
            g = infra.gpu()[0]
        self.assertEqual((g["util"], g["vram_used"], g["temp"]), (78.0, 14503.0, 46.0))

    def test_status_text(self):
        with Patch(infra, gpu=lambda: [], gpu_apps=lambda: []):
            out = infra.pc_status()
        self.assertIn("RAM", out)
        self.assertIn("frei von", out)


class Net(unittest.TestCase):
    def diag(self, gw="192.0.2.1", router=(0, 3), internet=(0, 15), dns=40, http=100):
        pings = {gw: router, "1.1.1.1": internet}
        with Patch(infra, gateway=lambda: gw, ping=lambda h, count=3: pings[h], _dns=lambda: dns, _http=lambda: http):
            return infra.net_diagnose()

    def test_all_good(self):
        self.assertIn("Alles im grünen Bereich", self.diag())

    def test_router_side(self):
        self.assertIn("im Heimnetz", self.diag(router=(100, None)))

    def test_provider_side(self):
        self.assertIn("das Internet dahinter nicht", self.diag(internet=(100, None)))

    def test_dns(self):
        self.assertIn("(DNS) ist das Problem", self.diag(dns=None))

    def test_no_network(self):
        self.assertIn("keine Netzwerkverbindung", self.diag(gw=None))


class Backup(unittest.TestCase):
    def test_failed_words(self):
        self.assertTrue(infra.backup_failed("Cloud: failed, zuletzt 2026/10/21"))
        self.assertFalse(infra.backup_failed("Cloud: success, zuletzt 2026/10/21"))

    def test_incident_from_failed_backup(self):
        hw._BACKUP.update(day="", finding=None)
        with Patch(infra, backup_status=lambda: "Cloud: failed, zuletzt gestern"), \
             Patch(jarvis_mcp, SYNO_URL="https://nas", SYNO_USER="u", SYNO_PASSWORD="p"):
            found = hw.check_backup()
        if found:                                    # only after 9 h
            self.assertFalse(found[0]["ok"])
            self.assertIn("Backup", found[0]["down"])


class Home(unittest.TestCase):
    def test_check(self):
        now = dt.datetime(2026, 10, 22, 12, tzinfo=dt.timezone.utc)
        states = [
            {"entity_id": "light.kueche", "state": "on", "attributes": {"friendly_name": "Küche"}},
            {"entity_id": "switch.drucker", "state": "unavailable", "attributes": {"friendly_name": "Drucker"}},
            {"entity_id": "sensor.tuer_batterie", "state": "12",
             "attributes": {"friendly_name": "Tür Batterie", "device_class": "battery", "unit_of_measurement": "%"}},
            {"entity_id": "sensor.bad_temp", "state": "21", "last_updated": "2026-10-20T08:00:00+00:00",
             "attributes": {"friendly_name": "Bad Temperatur", "device_class": "temperature"}},
            {"entity_id": "update.ha_core", "state": "unavailable", "attributes": {}},
        ]
        out = infra.home_check(states, now=now)
        self.assertIn("Nicht erreichbar (1): Drucker", out)
        self.assertIn("Tür Batterie 12 %", out)
        self.assertIn("Bad Temperatur", out)


class Phone(unittest.TestCase):
    def test_not_configured(self):
        self.assertIn("nicht eingerichtet", infra.phone_notify("Hallo"))

    def test_push(self):
        calls = []
        with Patch(infra, PHONE="notify.mobile_app_iphone", PHONE_LEVEL="critical"), \
             Patch(jarvis_mcp, _ha=lambda path, body=None: calls.append((path, body)) or []):
            self.assertEqual(infra.push_if_needed("Pi weg", "critical"), "Ans Handy geschickt.")
            self.assertIsNone(infra.push_if_needed("Container", "warn"))
        self.assertEqual(calls, [("/api/services/notify/mobile_app_iphone", {"title": "Jarvis", "message": "Pi weg"})])


class History(unittest.TestCase):
    def setUp(self):
        commands.update_state(lambda d: d.pop("homelab", None))
        if hw.HISTORY.exists():
            hw.HISTORY.unlink()

    def test_incident_written_and_severity_kept(self):
        f = dict(key="pi", ok=False, severity="critical", down="Der Pi ist seit {min} Minuten weg.", up="Pi wieder da.")
        hw.process([f], now=1000, quiet=False)
        out = hw.process([f], now=1300, quiet=False)
        self.assertEqual(out[0].severity, "critical")
        hw.process([dict(f, ok=True)], now=1600, quiet=False)
        h = hw.history()
        self.assertEqual([r["state"] for r in h], ["ok", "bad"])
        self.assertIn("seit 5 Minuten", h[1]["text"])


class Routing(unittest.TestCase):
    def test_groups(self):
        self.assertIn("systems", router.llm_route("Warum ist das Internet so langsam?")["groups"])
        self.assertIn("systems", router.llm_route("Wie voll ist meine Grafikkarte?")["groups"])
        self.assertIn("phone", router.llm_route("Schick mir das aufs Handy")["groups"])


if __name__ == "__main__":
    unittest.main()
