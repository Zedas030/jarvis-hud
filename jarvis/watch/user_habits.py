"""
The user's own habits: what he asks Jarvis at the same time of day, learned from the usage log
(logs/usage.jsonl) — plain counting, 0 tokens.

  habits()   questions asked within ±TOLERANCE minutes of the same time on at least MIN_DAYS
             different days ("When do I have to leave?" on Tuesdays around 7:40). Only questions whose
             answer is a reading (weather, commute, parcels, mail …) count — switching something
             at the same time every day is house_habits' business (and a routine's).
  proposal   once a week one habit becomes a proposal (proposals.py, kind "habit"): accepted on
             the card, it is a job in state.json → habit_jobs:
               say   a question the local router answers: Jarvis says the answer by himself at
                     that time ("commute today 24 minutes, leave at 7:55")
               warm  a question that needs the model: the answer is fetched a few minutes before
                     and put in the answer cache, so the question itself costs 0 tokens and no wait
  jobs       run by step() (every minute): once per job and day, never in the quiet hours for "say"

"Which habits do you know?" lists the jobs, "Forget the habit …" removes one.
Work topics are skipped before anything is counted (privacy.py).
"""
import collections
import datetime as dt
import json
import os
import re
import statistics
import time

import background
import commands
import events

ENABLED = os.environ.get("JARVIS_USER_HABITS", "1").strip() not in ("0", "false", "no", "off")
DAYS = int(os.environ.get("JARVIS_USER_HABITS_DAYS", "21"))
MIN_DAYS = int(os.environ.get("JARVIS_USER_HABITS_MIN_DAYS", "4"))
MIN_SAME_WEEKDAY = 3
TOLERANCE = 30                     # minutes around the usual time
WARM_LEAD_MIN = 3                  # fetch the model's answer this long before the usual time
READ_HANDLERS = {"weather", "sun", "commute", "parcels", "nas", "pi", "costs", "situation", "coach", "context",
                 "report", "printer", "solar", "alerts", "episodes", "maintenance", "wellbeing", "media"}
_SKIP_WORDS = re.compile(r"\b(switch|turn|open|start|play|timer|alarm|remind|delete|add|remember|stop)", re.I)


def _norm(message):
    t = re.sub(r"[^\w ]+", " ", re.sub(r"['’]", "", (message or "").lower()))
    t = re.sub(r"\b(jarvis|please|sir|hey)\b", " ", t)
    return " ".join(t.split())[:70]


def _rows(now=None):
    import learning
    since = (now or time.time()) - DAYS * 86400
    out = []
    try:
        with learning.USAGE.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                    t = time.mktime(time.strptime(r["at"], "%Y-%m-%d %H:%M:%S"))
                except (ValueError, KeyError):
                    continue
                route = str(r.get("route", ""))
                if t < since or route.startswith(("background", "briefing", "cache")) or not r.get("message"):
                    continue
                if route.startswith("local:") and route.split(":", 1)[1] not in READ_HANDLERS:
                    continue
                if route.startswith(("llm", "fallback")) and not _llm_ok(r):
                    continue
                out.append(dict(t=t, message=r["message"], route=route))
    except OSError:
        pass
    return out


def _llm_ok(row):
    import answer_cache
    groups = [g for g in row.get("groups") or [] if g != "web"]
    return answer_cache.cacheable(row.get("message", ""), groups) and not _SKIP_WORDS.search(row.get("message", ""))


def _minute(t):
    lt = time.localtime(t)
    return lt.tm_hour * 60 + lt.tm_min


def _dist(a, b):
    d = abs(a - b) % 1440
    return min(d, 1440 - d)


