[← Back to the README](../README.md)

# Features

Everything Jarvis can do: the highlights, the long list of small checkmarks and the state of the feature list.

## Highlights

| | |
|---|---|
| **Hands-free talking** | Live microphone with pause and end-of-sentence detection, push-to-talk, interrupting by talking over him: he stops, listens and carries on if nothing comes. Optionally with a wake word ("Jarvis …" or locally with openWakeWord); without "Jarvis" nothing is processed or logged. |
| ⏰ **Timers, alarms, reminders** | *"Timer 10 minutes for the pasta"*, *"Wake me tomorrow at half past six"*, *"Remind me about the laundry in 20 minutes"*, *"Turn off the light in an hour"*. Local, no tokens, survives restarts. |
| **Speaks up by himself** | Washing machine done, appointment in 15 minutes or "time to leave" (with travel time), parcel arriving today, battery full, NAS problem, new device on the WiFi. With quiet hours. |
| **On the road and post** | *"When do I have to leave?"* (travel time to the next appointment through OpenStreetMap), *"Is a parcel coming today?"* (from the shipping mails). Local, no tokens. |
| **PC control** | *"Volume to 30 percent"*, *"Mute"*, *"Open Spotify"*, *"Lock the screen"*. Spotify gets quieter while Jarvis listens or speaks, on every device of the account; at night he speaks more quietly. |
| **Gets more economical** | Remembers wordings that Claude had to resolve once ("reading lamp off") and switches them himself from then on. The same questions within 5 minutes come from the cache. *"Usage report"* tells how much ran locally. |
| **Pronunciation** | NAS, Pi, kWh & co. are spoken correctly; own words with *"Pronounce Tuya as Too-ya"*. |
| **Lists and routines** | Shopping and to-do list by voice (Home Assistant or a vault note). Routines like "Good night": one sentence, several actions, created by voice. |
| **Controlled and honest** | Fixed rules in code instead of the prompt, protection against third-party instructions in mails and web pages, audit log, emergency stop, a checkable source for every memory, honest feedback when switching. |
| **Personality** | Standard, professional, technical, casual on request; typed questions get detailed answers, spoken ones short answers. |
| **Windows** | Arranging windows, workspaces, files and clipboard, screen text through local OCR, approved scripts. |
| **Learns** | Rates his own conversations, suggests fixed rules on Sundays, does a weekly review with patterns from sleep, steps, stress and golf, reports homelab outages only when they matter and suggests a new ability twice a week. Without work topics, changes only with a card. |
| **Fallback mode** | If Claude fails or the limit is reached, a free chain takes over: Groq → Gemini → Ollama, with the same tools and confirmation cards. |
| **Real memory** | Notes by topic, an automatic conversation log, remembering and forgetting on his own (expiry dates, trash folder). |
| **Local intent router** | Lights, sockets, music, time, weather, energy and system status without an LLM, with an answer in milliseconds. |
| **Lean LLM calls** | Own prompt instead of the Claude Code default, tools as needed, automatic model choice (Haiku/Sonnet). Measured at **over 25× fewer tokens** than a standard call. |
| **Fail-safe voice** | A chain of cloud, local GPU and free voices. Used-up providers are skipped until their reset, without an audible break. |
| **Smart home** | Home Assistant: switching, querying states, energy (solar/battery), room climate, air quality. Optionally the full HA MCP server for automations and history. |
| **Home network** | Status of the Raspberry Pi and Synology NAS, device list, watcher for new devices on the WiFi. *"Situation report"* sums up Uptime Kuma, the security centre on the Pi and the energy loop, and reports outages, urgent security findings and a stuck zero export by itself. |
| **Everyday life** | Calendar (CalDAV/iCloud), mailbox (IMAP, read only), Garmin health data, Spotify, web search. |
| **HUD** | Dashboard with a weather and activity traffic light, house, health, systems, mail, 3D knowledge graph and usage display. |
| **Controlled** | `localhost` only, confirmation cards for system actions, protected devices, no self-modification at runtime. |

## What it can do

The architecture separates the **router** (rules), the **tools** (MCP server) and the **memory** (Markdown). New abilities usually take only a few lines.

