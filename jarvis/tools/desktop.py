"""
Windows desktop: arrange windows, search and read files in shared folders,
read the clipboard, read the screen (local OCR) and run allow-listed scripts.

Windows    "Spotify nach rechts", "Chrome links und Code rechts", "Discord auf
           Monitor 2", "Spotify maximieren" — local, 0 tokens (handle). Workspaces
           are routines whose steps open apps and arrange them, e.g.
           {"do": "Öffne Discord"}, {"do": "Discord nach rechts"}.
Files      Only below JARVIS_FILE_ROOTS (default Desktop, Documents, Downloads).
           Text, Markdown, CSV, logs, JSON, code and Word (.docx). Files that look
           like work are not read (privacy.py).
Clipboard  Only when asked ("was habe ich kopiert?"); text only.
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


POSITIONS = {"links": (0, 0, .5, 1), "rechts": (.5, 0, .5, 1), "oben links": (0, 0, .5, .5),
             "oben rechts": (.5, 0, .5, .5), "unten links": (0, .5, .5, .5), "unten rechts": (.5, .5, .5, .5),
             "mitte": (.15, .1, .7, .8), "voll": None}


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


def arrange(app, position="links", monitor=1, wait=10):
    """Moves the app's window; waits up to `wait` s for a just-opened app. Returns German text."""
    position = _position(position)
    if position is None:
        return "Diese Position kenne ich nicht (links, rechts, oben links, …, mitte, voll)."
    end = time.time() + wait
    while True:
        hwnd = find_window(app)
        if hwnd or time.time() >= end:
            break
        time.sleep(0.5)
    if not hwnd:
        return f"Kein Fenster von {app} gefunden."
    place(hwnd, position, int(monitor or 1))
    return f"{app} liegt jetzt {'im Vollbild' if position == 'voll' else position}" + (f" auf Monitor {monitor}" if int(monitor or 1) > 1 else "") + "."


def _position(text):
    t = (text or "").lower().strip()
    t = re.sub(r"\b(die |der |den |seite|hälfte|nach |auf )", "", t).strip()
    t = {"linke": "links", "rechte": "rechts", "vollbild": "voll", "maximieren": "voll", "maximiert": "voll",
         "groß": "voll", "ganz": "voll"}.get(t, t)
    return t if t in POSITIONS else None


_ARRANGE = re.compile(
    r"^(?:schieb |leg |pack |mach |setz |stell )?(?:das |die |den )?(?:fenster )?(?:von )?([\w .+-]{2,30}?) "
    r"(?:nach |auf die |an die |in die )?(linke seite|rechte seite|linke hälfte|rechte hälfte|links|rechts|"
    r"oben links|oben rechts|unten links|unten rechts|mitte|vollbild|maximieren|groß)"
    r"(?: (?:auf |an )?(?:den )?(?:monitor|bildschirm) (\d))?$")


def handle(n):
    """Local router hook: 'Spotify nach rechts', 'Chrome links und Code rechts'. Reply or None."""
    if not IS_WIN or not re.search(r"\b(links|rechts|oben|unten|mitte|vollbild|maximier|monitor \d)", n):
        return None
    parts = re.split(r"\s+und\s+", n.strip(" .!"))
    plan = []
    for p in parts:
        m = _ARRANGE.match(p.strip())
        if not m:
            return None
        app = re.sub(r"^(jarvis )?(bitte )?", "", m.group(1)).strip()
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
        return "Wonach soll ich suchen?"
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
        return f"Keine Datei zu „{query}“ in {', '.join(r.name for r in FILE_ROOTS)}."
    hits.sort(key=lambda h: (-h[0], -h[1]))
    return "\n".join(f"{p} ({_size(s)}, {time.strftime('%d.%m.%Y', time.localtime(m))})"
                     for _, m, p, s in hits[:limit])


def _size(n):
    return f"{n / 1e6:.1f} MB" if n > 1e6 else f"{max(1, n // 1000)} kB"


