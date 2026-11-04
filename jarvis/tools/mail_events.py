"""
Appointments from mail — booking confirmations (PC CADDIE tee times, golftimer
lessons, Golf House fittings ...) found in the inbox and offered for the calendar.

  scan()          future events from the calendar attachments (.ics) of the last 24 hours'
                  mails (mailboxes.LOOKBACK_H), minus cancelled ones and minus what the
                  calendar already holds (icloud_calendar.check: same day, similar title);
                  an overlap with another entry goes with the offer.
                  Read-only; the mail content counts as untrusted (policy.UNTRUSTED).
  describe(c)     the list with an id per event, for the model and the card
  add(ids)        writes the chosen ones to ICLOUD_DEFAULT_CALENDAR — only through a
                  confirmation card (policy: mail_events_add = CONFIRM)
  check()         background, every half hour from DAILY_HOUR: bookings from TRUSTED senders
                  are entered right away and announced; all others become one proposal
                  (kind "calendar") that goes through a card
  undo()          takes the automatically entered ones out again ("rückgängig")

The one exception to "background jobs never change anything" (CLAUDE.md, SECURITY.md),
chosen by the user: TRUSTED senders only, checked on the real address (not the display
name), done by this fixed code and never by the model, no links in the entry, every
entry announced and undoable. A forged sender is the remaining risk; the mail provider's
spam filter is the guard there.

Only mails with a calendar attachment are read as appointments: booking systems write
those reliably, prose would need guessing. Cancellations are an attachment with
METHOD:CANCEL / STATUS:CANCELLED, or a "Stornierung" mail naming the same start time
(PC CADDIE sends those without an attachment).
"""
import datetime as dt
import hashlib
import html
import json
import os
import pathlib
import re
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
PATH = pathlib.Path(os.environ.get("JARVIS_MAIL_EVENTS", ROOT / "logs" / "mail_events.json"))
DAILY = os.environ.get("JARVIS_MAIL_EVENTS_DAILY", "1").strip().lower() not in ("0", "off", "false", "no", "aus")
DAILY_HOUR = int(os.environ.get("JARVIS_MAIL_EVENTS_HOUR", "7"))
CACHE_S = 1800        # add() trusts a scan this fresh, otherwise it looks again
SAME_START_MIN = 10   # an entry this close in the calendar counts as already there
# Domains whose bookings go in without a card (subdomains too). Extend in .env.
TRUSTED = tuple(d.strip().lower().lstrip("@.") for d in os.environ.get(
    "JARVIS_MAIL_EVENTS_TRUSTED", "pccaddie-online.de,golftimer.de,golfhouse.de").split(",") if d.strip())
QUIET_FROM = 22       # no automatic entries (and no announcement) late at night
CANCEL_RE = re.compile(r"stornier|storno|abgesagt|absage|cancel", re.I)
URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)
WHEN_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})[,\s]+(?:um\s+)?(\d{1,2}):(\d{2})")


# ── reading mail ─────────────────────────────────────────────
def _dec(v):
    import email.header
    try:
        return str(email.header.make_header(email.header.decode_header(v or ""))).strip()
    except Exception:  # noqa: BLE001 — a broken header is still worth its raw text
        return (v or "").strip()


