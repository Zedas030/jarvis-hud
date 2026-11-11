"""
"What's on tomorrow?" — one short look ahead, also as the last step of "Good night". Local, 0 tokens.

    Your alarm goes off at 6:45. Tomorrow at 9:30 Dentist, then two more appointments. A parcel from Amazon
    is expected. Weather: partly cloudy, 4 to 11 degrees, 20 percent chance of rain.

Sources, each optional (a missing one is left out, nothing is guessed):
  alarm     the next Jarvis alarm within the next 24 hours (scheduler.py)
  calendar  tomorrow's appointments (icloud_calendar.py); the all-day "📦 …" entries of shipments.py
            are the expected parcels
  weather   Open-Meteo for tomorrow (the same place as the morning briefing)

  handle(n)   local router: "whats on tomorrow", "what is happening tomorrow", "preview of tomorrow"
  spoken(now) the sentence
"""
import datetime as dt
import re


def _alarm(now):
    import scheduler
    horizon = now.timestamp() + 24 * 3600
    alarms = [i for i in scheduler.items() if i.get("kind") == "alarm" and now.timestamp() < i["at"] <= horizon]
    if not alarms:
        return ""
    at = dt.datetime.fromtimestamp(alarms[0]["at"])
    return f"Your alarm goes off at {at.hour}:{at.minute:02d}."


def _calendar(day):
    import icloud_calendar
    if not icloud_calendar.configured():
        return "", ""
    evts = icloud_calendar.events(day, 1)
    parcels = [e for e in evts if e.get("allday") and str(e.get("title", "")).startswith("📦")]
    timed = [e for e in evts if not e.get("allday") and e["start"].date() == day]
    allday = [e for e in evts if e.get("allday") and e not in parcels]
    parts = []
    if timed:
        first = timed[0]
        parts.append(f"Tomorrow at {first['start'].hour}:{first['start'].minute:02d} {first.get('title') or 'an appointment'}")
        if len(timed) > 1:
            more = len(timed) - 1
            parts[-1] += ", then " + ("one more appointment" if more == 1 else f"{more} more appointments")
        parts[-1] += "."
    if allday:
        parts.append("All day: " + ", ".join(e.get("title") or "untitled" for e in allday[:2]) + ".")
    if not parts:
        parts.append("There are no appointments in the calendar tomorrow.")
    pkg = ""
    if parcels:
        shops = [re.sub(r"^📦\s*", "", e.get("title", "")).split("·")[0].strip() for e in parcels]
        pkg = ("A parcel" if len(parcels) == 1 else f"{len(parcels)} parcels") + \
            (f" from {', '.join(s for s in shops if s)}" if any(shops) else "") + \
            (" is expected." if len(parcels) == 1 else " are expected.")
    return " ".join(parts), pkg


def _weather(day):
    import briefing
    d = briefing._get("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&timezone=%s"
                      "&forecast_days=2&daily=weather_code,temperature_2m_max,temperature_2m_min,"
                      "precipitation_probability_max" % (briefing.LAT, briefing.LON, briefing.TZ_Q))["daily"]
    i = d["time"].index(day.isoformat()) if day.isoformat() in d["time"] else 1
    return (f"Weather: {briefing.WEATHER_CODES.get(d['weather_code'][i], 'changeable')}, "
            f"{d['temperature_2m_min'][i]:.0f} to {d['temperature_2m_max'][i]:.0f} degrees, "
            f"{d['precipitation_probability_max'][i]} percent chance of rain.")


def spoken(now=None):
    now = now or dt.datetime.now()
    day = (now + dt.timedelta(days=1)).date() if now.hour >= 4 else now.date()   # after midnight "tomorrow" is today
    parts = []
    for fn in (lambda: _alarm(now), lambda: _calendar(day), lambda: _weather(day)):
        try:
            got = fn()
        except Exception:  # noqa: BLE001 — calendar or weather down: say the rest
            continue
        parts += [g for g in (got if isinstance(got, tuple) else (got,)) if g]
    return " ".join(parts) or "I have no data for tomorrow right now, Sir."


_ASK = re.compile(r"\b((whats|what is) (on|planned|happening|coming up|up) (for )?tomorrow|"
                  r"what (do i have|have i got|is there|does tomorrow look like|awaits me)( on| for)? ?(tomorrow)?|"
                  r"(preview|outlook) (of|for) tomorrow|whats tomorrow (look|looking) like|"
                  r"what is tomorrow (look|looking) like|what happens tomorrow)\b")


def handle(n):
    if not _ASK.search(n) or "tomorrow" not in n:
        return None
    return spoken()
