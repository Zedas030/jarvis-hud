"""
Weekly review: sleep, steps, stress, Body Battery and golf rounds — with patterns.

Daily (from 9 h) the finished days are fetched from Garmin once and kept locally
in JARVIS_HEALTH_DATA (logs/health_daily.json; never in the repository). The user
can add what Garmin cannot know by voice — "Ich habe um 22 Uhr gegessen",
"zwei Bier getrunken" — through the tool health_log (log_entry below).

Sunday (from JARVIS_REVIEW_HOUR, default 18 h) the week is summed up and compared
with the week before. Patterns are computed in code, not guessed by the model:
days with a feature (late dinner, alcohol, golf, 10,000 steps, high stress) are
compared with days without it, judged by the following night (sleep score, deep
sleep, Body Battery the next morning). A pattern is only named with at least
three days on each side and a clear difference, always with the counts — it is
a hint, not proof. The model only phrases the result for speaking.

Note: Erinnerungen/Gesundheit/Wochenrückblick <year>-KW<nn>.
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

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
DATA = pathlib.Path(os.environ.get("JARVIS_HEALTH_DATA", ROOT / "logs" / "health_daily.json"))
REVIEW_HOUR = int(os.environ.get("JARVIS_REVIEW_HOUR", "18"))
LATE_MEAL = os.environ.get("JARVIS_LATE_MEAL", "21:00").strip()
BACKFILL_DAYS = int(os.environ.get("JARVIS_HEALTH_BACKFILL", "14"))
PATTERN_DAYS = int(os.environ.get("JARVIS_PATTERN_DAYS", "42"))
ENABLED = os.environ.get("JARVIS_REVIEW", "1").strip() not in ("0", "false", "no", "off")
_LOCK = threading.Lock()

SYSTEM = ("Du bist JARVIS. Aus fertig berechneten Gesundheitszahlen formulierst du einen gesprochenen "
          "Wochenrückblick: Deutsch, du siezt den User, trocken-britisch, vier bis sechs kurze Sätze, kein Markdown, "
          "Zahlen wie gesprochen. Nenne nur Zahlen und Muster aus den Fakten, erfinde nichts, keine medizinischen "
          "Ratschläge. Muster vorsichtig formulieren (Häufung, kein Beweis).")


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


_KINDS = [("essen", r"\b(gegessen|essen|abendessen|abendbrot|mahlzeit|snack|pizza|döner|burger)"),
          ("alkohol", r"\b(alkohol|bier|wein|sekt|schnaps|cocktail|getrunken)"),
          ("koffein", r"\b(kaffee|espresso|cola|energy|koffein)"),
          ("training", r"\b(training|sport|gelaufen|laufen|fitness|gym)")]


def log_entry(what, at="", now=None):
    """health_log: what + time ('22:10', 'gestern 22:10', 'YYYY-MM-DD HH:MM'). After midnight until 5 h
    an entry counts for the evening before."""
    now = now or dt.datetime.now()
    text = " ".join((what or "").split())[:80]
    if not text:
        return "Was soll ich notieren?"
    kind = next((k for k, pat in _KINDS if re.search(pat, text.lower())), "notiz")
    at = (at or "").strip().lower()
    day = now.date()
    if "gestern" in at:
        day -= dt.timedelta(days=1)
    m = re.search(r"(\d{4}-\d{2}-\d{2})", at)
    if m:
        day = dt.date.fromisoformat(m.group(1))
    m = re.search(r"(\d{1,2})(?::|\.|\s*uhr\s*)?(\d{2})?", re.sub(r"\d{4}-\d{2}-\d{2}", "", at))
    hh, mm = (int(m.group(1)), int(m.group(2) or 0)) if m else (now.hour, now.minute)
    if not (0 <= hh < 24 and 0 <= mm < 60):
        return "Die Uhrzeit verstehe ich nicht."
    if hh < 5:
        day -= dt.timedelta(days=1)
        clock = f"{hh + 24:02d}:{mm:02d}"     # 00:30 → "24:30" of the evening before, sorts after 23:00
    else:
        clock = f"{hh:02d}:{mm:02d}"
    _update(lambda d: d.setdefault(day.isoformat(), {}).setdefault("log", []).append(
        {"kind": kind, "what": text, "at": clock}))
    return f"Notiert: {text}, {clock if hh >= 5 else f'{hh}:{mm:02d}'} Uhr ({day.strftime('%d.%m.')})."


# ── Garmin ───────────────────────────────────────────────────

def _garmin():
    import jarvis_mcp as j
    from garminconnect import Garmin
    if not (j.GARMIN_EMAIL and j.GARMIN_PASSWORD):
        raise RuntimeError("Garmin nicht verbunden")
    g = Garmin(j.GARMIN_EMAIL, j.GARMIN_PASSWORD, return_on_mfa=True)
    needs_mfa, _ = g.login(j.GARMIN_TOKENS)
    if needs_mfa:
        raise RuntimeError("Garmin verlangt Zwei-Faktor-Anmeldung")
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
            print(f"  weekly_review: {day}: {e}", flush=True)

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
    meals = [e["at"] for e in log if e.get("kind") == "essen"]
    f = {"spätes Essen (ab %s Uhr)" % LATE_MEAL: (max(meals) >= LATE_MEAL) if meals else None,
         "Alkohol": True if any(e.get("kind") == "alkohol" for e in log) else (False if log else None),
         "einer Golfrunde": (rec.get("golf") or {}).get("rounds", 0) > 0 if "golf" in rec else None,
         "über 10.000 Schritten": rec["steps"] >= 10000 if rec.get("steps") is not None else None,
         "hohem Stress (Schnitt ab 40)": rec["stress"] >= 40 if rec.get("stress") is not None else None}
    return f


_OUTCOMES = [("Schlafscore", lambda r: r.get("sleep_score"), 5, ""),
             ("Tiefschlaf", lambda r: r["deep_s"] / 60 if r.get("deep_s") else None, 10, " Minuten"),
             ("Body Battery am Morgen", lambda r: r.get("bb_high"), 8, "")]


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
                out.append(f"Nach Tagen mit {feat}: {name} im Schnitt {round(ma)}{unit} statt {round(mb)}{unit} "
                           f"({len(a)} gegen {len(b)} Nächte).")
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
            out.append(f"Mehr Schritte gingen {'mit besserem' if r > 0 else 'mit schlechterem'} Schlafscore in "
                       f"der Nacht danach einher (Zusammenhang {r:+.1f} über {len(pairs)} Tage).")
    return out


def _fmt(v, unit="", nd=1):
    if v is None:
        return "–"
    return f"{round(v, nd) if nd else round(v)}{unit}".replace(".", ",")


def facts(d, end):
    cur, prev = week_stats(d, end), week_stats(d, end - dt.timedelta(days=7))
    found = patterns(d, end)
    lines = [f"Woche bis {end.strftime('%d.%m.')}: Daten an {cur['days']} von 7 Tagen."]
    if cur["days"]:
        lines += [f"Schlaf im Schnitt {_fmt(cur['sleep_h'], ' h')} (Vorwoche {_fmt(prev['sleep_h'], ' h')}), "
                  f"Schlafscore {_fmt(cur['sleep_score'], nd=0)} (Vorwoche {_fmt(prev['sleep_score'], nd=0)}), "
                  f"Tiefschlaf {_fmt(cur['deep_min'], ' min', 0)}.",
                  f"Schritte im Schnitt {_fmt(cur['steps'], nd=0)} pro Tag (Vorwoche {_fmt(prev['steps'], nd=0)}).",
                  f"Stress im Schnitt {_fmt(cur['stress'], nd=0)} (Vorwoche {_fmt(prev['stress'], nd=0)}), "
                  f"Body Battery morgens {_fmt(cur['bb_high'], nd=0)} (Vorwoche {_fmt(prev['bb_high'], nd=0)}).",
                  f"Golf: {cur['golf_rounds']} Runde(n), {cur['golf_minutes']} Minuten "
                  f"(Vorwoche {prev['golf_rounds']})."]
    if found:
        lines.append("Muster der letzten Wochen:\n" + "\n".join(f"- {p}" for p in found))
    else:
        lines.append("Muster: noch zu wenig Daten für belastbare Aussagen (je mindestens drei Tage mit und ohne). "
                     "Essenszeiten kennt Garmin nicht — die notiert Jarvis, wenn der User sie sagt.")
    return cur, found, "\n".join(lines)


def review(today=None, speak=True):
    """Writes the weekly note and speaks the summary. Returns the spoken text (or None without data)."""
    today = today or dt.date.today()
    end = today - dt.timedelta(days=1)
    d = load()
    cur, found, text = facts(d, end)
    if not cur["days"]:
        return None
    spoken = background.ask("weekly-review", SYSTEM, "Fakten:\n" + text) or text.split("Muster")[0].strip()
    week = background.week_key(end)
    folder = memory.MEMORY_DIR / "Gesundheit"
    folder.mkdir(parents=True, exist_ok=True)
    expires = (today + dt.timedelta(days=365)).isoformat()
    (folder / f"Wochenrückblick {week}.md").write_text(
        f"---\ntype: gesundheit\nwichtigkeit: niedrig\nupdated: {today.isoformat()}\nablauf: {expires}\n---\n\n"
        f"# Wochenrückblick {week}\n\nGehört zu [[Gesundheit]].\n\n" + text +
        "\n\n## Gesprochen\n\n" + " ".join(spoken.split()) + "\n", encoding="utf-8")
    if speak:
        events.publish("say", spoken, kind="health")
    return spoken


def step(now=None):
    now = now or dt.datetime.now()
    if now.hour >= 9 and background.claim("health-fetch", now.date().isoformat()):
        try:
            fetch(now.date())
        except Exception as e:  # noqa: BLE001 — Garmin down, no login: the review says what is missing
            print(f"  weekly_review: Garmin nicht lesbar ({e})", flush=True)
    if now.weekday() == 6 and now.hour >= REVIEW_HOUR and background.claim("weekly-review", background.week_key(now.date())):
        review(now.date())


def start():
    if ENABLED:
        background.loop("weekly_review", 600, step)
