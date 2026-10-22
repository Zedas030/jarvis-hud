"""
Windows package: window arranging (faked windows), files in shared folders,
screen OCR on a generated image, allow-listed scripts. Never touches the real desktop.

    .venv\\Scripts\\python.exe -m unittest tests.test_desktop -v
"""
import json
import os
import pathlib
import subprocess
import sys
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402  (must come before any Jarvis module)

import desktop      # noqa: E402
import jarvis_mcp   # noqa: E402
import router       # noqa: E402

WINS = [(101, "Spotify Premium", "spotify.exe"), (102, "Jarvis – Opera", "opera.exe"),
        (103, "main.py - jarvis - Visual Studio Code", "code.exe"), (104, "KeePassXC", "keepassxc.exe")]


class Windows(unittest.TestCase):
    def setUp(self):
        self._w, self._p, self._mon = desktop.windows, desktop.place, desktop.monitors
        self.placed = []
        desktop.windows = lambda: WINS
        desktop.place = lambda hwnd, pos, mon=1: self.placed.append((hwnd, pos, mon)) or True
        desktop.monitors = lambda: [(0, 0, 2560, 1392), (2560, 0, 5120, 1392)]

    def tearDown(self):
        desktop.windows, desktop.place, desktop.monitors = self._w, self._p, self._mon

    def test_find(self):
        self.assertEqual(desktop.find_window("spotify"), 101)
        self.assertEqual(desktop.find_window("browser"), 102)
        self.assertEqual(desktop.find_window("vs code"), 103)
        self.assertIsNone(desktop.find_window("steam"))

    def test_local_voice_commands(self):
        r = router.route("Spotify nach rechts")
        self.assertEqual(r["kind"], "local")
        self.assertEqual(self.placed, [(101, "rechts", 1)])
        self.placed.clear()
        router.route("Browser links und Code rechts auf Monitor 2")
        self.assertEqual(self.placed, [(102, "links", 1), (103, "rechts", 2)])

    def test_unknown_app_goes_to_llm(self):
        self.assertEqual(router.route("Steam nach rechts")["kind"], "llm")
        self.assertEqual(self.placed, [])

    def test_tool(self):
        out = jarvis_mcp.call_tool("window_arrange", {"app": "spotify", "position": "voll", "monitor": 2})
        self.assertIn("im Vollbild auf Monitor 2", out)
        self.assertIn("kenne ich nicht", desktop.arrange("spotify", "diagonal"))


class Files(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = desktop.FILE_ROOTS[0]
        (root / "Urlaub").mkdir(parents=True, exist_ok=True)
        (root / "Urlaub" / "Packliste Mallorca.md").write_text("- Sonnencreme\n- Golfschläger\n", encoding="utf-8")
        (root / "Kunde Contoso Angebot.txt").write_text("Angebot für den Kunden", encoding="utf-8")
        with zipfile.ZipFile(root / "Brief.docx", "w") as z:
            z.writestr("word/document.xml", "<w:document><w:body><w:p><w:r><w:t>Hallo Anna,</w:t></w:r></w:p>"
                                            "<w:p><w:r><w:t>bis Sonntag.</w:t></w:r></w:p></w:body></w:document>")
        (TMP / "outside.txt").write_text("geheim", encoding="utf-8")

    def test_search(self):
        out = desktop.search("packliste")
        self.assertIn("Packliste Mallorca.md", out)
        self.assertNotIn("Contoso", desktop.search("angebot"))          # work stays out

    def test_read(self):
        out = desktop.read(str(desktop.FILE_ROOTS[0] / "Urlaub" / "Packliste Mallorca.md"))
        self.assertIn("Golfschläger", out)
        self.assertEqual(desktop.read(str(desktop.FILE_ROOTS[0] / "Brief.docx")).strip(), "Hallo Anna,\nbis Sonntag.")
        self.assertIn("Abgelehnt", desktop.read(str(TMP / "outside.txt")))
        self.assertIn("Abgelehnt", desktop.read(str(desktop.FILE_ROOTS[0] / ".." / "outside.txt")))
        self.assertIn("Arbeit", desktop.read(str(desktop.FILE_ROOTS[0] / "Kunde Contoso Angebot.txt")))

    def test_file_content_is_untrusted(self):
        jarvis_mcp.TAINTED.update(on=False, by="")
        out = jarvis_mcp.handle_call("file_read", {"path": str(desktop.FILE_ROOTS[0] / "Urlaub" / "Packliste Mallorca.md")})
        self.assertTrue(out.startswith("«FREMDE DATEN aus file_read"))
        self.assertTrue(jarvis_mcp.TAINTED["on"])
        jarvis_mcp.TAINTED.update(on=False, by="")


@unittest.skipUnless(os.name == "nt", "Windows OCR")
class Screen(unittest.TestCase):
    def test_ocr_on_generated_image(self):
        img = TMP / "ocr.png"
        subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                        "Add-Type -AssemblyName System.Drawing; $b=New-Object System.Drawing.Bitmap 700,120; "
                        "$g=[System.Drawing.Graphics]::FromImage($b); $g.Clear([System.Drawing.Color]::White); "
                        "$f=New-Object System.Drawing.Font('Segoe UI',26); "
                        "$g.DrawString('Build fehlgeschlagen',$f,[System.Drawing.Brushes]::Black,10,30); "
                        f"$b.Save('{img}')"], check=True, timeout=60)
        out = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                              str(desktop.OCR_PS), "-ImagePath", str(img)],
                             capture_output=True, text=True, encoding="utf-8", timeout=60).stdout
        self.assertIn("Build fehlgeschlagen", out)

    def test_sensitive_window_refused(self):
        old_w, old_fg = desktop.windows, desktop._foreground_title
        desktop.windows = lambda: WINS
        desktop._foreground_title = lambda: (104, "KeePassXC")
        try:
            self.assertIn("Abgelehnt", desktop.screen_text())
            desktop._foreground_title = lambda: (101, "Spotify Premium")
            self.assertIn("Abgelehnt", desktop.screen_text(whole=True))   # KeePass is visible somewhere
        finally:
            desktop.windows, desktop._foreground_title = old_w, old_fg

    def test_only_routed_on_request(self):
        self.assertIn("screen", router.llm_route("Was steht in der Fehlermeldung auf meinem Bildschirm?")["groups"])
        self.assertNotIn("screen", router.llm_route("Erklär mir, wie Wetterfronten entstehen")["groups"])


class Scripts(unittest.TestCase):
    def setUp(self):
        script = TMP / "hallo.bat"
        script.write_text("@echo Hallo von Jarvis\r\n", encoding="utf-8")
        desktop.SCRIPTS.write_text(json.dumps({"hallo": {"path": str(script), "beschreibung": "Test", "timeout": 20}}),
                                   encoding="utf-8")

    def test_only_allowed(self):
        self.assertIn("hallo: Test", desktop.script_list())
        self.assertEqual(desktop.script_run("format c"), "Dieses Skript ist nicht freigegeben.")

    @unittest.skipUnless(os.name == "nt", "cmd.exe")
    def test_run(self):
        out = desktop.script_run("hallo")
        self.assertIn("hallo fertig", out)
        self.assertIn("Hallo von Jarvis", out)

    def test_needs_card(self):
        import runtime
        self.assertNotIn("script_run", runtime.NO_CONFIRM)


if __name__ == "__main__":
    unittest.main()
