# Porting Jarvis to Ubuntu — portability check

State of `feature/jarvis-evolution` on 2026-10-24. Goal: Jarvis's home becomes an Ubuntu machine
in one to two months. This lists every Windows-specific spot and proposes one platform switch
(`jarvis/core/hostos.py`) that they all go through.

## Verdict

The core already runs anywhere: server, router, memory, Home Assistant, mail, calendar,
parcels, printer, homelab, policy and the Claude CLI are standard library or portable
packages, files are opened with an explicit `encoding="utf-8"` everywhere, and paths come from
`pathlib`. Voice (faster-whisper on CUDA, Piper, edge-tts, Chatterbox, sounddevice) runs on Linux
as well. What is Windows-only is the **PC itself**: volume, windows, screen, clipboard, opening
apps, locking, the desktop card, PowerShell calls, and the start/autostart scripts.

**One decision comes first:** will the Ubuntu machine be a **headless server** or a **desktop
you sit at**?

- Headless (recommended for a "home environment"): the PC features are about the Windows PC you
  sit at, not the server. They move to a small **PC agent** on that PC (a few endpoints: volume,
  open, windows, screen, clipboard, lock), which Jarvis calls over the LAN. On the server, `hostos`
  only needs the process and network parts.
- Desktop: `hostos` gets Linux implementations (PipeWire, xdg-open, wmctrl/xdotool, grim,
  tesseract). Window placement is unreliable under Wayland — Ubuntu would then run on X11.

The switch below works for both: each PC function has a Windows implementation, a Linux one,
and later possibly a "remote" one.

## Inventory

Effort: S = minutes, M = an hour or two, L = half a day or more.

### 1. Starting processes (pure mechanics, S)

`creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)` already degrades to `0` on Linux,
so these work today; they only belong behind one name.

| Where | What |
|---|---|
| `jarvis/core/background.py:57` | background job processes |
| `jarvis/core/runtime.py:114, 241, 604` | Claude CLI processes |
| `jarvis/llm/fallback_llm.py:192` | fallback model |
| `jarvis/tools/infra.py:32` | `_run` helper |
| `jarvis/tools/updates.py:302` | update commands |
| `jarvis/tools/jarvis_mcp.py:857, 1240, 1247, 1357` | tasklist, PowerShell, browser (`DETACHED_PROCESS`) |
| `jarvis/voice/tts_chain.py:277` | local GPU voice server |
| `jarvis/voice/vision.py:71, 91` | screenshot, Claude call |
| `jarvis/tools/desktop.py:335, 381` | OCR, scripts |
| `jarvis/watch/homelab_watch.py:147–149` | `ping` (already switches `-n`/`-c` on `os.name`) |

### 2. Paths and start-up (S–M)

| Where | Windows | Linux |
|---|---|---|
| `start-jarvis.bat` | batch file, supervisor in `server.py` | `start-jarvis.sh` (same three lines) |
| `scripts/install-autostart.ps1` | Task Scheduler at logon | systemd **user** unit `jarvis.service` (`Restart=on-failure`), or system unit when headless |
| `jarvis/voice/tts_chain.py:304` | `.venv-tts\Scripts\python.exe` | `.venv-tts/bin/python` |
| `jarvis/core/runtime.py:82–93` | `claude.cmd` → `claude.exe` shim | already handled (`~/.local/bin/claude`, `shutil.which`) |
| `jarvis/core/runtime.py:154, 560` | passes `LOCALAPPDATA` to the MCP server | harmless when empty; drop on Linux |
| `server.py:1098–1103` | `JARVIS_BROWSER` path to Opera/Chrome | headless: `JARVIS_OPEN=0`, the HUD is opened from the PC/iPad |
| `jarvis/tools/desktop.py:34–36` | default roots `~/Desktop, ~/Documents, ~/Downloads` | German Ubuntu names them `Schreibtisch`, `Dokumente`: ask `xdg-user-dir` |
| Docs, docstrings, `CLAUDE.md` | `.venv\Scripts\python.exe` | `.venv/bin/python` (mention both) |

### 3. Network and processes via PowerShell / Windows tools (S–M)

