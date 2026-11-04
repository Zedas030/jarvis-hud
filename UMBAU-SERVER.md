# Umbauplan: Jarvis auf einen eigenen Ubuntu-Server (ohne GPU)

Stand 29.10.2026. Ergänzt `PORTING.md` (Inventar der Windows-Stellen) um die konkrete Entscheidung,
die Hardware, die Phasen und die Abnahme. Deutsch, weil es der Arbeitsplan des Nutzers ist.

## 1. Entscheidung

- **Ziel:** Jarvis läuft 24/7 auf einem kleinen, leisen Ubuntu-Server im LAN. Der große PC (RTX 5080) wird
  wieder nur für Arbeit und Spiele eingeschaltet.
- **Headless.** Der Server hat keinen Bildschirm, kein Mikrofon, keinen Lautsprecher. Alles, was den PC
  betrifft (Lautstärke, Fenster, Apps, Zwischenablage, Bildschirmfoto, Sperren), bleibt auf dem PC und wird
  über einen kleinen **PC-Agenten** angesprochen. Genau die in `PORTING.md` empfohlene Variante.
- **Keine GPU, erst einmal.** Die Logs vom 14.–29.10. zeigen: Claude ist das Gehirn, die Stimme kommt aus der
  Cloud (Fish Audio, ElevenLabs), der lokale Router beantwortet rund 20 % der Anfragen ohne Modell. Wirklich
  täglich auf der GPU arbeitet nur Whisper. Chatterbox ist Platz 3 der Stimmen-Kette, Ollama hatte 21 Aufrufe
  in zwei Wochen und nur im Notbetrieb. Beides kann weg bzw. auf CPU.
- **Rückfalltür:** Der M920s hat einen PCIe-x16-Slot (Low Profile, 75 W). Wird die CPU-Latenz von Whisper
  störend, kommt eine gebrauchte RTX A2000 12 GB dazu, ohne sonst etwas zu ändern.

### Strom (gemessen an der Steckdose „PC“, Home Assistant)

| | kWh/Monat | ≈ €/Monat (0,34 €/kWh) |
|---|---|---|
| PC heute (September, ohne Jarvis-Entwicklung) | 30 | 10 |
| PC 24/7 als Jarvis-Host (Idle 80–100 W) | +58 bis +72 | +20 bis +24 |
| M920s i7-8700, 32 GB, ohne GPU (12–15 W) | ≈ 10 | ≈ 3,50 |

## 2. Hardware und Einkauf

Angebot: Fibich-IT (Kleinanzeigen 2440118316), Lenovo ThinkCentre M920s SFF, 12 Monate Gewährleistung.

| Posten | Preis |
|---|---|
| M920s Basis (i3-9100, 8 GB, 256 GB NVMe, Win 11 Pro) | 175 € |
| CPU i7-8700 (6C/12T, 65 W) | +95 € |
| SSD 512 GB NVMe | +30 € |
| Versand | +9 € |
| RAM selbst: 2x 16 GB DDR4-2666 UDIMM (Dual Channel, 2 Slots bleiben frei) | ≈ 55 € |
| **Summe** | **≈ 365 €** |

Nicht kaufen: RAM beim Händler (+149 € für 32 GB), WLAN-Karte (LAN-Kabel), RX 550 (kein CUDA, nutzlos).
Wichtig: Der i7-**8700** mit 12 Threads ist für viele kleine Prozesse besser als der i7-9700 (8 Threads)
aus dem eBay-Angebot.

Dazu: eine Messsteckdose für den Server (der Verbrauch soll in Home Assistant und im HUD sichtbar sein,
wie beim PC), ein USB-Stick für Ubuntu, ein Netzwerkkabel zur FRITZ!Box oder zum Switch.

Optional später: RTX A2000 12 GB gebraucht (300–380 €) für Whisper `large-v3-turbo`, Chatterbox und ein
7B-Modell gleichzeitig. Die A2000 6 GB (~200 €) reicht nur für Whisper plus Chatterbox.

## 3. Zielbild

