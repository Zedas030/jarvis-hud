"""
Windows desktop: arrange windows, search and read files in shared folders,
read the clipboard, read the screen (local OCR) and run allow-listed scripts.

Windows    "Spotify to the right", "Chrome left and Code right", "Discord on
           monitor 2", "Spotify full screen" — local, 0 tokens (handle). Workspaces
           are routines whose steps open apps and arrange them, e.g.
           {"do": "Open Discord"}, {"do": "Discord to the right"}.
Files      Only below JARVIS_FILE_ROOTS (default Desktop, Documents, Downloads).
           Text, Markdown, CSV, logs, JSON, code and Word (.docx). Files that look
           like work are not read (privacy.py).
Clipboard  Only when asked ("what did I copy?"); text only.
Screen     Only when asked. A screenshot of the active window (or the whole
           screen) is read by the Windows OCR on this PC; the image never leaves
           it, only the recognised text goes to the model. Refused while a
           password manager, banking or messenger window is visible
           (JARVIS_SCREEN_BLOCK adds patterns).
Scripts    Only names from config/scripts.json, never free commands; always with a
           confirmation card (policy CONFIRM), with a time limit.
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import pathlib
import re
import subprocess
import tempfile
import time
import zipfile

import hostos

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
_HOME = pathlib.Path.home()
FILE_ROOTS = [pathlib.Path(os.path.expandvars(os.path.expanduser(p.strip())))
              for p in os.environ.get("JARVIS_FILE_ROOTS",
                                      f"{_HOME / 'Desktop'},{_HOME / 'Documents'},{_HOME / 'Downloads'}").split(",")
              if p.strip()]
SCRIPTS = pathlib.Path(os.environ.get("JARVIS_SCRIPTS", ROOT / "config" / "scripts.json"))
TEXT_EXT = {".txt", ".md", ".csv", ".log", ".json", ".py", ".js", ".ts", ".ps1", ".bat", ".ini", ".yaml", ".yml",
            ".xml", ".html", ".cfg", ".conf", ".docx"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "AppData", "$RECYCLE.BIN"}
SCREEN_BLOCK = re.compile(
    r"keepass|1password|bitwarden|lastpass|dashlane|passwort|password|sparkasse|volksbank|commerzbank|"
    r"deutsche bank|ing-diba|\bing\b|comdirect|dkb|n26|paypal|banking|online-?banking|whatsapp|signal|telegram|"
    r"threema|messenger" + (("|" + os.environ["JARVIS_SCREEN_BLOCK"]) if os.environ.get("JARVIS_SCREEN_BLOCK") else ""),
    re.I)
IS_WIN = os.name == "nt"


# ── windows ──────────────────────────────────────────────────

def _user32():
    return ctypes.windll.user32


def windows():
    """[(hwnd, title, exe)] of visible top-level windows with a title."""
    if not IS_WIN:
        return []
    import psutil
    u = _user32()
    out = []
    pid = wt.DWORD()

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if u.IsWindowVisible(hwnd) and u.GetWindowTextLengthW(hwnd) > 0 and not u.GetWindow(hwnd, 4):  # no owner
            buf = ctypes.create_unicode_buffer(512)
            u.GetWindowTextW(hwnd, buf, 512)
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            try:
                exe = psutil.Process(pid.value).name().lower()
            except Exception:  # noqa: BLE001
                exe = ""
            out.append((hwnd, buf.value, exe))
        return True
    u.EnumWindows(cb, 0)
    return out


def find_window(app, wins=None):
    """Best window for a spoken app name ('spotify', 'chrome', 'code'), by exe name then title."""
    want = app.lower().strip()
    if not want:
        return None
    aliases = {"vs code": "code", "visual studio code": "code", "edge": "msedge", "terminal": "windowsterminal"}
    want = aliases.get(want, want)
    wins = windows() if wins is None else wins
    if want == "browser":                       # whichever browser is open
        for b in ("opera", "chrome", "msedge", "firefox", "brave", "vivaldi"):
            hwnd = find_window(b, wins)
            if hwnd:
                return hwnd
        return None
    for hwnd, title, exe in wins:
        if exe.removesuffix(".exe") == want:
            return hwnd
    for hwnd, title, exe in wins:
        if want in exe or want in title.lower():
            return hwnd
    return None


class _RECT(ctypes.Structure):
    _fields_ = [("left", wt.LONG), ("top", wt.LONG), ("right", wt.LONG), ("bottom", wt.LONG)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", _RECT), ("rcWork", _RECT), ("dwFlags", wt.DWORD)]


def monitors():
    """Work areas [(left, top, right, bottom)] of all monitors, primary first."""
    u = _user32()
    out = []
    MonitorEnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(_RECT), wt.LPARAM)

    def cb(hmon, hdc, rect, _):
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        u.GetMonitorInfoW(hmon, ctypes.byref(mi))
        r = mi.rcWork
        out.append(((r.left, r.top, r.right, r.bottom), bool(mi.dwFlags & 1)))
        return True
    u.EnumDisplayMonitors(0, 0, MonitorEnumProc(cb), 0)
    out.sort(key=lambda m: (not m[1], m[0][0]))
    return [m[0] for m in out]


POSITIONS = {"left": (0, 0, .5, 1), "right": (.5, 0, .5, 1), "top left": (0, 0, .5, .5),
             "top right": (.5, 0, .5, .5), "bottom left": (0, .5, .5, .5), "bottom right": (.5, .5, .5, .5),
             "centre": (.15, .1, .7, .8), "full": None}


def place(hwnd, position, monitor=1):
    u = _user32()
    mons = monitors() or [(0, 0, 1920, 1080)]
    left, top, right, bottom = mons[max(0, min(monitor - 1, len(mons) - 1))]
    u.ShowWindow(hwnd, 9)                            # SW_RESTORE (leave minimised/maximised state)
    frac = POSITIONS.get(position)
    if frac is None:                                 # full screen on that monitor
        u.MoveWindow(hwnd, left, top, right - left, bottom - top, True)
        u.ShowWindow(hwnd, 3)                        # SW_MAXIMIZE
    else:
        w, h = right - left, bottom - top
        u.MoveWindow(hwnd, int(left + frac[0] * w), int(top + frac[1] * h), int(frac[2] * w), int(frac[3] * h), True)
    u.SetForegroundWindow(hwnd)
    return True


def arrange(app, position="left", monitor=1, wait=10):
    """Moves the app's window; waits up to `wait` s for a just-opened app. Returns spoken text."""
    position = _position(position)
    if position is None:
        return "I do not know this position (left, right, top left, …, centre, full)."
    end = time.time() + wait
    while True:
        hwnd = find_window(app)
        if hwnd or time.time() >= end:
            break
        time.sleep(0.5)
    if not hwnd:
        return f"No window of {app} found."
    place(hwnd, position, int(monitor or 1))
    return f"{app} is now {'full screen' if position == 'full' else 'on the ' + position}" + (f" on monitor {monitor}" if int(monitor or 1) > 1 else "") + "."


