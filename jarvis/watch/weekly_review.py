"""
Weekly review: sleep, steps, stress, Body Battery and golf rounds — with patterns.

Daily (from 9 h) the finished days are fetched from Garmin once and kept locally
in JARVIS_HEALTH_DATA (logs/health_daily.json; never in the repository). The user
can add what Garmin cannot know by voice — "I had dinner at 10 pm",
"had two beers" — through the tool health_log (log_entry below).

Sunday (from JARVIS_REVIEW_HOUR, default 18 h) the week is summed up and compared
with the week before. Patterns are computed in code, not guessed by the model:
days with a feature (late dinner, alcohol, golf, 10,000 steps, high stress) are
compared with days without it, judged by the following night (sleep score, deep
sleep, Body Battery the next morning). A pattern is only named with at least
three days on each side and a clear difference, always with the counts — it is
a hint, not proof. The model only phrases the result for speaking.

Note: Memories/Health/Weekly review <year>-W<nn>.
"""
import datetime as dt
import json
import os
import pathlib
import re
import statistics
import threading
import time

import background
import events
import memory
import jarvis_log

log = jarvis_log.get(__name__)

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
DATA = pathlib.Path(os.environ.get("JARVIS_HEALTH_DATA", ROOT / "logs" / "health_daily.json"))
REVIEW_HOUR = int(os.environ.get("JARVIS_REVIEW_HOUR", "18"))
LATE_MEAL = os.environ.get("JARVIS_LATE_MEAL", "21:00").strip()
BACKFILL_DAYS = int(os.environ.get("JARVIS_HEALTH_BACKFILL", "14"))
PATTERN_DAYS = int(os.environ.get("JARVIS_PATTERN_DAYS", "42"))
ENABLED = os.environ.get("JARVIS_REVIEW", "1").strip() not in ("0", "false", "no", "off")
_LOCK = threading.Lock()

SYSTEM = ("You are JARVIS. From health figures that are already computed you phrase a spoken "
          "weekly review: English, formal, dry and British, four to six short sentences, no Markdown, "
          "numbers as spoken. Only name numbers and patterns from the facts, invent nothing, no medical "
          "advice. Phrase patterns carefully (a tendency, not proof).")


# ── local store ──────────────────────────────────────────────

def load():
    try:
        return json.loads(DATA.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(d):
    DATA.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, DATA)


def _update(fn):
    with _LOCK:
        d = load()
        out = fn(d)
        _save(d)
        return out


_KINDS = [("meal", r"\b(ate|eaten|eat|dinner|supper|lunch|meal|snack|pizza|kebab|burger)"),
          ("alcohol", r"\b(alcohol|beers?|wine|prosecco|champagne|spirits|schnapps|cocktails?|drank|drinks?)"),
          ("caffeine", r"\b(coffee|espresso|cola|energy|caffeine)"),
          ("training", r"\b(training|workout|sport|ran|run|running|fitness|gym)")]


def log_entry(what, at="", now=None):
    """health_log: what + time ('22:10', 'yesterday 22:10', 'YYYY-MM-DD HH:MM'). After midnight until 5 h
    an entry counts for the evening before."""
    now = now or dt.datetime.now()
    text = " ".join((what or "").split())[:80]
    if not text:
        return "What should I note?"
    kind = next((k for k, pat in _KINDS if re.search(pat, text.lower())), "note")
    at = (at or "").strip().lower()
    day = now.date()
    if "yesterday" in at:
        day -= dt.timedelta(days=1)
    m = re.search(r"(\d{4}-\d{2}-\d{2})", at)
    if m:
        day = dt.date.fromisoformat(m.group(1))
    m = re.search(r"(\d{1,2})(?::|\.|\s*h\s*)?(\d{2})?", re.sub(r"\d{4}-\d{2}-\d{2}", "", at))
    hh, mm = (int(m.group(1)), int(m.group(2) or 0)) if m else (now.hour, now.minute)
    if not (0 <= hh < 24 and 0 <= mm < 60):
        return "I do not understand the time."
    if hh < 5:
        day -= dt.timedelta(days=1)
        clock = f"{hh + 24:02d}:{mm:02d}"     # 00:30 → "24:30" of the evening before, sorts after 23:00
    else:
        clock = f"{hh:02d}:{mm:02d}"
    _update(lambda d: d.setdefault(day.isoformat(), {}).setdefault("log", []).append(
        {"kind": kind, "what": text, "at": clock}))
    return f"Noted: {text}, {clock if hh >= 5 else f'{hh}:{mm:02d}'} ({day.isoformat()})."


