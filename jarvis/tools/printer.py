"""
The 3D printer (Bambu Lab, ha-bambulab integration in Home Assistant) — read only, 0 tokens.

  describe(states, n)  "Wie weit ist der Drucker?", "Welches Filament ist im AMS?",
                       "Wie heiß ist die Düse?" — one spoken answer from the HA states
  handle(n, raw)       the router's entry: printer questions, never commands
  watch(states, say)   background (watchers.py): says when a print is done, failed, paused
                       or the printer reports an error. Only speaks, never switches anything.

The printer is found by its sensor "<prefix>_druckstatus" (or "_print_status" when HA runs in
English); JARVIS_PRINTER names the prefix if there are several, 0 switches it all off.
"""
import datetime as dt
import os
import re

PREFIX = os.environ.get("JARVIS_PRINTER", "").strip().lower()
OFF = PREFIX in ("0", "off", "aus", "false", "no")

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
    "paused_filament_runout": "das Filament ist leer",
    "paused_first_layer_error": "die erste Schicht sieht fehlerhaft aus",
    "paused_nozzle_filament_covered_detected": "an der Düse klebt Filament",
    "paused_cutter_error": "der Filamentschneider klemmt",
    "paused_front_cover_falling": "die Frontabdeckung ist offen",
    "paused_nozzle_temperature_malfunction": "die Düsentemperatur stimmt nicht",
    "paused_heat_bed_temperature_malfunction": "die Druckbetttemperatur stimmt nicht",
    "paused_spaghetti_defect": "die Kamera sieht Filamentsalat",
    "paused_ams_lost": "das AMS antwortet nicht",
    "paused_low_fan_speed_heat_break": "ein Lüfter dreht zu langsam",
    "paused_chamber_temperature_control_error": "die Druckraumtemperatur stimmt nicht",
    "paused_skipped_step": "ein Motor hat Schritte verloren",
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
        return f"{h} Stunde{'n' if h != 1 else ''} {m} Minute{'n' if m != 1 else ''}"
    if h:
        return f"{h} Stunde{'n' if h != 1 else ''}"
    return f"{m} Minute{'n' if m != 1 else ''}"


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
    return f" „{p['task']}“" if _known(p.get("task")) else ""


def _degrees(v):
    n = _num(v)
    return f"{round(n)} Grad" if n is not None else "?"


def _temps(p):
    parts = []
    for key, name in (("nozzle", "Düse"), ("bed", "Druckbett"), ("chamber", "Druckraum")):
        if _known(p.get(key)):
            target = _num(p.get(key + "_target"))
            parts.append(f"{name} {_degrees(p[key])}" + (f" von {round(target)}" if target else ""))
    return ", ".join(parts)


def _filament(p):
    if not p["slots"]:
        return "Ein AMS sehe ich nicht, Sir."
    rows = []
    for ams, n, s in p["slots"]:
        a = s.get("attributes") or {}
        name = s["state"] if _known(s["state"]) and not a.get("empty") else ""
        left = a.get("remain")
        where = f"Slot {n}" if len(p["humidity"]) <= 1 else f"AMS {ams} Slot {n}"
        rows.append(f"{where} {name}" + (f" ({left} Prozent übrig)" if name and isinstance(left, (int, float))
                                         and left >= 0 else "") if name else f"{where} leer")
    hum = "; ".join(f"Luftfeuchtigkeit {v} Prozent" for _, v in p["humidity"] if _known(v))
    return "Im AMS: " + ", ".join(rows) + "." + (f" {hum}." if hum else "")


def describe(states, n=""):
    p = find(states)
    if not p:
        return "Den 3D-Drucker finde ich in Home Assistant nicht, Sir."
    if p.get("online") == "off" or p["status"] in ("offline", "unavailable"):
        return "Der Drucker ist offline, Sir."
    if re.search(r"\b(filament|ams|spule\w*|rolle\w*|material)\b", n):
        return _filament(p)
    if re.search(r"\b(temperatur\w*|heiß|warm|grad|düse|bett|druckbett|druckraum)\b", n):
        return f"Am Drucker: {_temps(p)}."
    status = p["status"]
    text = {"idle": "Der Drucker ist frei, Sir.",
            "finish": f"Der letzte Druck{_task(p)} ist fertig, Sir.",
            "failed": f"Der letzte Druck{_task(p)} ist fehlgeschlagen, Sir."}.get(status)
    if status in BUSY:
        progress = _num(p.get("progress"))
        text = (f"Der Drucker {'ist pausiert bei' if status == 'pause' else 'druckt'}{_task(p)}"
                + (f": {round(progress)} Prozent" if progress is not None else ""))
        layer, layers = _num(p.get("layer")), _num(p.get("layers"))
        if layer and layers:
            text += f", Schicht {round(layer)} von {round(layers)}"
        why = _PAUSED.get(p.get("stage", ""))
        if status == "pause" and why:
            text += f" — {why}"
        remaining = _num(p.get("remaining"))
        if status != "pause" and remaining:
            end = _clock(p.get("end")) if _known(p.get("end")) else ""
            text += f". Noch etwa {_duration(remaining)}" + (f", fertig gegen {end} Uhr" if end else "")
        text += "."
    text = text or f"Der Drucker meldet: {status}."
    real, blind = _real(p)
    if real:
        text += f" Er meldet einen Fehler: {'; '.join(real)}."
    elif blind:
        text += " Er meldet einen Fehler — die Details stehen in der Bambu-App."
    return text


_ASK = re.compile(r"\b(3d drucker|drucker|druck|druckt|drucken|bambu|filament|ams|düse|druckbett|druckraum)\b")
_QUESTION = re.compile(r"\b(wie|was|wann|welche\w*|ist|sind|läuft|fertig|status|noch|weit|lange|druckt|gerade)\b")
# switching or paper printing: not a status question
_NOT = re.compile(r"\b(licht|lampe|beleuchtung|schalt\w*|mach\w*|stopp\w*|abbrech\w*|pausier\w*|fortsetz\w*|"
                  r"druck\w* .{0,25}(aus|datei|dokument|pdf|seite)|ausdrucken|papier|luftdruck|blutdruck)\b")


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
        say(f"printer_done:{job}", f"Sir, der 3D-Druck{_task(p)} ist fertig.")
    elif p["status"] == "failed" and last["status"] != "failed":
        say(f"printer_failed:{job}", f"Sir, der 3D-Druck{_task(p)} ist fehlgeschlagen. Schauen Sie bitte nach dem Drucker.")
    elif p["status"] == "pause" and last["status"] in ("running", "prepare"):
        why = _PAUSED.get(p.get("stage", ""))
        if why:                        # a pause the user pressed himself needs no notice
            say(f"printer_pause:{job}:{p.get('stage')}", f"Sir, der 3D-Drucker hat angehalten: {why}.")
    new = [t for t in real if t not in (last.get("real") or [])]
    if new:
        say(f"printer_error:{job}:{'|'.join(new)}"[:200], f"Sir, der 3D-Drucker meldet einen Fehler: {'; '.join(new)}.")
    elif blind and not last.get("blind"):
        if now["skipped"] != job and p["status"] in BUSY:
            now["skipped"] = job        # the first one of a print is the first-layer scan
        else:
            count = (p["_attr"].get("hms") or {}).get("Count")
            say(f"printer_error:{job}:{count}", "Sir, der 3D-Drucker meldet einen Fehler. Die Details stehen in der Bambu-App.")