```
 ┌────────────────────────────┐        ┌──────────────────────────────┐
 │ Server (Ubuntu 24.04, 24/7)│  LAN   │ PC (Windows 11, nur bei Bedarf)│
 │  server.py :8720 / :8721   │◀──────▶│  Browser-HUD (Mikro, Karten)  │
 │  Router · Gedächtnis · HA  │  HTTP  │  PC-Agent :8740 (Lautstärke,  │
 │  Claude-CLI (Abo)          │        │   Fenster, Apps, Screenshot…) │
 │  Whisper CPU · Piper CPU   │        │  Obsidian (Vault-Sync)        │
 │  Hintergrundjobs · Timer   │        └──────────────────────────────┘
 └──────┬──────────────┬──────┘
        │ Tailscale    │ LAN
 ┌──────▼──────┐  ┌────▼──────────────────────────┐
 │ iPad/iPhone │  │ Pi: Home Assistant, Pi-hole,  │
 │ (gepaart)   │  │ MQTT, Kuma, Grafana · NAS     │
 └─────────────┘  └───────────────────────────────┘
```

Am Pi ändert sich nichts. Home Assistant bleibt dort, Jarvis spricht es wie heute über `HA_URL` an.

## 4. Was sich konkret ändert

| Bereich | Heute (Windows-PC) | Auf dem Server | Aufwand |
|---|---|---|---|
| Spracherkennung | faster-whisper `large-v3-turbo` auf CUDA, Fallback `small` CPU | `WHISPER_LOCAL_DEVICE=cpu`, `WHISPER_LOCAL_MODEL=small` (int8). Option: `JARVIS_STT=elevenlabs` oder Groq-Whisper, dann Cloud | S (nur `.env`) |
| Stimme | `TTS_CHAIN=fish,elevenlabs,local,azure,google,edge`; `local` = Chatterbox-GPU-Server (`.venv-tts`, `LOCAL_TTS_URL`), sonst Piper | Kette bleibt. `LOCAL_TTS_URL` leer lassen, `.venv-tts` nicht installieren, dann ist `local` automatisch Piper CPU (`models/piper/de_DE-thorsten-high.onnx`) | S |
| Notbetrieb | `groq,gemini,ollama` | `JARVIS_FALLBACK=groq,gemini`. Ollama optional auf CPU (`qwen2.5:7b-instruct`, 5–7 Token/s, ~5 GB RAM) | S |
| Browser öffnen | `JARVIS_OPEN=1`, `JARVIS_BROWSER=Opera GX` | `JARVIS_OPEN=0`; das HUD wird vom PC, iPad, iPhone geöffnet | S |
| PC-Funktionen | `pc.py` (pycaw), `desktop.py` (Fenster, OCR, Zwischenablage), `vision.py` (Screenshot), Spotify-Ducking | `pc_backend` mit `JARVIS_PC=remote` → PC-Agent. PC aus → Antwort „Der PC ist aus“ | L (siehe Phase 0 und 3) |
| PowerShell-Reste | `router.py`, `desktop.py`, `infra.py`, `jarvis_mcp.py`, `vision.py` | hinter `hostos` / `pc_backend`; Gateway und Nachbarn über `ip route` / `ip neigh` | M |
| Systemstatus | `infra.py`/`sysmon.py` lesen CPU, RAM, GPU (nvidia-smi) des PCs | Zeigt den Server; `nvidia-smi` fehlt → GPU-Liste leer, Code fängt das ab. Status des PCs später über den PC-Agenten | S jetzt, M später |
| Aktivierungswort | `JARVIS_WAKE=Jarvis` (= `text`), optional `oww` mit Mikro am Server | Server hat kein Mikro: `text` bleibt; iPad/iPhone erkennen „Jarvis“ bereits lokal (`ui/vendor`). Für den PC-Browser dasselbe Modell nutzen | S–M |
| Zugriff aufs HUD | PC: `127.0.0.1:8720` (volle Rechte, `LOCAL_ONLY`-Endpunkte). Geräte: `tailscale serve` → `:8721`, gepaart | PC ist jetzt auch „remote“. Nötig: vertrauenswürdiger PC für `LOCAL_ONLY` (Freigabekarten, `/api/hud`, Pairing). Vorschlag unten | M |
| Start / Autostart | `start-jarvis.bat`, Aufgabenplanung | `start-jarvis.sh`, systemd-Unit `jarvis.service` (`Restart=on-failure`, `After=network-online.target`) | S |
| Claude-CLI | `claude.exe` (npm) | `npm i -g @anthropic-ai/claude-code`, einmal `claude login` (Abo, OAuth-Link im PC-Browser öffnen). Nie `ANTHROPIC_API_KEY` | S |
| Vault / Obsidian | `JARVIS_WORKDIR=C:\Users\nutzer\jarvis-hud\vault`, Obsidian liest lokal | Vault liegt auf dem Server. Obsidian am PC über **Syncthing** (empfohlen, bidirektional, offline-fähig) oder SMB-Freigabe | M |
| Backups | lokal unter `backups/` | weiter lokal, zusätzlich nächtlich per rsync auf die NAS (wie die HA-Backups) | S |
| Pfade | `.venv\Scripts\python.exe`, `%LOCALAPPDATA%` | `.venv/bin/python` (`hostos.venv_python` kann das schon), `LOCALAPPDATA` entfällt | S |
| Dokumentensuche, Paperless, Drucker, Kalender, Mail, Garmin, Spotify-API | portabel | unverändert | – |

