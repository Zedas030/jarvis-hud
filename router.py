"""
Intent router — decides per message, before any LLM is involved:

  local   simple commands answered by code alone (0 tokens): switch devices,
          music keys, time/date, weather/golf, solar, NAS, Pi.
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
import time
import urllib.request

MODEL_SIMPLE = os.environ.get("JARVIS_MODEL_SIMPLE", "haiku").strip()
MODEL_SMART = os.environ.get("JARVIS_MODEL_SMART", "sonnet").strip() or os.environ.get("JARVIS_MODEL", "sonnet")
LOCAL_ENABLED = os.environ.get("JARVIS_LOCAL_ROUTER", "1").strip() not in ("0", "false", "no", "off")

WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
MONTHS = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September",
          "Oktober", "November", "Dezember")


def _norm(text):
    return " " + re.sub(r"[^\wäöüß%]+", " ", (text or "").lower()).strip() + " "


def _has(n, *words):
    return any(re.search(r"\b" + w, n) for w in words)


def _sir():
    return random.choice(("Sir", "Sir", "Sir", ""))


def _say(*options):
    s = random.choice(options).strip()
    return s.replace(", .", ".").replace(" ,", ",")


# ── tool groups + model choice for the LLM path ──────────────
GROUP_RULES = [
    ("home", r"\b(licht|lampe|led\b|steckdose|schalt|kühlschrank|spülmaschine|waschmaschine|drucker|zelt|bett\b|heizung|thermostat|temperatur im|grad im|luftreiniger|"
             r"ventilator|szene|wohnzimmer|schlafzimmer|gaming|küche|boiler|smart ?home|home ?assistant|"
             r"solar|akku|speicher|strom|watt|sensor|luft|feucht|echo|fire ?tv|fernseher|tv\b)"),
    ("mail", r"\b(mail|e-mail|postfach|posteingang|newsletter|nachricht von|geschrieben)"),
    ("calendar", r"\b(termin|kalender|eintragen|trag\w* .*ein|erinner|verabredung|meeting|wann (ist|habe))"),
    ("systems", r"\b(pi\b|raspi|raspberry|nas\b|synology|netzwerk|wlan|router|fritz|gerät|server|pi-hole|pihole|ip\b)"),
    ("health", r"\b(schlaf|geschlafen|schritte|puls|garmin|body ?battery|stress|fitness|training|lauf)"),
    ("music", r"\b(musik|spotify|song|lied|playlist|album|abspielen)"),
    ("web", r"\b(such|recherch|google|internet|online|website|webseite|seite|öffne|news|nachrichten|aktuell|"
            r"preis|kostet|wer ist|wer war|was ist|wie viel kostet|wetter|bundesliga|spiel|ergebnis|kurs|aktie)"),
    ("pc", r"\b(programm|starte|befehl|powershell|kommandozeile|datei|ordner|installier|bildschirm|screenshot|"
           r"fenster|desktop|computer|rechner)"),
]
SMART_HINTS = re.compile(
    r"\b(erklär|warum|wieso|analys|vergleich|plan|schreib|formulier|zusammenfass|recherch|überleg|hilf mir|"
    r"code|programm|berechne|strategie|optimier|bewert|was meinst du|ausführlich|genau)", re.I)


def llm_route(message, ha_full=False):
    n = _norm(message)
    groups = ["memory"]                     # remembering works in every turn
    for name, pat in GROUP_RULES:
        if re.search(pat, n):
            groups.append(name)
    if ha_full:
        groups.append("ha_full")
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
    if not _has(n, "wie spät", "uhrzeit", "wieviel uhr", "wie viel uhr", "welche zeit"):
        return None
    now = dt.datetime.now()
    return _say(f"Es ist {now.hour} Uhr {now.minute:02d}, {_sir()}.", f"{now:%H:%M} Uhr, {_sir()}.")


def _date(n):
    if not (_has(n, "welcher tag", "welches datum", "der wievielte", "welchen tag", "datum") and not _has(n, "termin")):
        return None
    d = dt.date.today()
    return f"Heute ist {WEEKDAYS[d.weekday()]}, der {d.day}. {MONTHS[d.month - 1]}, {_sir()}."


_WX = {}


def _forecast():
    if _WX.get("at", 0) > time.time() - 900:
        return _WX["d"]
    import briefing
    d = briefing._get("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&timezone=%s"
                      "&forecast_days=3&current=temperature_2m,weather_code"
                      "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
                      "precipitation_probability_max,wind_speed_10m_max" % (briefing.LAT, briefing.LON, briefing.TZ_Q))
    _WX.update(at=time.time(), d=d)
    return d


def _weather(n):
    golf = _has(n, "golf")
    if not (_has(n, "wetter", "regnet", "regen", "temperatur draußen", "wie warm", "wie kalt", "sonne scheint")
            or (golf and _has(n, "kann ich", "taugt", "wetter", "lohnt", "geht", "spielen", "heute", "morgen"))):
        return None
    if _has(n, "woche", "wochenende", "übermorgen", "nächste"):
        return None                              # longer outlook: let the LLM do it
    import briefing
    import dashboard
    try:
        d = _forecast()
    except Exception:  # noqa: BLE001
        return None
    i = 1 if _has(n, "morgen") and not _has(n, "heute morgen") else 0
    day = d["daily"]
    sky = briefing.WEATHER_CODES.get(day["weather_code"][i], "wechselhaft")
    tmax, tmin = round(day["temperature_2m_max"][i]), round(day["temperature_2m_min"][i])
    rain, wind = day["precipitation_probability_max"][i] or 0, round(day["wind_speed_10m_max"][i] or 0)
    when = "Morgen" if i else "Heute"
    now = ""
    if i == 0 and d.get("current", {}).get("temperature_2m") is not None:
        now = f" Gerade sind es {round(d['current']['temperature_2m'])} Grad."
    text = (f"{when} {sky}, {tmin} bis {tmax} Grad, Regenrisiko {rain} Prozent, Wind bis {wind} km/h.{now}")
    if golf:
        try:
            w = _dash("weather", 1800, dashboard._weather)
            verdict = {"ja": "passt es", "mit Jacke": "geht es, aber mit Jacke", "nein": "lieber nicht"}
            v = verdict.get(w.get("golf"), "schwer zu sagen")
            gday = w.get("golf_day", "heute")
            if gday == "heute":
                text += f" Golf heute: {v}, {_sir()}."
            elif i == 0:
                text += f" Für eine Runde heute reicht das Tageslicht nicht mehr. {gday.capitalize()} {v}, {_sir()}."
            else:
                text += f" Golf {gday}: {v}, {_sir()}."
        except Exception:  # noqa: BLE001
            pass
    elif rain >= 60:
        text += f" Schirm empfohlen, {_sir()}."
    return text.replace(" ,", ",").replace(", .", ".")


def _solar(n):
    if not (_has(n, "solar", "photovoltaik", "pv\\b", "balkonkraftwerk", "akku", "speicher", "batterie")
            and not _has(n, "warum", "erklär", "verlauf", "gestern", "woche", "monat", "automation")):
        return None
    import dashboard
    try:
        h = _dash("house", 30, dashboard._house)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(h, dict) or h.get("error"):
        return None
    parts = [f"Die Solaranlage liefert gerade {h.get('solar_w', 0)} Watt"]
    if h.get("soc") is not None:
        parts.append(f"der Akku steht bei {round(h['soc'])} Prozent")
    if h.get("gen_kwh") is not None:
        parts.append(f"heute erzeugt: {str(h['gen_kwh']).replace('.', ',')} Kilowattstunden")
    return ", ".join(parts) + f", {_sir()}."


def _nas(n):
    if not (_has(n, "nas\\b", "synology") and not _has(n, "datei", "ordner", "freigabe", "backup", "warum")):
        return None
    import dashboard
    try:
        s = _dash("nas", 300, dashboard._nas)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(s, dict) or s.get("error"):
        return None
    ok = "alles in Ordnung" if s.get("ok") else "mindestens eine Platte oder ein Volume meldet ein Problem"
    used = f"{s.get('used_pct')} Prozent belegt ({str(s.get('used_tb')).replace('.', ',')} von " \
           f"{str(s.get('total_tb')).replace('.', ',')} Terabyte)" if s.get("used_pct") is not None else ""
    temp = f", {round(s['temp'])} Grad" if s.get("temp") is not None else ""
    return f"Das NAS: {ok}{temp}, {used}, {_sir()}.".replace(", ,", ",")


def _pi(n):
    if not (_has(n, "pi\\b", "raspi", "raspberry") and _has(n, "status", "wie geht", "läuft", "alles ok", "temperatur",
                                                             "zustand", "fit", "gesund")):
        return None
    import dashboard
    try:
        s = _dash("pi", 120, dashboard._pi)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(s, dict) or s.get("error"):
        return None
    cont = f", {s.get('up')} von {s.get('containers')} Containern laufen" if s.get("containers") else ""
    de = {"week": ("Woche", "Wochen"), "day": ("Tag", "Tagen"), "hour": ("Stunde", "Stunden")}
    parts = [f"{num} {de[unit][0] if num == '1' else de[unit][1]}"
             for num, unit in re.findall(r"(\d+) (week|day|hour)", s.get("uptime") or "")[:2]]
    since = " und ".join(parts) or "einer Weile"
    disk = f", Speicher zu {round(s['disk'])} Prozent voll" if s.get("disk") is not None else ""
    return f"Der Pi läuft seit {since}, {round(s.get('temp') or 0)} Grad{disk}{cont}, {_sir()}."


MUSIC = [
    (r"\b(pause|pausier|stopp|stop|anhalten|halt die musik|musik aus)", "play_pause", "Pausiert"),
    (r"\b(weiter ?spielen|fortsetzen|musik an|spiel musik|play)\b", "play_pause", "Läuft"),
    (r"\b(nächste[nrs]?|skip|überspring)", "next", "Nächster Titel"),
    (r"\b(vorherige[nrs]?|zurück|letzte[nrs]? (lied|song|titel))", "previous", "Vorheriger Titel"),
    (r"\b(lauter)", "volume_up", "Lauter"),
    (r"\b(leiser)", "volume_down", "Leiser"),
]


def _music(n):
    if not _has(n, "musik", "spotify", "song", "lied", "titel", "track") and not re.search(
            r"\b(lauter|leiser)\b", n) or _has(n, "jarvis.*(lauter|leiser)", "stimme", "sprich"):
        return None
    if _has(n, "such", "spiel .* von", "spiele .* von", "playlist", "album"):
        return None                               # searching needs the LLM to pick the query
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
# LEDs, auto-update) are never targets; sockets that keep things alive are
# never switched off without Claude (and his confirmation rules).
_DEV = {"at": 0.0, "list": []}
_CONFIG_WORDS = re.compile(r"kindersicherung|automatisch|\bled\b|display|update|abschaltung|shuffle|wiederholen|"
                           r"nicht stören|stummschalt", re.I)
PROTECTED_OFF = re.compile(r"router|\bpc\b|raspi|\bpi\b|kühlschrank|smart hub|nas", re.I)
_DOMAIN_WORDS = {"licht": "light", "lampe": "light", "lampen": "light", "steckdose": "switch",
                 "ventilator": "fan", "szene": "scene"}
_FILLER = {"der", "die", "das", "den", "dem", "im", "in", "am", "an", "aus", "ein", "bitte", "mal", "mach", "mache",
           "schalte", "schalt", "jarvis", "und", "auf", "zu", "von", "sir", "kannst", "du", "die", "alle", "einschalten",
           "ausschalten", "anmachen", "ausmachen", "prozent", "%", "licht", "lampe", "steckdose", "lichter", "lampen"}


def _devices():
    if _DEV["at"] > time.time() - 300:
        return _DEV["list"]
    import briefing
    out = []
    for s in briefing._ha_states():
        eid = s["entity_id"]
        dom = eid.split(".")[0]
        name = (s.get("attributes", {}).get("friendly_name") or eid).strip()
        if dom not in ("light", "switch", "fan", "scene") or _CONFIG_WORDS.search(name):
            continue
        out.append({"eid": eid, "dom": dom, "name": name, "words": set(_norm(name).split())})
    _DEV.update(at=time.time(), list=out)
    return out


QUESTION = re.compile(r"^\s*(ist|sind|läuft|laufen|brennt|brennen|hat|haben|wie|was|welche\w*|warum|wieso|"
                      r"steht|stehen|status|zustand|gibt es|weißt du)\b")


def _match_devices(n, hint=None):
    words = {w for w in n.split() if w not in _FILLER and len(w) > 1}
    best, score = [], 0
    for d in _devices():
        if hint and d["dom"] != hint and not (hint == "switch" and d["dom"] == "fan"):
            continue
        sc = len(d["words"] & words)
        if not sc:
            continue
        if sc > score:
            best, score = [d], sc
        elif sc == score:
            best.append(d)
    if len(best) > 1:
        exact = [d for d in best if d["words"] <= words]
        if len(exact) == 1:
            best = exact
    return best


def _home_state(n):
    """'Ist der Luftreiniger an?' — answered from Home Assistant, nothing is switched."""
    if not (QUESTION.search(n) and re.search(r"\b(an|aus|ein|eingeschaltet|ausgeschaltet|läuft|laufen)\s*$", n.strip())):
        return None
    hint = next((d for w, d in _DOMAIN_WORDS.items() if re.search(r"\b" + w, n)), None)
    try:
        best = _match_devices(n, hint)
        if not best or len(best) > 3:
            return None
        import briefing
        states = {s["entity_id"]: s["state"] for s in briefing._ha_states()}
    except Exception:  # noqa: BLE001
        return None
    word = {"on": "an", "off": "aus", "playing": "an", "idle": "aus"}
    parts = [f"{d['name']} ist {word.get(states.get(d['eid']), states.get(d['eid'], 'unbekannt'))}" for d in best]
    return " und ".join(parts) + f", {_sir()}."


def _home(n):
    if QUESTION.search(n):
        return None                                # a question never switches anything
    on = re.search(r"\b(an|ein|einschalten|anmachen|anschalten|aktivier\w*)\s*$|\b(schalt\w* .* an|mach\w* .* an)\b", n.strip())
    off = re.search(r"\b(aus|ausschalten|ausmachen|abschalten|deaktivier\w*)\s*$|\b(schalt\w* .* aus|mach\w* .* aus)\b", n.strip())
    pct = re.search(r"\bauf (\d{1,3}) ?(%|prozent)", n)
    scene = re.search(r"\b(szene|stimmung)\s+(\w[\w ]*)", n)
    if not (on or off or pct or scene):
        return None
    if _has(n, "wenn", "automati", "jeden", "täglich", "um \\d", "timer", "später", "warum", "ob "):
        return None                                # schedules/conditions: LLM
    try:
        devs = _devices()
    except Exception:  # noqa: BLE001
        return None
    words = {w for w in n.split() if w not in _FILLER and len(w) > 1}
    hint = next((d for w, d in _DOMAIN_WORDS.items() if re.search(r"\b" + w, n)), None)
    best, score = [], 0
    for d in devs:
        if hint and d["dom"] != hint and not (hint == "switch" and d["dom"] == "fan"):
            continue
        sc = len(d["words"] & words)
        if not sc:
            continue
        if sc > score:
            best, score = [d], sc
        elif sc == score:
            best.append(d)
    if len(best) > 1:
        exact = [d for d in best if d["words"] <= words]   # "TV" beats "TV Schlafzimmer" for "schalt das TV an"
        if len(exact) == 1:
            best = exact
    if not best or len(best) > 4:
        return None
    if len(best) > 1 and not hint:
        return None                                # "mach Wohnzimmer an" — which one? ask the LLM
    service = "turn_on" if (on or pct or scene) and not off else "turn_off"
    if service == "turn_off" and any(PROTECTED_OFF.search(d["name"]) for d in best):
        return None
    import jarvis_mcp
    done = []
    for d in best:
        data = {"brightness_pct": max(1, min(100, int(pct.group(1))))} if pct and d["dom"] == "light" else None
        svc = "turn_on" if d["dom"] == "scene" else service
        res = jarvis_mcp._home("home_control", {"entity_id": d["eid"], "service": svc, "data": data or {}})
        if not res.startswith("Erledigt"):
            return None
        done.append(d["name"])
    what = " und ".join(done)
    state = f"auf {pct.group(1)} Prozent" if pct else ("aktiviert" if best[0]["dom"] == "scene"
                                                         else "an" if service == "turn_on" else "aus")
    return _say(f"{what} ist {state}, {_sir()}.", f"Erledigt, {_sir()}. {what} {state}.") \
        .replace(" ist", " sind", 1 if len(done) > 1 else 0)


LOCAL_HANDLERS = [_home_state, _home, _music, _time, _date, _weather, _solar, _nas, _pi]


def route(message, ha_full=False):
    """{'kind': 'local', 'reply': ..., 'handler': ...} or the llm_route dict."""
    text = (message or "").strip()
    if LOCAL_ENABLED and text and len(text) < 160 and not text.startswith("/"):
        n = _norm(text)
        if text.rstrip().endswith("?") and not QUESTION.search(n):
            n = " ist" + n                         # "Luftreiniger an?" is a question too
        for h in LOCAL_HANDLERS:
            try:
                reply = h(n)
            except Exception:  # noqa: BLE001
                reply = None
            if reply:
                reply = re.sub(r",\s*\.", ".", reply).replace(" ,", ",").replace("  ", " ")
                return {"kind": "local", "reply": reply, "handler": h.__name__.strip("_")}
    return llm_route(text, ha_full)
