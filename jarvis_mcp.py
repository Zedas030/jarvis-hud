"""
JARVIS' own MCP server (stdio, standard library only). Claude Code starts it
for every turn via --mcp-config. Two tools:

- approve       — the --permission-prompt-tool. Every tool call that is not
                  pre-allowed (shell commands, writes outside the Jarvis folder,
                  ...) lands here. It asks the running HUD server, which shows a
                  confirmation card to the user and blocks until he answers.
                  No answer within the timeout means deny.
- open_website  — opens an http(s) URL in the configured browser. Harmless, so it is
                  pre-allowed and needs no confirmation.
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
_env = os.path.join(HERE, ".env")
if os.path.exists(_env):   # calendar credentials etc. — read directly, never via argv
    for _line in open(_env, encoding="utf-8"):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
sys.path.insert(0, HERE)

PERM_URL = os.environ.get("JARVIS_PERM_URL", "http://127.0.0.1:8720/api/permission/request")
PERM_TOKEN = os.environ.get("JARVIS_PERM_TOKEN", "")
PERM_TIMEOUT = int(os.environ.get("JARVIS_PERM_TIMEOUT", "75"))
# Browser for open_website: full path in JARVIS_BROWSER, empty = the system default browser.
BROWSER = os.path.expandvars(os.environ.get("JARVIS_BROWSER", "").strip())

TOOLS = [
    {
        "name": "approve",
        "description": "Permission gate: asks the user to confirm a tool call in the HUD.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string"},
                "input": {"type": "object"},
                "tool_use_id": {"type": "string"},
            },
            "required": ["tool_name", "input"],
        },
    },
    {
        "name": "open_website",
        "description": ("Open a website for the user in the configured browser. "
                        "Use this for every 'open/show me <site>' request instead of a shell command."),
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Full http(s) URL"}},
            "required": ["url"],
        },
    },
    {
        "name": "calendar_events",
        "description": ("Read the user's calendar (iCloud/CalDAV). Returns events from `start` "
                        "(YYYY-MM-DD, default today) for `days` days (default 1, max 62)."),
        "inputSchema": {
            "type": "object",
            "properties": {"start": {"type": "string"}, "days": {"type": "integer"}},
        },
    },
    {
        "name": "calendar_add",
        "description": ("Create an event in the user's calendar. Needs their confirmation. "
                        "start/end as 'YYYY-MM-DD HH:MM' local time, or 'YYYY-MM-DD' for all-day. "
                        "end defaults to one hour after start."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"},
                "location": {"type": "string"}, "notes": {"type": "string"},
                "calendar": {"type": "string", "description": "calendar name, optional"},
            },
            "required": ["title", "start"],
        },
    },
]


TOOLS += [
    {
        "name": "network_devices",
        "description": ("List the devices currently seen on the user's home network (IP, MAC, hostname). "
                        "Read-only, no confirmation needed."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "pi_status",
        "description": ("Health of the user's Raspberry Pi / home server: uptime, CPU temperature, "
                        "load, memory, disk, service states. Read-only, no confirmation needed. "
                        "For anything else on the Pi use ssh via Bash/PowerShell — that needs his confirmation."),
        "inputSchema": {"type": "object", "properties": {}},
    },
]

TOOLS += [
    {
        "name": "home_states",
        "description": ("Read the user's smart home (Home Assistant): devices with name, entity_id and state. "
                        "Optional `search` filters by name or entity_id (e.g. 'wohnzimmer', 'light.'). "
                        "Read-only, no confirmation needed."),
        "inputSchema": {"type": "object", "properties": {"search": {"type": "string"}}},
    },
    {
        "name": "home_control",
        "description": ("Control a Home Assistant device. `service` e.g. turn_on, turn_off, toggle, "
                        "media_play_pause, media_next_track, volume_set; `entity_id` from home_states; "
                        "optional `data` like {\"brightness_pct\": 40} or {\"volume_level\": 0.3}. "
                        "Only lights, switches, fans, media players and scenes — nothing else."),
        "inputSchema": {
            "type": "object",
            "properties": {"entity_id": {"type": "string"}, "service": {"type": "string"},
                           "data": {"type": "object"}},
            "required": ["entity_id", "service"],
        },
    },
]

TOOLS += [
    {
        "name": "mail_inbox",
        "description": ("Read the user's mail inbox via IMAP (read-only): newest mails with sender, subject, date "
                        "and a short preview. `unread_only` (default true), `limit` (default 5, max 15), "
                        "optional `search` in subject/sender. Never sends, deletes or marks anything."),
        "inputSchema": {
            "type": "object",
            "properties": {"unread_only": {"type": "boolean"}, "limit": {"type": "integer"},
                           "search": {"type": "string"}},
        },
    },
]

TOOLS += [
    {
        "name": "music",
        "description": ("Control music on the user's PC (Spotify desktop app) via the system media keys. "
                        "`action`: play_pause, next, previous, volume_up, volume_down, mute, or search "
                        "(opens Spotify with `query` searched — he picks/plays from there). "
                        "Starts Spotify first if it is not running. Harmless, no confirmation needed."),
        "inputSchema": {
            "type": "object",
            "properties": {"action": {"type": "string"}, "query": {"type": "string"}},
            "required": ["action"],
        },
    },
]

TOOLS += [
    {
        "name": "nas_status",
        "description": ("Health of the user's Synology NAS: model, uptime, temperature, CPU/RAM, "
                        "volumes with used space and status, every disk with health and temperature. "
                        "Read-only, no confirmation needed."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "garmin_health",
        "description": ("The user's Garmin data: per day sleep (duration, phases, score), steps, "
                        "resting heart rate, stress and body battery. `days` (default 1 = today, max 14) "
                        "counts back from today; sleep of a day is the night that ended that morning. "
                        "Read-only, no confirmation needed."),
        "inputSchema": {"type": "object", "properties": {"days": {"type": "integer"}}},
    },
]

TOOLS += [
    {
        "name": "web_fetch",
        "description": ("Fetch a web page or JSON API and return its readable text, shortened. "
                        "Prefer the proven sources in the memory note 'Websites'."),
        "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
    },
    {
        "name": "memory_search",
        "description": "Search the user's notes and past conversations (titles and snippets).",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    {
        "name": "memory_read",
        "description": "Read one note in full, by title (e.g. 'Golf', 'Smart Home', 'Gespräch 2026-10-13').",
        "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
    },
    {
        "name": "memory_save",
        "description": ("Remember something lasting about the user. `note`: Profil, Befehle, Vorlieben, Websites, "
                        "Termine, Wissen, Personen/<Name> or Projekte/<Name>. `text`: one short bullet. "
                        "`replace` (optional): an existing bullet text to replace instead of appending."),
        "inputSchema": {"type": "object", "properties": {
            "note": {"type": "string"}, "text": {"type": "string"}, "replace": {"type": "string"}},
            "required": ["note", "text"]},
    },
]

# Which tools each group brings. The router in the server picks the groups per
# request and passes them in JARVIS_TOOL_GROUPS, so a question about the weather
# does not carry the definitions of mail, NAS and calendar tools along.
GROUPS = {
    "memory": {"memory_search", "memory_read", "memory_save"},
    "web": {"web_fetch", "open_website"},
    "calendar": {"calendar_events", "calendar_add"},
    "home": {"home_states", "home_control"},
    "mail": {"mail_inbox"},
    "music": {"music"},
    "systems": {"pi_status", "nas_status", "network_devices"},
    "health": {"garmin_health"},
}
ACTIVE_GROUPS = [g.strip() for g in os.environ.get("JARVIS_TOOL_GROUPS", "").split(",") if g.strip()]
# Hard cap per tool result — results are sent again with every later API call of the turn.
MAX_RESULT = int(os.environ.get("JARVIS_TOOL_RESULT_MAX", "2500"))


def listed_tools():
    if not ACTIVE_GROUPS:          # legacy mode: everything
        return TOOLS
    names = {"approve"}
    for g in ACTIVE_GROUPS:
        names |= GROUPS.get(g, set())
    return [t for t in TOOLS if t["name"] in names]


def _web_fetch(url):
    import html as _html
    import re as _re
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return "Nur http(s)-Adressen."
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 JARVIS", "Accept-Language": "de,en"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read(600_000)
            ctype = r.headers.get("content-type", "")
            charset = r.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code} bei {url}"
    except Exception as e:  # noqa: BLE001
        return f"Nicht erreichbar: {e}"
    text = raw.decode(charset, "replace")
    if "json" in ctype or text.lstrip()[:1] in "{[":
        try:
            return json.dumps(json.loads(text), ensure_ascii=False, separators=(",", ":"))
        except ValueError:
            pass
    text = _re.sub(r"(?is)<(script|style|noscript|svg|head|nav|footer|form)[^>]*>.*?</\1>", " ", text)
    text = _re.sub(r"(?s)<[^>]+>", " ", text)
    return " ".join(_html.unescape(text).split())


def _memory(name, args):
    import re as _re
    import memory
    if name == "memory_search":
        # Full-text, word-wise: return the matching lines, not whole notes.
        words = [w for w in _re.split(r"[\W_]+", (args.get("query") or "").lower()) if len(w) > 2]
        if not words:
            return "Suchbegriff fehlt."
        scored = []
        for path in memory._notes():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            low = text.lower()
            pats = [_re.compile(r"\b" + _re.escape(w)) for w in words]   # word starts, not "auto" in "Automatisch"
            whole = [_re.compile(r"\b" + _re.escape(w) + r"\b") for w in words]
            # capped per word: a long note must not win just by repeating a word
            score = (sum(min(len(p.findall(low)), 2) for p in pats)
                     + 3 * sum(min(len(p.findall(low)), 2) for p in whole)
                     + 3 * sum(w in path.stem.lower() for w in words))
            if not score:
                continue
            if "Gespraeche" in path.parts:
                score *= 0.2          # facts before chat logs (they are long and match everything)
            lines = [ln.strip() for ln in text.splitlines()
                     if any(p.search(ln.lower()) for p in pats) and not ln.startswith(("type:", "updated:"))]
            scored.append((score, path.stem, lines[:3]))
        scored.sort(key=lambda x: -x[0])
        out = [f"· {t}: " + " | ".join(ln[:160] for ln in lines) for _, t, lines in scored[:6]]
        return "\n".join(out) or "Nichts gefunden."
    if name == "memory_read":
        title = (args.get("title") or "").strip().lower()
        for path in memory._notes():
            if path.stem.lower() == title:
                _, body = memory._parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
                return body.strip()
        return f"Keine Notiz '{args.get('title')}'. memory_search hilft beim Finden."
    # memory_save
    note = (args.get("note") or "").strip().strip("/\\")
    text = " ".join((args.get("text") or "").split())
    if not note or not text or ".." in note or not _re.fullmatch(r"[\w äöüÄÖÜß.\-/!]+", note):
        return "Ungültige Notiz oder leerer Text."
    path = memory.MEMORY_DIR / (note + ".md")
    path.parent.mkdir(parents=True, exist_ok=True)
    import time as _t
    today = _t.strftime("%Y-%m-%d")
    if path.exists():
        body = path.read_text(encoding="utf-8")
        old = (args.get("replace") or "").strip()
        if old and old in body:
            body = body.replace(old, text, 1)
        elif text in body:
            return "Steht schon drin."
        else:
            body = body.rstrip() + f"\n- {text}\n"
        body = _re.sub(r"(?m)^updated: .*$", f"updated: {today}", body, count=1)
    else:
        body = (f"---\ntype: {'person' if note.lower().startswith('personen/') else 'erinnerung'}\n"
                f"wichtigkeit: mittel\nupdated: {today}\n---\n\n# {note.split('/')[-1]}\n\n"
                f"Gehört zu [[Profil]].\n\n- {text}\n")
    path.write_text(body, encoding="utf-8")
    return "Gespeichert."


# Virtual-key codes of the media keys — they reach whatever player is active.
MEDIA_KEYS = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1,
              "volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD}


def _spotify_running():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Spotify.exe", "/NH"], capture_output=True,
                         text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return "Spotify.exe" in out.stdout


def _music(args):
    import ctypes
    import time
    import urllib.parse
    action = (args.get("action") or "").strip().lower()
    if action == "search":
        q = (args.get("query") or "").strip()
        if not q:
            return "Wonach soll ich suchen?"
        os.startfile("spotify:search:" + urllib.parse.quote(q))
        return f"Spotify zeigt die Suche nach „{q}“."
    if action not in MEDIA_KEYS:
        return f"Unbekannte Aktion. Möglich: {', '.join(MEDIA_KEYS)}, search."
    if action == "play_pause" and not _spotify_running():
        os.startfile("spotify:")
        time.sleep(4)   # let the app come up before it gets the key
    times = 5 if action.startswith("volume") else 1   # one key press is only ~2 %
    for _ in range(times):
        ctypes.windll.user32.keybd_event(MEDIA_KEYS[action], 0, 1, 0)   # KEYEVENTF_EXTENDEDKEY
        ctypes.windll.user32.keybd_event(MEDIA_KEYS[action], 0, 3, 0)   # ... | KEYUP
    return f"Erledigt: {action}."


MAIL_HOST = os.environ.get("MAIL_IMAP_HOST", "").strip()
MAIL_USER = os.environ.get("MAIL_USER", "").strip()
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "").strip()


def _mail(args):
    import email
    import email.header
    import email.utils
    import imaplib
    if not (MAIL_USER and MAIL_PASSWORD):
        return ("Postfach noch nicht verbunden: In .env fehlen MAIL_USER und MAIL_PASSWORD, und beim Mail-Anbieter "
                "muss unter Einstellungen → POP3/IMAP der IMAP-Zugriff aktiviert sein. Das trägt der User selbst ein.")

    def dec(v):
        return str(email.header.make_header(email.header.decode_header(v or ""))).strip()

    def preview(msg):
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                body = part.get_payload(decode=True) or b""
                text = body.decode(part.get_content_charset() or "utf-8", "replace")
                return " ".join(text.split())[:120]
        return ""

    limit = max(1, min(int(args.get("limit") or 5), 15))
    try:
        box = imaplib.IMAP4_SSL(MAIL_HOST, 993, timeout=20)
        box.login(MAIL_USER, MAIL_PASSWORD)
        box.select("INBOX", readonly=True)   # readonly: nothing gets marked as read
        crit = ["UNSEEN"] if args.get("unread_only", True) else ["ALL"]
        _, data = box.search(None, *crit)
        ids = data[0].split()[-limit:][::-1]
        q = (args.get("search") or "").lower()
        rows = []
        for i in ids:
            _, parts = box.fetch(i, "(BODY.PEEK[])")
            msg = email.message_from_bytes(parts[0][1])
            frm, subj = dec(msg.get("From")), dec(msg.get("Subject"))
            if q and q not in frm.lower() and q not in subj.lower():
                continue
            when = email.utils.parsedate_to_datetime(msg.get("Date")).strftime("%d.%m. %H:%M") \
                if msg.get("Date") else "?"
            rows.append(f"{when} · {frm}\n  {subj}\n  {preview(msg)}")
        box.logout()
        head = f"{len(data[0].split())} {'ungelesene' if crit == ['UNSEEN'] else ''} Mails im Posteingang.\n"
        return (head + "\n".join(rows))[:6000] if rows else head + "Keine passenden Mails."
    except imaplib.IMAP4.error as e:
        return f"IMAP-Anmeldung fehlgeschlagen ({e}). IMAP aktiviert und Passwort richtig?"
    except Exception as e:  # noqa: BLE001
        return f"Postfach nicht erreichbar: {e}"


HA_URL = os.environ.get("HA_URL", "http://homeassistant.local:8123").strip().rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "").strip()
# Harmless to switch by voice. Locks, alarms, scripts, climate etc. stay out.
HA_DOMAINS = {"light", "switch", "fan", "media_player", "scene"}
HA_SHOWN = HA_DOMAINS | {"sensor", "binary_sensor", "climate", "cover", "weather", "person"}


def _ha(path, body=None):
    req = urllib.request.Request(HA_URL + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {HA_TOKEN}",
                                          "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read() or b"null")


def _home(name, args):
    if not HA_TOKEN:
        return ("Home Assistant noch nicht verbunden: In .env fehlt HA_TOKEN (langlebiges Zugriffstoken "
                "aus dem Home-Assistant-Profil). Das muss der User selbst eintragen.")
    try:
        if name == "home_states":
            q = (args.get("search") or "").lower()
            rows = []
            for s in _ha("/api/states"):
                eid = s["entity_id"]
                if eid.split(".")[0] not in HA_SHOWN or s["state"] in ("unavailable", "unknown"):
                    continue
                label = s.get("attributes", {}).get("friendly_name", eid)
                if q and q not in eid.lower() and q not in label.lower():
                    continue
                if not q and eid.split(".")[0] not in HA_DOMAINS:
                    continue   # unfiltered: switchable devices only — sensors via `search`
                unit = s.get("attributes", {}).get("unit_of_measurement", "")
                rows.append(f"{label} ({eid}): {s['state']} {unit}".rstrip())
            return "\n".join(sorted(rows))[:6000] or "Nichts gefunden."
        eid = (args.get("entity_id") or "").strip()
        domain, service = eid.split(".")[0], (args.get("service") or "").strip()
        if domain not in HA_DOMAINS:
            return f"Abgelehnt: {domain} darf ich nicht schalten, nur {', '.join(sorted(HA_DOMAINS))}."
        if not service.replace("_", "").isalnum():
            return "Abgelehnt: ungültiger Dienst."
        _ha(f"/api/services/{domain}/{service}", dict(args.get("data") or {}, entity_id=eid))
        return f"Erledigt: {domain}.{service} für {eid}."
    except urllib.error.HTTPError as e:
        return f"Home Assistant meldet Fehler {e.code}" + (" (Token ungültig?)" if e.code == 401 else "")
    except Exception as e:  # noqa: BLE001
        return f"Home Assistant nicht erreichbar: {e}"


SYNO_URL = os.environ.get("SYNOLOGY_URL", "").strip().rstrip("/")
SYNO_USER = os.environ.get("SYNOLOGY_USER", "").strip()
SYNO_PASSWORD = os.environ.get("SYNOLOGY_PASSWORD", "").strip()


def _syno(params, sid=None):
    import urllib.parse
    if sid:
        params = dict(params, _sid=sid)
    with urllib.request.urlopen(f"{SYNO_URL}/webapi/entry.cgi?{urllib.parse.urlencode(params)}",
                                timeout=20) as r:
        data = json.loads(r.read())
    if not data.get("success"):
        raise RuntimeError(f"DSM-Fehler {data.get('error', {}).get('code', '?')} bei {params.get('api')}")
    return data.get("data") or {}


def _nas_status():
    if not (SYNO_USER and SYNO_PASSWORD):
        return ("NAS noch nicht verbunden: In .env fehlen SYNOLOGY_USER und SYNOLOGY_PASSWORD (eigener DSM-Benutzer "
                "in der Gruppe administrators, ohne Zwei-Faktor). Das trägt der User selbst ein.")
    try:
        sid = _syno({"api": "SYNO.API.Auth", "version": 6, "method": "login", "account": SYNO_USER,
                     "passwd": SYNO_PASSWORD, "session": "jarvis", "format": "sid"})["sid"]
    except Exception as e:  # noqa: BLE001
        return f"NAS-Anmeldung fehlgeschlagen ({e}). 400/401 = Benutzer oder Passwort falsch, 403 = Zwei-Faktor aktiv."
    gb = lambda b: f"{int(b) / 1e9:.0f} GB" if int(b) < 1e12 else f"{int(b) / 1e12:.1f} TB"  # noqa: E731
    rows = []
    try:
        for label, call in (
            ("system", {"api": "SYNO.Core.System", "version": 1, "method": "info"}),
            ("last", {"api": "SYNO.Core.System.Utilization", "version": 1, "method": "get"}),
            ("speicher", {"api": "SYNO.Storage.CGI.Storage", "version": 1, "method": "load_info"}),
        ):
            try:
                d = _syno(call, sid)
            except Exception as e:  # noqa: BLE001
                rows.append(f"{label}: nicht lesbar ({e})")
                continue
            if label == "system":
                rows.append(f"modell: {d.get('model', '?')}, {d.get('firmware_ver', '?')}, "
                            f"temperatur {d.get('sys_temp', '?')} C, laufzeit {d.get('up_time', '?')}")
            elif label == "last":
                cpu, mem = d.get("cpu", {}), d.get("memory", {})
                rows.append(f"cpu: {int(cpu.get('user_load', 0)) + int(cpu.get('system_load', 0))} %, "
                            f"ram: {mem.get('real_usage', '?')} %")
            else:
                for v in d.get("volumes", []):
                    size = v.get("size", {})
                    used, total = int(size.get("used", 0)), int(size.get("total", 0)) or 1
                    rows.append(f"volume {v.get('display_name', v.get('id'))}: {gb(used)} von {gb(total)} "
                                f"belegt ({used * 100 // total} %), status {v.get('status', '?')}")
                for k in d.get("disks", []):
                    rows.append(f"platte {k.get('name', k.get('id'))} ({k.get('model', '').strip()}, "
                                f"{gb(k.get('size_total', 0))}): status {k.get('status', '?')}, "
                                f"smart {k.get('smart_status', '?')}, {k.get('temp', '?')} C")
    finally:
        try:
            _syno({"api": "SYNO.API.Auth", "version": 6, "method": "logout", "session": "jarvis"}, sid)
        except Exception:  # noqa: BLE001
            pass
    return "\n".join(rows)[:4000]


GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL", "").strip()
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD", "").strip()
GARMIN_TOKENS = os.path.join(HERE, ".garmin_tokens")   # login once, then reuse the session tokens


def _garmin(args):
    import datetime as dt
    if not (GARMIN_EMAIL and GARMIN_PASSWORD):
        return "Garmin noch nicht verbunden: In .env fehlen GARMIN_EMAIL und GARMIN_PASSWORD. Das trägt der User selbst ein."
    try:
        from garminconnect import Garmin
    except ImportError:
        return "Das Python-Paket garminconnect fehlt noch (pip install garminconnect in der .venv)."
    try:
        g = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD, return_on_mfa=True)
        needs_mfa, _ = g.login(GARMIN_TOKENS)   # reuses stored tokens, else logs in with the password
        if needs_mfa:
            return "Garmin verlangt Zwei-Faktor-Anmeldung — die kann ich nicht beantworten. Bitte dort abschalten."
        os.makedirs(GARMIN_TOKENS, exist_ok=True)
        g.client.dump(GARMIN_TOKENS)
    except Exception as e:  # noqa: BLE001
        return f"Garmin-Anmeldung fehlgeschlagen ({e}). Passwort richtig, Zwei-Faktor aus?"
    hm = lambda s: f"{int(s or 0) // 3600}:{int(s or 0) % 3600 // 60:02d} h"  # noqa: E731
    rows = []
    days = max(1, min(int(args.get("days") or 1), 14))
    for i in range(days):
        day = (dt.date.today() - dt.timedelta(days=i)).isoformat()
        parts = [day]
        try:
            s = (g.get_sleep_data(day) or {}).get("dailySleepDTO") or {}
            if s.get("sleepTimeSeconds"):
                score = ((s.get("sleepScores") or {}).get("overall") or {}).get("value", "?")
                parts.append(f"schlaf {hm(s['sleepTimeSeconds'])} (tief {hm(s.get('deepSleepSeconds'))}, "
                             f"REM {hm(s.get('remSleepSeconds'))}, wach {hm(s.get('awakeSleepSeconds'))}), score {score}")
        except Exception as e:  # noqa: BLE001
            parts.append(f"schlaf nicht lesbar ({e})")
        try:
            st = g.get_stats(day) or {}
            parts.append(f"schritte {st.get('totalSteps', '?')}, ruhepuls {st.get('restingHeartRate', '?')}, "
                         f"stress {st.get('averageStressLevel', '?')}, body battery "
                         f"{st.get('bodyBatteryLowestValue', '?')}–{st.get('bodyBatteryHighestValue', '?')}, "
                         f"jetzt {st.get('bodyBatteryMostRecentValue', '?')}")
        except Exception as e:  # noqa: BLE001
            parts.append(f"tageswerte nicht lesbar ({e})")
        rows.append(" · ".join(parts))
    return "\n".join(rows)[:5000]


PI_HOST = os.environ.get("JARVIS_PI_HOST", "").strip()
PI_USER = os.environ.get("JARVIS_PI_USER", "pi").strip()
# Fixed, read-only command — the model cannot change what runs here.
PI_STATUS_CMD = (
    "echo host: $(hostname); echo uptime: $(uptime -p); "
    "echo temp: $(awk '{printf \"%.1f C\", $1/1000}' /sys/class/thermal/thermal_zone0/temp 2>/dev/null); "
    "echo load: $(cut -d' ' -f1-3 /proc/loadavg); "
    "free -m | awk '/Mem:/{print \"ram: \" $3 \" von \" $2 \" MB belegt\"}'; "
    "df -h / | awk 'NR==2{print \"disk: \" $3 \" von \" $2 \" belegt (\" $5 \")\"}'; "
    "for s in pihole-FTL docker home-assistant@homeassistant hassio-supervisor; do "
    "st=$(systemctl is-active $s 2>/dev/null); [ \"$st\" != inactive ] && [ -n \"$st\" ] && echo \"dienst $s: $st\"; done; "
    "command -v docker >/dev/null && docker ps --format 'container {{.Names}}: {{.Status}}' 2>/dev/null | head -12; "
    "true"
)


def _network_devices():
    ps = ("Get-NetNeighbor -AddressFamily IPv4 | Where-Object { $_.State -ne 'Unreachable' -and "
          "$_.LinkLayerAddress -and $_.LinkLayerAddress -notmatch '^(00-00-00-00-00-00|FF-FF-FF-FF-FF-FF|01-00-5E)' } | "
          "ForEach-Object { $n = try { [System.Net.Dns]::GetHostEntry($_.IPAddress).HostName } catch { '' }; "
          "'{0,-16} {1,-18} {2}' -f $_.IPAddress, $_.LinkLayerAddress, $n }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                         timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return (out.stdout.strip() or out.stderr.strip() or "Keine Geräte gefunden.")[:4000]


def _pi_status():
    out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", f"{PI_USER}@{PI_HOST}",
                          PI_STATUS_CMD], capture_output=True, text=True, timeout=40,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if out.returncode != 0 and not out.stdout.strip():
        return f"Pi nicht erreichbar ({PI_USER}@{PI_HOST}): {out.stderr.strip()[:300]}"
    return out.stdout.strip()[:4000]


def _calendar(name, args):
    import datetime as dt
    import icloud_calendar as cal
    if not cal.configured():
        return ("Kalender noch nicht eingerichtet: In .env fehlen ICLOUD_USER und ICLOUD_APP_PASSWORD "
                "(app-spezifisches Passwort von account.apple.com). Das muss der User selbst eintragen.")
    try:
        if name == "calendar_events":
            start = args.get("start")
            day = dt.datetime.strptime(start[:10], "%Y-%m-%d").date() if start else None
            names = ", ".join(n for n, _ in cal.calendars())
            return f"Kalender: {names}\n" + cal.describe(cal.events(day, args.get("days") or 1))
        return cal.add_event(args.get("title", ""), args.get("start", ""), args.get("end"),
                             args.get("location", ""), args.get("notes", ""), args.get("calendar", ""))
    except Exception as e:  # noqa: BLE001
        return f"Kalenderfehler: {e}"


def _ask(tool_name, tool_input):
    body = json.dumps({"tool_name": tool_name, "input": tool_input}).encode()
    req = urllib.request.Request(PERM_URL, data=body, method="POST", headers={
        "content-type": "application/json", "X-Jarvis-Perm": PERM_TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=PERM_TIMEOUT + 15) as r:
            allowed = bool(json.loads(r.read()).get("allow"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"behavior": "deny", "message": f"Bestätigung nicht möglich ({e}). Abgelehnt."}
    if allowed:
        return {"behavior": "allow", "updatedInput": tool_input}
    return {"behavior": "deny", "message": "Der User hat diese Aktion nicht bestätigt. Nicht ausführen, keinen Umweg versuchen."}


def _open(url):
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url if url and "://" not in url else ""
    if not url:
        return "Abgelehnt: nur http(s)-Adressen."
    if BROWSER and os.path.exists(BROWSER):
        subprocess.Popen([BROWSER, url], close_fds=True,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    else:
        os.startfile(url)            # system default browser
    return f"Geöffnet: {url}"


def _reply(mid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": mid}
    if error:
        msg["error"] = {"code": -32601, "message": error}
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid, method = msg.get("id"), msg.get("method")
        if mid is None:          # notifications need no answer
            continue
        if method == "initialize":
            _reply(mid, {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2024-11-05"),
                         "capabilities": {"tools": {}},
                         "serverInfo": {"name": "jarvis", "version": "1.0"}})
        elif method == "tools/list":
            _reply(mid, {"tools": listed_tools()})
        elif method == "tools/call":
            params = msg.get("params") or {}
            name, args = params.get("name"), params.get("arguments") or {}
            if name == "approve":
                text = json.dumps(_ask(args.get("tool_name", "?"), args.get("input") or {}))
            elif name == "open_website":
                text = _open(args.get("url"))
            elif name in ("calendar_events", "calendar_add"):
                text = _calendar(name, args)
            elif name == "music":
                try:
                    text = _music(args)
                except Exception as e:  # noqa: BLE001
                    text = f"Musik-Fehler: {e}"
            elif name == "mail_inbox":
                text = _mail(args)
            elif name in ("nas_status", "garmin_health"):
                try:
                    text = _nas_status() if name == "nas_status" else _garmin(args)
                except Exception as e:  # noqa: BLE001
                    text = f"Fehler: {e}"
            elif name in ("home_states", "home_control"):
                text = _home(name, args)
            elif name in ("network_devices", "pi_status"):
                try:
                    text = _network_devices() if name == "network_devices" else _pi_status()
                except Exception as e:  # noqa: BLE001
                    text = f"Fehler: {e}"
            elif name == "web_fetch":
                text = _web_fetch(args.get("url"))
            elif name in ("memory_search", "memory_read", "memory_save"):
                try:
                    text = _memory(name, args)
                except Exception as e:  # noqa: BLE001
                    text = f"Gedächtnis-Fehler: {e}"
            else:
                _reply(mid, error=f"unknown tool {name}")
                continue
            if name != "approve" and len(text) > MAX_RESULT:
                text = text[:MAX_RESULT] + f" … [gekürzt, {len(text)} Zeichen]"
            _reply(mid, {"content": [{"type": "text", "text": text}]})
        elif method == "ping":
            _reply(mid, {})
        else:
            _reply(mid, error=f"unsupported method {method}")


if __name__ == "__main__":
    main()
