"""
Predictive monitoring on top of homelab_watch: history, forecasts, recurring faults.

Daily values (logs/metrics_daily.json, JARVIS_METRICS_DAYS = 180 kept):
  nas_used, pc_disk, pi_disk — percent used, one value per day (the day's last)

Forecast: a straight line through the last JARVIS_FORECAST_WINDOW (30) days. A
warning ("in about 40 days full") needs at least 7 values over at least 7 days,
a rising trend and fewer than JARVIS_FORECAST_WARN_DAYS (60) days left. Every
forecast states its data basis (days, values) and how well the line fits (R²) as
"confidence high/medium/low" — it is an estimate, not a promise.

Recurring faults: an incident key that went bad three times or more within seven
days is called recurring (homelab_watch adds "outage number n in 7 days").
"""
import datetime as dt
import json
import os
import pathlib
import statistics
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
PATH = pathlib.Path(os.environ.get("JARVIS_METRICS_DAILY", ROOT / "logs" / "metrics_daily.json"))
KEEP_DAYS = int(os.environ.get("JARVIS_METRICS_DAYS", "180"))
WINDOW = int(os.environ.get("JARVIS_FORECAST_WINDOW", "30"))
WARN_DAYS = int(os.environ.get("JARVIS_FORECAST_WARN_DAYS", "60"))
DAILY = ("nas_used", "pc_disk", "pi_disk")
LABEL = {"nas_used": "The NAS", "pc_disk": "The PC's system disk", "pi_disk": "The Pi's memory card"}


def load():
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record(metrics, day=None):
    """Keeps the day's last value of each daily metric (writes only when a value changed)."""
    day = (day or dt.date.today()).isoformat()
    data = load()
    changed = False
    for k in DAILY:
        if metrics.get(k) is not None and data.get(k, {}).get(day) != round(float(metrics[k]), 2):
            data.setdefault(k, {})[day] = round(float(metrics[k]), 2)
            changed = True
    if not changed:
        return
    cutoff = (dt.date.fromisoformat(day) - dt.timedelta(days=KEEP_DAYS)).isoformat()
    for k in data:
        data[k] = {d: v for d, v in data[k].items() if d >= cutoff}
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, PATH)


def forecast(key, today=None, data=None):
    """dict(days_left, slope_per_day, r2, n, span, current, confidence) or None without enough data."""
    today = today or dt.date.today()
    series = (data if data is not None else load()).get(key) or {}
    start = (today - dt.timedelta(days=WINDOW)).isoformat()
    pts = sorted((dt.date.fromisoformat(d), v) for d, v in series.items() if d >= start)
    if len(pts) < 7 or (pts[-1][0] - pts[0][0]).days < 7:
        return None
    xs = [(d - pts[0][0]).days for d, _ in pts]
    ys = [v for _, v in pts]
    try:
        slope, intercept = statistics.linear_regression(xs, ys)
    except statistics.StatisticsError:
        return None
    mean = statistics.mean(ys)
    ss_tot = sum((y - mean) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
    current = ys[-1]
    days_left = (100 - current) / slope if slope > 0.001 else None
    conf = "high" if r2 >= 0.8 and len(pts) >= 14 else "medium" if r2 >= 0.5 else "low"
    return dict(days_left=days_left, slope=slope, r2=r2, n=len(pts), span=(pts[-1][0] - pts[0][0]).days,
                current=current, confidence=conf)


def forecast_findings(today=None, data=None):
    """Findings in homelab_watch format for every metric heading to full within WARN_DAYS."""
    out = []
    for key in DAILY:
        f = forecast(key, today, data)
        if f is None:
            continue
        soon = f["days_left"] is not None and f["days_left"] < WARN_DAYS and f["confidence"] != "low"
        text = (f"{LABEL[key]} is {f['current']:.0f} percent used and would be full in about "
                f"{f['days_left']:.0f} days at the same pace" if f["days_left"] is not None else f"{LABEL[key]}: no increase")
        basis = f" (forecast, confidence {f['confidence']}, {f['n']} readings over {f['span']} days)."
        out.append(dict(key=f"forecast:{key}", ok=not soon, severity="warn", down=text + basis,
                        up=f"{LABEL[key]}: the forecast is relaxed again."))
    return out


def describe(today=None):
    rows = []
    for key in DAILY:
        f = forecast(key, today)
        if f is None:
            n = len(load().get(key) or {})
            rows.append(f"- {LABEL[key]}: not enough data yet ({n} days, 7 are needed)")
        elif f["days_left"] is None:
            rows.append(f"- {LABEL[key]}: {f['current']:.0f} %, no increase ({f['n']} values)")
        else:
            rows.append(f"- {LABEL[key]}: {f['current']:.0f} %, +{f['slope']:.2f} points a day, full in ~{f['days_left']:.0f} "
                        f"days (confidence {f['confidence']}, R² {f['r2']:.2f}, {f['n']} values)")
    return "\n".join(rows)


def recurring(key, now=None, days=7):
    """How often this incident key went bad within the last `days`."""
    import homelab_watch
    now = now or time.time()
    since = time.strftime("%Y-%m-%d %H:%M", time.localtime(now - days * 86400))
    until = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    return sum(1 for r in homelab_watch.history(500)
               if r["key"] == key and r["state"] == "bad" and since <= r["at"] <= until)
