"""
"Hol die Musik ins Wohnzimmer": the soundbar is the Spotify device of the room; when Spotify does not list
it, Jarvis asks again a few times before it settles for the worse speaker (the Echo Dot alone). No Spotify,
no house.

    .venv\\Scripts\\python.exe -m unittest tests.test_music_room -v
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import jarvis_mcp        # noqa: E402
import speakers          # noqa: E402
import spotify           # noqa: E402

PROF = {"speakers": {"Wohnzimmer": {"notify": "notify.alexa_media_nutzer_echo_dot", "spotify": "Wohnzimmer",
                                    "spotify_fallback": "Nutzer' Echo Dot"},
                     "Schlafzimmer": {"player": "media_player.schlafzimmer"}}}
MISSING = "Spotify kennt kein Gerät „Wohnzimmer“. Gerade erreichbar: Nutzer' Echo Dot."


def prof():
    return mock.patch("jarvis_profile.ha", side_effect=lambda k, d=None: PROF.get(k, d))


class Fallback(unittest.TestCase):
    def setUp(self):
        self.patches = [prof(), mock.patch.object(spotify, "configured", return_value=True),
                        mock.patch.object(jarvis_mcp.time, "sleep"), mock.patch.object(jarvis_mcp, "MUSIC_ROOM_TRIES", 3)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_config(self):
        self.assertEqual(speakers.spotify_device("Wohnzimmer"), ("Wohnzimmer", "Wohnzimmer", ""))
        self.assertEqual(speakers.spotify_fallback("wohnzimmer"), "Nutzer' Echo Dot")
        self.assertEqual(speakers.spotify_fallback("Schlafzimmer"), "")
        self.assertEqual(speakers.spotify_fallback("Keller"), "")

    def test_soundbar_first_time(self):
        with mock.patch.object(spotify, "transfer", return_value=(True, "Wohnzimmer")) as t:
            self.assertEqual(jarvis_mcp._music_room("Wohnzimmer"), "Die Musik läuft jetzt im Wohnzimmer.")
        self.assertEqual([c.args[0] for c in t.call_args_list], ["Wohnzimmer"])

    def test_soundbar_back_on_the_second_try(self):
        answers = iter([(False, MISSING), (True, "Wohnzimmer")])
        with mock.patch.object(spotify, "transfer", side_effect=lambda n: next(answers)) as t:
            self.assertEqual(jarvis_mcp._music_room("Wohnzimmer"), "Die Musik läuft jetzt im Wohnzimmer.")
        self.assertEqual([c.args[0] for c in t.call_args_list], ["Wohnzimmer", "Wohnzimmer"])
        self.assertEqual(jarvis_mcp.time.sleep.call_count, 1)

    def test_echo_dot_only_after_three_tries(self):
        answers = iter([(False, MISSING)] * 3 + [(True, "Nutzer' Echo Dot")])
        with mock.patch.object(spotify, "transfer", side_effect=lambda n: next(answers)) as t:
            text = jarvis_mcp._music_room("Wohnzimmer")
        self.assertEqual([c.args[0] for c in t.call_args_list], ["Wohnzimmer"] * 3 + ["Nutzer' Echo Dot"])
        self.assertIn("ersatzweise", text)
        self.assertIn("Nutzer' Echo Dot", text)
        self.assertEqual(jarvis_mcp.time.sleep.call_count, 2)

    def test_other_errors_do_not_retry(self):
        with mock.patch.object(spotify, "transfer", return_value=(False, "Spotify antwortet nicht (timeout).")) as t:
            self.assertEqual(jarvis_mcp._music_room("Wohnzimmer"), "Spotify antwortet nicht (timeout).")
        self.assertEqual(t.call_count, 1)

    def test_room_without_fallback_asks_once(self):
        with mock.patch.object(spotify, "transfer", return_value=(False, MISSING.replace("Wohnzimmer", "Schlafzimmer"))) as t, \
                mock.patch.object(jarvis_mcp, "_music_room_ha", return_value=False):
            self.assertIn("nicht erreichbar", jarvis_mcp._music_room("Schlafzimmer"))
        self.assertEqual(t.call_count, 1)


if __name__ == "__main__":
    unittest.main()
