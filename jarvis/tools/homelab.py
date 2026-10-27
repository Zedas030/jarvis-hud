"""
Homelab figures — what Grafana shows, read at the source; all read-only, 0 tokens.

  Prometheus (PROMETHEUS_URL, default http://<pi>:9090) already scrapes the Pi
  (node), the FRITZ!Box (fritz_exporter), Home Assistant and this PC
  (windows_exporter). Jarvis asks it directly instead of the Grafana UI:

    describe(topic)   internet | pi | pc | wlan | alles — current values plus today's peak
    query(expr, hours) free PromQL for questions over a time span ("wann war die Last
                      heute am höchsten?"); hours > 0 = range query, a few points per series
    tile()            the compact values for the HUD tile

  Pi-hole 6 (PIHOLE_URL, default http://<pi>:8080, PIHOLE_PASSWORD = an app password
  from Pi-hole → Settings → Web interface/API). pihole() = queries today, share
  blocked, top blocked domains, busiest devices, filter on/off. Only blocked domains
  are listed — what was looked up and allowed says too much about the household.
"""
import datetime as dt
import json
import os
import time
import urllib.parse
import urllib.request

_PI = os.environ.get("JARVIS_PI_HOST", "").strip()
PROM = os.environ.get("PROMETHEUS_URL", "").strip().rstrip("/") or (f"http://{_PI}:9090" if _PI else "")
PIHOLE = os.environ.get("PIHOLE_URL", "").strip().rstrip("/") or (f"http://{_PI}:8080" if _PI else "")
PIHOLE_PASSWORD = os.environ.get("PIHOLE_PASSWORD", "").strip()
MAX_SERIES = 12      # a free query may match hundreds of series; the answer is read aloud


# ── Prometheus ───────────────────────────────────────────────
def _prom(path, **params):
    if not PROM:
        raise RuntimeError("Prometheus nicht eingerichtet (PROMETHEUS_URL oder JARVIS_PI_HOST in .env)")
    with urllib.request.urlopen(f"{PROM}{path}?{urllib.parse.urlencode(params)}", timeout=15) as r:
        d = json.loads(r.read())
    if d.get("status") != "success":
        raise RuntimeError(d.get("error") or "Prometheus-Fehler")
    return d["data"]["result"]


def value(expr):
    """First value of an instant query, or None."""
    res = _prom("/api/v1/query", query=expr)
    return float(res[0]["value"][1]) if res else None


def values(expr):
    """[(labels, value)] of an instant query."""
    return [(r["metric"], float(r["value"][1])) for r in _prom("/api/v1/query", query=expr)]


