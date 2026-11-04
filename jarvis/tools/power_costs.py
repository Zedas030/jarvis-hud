"""
Electricity costs per plug and standby — read-only, 0 tokens.

  Home Assistant  every plug with a monthly meter sensor.X<JARVIS_PLUG_MONTH_SUFFIX>
                  ("this month's consumption" of the Tapo integration) and the house
                  meter (JARVIS_HOUSE_MONTH, default sensor.house_consumption_this_month)
  standby         the lowest draw per day while a plug is switched on, learned by
                  energy_coach.py; plugs in "always_on" (config/energy_coach.json) are
                  meant to run day and night and are left out
  price           JARVIS_POWER_PRICE in €/kWh (default 0.35)

  report(device)  the month so far and the projection for the whole month; with a
                  device word only that plug
  standby()       which plugs draw power while doing nothing, and what that costs a year
"""
import calendar
import datetime as dt
import os
import statistics

PRICE = float(os.environ.get("JARVIS_POWER_PRICE", "0.35").replace(",", "."))
BUDGET = float(os.environ.get("JARVIS_POWER_BUDGET_EUR", "0").replace(",", ".") or 0)   # € a month, 0 = off
BUDGET_FROM = 0.15         # no projection before 15 % of the month has passed: too wobbly
# entity ids as Home Assistant names them in English (a German HA: _der_verbrauch_dieses_monats)
MONTH_SUFFIX = os.environ.get("JARVIS_PLUG_MONTH_SUFFIX", "_this_month_s_consumption")
HOUSE_MONTH = os.environ.get("JARVIS_HOUSE_MONTH", "sensor.house_consumption_this_month")
STANDBY_W = 2.0            # below this a plug is quiet enough
STANDBY_DAYS = 3           # days of data before a standby figure counts


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _eur(x):
    return f"{x:.2f} euros"


def _fmt(x, digits=0):
    return f"{x:.{digits}f}"


def month_factor(now):
    """How much of the month has passed (0..1], for the projection."""
    days = calendar.monthrange(now.year, now.month)[1]
    return min(1.0, max((now.day - 1 + now.hour / 24) / days, 1 / (days * 24)))


def rows(states):
    """[(name, switch, kWh this month)] for every plug with a monthly meter, biggest first."""
    out = []
    for eid, s in states.items():
        if not eid.startswith("switch."):
            continue
        base = eid.split(".", 1)[1]
        kwh = _num((states.get(f"sensor.{base}{MONTH_SUFFIX}") or {}).get("state"))
        if kwh is None:
            continue
        name = ((s.get("attributes") or {}).get("friendly_name") or base).strip()
        out.append((name, eid, kwh))
    return sorted(out, key=lambda r: -r[2])


def _match(word, found):
    import energy_coach
    w = energy_coach._norm(word)
    hits = [r for r in found if w and (w in energy_coach._norm(r[0]) or energy_coach._norm(r[0]) in w)]
    return hits[0] if len(hits) == 1 else None


def say_month(states, now, device=""):
    found, f = rows(states), month_factor(now)
    if device:
        r = _match(device, found)
        if not r:
            names = ", ".join(n for n, _, _ in found) or "none"
            return f"I cannot find a power meter for “{device}”. I know: {names}."
        name, _, kwh = r
        return (f"{name}: so far this month {_fmt(kwh, 1)} kilowatt hours, that is {_eur(kwh * PRICE)}. "
                f"Projected to the whole month about {_eur(kwh / f * PRICE)}.")
    if not found:
        return "I cannot find any sockets with a power meter."
    parts = []
    house = _num((states.get(HOUSE_MONTH) or {}).get("state"))
    if house is not None:
        parts.append(f"The house has used {_fmt(house)} kilowatt hours so far this month, {_eur(house * PRICE)}; "
                     f"projected about {_eur(house / f * PRICE)} for the whole month.")
    top = [r for r in found if r[2] >= 0.1][:4]
    parts.append("The biggest consumers at the sockets: "
                 + ", ".join(f"{n} {_fmt(k, 1)} kWh ({_eur(k * PRICE)})" for n, _, k in top) + ".")
    parts.append(f"Calculated at {_fmt(PRICE * 100)} cents per kilowatt hour.")
    return " ".join(parts)


