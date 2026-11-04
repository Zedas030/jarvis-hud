# Voice pipeline analysis: listening, interrupting, carrying on

> Implemented on 2026-11-02 (branch `feat/voice-engine`): P0–P4 from section 5 are built, see `docs/voice.md` →
> "Voice engine". Still open: the iPad connection through `tailscale serve`, the room satellite and the
> trial of the Sonos echo cancellation with a reference signal.

As of 2026-11-02, phase 1 (analysis only, no code changed). Basis: a complete read of
`ui/app.js`, `server.py`, `jarvis/voice/*`, `jarvis/core/runtime.py`, the logs (`usage.jsonl`,
`wake_device.log`, `spotify.log`) and measurements on this PC (RTX 5080, i7, Python 3.12). The analysis was
made on the German edition; the figures apply to the English one as well.

## 1. Current pipeline

The whole conversation logic (microphone, level VAD, end of sentence, interrupting, playback) lives in the
**browser** (`ui/app.js`, ~2900 lines). The server is a stateless HTTP service with three
voice endpoints: `/api/listen` (clip → text), `/api/run` (text → NDJSON stream), `/api/speak`
(sentence → audio). There is no lasting audio channel between browser and server.

| Stage | Today | File |
|---|---|---|
| Audio capture | `getUserMedia` with browser AEC and noise suppression, AGC off. The AudioWorklet "jarvis-cap" keeps 600 ms of pre-roll and builds a 16 kHz WAV in JS. Fallback MediaRecorder (webm). | `app.js` → `Cap`, `Live.enable` |
| Clock | The AudioWorklet "jarvis-tick" calls `Live.loop()` every ~16 ms (also runs in a background tab). | `TICK_WORKLET` |
| VAD | Plain RMS level (AnalyserNode, 1024 samples) against a calibrated threshold (median of room silence × 3.2, min. 0.015) plus a slowly tracked background level. No ML VAD. | `Live.calibrate`, `Live.loop` |
| End of sentence | Fixed silence `SILENCE_MS = 2000`. Then transcription; if the text sounds unfinished (regex on connectors, missing full stop), it waits another 3 s (`UNFINISHED_HOLD_MS`). Whoever keeps talking up to 7 s after sending cancels the running Claude call, and the text is glued together (`CONTINUE_MS`). | `looksUnfinished`, `Live.send` |
| Wake word | `.env`: `JARVIS_WAKE=Jarvis` = mode **text**: every utterance is transcribed with Whisper and checked by regex for spellings of "Jarvis". `oww` on the PC is not installed (openwakeword, sounddevice missing in `.venv`). On iPad/iPhone openWakeWord runs in a web worker (onnxruntime-web, model `jarvis_v1`). After a bare "Jarvis" a 7 s window. **A conversation mode already exists** (`JARVIS_FOLLOWUP_S`), but is set to 0 = off. | `wakeword.py`, `ui/wakeword.js`, `Wake` |
| STT | The whole clip by POST. faster-whisper `large-v3-turbo` fp16 on CUDA, beam 5, `vad_filter`, `initial_prompt` + `hotwords`; CPU fallback `small` int8. No partials, no streaming. A ghost filter for silence hallucinations. The resource manager drops the GPU model under memory pressure (reloading measured at 3.5 s). | `voice.py` |
| LLM | The router (`router.py`) answers ~20 % locally. Otherwise `claude -p --output-format stream-json` with a pre-warmed process (`lean_prewarm`), token deltas as NDJSON to the browser. Fallback mode Groq → Gemini → Ollama. | `runtime.py`, `server.py::_lean` |
| TTS | The browser cuts finished sentences out of the delta stream (`feedSpeech`, min. 28 characters) and requests them **one by one** from `/api/speak`; the requests overlap with Claude. Chain `fish,elevenlabs,local,azure,google,edge`. Only Fish streams (first bytes ~0.7 s), all others deliver the sentence in one piece. `local` = Chatterbox GPU server (not streamed) or Piper CPU. Piper has a streaming API that is unused. | `tts_chain.py`, `local_tts_server.py` |
| Playback | MediaSource (mp3 stream) → `<audio>` → AudioContext fallback. Sonos: one live MP3 stream per answer through HA `announce`, 1.5 s lead (`LIVE_AHEAD_S`). Echo: only text to Alexa TTS, the duration is estimated (15 characters/s + 1 s). | `playUrl`, `sonos.py`, `alexa_voice.py` |
| Barge-in | While `Live.speaking`: a level above `bargeNow()` = max(room × 3, background × 2.2, 0.02; with Sonos also the own echo level × 1.8) starts a recording. After 400 ms of sustained level `pauseSpeech()`. The clip ends after 550 ms of silence (1000 ms when paused), goes to `/api/listen`, then regex classification: name, filler word, stop word, text comparison with the own answer (`echoOf`), TV/sport ambience. | `Barge`, `bargeNow`, `pauseSpeech` |
| Resuming | Sentence-grained: the interrupted sentence is played again from the start (`item.again`). With the Sonos all waiting sentences are synthesised anew. | `resumeSpeech`, `playSpeechQueue` |
| Concurrency | Server: `ThreadingHTTPServer`, one `RUN_LOCK`, `EMIT_LOCK`. Browser: single-thread JS plus worklets. **No state machine**; the state lies in ~15 flags (`Live.on/speaking/armed/sending/continuing/partial/ptt`, `Speech.active/paused/cancelled`, `Barge.rec/busy`, `running`, `pendingConfirm`, `briefingActive`, `Wake.until`). | – |
| Metrics | Only `logs/wake_device.log` (one line per started interruption) and `usage.jsonl` (total time per answer). No stage latencies, no Prometheus. | `BargeLog`, `log_usage` |

