"""
The voice engine without microphone, Whisper, Piper or Claude: tones stand for speech, a fake
recogniser says what was 'heard', the model answers from a list. Each test is one of the
conversations the engine has to get right.
"""
import time
import unittest

import isolate  # noqa: F401 — first: state and logs into a temp folder

import commands
import voice_config
import voice_dsp
import voice_endpoint
import voice_metrics
import voice_state as st
import voice_tts
import voice_wake
from voice_fakes import (feed, feed_ms, make_session, silence, tone, noise_burst)


class Config(unittest.TestCase):
    def test_defaults_and_overrides(self):
        cfg = voice_config.make({"vad": {"start_threshold": 0.9}})
        self.assertEqual(cfg.vad.start_threshold, 0.9)
        self.assertEqual(cfg.vad.stop_threshold, voice_config.DEFAULTS["vad"]["stop_threshold"])
        self.assertIn("mhm", cfg.barge.backchannel)
        self.assertEqual(cfg["engine"]["frame_ms"], 20)

    def test_file_is_read(self):
        cfg = voice_config.load()
        self.assertEqual(cfg.engine.port, 8724)


class StateMachine(unittest.TestCase):
    def test_full_cycle_and_refusal(self):
        m = st.Machine("t", log_path=None)
        for s in (st.LISTENING, st.THINKING, st.SPEAKING, st.INTERRUPTED, st.RESUMING, st.SPEAKING, st.IDLE):
            m.go(s, "test")
        self.assertEqual([h["to"] for h in m.history][-3:], [st.RESUMING, st.SPEAKING, st.IDLE])
        with self.assertRaises(RuntimeError):
            m.go(st.RESUMING, "resuming from idle is not a thing")
        self.assertEqual(m.state, st.IDLE)
        seen = []
        m.on_change(lambda o, n, w, at: seen.append((o, n)))
        m.go(st.LISTENING, "mic")
        self.assertEqual(seen, [(st.IDLE, st.LISTENING)])
        m.force(st.RESUMING, "test")
        self.assertEqual(m.state, st.RESUMING)


class Endpointing(unittest.TestCase):
    def test_rules(self):
        self.assertTrue(voice_endpoint.looks_unfinished("And then, um,"))
        self.assertTrue(voice_endpoint.looks_unfinished("Turn the light and"))
        self.assertTrue(voice_endpoint.looks_unfinished("i want to"))
        self.assertFalse(voice_endpoint.looks_unfinished("Turn off the light in the living room."))
        self.assertTrue(voice_endpoint.looks_like_question("What will the weather be like tomorrow"))
        self.assertTrue(voice_endpoint.looks_like_question("Is the Pi still on?"))

    def test_adaptive_wait(self):
        ep = voice_endpoint.Endpointer(voice_config.make().endpoint)
        done, unfinished = ep.wait_for("Turn off the light."), ep.wait_for("And then, um,")
        self.assertLess(done, unfinished)
        self.assertEqual(ep.wait_for(None), 700)
        self.assertEqual(ep.wait_for("What time is it?"), 350)
        ep.learn(True)
        self.assertEqual(ep.wait_for("Turn off the light."), done + 100)
        for _ in range(10):
            ep.learn(True)
        self.assertEqual(ep.learned, 600)
        ep.learn(False)
        self.assertEqual(ep.learned, 550)


class WakeWord(unittest.TestCase):
    def test_check(self):
        self.assertEqual(voice_wake.check("Jarvis, turn off the light."), (True, "turn off the light."))
        self.assertEqual(voice_wake.check("Turn off the light, Jarvis."), (True, "Turn off the light."))
        self.assertEqual(voice_wake.check("Could you, Jarvis, have a look?")[0], True)
        self.assertEqual(voice_wake.check("Could you, Jarvis, have a look?", strict=True)[0], False)
        self.assertEqual(voice_wake.check("Turn off the light.", awake=True), (True, "Turn off the light."))
        self.assertFalse(voice_wake.check("Turn off the light.")[0])
        self.assertTrue(voice_wake.is_bare("Jarvis?"))
        self.assertFalse(voice_wake.is_bare("Jarvis, what time is it?"))
        self.assertTrue(voice_wake.names("Danke, Jarvis"))


