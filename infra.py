"""
Infrastructure and smart home checks — all read-only, 0 tokens.

  pc_status()      CPU, RAM, GPU/VRAM (nvidia-smi), disks, uptime, biggest processes
  net_diagnose()   "Warum ist das Internet langsam?" step by step: router, internet,
                   DNS, HTTP — with a verdict where the problem sits (no speed test,
                   that would download data from a third party)
  backup_status()  Hyper Backup tasks on the Synology (SYNO.Backup.Task). The Jarvis
                   DSM user may need rights to see them; if none are visible it says so.
  home_check()     Home Assistant: unavailable devices, low batteries, sensors silent
                   for a day ("Sind alle Sensoren online?")
  phone_notify()   push to the phone through the Home Assistant app
                   (JARVIS_PHONE_NOTIFY = notify service, e.g. notify.mobile_app_iphone)
  pc_metrics()     values for the homelab observer (VRAM, GPU temperature, free disk)
"""
import datetime as dt
import json
import os
import re
import socket
import subprocess
import time
import urllib.request

PHONE = os.environ.get("JARVIS_PHONE_NOTIFY", "").strip()
PHONE_LEVEL = os.environ.get("JARVIS_PHONE_PUSH", "critical").strip().lower()   # critical | all | off


def _run(cmd, timeout=10):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return p.returncode, p.stdout
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""


# ── PC ───────────────────────────────────────────────────────

def gpu():
    """[{name, util, vram_used, vram_total, temp}] from nvidia-smi, or []."""
    code, out = _run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                      "--format=csv,noheader,nounits"])
    rows = []
    if code == 0:
        for line in out.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 5:
                try:
                    rows.append(dict(name=parts[0], util=float(parts[1]), vram_used=float(parts[2]),
                                     vram_total=float(parts[3]), temp=float(parts[4])))
                except ValueError:
                    pass
    return rows


def gpu_apps():
    """[(process name, MiB)] holding VRAM (compute apps; games/graphics show as N/A on Windows)."""
    code, out = _run(["nvidia-smi", "--query-compute-apps=process_name,used_memory", "--format=csv,noheader,nounits"])
    apps = []
    if code == 0:
        for line in out.strip().splitlines():
            name, _, mem = line.rpartition(",")
            try:
                apps.append((os.path.basename(name.strip()), float(mem)))
            except ValueError:
                apps.append((os.path.basename(name.strip()), 0.0))
    return apps


def pc_metrics():
    import psutil
    m = {"pc_cpu": psutil.cpu_percent(interval=0.3), "pc_ram": psutil.virtual_memory().percent}
    g = gpu()
    if g:
        m["pc_vram"] = round(g[0]["vram_used"] * 100 / max(g[0]["vram_total"], 1))
        m["pc_gpu_temp"] = g[0]["temp"]
    try:
        m["pc_disk"] = psutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\").percent
    except OSError:
        pass
    return m


def pc_status():
    import psutil
    vm = psutil.virtual_memory()
    up = dt.timedelta(seconds=int(time.time() - psutil.boot_time()))
    lines = [f"CPU {psutil.cpu_percent(interval=0.5):.0f} % ({psutil.cpu_count()} Threads), "
             f"RAM {vm.used / 2**30:.1f} von {vm.total / 2**30:.0f} GB ({vm.percent:.0f} %), "
             f"läuft seit {up.days} Tagen {up.seconds // 3600} Stunden"]
    for g in gpu():
        lines.append(f"GPU {g['name']}: {g['util']:.0f} % Last, VRAM {g['vram_used'] / 1024:.1f} von "
                     f"{g['vram_total'] / 1024:.0f} GB, {g['temp']:.0f} Grad")
    apps = gpu_apps()
    if apps:
        lines.append("VRAM belegt von: " + ", ".join(f"{n} ({m / 1024:.1f} GB)" if m else n for n, m in apps[:6]))
    for part in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        lines.append(f"Laufwerk {part.device.rstrip(chr(92))} {u.free / 2**30:.0f} GB frei von {u.total / 2**30:.0f} GB")
    procs = []
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            procs.append((p.info["memory_info"].rss, p.info["name"]))
        except (psutil.Error, TypeError, AttributeError):
            pass
    agg = {}
    for rss, name in procs:
        agg[name] = agg.get(name, 0) + rss
    top = sorted(agg.items(), key=lambda x: -x[1])[:5]
    lines.append("Größte Programme (RAM): " + ", ".join(f"{n} {r / 2**30:.1f} GB" for n, r in top))
    return "\n".join(lines)