def _text(msg):
    """Plain text of a mail; HTML stripped when there is no text part."""
    plain = rich = ""
    for part in msg.walk():
        if part.get_filename():
            continue
        ct = part.get_content_type()
        if ct in ("text/plain", "text/html"):
            t = (part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", "replace")
            if ct == "text/plain" and not plain:
                plain = t
            elif ct == "text/html" and not rich:
                rich = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(style|script).*?</\1>", " ", t)))
    return " ".join((plain or rich).split())


def _mails(hours=None):
    """-> [{from, addr, subject, date, text, ics: [str], account}] of every account's inbox, last
    `hours` (at most mailboxes.LOOKBACK_H), that carry a calendar attachment or a cancellation
    in the subject. Read-only."""
    import email
    import email.utils
    import mailboxes
    if not mailboxes.configured():
        raise RuntimeError("Postfach nicht verbunden (MAIL_IMAP_HOST, MAIL_USER, MAIL_PASSWORD)")

    def read(acc, box):
        ids = mailboxes.recent(box, hours)
        if not ids:
            return []
        # One cheap round for all: structure and subject; full mails only where it matters.
        _, rows = box.fetch(b",".join(ids), "(BODYSTRUCTURE BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
        wanted = []
        for row in rows:
            if not isinstance(row, tuple):
                continue
            head = row[0].decode("utf-8", "replace")
            subj = _dec(email.message_from_bytes(row[1]).get("Subject"))
            if '"calendar"' in head.lower() or CANCEL_RE.search(subj):
                wanted.append(head.split()[0])
        out = []
        for i in wanted:
            _, parts = box.fetch(i, "(BODY.PEEK[])")
            msg = email.message_from_bytes(parts[0][1])
            ics = [(p.get_payload(decode=True) or b"").decode(p.get_content_charset() or "utf-8", "replace")
                   for p in msg.walk() if p.get_content_type() == "text/calendar"]
            sent = email.utils.parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None
            out.append({"from": re.sub(r"\s*<[^>]*>", "", _dec(msg.get("From"))).strip('" '),
                        "addr": email.utils.parseaddr(msg.get("From") or "")[1].lower(),
                        "subject": _dec(msg.get("Subject")), "date": sent.strftime("%d.%m.%Y") if sent else "",
                        "text": _text(msg), "ics": ics, "account": acc["name"]})
        return out

    results, errors = mailboxes.each(read)
    if not results and errors:
        raise RuntimeError("; ".join(errors))
    return [m for r in results for m in r]

# ── understanding ────────────────────────────────────────────
def parse_ics(ics):
    """[{uid, title, start, end, allday, location, description, cancelled}] — the calendar
    module's parser plus what it leaves out (description, uid, cancellation)."""
    import icloud_calendar as cal
    method_cancel = bool(re.search(r"^METHOD:CANCEL", ics, re.M | re.I))
    out, cur = [], None
    for line in cal._unfold(ics):
        if line == "BEGIN:VEVENT":
            cur = {"cancelled": method_cancel}
        elif line == "END:VEVENT" and cur is not None:
            if "start" in cur:
                out.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            head, value = line.split(":", 1)
            name, _, params = head.partition(";")
            if name == "SUMMARY":
                cur["title"] = cal._unescape(value)
            elif name == "LOCATION":
                cur["location"] = cal._unescape(value)
            elif name == "DESCRIPTION":
                cur["description"] = cal._unescape(value)
            elif name == "UID":
                cur["uid"] = value.strip()
            elif name == "STATUS" and value.strip().upper() == "CANCELLED":
                cur["cancelled"] = True
            elif name in ("DTSTART", "DTEND"):
                when, allday = cal._parse_dt(value, params)
                cur["start" if name == "DTSTART" else "end"] = when
                if name == "DTSTART":
                    cur["allday"] = allday
    return out


def cancelled_starts(mail):
    """Start times a cancellation mail names ("18.10.2026 08:30 Uhr")."""
    if not CANCEL_RE.search(mail["subject"] + " " + mail["text"][:400]):
        return []
    found = []
    for d, m, y, hh, mm in WHEN_RE.findall(mail["subject"] + " " + mail["text"]):
        try:
            found.append(dt.datetime(int(y), int(m), int(d), int(hh), int(mm)))
        except ValueError:
            pass
    return found


def _title(ev, mail):
    """The attachment's title; when that is only the shop's name (Golf House), the kind of
    booking from the mail text goes with it ("Buchung: 60-minütiges Fitting")."""
    title = (ev.get("title") or mail["subject"] or "Termin").strip()
    # "Training bei X (16:00 - 16:50)": the calendar shows the time anyway
    title = re.sub(r"\s*\(\d{1,2}[:.]\d{2}\s*[-–]\s*\d{1,2}[:.]\d{2}(?:\s*Uhr)?\)\s*$", "", title)
    kind = re.search(r"Buchung:\s*(.{3,60}?)\s+(?:Datum|Zeit|Ort|Uhrzeit)\b", mail["text"])
    if kind and kind.group(1).lower() not in title.lower():
        title = f"{title} · {kind.group(1).strip()}"
    return title[:120]


def trusted(addr):
    """True for an address at a TRUSTED domain or one of its subdomains."""
    dom = (addr or "").rsplit("@", 1)[-1].lower()
    return bool(dom) and any(dom == d or dom.endswith("." + d) for d in TRUSTED)


def _id(ev):
    return hashlib.sha1(f"{ev.get('uid', '')}|{ev['start']:%Y%m%d%H%M}".encode()).hexdigest()[:6]


def candidates(mails, existing, now=None):
    """Pure: mails (as _mails returns them) and the calendar's events -> what to offer."""
    now = now or dt.datetime.now()
    cancelled = [s for m in mails for s in cancelled_starts(m)]
    out, seen = [], set()
    for m in mails:
        for ics in m["ics"]:
            for ev in parse_ics(ics):
                start = ev["start"]
                if ev.get("cancelled") or start <= now:
                    if ev.get("cancelled"):
                        cancelled.append(start)
                    continue
                c = {"id": _id(ev), "title": _title(ev, m), "start": start, "end": ev.get("end"),
                     "allday": ev.get("allday", False), "location": ev.get("location", ""),
                     # no links from a mail end up in the calendar, whoever sent it
                     "notes": (URL_RE.sub("", ev.get("description", ""))[:600].strip() + "\n"
                               if ev.get("description") else "")
                     + f"Aus der Mail „{m['subject'][:80]}“ von {m['from']} ({m['date']}).",
                     "source": m["from"], "source_addr": m.get("addr", ""), "trusted": trusted(m.get("addr", ""))}
                if c["id"] not in seen:
                    seen.add(c["id"])
                    out.append(c)
    near = lambda a, b: abs((a - b).total_seconds()) <= SAME_START_MIN * 60  # noqa: E731
    # A Teams or Outlook invitation carries an .ics too — work stays out of Jarvis (privacy.py),
    # so it is never offered for the private calendar.
    import privacy
    out = [c for c in out if not privacy.is_work(c["title"], c["notes"], c["location"], c["source"])]
    out = [c for c in out if not any(near(c["start"], s) for s in cancelled)]
    out = [c for c in out if not any(near(c["start"], e["start"]) for e in existing)]
    # what the calendar holds that day under a similar title is there already; an overlap is named
    import icloud_calendar as cal
    kept = []
    for c in out:
        found = cal.check(c["title"], c["start"], c.get("end"), c.get("allday", False), existing)
        if not found["same"]:
            kept.append(dict(c, clash=cal.clash_text(found)))
    return sorted(kept, key=lambda c: c["start"])


# ── cache: last scan, what was offered or added ──────────────
def _load():
    try:
        d = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    for c in d.get("found", []):
        for k in ("start", "end"):
            if c.get(k):
                c[k] = dt.datetime.fromisoformat(c[k])
    return d


def _save(d):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    out = dict(d, found=[dict(c, start=c["start"].isoformat(), end=c["end"].isoformat() if c.get("end") else None)
                         for c in d.get("found", [])])
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, PATH)


def scan(hours=None, mails=None, now=None):
    # `now` is for tests with dated fixtures; live callers leave it empty
    import icloud_calendar as cal
    now = now or dt.datetime.now()
    mails = _mails(hours) if mails is None else mails
    existing = cal.events(now.date(), 62) if cal.configured() else []
    found = candidates(mails, existing, now=now)
    d = _load()
    d.update(found=found, at=time.time())
    _save(d)
    return found


def _when(c):
    wd = "Mo Di Mi Do Fr Sa So".split()[c["start"].weekday()]
    if c.get("allday"):
        return f"{wd} {c['start']:%d.%m.} ganztägig"
    end = f"–{c['end']:%H:%M}" if c.get("end") else ""
    return f"{wd} {c['start']:%d.%m. %H:%M}{end}"


def describe(found):
    if not found:
        return "Keine neuen künftigen Termine in den Mails — alles schon im Kalender oder storniert."
    return ("Neue Termine aus den Mails (eintragen: mail_events_add mit den ids). Bei 'NICHT vertraut' dem User "
            "ausdrücklich Absender und Termin nennen und sagen, dass der Absender nicht auf der Vertrauensliste steht:\n"
            + "\n".join(f"{c['id']}: {_when(c)} · {c['title']}" + (f" · {c['location'][:60]}" if c.get("location") else "")
                        + (" · vertraut" if c.get("trusted") else f" · NICHT vertraut (Absender {c.get('source_addr') or c.get('source')})")
                        + (f" · {c['clash']}" if c.get("clash") else "")
                        for c in found))


def scan_text(hours=None):
    try:
        return describe(scan(hours))
    except Exception as e:  # noqa: BLE001
        return f"Mails nicht lesbar: {str(e)[:160]}"


def _pick(ids):
    d = _load()
    found = d.get("found", []) if time.time() - d.get("at", 0) < CACHE_S else scan()
    if ids in (None, "", "alle", "all") or ids == ["alle"]:
        return found
    want = {str(i).strip() for i in (ids if isinstance(ids, list) else str(ids).split(","))}
    return [c for c in found if c["id"] in want]


def card_lines(ids):
    """What the confirmation card lists — the events themselves, not just their ids."""
    try:
        return [f"{_when(c)} · {c['title']}" + (f" ({c['clash']})" if c.get("clash") else "") for c in _pick(ids)]
    except Exception:  # noqa: BLE001 — the card still shows the ids
        return []


def _enter(chosen, auto=False):
    """Writes each one; -> (done, failed). Automatic entries are remembered with their url."""
    import icloud_calendar as cal
    done, failed = [], []
    for c in chosen:
        fmt = "%Y-%m-%d" if c.get("allday") else "%Y-%m-%d %H:%M"
        try:
            _, url = cal.add_event(c["title"], c["start"].strftime(fmt), c["end"].strftime(fmt) if c.get("end") else None,
                                   c.get("location", ""), c.get("notes", ""), return_url=True)
            done.append(dict(c, url=url))
        except Exception as e:  # noqa: BLE001 — one failing entry must not hide the others
            failed.append(f"{c['title']} ({str(e)[:60]})")
    if auto and done:
        d = _load()
        d["auto"] = (d.get("auto", []) + [{"id": c["id"], "url": c["url"], "what": f"{_when(c)} {c['title']}",
                                           "at": time.time()} for c in done])[-40:]
        _save(d)
    return done, failed


def add(ids):
    chosen = _pick(ids)
    if not chosen:
        return "Keine passenden Termine — erst mail_events_scan, dann die ids von dort."
    done, failed = _enter(chosen)
    d = _load()
    d["added"] = sorted(set(d.get("added", [])) | {c["id"] for c in done})
    d["found"] = [c for c in d.get("found", []) if c["id"] not in {x["id"] for x in done}]
    _save(d)
    target = os.environ.get("ICLOUD_DEFAULT_CALENDAR", "").strip() or "den ersten Kalender"
    text = f"{len(done)} Termin(e) in {target} eingetragen: " + "; ".join(f"{_when(c)} {c['title']}" for c in done)
    return text + (f". Nicht geklappt: {'; '.join(failed)}" if failed else "")


# ── background ───────────────────────────────────────────────
def withdraw_stale(found):
    """An open offer stays a week, but its events do not wait: past ones, ones in the calendar by
    now, cancelled ones or ones whose mail fell out of the window are no longer in `found`. An
    offer with none of its events left is withdrawn — before, Jarvis kept bringing it up.
    -> how many were withdrawn."""
    import proposals
    live, n = {c["id"] for c in found}, 0
    for p in proposals.pending():
        if p["kind"] == "calendar" and not live & set((p.get("payload") or {}).get("ids") or []):
            proposals.resolve(p["id"], "stale")
            n += 1
    return n


def check(now=None, mails=None):
    """Every half hour between DAILY_HOUR and QUIET_FROM: trusted bookings go straight in and
    are announced; the rest becomes one proposal (each id offered once)."""
    now = now or dt.datetime.now()
    if not DAILY_HOUR <= now.hour < QUIET_FROM:
        return
    found = scan(mails=mails, now=now)
    withdraw_stale(found)
    d = _load()
    done_before = set(d.get("offered", [])) | set(d.get("added", []))
    new = [c for c in found if c["id"] not in done_before]
    if not new:
        return
    import events
    auto = [c for c in new if c.get("trusted")]
    ask = [c for c in new if not c.get("trusted")]
    if auto:
        done, failed = _enter(auto, auto=True)
        d = _load()
        d["added"] = sorted(set(d.get("added", [])) | {c["id"] for c in done})
        _save(d)
        if done:
            what = "; ".join(f"{_when(c)} {c['title']}" + (f" ({c['clash']})" if c.get("clash") else "")
                             for c in done[:3])
            events.publish("say", f"Sir, ich habe {len(done)} Termin(e) aus Ihren Mails eingetragen: {what}. "
                                  "Sagen Sie „Termine rückgängig“, dann nehme ich sie wieder heraus.",
                           key="mail_events_auto")
    if ask:
        import proposals
        proposals.add("calendar", f"{len(ask)} Termin(e) aus Mails eintragen",
                      "Termine aus Mails von Absendern, die NICHT auf der Vertrauensliste stehen — beim "
                      "Erwähnen Absender und Termin nennen: "
                      + "; ".join(f"{c['source_addr'] or c['source']}: {_when(c)} {c['title']}"
                                  + (f" ({c['clash']})" if c.get("clash") else "") for c in ask[:4]),
                      payload={"ids": [c["id"] for c in ask]}, source="mail_events")
        d = _load()
        d["offered"] = sorted(set(d.get("offered", [])) | {c["id"] for c in ask})
        _save(d)
        # named explicitly: who sent it, what it is, and that the sender is not on the list
        which = "; ".join(f"{c['source_addr'] or c['source']}: {_when(c)} {c['title']}"
                          + (f" ({c['clash']})" if c.get("clash") else "") for c in ask[:3])
        more = f" und {len(ask) - 3} weitere" if len(ask) > 3 else ""
        events.publish("say", f"Sir, Achtung: {len(ask)} Mail(s) mit Termin von Absendern, die nicht auf der "
                              f"Vertrauensliste stehen — {which}{more}. Die trage ich nicht von selbst ein. "
                              "Soll ich sie eintragen?", key="mail_events")


def undo(hours=24):
    """Takes out what was entered automatically in the last `hours` (default: a day)."""
    import icloud_calendar as cal
    d = _load()
    cutoff = time.time() - float(hours or 24) * 3600
    recent = [a for a in d.get("auto", []) if a.get("at", 0) >= cutoff]
    # deliveries (shipments.py) count as automatic entries too: one "rückgängig" for both
    try:
        import shipments
        parcels_gone = shipments.undo(hours)
    except Exception:  # noqa: BLE001
        parcels_gone = []
    if not recent:
        if parcels_gone:
            return f"{len(parcels_gone)} Liefertermin(e) wieder herausgenommen: " + "; ".join(a["what"] for a in parcels_gone)
        return "In letzter Zeit habe ich keine Termine automatisch eingetragen."
    gone, failed = [], []
    for a in recent:
        try:
            cal.delete_event(a["url"])
            gone.append(a)
        except Exception as e:  # noqa: BLE001
            failed.append(f"{a['what']} ({str(e)[:60]})")
    ids = {a["id"] for a in gone}
    d["auto"] = [a for a in d.get("auto", []) if a["id"] not in ids]
    # stays in "added": the same booking must not come straight back in at the next check
    _save(d)
    gone = gone + parcels_gone
    text = f"{len(gone)} Termin(e) wieder herausgenommen: " + "; ".join(a["what"] for a in gone)
    return text + (f". Nicht geklappt: {'; '.join(failed)}" if failed else "")


def start():
    if DAILY:
        import background
        background.loop("mail_events", 1800, check)
