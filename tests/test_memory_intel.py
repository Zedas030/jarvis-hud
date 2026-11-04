"""
Memory intelligence: duplicates, contradictions (never overwritten), links to
people/projects/devices, expiry, review.

    .venv\\Scripts\\python.exe -m unittest tests.test_memory_intel -v
"""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import jarvis_mcp          # noqa: E402
import memory              # noqa: E402
import memory_intel as mi  # noqa: E402
import memory_tools as mt  # noqa: E402


def save(text, note="Knowledge", **kw):
    return jarvis_mcp._memory("memory_save", dict(note=note, text=text, **kw))


class Compare(unittest.TestCase):
    def test_kinds(self):
        cases = [("Birthday on 3 May", ["Birthday on 5 May"], "conflict"),
                 ("Lives in Bonn", ["Lives in Cologne _(said, 2026-10-01)_"], "conflict"),
                 ("Does not like coriander", ["Likes coriander"], "conflict"),
                 ("Likes short answers", ["Likes short answers _(said, 2026-10-01)_"], "duplicate"),
                 ("Likes coffee black", ["Likes coffee"], "related")]
        for new, old, kind in cases:
            self.assertEqual(mi.compare(new, old)[0], kind, new)
        for new, old in (("Likes golf", ["Likes jazz"]), ("NAS has IP 192.0.2.20", ["Pi has IP 192.0.2.10"])):
            self.assertIsNone(mi.compare(new, old), new)


class Save(unittest.TestCase):
    def setUp(self):
        for name in ("Knowledge", "People/Anna", "Projects/Move"):
            p = memory.MEMORY_DIR / f"{name}.md"
            if p.exists():
                p.unlink()

    def test_conflict_is_never_overwritten(self):
        save("Lives in Cologne", source="said")
        out = save("Lives in Bonn", source="inferred")
        self.assertIn("possible contradiction", out)
        self.assertIn("Ask the user", out)
        body = (memory.MEMORY_DIR / "Knowledge.md").read_text(encoding="utf-8")
        self.assertIn("Lives in Cologne", body)
        self.assertNotIn("Bonn", body)
        self.assertEqual(save("Lives in Bonn", source="said", replace="Lives in Cologne"), "Saved.")
        self.assertIn("Lives in Bonn", (memory.MEMORY_DIR / "Knowledge.md").read_text(encoding="utf-8"))

    def test_duplicate_and_related(self):
        save("Likes coffee", source="said")
        self.assertIn("Already in there", save("likes coffee"))
        self.assertIn("adds to “Likes coffee”", save("Likes coffee black", source="said"))

    def test_links(self):
        save("Birthday in May", note="People/Anna", source="said")
        save("Get boxes", note="Projects/Move", source="said")
        save("Anna helps with the Move", source="said")
        body = (memory.MEMORY_DIR / "Knowledge.md").read_text(encoding="utf-8")
        self.assertIn("[[Anna]] helps with the [[Move]]", body)
        anna = (memory.MEMORY_DIR / "People" / "Anna.md").read_text(encoding="utf-8")
        self.assertNotIn("[[Anna]]", anna)                     # no link to itself

    def test_guessed_and_expiry(self):
        save("Maybe plays tennis", source="guessed")
        save("Is on holiday", source="said", until="2026-10-30")
        bullets = mt._bullets(memory.MEMORY_DIR / "Knowledge.md")
        self.assertEqual(mt.for_prompt(bullets[0]), "Maybe plays tennis (guess)")
        self.assertEqual(mt.expiry(bullets[1]), dt.date(2026, 10, 30))
        self.assertEqual(mt.expire_bullets(today=dt.date(2026, 10, 29)), 0)
        self.assertEqual(mt.expire_bullets(today=dt.date(2026, 10, 31)), 1)
        self.assertNotIn("holiday", (memory.MEMORY_DIR / "Knowledge.md").read_text(encoding="utf-8"))

    def test_review(self):
        (memory.MEMORY_DIR / "Knowledge.md").write_text(
            "# Knowledge\n\n- Lives in Cologne _(said, 2026-10-01)_\n- Lives in Bonn _(inferred, 2026-10-02)_\n"
            "- Likes tea\n- Likes tea\n", encoding="utf-8")
        out = mi.review()
        self.assertIn("Contradiction (a property there is only one of)", out)
        self.assertIn("Duplicate", out)


if __name__ == "__main__":
    unittest.main()