def read(path, max_chars=8000):
    import privacy
    p = pathlib.Path(os.path.expandvars(os.path.expanduser((path or "").strip().strip('"'))))
    if not _inside_roots(p):
        return "Abgelehnt: nur Dateien in den freigegebenen Ordnern (" + ", ".join(str(r) for r in FILE_ROOTS) + ")."
    if not p.is_file():
        return "Datei nicht gefunden."
    if p.suffix.lower() not in TEXT_EXT:
        return f"{p.suffix or 'Dieses Format'} kann ich nicht lesen (nur Text, Markdown, CSV, Logs, Code, Word)."
    if p.suffix.lower() == ".docx":
        try:
            with zipfile.ZipFile(p) as z:
                xml = z.read("word/document.xml").decode("utf-8", "replace")
            text = re.sub(r"<w:p[ >].*?</w:p>", lambda m: re.sub(r"<[^>]+>", "", m.group(0)) + "\n", xml, flags=re.S)
            text = re.sub(r"<[^>]+>", "", text)
        except (OSError, KeyError, zipfile.BadZipFile) as e:
            return f"Word-Datei nicht lesbar: {e}"
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
    if privacy.is_work(str(p), text[:3000]):
        return "Das sieht nach Arbeit aus — lese ich nicht."
    return text[:max_chars] + (f"\n… [gekürzt, {len(text)} Zeichen]" if len(text) > max_chars else "")


# ── clipboard ────────────────────────────────────────────────

def clipboard(max_chars=6000):
    if not IS_WIN:
        return "Zwischenablage gibt es nur unter Windows."
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
        return "Die Zwischenablage ist gerade blockiert."
    try:
        h = u.GetClipboardData(13)                   # CF_UNICODETEXT
        if not h:
            return "In der Zwischenablage ist kein Text."
        ptr = k.GlobalLock(h)
        try:
            text = ctypes.wstring_at(ptr)
        finally:
            k.GlobalUnlock(h)
    finally:
        u.CloseClipboard()
    import privacy
    if privacy.is_work(text[:3000]):
        return "Der kopierte Text sieht nach Arbeit aus — den lese ich nicht."
    return text[:max_chars] + (f"\n… [gekürzt, {len(text)} Zeichen]" if len(text) > max_chars else "")


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
        return "Bildschirm lesen gibt es nur unter Windows."
    hwnd, title = _foreground_title()
    titles = [t for _, t, _ in windows()] if whole else [title]
    bad = next((t for t in titles if SCREEN_BLOCK.search(t)), None)
    if bad:
        return f"Abgelehnt: „{bad[:40]}“ ist sichtbar (Passwörter, Bank oder Nachrichten). Das lese ich nicht."
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
        return "Die Texterkennung hat zu lange gebraucht."
    text = (p.stdout or "").strip()
    if p.returncode != 0 or not text:
        return f"Texterkennung fehlgeschlagen: {(p.stderr or 'kein Text erkannt').strip()[:200]}"
    head = f"Fenster: {title}\n" if not whole else "Ganzer Bildschirm\n"
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
        return "Keine Skripte freigegeben (config/scripts.json, Vorlage scripts.example.json)."
    return "\n".join(f"- {k}: {v.get('beschreibung', '')}" for k, v in s.items())


def script_run(name):
    s = scripts().get((name or "").lower().strip())
    if not s:
        return "Dieses Skript ist nicht freigegeben."
    path = pathlib.Path(os.path.expandvars(os.path.expanduser(s["path"])))
    if not path.is_file():
        return f"Skriptdatei fehlt: {path}"
    if path.suffix.lower() == ".ps1":
        cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path)]
    elif path.suffix.lower() in (".bat", ".cmd"):
        cmd = ["cmd.exe", "/c", str(path)]
    elif path.suffix.lower() == ".py":
        cmd = [os.sys.executable, str(path)]
    else:
        return "Nur .ps1, .bat, .cmd oder .py."
    cmd += [str(a) for a in s.get("args") or []]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=int(s.get("timeout") or 120), cwd=str(path.parent),
                           **hostos.QUIET)
    except subprocess.TimeoutExpired:
        return f"Abgebrochen: {name} lief länger als {s.get('timeout') or 120} s."
    out = ((p.stdout or "") + ("\n" + p.stderr if p.stderr else "")).strip()
    return (f"{name} {'fertig' if p.returncode == 0 else f'mit Fehler {p.returncode}'}."
            + (f"\n{out[-2500:]}" if out else ""))