def _position(text):
    t = (text or "").lower().strip()
    t = re.sub(r"\b(the |side|half|to |on |onto )", "", t).strip()
    t = {"fullscreen": "full", "full screen": "full", "maximise": "full", "maximize": "full", "maximised": "full",
         "maximized": "full", "big": "full", "whole": "full", "center": "centre", "middle": "centre"}.get(t, t)
    return t if t in POSITIONS else None


_ARRANGE = re.compile(
    r"^(?:move |put |snap |place |make |set )?(?:the )?(?:window )?(?:of )?([\w .+-]{2,30}?) "
    r"(?:to the |to |on the |on |onto the )?(left side|right side|left half|right half|left|right|"
    r"top left|top right|bottom left|bottom right|centre|center|middle|full ?screen|maximi[sz]ed|big)"
    r"(?: (?:on |to )?(?:the )?(?:monitor|screen|display) (\d))?$")


def handle(n):
    """Local router hook: 'Spotify to the right', 'Chrome left and Code right'. Reply or None."""
    if not IS_WIN or not re.search(r"\b(left|right|top|bottom|centre|center|middle|full ?screen|maximi|monitor \d)", n):
        return None
    parts = re.split(r"\s+and\s+", n.strip(" .!"))
    plan = []
    for p in parts:
        m = _ARRANGE.match(p.strip())
        if not m:
            return None
        app = re.sub(r"^(jarvis )?(please )?", "", m.group(1)).strip()
        if not find_window(app):
            return None                              # not a window we know: let the LLM handle it
        plan.append((app, m.group(2), int(m.group(3) or 1)))
    return " ".join(arrange(a, p, mon, wait=0) for a, p, mon in plan)