## 2. Latency per stage

Measured (M) on this PC on 2026-11-02 or from logs 2026-10-14 to 10-31; estimated (E) from code and comments.

| Stage | Value | Source |
|---|---|---|
| Silence until the sentence counts as ended | **2000 ms** fixed, +3000 ms if "unfinished" | `SILENCE_MS`, `UNFINISHED_HOLD_MS` |
| Building the WAV, local POST | 20–50 ms | E |
| STT GPU `large-v3-turbo`, 7.5 s clip | **0.24 s** (beam 5), 0.18 s (beam 1) | M |
| STT CPU `small` int8, 7.5 s clip | **1.87 s** (beam 5), 1.33 s (beam 1) | M (preview of the server, i7-8700 similar) |
| Reloading the STT model (after being dropped) | 3.5 s GPU, 1.1 s CPU | M |
| Router, local answer | **21 ms** p50, 444 ms p90 (n = 108) | `usage.jsonl` |
| Claude Haiku, whole answer | **4.4 s** p50, 8.8 s p90 (n = 248) | `usage.jsonl` |
| Claude Sonnet, whole answer | 5.6 s p50, 14.8 s p90 (n = 73) | `usage.jsonl` |
| Claude: first token / first sentence | ~1–2.5 s / ~1.5–3 s | E (pre-warmed process; not logged) |
| TTS Fish, first bytes | ~0.7 s (whole ~2.5 s) | comment in `tts_chain.py` |
| TTS Piper CPU | **0.19 s** for 1.4 s of audio, 0.54 s for 5.4 s of audio (RTF 0.10–0.14) | M |
| TTS Chatterbox GPU | ~1–3 s per sentence, not streamed | E |
| Playback start on the device | 50–150 ms (MediaSource) | E |
| Sonos: lead | +1.2 s `LEAD_S` + HA call 0.3–0.5 s | code |
| Echo (Alexa) | +1 s `LAG_S`, duration only estimated | code |

**End of the utterance to the first audible word (today):**

| Path | Calculation | Total |
|---|---|---|
| Router, PC speaker | 2.0 + 0.05 + 0.25 + 0.02 + 0.7 + 0.1 | **≈ 3.1 s** |
| Claude Haiku, PC speaker | 2.0 + 0.05 + 0.25 + ~2.0 (first sentence) + 0.7 + 0.1 | **≈ 5.1 s** |
| the same through the Sonos | + 1.5–1.7 | ≈ 6.7 s |

Goal according to the brief: < 800 ms. The biggest single item is the fixed 2 s silence timer, the second the
time until the first complete sentence from Claude, the third the TTS first-byte latency.

**Barge-in reaction time (today):**

| Output | until the output stops | until the decision (carry on / new question) |
|---|---|---|
| PC speaker | 400 ms level + 16 ms clock ≈ **420–450 ms** (goal ≤ 200) | + 550 ms silence + 0.25 s STT ≈ 1.2 s after the last word |
| Sonos | ≈ 420 ms + 1.5 s buffer ≈ **2 s** | as above |
| Echo | the running sentence can **not** be stopped, only blocks not yet sent | – |

## 3. Why it does not work reliably today

### 3.1 Letting the user finish
- The silence timer is a fixed value and wrong for both goals: 2 s is too long for the answer latency
  and too short for real thinking pauses (the "unfinished" detection only kicks in after the transcription,
  so at the earliest 2.3 s after falling silent, and then extends by a flat 3 s).
