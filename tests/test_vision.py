"""
Vision: only on request, card per picture, blocked windows, no tools in the
image call, picture deleted afterwards. Claude and the screen are faked.

    .venv\\Scripts\\python.exe -m unittest tests.test_vision -v
"""
import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import desktop    # noqa: E402
import policy     # noqa: E402
import router     # noqa: E402
import runtime    # noqa: E402
import vision     # noqa: E402


class Patch:
    def __init__(self, obj, **kw):
        self.obj, self.kw, self.old = obj, kw, {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = getattr(self.obj, k)
            setattr(self.obj, k, v)

    def __exit__(self, *x):
        for k, v in self.old.items():
            setattr(self.obj, k, v)


class Rules(unittest.TestCase):
    def test_card_for_every_picture(self):
        self.assertEqual(policy.action_class("screen_describe"), policy.CONFIRM)
        self.assertNotIn("screen_describe", runtime.NO_CONFIRM)
        self.assertIn("screen_describe", policy.UNTRUSTED)
        self.assertIn("Anthropic", policy.describe("mcp__jarvis__screen_describe", {"reason": "x"}))

    def test_only_offered_when_asked_about_the_screen(self):
        self.assertIn("screen", router.llm_route("Where do I find the setting in this window on my screen?")["groups"])
        self.assertNotIn("screen", router.llm_route("Tell me something about golf")["groups"])

    def test_disabled_and_no_claude(self):
        with Patch(vision, ENABLED=False):
            self.assertIn("switched off", vision.describe())
        with Patch(vision, ENABLED=True), Patch(runtime, runtime_kind=lambda: "mock"):
            self.assertIn("needs Claude", vision.describe())

    def test_blocked_windows(self):
        with Patch(desktop, _foreground_title=lambda: (1, "Example Bank online banking"),
                   windows=lambda: [(1, "Example Bank online banking", "opera.exe")]):
            path, refusal = vision.capture()
        self.assertIsNone(path)
        self.assertIn("Refused", refusal)


class Call(unittest.TestCase):
    def test_no_tools_and_image_block(self):
        img = pathlib.Path(tempfile.mkdtemp()) / "s.png"
        img.write_bytes(b"\x89PNG fake")
        seen = {}

        class P:
            returncode = 0
            stdout = json.dumps({"type": "result", "subtype": "success", "result": "A window.", "usage": {}})
            stderr = ""

        def run(cmd, input=None, **kw):
            seen["cmd"], seen["input"] = cmd, json.loads(input)
            return P()
        with Patch(vision.subprocess, run=run):
            self.assertEqual(vision.ask_image(img, "What is that?"), "A window.")
        cmd = seen["cmd"]
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")          # no tools at all
        self.assertNotIn("--allowedTools", cmd)
        blocks = seen["input"]["message"]["content"]
        self.assertEqual(blocks[0]["type"], "image")
        self.assertEqual(blocks[1]["text"], "What is that?")

    def test_picture_deleted_and_marked_as_observation(self):
        folder = pathlib.Path(tempfile.mkdtemp(prefix="jarvis-vision-"))
        img = folder / "screen.png"
        img.write_bytes(b"x")
        with Patch(vision, available=lambda: (True, ""), capture=lambda whole=False: (img, None),
                   ask_image=lambda path, q: "A menu at the top left."):
            out = vision.describe("Where is the menu?")
        self.assertTrue(out.startswith("Observation (from the image, may be wrong)"))
        self.assertFalse(img.exists())
        self.assertFalse(folder.exists())

    def test_failure_still_deletes(self):
        folder = pathlib.Path(tempfile.mkdtemp(prefix="jarvis-vision-"))
        img = folder / "screen.png"
        img.write_bytes(b"x")

        def boom(path, q):
            raise RuntimeError("Limit")
        with Patch(vision, available=lambda: (True, ""), capture=lambda whole=False: (img, None), ask_image=boom):
            self.assertIn("failed", vision.describe())
        self.assertFalse(img.exists())


if __name__ == "__main__":
    unittest.main()
