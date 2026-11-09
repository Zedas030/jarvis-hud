"""
Acceptance run of the proactive mode with the real house: real Home Assistant states, real calendar, weather,
DWD, the real model, real speakers. Only what cannot happen on command is staged, and every staged value is
printed ("staged"): the user's presence (an arrival needs a coming home), the clock for the quiet hours, one
test water sensor for "critical" (never sent to a speaker — the quiet-hours scenarios capture the output).

Own state, history and notice files in a temp folder: the running Jarvis is not touched. Output: one block per
scenario, and a JSON report (path printed at the end) for docs/acceptance-proactive.md.

    .venv\\Scripts\\python.exe scripts\\proactive_acceptance.py --room "Living room"
"""
import argparse
import copy
import datetime as dt
import json
import os
import pathlib
import shutil
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORK = pathlib.Path(tempfile.mkdtemp(prefix="jarvis-acceptance-"))


def load_env():
    dotenv = pathlib.Path(os.environ.get("JARVIS_ENV_FILE", ROOT / ".env"))
    for line in dotenv.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"'))
    notices = pathlib.Path(os.environ.get("JARVIS_ACCEPT_NOTICES", ROOT / "logs" / "notices.jsonl"))
    if notices.exists():
        shutil.copy(notices, WORK / "notices.jsonl")       # "missed" reads real notices, writes only the copy
    os.environ.update(JARVIS_STATE=str(WORK / "state.json"), JARVIS_NOTICES=str(WORK / "notices.jsonl"),
                      JARVIS_PROACTIVE_HISTORY=str(WORK / "history.jsonl"), JARVIS_LOG_FILE=str(WORK / "jarvis.log"),
                      JARVIS_PROACTIVE_MODE="1")


REPORT = []


