# Rebuild plan: Jarvis on its own Ubuntu server (without a GPU)

As of 2026-10-29. It adds to `PORTING.md` (inventory of the Windows-specific places) the concrete decision,
the hardware, the phases and the acceptance test.

## 1. Decision

- **Goal:** Jarvis runs 24/7 on a small, quiet Ubuntu server in the LAN. The big PC (RTX 5080) is again
  only switched on for work and games.
- **Headless.** The server has no screen, no microphone, no speaker. Everything that concerns the PC
  (volume, windows, apps, clipboard, screenshot, locking) stays on the PC and is addressed through a
  small **PC agent**. Exactly the variant recommended in `PORTING.md`.
- **No GPU, for now.** The logs from 2026-10-14 to 10-29 show: Claude is the brain, the voice comes from the
  cloud (Fish Audio, ElevenLabs), the local router answers about 20 % of the requests without a model. The only
  thing that really works on the GPU every day is Whisper. Chatterbox is number 3 of the voice chain, Ollama had
  21 calls in two weeks and only in fallback mode. Both can go or move to the CPU.
- **Way back:** the M920s has a PCIe x16 slot (low profile, 75 W). If the CPU latency of Whisper becomes
  annoying, a used RTX A2000 12 GB goes in, without changing anything else.

### Power (measured at the "PC" socket, Home Assistant)

| | kWh/month | ≈ €/month (€0.34/kWh) |
|---|---|---|
| PC today (September, without Jarvis development) | 30 | 10 |
| PC 24/7 as the Jarvis host (idle 80–100 W) | +58 to +72 | +20 to +24 |
| M920s i7-8700, 32 GB, without a GPU (12–15 W) | ≈ 10 | ≈ 3.50 |

## 2. Hardware and shopping

Offer: Fibich-IT (Kleinanzeigen listing 2440118316), Lenovo ThinkCentre M920s SFF, 12 months warranty.

| Item | Price |
|---|---|
| M920s base (i3-9100, 8 GB, 256 GB NVMe, Win 11 Pro) | €175 |
| CPU i7-8700 (6C/12T, 65 W) | +€95 |
| SSD 512 GB NVMe | +€30 |
| Shipping | +€9 |
| RAM yourself: 2x 16 GB DDR4-2666 UDIMM (dual channel, 2 slots stay free) | ≈ €55 |
| **Total** | **≈ €365** |

Do not buy: RAM from the dealer (+€149 for 32 GB), a WiFi card (LAN cable), an RX 550 (no CUDA, useless).
Important: the i7-**8700** with 12 threads is better for many small processes than the i7-9700 (8 threads)
from the eBay offer.

