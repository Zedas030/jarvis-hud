# Proactive mode

Jarvis speaks up by himself – at the right moment, in the right room, and keeps quiet by clear rules.
Code: `jarvis/watch/proactive.py` (control), `quiet_rules.py` (quiet rules), `proactive_sources.py`
(sources), `proactive_config.py` (settings), `jarvis/core/mic_use.py` (microphone in use).

## Switching it on

```powershell
copy config\proactive.example.yaml config\proactive.yaml   # adapt the entities, enabled: true
.venv\Scripts\python.exe scripts\proactive_ha_setup.py     # creates the three HA switches (once)
```

`enabled: false` (or `JARVIS_PROACTIVE_MODE=0`) switches everything off: the welcome, the morning briefing and
the DWD warnings then run exactly as before through `away_check.py`, `morning_echo.py` and `weather_alerts.py`.
The file is read again whenever it changes, no restart needed.

## When Jarvis speaks

| Trigger | When | What |
|---|---|---|
| Coming home | `person.*` is `home` again after at least 20 min away | briefing as soon as a presence sensor knows the room (after 3 min at the latest, on the default speaker) |
| Morning | first entry into the bathroom after the night (5–11 h), once a day | weather, warnings, appointments, parcels, sun, what happened at night. Counts as the day's briefing |
| Instant | every round (30 s) | critical and important news from HA: water/smoke, NOAH difference, socket offline, DWD warnings |
| Good night | "Jarvis, good night" | appended to your routine "Good night": devices still on, open windows, an early appointment tomorrow |

A briefing lasts at most 25 seconds (about 60 words); what no longer fits only appears on the iPad.
Claude (Haiku, without tools) words it; if the model does not answer within 25 s, Jarvis speaks a fixed text
built from the notices. Spoken notices are remembered with a fingerprint: the same thing comes again only when
something changed (e.g. a new battery difference, a different appointment).

Every notice has a level: **critical** (water, smoke, severe weather), **important** (battery, device offline,
appointment soon), **nice to know** (weather, parcels, solar tip). The levels are in
`config/proactive.yaml → priorities`.

## When Jarvis keeps quiet

In each of these cases he speaks **only what is critical**:

| Reason | Detected by |
|---|---|
| Quiet hours | `quiet.hours` (default 22–7). The morning briefing ends the night |
| Quiet | "Jarvis, quiet for an hour", "… for 20 minutes", "no announcements today" (until 5 h) or the switch `input_boolean.jarvis_quiet` |
| Guests | switch `input_boolean.jarvis_guest_mode` or "guest mode on" |
| Phone / video call | a program uses the PC microphone (except the programs in `calls.mic_ignore`) or an entity from `calls.entities` is on (iPhone focus) |
| Not at home | `person.*` is not `home`: important notices wait for the arrival briefing, critical ones also go to the phone |
| Proactive off | switch `input_boolean.jarvis_proactive` off, or the emergency stop is active |

Important notices held back because of quiet, guests, a call or being away are said afterwards while they are
still valid. What was held during the quiet hours comes in the morning briefing, not all at once at 7:00.
"Nice to know" never interrupts; it waits for the next briefing.

About calls: the browser with the HUD keeps the microphone open for "Jarvis" all the time and is therefore in
`mic_ignore`. A video call **in the browser** is not noticed this way; Teams, Zoom, Discord and the Windows phone
app are. For calls on the iPhone, switch on the focus sensor of the HA app (iPhone: HA app → Settings → Companion
App → Sensors → Focus); then `binary_sensor.<phone>_focus` is on while a focus is active.

## Voice commands

| Say | Effect |
|---|---|
| "Jarvis, tell me more" | details of the last notice (after a briefing: of all its points) |
| "not important" / "weather not important" | feedback; after 3× within 30 days this kind of notice drops one level (critical never) |
| "What have you learned about announcements?" | lists the feedback |
| "Reset announcement feedback" / "Reset the weather announcement feedback" | everything or one kind back to its normal level |
| "Quiet for an hour" · "no announcements today" · "end the quiet" | quiet on/off (switches the HA switch too) |
| "Guest mode on/off" · "proactive mode on/off" | the HA switches |

Kinds of notice for feedback: weather, storm/warning, parcel, sun/solar/energy, appointment/calendar,
battery/NOAH, offline/socket/plug, devices, windows, missed.

## iPad and PC: history

The bell shows under **ANNOUNCEMENTS · PROACTIVE** the latest announcements with time, level, room and whether
they were spoken, only shown or held back – with the reason. Next to each the button **not important**; above,
what was learned with **reset**, a running quiet and sources that are down. Data: `GET /api/proactive`,
feedback: `POST /api/proactive/feedback`. The history is in `logs/proactive_history.jsonl` (last 500);
work appointments are not stored there.

## Sources

Each source can be switched off on its own in `sources.<name>.enabled`. If one fails (an error or no answer
within 12 s), it is skipped, noted once in the log (`logs/jarvis.log`: "source … is down", later "… is back")
and the briefing runs with the rest.

| Source | From |
|---|---|
| `calendar`, `parcels` | iCloud calendar (`ICLOUD_*` in `.env`); parcels are the "📦" entries from `shipments.py` |
| `weather` | open-meteo (`JARVIS_WEATHER_LAT/LON`) |
| `dwd` | warnings of the German weather service (DWD) through Bright Sky |
| `energy` | energy coach (solar surplus) |
| `noah` | HA switch `input_boolean.noah_diff_warning` (set by the HA power automation from 6 % difference), plus master and slave SoC |
| `devices_offline` | `binary_sensor.*_cloud_connection` off for longer than 15 min |
| `safety` | `binary_sensor` with `device_class` moisture/smoke/gas/carbon_monoxide – as soon as such sensors are in HA |
| `devices_on`, `windows` | in the evening: what is still running (as when leaving), open windows/doors |
| `missed` | coming home / in the morning: what Jarvis announced in the meantime |

## Secrets

No credentials in `config/proactive.yaml`. Everything secret is in `.env`:

| Variable | What for | Change / rotate |
|---|---|---|
| `HA_TOKEN`, `HA_URL` | read Home Assistant, set switches, announcements | HA → Profile → Security → create a new long-lived token, put it in `.env`, restart Jarvis, delete the old token |
| `ICLOUD_USER`, `ICLOUD_APP_PASSWORD` | appointments | account.apple.com → app-specific passwords: create a new one, put it in, revoke the old one |

## Linux server and Docker

The mode contains nothing that only runs on Windows: paths come from environment variables
(`JARVIS_PROACTIVE_CONFIG`, `JARVIS_PROACTIVE_HISTORY`), the switch from `JARVIS_PROACTIVE_MODE`.
On Linux `mic_use.py` finds no PC microphone and reports nothing; there, put an HA entity into `calls.entities`
that the PC provides (e.g. HASS.Agent "microphone in use"). The tests run on Linux in `python:3.12-slim`
(shown on the Pi, see `docs/acceptance-proactive.md`).

## Diagnosis

```powershell
# run one trigger now with real data (only on the PC)
curl -X POST http://127.0.0.1:8720/api/proactive/trigger -H "X-Jarvis-Token: <from the HUD>" `
     -H "Origin: http://127.0.0.1:8720" -H "Content-Type: application/json" -d '{\"mode\":\"arrival\"}'
.venv\Scripts\python.exe -m unittest tests.test_quiet_rules tests.test_proactive tests.test_proactive_e2e -v
```
