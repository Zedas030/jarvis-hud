"""
Updates — parsing and routing only: no Home Assistant, no NAS, no Pi.

    .venv\\Scripts\\python.exe -m unittest tests.test_updates -v
"""
import isolate  # noqa: F401  (must come first)
import unittest
from unittest import mock

import policy
import router
import updates


class Updates(unittest.TestCase):
    def test_install_needs_card_check_does_not(self):
        self.assertEqual(policy.action_class("update_install"), policy.CONFIRM)
        self.assertEqual(policy.action_class("mcp__jarvis__updates_check"), policy.READ)

    def test_router_brings_update_tools(self):
        for text in ("Gibt es Updates für das NAS?", "Aktualisiere bitte die FRITZ!Box", "Ist die Firmware aktuell?"):
            self.assertIn("updates", router.llm_route(text)["groups"], text)

    def test_update_questions_skip_the_status_line(self):
        # Heard like this: each got "Das NAS: alles in Ordnung …" from the local status handler.
        for text in ("kannst du meinen NAS updaten, das Knowledgey NAS?", "mach ein Software Update von dem NAS.",
                     "Ja, prüfe, ob irgendwelche Apps oder irgendwelche Updates auf dem NAS verfügbar sind.",
                     "Wie läuft das Update auf dem Pi?"):
            r = router.route(text)
            self.assertEqual(r["kind"], "llm", text)
            self.assertIn("updates", r["groups"], text)
        # a fixed NAS reading: the status question must still reach the local handler, without the real NAS
        nas = {"ok": True, "temp": 40, "used_pct": 50, "used_tb": 2.0, "total_tb": 4.0}
        with mock.patch.object(router, "_dash", return_value=nas):
            self.assertEqual(router.route("Wie geht es dem NAS?").get("handler"), "nas")

    def test_ha_only_entities_with_update(self):
        states = [
            {"entity_id": "update.fritz", "state": "on",
             "attributes": {"friendly_name": "FRITZ!OS", "installed_version": "8.25", "latest_version": "8.30",
                            "supported_features": 7}},
            {"entity_id": "update.levoit", "state": "on",
             "attributes": {"friendly_name": "Levoit", "installed_version": "1", "latest_version": "2",
                            "supported_features": 0}},
            {"entity_id": "update.done", "state": "off", "attributes": {}},
            {"entity_id": "light.x", "state": "on", "attributes": {}},
        ]
        got = updates._ha_updates(states)
        self.assertEqual([(u[0], u[4]) for u in got], [("ha:update.fritz", True), ("ha:update.levoit", False)])

    def test_pi_parse(self):
        out = ("APT 22\nREBOOT\nPIHOLE Core version is v6.0.5 (Latest: v6.0.6)\n"
               "PIHOLE Web version is v6.0.2 (Latest: v6.0.2)\nLOG fertig, Code 0\n")
        st = updates.parse_pi(out)
        self.assertEqual((st["apt"], st["reboot"], st["running"], st["pihole_update"]), (22, True, False, True))
        self.assertEqual(st["log"], "fertig, Code 0")

    def test_pi_parse_pihole_current(self):
        st = updates.parse_pi("APT 0\nRUNNING\nPIHOLE Core version is v6.0.6 (Latest: v6.0.6)\n")
        self.assertEqual((st["apt"], st["running"], st["pihole_update"]), (0, True, False))
        self.assertIsNone(updates.parse_pi("APT 3\n")["pihole_update"])

    def test_version_compare(self):
        self.assertTrue(updates._newer("3.1.2-1025", "3.1.1-1024"))
        self.assertFalse(updates._newer("3.1.10-1", "3.1.10-1"))
        self.assertTrue(updates._newer("3.1.10-1", "3.1.9-9"))

    def test_package_steps_match_the_package_centre(self):
        # DSM answered 120 (invalid parameter) to two earlier guesses; this is synology-api's flow.
        s = {"link": "https://example.invalid/CMS.spk", "md5": "abc", "size": 2824634}
        download, status, fetched, install = updates.package_steps("CMS", s, "/volume1")
        self.assertEqual((download["method"], download["operation"], download["name"], download["filesize"],
                          download["blqinst"]), ("install", "install", "CMS", "2824634", "false"))
        self.assertEqual(status("t1")["task_id"], "t1")
        self.assertEqual((fetched("t1")["api"], fetched("t1")["taskid"]),
                         ("SYNO.Core.Package.Installation.Download", "t1"))
        step = install("/volume1/@tmp/synopkg/download.x/@SYNOPKG_DOWNLOAD_CMS")
        self.assertEqual((step["method"], step["volume_path"], step["force"], step["installrunpackage"]),
                         ("install", "/volume1", "true", "true"))
        self.assertNotIn("upgrade", step.values(), "method upgrade got DSM error 4501")

    def test_unknown_target_runs_nothing(self):
        self.assertIn("Unbekanntes Ziel", updates.install("rm -rf /"))

    def test_report_only_targets_run_nothing(self):
        with mock.patch.object(updates, "_pi") as pi, mock.patch.object(updates, "_ha") as ha:
            for target in ("pi:docker:homeassistant", "fritz:Hier Key eintragen", "fw:tp-link-p110"):
                self.assertIn("nicht", updates.install(target), target)
        pi.assert_not_called()
        ha.assert_not_called()

    def test_firmware_drift_names_only_the_older_ones(self):
        devices = [
            ["TP-Link", "P110", "TV", "1.4.8 Build 260804 Rel.045837"],
            ["TP-Link", "P110", "Kühlschrank", "1.4.0 Build 251020 Rel.161559"],
            ["TP-Link", "P110", "PC", "1.4.6 Build 260309 Rel.093810"],
            ["TP-Link", "P110", "Zelt", "1.4.8 Build 260804 Rel.045837"],
            ["TP-Link", "KE100", "Links", "2.15.0"], ["TP-Link", "KE100", "Rechts", "2.15.0"],
            ["VeSync", "Core300S", "Luft", "2.0.00"],
            # FRITZ's tracker copies the client's version; it must not count as a second plug
            ["FRITZ!", "FRITZ!Box Tracked device", "TV", "1.4.6"], ["FRITZ!", "FRITZ!Box Tracked device", "x", "1.0"],
            [None, None, "Sun", None],
        ]
        got = updates.firmware_drift(devices)
        self.assertEqual(len(got), 1)
        maker, model, newest, behind = got[0]
        self.assertEqual((maker, model, updates._short(newest)), ("TP-Link", "P110", "1.4.8"))
        self.assertEqual([n for n, _sw in behind], ["Kühlschrank", "PC"])

    def test_fritz_versions(self):
        xml = ("<j:BoxInfo><j:Name>FRITZ!Repeater 1200 AX</j:Name><j:HW>268</j:HW>"
               "<j:Version>268.08.25</j:Version></j:BoxInfo>")
        self.assertEqual(updates.parse_boxinfo(xml), ("FRITZ!Repeater 1200 AX", "08.25"))
        self.assertIsNone(updates.parse_boxinfo("<html>login</html>"))
        self.assertEqual(updates.avm_listing_url("FRITZ!Repeater 1200 AX"),
                         "https://download.avm.de/fritzwlan/fritzrepeater-1200-ax/deutschland/fritz.os/")
        self.assertEqual(updates.avm_listing_url("FRITZ!Box 6660 Cable"),
                         "https://download.avm.de/fritzbox/fritzbox-6660-cable/deutschland/fritz.os/")
        listing = ('<a href="../">../</a><a href="FRITZ.Repeater_1200_AX-07.57.image">x</a>'
                   '<a href="FRITZ.Repeater_1200_AX-08.25.image">x</a><a href="info_de.txt">x</a>')
        self.assertEqual(updates.avm_latest(listing), "08.25")
        self.assertIsNone(updates.avm_latest("<html>404</html>"))

    def test_pi_parse_docker(self):
        out = ("APT 0\nDOCKER homeassistant|ghcr.io/home-assistant/home-assistant:stable|"
               "ghcr.io/home-assistant/home-assistant@sha256:aaa\nDOCKER kuma|louislam/uptime-kuma:1|\n")
        self.assertEqual(updates.parse_pi(out)["docker"],
                         [("homeassistant", "ghcr.io/home-assistant/home-assistant:stable", {"sha256:aaa"}),
                          ("kuma", "louislam/uptime-kuma:1", set())])

    def test_split_image(self):
        self.assertEqual(updates.split_image("ghcr.io/home-assistant/home-assistant:stable"),
                         ("ghcr.io", "home-assistant/home-assistant", "stable"))
        self.assertEqual(updates.split_image("louislam/uptime-kuma:1"), ("registry-1.docker.io", "louislam/uptime-kuma", "1"))
        self.assertEqual(updates.split_image("redis"), ("registry-1.docker.io", "library/redis", "latest"))
        self.assertEqual(updates.split_image("localhost:5000/app"), ("localhost:5000", "app", "latest"))

    def test_docker_rows_only_for_moved_tags(self):
        remote = {"a:latest": "sha256:new", "b:latest": "sha256:same"}
        containers = [("a", "a:latest", {"sha256:old"}), ("b", "b:latest", {"sha256:same"}),
                      ("c", "c@sha256:pinned", set())]
        rows, notes = updates.docker_rows(containers, digest=remote.get)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].startswith("pi:docker:a: "))
        self.assertIn("nur Anzeige", rows[0])
        self.assertEqual(notes, [])

    def test_docker_rows_name_the_ha_version(self):
        with mock.patch.object(updates, "_ha_core_versions", return_value=("2026.9.1", "2026.10.3")):
            rows, _ = updates.docker_rows([("homeassistant", "ghcr.io/home-assistant/home-assistant:stable", {"x"})],
                                          digest=lambda _i: "y")
        self.assertIn("Home Assistant 2026.9.1 → 2026.10.3", rows[0])


if __name__ == "__main__":
    unittest.main()