| Where | Windows | Linux |
|---|---|---|
| `jarvis/tools/infra.py:121` | `Get-NetRoute` → default gateway | `ip route show default` |
| `jarvis/tools/jarvis_mcp.py:1235–1247` | `Get-NetNeighbor` → devices in the LAN | `ip neigh` |
| `jarvis/tools/jarvis_mcp.py:856` | `tasklist` → is Spotify running | `psutil` (already a dependency of sysmon) |
| `jarvis/tools/infra.py`, `sysmon.py`, `resources.py` | `nvidia-smi`, `psutil` | same on Linux — nothing to do |

### 4. PC control — the real Windows parts (M–L)

| Where | What | Windows | Linux desktop | Headless |
|---|---|---|---|---|
| `jarvis/tools/pc.py:34–95` | volume, mute, steps | pycaw / Core Audio (`comtypes`) | `wpctl` (PipeWire) or `pactl` | PC agent |
| `jarvis/tools/pc.py:95` | ducking: Spotify quieter while Jarvis speaks | per-app session volume | `pactl set-sink-input-volume` | PC agent, or Spotify Web API volume |
| `jarvis/tools/pc.py:120–133` | open apps (`os.startfile`, `config/apps.json` with `.exe` paths) | `os.startfile` | `xdg-open` / `gtk-launch` with `.desktop` names | PC agent |
| `jarvis/tools/pc.py:137–139` | lock screen | `LockWorkStation` | `loginctl lock-session` | PC agent |
| `jarvis/tools/jarvis_mcp.py:870–886` | Spotify: `spotify:` URIs, media keys via `keybd_event` | `os.startfile`, `user32` | `xdg-open spotify:`, `playerctl` | Spotify Web API only (already the first choice) |
| `jarvis/tools/jarvis_mcp.py:1349–1359` | open a website | browser path or `os.startfile` | `xdg-open` | PC agent or HUD opens it |
| `jarvis/tools/desktop.py:52–151` | list/place windows, monitors | `user32` (EnumWindows, MonitorFromWindow …) | `wmctrl`/`xdotool` (X11 only) | PC agent |
| `jarvis/tools/desktop.py:273` | clipboard | `user32`/`kernel32` | `wl-paste` / `xclip -o` | PC agent |
| `jarvis/tools/desktop.py:309–335`, `scripts/ocr_screen.ps1` | screenshot + Windows OCR | PowerShell, `Windows.Media.Ocr` | `grim`/`gnome-screenshot` + `tesseract -l deu` | PC agent |
| `jarvis/voice/vision.py:55–71` | screenshot for the image model | same script with `-SaveTo` | same as above | PC agent |
| `jarvis/tools/desktop.py:362–381` | allow-listed scripts `.ps1` / `.bat` / `.py` | `powershell.exe`, `cmd.exe` | `.sh` via `bash`, `.py` | on the server: `.sh`, `.py` |
| `jarvis/voice/overlay.py` | always-on-top desktop card | tkinter | tkinter works (X11/XWayland) | not needed (no desktop) |

### 5. Prompts and model tools (S)

| Where | What |
|---|---|
| `jarvis/core/commands.py:191, 198, 305` | prompt says "PowerShell", "`Start-Process`", "PC (Windows)" |
| `jarvis/core/runtime.py:541` | group `pc` brings the built-in tool `PowerShell` → `Bash` on Linux |
| `jarvis/core/router.py:79` | the word "powershell" routes to `pc` — keep, add "terminal", "bash" |
| `jarvis/tools/jarvis_mcp.py:100` | tool description "ssh via Bash/PowerShell" |
| `server.py:280` | card text for `PowerShell` — keep, `Bash` is already there |

### 6. File system behaviour (S, but easy to miss)

- **Case-sensitive file names.** `memory_save` writes `MEMORY_DIR / (note + ".md")`
  (`jarvis/tools/jarvis_mcp.py` near the `# memory_save` comment). On Windows "vorlieben" and
  "Vorlieben" are the same file; on Linux the model would create a second note. Look up an
  existing note case-insensitively first (`memory_read` already compares `stem.lower()`).
  The same holds for `config/*.json` names and anything matched by name in the vault.
