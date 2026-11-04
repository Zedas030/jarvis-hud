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
        r = router.route("Spotify to the right")
        self.assertEqual(r["kind"], "local")
        self.assertEqual(self.placed, [(101, "right", 1)])
        self.placed.clear()
        router.route("Browser left and Code right on monitor 2")
        self.assertEqual(self.placed, [(102, "left", 1), (103, "right", 2)])

    def test_unknown_app_goes_to_llm(self):
        self.assertEqual(router.route("Steam to the right")["kind"], "llm")
        self.assertEqual(self.placed, [])

    def test_tool(self):
        out = jarvis_mcp.call_tool("window_arrange", {"app": "spotify", "position": "full", "monitor": 2})
        self.assertIn("full screen on monitor 2", out)
        self.assertIn("I do not know this position", desktop.arrange("spotify", "diagonal"))


class Files(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = desktop.FILE_ROOTS[0]
        (root / "Holiday").mkdir(parents=True, exist_ok=True)
        (root / "Holiday" / "Packing list Mallorca.md").write_text("- Sun cream\n- Golf clubs\n", encoding="utf-8")
        (root / "Customer Contoso offer.txt").write_text("Offer for the customer", encoding="utf-8")
        with zipfile.ZipFile(root / "Letter.docx", "w") as z:
            z.writestr("word/document.xml", "<w:document><w:body><w:p><w:r><w:t>Hello Anna,</w:t></w:r></w:p>"
                                            "<w:p><w:r><w:t>see you Sunday.</w:t></w:r></w:p></w:body></w:document>")
        (TMP / "outside.txt").write_text("secret", encoding="utf-8")

    def test_search(self):
        out = desktop.search("packing list")
        self.assertIn("Packing list Mallorca.md", out)
        self.assertNotIn("Contoso", desktop.search("offer"))          # work stays out

    def test_read(self):
        out = desktop.read(str(desktop.FILE_ROOTS[0] / "Holiday" / "Packing list Mallorca.md"))
        self.assertIn("Golf clubs", out)
        self.assertEqual(desktop.read(str(desktop.FILE_ROOTS[0] / "Letter.docx")).strip(), "Hello Anna,\nsee you Sunday.")
        self.assertIn("Refused", desktop.read(str(TMP / "outside.txt")))
        self.assertIn("Refused", desktop.read(str(desktop.FILE_ROOTS[0] / ".." / "outside.txt")))
        self.assertIn("work", desktop.read(str(desktop.FILE_ROOTS[0] / "Customer Contoso offer.txt")))

    def test_file_content_is_untrusted(self):
        jarvis_mcp.TAINTED.update(on=False, by="")
        out = jarvis_mcp.handle_call("file_read", {"path": str(desktop.FILE_ROOTS[0] / "Holiday" / "Packing list Mallorca.md")})
        self.assertTrue(out.startswith("«THIRD-PARTY DATA from file_read"))
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
                        "$g.DrawString('Build failed',$f,[System.Drawing.Brushes]::Black,10,30); "
                        f"$b.Save('{img}')"], check=True, timeout=60)
        out = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                              str(desktop.OCR_PS), "-ImagePath", str(img)],
                             capture_output=True, text=True, encoding="utf-8", timeout=60).stdout
        self.assertIn("Build failed", out)

    def test_sensitive_window_refused(self):
        old_w, old_fg = desktop.windows, desktop._foreground_title
        desktop.windows = lambda: WINS
        desktop._foreground_title = lambda: (104, "KeePassXC")
        try:
            self.assertIn("Refused", desktop.screen_text())
            desktop._foreground_title = lambda: (101, "Spotify Premium")
            self.assertIn("Refused", desktop.screen_text(whole=True))   # KeePass is visible somewhere
        finally:
            desktop.windows, desktop._foreground_title = old_w, old_fg

    def test_only_routed_on_request(self):
        self.assertIn("screen", router.llm_route("What does the error message on my screen say?")["groups"])
        self.assertNotIn("screen", router.llm_route("Explain to me how weather fronts form")["groups"])


class Scripts(unittest.TestCase):
    def setUp(self):
        script = TMP / "hello.bat"
        script.write_text("@echo Hello from Jarvis\r\n", encoding="utf-8")
        desktop.SCRIPTS.write_text(json.dumps({"hello": {"path": str(script), "description": "Test", "timeout": 20}}),
                                   encoding="utf-8")

    def test_only_allowed(self):
        self.assertIn("hello: Test", desktop.script_list())
        self.assertEqual(desktop.script_run("format c"), "This script is not allowed.")

    @unittest.skipUnless(os.name == "nt", "cmd.exe")
    def test_run(self):
        out = desktop.script_run("hello")
        self.assertIn("hello done", out)
        self.assertIn("Hello from Jarvis", out)

    def test_needs_card(self):
        import runtime
        self.assertNotIn("script_run", runtime.NO_CONFIRM)


if __name__ == "__main__":
    unittest.main()
