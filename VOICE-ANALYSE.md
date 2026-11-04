# Analyse Sprachpipeline: Zuhören, Unterbrechen, Weitersprechen

> Umgesetzt am 02.11.2026 (Branch `feat/voice-engine`): P0–P4 aus Abschnitt 5 sind gebaut, siehe README →
> „Sprach-Engine“. Offen bleiben die iPad-Anbindung über `tailscale serve`, der Raum-Satellit und die
> Erprobung der Sonos-Echo-Unterdrückung mit Referenzsignal.

Stand 02.11.2026, Phase 1 (nur Analyse, kein Code geändert). Grundlage: vollständiges Lesen von
`ui/app.js`, `server.py`, `jarvis/voice/*`, `jarvis/core/runtime.py`, Logs (`usage.jsonl`,
`wake_device.log`, `spotify.log`) sowie Messungen auf diesem PC (RTX 5080, i7, Python 3.12).

## 1. Ist-Pipeline

Die gesamte Gesprächslogik (Mikrofon, Pegel-VAD, Satzende, Unterbrechen, Wiedergabe) lebt im
**Browser** (`ui/app.js`, ~2900 Zeilen). Der Server ist ein zustandsloser HTTP-Dienst mit drei
Sprach-Endpunkten: `/api/listen` (Clip → Text), `/api/run` (Text → NDJSON-Stream), `/api/speak`
(Satz → Audio). Es gibt keinen dauerhaften Audiokanal zwischen Browser und Server.