**Everyday life**
- *"Good morning"* gives a daily briefing with weather, appointments, energy, nearly empty batteries and new mails.
- *"Can I still go for a run / play golf today?"* gives a traffic light from weather, wind, daylight and working hours.
- *"What is on tomorrow?"* or *"Put the dentist in for Friday at ten"* (with confirmation).
- *"Do I have important mails?"* gives a summary without newsletters.
- *"Remember that Anna's birthday is on 3 May"* creates a note under `People/Anna`.
- *"Timer 8 minutes for the pizza"*, *"How much longer?"*, *"Wake me at 6:45"*, *"Snooze"*, *"Alarm off"*
- *"Remind me tomorrow at 9 to call Anna"*, *"Which reminders do I have?"*
- *"Put milk and eggs on the shopping list"*, *"What is on the shopping list?"*, *"Take the milk"* … *"off the shopping list"*
- *"When I say good night, turn off all the lights and tell me when the alarm rings"* creates a routine. From then on *"Good night"* runs without an LLM.

**At home**
- *"Set the living room light to 40 percent"*, *"Is the washing machine still on?"*, *"Cinema scene"*
- *"How much is the solar system delivering, and how full is the battery?"*
- *"What does the PC cost me per month?"*, *"Who is drawing standby power?"*: electricity costs from the sockets' meters (price `JARVIS_POWER_PRICE`); the energy coach measures standby over several days.
- If you leave the flat and a light, music or a device from `config/home.json` → `away_check` is still running, a message comes to your phone together with a "switch off" suggestion that you confirm on a card in Jarvis. He switches nothing by himself.
- Sundays from 6 pm: a weekly report on the home network (outages, security rating, internet drops, consumption and solar power), spoken and as a note under `Memories/Homelab`.
- *"Which security findings are open?"*, *"Accept the Echo finding as an exception for 30 days"* (card), *"Start a security check"*.
- When you come home after at least 20 minutes: *"Welcome back. While you were out: …"* with what Jarvis reported during that time.
- Official warnings from the German weather service (through Bright Sky) he reports by himself, from "moderate"; severe ones also at night and to the phone. *"Are there any severe weather warnings?"*
- Habits in the house: if you switch a light on at the same time on at least 4 of 10 days, Jarvis suggests a routine on Saturday (*"Good night"*), only created after the card. If a light is on much longer than usual, he says so once.
- *"Say in the living room: dinner is ready"* speaks through the Echo Dot, in the bedroom through the Sonos (`config/home.json` → `speakers`).
- *"Play the music from the bedroom in the living room"*, *"Music into the bedroom"* moves the Spotify playback to the room's speaker (Spotify Connect, keeps playing there; needs the Spotify login). Which Spotify name belongs to the room is in `config/home.json` → `speakers` → `spotify`; if it is missing, the room name applies (Sonos and Alexa groups are usually named like the room in Spotify). Without a room a device name works as well, for example *"Music on the PC"*.
- *"Open Netflix on the TV"*, *"TV pause/louder/quieter"* through Alexa on the Fire TV — only fixed commands and app names (`config/home.json` → `tv`).
- Answers from the Echo: choose 🔈 "Echo living room" on the HUD button — what you type or say on the PC is then spoken by Alexa (in Alexa's voice, sentence by sentence; costs no voice credit).
- Morning briefing: when something in the living room is switched on for the first time in the morning (LED, TV, or you talk to the Echo), the Echo says weather, warnings, appointments, the solar tip, the night's notices and unread mails once (`config/home.json` → `morning_briefing`).
- Follow-ups: after *"Living room light on"*, for two minutes *"brighter"*, *"darker"*, *"to 30 percent"*, *"off again"* are enough; after *"TV on"* also *"and in the bedroom too"*.
- *"Energy tip"*, *"When should I turn on the dishwasher?"*: from battery level, house consumption and the solar forecast the energy coach says when there is power to spare today or tomorrow and what should run then. If the battery is full and there is a surplus, he speaks up by himself once a day. He never switches by himself. Sockets that were off at first sight (e.g. the boiler) count as rarely used and are never suggested; *"I use the boiler often"* changes that.
- *"Which batteries do I have to change?"*
- With the HA MCP server: *"Why is it so cold in the bedroom? Show me the history."*

**Tech and network**
- *"How is the Pi / the NAS doing?"* gives temperature, storage and uptime, plus the disk status with a warning from 85 %.
- *"Is there an unknown device on the WiFi?"* The watcher reports new devices by itself.
- *"Restart the containers on the Pi"* (tool `pi_run`: one command over SSH, always with a confirmation card; it refuses obviously destructive commands).
- *"Situation report"*, *"Is everything running?"*, *"How is the security situation?"*, *"Is the zero export running?"* give, locally and without tokens, the state from Uptime Kuma (status page `jarvis`), the ORBIT security centre and Home Assistant (`config/homelab.json` → `energy`).

**Smarter every day** (since October 2026)
- *"What did I ask you about the printer last week?"*, *"What did we talk about yesterday?"* are answered by the router from the conversation log (0 tokens). *"How did I sleep the last time I was ill?"* gets the tool `memory_timeline`: per day conversations, Garmin, notices, outages and storage levels, joined by date (`episodes.py`).
- **Your habits:** if you ask the same thing at the same time on at least four days (*"When do I have to leave?"* on Tuesdays at 7:40), Jarvis suggests on Saturday to tell you by himself or, if the model has to answer, to fetch the answer into the cache three minutes earlier so that it comes right away and without tokens. Only the card sets it up. *"Which habits do you know?"*, *"Forget the habit …"* (`user_habits.py`).
- **Garmin as a trigger:** a sleep score under 60, stress from 50 or Body Battery under 30 in the morning make the morning "gentle": a short, calm briefing, no sarcasm, suggestions and energy tips only shown instead of spoken, the voice a little quieter. In the evening from 9 pm with Body Battery under 25 a single hint at an early evening, with the trigger of the good night routine. *"How am I today?"* (`wellbeing.py`).
- **Before setting off:** if an appointment has a place, Jarvis reports 30 minutes before the time to leave what is still running at home (like when leaving) and which thermostats he would set to the away temperature (`config/home.json` → `departure`; `climate_away: 0` switches them off instead). The suggestion is then already waiting: *"switch off"* is enough, plus the card, or the yes button on the phone (`departure.py`).
- **Electricity budget:** `JARVIS_POWER_BUDGET_EUR` (or `budget_eur` in `config/energy_coach.json`): *"Am I within budget?"*, and from the fifth day of the month once a day from 6 pm a notice if the projection is above it, with the biggest cause.
- **Maintenance calendar:** certificates that expire soon, Docker images on the Pi that are too old, the age of the last NAS backup, disks that are not "normal" (`config/homelab.json` → `maintenance`). *"What is in the maintenance calendar?"*, *"When does the certificate expire?"*, a daily notice per item that is due (`maintenance.py`).
- **Paperless-ngx:** *"What do I pay for liability insurance?"*, *"When does the phone contract end?"*: full-text search in the scanned post, reading documents (`PAPERLESS_URL`, `PAPERLESS_TOKEN`). Third-party content, read only.
- **UniFi:** *"Who is on the WiFi right now?"*, *"Are all access points running?"* (`UNIFI_URL` with an API key or a user; `unifi.py`).
- **Jellyfin:** *"What did I watch last?"*, *"Continue watching the series on the TV"*, *"Search for the film Heat"*; playback on every device with the Jellyfin app (`JELLYFIN_URL`, `JELLYFIN_API_KEY`, `JELLYFIN_USER`, `JELLYFIN_TV`).
- **Voice by time of day:** from 9 pm he speaks more quietly and a little more slowly, during quiet hours he whispers (voices with stage tags get `[whispers]`), and the model then answers in two sentences at most. *"Whisper mode on"* / *"Speak normally again"* switch by hand until the morning (`daytime.py`).
- **Asking back with memory:** *"Turn on the living room"* with a lamp and a socket: Jarvis asks once *"Do you mean the lamp or the socket?"*, carries out the answer and remembers it for this wording; after that he never asks again and needs no model (`learning.py` → `choices`, shown under *"What have you learned?"*).
- **Phone as a card:** suggestions when leaving and before setting off come to the phone with yes/no buttons (Home Assistant app). The answer comes back through a helper in Home Assistant and counts like the card; see [Phone](usage.md#phone-yes-and-no-on-the-go) (`phone_actions.py`).
- **Focus sessions:** *"Focus 45 minutes"*: notices are collected instead of spoken (alarms ring), the focus playlist starts (`config/home.json` → `focus`), after ten minutes in YouTube, Netflix or Reddit a single hint comes, and at the end the summary with what came up in the meantime. *"End focus"*, *"How much focus time is left?"* (`focus.py`).
- **HA in real time:** Jarvis keeps a WebSocket connection to Home Assistant (`ha_events.py`) and gets every state change at once. Doors, windows, water, smoke, motion, presence, the printer status and the Jarvis switches wake the notice checks at once instead of at the next 30-second round; every place that used to fetch the full state list over REST reads the live copy. If the connection drops, Jarvis polls as before. Switch off: `JARVIS_HA_WS=0`.
- **"Why did the light turn on?"** Jarvis reads the HA logbook and follows the cause up to two steps back: *"Bedroom light went off at 7:20. Cause: the automation 'Light follows me', triggered by 'Room', which was set by: the automation 'Where am I', triggered by 'Presence bedroom'."* No context in the logbook: switched on the device itself or in its app (`ha_why.py`).
- **"Where is …?"** *"I put the spare key in the blue box in the hallway"*, *"Remember, the passport is in the safe"*, *"The winter tyres are now in the basement"* go into the note **Places**; *"Where is the spare key?"* finds it. A new place replaces the old one. Without "remember", "now" or "I put …" nothing is saved, so the TV cannot fill the note (`places.py`).
- **Birthdays:** the day before at 18:00: *"Tomorrow is Anna's birthday, Anna turns 34. I have noted: likes tulips."* From the people notes and bullets like "Ben's birthday is on 1990-05-04", also ones he saved by himself; the gift idea is the preferences from the person's note, no model call. *"Who has a birthday soon?"*, *"When is Anna's birthday?"* (`birthdays.py`).
- **"What have you done today?"** Requests and the local share, what he said, saved and proposed by himself and what he changed with or without a card, from his own logs (`activity.py`).
- **Night report of the ghosts:** in the morning from 7:30, only when a presence or motion sensor triggered at least 5 times while you were in another room, or a light went on at night, with its cause. *"How was the night?"* asks any time (`night_report.py`).
- **Airing and mould:** Jarvis compares the absolute humidity indoors (humidity and temperature sensors with room names) with the outside air. From 60 % and clearly drier air outside an airing tip comes once per room and day (8–21 h); if a room is mostly above 65 % for three days, a mould warning once a week. *"Should I air the room?"* (`airing.py`).
- **Print ideas:** *"Remember as a print idea: cable holder for the desk"*, *"What did I want to print?"*, *"The cable holder is printed"*, *"Search for a cable holder on MakerWorld"* opens the search in the browser (`print_ideas.py`, list in the vault).
- **Kuma alarms at once, restart after approval:** the HA automation that sends Uptime Kuma's alarm to the phone also fires the event `jarvis_alert`; Jarvis then announces an outage at once instead of up to ten minutes later (`kuma_alerts.py`). If the monitor belongs to a container on the Pi (`config/homelab.json` → `kuma_restart`), he asks once *"Shall I restart the container …?"*; a *"yes"* goes through the confirmation card, only then `docker restart` runs. If the service comes back by itself, he withdraws the question.
- **Noise filter:** a sensor that switches more than 30 times an hour (ghost pulses of presence sensors) stops waking the watchers for an hour; its value still lands in the live copy (`JARVIS_HA_WS_NOISY`).
- **What's on tomorrow?** Alarm, first appointment and how many follow, all-day entries, expected parcels and the weather in a few sentences, locally (`tomorrow.py`). Also runs as the second-to-last step of the "Good night" routine.
- **Weekly token budget:** `JARVIS_TOKEN_BUDGET_WEEK` (default 2 million) counts everything that went to a model since Monday. At 80 and 100 % Jarvis says so once a week (`token_budget.py`); *"How many tokens?"* gives the state, the "Tokens" tile shows the share.
- **Crash watchdog:** every minute Jarvis stamps the time into the HA helper `input_datetime.jarvis_heartbeat`, but only while his own server still answers (`heartbeat.py`). If the PC runs (smart plug above 80 W) for 10 minutes without a stamp, the automation "Jarvis: heartbeat watchdog" sends a push to the phone, once per outage. A switched-off PC triggers nothing. Set up with `scripts/heartbeat_ha_setup.py`; test instances on other ports do not stamp.
- **Self-check:** if you ask a question again in other words within 25 seconds, that counts as a miss of the first answer, together with the handler or model that gave it. The learning loop sees these places, *"How well did you do this week?"* names them (`selfcheck.py`).
- **Token tile:** in the room display the "Tokens" tile shows the week, the share of requests handled locally and the trend against the week before; the sheet lists the most expensive repeated questions, i.e. the candidates for the router.
- **Routine preview:** *"What happens when I say good night?"*, *"What does the evening routine do?"* lists the steps without switching anything.

**Ideas for extensions** (see [Feature list](#feature-list))
- Camera or screen context on request
- Room microphones (ESP32/satellites), several user profiles through speaker recognition
- More tool groups: Proxmox, vehicle APIs, fitness platforms
- An own voice from a voice sample (local), several languages at once

## Feature list

The plan per build-out phase is in `ROADMAP.md`, the inventory from 2026-11-02 in `INVENTORY.md`. Here is the feature list:

- [x] Wake word ("Jarvis …") as an alternative to the always-on microphone, follow-up questions without the wake word
- [x] Voice engine on the server: Silero VAD, adaptive end of sentence with partials, interrupting in 150 ms, resuming at the start of the sentence, metrics for Grafana
- [x] Voice engine for iPad/iPhone (through the remote HUD, `/voice-ws`)
- [ ] Room satellite with openWakeWord
- [ ] Try out echo cancellation for the Sonos with a reference signal (speexdsp) in everyday use
- [x] Timers, alarms, reminders and scheduled commands
- [x] Notices by himself (devices done, appointments, battery, NAS, new devices) with quiet hours
- [x] Shopping and to-do list, routines by voice
- [x] Fallback mode with a free replacement chain (Groq, Gemini, Ollama) including tools
- [x] Automated tests for the router
- [x] Commute times, parcel status, PC control, ducking, pronunciation, learning router, answer cache
- [x] Autostart and restart after a crash, crash watchdog in Home Assistant with a push notice
- [x] Learning loop, weekly review, homelab watcher, suggestion module
- [x] Action classes, audit log, emergency stop, memory control, protection against third-party instructions
- [x] Personality profiles, windows/files/screen, PC and network diagnosis, mail drafts, research, plans, notice centre
- [x] Resource manager, skill factory with approval, context engine, task engine, memory intelligence, forecasts, document search
- [x] PDF in the document search (pypdf), image description on request with a card per picture
- [ ] Mouse and keyboard control (deliberately not built; only as a separate, strictly controlled extension)
- [ ] A larger local model only in fallback mode (sharing VRAM with the GPU voice)
- [ ] Central control: screen, room microphones, speaker recognition (deliberately left out: one user)
- [x] Access from the phone: suggestions with yes/no buttons, answer through Home Assistant
- [x] Focus sessions with distraction hints
- [ ] Screen and camera context on request
- [ ] Several user profiles and room satellites (ESP32)
- [ ] Linux/macOS start scripts
- [x] More tool groups: Paperless (documents), UniFi (network controller), Jellyfin (media server)
- [ ] Hypervisor (Proxmox)
- [x] Episodic memory (conversations, Garmin, notices and outages per day), the user's habits, Garmin as a trigger, preparation before setting off, electricity budget, maintenance calendar, voice by time of day, asking back with memory, self-check, token tile, routine preview
- [x] Situation report from Uptime Kuma, the security centre and the energy loop, with notices by himself
- [x] Energy coach: predicting solar surplus, suggesting shiftable devices, remembering rarely used sockets
- [x] Electricity costs per device and standby, check when leaving the flat, weekly report on the home network, security findings by voice
- [x] Welcome back, severe weather warnings, habits in the house (routine suggestions, on too long), announcements in other rooms, Fire TV by voice
- [x] Answers through the Echo, morning briefing in the living room, follow-ups when switching (brighter, off again, in the bedroom too)
- [x] Automated tests for the voice chain
- [x] English edition (speech, prompts, intents, UI, docs); German edition in [jarvis-hud-de](https://github.com/Zedas030/jarvis-hud-de)

<sub>Originally inspired by <a href="https://github.com/Itsme23476/jarvis-claude-code">jarvis-claude-code</a>. Before passing it on independently, clarify the licence situation of the original.</sub>