- Because there are no partials, nothing can be decided about sentence completeness before the clip ends.
- If the user keeps talking after sending, the running Claude call is cancelled (tokens gone) and
  the answer starts over. That is a repair, not avoidance.
- Level VAD with AGC off: quiet sentence starts, fricatives and distance to the mic shift the detection;
  coughs and keyboard, on the other hand, are often above the threshold.

### 3.2 Interrupting
- The bar for the user is high: 3 × room silence, 2.2 × background, and with Sonos/Echo also
  1.8 × the own echo level. In `wake_device.log` (2026-10-28 to 10-31, Echo living room) for most
  answers the "loudest moment" lies **below** the bar (e.g. 0.085 vs. 0.134; 0.105 vs. 0.125;
  0.074 vs. 0.122) with "0 clips checked". Those are attempts to cut in that nothing happened to.
  Successful interruptions are almost exclusively an isolated "Jarvis.".
- There is **no echo cancellation for external speakers**: the browser AEC only knows the
  device's own output. For the Sonos the reference signal is on the server, for Alexa TTS none
  exists at all. The replacement (level comparison + text comparison `echoOf`) is the reason for the high bar.
- 400 ms of sustained level before the pause, no ducking before that. On the Sonos 1.5 s of buffer come on top.
- Short clips produce Whisper hallucinations ("Tu mozno ma genau ako", 2026-10-31 11:48) that could
  count as "words"; only the missing name prevented that.

### 3.3 Carrying on
- The classification (noise / backchannel / stop / real utterance) only happens once the clip has
  ended, and only by regex. A throat-clearing with two recognised words in the pause becomes a new question.
- With the Sonos, `stop(hold=True)` discards the stream; when carrying on, all waiting sentences are
  synthesised anew (cost, latency, audible gap).
- The conversation history holds the **whole** answer (`commands.remember_turn(message, text)` in
  `server.py::_lean`), not the spoken part. The browser knows the spoken share
  (`Speech.upTo`), the server does not.
- "Interrupted, then silence → carry on" only works for clips without words; carrying on
  after a freely chosen time is not provided for.

### 3.4 Structure
- No explicit state, no log of the state changes. Errors can only be traced with the browser console.
- All thresholds are constants in `app.js`; `docs/voice.md` recommends editing them there.
- The logic hangs on the browser tab (iOS audio unlock, background throttling, different levels per device).
- For the 24/7 Linux server without a microphone the split browser = ear, server = brain is right in
  principle, but AEC, VAD and endpointing then belong on the side that has the reference signal and the
  computing power: the server.

## 4. What is technically achievable, and what is not

| Goal | Achievable | Compromise |
|---|---|---|
| Barge-in ≤ 200 ms on the device speaker | **Yes**: ducking on the first VAD frame (~60–100 ms), pause after 150 ms of confirmed speech. | – |
| Barge-in ≤ 200 ms on the Sonos | **No.** The Sonos buffers 0.5–1.5 s itself; HA calls take 0.3–0.5 s. | Realistically 0.8–1.2 s: cut the stream right away, `volume_set` (ducking) through HA in parallel, lead from 1.5 to ~0.8 s. |
| Barge-in on the Echo | **No.** Alexa TTS can be neither stopped nor referenced. | Echo only for announcements, not for dialogues; dialogues through the Sonos or a dedicated speaker. |
| < 800 ms to the first word, router answers | **Yes**: endpointing 300–500 ms + router 20 ms + Piper 150 ms. | – |
| < 800 ms for Claude answers | **No.** `claude -p` delivers the first token after ~1–2.5 s; the first sentence takes longer. | Realistically 2–3 s. Between 0.8 and 2 s a soft thinking tone (no filler word); the first TTS chunk already at the first comma instead of the end of the sentence. |
| Resuming at the start of the sentence | **Yes** (already laid out today). | Sentence boundary from the TTS buffer, no new synthesis. |
| Speaker separation ("not talking to me") | Only roughly. | Wake word + conversation window + ambience; real speaker recognition later (satellite). |

## 5. Implementation plan, prioritised

### P0: immediate measures without a rebuild (1–2 days, reversible through `.env` / constants)
1. `JARVIS_FOLLOWUP_S=4` in `.env`: the conversation mode that already exists (1.6 × longer after a question).
2. Only the spoken part into the memory: on an interruption the HUD reports the spoken share
   (`Speech.upTo`) to a new endpoint `/api/spoken`; `remember_turn` and `memory.log_turn` take
   that text plus the mark "[interrupted]".
