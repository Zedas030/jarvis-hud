# Acceptance: room awareness

As of 2026-11-05, 14:45. The user was not at home at the time (`person.user` = `not_home`, helper "Unknown").
The run was done on the German edition; commands and replies below are given in English.

## Shown

| Point | Evidence | Result |
|---|---|---|
| Regression tests before | `unittest discover -s tests` on the base commit, before the first change | 683 tests, OK |
| Regression tests after | the same suite with the room commands | 714 tests (683 + 31 new), OK |
| Feature flag off = old behaviour | `test_room_scope.Fallbacks.test_switched_off_behaves_as_before` | OK |
| HA unreachable = old behaviour | `Fallbacks.test_home_assistant_down_behaves_as_before`, `Presence.test_hiccup_then_stale` | OK |
| Sonos unavailable → the device answers, says why once | `Output.test_unavailable_sonos`, `test_failed_announcement`, `test_presence_down_told_once` | OK |
| Devices per room from the real HA areas | `scripts/room_probe.py` against the real HA | Living room: LED, TV, 2 thermostats · Bedroom: 2 thermostats, air purifier, TV bedroom, Sonos, iPad socket |
| Commands against the real devices (room given, switching captured) | trial run with the real HA, `jarvis_mcp._home` recorded | Living room: "light on" → the LED, "TV on" → `switch.tv`, "heating on" → living-room thermostats · Bedroom: "light on" → question, "yes" → the LED; "TV on" → `switch.tv_bedroom`; "warmer" → bedroom thermostats · unsure: "TV on" → "Living room or Bedroom?", "Bedroom" → `switch.tv_bedroom` |

## Partial live acceptance 2026-11-05, 16:37–16:45

User at home, the change in the running Jarvis (restart 16:39), `scripts\room_probe.py --watch` running alongside.

| Point | Evidence | Result |
|---|---|---|
| Live suite | `unittest discover -s tests` in the live folder, together with the open changes of another session | OK |
| Room detected live | `room_probe.py`: Living room, sure, output Echo living room | OK |
| iPad data | `/api/dashboard` → `house.where` = Living room, sure, online | OK (the tile itself not seen) |
| "Light off" / "light on" in the living room | `logs/audit.jsonl` 16:41:26 / 16:41:30, local (0 tokens) | OK |
| Radar ghost | bedroom sensor briefly busy (16:40:10, 16:42:24), the helper stayed on the living room, no room change | OK: no wrong change |

Found and fixed (16:47): music through Spotify Connect on the living-room soundbar did not get quieter when the
Echo answered. The event `room_audio` (answer through the Echo or Sonos) did not call `duckMusic` in the browser,
only the output on the device itself did. Fixed in `ui/voice-client.js`. Confirmed live at 16:56:
`logs/spotify.log` "quieter 17 % → 5 %", back to 17 % after the answer; user: "music gets quieter, fine".

Noticed: Jarvis had not been running since about 12:09 before the test (no crash in the log), restarted with
`start-jarvis.bat`.

Also found and fixed: a learned wording ("light on" → the LED) skipped the question in the bedroom, and the Sonos
switch "Bedroom TV autoplay" counted as a device. Both have a test now.

## Open (needs the user at home and the change live)

- [ ] A real change of room: run `scripts\room_probe.py --watch`, walk living room → bedroom → bathroom → living room
- [x] "Light on" in the living room switches the LED (live 2026-11-05, 16:41)
- [ ] In the bedroom "light on" brings the question. There is no second switchable light in the flat
- [ ] Answer through the Sonos in the bedroom while music plays there: the music gets quieter and plays on afterwards (volume, track, grouping)
- [ ] The question while walking (helper still on the old room, the new sensor busy)
- [ ] Fallback: Sonos without power → "The speaker … cannot be reached right now" on the device; HA stopped → "I cannot see which room you are in right now"
- [ ] iPad: the "Room" tile visible (the data arrives, the display on the iPad not seen)

## Deliberately not built

- A confidence score of its own from many signals: the hold logic against radar ghosts is already in the HA automation "Jarvis: where is the user". Jarvis only checks whether the sensors contradict the helper right now.
- "No output in rooms where someone else sleeps": nobody else sleeps in the flat. Answers only go to your own room anyway, at night at the night volume.
- YAML configuration for this: Jarvis uses `config/home.json` and `.env` throughout here, a second format would be a break.