# ── Garmin ───────────────────────────────────────────────────

def _garmin():
    import jarvis_mcp as j
    from garminconnect import Garmin
    if not (j.GARMIN_EMAIL and j.GARMIN_PASSWORD):
        raise RuntimeError("Garmin not connected")
    g = Garmin(j.GARMIN_EMAIL, j.GARMIN_PASSWORD, return_on_mfa=True)
    needs_mfa, _ = g.login(j.GARMIN_TOKENS)
    if needs_mfa:
        raise RuntimeError("Garmin asks for a two-factor login")
    os.makedirs(j.GARMIN_TOKENS, exist_ok=True)
    g.client.dump(j.GARMIN_TOKENS)
    return g


def _clock(ms):
    try:
        return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime("%H:%M")   # *Local stamps: no shift
    except (TypeError, ValueError, OSError):
        return None


def fetch_day(g, day):
    rec = {}
    s = (g.get_sleep_data(day) or {}).get("dailySleepDTO") or {}
    if s.get("sleepTimeSeconds"):
        rec.update(sleep_s=s["sleepTimeSeconds"], deep_s=s.get("deepSleepSeconds"), rem_s=s.get("remSleepSeconds"),
                   awake_s=s.get("awakeSleepSeconds"),
                   sleep_score=((s.get("sleepScores") or {}).get("overall") or {}).get("value"),
                   bed=_clock(s.get("sleepStartTimestampLocal")))
    st = g.get_stats(day) or {}
    rec.update(steps=st.get("totalSteps"), rhr=st.get("restingHeartRate"), stress=st.get("averageStressLevel"),
               bb_low=st.get("bodyBatteryLowestValue"), bb_high=st.get("bodyBatteryHighestValue"))
    rounds = [a for a in (g.get_activities_by_date(day, day) or [])
              if "golf" in str((a.get("activityType") or {}).get("typeKey", "")).lower()]
    rec["golf"] = {"rounds": len(rounds), "minutes": round(sum(a.get("duration") or 0 for a in rounds) / 60)}
    return {k: v for k, v in rec.items() if v is not None}


def fetch(today=None, g=None):
    """Fetches finished days that are missing (up to BACKFILL_DAYS back). Returns how many were stored."""
    today = today or dt.date.today()
    have = load()
    missing = [(today - dt.timedelta(days=i)).isoformat() for i in range(1, BACKFILL_DAYS + 1)]
    missing = [d for d in missing if "steps" not in (have.get(d) or {})]
    if not missing:
        return 0
    g = g or _garmin()
    got = {}
    for day in missing:
        try:
            got[day] = fetch_day(g, day)
        except Exception as e:  # noqa: BLE001 — one bad day must not lose the others
            log.warning("%s: %s", day, e)

    def merge(d):
        for day, rec in got.items():
            d.setdefault(day, {}).update(rec)
    _update(merge)
    return len(got)


# ── numbers and patterns ─────────────────────────────────────

