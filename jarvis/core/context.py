"""
Context engine: one place that gathers what Jarvis knows right now — each piece
with its age, source and how far it can be trusted.

Sources (all optional; a missing integration is reported as "not available"):
  time       clock and date                                   local
  calendar   today's appointments (iCloud), work only counted service (invitations: third-party)
  weather    open-meteo                                       service
  timers     timers, alarms, reminders                        local
  plans      open plan steps due today                        local
  warnings   active homelab incidents, emergency stop, GPU budget, open proposals   local
  pc         CPU, RAM, VRAM                                    local
  house      Home Assistant summary (solar, battery)           service

Every source runs with a time limit (JARVIS_CONTEXT_TIMEOUT, 4 s) in parallel and
is cached for its own TTL in memory only — nothing is written to disk. When a
source is slow or down, the last good value is shown as "stale (as of …)".

day_overview() answers "What does my day look like?" without any LLM. block() gives
the LLM path a short "situation" (only warnings and stale markers) so it does not ask
the same services again.
"""
import concurrent.futures as cf
import datetime as dt
import os
import threading
import time

TIMEOUT = float(os.environ.get("JARVIS_CONTEXT_TIMEOUT", "4"))
_CACHE = {}                      # name -> {"data", "at", "error"}
_LOCK = threading.Lock()
_POOL = cf.ThreadPoolExecutor(max_workers=6, thread_name_prefix="context")


# ── sources: fn() -> data (any), raising on failure ─────────

def _time():
    now = dt.datetime.now()
    day = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")[now.weekday()]
    return {"now": now, "text": f"{day}, {now:%Y-%m-%d, %H:%M}"}


def _calendar():
    import icloud_calendar as cal
    import privacy
    if not cal.configured():
        raise LookupError("calendar not set up")
    evts = cal.events(dt.date.today(), 1)
    private = [e for e in evts if not privacy.is_work(e.get("title", ""), e.get("location", ""))]
    return {"events": private, "work": len(evts) - len(private)}


def _weather():
    import dashboard
    w = dashboard._weather()
    if not isinstance(w, dict) or w.get("error"):
        raise RuntimeError((w or {}).get("error", "no weather"))
    return w


def _timers():
    import scheduler
    now = time.time()
    end = time.mktime((dt.date.today() + dt.timedelta(days=1)).timetuple())
    return [i for i in scheduler.items() if now - 60 <= i["at"] < end]


def _plans():
    import re
    import memory
    today = dt.date.today().isoformat()
    folder = memory.MEMORY_DIR / "Projects"
    due = []
    for path in sorted(folder.glob("*.md")) if folder.exists() else []:
        for d, t in re.findall(r"(?m)^- \[ \] (\d{4}-\d{2}-\d{2}): (.+)$", path.read_text(encoding="utf-8")):
            if d <= today:
                due.append((path.stem, t, d < today))
    return due


def _warnings():
    import commands
    import homelab_watch
    import proposals
    out = []
    state = commands.load()
    for key, s in (state.get("homelab") or {}).items():
        if s.get("bad", 0) >= homelab_watch.CONFIRM:
            mins = max(1, round((time.time() - (s.get("since") or time.time())) / 60))
            out.append({"severity": "warn", "text": f"{key} disturbed for {mins} min.", "key": key})
    if (state.get("paused_until") or 0) > time.time():
        out.append({"severity": "info", "text": "emergency stop active until "
                    + time.strftime("%H:%M", time.localtime(state["paused_until"]))})
    try:
        import resources
        rs = resources.status()
        if rs.get("shed"):
            out.append({"severity": "info", "text": "graphics memory tight, given back: " + ", ".join(rs["shed"])})
    except Exception:  # noqa: BLE001
        pass
    n = len(proposals.pending())
    if n:
        out.append({"severity": "info", "text": f"{n} proposal(s) waiting for you"})
    try:
        import focus
        if focus.active():
            until = time.strftime("%H:%M", time.localtime(focus.state()["until"]))
            out.append({"severity": "info", "text": f"focus session until {until}: notices are held back"})
    except Exception:  # noqa: BLE001
        pass
    return out


def _pc():
    import infra
    return infra.pc_metrics()


def _house():
    import dashboard
    h = dashboard._house()
    if not isinstance(h, dict) or h.get("error"):
        raise RuntimeError((h or {}).get("error", "Home Assistant not reachable"))
    return h


# name: (fn, ttl seconds, trust)
SOURCES = {
    "time": (_time, 0, "local"),
    "calendar": (_calendar, 300, "third-party"),  # titles of invitations are written by others
    "weather": (_weather, 1800, "service"),
    "timers": (_timers, 0, "local"),
    "plans": (_plans, 300, "local"),
    "warnings": (_warnings, 0, "local"),
    "pc": (_pc, 20, "local"),
    "house": (_house, 60, "service"),
}


