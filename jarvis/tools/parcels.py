"""
Parcels — read from the shipping mails in the inbox, no tracking API, 0 tokens.

    "Kommt heute ein Paket?", "Wo ist mein Paket?", "Welche Lieferungen sind unterwegs?"

Looks at the mail headers (sender, subject, date) of the last 24 hours
(mailboxes.LOOKBACK_H) for the usual carriers and shops and sorts them into: heute
in Zustellung, abholbereit, unterwegs, zugestellt. Read-only (IMAP readonly +
BODY.PEEK), nothing is marked.
The watcher announces "heute in Zustellung" once per mail.
"""
import email
import email.header
import email.utils
import imaplib
import re
import time

CARRIERS = r"dhl|deutsche post|hermes|dpd|gls|ups|fedex|amazon|go express|trans-o-flex"
STATUS = [
    ("heute", r"heute|today|in zustellung|zustellung erfolgt heute|out for delivery|wird heute|kommt heute|auf dem weg zu (ihnen|dir)"),
    ("abholbereit", r"abhol|packstation|filiale|paketshop|paket-?shop|parcel ?shop|liegt bereit|pick ?up"),
    ("zugestellt", r"zugestellt|delivered|wurde geliefert|ist angekommen|abgegeben"),
    ("unterwegs", r"versandt|versendet|verschickt|unterwegs|shipped|dispatched|auf dem weg|sendung|lieferung|paket"),
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
    """Latest mail per sender; delivered ones only if from the last two days, 'heute' only if from today."""
    now = now or time.time()
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    seen, out = set(), []
    for s in items:
        if s["sender"] in seen:
            continue
        seen.add(s["sender"])
        if s["status"] == "zugestellt" and now - s["when"] > 2 * 86400:
            continue
        if s["status"] == "heute" and time.strftime("%Y-%m-%d", time.localtime(s["when"])) != today:
            s = dict(s, status="unterwegs")
        out.append(s)
    return out


def describe(items):
    if not items:
        return "Keine Lieferungen in Sicht, Sir. Zumindest laut Postfach."
    groups = {}
    for s in items:
        groups.setdefault(s["status"], []).append(s["sender"])
    parts = []
    for st, label in (("heute", "heute in Zustellung"), ("abholbereit", "abholbereit"),
                      ("unterwegs", "unterwegs"), ("zugestellt", "zugestellt")):
        if groups.get(st):
            names = groups[st][:4]
            parts.append(f"{label}: " + (", ".join(names[:-1]) + " und " + names[-1] if len(names) > 1 else names[0]))
    return "Pakete, " + "; ".join(parts) + "."


_ASK = re.compile(r"\b(paket\w*|lieferung\w*|sendung\w*|päckchen)\b")


def handle(n, raw=""):
    if not _ASK.search(n) or re.search(r"\b(bestell|schick|versend|verschick|aufgeb|install|update)\w*", n):
        return None
    if not re.search(r"\b(kommt|kommen|wo|wann|welche\w*|gibt es|ist|sind|hab\w*|status|unterwegs|heute|"
                     r"erwarte\w*|zugestellt|abhol\w*)\b", n):
        return None                                 # "Paket" in another sense: not a delivery question
    try:
        import shipments as tracked
        if tracked.active():
            return tracked.describe()       # shop, carrier, expected day — knows more than the subjects
        items = current(shipments())
    except Exception:  # noqa: BLE001 — mailbox down: the LLM path can still try
        return None
    if re.search(r"\bheute\b", n):
        today = [s for s in items if s["status"] == "heute"]
        if not today:
            return "Heute ist laut Postfach kein Paket in Zustellung, Sir."
        return describe(today)
    return describe(items)