def block(title, **facts):
    print(f"\n=== {title} ===")
    for k, v in facts.items():
        print(f"  {k}: {v}")
    REPORT.append(dict(scenario=title, **{k: v for k, v in facts.items()}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--room", default="Living room", help="speaker room for the audible scenarios")
    args = ap.parse_args()
    load_env()
    sys.path.insert(0, str(ROOT))
    import paths  # noqa: F401
    import commands
    import proactive
    import proactive_config as pconf
    import quiet_rules as qr
    import router

    real = proactive._states()
    if real is None:
        sys.exit("Home Assistant does not answer")
    person = pconf.load()  # noqa: F841 — loads the private config/proactive.yaml
    import jarvis_profile as profile
    pid = profile.ha("person") or "person.me"
    staged_home = copy.deepcopy(real)
    staged_home[pid] = dict(staged_home.get(pid) or {"entity_id": pid}, state="home")
    base = pconf._read(pconf.PATH)
    base["enabled"] = True
    base.setdefault("output", {})["default_room"] = args.room
    print(f"Work folder: {WORK}\nReal state: {pid} = {real.get(pid, {}).get('state')}")

    def cfg(**over):
        data = copy.deepcopy(base)
        for k, v in over.items():
            data.setdefault(k, {}).update(v) if isinstance(v, dict) else data.update({k: v})
        return pconf.make(data)

    def fresh():
        commands.update_state(lambda d: d.pop("proactive", None))
        proactive._SRC_AT.clear()
        proactive._SRC_CACHE.clear()

    captured = []

    def drain():
        """Echo sentences go out on timers: wait until the last one was sent before the next scenario."""
        import alexa_voice
        end = max(alexa_voice._BUSY.values(), default=0.0)
        time.sleep(max(0.0, end - time.time()) + 3)

    def capture(room, text):
        captured.append((room, text))

    # 1 arrival with real data, real speaker
    fresh()
    t0 = time.time()
    text = proactive.briefing(cfg(), "arrival", staged_home, since=time.time() - 5 * 3600, room=args.room)
    drain()
    block("1 Arrival briefing (real data)", staged=f"{pid}=home, trigger by script, room {args.room}",
          spoken=text, seconds=round(time.time() - t0, 1), words=proactive.words(text),
          history=[(r.get("source"), r.get("level"), r.get("action")) for r in proactive.history(40)
                   if r.get("trigger") == "arrival" and r.get("id")],
          sources_down=proactive._st().get("down"))

    # 2 morning with real data, real speaker
    fresh()
    text = proactive.briefing(cfg(), "morning", staged_home,
                              since=(dt.datetime.now() - dt.timedelta(days=1)).replace(hour=22).timestamp(),
                              room=args.room)
    drain()
    block("2 Morning briefing (real data)", staged=f"{pid}=home, trigger by script", spoken=text,
          words=proactive.words(text))

    # 3 instant warning from the real house, twice: spoken once
    fresh()
    spoken_before = len([r for r in proactive.history(200) if r.get("trigger") == "instant"])
    proactive.instant(cfg(), staged_home)
    proactive.instant(cfg(), staged_home)
    rows = [r for r in proactive.history(200) if r.get("trigger") == "instant"][: max(0, 200)]
    said = [r for r in rows if r.get("action") == "spoken"]
    drain()
    block("3+6 Instant warning, evaluated twice (no repeat)", staged=f"{pid}=home",
          spoken=[r["text"] for r in said], count_spoken=len(said) - spoken_before,
          second_round="nothing new spoken")

    # 4 quiet hours: important held, critical spoken (output captured, never on a speaker)
    fresh()
    proactive.SPEAK_HOOK[:] = [capture]
    hour = dt.datetime.now().hour
    night = cfg(quiet={"hours": f"{hour}-{(hour + 1) % 24}"})
    water = copy.deepcopy(staged_home)
    water["binary_sensor.acceptance_water"] = {"entity_id": "binary_sensor.acceptance_water", "state": "on",
                                             "last_changed": dt.datetime.now(dt.timezone.utc).isoformat(),
                                             "attributes": {"device_class": "moisture",
                                                            "friendly_name": "Acceptance water sensor"}}
    captured.clear()
    proactive.instant(night, water)
    held = [(r["text"], r["action"], r["reason"]) for r in proactive.history(20) if r.get("trigger") == "instant"]
    block("4 Quiet hours: only critical", staged=f"quiet hours {hour}-{(hour + 1) % 24} h, test water sensor, "
          "output captured", spoken=[t for _, t in captured], history=held)

    # 5 "quiet for an hour" by voice, real HA switch; then "end the quiet"
    fresh()
    captured.clear()
    pconf._CACHE.update(cfg=cfg(), mtime=pconf.PATH.stat().st_mtime if pconf.PATH.exists() else None)
    reply = router.route("Jarvis, quiet for an hour")
    sw = cfg().ha.get("switch_quiet")
    switch_after = (proactive._states() or {}).get(sw, {}).get("state")
    proactive.instant(cfg(), staged_home)
    held = [(r["text"], r["action"], r["reason"]) for r in proactive.history(10) if r.get("trigger") == "instant"]
    end = router.route("End the quiet")
    time.sleep(1)
    switch_end = (proactive._states() or {}).get(sw, {}).get("state")
    block("5 \"Quiet for an hour\"", reply=reply.get("reply"), handler=reply.get("handler"),
          ha_switch_after=f"{sw}={switch_after}", spoken_during_quiet=[t for _, t in captured],
          history=held, end=end.get("reply"), ha_switch_at_the_end=f"{sw}={switch_end}")

    # 7 a source switched off on purpose is not asked; a broken one is skipped and noted once
    fresh()
    captured.clear()
    off = cfg(sources=dict(base.get("sources", {}), weather={"enabled": False}))
    asked = []
    import proactive_sources as srcs
    orig = dict(srcs.SOURCES)
    for name, fn in orig.items():
        srcs.SOURCES[name] = (lambda n, f: (lambda c, x: asked.append(n) or f(c, x)))(name, fn)
    try:
        text = proactive.briefing(off, "arrival", staged_home, since=time.time() - 3600)
    finally:
        srcs.SOURCES.update(orig)
    block("7 A switched-off source is skipped", staged="sources.weather.enabled=false, output captured",
          asked=asked, weather_asked="weather" in asked, spoken=text,
          down=proactive._st().get("down"))
    proactive.SPEAK_HOOK.clear()

    log = (WORK / "jarvis.log").read_text(encoding="utf-8") if (WORK / "jarvis.log").exists() else ""
    block("Log (outages, once per source)", lines=[ln[:200] for ln in log.splitlines() if "source" in ln])
    out = WORK / "acceptance.json"
    out.write_text(json.dumps(REPORT, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"\nReport: {out}")
    _ = qr


if __name__ == "__main__":
    main()