| Stufe | Heute | Datei |
|---|---|---|
| Audio-Capture | `getUserMedia` mit Browser-AEC und -Rauschunterdrückung, AGC aus. AudioWorklet „jarvis-cap“ hält 600 ms Pre-Roll, baut 16-kHz-WAV in JS. Fallback MediaRecorder (webm). | `app.js` → `Cap`, `Live.enable` |
| Takt | AudioWorklet „jarvis-tick“ ruft `Live.loop()` alle ~16 ms (läuft auch im Hintergrund-Tab). | `TICK_WORKLET` |
| VAD | Reiner RMS-Pegel (AnalyserNode, 1024 Samples) gegen kalibrierte Schwelle (Median der Raumstille × 3,2, min. 0,015) plus langsam nachgeführter Hintergrundpegel. Kein ML-VAD. | `Live.calibrate`, `Live.loop` |
| Satzende | Fixe Stille `SILENCE_MS = 2000`. Danach Transkription; klingt der Text unfertig (Regex auf Konnektoren, fehlender Punkt), wird 3 s weiter gewartet (`UNFINISHED_HOLD_MS`). Wer bis 7 s nach dem Senden weiterspricht, bricht den laufenden Claude-Aufruf ab, Text wird zusammengeklebt (`CONTINUE_MS`). | `looksUnfinished`, `Live.send` |
| Wake Word | `.env`: `JARVIS_WAKE=Jarvis` = Modus **text**: jede Äußerung wird mit Whisper transkribiert und per Regex auf „Jarvis“-Schreibweisen geprüft. `oww` am PC ist nicht installiert (openwakeword, sounddevice fehlen in `.venv`). Auf iPad/iPhone läuft openWakeWord im Web Worker (onnxruntime-web, Modell `jarvis_v1`). Nach einem bloßen „Jarvis“ 7 s Fenster. **Konversationsmodus existiert bereits** (`JARVIS_FOLLOWUP_S`), steht aber auf 0 = aus. | `wakeword.py`, `ui/wakeword.js`, `Wake` |
| STT | Ganzer Clip per POST. faster-whisper `large-v3-turbo` fp16 auf CUDA, Beam 5, `vad_filter`, `initial_prompt` + `hotwords`; CPU-Fallback `small` int8. Keine Partials, kein Streaming. Geister-Filter für Stille-Halluzinationen. Der Ressourcen-Manager wirft das GPU-Modell bei Speicherdruck ab (Neuladen gemessen 3,5 s). | `voice.py` |
| LLM | Router (`router.py`) beantwortet ~20 % lokal. Sonst `claude -p --output-format stream-json` mit vorgewärmtem Prozess (`lean_prewarm`), Token-Deltas als NDJSON zum Browser. Notbetrieb Groq → Gemini → Ollama. | `runtime.py`, `server.py::_lean` |
| TTS | Browser schneidet fertige Sätze aus dem Delta-Strom (`feedSpeech`, min. 28 Zeichen) und fragt sie **einzeln** bei `/api/speak` an; Anfragen überlappen mit Claude. Kette `fish,elevenlabs,local,azure,google,edge`. Nur Fish streamt (erste Bytes ~0,7 s), alle anderen liefern den Satz am Stück. `local` = Chatterbox-GPU-Server (nicht gestreamt) bzw. Piper CPU. Piper hat eine Streaming-API, die ungenutzt ist. | `tts_chain.py`, `local_tts_server.py` |
| Playback | MediaSource (mp3-Stream) → `<audio>` → AudioContext-Fallback. Sonos: ein Live-MP3-Stream pro Antwort über HA `announce`, 1,5 s Vorlauf (`LIVE_AHEAD_S`). Echo: nur Text an Alexa-TTS, Dauer wird geschätzt (15 Zeichen/s + 1 s). | `playUrl`, `sonos.py`, `alexa_voice.py` |
| Barge-in | Während `Live.speaking`: Pegel über `bargeNow()` = max(Raum × 3, Hintergrund × 2,2, 0,02; bei Sonos zusätzlich eigener Echo-Pegel × 1,8) startet eine Aufnahme. Nach 400 ms anhaltendem Pegel `pauseSpeech()`. Clip endet nach 550 ms Stille (1000 ms wenn pausiert), geht an `/api/listen`, dann Regex-Klassifikation: Name, Füllwort, Stopp-Wort, Textabgleich mit eigener Antwort (`echoOf`), TV/Sport-Ambiente. | `Barge`, `bargeNow`, `pauseSpeech` |
| Fortsetzen | Satzgranular: der unterbrochene Satz wird von vorn abgespielt (`item.again`). Beim Sonos werden alle wartenden Sätze neu synthetisiert. | `resumeSpeech`, `playSpeechQueue` |
| Nebenläufigkeit | Server: `ThreadingHTTPServer`, ein `RUN_LOCK`, `EMIT_LOCK`. Browser: Single-Thread-JS plus Worklets. **Keine Zustandsmaschine**; der Zustand liegt in ~15 Flags (`Live.on/speaking/armed/sending/continuing/partial/ptt`, `Speech.active/paused/cancelled`, `Barge.rec/busy`, `running`, `pendingConfirm`, `briefingActive`, `Wake.until`). | – |
| Metriken | Nur `logs/wake_device.log` (eine Zeile pro angefangenem Unterbrechen) und `usage.jsonl` (Gesamtzeit je Antwort). Keine Stufenlatenzen, kein Prometheus. | `BargeLog`, `log_usage` |

## 2. Latenz je Stufe

Gemessen (M) auf diesem PC am 02.11.2026 bzw. aus Logs 14.–31.10.; geschätzt (S) aus Code und Kommentaren.

| Stufe | Wert | Quelle |
|---|---|---|
| Stille bis Satzende gilt | **2000 ms** fix, +3000 ms wenn „unfertig“ | `SILENCE_MS`, `UNFINISHED_HOLD_MS` |
| WAV bauen, POST lokal | 20–50 ms | S |
| STT GPU `large-v3-turbo`, 7,5-s-Clip | **0,24 s** (Beam 5), 0,18 s (Beam 1) | M |
| STT CPU `small` int8, 7,5-s-Clip | **1,87 s** (Beam 5), 1,33 s (Beam 1) | M (Vorschau auf den Server, i7-8700 ähnlich) |
| STT-Modell neu laden (nach Abwurf) | 3,5 s GPU, 1,1 s CPU | M |
| Router, lokale Antwort | **21 ms** p50, 444 ms p90 (n = 108) | `usage.jsonl` |
| Claude Haiku, ganze Antwort | **4,4 s** p50, 8,8 s p90 (n = 248) | `usage.jsonl` |
| Claude Sonnet, ganze Antwort | 5,6 s p50, 14,8 s p90 (n = 73) | `usage.jsonl` |
| Claude: erster Token / erster Satz | ~1–2,5 s / ~1,5–3 s | S (vorgewärmter Prozess; nicht geloggt) |
| TTS Fish, erste Bytes | ~0,7 s (ganz ~2,5 s) | Kommentar `tts_chain.py` |
| TTS Piper CPU | **0,19 s** für 1,4 s Audio, 0,54 s für 5,4 s Audio (RTF 0,10–0,14) | M |
| TTS Chatterbox GPU | ~1–3 s je Satz, nicht gestreamt | S |
| Playback-Start Gerät | 50–150 ms (MediaSource) | S |
| Sonos: Vorlauf | +1,2 s `LEAD_S` + HA-Aufruf 0,3–0,5 s | Code |
| Echo (Alexa) | +1 s `LAG_S`, Dauer nur geschätzt | Code |

