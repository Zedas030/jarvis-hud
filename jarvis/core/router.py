"""
Intent router — decides per message, before any LLM is involved:

  local   simple commands answered by code alone (0 tokens): routines, timers,
          alarms and reminders, shopping/to-do lists, switch devices, music keys,
          time/date, weather/golf, sunrise/sunset, solar, NAS, Pi.
  llm     everything else, with the smallest fitting setup: which tool groups
          the request needs and whether Haiku is enough or Sonnet is needed.

Rules are plain keyword patterns, so they cost nothing and are easy to tune.
When a local handler is unsure (ambiguous device, protected socket, data not
reachable) it returns None and the request goes to the LLM instead.
"""
import datetime as dt
import json
import os
import random
import re
import threading
import time
import urllib.request

MODEL_SIMPLE = os.environ.get("JARVIS_MODEL_SIMPLE", "haiku").strip()
MODEL_SMART = os.environ.get("JARVIS_MODEL_SMART", "sonnet").strip() or os.environ.get("JARVIS_MODEL", "sonnet")
LOCAL_ENABLED = os.environ.get("JARVIS_LOCAL_ROUTER", "1").strip() not in ("0", "false", "no", "off")

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December")


def _norm(text):
    # apostrophes go first, so "what's" and "don't" become one word each ("whats", "dont")
    text = re.sub(r"['’]", "", (text or "").lower())
    return " " + re.sub(r"[^\w%]+", " ", text).strip() + " "


def _has(n, *words):
    return any(re.search(r"\b" + w, n) for w in words)


def _sir():
    return random.choice(("Sir", "Sir", "Sir", ""))


def _say(*options):
    s = random.choice(options).strip()
    return s.replace(", .", ".").replace(" ,", ",")


# ── tool groups + model choice for the LLM path ──────────────
GROUP_RULES = [
    ("home", r"\b(light|lamp|led\b|socket|plug\b|outlet|switch|fridge|dishwasher|washing machine|printer|3d|filament|ams\b|bambu|tent|bed\b|heating|heater|thermostat|temperature in|degrees in|air purifier|"
             r"fan\b|scene|living ?room|bedroom|gaming|kitchen|boiler|smart ?home|home ?assistant|"
             r"solar|battery|storage|power|electricity|watt|surplus|energy ?coach|sensor|air\b|humid|echo|fire ?tv|television|tv\b)"),
    ("mail", r"\b(mail|e-mail|email|inbox|mailbox|newsletter|message from|wrote|written|read)"),
    ("calendar", r"\b(appointment|calendar|put .{0,30}in|add .{0,30}to (my|the) calendar|remind|date with|meeting|when (is|do i have))"),
    # "What else is on today?" got memory only, and Jarvis said he could not see the calendar
    ("calendar", r"\b(what\w*( else)?( is)? on\b|whats up (today|tomorrow)|do i have .{0,25}\b(planned|on|free|time)\b|am i free|"
                 r"plans? for (today|tomorrow|the week|\w+day)|day plan|week plan|schedule|whats happening (today|tomorrow|on \w+))"),
    # bookings in the inbox that belong in the calendar: both tool groups at once
    ("mail", r"\b(booking|bookings|booked|reserv|confirmation|tee ?time|start time|start times)"),
    ("mail", r"\b(parcel\w*|package\w*|deliver\w*|shipment\w*|shipping|tracking|ordered|order\b)"),
    ("calendar", r"\b(booking|bookings|booked|reserv|confirmation|tee ?time|start time|start times)"),
    ("calendar", r"\b(undo|take .{0,20}out again|remove .{0,20}again|appointments? .{0,20}(delet|remov))"),
    ("systems", r"\b(pi\b|raspi|raspberry|nas\b|synology|network|wifi|wi-fi|wlan|router|fritz|device|server|pi-hole|pihole|ip\b|"
                r"internet|dns|backup|graphics card|gpu|vram|cpu|processor|memory usage|ram\b|hard ?drive|disk|"
                r"storage space|drive\b|went down|outage|forecast|when full|be full)"),
    ("homelab", r"\b(pi-?hole|ads\b|advertising|block|grafana|prometheus|homelab|internet|speed|bandwidth|mbit|"
                r"download|upload|data volume|data (today|used)|wifi|wlan|mesh|load|cpu|history|peak|"
                r"highest|the most)"),
    ("homelab", r"\b(status report|situation report|uptime|kuma|security cent|security finding|security situation|zero export|"
                r"energy ?loop)"),
    ("homelab", r"\b(fails|failed|goes down|crashes|depends|dependenc|single point|spof|blast.?radius|what fails)"),
    ("homelab", r"\b(restore[ -]?probe|restore test|backup[ -]?test|update[ -]?airlock|container[ -]?update|"
                r"detective|unusual|placement advisor|zigbee|homelab tools?|pi tools?)"),
    ("health", r"\b(sleep|slept|steps|pulse|heart rate|garmin|body ?battery|stress|fitness|training|run\b|running|"
               r"ate\b|eaten|dinner|alcohol|beer\b|wine\b|review|weekly review)"),
    ("music", r"\b(music|spotify|song|track|playlist|album|play\b)"),
    ("weather", r"\b(weather|forecast|sun\b|sunny|sunshine|uv\b|uv-|rain|raining|umbrella|snow|snowing|thunder|"
                r"storm|wind\b|windy|fog|frost|icy|temperature(?! in)|degrees outside|how warm|how cold|"
                r"get light|gets light|get dark|gets dark|how long (is|does it stay) .{0,25}\blight|daylight)"),
    ("web", r"\b(search|research|google|look up|internet|online|website|web page|page\b|open\b|news|latest|current|"
            r"find out|figure out|check (online|the web)|look (it )?up|"
            r"price|cost|who is|who was|what is|how much is|how much does|football|premier league|match\b|game\b|result|"
            r"exchange rate|stock|share price)"),
    ("pc", r"\b(program|start\b|launch|command|powershell|command line|terminal|file|folder|install|screen|screenshot|"
           r"window|desktop|computer|pc\b)"),
    ("timers", r"\b(timer|alarm|wake|remind|snooze|countdown)"),
    ("lists", r"\b(shopping|to-?do|task list|errand)"),
    ("routines", r"\b(routine|when i say|whenever i say|as soon as i say|every time i say)"),
    ("proposals", r"\b(suggest|proposal|rule|accept|adopt|learning log|learned|learnt|wording|draft|drafts)"),
    ("memory_admin", r"\b(forget|delete|why did you|what do you know about me|contradict|duplicate|"
                     r"check (my|your) memory|clean up|tidy up|"
                     r"what have you (stored|saved)|stored|saved|correct|no longer true|not true any ?more|export|backup)"),
    ("windows", r"\b(window|side by side|arrange|workspace|monitor \d|left side|right side|left half|right half)"),
    ("files", r"\b(file|files|document|documents|folder|word ?file|text file|downloads|manual|instructions|"
              r"handbook|user guide|in my (papers|notes|documents)|pdf)"),
    ("clipboard", r"\b(clipboard|copied)"),
    ("screen", r"\b(screen|what do you see|error message|this window|on my monitor|what does it say)"),
    ("scripts", r"\b(script)"),
    ("mail", r"\b(draft|mail to|email to|write .{0,40}(mail|message)|reply to)"),
    ("dayprep", r"\b(what do i need to know (for|about)|prepare? .{0,20}(for|appointment|meeting)|preparation|"
                r"what does (my|the) (day|morning) look like|whats on (tomorrow|today))"),
    ("research", r"\b(research|thorough|sources|fact ?check|let me know when|tell me when|watch|keep an eye|"
                 r"keep me (posted|updated)|whats new)"),
    ("plans", r"\b(plan .{0,30}(over|until|for the)|over (several|two|three|\d+) (days|weeks)|"
              r"my plan|step .{0,20}done|tick off|check off|project plan)"),
    ("phone", r"\b(to my phone|on my phone|to the phone|push( notification)?|send .{0,30}phone)"),
    ("tasks", r"\b(check\w*|diagnos|report|inspection|audit)"),
    ("updates", r"\b(update|upgrade|firmware|new version|up to date|outdated)"),
    ("audit", r"\b(what did you (do|switch|run)( today| yesterday)?|what have you (done|switched)|log of your|audit|"
              r"what did you execute)"),
    # looking back: the conversation log, Garmin days, notices and incidents joined by date
    ("episodes", r"\b(last week|the week before|recently|the other day|lately|do you remember|back then|\w+ (days|weeks) ago|"
                 r"when i .{0,40}\b(was|had|lay)\b|what did we talk about|what did i ask you|"
                 r"how was .{0,30}(sleep|consumption|stress|pulse|night|day) .{0,30}\b(when|on|last|ago)\b)"),
    ("paperless", r"\b(paperless|archive|papers|invoice|bill\b|contract|insurance|policy|cancellation|notice|tax|"
                  r"scanned|what do i pay for|when does .{0,30}(contract|insurance|subscription) (end|run out|expire)|contract number|customer number)"),
    ("unifi", r"\b(unifi|who is .{0,20}(on|in) (my |the )?(wifi|wlan|network)|which devices .{0,20}(on|in) (the |my )?(wifi|wlan|network)|access ?point|"
              r"wifi[- ]?clients?|wlan[- ]?clients?|signal .{0,20}(wifi|device))"),
    ("media", r"\b(jellyfin|plex|media library|series|show\b|movie|film|episode|season|keep watching|continue watching|"
              r"last (watched|seen)|library|whats new to watch)"),
]
# A short yes/no while a proposal is open: bring the proposal tools along.
PROPOSAL_REPLY = re.compile(r"^\W*(yes|yeah|yep|sure|gladly|ok(ay)?|do it|go ahead|agreed|fine|no|nope|rather not|"
                            r"not needed|later)\b", re.I)
