"""
"When do I have to leave?" — travel time to the next appointment with a place.

Car routing over OpenStreetMap: addresses via Nominatim, driving time via the
public OSRM server. Both free, no account, no LLM. OSRM knows no traffic, so
JARVIS_TRAFFIC_FACTOR (default 1.25) pads it, plus JARVIS_COMMUTE_BUFFER_MIN
for parking and walking. Start point: JARVIS_HOME_LAT/LON (else the weather
coordinates). The watcher reminds you when it is time to leave.

    "When do I have to leave?", "How long does it take me to the central station?"
"""
import datetime as dt
import json
import os
import re
import time
import urllib.parse
import urllib.request

HOME = (os.environ.get("JARVIS_HOME_LAT") or os.environ.get("JARVIS_WEATHER_LAT", "50.11"),
        os.environ.get("JARVIS_HOME_LON") or os.environ.get("JARVIS_WEATHER_LON", "8.68"))
FACTOR = float(os.environ.get("JARVIS_TRAFFIC_FACTOR", "1.25"))
BUFFER_MIN = int(os.environ.get("JARVIS_COMMUTE_BUFFER_MIN", "5"))
UA = {"User-Agent": "JARVIS-HUD/1.0 (personal assistant)"}
_GEO, _ROUTE = {}, {}


def _get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=12) as r:
        return json.loads(r.read())


def geocode(place):
    place = " ".join((place or "").split())
    if not place:
        return None
    if place in _GEO:
        return _GEO[place]
    near = f"&viewbox={float(HOME[1]) - 1},{float(HOME[0]) + 0.7},{float(HOME[1]) + 1},{float(HOME[0]) - 0.7}"
    res = _get("https://nominatim.openstreetmap.org/search?format=json&limit=1&accept-language=en&q="
               + urllib.parse.quote(place) + near)
    _GEO[place] = (res[0]["lat"], res[0]["lon"]) if res else None
    return _GEO[place]


def drive_minutes(dest):
    """Driving minutes from home to (lat, lon), traffic factor applied."""
    key = (round(float(dest[0]), 4), round(float(dest[1]), 4))
    hit = _ROUTE.get(key)
    if hit and time.time() - hit[0] < 6 * 3600:
        return hit[1]
    d = _get(f"https://router.project-osrm.org/route/v1/driving/{HOME[1]},{HOME[0]};{dest[1]},{dest[0]}"
             "?overview=false")
    if d.get("code") != "Ok" or not d.get("routes"):
        return None
    mins = round(d["routes"][0]["duration"] / 60 * FACTOR)
    _ROUTE[key] = (time.time(), mins)
    return mins


def leave_at(start, minutes):
    return start - dt.timedelta(minutes=minutes + BUFFER_MIN)


def next_trip(now=None):
    """(event, minutes, leave_time) for the next timed appointment with a place today/tomorrow."""
    import icloud_calendar as cal
    if not cal.configured():
        return None
    now = now or dt.datetime.now()
    for e in cal.events(now.date(), 2):
        if e.get("allday") or not e.get("location") or e["start"] <= now:
            continue
        dest = geocode(e["location"]) or geocode(e["location"].split(",")[0])
        if not dest:
            continue
        mins = drive_minutes(dest)
        if mins is None:
            continue
        return e, mins, leave_at(e["start"], mins)
    return None


def _hm(t):
    return f"{t.hour}:{t.minute:02d}"


_WHEN = re.compile(r"\b(when (do|should|must) i (have to )?(leave|go|set off|head out)|when do i need to (leave|go|set off))\b")
_HOWLONG = re.compile(r"\bhow long (does it take( me)?|do i need|will it take( me)?) to (get to|drive to|go to|reach)\s+(?:the\s+)?(?P<to>.+?)\s*\??$", re.I)


def handle(n, raw=""):
    if _WHEN.search(n):
        try:
            trip = next_trip()
        except Exception:  # noqa: BLE001 — calendar or map service down
            return None
        if not trip:
            return "There is no appointment with a place in the calendar today or tomorrow, Sir. So you can stay."
        e, mins, leave = trip
        day = "" if e["start"].date() == dt.date.today() else "tomorrow "
        late = leave <= dt.datetime.now()
        when = "right away" if late else f"{day}at {_hm(leave)}"
        return (f"For {e.get('title', 'your appointment')} at {_hm(e['start'])} you need about {mins} "
                f"minutes by car. Leave {when}" + (", you are actually running late already." if late else ", Sir."))
    m = _HOWLONG.search(raw or "")
    if m:
        to = m.group("to").strip(" ?.!")
        try:
            dest = geocode(to)
            mins = drive_minutes(dest) if dest else None
        except Exception:  # noqa: BLE001
            return None
        if mins is None:
            return None                     # unknown place: the LLM can ask back
        return f"About {mins} minutes by car to {to}, Sir."
    return None