class Splitter(unittest.TestCase):
    def test_sentences_and_first_clause(self):
        sp = voice_tts.Splitter(voice_config.make().tts)
        text = "Good evening, Sir, the system is delivering two point four kilowatts right now and the battery is full. The Pi is running. Anything else?"
        got = []
        for i in range(0, len(text) + 4, 4):
            got += sp.feed(text[:i], final=i >= len(text))
        self.assertEqual(got[0], "Good evening, Sir,")             # the voice starts on the first clause
        self.assertEqual(got[-1], "The Pi is running. Anything else?")
        self.assertEqual(" ".join(got), text)

    def test_tags_do_not_count(self):
        sp = voice_tts.Splitter(voice_config.make().tts)
        self.assertEqual(sp.feed("[dry] Ja.", final=True), ["[dry] Ja."])
        self.assertEqual(sp.feed("", final=True), [])


class Decoder(unittest.TestCase):
    def test_wav_and_pcm(self):
        pcm = tone(100, 22050)
        d = voice_dsp.Decoder("audio/wav", 24000)
        wav = voice_dsp.wav_bytes(pcm, 22050)
        out = d.feed(wav[:30]) + d.feed(wav[30:])
        self.assertAlmostEqual(len(out) / 2 / 24000, 0.1, places=2)
        d = voice_dsp.Decoder("audio/pcm;rate=16000", 24000)
        self.assertEqual(len(d.feed(bytes(3200))), 4800)

    def test_fade_and_delay(self):
        pcm = tone(50)
        faded = voice_dsp.fade(pcm, 1.0, 0.0)
        self.assertLess(voice_dsp.rms(faded), voice_dsp.rms(pcm))
        ref = silence(200) + tone(200) + silence(600)
        mic = silence(500) + tone(200) + silence(300)
        self.assertAlmostEqual(voice_dsp.estimate_delay(ref, mic, 16000), 300, delta=20)


class Metrics(unittest.TestCase):
    def test_turn_and_prometheus(self):
        voice_metrics.reset()
        t = voice_metrics.Turn(clock=lambda: 0.0)
        t.t = {"start": 0, "speech_end": 1.0, "endpoint": 1.4, "stt_done": 1.6, "first_audio_out": 2.2}
        voice_metrics.record(t)
        snap = voice_metrics.snapshot()
        self.assertEqual(snap["last"]["first_word_ms"], 1200)
        self.assertEqual(snap["last"]["wait_ms"], 400)
        text = voice_metrics.prometheus()
        self.assertIn("jarvis_voice_first_word_ms_bucket", text)
        self.assertIn("jarvis_voice_late_first_word_total 1", text)


