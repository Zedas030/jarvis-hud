"""
Metrics for every module, exposed in Prometheus text format at GET /metrics (Grafana on the Pi).

  counter(name, n=1, **labels)        monotonic count, e.g. counter("turns_total", route="local")
  gauge(name, value, **labels)        last value, e.g. gauge("pool_processes", 3)
  observe(name, value, **labels)      histogram with buckets, e.g. observe("turn_ms", 812, route="llm")
  prometheus()                        the exposition text; names get the prefix "jarvis_"
  snapshot()                          the same figures as a dict (for /api/status and tests)

The voice engine keeps its own registry (voice_metrics.py, written before this one) and the
server appends its text. Everything here is standard library: a few dicts behind one lock, so
a metric costs microseconds and never raises into the caller — a failing metric must not break
a turn. Label values are shortened and quoted; names are not validated beyond [a-z0-9_].

What the server records (see the call sites):
  jarvis_turns_total{route}                 a finished exchange; route = local | llm | fallback | cached | briefing
  jarvis_turn_ms{route}                     end-to-end duration from the request to the last word
  jarvis_tokens_total{model,kind}           input | output | cache_read | cache_write, from the API's usage
  jarvis_model_calls_total{model,route}     one per model answer (background jobs: route="background")
  jarvis_cards_total{outcome}               confirmation cards: allowed | denied | timeout | nobody
  jarvis_fallback_cooldowns_total{provider,why}  a stand-in provider was put on hold (limit, error, unreachable)
  jarvis_job_runs_total{job} / jarvis_job_errors_total{job} / jarvis_job_ms{job}   background.loop
  jarvis_http_requests_total{path,status}   every API call (static files and the HUD as path="static")
  jarvis_log_events_total{level,logger}     warnings and errors as they are logged (jarvis_log.py)
  jarvis_tool_calls_total{cls,ok}           tool calls of today from logs/audit.jsonl (the MCP server
                                            is its own process per request, so the server counts the log)
"""
import json
import threading
import time

PREFIX = "jarvis_"
BUCKETS_MS = (50, 100, 200, 400, 800, 1200, 2000, 4000, 8000, 15000, 30000)
_LOCK = threading.Lock()
_COUNTERS = {}      # (name, labels) -> float
_GAUGES = {}        # (name, labels) -> float
_HIST = {}          # (name, labels) -> {"buckets": {le: n}, "sum": x, "count": n}
_HELP = {}          # name -> help text
STARTED = time.time()


def _labels(labels):
    return tuple(sorted((k, str(v)[:60]) for k, v in labels.items() if v is not None))


def _labelstr(labels):
    if not labels:
        return ""
    return "{" + ",".join(f'{k}="{_escape(v)}"' for k, v in labels) + "}"


def _escape(v):
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def describe(name, text):
    """A HELP line for the exposition (optional; counters work without it)."""
    _HELP[name] = text


def counter(name, n=1, **labels):
    try:
        with _LOCK:
            key = (name, _labels(labels))
            _COUNTERS[key] = _COUNTERS.get(key, 0) + n
    except Exception:  # noqa: BLE001 — a metric never breaks the caller
        pass


def gauge(name, value, **labels):
    try:
        with _LOCK:
            _GAUGES[(name, _labels(labels))] = float(value)
    except Exception:  # noqa: BLE001
        pass


def observe(name, value, buckets=BUCKETS_MS, **labels):
    if value is None:
        return
    try:
        value = float(value)
        with _LOCK:
            h = _HIST.setdefault((name, _labels(labels)), {"buckets": {b: 0 for b in buckets}, "sum": 0.0, "count": 0})
            for b in h["buckets"]:
                if value <= b:
                    h["buckets"][b] += 1
            h["sum"] += value
            h["count"] += 1
    except Exception:  # noqa: BLE001
        pass


def timed(name, **labels):
    """Context manager: observe(name, elapsed ms) on exit, whatever happens inside."""
    return _Timer(name, labels)


