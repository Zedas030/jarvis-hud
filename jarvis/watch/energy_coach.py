"""
Energy coach: when is solar power left over, and what is worth running then — read-only,
0 tokens. Jarvis never switches anything himself; he only says when it would pay off.

  now        storage charge, solar and house load from Home Assistant (config/home.json)
  forecast   hourly shortwave radiation from open-meteo for today and tomorrow, turned into
             watts with a factor learned each evening from what the panels really produced
  plugs      every switch.X with a power sensor sensor.X<JARVIS_PLUG_POWER_SUFFIX> is learned:
             typical watts, energy on the days it is used, last day it was on

Rarely used plugs: a plug that is off the first time the coach sees it counts as rarely used
(the user switches such plugs — the boiler — on only now and then) and is never suggested.
It counts as used again after JARVIS_COACH_REUSE_DAYS (2) days of use within two weeks; a plug
not switched on for 14 days becomes rare. What the user says ("I use the boiler often")
overrides both and stays.

  advise(...)   pure: the numbers and the best window, from states, memory and forecast
  report()      the spoken answer for the router and the energy_coach tool
  set_usage()   "I use it often/rarely" for one plug
  step()        background: learn, calibrate in the evening, announce a surplus once a day

Also kept for other modules: each plug's lowest draw per day while switched on (standby, power_costs.py)
and the solar yield per day (homelab_review.py).

Memory: logs/energy_coach.json (JARVIS_ENERGY_COACH_STATE). Config: config/energy_coach.json
(JARVIS_ENERGY_COACH_CONFIG) — which plugs can be shifted, battery size, panel peak.
"""
import datetime as dt
import json
import os
import pathlib
import re
import statistics
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
STATE = pathlib.Path(os.environ.get("JARVIS_ENERGY_COACH_STATE", ROOT / "logs" / "energy_coach.json"))
CONFIG = pathlib.Path(os.environ.get("JARVIS_ENERGY_COACH_CONFIG", ROOT / "config" / "energy_coach.json"))
ENABLED = os.environ.get("JARVIS_ENERGY_COACH", "1").strip() not in ("0", "false", "no", "off")
EVERY = int(os.environ.get("JARVIS_ENERGY_COACH_SECONDS", "300"))
# entity ids as Home Assistant names them in English (a German HA: _derzeitiger_verbrauch, _heutiger_verbrauch)
POWER_SUFFIX = os.environ.get("JARVIS_PLUG_POWER_SUFFIX", "_current_consumption")
DAY_SUFFIX = os.environ.get("JARVIS_PLUG_DAY_SUFFIX", "_today_s_consumption")
HOUSE = os.environ.get("JARVIS_HOUSE_POWER", "sensor.house_consumption")
REUSE_DAYS = int(os.environ.get("JARVIS_COACH_REUSE_DAYS", "2"))
IDLE_DAYS = 14                 # not switched on for this long = rarely used
USED_KWH, RUN_W = 0.05, 10     # a day counts as used from 50 Wh; a plug is running from 10 W
FULL_SOC = 95                  # the battery takes nothing more from here on
MIN_SPILL_W = 150              # surplus worth mentioning
DEFAULTS = {"shiftable": {}, "battery_kwh": 4.0, "pv_peak_w": 800, "hours": [8, 18]}
_LOCK = threading.Lock()


# ── memory and config ────────────────────────────────────────

def config():
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    return dict(DEFAULTS, **{k: v for k, v in cfg.items() if not k.startswith("_")})


def load():
    try:
        mem = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        mem = {}
    for key, empty in (("devices", {}), ("house", []), ("calib", []), ("announced", ""), ("streak", 0), ("gen", {})):
        mem.setdefault(key, empty)
    return mem