- Line endings: the repository uses `core.autocrlf=true`. On Linux set `autocrlf=input`;
  Python reads both, nothing in the code depends on `\r\n`.
- `jarvis/memory/docsearch.py:30`, `desktop.py:39`: text extensions list `.ps1`, `.bat` — add `.sh`.

### 7. Tests

- `tests/test_desktop.py:94, 136` already skip the Windows OCR and `cmd.exe` tests off Windows.
- Everything else is meant to run without Windows; this is only proven once the suite runs on
  Linux. Cheapest way now: WSL (Ubuntu) on this PC,
  `python3 -m venv .venv && .venv/bin/python -m unittest discover -s tests`.

## Proposal: `jarvis/core/hostos.py`

One module, plain functions, standard library first — the same style as the rest. Everything
that differs per operating system lives here; the feature modules ask `hostos` and stay free of
`os.name`. (`platform` would shadow the standard library, and `host` is a common variable name, hence `hostos`.)

```python
"""
What differs between Windows and Linux, in one place. Feature modules never ask os.name.
"""
import os, shutil, subprocess, sys, pathlib

IS_WIN = os.name == "nt"
# keyword arguments for every subprocess: no console window on Windows
QUIET = {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WIN else {}
DETACHED = {"creationflags": subprocess.DETACHED_PROCESS} if IS_WIN else {"start_new_session": True}

def venv_python(root, name=".venv"):          # .venv\Scripts\python.exe | .venv/bin/python
def open_target(target):                       # os.startfile | xdg-open
def ping_args(addr, timeout_s=2):              # ["ping", "-n", "1", "-w", "2000", addr] | ["ping", "-c", "1", "-W", "2", addr]
def default_gateway():                         # Get-NetRoute | ip route show default
def neighbours():                              # Get-NetNeighbor | ip neigh  -> [(ip, mac)]
def process_running(name):                     # psutil on both
def user_dirs():                               # Desktop/Documents/Downloads | xdg-user-dir
def shell_tool():                              # "PowerShell" | "Bash"  (runtime.GROUP_BUILTINS, prompt text)
def script_command(path):                      # .ps1/.bat/.cmd | .sh ; .py on both
```

The PC functions get a second layer, because they may end up on another machine:

```python
# jarvis/tools/pc_backend.py  — chosen by JARVIS_PC = local | remote | off
volume() / set_volume(pct) / mute(on) / duck(on)
open_app(name) / lock()
windows() / place(app, position, monitor)
clipboard() / screenshot(path, whole) / ocr(path)
media_key(action)
```

`local` imports `_pc_win` or `_pc_linux`; `remote` posts to the PC agent (token like the
existing pairing, LAN only, every call audited); `off` answers "Am Server gibt es keinen
Bildschirm". `pc.py`, `desktop.py` and `vision.py` keep their German answers, routing and policy
classes unchanged and only call `pc_backend`. The policy stays where it is: the agent executes,
it never decides.

## Order of work

1. **Done 2026-10-24** (`tests/test_hostos.py`). **Now, on Windows, no behaviour change:** add `hostos.py`; replace the 17 `creationflags`
   spots, `venv_python`, `ping_args`, `open_target`, `shell_tool`; fix the case-insensitive
   `memory_save` (now `memory.note_path`, also used by `overview` and the plans). Tests for `hostos`
   with both branches (monkeypatched `IS_WIN`). Default gateway and neighbours stay for step 4. S–M.
2. **Now:** run the test suite in WSL once and fix what breaks. M.
3. **Now:** `pc_backend` with the Windows code moved out of `pc.py`/`desktop.py` (pure move).
   M–L.
4. **At the move:** `start-jarvis.sh`, systemd unit, `.env` (`JARVIS_OPEN=0`, `JARVIS_PC`),
   Linux branches for gateway/neighbours, NVIDIA driver + CUDA for Whisper and the GPU voice. M.
5. **Then, depending on the decision:** PC agent on the Windows PC (headless) or the Linux
   desktop implementations. L.

Rule until then: no new Windows-only calls outside `hostos.py` / `pc_backend`.
