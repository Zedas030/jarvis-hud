"""
Weekly homelab report: Sunday from JARVIS_REVIEW_HOUR (18 h), once per week — spoken and kept
as a note (Erinnerungen/Homelab/Wochenbericht <year>-KW<nn>). Plain rules, 0 tokens.

  outages     homelab_watch's incident log (logs/homelab_events.jsonl): how many, which
  security    the security centre's history: score at the start of the week and now,
              open findings, findings resolved this week, internet drops in the router log
  energy      house consumption this week (JARVIS_HOUSE_WEEK, default sensor.hausverbrauch_diese_woche)
              with its cost, and the solar yield per day as kept by energy_coach.py

Every source is optional; a dead one is left out of the report, never guessed.
"""
import datetime as dt
import os

import background
import events

REVIEW_HOUR = int(os.environ.get("JARVIS_REVIEW_HOUR", "18"))
HOUSE_WEEK = os.environ.get("JARVIS_HOUSE_WEEK", "sensor.hausverbrauch_diese_woche")
ENABLED = os.environ.get("JARVIS_HOMELAB_REVIEW", "1").strip() not in ("0", "false", "no", "off")


def _de(x, digits=1):
    return f"{x:.{digits}f}".replace(".", ",")


def outages(history, start):
    """[text] of the incidents that started in the week (newest first), each kind once with its count."""
    since = start.strftime("%Y-%m-%d %H:%M")
    hits = [h for h in history if h.get("state") == "bad" and h.get("at", "") >= since]
    seen = {}
    for h in hits:
        seen.setdefault(h.get("key") or h["text"], [h["text"], 0])[1] += 1
    return [text + (f" ({n}-mal)" if n > 1 else "") for text, n in seen.values()], len(hits)


def security_part(rep, start):
    if not rep:
        return ""
    hist = [h for h in rep.get("history") or [] if h.get("t", "") >= start.isoformat()]
    counts = rep.get("counts") or {}
    open_ = ", ".join(f"{counts[s]} {s}" for s in ("hoch", "mittel", "niedrig") if counts.get(s)) or "keine"
    text = f"Sicherheit: jetzt {rep.get('score', '?')} von 100 Punkten"
    if hist and hist[0].get("score") != rep.get("score"):
        text += f" (Wochenbeginn {hist[0]['score']})"
    text += f", offene Befunde: {open_}."
    fixed = [r for r in rep.get("resolved") or [] if r.get("at", "") >= start.isoformat()]
    if fixed:
        text += f" Diese Woche erledigt: {len(fixed)}."
    fails = (rep.get("router") or {}).get("inet_fails")
    if fails:
        text += f" Im Protokoll der FRITZ!Box stehen {fails} Internet-Abbrüche."
    return text


def energy_part(house_kwh, price, gen_days, start):
    parts = []
    if house_kwh is not None:
        parts.append(f"Das Haus hat diese Woche {_de(house_kwh)} Kilowattstunden verbraucht, "
                     f"etwa {_de(house_kwh * price, 2)} Euro")
    days = {d: v for d, v in (gen_days or {}).items() if d >= start.strftime("%Y-%m-%d")}
    if days:
        best = max(days, key=days.get)
        parts.append(f"die Sonne hat {_de(sum(days.values()))} Kilowattstunden geliefert, am meisten am "
                     f"{best[8:10]}.{best[5:7]}. ({_de(days[best])})")
    return ("Energie: " + ", ".join(parts) + ".") if parts else ""


def facts(now, history, rep, house_kwh, price, gen_days):
    start = (now - dt.timedelta(days=7)).replace(minute=0, second=0, microsecond=0)
    out, n = outages(history, start)
    lines = [f"Ausfälle: {n}" + (" — " + " ".join(out[:3]) if out else ", die Woche lief ohne Störung.")]
    lines += [p for p in (security_part(rep, start.astimezone()), energy_part(house_kwh, price, gen_days, start)) if p]
    return lines


def review(now=None, speak=True):
    import energy_coach
    import homelab_watch
    import memory
    import power_costs
    import situation
    now = now or dt.datetime.now()
    try:
        rep = situation.security()
    except Exception:  # noqa: BLE001 — the report stays useful without the security centre
        rep = None
    try:
        house = power_costs._num((power_costs._states().get(HOUSE_WEEK) or {}).get("state"))
    except Exception:  # noqa: BLE001
        house = None
    lines = facts(now, homelab_watch.history(200), rep, house, power_costs.PRICE, energy_coach.load().get("gen"))
    spoken = "Sir, der Wochenbericht fürs Heimnetz. " + " ".join(lines)
    week = background.week_key(now.date())
    folder = memory.MEMORY_DIR / "Homelab"
    folder.mkdir(parents=True, exist_ok=True)
    expires = (now.date() + dt.timedelta(days=365)).isoformat()
    (folder / f"Wochenbericht {week}.md").write_text(
        f"---\ntype: homelab\nwichtigkeit: niedrig\nupdated: {now.date().isoformat()}\nablauf: {expires}\n---\n\n"
        f"# Wochenbericht Heimnetz {week}\n\n" + "\n".join(f"- {line}" for line in lines) + "\n", encoding="utf-8")
    if speak:
        events.publish("say", spoken, kind="homelab")
    return spoken


def step(now=None):
    now = now or dt.datetime.now()
    if now.weekday() == 6 and now.hour >= REVIEW_HOUR and background.claim("homelab-review", background.week_key(now.date())):
        review(now)


def start():
    if ENABLED:
        background.loop("homelab_review", 600, step)
