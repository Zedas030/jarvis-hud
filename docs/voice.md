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
| `barge` | `duck_ms`, `duck_gain`, `confirm_ms`, `fade_ms`, `clip_silence_ms`, `resume_after_ms`, `resume_skip_ratio`, `backchannel`, `stop_words`, `echo_text_ratio`, `sonos_duck_via_ha` |
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
| Says a sentence twice after hearing himself | `barge.resume_skip_ratio` | 0.6 (smaller = goes on with the next sentence more often) |
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

## Home Assistant Voice PE

Nabu Casa's speaker with a microphone is only the ear in the room. It detects the wake word itself ("Hey Jarvis") and sends what was said to the PC through Home Assistant. There Whisper on the graphics card recognises the text, Jarvis answers it and speaks the answer in his own voice on the room's speaker. Home Assistant is only the messenger, because the Voice PE does not work without Home Assistant. This is how it goes:

```
Voice PE ──"Hey Jarvis"──▶ Home Assistant (Pi) ──audio──▶ /api/hear on the PC: Whisper large-v3-turbo on the GPU ──▶ text
                              └──text──▶ /api/ask: router, Claude, tools ──▶ answer on the room's speaker (Sonos/Echo)
PC off: Whisper on the Pi ──▶ Home Assistant's own agent ("light off", timers) ──▶ Piper ──▶ Voice PE
```

