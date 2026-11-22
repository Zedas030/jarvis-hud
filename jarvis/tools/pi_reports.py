"""
Pi reports — reads the status of the homelab tools on the Pi; read-only, 0 tokens.

    backups   Restore Probe (:8096/latest.json): last night's test restore of every backed-up service
    updates   Update-Schleuse / update airlock (:8100/status.json): container updates waiting, held back or failed
    power     Stromfresser-Detektiv / power hog detective (:8101/api/status): open findings on the power plugs
    zigbee    Zigbee-Platzberater / placement advisor (:8099/api/state): router recommendation and channel check
    battery   Akku-Doktor / battery doctor (:8104/api/status): health of the home battery (usable kWh, SOH, cycles, warranty)
    automations  Automations-Prüfer / automation checker (:8102/api/report): broken, looping or conflicting HA automations
    drift     Homelab-Drift (:8097/metrics, Prometheus text): changes on the hosts, scan health

    report(topic)      (severity, English sentence); severity "ok", "attention" or "unknown"
    spoken(topic)      the sentence for the voice output ("all" = every tool in one go)
    describe(topic)    text for the LLM tool `pi_reports`
    briefing_line()    only what needs attention, "" when everything is fine — for the morning briefing

The tools are still growing, so every reader only relies on a few fields and treats anything else as
optional. Texts the tools write themselves (power findings, Zigbee advice) are passed on as they are.
URLs: JARVIS_RESTORE_PROBE_URL, JARVIS_UPDATE_AIRLOCK_URL, JARVIS_STROMFRESSER_URL, JARVIS_ZIGBEE_ADVISOR_URL,
JARVIS_AKKU_DOKTOR_URL, JARVIS_AUTOMATIONS_PRUEFER_URL, JARVIS_HOMELAB_DRIFT_URL in .env; default http://<JARVIS_PI_HOST>:<port>/<path>.
"""
import json
import os
import time
import urllib.request

_PI = os.environ.get("JARVIS_PI_HOST", "").strip()
TIMEOUT = float(os.environ.get("JARVIS_PI_REPORTS_TIMEOUT", "4"))
CACHE_S = float(os.environ.get("JARVIS_PI_REPORTS_CACHE_S", "60"))
RESTORE_MAX_AGE_H = 36       # the probe runs every night; older than that means it did not run
MAX_ITEMS = 3                # findings read aloud; the rest is counted


def _url(env, port, path):
    url = os.environ.get(env, "").strip()
    return url or (f"http://{_PI}:{port}{path}" if _PI else "")


SOURCES = {
    "backups": ("Restore Probe", _url("JARVIS_RESTORE_PROBE_URL", 8096, "/latest.json")),
    "updates": ("Update airlock", _url("JARVIS_UPDATE_AIRLOCK_URL", 8100, "/status.json")),
    "power": ("Power hog detective", _url("JARVIS_STROMFRESSER_URL", 8101, "/api/status")),
    "zigbee": ("Zigbee placement advisor", _url("JARVIS_ZIGBEE_ADVISOR_URL", 8099, "/api/state")),
    "battery": ("Battery doctor", _url("JARVIS_AKKU_DOKTOR_URL", 8104, "/api/status")),
    "automations": ("Automation checker", _url("JARVIS_AUTOMATIONS_PRUEFER_URL", 8102, "/api/report")),
    "drift": ("Homelab drift", _url("JARVIS_HOMELAB_DRIFT_URL", 8097, "/metrics")),
}
_PROM = {"drift"}            # Homelab-Drift only writes HTML pages and its /metrics; no JSON of its own
CHECKER_STALE_H = 3          # the checker runs every few minutes; hours without a check means it hangs
DRIFT_STALE_H = 2            # Drift scans every 15 min
_CACHE = {}