def standby_rows(mem, cfg, states):
    """[(name, typical lowest watts, € a year)] for plugs that draw power while idle."""
    keep = set(cfg.get("always_on") or [])
    out = []
    for eid, d in (mem.get("devices") or {}).items():
        low = list((d.get("min_w") or {}).values())
        if eid in keep or len(low) < STANDBY_DAYS:
            continue
        w = statistics.median(low)
        if w >= STANDBY_W:
            out.append((d["name"], w, w * 24 * 365 / 1000 * PRICE))
    return sorted(out, key=lambda r: -r[1])


def say_standby(mem, cfg, states):
    found = standby_rows(mem, cfg, states)
    days = max((len((d.get("min_w") or {})) for d in (mem.get("devices") or {}).values()), default=0)
    if days < STANDBY_DAYS:
        return (f"For standby I need at least {STANDBY_DAYS} days of readings, so far I have {days}. "
                "I keep collecting.")
    if not found:
        return "No device draws noticeable power while idle — nothing to do."
    rows_ = ", ".join(f"{n} with about {_fmt(w)} watts ({_eur(c)} a year)" for n, w, c in found[:4])
    return f"Drawing power while idle: {rows_}. Switching the socket off saves exactly that."


def budget():
    """The monthly budget in €: JARVIS_POWER_BUDGET_EUR, else config/energy_coach.json → budget_eur, else 0."""
    if BUDGET:
        return BUDGET
    try:
        import energy_coach
        return float(energy_coach.config().get("budget_eur") or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def budget_status(states, now, limit=None):
    """dict(budget, so_far, projected, over, top=(name, €)) or None without a budget or house meter."""
    limit = budget() if limit is None else limit
    house = _num((states.get(HOUSE_MONTH) or {}).get("state"))
    if not limit or house is None:
        return None
    f = month_factor(now)
    projected = house / f * PRICE
    found = rows(states)
    top = (found[0][0], found[0][2] / f * PRICE) if found else None
    return dict(budget=limit, so_far=house * PRICE, projected=projected, over=projected - limit, top=top,
                early=f < BUDGET_FROM)


def say_budget(states, now):
    b = budget_status(states, now)
    if b is None:
        return ("No power budget is set (JARVIS_POWER_BUDGET_EUR or budget_eur in config/energy_coach.json)"
                if not budget() else "Without a house meter I cannot check the budget.")
    if b["early"]:
        return f"The month is still young: so far {_eur(b['so_far'])}, budget {_eur(b['budget'])}. I will risk a projection from the fifth day on."
    head = f"Projected {_eur(b['projected'])} against a budget of {_eur(b['budget'])}"
    if b["over"] > 1:
        tail = f" — about {_eur(b['over'])} over."
        if b["top"]:
            tail += f" Biggest cause at the sockets: {b['top'][0]} with about {_eur(b['top'][1])} a month."
        return head + tail
    return head + f" — within budget, {_eur(-b['over'])} to spare."


def budget_notice(states, now=None):
    """The once-a-day notice when the projection is over the budget, else None (watchers.py)."""
    b = budget_status(states, now or dt.datetime.now())
    if not b or b["early"] or b["over"] <= 1:
        return None
    top = f" Biggest item: {b['top'][0]}." if b["top"] else ""
    return (f"Sir, electricity is over budget: projected {_eur(b['projected'])} instead of {_eur(b['budget'])} "
            f"this month.{top}")


def _states():
    import briefing
    return {s["entity_id"]: s for s in briefing._ha_states()}


def report(device="", standby=False, budget=False):
    states = _states()
    if not states:
        return "Without Home Assistant I cannot read the power costs right now."
    if standby:
        import energy_coach
        return say_standby(energy_coach.load(), energy_coach.config(), states)
    if budget:
        return say_budget(states, dt.datetime.now())
    text = say_month(states, dt.datetime.now(), device)
    if not device and budget_status(states, dt.datetime.now()):
        text += " " + say_budget(states, dt.datetime.now())
    return text
