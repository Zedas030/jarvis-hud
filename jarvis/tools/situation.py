"""
Homelab situation report: one spoken summary from what already watches the
house on the Pi — all read-only, 0 tokens.

  Uptime Kuma    a status page (KUMA_URL, default http://<pi>:3001; JARVIS_KUMA_SLUG, default
                 "jarvis") — Kuma 1.x has no read API without a key, a public status page has
                 one. Which monitors are down, and since when.
  ORBIT          the security centre on the Pi (JARVIS_ORBIT_URL, default http://<pi>:8092):
                 score, open findings by severity, age of the last scan.
  energy loop    Home Assistant: the entities and automations the zero-feed-in loop needs
                 (config/homelab.json → "energy"; nothing configured = not checked).

  report(topic)  the spoken status report: all | kuma | security | energy
  findings(cfg)  incidents for homelab_watch, in the shape of its own checks
  open_findings()  the open findings with their ids, what to do, how long it takes
  accept(id, reason, days)  a finding as a reasoned exception (the security centre's own API;
                 the tool is CONFIRM, so always a card) — 30, 90 or 365 days
  run_scan()     starts a quick scan (read-only on the Pi, about 10 s)

Device names in the security findings come from the network (DHCP names), so the tool's
result is treated as untrusted (policy.UNTRUSTED).
"""
import datetime as dt
import json
import os
import urllib.error
import urllib.request

_PI = os.environ.get("JARVIS_PI_HOST", "").strip()
KUMA = os.environ.get("KUMA_URL", "").strip().rstrip("/") or (f"http://{_PI}:3001" if _PI else "")
KUMA_SLUG = os.environ.get("JARVIS_KUMA_SLUG", "jarvis").strip()
ORBIT = os.environ.get("JARVIS_ORBIT_URL", "").strip().rstrip("/") or (f"http://{_PI}:8092" if _PI else "")
SCAN_STALE_H = float(os.environ.get("JARVIS_ORBIT_STALE_HOURS", "2"))   # the quick scan runs every 15 min
SEVERITIES = ("high", "medium", "low")
# the security centre's API may answer in German (hoch, mittel, niedrig): read both
_SEV = {"hoch": "high", "mittel": "medium", "niedrig": "low"}


def _sev(finding):
    s = str((finding or {}).get("sev") or "").lower()
    return _SEV.get(s, s)


class NotSetUp(Exception):
    """The source is not configured (no URL, no status page) — say so, never count it as an outage."""


def _get(url, timeout=8):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _minutes(since, now=None):
    return max(1, round(((now or _now()) - since).total_seconds() / 60)) if since else None


def _ago(minutes):
    if minutes is None:
        return ""
    if minutes < 90:
        return f"for {minutes} minutes"
    if minutes < 48 * 60:
        return f"for {round(minutes / 60)} hours"
    return f"for {round(minutes / 1440)} days"


# ── Uptime Kuma ──────────────────────────────────────────────

def _kuma_time(s):
    # heartbeat times are UTC without a zone: "2026-10-28 11:47:50.123"
    try:
        return dt.datetime.fromisoformat(str(s).replace(" ", "T")[:19]).replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def parse_kuma(page, beats, now=None):
    """[{name, up, since}] from the status page and its heartbeat list. Status 0 = down, 3 = maintenance."""
    out = []
    lists = (beats or {}).get("heartbeatList") or {}
    for group in (page or {}).get("publicGroupList") or []:
        for mon in group.get("monitorList") or []:
            hb = sorted(lists.get(str(mon.get("id"))) or [], key=lambda b: str(b.get("time")))
            if not hb:
                continue
            up = hb[-1].get("status") != 0
            since = None
            if not up:                                  # the first beat of the current down streak
                for b in reversed(hb):
                    if b.get("status") != 0:
                        break
                    since = _kuma_time(b.get("time"))
            out.append(dict(name=mon.get("name") or f"Monitor {mon.get('id')}", up=up,
                            minutes=_minutes(since, now), msg=str(hb[-1].get("msg") or "")[:80]))
    return out