class Conversation(unittest.TestCase):
    """The six situations from the brief, with tones for speech and fakes for the models."""

    def setUp(self):
        import wakeword
        self._wake = wakeword.MODE
        wakeword.MODE = "off"             # another test file may have left the text mode on

    def tearDown(self):
        import wakeword
        wakeword.MODE = self._wake
        if getattr(self, "session", None):
            self.session.close()

    def speak_and_wait(self, finals, answer="The light is off. Anything else, Sir?", hold=False, **kw):
        self.session, self.client, _ = make_session(finals=finals, answer=answer, **kw)
        self.client.hold = hold
        s, c = self.session, self.client
        feed_ms(s, tone(400))                       # the user talks
        self.assertTrue(s.tracker.speaking)
        feed_ms(s, silence(600))                    # no partials: the no-text wait (400 ms) ends the turn
        self.assertTrue(self.passed(c, st.THINKING), c.states())
        return s, c

    def wait_count(self, c, state, n, timeout=3.0):
        end = time.time() + timeout
        while time.time() < end:
            if c.states().count(state) >= n:
                return True
            time.sleep(0.01)
        return False

    def passed(self, c, state, timeout=2.0):
        """The fakes are faster than the test: look at the history, not the current state."""
        end = time.time() + timeout
        while time.time() < end:
            if state in c.states():
                return True
            time.sleep(0.01)
        return False

    def test_turn_router_answer_is_spoken_and_window_opens(self):
        s, c = self.speak_and_wait(["Jarvis, turn off the light"])
        self.assertTrue(c.wait_event("audio_begin", 3))
        self.assertTrue(c.wait_state(st.LISTENING, 4))
        self.assertIn(st.SPEAKING, c.states())
        self.assertEqual(s.posts[0][0], "/api/run")
        self.assertEqual(s.posts[0][1]["message"], "turn off the light")      # the name is stripped
        self.assertGreater(s.awake_until, time.time())                       # conversation window
        done = c.of("speak_done")[-1]
        self.assertFalse(done["interrupted"])
        self.assertIn("Anything else", done["spoken"])

    def test_without_name_nothing_happens(self):
        import wakeword
        old = wakeword.MODE
        wakeword.MODE = "text"
        try:
            s, c = self.speak_and_wait(["turn off the light"])
            self.assertTrue(c.wait_state(st.LISTENING, 2))
            self.assertEqual([p for p in s.posts if p[0] == "/api/run"], [])
        finally:
            wakeword.MODE = old

    def test_thinking_pause_mid_sentence_waits(self):
        """'And then ...' (pause) '... turn the music down' is one request, not two."""
        self.session, c, _ = make_session(finals=["and then turn the music down"], partials=["and then", "and then"],
                                          partials_enabled=True)
        s = self.session
        feed_ms(s, tone(700))
        self.assertTrue(c.wait_event("partial", 1.5))
        feed_ms(s, silence(500))                    # a 500 ms thinking pause: unfinished → wait is 900 ms
        self.assertEqual(s.machine.state, st.LISTENING)
        self.assertTrue(s.tracker.speaking, "the pause must not end the utterance")
        feed_ms(s, tone(400))
        feed_ms(s, silence(1000))
        self.assertTrue(self.passed(c, st.THINKING))
        self.assertTrue(c.wait_state(st.LISTENING, 3))
        self.assertEqual(len([p for p in s.posts if p[0] == "/api/run"]), 1)
        self.assertEqual(s.posts[0][1]["message"], "and then turn the music down")

    def test_user_continues_after_turn_was_sent(self):
        """He already asked the model; the user goes on: the run is cancelled and both halves glued."""
        self.session, c, _ = make_session(finals=["turn off the light", "in the bedroom"], run_delay=0.3)
        s = self.session
        s.awake_until = time.time() + 30
        feed_ms(s, tone(400))
        feed_ms(s, silence(500))
        self.assertTrue(c.wait_state(st.THINKING, 1))
        time.sleep(0.1)
        feed_ms(s, tone(400))                       # within continue_window_ms
        self.assertTrue(c.wait_state(st.LISTENING, 1))
        feed_ms(s, silence(500))
        self.assertTrue(c.wait_state(st.SPEAKING, 3))
        runs = [p for p in s.posts if p[0] == "/api/run"]
        self.assertEqual(runs[-1][1]["message"], "turn off the light in the bedroom")
        self.assertIn("/api/cancel", [p[0] for p in s.posts])
        self.assertGreater(s.endpoint.learned, 0)

    def interrupt(self, finals, answer="The first sentence of the answer is here. A second sentence comes after it. A third sentence to finish.", **kw):
        s, c = self.speak_and_wait(finals, answer=answer, hold=True, **kw)
        self.assertTrue(c.wait_event("audio_begin", 3))
        time.sleep(0.05)
        feed_ms(s, tone(220))                       # talking over him: duck after 40 ms, pause after 120 ms
        self.assertTrue(c.wait_state(st.INTERRUPTED, 1), c.states())
        gains = c.of("gain")
        self.assertTrue(gains and gains[0]["value"] < 1)
        self.assertIn("flush", [e["t"] for e in c.events])
        return s, c

    def test_interruption_with_question_starts_new_turn_and_keeps_spoken_part(self):
        commands.update_state(lambda d: d.__setitem__("window", []))
        s, c = self.interrupt(["Jarvis, turn off the light", "What time is it"],
                              answer=["The first sentence of the answer is here. A second sentence comes after it.", "It is ten o'clock."])
        commands.remember_turn("turn off the light", "The first sentence of the answer is here. A second sentence comes after it.")
        feed_ms(s, silence(300))                    # the clip ends
        self.assertTrue(self.passed(c, st.THINKING) and c.states().count(st.THINKING) == 2 or
                        self.wait_count(c, st.THINKING, 2), c.states())
        runs = [p for p in s.posts if p[0] == "/api/run"]
        self.assertEqual(len(runs), 2)
        self.assertTrue(runs[1][1]["message"].startswith("What time is it"))
        self.assertIn("interrupted", runs[1][1]["hint"])
        self.assertNotIn("interrupted", runs[1][1]["message"])
        win = commands.load()["window"]
        self.assertIn("interrupted", win[-1]["a"])          # nothing had ended yet: no planned text leaks in
        self.assertNotIn("second sentence", win[-1]["a"])

    def test_backchannel_resumes_at_sentence_start(self):
        s, c = self.interrupt(["Jarvis, turn off the light", "mhm"])
        first_begin = c.of("audio_begin")[0]
        before = len(c.of("audio_begin"))            # sentences already streamed ahead before the pause
        feed_ms(s, silence(300))
        self.assertTrue(c.wait_state(st.SPEAKING, 2), c.states())
        self.assertIn(st.RESUMING, c.states())
        begins = c.wait_event("audio_begin", 2, n=before + 1)
        self.assertEqual(begins[before]["id"], first_begin["id"])     # the same sentence again, from its start
        self.assertTrue(begins[before]["again"])
        self.assertEqual(len([p for p in s.posts if p[0] == "/api/run"]), 1)

    def test_cough_is_a_blip_and_short_noise_resumes(self):
        s, c = self.speak_and_wait(["Jarvis, turn off the light"], hold=True)
        self.assertTrue(c.wait_event("audio_begin", 3))
        feed_ms(s, noise_burst(60))                 # shorter than confirm_ms: duck, no pause
        feed_ms(s, silence(100))
        self.assertEqual(s.machine.state, st.SPEAKING)
        gains = c.of("gain")
        self.assertTrue(gains and gains[-1]["value"] == 1.0, gains)
        feed_ms(s, noise_burst(160))                # long enough to pause, then nothing: resume
        self.assertTrue(c.wait_state(st.INTERRUPTED, 1))
        feed_ms(s, silence(300))
        self.assertTrue(c.wait_state(st.SPEAKING, 2), c.states())
        self.assertEqual(len([p for p in s.posts if p[0] == "/api/run"]), 1)

    def test_stop_word_ends_the_answer(self):
        s, c = self.interrupt(["Jarvis, turn off the light", "stop"])
        feed_ms(s, silence(300))
        self.assertTrue(c.wait_state(st.LISTENING, 2), c.states())
        done = c.of("speak_done")[-1]
        self.assertTrue(done["interrupted"])
        self.assertEqual(len([p for p in s.posts if p[0] == "/api/run"]), 1)
        n_audio = len(c.audio)
        time.sleep(0.3)
        self.assertEqual(len(c.audio), n_audio, "nothing more is sent after stop")

    def test_interrupted_then_silence_resumes(self):
        s, c = self.interrupt(["Jarvis, turn off the light", "What"])       # the clip: too short to be a request
        feed_ms(s, silence(300))
        self.assertTrue(c.wait_state(st.SPEAKING, 2), c.states())
        self.assertIn(st.RESUMING, c.states())

    def test_typed_question_is_spoken_and_stop_button_works(self):
        self.session, c, _ = make_session(answer="A typed answer that is spoken. And one more sentence.")
        s = self.session
        c.hold = True
        s.text("What time is it?", typed=True)
        self.assertTrue(c.wait_event("audio_begin", 3))
        s.stop()
        self.assertTrue(c.wait_state(st.LISTENING, 1))
        self.assertTrue(c.of("speak_done")[-1]["interrupted"])

    def test_notice_waits_for_a_quiet_moment(self):
        self.session, c, _ = make_session(answer="Answer.")
        s = self.session
        s.say("The washing machine is done.")
        self.assertTrue(c.wait_event("audio_begin", 3))
        self.assertTrue(c.wait_state(st.LISTENING, 3))
        self.assertEqual([p for p in s.posts if p[0] == "/api/run"], [])


    # ── TV, videos and his own echo (2026-11-09) ──────────────
    def text_mode(self):
        import wakeword
        wakeword.MODE = "text"                      # tearDown puts the old mode back

    def runs(self, s):
        return [p[1]["message"] for p in s.posts if p[0] == "/api/run"]

    def test_foreign_commentary_is_the_tv(self):
        """'Jarvis Kelsey, die geplante Zeit ...' was Travis Kelce on a German NFL broadcast."""
        self.text_mode()
        s, c = self.speak_and_wait(["Jarvis Kelsey, die geplante Zeit für den dritten Versuch ist jetzt vorbei."])
        self.assertTrue(c.wait_state(st.LISTENING, 2))
        self.assertEqual(self.runs(s), [])

    def test_his_own_answer_heard_back_is_dropped(self):
        self.text_mode()
        self.session, self.client, _ = make_session(finals=["Living room left is set to 20 degrees Celsius. Done."])
        s, c = self.session, self.client
        s.awake_until = time.time() + 30                # right after his answer: the follow-up window is open
        s.guard.remember("done: living room left is set to 20 °c. done: living room right is set to 20 °c.")
        s.guard.remember("living room left is set to 20 degrees celsius. done.")
        feed_ms(s, tone(400))
        feed_ms(s, silence(600))
        self.assertTrue(self.passed(c, st.THINKING))
        self.assertTrue(c.wait_state(st.LISTENING, 2))
        self.assertEqual(self.runs(s), [])

    def test_follow_up_needs_the_name_while_the_tv_plays(self):
        self.text_mode()
        self.session, self.client, _ = make_session(finals=["That was a strong play tonight"])
        s, c = self.session, self.client
        s.ambience = "tv"
        s.open_window(30)                               # after an answer
        feed_ms(s, tone(400))
        feed_ms(s, silence(600))
        self.assertTrue(self.passed(c, st.THINKING))
        self.assertTrue(c.wait_state(st.LISTENING, 2))
        self.assertEqual(self.runs(s), [])
        s.ambience = ""                                 # without the TV the same window counts
        self.assertTrue(s.awake(strict=False))
        s.open_window(30, by_name=True)                 # a bare "Jarvis" opens it even with the TV on
        self.assertTrue(s.awake(strict=True))

    def test_video_on_this_pc_counts_like_a_tv(self):
        import pc_audio
        self.session, _c, _ = make_session()
        s = self.session
        s.device = "pc"
        old = pc_audio.ON
        pc_audio.ON, pc_audio._STATE["started"] = True, True   # no real sampler in a test
        try:
            pc_audio.reset()
            self.assertFalse(s.ambient())
            now = time.time()
            for i in range(4):
                pc_audio.add_sample(True, now - 4 + i)
            self.assertTrue(s.ambient())
            pc_audio.reset()
            pc_audio.own(True)                          # his own voice from the browser is no video
            for i in range(4):
                pc_audio.add_sample(True, now - 4 + i)
            self.assertFalse(s.ambient())
        finally:
            pc_audio.reset()
            pc_audio.ON = old


class ForeignWords(unittest.TestCase):
    def test_foreign(self):
        f = voice_wake.foreign
        self.assertTrue(f("Die Chiefs haben den ersten Versuch, das ist ein starker Lauf von dem Mann."))
        self.assertTrue(f("Jarvis, ich gehe jetzt auf 27 Dollar und die Bills kommen."))
        self.assertTrue(f("Randaleically un Barack wasn" + chr(0x771f) + " oli " + chr(0x412) * 4))
        self.assertFalse(f("Jarvis, play Let it be by the Beatles"))
        self.assertFalse(f("Jarvis, turn on the light in the living room"))
        self.assertFalse(f("Also, wie gesagt, wenn es vorbei ist. Jarvis, good night, start the night routine."))
        self.assertFalse(f("Living room, 20 degrees, Jarvis."))

if __name__ == "__main__":
    unittest.main()
