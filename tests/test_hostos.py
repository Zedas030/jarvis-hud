"""
Platform switch (hostos.py) — both branches, nothing is started or opened for real.

    .venv\\Scripts\\python.exe -m unittest tests.test_hostos -v
"""
import isolate  # noqa: F401  (must come first)
import os
import pathlib
import re
import subprocess
import unittest

import hostos
import memory

ROOT = pathlib.Path(__file__).resolve().parents[1]


class Branch:
    """Pretends to run on the other system for the length of a with-block."""
    def __init__(self, win):
        self.win = win

    def __enter__(self):
        self.saved = hostos.IS_WIN
        hostos.IS_WIN = self.win

    def __exit__(self, *exc):
        hostos.IS_WIN = self.saved


class HostOS(unittest.TestCase):
    def test_this_system_behaves_as_before(self):
        # what every module passed before the switch
        if os.name == "nt":
            self.assertEqual(hostos.QUIET, {"creationflags": subprocess.CREATE_NO_WINDOW})
            self.assertEqual(hostos.DETACHED, {"creationflags": subprocess.DETACHED_PROCESS})
        else:
            self.assertEqual(hostos.QUIET, {})

    def test_venv_python(self):
        with Branch(True):
            self.assertEqual(hostos.venv_python("/r", ".venv-tts"), str(pathlib.Path("/r/.venv-tts/Scripts/python.exe")))
        with Branch(False):
            self.assertEqual(hostos.venv_python("/r"), str(pathlib.Path("/r/.venv/bin/python")))

    def test_ping(self):
        with Branch(True):
            self.assertEqual(hostos.ping_args("10.0.0.1"), ["ping", "-n", "1", "-w", "1500", "10.0.0.1"])
        with Branch(False):
            self.assertEqual(hostos.ping_args("10.0.0.1"), ["ping", "-c", "1", "-w", "2", "10.0.0.1"])

    def test_shell_tool(self):
        with Branch(True):
            self.assertEqual(hostos.shell_tool(), "PowerShell")
        with Branch(False):
            self.assertEqual(hostos.shell_tool(), "Bash")

    def test_open_target(self):
        opened, started = [], []
        real = (getattr(os, "startfile", None), subprocess.Popen)
        os.startfile = opened.append
        subprocess.Popen = lambda args, **kw: started.append((args, kw))
        try:
            with Branch(True):
                hostos.open_target("spotify:")
            with Branch(False):
                hostos.open_target("https://example.org")
        finally:
            if real[0] is None:
                del os.startfile
            else:
                os.startfile = real[0]
            subprocess.Popen = real[1]
        self.assertEqual(opened, ["spotify:"])
        self.assertEqual(started[0][0], ["xdg-open", "https://example.org"])

    def test_no_platform_checks_outside_hostos(self):
        # the rule from PORTING.md: new Windows calls go through hostos
        pattern = re.compile(r'getattr\(subprocess, "(CREATE_NO_WINDOW|DETACHED_PROCESS)"|os\.startfile\(|"Scripts", "python\.exe"')
        hits = [f"{p.relative_to(ROOT)}:{i}" for p in (ROOT / "jarvis").rglob("*.py") if p.name != "hostos.py"
                for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if pattern.search(line)]
        self.assertEqual(hits, [])


class NoteNames(unittest.TestCase):
    def test_existing_note_found_whatever_the_case(self):
        path = memory.MEMORY_DIR / "Projekte" / "Umzug Test.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
        self.addCleanup(path.unlink)
        found = memory.note_path("projekte/umzug test")
        self.assertTrue(found.exists())
        self.assertTrue(os.path.samefile(found, path))

    def test_new_note_keeps_its_name(self):
        self.assertEqual(memory.note_path("Neu/Idee"), memory.MEMORY_DIR / "Neu" / "Idee.md")

    def test_memory_save_writes_into_the_existing_note(self):
        import jarvis_mcp
        path = memory.MEMORY_DIR / "Testnotiz Hostos.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\ntype: wissen\n---\n\n- alt\n", encoding="utf-8")
        self.addCleanup(lambda: path.exists() and path.unlink())
        jarvis_mcp.call_tool("memory_save", {"note": "test note hostos", "text": "New entry for testing",
                                             "quelle": "gesagt"})
        same = [p for p in memory.MEMORY_DIR.glob("*.md") if p.stem.lower() == "test note hostos"]
        self.assertEqual(len(same), 1)
        self.assertIn("New entry for testing", same[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
