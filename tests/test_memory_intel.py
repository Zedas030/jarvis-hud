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


def save(text, note="Wissen", **kw):
    return jarvis_mcp._memory("memory_save", dict(note=note, text=text, **kw))


class Compare(unittest.TestCase):
    def test_kinds(self):
        cases = [("Geburtstag am 3. Mai", ["Geburtstag am 5. Mai"], "conflict"),
                 ("Wohnt in Bonn", ["Wohnt in Köln _(gesagt, 01.10.2026)_"], "conflict"),
                 ("Mag keinen Koriander", ["Mag Koriander"], "conflict"),
                 ("Mag kurze Antworten", ["Mag kurze Antworten _(gesagt, 01.10.2026)_"], "duplicate"),
                 ("Mag Kaffee schwarz", ["Mag Kaffee"], "related")]
        for new, old, kind in cases:
            self.assertEqual(mi.compare(new, old)[0], kind, new)
        for new, old in (("Mag Golf", ["Mag Jazz"]), ("NAS hat IP <nas-ip>", ["Pi hat IP <pi-ip>"])):
            self.assertIsNone(mi.compare(new, old), new)


class Save(unittest.TestCase):
    def setUp(self):
        for name in ("Wissen", "Personen/Anna", "Projekte/Umzug"):
            p = memory.MEMORY_DIR / f"{name}.md"
            if p.exists():
                p.unlink()

    def test_conflict_is_never_overwritten(self):
        save("Wohnt in Köln", quelle="gesagt")
        out = save("Wohnt in Bonn", quelle="abgeleitet")
        self.assertIn("möglicher Widerspruch", out)
        self.assertIn("Frag den User", out)
        body = (memory.MEMORY_DIR / "Wissen.md").read_text(encoding="utf-8")
        self.assertIn("Wohnt in Köln", body)
        self.assertNotIn("Bonn", body)
        self.assertEqual(save("Wohnt in Bonn", quelle="gesagt", replace="Wohnt in Köln"), "Gespeichert.")
        self.assertIn("Wohnt in Bonn", (memory.MEMORY_DIR / "Wissen.md").read_text(encoding="utf-8"))

    def test_duplicate_and_related(self):
        save("Mag Kaffee", quelle="gesagt")
        self.assertIn("Steht so schon drin", save("mag Kaffee"))
        self.assertIn("ergänzt „Mag Kaffee“", save("Mag Kaffee schwarz", quelle="gesagt"))

    def test_links(self):
        save("Geburtstag im Mai", note="Personen/Anna", quelle="gesagt")
        save("Kartons besorgen", note="Projekte/Umzug", quelle="gesagt")
        save("Anna hilft beim Umzug", quelle="gesagt")
        body = (memory.MEMORY_DIR / "Wissen.md").read_text(encoding="utf-8")
        self.assertIn("[[Anna]] hilft beim [[Umzug]]", body)
        anna = (memory.MEMORY_DIR / "Personen" / "Anna.md").read_text(encoding="utf-8")
        self.assertNotIn("[[Anna]]", anna)                     # no link to itself

    def test_vermutet_and_expiry(self):
        save("Spielt vielleicht Tennis", quelle="vermutet")
        save("Ist im Urlaub", quelle="gesagt", bis="2026-10-30")
        bullets = mt._bullets(memory.MEMORY_DIR / "Wissen.md")
        self.assertEqual(mt.for_prompt(bullets[0]), "Spielt vielleicht Tennis (Vermutung)")
        self.assertEqual(mt.expiry(bullets[1]), dt.date(2026, 10, 30))
        self.assertEqual(mt.expire_bullets(today=dt.date(2026, 10, 29)), 0)
        self.assertEqual(mt.expire_bullets(today=dt.date(2026, 10, 31)), 1)
        self.assertNotIn("Urlaub", (memory.MEMORY_DIR / "Wissen.md").read_text(encoding="utf-8"))

    def test_review(self):
        (memory.MEMORY_DIR / "Wissen.md").write_text(
            "# Wissen\n\n- Wohnt in Köln _(gesagt, 01.10.2026)_\n- Wohnt in Bonn _(abgeleitet, 02.10.2026)_\n"
            "- Mag Tee\n- Mag Tee\n", encoding="utf-8")
        out = mi.review()
        self.assertIn("Widerspruch (Eigenschaft, die es nur einmal gibt)", out)
        self.assertIn("Doppelt", out)


if __name__ == "__main__":
    unittest.main()