SMART_HINTS = re.compile(
    r"\b(explain|why|analy[sz]|compare|plan|write|phrase|summar|research|think about|consider|help me|"
    r"code|program|calculate|strategy|optimi[sz]|evaluate|assess|what do you think|in detail|detailed|exactly)", re.I)


def llm_route(message, ha_full=False):
    n = _norm(message)
    groups = ["memory"]                     # remembering works in every turn
    for name, pat in GROUP_RULES:
        if re.search(pat, n):
            groups.append(name)
    if ha_full:
        groups.append("ha_full")
    groups = list(dict.fromkeys(groups))       # one group can match several patterns
    if "proposals" not in groups and PROPOSAL_REPLY.search(n):
        import proposals
        if proposals.pending():
            groups.append("proposals")
    smart = (bool(SMART_HINTS.search(message)) or len(message) > 220
             or any(g in groups for g in ("web", "pc", "ha_full")) or len(groups) > 3)
    return {"kind": "llm", "groups": groups, "model": MODEL_SMART if smart else MODEL_SIMPLE,
            "reason": "smart" if smart else "simple"}


# ── local handlers ───────────────────────────────────────────
def _dash(key, ttl, fn):
    """Dashboard data: the HUD keeps this cache warm; if it is still empty, fetch now."""
    import dashboard
    val = dashboard._cached(key, ttl, fn)
    return val if val is not None else fn()


def _time(n):
    if not _has(n, "what time", "time is it", "whats the time", "the time please", "tell me the time", "current time"):
        return None
    now = dt.datetime.now()
    return _say(f"It is {now:%H:%M}, {_sir()}.", f"{now:%H:%M}, {_sir()}.")


def _date(n):
    if not (_has(n, "what day", "which day", "what date", "whats the date", "todays date", "date") and not _has(n, "appointment")):
        return None
    d = dt.date.today()
    return f"Today is {WEEKDAYS[d.weekday()]}, {MONTHS[d.month - 1]} {d.day}, {_sir()}."


_WX = {}


def _forecast():
    if _WX.get("at", 0) > time.time() - 900:
        return _WX["d"]
    import briefing
    d = briefing._get("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&timezone=%s"
                      "&forecast_days=3&current=temperature_2m,weather_code"
                      "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
                      "precipitation_probability_max,wind_speed_10m_max,sunrise,sunset"
                      % (briefing.LAT, briefing.LON, briefing.TZ_Q))
    _WX.update(at=time.time(), d=d)
    return d


def _day_index(n):
    """0 today, 1 tomorrow, 2 the day after tomorrow."""
    return 2 if _has(n, "day after tomorrow") else 1 if _has(n, "tomorrow") else 0


def _sun(n):
    rise = _has(n, "sunrise", "sun rise", "sun come up", "sun comes up", "get light", "gets light")
    sets = _has(n, "sunset", "sun set", "sun go down", "sun goes down", "get dark", "gets dark")
    both = _has(n, "sun times", r"how long (is|does it stay) .{0,25}\blight", "daylight")
    if not (rise or sets or both) or _has(n, "week", "weekend", "next") or _has(n, "golf"):
        return None
    try:
        day = _forecast()["daily"]
    except Exception:  # noqa: BLE001
        return None
    i = _day_index(n)
    up, down = day["sunrise"][i][-5:], day["sunset"][i][-5:]
    when = ("today", "tomorrow", "the day after tomorrow")[i]
    if both or (rise and sets):
        a, b = (dt.datetime.fromisoformat(day[k][i]) for k in ("sunrise", "sunset"))
        h, m = divmod(round((b - a).total_seconds() / 60), 60)
        return f"The sun rises {when} at {up} and sets at {down}, {h} hours {m} minutes of daylight, {_sir()}."
    if rise:
        return _say(f"Sunrise {when} at {up}, {_sir()}.", f"The sun rises {when} at {up}, {_sir()}.")
    return _say(f"Sunset {when} at {down}, {_sir()}.", f"The sun sets {when} at {down}, {_sir()}.")


def _weather(n):
    # Golf only as a weather question ("Can I play golf tomorrow?"); "I have a golf lesson tomorrow,
    # what do you recommend?" or a question about new clubs is a conversation for the LLM.
    golf = _has(n, "golf") and len(n.split()) <= 14 and not _has(n, "coach", "lesson", "club", "recommend",
                                                                   "think", "tip", "technique", "swing", "handicap")
    if not (_has(n, "weather", "raining", "rain", "temperature outside", "how warm", "how cold", "sun shining", "sunny")
            or (golf and (_has(n, "can i", "good for", "weather", "worth", "ok", "okay")
                          or (_has(n, "go", "play") and len(n.split()) <= 7)
                          or (_has(n, "today", "tomorrow") and len(n.split()) <= 4)))):
        return None
    if _has(n, "week", "weekend", "day after tomorrow", "next"):
        return None                              # longer outlook: let the LLM do it
    import briefing
    import dashboard
    try:
        d = _forecast()
    except Exception:  # noqa: BLE001
        return None
    i = 1 if _has(n, "tomorrow") else 0
    day = d["daily"]
    sky = briefing.WEATHER_CODES.get(day["weather_code"][i], "changeable")
    tmax, tmin = round(day["temperature_2m_max"][i]), round(day["temperature_2m_min"][i])
    rain, wind = day["precipitation_probability_max"][i] or 0, round(day["wind_speed_10m_max"][i] or 0)
    when = "Tomorrow" if i else "Today"
    now = ""
    if i == 0 and d.get("current", {}).get("temperature_2m") is not None:
        now = f" Right now it is {round(d['current']['temperature_2m'])} degrees."
    text = (f"{when} {sky}, {tmin} to {tmax} degrees, {rain} percent chance of rain, wind up to {wind} km/h.{now}")
    if golf:
        try:
            w = _dash("weather", 1800, dashboard._weather)
            verdict = {"yes": "it is fine", "with jacket": "it works, but bring a jacket", "no": "better not"}
            v = verdict.get(w.get("golf"), "hard to say")
            gday = w.get("golf_day", "today")
            if gday == "today":
                text += f" Golf today: {v}, {_sir()}."
            elif i == 0:
                text += f" There is not enough daylight left for a round today. {gday.capitalize()}: {v}, {_sir()}."
            else:
                text += f" Golf {gday}: {v}, {_sir()}."
        except Exception:  # noqa: BLE001
            pass
    elif rain >= 60:
        text += f" Take an umbrella, {_sir()}."
    return text.replace(" ,", ",").replace(", .", ".")


