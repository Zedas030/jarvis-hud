"""
Task engine: multi-step jobs with a state per step, time limits, retries,
cancellation and a check after every step — e.g. "Prüf mein NAS".

A task is a recipe of steps. Each step:
  run()      reads something through existing functions (dashboard, infra, jarvis_mcp)
  check(r)   verifies the result really is what was expected -> (status, finding)
             status: ok | warn | fail ; a step that cannot run is "error" or "timeout"
  timeout    seconds (JARVIS_TASK_STEP_TIMEOUT default) ; retries for transient errors

States: CREATED → RUNNING → VERIFYING → COMPLETED | FAILED | CANCELLED | TIMED_OUT.
Persisted in logs/tasks.json (last 30). Progress goes to the HUD (events "task"),
the result is spoken once at the end.

All recipes here only read. Findings come with a recovery plan in words; nothing
is repaired automatically — a repair needs the user and, where a tool exists, its
confirmation card. Recipes cannot run twice at once (no duplicate side effects
even if one were added later). No LLM, 0 tokens.
"""
import concurrent.futures as cf
import json
import os
import pathlib
import re
import secrets
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
PATH = pathlib.Path(os.environ.get("JARVIS_TASKS", ROOT / "logs" / "tasks.json"))
STEP_TIMEOUT = float(os.environ.get("JARVIS_TASK_STEP_TIMEOUT", "45"))
MAX_RUNNING = int(os.environ.get("JARVIS_TASK_MAX_RUNNING", "2"))
_LOCK = threading.Lock()
_CANCEL = set()
_RUNNING = {}                    # recipe -> task id
_POOL = cf.ThreadPoolExecutor(max_workers=4, thread_name_prefix="task-step")


# ── recipe building blocks ───────────────────────────────────

def _nas_info():
    import dashboard
    s = dashboard._nas()
    if not isinstance(s, dict):
        raise RuntimeError("keine Antwort")
    if s.get("error"):
        raise LookupError(s["error"])
    return s


def _pi_info():
    import homelab_watch
    import jarvis_mcp as j
    if not j.PI_HOST:
        raise LookupError("Pi nicht eingerichtet (JARVIS_PI_HOST)")
    out = j._pi_status()
    if out.startswith("Pi nicht erreichbar"):
        raise ConnectionError(out[:120])
    return homelab_watch.parse_pi(out)


def _ok(text):
    return "ok", text


def _warn(text):
    return "warn", text


def _fail(text):
    return "fail", text


def _nas_reach(r):
    return _ok(f"{r.get('model') or 'NAS'} antwortet, {r.get('disks', '?')} Platten")


def _nas_health(r):
    return _ok("Platten und Volumes gesund") if r.get("ok") else _fail("Eine Platte oder ein Volume meldet ein Problem")


def _nas_space(r):
    pct = r.get("used_pct")
    if pct is None:
        return _warn("Belegung unbekannt")
    text = f"{pct} % belegt ({r.get('used_tb')} von {r.get('total_tb')} TB)"
    return (_fail if pct >= 95 else _warn if pct >= 85 else _ok)(text)


def _nas_temp(r):
    t = r.get("temp")
    if t is None:
        return _warn("Temperatur unbekannt")
    return (_warn if t >= 60 else _ok)(f"{t:.0f} Grad")


def _backup(text):
    import infra
    if infra.backup_failed(text):
        return _fail(text.splitlines()[0][:120])
    if text.startswith("Keine Hyper-Backup"):
        return _warn("Keine Backup-Aufgaben sichtbar (keine eingerichtet oder fehlende Rechte)")
    return _ok(text.splitlines()[0][:120])


def _pi_services(r):
    bad = [f"{n} {s}" for n, s in r["services"] if s != "active"]
    bad += [f"{n} ({s.strip()[:30]})" for n, s in r["containers"] if not s.startswith("Up") or "unhealthy" in s]
    if bad:
        return _fail("Läuft nicht sauber: " + ", ".join(bad[:5]))
    return _ok(f"{len(r['services'])} Dienste, {len(r['containers'])} Container laufen")