- **What Jarvis can do:** everything he can do in the HUD, including "light off" (his router, 0 tokens). He is told the room the Voice PE is assigned to in Home Assistant. If the room has no speaker, the Voice PE reads the answer out itself (Piper).
- **Which room he answers in (since 2026-11-19, `room_scope.answer_room`):** where you are, not necessarily at the satellite that heard you. If exactly one presence sensor sees somebody (`room_scope.sensors` in `config/home.json`, the bathroom counts as the bedroom), that room answers. If several rooms see somebody, the satellite's room answers (its area in HA). If nobody is seen right now (sitting still, a radar gap), he takes the room HA last has you in (`input_select.user_room`), and only without that the satellite's room. The same room then also applies to commands without a room ("light on"). The reason: the ReSpeaker belongs to the bedroom but stood in the living room and always answered in the bedroom. With `JARVIS_ROOM_SCOPE=0` only the satellite's room counts again.
- **"Jarvis, stop":** the wake word alone already stops what he is saying. "Stop", "stop it", "be quiet" or "that's enough" on its own is then handled by the router locally and without an answer (`router._stop_talk`, answers as a quiet result; `remote_ask` does not speak quiet results). Before, a bare "stop" went to Claude.
- **How fast:** Jarvis speaks the first sentence as soon as it is finished and appends every further one to the same announcement while Claude is still writing, like at the PC (`remote_ask.RoomVoice`, the same sentence splitter as the voice engine). Compared with the microphone at the PC about 1 s is added: Home Assistant waits a fixed 0.7 s or so after the last word (at the PC the engine learns 0.35–0.45 s), and the Sonos needs a moment until the announcement starts. Interrupting works through the wake word: as soon as a satellite listens, Jarvis stops speaking everywhere, at the PC, on the Sonos and on the Echo (`satellite_quiet.py`, `JARVIS_SATELLITE_HUSH`). For that he briefly sets the Echo to volume 0, because Alexa's reading cannot be cancelled; afterwards the old volume comes back. Otherwise the microphone would have recorded his own answer as well. About 1 s of him can still be in it at the start. If a sentence fails on the speaker, the Voice PE reads the rest out itself.
- **TV and YouTube:** the Voice PE cannot tell the user's voice from the TV's. With YouTube or the TV playing loudly it did not find the end of the sentence, listened up to Home Assistant's limit (15 s), and Whisper wrote down the TV as well. So while a living room satellite listens, Jarvis turns every program of the PC except himself down to 10 % of its volume (per program, the master volume stays) and mutes the soundbar (Samsung multiroom API, `JARVIS_SOUNDBAR_HOST`). When the satellite stops listening, everything is as before, at the latest after 20 s; a soundbar the user muted by hand stays muted (`listen_duck.py`, `JARVIS_LISTEN_DUCK`; which rooms: `JARVIS_LISTEN_DUCK_WHERE`, default `living room`). If the TV's sound runs through its own speakers, Jarvis cannot turn it down: the 2015 Samsung is not in Home Assistant.
- **Questions back without a wake word:** if Jarvis' answer ends with a question (also "Do you mean X? If so, …"), he waits until the Echo or Sonos is done and then calls `assist_satellite.start_conversation` for the satellite that asked. The Voice PE plays a quarter second of silence (`www/jarvis/silence.wav`, it never says anything) and listens without a wake word; if nothing comes for 5 s, it closes again. Home Assistant's own `continue_conversation` does not work here: it would open the microphone while Jarvis is still speaking on the Echo (`satellite_followup.py`, `JARVIS_SAT_FOLLOWUP`). If the answer to a question back starts with yes or no, Jarvis takes that as agreement or refusal, even if Whisper mishears the rest.
- **Why on the PC:** measured on 2026-11-17 with the same five sentences. Through Home Assistant, Tailscale and the GPU a sentence takes 0.16 to 0.29 s, Whisper on the Pi (base-int8, Raspberry Pi 4) 7 to 17 s, and it mishears more often.
- **Learning from false wake-ups (since 2026-11-22, `wake_learn.py`):** if a satellite wakes up without you having called it and you say "stop" (also "stop it", "never mind" …), Jarvis remembers that as a false wake-up, provided he was saying nothing at that moment that the wake-up could have cut off. If you cut off an answer with "Jarvis, stop", that does not count, and neither does a microphone Jarvis opened himself for a question back. If nobody says anything after the wake-up, or what was said was not meant for him (`remote_ask.IGNORE`), it is only a suspected false wake-up. The satellites detect the word on the device: there is no score and no audio of the trigger moment in Home Assistant. For each case one line is stored in `logs/wake_false.jsonl` (time, satellite, room, the sensitivity that was set, what was playing: TV, music, sound on the PC; never the words) and with it the audio in `logs/wake_false/`: `…-after.wav` is what the satellite heard after waking up, `…-trigger.wav` is the last 6 s before it from the PC's microphone, when the satellite stands in the PC's room (`JARVIS_WAKE_LEARN_PC_ROOM`; until then the audio is only in memory). For "not meant for me" no audio is stored. Two things follow from it. **Sensitivity:** after 3 confirmed false wake-ups of one satellite in 7 days (counted since the sensitivity was last changed, `JARVIS_WAKE_LEARN_PROPOSE`) Jarvis proposes to set it one step less sensitive; that only happens after your yes on the card, and after a no he does not ask again for a week. **Model:** `scripts\wake_negatives.py --apply` copies the clips into the training set in WSL as counter-examples (`data/samples/jwfalse_trig_1` and `jwfalse_room_1`); it leaves out a trigger clip in which Whisper hears "Jarvis". Training is done by hand with `tools/07k_jw2.sh` (from 20 clips, jw2 from jw1; never run so far), then compare as before (`tools/12j_eval_jw.sh`), build the firmware and flash it. "How often were you woken up by mistake?" is answered by Jarvis locally. Off: `JARVIS_WAKE_LEARN=0`. At the PC itself there is no trained wake word (Whisper plus the name check); there a "stop" is always the cutting off of an answer and is not counted.
- **PC off:** the integration notices that while warming up, already while you are speaking. Then Whisper on the Pi gets the same audio, and Home Assistant's own agent switches lights and sets timers. For everything else the Voice PE says: "Jarvis cannot be reached right now, the PC is probably off." That is slow, but it works.
- **Approval cards** (add an appointment, a command on the Pi) are declined by Jarvis at once from the Voice PE, and he says so. That only works at the PC or in the HUD.