3. Ducking instead of a hard pause: on the first frame above the bar the gain goes to 20 % in 50 ms, pause after
   150 ms instead of 400 ms (`PAUSE_MS`). 30 ms fades on pause/resume against clicks.
4. Stream Piper (`synthesize()` chunk by chunk instead of `synthesize_wav`) and the first TTS chunk at the first
   comma once the sentence gets longer than ~60 characters (`feedSpeech`).
5. `SILENCE_MS` 2000 → 1100 as the base, the existing "unfinished" extension stays. Noticeably
   faster, thinking pauses stay covered by the connector rule. (The proper solution is P1.)

### P1: voice engine on the server (core of the rebuild, 1–2 weeks)
A new package `jarvis/voice/engine/` in Python, one thread per audio session, the audio thread never blocks:

- **Transport:** WebSocket, 16 kHz mono int16, 20 ms frames from the client to the server; server → client
  audio chunks (PCM or Opus) plus JSON events (state, partials, metrics). The browser becomes a
  thin client (AudioWorklet capture → WS, playback from WS). Later the same contract for a
  Python satellite with `sounddevice` in the room. (`server.py` uses `http.server`; WebSockets need
  either `websockets` as a dependency or a small own RFC 6455 handler in the
  standard library. Proposal: `websockets`, see dependencies.)
- **State machine** `IDLE → LISTENING → THINKING → SPEAKING → (INTERRUPTED → LISTENING | RESUMING → SPEAKING) → IDLE`,
  every change with timestamp and trigger in `logs/voice_state.jsonl` and as an event to the HUD.
  The mic stays active in SPEAKING.
- **VAD:** Silero VAD v5 (MIT, ONNX, 32 ms frames, ~1 ms CPU) through the existing `onnxruntime`.
  Two threshold sets: `start` (LISTENING, e.g. 0.5) and `barge` (SPEAKING, e.g. 0.7 plus a
  minimum duration of 150 ms), both in the config.
- **Noise suppression:** RNNoise (BSD-3, ~1 % of a CPU core) as the default before the VAD; DeepFilterNet3
  (MIT/Apache, clearly better, ~1 CPU core, GPU capable) as an option. Both offline.
- **AEC:**
  - Device speaker: the browser AEC stays (free, works). Satellite: speexdsp echo canceller
    or WebRTC APM, reference = the audio the server is currently sending to this client.
  - Sonos: server-side AEC with reference = the decoded live stream. Estimate the delay by
    cross-correlation over the first second of every announcement (expected 1–2 s, varies), then a
    Speex filter with a long tail (≥ 2 s at 16 kHz). Fallback if the estimate does not converge:
    today's level and text comparison. Honestly, this is the least certain building block; the prototype
    has to prove it before it goes in.
  - Echo/Alexa: no reference possible, stays with the text comparison.
- **Semantic endpointing:** an adaptive wait instead of a fixed value:
  `wait = base (350 ms) + unfinished? (+900 ms) + question at the end? (−100 ms) + error history (learns
  from "user kept talking": +100 ms per case, cap 1500 ms)`.
  Sentence completeness from the last partial: the existing connector list from `looksUnfinished`
  plus optionally a small turn model (candidate: pipecat **smart-turn v3**, BSD-2, multilingual,
  ~10 ms CPU; check with recordings in the target language before it goes in).
- **STT with partials:** faster-whisper on a growing buffer every ~700 ms (GPU: 0.2 s per pass,
  bearable), partials only for endpointing and HUD, the final pass with beam 5 at the end of the sentence.
  On the CPU (`small`, 1.3–1.9 s) real streaming is not possible: then endpointing from VAD +
  rules alone, STT once at the end. Own vocabulary stays through `initial_prompt`/`hotwords`
  (Home Assistant, Grafana, Axis, Salto, Telenot, Dedrone, device names collected automatically from
  `config/home.json`). Groq Whisper stays the cloud option for the server without a GPU.
- **Wake word:** openWakeWord on the server side for the satellite; in the browser the worker stays.
  Conversation mode from the engine (no wake word for `followup_s` after every answer).
- **TTS:** the chain stays, but every adapter delivers chunks (Fish streams, Piper streams, Edge already
  streams internally). The server keeps the TTS buffer per sentence, so it knows sentence boundaries for
  pause/resume and does not have to synthesise anything anew when carrying on. Voice choice on the server
  without a GPU: Piper is the only offline streaming voice with RTF ≈ 0.1 on the CPU; quality below
  Fish/ElevenLabs. With a GPU: Chatterbox stays, but as a stream (chunked generate) or not at all.
- **LLM:** token streaming unchanged; the engine starts TTS at the first sentence or the first long clause.

