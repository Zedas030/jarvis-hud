"""
The 3D printer (Bambu Lab, ha-bambulab integration in Home Assistant) — read only, 0 tokens.

  describe(states, n)  "How far along is the printer?", "Which filament is in the AMS?",
                       "How hot is the nozzle?" — one spoken answer from the HA states
  handle(n, raw)       the router's entry: printer questions, never commands
  watch(states, say)   background (watchers.py): says when a print is done, failed, paused
                       or the printer reports an error. Only speaks, never switches anything.

The printer is found by its sensor "<prefix>_print_status" (or "_druckstatus" when HA runs in
German); JARVIS_PRINTER names the prefix if there are several, 0 switches it all off.
"""
import datetime as dt
import os
import re

PREFIX = os.environ.get("JARVIS_PRINTER", "").strip().lower()
OFF = PREFIX in ("0", "off", "false", "no")

# what the integration calls each value, German and English entity ids
_KEYS = {
    "status": ("druckstatus", "print_status"),
    "progress": ("druckfortschritt", "print_progress"),
    "remaining": ("verbleibende_zeit", "remaining_time"),
    "end": ("endzeit", "end_time"),
    "start": ("startzeit", "start_time"),
    "layer": ("aktuelle_schicht", "current_layer"),
    "layers": ("gesamtzahl_der_schichten", "total_layer_count"),
    "task": ("name_der_aufgabe", "task_name"),
    "stage": ("aktueller_arbeitsschritt", "current_stage"),
    "nozzle": ("temperatur_der_duse", "nozzle_temperature"),
    "nozzle_target": ("zieltemperatur_der_duse", "nozzle_target_temperature"),
    "bed": ("druckbetttemperatur", "bed_temperature"),
    "bed_target": ("zieltemperatur_vom_druckbett", "target_bed_temperature"),
    "chamber": ("temperatur_im_druckraum", "chamber_temperature"),
    "online": ("online",),
    "error": ("druckfehler", "print_error"),
    "hms": ("hms_fehler", "hms_errors"),
}
BUSY = ("running", "prepare", "pause", "slicing", "init")
# why a print stopped by itself, from the current stage
_PAUSED = {
    "paused_filament_runout": "the filament has run out",
    "paused_first_layer_error": "the first layer looks faulty",
    "paused_nozzle_filament_covered_detected": "filament is stuck to the nozzle",
    "paused_cutter_error": "the filament cutter is jammed",
    "paused_front_cover_falling": "the front cover is open",
    "paused_nozzle_temperature_malfunction": "the nozzle temperature is off",
    "paused_heat_bed_temperature_malfunction": "the print bed temperature is off",
    "paused_spaghetti_defect": "the camera sees spaghetti",
    "paused_ams_lost": "the AMS does not answer",
    "paused_low_fan_speed_heat_break": "a fan is spinning too slowly",
    "paused_chamber_temperature_control_error": "the chamber temperature is off",
    "paused_skipped_step": "a motor has skipped steps",
}
_LAST = {}            # prefix -> {"status", "error", "hms", "real", "job", "blind"} of the previous round
# The first "error" of every print is the printer announcing its first-layer scan (Micro Lidar),
# not a fault. Real first-layer defects still come through as the "paused_first_layer_error" stage.
_SCAN_TEXT = re.compile(r"first.?layer|erste.?schicht|lidar|\bscan\w*|inspect\w*|inspiz\w*", re.I)
_SCAN_STAGES = ("inspecting_first_layer", "scanning_bed_surface", "identifying_build_plate_type",
                "calibrating_micro_lidar", "first_layer_scan")


