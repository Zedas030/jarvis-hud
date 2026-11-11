"""
Memory index (memory_index.py), saving facts after a conversation (reflection.remember) and the NAS store
(memory_nas.py) — vault in the temp folder, NAS faked.

    .venv\\Scripts\\python.exe -m unittest tests.test_memory_index -v
"""
import isolate  # noqa: F401  (must come first)
import os
import shutil
import time
import unittest
from unittest import mock

import memory
import memory_index as mi
import memory_nas
import reflection


def write(rel, text):
    p = memory.VAULT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


DIARY = ("---\ntype: conversation\nupdated: 2026-01-05\n---\n\n# Conversation 2026-01-05\n\n"
         "**09:12 · Sir:** My golf club fitting is at Fairway Corner\n\n**Jarvis:** Noted, Sir.\n"
         "\n**09:20 · Sir:** (work topic, not logged)\n\n**Jarvis:** (not logged)\n")


class Index(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(memory.VAULT, ignore_errors=True)
        try:
            os.remove(mi.PATH)
        except OSError:
            pass
        mi._LAST["scan"] = 0.0

    def test_notes_and_turns_are_found(self):
        write("Memories/Golf.md", "---\ntype: memory\n---\n\n# Golf\n\n- Handicap 18\n")
        write("Conversations/Conversation 2026-01-05.md", DIARY)
        mi.refresh(force=True)
        self.assertEqual(mi.stats(), {"note": 1, "turn": 1})        # the work exchange stays out
        hits = mi.search("Fitting Fairway")
        self.assertEqual(hits[0]["kind"], "turn")
        self.assertEqual(hits[0]["day"], "2026-01-05")
        self.assertIn("Handicap", mi.search("handicap")[0]["snippet"])

    def test_old_conversations_survive_the_diary_cleanup(self):
        diary = write("Conversations/Conversation 2026-01-05.md", DIARY)
        note = write("Memories/Knowledge.md", "# Knowledge\n\n- The basement is damp\n")
        mi.refresh(force=True)
        diary.unlink()
        note.unlink()
        mi.refresh(force=True)
        self.assertTrue(mi.search("Fairway"))                        # exchange kept
        self.assertFalse(mi.search("basement"))                      # deleted note gone

    def test_changed_note_is_reindexed(self):
        p = write("Memories/Profile.md", "# Profile\n\n- lives by the lake\n")
        mi.refresh(force=True)
        time.sleep(0.05)
        p.write_text("# Profile\n\n- lives in the forest\n", encoding="utf-8")
        os.utime(p, (time.time() + 5, time.time() + 5))
        mi.refresh(force=True)
        self.assertFalse(mi.search("lake"))
        self.assertTrue(mi.search("forest"))

    def test_diacritics_and_prefixes(self):
        write("Memories/Preferences.md", "# Preferences\n\n- Likes crème brûlée\n")
        self.assertTrue(mi.search("creme brulee"))
        self.assertTrue(mi.search("Crè"))


class Remember(unittest.TestCase):
    def test_only_allowed_notes_and_no_work(self):
        calls = []
        with mock.patch("jarvis_mcp._memory", lambda name, a: calls.append(a) or "Saved."), \
                mock.patch.object(reflection, "REMEMBER", True):
            saved = reflection.remember([
                {"note": "People/Anna", "text": "Anna's birthday is on 3 May"},
                {"note": "Commands", "text": "On 'hello' all lights off"},            # commands: never by itself
                {"note": "Preferences", "text": "Does not like coriander"},
                "Nonsense",
                {"note": "Knowledge", "text": "The customer wants the offer by Friday"}])  # work word
        notes = [c["note"] for c in calls]
        self.assertEqual(notes[:2], ["People/Anna", "Preferences"])
        self.assertTrue(all(c["source"] == "inferred" for c in calls))
        self.assertEqual(len(saved), len(calls))

    def test_log_names_what_was_kept(self):
        with mock.patch("time.strftime", return_value="2026-11-10"):
            n = reflection.append_log({}, when="2026-11-10 12:00",
                                      saved=[("People/Anna", "Anna likes tulips", "Saved."),
                                             ("Profile", "lives in Springfield", "Not saved — possible contradiction")])
        body = (memory.MEMORY_DIR / "Learning log.md").read_text(encoding="utf-8")
        self.assertEqual(n, 2)
        self.assertIn("- Saved ([[Anna]]): Anna likes tulips", body)
        self.assertIn("- Not saved ([[Profile]])", body)


class Nas(unittest.TestCase):
    def test_off_without_login(self):
        with mock.patch.object(memory_nas, "URL", ""):
            self.assertFalse(memory_nas.enabled())
            self.assertIn("off", memory_nas.sync())

    def test_prune_keeps_newest(self):
        seen = {}
        with mock.patch.object(memory_nas, "KEEP", 2), \
                mock.patch.object(memory_nas, "_call", lambda path, params, sid=None, timeout=0: seen.update(params)):
            old = memory_nas._prune("sid", ["memory_index.sqlite", "vault-2026-11-01.zip",
                                            "vault-2026-11-02.zip", "vault-2026-11-03.zip"])
        self.assertEqual(old, ["vault-2026-11-01.zip"])
        self.assertIn("vault-2026-11-01.zip", seen["path"])


if __name__ == "__main__":
    unittest.main()