**Ende der Äußerung bis erstes hörbares Wort (heute):**

| Pfad | Rechnung | Summe |
|---|---|---|
| Router, PC-Lautsprecher | 2,0 + 0,05 + 0,25 + 0,02 + 0,7 + 0,1 | **≈ 3,1 s** |
| Claude Haiku, PC-Lautsprecher | 2,0 + 0,05 + 0,25 + ~2,0 (erster Satz) + 0,7 + 0,1 | **≈ 5,1 s** |
| dito über Sonos | + 1,5–1,7 | ≈ 6,7 s |

Ziel laut Aufgabe: < 800 ms. Der größte Einzelposten ist der fixe 2-s-Stilletimer, der zweite die
Zeit bis zum ersten vollständigen Satz aus Claude, der dritte die TTS-Erstlatenz.

**Barge-in-Reaktionszeit (heute):**

| Ausgabe | bis Ausgabe stoppt | bis Entscheidung (weiter / neue Frage) |
|---|---|---|
| PC-Lautsprecher | 400 ms Pegel + 16 ms Takt ≈ **420–450 ms** (Ziel ≤ 200) | + 550 ms Stille + 0,25 s STT ≈ 1,2 s nach dem letzten Wort |
| Sonos | ≈ 420 ms + 1,5 s Puffer ≈ **2 s** | wie oben |
| Echo | laufender Satz **nicht** stoppbar, nur noch nicht gesendete Blöcke | – |

## 3. Warum es heute nicht zuverlässig funktioniert

### 3.1 Aussprechen lassen
- Der Stilletimer ist ein Festwert und für beide Ziele falsch: 2 s sind zu lang für die Antwortlatenz
  und zu kurz für echte Denkpausen (die „unfertig“-Erkennung greift erst nach der Transkription, also
  frühestens 2,3 s nach dem Verstummen, und verlängert dann pauschal um 3 s).
- Weil es keine Partials gibt, kann vor Ende des Clips nichts über Satzvollständigkeit entschieden werden.
- Spricht der Nutzer nach dem Senden weiter, wird der laufende Claude-Aufruf abgebrochen (Tokens weg) und
  die Antwort beginnt von vorn. Das ist eine Reparatur, keine Vermeidung.
- Pegel-VAD mit AGC aus: leise Satzanfänge, Frikative und Abstand zum Mikro verschieben die Erkennung;
  Husten und Tastatur liegen dagegen oft über der Schwelle.

### 3.2 Unterbrechen
- Die Hürde für den Nutzer ist hoch: 3 × Raumstille, 2,2 × Hintergrund, und bei Sonos/Echo zusätzlich
  1,8 × der eigene Echo-Pegel. In `wake_device.log` (28.–31.10., Echo Wohnzimmer) liegt bei den meisten
  Antworten der „lauteste Moment“ **unter** der Hürde (z. B. 0,085 vs. 0,134; 0,105 vs. 0,125;
  0,074 vs. 0,122) mit „0 Clips geprüft“. Das sind Versuche, dazwischenzureden, auf die nichts passierte.
  Erfolgreiche Unterbrechungen sind fast ausschließlich ein isoliertes „Jarvis.“.