def _solar(n):
    if not (_has(n, "solar", "photovoltaic", "pv\\b", "balcony power", "battery", "storage")
            # thresholds and "when" are questions about the setup, not the current reading
            and not _has(n, "body battery", "phone", "why", "explain", "history", "yesterday", "week", "month", "automati",
                         "when", "from what", "at what", "protection", "limit", "threshold", "stop", "setting", "wrong",
                         "double")):
        return None
    import dashboard
    try:
        h = _dash("house", 30, dashboard._house)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(h, dict) or h.get("error"):
        return None
    parts = [f"The solar system is delivering {h.get('solar_w', 0)} watts right now"]
    if h.get("soc") is not None:
        parts.append(f"the battery is at {round(h['soc'])} percent")
    if h.get("gen_kwh") is not None:
        parts.append(f"generated today: {h['gen_kwh']} kilowatt hours")
    return ", ".join(parts) + f", {_sir()}."


# Updates are a question for Claude with the update tools, never the quick status line
# ("Update the NAS" used to get "The NAS: all fine …").
_UPDATE_WORDS = ("update", "upgrade", "latest version", "version", "firmware", "dsm", "software",
                 "install", "packages?\\b", "apps?\\b")


def _nas(n):
    if not (_has(n, "nas\\b", "synology") and not _has(n, "file", "folder", "share", "backup", "why", *_UPDATE_WORDS)):
        return None
    import dashboard
    try:
        s = _dash("nas", 300, dashboard._nas)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(s, dict) or s.get("error"):
        return None
    ok = "all fine" if s.get("ok") else "at least one disk or volume reports a problem"
    used = f"{s.get('used_pct')} percent used ({s.get('used_tb')} of " \
           f"{s.get('total_tb')} terabytes)" if s.get("used_pct") is not None else ""
    temp = f", {round(s['temp'])} degrees" if s.get("temp") is not None else ""
    return f"The NAS: {ok}{temp}, {used}, {_sir()}.".replace(", ,", ",")


def _pi(n):
    if not (_has(n, "pi\\b", "raspi", "raspberry") and _has(n, "status", "how is", "hows", "running", "all ok", "temperature",
                                                             "state", "doing", "healthy", "fit")
            and not _has(n, *_UPDATE_WORDS)):
        return None
    import dashboard
    try:
        s = _dash("pi", 120, dashboard._pi)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(s, dict) or s.get("error"):
        return None
    cont = f", {s.get('up')} of {s.get('containers')} containers running" if s.get("containers") else ""
    en = {"week": ("week", "weeks"), "day": ("day", "days"), "hour": ("hour", "hours")}
    parts = [f"{num} {en[unit][0] if num == '1' else en[unit][1]}"
             for num, unit in re.findall(r"(\d+) (week|day|hour)", s.get("uptime") or "")[:2]]
    since = " and ".join(parts) or "a while"
    disk = f", storage {round(s['disk'])} percent full" if s.get("disk") is not None else ""
    return f"The Pi has been running for {since}, {round(s.get('temp') or 0)} degrees{disk}{cont}, {_sir()}."


MUSIC = [
    (r"\b(pause|stop|halt|stop the music|music off)", "pause", "Paused"),
    (r"\b(keep playing|resume|continue|music on|play music|play)\b", "play", "Playing"),
    (r"\b(next|skip)", "next", "Next track"),
    (r"\b(previous|go back|last (song|track))", "previous", "Previous track"),
    (r"\b(louder|volume up|turn it up)", "volume_up", "Louder"),
    (r"\b(quieter|softer|volume down|turn it down)", "volume_down", "Quieter"),
]


def _music_target(n):
    """The room the music should go to, when a speaker room is named: "move the music from the bedroom
    to the living room", "play the bedroom music in the living room". The room after "from / from the /
    out of the" is where it plays now, so the other one is the target; with one room named, that is the target.
    Said as a situation, "the music is playing in the bedroom and I'm sitting in the living room": the room where
    it is "playing" is the source, the room the user is in ("I'm sitting / I am / here in the …") the target."""
    import speakers
    try:
        rooms = speakers.rooms()
    except Exception:  # noqa: BLE001
        return None
    found = sorted((m.start(), r) for r in rooms for m in re.finditer(r"\b" + re.escape(_norm(r).strip()) + r"\b", n))
    if not found:
        return None
    mine = [r for pos, r in found
            if re.search(r"\b(i|im|i am|sitting|sit|standing|stand|lying|here)\b[^.,]{0,20}\b(in the|in) $", n[:pos])]
    if mine:
        return mine[0]
    targets = [r for pos, r in found
               if not re.search(r"\b(from|from the|out of|out of the|off the"
                                r"|(playing|plays|played|was playing|coming|comes)( \w+)? (in the|in|on the)) $", n[:pos])]
    return targets[0] if targets else found[-1][1]


def _music(n):
    if not _has(n, "music", "spotify", "song", "track") and not re.search(
            r"\b(louder|quieter|softer)\b", n) or _has(n, "jarvis.*(louder|quieter|softer)", "voice", "speak"):
        return None
    if _has(n, "search", "play .* by", "playlist", "album"):
        return None                               # searching needs the LLM to pick the query
    target = _music_target(n)
    if target:
        import jarvis_mcp
        try:
            text = jarvis_mcp._music({"action": "transfer", "room": target})
        except Exception:  # noqa: BLE001
            return None
        return f"{text[:-1]}, {_sir()}." if _sir() and text.endswith(".") else text
    for pat, action, text in MUSIC:
        if re.search(pat, n):
            import jarvis_mcp
            try:
                jarvis_mcp._music({"action": action})
            except Exception:  # noqa: BLE001
                return None
            return f"{text}, {_sir()}." if _sir() else f"{text}."
    return None


# Devices: friendly names from Home Assistant. Config switches (child lock,
# status LEDs of sockets, auto-update, FRITZ!Box internet access) are never
# targets; sockets that keep things alive are never switched off without
# Claude (and his confirmation rules). A *light* called "LED Living Room" is a
# real lamp, though — only switches named "... LED" are indicator lights.
_DEV = {"at": 0.0, "list": []}
_CONFIG_WORDS = re.compile(r"child lock|automatic|display|update|shutdown|shuffle|repeat|"
                           r"do not disturb|mute|internet access|autoplay|crossfade|loudness|"
                           r"grouping", re.I)    # the last four: Sonos settings HA shows as switches
_INDICATOR_LED = re.compile(r"\bled\b", re.I)
PROTECTED_OFF = re.compile(r"router|\bpc\b|raspi|\bpi\b|fridge|smart hub|nas|fritz|wi-?fi|wlan", re.I)
_DOMAIN_WORDS = {"light": "light", "lights": "light", "lamp": "light", "lamps": "light", "led": "light",
                 "socket": "switch", "plug": "switch", "outlet": "switch", "fan": "fan", "scene": "scene"}