def _days(d, end, n):
    return [(end - dt.timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def _mean(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return round(statistics.mean(vals), 1) if vals else None


def week_stats(d, end):
    """Seven days ending with `end` (a date)."""
    recs = [d.get(k) or {} for k in _days(d, end, 7)]
    return dict(
        days=sum(1 for r in recs if r.get("steps") is not None or r.get("sleep_s")),
        sleep_h=_mean([r["sleep_s"] / 3600 for r in recs if r.get("sleep_s")]),
        sleep_score=_mean([r.get("sleep_score") for r in recs]),
        deep_min=_mean([r["deep_s"] / 60 for r in recs if r.get("deep_s")]),
        steps=_mean([r.get("steps") for r in recs]),
        steps_total=sum(r.get("steps") or 0 for r in recs),
        stress=_mean([r.get("stress") for r in recs]),
        bb_high=_mean([r.get("bb_high") for r in recs]),
        golf_rounds=sum((r.get("golf") or {}).get("rounds", 0) for r in recs),
        golf_minutes=sum((r.get("golf") or {}).get("minutes", 0) for r in recs),
    )


def _features(rec):
    log = rec.get("log") or []
    meals = [e["at"] for e in log if e.get("kind") in ("meal", "essen")]
    f = {"a late meal (from %s)" % LATE_MEAL: (max(meals) >= LATE_MEAL) if meals else None,
         "alcohol": True if any(e.get("kind") in ("alcohol", "alkohol") for e in log) else (False if log else None),
         "a round of golf": (rec.get("golf") or {}).get("rounds", 0) > 0 if "golf" in rec else None,
         "over 10,000 steps": rec["steps"] >= 10000 if rec.get("steps") is not None else None,
         "high stress (average 40 and up)": rec["stress"] >= 40 if rec.get("stress") is not None else None}
    return f


_OUTCOMES = [("sleep score", lambda r: r.get("sleep_score"), 5, ""),
             ("deep sleep", lambda r: r["deep_s"] / 60 if r.get("deep_s") else None, 10, " minutes"),
             ("Body Battery in the morning", lambda r: r.get("bb_high"), 8, "")]


def patterns(d, end, days=PATTERN_DAYS, min_each=3):
    """Hints like 'after late dinners the sleep score was 68 instead of 77 (4 vs. 11 nights)'."""
    out = []
    keys = _days(d, end, days)
    for feat in _features({}):
        yes, no = {}, {}
        for k in keys:
            nxt = d.get((dt.date.fromisoformat(k) + dt.timedelta(days=1)).isoformat()) or {}
            val = _features(d.get(k) or {}).get(feat)
            if val is None:
                continue
            for name, get, _, _ in _OUTCOMES:
                o = get(nxt)
                if o is not None:
                    (yes if val else no).setdefault(name, []).append(o)
        for name, _, threshold, unit in _OUTCOMES:
            a, b = yes.get(name, []), no.get(name, [])
            if len(a) < min_each or len(b) < min_each:
                continue
            ma, mb = statistics.mean(a), statistics.mean(b)
            if abs(ma - mb) >= threshold:
                out.append(f"After days with {feat}: {name} on average {round(ma)}{unit} instead of {round(mb)}{unit} "
                           f"({len(a)} vs. {len(b)} nights).")
    steps = [(d.get(k, {}).get("steps"),
              (d.get((dt.date.fromisoformat(k) + dt.timedelta(days=1)).isoformat()) or {}).get("sleep_score"))
             for k in keys]
    pairs = [(s, q) for s, q in steps if s is not None and q is not None]
    if len(pairs) >= 10:
        try:
            r = statistics.correlation([p[0] for p in pairs], [p[1] for p in pairs])
        except statistics.StatisticsError:
            r = 0
        if abs(r) >= 0.4:
            out.append(f"More steps went with a {'better' if r > 0 else 'worse'} sleep score in "
                       f"the night after (correlation {r:+.1f} over {len(pairs)} days).")
    return out


def _fmt(v, unit="", nd=1):
    if v is None:
        return "–"
    return f"{round(v, nd) if nd else round(v)}{unit}"


ROOM_SENSORS = {"Living Room": "sensor.time_living_room_7_days", "Bedroom": "sensor.time_bedroom_7_days"}


def rooms_line(states=None):
    """Hours per room over the last seven days, from Home Assistant's history_stats sensors over the presence
    helper (input_select.user_room; Bedroom includes the bathroom). "" without Home Assistant or sensors."""
    if states is None:
        try:
            import briefing
            states = {s["entity_id"]: s for s in briefing._ha_states()}
        except Exception:  # noqa: BLE001 — a bonus line, never a reason to skip the review
            return ""
    parts = []
    for room, eid in ROOM_SENSORS.items():
        try:
            hours = float((states.get(eid) or {}).get("state"))
        except (TypeError, ValueError):
            continue
        parts.append(f"{room} {hours:.0f} h")
    if not parts:
        return ""
    return "Time at home by room (presence sensors, last 7 days): " + ", ".join(parts) + "."


def facts(d, end, states=None):
    cur, prev = week_stats(d, end), week_stats(d, end - dt.timedelta(days=7))
    found = patterns(d, end)
    lines = [f"Week up to {end.isoformat()}: data on {cur['days']} of 7 days."]
    if cur["days"]:
        lines += [f"Sleep on average {_fmt(cur['sleep_h'], ' h')} (week before {_fmt(prev['sleep_h'], ' h')}), "
                  f"sleep score {_fmt(cur['sleep_score'], nd=0)} (week before {_fmt(prev['sleep_score'], nd=0)}), "
                  f"deep sleep {_fmt(cur['deep_min'], ' min', 0)}.",
                  f"Steps on average {_fmt(cur['steps'], nd=0)} a day (week before {_fmt(prev['steps'], nd=0)}).",
                  f"Stress on average {_fmt(cur['stress'], nd=0)} (week before {_fmt(prev['stress'], nd=0)}), "
                  f"Body Battery in the morning {_fmt(cur['bb_high'], nd=0)} (week before {_fmt(prev['bb_high'], nd=0)}).",
                  f"Golf: {cur['golf_rounds']} round(s), {cur['golf_minutes']} minutes "
                  f"(week before {prev['golf_rounds']})."]
    rooms = rooms_line(states)
    if rooms:
        lines.append(rooms)
    if found:
        lines.append("Patterns of the last weeks:\n" + "\n".join(f"- {p}" for p in found))
    else:
        lines.append("Patterns: not enough data yet for solid statements (at least three days with and without each). "
                     "Garmin does not know meal times — Jarvis notes them when the user says them.")
    return cur, found, "\n".join(lines)


def review(today=None, speak=True):
    """Writes the weekly note and speaks the summary. Returns the spoken text (or None without data)."""
    today = today or dt.date.today()
    end = today - dt.timedelta(days=1)
    d = load()
    cur, found, text = facts(d, end)
    if not cur["days"]:
        return None
    spoken = background.ask("weekly-review", SYSTEM, "Facts:\n" + text) or text.split("Patterns")[0].strip()
    week = background.week_key(end)
    folder = memory.MEMORY_DIR / "Health"
    folder.mkdir(parents=True, exist_ok=True)
    expires = (today + dt.timedelta(days=365)).isoformat()
    (folder / f"Weekly review {week}.md").write_text(
        f"---\ntype: health\nimportance: low\nupdated: {today.isoformat()}\nexpires: {expires}\n---\n\n"
        f"# Weekly review {week}\n\nBelongs to [[Health]].\n\n" + text +
        "\n\n## Spoken\n\n" + " ".join(spoken.split()) + "\n", encoding="utf-8")
    if speak:
        events.publish("say", spoken, kind="health")
    return spoken


def step(now=None):
    now = now or dt.datetime.now()
    if now.hour >= 9 and background.claim("health-fetch", now.date().isoformat()):
        try:
            fetch(now.date())
        except Exception as e:  # noqa: BLE001 — Garmin down, no login: the review says what is missing
            log.warning("Garmin not readable (%s)", e)
    if now.weekday() == 6 and now.hour >= REVIEW_HOUR and background.claim("weekly-review", background.week_key(now.date())):
        review(now.date())


def start():
    if ENABLED:
        background.loop("weekly_review", 600, step)