- Es gibt **keine Echo-Unterdrückung für externe Lautsprecher**: Die Browser-AEC kennt nur den
  Ausgang des Geräts selbst. Für Sonos liegt das Referenzsignal auf dem Server, für Alexa-TTS existiert
  gar keines. Der Ersatz (Pegelvergleich + Textabgleich `echoOf`) ist der Grund für die hohe Hürde.
- 400 ms anhaltender Pegel vor der Pause, kein Ducking davor. Auf dem Sonos kommen 1,5 s Puffer dazu.
- Kurze Clips erzeugen Whisper-Halluzinationen („Tu mozno ma genau ako“, 31.10. 11:48), die als „Worte“
  zählen könnten; nur der fehlende Name hat es verhindert.

### 3.3 Weitermachen
- Die Klassifikation (Rauschen / Rückmeldung / Stopp / echte Äußerung) passiert erst, wenn der Clip zu
  Ende ist, und nur per Regex. Ein Räuspern mit zwei erkannten Wörtern in der Pause wird zur neuen Frage.
- Beim Sonos verwirft `stop(hold=True)` den Stream; beim Weitersprechen werden alle wartenden Sätze
  neu synthetisiert (Kosten, Latenz, hörbare Lücke).
- Im Gesprächsverlauf steht die **ganze** Antwort (`commands.remember_turn(message, text)` in
  `server.py::_lean`), nicht der gesprochene Teil. Der Browser kennt den gesprochenen Anteil
  (`Speech.upTo`), der Server nicht.
- „Unterbrochen, dann Stille → weiter“ funktioniert nur für Clips ohne Worte; ein Weitersprechen
  nach frei wählbarer Zeit ist nicht vorgesehen.

### 3.4 Struktur
- Kein expliziter Zustand, kein Log der Zustandswechsel. Fehler sind nur mit Konsole im Browser nachvollziehbar.
- Alle Schwellwerte sind Konstanten in `app.js`; die README empfiehlt, dort zu editieren.
- Die Logik hängt am Browser-Tab (iOS-Audio-Unlock, Hintergrund-Drosselung, pro Gerät andere Pegel).
- Für den 24/7-Linux-Server ohne Mikrofon ist die Trennung Browser = Ohr, Server = Hirn grundsätzlich
  richtig, aber AEC, VAD und Endpointing gehören dann auf die Seite, die das Referenzsignal und die
  Rechenleistung hat: den Server.

## 4. Was technisch erreichbar ist, und was nicht

| Ziel | Erreichbar | Kompromiss |
|---|---|---|
| Barge-in ≤ 200 ms am Gerätelautsprecher | **Ja**: Ducking beim ersten VAD-Frame (~60–100 ms), Pause nach 150 ms bestätigter Sprache. | – |
| Barge-in ≤ 200 ms am Sonos | **Nein.** Der Sonos puffert selbst 0,5–1,5 s; HA-Aufrufe dauern 0,3–0,5 s. | Realistisch 0,8–1,2 s: Stream sofort kappen, parallel `volume_set` (Ducking) über HA, Vorlauf von 1,5 auf ~0,8 s. |
| Barge-in am Echo | **Nein.** Alexa-TTS ist weder stopp- noch referenzierbar. | Echo nur für Durchsagen, nicht für Dialoge; Dialoge über Sonos oder eigenen Lautsprecher. |
| < 800 ms bis zum ersten Wort, Router-Antworten | **Ja**: Endpointing 300–500 ms + Router 20 ms + Piper 150 ms. | – |
| < 800 ms bei Claude-Antworten | **Nein.** `claude -p` liefert den ersten Token nach ~1–2,5 s; der erste Satz braucht länger. | Realistisch 2–3 s. Zwischen 0,8 und 2 s ein leiser Denk-Ton (kein Füllwort); erster TTS-Chunk schon am ersten Komma statt am Satzende. |
| Fortsetzen am Satzanfang | **Ja** (heute schon angelegt). | Satzgrenze aus dem TTS-Puffer, nicht neu synthetisieren. |
| Sprecher-Trennung („nicht mit mir geredet“) | Nur grob. | Wake Word + Konversationsfenster + Ambiente; echte Sprechererkennung später (Satellit). |

## 5. Umsetzungsplan, priorisiert

