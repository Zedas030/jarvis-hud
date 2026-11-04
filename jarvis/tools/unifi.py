"""
UniFi network controller: who is in the WLAN right now, and how the access points are doing —
read-only, a second presence source next to Home Assistant.

  UNIFI_URL        https://10.0.0.1 (the UniFi OS console; self-signed certificate is fine)
  UNIFI_API_KEY    an API key (Console → Control Plane → Integrations) — the Network API v1
  UNIFI_USER / UNIFI_PASSWORD   instead of a key: a local controller user (legacy API)
  UNIFI_SITE       site name for the legacy API (default "default")
  UNIFI_VERIFY_TLS 1 to verify the certificate (default 0)

  clients()        [{name, ip, wired, ssid, signal, since}] of the connected clients
  devices()        [{name, model, state, ip}] of the UniFi devices (APs, switches, gateway)
  describe(topic)  spoken text for the tool unifi_status: clients | geraete | alles
  online(names)    which of the given client names are online (presence for other modules)

Client names come from the devices themselves (DHCP) — the tool's text is untrusted data.
"""
import datetime as dt
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request

URL = os.environ.get("UNIFI_URL", "").strip().rstrip("/")
API_KEY = os.environ.get("UNIFI_API_KEY", "").strip()
USER = os.environ.get("UNIFI_USER", "").strip()
PASSWORD = os.environ.get("UNIFI_PASSWORD", "").strip()
SITE = os.environ.get("UNIFI_SITE", "default").strip() or "default"
VERIFY = os.environ.get("UNIFI_VERIFY_TLS", "0").strip() in ("1", "true", "yes")
TIMEOUT = 10
_SESSION = {"cookie": "", "csrf": "", "at": 0.0, "site_id": ""}
_CACHE = {"at": 0.0, "clients": None}


def configured():
    return bool(URL and (API_KEY or (USER and PASSWORD)))


def _ctx():
    if VERIFY:
        return ssl.create_default_context()
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    return ctx


def _request(path, body=None, headers=None):
    req = urllib.request.Request(URL + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers=dict({"accept": "application/json", "content-type": "application/json"},
                                              **(headers or {})))
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx()) as r:
        raw = r.read()
        return json.loads(raw or b"null"), r.headers


# ── Network API v1 (API key) ─────────────────────────────────

def _v1(path, **params):
    q = ("?" + "&".join(f"{k}={v}" for k, v in params.items())) if params else ""
    data, _ = _request("/proxy/network/integration/v1" + path + q, headers={"X-API-KEY": API_KEY})
    return data


def _site_id():
    if not _SESSION["site_id"]:
        sites = (_v1("/sites") or {}).get("data") or []
        if not sites:
            raise RuntimeError("keine Site gefunden")
        _SESSION["site_id"] = sites[0]["id"]
    return _SESSION["site_id"]


def _clients_v1():
    rows = (_v1(f"/sites/{_site_id()}/clients", limit=200) or {}).get("data") or []
    out = []
    for c in rows:
        out.append(dict(name=c.get("name") or c.get("macAddress", "?"), ip=c.get("ipAddress", ""),
                        wired=(c.get("type") or "").upper() == "WIRED", ssid="", signal=None,
                        since=c.get("connectedAt", "")))
    return out


def _devices_v1():
    rows = (_v1(f"/sites/{_site_id()}/devices", limit=100) or {}).get("data") or []
    return [dict(name=d.get("name") or d.get("model", "?"), model=d.get("model", ""), state=(d.get("state") or "").lower(),
                 ip=d.get("ipAddress", "")) for d in rows]


# ── legacy API (user + password) ─────────────────────────────

def _login():
    if _SESSION["cookie"] and time.time() - _SESSION["at"] < 1800:
        return
    _, headers = _request("/api/auth/login", {"username": USER, "password": PASSWORD, "rememberMe": False})
    cookies = headers.get_all("set-cookie") or []
    _SESSION.update(cookie="; ".join(c.split(";", 1)[0] for c in cookies), csrf=headers.get("x-csrf-token", ""),
                    at=time.time())


def _legacy(path):
    _login()
    try:
        data, _ = _request(f"/proxy/network/api/s/{SITE}{path}",
                           headers={"cookie": _SESSION["cookie"], "x-csrf-token": _SESSION["csrf"]})
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            _SESSION.update(cookie="", at=0.0)
        raise
    return data.get("data") or []