Also: a metering socket for the server (its consumption should be visible in Home Assistant and in the HUD,
like the PC's), a USB stick for Ubuntu, a network cable to the FRITZ!Box or the switch.

Optionally later: a used RTX A2000 12 GB (€300–380) for Whisper `large-v3-turbo`, Chatterbox and a
7B model at the same time. The A2000 6 GB (~€200) is only enough for Whisper plus Chatterbox.

## 3. Target picture

```
 ┌────────────────────────────┐        ┌──────────────────────────────┐
 │ Server (Ubuntu 24.04, 24/7)│  LAN   │ PC (Windows 11, only on demand)│
 │  server.py :8720 / :8721   │◀──────▶│  browser HUD (mic, cards)     │
 │  router · memory · HA      │  HTTP  │  PC agent :8740 (volume,      │
 │  Claude CLI (subscription) │        │   windows, apps, screenshot…) │
 │  Whisper CPU · Piper CPU   │        │  Obsidian (vault sync)        │
 │  background jobs · timers  │        └──────────────────────────────┘
 └──────┬──────────────┬──────┘
        │ Tailscale    │ LAN
 ┌──────▼──────┐  ┌────▼──────────────────────────┐
 │ iPad/iPhone │  │ Pi: Home Assistant, Pi-hole,  │
 │ (paired)    │  │ MQTT, Kuma, Grafana · NAS     │
 └─────────────┘  └───────────────────────────────┘
```

Nothing changes on the Pi. Home Assistant stays there; Jarvis talks to it through `HA_URL` as today.

## 4. What changes in detail

| Area | Today (Windows PC) | On the server | Effort |
|---|---|---|---|
| Speech recognition | faster-whisper `large-v3-turbo` on CUDA, fallback `small` CPU | `WHISPER_LOCAL_DEVICE=cpu`, `WHISPER_LOCAL_MODEL=small` (int8). Option: `JARVIS_STT=elevenlabs` or Groq Whisper, then cloud | S (only `.env`) |
| Voice | `TTS_CHAIN=fish,elevenlabs,local,azure,google,edge`; `local` = Chatterbox GPU server (`.venv-tts`, `LOCAL_TTS_URL`), otherwise Piper | The chain stays. Leave `LOCAL_TTS_URL` empty, do not install `.venv-tts`; then `local` is automatically Piper CPU (`models/piper/en_GB-alan-medium.onnx`) | S |
| Fallback mode | `groq,gemini,ollama` | `JARVIS_FALLBACK=groq,gemini`. Ollama optionally on the CPU (`qwen2.5:7b-instruct`, 5–7 tokens/s, ~5 GB RAM) | S |
| Opening the browser | `JARVIS_OPEN=1`, `JARVIS_BROWSER=Opera GX` | `JARVIS_OPEN=0`; the HUD is opened from the PC, iPad, iPhone | S |
| PC functions | `pc.py` (pycaw), `desktop.py` (windows, OCR, clipboard), `vision.py` (screenshot), Spotify ducking | `pc_backend` with `JARVIS_PC=remote` → PC agent. PC off → answer "The PC is off" | L (see phases 0 and 3) |
| PowerShell leftovers | `router.py`, `desktop.py`, `infra.py`, `jarvis_mcp.py`, `vision.py` | behind `hostos` / `pc_backend`; gateway and neighbours through `ip route` / `ip neigh` | M |
| System status | `infra.py`/`sysmon.py` read the PC's CPU, RAM, GPU (nvidia-smi) | Shows the server; `nvidia-smi` is missing → GPU list empty, the code handles that. The PC's status later through the PC agent | S now, M later |
| Wake word | `JARVIS_WAKE=Jarvis` (= `text`), optionally `oww` with a mic on the server | The server has no mic: `text` stays; iPad/iPhone already detect "Jarvis" locally (`ui/vendor`). Use the same model for the PC browser | S–M |
| Access to the HUD | PC: `127.0.0.1:8720` (full rights, `LOCAL_ONLY` endpoints). Devices: `tailscale serve` → `:8721`, paired | The PC is now "remote" as well. Needed: a trusted PC for `LOCAL_ONLY` (approval cards, `/api/hud`, pairing). Proposal below | M |
| Start / autostart | `start-jarvis.bat`, Task Scheduler | `start-jarvis.sh`, systemd unit `jarvis.service` (`Restart=on-failure`, `After=network-online.target`) | S |
| Claude CLI | `claude.exe` (npm) | `npm i -g @anthropic-ai/claude-code`, `claude login` once (subscription, open the OAuth link in the PC browser). Never `ANTHROPIC_API_KEY` | S |
| Vault / Obsidian | `JARVIS_WORKDIR=C:\Users\user\jarvis-hud\vault`, Obsidian reads locally | The vault lives on the server. Obsidian on the PC through **Syncthing** (recommended, two-way, works offline) or an SMB share | M |
| Backups | local under `backups/` | still local, plus nightly by rsync to the NAS (like the HA backups) | S |
| Paths | `.venv\Scripts\python.exe`, `%LOCALAPPDATA%` | `.venv/bin/python` (`hostos.venv_python` can already do that), `LOCALAPPDATA` goes away | S |
| Document search, Paperless, printer, calendar, mail, Garmin, Spotify API | portable | unchanged | – |

### Access from the PC to the server Jarvis

Today `server.py` decides through `client_address == 127.0.0.1` whether a call is "the own PC". On the
server that only holds for SSH tunnels. Recommendation, in this order:

1. **Transition (no code):** an SSH tunnel from the PC, `ssh -L 8720:127.0.0.1:8720 jarvis@server`. The HUD on
   the PC then runs exactly as today under `http://localhost:8720`. Enough for the switch-over phase.
2. **Permanent (small change):** `JARVIS_TRUSTED_IPS` in `.env` (the PC's Tailscale IP). Calls from there
   count as local for `LOCAL_ONLY`. The server still binds `PORT` only to `127.0.0.1` and `tailscale serve`
   delivers `:8720` as a second path, or the server binds to the Tailscale address. A test in
   `tests/` for "a foreign IP stays foreign".
3. Pairing for iPad/iPhone stays as today through `:8721`.

## 5. Phases

### Phase 0: preparing, still on Windows (no change in behaviour)

Steps 2 and 3 from `PORTING.md`, plus access. Each point testable on its own; Jarvis keeps running normally.

1. Run the test suite once in WSL (`python -m unittest discover -s tests`), fix the failures. Shows the
   remaining Windows assumptions before the server is there.
2. Create `pc_backend.py`, move the Windows code from `pc.py`, `desktop.py`, `vision.py` to `_pc_win.py`
   (a pure move). `JARVIS_PC=local|remote|off`, default `local`. Tests for `off` ("no screen on the server").
3. The last PowerShell calls (`router.py`, `infra.py`, `jarvis_mcp.py`) behind `hostos`: gateway, neighbours,
   process list (psutil), scripts.
4. `JARVIS_TRUSTED_IPS` for `LOCAL_ONLY` (see above), with a test.
5. `start-jarvis.sh` and `deploy/jarvis.service` into the repo, add the Linux paths to `docs/installation.md`.
6. Measure before switching: the latency of a typical voice input today (GPU) as the reference. For that
   set `WHISPER_LOCAL_DEVICE=cpu` once on the PC and speak the same sentences. If `small` on the
   i7-8700 takes more than about 2.5 s, the decision for Groq Whisper or the A2000 is already made here.

Effort: one weekend.

### Phase 1: setting up the server

1. Order the hardware, fit the RAM, run MemTest once (the Ubuntu installer offers it).
2. **Ubuntu Server 24.04 LTS**, minimal, no desktop. User `jarvis`, SSH with a key only,
   `unattended-upgrades` on. Host name `jarvis-server`.
3. FRITZ!Box: a fixed IP assignment (e.g. 10.0.0.111), enter the name. Pi-hole then knows it through DNS.
4. Install Tailscale and sign in (`tailscale up --ssh`), approve the device in the admin console.
5. Packages: `python3.12-venv`, `ffmpeg`, `git`, `rsync`, `nodejs` (LTS through NodeSource), `claude` globally,
   `claude login` once.
6. Clone the repo (`~/jarvis-hud`), create `.venv`, `pip install -r requirements.txt` (since 2026-11-02 without
   the GPU lines; they are in `requirements-gpu.txt`, Windows packages in `requirements-windows.txt`). Copy the
   Piper model from `models/`, fetch the wake word files with `scripts/fetch_wakeword.py`.
7. Copy `.env`, `config/*.json`, `vault/`, `state.json`, `logs/` from the PC (rsync over Tailscale). Adjust
   `.env`, see section 6.
8. Metering socket on the server, name it in Home Assistant ("Server"), add it to `config/homelab.json` and
   the energy coach.

Effort: an afternoon plus delivery time.

### Phase 2: parallel operation

1. Start the server first with `JARVIS_RUNTIME=mock`: check HUD, router, HA connection, timers, calendar, mail,
   without Claude tokens.
2. Then for real, but with a different `JARVIS_PORT` than the PC Jarvis and without `tailscale serve`, so that
   the iPad keeps using the PC. Access through an SSH tunnel from the PC.
3. Let it run along for a week: background jobs (briefing, reflection, weekly review, mail events) **off** on
   the server (`JARVIS_BACKGROUND=0` or equivalent), otherwise they run twice and write into the memory twice.
   Test interactively only.
4. Measure latency: speaking → answer starts. Reference from phase 0.

### Phase 3: PC agent

A small Python service on the Windows PC (`agent/pc_agent.py`, standard library + pycaw/comtypes as today),
binds to the Tailscale address or LAN IP, port 8740, token in the `.env` of both sides, only requests from the
server accepted, every call in the audit log. The endpoints match `pc_backend` 1:1:
`volume`, `set_volume`, `mute`, `duck`, `open_app`, `lock`, `windows`, `place`, `clipboard`, `screenshot`,
`ocr`, `media_key`, plus `status` (CPU, RAM, GPU of the PC for the HUD). Autostart through the existing
`install-autostart.ps1` logic. The policy classes stay in the server (`policy.py`); the agent decides nothing.

If the PC is off, `pc_backend.remote` answers within 1 s with "The PC is off" and the router
does not switch to the model.

Effort: one day.

### Phase 4: switching over

1. Stop the PC Jarvis, remove the autostart (`install-autostart.ps1 -Remove`).
2. Rsync the latest state of `vault/`, `state.json`, `config/`, `logs/` to the server.
3. Server: `JARVIS_PORT=8720`, background jobs on, set up `tailscale serve` for `:8721` on the server,
   remove it on the PC. `JARVIS_REMOTE_HOSTS` to the server's new Tailscale name.
4. Pair iPad/iPhone again (or copy `config/devices.json` along, then that is not needed).
5. Syncthing: the `vault` folder between server and PC; Obsidian opens the PC folder as before.
6. Uptime Kuma on the Pi: a monitor for `http://10.0.0.111:8720/` (only reachable while Jarvis runs).
7. Backups: cron on the server, nightly `backups/` and `vault/` by rsync to the NAS.
8. Watch it for a week. Then delete `.venv-tts` and the CUDA packages on the PC (about 6 GB).

### Phase 5: optional

- Add an RTX A2000 if the Whisper latency is annoying. Then `WHISPER_LOCAL_DEVICE=auto` and optionally `.venv-tts`
  with Chatterbox back into the chain.
- Ollama on the CPU for the fallback mode, if Groq and Gemini fail at the same time too often.
- Let Jarvis watch its own server in `homelab_watch` (temperature, disk, load).

## 6. `.env` on the server (differences from the PC)

```
JARVIS_OPEN=0
# JARVIS_BROWSER is not needed
JARVIS_WORKDIR=/home/jarvis/jarvis-hud/vault
JARVIS_STT=local
WHISPER_LOCAL_DEVICE=cpu
WHISPER_LOCAL_MODEL=small
TTS_CHAIN=fish,elevenlabs,local,azure,google,edge
# leave LOCAL_TTS_URL empty → "local" is Piper (CPU)
JARVIS_FALLBACK=groq,gemini
JARVIS_PC=remote
JARVIS_PC_AGENT=http://<tailscale-ip-pc>:8740
JARVIS_PC_TOKEN=<shared secret>
JARVIS_TRUSTED_IPS=<tailscale-ip-pc>
JARVIS_REMOTE=1
JARVIS_REMOTE_HOSTS=jarvis-server.tailxxxx.ts.net
JARVIS_WAKE=Jarvis
```

Everything else (HA, mail, calendar, Spotify, Garmin, models, lean mode, router) stays the same.

## 7. Acceptance

The PC Jarvis is only switched off once everything is green.

- [ ] `python -m unittest discover -s tests` green on the server
- [ ] Voice command through the router ("Living room light off") under 1 s, through Claude Haiku ("What is on tomorrow?")
      answer starts in under 4 s, speech recognition `small` CPU under 2.5 s for a 5-second sentence
- [ ] Confirm an approval card from the PC browser (checks `JARVIS_TRUSTED_IPS`)
- [ ] iPad: "Jarvis" detected locally, answer with voice, confirm a card
- [ ] A timer survives `systemctl restart jarvis`
- [ ] Briefing at the usual time, only once
- [ ] Fallback mode: simulate the Claude limit → Groq answers with tools
- [ ] PC agent: "Volume 30 percent", "Open Spotify", "Lock the screen", a screenshot question; PC off →
      "The PC is off" without a model call
- [ ] Obsidian on the PC shows a note that Jarvis just created (Syncthing)
- [ ] The server's consumption visible in HA, idle under 20 W
- [ ] A backup on the NAS exists and can be restored

## 8. Risks and open points

| Risk | Assessment | Countermeasure |
|---|---|---|
| Whisper too slow on the CPU | `small` int8 on 6 cores: 1–2 s for short commands, `medium` 4–6 s. Noticeable, but fine for everyday use | Phase 0 measures it beforehand; alternatives Groq Whisper (cloud, free, very fast) or the A2000 |
| Double background jobs in parallel operation | Memory and calendar would get entries twice | Switch background jobs on the server on only in phase 4 |
| `claude login` headless | OAuth needs a browser once | Open the link in the PC browser, paste the code on the server; subscription login, no API key |
| Vault conflicts through sync | Jarvis and Obsidian both write | Syncthing with versioning "Simple file versioning"; Jarvis writes atomically (temp + rename) |
| PC functions while the PC is off | Expected, not an error | A clear answer, a router rule, no model call |
| Power sensor missing | Without measurement no control over the goal | Metering socket from the start |
| 210 W power supply with a later GPU | i7-8700 65 W + A2000 70 W fits | No card above 75 W, no PCIe power connector present |

## 9. Documentation

After phase 4 the Pi docs (`/var/www/raspi-doku/index.html`) get: the new server under "Dienste im
Detail" (services in detail: IP, ports 8720/8721/8740, systemd unit, backup target), an entry under "Änderungen"
(changes), the Kuma monitor. In this repo: `docs/installation.md` (quick start Linux), `ARCHITECTURE.md`
(`pc_backend`, PC agent), `SECURITY.md` (`JARVIS_TRUSTED_IPS`, agent token), `.env.example`.