_FILLER = {"the", "a", "an", "in", "at", "on", "off", "of", "to", "up", "down", "please", "jarvis", "turn", "switch",
           "power", "set", "put", "make", "and", "sir", "can", "could", "you", "all", "my", "activate", "deactivate",
           "enable", "disable", "percent", "%", "light", "lamp", "socket", "plug", "outlet", "lights", "lamps", "now"}


def _aliases():
    """Extra words per entity from config/home.json → "device_aliases": the lamp HA calls "LED" is also
    "living room", "light" or "socket" to the user. Read with every device refresh, no restart needed."""
    import jarvis_profile
    return jarvis_profile._load_home().get("device_aliases") or {}


def _devices():
    if _DEV["at"] > time.time() - 300:
        return _DEV["list"]
    import briefing
    aliases = _aliases()
    out = []
    for s in briefing._ha_states():
        eid = s["entity_id"]
        dom = eid.split(".")[0]
        name = (s.get("attributes", {}).get("friendly_name") or eid).strip()
        if dom not in ("light", "switch", "fan", "scene") or _CONFIG_WORDS.search(name):
            continue
        if dom == "switch" and (_INDICATOR_LED.search(name) or eid.endswith("_led")):
            continue                               # the status LED of a socket, not a lamp
        if s.get("state") == "unavailable":
            continue                               # a dead lamp must not tie with the live one
        base = set(_norm(name).split())
        alias = set(_norm(" ".join(aliases.get(eid) or [])).split())
        out.append({"eid": eid, "dom": dom, "name": name, "base": base, "words": base | alias, "alias": alias})
    _DEV.update(at=time.time(), list=out)
    return out


QUESTION = re.compile(r"^\s*(is|are|does|do|did|has|have|how|what|whats|which|why|"
                      r"status|state|is there|are there|do you know)\b")


def _match_devices(n, hint=None, devs=None, prefer=None):
    """The devices meant by n. `prefer` (entity ids of the room the user is in) breaks a tie before
    the exact-name rule does: "TV on" in the bedroom is the bedroom TV, not the living-room one."""
    said = set(n.split())
    words = {w for w in said if w not in _FILLER and len(w) > 1}
    devs = _devices() if devs is None else devs
    if not any(d["words"] & words for d in devs):
        words = said                               # only generic words ("light off"): an alias may claim them
        devs = [d for d in devs if d["alias"] & said]
    best, score = [], 0
    for d in devs:
        if hint and d["dom"] != hint and not (hint == "switch" and d["dom"] == "fan") \
                and not d["alias"] & said & _DOMAIN_WORDS.keys():
            continue                               # "socket" for a lamp the user calls a socket is fine
        sc = len(d["words"] & words)
        if not sc:
            continue
        if sc > score:
            best, score = [d], sc
        elif sc == score:
            best.append(d)
    if len(best) > 1 and prefer:
        here = [d for d in best if d["eid"] in prefer]
        if len(here) > 1:                          # "TV Bedroom" is the TV here, "Bedroom TV Autoplay" not
            plain = [d for d in here if {w for w in d["base"] if not _ROOM_WORD.fullmatch(w)} <= words]
            here = plain or here
        if here:
            best = here
    if len(best) > 1:
        exact = [d for d in best if d["base"] <= words]   # "TV" beats "TV Bedroom" for "turn the tv on"
        if len(exact) == 1:
            best = exact
    return best


def _hint(n):
    return next((d for w, d in _DOMAIN_WORDS.items() if re.search(r"\b" + w + r"\b", n)), None)


def _home_state(n):
    """'Is the air purifier on?' — answered from Home Assistant, nothing is switched."""
    if not (QUESTION.search(n) and re.search(r"\b(on|off|switched on|switched off|running)\s*$", n.strip())):
        return None
    hint = _hint(n)
    try:
        best = _match_devices(n, hint)
        if not best or len(best) > 3:
            return None
        import briefing
        states = {s["entity_id"]: s["state"] for s in briefing._ha_states()}
    except Exception:  # noqa: BLE001
        return None
    word = {"on": "on", "off": "off", "playing": "on", "idle": "off"}
    parts = [f"{d['name']} is {word.get(states.get(d['eid']), states.get(d['eid'], 'unknown'))}" for d in best]
    return " and ".join(parts) + f", {_sir()}."


_ON = r"\b(?:on|activate\w*|enable)\s*$|\b(?:turn|switch|power)\w*\b.*\bon\b|^\s*(?:please\s+)?(?:activate|enable)\b"
_OFF = r"\b(?:off|deactivate\w*|disable)\s*$|\b(?:turn|switch|power|shut)\w*\b.*\boff\b|^\s*(?:please\s+)?(?:deactivate|disable)\b"


# ── the room a command without a room belongs to (room_scope.py) ──
# "light on", "warmer", "TV on" name no room: they mean the room the presence sensors see the user in.
# A room word in the sentence always wins. When the sensors disagree (walking, a radar ghost) and the
# answer would change what is switched, Jarvis asks "Living room or Bedroom?" and the reply runs
# the command for that room.
ROOM_ASK_S = 60
_ROOMQ = {"n": "", "rooms": [], "handler": None, "at": 0.0}


def _names_room(n):
    import room_scope
    names = [_norm(r).strip() for r in room_scope.rooms()]
    return bool(re.search(r"\b(" + _ROOM_WORD.pattern + r")\b", n)) or any(
        re.search(r"\b" + re.escape(r) + r"\b", n) for r in names if r)


def _scope(n, room=None):
    """None when the room plays no part (feature off, a room named, HA unreachable); else the
    room_scope.current() dict, with `room` forced when the user just answered the room question."""
    try:
        import room_scope
        if not room_scope.enabled():
            return None
        if room:
            return {"room": room, "sure": True, "candidates": [room], "status": "ok"}
        if _names_room(n):
            return None
        cur = room_scope.current()
    except Exception:  # noqa: BLE001 — never a reason to fail a command
        return None
    return cur if cur["status"] == "ok" and cur["candidates"] else None


def _ask_room(n, rooms, handler):
    _ROOMQ.update(n=n, rooms=list(rooms), handler=handler, at=time.time())
    return f"{', '.join(rooms[:-1])} or {rooms[-1]}, {_sir()}?".replace(", ?", "?")


def _room_answer(n):
    """The reply to "Living room or Bedroom?": a room name, maybe with "in the"."""
    if time.time() - _ROOMQ["at"] > ROOM_ASK_S or not _ROOMQ["rooms"]:
        return None
    s = re.sub(r"\b(please|jarvis|sir|in the|in|i am|im|i am in|here|the)\b", " ", n)
    s = " ".join(s.split())
    picked = [r for r in _ROOMQ["rooms"] if _norm(r).strip() == s or (s and s in _norm(r).split())]
    if len(picked) != 1:
        if re.fullmatch(r"(no|nothing|never mind|forget it|cancel|leave it|doesnt matter)", s):
            _ROOMQ.update(n="", rooms=[], handler=None, at=0.0)
            return f"All right, never mind, {_sir()}.".replace(", .", ".")
        return None
    asked, handler = _ROOMQ["n"], _ROOMQ["handler"]
    _ROOMQ.update(n="", rooms=[], handler=None, at=0.0)
    return handler(asked, room=picked[0])


CLIMATE_STEP = float(os.environ.get("JARVIS_CLIMATE_STEP", "1"))
CLIMATE_DEFAULT = float(os.environ.get("JARVIS_CLIMATE_DEFAULT", "20"))
_RELATIVE = re.compile(r"\s*(?:(?:make|make it|it|please|a bit|a little|slightly|bit|little|some|the|heating|"
                       r"the heating|set|turn|turn it|a)\s+)*(warmer|colder|cooler)"
                       r"(?:\s+(?:please|now))*(?:\s+(?:in the|in)\s+[a-z ]+?)?\s*")


