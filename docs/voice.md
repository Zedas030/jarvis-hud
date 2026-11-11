[← Back to the README](../README.md)

# Voice and voice engine

The provider chain for speech output, the voice engine for listening, interrupting and resuming, and the fallback mode without Claude.

## Speech output: provider chain

```
Fish Audio ─▶ ElevenLabs ─▶ local (Chatterbox GPU · Piper CPU) ─▶ Azure ─▶ Google ─▶ Edge
```

- **One interface:** every provider is an adapter with `configured()`, `synth()` and `balance()`. The order is in `TTS_CHAIN`.
- **Used up** (402, quota/credit error, invalid key, credit API reports 0): the provider is blocked until its reset, and that survives restarts too. The reset is the date according to the API, the start of the month for free tiers, 6 h for an invalid key, otherwise 24 h. After that it is automatically back in its place.
- **Temporary** (network, timeout, 5xx): only the current sentence switches provider and is created again in full. There is no audible break.
- **Transparency:** the dashboard shows the active provider with its remaining credit and the blocked providers with their date. On a switch there is optionally a short announcement.
- **Tone tags** like `[dry]` are applied by providers that support them and removed for all others.
- The chain is plain code, it uses no LLM tokens.
- **English voices:** the defaults are `en-GB-RyanNeural` (Edge, Azure), `en-GB-Chirp3-HD-Charon` (Google), Piper `en_GB-alan-medium` and Chatterbox with `language_id` `en`; for Fish Audio and ElevenLabs the voice id decides (pick an English voice). The overview of what changed from the German edition is in the README under [Language and voice settings](../README.md#language-and-voice-settings).

## Voice engine: listening, interrupting, resuming

Since 2026-11-02 the conversation no longer runs in the browser but in a **full-duplex engine on the
server** (`jarvis/voice/voice_engine.py`). The browser sends the microphone as 20 ms frames over a
WebSocket (`ws://127.0.0.1:8724`) and plays the sentences that come back; everything else (voice activity,
end of sentence, interrupting, resuming, the model call) is decided by the server. A room satellite on the
Linux server will later get the same interface. The analysis that led to the rebuild is in
[`VOICE-ANALYSIS.md`](../VOICE-ANALYSIS.md).

**iPad and iPhone (since 2026-11-10):** paired devices use the engine too. The engine only listens on
127.0.0.1, and `tailscale serve` only forwards the remote port; so the remote HUD passes a WebSocket on
`wss://<pc>.ts.net/voice-ws` byte for byte through to the engine (`server._voice_tunnel`), only for paired
devices with a matching origin. After that the engine checks the page token as on the PC and recognises the
device by its cookie. Nothing changes in the Tailscale configuration. Back to the old path: `engine.remote: false`
in `config/voice.yaml`.

### State machine

```
IDLE → LISTENING → THINKING → SPEAKING → ( INTERRUPTED → LISTENING | RESUMING → SPEAKING ) → IDLE
```

Every change is in `logs/voice_state.jsonl` with timestamp and trigger, and in the HUD log. The
microphone stays open in SPEAKING.

| State | What happens |
|---|---|
| LISTENING | **Silero VAD** (ONNX, ~0.1 ms per frame) detects speech; every ~0.7 s a **partial** from Whisper (only on the GPU). The end of the sentence is **adaptive**: a finished sentence 450 ms, a question 350 ms, unfinished ("and then, um,") 1350 ms, without text 700 ms; whoever keeps talking after sending teaches the engine to wait 100 ms longer (up to +600 ms). Then Whisper with beam 5, the wake word check, the request to `/api/run` (router, cache, Claude, fallback mode as before). |
| THINKING | Speech shortly after sending: the running call is cancelled, both halves are glued together. After 1.2 s without a first word a soft thinking tone. |
| SPEAKING | Sentences come from the token stream (`voice_tts.py`), the first one already at the first comma ("Good evening, Sir,"). Every sentence is synthesised **once** and kept. Talking over him: after 60 ms of speech the volume goes to 20 %, after 150 ms pause → INTERRUPTED (measured 143–218 ms until the pause). |
| INTERRUPTED | The clip is transcribed and classified: **noise / own echo** → carry on; **backchannel** ("mhm", "yes", "right") → carry on; **stop word** → end; **one word without the name** → carry on; **a real utterance** → a new request, with the note "interrupted after sentence N of M" in the context; **nothing said** after 1.5 s → carry on. |
| RESUMING | The interrupted sentence plays from its start, then the rest, without new synthesis. |

On an interruption only the part **actually spoken** plus `[interrupted]` goes into the conversation window
(`state.json` → `window`), not the planned rest.

### Measured (this PC, RTX 5080, Piper as the voice, router answer)

| Stage | Value |
|---|---|
| End of the utterance until the end of the sentence counts | 350–470 ms |
| Whisper `large-v3-turbo` final | 130–160 ms |
| Router | 7–20 ms |
| Piper, first sound | 130–160 ms (2.2 s for the very first sentence: loading the model) |
| **End of the utterance to the first word** | **≈ 0.8–0.9 s** (router), with Claude plus its first sentence (1–3 s) |
| Talking over him until the pause | 143–218 ms |

`scripts/voice_scenarios.py` plays the recordings from `tests/fixtures/voice/` through the real engine
(Silero, Whisper, voice) with a fake model and prints states, transcripts and metrics. The recordings are
English (spoken by the Windows voice "Microsoft Zira"; `scripts/make_voice_fixtures.py` makes Piper versions,
and you can replace them with your own voice).

### Configuration

Everything is commented in [`config/voice.yaml`](../config/voice.yaml) (PyYAML; `JARVIS_VOICE_CONFIG` points
to another file; missing keys fall back to the defaults in `voice_config.py`):

| Block | Most important values |
|---|---|
| `engine` | `enabled` (false = old browser pipeline), `bind`/`port` (8724), `preroll_ms`, `debug_audio` (segments as WAV into `logs/voice_debug/`) |
| `vad` | `start_threshold` 0.55 (user starts), `barge_threshold` 0.70 (user interrupts), `min_speech_ms`, `max_speech_ms` |
| `endpoint` | `base_ms`, `unfinished_extra_ms`, `question_bonus_ms`, `no_text_ms`, `max_ms`, `learn_step_ms`/`learn_max_ms`, `continue_window_ms`, `partial_every_ms`, `turn_model` (optional ONNX turn model) |
| `stt` | `partials` (auto/true/false), `final_beam`, `vocabulary` (becomes Whisper's prompt together with `WHISPER_LOCAL_PROMPT` and the room, device and speaker names from `config/home.json`) |
| `wake` | `conversation_s` (conversation mode after every answer, ×1.6 after a question), `window_s`, `barge_needs_name_with_ambience` |
| `barge` | `duck_ms`, `duck_gain`, `confirm_ms`, `fade_ms`, `clip_silence_ms`, `resume_after_ms`, `backchannel`, `stop_words`, `echo_text_ratio`, `sonos_duck_via_ha` |
| `tts` | `first_chunk_min_chars`/`first_chunk_max_chars`, `min_chunk_chars`, `gap_ms`, `provider_timeout_s` |
| `llm` | `thinking_cue_after_ms`, `first_word_target_ms` |
| `metrics` | `file` (`logs/voice_metrics.jsonl`), `prometheus` (GET `/metrics`) |

Once: `.venv\Scripts\python.exe scripts\fetch_voice_models.py` downloads Silero VAD into `models/`
(without the file a level VAD runs). The engine needs `websockets` and `pyyaml` (`requirements.txt`).

### Metrics

`logs/voice_metrics.jsonl` has one line per conversation (`wait_ms`, `stt_ms`, `llm_first_ms`,
`first_sentence_ms`, `tts_first_ms`, `first_word_ms`, `barge_react_ms`, `interrupted`, `spoken`).
`GET http://localhost:8720/metrics` delivers the same as Prometheus text (histograms, counters
`jarvis_voice_barge_ins_total{outcome="…"}`, `jarvis_voice_echo_false_alarms_total`,
`jarvis_voice_user_continued_total`, `jarvis_voice_late_first_word_total`) for Grafana on the Pi;
`GET /api/voice/metrics` as JSON for the HUD.

### Speakers in the room

| Output | Interrupting | Resuming |
|---|---|---|
| PC / iPad itself | 150 ms until the pause (ducking from 60 ms), the browser echo cancellation knows its own output | at the start of the sentence, without new synthesis |
| Sonos | The stream is cut, the volume lowered through Home Assistant; the Sonos buffers 0.5–1.5 s itself, so ≈ 1 s until silence. The echo of his own voice is filtered by a level model plus text comparison (`EchoGuard`); a speexdsp canceller is used when the package is installed (not tested) | at the start of the sentence, the announcement starts again |
| Echo (Alexa) | Alexa TTS can be neither stopped nor referenced: only an utterance **with "Jarvis"** counts | – |

### Tuning log

First look at `logs/voice_state.jsonl` and `logs/voice_metrics.jsonl` (or
`scripts/voice_scenarios.py`), then change **one** value, save the file (it is read again at the next
conversation), speak ten sentences.

| Symptom | Value | Direction |
|---|---|---|
| Answers in the middle of my sentence | `endpoint.base_ms` | +100 ms; for thinking pauses `unfinished_extra_ms` +300 ms; check that `partials` run (the HUD reports "Partials" at the start, otherwise `no_text_ms` applies) |
| Answers too late | `endpoint.base_ms` | −50 ms, `question_bonus_ms` −50 ms; limit `learn_max_ms` |
| Does not hear sentence starts | `vad.start_threshold` | 0.55 → 0.45; `engine.preroll_ms` +200 |
| Reacts to coughing, keyboard, TV | `vad.start_threshold` | → 0.65; `vad.min_speech_ms` → 300 |
| Stutters while speaking without anyone talking over him | `vad.barge_threshold` | 0.70 → 0.80; `barge.confirm_ms` → 250; with Sonos `barge.echo_text_ratio` → 0.5 |
| Talking over him is not noticed | `vad.barge_threshold` | → 0.60; `barge.confirm_ms` → 120 |
| Stops after "mhm" | `barge.backchannel` | add the word (the way Whisper writes it, see `barge_text` in the HUD log) |
| Waits too long / too short after an interruption | `barge.resume_after_ms` | 1500 ± 500 |
| Clicks on pause/resume | `barge.fade_ms` | 30 → 60 |
| First sound comes late | `tts.first_chunk_max_chars` | 70 → 50; check the voice in `TTS_CHAIN` (Piper streams, Fish streams, Chatterbox does not) |
| Gets technical terms wrong | `stt.vocabulary` | add the term (or `WHISPER_LOCAL_PROMPT`) |
| Starts talking without "Jarvis" while the TV is on | `wake.barge_needs_name_with_ambience` | leave it true; `JARVIS_WAKE=Jarvis` |

## Fallback mode: replacement chain

If the Claude allowance is used up, the CLI does not start or a call breaks off before a word is said, the first available stage of this chain answers:

| Stage | Model | Cost | Note |
|---|---|---|---|
| **Groq** | gpt-oss-120b (`GROQ_MODEL`) | free tier, key on console.groq.com | very fast, the data is not used for training |
| **Gemini** | Gemini 3.5 Flash (`GEMINI_MODEL`) | free tier, key on aistudio.google.com | Google may use free requests for training, so **never** for mail, calendar, health or the daily briefing |
| **Ollama** | `qwen2.5:7b-instruct` locally | free, offline | the last lifeline |

- The replacement models get **the same Jarvis tools** as Claude (the groups the router chooses). `jarvis_mcp.py` runs as a child process for that, and what needs a confirmation card with Claude (e.g. `calendar_add`) needs it here too. Only `pc` (PowerShell) and the full HA MCP server are not available.
- If a provider reports its own limit (HTTP 429), it is skipped until the stated waiting time; an invalid key or a model no longer offered for six hours (reason in the tooltip).
- If a provider has already used a tool and then fails, **no** second one takes over, so that nothing is switched twice.
- Providers without a key are simply skipped. `JARVIS_FALLBACK=ollama` restores the old behaviour, `off` switches the fallback mode off.
- `JARVIS_FALLBACK_PRIVATE` (default `groq,ollama`) decides who may see private requests.
- In the usage log the answers appear as `fallback:groq`, `fallback:gemini` or `fallback:ollama`.

```
GROQ_API_KEY=...
GEMINI_API_KEY=...
```
