import isolate  # noqa: F401 — first: temp state, no house

import json
import unittest

import spotify


class FakePlayer:
    """Spotify's player endpoints as far as spotify.py uses them, on one phone."""

    def __init__(self, playing=True, device="phone"):
        self.playing, self.device, self.calls = playing, device, []

    def __call__(self, method, path, retry=True):
        self.calls.append((method, path.split("?")[0]))
        if path == "/me/player":
            if self.device is None:
                return 204, None
            return 200, {"is_playing": self.playing, "device": {"id": self.device}}
        if path.startswith("/me/player/pause"):
            self.playing = False
        elif path.startswith("/me/player/play"):
            self.playing = True
        elif path.startswith(("/me/player/next", "/me/player/previous")):
            self.playing = True                    # Spotify starts the new track
        return 204, None


class Hold(unittest.TestCase):
    def setUp(self):
        self.saved = (spotify._api, spotify.CLIENT_ID)
        spotify.CLIENT_ID = "test"
        spotify.save_token({"refresh_token": "r"})
        spotify.forget()
        self.player = spotify._api = FakePlayer()

    def tearDown(self):
        spotify._api, spotify.CLIENT_ID = self.saved
        spotify.forget()
        spotify.TOKEN_FILE.unlink(missing_ok=True)

    def test_wake_word_pauses_and_plays_on(self):
        self.assertTrue(spotify.hold())
        self.assertFalse(self.player.playing)
        self.assertTrue(spotify.release())
        self.assertTrue(self.player.playing)

    def test_nothing_playing_nothing_to_resume(self):
        self.player.playing = False
        self.assertFalse(spotify.hold())
        self.assertFalse(spotify.release())
        self.assertFalse(self.player.playing)

    def test_music_off_during_the_hold_stays_off(self):
        spotify.hold()
        self.assertTrue(spotify.control("pause"))
        spotify.release()
        self.assertFalse(self.player.playing)

    def test_toggle_during_the_hold_means_pause(self):
        spotify.hold()
        spotify.control("play_pause")
        spotify.release()
        self.assertFalse(self.player.playing)

    def test_next_track_during_the_hold_plays(self):
        spotify.hold()
        self.assertTrue(spotify.control("next"))
        self.assertTrue(self.player.playing)
        self.player.calls.clear()
        spotify.release()
        self.assertNotIn(("PUT", "/me/player/play"), self.player.calls)

    def test_music_started_elsewhere_is_left_alone(self):
        spotify.hold()
        self.player.playing, self.player.device = True, "pc"
        self.player.calls.clear()
        self.assertTrue(spotify.release())
        self.assertEqual(self.player.calls, [("GET", "/me/player")])

    def test_stale_playing_on_the_paused_phone_still_plays(self):
        spotify.hold()
        self.player.playing = True               # what the iPhone claimed while it was silent
        self.player.calls.clear()
        self.assertTrue(spotify.release())
        self.assertIn(("PUT", "/me/player/play"), self.player.calls)

    def test_hold_outlives_a_restart(self):
        spotify.hold("PC")
        spotify._HELD.update(device=None, at=0.0)  # what a fresh process knows
        spotify._restore()
        self.assertTrue(spotify.release("PC", "denkt"))
        self.assertTrue(self.player.playing)
        self.assertFalse(spotify.HELD_FILE.exists())
        self.assertIn("PC: weiter nach", spotify.LOG.read_text(encoding="utf-8"))

    def test_off_switch_and_no_login(self):
        spotify.HOLD = False
        try:
            self.assertFalse(spotify.hold())
        finally:
            spotify.HOLD = True
        spotify.TOKEN_FILE.unlink()
        self.assertFalse(spotify.hold())
        self.assertFalse(spotify.control("pause"))
        self.assertTrue(self.player.playing)


class LoudPlayer(FakePlayer):
    """A device Spotify can set the volume of (phone, PC, Connect speaker)."""

    def __init__(self, volume=60, **kw):
        super().__init__(**kw)
        self.volume = volume

    def __call__(self, method, path, retry=True):
        if path == "/me/player" and self.device is not None:
            self.calls.append((method, path))
            return 200, {"is_playing": self.playing,
                         "device": {"id": self.device, "volume_percent": self.volume, "supports_volume": True}}
        if path.startswith("/me/player/volume"):
            self.calls.append((method, "/me/player/volume"))
            self.volume = int(path.split("volume_percent=")[1].split("&")[0])
            return 204, None
        return super().__call__(method, path, retry)