def _climate_rooms(groups, n, room=None):
    """[(name, entities)] — the room named in n, or the one the user is in (room_scope)."""
    rooms = []
    for name, entities in groups.items():
        if not isinstance(entities, list):
            continue
        alias = _norm(str(name)).strip()
        pattern = r"(?<!\w)" + re.escape(alias) + r"(?:\s*(?:heating|heater|radiators?|thermostats?))?(?!\w)"
        if (room and alias == _norm(room).strip()) or (not room and alias and re.search(pattern, n)):
            rooms.append((str(name), [str(eid) for eid in entities if str(eid).startswith("climate.")]))
    return rooms


def _climate_step(name, entities, warmer):
    """"warmer" / "colder": one CLIMATE_STEP from the current target. An off thermostat (or one at its
    5-7 degree frost guard) goes on at CLIMATE_DEFAULT for "warmer"; "colder" leaves it off."""
    import jarvis_mcp
    targets, off = [], True
    for eid in entities:
        st = jarvis_mcp._ha(f"/api/states/{eid}") or {}
        t = (st.get("attributes") or {}).get("temperature")
        if st.get("state") not in ("off", "unavailable") and isinstance(t, (int, float)) and t > 7:
            targets.append(float(t))
            off = False
    if off and not warmer:
        return f"The heating {_in(name)} is already off, {_sir()}.".replace(", .", ".")
    goal = CLIMATE_DEFAULT if off else max(targets) + (CLIMATE_STEP if warmer else -CLIMATE_STEP)
    goal = max(5.0, min(30.0, round(goal * 2) / 2))
    for eid in entities:
        if off:
            res = jarvis_mcp._home("home_control", {"entity_id": eid, "service": "set_hvac_mode",
                                                    "data": {"hvac_mode": "heat"}})
            if not res or not res.startswith("Done"):
                return f"{name}: {res or 'No response from Home Assistant.'}"
        res = jarvis_mcp._home("home_control", {"entity_id": eid, "service": "set_temperature",
                                                "data": {"temperature": goal}})
        if not res or not res.startswith("Done"):
            return f"{name}: {res or 'No response from Home Assistant.'}"
    return f"Heating {_in(name)} {'on at' if off else 'set to'} {goal:g} degrees, {_sir()}.".replace(", .", ".")


def _in(room):
    return "in the " + room


def _climate(n, room=None):
    """Handle clear room-thermostat commands locally so the read-only HA MCP is never used for them."""
    if QUESTION.search(n) or not re.search(r"heat\w*|thermostat|temperature|warm|degree|colder|cooler", n):
        return None
    raw = _raw()
    temperature = re.search(r"\bto\s+(\d{1,2}(?:[.,]\d)?)\b|(\d{1,2}(?:[.,]\d)?)\s*(?:°\s*C|degrees?)\b", raw, re.I)
    off = re.search(r"\boff\b", n)
    on = re.search(r"\bon\b", n)
    relative = _RELATIVE.fullmatch(n)
    if sum((bool(off), bool(on), bool(temperature), bool(relative))) != 1:
        return None
    try:
        import jarvis_profile
        groups = jarvis_profile._load_home().get("climate_groups", {})
    except Exception:  # noqa: BLE001
        return None
    rooms = _climate_rooms(groups, n, room)
    if not rooms:
        scope = _scope(n)                          # no room named: the room the user is in
        if scope:
            known = {_norm(str(g)).strip() for g in groups}
            cands = [r for r in scope["candidates"] if _norm(r).strip() in known]
            if len(cands) > 1:
                return _ask_room(n, cands, _climate)
            if cands:
                rooms = _climate_rooms(groups, n, cands[0])
    if len(rooms) != 1 or not rooms[0][1]:
        return None
    name, entities = rooms[0]
    if relative:
        return _climate_step(name, entities, relative.group(1) == "warmer")
    if temperature:
        raw_value = temperature.group(1) or temperature.group(2)
        data = {"temperature": float(raw_value.replace(",", "."))}
        service = "set_temperature"
    else:
        data = {"hvac_mode": "off" if off else "heat"}
        service = "set_hvac_mode"
    import jarvis_mcp
    results = []
    for eid in entities:
        result = jarvis_mcp._home("home_control", {"entity_id": eid, "service": service, "data": data})
        if not result or not result.startswith("Done"):
            done = "; ".join(results)
            prefix = f"{done}. " if done else ""
            return f"{prefix}{name}: {result or 'No response from Home Assistant.'}"
        results.append(result)
    return " ".join(results)


def _home(n, room=None, force=None):
    """Switch what n names. `room` comes from the answer to "Living room or Bedroom?",
    `force` (devices) from the "yes" to "Shall I turn on the one in the living room?"."""
    if QUESTION.search(n):
        return None                                # a question never switches anything
    on = re.search(_ON, n.strip())
    off = re.search(_OFF, n.strip())
    pct = re.search(r"\bto (\d{1,3}) ?(%|percent)", n)
    scene = re.search(r"\b(scene|mood)\s+(\w[\w ]*)|\b(\w+) (scene|mood)\b", n)
    if not (on or off or pct or scene):
        return None
    if _has(n, "if\\b", "when", "automati", "every", "daily", "at \\d", "timer", "later", "why", "whether",
            "in \\w+ (seconds?|minutes?|hours?)\\b"):
        return None                                # schedules/conditions: scheduler or LLM
    if _has(n, "music\\b", "spotify\\b") and _music_target(n):
        return None                                # "turn the music on in the living room": the music, not a switch
    try:
        devs = _devices()
    except Exception:  # noqa: BLE001
        return None
    hint = _hint(n)
    scope = None if force is not None else _scope(n, room)
    import room_scope
    prefer = room_scope.eids(scope["room"]) if scope and scope["sure"] else None
    best = list(force) if force is not None else _match_devices(n, hint, devs, prefer)
    import learning
    learned = None if force is not None else learning.alias(n)   # a wording Claude once resolved for us
    if learned:
        by_eid = {d["eid"]: d for d in devs}
        best = [by_eid.get(e) or {"eid": e, "dom": e.split(".")[0], "name": e.split(".", 1)[1].replace("_", " "),
                                  "words": set()} for e in learned]
        hint = hint or best[0]["dom"]
    lights = [d for d in devs if d["dom"] == "light"]
    if not best and hint == "light" and re.search(r"\ball\b", n) and lights and not pct:
        best = lights                              # "turn all the lights off"
    elif not best or len(best) > 4:
        return None
    service = "turn_on" if (on or pct or scene) and not off else "turn_off"
    if scope and not re.search(r"\ball\b", n):
        if not scope["sure"]:
            # walking, or a radar ghost: ask only when the two rooms would mean different devices
            # (a learned wording means fixed devices: there the room changes nothing)
            picks = set() if learned else {tuple(sorted(d["eid"] for d in _match_devices(n, hint, devs, room_scope.eids(r))))
                                           for r in scope["candidates"]}
            if len(picks - {()}) > 1:
                return _ask_room(n, scope["candidates"], _home)
        elif service == "turn_on" and _generic(n, devs) and not any(d["eid"] in prefer for d in best):
            # "light on" where there is none: ask before switching on another room. "light off" just
            # goes ahead — switching off what is on elsewhere is what is meant, and harmless.
            where = sorted({room_scope.room_of(d["eid"]) for d in best})
            if "" not in where:
                return _ask_elsewhere(n, scope["room"], best, where, hint)
    if len(best) > 1 and not hint and not all(d["dom"] == "light" for d in best):
        # "living room on" with a lamp and a socket: an answer the user gave once is kept
        # (learning.choice); otherwise ask back locally and remember the answer — no model needed
        chosen = learning.choice(n)
        picked = [d for d in best if d["eid"] in chosen]
        if not picked:
            return _ask_choice(n, best)
        best = picked
    # Only what keeps things alive is protected — a lamp called "LED PC" is just a lamp.
    if service == "turn_off" and any(d["dom"] != "light" and (PROTECTED_OFF.search(d["name"])
                                                               or PROTECTED_OFF.search(d["eid"])) for d in best):
        return None
    import jarvis_mcp
    done = []
    for d in best:
        data = {"brightness_pct": max(1, min(100, int(pct.group(1))))} if pct and d["dom"] == "light" else None
        svc = "turn_on" if d["dom"] == "scene" else service
        res = jarvis_mcp._home("home_control", {"entity_id": d["eid"], "service": svc, "data": data or {}})
        if res.startswith("Sent"):                  # sent, but the state did not follow: say so
            return f"{d['name']} is not responding, {_sir()}: the command went out, but the state has not changed."
        if not res.startswith("Done"):
            return None
        done.append(d["name"])
    _SWITCHED.update(devs=best, service=service, at=time.time())
    what = " and ".join(done)
    state = f"at {pct.group(1)} percent" if pct else ("activated" if best[0]["dom"] == "scene"
                                                        else "on" if service == "turn_on" else "off")
    return _say(f"{what} is {state}, {_sir()}.", f"Done, {_sir()}. {what} {state}.") \
        .replace(" is", " are", 1 if len(done) > 1 else 0)


