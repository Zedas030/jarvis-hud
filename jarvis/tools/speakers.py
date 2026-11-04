"""
Speakers in other rooms and the TV — through Home Assistant, no new hardware.

  announce(room, text)  a spoken announcement in a room: an Echo speaks with Alexa's voice
                        (notify.alexa_media_*, type announce), a Sonos with Home Assistant's
                        TTS (tts.speak, English). Jarvis' own voice stays on the PC/iPad.
                        In the quiet hours both speak quietly: the Echo is turned down to
                        JARVIS_ALEXA_NIGHT_VOLUME and back, the Sonos announces at
                        JARVIS_SONOS_NIGHT_VOLUME and returns to its own volume.
                        No room (or "here") = the room the presence sensors see the user in.
  tv(command, app)      the Fire TV through Alexa Media Player: the command is sent to the TV's
                        Alexa as if spoken there (media_player.play_media, type custom).
                        Only fixed commands; an app name is checked — no free text reaches
                        Alexa, so nothing can be bought or called that way.

  spotify_device(room)  which Spotify Connect device plays in that room (for spotify.transfer): the
                        `spotify` entry of the room, else the room's name — Sonos and Alexa groups
                        usually carry the room's name in Spotify anyway.
  spotify_fallback(room) the room's `spotify_fallback` device: a worse speaker that only plays when the
                        first one stays unreachable (the Echo Dot alone when the soundbar is not in
                        Spotify's device list), or "".

config/home.json:
  "speakers": {"Living Room": {"notify": "notify.alexa_media_user_echo_dot", "spotify": "Living Room",
                               "spotify_fallback": "User's Echo Dot"},
               "Bedroom": {"tts": "tts.google_translate_en_com",
                           "player": "media_player.bedroom_bedroom"}},
  "tv": "media_player.user_fire_tv"
"""
import re

# what is said to the TV's Alexa for each command (in the language of the Alexa account)
TV_COMMANDS = {
    "pause": "pause", "resume": "resume", "louder": "volume up", "quieter": "volume down", "mute": "mute",
    "unmute": "unmute", "back": "go back", "home": "go home",
    "off": "turn off the tv", "on": "turn on the tv",
}
APP = re.compile(r"^[a-zA-Z0-9+ .-]{2,30}$")
HERE = re.compile(r"^(here|with me|where i am|in my room|current)$", re.I)
# words that must never reach Alexa inside an app name
FORBIDDEN = re.compile(r"buy|order|purchase|call|phone|message|shop|pay|subscri|alexa|routine|skill", re.I)


def _quiet():
    try:
        import events
        return events.quiet_now()
    except Exception:  # noqa: BLE001
        return False


def _cfg():
    import jarvis_profile as profile
    return profile.ha("speakers", {}) or {}, profile.ha("tv")


def rooms():
    return sorted(_cfg()[0])


def _room(word, speakers):
    w = (word or "").strip().lower()
    hits = [r for r in speakers if r.lower() in w or w in r.lower()] if w else []
    return hits[0] if len(hits) == 1 else None


def spotify_device(room):
    """(room name, Spotify device name, Home Assistant player or "") for a room with a speaker; (None, None, "")
    when the room is unknown. The player is the way round Spotify Connect: a Sonos is only in Spotify's device
    list while it plays, but Home Assistant can hand it a Spotify playlist or track directly."""
    speakers, _ = _cfg()
    name = _room(room, speakers)
    if not name:
        return None, None, ""
    return name, (speakers[name].get("spotify") or name), (speakers[name].get("player") or "")


def spotify_fallback(room):
    """The room's second-choice Spotify Connect device, or ""."""
    speakers, _ = _cfg()
    name = _room(room, speakers)
    return (speakers[name].get("spotify_fallback") or "") if name else ""


def announce(room, text):
    import jarvis_mcp as j
    import whereabouts
    speakers, _ = _cfg()
    room = " ".join(str(room or "").split())
    if not room or HERE.match(room):
        room = whereabouts.room()
        if not room:
            return ("I do not know which room you are in right now. Tell me the room: "
                    f"{', '.join(sorted(speakers)) or 'none set up'}.")
    name = _room(room, speakers)
    if not name:
        return f"I do not know a speaker for “{room}”. I can speak in: {', '.join(sorted(speakers)) or 'nowhere'}."
    text = " ".join(str(text or "").split())[:300]
    if not text:
        return "What should I announce?"
    sp = speakers[name]
    try:
        if sp.get("notify"):
            import alexa_voice
            alexa_voice.night_down(sp["notify"], alexa_voice.seconds(text) + 2)   # quiet hours: Echo turned down
            service = sp["notify"].split(".", 1)[1]
            j._ha(f"/api/services/notify/{service}", {"message": text, "data": {"type": "announce"}})
        elif sp.get("tts") and sp.get("player") and _quiet():
            # quiet hours: an announcement at the night volume, the Sonos goes back to its own afterwards
            import urllib.parse
            import sonos
            uri = f"media-source://tts/{sp['tts']}?" + urllib.parse.urlencode({"message": text, "language": "en"})
            j._ha("/api/services/media_player/play_media", {
                "entity_id": sp["player"], "media_content_id": uri, "media_content_type": "music",
                "announce": True, "extra": {"volume": sonos._percent(sonos.NIGHT_VOLUME)}})
        elif sp.get("tts") and sp.get("player"):
            j._ha("/api/services/tts/speak", {"entity_id": sp["tts"], "media_player_entity_id": sp["player"],
                                              "message": text, "language": "en"})
        else:
            return f"No speaker is set up for the {name}."
    except Exception as e:  # noqa: BLE001
        return f"The announcement in the {name} did not work ({str(e)[:60]})."
    return f"Announced in the {name}."


def tv(command, app=""):
    import jarvis_mcp as j
    _, player = _cfg()
    if not player:
        return "Nothing is set up for the TV (config/home.json → tv)."
    command = (command or "").strip().lower()
    if command == "app":
        app = " ".join(str(app or "").split())
        if not APP.match(app) or FORBIDDEN.search(app):
            return f"I do not open “{app}” — only app names."
        spoken = f"open {app}"
    elif command in TV_COMMANDS:
        spoken = TV_COMMANDS[command]
    else:
        return f"I cannot do that on the TV. Possible: {', '.join(sorted(TV_COMMANDS))} or opening an app."
    result = j._home("home_control", {"entity_id": player, "service": "play_media",
                                      "data": {"media_content_type": "custom", "media_content_id": spoken}})
    if result and result.startswith(("Refused", "Home Assistant reports", "Home Assistant not")):
        return result
    return "Done." if command != "app" else f"Opening {app}."
