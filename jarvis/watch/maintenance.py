"""
Maintenance calendar for the homelab: not what is broken (homelab_watch), but what will need a
hand soon — all read-only, 0 tokens, once a day.

  certificates   TLS certificates of the hosts in config/homelab.json → maintenance.certs
                 ("host:port"): days until they expire. Verified when possible; self-signed
                 ones are read from the raw certificate.
  docker images  on the Pi: how old the image of every running container is (days since it
                 was built) — older than docker_max_days wants a pull
  backups        the last Hyper Backup run on the NAS (infra.backup_status): older than
                 backup_max_days is a finding
  NAS disks      a disk that is not "normal" (the dashboard's reading)

  items()        [{what, days, severity, text}] sorted by urgency
  describe()     the spoken calendar, also the tool maintenance_status
  step()         daily from 9 h: one notice per item that is due (watchers-style, once a day)

config/homelab.json:
  "maintenance": {"certs": ["homeassistant.local:8123", "nas.local:5001"], "cert_warn_days": 14,
                  "docker_max_days": 120, "backup_max_days": 3}
"""
import datetime as dt
import os
import re
import socket
import ssl
import subprocess
import time

import background
import events
import hostos

ENABLED = os.environ.get("JARVIS_MAINTENANCE", "1").strip() not in ("0", "false", "no", "off")
FROM_HOUR = 9
DEFAULTS = {"cert_warn_days": 14, "docker_max_days": 120, "backup_max_days": 3}
DOCKER_AGES_CMD = ("command -v docker >/dev/null && docker ps --format '{{.Names}} {{.Image}}' | while read n i; do "
                   "echo \"image $n $(docker image inspect --format '{{.Created}}' \"$i\" 2>/dev/null)\"; done; true")
_CACHE = {"day": "", "items": []}


def config():
    import homelab_watch
    cfg = dict(DEFAULTS)
    cfg.update(homelab_watch.config().get("maintenance") or {})
    return cfg


# ── certificates ─────────────────────────────────────────────

def _der_not_after(der):
    """notAfter from a DER certificate without any library: the second time value in the
    certificate (notBefore comes first). UTCTime (0x17) or GeneralizedTime (0x18)."""
    found = []
    i = 0
    while i < len(der) - 2 and len(found) < 2:
        tag, ln = der[i], der[i + 1]
        if tag in (0x17, 0x18) and ln in (13, 15):
            raw = der[i + 2:i + 2 + ln].decode("ascii", "replace")
            try:
                found.append(dt.datetime.strptime(raw, "%y%m%d%H%M%SZ" if tag == 0x17 else "%Y%m%d%H%M%SZ"))
            except ValueError:
                pass
            i += 2 + ln
        else:
            i += 1
    return found[1] if len(found) == 2 else None


def cert_days(host, port=443, timeout=6):
    """Days until the certificate of host:port expires (negative = expired), or None."""
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                not_after = tls.getpeercert().get("notAfter")
        expires = dt.datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after))
    except ssl.SSLCertVerificationError:
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
        expires = _der_not_after(der or b"")
        if expires is None:
            return None
    except (OSError, ValueError):
        return None
    return (expires - dt.datetime.now()).days


def certificates(cfg):
    out = []
    for spec in cfg.get("certs") or []:
        host, _, port = str(spec).partition(":")
        days = cert_days(host.strip(), int(port) if port.isdigit() else 443)
        if days is None:
            out.append(dict(what=f"Zertifikat {host}", days=None, severity="info",
                            text=f"I could not read the certificate of {host}."))
            continue
        due = days <= int(cfg.get("cert_warn_days", 14))
        text = (f"The certificate of {host} expired {-days} days ago." if days < 0 else
                f"The certificate of {host} expires in {days} days." if due else
                f"Certificate {host}: {days} days left.")
        out.append(dict(what=f"Zertifikat {host}", days=days, severity="warn" if due else "ok", text=text))
    return out


# ── docker images on the Pi ──────────────────────────────────

def _pi(cmd):
    import jarvis_mcp as j
    if not j.PI_HOST:
        return ""
    out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", f"{j.PI_USER}@{j.PI_HOST}", cmd],
                         capture_output=True, text=True, timeout=40, **hostos.QUIET)
    return out.stdout if out.returncode == 0 or out.stdout.strip() else ""


def parse_images(text, now=None):
    """[(container, days old)] from 'image <name> <created ISO>' lines."""
    now = now or dt.datetime.now(dt.timezone.utc)
    out = []
    for m in re.finditer(r"^image (\S+) (\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})", text, re.M):
        created = dt.datetime.fromisoformat(f"{m.group(2)}T{m.group(3)}+00:00")
        out.append((m.group(1), (now - created).days))
    return out