# ── network ──────────────────────────────────────────────────

def gateway():
    code, out = _run(["powershell.exe", "-NoProfile", "-Command",
                      "(Get-NetRoute -DestinationPrefix 0.0.0.0/0 | Sort-Object RouteMetric | Select-Object -First 1).NextHop"])
    gw = out.strip()
    return gw if code == 0 and re.fullmatch(r"\d+\.\d+\.\d+\.\d+", gw) else None


def ping(host, count=3):
    """(lost %, average ms) — None ms if nothing came back."""
    code, out = _run(["ping", "-n", str(count), "-w", "1500", host], timeout=count * 3 + 5)
    times = [int(t) for t in re.findall(r"(?:Zeit|time)[=<](\d+)\s*ms", out, re.I)]
    lost = round(100 * (count - len(times)) / count)
    return lost, (sum(times) / len(times) if times else None)


def _dns(name="www.google.com"):
    t0 = time.time()
    try:
        socket.getaddrinfo(name, 443)
        return round((time.time() - t0) * 1000)
    except OSError:
        return None


def _http(url="https://www.google.com/generate_204"):
    t0 = time.time()
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            r.read()
        return round((time.time() - t0) * 1000)
    except Exception:  # noqa: BLE001
        return None


def net_diagnose():
    rows, verdict = [], None
    gw = gateway()
    if gw:
        lost, ms = ping(gw)
        rows.append(f"Router {gw}: " + (f"{ms:.0f} ms, {lost} % Verlust" if ms is not None else "antwortet nicht"))
        if ms is None or lost >= 34:
            verdict = "Das Problem liegt im Heimnetz: der Router antwortet schlecht (WLAN oder Kabel prüfen)."
        elif ms > 30:
            verdict = verdict or "Die Verbindung zum Router ist träge — oft WLAN-Empfang oder ein überlasteter Kanal."
    else:
        rows.append("Router: kein Standard-Gateway gefunden")
        verdict = "Der PC hat gerade keine Netzwerkverbindung."
    lost, ms = ping("1.1.1.1")
    rows.append("Internet (1.1.1.1): " + (f"{ms:.0f} ms, {lost} % Verlust" if ms is not None else "nicht erreichbar"))
    if not verdict and (ms is None or lost >= 34):
        verdict = "Das Heimnetz ist in Ordnung, aber das Internet dahinter nicht — Router-Internetverbindung oder Anbieter."
    elif not verdict and ms > 80:
        verdict = "Das Internet ist erreichbar, aber langsam (hohe Latenz) — eher beim Anbieter oder eine volle Leitung."
    dns = _dns()
    rows.append("DNS: " + (f"{dns} ms" if dns is not None else "Namensauflösung fehlgeschlagen"))
    if not verdict and (dns is None or dns > 300):
        verdict = ("Namensauflösung (DNS) ist das Problem" + (" — vielleicht der Pi-hole." if os.environ.get("JARVIS_PI_HOST") else "."))
    http = _http()
    rows.append("Webseiten-Abruf: " + (f"{http} ms" if http is not None else "fehlgeschlagen"))
    if not verdict and (http is None or http > 1500):
        verdict = "Router, Internet und DNS sind ok, aber der Seitenabruf ist langsam — evtl. Browser, Proxy oder Gegenstelle."
    return "\n".join(rows) + "\nUrteil: " + (verdict or "Alles im grünen Bereich.")


# ── Synology Hyper Backup ───────────────────────────────────

