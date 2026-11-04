"""
Two microphones, one sentence: in the room the presence sensors see the user in, the device named in
config/home.json → mic_rooms takes the words (the iPad in the bedroom), the PC drops them. Elsewhere, or
with the iPad gone, the PC answers as before. Fakes only, no Whisper, no Claude.

    .venv\\Scripts\\python.exe -m unittest tests.test_mic_rooms -v
"""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import voice_engine      # noqa: E402
import voice_state as st  # noqa: E402
from voice_fakes import Client, FakeResponse, answer_events, fake_speak_stream, feed_ms, make_session, silence, tone  # noqa: E402

SAID = "Jarvis, turn off the light"


def second_session(shared, device):
    """Another microphone on the same engine, wired like make_session's."""
    client = Client()
    session = voice_engine.Session(shared, client.send, client.send_audio, device=device)
    client.session = session
    session.synth.speak_stream = fake_speak_stream()
    session.posts = []

    def fake_post(path, body, stream=False, timeout=30):
        session.posts.append((path, body))
        return FakeResponse(answer_events("The light is off.")) if path == "/api/run" else {}
    session._post = fake_post
    return session, client


def speak(session):
    feed_ms(session, tone(400))
    feed_ms(session, silence(600))


def ran(session, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if any(p[0] == "/api/run" for p in session.posts):
            return True
        time.sleep(0.01)
    return False


class MicRooms(unittest.TestCase):
    def setUp(self):
        self.pc, self.pc_client, self.shared = make_session(finals=[SAID, SAID])
        self.ipad, self.ipad_client = second_session(self.shared, "iPad")
        self.shared.sessions = {1: self.pc, 2: self.ipad}
        self.shared.preferred = lambda: "iPad"
        self.hold = mock.patch.object(voice_engine, "MIC_HOLD_S", 1.5)
        self.hold.start()

    def tearDown(self):
        self.hold.stop()
        self.pc.close()
        self.ipad.close()

    def settle(self, client, timeout=2.0):
        end = time.time() + timeout
        while time.time() < end:
            if client.session.machine.state in (st.LISTENING, st.IDLE) and st.THINKING in client.states():
                return
            time.sleep(0.01)

    def test_ipad_heard_it_first_pc_stays_quiet(self):
        speak(self.ipad)
        speak(self.pc)
        self.assertTrue(ran(self.ipad))
        self.settle(self.pc_client)
        self.assertFalse(ran(self.pc, 0.5))
        self.assertTrue(any("iPad is listening" in e.get("text", "") for e in self.pc_client.of("log")), self.pc_client.events[-5:])

    def test_pc_heard_it_first_waits_for_the_ipad(self):
        speak(self.pc)                                   # the PC's endpoint fires earlier through the wall
        speak(self.ipad)
        self.assertTrue(ran(self.ipad))
        self.settle(self.pc_client)
        self.assertFalse(ran(self.pc, 0.5))

    def test_only_the_pc_heard_it(self):
        speak(self.pc)
        self.assertTrue(ran(self.pc))                    # after the hold: the iPad was silent

    def test_ipad_not_connected(self):
        self.shared.sessions = {1: self.pc}
        speak(self.pc)
        self.assertTrue(ran(self.pc, 1.0))
        self.assertFalse(any("iPad" in e.get("text", "") for e in self.pc_client.of("log")))

    def test_other_room_both_answer(self):
        self.shared.preferred = lambda: ""
        speak(self.ipad)
        speak(self.pc)
        self.assertTrue(ran(self.ipad))
        self.assertTrue(ran(self.pc))

    def test_preferred_mic_from_config_and_room(self):
        with mock.patch("whereabouts.room", return_value="Bedroom"), \
                mock.patch("jarvis_profile.ha", side_effect=lambda k, d=None: {"mic_rooms": {"Bedroom": "iPad"}}.get(k, d)):
            self.assertEqual(voice_engine.preferred_mic(), "iPad")
        with mock.patch("whereabouts.room", return_value="Living room"), \
                mock.patch("jarvis_profile.ha", side_effect=lambda k, d=None: {"mic_rooms": {"Bedroom": "iPad"}}.get(k, d)):
            self.assertEqual(voice_engine.preferred_mic(), "")
        with mock.patch("whereabouts.room", return_value=""):
            self.assertEqual(voice_engine.preferred_mic(), "")


if __name__ == "__main__":
    unittest.main()
