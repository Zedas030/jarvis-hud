"""/api/ask and /api/hear: Home Assistant's voice satellite with a key; answers said on the room's speaker
while they are written, cards denied."""
import http.client
import json
import os
import sys
import threading
import time
import unittest
import unittest.mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402  (must come before any Jarvis module)

import devices  # noqa: E402
devices.PATH = TMP / "devices-ask.json"
import remote_ask  # noqa: E402
import server   # noqa: E402

HOST = "jarvis-pc.tail1234.ts.net"


class FakeRun(BaseHTTPRequestHandler):
    """Stands in for /api/run and /api/permission/answer on the loopback."""
    events = []
    seen = []
    sent = []          # time each event went out
    status = 200
    gap = 0.0          # pause between events, like Claude writing

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        FakeRun.seen.append((self.path, body, self.headers.get("X-Jarvis-Token")))
        if self.path == "/api/permission/answer":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok": true}')
            return
        self.send_response(FakeRun.status)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        for ev in FakeRun.events:
            if FakeRun.gap:
                time.sleep(FakeRun.gap)
            self.wfile.write((json.dumps(ev) + "\n").encode())
            self.wfile.flush()
            FakeRun.sent.append(time.time())


class Speakable(unittest.TestCase):
    def test_markdown_and_emoji_go(self):
        self.assertEqual(remote_ask.speakable("**Yes**, Sir 👍\n- Light on\n- [Weather](https://x.example) good"),
                         "Yes, Sir. Light on. Weather good.")
        self.assertEqual(remote_ask.speakable("Hello , Sir . input_boolean stays"), "Hello, Sir. input_boolean stays.")

    def test_hint_names_the_room(self):
        self.assertIn("in the room Kitchen", remote_ask.hint_for("Kitchen"))
        self.assertNotIn("in the room", remote_ask.hint_for())


class AskLoop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeRun)
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()
        cls.port = cls.fake.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.fake.shutdown()

    def setUp(self):
        FakeRun.seen.clear()
        FakeRun.sent.clear()
        FakeRun.status = 200
        FakeRun.gap = 0.0

    def test_answer_is_collected(self):
        FakeRun.events = [{"t": "delta", "text": "In the living room "}, {"t": "delta", "text": "it is **21 degrees**."},
                          {"t": "complete", "ms": 5}]
        out = remote_ask.ask("How warm is it?", self.port, "tok")
        self.assertEqual(out, {"text": "In the living room it is 21 degrees.", "status": "ok"})
        path, body, token = FakeRun.seen[0]
        self.assertEqual((path, body["message"], body["typed"], token), ("/api/run", "How warm is it?", False, "tok"))
        self.assertIn("Home Assistant", body["hint"])

    def test_quiet_answer_is_not_said(self):
        FakeRun.events = [{"t": "quiet"}, {"t": "delta", "text": "Stopped."}, {"t": "complete"}]
        self.assertEqual(remote_ask.ask("Stop", self.port, "tok"), {"text": "", "status": "quiet"})

    def test_card_is_denied_at_once(self):
        FakeRun.events = [{"t": "confirm", "id": "abc", "tool": "calendar_add"},
                          {"t": "delta", "text": "Not entered."}, {"t": "complete"}]
        out = remote_ask.ask("Put the dentist in the calendar", self.port, "tok")
        self.assertEqual(out["status"], "card")
        self.assertIn("confirmation", out["text"])
        self.assertIn(("/api/permission/answer", {"id": "abc", "allow": False}, "tok"), FakeRun.seen)

    def test_busy(self):
        FakeRun.status = 409
        FakeRun.events = []
        self.assertEqual(remote_ask.ask("Hello", self.port, "tok")["status"], "busy")

    def test_nothing_listening(self):
        port = self.port
        self.fake.shutdown()
        self.fake.server_close()
        try:
            self.assertEqual(remote_ask.ask("Hello", port, "tok", timeout=2)["status"], "error")
        finally:
            AskLoop.setUpClass()


class AskEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.remote = ThreadingHTTPServer(("127.0.0.1", 0), server.RemoteHandler)
        cls.local = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        server.PORT = cls.local.server_address[1]
        for s in (cls.remote, cls.local):
            threading.Thread(target=s.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        for s in (cls.remote, cls.local):
            s.shutdown()

    def setUp(self):
        devices.PATH.unlink(missing_ok=True)
        devices._CODE.update(code=None, until=0, tries=0)
        server.REMOTE_HOSTS.clear()
        self.calls = []
        self._orig = remote_ask.ask
        remote_ask.ask = lambda text, port, token, room="", speak=False: (
            self.calls.append((text, room, speak, token == server.API_TOKEN)) or {"text": "Gladly.", "status": "ok"})

    def tearDown(self):
        remote_ask.ask = self._orig

    def req(self, method, path, body=None, host=HOST, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.remote.server_address[1], timeout=5)
        h = {"Host": host, "Content-Type": "application/json", **(headers or {})}
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def key(self):
        devices.start_pairing()
        r, data = self.req("POST", "/api/pair", {"code": devices._CODE["code"], "name": "Home Assistant", "purpose": "ask"},
                           headers={"Origin": "https://" + HOST})
        self.assertEqual(r.status, 200)
        self.assertIsNone(r.getheader("Set-Cookie"), "a key is shown, never set as the browser's cookie")
        return json.loads(data)["key"]

    def test_key_answers(self):
        key = self.key()
        r, data = self.req("POST", "/api/ask", {"text": "  How   warm is it? "}, headers={"Authorization": "Bearer " + key})
        self.assertEqual((r.status, json.loads(data)["text"]), (200, "Gladly."))
        self.assertEqual(self.calls, [("How warm is it?", "", False, True)])
        r, _ = self.req("POST", "/api/ask", {"text": "Light on", "source": "ha", "room": "Kitchen", "speak": True},
                        headers={"Authorization": "bearer " + key})
        self.assertEqual(self.calls[-1], ("Light on", "Kitchen", True, True))
        self.assertEqual([d["kind"] for d in devices.listing()], ["ask"])

    def test_without_or_wrong_key(self):
        self.assertEqual(self.req("POST", "/api/ask", {"text": "hi"})[0].status, 401)
        self.assertEqual(self.req("POST", "/api/ask", {"text": "hi"}, headers={"Authorization": "Bearer nope"})[0].status, 401)
        key = self.key()
        self.assertEqual(self.req("POST", "/api/ask", {"text": "hi"}, host="evil.example.com",
                                  headers={"Authorization": "Bearer " + key})[0].status, 403)
        self.assertEqual(self.calls, [])

    def test_key_is_no_cookie_and_cookie_is_no_key(self):
        key = self.key()
        r, _ = self.req("GET", "/", headers={"Cookie": f"{devices.COOKIE}={key}"})
        self.assertEqual(r.status, 302, "a Home Assistant key must not open the HUD")
        devices.start_pairing()
        r, _ = self.req("POST", "/api/pair", {"code": devices._CODE["code"], "name": "iPhone"}, headers={"Origin": "https://" + HOST})
        cookie_token = r.getheader("Set-Cookie").split(";")[0].split("=", 1)[1]
        self.assertEqual(self.req("POST", "/api/ask", {"text": "hi"},
                                  headers={"Authorization": "Bearer " + cookie_token})[0].status, 401)

    def test_empty_question(self):
        key = self.key()
        r, data = self.req("POST", "/api/ask", {"text": "   "}, headers={"Authorization": "Bearer " + key})
        self.assertEqual(json.loads(data)["status"], "empty")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()


class RoomVoiceLoop(unittest.TestCase):
    """2026-11-17: the Voice PE only listens; the answer comes from the room's Sonos in Jarvis' own voice,
    sentence by sentence while Claude is still writing."""
    ANSWER = [{"t": "delta", "text": "Good evening, Sir. In the bedroom it is "},
              {"t": "delta", "text": "twenty-one degrees. "},
              {"t": "delta", "text": "The window is closed."}, {"t": "complete"}]

    @classmethod
    def setUpClass(cls):
        cls.fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeRun)
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()
        cls.port = cls.fake.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.fake.shutdown()

    def setUp(self):
        AskLoop.setUp(self)
        import alexa_voice
        import sonos
        import voice
        self.said, self.fail_on = [], set()

        def say(eid, chunks, mime, text="", quiet=False):
            list(chunks)
            if len(self.said) in self.fail_on:
                raise sonos.Fallback(b"x", mime)
            self.said.append((text, time.time()))
            return 1.0
        self.patches = [
            unittest.mock.patch.object(remote_ask, "room_speaker",
                                       side_effect=lambda room: "media_player.bedroom" if room == "Bedroom" else ""),
            unittest.mock.patch.object(sonos, "label", return_value="Sonos Bedroom"),
            unittest.mock.patch.object(sonos, "say", side_effect=say),
            unittest.mock.patch.object(alexa_voice, "is_alexa", return_value=False),
            unittest.mock.patch.object(voice, "speak_stream", side_effect=lambda t: (iter([b"x"]), "audio/mpeg", "edge")),
        ]
        for pt in self.patches:
            pt.start()

    def tearDown(self):
        for pt in self.patches:
            pt.stop()

    def test_first_sentence_while_claude_still_writes(self):
        FakeRun.events, FakeRun.gap = self.ANSWER, 0.3
        out = remote_ask.ask("How warm is it?", self.port, "tok", room="Bedroom", speak=True)
        self.assertEqual(out.get("spoken_on"), "Sonos Bedroom")
        self.assertEqual(" ".join(t for t, _ in self.said), "Good evening, Sir. In the bedroom it is twenty-one degrees. The window is closed.")
        self.assertGreater(len(self.said), 1, "one sentence at a time, not the whole answer at the end")
        self.assertLess(self.said[0][1], FakeRun.sent[-1], "the first sentence is out before the answer is complete")

    def test_speaker_fails_midway_satellite_reads_the_rest(self):
        FakeRun.events = self.ANSWER
        self.fail_on = {1}
        out = remote_ask.ask("How warm is it?", self.port, "tok", room="Bedroom", speak=True)
        self.assertNotIn("spoken_on", out)
        self.assertEqual(len(self.said), 1)
        self.assertNotIn(self.said[0][0], out["text"])
        self.assertTrue(out["text"].endswith("The window is closed."))

    def test_speaker_fails_at_once_satellite_reads_all(self):
        FakeRun.events = self.ANSWER
        self.fail_on = {0}
        out = remote_ask.ask("How warm is it?", self.port, "tok", room="Bedroom", speak=True)
        self.assertNotIn("spoken_on", out)
        self.assertTrue(out["text"].startswith("Good evening, Sir."))

    def test_card_note_is_said_too(self):
        FakeRun.events = [{"t": "confirm", "id": "abc"}, {"t": "delta", "text": "Not entered."}, {"t": "complete"}]
        out = remote_ask.ask("Put the dentist in the calendar", self.port, "tok", room="Bedroom", speak=True)
        self.assertEqual((out["status"], out.get("spoken_on")), ("card", "Sonos Bedroom"))
        self.assertIn("confirmation", self.said[-1][0])

    def test_tv_talk_is_dropped_silently(self):
        """2026-11-19: like Alexa, speech not meant for Jarvis (TV, word salad) is dropped — nothing said."""
        FakeRun.events = [{"t": "delta", "text": "IGN"}, {"t": "delta", "text": "ORE"}, {"t": "complete"}]
        out = remote_ask.ask("and then the inspector said to her", self.port, "tok", room="Bedroom", speak=True)
        self.assertEqual(out, {"text": "", "status": "ignored"})
        self.assertEqual(self.said, [], "the marker is never spoken, not even while it streams in")

    def test_an_answer_starting_like_the_marker_is_still_said(self):
        FakeRun.events = [{"t": "delta", "text": "I"}, {"t": "delta", "text": "t is raining in Berlin."}, {"t": "complete"}]
        out = remote_ask.ask("Is it raining?", self.port, "tok", room="Bedroom", speak=True)
        self.assertEqual(out.get("spoken_on"), "Sonos Bedroom")
        self.assertEqual(" ".join(t for t, _ in self.said), "It is raining in Berlin.")

    def test_hint_tells_the_model_how_to_drop_it(self):
        self.assertIn(remote_ask.IGNORE, remote_ask.hint_for("Living room"))
        self.assertTrue(remote_ask.ignored("Ignore."))
        self.assertFalse(remote_ask.ignored("I will not ignore that."))

    def test_dropped_talk_stays_out_of_the_diary(self):
        import memory
        before = sorted(memory.DIARY_DIR.glob("*.md")) if memory.DIARY_DIR.exists() else []
        sizes = {p: p.stat().st_size for p in before}
        memory.log_turn("and then the inspector said", "IGNORE")
        after = sorted(memory.DIARY_DIR.glob("*.md")) if memory.DIARY_DIR.exists() else []
        self.assertEqual(after, before)
        self.assertEqual({p: p.stat().st_size for p in after}, sizes)

    def test_busy_is_said_in_the_room(self):
        FakeRun.status, FakeRun.events = 409, []
        out = remote_ask.ask("Hello", self.port, "tok", room="Bedroom", speak=True)
        self.assertEqual((out["status"], out.get("spoken_on")), ("busy", "Sonos Bedroom"))

    def test_no_speaker_or_not_asked(self):
        FakeRun.events = self.ANSWER
        out = remote_ask.ask("How warm is it?", self.port, "tok", room="Kitchen", speak=True)
        self.assertNotIn("spoken_on", out)
        out = remote_ask.ask("How warm is it?", self.port, "tok", room="Bedroom")
        self.assertNotIn("spoken_on", out)
        self.assertEqual(out["text"], "Good evening, Sir. In the bedroom it is twenty-one degrees. The window is closed.")
        self.assertEqual(self.said, [])