def habits(rows=None, now=None):
    """[{key, text, route, minute, at, days, weekdays}] — one per question, its strongest time."""
    import privacy
    rows = _rows(now) if rows is None else rows
    by = collections.defaultdict(list)
    for r in rows:
        k = _norm(r["message"])
        if len(k.split()) >= 2 and not privacy.is_work(r["message"]):
            by[k].append(r)
    out = []
    for k, items in by.items():
        best = None
        for r in items:
            near = [u for u in items if _dist(_minute(u["t"]), _minute(r["t"])) <= TOLERANCE]
            dates = {time.strftime("%Y-%m-%d", time.localtime(u["t"])) for u in near}
            if len(dates) < MIN_SAME_WEEKDAY or (best and len(dates) <= best[1]):
                continue
            weekdays = collections.Counter(time.localtime(u["t"]).tm_wday for u in near)
            wd, n_wd = weekdays.most_common(1)[0]
            days_same = len({time.strftime("%Y-%m-%d", time.localtime(u["t"])) for u in near
                             if time.localtime(u["t"]).tm_wday == wd})
            if days_same >= MIN_SAME_WEEKDAY and days_same >= len(dates) - 1:
                wds = [wd]                              # one weekday: "on Tuesdays"
            elif len(dates) >= MIN_DAYS:
                wds = list(range(7))                    # most days: daily
            else:
                continue
            offs = sorted((_minute(u["t"]) - _minute(r["t"]) + 720) % 1440 - 720 for u in near)
            center = (_minute(r["t"]) + int(statistics.median(offs))) % 1440
            best = (center, len(dates), wds, near[-1])
        if best:
            center, n, wds, example = best
            out.append(dict(key=k, text=example["message"][:120], route=example["route"], minute=center,
                            at=f"{center // 60:02d}:{center % 60:02d}", days=n, weekdays=wds))
    return sorted(out, key=lambda h: -h["days"])


# ── jobs ─────────────────────────────────────────────────────

WEEKDAY_NAMES = ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays")


def _when(weekdays, at):
    days = "every day" if len(weekdays) >= 7 else "on " + ", ".join(WEEKDAY_NAMES[d] for d in sorted(weekdays))
    return f"{days} at {int(at[:2])}:{at[3:]}"


def jobs():
    return list(commands.load().get("habit_jobs") or [])


def add_job(payload):
    """Accepted proposal → job. Returns spoken text."""
    job = dict(id=payload.get("key", "")[:70], text=str(payload.get("text", ""))[:120], at=str(payload.get("at", "08:00")),
               weekdays=[int(d) for d in payload.get("weekdays") or range(7)], mode=payload.get("mode") or "say")
    if not job["id"] or not job["text"]:
        return "The question is missing for that."

    def mut(d):
        rest = [j for j in (d.get("habit_jobs") or []) if j.get("id") != job["id"]]
        d["habit_jobs"] = rest + [job]
    commands.update_state(mut)
    if job["mode"] == "say":
        return f"Set up: {_when(job['weekdays'], job['at'])} I will tell you “{job['text']}” on my own."
    return (f"Set up: {_when(job['weekdays'], job['at'])} I will fetch the answer to “{job['text']}” in advance, "
            "so it comes instantly and without tokens.")


def remove_job(words):
    want = _norm(words)
    keep = [j for j in jobs() if want and want not in _norm(j["text"]) and _norm(j["text"]) not in want]
    gone = len(jobs()) - len(keep)
    if not gone:
        return "No habit matches that, Sir."
    commands.update_state(lambda d: d.__setitem__("habit_jobs", keep))
    return f"Forgotten, Sir. {gone} habit(s) dropped."


def describe_jobs():
    js = jobs()
    if not js:
        return "I have not taken over any of your habits yet, Sir. I make suggestions for that on Saturdays."
    rows = [f"{_when(j['weekdays'], j['at'])}: “{j['text']}” ({'I say it on my own' if j['mode'] == 'say' else 'I fetch it in advance'})"
            for j in js]
    return "Your habits that I know: " + "; ".join(rows) + "."


def _mode(route):
    return "say" if route.startswith("local:") else "warm"


