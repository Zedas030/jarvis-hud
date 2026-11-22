# Data flows: what leaves the house

Generated from `jarvis/safety/data_flows.py` (`scripts/data_flows.py --write`); do not edit by hand.
Which rows are active in your installation is shown by `scripts/data_flows.py` or the question
"Which data go to the cloud?".

## Conversations

| Destination | What goes there | Turn off |
|---|---|---|
| Claude (Anthropic) | Every request the local router does not answer itself: your sentence, the last exchanges, matching notes and the results of the tools needed for it (mail, calendar, files only when asked). A screenshot only after you confirmed the card. | Cannot be turned off, this is the brain. Image description off: JARVIS_VISION=0. |
| Groq (fallback mode) | Only when Claude is blocked or fails: the same request as to Claude, including mail, calendar and health. | Empty GROQ_API_KEY or set JARVIS_FALLBACK=ollama (or off). |
| Google Gemini (fallback mode) | Only in fallback mode after Groq, with a redacted context; never requests about mail, calendar or health (the free tier may train on prompts). | Empty GEMINI_API_KEY or take gemini out of JARVIS_FALLBACK. |

## Voice

| Destination | What goes there | Turn off |
|---|---|---|
| Fish Audio (speech output) | The text Jarvis speaks. | Take fish out of TTS_CHAIN; `local` speaks without a cloud. |
| ElevenLabs (speech output) | The text Jarvis speaks. | Take elevenlabs out of TTS_CHAIN. |
| Microsoft Azure Speech (speech output) | The text Jarvis speaks. | Take azure out of TTS_CHAIN. |
| Google Text-to-Speech (speech output) | The text Jarvis speaks. | Take google out of TTS_CHAIN. |
| Microsoft Edge voices (speech output) | The text Jarvis speaks (free service, no account). | Take edge out of TTS_CHAIN. |
| Fish Audio / ElevenLabs (speech recognition) | The recording of your sentence — only when recognition is explicitly set to a cloud service. The default is Whisper on your own PC. | JARVIS_STT=local (or auto with faster-whisper installed). |
| Amazon Alexa (Echo as a speaker) | The text of the answer when an Echo reads it aloud: through Home Assistant to Amazon. | Remove the Echo as a speaker from config/home.json. |

## Services

| Destination | What goes there | Turn off |
|---|---|---|
| Your mail provider (IMAP) | Jarvis signs in to your mailbox, reads and searches; drafts land in the Drafts folder. Nothing is sent. | Empty IMAP_HOST/MAIL_USER. |
| Apple iCloud (calendar) | Reading the calendar; new appointments are entered after confirmation. | Empty ICLOUD_USER. |
| Garmin Connect | Sign-in with your account; sleep and activity values come from there to the PC. | Empty GARMIN_EMAIL. |
| Spotify | Controlling playback and reading what is playing. | Empty JARVIS_SPOTIFY_CLIENT_ID. |
| Open-Meteo (weather) | The weather coordinates from .env (JARVIS_WEATHER_LAT/LON) and the names of places whose weather you ask about. | Cannot be turned off; enter coarser coordinates if the exact location should not go out. |
| Bright Sky (warnings of the German weather service) | The weather coordinates. | JARVIS_WEATHER_ALERTS=0. |
| OpenStreetMap (Nominatim, OSRM) | For questions about travel time: the destination address and the coordinates of your home. | Do not ask for travel times; enter coarser JARVIS_HOME_LAT/LON. |
| DHL (parcel tracking) | Tracking numbers from your shipping mails. | Empty DHL_API_KEY. |
| 17TRACK (parcel tracking) | Tracking numbers from your shipping mails. | Empty TRACK17_API_KEY. |
| Push to the phone (Home Assistant app, via Apple/Google) | The text of a proposal or a notice. | Empty JARVIS_PHONE_NOTIFY. |
| Web pages and web search | Only when asked: search terms and the address that is opened. | Do not ask for it. |
| MakerWorld (print ideas) | Only when asked: the search term. | Do not ask for it. |

## No personal data

| Destination | What goes there | Turn off |
|---|---|---|
| Anthropic Cost API | Only the billing key; costs come back. | Empty JARVIS_COST_KEY. |
| Version and connection checks (AVM, home-assistant.io, google.com) | Only the request itself (your IP address), no content. | Cannot be turned off. |

## Stays in the house

Speech recognition (Whisper on the PC), wake word, memory and conversation log, Home Assistant, Sonos, NAS, Pi, Paperless, Jellyfin, UniFi, screen text recognition, the local voice and Ollama.