class _Timer:
    def __init__(self, name, labels):
        self.name, self.labels, self.t0 = name, labels, 0.0

    def __enter__(self):
        self.t0 = time.time()
        return self

    def __exit__(self, *exc):
        observe(self.name, (time.time() - self.t0) * 1000, **self.labels)
        return False


def snapshot():
    with _LOCK:
        return {
            "counters": {f"{n}{_labelstr(l)}": v for (n, l), v in sorted(_COUNTERS.items())},
            "gauges": {f"{n}{_labelstr(l)}": v for (n, l), v in sorted(_GAUGES.items())},
            "histograms": {f"{n}{_labelstr(l)}": {"count": h["count"], "sum": round(h["sum"], 1),
                                                   "avg": round(h["sum"] / h["count"], 1) if h["count"] else 0}
                           for (n, l), h in sorted(_HIST.items())},
            "uptime_s": int(time.time() - STARTED),
        }


def prometheus():
    """Text exposition (version 0.0.4). Counters end in _total by their names; histograms get
    _bucket/_sum/_count."""
    out = []
    with _LOCK:
        names = sorted({n for n, _ in _COUNTERS} | {n for n, _ in _GAUGES} | {n for n, _ in _HIST})
        for name in names:
            full = PREFIX + name
            kind = "histogram" if any(n == name for n, _ in _HIST) else "gauge" if any(n == name for n, _ in _GAUGES) else "counter"
            if name in _HELP:
                out.append(f"# HELP {full} {_HELP[name]}")
            out.append(f"# TYPE {full} {kind}")
            for (n, l), v in sorted(_COUNTERS.items()):
                if n == name:
                    out.append(f"{full}{_labelstr(l)} {_num(v)}")
            for (n, l), v in sorted(_GAUGES.items()):
                if n == name:
                    out.append(f"{full}{_labelstr(l)} {_num(v)}")
            for (n, l), h in sorted(_HIST.items()):
                if n != name:
                    continue
                for b, c in h["buckets"].items():
                    out.append(f"{full}_bucket{_labelstr(l + (('le', _num(b)),))} {c}")
                out.append(f"{full}_bucket{_labelstr(l + (('le', '+Inf'),))} {h['count']}")
                out.append(f"{full}_sum{_labelstr(l)} {_num(h['sum'])}")
                out.append(f"{full}_count{_labelstr(l)} {h['count']}")
        out.append(f"# TYPE {PREFIX}uptime_seconds gauge")
        out.append(f"{PREFIX}uptime_seconds {int(time.time() - STARTED)}")
    return "\n".join(out) + "\n"


def _num(v):
    v = float(v)
    return str(int(v)) if v.is_integer() else f"{v:.3f}".rstrip("0").rstrip(".")


def tool_calls_from_audit(path, day=None):
    """jarvis_tool_calls_total{cls,ok} for one day, counted from the audit log at scrape time: the
    MCP server runs as its own process per request, so its calls never reach this registry directly.
    Returns the exposition lines (or none if the log is unreadable)."""
    day = day or time.strftime("%Y-%m-%d")
    counts = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.startswith('{"at": "' + day):
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("kind") != "tool":
                    continue
                key = (str(row.get("cls") or "?"), "true" if row.get("ok", True) else "false")
                counts[key] = counts.get(key, 0) + 1
    except OSError:
        return ""
    if not counts:
        return ""
    lines = [f"# HELP {PREFIX}tool_calls_total tool calls of today from logs/audit.jsonl",
             f"# TYPE {PREFIX}tool_calls_total counter"]
    for (cls, ok), n in sorted(counts.items()):
        lines.append(f'{PREFIX}tool_calls_total{{cls="{cls}",ok="{ok}"}} {n}')
    return "\n".join(lines) + "\n"


def reset():
    with _LOCK:
        _COUNTERS.clear()
        _GAUGES.clear()
        _HIST.clear()
