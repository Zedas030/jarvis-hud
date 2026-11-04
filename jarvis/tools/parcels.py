"""
Parcels — read from the shipping mails in the inbox, no tracking API, 0 tokens.

    "Is a parcel coming today?", "Where is my parcel?", "Which deliveries are on the way?"

Looks at the mail headers (sender, subject, date) of the last 24 hours
(mailboxes.LOOKBACK_H) for the usual carriers and shops and sorts them into: out for
delivery today, ready for pickup, on the way, delivered. Carrier mails come in German or English,
so the patterns know both. Read-only (IMAP readonly +
BODY.PEEK), nothing is marked.
The watcher announces "out for delivery today" once per mail.
"""
import email
import email.header
import email.utils
import imaplib
import re
import time

CARRIERS = r"dhl|deutsche post|hermes|dpd|gls|ups|fedex|amazon|go express|trans-o-flex"
STATUS = [
    ("today", r"heute|today|in zustellung|zustellung erfolgt heute|out for delivery|wird heute|kommt heute|"
              r"arriving today|auf dem weg zu (ihnen|dir)"),
    ("pickup", r"abhol|packstation|filiale|paketshop|paket-?shop|parcel ?shop|liegt bereit|pick ?up|collect"),
    ("delivered", r"zugestellt|delivered|wurde geliefert|ist angekommen|abgegeben|has arrived"),
    ("on the way", r"versandt|versendet|verschickt|unterwegs|shipped|dispatched|on its way|auf dem weg|sendung|"
                   r"lieferung|paket|parcel|package|shipment"),
]
_CACHE = {"at": 0.0, "items": []}


def classify(subject):
    s = (subject or "").lower()
    for name, pat in STATUS:
        if re.search(pat, s):
            return name
    return None


def _dec(v):
    try:
        return str(email.header.make_header(email.header.decode_header(v or ""))).strip()
    except Exception:  # noqa: BLE001
        return v or ""


def shipments(hours=None, ttl=600):
    """[{sender, subject, status, when (ts), id}] newest first, cached for ten minutes. Every account."""
    if time.time() - _CACHE["at"] < ttl:
        return _CACHE["items"]
    import mailboxes
    if not mailboxes.configured():
        return []
    out = []

    def read(acc, box):
        for i in mailboxes.recent(box, hours)[-200:]:
            _, parts = box.fetch(i, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])")
            msg = email.message_from_bytes(parts[0][1])
            frm, subj = _dec(msg.get("From")), _dec(msg.get("Subject"))
            if not re.search(CARRIERS, frm + " " + subj, re.I):
                continue
            st = classify(subj)
            if not st:
                continue
            name, addr = email.utils.parseaddr(frm)
            try:
                when = email.utils.parsedate_to_datetime(msg.get("Date")).timestamp()
            except Exception:  # noqa: BLE001
                when = time.time()
            out.append(dict(sender=name or addr, subject=subj, status=st, when=when,
                            id=(msg.get("Message-ID") or f"{frm}{subj}").strip()))

    mailboxes.each(read, timeout=20)
    out.sort(key=lambda x: -x["when"])
    _CACHE.update(at=time.time(), items=out)
    return out

def current(items, now=None):
    """Latest mail per sender; delivered ones only if from the last two days, 'today' only if from today."""
    now = now or time.time()
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    seen, out = set(), []
    for s in items:
        if s["sender"] in seen:
            continue
        seen.add(s["sender"])
        if s["status"] == "delivered" and now - s["when"] > 2 * 86400:
            continue
        if s["status"] == "today" and time.strftime("%Y-%m-%d", time.localtime(s["when"])) != today:
            s = dict(s, status="on the way")
        out.append(s)
    return out


def describe(items):
    if not items:
        return "No deliveries in sight, Sir. At least according to the mailbox."
    groups = {}
    for s in items:
        groups.setdefault(s["status"], []).append(s["sender"])
    parts = []
    for st, label in (("today", "out for delivery today"), ("pickup", "ready for pickup"),
                      ("on the way", "on the way"), ("delivered", "delivered")):
        if groups.get(st):
            names = groups[st][:4]
            parts.append(f"{label}: " + (", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0]))
    return "Parcels, " + "; ".join(parts) + "."


_ASK = re.compile(r"\b(parcels?|packages?|deliver(y|ies)|shipments?)\b")


def handle(n, raw=""):
    if not _ASK.search(n) or re.search(r"\b(order|send|ship|post|install|update)\w*", n):
        return None
    if not re.search(r"\b(coming|come|arrive\w*|where|when|which|what|any|is|are|have|status|on the way|today|"
                     r"expect\w*|delivered|pick\w*)\b", n):
        return None                                 # "package" in another sense: not a delivery question
    try:
        import shipments as tracked
        if tracked.active():
            return tracked.describe()       # shop, carrier, expected day — knows more than the subjects
        items = current(shipments())
    except Exception:  # noqa: BLE001 — mailbox down: the LLM path can still try
        return None
    if re.search(r"\btoday\b", n):
        today = [s for s in items if s["status"] == "today"]
        if not today:
            return "According to the mailbox no parcel is out for delivery today, Sir."
        return describe(today)
    return describe(items)
