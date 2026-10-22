"""
iPhone calendar for JARVIS — iCloud over CalDAV, standard library only.

Needs two lines in .env:
    ICLOUD_USER=<Apple-ID e-mail>
    ICLOUD_APP_PASSWORD=<app-specific password from account.apple.com>
Optional:
    ICLOUD_CALENDARS=Privat,Arbeit     (only these calendars; default: all)
    ICLOUD_DEFAULT_CALENDAR=Privat     (where new events go; default: first one)

Reading is free for JARVIS; creating events goes through the HUD confirmation
card (the add tool is deliberately not pre-allowed).
"""
import base64
import datetime as dt
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET

BASE = os.environ.get("ICLOUD_CALDAV_URL", "https://caldav.icloud.com/")
NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav", "cs": "http://calendarserver.org/ns/",
      "ical": "http://apple.com/ns/ical/"}
_CACHE = {"cals": None, "at": 0.0, "agenda": None, "agenda_at": 0.0}
_LOCK = threading.Lock()


def configured():
    return bool(os.environ.get("ICLOUD_USER") and os.environ.get("ICLOUD_APP_PASSWORD"))


# ── HTTP ────────────────────────────────────────────────────────
def _request(method, url, body=None, depth=None, ctype="application/xml; charset=utf-8", extra=None):
    """CalDAV request that follows iCloud's cross-host redirects with the same
    method and auth (urllib only redirects GET)."""
    cred = base64.b64encode(f"{os.environ['ICLOUD_USER']}:{os.environ['ICLOUD_APP_PASSWORD']}".encode()).decode()
    for _ in range(5):
        headers = {"Authorization": "Basic " + cred, "User-Agent": "JARVIS-HUD/1.0"}
        if body is not None:
            headers["Content-Type"] = ctype
        if depth is not None:
            headers["Depth"] = str(depth)
        headers.update(extra or {})
        req = urllib.request.Request(url, data=body.encode() if isinstance(body, str) else body,
                                     method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.status, r.read().decode("utf-8", "replace"), r.geturl()
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 307, 308) and e.headers.get("Location"):
                url = urllib.parse.urljoin(url, e.headers["Location"])
                continue
            if e.code == 401:
                raise RuntimeError("iCloud lehnt die Anmeldung ab — Apple-ID oder app-spezifisches Passwort prüfen.")
            raise RuntimeError(f"iCloud CalDAV HTTP {e.code}: {e.read(200).decode('utf-8', 'replace')}")
    raise RuntimeError("zu viele Weiterleitungen von iCloud")


def _propfind(url, props, depth=0):
    body = ('<?xml version="1.0" encoding="utf-8"?><d:propfind xmlns:d="DAV:" '
            'xmlns:c="urn:ietf:params:xml:ns:caldav" xmlns:cs="http://calendarserver.org/ns/" '
            'xmlns:ical="http://apple.com/ns/ical/"><d:prop>' + props + '</d:prop></d:propfind>')
    _, text, final = _request("PROPFIND", url, body, depth)
    return ET.fromstring(text), final


def calendars(force=False):
    """[(name, url)] of event calendars, discovered once per 6 h."""
    with _LOCK:
        if _CACHE["cals"] and not force and time.time() - _CACHE["at"] < 6 * 3600:
            return _CACHE["cals"]
        root, final = _propfind(BASE, "<d:current-user-principal/>")
        href = root.find(".//d:current-user-principal/d:href", NS).text
        principal = urllib.parse.urljoin(final, href)
        root, final = _propfind(principal, "<c:calendar-home-set/>")
        home = urllib.parse.urljoin(final, root.find(".//c:calendar-home-set/d:href", NS).text)
        root, final = _propfind(home, "<d:displayname/><d:resourcetype/>"
                                      "<c:supported-calendar-component-set/>", depth=1)
        wanted = {n.strip().lower() for n in os.environ.get("ICLOUD_CALENDARS", "").split(",") if n.strip()}
        cals = []
        for resp in root.findall("d:response", NS):
            if resp.find(".//d:resourcetype/c:calendar", NS) is None:
                continue
            comps = [c.get("name") for c in resp.findall(".//c:supported-calendar-component-set/c:comp", NS)]
            if comps and "VEVENT" not in comps:
                continue   # reminders lists etc.
            name = (resp.findtext(".//d:displayname", default="", namespaces=NS) or "Kalender").strip()
            if wanted and name.lower() not in wanted:
                continue
            cals.append((name, urllib.parse.urljoin(final, resp.find("d:href", NS).text)))
        _CACHE.update(cals=cals, at=time.time())
        return cals