def _pi_values(r):
    t, d, load = r.get("temp"), r.get("disk"), r.get("load")
    msg = f"{t} Grad, Speicherkarte {d} %, Last {load}"
    if (t or 0) >= 75 or (d or 0) >= 90:
        return _warn(msg)
    return _ok(msg)


def _net(text):
    verdict = text.rsplit("Urteil:", 1)[-1].strip()
    return (_ok if "grünen Bereich" in verdict else _warn)(verdict)


def _devices(text):
    n = len([ln for ln in text.splitlines() if ln.strip()])
    return _ok(f"{n} Geräte im Netz sichtbar") if n else _warn("Keine Geräte gefunden")


def _pc(m):
    msg = f"CPU {m['pc_cpu']:.0f} %, RAM {m['pc_ram']:.0f} %" + \
          (f", VRAM {m['pc_vram']} %, GPU {m['pc_gpu_temp']:.0f} Grad" if "pc_vram" in m else "") + \
          (f", Systemplatte {m['pc_disk']:.0f} % belegt" if "pc_disk" in m else "")
    if m.get("pc_disk", 0) >= 92 or m.get("pc_gpu_temp", 0) >= 85 or m.get("pc_ram", 0) >= 95:
        return _warn(msg)
    return _ok(msg)


def _res(text):
    return (_warn if "abgegeben" in text else _ok)(text.splitlines()[0])


def _home(text):
    m = re.search(r"Nicht erreichbar \((\d+)\)", text)
    return (_warn(text.splitlines()[1]) if m else _ok("Alle Geräte erreichbar"))


def _call(name, args=None):
    import jarvis_mcp
    return lambda: jarvis_mcp.call_tool(name, args or {})


NAS = [
    ("NAS erreichen", _nas_info, _nas_reach, 1),
    ("Platten und Volumes", _nas_info, _nas_health, 0),
    ("Speicherplatz", _nas_info, _nas_space, 0),
    ("Temperatur", _nas_info, _nas_temp, 0),
    ("Backups", lambda: __import__("infra").backup_status(), _backup, 1),
]
PI = [
    ("Pi erreichen", _pi_info, lambda r: _ok("antwortet"), 1),
    ("Dienste und Container", _pi_info, _pi_services, 0),
    ("Temperatur, Speicher, Last", _pi_info, _pi_values, 0),
]
NET = [
    ("Router, Internet, DNS", lambda: __import__("infra").net_diagnose(), _net, 1),
    ("Geräte im Netz", _call("network_devices"), _devices, 0),
]
PC = [
    ("Auslastung", lambda: __import__("infra").pc_metrics(), _pc, 0),
    ("Grafikspeicher-Budget", lambda: __import__("resources").describe(), _res, 0),
]
HOME = [("Home Assistant prüfen", lambda: __import__("infra").home_check(), _home, 1)]

RECIPES = {
    "nas": ("NAS-Prüfung", NAS),
    "pi": ("Pi-Prüfung", PI),
    "netz": ("Netzwerk-Prüfung", NET),
    "pc": ("PC-Prüfung", PC),
    "haus": ("Smart-Home-Prüfung", HOME),
    "system": ("Systemcheck", NAS + PI + NET + PC + HOME),
}

RECOVERY = {
    "Platten und Volumes": "Im DSM unter Speicher-Manager nachsehen, welche Platte betroffen ist; Backup prüfen, bevor etwas getauscht wird.",
    "Speicherplatz": "Große oder alte Daten aufräumen oder Papierkörbe der Freigaben leeren — ich lösche nichts selbst.",
    "Backups": "Hyper-Backup-Protokoll im DSM ansehen und die Aufgabe manuell starten.",
    "Dienste und Container": "Den Dienst/Container auf dem Pi neu starten (per SSH, nur nach Ihrer Bestätigung).",
    "Temperatur, Speicher, Last": "Belüftung prüfen; bei voller Speicherkarte Logs oder alte Images aufräumen.",
    "Router, Internet, DNS": "Router neu starten oder beim Anbieter nach Störungen sehen; bei DNS den Pi-hole prüfen.",
    "Auslastung": "Speicherhungrige Programme schließen; Systemplatte aufräumen.",
    "Home Assistant prüfen": "Nicht erreichbare Geräte in Home Assistant prüfen (Batterie, Funk, Strom).",
    "Temperatur": "Belüftung des NAS prüfen.",
}