### Zugriff vom PC auf den Server-Jarvis

Heute entscheidet `server.py` über `client_address == 127.0.0.1`, ob ein Aufruf „der eigene PC“ ist. Auf dem
Server trifft das nur noch für SSH-Tunnel zu. Empfehlung in dieser Reihenfolge:

1. **Übergang (kein Code):** SSH-Tunnel vom PC, `ssh -L 8720:127.0.0.1:8720 jarvis@server`. Das HUD am PC läuft
   dann exakt wie heute unter `http://localhost:8720`. Reicht für die Umstellungsphase.
2. **Dauerhaft (kleine Änderung):** `JARVIS_TRUSTED_IPS` in `.env` (Tailscale-IP des PCs). Aufrufe von dort
   gelten für `LOCAL_ONLY` als lokal. Der Server bindet `PORT` weiterhin nur an `127.0.0.1` und `tailscale serve`
   liefert `:8720` als zweiten Pfad aus, oder der Server bindet an die Tailscale-Adresse. Ein Test in
   `tests/` für „fremde IP bleibt fremd“.
3. Pairing für iPad/iPhone bleibt wie heute über `:8721`.

## 5. Phasen

### Phase 0: Vorbereiten, noch auf Windows (kein Verhaltenswechsel)

Schritte 2 und 3 aus `PORTING.md`, plus Zugriff. Jeder Punkt einzeln testbar, Jarvis läuft dabei normal weiter.

1. Testsuite einmal in WSL laufen lassen (`python -m unittest discover -s tests`), Fehler fixen. Zeigt die
   verbliebenen Windows-Annahmen, bevor der Server da ist.
2. `pc_backend.py` anlegen, Windows-Code aus `pc.py`, `desktop.py`, `vision.py` nach `_pc_win.py` verschieben
   (reiner Umzug). `JARVIS_PC=local|remote|off`, Standard `local`. Tests für `off` („kein Bildschirm am Server“).
3. Letzte PowerShell-Aufrufe (`router.py`, `infra.py`, `jarvis_mcp.py`) hinter `hostos`: Gateway, Nachbarn,
   Prozessliste (psutil), Skripte.
4. `JARVIS_TRUSTED_IPS` für `LOCAL_ONLY` (siehe oben), mit Test.
5. `start-jarvis.sh` und `deploy/jarvis.service` ins Repo, `README` um Linux-Pfade ergänzen.
6. Messen, bevor umgestellt wird: Latenz einer typischen Spracheingabe heute (GPU) als Referenz. Dafür
   `WHISPER_LOCAL_DEVICE=cpu` einmal am PC setzen und dieselben Sätze sprechen. Liegt `small` auf dem
   i7-8700 über etwa 2,5 s, fällt die Entscheidung für Groq-Whisper oder die A2000 schon hier.

Aufwand: ein Wochenende.

### Phase 1: Server aufsetzen

1. Hardware bestellen, RAM einbauen, einmal MemTest (Ubuntu-Installer bietet es an).
2. **Ubuntu Server 24.04 LTS**, minimal, kein Desktop. Benutzer `jarvis`, SSH nur mit Schlüssel,
   `unattended-upgrades` an. Hostname `jarvis-server`.
3. FRITZ!Box: feste IP-Zuweisung (z. B. 10.0.0.111), Name eintragen. Pi-hole kennt ihn dann per DNS.
4. Tailscale installieren und anmelden (`tailscale up --ssh`), im Admin das Gerät freigeben.
5. Pakete: `python3.12-venv`, `ffmpeg`, `git`, `rsync`, `nodejs` (LTS über NodeSource), `claude` global,
   `claude login` einmalig.
