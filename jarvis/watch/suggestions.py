"""
Suggestion module: twice a week Jarvis proposes one new ability that fits the
user's habits, with a reason.

Basis (last 14 days, work topics filtered out before the model sees anything):
the usage log (what still goes to the LLM, which tool groups, at what times),
the routines that exist, the learned rules and the earlier suggestions (so
nothing comes twice). One background call picks a single suggestion:

  routine     something the user does by voice again and again → a routine
  erinnerung  a recurring moment worth a reminder
  idee        needs new code → only explained and noted; Jarvis never changes
              his own code, that stays with the user in Claude Code

Routines and reminders are only set up after a confirmation card in the next
conversation (proposals.py). Every suggestion is listed in the note
Erinnerungen/Vorschläge with its status (offen, angenommen, abgelehnt).

When: JARVIS_SUGGEST_DAYS (weekday numbers, Monday = 0; default "1,4" = Tuesday
and Friday) at JARVIS_SUGGEST_TIME (18:30), never in the quiet hours.
"""
import collections
import datetime as dt
import json
import os
import pathlib
import re
import time

import background
import events
import memory
import privacy
import proposals

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
USAGE_LOG = ROOT / "logs" / "usage.jsonl"
DAYS = [int(x) for x in os.environ.get("JARVIS_SUGGEST_DAYS", "1,4").split(",") if x.strip().isdigit()]
AT = os.environ.get("JARVIS_SUGGEST_TIME", "18:30").strip()
ENABLED = os.environ.get("JARVIS_SUGGEST", "1").strip() not in ("0", "false", "no", "off")
NOTE = "Vorschläge"
STATUS = {"angenommen", "abgelehnt", "offen"}

CAPABILITIES = ("Licht/Steckdosen/Szenen (Home Assistant), Timer, Wecker, Erinnerungen, Einkaufs- und To-do-Liste, "
                "Routinen auf einen Satz, Wetter, Kalender lesen/eintragen, Mails lesen, Spotify, Pi/NAS/Netzwerk-Status, "
                "Garmin-Gesundheitsdaten, Wochenrückblick, Fahrzeit zu Terminen, Paketstatus, PC-Lautstärke/Apps, "
                "Websuche, Gedächtnis-Notizen, Tagesbriefing")

SYSTEM = ("Du bist JARVIS und schlägst dem User genau eine neue Fähigkeit vor, die zu seinen beobachteten "
          "Gewohnheiten passt. Konkret, nützlich, nicht schon vorhanden, nichts aus der Arbeit. Antworte nur mit JSON.")


def _usage(days=14, now=None):
    since = (now or time.time()) - days * 86400
    rows = []
    try:
        with USAGE_LOG.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                    t = time.mktime(time.strptime(r["at"], "%Y-%m-%d %H:%M:%S"))
                except (ValueError, KeyError):
                    continue
                if t >= since and not str(r.get("route", "")).startswith("background"):
                    rows.append(dict(r, t=t))
    except OSError:
        pass
    return rows


def habits(now=None):
    """A compact, work-free picture of the last two weeks."""
    rows = [r for r in _usage(now=now) if not privacy.is_work(r.get("message", ""))]
    if not rows:
        return ""
    routes = collections.Counter(r["route"].split(":")[0] + (":" + r["route"].split(":")[1]
                                                             if r["route"].startswith("local:") else "") for r in rows)
    groups = collections.Counter(g for r in rows for g in r.get("groups") or [] if g != "memory")
    hours = collections.Counter(time.localtime(r["t"]).tm_hour for r in rows)
    llm = collections.Counter(" ".join(r.get("message", "").lower().split())[:70]
                              for r in rows if r["route"].startswith(("llm", "fallback")))
    days = collections.Counter(time.strftime("%a", time.localtime(r["t"])) for r in rows)
    return "\n".join([
        f"Anfragen in 14 Tagen: {len(rows)}",
        "Wege: " + ", ".join(f"{k} {v}" for k, v in routes.most_common(8)),
        "Werkzeuggruppen: " + ", ".join(f"{k} {v}" for k, v in groups.most_common(8)),
        "Uhrzeiten (Stunde: Anzahl): " + ", ".join(f"{h}: {n}" for h, n in sorted(hours.items())),
        "Wochentage: " + ", ".join(f"{d} {n}" for d, n in days.most_common()),
        "Häufige Fragen, die noch ans Sprachmodell gehen:\n" + "\n".join(f"- {m} ({n}×)" for m, n in llm.most_common(12)),
    ])


def _note_path():
    return memory.MEMORY_DIR / f"{NOTE}.md"


def earlier():
    p = _note_path()
    if not p.exists():
        return []
    return [ln[2:].strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.startswith("- ")]


