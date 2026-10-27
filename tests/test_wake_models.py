"""
Wake word models on the iPad: which one decides, which run alongside, and that the browser
worker, the server and the download script agree on the files. No microphone, no network.

    .venv\\Scripts\\python.exe -m unittest tests.test_wake_models -v
"""
import isolate  # noqa: F401  (must come first)
import importlib.util
import pathlib
import re
import tempfile
import unittest

import wakeword

ROOT = pathlib.Path(__file__).resolve().parents[1]


def fetch_script():
    spec = importlib.util.spec_from_file_location("fetch_wakeword", ROOT / "scripts" / "fetch_wakeword.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LocalModels(unittest.TestCase):
    def setUp(self):
        self.saved = (wakeword.LOCAL_MODEL, wakeword.VENDOR_OWW)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        wakeword.VENDOR_OWW = self.tmp

    def tearDown(self):
        wakeword.LOCAL_MODEL, wakeword.VENDOR_OWW = self.saved

    def fetched(self, *names):
        for n in names:
            (self.tmp / wakeword.LOCAL_FILES[n]).write_bytes(b"x")

    def test_deciding_model_first_then_what_was_fetched(self):
        wakeword.LOCAL_MODEL = "jarvis_v2"
        self.fetched("hey_jarvis", "jarvis_v1", "jarvis_v2")
        self.assertEqual([n for n, _ in wakeword.local_models()], ["jarvis_v2", "hey_jarvis", "jarvis_v1"])
        self.assertEqual(wakeword.status()["local_models"][0], "jarvis_v2")

    def test_missing_extras_are_left_out(self):
        wakeword.LOCAL_MODEL = "hey_jarvis"
        self.fetched("hey_jarvis")
        self.assertEqual(wakeword.local_models(), [("hey_jarvis", "hey_jarvis_v0.1.onnx")])

    def test_unknown_name_falls_back(self):
        import os
        import importlib
        os.environ["JARVIS_WAKE_LOCAL_MODEL"] = "../../secret"
        try:
            importlib.reload(wakeword)
            self.assertEqual(wakeword.LOCAL_MODEL, "hey_jarvis")
        finally:
            del os.environ["JARVIS_WAKE_LOCAL_MODEL"]
            importlib.reload(wakeword)


class SameFilesEverywhere(unittest.TestCase):
    def test_worker_server_and_download_agree(self):
        js = (ROOT / "ui" / "wakeword.js").read_text(encoding="utf-8")
        worker = dict(re.findall(r"(\w+): '([\w.]+\.onnx)'", re.search(r"var FILES = \{([^}]*)\}", js).group(1)))
        self.assertEqual(worker, wakeword.LOCAL_FILES)
        f = fetch_script()
        downloaded = set(f.OWW_FILES) | set(f.COMMUNITY_FILES)
        self.assertLessEqual(set(wakeword.LOCAL_FILES.values()), downloaded)

    def test_every_download_is_pinned_and_checked(self):
        f = fetch_script()
        self.assertRegex(f.COMMUNITY, r"^[0-9a-f]{40}$")          # a commit, never a moving branch
        for sha in list(f.OWW_FILES.values()) + list(f.COMMUNITY_FILES.values()):
            self.assertRegex(sha, r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