def images(cfg):
    out = []
    limit = int(cfg.get("docker_max_days", 120))
    for name, days in parse_images(_pi(DOCKER_AGES_CMD)):
        old = days > limit
        out.append(dict(what=f"Container {name}", days=limit - days, severity="warn" if old else "ok",
                        text=(f"The image of {name} on the Pi is {days} days old — a pull is due." if old
                              else f"Container {name}: image {days} days old.")))
    return out


# ── backups and disks ────────────────────────────────────────

def backup_age(text, today=None):
    """Days since the newest 'last <date>' in infra.backup_status()'s text, or None."""
    today = today or dt.date.today()
    dates = []
    for m in re.finditer(r"(?:last|zuletzt) (\d{4}-\d{2}-\d{2})", text or ""):
        try:
            dates.append(dt.date.fromisoformat(m.group(1)))
        except ValueError:
            pass
    for m in re.finditer(r"(?:last|zuletzt) (\d{1,2})\.(\d{1,2})\.(\d{4})", text or ""):
        dates.append(dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
    return (today - max(dates)).days if dates else None


def backups(cfg):
    import infra
    import jarvis_mcp as j
    if not (j.SYNO_URL and j.SYNO_USER and j.SYNO_PASSWORD):
        return []
    text = infra.backup_status()
    age = backup_age(text)
    if age is None:
        return []
    limit = int(cfg.get("backup_max_days", 3))
    old = age > limit
    return [dict(what="Backup NAS", days=limit - age, severity="warn" if old else "ok",
                 text=(f"The last backup on the NAS was {age} days ago." if old
                       else f"Backup on the NAS: last one {age} day(s) ago."))]


def disks():
    import dashboard
    s = dashboard._cached("nas", 300, dashboard._nas)
    if not isinstance(s, dict) or s.get("error") or s.get("ok") is None:
        return []
    return [dict(what="NAS-Platten", days=0 if not s["ok"] else None, severity="warn" if not s["ok"] else "ok",
                 text="A disk or a volume in the NAS is not normal — check SMART." if not s["ok"]
                 else "NAS disks: all normal.")]


# ── all together ─────────────────────────────────────────────

def items(now=None):
    now = now or dt.datetime.now()
    today = now.date().isoformat()
    if _CACHE["day"] == today:
        return _CACHE["items"]
    cfg = config()
    found = []
    for fn in (lambda: certificates(cfg), lambda: images(cfg), lambda: backups(cfg), disks):
        try:
            found += fn()
        except Exception as e:  # noqa: BLE001 — one source down must not hide the rest
            print(f"  maintenance: {e}", flush=True)
    order = {"warn": 0, "info": 1, "ok": 2}
    found.sort(key=lambda i: (order.get(i["severity"], 3), i["days"] if i["days"] is not None else 9999))
    _CACHE.update(day=today, items=found)
    return found


def describe(now=None):
    rows = items(now)
    if not rows:
        return ("Nothing in the maintenance calendar yet, Sir. I enter certificates, containers and backups as soon as "
                "config/homelab.json names something under maintenance and the Pi or NAS can be reached.")
    due = [r for r in rows if r["severity"] == "warn"]
    fine = [r for r in rows if r["severity"] == "ok"]
    out = []
    if due:
        out.append("Due: " + " ".join(r["text"] for r in due[:5]))
    if fine:
        soon = sorted((r for r in fine if r["days"] is not None), key=lambda r: r["days"])[:3]
        out.append("Fine: " + ", ".join(r["text"].rstrip(".") for r in soon) + ".")
    unknown = [r for r in rows if r["severity"] == "info"]
    if unknown:
        out.append(" ".join(r["text"] for r in unknown[:3]))
    return " ".join(out)


def step(now=None):
    now = now or dt.datetime.now()
    if now.hour < FROM_HOUR or not background.claim("maintenance", now.date().isoformat()):
        return
    import watchers
    for r in items(now):
        if r["severity"] == "warn":
            watchers.once(f"maintenance:{r['what']}", "Sir, " + r["text"][0].lower() + r["text"][1:], kind="systems")


_ASK = re.compile(r"\b(maintenance|maintenance calendar|what is (due|coming up)( soon)?( in the homelab)?|"
                  r"certificates?|what (expires|runs out)( soon)?|how old (are|is) .{0,20}(containers?|images?|backups?))\b")


def handle(n):
    return describe() if _ASK.search(n) else None


def start():
    if ENABLED:
        background.loop("maintenance", 1800, step)