class RoomSpeakerChoice(unittest.TestCase):
    def test_unknown_room_hushed_or_off(self):
        import sonos
        import whereabouts
        with unittest.mock.patch.object(whereabouts, "speaker", return_value=""):
            self.assertEqual(remote_ask.room_speaker("Basement"), "")
        self.assertEqual(remote_ask.room_speaker(""), "")
        with unittest.mock.patch.object(whereabouts, "speaker", return_value="media_player.bedroom"), \
                unittest.mock.patch.object(sonos, "label", return_value="Sonos"), \
                unittest.mock.patch.object(sonos, "_available", return_value=True), \
                unittest.mock.patch.object(sonos, "hushed", return_value=False):
            self.assertEqual(remote_ask.room_speaker("Bedroom"), "media_player.bedroom")
            with unittest.mock.patch.object(sonos, "hushed", return_value=True):
                self.assertEqual(remote_ask.room_speaker("Bedroom"), "")
            with unittest.mock.patch.object(remote_ask, "ROOM_SPEAKER", False):
                self.assertEqual(remote_ask.room_speaker("Bedroom"), "")


class Hear(unittest.TestCase):
    """/api/hear: the Voice PE's audio transcribed by the PC's Whisper (remote_ask.hear)."""
    def setUp(self):
        self._rec = remote_ask._HEAR["rec"]

    def tearDown(self):
        remote_ask._HEAR["rec"] = self._rec

    def fake(self, **kw):
        rec = unittest.mock.Mock(**{"available": True, **kw})
        remote_ask._HEAR["rec"] = rec
        return rec

    def test_text_and_empty(self):
        rec = self.fake()
        rec.final.return_value = "How healthy is the battery?"
        out = remote_ask.hear(b"\x01\x00" * 16000)
        self.assertEqual((out["text"], out["status"]), ("How healthy is the battery?", "ok"))
        rec.final.assert_called_once_with(b"\x01\x00" * 16000)
        rec.final.return_value = ""
        self.assertEqual(remote_ask.hear(b"\0\0" * 16000)["status"], "empty")

    def test_failure_is_an_error_for_the_fallback(self):
        rec = self.fake()
        rec.final.side_effect = RuntimeError("CUDA out of memory")
        self.assertEqual(remote_ask.hear(b"\0\0" * 16000)["status"], "error")
        self.fake(available=False)
        self.assertEqual(remote_ask.hear(b"\0\0" * 16000)["status"], "error")