def find(states):
    """-> {"prefix", key: state, "_attr": {key: attributes}, "slots": [...], "humidity": [...]} or None."""
    if OFF:
        return None
    by = {s["entity_id"]: s for s in states or []}
    prefix = PREFIX
    if not prefix:
        hits = sorted(e[len("sensor."):-len("_" + suffix)] for e in by for suffix in _KEYS["status"]
                      if e.startswith("sensor.") and e.endswith("_" + suffix))
        if not hits:
            return None
        prefix = hits[0]
    out = {"prefix": prefix, "_attr": {}}
    for key, names in _KEYS.items():
        for dom in ("sensor", "binary_sensor"):
            s = next((by[f"{dom}.{prefix}_{n}"] for n in names if f"{dom}.{prefix}_{n}" in by), None)
            if s:
                out[key], out["_attr"][key] = s["state"], s.get("attributes") or {}
                break
    if "status" not in out:
        return None
    slot = re.compile(rf"sensor\.{re.escape(prefix)}_ams_(\d+)_slot_(\d+)$")
    hum = re.compile(rf"sensor\.{re.escape(prefix)}_ams_(\d+)_(luftfeuchtigkeit|humidity)$")
    out["slots"] = sorted((int(m.group(1)), int(m.group(2)), by[e]) for e in by for m in [slot.match(e)] if m)
    out["humidity"] = sorted((int(m.group(1)), by[e]["state"]) for e in by for m in [hum.match(e)] if m)
    return out


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _known(v):
    return v not in (None, "", "unknown", "unavailable", "none", "?")


def _duration(hours):
    mins = round((hours or 0) * 60)
    h, m = divmod(mins, 60)
    if h and m:
        return f"{h} hour{'s' if h != 1 else ''} {m} minute{'s' if m != 1 else ''}"
    if h:
        return f"{h} hour{'s' if h != 1 else ''}"
    return f"{m} minute{'s' if m != 1 else ''}"


def _clock(iso):
    try:
        t = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return ""
    return (t.astimezone() if t.tzinfo else t).strftime("%H:%M")


def _messages(p):
    """-> (texts, blind): the readable error/HMS messages, and whether an error is on without any."""
    texts, blind = [], False
    for key in ("error", "hms"):
        if p.get(key) != "on":
            continue
        a = p["_attr"].get(key) or {}
        # ha-bambulab lists HMS messages as "1-Error", "1-Severity", ...; print_error as "error"/"code"
        numbered = [k for k in a if re.fullmatch(r"\d+-Error", str(k)) and _known(a[k])]
        if numbered:
            texts += [str(a[k]) for k in numbered
                      if str(a.get(k.split("-")[0] + "-Severity", "")).lower() != "info"]
            continue                    # only info messages: nothing to report
        found = [str(a[k]) for k in ("error", "Error", "message", "description") if _known(a.get(k))]
        texts += found
        blind = blind or not found
    return texts, blind


def _real(p):
    """-> (real messages, blind error on): the scan notice and info messages left out."""
    texts, blind = _messages(p)
    real = [t.strip().rstrip(".") for t in texts if not _SCAN_TEXT.search(t)]
    return real, blind and p.get("stage") not in _SCAN_STAGES


def _task(p):
    return f" “{p['task']}”" if _known(p.get("task")) else ""


def _degrees(v):
    n = _num(v)
    return f"{round(n)} degrees" if n is not None else "?"


def _temps(p):
    parts = []
    for key, name in (("nozzle", "nozzle"), ("bed", "print bed"), ("chamber", "chamber")):
        if _known(p.get(key)):
            target = _num(p.get(key + "_target"))
            parts.append(f"{name} {_degrees(p[key])}" + (f" of {round(target)}" if target else ""))
    return ", ".join(parts)


def _filament(p):
    if not p["slots"]:
        return "I do not see an AMS, Sir."
    rows = []
    for ams, n, s in p["slots"]:
        a = s.get("attributes") or {}
        name = s["state"] if _known(s["state"]) and not a.get("empty") else ""
        left = a.get("remain")
        where = f"Slot {n}" if len(p["humidity"]) <= 1 else f"AMS {ams} Slot {n}"
        rows.append(f"{where} {name}" + (f" ({left} percent left)" if name and isinstance(left, (int, float))
                                         and left >= 0 else "") if name else f"{where} empty")
    hum = "; ".join(f"humidity {v} percent" for _, v in p["humidity"] if _known(v))
    return "In the AMS: " + ", ".join(rows) + "." + (f" {hum.capitalize()}." if hum else "")