# ── files ────────────────────────────────────────────────────

def _inside_roots(path):
    try:
        real = pathlib.Path(os.path.realpath(path))
    except OSError:
        return False
    return any(real == r or r in real.parents for r in (pathlib.Path(os.path.realpath(x)) for x in FILE_ROOTS))


def search(query, limit=15, max_files=20000):
    words = [w for w in re.split(r"[\s_.-]+", (query or "").lower()) if len(w) > 1]
    if not words:
        return "What should I search for?"
    import privacy
    hits, seen = [], 0
    for root in FILE_ROOTS:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            if dirpath[len(str(root)):].count(os.sep) > 6:
                dirnames[:] = []
            for f in filenames:
                seen += 1
                low = f.lower()
                score = sum(w in low for w in words)
                if score and not privacy.is_work(f, dirpath):
                    p = pathlib.Path(dirpath) / f
                    try:
                        st = p.stat()
                    except OSError:
                        continue
                    hits.append((score, st.st_mtime, p, st.st_size))
            if seen > max_files:
                break
    if not hits:
        return f"No file for “{query}” in {', '.join(r.name for r in FILE_ROOTS)}."
    hits.sort(key=lambda h: (-h[0], -h[1]))
    return "\n".join(f"{p} ({_size(s)}, {time.strftime('%Y-%m-%d', time.localtime(m))})"
                     for _, m, p, s in hits[:limit])


def _size(n):
    return f"{n / 1e6:.1f} MB" if n > 1e6 else f"{max(1, n // 1000)} kB"


def read(path, max_chars=8000):
    import privacy
    p = pathlib.Path(os.path.expandvars(os.path.expanduser((path or "").strip().strip('"'))))
    if not _inside_roots(p):
        return "Refused: only files in the shared folders (" + ", ".join(str(r) for r in FILE_ROOTS) + ")."
    if not p.is_file():
        return "File not found."
    if p.suffix.lower() not in TEXT_EXT:
        return f"I cannot read {p.suffix or 'this format'} (only text, Markdown, CSV, logs, code, Word)."
    if p.suffix.lower() == ".docx":
        try:
            with zipfile.ZipFile(p) as z:
                xml = z.read("word/document.xml").decode("utf-8", "replace")
            text = re.sub(r"<w:p[ >].*?</w:p>", lambda m: re.sub(r"<[^>]+>", "", m.group(0)) + "\n", xml, flags=re.S)
            text = re.sub(r"<[^>]+>", "", text)
        except (OSError, KeyError, zipfile.BadZipFile) as e:
            return f"Word file not readable: {e}"
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
    if privacy.is_work(str(p), text[:3000]):
        return "That looks like work — I do not read it."
    return text[:max_chars] + (f"\n… [shortened, {len(text)} characters]" if len(text) > max_chars else "")


# ── clipboard ────────────────────────────────────────────────