def propose(found=None, now=None):
    """One habit that is no job yet and was not declined → proposal + notice. Returns the id or None."""
    import proposals
    found = habits(now=now) if found is None else found
    have = {j["id"] for j in jobs()}
    declined = {p.get("source", "") for p in proposals._load()
                if p.get("kind") == "habit" and p.get("status") in ("declined", "accepted")}
    for h in found:
        if h["key"] in have or f"user_habits:{h['key']}" in declined:
            continue
        mode = _mode(h["route"])
        when = _when(h["weekdays"], h["at"])
        payload = dict(key=h["key"], text=h["text"], at=h["at"], weekdays=h["weekdays"], mode=mode)
        why = f"You asked this on {h['days']} days around this time."
        title = (f"{when[:1].upper() + when[1:]} say on my own: “{h['text']}”" if mode == "say"
                 else f"{when[:1].upper() + when[1:]} fetch in advance: “{h['text']}”")
        pid = proposals.add("habit", title, why, payload, source=f"user_habits:{h['key']}")
        events.publish("say", f"Sir, I noticed: {why} Shall I "
                              f"{'say it on my own' if mode == 'say' else 'fetch it in advance, so it comes instantly'} {when}? "
                              "Just say yes.", kind="habit")
        return pid
    return None


def _prewarm(text):
    """The model's answer, fetched now and put in the answer cache (only for read-only questions)."""
    import answer_cache
    import router
    import runtime
    r = router.llm_route(text)
    groups = r["groups"] + (["web"] if router.WEB_ALWAYS and "web" not in r["groups"] else [])
    if not answer_cache.cacheable(text, [g for g in groups if g != "web"]):
        return None
    system = commands.lean_system(groups)
    prefix = commands.lean_prefix(text, groups)
    t0, reply, usage = time.time(), [], {}
    for ev in runtime.run_lean(text, r["model"], groups, system, prefix):
        if ev.get("t") == "delta":
            reply.append(ev.get("text") or "")
        elif ev.get("t") == "usage":
            usage = ev
        elif ev.get("t") == "error":
            return None
    answer = "".join(reply).strip()
    if answer:
        answer_cache.put(text, groups, answer)
        background._log("habit-warm", r["model"], usage, text, answer, int((time.time() - t0) * 1000))
    return answer


def run_job(job, now=None):
    import router
    if job.get("mode") == "warm":
        try:
            return _prewarm(job["text"])
        except Exception as e:  # noqa: BLE001 — a missed warm-up costs nothing but the wait
            print(f"  user_habits: {e}", flush=True)
            return None
    if events.quiet_now(now):
        return None
    reply = router.run_local(job["text"])
    if reply:
        events.publish("say", f"Sir, as usual: {reply}", kind="habit", key=f"habit:{job['id']}")
    return reply


def due(job, now):
    if now.weekday() not in job.get("weekdays", []):
        return False
    hh, mm = (int(x) for x in job["at"].split(":"))
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if job.get("mode") == "warm":
        target -= dt.timedelta(minutes=WARM_LEAD_MIN)
    return 0 <= (now - target).total_seconds() < 120


def step(now=None):
    now = now or dt.datetime.now()
    for job in jobs():
        if due(job, now) and background.claim(f"habit:{job['id']}", now.date().isoformat()):
            run_job(job, now)
    if now.weekday() == 5 and now.hour >= 10 and not events.quiet_now(now) \
            and background.claim("user-habits-propose", background.week_key(now.date())):
        propose(now=now.timestamp())


# ── local router ─────────────────────────────────────────────

def handle(n):
    if re.search(r"\b(which|what) habits (do you know|have you got|do you have)\b|\bmy habits\b", n):
        return describe_jobs()
    m = re.search(r"\b(?:forget|drop|delete|remove) (?:the |my )?habit (.+)$", n.strip())
    if m:
        return remove_job(m.group(1))
    return None


def start():
    if ENABLED:
        background.loop("user_habits", 60, step)