def save(mem):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(mem, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt(x, digits=1):
    return f"{x:.{digits}f}"


def _day(now):
    return now.strftime("%Y-%m-%d")


# ── plugs ────────────────────────────────────────────────────

def plugs(states):
    """{switch: {name, on, watts, kwh}} for every switch with a power sensor next to it."""
    out = {}
    for eid, s in states.items():
        if not eid.startswith("switch."):
            continue
        base = eid.split(".", 1)[1]
        power = states.get(f"sensor.{base}{POWER_SUFFIX}")
        if power is None or s.get("state") not in ("on", "off"):
            continue
        name = (s.get("attributes") or {}).get("friendly_name") or base
        out[eid] = dict(name=name.strip(), on=s["state"] == "on", watts=_num(power.get("state")) or 0.0,
                        kwh=_num((states.get(f"sensor.{base}{DAY_SUFFIX}") or {}).get("state")) or 0.0)
    return out


def _recent(days, now, span=IDLE_DAYS):
    first = _day(now - dt.timedelta(days=span))
    return [d for d in days if d >= first]


def learn(mem, found, now):
    """Updates the plug memory; returns [(name, 'rare'|'used')] for plugs that changed."""
    today, changed = _day(now), []
    for eid, p in found.items():
        d = mem["devices"].get(eid)
        if d is None:
            d = mem["devices"][eid] = dict(name=p["name"], first_seen=today, used_days=[], watts=[], day_kwh={},
                                           rare=not p["on"], by="auto",
                                           why="was off when I first got to know the sockets" if not p["on"] else "")
        d["name"] = p["name"]
        if p["on"]:
            d["last_on"] = today
        if p["watts"] >= RUN_W:
            d["watts"] = (d["watts"] + [round(p["watts"], 1)])[-50:]
        if p["on"]:                                    # the lowest draw of a day while switched on = standby
            low = d.setdefault("min_w", {})
            low[today] = round(min(low.get(today, p["watts"]), p["watts"]), 1)
            d["min_w"] = dict(sorted(low.items())[-14:])
        if p["kwh"] > 0:
            d["day_kwh"][today] = max(d["day_kwh"].get(today, 0), round(p["kwh"], 3))
            d["day_kwh"] = dict(sorted(d["day_kwh"].items())[-30:])
        if (p["kwh"] >= USED_KWH or p["watts"] >= RUN_W) and today not in d["used_days"]:
            d["used_days"] = (d["used_days"] + [today])[-30:]
        if d.get("by") == "user":
            continue                                   # what the user said stays
        if d["rare"] and len(_recent(d["used_days"], now)) >= REUSE_DAYS:
            d.update(rare=False, why="used regularly again")
            changed.append((d["name"], "used"))
        elif not d["rare"] and not p["on"] and (d.get("last_on") or d["first_seen"]) < _day(now - dt.timedelta(days=IDLE_DAYS)):
            d.update(rare=True, why=f"not switched on for more than {IDLE_DAYS} days")
            changed.append((d["name"], "rare"))
    return changed


def typical(d):
    """(watts while running, kWh on a day of use) — None while nothing is known."""
    w = statistics.median(d["watts"]) if d.get("watts") else None
    used = [v for v in (d.get("day_kwh") or {}).values() if v >= USED_KWH]
    return w, (statistics.median(used) if used else None)


def _match(word, mem):
    """The plug whose name fits a spoken word ('boiler' → 'Kitchen Boiler')."""
    w = _norm(word)
    hits = [eid for eid, d in mem["devices"].items() if w and (w in _norm(d["name"]) or _norm(d["name"]) in w)]
    return hits[0] if len(hits) == 1 else None


def _norm(s):
    s = (s or "").lower().replace("ü", "u").replace("ö", "o").replace("ä", "a").replace("ß", "ss")
    return re.sub(r"[^a-z0-9]+", "", s)


def set_usage(word, often):
    """'I use it often/rarely' for one plug; the user's word overrides what the coach learned."""
    with _LOCK:
        mem = load()
        eid = _match(word, mem)
        if not eid:
            known = ", ".join(sorted(d["name"] for d in mem["devices"].values())) or "none yet"
            return f"Which socket do you mean by “{word}”? I know: {known}."
        d = mem["devices"][eid]
        d.update(rare=not often, by="user", why="said so" if often else "rarely used, said so")
        save(mem)
    return (f"Noted: you use {d['name']} {'often' if often else 'rarely'} — "
            + ("I will suggest it again when there is solar surplus." if often else "I will not suggest it any more."))


# ── forecast ─────────────────────────────────────────────────

_FC = {"at": 0.0, "hours": []}


def forecast():
    """[(local datetime, W/m²)] for today and tomorrow, cached for an hour."""
    if time.time() - _FC["at"] < 3600 and _FC["hours"]:
        return _FC["hours"]
    import briefing
    d = briefing._get("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&timezone=%s"
                      "&forecast_days=2&hourly=shortwave_radiation" % (briefing.LAT, briefing.LON, briefing.TZ_Q))
    h = d["hourly"]
    _FC.update(at=time.time(), hours=[(dt.datetime.fromisoformat(t), r or 0) for t, r in
                                      zip(h["time"], h["shortwave_radiation"])])
    return _FC["hours"]


def factor(mem, cfg):
    """Panel watts per W/m², learned from the evening calibrations (upper half: curtailed days are low)."""
    vals = sorted(c["f"] for c in mem["calib"][-14:])
    if len(vals) >= 3:
        return vals[len(vals) * 3 // 4]
    return cfg["pv_peak_w"] / 1000 * 0.8


def calibrate(mem, gen_kwh, fc, now):
    """Once per evening: today's real yield against today's radiation."""
    today = _day(now)
    if now.hour < 20 or any(c["day"] == today for c in mem["calib"]) or gen_kwh is None:
        return False
    rad = sum(r for t, r in fc if t.date() == now.date())          # Wh/m² over the day
    if rad < 500:
        return False                                              # grey days say little about the panels
    mem["calib"] = (mem["calib"] + [{"day": today, "f": round(gen_kwh * 1000 / rad, 3)}])[-30:]
    return True


# ── advice ───────────────────────────────────────────────────

def _base_w(mem, house_now):
    vals = [v for v in mem["house"][-2016:] if v is not None]
    return statistics.median(vals) if len(vals) >= 12 else (house_now or 300)


def window(fc, start, end, f, base_w, room_kwh):
    """Hours in [start, end) where the battery is already full and the sun still delivers more than the house
    uses. Returns (spill kWh, first hour, last hour) of the best run, or (0, None, None)."""
    runs, cur, room = [], [], room_kwh * 1000
    for t, rad in fc:
        if not start <= t < end:
            continue
        extra = rad * f - base_w
        if extra <= 0:
            if cur:
                runs.append(cur)
            cur = []
            continue
        take = min(room, extra)
        room -= take
        spill = extra - take
        if spill >= MIN_SPILL_W / 2:
            cur.append((t, spill))
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    if not runs:
        return 0.0, None, None
    best = max(runs, key=lambda r: sum(s for _, s in r))
    return sum(s for _, s in best) / 1000, best[0][0].hour, best[-1][0].hour + 1


def advise(states, mem, cfg, fc, now):
    import jarvis_profile as profile
    val = lambda eid: _num((states.get(eid) or {}).get("state")) if eid else None  # noqa: E731
    soc = val(profile.ha("storage_soc"))
    solar = sum(val(e) or 0 for e in profile.ha("solar_power", []))
    house = val(HOUSE)
    f, base = factor(mem, cfg), _base_w(mem, house)
    room = (100 - (soc or 0)) / 100 * cfg["battery_kwh"]
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today = window(fc, now.replace(minute=0, second=0, microsecond=0), midnight + dt.timedelta(days=1), f, base, room)
    tomorrow = window(fc, midnight + dt.timedelta(days=1), midnight + dt.timedelta(days=2), f, base,
                      cfg["battery_kwh"] * 0.8)          # overnight the house empties most of it
    pv_today = sum(r * f for t, r in fc if now <= t < midnight + dt.timedelta(days=1)) / 1000
    found = plugs(states)
    cands, rare = [], []
    for eid, label in (cfg.get("shiftable") or {}).items():
        d = mem["devices"].get(eid)
        if d and d.get("rare"):
            rare.append(d["name"])
            continue
        w, kwh = typical(d or {})
        running = (found.get(eid) or {}).get("watts", 0) >= RUN_W
        cands.append(dict(eid=eid, label=label, watts=w, kwh=kwh, running=running))
    spill_now = solar - (house or 0) if soc is not None and soc >= FULL_SOC and house is not None else 0
    return dict(soc=soc, solar=solar, house=house, base=base, spill_now=spill_now, pv_today=pv_today,
                today=today, tomorrow=tomorrow, cands=cands, rare=sorted(set(rare)), factor=f)


def _fits(cands, kwh):
    return [c for c in cands if not c["running"] and (c["kwh"] is None or c["kwh"] <= kwh * 1.2)]


def _names(cands):
    labels = [c["label"] for c in cands]
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " or " + labels[-1] if labels else ""


def say(a, detail=True):
    parts = []
    if a["soc"] is not None:
        parts.append(f"Battery {round(a['soc'])} percent, sun right now {round(a['solar'])} watts"
                     + (f", the house needs {round(a['house'])}" if a["house"] is not None else "") + ".")
    if a["spill_now"] >= MIN_SPILL_W:
        fit = _fits(a["cands"], 99)
        parts.append(f"There are {round(a['spill_now'])} watts of surplus right now, the battery is full"
                     + (f" — a good moment for {_names(fit)}." if fit else "."))
    kwh, h1, h2 = a["today"]
    if kwh >= 0.3:
        fit = _fits(a["cands"], kwh)
        parts.append(f"Today between {h1}:00 and {h2}:00 about {_fmt(kwh)} kilowatt hours are left over once the battery is full"
                     + (f" — right for {_names(fit)}." if fit else "."))
    else:
        parts.append(f"Today about {_fmt(a['pv_today'])} more kilowatt hours of sun are coming — "
                     + ("that goes into the battery, there is no surplus." if a["pv_today"] >= 0.2 else "practically nothing."))
    kwh2, t1, t2 = a["tomorrow"]
    if kwh2 >= 0.3:
        fit = _fits(a["cands"], kwh2)
        parts.append(f"Tomorrow looks better: between {t1}:00 and {t2}:00 about {_fmt(kwh2)} kilowatt hours of surplus"
                     + (f", good for {_names(fit)}." if fit else "."))
    elif detail:
        parts.append("Tomorrow there will probably be no surplus either.")
    if detail and a["rare"]:
        parts.append("Not suggested because rarely used: " + ", ".join(a["rare"]) + ".")
    return " ".join(parts)


def _states():
    import briefing
    return {s["entity_id"]: s for s in briefing._ha_states()}


def report(detail=True):
    states = _states()
    if not states:
        return "Without Home Assistant I cannot give an energy tip right now."
    mem, cfg = load(), config()
    try:
        fc = forecast()
    except Exception:  # noqa: BLE001 — without the forecast the current values still help
        fc = []
    return say(advise(states, mem, cfg, fc, dt.datetime.now()), detail)


def morning_line():
    """One line for the morning briefing when today brings a surplus; '' otherwise."""
    try:
        states, mem, cfg = _states(), load(), config()
        a = advise(states, mem, cfg, forecast(), dt.datetime.now())
    except Exception:  # noqa: BLE001
        return ""
    kwh, h1, h2 = a["today"]
    if kwh < 0.3:
        return ""
    fit = _fits(a["cands"], kwh)
    return (f"Energy coach: today between {h1}:00 and {h2}:00 about {_fmt(kwh)} kWh of solar surplus"
            + (f", good for {_names(fit)}." if fit else "."))


# ── background ───────────────────────────────────────────────

def surplus_notice(a, mem, now, quiet, cfg):
    """The text to announce now, or None. Twice in a row over MIN_SPILL_W, once a day, inside the hours."""
    h0, h1 = cfg.get("hours") or DEFAULTS["hours"]
    mem["streak"] = mem["streak"] + 1 if a["spill_now"] >= MIN_SPILL_W else 0
    fit = _fits(a["cands"], 99)
    if mem["streak"] < 2 or mem["announced"] == _day(now) or quiet or not h0 <= now.hour < h1 or not fit:
        return None
    mem["announced"] = _day(now)
    return (f"Sir, the battery is full and the sun is delivering {round(a['spill_now'])} watts more than the house needs. "
            f"A good moment for {_names(fit)}.")


def step():
    import events
    import jarvis_profile as profile
    states = _states()
    if not states:
        return
    now = dt.datetime.now()
    with _LOCK:
        mem, cfg = load(), config()
        learn(mem, plugs(states), now)
        house = _num((states.get(HOUSE) or {}).get("state"))
        mem["house"] = (mem["house"] + [house])[-2016:]       # one week of 5-minute samples
        try:
            fc = forecast()
        except Exception:  # noqa: BLE001
            fc = []
        gen = sum(_num((states.get(e) or {}).get("state")) or 0 for e in profile.ha("generation_today", []))
        calibrate(mem, gen, fc, now)
        if gen:                                        # the day's yield, for the weekly homelab report
            mem["gen"][_day(now)] = max(mem["gen"].get(_day(now), 0), round(gen, 2))
            mem["gen"] = dict(sorted(mem["gen"].items())[-21:])
        text = surplus_notice(advise(states, mem, cfg, fc, now), mem, now, events.quiet_now(), cfg)
        save(mem)
    if text:
        events.publish("say", text, kind="energy", key=f"energy:{_day(now)}")


def start():
    if ENABLED:
        import background
        background.loop("energy_coach", EVERY, step)