def describe(states, n=""):
    p = find(states)
    if not p:
        return "I cannot find the 3D printer in Home Assistant, Sir."
    if p.get("online") == "off" or p["status"] in ("offline", "unavailable"):
        return "The printer is offline, Sir."
    if re.search(r"\b(filament|ams|spools?|rolls?|material)\b", n):
        return _filament(p)
    if re.search(r"\b(temperatures?|hot|warm|degrees|nozzle|bed|print bed|chamber)\b", n):
        return f"At the printer: {_temps(p)}."
    status = p["status"]
    text = {"idle": "The printer is free, Sir.",
            "finish": f"The last print{_task(p)} is finished, Sir.",
            "failed": f"The last print{_task(p)} failed, Sir."}.get(status)
    if status in BUSY:
        progress = _num(p.get("progress"))
        text = (f"The printer {'is paused at' if status == 'pause' else 'is printing'}{_task(p)}"
                + (f": {round(progress)} percent" if progress is not None else ""))
        layer, layers = _num(p.get("layer")), _num(p.get("layers"))
        if layer and layers:
            text += f", layer {round(layer)} of {round(layers)}"
        why = _PAUSED.get(p.get("stage", ""))
        if status == "pause" and why:
            text += f" — {why}"
        remaining = _num(p.get("remaining"))
        if status != "pause" and remaining:
            end = _clock(p.get("end")) if _known(p.get("end")) else ""
            text += f". About {_duration(remaining)} to go" + (f", done around {end}" if end else "")
        text += "."
    text = text or f"The printer reports: {status}."
    real, blind = _real(p)
    if real:
        text += f" It reports an error: {'; '.join(real)}."
    elif blind:
        text += " It reports an error — the details are in the Bambu app."
    return text


_ASK = re.compile(r"\b(3d printer|printer|print|printing|bambu|filament|ams|nozzle|print bed|chamber)\b")
_QUESTION = re.compile(r"\b(how|what|whats|when|which|is|are|running|done|finished|status|still|far|long|printing|now)\b")
# switching or paper printing: not a status question
_NOT = re.compile(r"\b(light|lamp|lighting|switch\w*|turn\w*|stop\w*|cancel\w*|pause|resume|"
                  r"print (out|this|the (file|document|pdf|page))|print\w* .{0,25}(file|document|pdf|page)|paper|"
                  r"air pressure|blood pressure)\b")


def handle(n, raw=""):
    if OFF or not _ASK.search(n) or not _QUESTION.search(n) or _NOT.search(n):
        return None
    try:
        import briefing
        states = briefing._ha_states()
    except Exception:  # noqa: BLE001 — HA down: the LLM path can still try
        return None
    return describe(states, n) if find(states) else None


def watch(states, say):
    """One round: say(key, text) on a finished, failed or paused print and on a new error.
    The first round after a start only remembers — no notice for what was already so.
    The first-layer scan at the start of every print is not an error: messages about it are
    left out, and an error without readable details is skipped once per print."""
    p = find(states)
    if not p:
        return
    last = _LAST.get(p["prefix"])
    job = f"{p.get('task', '')}|{p.get('start', '')}"[:120]
    real, blind = _real(p)
    now = {"status": p["status"], "error": p.get("error"), "hms": p.get("hms"), "stage": p.get("stage"),
           "real": real, "blind": blind, "skipped": (last or {}).get("skipped")}
    _LAST[p["prefix"]] = now
    if last is None or p["status"] in ("unknown", "unavailable"):
        return
    was_busy = last["status"] in BUSY
    if p["status"] == "finish" and was_busy:
        say(f"printer_done:{job}", f"Sir, the 3D print{_task(p)} is finished.")
    elif p["status"] == "failed" and last["status"] != "failed":
        say(f"printer_failed:{job}", f"Sir, the 3D print{_task(p)} failed. Please have a look at the printer.")
    elif p["status"] == "pause" and last["status"] in ("running", "prepare"):
        why = _PAUSED.get(p.get("stage", ""))
        if why:                        # a pause the user pressed himself needs no notice
            say(f"printer_pause:{job}:{p.get('stage')}", f"Sir, the 3D printer has stopped: {why}.")
    new = [t for t in real if t not in (last.get("real") or [])]
    if new:
        say(f"printer_error:{job}:{'|'.join(new)}"[:200], f"Sir, the 3D printer reports an error: {'; '.join(new)}.")
    elif blind and not last.get("blind"):
        if now["skipped"] != job and p["status"] in BUSY:
            now["skipped"] = job        # the first one of a print is the first-layer scan
        else:
            count = (p["_attr"].get("hms") or {}).get("Count")
            say(f"printer_error:{job}:{count}", "Sir, the 3D printer reports an error. The details are in the Bambu app.")