def _prom(text):
    """Prometheus text format -> {(name, ((label, value), ...)): float}. Enough for counters and gauges."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        head, _, value = line.rpartition(" ")
        name, _, labels = head.partition("{")
        pairs = tuple(sorted((k.strip(), v.strip().strip('"'))
                             for k, _, v in (p.partition("=") for p in labels.rstrip("}").split(",") if "=" in p)))
        try:
            out[(name, pairs)] = float(value)
        except ValueError:
            continue
    return out


def fetch(topic):
    name, url = SOURCES[topic]
    if not url:
        raise RuntimeError(f"{name} is not set up (JARVIS_PI_HOST in .env)")
    hit = _CACHE.get(topic)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
        raw = r.read()
    data = _prom(raw.decode("utf-8", "replace")) if topic in _PROM else json.loads(raw)
    _CACHE[topic] = (time.time(), data)
    return data


def _join(names):
    names = [n for n in names if n]
    if len(names) > 1:
        return ", ".join(names[:-1]) + " and " + names[-1]
    return names[0] if names else ""


def _more(items, shown):
    rest = len(items) - shown
    return f" Plus {rest} more." if rest > 0 else ""


# ── Restore Probe ────────────────────────────────────────────────
def _backups(d, brief=False):
    services = d.get("services") or []
    bad = [s for s in services if s.get("status") not in ("ok", "skipped")]
    finished = d.get("finished") or d.get("started") or 0
    age_h = (time.time() - finished) / 3600 if finished else None
    if age_h is not None and age_h > RESTORE_MAX_AGE_H:
        return "attention", (f"The last restore test of the backups is {age_h / 24:.0f} days old – "
                             "the Restore Probe has not run since.")
    if d.get("error") and not services:
        return "attention", "Last night's restore test of the backups failed."
    if bad:
        names = [s.get("title") or s.get("name") for s in bad]
        warn_only = all(s.get("status") in ("warn", "warning") for s in bad)
        what = "finished with warnings" if warn_only else "could not be restored cleanly"
        return "attention", (f"In last night's backup test, {_join(names[:MAX_ITEMS])} {what}."
                             f"{_more(names, MAX_ITEMS)} {len(services) - len(bad)} of {len(services)} "
                             "services were fine.")
    if not services:
        return "unknown", "The Restore Probe has no report with services yet."
    return "ok", f"The backups are fine: all {len(services)} services were test-restored last night."


# ── Update airlock ───────────────────────────────────────────────
_POSTPONE = (("at home", "because you were at home"), ("active", "because you were at home"),
             ("quiet", "because of quiet hours"), ("window", "because there was no maintenance window"))
_PRETTY = {"homeassistant": "Home Assistant", "uptime-kuma": "Uptime Kuma"}


def _cname(name):
    return _PRETTY.get(name, name)


def _updates(d, brief=False):
    cs = d.get("containers") or {}
    by = {}
    for name, c in cs.items():
        by.setdefault((c or {}).get("status") or "", []).append((name, c or {}))
    parts, severity = [], "ok"
    broken = by.get("error", []) + by.get("test_failed", []) + by.get("rolled_back", [])
    if broken:
        severity = "attention"
        parts.append(f"An update of {_join([_cname(n) for n, _ in broken[:MAX_ITEMS]])} failed or was rolled back."
                     + _more(broken, MAX_ITEMS))
    for name, c in by.get("held", []):
        severity = "attention"
        why = {"breaking_change": "it has breaking changes", "major": "it is a major version jump"}.get(
            c.get("held_reason") or "", "it needs a check first")
        ver = f" {c['available_version']}" if c.get("available_version") and "@" not in c["available_version"] else ""
        parts.append(f"{_cname(name)}{ver} is waiting for you because {why}.")
    pending = by.get("ready", []) + by.get("candidate", [])
    if pending and not brief:          # routine, runs on its own: not worth the morning briefing
        line = (f"{len(pending)} container update{'s are' if len(pending) != 1 else ' is'} pending and will "
                "go through automatically")
        reason = ((d.get("global") or {}).get("last_postponed") or {}).get("reason") or ""
        why = next((w for key, w in _POSTPONE if key in reason), "")
        parts.append(line + (f"; last postponed {why}." if why else "."))
    if d.get("overall") == "error" and not broken:
        severity = "attention"
        parts.append("The last check of the update airlock failed.")
    if not parts:
        return "ok", "All containers are up to date."
    return severity, " ".join(parts)


# ── Power hog detective ──────────────────────────────────────────
def _power(d, brief=False):
    open_ = [o for o in d.get("offen") or [] if (o.get("zustand") or "offen") == "offen"]
    if open_:
        texts = [(o.get("meldung") or o.get("geraet") or "").strip() for o in open_]
        head = "One open finding" if len(open_) == 1 else f"{len(open_)} open findings"
        return "attention", f"Power hog detective: {head}. " + " ".join(texts[:MAX_ITEMS]) + _more(texts, MAX_ITEMS)
    learning = [k for k, v in (d.get("lernfortschritt") or {}).items()
                if isinstance(v, str) and "/" in v and v.split("/")[0] != v.split("/")[1]]
    tail = f" Still learning: {len(learning)} device{'s' if len(learning) != 1 else ''}." if learning else ""
    return "ok", "Nothing unusual about the power use." + tail


# ── Zigbee placement advisor ─────────────────────────────────────
def _zigbee(d, brief=False):
    rec = d.get("recommendation") or {}
    text = [t for t in d.get("text") or [] if t]
    ch = d.get("channel") or {}
    health = d.get("health") or {}
    severity = "ok"
    if rec.get("status") == "insufficient_data":
        lines = ["Not enough data yet for a recommendation.", d.get("basis") or ""]
    else:
        lines = text[:MAX_ITEMS] or ["No recommendation."]
        if rec.get("candidates") or rec.get("weak"):
            severity = "attention"
    if ch.get("tone") not in (None, "", "ok"):
        severity = "attention"
        lines.append(ch.get("headline") or ch.get("text") or "")
    if health and health.get("ok") is False:
        severity = "attention"
        lines.append(health.get("text") or "The placement advisor gets no data from Home Assistant.")
    return severity, " ".join(x.strip() for x in lines if x and x.strip())


def _fix(text):
    """Akku-Doktor 1.1.0 sends some of its German texts double-encoded ("verlÃ¤sslich"); undo that, else keep as is."""
    text = str(text or "").strip()
    if "Ã" not in text and "Â" not in text and "â€" not in text:
        return text
    for enc in ("cp1252", "latin-1"):
        try:
            return text.encode(enc).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return text


# ── Battery doctor ───────────────────────────────────────────────
def _battery(d, brief=False):
    system = (d.get("storages") or {}).get("system") or {}
    cap, war = system.get("capacity") or {}, system.get("warranty") or {}
    severity, parts = "ok", []
    if d.get("ha_connected") is False:
        severity = "attention"
        parts.append("The battery doctor gets no data from Home Assistant right now.")
    alerts = [a for a in d.get("alerts") or [] if a]
    for a in alerts[:MAX_ITEMS]:
        severity = "attention"
        text = (a.get("text") or a.get("message") or a.get("title") or "") if isinstance(a, dict) else a
        parts.append(_fix(text).rstrip(".") + ".")
    if alerts:
        parts[-1] += _more(alerts, MAX_ITEMS)
    if war.get("status") not in (None, "", "ok"):
        severity = "attention"
        notes = " ".join(_fix(n) for n in war.get("notes") or [] if n)
        parts.append("Something about the battery's warranty needs a look." + (f" {notes}" if notes else ""))
    if brief:
        return severity, " ".join(parts)
    soh, kwh = cap.get("soh"), cap.get("cap_kwh")
    if cap.get("status") == "ok" and soh is not None and kwh is not None:
        line = f"The battery still has about {soh:.0f} percent of its capacity, around {kwh:.1f} kilowatt hours usable"
        if cap.get("lo") is not None and cap.get("hi") is not None:
            line += f" ({cap['lo']:.1f} to {cap['hi']:.1f})"
        parts.insert(0, line + ".")
    else:
        parts.insert(0, "The data is not enough yet for a reliable estimate of the capacity.")
    cycles = (d.get("cycles") or {}).get("system")
    if cycles:
        end = str(war.get("end") or "")[:4]
        parts.append(f"{cycles:.0f} full cycles so far" + (f", warranty until {end}." if end else "."))
    return severity, " ".join(parts)


# ── Automation checker ───────────────────────────────────────────
def _ftitle(f):
    return str(f.get("title") or f.get("item_label") or f.get("item") or "").strip().rstrip(".")


def _automations(d, brief=False):
    findings = d.get("findings") or []
    crit = [f for f in findings if f.get("severity") == "critical"]
    warn = [f for f in findings if f.get("severity") == "warning"]
    last = d.get("last_check") or 0
    age_h = (time.time() - last) / 3600 if last else None
    severity, parts = "ok", []
    if d.get("status") not in (None, "", "ok") or d.get("trusted") is False:
        severity = "attention"
        detail = str(d.get("detail") or "").strip().rstrip(".")
        parts.append("The automation checker cannot read Home Assistant properly right now" + (f": {detail}." if detail else "."))
    elif age_h is not None and age_h > CHECKER_STALE_H:
        severity = "attention"
        parts.append(f"The automation checker has not checked for {age_h:.0f} hours.")
    if crit:
        severity = "attention"
        head = "One critical finding" if len(crit) == 1 else f"{len(crit)} critical findings"
        parts.append(f"{head} in the automations: " + "; ".join(_ftitle(f) for f in crit[:MAX_ITEMS]) + "."
                     + _more(crit, MAX_ITEMS))
    if warn and not brief:                 # warnings stay out of the morning briefing; the checker pushes criticals itself
        severity = "attention"
        head = "One warning" if len(warn) == 1 else f"{len(warn)} warnings"
        parts.append(f"{head}: " + "; ".join(_ftitle(f) for f in warn[:MAX_ITEMS]) + "." + _more(warn, MAX_ITEMS))
    if parts:
        return severity, " ".join(parts)
    info = sum(1 for f in findings if f.get("severity") == "info")
    tail = f" Plus {info} tidiness note{'s' if info != 1 else ''}." if info and not brief else ""
    return "ok", "The automations are fine." + tail


# ── Homelab drift ────────────────────────────────────────────────
def _drift(m, brief=False):
    if not m:
        raise ValueError("no metrics")

    def val(name, **labels):
        return m.get((name, tuple(sorted(labels.items()))))

    severity, parts = "ok", []
    last = val("homelab_drift_last_success_timestamp_seconds")
    age_h = (time.time() - last) / 3600 if last else None
    if val("homelab_drift_scan_ok") == 0:
        severity = "attention"
        parts.append("The last scan of Homelab drift failed.")
    elif age_h is not None and age_h > DRIFT_STALE_H:
        severity = "attention"
        parts.append(f"Homelab drift has not scanned for {age_h:.0f} hours.")
    down = sorted(dict(labels).get("host", "?") for (name, labels), v in m.items()
                  if name == "homelab_drift_host_reachable" and v == 0)
    if down:
        severity = "attention"
        parts.append(f"Homelab drift cannot reach {_join(down)}.")
    instant = int(val("homelab_drift_findings", severity="instant", status="open") or 0)
    digest = int(val("homelab_drift_findings", severity="digest", status="open") or 0)
    if instant:
        severity = "attention"
        parts.append("One important change in the homelab is still open." if instant == 1
                     else f"{instant} important changes in the homelab are still open.")
    if digest and not brief:
        parts.append("One smaller change is waiting for the weekly report." if digest == 1
                     else f"{digest} smaller changes are waiting for the weekly report.")
    if not parts:
        return "ok", "Nothing unexpected has changed in the homelab."
    if severity == "ok":
        parts.insert(0, "Nothing urgent in the homelab.")
    return severity, " ".join(parts)


_READERS = {"backups": _backups, "updates": _updates, "power": _power, "zigbee": _zigbee,
            "battery": _battery, "automations": _automations, "drift": _drift}


def report(topic, brief=False):
    name = SOURCES[topic][0]
    try:
        data = fetch(topic)
    except Exception:  # noqa: BLE001 — a tool that is down is a finding of its own, not a crash
        return "unknown", f"The {name} cannot be reached right now."
    try:
        return _READERS[topic](data, brief)
    except Exception:  # noqa: BLE001 — format changed under us
        return "unknown", f"I cannot read the report of the {name} right now."


def spoken(topic):
    if topic != "all":
        return report(topic)[1]
    results = [report(t) for t in SOURCES]
    bad = [text for sev, text in results if sev != "ok"]
    if not bad:
        return ("Everything on the Pi is fine: backups, updates, power, Zigbee, battery, automations "
                "and homelab without findings.")
    return " ".join(bad)


def describe(topic):
    topics = list(SOURCES) if topic in ("", None, "all") else [topic]
    out = []
    for t in topics:
        sev, text = report(t)
        out.append(f"[{SOURCES[t][0]} · {sev}] {text}")
    return "\n".join(out)


def briefing_line():
    bad = []
    for t in SOURCES:
        sev, text = report(t, brief=True)
        if sev == "attention":
            bad.append(text)
    return ("Homelab tools on the Pi: " + " ".join(bad)) if bad else ""