def backup_status():
    import jarvis_mcp as j
    if not (j.SYNO_URL and j.SYNO_USER and j.SYNO_PASSWORD):
        return "NAS nicht verbunden (SYNOLOGY_URL, SYNOLOGY_USER, SYNOLOGY_PASSWORD in .env)."
    sid = j._syno({"api": "SYNO.API.Auth", "version": 6, "method": "login", "account": j.SYNO_USER,
                   "passwd": j.SYNO_PASSWORD, "session": "jarvis", "format": "sid"})["sid"]
    try:
        data = j._syno({"api": "SYNO.Backup.Task", "version": 1, "method": "list",
                        "additional": json.dumps(["last_bkp_time", "next_bkp_time", "last_bkp_result"])}, sid)
    finally:
        try:
            j._syno({"api": "SYNO.API.Auth", "version": 6, "method": "logout", "session": "jarvis"}, sid)
        except Exception:  # noqa: BLE001
            pass
    tasks = data.get("task_list") or []
    if not tasks:
        return ("Keine Hyper-Backup-Aufgaben sichtbar. Entweder ist keine eingerichtet, oder dem Jarvis-Benutzer auf "
                "dem NAS fehlt die Berechtigung (Hyper Backup zeigt Aufgaben nur Administratoren).")
    rows = []
    for t in tasks:
        name = t.get("name") or t.get("task_name") or f"Aufgabe {t.get('task_id')}"
        result = str(t.get("last_bkp_result") or t.get("status") or t.get("state") or "?")
        when = t.get("last_bkp_time") or ""
        nxt = t.get("next_bkp_time") or ""
        rows.append(f"{name}: {result}" + (f", zuletzt {when}" if when else "") + (f", nächstes {nxt}" if nxt else ""))
    return "\n".join(rows)


def backup_failed(text):
    return bool(re.search(r"\b(fail|error|partial|cancel|abgebrochen|fehler)", text, re.I))


# ── Home Assistant ───────────────────────────────────────────

def home_check(states=None, now=None):
    import briefing
    states = briefing._ha_states() if states is None else states
    if not states:
        return "Home Assistant liefert gerade keine Geräte."
    now = now or dt.datetime.now(dt.timezone.utc)
    domains = {"light", "switch", "sensor", "binary_sensor", "climate", "fan", "media_player", "cover"}
    unavailable, low, silent = [], [], []
    for s in states:
        eid, st = s["entity_id"], s.get("state")
        dom = eid.split(".")[0]
        if dom not in domains:
            continue
        a = s.get("attributes") or {}
        name = a.get("friendly_name") or eid
        if st in ("unavailable", "unknown"):
            unavailable.append(name)
            continue
        if a.get("device_class") == "battery" and a.get("unit_of_measurement") == "%":
            try:
                if float(st) < 20:
                    low.append(f"{name} {float(st):.0f} %")
            except ValueError:
                pass
        if dom == "sensor" and a.get("device_class") in ("temperature", "humidity"):
            try:
                seen = dt.datetime.fromisoformat(s.get("last_reported") or s.get("last_updated"))
                if (now - seen).total_seconds() > 86400:
                    silent.append(name)
            except (TypeError, ValueError):
                pass
    out = [f"{len(states)} Einträge geprüft."]
    out.append(f"Nicht erreichbar ({len(unavailable)}): " + ", ".join(unavailable[:12]) if unavailable
               else "Alle Geräte erreichbar.")
    if low:
        out.append("Batterie schwach: " + ", ".join(low[:10]))
    if silent:
        out.append("Seit über einem Tag still: " + ", ".join(silent[:10]))
    return "\n".join(out)


# ── phone ────────────────────────────────────────────────────

def phone_notify(text, title="Jarvis"):
    """Push via the HA companion app. Returns German text."""
    if not PHONE or not PHONE.startswith("notify."):
        return "Handy-Benachrichtigung nicht eingerichtet (JARVIS_PHONE_NOTIFY, z. B. notify.mobile_app_iphone)."
    import jarvis_mcp as j
    try:
        j._ha(f"/api/services/notify/{PHONE.split('.', 1)[1]}", {"title": title, "message": (text or "")[:500]})
    except Exception as e:  # noqa: BLE001
        return f"Handy nicht erreichbar: {e}"
    return "Ans Handy geschickt."


def push_if_needed(text, severity):
    """Homelab messages go to the phone too: critical ones by default (JARVIS_PHONE_PUSH)."""
    if PHONE and (PHONE_LEVEL == "all" or (PHONE_LEVEL == "critical" and severity == "critical")):
        return phone_notify(text)
    return None