6. Repo klonen (`~/jarvis-hud`), `.venv` anlegen, `pip install -r requirements.txt` (seit 02.11.2026 ohne
   GPU-Zeilen; die stehen in `requirements-gpu.txt`, Windows-Pakete in `requirements-windows.txt`). Piper-Modell aus `models/`
   kopieren, Wake-Word-Dateien mit `scripts/fetch_wakeword.py` holen.
7. `.env`, `config/*.json`, `vault/`, `state.json`, `logs/` vom PC kopieren (rsync über Tailscale). `.env`
   anpassen, siehe Abschnitt 6.
8. Messsteckdose an den Server, in Home Assistant benennen („Server“), in `config/homelab.json` und
   den Energie-Coach aufnehmen.

Aufwand: ein Nachmittag plus Lieferzeit.

### Phase 2: Parallelbetrieb

1. Server zuerst mit `JARVIS_RUNTIME=mock` starten: HUD, Router, HA-Anbindung, Timer, Kalender, Mail prüfen,
   ohne Claude-Tokens.
2. Dann echt, aber mit anderem `JARVIS_PORT` als der PC-Jarvis und ohne `tailscale serve`, damit das iPad
   weiter den PC nutzt. Zugriff über SSH-Tunnel vom PC.
3. Eine Woche mitlaufen lassen: Hintergrundjobs (Briefing, Reflexion, Wochenrückblick, Mail-Ereignisse) auf
   dem Server **aus** (`JARVIS_BACKGROUND=0` oder gleichwertig), sonst laufen sie doppelt und schreiben doppelt
   ins Gedächtnis. Nur interaktiv testen.
4. Latenz messen: Sprechen → Antwort beginnt. Referenz aus Phase 0.

### Phase 3: PC-Agent

Kleiner Python-Dienst auf dem Windows-PC (`agent/pc_agent.py`, Standardbibliothek + pycaw/comtypes wie heute),
bindet an die Tailscale-Adresse oder LAN-IP, Port 8740, Token in `.env` beider Seiten, nur Anfragen vom
Server akzeptiert, jeder Aufruf im Audit-Log. Endpunkte entsprechen 1:1 `pc_backend`:
`volume`, `set_volume`, `mute`, `duck`, `open_app`, `lock`, `windows`, `place`, `clipboard`, `screenshot`,
`ocr`, `media_key`, dazu `status` (CPU, RAM, GPU des PCs für das HUD). Autostart über die bestehende
`install-autostart.ps1`-Logik. Policy-Klassen bleiben im Server (`policy.py`), der Agent entscheidet nichts.

Ist der PC aus, antwortet `pc_backend.remote` innerhalb von 1 s mit „Der PC ist aus“ und der Router
schaltet nicht ins Modell.

Aufwand: ein Tag.

### Phase 4: Umschalten

1. PC-Jarvis stoppen, Autostart entfernen (`install-autostart.ps1 -Remove`).
2. Letzten Stand von `vault/`, `state.json`, `config/`, `logs/` auf den Server rsyncen.
3. Server: `JARVIS_PORT=8720`, Hintergrundjobs an, `tailscale serve` für `:8721` auf dem Server einrichten,
   auf dem PC entfernen. `JARVIS_REMOTE_HOSTS` auf den neuen Tailscale-Namen des Servers.
4. iPad/iPhone neu pairen (oder `config/devices.json` mitkopieren, dann entfällt das).
5. Syncthing: Ordner `vault` zwischen Server und PC, Obsidian öffnet den PC-Ordner wie bisher.
6. Uptime Kuma auf dem Pi: Monitor für `http://10.0.0.111:8720/` (nur erreichbar, wenn Jarvis läuft).
7. Backups: Cron auf dem Server, nächtlich `backups/` und `vault/` per rsync auf die NAS.
8. Eine Woche beobachten. Danach `.venv-tts` und die CUDA-Pakete auf dem PC löschen (ca. 6 GB).

### Phase 5: Optional

- RTX A2000 nachrüsten, falls Whisper-Latenz stört. Dann `WHISPER_LOCAL_DEVICE=auto` und optional `.venv-tts`
  mit Chatterbox wieder in die Kette.
- Ollama auf CPU für den Notbetrieb, wenn Groq und Gemini zu oft gleichzeitig ausfallen.
- Jarvis in `homelab_watch` den eigenen Server beobachten lassen (Temperatur, Platte, Last).

## 6. `.env` auf dem Server (Unterschiede zum PC)