def _record(title, why, status="offen"):
    p = _note_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    if not p.exists():
        p.write_text(f"---\ntype: erinnerung\nwichtigkeit: niedrig\nupdated: {today}\n---\n\n# {NOTE}\n\n"
                     "Fähigkeiten, die Jarvis vorgeschlagen hat. Gehört zu [[Profil]].\n", encoding="utf-8")
    body = re.sub(r"(?m)^updated: .*$", f"updated: {today}", p.read_text(encoding="utf-8"), count=1)
    p.write_text(body.rstrip() + f"\n- {today} · {status} · {title} — {why}\n", encoding="utf-8")


def mark(proposal, status):
    """Updates the status of a suggestion in the note (no-op for proposals from other modules)."""
    if proposal.get("source") != "suggestions" or status not in STATUS:
        return
    p = _note_path()
    if not p.exists():
        return
    body = p.read_text(encoding="utf-8")
    pat = re.compile(r"(?m)^(- [\d-]+ · )offen( · " + re.escape(proposal["title"]) + r" — )")
    p.write_text(pat.sub(lambda m: m.group(1) + status + m.group(2), body, count=1), encoding="utf-8")


def _prompt(now):
    import reflection
    import routines
    rs = [r.get("name") for r in routines.load()]
    return ("Beobachtete Gewohnheiten:\n" + (habits() or "(noch kaum Daten)") +
            f"\n\nJetzt: {now.strftime('%A, %d.%m.%Y %H:%M')}"
            f"\nSchon vorhanden: {CAPABILITIES}"
            f"\nRoutinen: {', '.join(rs) or 'keine'}"
            f"\nGelernte Regeln: {'; '.join(reflection.rules_list()) or 'keine'}"
            "\nFrühere Vorschläge (nicht wiederholen):\n" + ("\n".join(f"- {e}" for e in earlier()[-15:]) or "- keine") +
            "\n\nSchlag genau eine neue Fähigkeit vor. JSON:\n"
            "{\"titel\": \"kurz\", \"begruendung\": \"ein Satz, welche Gewohnheit dazu passt\", "
            "\"art\": \"routine|erinnerung|idee\", "
            "\"routine\": {\"name\": \"...\", \"triggers\": [\"kleingeschriebener satz\"], \"steps\": "
            "[{\"do\": \"einfacher deutscher Befehl\"} oder {\"tell\": \"deutsche Frage\"} oder {\"say\": \"Text\"}]}, "
            "\"erinnerung\": {\"at\": \"YYYY-MM-DD HH:MM\", \"text\": \"...\"}}\n"
            "routine/erinnerung nur bei der passenden art. Was neuen Code bräuchte, ist art idee.")


def suggest(now=None):
    """Creates one suggestion and announces it. Returns the proposal id or None."""
    import routines
    now = now or dt.datetime.now()
    result = background.parse_json(background.ask("suggestion", SYSTEM, _prompt(now))) or {}
    title = " ".join(str(result.get("titel", "")).split())[:100]
    why = " ".join(str(result.get("begruendung", "")).split())[:300]
    if not title or not why or privacy.is_work(title, why, json.dumps(result, ensure_ascii=False)):
        return None
    if any(title.lower() in e.lower() for e in earlier()):
        return None
    kind, payload = "idea", {}
    if result.get("art") == "routine" and isinstance(result.get("routine"), dict):
        r = result["routine"]
        payload = {"name": str(r.get("name") or title)[:60],
                   "triggers": [str(t).lower()[:80] for t in r.get("triggers") or [] if str(t).strip()],
                   "steps": r.get("steps") or []}
        kind = "routine" if payload["triggers"] and not routines.validate(payload["steps"]) else "idea"
    elif result.get("art") == "erinnerung" and isinstance(result.get("erinnerung"), dict):
        e = result["erinnerung"]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", str(e.get("at", ""))) and e.get("text"):
            kind, payload = "reminder", {"kind": "reminder", "at": e["at"], "text": str(e["text"])[:120]}
    pid = proposals.add(kind, title, why, payload if kind != "idea" else {}, source="suggestions")
    _record(title, why)
    tail = ("Soll ich das einrichten? Vorher zeige ich Ihnen eine Bestätigungskarte." if kind in ("routine", "reminder")
            else "Das müsste in Claude Code gebaut werden; ich habe es unter Vorschläge notiert.")
    events.publish("say", f"Sir, ein Vorschlag: {title}. {why} {tail}", kind="suggestion")
    return pid


def due(now):
    hh, mm = (int(x) for x in AT.split(":"))
    return now.weekday() in DAYS and (now.hour, now.minute) >= (hh, mm) and not events.quiet_now(now)


def step(now=None):
    now = now or dt.datetime.now()
    if due(now) and background.claim("suggestion", now.date().isoformat()):
        suggest(now)


def start():
    if ENABLED:
        background.loop("suggestions", 600, step)