### P0: Sofortmaßnahmen ohne Umbau (1–2 Tage, rückgängig per `.env` / Konstanten)
1. `JARVIS_FOLLOWUP_S=4` in `.env`: Konversationsmodus, den es schon gibt (nach einer Frage 1,6 × länger).
2. Nur den gesprochenen Teil ins Gedächtnis: HUD meldet beim Unterbrechen den gesprochenen Anteil
   (`Speech.upTo`) an einen neuen Endpunkt `/api/spoken`; `remember_turn` und `memory.log_turn` nehmen
   diesen Text plus Marke „[unterbrochen]“.
3. Ducking statt harter Pause: beim ersten Frame über der Hürde Gain in 50 ms auf 20 %, Pause nach
   150 ms statt 400 ms (`PAUSE_MS`). Fades 30 ms bei Pause/Weiter gegen Knacken.
4. Piper streamen (`synthesize()` Chunk-weise statt `synthesize_wav`) und erster TTS-Chunk am ersten
   Komma, wenn der Satz länger als ~60 Zeichen wird (`feedSpeech`).
5. `SILENCE_MS` 2000 → 1100 als Basis, die vorhandene „unfertig“-Verlängerung bleibt. Spürbar
   schneller, Denkpausen bleiben über die Konnektor-Regel abgedeckt. (Die richtige Lösung ist P1.)

### P1: Sprach-Engine auf dem Server (Kern des Umbaus, 1–2 Wochen)
Neues Paket `jarvis/voice/engine/` in Python, ein Thread pro Audio-Session, Audio-Thread blockiert nie:

- **Transport:** WebSocket, 16 kHz mono int16, 20-ms-Frames vom Client zum Server; Server → Client
  Audio-Chunks (PCM oder Opus) plus JSON-Ereignisse (Zustand, Partials, Metriken). Der Browser wird
  dünner Client (AudioWorklet-Capture → WS, Playback aus WS). Später derselbe Vertrag für einen
  Python-Satelliten mit `sounddevice` im Raum. (`server.py` nutzt `http.server`; WebSockets brauchen
  entweder `websockets` als Abhängigkeit oder einen kleinen eigenen RFC-6455-Handler in der
  Standardbibliothek. Vorschlag: `websockets`, siehe Abhängigkeiten.)
- **Zustandsmaschine** `IDLE → LISTENING → THINKING → SPEAKING → (INTERRUPTED → LISTENING | RESUMING → SPEAKING) → IDLE`,
  jeder Wechsel mit Zeitstempel und Auslöser in `logs/voice_state.jsonl` und als Ereignis ans HUD.
  Mikro bleibt in SPEAKING aktiv.
- **VAD:** Silero VAD v5 (MIT, ONNX, 32-ms-Frames, ~1 ms CPU) über das vorhandene `onnxruntime`.
  Zwei Schwellensätze: `start` (LISTENING, z. B. 0,5) und `barge` (SPEAKING, z. B. 0,7 plus
  Mindestdauer 150 ms), beide in der Config.
- **Rauschunterdrückung:** RNNoise (BSD-3, ~1 % CPU-Kern) als Standard vor dem VAD; DeepFilterNet3
  (MIT/Apache, deutlich besser, ~1 Kern CPU, GPU-fähig) als Option. Beide offline.
- **AEC:**
  - Gerätelautsprecher: Browser-AEC bleibt (kostenlos, funktioniert). Satellit: speexdsp-Echo-Canceller
    oder WebRTC-APM, Referenz = das Audio, das der Server gerade an diesen Client schickt.
  - Sonos: serverseitige AEC mit Referenz = der dekodierte Live-Stream. Verzögerung per
    Kreuzkorrelation auf der ersten Sekunde jeder Durchsage schätzen (erwartet 1–2 s, schwankt), dann
    Speex-Filter mit langem Schweif (≥ 2 s bei 16 kHz). Fallback, wenn die Schätzung nicht konvergiert:
    heutiger Pegel- und Textvergleich. Ehrlich gesagt ist das der unsicherste Baustein; der Prototyp
    muss das vor dem Einbau beweisen.
  - Echo/Alexa: keine Referenz möglich, bleibt beim Textabgleich.