```
JARVIS_OPEN=0
# JARVIS_BROWSER entfällt
JARVIS_WORKDIR=/home/jarvis/jarvis-hud/vault
JARVIS_STT=local
WHISPER_LOCAL_DEVICE=cpu
WHISPER_LOCAL_MODEL=small
TTS_CHAIN=fish,elevenlabs,local,azure,google,edge
# LOCAL_TTS_URL leer lassen → "local" ist Piper (CPU)
JARVIS_FALLBACK=groq,gemini
JARVIS_PC=remote
JARVIS_PC_AGENT=http://<tailscale-ip-pc>:8740
JARVIS_PC_TOKEN=<gemeinsames Geheimnis>
JARVIS_TRUSTED_IPS=<tailscale-ip-pc>
JARVIS_REMOTE=1
JARVIS_REMOTE_HOSTS=jarvis-server.tailxxxx.ts.net
JARVIS_WAKE=Jarvis
```

Alles andere (HA, Mail, Kalender, Spotify, Garmin, Modelle, Lean-Modus, Router) bleibt identisch.

## 7. Abnahme

Erst wenn alles grün ist, wird der PC-Jarvis abgeschaltet.

- [ ] `python -m unittest discover -s tests` grün auf dem Server
- [ ] Sprachbefehl über Router („Licht im Wohnzimmer aus“) unter 1 s, über Claude Haiku („Was steht morgen an?“)
      Antwortbeginn unter 4 s, Spracherkennung `small` CPU unter 2,5 s für einen 5-Sekunden-Satz
- [ ] Freigabekarte vom PC-Browser aus bestätigen (prüft `JARVIS_TRUSTED_IPS`)
- [ ] iPad: „Jarvis“ lokal erkannt, Antwort mit Stimme, Karte bestätigen
- [ ] Timer übersteht `systemctl restart jarvis`
- [ ] Briefing um die gewohnte Zeit, nur einmal
- [ ] Notbetrieb: Claude-Limit simulieren → Groq antwortet mit Werkzeugen
- [ ] PC-Agent: „Lautstärke 30 Prozent“, „Öffne Spotify“, „Sperr den Bildschirm“, Screenshot-Frage; PC aus →
      „Der PC ist aus“ ohne Modellaufruf
- [ ] Obsidian am PC zeigt eine Notiz, die Jarvis gerade angelegt hat (Syncthing)
- [ ] Verbrauch des Servers in HA sichtbar, Idle unter 20 W
- [ ] Backup auf der NAS vorhanden und zurückspielbar

## 8. Risiken und offene Punkte

| Risiko | Einschätzung | Gegenmaßnahme |
|---|---|---|
| Whisper auf CPU zu langsam | `small` int8 auf 6 Kernen: 1–2 s für kurze Befehle, `medium` 4–6 s. Spürbar, aber alltagstauglich | Phase 0 misst es vorab; Alternativen Groq-Whisper (Cloud, gratis, sehr schnell) oder A2000 |
| Doppelte Hintergrundjobs im Parallelbetrieb | Gedächtnis und Kalender bekämen Einträge zweimal | Hintergrundjobs auf dem Server erst in Phase 4 einschalten |
| `claude login` headless | OAuth braucht einmal einen Browser | Link im PC-Browser öffnen, Code am Server einfügen; Abo-Login, kein API-Key |
| Vault-Konflikte durch Sync | Jarvis und Obsidian schreiben beide | Syncthing mit Versionierung „Einfache Dateiversionen“; Jarvis schreibt atomar (temp + rename) |
| PC-Funktionen bei ausgeschaltetem PC | Erwartbar, kein Fehler | Klare Ansage, Router-Regel, kein Modellaufruf |
| Stromsensor fehlt | Ohne Messung keine Kontrolle über das Ziel | Messsteckdose von Anfang an |
| Netzteil 210 W bei späterer GPU | i7-8700 65 W + A2000 70 W passt | Keine Karte über 75 W, kein PCIe-Stromstecker vorhanden |

## 9. Dokumentation

Nach Phase 4 gehören in die Pi-Doku (`/var/www/raspi-doku/index.html`): der neue Server unter „Dienste im
Detail“ (IP, Ports 8720/8721/8740, systemd-Unit, Backup-Ziel), ein Eintrag unter „Änderungen“, der Kuma-Monitor.
In diesem Repo: `README.md` (Schnellstart Linux), `ARCHITECTURE.md` (`pc_backend`, PC-Agent), `SECURITY.md`
(`JARVIS_TRUSTED_IPS`, Agent-Token), `.env.example`.