### P2: barge-in and resume logic (on top of P1, 3–5 days)
While SPEAKING: VAD hit → ducking (50 ms ramp) → after 150 ms of confirmed speech PAUSE,
state INTERRUPTED, remember sentence index and byte offset. Short STT on the segment, classification:
- no speech VAD share ≥ 60 % or the transcript empty/a hallucination pattern → RESUMING right away
- backchannel list (`mhm`, `yes`, `right`, `okay`, `aha`, `I see`) → RESUMING at the start of the sentence
- stop list (`stop`, `halt`, `quiet`, `thanks, that's enough`, `never mind`) → IDLE, drop the rest
- a real utterance → LISTENING until endpointing, then THINKING; context to the LLM: the spoken part
  as the assistant answer plus the note "interrupted after sentence N of M"
- silence > `resume_after_ms` (default 1500) without an utterance → RESUMING at the start of the sentence
Sonos: cut the stream plus `volume_set` in parallel; resuming appends the sentences again from the start of the sentence.

### P3: configuration and robustness (2–3 days)
- All thresholds and timeouts in **one** file with comments and defaults. Wish: YAML. So far the
  repo knows `.env` and `config/*.json`; YAML needs PyYAML (a new dependency). Proposal:
  `config/voice.yaml` with PyYAML, or the same structure as `config/voice.json` without a new dependency.
- Audio devices by name (satellite), Windows now, Linux later (`hostos`).
- Failure of STT/TTS/LLM: a clear sentence ("I can't hear well right now"), automatic reconnect of the
  WebSocket sessions, a watchdog per stage.
- Debug mode: writes segments (user, barge-in clips, reference) as WAV with a state log.

### P4: measuring and testing (2–3 days)
- Metrics per stage: endpointing wait, STT, first token, first sentence, TTS first-byte latency, time to the
  first word, barge-in reaction time, false triggers through echo, answering too early (the user kept
  talking), as `logs/voice_metrics.jsonl` and `/metrics` in the Prometheus text format (standard library).
- Test scripts with WAV fixtures: thinking pause in the sentence, interruption with a question, backchannel, cough,
  stop, interruption without a follow-up utterance; they run offline against the engine without a microphone.
- Tuning log in `docs/voice.md`: which values for "too early" / "too late" / "stutters" / "does not hear".

## 6. New dependencies (proposal, not installed yet)

| Package | Purpose | Licence | Offline | Install |
|---|---|---|---|---|
| `silero-vad` (ONNX file, runs through the existing `onnxruntime`) | VAD | MIT | yes | `pip install silero-vad` or just the `.onnx` into `models/` |
| `websockets` | audio channel browser/satellite ↔ server | BSD-3 | yes | `pip install websockets` |
| `pyrnnoise` or an `rnnoise` binding | noise suppression | BSD-3 | yes | `pip install pyrnnoise` (check the name before it goes in) |
| `speexdsp` Python binding (e.g. `speexdsp-python`) | AEC satellite/Sonos | BSD-3 | yes | `pip install speexdsp-python` (check the build on Windows; alternative a WebRTC APM binding) |
| `openwakeword`, `sounddevice` | wake word and microphone for the satellite | Apache-2.0 / MIT | yes | `pip install openwakeword sounddevice` |
| `PyYAML` (only with a YAML config) | config | MIT | yes | `pip install pyyaml` |
| optional `deepfilternet` | better noise suppression | MIT/Apache | yes | `pip install deepfilternet` (pulls in PyTorch, ~2 GB) |
| optional smart-turn v3 (ONNX) | turn detection | BSD-2 | yes | model into `models/`, runs through `onnxruntime` |

`faster-whisper`, `piper-tts`, `onnxruntime`, `av` are present.

## 7. Decisions that are hard to undo (please okay)

1. **Where the engine lives:** the audio pipeline (VAD, AEC, endpointing, state machine) on the server, the
   browser becomes a thin client with a WebSocket. Alternative: everything stays in JS in the browser (done
   sooner, but no Sonos AEC, no satellite, no partials on the server, and on the Linux server
   the same building site once more later). Recommendation: server.
2. **WebSocket library** `websockets` instead of an own build in the standard library.
3. **Config format** YAML (PyYAML) or JSON in the existing `config/` style.
4. **Voice without a GPU:** accept Piper as the local streaming voice (quality compromise), Fish/
   ElevenLabs stay at the front of the chain, Chatterbox only with a GPU and only streamed.
5. **Give up the Echo (Alexa) as a dialogue output**; announcements there stay.

Without an okay on 1 I start with P0 (purely additive, in `.env`, `app.js`, `server.py`), which holds
in any case.