def _clients_legacy():
    out = []
    for c in _legacy("/stat/sta"):
        out.append(dict(name=c.get("name") or c.get("hostname") or c.get("mac", "?"), ip=c.get("ip", ""),
                        wired=bool(c.get("is_wired")), ssid=c.get("essid", ""), signal=c.get("signal"),
                        since=dt.datetime.fromtimestamp(c["assoc_time"]).isoformat() if c.get("assoc_time") else ""))
    return out


def _devices_legacy():
    return [dict(name=d.get("name") or d.get("model", "?"), model=d.get("model", ""),
                 state={1: "online", 0: "offline"}.get(d.get("state"), str(d.get("state"))), ip=d.get("ip", ""))
            for d in _legacy("/stat/device")]


# ── public ───────────────────────────────────────────────────

def clients(ttl=30):
    if not configured():
        raise RuntimeError("UniFi nicht eingerichtet (UNIFI_URL und UNIFI_API_KEY oder UNIFI_USER/UNIFI_PASSWORD in .env)")
    if _CACHE["clients"] is not None and time.time() - _CACHE["at"] < ttl:
        return _CACHE["clients"]
    rows = _clients_v1() if API_KEY else _clients_legacy()
    _CACHE.update(at=time.time(), clients=rows)
    return rows


def devices():
    if not configured():
        raise RuntimeError("UniFi nicht eingerichtet")
    return _devices_v1() if API_KEY else _devices_legacy()


def online(names):
    """Those of `names` (substrings, case-insensitive) with a connected client, or [] without UniFi."""
    try:
        rows = clients()
    except Exception:  # noqa: BLE001
        return []
    want = [n.lower() for n in names if n]
    return [n for n in want if any(n in (c["name"] or "").lower() for c in rows)]


def say_clients(rows):
    if not rows:
        return "Gerade ist kein Gerät im Netz verbunden — oder der Controller sieht keines."
    wifi = [c for c in rows if not c["wired"]]
    wired = [c for c in rows if c["wired"]]
    by_ssid = {}
    for c in wifi:
        by_ssid.setdefault(c["ssid"] or "WLAN", []).append(c["name"])
    parts = [f"{len(rows)} Geräte verbunden, {len(wifi)} im WLAN, {len(wired)} per Kabel."]
    for ssid, names in sorted(by_ssid.items(), key=lambda kv: -len(kv[1])):
        parts.append(f"{ssid}: " + ", ".join(sorted(names)[:12]) + (f" und {len(names) - 12} weitere" if len(names) > 12 else ""))
    if wired:
        parts.append("Kabel: " + ", ".join(sorted(c["name"] for c in wired)[:10]))
    weak = [c for c in wifi if isinstance(c.get("signal"), (int, float)) and c["signal"] <= -75]
    if weak:
        parts.append("Schwacher Empfang: " + ", ".join(f"{c['name']} ({c['signal']} dBm)" for c in weak[:5]))
    return "\n".join(parts)


def say_devices(rows):
    if not rows:
        return "Keine UniFi-Geräte gefunden."
    off = [d for d in rows if d["state"] not in ("online", "1")]
    head = f"{len(rows)} UniFi-Geräte, " + (f"{len(off)} nicht online: " + ", ".join(d["name"] for d in off) if off else "alle online")
    return head + ". " + ", ".join(f"{d['name']} ({d['model']})" for d in rows[:8]) + "."


def describe(topic="clients"):
    try:
        t = (topic or "clients").lower()
        if t in ("geraete", "geräte", "devices"):
            return say_devices(devices())
        if t in ("alles", "all"):
            return say_clients(clients()) + "\n" + say_devices(devices())
        return say_clients(clients())
    except Exception as e:  # noqa: BLE001
        return f"UniFi nicht lesbar: {str(e)[:160]}"


_ASK = re.compile(r"\b(wer ist (gerade |alles )?(im|in meinem) (wlan|netz|netzwerk)|welche geräte (sind|hängen) "
                  r"(gerade )?(im|am) (wlan|netz|netzwerk)|wie viele geräte (sind )?(im|am) (wlan|netz)|unifi|"
                  r"access ?points?)\b")


def handle(n):
    if not _ASK.search(n) or not configured():
        return None
    return describe("geraete" if re.search(r"access ?point|unifi", n) else "clients")