def get(names=None, timeout=None):
    """{name: item} with item = {data, at, age_s, ok, stale, trust, error}. Never raises."""
    names = [n for n in (names or SOURCES) if n in SOURCES]
    timeout = TIMEOUT if timeout is None else timeout
    now = time.time()
    out, todo = {}, {}
    with _LOCK:
        for n in names:
            fn, ttl, trust = SOURCES[n]
            c = _CACHE.get(n)
            if c and c.get("data") is not None and ttl and now - c["at"] < ttl:
                out[n] = dict(data=c["data"], at=c["at"], ok=True, stale=False, trust=trust, error=None)
            else:
                todo[n] = _POOL.submit(fn)
    deadline = now + timeout
    for n, fut in todo.items():
        fn, ttl, trust = SOURCES[n]
        try:
            data = fut.result(timeout=max(0.0, deadline - time.time()))
            with _LOCK:
                _CACHE[n] = {"data": data, "at": time.time()}
            out[n] = dict(data=data, at=time.time(), ok=True, stale=False, trust=trust, error=None)
        except Exception as e:  # noqa: BLE001 — timeout or source failure
            err = "time limit" if isinstance(e, cf.TimeoutError) else str(e)[:120]
            c = _CACHE.get(n)
            if c and c.get("data") is not None:
                out[n] = dict(data=c["data"], at=c["at"], ok=True, stale=True, trust=trust, error=err)
            else:
                out[n] = dict(data=None, at=None, ok=False, stale=False, trust=trust, error=err)
    for item in out.values():
        item["age_s"] = round(time.time() - item["at"]) if item["at"] else None
    return out


def _as_of(item):
    return time.strftime("%H:%M", time.localtime(item["at"])) if item.get("at") else "?"


# ── local answer: "What does my day look like?" ─────────────────

def day_overview(ctx=None):
    ctx = ctx or get(["time", "calendar", "weather", "timers", "plans", "warnings"])
    now = ctx["time"]["data"]["now"]
    parts = []
    w = ctx.get("weather") or {}
    if w.get("ok"):
        d = w["data"]
        parts.append(f"Weather: {d.get('sky', '')}, {round(d['tmin'])} to {round(d['tmax'])} degrees, "
                     f"rain {d.get('rain', 0)} percent" + (" (stale)" if w["stale"] else "") + ".")
    k = ctx.get("calendar") or {}
    if k.get("ok"):
        evts = [e for e in k["data"]["events"] if (e.get("end") or e["start"]) >= now or e.get("allday")]
        if evts:
            rows = []
            for e in evts[:5]:
                when = "all day" if e.get("allday") else e["start"].strftime("%H:%M")
                row = f"{when} {e.get('title', 'appointment')}"
                if e.get("location") and not e.get("allday"):
                    try:
                        import commute
                        dest = commute.geocode(e["location"])
                        mins = commute.drive_minutes(dest) if dest else None
                        if mins:
                            row += f", leave at {commute.leave_at(e['start'], mins).strftime('%H:%M')}"
                    except Exception:  # noqa: BLE001
                        pass
                rows.append(row)
            parts.append("Appointments: " + "; ".join(rows) + ".")
        else:
            parts.append("No more private appointments today.")
        if k["data"]["work"]:
            parts.append(f"Plus {k['data']['work']} work appointment(s).")
    else:
        parts.append("Calendar: not available.")
    t = ctx.get("timers") or {}
    if t.get("ok") and t["data"]:
        parts.append("Reminders: " + "; ".join(
            f"{time.strftime('%H:%M', time.localtime(i['at']))} {i.get('text') or i['kind']}" for i in t["data"][:4]) + ".")
    p = ctx.get("plans") or {}
    if p.get("ok") and p["data"]:
        parts.append("Due from your plans: " + "; ".join(
            f"{t}" + (" (overdue)" if late else "") for _, t, late in p["data"][:4]) + ".")
    wa = ctx.get("warnings") or {}
    if wa.get("ok") and wa["data"]:
        parts.append("Notes: " + "; ".join(x["text"] for x in wa["data"][:4]) + ".")
    return " ".join(parts) or "Nothing on for today, Sir."


_DAY = __import__("re").compile(r"\b(what does (my|the) day look like|hows (my|the) day looking|how does today look|"
                                r"day overview|overview (for|of) today|whats on for today)\b")


def handle(n):
    """Local router hook."""
    if not _DAY.search(n):
        return None
    return day_overview()


# ── LLM path: a short "situation" ─────────────────────────────────

def block(message="", groups=()):
    """Only what the model should not have to look up again: warnings and stale sources. '' if nothing."""
    ctx = get(["warnings"], timeout=1.5)
    lines = [x["text"] for x in (ctx["warnings"]["data"] or [])] if ctx["warnings"]["ok"] else []
    return ("Situation (local, current): " + "; ".join(lines)) if lines else ""


def describe(names=None):
    """Tool text: every source with age, trust and state."""
    rows = []
    for n, item in get(names).items():
        if not item["ok"]:
            rows.append(f"- {n}: not available ({item['error']})")
            continue
        data = item["data"]
        text = data.get("text") if isinstance(data, dict) and "text" in data else str(data)[:300]
        flag = f"stale, as of {_as_of(item)}" if item["stale"] else f"{item['age_s']} s ago"
        rows.append(f"- {n} ({item['trust']}, {flag}): {text}")
    return "\n".join(rows)