def kuma():
    if not (KUMA and KUMA_SLUG):
        raise NotSetUp("Uptime Kuma not set up (KUMA_URL or JARVIS_PI_HOST in .env)")
    missing = NotSetUp(f"Uptime Kuma has no status page “{KUMA_SLUG}” with the services")
    try:
        # heartbeats first: for an unknown slug they come back empty at once, while Kuma 1.23
        # never answers the page request itself
        beats = _get(f"{KUMA}/api/status-page/heartbeat/{KUMA_SLUG}")
        if not (beats or {}).get("heartbeatList"):
            raise missing
        page = _get(f"{KUMA}/api/status-page/{KUMA_SLUG}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise missing from e
        raise
    return parse_kuma(page, beats)


def say_kuma(monitors):
    if not monitors:
        return "Uptime Kuma: the status page shows no services."
    down = [m for m in monitors if not m["up"]]
    if not down:
        return f"Uptime Kuma: all {len(monitors)} services reachable."
    names = ", ".join(f"{m['name']} {_ago(m['minutes'])}".strip() for m in down)
    return f"Uptime Kuma: {len(down)} of {len(monitors)} services down — {names}."


# ── ORBIT security centre ────────────────────────────────────

def security():
    if not ORBIT:
        raise NotSetUp("Security centre not set up (JARVIS_ORBIT_URL or JARVIS_PI_HOST in .env)")
    try:
        return _get(f"{ORBIT}/api/security", timeout=10)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise NotSetUp("There is no security centre on the Pi") from e
        raise


def scan_age_minutes(rep, now=None):
    try:
        return _minutes(dt.datetime.fromisoformat(rep["generated"]), now)
    except (KeyError, TypeError, ValueError):
        return None


def say_security(rep, now=None, detail=False):
    counts = {_SEV.get(k, k): v for k, v in (rep.get("counts") or {}).items()}
    open_ = [f for f in rep.get("findings") or [] if _sev(f) in SEVERITIES]
    parts = [f"Security: {rep.get('score', '?')} of 100 points"]
    if not open_:
        parts.append("no open findings")
    else:
        high = counts.get("high", 0)
        parts.append("nothing urgent" if not high else f"{high} {'urgent finding' if high == 1 else 'urgent findings'}")
        rest = [f"{counts.get(s, 0)} {s}" for s in ("medium", "low") if counts.get(s)]
        if rest:
            parts.append(" and ".join(rest))
    text = ", ".join(parts) + "."
    shown = sorted(open_, key=lambda f: SEVERITIES.index(_sev(f)))[: (6 if detail else 2)]
    shown = [f for f in shown if detail or _sev(f) != "low"]
    if shown:
        text += " " + " ".join(f"{_sev(f).capitalize()}: {f.get('title', '')}." for f in shown)
    age = scan_age_minutes(rep, now)
    if age is not None and age > SCAN_STALE_H * 60:
        text += f" Note: the last check is {_ago(age).replace('for ', '')} old."
    return text


# ── energy loop (Home Assistant) ─────────────────────────────

def _states():
    import briefing
    return {s["entity_id"]: s for s in briefing._ha_states()}


def _label(spec, eid, states):
    if isinstance(spec, dict) and spec.get(eid):
        return spec[eid]
    return ((states.get(eid) or {}).get("attributes") or {}).get("friendly_name") or eid


def energy_problems(cfg, states, now=None):
    """[(key, text)] for every part of the loop that is not working; [] = all fine.

    cfg: {"entities": {id: label} | [id], "online": {binary_sensor: label},
          "automations": {automation: max seconds since it last ran, 0 = only switched on}}"""
    now = now or _now()
    out = []
    ents = cfg.get("entities") or {}
    missing = [_label(ents, e, states) for e in ents
               if (states.get(e) or {}).get("state", "unavailable") in ("unavailable", "unknown", "none", "")]
    if missing:
        out.append(("energy:data", "The energy loop is missing values: " + ", ".join(missing) + "."))
    online = cfg.get("online") or {}
    for e in online:
        if (states.get(e) or {}).get("state") != "on":
            out.append((f"energy:{e}", f"{_label(online, e, states)} has no connection."))
    for e, max_s in (cfg.get("automations") or {}).items():
        s = states.get(e) or {}
        name = _label({}, e, states)
        if s.get("state") != "on":
            out.append((f"energy:{e}", f"The automation “{name}” is {'off' if s.get('state') == 'off' else 'not available'}."))
            continue
        try:
            last = dt.datetime.fromisoformat((s.get("attributes") or {}).get("last_triggered") or "")
        except ValueError:
            last = None
        if max_s and (last is None or (now - last).total_seconds() > max_s):
            out.append((f"energy:{e}", f"The automation “{name}” has not run {_ago(_minutes(last, now)) or 'ever'}."))
    return out


def say_energy(problems):
    if not problems:
        return "Energy loop: running."
    return "Energy loop: " + " ".join(t for _, t in problems)


# ── report ───────────────────────────────────────────────────

def _cfg():
    import homelab_watch
    return homelab_watch.config()


_TOPICS = {"alles": "all", "sicherheit": "security", "energie": "energy"}


def report(topic="all", detail=False):
    topic = (topic or "all").lower()
    topic = _TOPICS.get(topic, topic)
    cfg = _cfg()
    parts = []
    if topic in ("all", "kuma"):
        try:
            parts.append(say_kuma(kuma()))
        except NotSetUp as e:
            parts.append(f"{e}.")
        except Exception as e:  # noqa: BLE001 — one dead source must not silence the rest
            parts.append(f"Uptime Kuma does not answer ({str(e)[:60]}).")
    if topic in ("all", "security"):
        try:
            parts.append(say_security(security(), detail=detail))
        except NotSetUp as e:
            parts.append(f"{e}.")
        except Exception as e:  # noqa: BLE001
            parts.append(f"The security centre does not answer ({str(e)[:60]}).")
    if topic in ("all", "energy") and cfg.get("energy"):
        try:
            parts.append(say_energy(energy_problems(cfg["energy"], _states())))
        except Exception as e:  # noqa: BLE001
            parts.append(f"I cannot check the energy loop right now ({str(e)[:60]}).")
    elif topic == "energy":
        parts.append("Nothing is set up for the energy loop (config/homelab.json → energy).")
    if topic == "all":
        try:
            import homelab_watch
            last = next((h for h in homelab_watch.history(5) if h.get("state") == "bad"), None)
            if last:
                parts.append(f"Last disturbance on {last['at'][:10]} at {last['at'][11:]}: {last['text']}")
        except Exception:  # noqa: BLE001
            pass
    return " ".join(parts)


# ── working through findings ─────────────────────────────────

ACCEPT_DAYS = (30, 90, 365)


def _post(path, body):
    # the X-Orbit header is what the security centre's API requires for every change
    data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"{ORBIT}{path}", data=data, method="POST",
                                 headers={"X-Orbit": "1", "Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"null")


def open_findings():
    rep = security()
    rows = []
    for f in sorted(rep.get("findings") or [], key=lambda f: SEVERITIES.index(_sev(f)) if _sev(f) in SEVERITIES else 9):
        fix = " ".join((f.get("fix") or [])[:2])
        rows.append(f"[{f.get('id')}] {_sev(f)}: {f.get('title')}. Why: {f.get('why', '')} "
                    f"What to do: {fix} ({f.get('effort', '?')})")
    # the report keeps the exception under "accepted" inside each finding
    acc = [f"{a.get('title')} ({'until ' + until[:10] if until else 'no end date'})"
           for a in rep.get("accepted") or [] for until in [((a.get("accepted") or {}).get("until") or "")]]
    return (("Open findings:\n" + "\n".join(rows)) if rows else "No open findings.") + \
        ("\nAccepted as exception: " + "; ".join(acc) if acc else "")


def accept(fid, reason, days):
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 0
    if days not in ACCEPT_DAYS:
        return "An exception lasts 30, 90 or 365 days — without end date only on the website."
    reason = " ".join(str(reason or "").split())[:280]
    if len(reason) < 10:
        return "For an exception I need a reason in one sentence."
    if not any(f.get("id") == fid for f in security().get("findings") or []):
        return f"There is no open finding “{fid}”."
    res = _post("/api/security/accept", {"id": fid, "days": days, "reason": "Accepted via Jarvis: " + reason})
    if not (res or {}).get("ok"):
        return f"The security centre refused ({(res or {}).get('reason', '?')})."
    run_scan()
    return f"Entered: exception for {days} days. The check is running again right now and takes it into account."


def run_scan():
    try:
        _post("/api/security/run/quick", {})
    except urllib.error.HTTPError as e:
        return "A check is already running." if e.code == 409 else f"The check could not be started (HTTP {e.code})."
    return "The quick check is running, it will be done in about 15 seconds."


# ── findings for homelab_watch ───────────────────────────────

def _skipped(name, cfg):
    return any(s.lower() in name.lower() for s in cfg.get("kuma_skip") or [])


def kuma_findings(monitors, cfg):
    critical = cfg.get("kuma_critical") or []
    return [dict(key=f"kuma:{m['name']}", ok=m["up"],
                 severity="critical" if any(c.lower() in m["name"].lower() for c in critical) else "warn",
                 down=f"Uptime Kuma reports {m['name']} as down for about {{min}} minutes.",
                 up=f"{m['name']} is reachable again according to Uptime Kuma.")
            for m in monitors if not _skipped(m["name"], cfg)]


def security_findings(rep, cfg, known, now=None):
    """Open findings of the announced severities; titles seen before and now gone come back as ok."""
    sevs = {_SEV.get(s, s) for s in (cfg.get("security_announce") or ["high"])}
    found, keys = [], set()
    for f in rep.get("findings") or []:
        if _sev(f) in sevs and f.get("title"):
            key = "security:" + f["title"][:120]
            keys.add(key)
            found.append(dict(key=key, ok=False, severity="warn",
                              down=f"The security centre reports: {f['title']}.",
                              up=f"Resolved according to the security centre: {f['title']}."))
    for key in known:
        if key.startswith("security:") and key != "security:stale" and key not in keys:
            found.append(dict(key=key, ok=True, severity="warn", down="",
                              up=f"Resolved according to the security centre: {key[9:]}."))
    age = scan_age_minutes(rep, now)
    found.append(dict(key="security:stale", ok=age is None or age <= SCAN_STALE_H * 60, severity="warn",
                      down="The security centre on the Pi has not checked for more than "
                           f"{round(SCAN_STALE_H)} hours.",
                      up="The security centre is checking again."))
    return found


def energy_findings(ecfg, states, now=None):
    bad = dict(energy_problems(ecfg, states, now))
    keys = ["energy:data"] + [f"energy:{e}" for e in (ecfg.get("online") or {})] \
        + [f"energy:{e}" for e in (ecfg.get("automations") or {})]
    return [dict(key=k, ok=k not in bad, severity="warn", down=bad.get(k, ""),
                 up="The energy loop is running again." if k == "energy:data" else
                 "Everything in the energy loop is fine again.")
            for k in keys]


def findings(cfg):
    """All three sources; an unreachable or unconfigured source yields nothing (the Pi check covers that)."""
    import commands
    known = list((commands.load().get("homelab") or {}).keys())
    out = []
    try:
        out += kuma_findings(kuma(), cfg)
    except Exception:  # noqa: BLE001
        pass
    try:
        out += security_findings(security(), cfg, known)
    except Exception:  # noqa: BLE001
        pass
    if cfg.get("energy"):
        try:
            out += energy_findings(cfg["energy"], _states())
        except Exception:  # noqa: BLE001
            pass
    return out