# ── iCalendar parsing ───────────────────────────────────────────
def _unfold(ics):
    return re.sub(r"\r?\n[ \t]", "", ics).splitlines()


def _parse_dt(value, params):
    """-> (datetime in local time, all_day)"""
    if "VALUE=DATE" in params or re.fullmatch(r"\d{8}", value):
        return dt.datetime.strptime(value[:8], "%Y%m%d"), True
    if value.endswith("Z"):
        utc = dt.datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
        return utc.astimezone().replace(tzinfo=None), False
    return dt.datetime.strptime(value[:15], "%Y%m%dT%H%M%S"), False   # TZID: assume local


def _unescape(v):
    return v.replace("\\n", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\").strip()


def parse_events(ics, calendar=""):
    out, cur = [], None
    for line in _unfold(ics):
        if line == "BEGIN:VEVENT":
            cur = {"calendar": calendar}
        elif line == "END:VEVENT" and cur is not None:
            if "start" in cur:
                out.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            head, value = line.split(":", 1)
            name, _, params = head.partition(";")
            if name == "SUMMARY":
                cur["title"] = _unescape(value)
            elif name == "LOCATION":
                cur["location"] = _unescape(value)
            elif name in ("DTSTART", "DTEND"):
                when, allday = _parse_dt(value, params)
                cur["start" if name == "DTSTART" else "end"] = when
                if name == "DTSTART":
                    cur["allday"] = allday
    return out


# ── public API ──────────────────────────────────────────────────
def _stamp(d):
    return d.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def events(start=None, days=1):
    """Events from `start` (date or datetime, default today) for `days` days,
    recurring ones expanded by the server. Sorted by start."""
    if not configured():
        raise RuntimeError("iCloud-Kalender ist nicht eingerichtet (ICLOUD_USER / ICLOUD_APP_PASSWORD fehlen).")
    if start is None:
        start = dt.date.today()
    if isinstance(start, dt.date) and not isinstance(start, dt.datetime):
        start = dt.datetime.combine(start, dt.time())
    begin = start.astimezone()
    end = begin + dt.timedelta(days=max(1, min(int(days), 62)))
    rng = f'start="{_stamp(begin)}" end="{_stamp(end)}"'
    body = ('<?xml version="1.0" encoding="utf-8"?><c:calendar-query xmlns:d="DAV:" '
            'xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><c:calendar-data>'
            f'<c:expand {rng}/></c:calendar-data></d:prop><c:filter><c:comp-filter name="VCALENDAR">'
            f'<c:comp-filter name="VEVENT"><c:time-range {rng}/></c:comp-filter></c:comp-filter>'
            '</c:filter></c:calendar-query>')
    found = []
    for name, url in calendars():
        _, text, _ = _request("REPORT", url, body, depth=1)
        root = ET.fromstring(text)
        for data in root.iter("{urn:ietf:params:xml:ns:caldav}calendar-data"):
            found += parse_events(data.text or "", name)
    lo, hi = begin.replace(tzinfo=None), end.replace(tzinfo=None)
    found = [e for e in found if e["start"] < hi and e.get("end", e["start"]) >= lo]
    return sorted(found, key=lambda e: (e["start"], e.get("title", "")))


def describe(evts):
    if not evts:
        return "Keine Termine."
    lines, day = [], None
    for e in evts:
        d = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")[e["start"].weekday()] + e["start"].strftime(" %d.%m.")
        if d != day:
            lines.append(d)
            day = d
        t = "ganztägig" if e.get("allday") else e["start"].strftime("%H:%M") + (
            "–" + e["end"].strftime("%H:%M") if e.get("end") else "")
        loc = f" ({e['location']})" if e.get("location") else ""
        lines.append(f"  {t}  {e.get('title', '(ohne Titel)')}{loc}  [{e['calendar']}]")
    return "\n".join(lines)


def agenda_block():
    """Today + tomorrow for the turn context. Never raises and never makes a
    question wait on iCloud: a stale cache is returned at once and refreshed
    in the background (only the very first call fetches synchronously)."""
    if not configured():
        return ""
    fresh = time.time() - _CACHE["agenda_at"] < 600
    if _CACHE["agenda"] is not None:
        if not fresh and not _CACHE.get("refreshing"):
            _CACHE["refreshing"] = True
            threading.Thread(target=_refresh_agenda, daemon=True).start()
        return _CACHE["agenda"]
    return _refresh_agenda()


def _refresh_agenda():
    try:
        text = describe(events(dt.date.today(), 2))
        block = "## Kalender (iPhone/iCloud) — heute und morgen\n" + text
    except Exception as e:  # noqa: BLE001
        block = f"## Kalender (iPhone/iCloud)\nNicht erreichbar: {str(e)[:160]}"
    _CACHE.update(agenda=block, agenda_at=time.time(), refreshing=False)
    return block


def add_event(title, start, end=None, location="", notes="", calendar="", allday=False):
    """Create an event. start/end: 'YYYY-MM-DD HH:MM' (local) or 'YYYY-MM-DD' for all-day."""
    if not configured():
        raise RuntimeError("iCloud-Kalender ist nicht eingerichtet.")
    cals = calendars()
    if not cals:
        raise RuntimeError("Keine Kalender gefunden.")
    want = (calendar or os.environ.get("ICLOUD_DEFAULT_CALENDAR", "")).strip().lower()
    target = next((c for c in cals if c[0].lower() == want), cals[0])

    def esc(v):
        return (v or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    allday = allday or bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", start.strip()))
    if allday:
        s = dt.datetime.strptime(start.strip()[:10], "%Y-%m-%d")
        e = dt.datetime.strptime(end.strip()[:10], "%Y-%m-%d") if end else s
        times = (f"DTSTART;VALUE=DATE:{s:%Y%m%d}", f"DTEND;VALUE=DATE:{e + dt.timedelta(days=1):%Y%m%d}")
    else:
        s = dt.datetime.strptime(start.strip()[:16].replace("T", " "), "%Y-%m-%d %H:%M")
        e = dt.datetime.strptime(end.strip()[:16].replace("T", " "), "%Y-%m-%d %H:%M") if end else s + dt.timedelta(hours=1)
        times = (f"DTSTART:{_stamp(s.astimezone())}", f"DTEND:{_stamp(e.astimezone())}")
    uid = f"{uuid.uuid4()}@jarvis-hud"
    ics = "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//JARVIS HUD//DE", "BEGIN:VEVENT",
        f"UID:{uid}", f"DTSTAMP:{_stamp(dt.datetime.now().astimezone())}", *times,
        f"SUMMARY:{esc(title)}",
        *([f"LOCATION:{esc(location)}"] if location else []),
        *([f"DESCRIPTION:{esc(notes)}"] if notes else []),
        "END:VEVENT", "END:VCALENDAR", ""])
    url = target[1].rstrip("/") + f"/{uid}.ics"
    _request("PUT", url, ics, ctype="text/calendar; charset=utf-8", extra={"If-None-Match": "*"})
    _CACHE["agenda"] = None
    when = s.strftime("%d.%m.%Y") + ("" if allday else s.strftime(" %H:%M"))
    return f"Eingetragen in '{target[0]}': {title} am {when}."