# An ambiguous target ("living room on": a lamp and a socket) is asked back once; the
# answer names one of them ("the lamp", "both") and is kept for that wording (learning.choice).
CHOICE_S = 60
_CHOICE = {"n": "", "devs": [], "at": 0.0, "yes": False}
_KIND = {"light": "no light", "switch": "no matching socket", "fan": "no fan"}


def _ask_choice(n, devs):
    _CHOICE.update(n=n, devs=devs, at=time.time(), yes=False)
    names = [d["name"] for d in devs]
    return f"Do you mean {', '.join(names[:-1])} or {names[-1]}, {_sir()}? Or both?".replace(", ?", "?")


def _generic(n, devs):
    """Only generic words ("light on"), no device's own name: then the room decides what is meant."""
    words = {w for w in n.split() if w not in _FILLER and len(w) > 1}
    return not any(d["words"] & words for d in devs)


def _ask_elsewhere(n, here, devs, where, hint):
    """"light on" in a room without a lamp: offer the one in the other room; "yes" switches it."""
    _CHOICE.update(n=n, devs=devs, at=time.time(), yes=True)
    return (f"There is {_KIND.get(hint, 'nothing suitable')} {_in(here)}, {_sir()}. "
            f"Shall I turn on the one {' and '.join(_in(w) for w in where)}?").replace(", .", ".")


def _choice(n):
    if time.time() - _CHOICE["at"] > CHOICE_S or not _CHOICE["devs"]:
        return None
    s = re.sub(r"\b(please|jarvis|sir|the|my|i mean|i meant)\b", " ", n).strip()
    s = " ".join(s.split())
    devs = _CHOICE["devs"]
    if _CHOICE.get("yes"):
        asked = _CHOICE["n"]
        if re.fullmatch(r"(yes|yeah|yep|yup|sure|gladly|exactly|okay|ok|do it|go ahead|yes do it|yes go ahead)", s):
            _CHOICE.update(n="", devs=[], at=0.0, yes=False)
            return _home(asked, force=devs)
        if re.fullmatch(r"(no|nope|nah|leave it|dont|not now|never mind|forget it)", s):
            _CHOICE.update(n="", devs=[], at=0.0, yes=False)
            return f"All right, never mind, {_sir()}.".replace(", .", ".")
        return None
    if re.fullmatch(r"\s*(both|all|both of them|all of them)\s*", s):
        picked = devs
    else:
        words = set(s.split())
        picked = [d for d in devs if d["words"] & words]
        if len(picked) != 1:
            picked = [d for d in devs if _norm(d["name"]).strip() in s] or picked
        if len(picked) != 1:
            return None                            # not an answer to the question: on to the other handlers
    import learning
    learning.remember_choice(_CHOICE["n"], [d["eid"] for d in picked])
    asked = _CHOICE["n"]
    _CHOICE.update(n="", devs=[], at=0.0)
    return _home(asked)


# Follow-ups to the last switching, within FOLLOW_S: "brighter", "darker", "to 30 percent",
# "off again", "and in the bedroom too". Short words with no device in them, so they only
# count right after Jarvis switched something; otherwise they go on to the LLM as before.
FOLLOW_S = 120
STEP_PCT = 25
_SWITCHED = {"devs": [], "service": "", "at": 0.0}
_ROOM_WORD = re.compile(r"\w*room|living|dining|kitchen|bath\w*|hall\w*|corridor|office|study|basement|cellar|garden|balcony|left|right")
_LEAD = r"(?:(?:and|now|then|please|make|make it|make them|set|set it|it|them|the)\s+)*"


def _followup(n):
    s = re.sub(r"\s+(please|sir|jarvis)\b", "", n.strip())
    if time.time() - _SWITCHED["at"] > FOLLOW_S or not _SWITCHED["devs"] or len(s.split()) > 6:
        return None
    devs = _SWITCHED["devs"]
    lights = [d for d in devs if d["dom"] == "light"]
    import jarvis_mcp

    def send(targets, service, data=None):
        for d in targets:
            res = jarvis_mcp._home("home_control", {"entity_id": d["eid"], "service": service, "data": data or {}})
            if not res.startswith(("Done", "Sent")):
                return False
        _SWITCHED["at"] = time.time()
        return True

    what = " and ".join(d["name"] for d in (lights or devs))
    m = re.fullmatch(_LEAD + r"(?:a bit|a little|slightly|much|even)?\s*(brighter|darker)", s)
    if m and lights:
        step = STEP_PCT if m.group(1) == "brighter" else -STEP_PCT
        if send(lights, "turn_on", {"brightness_step_pct": step}):
            return _say(f"{m.group(1).capitalize()}, {_sir()}.", f"{what} is {m.group(1)}, {_sir()}.")
        return None
    m = re.fullmatch(_LEAD + r"(?:to )?(\d{1,3}) ?(?:%|percent)", s)
    if m and lights:
        pct = max(1, min(100, int(m.group(1))))
        if send(lights, "turn_on", {"brightness_pct": pct}):
            return f"{what} at {pct} percent, {_sir()}."
        return None
    m = re.fullmatch(_LEAD + r"(?:turn |switch )?(?:it |them )?(?:back )?(on|off)(?: again)?", s)
    if m:
        service = "turn_off" if m.group(1) == "off" else "turn_on"
        if service == "turn_off" and any(d["dom"] != "light" and PROTECTED_OFF.search(d["name"] + " " + d["eid"])
                                         for d in devs):
            return None
        if send(devs, service):
            _SWITCHED["service"] = service
            state = "on" if service == "turn_on" else "off"
            return f"{' and '.join(d['name'] for d in devs)} {'are' if len(devs) > 1 else 'is'} {state}, {_sir()}."
        return None
    m = re.fullmatch(_LEAD + r"(?:also )?(?:in the|in|at the|at) ([a-z]+(?: [a-z]+)?) (?:too|as well)", s) \
        or re.fullmatch(_LEAD + r"also (?:in the|in|at the|at) ([a-z]+(?: [a-z]+)?)", s)
    if m:
        # the same kind of thing in the other room: "TV Bedroom" -> "tv", then the new room
        thing = " ".join(w for w in _norm(devs[0]["name"]).split() if not _ROOM_WORD.fullmatch(w))
        state = "on" if _SWITCHED["service"] == "turn_on" else "off"
        return _home(f"{thing} {m.group(1)} {state}".strip())
    return None