class HearEndpoint(AskEndpoint):
    def setUp(self):
        super().setUp()
        self.heard, self.warmed = [], []
        self._hear, self._warm = remote_ask.hear, remote_ask.warm
        remote_ask.hear = lambda pcm: self.heard.append(pcm) or {"text": "Light off", "status": "ok", "ms": 90}
        remote_ask.warm = lambda: self.warmed.append(1)

    def tearDown(self):
        remote_ask.hear, remote_ask.warm = self._hear, self._warm
        super().tearDown()

    def post_audio(self, pcm, key=None, host=HOST):
        c = http.client.HTTPConnection("127.0.0.1", self.remote.server_address[1], timeout=5)
        h = {"Host": host, "Content-Type": "audio/L16; rate=16000", "Content-Length": str(len(pcm))}
        if key:
            h["Authorization"] = "Bearer " + key
        try:
            c.request("POST", "/api/hear", body=pcm, headers=h)
            r = c.getresponse()
            return r.status, json.loads(r.read() or b"{}")
        finally:
            c.close()

    def test_audio_with_key(self):
        key = self.key()
        pcm = b"\x10\x00" * 32000
        self.assertEqual(self.post_audio(pcm, key), (200, {"text": "Light off", "status": "ok", "ms": 90}))
        self.assertEqual(self.heard, [pcm])

    def test_empty_body_only_warms(self):
        key = self.key()
        self.assertEqual(self.post_audio(b"", key), (200, {"text": "", "status": "warm"}))
        self.assertEqual((self.warmed, self.heard), ([1], []))

    def test_no_key_no_host_too_long(self):
        self.assertEqual(self.post_audio(b"\0\0" * 100)[0], 401)
        self.assertEqual(self.post_audio(b"\0\0" * 100, "nope")[0], 401)
        key = self.key()
        self.assertEqual(self.post_audio(b"\0\0" * 100, key, host="evil.example.com")[0], 403)
        self.assertEqual(self.post_audio(b"\0" * (remote_ask.HEAR_MAX_S * remote_ask.HEAR_RATE * 2 + 2), key)[0], 413)
        self.assertEqual(self.heard, [])


