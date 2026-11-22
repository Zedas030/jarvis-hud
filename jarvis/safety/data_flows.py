"""
Data flows: which data leave the house, to whom, and what turns each flow off.

  flows(env)     every destination with "active" worked out from the settings (.env)
  spoken(env)    a few sentences for "Which data go to the cloud?" (local router, 0 tokens)
  report(env)    the list for this installation, as text (scripts/data_flows.py)
  markdown()     the table for docs/data-flows.md (no state of this installation)

One list in code, so that the answer, the script and the document cannot drift apart;
tests/test_data_flows.py fails when a host in the code is missing here. Reads settings only:
no network, and no value from .env is ever printed.

Idea from the data-boundary scan in OpenJarvis (github.com/open-jarvis/OpenJarvis,
Apache-2.0: `jarvis scan --data-boundaries`); written from scratch for this project.
"""
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
HOME_CONFIG = pathlib.Path(os.environ.get("JARVIS_HOME_CONFIG") or ROOT / "config" / "home.json")

TALK, VOICE, SERVICE, PLAIN = "Conversations", "Voice", "Services", "No personal data"


def _set(*names):
    return lambda env: all((env.get(n) or "").strip() for n in names)


def _fallback(name, key):
    def active(env):
        order = (env.get("JARVIS_FALLBACK") or "groq,gemini,ollama").lower()
        return bool((env.get(key) or "").strip()) and name in [p.strip() for p in order.split(",")]
    return active


def _tts(name, key=""):
    """In the chain (TTS_CHAIN, else the single choice JARVIS_TTS) and, where one is needed, with a key."""
    def active(env):
        chain = (env.get("TTS_CHAIN") or "").strip().lower()
        names = [p.strip() for p in chain.split(",")] if chain else [(env.get("JARVIS_TTS") or "auto").strip().lower()]
        if not chain and names == ["auto"]:
            names = ["edge"]
        return name in names and (not key or bool((env.get(key) or "").strip()))
    return active


def _stt(env):
    kind = (env.get("JARVIS_STT") or "auto").strip().lower()
    return (kind == "fish" and bool(env.get("FISH_AUDIO_API_KEY"))) or (kind == "elevenlabs" and bool(env.get("ELEVENLABS_API_KEY")))


def _not_off(name):
    return lambda env: (env.get(name) or "1").strip().lower() not in ("0", "false", "no", "off")


def _echo(env):
    try:
        return "alexa_media" in HOME_CONFIG.read_text(encoding="utf-8-sig")
    except OSError:
        return False


def _always(env):
    return True