**Building blocks in the repo**

| File | Where to | What |
|---|---|---|
| `ha/custom_components/jarvis/` | `custom_components/jarvis` in the HA config folder | Speech recognition `stt.jarvis` (`/api/hear`, falls back to Whisper on the Pi) and conversation agent `conversation.jarvis` (`/api/ask`, falls back to Home Assistant's agent), both with the same key |
| `pi/voice/compose.yml` | `/opt/assist-voice/compose.yml` on the Pi | Reserve for a PC that is switched off: Wyoming Whisper (speech → text, port 10300) and Piper (text → speech, port 10200), both on 127.0.0.1 only |
| `scripts/ha_voice_setup.py` | runs on the PC | connects Whisper and Piper to HA, creates the pipeline "Jarvis" (`stt.jarvis`, `conversation.jarvis`, `prefer_local_intents` off), sets the wake word |

**Setting up**

1. On the Pi: `cd /opt/assist-voice && docker compose up -d` (downloads about 570 MB of images and the models the first time).
2. Restart Home Assistant so that it finds the integration "Jarvis".
3. Get the key (works at the PC): in the HUD **📱 Devices → New code**, then open `https://<your-pc>.<tailnet>.ts.net/pair.html?key` in the browser, enter the code, name "Home Assistant", **Get key**, **Copy**. It is shown only this one time and afterwards stands in the device list with 🔑. It only opens `/api/ask` and `/api/hear`, never the HUD.
4. In Home Assistant: *Settings → Devices & services → Add integration → Jarvis*. There you enter: the address (`https://<your-pc>.<tailnet>.ts.net`), the key and the PC's Tailscale IP (`tailscale ip -4 <pc-name>`). The IP is needed because the Pi, with `accept-dns` off, cannot resolve the `ts.net` names.
5. Plug in the Voice PE. The Home Assistant app on the phone reports "New device found"; enter the Wi-Fi there. Then give the device its **area** in HA (e.g. Bedroom).
6. At the PC: `.venv\Scripts\python.exe scripts\ha_voice_setup.py --apply`. Without `--apply` the script only shows what is missing. Run it again after an update of the integration.
7. Try it: *"Hey Jarvis, how healthy is the battery?"*, *"Hey Jarvis, light off."*

**Secrets:** the key from step 3 is entered only in Home Assistant's integration dialog; `scripts/ha_voice_setup.py` reads `HA_URL` and `HA_TOKEN` from `.env`. How to change the key is described under "Changing the key" below.

**Wake word twice:** if the Voice PE stands in the same room as the PC, both listen for "Jarvis". Then either choose "Okay Nabu" on the Voice PE (in HA at the device under *Wake word*) or switch the wake word off at the PC (`JARVIS_WAKE`).

**Second satellite: ReSpeaker Lite in the bedroom (since 2026-11-19).** Seeed ReSpeaker Lite (XMOS XU316, two microphones) with a soldered-on XIAO ESP32-S3, microphone only like the Voice PE. Firmware based on [formatBCE/Respeaker-Lite-ESPHome-integration](https://github.com/formatBCE/Respeaker-Lite-ESPHome-integration) (release `2026.11.2`, needs ESPHome 2026.11), with the same adjustments as on the Voice PE: a wake word "Hey / Okay / Hello Jarvis" (own models hj1 + hj4, both report "Hey Jarvis" so that HA's duplicate lock works across all satellites), the same thresholds, giving up after 5 s without speech, wake sound off. In HA: device "ReSpeaker Bedroom", area Bedroom, slot 1 "Hey / Okay / Hello Jarvis", slot 2 "Okay Nabu", "Very sensitive", all its own sounds off. The answer therefore comes from the bedroom Sonos. From 22:00 Jarvis already speaks more softly there before "good night" (`JARVIS_SONOS_LATE_VOLUME`, here 0.35 instead of 0.63, from `JARVIS_SONOS_LATE_FROM` until 7:00), after "good night" at the night volume (`JARVIS_SONOS_NIGHT_VOLUME`).

- **Building:** in WSL `~/work/hjww/tools/14_fw_rl.sh` (build in `fw/build/rl`, ESPHome venv `fw/venv-esphome11` from `tools/13_esphome11.sh`). An update then works over Wi-Fi: `fw/venv-esphome11/bin/esphome upload fw/build/rl/respeaker-bedroom.yaml --device <IP>`.
- **XMOS firmware:** from the factory it ran the I2S firmware 1.0.8. On its first start the ESPHome firmware installed 1.1.0 itself (48 kHz, its own channel for the wake word). That only works from 1.0.8; with older firmware update by USB DFU first, at the socket next to the audio jack. Do not cut the power while the LED blinks white.
- **Antenna:** the black foil on the cable is the Wi-Fi antenna, do not tear it off. Without it the signal was at −92 dBm and HA kept losing the connection; with it, it is about −53 dBm.
- **Entering the Wi-Fi again** (for instance after a factory reset): Improv over USB or Bluetooth. The USR button is not soldered to D2, so Improv BLE does not ask for a button press.
- **LED:** purple slow = listening, purple fast = thinking, red fast = error, red slow = no connection to HA, off = waiting.
- **Wake word "Jarvis" (since 2026-11-19, firmware `2026.11.2-jarvis2`):** with hj1/hj4 ("Hey/Okay Jarvis") the ReSpeaker hardly ever woke up for the user, "Okay Nabu" and "Kenobi" at once. The user pronounces the name without the r; the old models mostly knew the English pronunciation with an r. So there is now a model of its own, **jw1**, for the single word "Jarvis". It was trained with 77 recordings of the user (recording page, each distorted 30 times), 13,500 r-less "Jarvis" from 6 German Piper voices, 14,000 English speakers without the r, and counter-examples such as Davis, Travis, Java, Jawohl or Dschungel. The starting point was hj1. Test on 8 held-back recordings of the user at threshold 0.98: 0 of 8 missed, 0.94 false wake-ups per hour of party chatter (DipCo). hj1 missed 6 of 8. In HA: slot 1 "Jarvis", slot 2 "Okay Nabu", sensitivity "Moderately" (0.98; "Slightly" = 0.99, "Very" = 0.97). If the ReSpeaker stands in the same room as the PC, both wake up on "Jarvis". Building: WSL `tools/07j_jw1.sh` (training), `tools/12j_eval_jw.sh` (comparison), `tools/15_fw_rl_jw.sh` (firmware, build `fw/build/rl_jw`).
- **After moving or reinstalling HA (2026-11-19, HA onto the new Pi):** both satellites were connected, but both wake word slots stood at "none", and neither reacted any more. Afterwards check the slots of every satellite in HA and set them again (Voice PE: "Hey / Okay / Hello Jarvis" + "Okay Nabu", ReSpeaker: "Jarvis" + "Okay Nabu"). Slow red pulsing on the satellite means: no connection to HA. The cause: during the move HA had stored "unavailable" as the last state of the select (`.storage/core.restore_state`), which it cannot restore at start; it chooses nothing itself because the firmware reports two active words. At the restart at 16:09 it happened once more.

**Changing the key:** at the PC under **📱 Devices** delete the entry with 🔑, get a new one, then in HA at the Jarvis integration *⋮ → Reconfigure* and enter the new key. HA stores the key in its own configuration data (`.storage/core.config_entries`), not in a YAML file.

**Moving to a home server:** if Jarvis later runs on hardware of its own, only the address of the integration changes in Home Assistant (*⋮ → Reconfigure*). Without a graphics card there, rather take `small-int8` or larger and measure first.