# Some handlers need the original wording (what to remind of, what goes on the
# shopping list); the normalised text has lost case and punctuation.
# Per thread: the scheduler runs planned commands from its own thread.
_RAW = threading.local()


def _raw():
    return getattr(_RAW, "text", "")


def _timers(n):
    import scheduler
    return scheduler.handle(n, _raw())


def _lists(n):
    import lists
    return lists.handle(n, _raw())


def _routine(n):
    import routines
    if re.search(r"\b(which|what|show\w*|list)\b.*\broutines\b", n):
        return routines.describe()
    dry = routines.explain_request(_raw())
    if dry:
        return dry                                 # "What happens when I say good night?" — nothing runs
    r = routines.find(_raw())
    if not r:
        return None
    reply = routines.run(r, run_local)
    try:                                           # proactive mode: the evening check follows "good night"
        import proactive
        import proactive_config
        cfg = proactive_config.load()
        if proactive.owns("evening") and r.get("name", "").lower() == str(cfg.triggers.evening.get("routine") or "").lower():
            reply = f"{reply} {proactive.evening(cfg)}".strip()
    except Exception:  # noqa: BLE001 — the routine itself has run; the check is a bonus
        pass
    return reply


GOOD_NIGHT = re.compile(r"^\s*(jarvis )?good ?night( jarvis)?\s*$")


def _proactive(n):
    import proactive
    if GOOD_NIGHT.match(n) and proactive.owns("evening"):
        import routines
        if not routines.find(_raw()):
            return proactive.evening()             # no routine of that name: the check alone
        return None                                # _routine runs it and appends the check
    return proactive.handle(n, _raw())


def _pronounce(n):
    import pronounce
    return pronounce.handle(n, _raw())


def _pc(n):
    import pc
    return pc.handle(n, _raw())


def _commute(n):
    import commute
    return commute.handle(n, _raw())


def _parcels(n):
    import parcels
    return parcels.handle(n, _raw())


def _printer(n):
    import printer
    return printer.handle(n, _raw())


# "What happens if the Pi crashes?" — Blast-Radius on the Pi knows the dependencies. Unknown device or
# Blast-Radius unreachable: None, and the question goes to Claude (who has the outage_impact tool).
OUTAGE = re.compile(r"\b(what (happens|breaks|fails|stops working|stops|goes down|still works)\b.{0,40}\b(if|when|once)\b"
                    r".{0,60}\b(fails|failed|goes down|went down|crashes|crashed|dies|breaks|is broken|is offline|is down|"
                    r"loses power|has no power|goes out|is gone)|what (else |all )?(fails|stops working)\b.{0,30}\b"
                    r"(if|when|without)|what (all |else )?depends on)")


def _outage(n):
    if not OUTAGE.search(n):
        return None
    import outage_map
    try:
        return outage_map.spoken(outage_map.ask(_raw() or n.strip()))
    except Exception:  # noqa: BLE001
        return None


# Status of the homelab tools on the Pi (Restore Probe, update airlock, power hog detective, Zigbee placement advisor).
# Narrow on purpose: plain "power hogs" stays with power_costs' standby report, "any updates?" with updates_check.
PI_REPORTS = (
    ("backups", re.compile(r"\b(restore[ -]?probe|restore test|(are|is|were|was) (the |my )?backups?\b.{0,25}\b"
                           r"(ok|okay|fine|good|alright|restorable|intact|usable|clean)|backup[ -]?test|"
                           r"can (the )?backups be restored)")),
    ("updates", re.compile(r"\b(update[ -]?airlock|update[ -]?schleuse|container[ -]?updates?|"
                           r"what does the airlock say)")),
    ("power", re.compile(r"\b(power[ -]?hog detective|stromfresser|detective|anything (unusual|odd|strange) "
                         r"(about|with) (the )?(power|electricity)|(power|electricity) .{0,15}(anomal|unusual)|"
                         r"(plugs?|devices?) .{0,20}(no values|unusual))")),
    ("zigbee", re.compile(r"\b(placement advisor|platzberater|zigbee[ -]?(router|network|reception|connection|channel|"
                          r"signal)|where (should|could|can) .{0,40}zigbee|zigbee .{0,30}(stable|weak|good))")),
    ("all", re.compile(r"\b(homelab tools|pi tools|what do (the )?tools say|tools on the pi)")),
)


def _pi_reports(n):
    topic = next((t for t, rx in PI_REPORTS if rx.search(n)), None)
    if not topic or _has(n, "why", "explain", "how do i fix", "repair"):
        return None                                # why/fix questions need Claude (he has pi_reports too)
    import pi_reports
    return pi_reports.spoken(topic)


def _report(n):
    if not re.search(r"\b(usage report|token report|what have you learn(ed|t)|"
                     r"how many tokens?|how much have i used)\b", n):
        return None
    import learning
    return learning.spoken_report(7)


_SHIFT = r"(dishwasher|washing machine|washer|dryer|tumble dryer|boiler|printer|print|laundry)"
_OFTEN = r"(often|a lot|regularly|constantly|daily|every day|rarely|hardly ever|hardly|seldom|never)"


def _coach(n):
    usage = re.search(r"\b(?:i\s+)?(?:use|need)\s+(?:the|my)\s+(\w[\w-]*)\s+(?:\w+\s+)?" + _OFTEN + r"\b", n)
    before = re.search(r"\bi\s+" + _OFTEN + r"\s+(?:use|need)\s+(?:the|my)\s+(\w[\w-]*)", n)
    if usage or before:
        word, how = (usage.group(1), usage.group(2)) if usage else (before.group(2), before.group(1))
        import energy_coach
        return energy_coach.set_usage(word, how not in ("rarely", "hardly ever", "hardly", "seldom", "never"))
    if not re.search(r"\b(energy ?coach|energy ?tip|power ?tip|solar surplus|surplus|solar power|"
                     r"when (should|can|could) i .{0,40}" + _SHIFT + r"|"
                     r"is it worth .{0,30}(" + _SHIFT[1:-1] + r"|power|sun)|"
                     r"(start|run|turn on|switch on) .{0,10}(" + _SHIFT[1:-1] + r") .{0,15}(now|right now|today))", n):
        return None
    import energy_coach
    return energy_coach.report()


def _announce(n):
    m = re.match(r"^\s*(?:jarvis[,\s]+)?(?:say|announce|make an announcement)\s+(?:please\s+)?"
                 r"(?:in the|in|to the)\s+(.+?)\s*$", _raw(), re.I)
    if not m:
        return None
    import speakers
    rest = m.group(1)
    for room in sorted(speakers.rooms(), key=len, reverse=True):
        if rest.lower().startswith(room.lower()) and (len(rest) == len(room) or not rest[len(room)].isalnum()):
            text = re.sub(r"^\s*[:,]?\s*(?:that\s+)?", "", rest[len(room):])
            return speakers.announce(room, text) if text else None
    return None                                    # not a room with a speaker: Claude decides


_TV_DO = [("pause", r"pause|stop"), ("resume", r"resume(?! watching)|continue(?! watching)|play"),
          ("louder", r"louder|volume up"), ("quieter", r"quieter|volume down"), ("mute", r"mute|sound off"),
          ("unmute", r"unmute|sound on|sound back"), ("home", r"home screen|home"), ("back", r"go back|back button")]