# key, group, destination, hosts in the code (for the test), what goes there, check, how to turn it off
FLOWS = [
    ("anthropic", TALK, "Claude (Anthropic)", ("api.anthropic.com",),
     "Every request the local router does not answer itself: your sentence, the last exchanges, matching notes "
     "and the results of the tools needed for it (mail, calendar, files only when asked). "
     "A screenshot only after you confirmed the card.",
     lambda env: (env.get("JARVIS_RUNTIME") or "auto").strip().lower() != "mock",
     "Cannot be turned off, this is the brain. Image description off: JARVIS_VISION=0."),
    ("groq", TALK, "Groq (fallback mode)", ("api.groq.com",),
     "Only when Claude is blocked or fails: the same request as to Claude, including mail, calendar and health.",
     _fallback("groq", "GROQ_API_KEY"), "Empty GROQ_API_KEY or set JARVIS_FALLBACK=ollama (or off)."),
    ("gemini", TALK, "Google Gemini (fallback mode)", ("generativelanguage.googleapis.com",),
     "Only in fallback mode after Groq, with a redacted context; never requests about mail, calendar or health "
     "(the free tier may train on prompts).",
     _fallback("gemini", "GEMINI_API_KEY"), "Empty GEMINI_API_KEY or take gemini out of JARVIS_FALLBACK."),
    ("fish", VOICE, "Fish Audio (speech output)", ("api.fish.audio",),
     "The text Jarvis speaks.", _tts("fish", "FISH_AUDIO_API_KEY"), "Take fish out of TTS_CHAIN; `local` speaks without a cloud."),
    ("elevenlabs", VOICE, "ElevenLabs (speech output)", ("api.elevenlabs.io",),
     "The text Jarvis speaks.", _tts("elevenlabs", "ELEVENLABS_API_KEY"), "Take elevenlabs out of TTS_CHAIN."),
    ("azure", VOICE, "Microsoft Azure Speech (speech output)", (),
     "The text Jarvis speaks.", _tts("azure", "AZURE_SPEECH_KEY"), "Take azure out of TTS_CHAIN."),
    ("google_tts", VOICE, "Google Text-to-Speech (speech output)", ("texttospeech.googleapis.com",),
     "The text Jarvis speaks.", _tts("google", "GOOGLE_TTS_API_KEY"), "Take google out of TTS_CHAIN."),
    ("edge", VOICE, "Microsoft Edge voices (speech output)", (),
     "The text Jarvis speaks (free service, no account).", _tts("edge"), "Take edge out of TTS_CHAIN."),
    ("cloud_stt", VOICE, "Fish Audio / ElevenLabs (speech recognition)", (),
     "The recording of your sentence — only when recognition is explicitly set to a cloud service. "
     "The default is Whisper on your own PC.",
     _stt, "JARVIS_STT=local (or auto with faster-whisper installed)."),
    ("echo", VOICE, "Amazon Alexa (Echo as a speaker)", (),
     "The text of the answer when an Echo reads it aloud: through Home Assistant to Amazon.",
     _echo, "Remove the Echo as a speaker from config/home.json."),
    ("mail", SERVICE, "Your mail provider (IMAP)", (),
     "Jarvis signs in to your mailbox, reads and searches; drafts land in the Drafts folder. Nothing is sent.",
     lambda env: bool((env.get("IMAP_HOST") or env.get("MAIL_USER") or "").strip()), "Empty IMAP_HOST/MAIL_USER."),
    ("icloud", SERVICE, "Apple iCloud (calendar)", ("caldav.icloud.com",),
     "Reading the calendar; new appointments are entered after confirmation.", _set("ICLOUD_USER"), "Empty ICLOUD_USER."),
    ("garmin", SERVICE, "Garmin Connect", (),
     "Sign-in with your account; sleep and activity values come from there to the PC.", _set("GARMIN_EMAIL"), "Empty GARMIN_EMAIL."),
    ("spotify", SERVICE, "Spotify", ("api.spotify.com", "accounts.spotify.com"),
     "Controlling playback and reading what is playing.", _set("JARVIS_SPOTIFY_CLIENT_ID"), "Empty JARVIS_SPOTIFY_CLIENT_ID."),
    ("weather", SERVICE, "Open-Meteo (weather)", ("api.open-meteo.com", "geocoding-api.open-meteo.com"),
     "The weather coordinates from .env (JARVIS_WEATHER_LAT/LON) and the names of places whose weather you ask about.",
     _always, "Cannot be turned off; enter coarser coordinates if the exact location should not go out."),
    ("alerts", SERVICE, "Bright Sky (warnings of the German weather service)", ("api.brightsky.dev",),
     "The weather coordinates.", _not_off("JARVIS_WEATHER_ALERTS"), "JARVIS_WEATHER_ALERTS=0."),
    ("commute", SERVICE, "OpenStreetMap (Nominatim, OSRM)", ("nominatim.openstreetmap.org", "router.project-osrm.org"),
     "For questions about travel time: the destination address and the coordinates of your home.",
     _always, "Do not ask for travel times; enter coarser JARVIS_HOME_LAT/LON."),
    ("dhl", SERVICE, "DHL (parcel tracking)", ("api-eu.dhl.com",),
     "Tracking numbers from your shipping mails.", _set("DHL_API_KEY"), "Empty DHL_API_KEY."),
    ("track17", SERVICE, "17TRACK (parcel tracking)", ("api.17track.net",),
     "Tracking numbers from your shipping mails.", _set("TRACK17_API_KEY"), "Empty TRACK17_API_KEY."),
    ("phone", SERVICE, "Push to the phone (Home Assistant app, via Apple/Google)", (),
     "The text of a proposal or a notice.", _set("JARVIS_PHONE_NOTIFY"), "Empty JARVIS_PHONE_NOTIFY."),
    ("web", SERVICE, "Web pages and web search", (),
     "Only when asked: search terms and the address that is opened.", _always, "Do not ask for it."),
    ("makerworld", SERVICE, "MakerWorld (print ideas)", ("makerworld.com",),
     "Only when asked: the search term.", _always, "Do not ask for it."),
    ("cost", PLAIN, "Anthropic Cost API", ("api.anthropic.com",),
     "Only the billing key; costs come back.", _set("JARVIS_COST_KEY"), "Empty JARVIS_COST_KEY."),
    ("checks", PLAIN, "Version and connection checks (AVM, home-assistant.io, google.com)",
     ("download.avm.de", "version.home-assistant.io", "www.google.com"),
     "Only the request itself (your IP address), no content.", _always, "Cannot be turned off."),
]