class RoundedLoudPlayer(LoudPlayer):
    """Some Spotify Connect devices report a neighboring integer after setting volume."""

    def __call__(self, method, path, retry=True):
        if path.startswith("/me/player/volume"):
            self.calls.append((method, "/me/player/volume"))
            requested = int(path.split("volume_percent=")[1].split("&")[0])
            self.volume = max(0, requested - 1) if requested == spotify.QUIET_PCT else requested
            return 204, None
        return super().__call__(method, path, retry)


class Quiet(unittest.TestCase):
    """JARVIS_SPOTIFY_HOLD_MODE=leise: down to the lowest step instead of a pause, and back after."""

    def setUp(self):
        self.saved = (spotify._api, spotify.CLIENT_ID, spotify.HOLD_MODE)
        spotify.CLIENT_ID, spotify.HOLD_MODE = "test", "leise"
        spotify.save_token({"refresh_token": "r"})
        spotify.forget()
        self.player = spotify._api = LoudPlayer()

    def tearDown(self):
        spotify._api, spotify.CLIENT_ID, spotify.HOLD_MODE = self.saved
        spotify.forget()
        spotify.TOKEN_FILE.unlink(missing_ok=True)

    def test_quiet_not_paused_and_back(self):
        self.assertTrue(spotify.hold("PC"))
        self.assertTrue(self.player.playing)
        self.assertEqual(self.player.volume, spotify.QUIET_PCT)
        self.assertNotIn(("PUT", "/me/player/pause"), self.player.calls)
        self.assertTrue(spotify.release("PC"))
        self.assertEqual(self.player.volume, 60)
        self.assertNotIn(("PUT", "/me/player/play"), self.player.calls)

    def test_volume_changed_by_hand_stays(self):
        spotify.hold()
        self.player.volume = 35
        spotify.release()
        self.assertEqual(self.player.volume, 35)

    def test_one_percent_rounding_still_restores_old_volume(self):
        self.player = spotify._api = RoundedLoudPlayer()
        self.assertTrue(spotify.hold("PC"))
        self.assertEqual(self.player.volume, spotify.QUIET_PCT - 1)
        self.assertEqual(spotify._HELD["quiet"], spotify.QUIET_PCT)
        self.assertTrue(spotify.release("PC"))
        self.assertEqual(self.player.volume, 60)

    def test_stale_old_volume_at_release_reasserts_saved_value(self):
        self.assertTrue(spotify.hold("PC"))
        self.player.calls.clear()
        self.player.volume = 60  # Connect may still report the pre-duck volume for a while.
        self.assertTrue(spotify.release("PC"))
        self.assertEqual(self.player.volume, 60)
        self.assertIn(("PUT", "/me/player/volume"), self.player.calls)

    def test_legacy_hold_without_quiet_snapshot_restores(self):
        self.assertTrue(spotify.hold("PC"))
        held = json.loads(spotify.HELD_FILE.read_text(encoding="utf-8"))
        held.pop("quiet", None)
        spotify.HELD_FILE.write_text(json.dumps(held), encoding="utf-8")
        self.player.volume = 1  # The earlier version ducked to 1% and did not save that target.
        spotify._HELD.update(device=None, at=0.0, volume=None, quiet=None)
        spotify._restore()
        self.assertTrue(spotify.release("PC"))
        self.assertEqual(self.player.volume, 60)

    def test_music_off_during_the_hold_gets_its_volume_back(self):
        spotify.hold()
        self.assertTrue(spotify.control("pause"))
        self.assertFalse(self.player.playing)
        self.assertEqual(self.player.volume, 60)
        spotify.release()
        self.assertFalse(self.player.playing)

    def test_toggle_during_the_hold_still_pauses(self):
        spotify.hold()
        spotify.control("play_pause")
        self.assertFalse(self.player.playing)

    def test_survives_a_restart(self):
        spotify.hold()
        spotify._HELD.update(device=None, at=0.0, volume=None)
        spotify._restore()
        spotify.release()
        self.assertEqual(self.player.volume, 60)

    def test_device_without_volume_is_paused(self):
        spotify._api = self.player = FakePlayer()
        self.assertTrue(spotify.hold())
        self.assertFalse(self.player.playing)
        self.assertTrue(spotify.release())
        self.assertTrue(self.player.playing)

    def test_pause_mode_still_pauses(self):
        spotify.HOLD_MODE = "pause"
        spotify.hold()
        self.assertFalse(self.player.playing)
        self.assertEqual(self.player.volume, 60)


if __name__ == "__main__":
    unittest.main()