def _tv(n):
    if not re.search(r"\b(television|fire ?tv|telly|tv)\b", n) or re.search(r"\b(keep|continue|resume) watching\b", n):
        return None                                # "continue watching on the TV" is Jellyfin's (_media)
    on_tv = r"\s+on\s+(?:the\s+)?(?:television|fire ?tv|telly|tv)"
    app = (re.search(r"\b(?:open|start|launch|show me|put)\s+(?:on\s+)?([\w+.-]+(?: [\w+.-]+)?)" + on_tv, n)
           or re.match(r"^\s*(?:jarvis\s+)?([\w+.-]+)" + on_tv + r"\s*$", n))
    import speakers
    if app and not re.fullmatch(r"(the|it|that|sound|something|\w+ (on|off|louder|quieter))", app.group(1)):
        word = app.group(1)
        raw = re.search(re.escape(word), _raw(), re.I)
        return speakers.tv("app", raw.group(0) if raw else word)
    for cmd, pat in _TV_DO:
        if re.search(r"\b(" + pat + r")\b", n):
            return speakers.tv(cmd)
    return None                                    # "TV on/off" stays with the plug (_home)


def _alerts(n):
    if not re.search(r"\b(severe weather|weather warning|storm warning|thunderstorm warning|ice warning|heat warning|"
                     r"warnings? (from|by) the (dwd|weather service)|dwd)", n):
        return None
    import weather_alerts
    return weather_alerts.current()


def _costs(n):
    if not re.search(r"\b(power costs?|electricity costs?|power hogs?|standby|idle consumption|"
                     r"what does .{0,30}cost (me )?.{0,20}(in power|in electricity|per month|a month|per year|a year)|"
                     r"how much (power|electricity) does .{0,30}(use|draw|need|eat)|power consumption|"
                     r"electricity consumption|power budget|"
                     r"am i .{0,20}(within|under|over|on) .{0,10}(budget|plan|track))", n):
        return None
    import power_costs
    if _has(n, "standby", "idle", "power hog"):
        return power_costs.report(standby=True)
    if _has(n, "budget", "within", "on track", "on plan"):
        return power_costs.report(budget=True)
    m = re.search(r"\b(?:does|do)\s+(?:the|my)\s+(\w[\w-]*)", n)
    return power_costs.report(m.group(1) if m and m.group(1) not in ("power", "house", "home", "flat", "apartment") else "")


def _situation(n):
    if re.search(r"\b(security (check|scan)|security cent(er|re))\b.{0,25}\b(start|run|again|now|through)\b|"
                 r"\b(start|run|do) (a )?(new )?security (check|scan)", n):
        import situation
        return situation.run_scan()
    m = re.search(r"\b(status report|situation report|state of the (house|network|homelab)|hows it looking|"
                  r"is everything (running|ok|fine)|all green|(uptime )?kuma|security cent(er|re)|security situation|"
                  r"security findings?|zero export|energy ?loop)", n)
    if not m or _has(n, "why", "explain", "set up", "fix", "repair", "how does that work"):
        return None                                # why/fix questions need Claude, not the status line
    word = m.group(1)
    topic = ("kuma" if "kuma" in word else "security" if "security" in word
             else "energy" if "zero export" in word or "energy" in word else "all")
    import situation
    return situation.report(topic)


def _emergency(n):
    import emergency
    return emergency.handle(n, deny_cards=EMERGENCY_HOOKS.get("deny"), cancel=EMERGENCY_HOOKS.get("cancel"))


EMERGENCY_HOOKS = {}     # the server sets deny (open cards) and cancel (running Claude process)


def _personality(n):
    import personality
    return personality.handle(n)


def _desktop(n):
    import desktop
    return desktop.handle(n)


def _context(n):
    import context
    return context.handle(n)


def _tasks(n):
    import tasks
    return tasks.handle(n)


def _focus(n):
    import focus
    return focus.handle(n)


def _daytime(n):
    import daytime
    return daytime.handle(n)


def _episodes(n):
    import episodes
    return episodes.handle(n)


def _wellbeing(n):
    import wellbeing
    return wellbeing.handle(n)


def _maintenance(n):
    import maintenance
    return maintenance.handle(n)


def _unifi(n):
    import unifi
    return unifi.handle(n)


def _media(n):
    import jellyfin
    return jellyfin.handle(n)


def _selfcheck(n):
    import selfcheck
    return selfcheck.handle(n)


def _habits(n):
    import user_habits
    return user_habits.handle(n)


LOCAL_HANDLERS = [_emergency, _proactive, _choice, _room_answer, _personality, _focus, _daytime, _context, _tasks, _desktop, _routine, _timers, _lists, _pronounce,
                  _outage, _pi_reports, _report, _selfcheck, _habits, _episodes, _wellbeing, _maintenance, _situation, _coach, _costs, _announce, _tv, _alerts,
                  _unifi, _media, _pc, _commute, _parcels, _printer, _followup, _home_state, _climate, _home,
                  _music, _time, _date, _sun, _weather, _solar, _nas, _pi]


def _local(text, handlers):
    n = _norm(text)
    if text.rstrip().endswith("?") and not QUESTION.search(n):
        n = " is" + n                              # "Air purifier on?" is a question too
    saved, _RAW.text = _raw(), text
    try:
        for h in handlers:
            try:
                reply = h(n)
            except Exception:  # noqa: BLE001
                reply = None
            if reply:
                reply = re.sub(r",\s*\.", ".", reply).replace(" ,", ",").replace("  ", " ")
                return reply, h.__name__.strip("_")
    finally:
        _RAW.text = saved
    return None, None


def run_local(text):
    """Only the local path, for routine steps and scheduled commands: reply or None.
    Routines cannot call routines, so a loop is impossible."""
    reply, _ = _local((text or "").strip(), [h for h in LOCAL_HANDLERS if h is not _routine])
    return reply


# Follow-ups ("So what?", "Then everything again", "Do it") name no topic, so the
# keyword rules gave them memory only — and Jarvis, who still sees the conversation,
# said his tools were missing. A turn without a topic keeps the last turn's tools.
CARRY_S = 600
WEB_ALWAYS = os.environ.get("JARVIS_WEB_ALWAYS", "1").strip().lower() not in ("0", "off", "false", "no")
# A follow-up that does name a topic still belongs to the last question for a while:
# "At what percentage does the protection kick in?" after a question about the HA automation
# brought "home" of its own and lost the full HA server that reads automations.
JOIN_S = 180
_LAST = {"groups": [], "own": [], "model": None, "at": 0.0}


def route(message, ha_full=False):
    """{'kind': 'local', 'reply': ..., 'handler': ...} or the llm_route dict."""
    text = (message or "").strip()
    if LOCAL_ENABLED and text and len(text) < 160 and not text.startswith("/"):
        reply, handler = _local(text, LOCAL_HANDLERS)
        if reply:
            return {"kind": "local", "reply": reply, "handler": handler}
    r = llm_route(text, ha_full)
    own = [g for g in r["groups"] if g != "memory"]
    age = time.time() - _LAST["at"]
    if not own and _LAST["groups"] and age < CARRY_S:
        r = dict(r, groups=list(dict.fromkeys(r["groups"] + _LAST["groups"])), reason="follow-up",
                 model=_LAST["model"] if _LAST["model"] == MODEL_SMART else r["model"])
    elif own and _LAST["own"] and age < JOIN_S:
        # only the last turn's own groups join, so a long conversation does not pile them up
        joined = list(dict.fromkeys(r["groups"] + _LAST["own"]))
        if joined != r["groups"]:
            r = dict(r, groups=joined, model=MODEL_SMART if _LAST["model"] == MODEL_SMART else r["model"])
    if [g for g in r["groups"] if g != "memory"]:
        _LAST.update(groups=r["groups"], own=own or _LAST["own"], model=r["model"], at=time.time())
    # Web search rides along in every turn: what he cannot find in notes or tools, he looks
    # up instead of saying he does not know. Added last and kept out of _LAST — it is no
    # topic, and it does not ask for the bigger model (only a search request does, above).
    if WEB_ALWAYS and "web" not in r["groups"]:
        r = dict(r, groups=r["groups"] + ["web"])
    return r


def forget_topic():
    """New conversation (or a test): the next vague turn carries nothing over."""
    _LAST.update(groups=[], own=[], model=None, at=0.0)
