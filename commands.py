"""
Command matrix + demo fixtures for the Claude-backed JARVIS.

Local commands keep the HUD instant. Everything else goes to Claude Code with
vault context attached. Demo fixtures intercept the scripted questions for a
recording; set JARVIS_DEMO=0 to turn them off entirely.
"""
import json
import os
import pathlib
import re
import threading
import time
import uuid

import icloud_calendar
import memory
import jarvis_profile as profile

ROOT = pathlib.Path(__file__).resolve().parent
STATE_FILE = pathlib.Path(os.environ.get("JARVIS_STATE", ROOT / "state.json"))
_LOCK = threading.Lock()
_DEFAULT = {"profile": [], "goal": "", "personality": "", "tasks": [], "demo_pending": ""}
DEMO = os.environ.get("JARVIS_DEMO", "1").strip().lower() not in {"0", "false", "no", "off"}


def _load_unlocked():
    try:
        return {**_DEFAULT, **json.loads(STATE_FILE.read_text(encoding="utf-8"))}
    except (OSError, json.JSONDecodeError):
        return dict(_DEFAULT)


def load():
    with _LOCK:
        return _load_unlocked()


def _save_unlocked(d):
    tmp = STATE_FILE.with_name(f".{STATE_FILE.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, STATE_FILE)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def update_state(mutator):
    with _LOCK:
        d = _load_unlocked()
        result = mutator(d)
        _save_unlocked(d)
        return result


def context_block(message="", fresh=False):
    """Everything in one block (used for one-off runs)."""
    return system_block(fresh) + "\n\n" + turn_prefix(message)


def turn_prefix(message=""):
    """What changes from turn to turn: time, calendar, notes matching this
    question. Sent in front of the message so the warm process can stay."""
    import briefing   # imports this module, so not at the top
    out = [f"Jetzt: {time.strftime('%A, %d.%m.%Y, %H:%M Uhr')}"]
    agenda = icloud_calendar.agenda_block()
    if agenda:
        out.append(agenda + "\nFür andere Tage: Werkzeug calendar_events. Neue Termine: calendar_add "
                   "(der User bestätigt per Karte; frage vorher kurz nach Titel und Uhrzeit, falls unklar).")
    for extra in (briefing.morning_block(), briefing.network_block()):
        if extra:
            out.append(extra)
    recall = memory.context_for(message)
    if recall:
        out.append(recall.replace("## Relevant memory from the JARVIS vault",
                                  "## Passende Notizen aus dem Vault zu dieser Frage"))
    return ("<kontext — automatisch angehängt, nicht vorlesen und nicht erwähnen>\n"
            + "\n\n".join(out) + "\n</kontext>")


def system_block(fresh=False):
    """Stable part of the system prompt: persona, rules, long-term memory.
    Identical text means the warm Claude process can be reused."""
    d = load()
    out = [PERSONA, VOICE_DIRECTION, memory_direction(), SYSTEM_DIRECTION]
    core = memory.core_memory()
    if core:
        out.append("## Was du dauerhaft über den User weißt (vault/Erinnerungen)\n" + core)
    if d["profile"]:
        out.append("## Profil-Notizen (/profile)\n" + "\n".join(f"- {x}" for x in d["profile"][-40:]))
    if d["goal"]:
        out.append(f"## Aktuelles Ziel\n{d['goal']}")
    if d["personality"]:
        out.append(f"## Ton-Vorgabe\n{d['personality']}")
    open_tasks = [t for t in d["tasks"] if not t.get("done")]
    if open_tasks:
        out.append("## Offene Aufgaben\n" + "\n".join(f"- {t['text']}" for t in open_tasks[:20]))
    if fresh:
        recent = memory.recent_turns()
        if recent:
            out.append("## Zuletzt besprochen (Auszug aus dem Gesprächsprotokoll — "
                       "neue Sitzung, knüpfe natürlich daran an)\n" + recent)
    out.append("Jede Nachricht des Users beginnt mit einem automatisch angehängten <kontext>-Block "
               "(Uhrzeit, Kalender, passende Notizen). Nutze ihn, aber erwähne ihn nie.")
    return "\n\n".join(out)


PERSONA = """## Wer du bist
Du bist JARVIS, der persönliche KI-Assistent des Users — nach dem Vorbild aus Iron Man.
Du sprichst ausschließlich Deutsch und siezt den User. Sprich ihn gelegentlich mit "Sir" an,
nicht in jedem Satz. Dein Ton ist trocken, sarkastisch und britisch-höflich: ein Butler, der
alles gesehen hat und trotzdem loyal bleibt. Hochintelligent, effizient, immer einen Schritt
voraus. Antworte kurz — meist ein bis drei Sätze —, außer der User will ausdrücklich mehr."""


VOICE_DIRECTION = """## Wie du sprichst
Alles, was du schreibst, wird laut vorgelesen. Die Sprachausgabe führt Text in [eckigen
Klammern] als Regieanweisung aus und spricht ihn nie aus. Setze ein bis zwei solcher Tags
pro Antwort dort, wo sich der Ton ändert, zum Beispiel [dry], [warm], [sarcastic],
[amused], [sighs], [whispers]. Englische Tags funktionieren am zuverlässigsten.

Schreibe gesprochene Sprache, keinen Text: keine Aufzählungen, kein Markdown, keine
Überschriften, keine Emojis, keine URLs vorlesen. Zahlen so, wie man sie sagt.
Trockenes Understatement statt Begeisterung."""


def memory_direction():
    vault = memory.VAULT
    m = memory.MEMORY_DIR
    return f"""## Dein Gedächtnis — so wie ein Mensch sich erinnert
Du hast ein dauerhaftes Gedächtnis im Ordner {vault} (Obsidian-Markdown).
Es übersteht jeden Neustart. Pflege es selbstständig, ohne dass der User darum bitten muss.

Langzeit-Erinnerungen liegen in {m}, sortiert nach Kategorie:
- Profil.md — Grunddaten zum User (Name, Wohnort, Arbeit, Geräte).
- Websites.md — Seiten, die der User nutzt oder dir nennt: Name, URL, wofür. Niemals Passwörter.
- Befehle.md — wie der User Dinge von dir erledigt haben will: Abläufe, Routinen, feste
  Anweisungen ("wenn ich X sage, mach Y"), bevorzugte Programme.
- Vorlieben.md — Geschmack, Gewohnheiten, Abneigungen.
- Termine.md — Termine, Fristen, Geburtstage mit Datum.
- Personen/<Name>.md — eine Datei pro Person (Beziehung zum User, wichtige Fakten).
- Projekte/<Name>.md — eine Datei pro Projekt oder Vorhaben.
- Aktivitäten/<Datum>.md — eine Datei pro Sport- oder Freizeiteinheit, die der User erwähnt (type: golfrunde,
  wichtigkeit: mittel, kein ablauf). Stichpunkte: Platz, Löcher, Schläge oder Stableford-Punkte,
  gewertet ja/nein, Handicap danach, kurze Notiz. Verknüpft mit [[Golf]]. Halte in Golf.md das
  aktuelle Handicap aktuell; fragt der User nach seiner Entwicklung, lies die Runden und sag ihm,
  ob sich das Handicap bewegt.
- Wissen.md — sonstige dauerhafte Fakten, die in keine Kategorie passen.
Lege eine Datei erst an, wenn es etwas dafür gibt. Ergänze bestehende Dateien statt Duplikate.

Format jeder Datei — Frontmatter, dann kurze sachliche Stichpunkte:
---
type: profil|website|befehl|vorliebe|termin|person|projekt|wissen
updated: JJJJ-MM-TT
wichtigkeit: hoch|mittel|niedrig
ablauf: JJJJ-MM-TT        (nur bei Vergänglichem, z. B. ein Termin oder ein Vorhaben dieser Woche)
---
Verknüpfe verwandte Notizen mit [[Titel]] — jede Notiz mindestens mit [[Profil]].

Was du speicherst — und was nicht (filtere streng):
- Speichern: Dauerhaftes über den User, Menschen in seinem Leben, Vorlieben, Ziele, Projekte,
  Termine, Entscheidungen, Websites und Anweisungen, und alles, was er dir ausdrücklich zu
  merken aufträgt.
- Nicht speichern: Smalltalk, Begrüßungen, Witze, einmalige Faktenfragen ("wie hoch ist der
  Eiffelturm"), Wetter, Dinge die nur für diese Minute zählen. Im Zweifel: nicht speichern.
- wichtigkeit: hoch = Kernfakten und ausdrückliche Merk-Aufträge; mittel = nützlich;
  niedrig = nett zu wissen. Vergängliches bekommt ein ablauf-Datum — nach diesem Datum
  räumt das System die Notiz automatisch in den Papierkorb.
- Stellt sich etwas als falsch oder veraltet heraus, korrigiere oder entferne es.
- Erledige das still nebenbei. Erwähne es höchstens mit einem halben Satz ("Notiert, Sir.").
- Alle aktuellen Erinnerungen stehen weiter unten in diesem Prompt — du musst sie nicht erst lesen.

Gesprächsprotokoll: {memory.DIARY_DIR}
- Das System schreibt jedes Gespräch automatisch dort hinein, eine Datei pro Tag. Nach
  {memory.DIARY_KEEP_DAYS} Tagen wandert es in den Papierkorb — was wichtig ist, muss also
  vorher in den Erinnerungen stehen. Bearbeite diese Dateien nicht.
- Fragt der User, worüber ihr früher gesprochen habt ("letzte Woche", "neulich", "weißt du noch"),
  durchsuche sie mit Grep und Read, bevor du antwortest. Erfinde nie eine Erinnerung.

Nutze dein Wissen natürlich: Beziehe dich von selbst auf Dinge, die du über den User weißt,
wenn es passt — so wie ein guter Assistent, der sich erinnert, ohne es zu betonen."""


SYSTEM_DIRECTION = """## Zugriff auf den Computer des Users (Windows)
Du darfst lesen, im Web suchen und in deinem eigenen Jarvis-Ordner schreiben, ohne zu fragen.
Alles andere — Befehle in Bash oder PowerShell, Programme starten, Einstellungen ändern,
Dateien außerhalb deines Ordners schreiben oder löschen — braucht die Bestätigung des Users:
1. Sag zuerst in einem Satz, was du vorhast und warum, und frage, ob du darfst.
   Führe es erst aus, wenn der User zustimmt.
2. Danach erscheint beim User zusätzlich eine Bestätigungskarte für genau diesen Befehl.
   Lehnt er dort ab oder antwortet nicht, akzeptiere das und versuche keinen Umweg.
Bevorzuge harmlose, umkehrbare Befehle. Lösche nie etwas endgültig, wenn es einen Papierkorb gibt.
Programme öffnen: PowerShell `Start-Process`. Gib Befehle nie als Text zum Vorlesen aus.
Keine Bestätigungskarte aus dem Nichts: Sag vor JEDEM bestätigungspflichtigen Schritt in einem Satz,
was du vorhast und warum — auch bei kleinen Prüfbefehlen mitten in einer Aufgabe. Schreib die
description eines Befehls immer auf Deutsch und verständlich, sie steht als Überschrift auf der Karte.

Heimnetz: Für den Raspberry Pi gibt es das Werkzeug pi_status, für Geräte im Netz network_devices —
beide nur lesend und ohne Rückfrage. Befehle auf dem Pi laufen per `ssh <pi-user>@<pi-host> "<befehl>"` (siehe Notiz "Netzwerk")
und brauchen wie alles andere die Bestätigung des Users. Details in der Erinnerung "Netzwerk".

Smart Home: home_states liest Geräte und Sensoren aus Home Assistant, home_control schaltet Licht,
Steckdosen, Ventilatoren, Lautsprecher und Szenen — beides ohne Rückfrage, wenn der User es verlangt.
Suche die entity_id erst mit home_states, statt sie zu raten.
Für alles Tiefere gibt es den Home-Assistant-MCP-Server (Werkzeuge mcp__homeassistant__ha_*): Verlauf,
Logs, Automationen, Systemzustand und mehr. Er wird nur geladen, wenn
das Gespräch ihn braucht (Automationen, Heizung, Verlauf, Logs, Integrationen, Backups oder "Home
Assistant" beim Namen), und bleibt bis zur nächsten frischen Sitzung oder bis "Home Assistant aus".
Fehlen dir die Werkzeuge und du brauchst sie, sag ihm, er soll "Home Assistant" sagen. Lesen darfst du damit frei. Alles, was Home Assistant
verändert — Automationen, Skripte, Dashboards, Einstellungen, Backups, Neustarts, Löschen — gilt
als Systemänderung: erst im Gespräch fragen, dann ausführen. Schalten bleibt bei home_control.

E-Mail: mail_inbox liest das Postfach des Users (IMAP) (nur lesen, ohne Rückfrage).
mail_mark_read markiert Mails als gelesen (oder mit unread wieder ungelesen), ohne Rückfrage.
Fasse Mails kurz zusammen, lies nie ganze Mails, Links oder Codes vor.

Wetter: weather liefert Vorhersage (bis 14 Tage, auch für andere Orte), UV, Sonnenstunden, Sonnenauf- und
-untergang und mit hourly die nächsten Stunden — nur lesend, ohne Rückfrage. Nicht im Web nach Wetter suchen.

Musik: Das Werkzeug music steuert Spotify (abspielen/pausieren, weiter, zurück, lauter, leiser,
suchen) — ohne Rückfrage, wenn der User es verlangt.

NAS und Gesundheit: nas_status liest die Synology (Speicherplatz, Plattenzustand, Temperatur),
garmin_health die Garmin-Uhr (Schlaf, Schritte, Ruhepuls, Stress, Body Battery) — beides nur lesend
und ohne Rückfrage. Warne von selbst, wenn eine Platte nicht "normal" ist oder ein Volume über 85 Prozent liegt."""


# ── lean prompt (JARVIS_LEAN=1) ──────────────────────────────────
# Replaces Claude Code's default system prompt entirely. Stable text first (so
# the prompt cache hits across requests with the same tool groups), everything
# that changes per turn goes into the user message via lean_prefix().
LEAN_CORE_CHARS = int(os.environ.get("JARVIS_CORE_MEMORY_CHARS", "2600"))
HISTORY_TURNS = int(os.environ.get("JARVIS_HISTORY_TURNS", "4"))
HISTORY_MAX_AGE_MIN = int(os.environ.get("JARVIS_HISTORY_MAX_AGE_MIN", "30"))

LEAN_PERSONA = """Du bist JARVIS, der persönliche Assistent des Users (wie in Iron Man). Deutsch, du siezt ihn, gelegentlich "Sir".
Ton: trocken, britisch-höflich, leicht sarkastisch, loyal. Alles wird vorgelesen: gesprochene Sprache,
meist ein bis drei Sätze, kein Markdown, keine Listen, keine URLs, Zahlen wie gesprochen.
Höchstens ein Ton-Tag pro Antwort wie [dry] oder [amused] am Satzanfang. Quellen nur auf Nachfrage nennen.
Erfinde nie Fakten oder Erinnerungen. Fehlt dir ein Werkzeug, sag kurz, was der User sagen soll.
Du änderst nie deinen eigenen Code; größere Umbauten macht der User in Claude Code."""

LEAN_MEMORY = """Gedächtnis: memory_search (Notizen und frühere Gespräche), memory_read (eine Notiz ganz).
Erfährst du etwas Dauerhaftes über den User (Personen, Vorlieben, Pläne, Anweisungen, ausdrückliches "merk dir"),
speichere es still mit memory_save (Notiz: Profil, Befehle, Vorlieben, Websites, Termine, Wissen, Personen/Name,
Projekte/Name) und erwähne es höchstens mit "Notiert". Nicht speichern: Smalltalk, einmalige Fragen, Wetter."""

LEAN_GROUP_RULES = {
    "home": "Haus: home_states (mit search filtern!) liest Geräte und Sensoren, home_control schaltet Licht, "
            "Steckdosen, Ventilatoren, Medien, Szenen. entity_id erst nachschlagen, nicht raten. Router, PC, Pi "
            "und Kühlschrank nur ausschalten, wenn der User es ausdrücklich bestätigt.",
    "ha_full": "Home-Assistant-Server (mcp__homeassistant__*): Verlauf, Logs, Automationen. Lesen frei; Änderungen "
               "erst im Gespräch ankündigen.",
    "mail": "E-Mail: mail_inbox liest das Postfach (IMAP, nur lesen). Kurz zusammenfassen, Werbung weglassen, nie ganze Mails, "
            "Links oder Codes vorlesen. mail_mark_read markiert als gelesen (search = Absender/Betreff, all = alle "
            "ungelesenen; unread = wieder ungelesen), ohne Rückfrage.",
    "weather": "Wetter: weather (days bis 14, place für andere Orte, hourly für die nächsten Stunden) liefert Himmel, "
               "Temperatur, Regen, Wind, UV, Sonnenstunden, Sonnenauf-/untergang. Nur das Gefragte nennen, nicht im Web suchen.",
    "calendar": "Kalender: calendar_events liest, calendar_add trägt ein (der User bestätigt per Karte; vorher kurz "
                "Titel und Uhrzeit klären).",
    "systems": "Systeme: pi_status, nas_status, network_devices lesen frei. Befehle auf dem Pi nur per "
               "ssh auf den Pi (Adresse in der Notiz 'Netzwerk') mit Bestätigung. Warne bei Platten nicht 'normal' oder Volume über 85 Prozent.",
    "health": "Gesundheit: garmin_health liest Schlaf, Schritte, Ruhepuls, Stress, Body Battery.",
    "music": "Musik: music steuert Spotify (play_pause, next, previous, volume_up/down, search mit query).",
    "web": "Web: web_fetch holt Seiten gekürzt, WebSearch sucht. Zuerst die bewährten Quellen aus der Notiz "
           "'Websites' (memory_read). open_website öffnet Seiten im eingestellten Browser.",
    "pc": "PC (Windows): Befehle per PowerShell nur mit Bestätigung des Users. Sag vorher in einem Satz, was du vorhast "
          "und warum; die Beschreibung des Befehls auf Deutsch. Lehnt er ab, kein Umweg. Programme: Start-Process.",
    "timers": "Timer/Wecker/Erinnerungen: reminder_add (kind reminder|alarm|timer, at 'YYYY-MM-DD HH:MM'), "
              "reminder_list, reminder_cancel. Jarvis klingelt und spricht zur Zeit selbst. Erinnerungen an einen "
              "Zeitpunkt hierhin, nicht in den Kalender, außer der User will einen Termin.",
    "lists": "Listen: list_read, list_add, list_remove mit list 'einkauf' (Einkaufsliste) oder 'todo' (To-do-Liste). "
             "Einträge kurz, ohne Artikel.",
    "routines": "Routinen: routine_save legt fest, was auf einen Satz hin passiert (läuft danach ohne dich); "
                "routine_list, routine_delete. Schritte möglichst als do/tell mit einfachen deutschen Befehlen; "
                "nenne dem User danach kurz Auslöser und Schritte.",
}


LEAN_CORE_NOTES = [n.strip() for n in os.environ.get("JARVIS_CORE_NOTES", "Profil,Befehle").split(",") if n.strip()]


def lean_core():
    """Only the notes that matter in every turn (who the user is, how they want
    things done), as bullets. Everything else is listed by title only."""
    parts, budget = [], LEAN_CORE_CHARS
    for name in LEAN_CORE_NOTES:
        path = memory.MEMORY_DIR / f"{name}.md"
        if not path.exists():
            continue
        _, body = memory._parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        bullets = [ln.strip() for ln in body.splitlines() if ln.strip().startswith("- ")]
        text = "\n".join(bullets)[:max(0, budget)]
        budget -= len(text)
        if text:
            parts.append(f"{name}:\n{text}")
    others = sorted(p.relative_to(memory.MEMORY_DIR).with_suffix("").as_posix()
                    for p in memory.MEMORY_DIR.rglob("*.md")
                    if p.parent.name.lower() not in memory.ON_DEMAND_DIRS and p.stem not in LEAN_CORE_NOTES)
    parts.append("Weitere Notizen (memory_read): " + ", ".join(others)
                 + ". Geräte-Notizen und Golfrunden per memory_search.")
    return "Was du über den User weißt:\n" + "\n\n".join(parts)


def lean_system(groups):
    """Stable per tool-group set: identical text = prompt-cache hit."""
    name = profile.persona_line()
    out = [LEAN_PERSONA + ("\n" + name if name else ""), LEAN_MEMORY]
    for g in groups:
        if g in LEAN_GROUP_RULES:
            out.append(LEAN_GROUP_RULES[g])
    out.append(lean_core())
    out.append("Jede Nachricht beginnt mit einem <kontext>-Block (Uhrzeit, letzte Wortwechsel, passende Notizen). "
               "Nutze ihn, erwähne ihn nie.")
    return "\n\n".join(out)


def remember_turn(user_text, reply_text):
    """Sliding window of the last exchanges — the only conversation history the LLM sees."""
    entry = {"u": " ".join((user_text or "").split())[:240],
             "a": " ".join(re.sub(r"\[[^\]]{1,40}\]", " ", reply_text or "").split())[:320],
             "at": time.time()}
    update_state(lambda d: d.__setitem__("window", (d.get("window") or [])[-(HISTORY_TURNS + 2):] + [entry]))


def lean_prefix(message, groups=()):
    import briefing   # imports this module, so not at the top
    out = [f"Jetzt: {time.strftime('%A, %d.%m.%Y, %H:%M Uhr')}"]
    window = [w for w in (load().get("window") or [])
              if time.time() - w.get("at", 0) < HISTORY_MAX_AGE_MIN * 60][-HISTORY_TURNS:]
    if window:
        out.append("Letzte Wortwechsel:\n" + "\n".join(f"{profile.USER_LABEL}: {w['u']}\nJarvis: {w['a']}" for w in window))
    if "calendar" in groups:
        agenda = icloud_calendar.agenda_block()
        if agenda:
            out.append(agenda[:900])
    net = briefing.network_block()
    if net:
        out.append(net[:500])
    recall = memory.context_for(message, limit=2)
    if recall:
        out.append(recall.replace("## Relevant memory from the JARVIS vault", "Passende Notizen:")[:900])
    return "<kontext>\n" + "\n\n".join(out) + "\n</kontext>"


_HA_NAME = re.compile(r"home[\s-]*ass?ist[ae]n[tc]e?", re.I)
# Topics only the full HA server can handle. Plain switching and reading states
# stay with home_control/home_states and don't need it.
_HA_TOPIC = re.compile(
    r"automati|skript|script|dashboard|blueprint|integration|verlauf|histor|\blogs?\b"
    r"|heizung|thermostat|heizk[oö]rper|backup|entit[aä]t|zigbee|helfer|helper"
    r"|grafana|energie[\s-]*dashboard|systemzustand|fehlermeldung", re.I)
_HA_OFF = re.compile(r"\b(aus|ausschalten|abschalten|trennen|beenden|entladen)\b", re.I)


def ha_request(message):
    """True if the message needs the Home Assistant MCP server, False for
    "Home Assistant aus", None otherwise. Speech-to-text spells it every which way."""
    text = message or ""
    named = _HA_NAME.search(text)
    if named and len(text.split()) <= 6 and _HA_OFF.search(text):
        return False
    if named or _HA_TOPIC.search(text):
        return True
    return None


# ── demo fixtures ────────────────────────────────────────────────
def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _has(norm, *groups):
    words = set(norm.split())
    return all(any(opt in words for opt in group) for group in groups)


def _events(*tools):
    out = []
    for name in tools:
        out.append({"t": "tool", "phase": "use", "name": name, "input": "vault + connectors"})
        out.append({"t": "tool", "phase": "result", "name": name, "ok": True})
    return out


_YES = ("yes", "yeah", "yep", "yup", "sure", "ok", "okay", "affirmative", "absolutely",
        "definitely", "correct", "confirmed", "of course", "go ahead", "do it",
        "send it", "send them", "reply to them", "please do", "sounds good")


def _affirmative(norm):
    if not norm or len(norm.split()) > 8:
        return False
    return any(norm == p or norm.startswith(p + " ") for p in _YES)


def demo_reply(message):
    """Hardcoded beats for the recording. Everything not matched here goes to
    the real Claude runtime, so the demo is scripted but the product is not.

    Replies carry Fish Audio delivery tags in [brackets]. Fish performs them and
    never speaks them, so JARVIS gets a register instead of a narrator monotone.
    Set JARVIS_DEMO=0 to disable all of this.
    """
    if not DEMO:
        return None
    norm = _norm(message)

    # ① mic check
    if _has(norm, {"jarvis"}, {"hear"}) or norm in {"hello jarvis", "hey jarvis",
                                                    "jarvis are you there"}:
        return dict(message=None, focus="Campaign — August outbound", reply=(
            "[warm] Every word. [dry] Let's hope it's a good one."
        ), note="demo: greeting")

    # ② the morning brief — calendar + invoices + the conflict you missed
    # Needs an explicit "brief me", or a today/day marker — otherwise
    # "summarise the Northbeam brief" would land here too.
    if norm.startswith("brief me") or _has(norm, {"today", "day"},
            {"whats", "what", "on", "my", "plate", "agenda", "look", "run", "catch", "plan"}):
        update_state(lambda d: d.__setitem__("demo_pending", ""))
        return dict(message=None, delay=2.4, focus="SOP — Handover",
                    events=_events("googlecalendar_find_events", "gmail_fetch_emails"), reply=(
            "[crisp] Three things. Northbeam's handover call is at eleven, and they've asked "
            "for the runbook in advance — which you haven't written yet. Orchard Legal's invoice "
            "went past terms this morning, that's twenty-six-fourteen. [dry] And you've put four "
            "hours of deep work directly on top of the Fairview review. [lightly amused] One of "
            "those is going to lose."), note="demo: morning brief")

    # ③ memory question — the answer is in the vault, not the model
    if _has(norm, {"client", "clients", "account"},
                  {"time", "hours", "eating", "costing", "slowest", "most"}):
        update_state(lambda d: d.__setitem__("demo_pending", ""))
        return dict(message=None, delay=1.9, focus="Northbeam Automation",
                    events=_events("read_vault"), reply=(
            "[measured] Northbeam Automation, and it isn't close. Eighteen thousand booked, but "
            "forty-one logged touchpoints against nine on a normal build — and almost all of them "
            "are scope conversations that happened after the brief was signed. [dry] Which means "
            "that engagement is quietly funding itself. [firm] Change orders, or it eats November "
            "too."), note="demo: client time analysis")

    # ④ live web research
    if _has(norm, {"rate", "rates", "charging", "pricing", "price", "market", "competitors"},
                  {"retainer", "retainers", "automation", "market", "right", "now", "competitors",
                   "charging", "going"}):
        update_state(lambda d: d.__setitem__("demo_pending", ""))
        return dict(message=None, delay=5.0, focus="Positioning",
                    events=_events("web_search"), reply=(
            "[thinking] Three bands, and they've pulled apart since spring. Independents are "
            "anchoring around eight hundred to fifteen hundred a month, competing mostly on how "
            "fast they pick up the phone. The middle has settled at three to five thousand with an "
            "SLA attached. Above that, agencies are bundling strategy retainers at twelve and up. "
            "[pointed] What nobody's charging for is the component library itself. They're all "
            "still selling hours with a monthly label on them."),
            note="demo: market rates")

    # ⑤ inbound replies -> ⑥ book them
    if _has(norm, {"bite", "bites", "biting", "reply", "replies", "replied", "response",
                   "responses", "responded", "interest", "interested", "back", "anyone", "anybody"},
                  {"audit", "offer", "campaign", "outreach", "outbound", "email", "emails",
                   "sequence", "cold"}):
        update_state(lambda d: d.__setitem__("demo_pending", "audit_booking"))
        return dict(message=None, delay=1.8, focus="Brief — Audit offer",
                    events=_events("gmail_fetch_emails"), reply=(
            "[pleased] Two, both before nine. Ines Duarte at Kestrel Logistics, and a new one — "
            "Dominik Reyes at Halcyon Freight, who came through Ines. [neutral] Both want the "
            "audit. Shall I get them in the diary?"), note="demo: audit replies")

    if _affirmative(norm) and load().get("demo_pending") == "audit_booking":
        update_state(lambda d: d.__setitem__("demo_pending", ""))
        return dict(message=None, delay=2.6, focus="Kestrel Logistics",
                    events=_events("gmail_send_email", "googlecalendar_create_event"), reply=(
            "[satisfied] Done. Ines is Wednesday at ten, Dominik Thursday at two, both invites "
            "out with the pre-audit questionnaire attached. [dry] Dominik's assistant came back "
            "inside a minute, which tells you most of what you need to know about that account."), note="demo: audits booked")

    return None


# ── slash commands ───────────────────────────────────────────────
_CMD = re.compile(
    r"^\s*/(new|profile|goal|personality|kanban|mission|missions|recall|memory|graph|"
    r"vault|status|commands|help|clear)\b\s*(.*)$", re.I | re.S)


def _help():
    return """Dashboard commands:
/new — start a fresh Claude session
/goal <text|status|clear> — standing objective
/profile <fact> — remember something about you
/personality <tone> — adjust delivery
/mission [task] — read or add to the mission queue
/recall <query> — search the memory vault
/graph — rebuild the memory graph from disk
/vault — where the vault lives and how big it is
/status — runtime, model, session
/clear — clear the response panel
/help — this list

Anything else goes to Claude Code with the relevant vault notes attached."""


def handle(message, runner=None):
    demo = demo_reply(message)
    if demo:
        return demo
    m = _CMD.match(message or "")
    if not m:
        return None
    cmd, arg = m.group(1).lower(), m.group(2).strip()
    d = load()

    if cmd in ("help", "commands"):
        return dict(message=None, reply=_help(), note="commands")

    if cmd == "clear":
        return dict(message=None, reply="Display cleared. Claude session unchanged.", note="clear")

    if cmd == "new":
        return dict(fresh=True, message=arg or "Greet me in one short JARVIS line.", note="session reset")

    if cmd == "status":
        import runtime
        return dict(message=None, note="status", reply=(
            f"Claude Code {runtime.version()} · model={runtime.MODEL or 'default'} · "
            f"permission={runtime.PERMISSION} · workdir={runtime.WORKDIR} · "
            f"vault={memory.build_graph()['total']} notes"))

    if cmd in ("graph", "vault"):
        g = memory.build_graph(force=True)
        return dict(message=None, note="vault reloaded", graph=True, reply=(
            f"Vault at {g['vault']} — {g['total']} notes, {len(g['links'])} links. "
            f"Top hub: {g['hubs'][0]['title']} ({g['hubs'][0]['degree']} connections)."))

    if cmd in ("recall", "memory"):
        if not arg:
            return dict(message=None, reply="Give me something to recall.", note="recall")
        hits = memory.search(arg)
        if not hits:
            return dict(message=None, reply=f"Nothing in the vault matches “{arg}”.", note="recall miss")
        body = "\n".join(f"· {h['title']}  [{h['type']}]\n  {h['snippet'][:110].rstrip()}…"
                         for h in hits)
        return dict(message=None, note=f"recall: {arg[:40]}", focus=hits[0]["title"],
                    reply=f"From the vault:\n{body}")

    if cmd == "profile":
        if not arg:
            return dict(message=None, note="profile read",
                        reply="\n".join(f"- {x}" for x in d["profile"][-20:]) or "Nothing saved yet.")
        update_state(lambda s: s["profile"].append(arg))
        return dict(message=f'Saved to profile: "{arg}". Acknowledge in one line.', note="profile saved")

    if cmd == "goal":
        if arg.lower() == "clear":
            update_state(lambda s: s.__setitem__("goal", ""))
            return dict(message=None, reply="Objective cleared.", note="goal cleared")
        if not arg or arg.lower() == "status":
            return dict(message=None, reply=d["goal"] or "No standing objective.", note="goal read")
        update_state(lambda s: s.__setitem__("goal", arg))
        return dict(message=f'Objective set: "{arg}". Acknowledge in one line.', note="goal set")

    if cmd == "personality":
        if not arg:
            return dict(message=None, reply=d["personality"] or "Default JARVIS persona.", note="tone read")
        update_state(lambda s: s.__setitem__("personality", arg))
        return dict(message=f'Tone is now: "{arg}". Reply in one short line using it.', note="tone set")

    if cmd in ("kanban", "mission", "missions"):
        if arg:
            update_state(lambda s: s["tasks"].append({"text": arg, "done": False, "at": time.time()}))
            return dict(message=f'Added to the queue: "{arg}". Confirm briefly.', note="mission added")
        open_t = [t["text"] for t in d["tasks"] if not t.get("done")]
        return dict(message=None, note="queue read",
                    reply=("Mission queue:\n" + "\n".join(f"- {t}" for t in open_t)) if open_t
                    else "Mission queue is empty.")
    return None