- **Semantisches Endpointing:** adaptive Wartezeit statt Festwert:
  `warte = basis (350 ms) + unfertig? (+900 ms) + Frage-am-Ende? (−100 ms) + Fehlerhistorie (lernt
  aus „Nutzer hat weitergesprochen“: +100 ms je Fall, Obergrenze 1500 ms)`.
  Satzvollständigkeit aus dem letzten Partial: die vorhandene Konnektor-Liste aus `looksUnfinished`
  plus optional ein kleines Turn-Modell (Kandidat: pipecat **smart-turn v3**, BSD-2, mehrsprachig
  inkl. Deutsch, ~10 ms CPU; vor dem Einbau mit deutschen Aufnahmen prüfen).
- **STT mit Partials:** faster-whisper auf wachsendem Puffer alle ~700 ms (GPU: 0,2 s je Durchlauf,
  tragbar), Partials nur fürs Endpointing und HUD, finaler Lauf mit Beam 5 beim Satzende.
  Auf CPU (`small`, 1,3–1,9 s) ist echtes Streaming nicht drin: dann Endpointing allein aus VAD +
  Regeln, STT einmal am Ende. Eigenes Vokabular bleibt über `initial_prompt`/`hotwords`
  (Home Assistant, Grafana, Axis, Salto, Telenot, Dedrone, Gerätenamen aus `config/home.json`
  automatisch einsammeln). Groq-Whisper bleibt Cloud-Option für den Server ohne GPU.
- **Wake Word:** openWakeWord serverseitig für den Satelliten; im Browser bleibt der Worker.
  Konversationsmodus aus der Engine heraus (kein Wake Word für `followup_s` nach jeder Antwort).
- **TTS:** Kette bleibt, aber jeder Adapter liefert Chunks (Fish streamt, Piper streamt, Edge streamt
  schon intern). Der Server hält den TTS-Puffer satzweise, kennt also Satzgrenzen für Pause/Resume und
  muss beim Weitersprechen nichts neu synthetisieren. Stimmenwahl auf dem Server ohne GPU: Piper ist die
  einzige offline streamende deutsche Stimme mit RTF ≈ 0,1 auf CPU; Qualität unter Fish/ElevenLabs.
  Mit GPU: Chatterbox bleibt, aber als Stream (chunked generate) oder gar nicht.
- **LLM:** unverändert Token-Streaming; die Engine startet TTS am ersten Satz bzw. ersten langen Teilsatz.

### P2: Barge-in- und Resume-Logik (auf P1, 3–5 Tage)
Während SPEAKING: VAD-Treffer → Ducking (50 ms Rampe) → nach 150 ms bestätigter Sprache PAUSE,
Zustand INTERRUPTED, Satzindex und Byte-Offset merken. Kurz-STT auf das Segment, Klassifikation:
- kein Sprach-VAD-Anteil ≥ 60 % oder Transkript leer/Halluzinationsmuster → RESUMING sofort
- Backchannel-Liste (`mhm`, `ja`, `genau`, `okay`, `aha`, `verstehe`) → RESUMING am Satzanfang
- Stopp-Liste (`stopp`, `halt`, `ruhig`, `danke, reicht`, `schon gut`) → IDLE, Rest verwerfen
- echte Äußerung → LISTENING bis Endpointing, dann THINKING; Kontext an das LLM: gesprochener Teil
  als Assistenten-Antwort plus Hinweis „unterbrochen nach Satz N von M“
- Stille > `resume_after_ms` (Standard 1500) ohne Äußerung → RESUMING am Satzanfang
Sonos: Stream kappen plus `volume_set` parallel; Fortsetzen hängt die Sätze ab Satzanfang wieder an.

### P3: Konfiguration und Robustheit (2–3 Tage)
- Alle Schwellen und Timeouts in **einer** Datei mit Kommentaren und Defaults. Wunsch: YAML. Das
  Repo kennt bisher `.env` und `config/*.json`; YAML braucht PyYAML (neue Abhängigkeit). Vorschlag:
  `config/voice.yaml` mit PyYAML, oder dieselbe Struktur als `config/voice.json` ohne neue Abhängigkeit.