# ── storage ──────────────────────────────────────────────────

def _load():
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(items):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(items[-30:], ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, PATH)


def _update(tid, fn):
    with _LOCK:
        items = _load()
        for t in items:
            if t["id"] == tid:
                fn(t)
        _save(items)


def get(tid=None):
    items = _load()
    if tid:
        return next((t for t in items if t["id"] == tid), None)
    return items


def active():
    return [t for t in _load() if t["status"] in ("CREATED", "RUNNING", "VERIFYING")]


# ── running ──────────────────────────────────────────────────

def _publish(t, text=None):
    try:
        import events
        done = sum(1 for s in t["steps"] if s["status"] not in ("pending", "running"))
        events.publish("task", text or "", id=t["id"], title=t["title"], status=t["status"],
                       done=done, total=len(t["steps"]))
    except Exception:  # noqa: BLE001
        pass


def _run_step(fn, retries, timeout):
    """(result, error) with retries for exceptions; a timeout is not retried."""
    for attempt in range(retries + 1):
        fut = _POOL.submit(fn)
        try:
            return fut.result(timeout=timeout), None, attempt + 1
        except cf.TimeoutError:
            return None, "timeout", attempt + 1
        except LookupError as e:                    # not configured: retrying will not help
            return None, f"nicht verfügbar: {e}", attempt + 1
        except Exception as e:  # noqa: BLE001
            err = str(e)[:160]
            if attempt == retries:
                return None, err, attempt + 1
            time.sleep(1.0)
    return None, "?", retries + 1


def _execute(tid, recipe, steps, speak, step_timeout):
    cache = {}
    try:
        for i, (name, fn, check, retries) in enumerate(steps):
            if tid in _CANCEL:
                _update(tid, lambda t: t.update(status="CANCELLED", ended=time.time()))
                _publish(get(tid), "abgebrochen")
                return
            _update(tid, lambda t, i=i: (t["steps"][i].update(status="running", started=time.time()),
                                         t.update(status="RUNNING")))
            key = fn                                   # same source in several steps: ask it once
            if key in cache:
                result, err, attempts = cache[key]
            else:
                result, err, attempts = _run_step(fn, retries, step_timeout)
                cache[key] = (result, err, attempts)
            if err:
                status, finding = ("timeout" if err == "timeout" else "error"), err
            else:
                _update(tid, lambda t: t.update(status="VERIFYING"))
                try:
                    status, finding = check(result)
                except Exception as e:  # noqa: BLE001
                    status, finding = "error", f"Prüfung fehlgeschlagen: {e}"
            _update(tid, lambda t, i=i, s=status, f=finding, a=attempts: t["steps"][i].update(
                status=s, finding=f, attempts=a, ended=time.time()))
            _publish(get(tid))
        t = get(tid)
        bad = [s for s in t["steps"] if s["status"] in ("warn", "fail", "error", "timeout")]
        final = ("TIMED_OUT" if any(s["status"] == "timeout" for s in t["steps"]) and len(bad) == len(t["steps"])
                 else "FAILED" if all(s["status"] in ("error", "timeout") for s in t["steps"]) else "COMPLETED")
        report = summary(t, bad)
        _update(tid, lambda t: t.update(status=final, ended=time.time(), report=report))
        _publish(get(tid), report)
        if speak:
            import events
            events.publish("say", report, kind="task")
    finally:
        with _LOCK:
            _RUNNING.pop(recipe, None)
            _CANCEL.discard(tid)


