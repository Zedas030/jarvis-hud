"""
"Bring the music into the living room": the soundbar is the Spotify device of the room; when Spotify does not list
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

PROF = {"speakers": {"Living room": {"notify": "notify.alexa_media_user_echo_dot", "spotify": "Living room",
                                     "spotify_fallback": "User's Echo Dot"},
                     "Bedroom": {"player": "media_player.bedroom"}}}
MISSING = "Spotify knows no device “Living room”. Reachable right now: User's Echo Dot."


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
        self.assertEqual(speakers.spotify_device("Living room"), ("Living room", "Living room", ""))
        self.assertEqual(speakers.spotify_fallback("living room"), "User's Echo Dot")
        self.assertEqual(speakers.spotify_fallback("Bedroom"), "")
        self.assertEqual(speakers.spotify_fallback("Cellar"), "")

    def test_soundbar_first_time(self):
        with mock.patch.object(spotify, "transfer", return_value=(True, "Living room")) as t:
            self.assertEqual(jarvis_mcp._music_room("Living room"), "The music is now playing in the Living room.")
        self.assertEqual([c.args[0] for c in t.call_args_list], ["Living room"])

    def test_soundbar_back_on_the_second_try(self):
        answers = iter([(False, MISSING), (True, "Living room")])
        with mock.patch.object(spotify, "transfer", side_effect=lambda n: next(answers)) as t:
            self.assertEqual(jarvis_mcp._music_room("Living room"), "The music is now playing in the Living room.")
        self.assertEqual([c.args[0] for c in t.call_args_list], ["Living room", "Living room"])
        self.assertEqual(jarvis_mcp.time.sleep.call_count, 1)

    def test_echo_dot_only_after_three_tries(self):
        answers = iter([(False, MISSING)] * 3 + [(True, "User's Echo Dot")])
        with mock.patch.object(spotify, "transfer", side_effect=lambda n: next(answers)) as t:
            text = jarvis_mcp._music_room("Living room")
        self.assertEqual([c.args[0] for c in t.call_args_list], ["Living room"] * 3 + ["User's Echo Dot"])
        self.assertIn("instead", text)
        self.assertIn("User's Echo Dot", text)
        self.assertEqual(jarvis_mcp.time.sleep.call_count, 2)

    def test_other_errors_do_not_retry(self):
        with mock.patch.object(spotify, "transfer", return_value=(False, "Spotify does not answer (timeout).")) as t:
            self.assertEqual(jarvis_mcp._music_room("Living room"), "Spotify does not answer (timeout).")
        self.assertEqual(t.call_count, 1)

    def test_room_without_fallback_asks_once(self):
        with mock.patch.object(spotify, "transfer", return_value=(False, MISSING.replace("Living room", "Bedroom"))) as t, \
                mock.patch.object(jarvis_mcp, "_music_room_ha", return_value=False):
            self.assertIn("cannot be reached", jarvis_mcp._music_room("Bedroom"))
        self.assertEqual(t.call_count, 1)


if __name__ == "__main__":
    unittest.main()