def _since_midnight():
    now = dt.datetime.now()
    return max(60, int((now - now.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds()))


def _de(x, digits=0):
    return "—" if x is None else f"{x:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _gb(b):
    return "—" if b is None else (_de(b / 1e12, 1) + " TB" if b >= 1e12 else _de(b / 1e9, 0) + " GB")


# PromQL per figure — fixed, so the summaries never depend on what the model writes.
# Throughput comes from the box's own rate gauge: its byte counter wraps every few GB,
# and rate() over it reported 26 Gbit/s upload and "4 TB today".
Q = {
    "down": 'max(fritz_wan_datarate_bytes{direction="rx"}) * 8 / 1e6',
    "up": 'max(fritz_wan_datarate_bytes{direction="tx"}) * 8 / 1e6',
    "line_down": 'max(fritz_wan_max_bitrate_bps{direction="rx"}) / 1e6',
    "line_up": 'max(fritz_wan_max_bitrate_bps{direction="tx"}) / 1e6',
    "link": "max(fritz_wan_phys_link_status)",
    "fritz_uptime": "max(fritz_uptime_seconds_total)",
    "wifi": "sum by (wifi_name) (fritz_wifi_associations_count)",
    "known": "max(fritz_known_devices_count)",
    "pi_temp": "max(node_thermal_zone_temp)",
    "pi_cpu": '100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m])))',
    "pi_load": "max(node_load1)",
    "pi_ram": "100 * (1 - sum(node_memory_MemAvailable_bytes) / sum(node_memory_MemTotal_bytes))",
    "pi_disk_free": 'min(node_filesystem_avail_bytes{mountpoint="/"})',
    "pi_boot": "max(node_boot_time_seconds)",
    "pc_cpu": '100 * (1 - avg(rate(windows_cpu_time_total{mode="idle"}[5m])))',
    "pc_ram": "100 * (1 - sum(windows_memory_available_bytes) / sum(windows_memory_physical_total_bytes))",
    "pc_disks": 'windows_logical_disk_free_bytes{volume=~"[A-Z]:"}',
}


def _today_peak(expr, agg="max"):
    span = _since_midnight()
    return value(f"{agg}_over_time(({expr})[{span}s:5m])")


def internet():
    down, up = value(Q["down"]), value(Q["up"])
    span = _since_midnight()
    # bytes today = average rate since midnight x seconds since midnight
    got = value(f'avg_over_time(max(fritz_wan_datarate_bytes{{direction="rx"}})[{span}s:1m]) * {span}')
    sent = value(f'avg_over_time(max(fritz_wan_datarate_bytes{{direction="tx"}})[{span}s:1m]) * {span}')
    link, uptime = value(Q["link"]), value(Q["fritz_uptime"])
    rows = [f"internet: {'verbunden' if link == 1 else 'KEINE Leitung'}, gerade {_de(down, 1)} Mbit/s runter, "
            f"{_de(up, 1)} Mbit/s hoch (Leitung {_de(value(Q['line_down']))}/{_de(value(Q['line_up']))} Mbit/s)",
            f"heute: {_gb(got)} empfangen, {_gb(sent)} gesendet, Spitze {_de(_today_peak(Q['down']), 0)} Mbit/s runter"]
    if uptime:
        rows.append(f"fritzbox läuft seit {_de(uptime / 86400, 0)} Tagen ohne Neustart")
    return rows


def wlan():
    bands = ", ".join(f"{l.get('wifi_name', '?')} {int(v)}" for l, v in values(Q["wifi"]) if v)
    return [f"wlan: {bands or 'keine Geräte'} verbunden, {_de(value(Q['known']))} Geräte bekannt"]


def pi():
    boot = value(Q["pi_boot"])
    return [f"pi: {_de(value(Q['pi_temp']), 1)} °C (heute max {_de(_today_peak(Q['pi_temp']), 1)} °C), "
            f"CPU {_de(value(Q['pi_cpu']))} %, Last {_de(value(Q['pi_load']), 2)}, RAM {_de(value(Q['pi_ram']))} %, "
            f"frei auf der Karte {_gb(value(Q['pi_disk_free']))}"
            + (f", läuft seit {_de((time.time() - boot) / 86400, 0)} Tagen" if boot else "")]


def pc():
    disks = ", ".join(f"{l.get('volume')} {_gb(v)} frei" for l, v in sorted(values(Q["pc_disks"]), key=lambda x: x[0].get("volume", "")))
    return [f"pc: CPU {_de(value(Q['pc_cpu']))} % (heute Spitze {_de(_today_peak(Q['pc_cpu']))} %), "
            f"RAM {_de(value(Q['pc_ram']))} %, {disks}"]


TOPICS = {"internet": internet, "wlan": wlan, "pi": pi, "pc": pc}


def describe(topic="alles"):
    topic = (topic or "alles").strip().lower()
    parts = TOPICS.values() if topic in ("alles", "all", "") else [TOPICS.get(topic)]
    if None in parts:
        return f"Unbekannt: {topic}. Möglich: {', '.join(TOPICS)}, alles."
    rows = []
    for fn in parts:
        try:
            rows += fn()
        except Exception as e:  # noqa: BLE001 — one exporter down must not hide the others
            rows.append(f"{fn.__name__}: nicht lesbar ({str(e)[:80]})")
    return "\n".join(rows)


def query(expr, hours=0):
    """Free PromQL, read-only by nature. hours > 0: range over the last hours, ~8 points each."""
    expr = (expr or "").strip()
    if not expr:
        return "Leere Abfrage."
    try:
        if hours and float(hours) > 0:
            end = time.time()
            start = end - float(hours) * 3600
            step = max(60, int((end - start) / 8))
            res = _prom("/api/v1/query_range", query=expr, start=int(start), end=int(end), step=step)
            out = []
            for r in res[:MAX_SERIES]:
                pts = ", ".join(f"{dt.datetime.fromtimestamp(float(t)):%H:%M} {_de(float(v), 2)}" for t, v in r["values"])
                out.append(f"{_labels(r['metric'])}: {pts}")
        else:
            res = _prom("/api/v1/query", query=expr)
            out = [f"{_labels(r['metric'])}: {_de(float(r['value'][1]), 2)}" for r in res[:MAX_SERIES]]
        more = f"\n… {len(res) - MAX_SERIES} weitere Reihen" if len(res) > MAX_SERIES else ""
        return ("\n".join(out) or "Keine Daten.") + more
    except Exception as e:  # noqa: BLE001
        return f"Prometheus: {str(e)[:200]}"


def _labels(m):
    keep = {k: v for k, v in m.items() if k not in ("instance", "serial", "friendly_name")}
    return ", ".join(f"{k}={v}" for k, v in keep.items()) or "wert"


# ── Pi-hole 6 ────────────────────────────────────────────────
def _pihole(sid, path):
    req = urllib.request.Request(PIHOLE + path, headers={"X-FTL-SID": sid, "accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _pihole_session():
    req = urllib.request.Request(PIHOLE + "/api/auth", data=json.dumps({"password": PIHOLE_PASSWORD}).encode(),
                                 method="POST", headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        s = (json.loads(r.read()).get("session") or {})
    if not s.get("valid"):
        raise RuntimeError("Anmeldung abgelehnt — App-Passwort prüfen")
    return s["sid"]


def _pihole_logout(sid):
    try:
        urllib.request.urlopen(urllib.request.Request(PIHOLE + "/api/auth", method="DELETE",
                                                      headers={"X-FTL-SID": sid}), timeout=5).close()
    except Exception:  # noqa: BLE001 — sessions expire on their own
        pass


def pihole_data():
    """-> dict or raises. One login per call; Pi-hole allows only a few sessions at once."""
    if not PIHOLE_PASSWORD:
        raise RuntimeError("PIHOLE_PASSWORD fehlt in .env (Pi-hole → Einstellungen → Web-Oberfläche/API → App-Passwort)")
    sid = _pihole_session()
    try:
        s = _pihole(sid, "/api/stats/summary")
        blocked = _pihole(sid, "/api/stats/top_domains?blocked=true&count=5").get("domains") or []
        clients = _pihole(sid, "/api/stats/top_clients?count=5").get("clients") or []
        blocking = (_pihole(sid, "/api/dns/blocking") or {}).get("blocking")
    finally:
        _pihole_logout(sid)
    q = s.get("queries") or {}
    return {"total": q.get("total"), "blocked": q.get("blocked"), "pct": q.get("percent_blocked"),
            "clients": (s.get("clients") or {}).get("active"), "list": (s.get("gravity") or {}).get("domains_being_blocked"),
            "blocking": blocking, "top_blocked": [(d.get("domain"), d.get("count")) for d in blocked],
            "top_clients": [(c.get("name") or c.get("ip"), c.get("count")) for c in clients]}


def pihole():
    try:
        d = pihole_data()
    except Exception as e:  # noqa: BLE001
        return f"Pi-hole nicht lesbar: {str(e)[:160]}"
    state = {"enabled": "an", "disabled": "AUS", "failed": "fehlerhaft"}.get(d["blocking"], d["blocking"] or "?")
    rows = [f"pi-hole: Filter {state}, {_de(d['total'])} Anfragen (letzte 24 h), {_de(d['blocked'])} blockiert "
            f"({_de(d['pct'], 1)} %), {_de(d['clients'])} aktive Geräte, Sperrliste {_de(d['list'])} Domains"]
    if d["top_blocked"]:
        rows.append("am häufigsten blockiert: " + ", ".join(f"{n} ({_de(c)})" for n, c in d["top_blocked"]))
    if d["top_clients"]:
        rows.append("aktivste Geräte: " + ", ".join(f"{n} ({_de(c)})" for n, c in d["top_clients"]))
    return "\n".join(rows)


# ── HUD tile ─────────────────────────────────────────────────
def tile():
    """Few numbers, each on its own: a missing exporter leaves a dash, not an empty tile."""
    out = {}
    for key in ("down", "up", "line_down", "link", "pi_temp", "pi_cpu"):   # the PC is in the live strip
        try:
            out[key] = value(Q[key])
        except Exception:  # noqa: BLE001
            out[key] = None
    try:
        d = pihole_data()
        out.update(ph_pct=d["pct"], ph_blocked=d["blocked"], ph_on=d["blocking"] == "enabled")
    except Exception as e:  # noqa: BLE001
        out.update(ph_pct=None, ph_error=str(e)[:80])
    return out