def summary(t, bad=None):
    bad = [s for s in t["steps"] if s["status"] in ("warn", "fail", "error", "timeout")] if bad is None else bad
    okn = sum(1 for s in t["steps"] if s["status"] == "ok")
    if not bad:
        return f"{t['title']} abgeschlossen: alle {okn} Schritte in Ordnung."
    parts = [f"{t['title']}: {okn} von {len(t['steps'])} Schritten in Ordnung."]
    for s in bad[:4]:
        label = {"warn": "Hinweis", "fail": "Problem", "error": "nicht prüfbar", "timeout": "Zeitlimit"}[s["status"]]
        parts.append(f"{s['name']} — {label}: {s.get('finding', '')}.")
    fixes = [RECOVERY[s["name"]] for s in bad if s["status"] in ("warn", "fail") and s["name"] in RECOVERY]
    if fixes:
        parts.append("Vorschlag: " + " ".join(dict.fromkeys(fixes)) + " Ich ändere nichts ohne Ihre Freigabe.")
    return " ".join(parts)


def start(recipe, speak=True, wait=False, step_timeout=None):
    """Starts a recipe in the background. Returns (task id or None, German text)."""
    recipe = (recipe or "").lower().strip()
    if recipe not in RECIPES:
        return None, f"Diese Prüfung kenne ich nicht ({', '.join(RECIPES)})."
    title, steps = RECIPES[recipe]
    with _LOCK:
        if recipe in _RUNNING:
            return _RUNNING[recipe], f"{title} läuft schon."
        if len(_RUNNING) >= MAX_RUNNING:
            return None, "Es laufen schon zwei Prüfungen — gleich wieder."
        tid = secrets.token_hex(3)
        _RUNNING[recipe] = tid
        items = _load()
        items.append({"id": tid, "recipe": recipe, "title": title, "status": "CREATED", "created": time.time(),
                      "steps": [{"name": n, "status": "pending"} for n, _, _, _ in steps]})
        _save(items)
    th = threading.Thread(target=_execute, args=(tid, recipe, steps, speak, step_timeout or STEP_TIMEOUT),
                          daemon=True, name=f"task-{tid}")
    th.start()
    if wait:
        th.join()
    return tid, f"{title} gestartet ({len(steps)} Schritte). Ich melde mich mit dem Ergebnis."


def cancel(tid=None):
    targets = [t for t in active() if not tid or t["id"] == tid]
    for t in targets:
        _CANCEL.add(t["id"])
    return f"{len(targets)} Prüfung(en) werden abgebrochen." if targets else "Es läuft keine Prüfung."


def describe(tid=None):
    items = [get(tid)] if tid else _load()[-5:][::-1]
    items = [t for t in items if t]
    if not items:
        return "Noch keine Prüfungen."
    rows = []
    for t in items:
        steps = ", ".join(f"{s['name']}: {s['status']}" for s in t["steps"])
        rows.append(f"{t['id']} {t['title']} — {t['status']}" + (f"\n  {t['report']}" if t.get("report") else f"\n  {steps}"))
    return "\n".join(rows)


_START = re.compile(r"\b(prüf\w*|check\w*|diagnos\w*|untersuch\w*)\b")
_WHAT = [("system", r"\b(systemcheck|alles|gesamte system|alle systeme)\b"), ("nas", r"\b(nas|synology)\b"),
         ("pi", r"\b(pi|raspi|raspberry)\b"), ("netz", r"\b(netzwerk|heimnetz|wlan|internetverbindung)\b"),
         ("pc", r"\b(pc|computer|rechner)\b"), ("haus", r"\b(smart ?home|sensoren|home assistant)\b")]


def handle(n):
    """Local router hook: 'Prüf mein NAS', 'Systemcheck', 'Brich die Prüfung ab'."""
    if re.search(r"\b(brich|abbrechen|stopp?)\b.*\bprüfung\b|\bprüfung\b.*\b(abbrechen|stoppen)\b", n):
        return cancel()
    if not (_START.search(n) or re.search(r"\bsystemcheck\b", n)):
        return None
    if re.search(r"\b(ob|warum|wieso)\b", n) or len(n) > 60:
        return None                              # "prüf, ob …" is a question for the LLM, not a check run
    for recipe, pat in _WHAT:
        if re.search(pat, n):
            return start(recipe)[1]
    return None