class SatelliteWithoutArea(unittest.TestCase):
    def test_room_from_presence(self):
        import room_scope
        import whereabouts
        seen = []
        with unittest.mock.patch.object(room_scope, "enabled", return_value=False), \
                unittest.mock.patch.object(whereabouts, "room", return_value="Bedroom"), \
                unittest.mock.patch.object(remote_ask, "room_speaker", side_effect=lambda r: seen.append(r) or ""), \
                unittest.mock.patch.object(remote_ask, "_run", return_value=("Gladly.", "ok", 0)):
            out = remote_ask.ask("Hello", 1, "t", room="", speak=True)
        self.assertEqual(seen, ["Bedroom"])
        self.assertEqual(out["text"], "Gladly.")

    def test_answer_goes_where_he_is(self):
        """The bedroom ReSpeaker heard him, but only the living room sees someone: the living room answers."""
        import room_scope
        seen = []
        with unittest.mock.patch.object(room_scope, "enabled", return_value=True), \
                unittest.mock.patch.object(room_scope, "_busy", return_value={"Living room"}), \
                unittest.mock.patch.object(remote_ask, "room_speaker", side_effect=lambda r: seen.append(r) or ""), \
                unittest.mock.patch.object(remote_ask, "_run", return_value=("Gladly.", "ok", 0)) as run:
            remote_ask.ask("Hello", 1, "t", room="Bedroom", speak=True)
        self.assertEqual(seen, ["Living room"])
        self.assertEqual(run.call_args[0][3], "Living room", "the hint names the room he is in")
