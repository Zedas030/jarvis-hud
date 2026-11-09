# Acceptance: proactive mode

As of 2026-11-05, about 15:45–16:00. Built on the room-aware commands (see
[acceptance-room-commands.md](acceptance-room-commands.md)), which therefore came first.
The run was done on the German edition; the spoken lines below are given in English.

**What "staged" means:** the user was out during the acceptance (`person.user = not_home`). Coming home or the
first trip to the bathroom in the morning cannot be triggered on command. So the acceptance script
(`scripts/proactive_acceptance.py`) set the presence to `home` and started the triggers itself.
**All data was real:** HA states, iCloud calendar, open-meteo, DWD, last night's notices, the Claude model, the
Echo in the living room. Nobody heard the announcements, because nobody was at home.
The script had its own state, history and notice file; the running Jarvis was not changed.

## Shown

| # | Requirement | Result |
|---|---|---|
| 1 | Regression tests green | Windows: **791 tests OK**, among them 11 new regression tests (`test_regression_notices.py`) written before the first change, plus the existing ones for the welcome, morning briefing, router and routines. Linux (`python:3.12-slim` in Docker on the Pi): all 76 proactive and regression tests OK. In the full Linux suite only `test_desktop.test_local_voice_commands` fails (a Windows command); it does the same on the base branch |
| 2 | Arrival briefing with real data | Spoken through the Echo in the living room, 32 words, worded by Claude: "Welcome back, Sir. The air purifier is offline. A parcel from Amazon is coming today. The weather: fog, 0 to 10 degrees, no rain, wind up to 8 kilometres per hour. Sunset at 16:29." Ordered by level: the important point first, the nice ones after |
| 3 | Morning briefing | "Good morning, Sir. The air purifier is offline. A parcel from Amazon is coming today. The weather: fog, between zero and ten degrees, chance of rain zero percent, … The energy loop is all fine again. There are also six more notices." That is 49 words, so within the 25 s; the rest of the night is on the iPad |
| 4 | Instant warning | The air purifier really had been offline in HA since 9:05. Spoken: "Air purifier is offline." (level important) |
| 5 | Quiet during the quiet hours, except critical | Quiet hours set to 15–16 h, plus a test water sensor. "Air purifier is offline." was **held back (quiet hours)**, "Attention: acceptance water sensor reports water!" was **spoken (critical)**. The output of this scenario was captured and did not go to a speaker |
| 6 | "Quiet for an hour" | Reply: "Understood, until 16:46 I will only say what is critical." The real HA switch `input_boolean.jarvis_quiet` went **on**, the offline notice was **held back (quiet)**, nothing was spoken. "End the quiet" gave "All right, I will speak up again.", and the switch went **off** again |
| 7 | No repeated notice | Two instant rounds right after each other: "Air purifier is offline." was spoken exactly **once**. In the arrival test (end to end), too, the same notice does not come again after the briefing |
| 8 | A switched-off source is skipped | With `sources.weather.enabled: false`, `weather` was not asked at all (asked: safety, dwd, noah, devices_offline, calendar, bsr, parcels, missed, energy). The briefing ran without weather |
| 8b | A failed source is skipped | `bsr` (waste collection; removed again on request on the evening of 2026-11-05) was switched on but had no key yet. The log said "source bsr is down and is skipped (neither BSR_SCHEDULE_ID nor BSR_ICS_URL in .env)", once per run and not once per round; every briefing still ran |
| 9 | History on the iPad | The HTTP part of Jarvis ran from the branch on port 8740, view `tablet`. The bell shows **ANNOUNCEMENTS · PROACTIVE** with time, level, room and spoken / held back with the reason. The button "not important" answered "noted", `/api/proactive` then reported `parcels: 1× not important` |
| 10 | HA switches | Created with `scripts/proactive_ha_setup.py`: `input_boolean.jarvis_proactive` (on), `input_boolean.jarvis_guest_mode` (off), `input_boolean.jarvis_quiet` (off) |

Covered by tests, not shown live:
- quiet rules as a table of all combinations of level, reason and mode (`test_quiet_rules.py`)
- guest mode and calls (iPhone focus, PC microphone with an exception list)
- away: important notices wait, critical ones also go to the phone
- learning by "not important": one level down after 3×, "critical" always stays critical, reset
- "tell me more", "good night" as an addition to the routine
- quiet ends when the HA switch is switched off by hand
- Home Assistant down: the briefing still runs
- feature flag off: everything as before
- end-to-end test with a fake HA and the real Jarvis HTTP server (`test_proactive_e2e.py`)

## Open (needs the user at home)

- **A real homecoming:** `person.user` changes to `home`, then a presence sensor; the briefing comes in the right
  room. The flow is checked in the end-to-end test with a fake HA, not yet with the real house.
- **A real bathroom visit in the morning** (5–11 h). Once the mode is live, it takes the morning briefing over
  from `morning_echo`.
- **iPhone focus:** `binary_sensor.user_focus` is "unavailable" in HA. The sensor has to be switched on in the HA
  app on the iPhone, otherwise only the PC microphone counts.

## Built this way on purpose

- **Length of the arrival briefing:** Claude needs 20–25 s to word it. So the briefing comes about 25 s after the
  room is known. If you want it faster, set `output.formulate_with_llm: false` and get the fixed text at once.
  For "good night" Jarvis waits at most 8 s for the model.
- **Video call in the browser:** the browser with the HUD keeps the microphone open all the time and is therefore
  on the exception list. A call in the browser is not noticed this way; Teams, Zoom, Discord and other own
  programs are.
- **No kitchen sensor:** in the morning only the bathroom triggers (your choice). Another sensor is one line in
  `triggers.morning.sensors`.
- **Calendar not instant:** appointments do not come as instant notices. "In 15 minutes: …" is already said by
  `watchers.calendar`; the mode would otherwise say it twice.