- Audio-Geräte per Name (Satellit), Windows jetzt, Linux später (`hostos`).
- Ausfall von STT/TTS/LLM: klarer Satz („Ich höre gerade schlecht“), automatischer Reconnect der
  WebSocket-Sessions, Watchdog je Stufe.
- Debug-Modus: schreibt Segmente (Nutzer, Barge-in-Clips, Referenz) als WAV mit Zustandsprotokoll.

### P4: Messen und Testen (2–3 Tage)
- Metriken je Stufe: Endpointing-Wartezeit, STT, erster Token, erster Satz, TTS-Erstlatenz, Zeit bis zum
  ersten Wort, Barge-in-Reaktionszeit, Fehlauslösungen durch Echo, zu frühes Antworten (Nutzer sprach
  weiter), als `logs/voice_metrics.jsonl` und `/metrics` im Prometheus-Textformat (Standardbibliothek).
- Testskripte mit WAV-Fixtures: Denkpause im Satz, Unterbrechung mit Frage, Backchannel, Husten,
  Stopp, Unterbrechung ohne Folgeäußerung; laufen offline gegen die Engine ohne Mikrofon.
- Tuning-Protokoll in der README: welche Werte bei „zu früh“ / „zu spät“ / „stockt“ / „hört nicht“.

## 6. Neue Abhängigkeiten (Vorschlag, noch nicht installiert)

| Paket | Zweck | Lizenz | Offline | Install |
|---|---|---|---|---|
| `silero-vad` (ONNX-Datei, läuft über vorhandenes `onnxruntime`) | VAD | MIT | ja | `pip install silero-vad` oder nur die `.onnx` nach `models/` |
| `websockets` | Audiokanal Browser/Satellit ↔ Server | BSD-3 | ja | `pip install websockets` |
| `pyrnnoise` oder `rnnoise`-Binding | Rauschunterdrückung | BSD-3 | ja | `pip install pyrnnoise` (Name vor Einbau prüfen) |
| `speexdsp` Python-Binding (z. B. `speexdsp-python`) | AEC Satellit/Sonos | BSD-3 | ja | `pip install speexdsp-python` (Build unter Windows prüfen; Alternative WebRTC-APM-Binding) |
| `openwakeword`, `sounddevice` | Wake Word und Mikrofon für den Satelliten | Apache-2.0 / MIT | ja | `pip install openwakeword sounddevice` |
| `PyYAML` (nur bei YAML-Config) | Config | MIT | ja | `pip install pyyaml` |
| optional `deepfilternet` | bessere Rauschunterdrückung | MIT/Apache | ja | `pip install deepfilternet` (zieht PyTorch, ~2 GB) |
| optional smart-turn v3 (ONNX) | Turn-Erkennung | BSD-2 | ja | Modell nach `models/`, läuft über `onnxruntime` |

`faster-whisper`, `piper-tts`, `onnxruntime`, `av` sind vorhanden.

## 7. Entscheidungen, die schwer rückgängig sind (bitte Okay)

1. **Ort der Engine:** Audio-Pipeline (VAD, AEC, Endpointing, Zustandsmaschine) auf den Server, der
   Browser wird dünner Client mit WebSocket. Alternative: alles bleibt in JS im Browser (schneller
   fertig, aber keine Sonos-AEC, kein Satellit, keine Partials auf dem Server, und auf dem Linux-Server
   später dieselbe Baustelle noch einmal). Empfehlung: Server.
2. **WebSocket-Bibliothek** `websockets` statt Eigenbau in der Standardbibliothek.
3. **Config-Format** YAML (PyYAML) oder JSON im bestehenden `config/`-Stil.
4. **Stimme ohne GPU:** Piper als lokale Streaming-Stimme akzeptieren (Qualitätskompromiss), Fish/
   ElevenLabs bleiben vorne in der Kette, Chatterbox nur mit GPU und nur gestreamt.
5. **Echo (Alexa) als Dialogausgabe** aufgeben; Durchsagen dort bleiben.

Ohne Okay zu 1 beginne ich mit P0 (rein additiv, in `.env`, `app.js`, `server.py`), das in jedem Fall
Bestand hat.