def clipboard(max_chars=6000):
    if not IS_WIN:
        return "The clipboard is only available on Windows."
    u, k = _user32(), ctypes.windll.kernel32
    u.GetClipboardData.restype = ctypes.c_void_p
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [ctypes.c_void_p]
    k.GlobalUnlock.argtypes = [ctypes.c_void_p]
    for _ in range(5):
        if u.OpenClipboard(0):
            break
        time.sleep(0.1)
    else:
        return "The clipboard is blocked right now."
    try:
        h = u.GetClipboardData(13)                   # CF_UNICODETEXT
        if not h:
            return "There is no text in the clipboard."
        ptr = k.GlobalLock(h)
        try:
            text = ctypes.wstring_at(ptr)
        finally:
            k.GlobalUnlock(h)
    finally:
        u.CloseClipboard()
    import privacy
    if privacy.is_work(text[:3000]):
        return "The copied text looks like work — I do not read it."
    return text[:max_chars] + (f"\n… [shortened, {len(text)} characters]" if len(text) > max_chars else "")


# ── screen (local OCR) ───────────────────────────────────────

OCR_PS = ROOT / "scripts" / "ocr_screen.ps1"


def _foreground_title():
    u = _user32()
    hwnd = u.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(512)
    u.GetWindowTextW(hwnd, buf, 512)
    return hwnd, buf.value


def screen_text(whole=False):
    """OCR of the active window (or the whole screen). Text only, image stays local and is deleted."""
    if not IS_WIN:
        return "Reading the screen is only available on Windows."
    hwnd, title = _foreground_title()
    titles = [t for _, t, _ in windows()] if whole else [title]
    bad = next((t for t in titles if SCREEN_BLOCK.search(t)), None)
    if bad:
        return f"Refused: “{bad[:40]}” is visible (passwords, bank or messages). I do not read that."
    rect = _RECT()
    if not whole:
        _user32().GetWindowRect(hwnd, ctypes.byref(rect))
    args = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(OCR_PS)]
    if not whole:
        args += ["-Left", str(rect.left), "-Top", str(rect.top), "-Width", str(max(1, rect.right - rect.left)),
                 "-Height", str(max(1, rect.bottom - rect.top))]
    try:
        p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40,
                           **hostos.QUIET)
    except subprocess.TimeoutExpired:
        return "The text recognition took too long."
    text = (p.stdout or "").strip()
    if p.returncode != 0 or not text:
        return f"Text recognition failed: {(p.stderr or 'no text recognised').strip()[:200]}"
    head = f"Window: {title}\n" if not whole else "Whole screen\n"
    return head + text[:7000]


# ── allow-listed scripts ─────────────────────────────────────

def scripts():
    try:
        data = json.loads(SCRIPTS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k.lower(): v for k, v in data.items() if not k.startswith("_") and isinstance(v, dict) and v.get("path")}


def script_list():
    s = scripts()
    if not s:
        return "No scripts allowed (config/scripts.json, template scripts.example.json)."
    return "\n".join(f"- {k}: {v.get('description', '')}" for k, v in s.items())


def script_run(name):
    s = scripts().get((name or "").lower().strip())
    if not s:
        return "This script is not allowed."
    path = pathlib.Path(os.path.expandvars(os.path.expanduser(s["path"])))
    if not path.is_file():
        return f"Script file missing: {path}"
    if path.suffix.lower() == ".ps1":
        cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path)]
    elif path.suffix.lower() in (".bat", ".cmd"):
        cmd = ["cmd.exe", "/c", str(path)]
    elif path.suffix.lower() == ".py":
        cmd = [os.sys.executable, str(path)]
    else:
        return "Only .ps1, .bat, .cmd or .py."
    cmd += [str(a) for a in s.get("args") or []]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=int(s.get("timeout") or 120), cwd=str(path.parent),
                           **hostos.QUIET)
    except subprocess.TimeoutExpired:
        return f"Cancelled: {name} ran longer than {s.get('timeout') or 120} s."
    out = ((p.stdout or "") + ("\n" + p.stderr if p.stderr else "")).strip()
    return (f"{name} {'done' if p.returncode == 0 else f'failed with error {p.returncode}'}."
            + (f"\n{out[-2500:]}" if out else ""))