# What never leaves the house, said in one breath.
LOCAL = ("Speech recognition (Whisper on the PC), wake word, memory and conversation log, Home Assistant, "
         "Sonos, NAS, Pi, Paperless, Jellyfin, UniFi, screen text recognition, the local voice and Ollama")


def flows(env=None):
    env = os.environ if env is None else env
    out = []
    for key, group, name, hosts, what, check, off in FLOWS:
        try:
            active = bool(check(env))
        except Exception:  # noqa: BLE001 — a broken setting is not a reason to hide the row
            active = False
        out.append(dict(key=key, group=group, name=name, hosts=hosts, what=what, active=active, off=off))
    return out


def _short(name):
    return name.split(" (")[0]


def spoken(env=None):
    """A few sentences: who gets something right now, what stays at home."""
    rows = [f for f in flows(env) if f["active"] and f["group"] != PLAIN]
    by = {g: [_short(f["name"]) for f in rows if f["group"] == g] for g in (TALK, VOICE, SERVICE)}
    out = [f"Data currently leave the house to {len(rows)} services."]
    if by[TALK]:
        first, rest = by[TALK][0], by[TALK][1:]
        out.append(f"Your requests go to {first}" + (f", in fallback mode to {' and '.join(rest)}." if rest else "."))
    if by[VOICE]:
        out.append("What I say goes to " + ", ".join(by[VOICE]) + ".")
    if by[SERVICE]:
        out.append("On top of that come " + ", ".join(by[SERVICE]) + ".")
    out.append("Speech recognition, memory and Home Assistant stay in the house. "
               "The full list with the switches is in docs/data-flows.md.")
    return " ".join(out)


def report(env=None):
    lines = []
    rows = flows(env)
    for group in (TALK, VOICE, SERVICE, PLAIN):
        lines.append(f"\n{group}")
        for f in (r for r in rows if r["group"] == group):
            lines.append(f"  [{'active' if f['active'] else '   off'}] {f['name']}\n          {f['what']}\n          Turn off: {f['off']}")
    lines.append(f"\nStays in the house: {LOCAL}.")
    active = sum(1 for r in rows if r["active"] and r["group"] != PLAIN)
    return f"{active} services outside the house currently get data ({len(rows)} are possible)." + "\n".join(lines)


def markdown():
    out = ["# Data flows: what leaves the house",
           "",
           "Generated from `jarvis/safety/data_flows.py` (`scripts/data_flows.py --write`); do not edit by hand.",
           "Which rows are active in your installation is shown by `scripts/data_flows.py` or the question",
           "\"Which data go to the cloud?\".",
           ""]
    for group in (TALK, VOICE, SERVICE, PLAIN):
        out += [f"## {group}", "", "| Destination | What goes there | Turn off |", "|---|---|---|"]
        out += [f"| {name} | {what} | {off} |" for _, g, name, _, what, _, off in FLOWS if g == group]
        out.append("")
    out += ["## Stays in the house", "", LOCAL + ".", ""]
    return "\n".join(out)
