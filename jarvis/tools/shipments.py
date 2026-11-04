"""
Shipment tracking — orders on their way, from the shipping mails of every account,
with the expected day in the calendar and a reminder on that day.

  scan()          shipping mails of the last 24 hours (mailboxes.LOOKBACK_H, all accounts): shop, order, carrier,
                  tracking number, expected day if the mail names one, status
  refresh()       live status and expected day, at most every REFRESH_S per shipment:
                  17TRACK (TRACK17_API_KEY) for every carrier — DHL, Hermes, DPD, GLS, UPS ... —
                  or DHL's own API (DHL_API_KEY) for DHL only. Without a key: the mails alone.
                  Only the tracking number leaves the house; 17TRACK then knows it.
  sync()          calendar: one all-day entry "📦 Shop · Carrier" in ICLOUD_DEFAULT_CALENDAR;
                  it moves when the day changes and becomes "✓ delivered: …" once delivered.
                  Announced; the day itself gets a reminder in the morning.
  describe()      what is on its way, for "Where is my parcel?" and the shipments_status tool
  undo(hours)     takes the automatic entries out again (with mail_events.undo: "undo the appointments")

Part of the one exception to "background jobs never change anything" (CLAUDE.md), chosen
by the user for deliveries: fixed code, no model, no links, every entry announced and
undoable. Same-day services (food, groceries) are no shipments and stay out, as does work.

Carrier and shop mails arrive in German or English, so the parsing patterns below know both.
"""
import datetime as dt
import html
import json
import os
import pathlib
import re
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
PATH = pathlib.Path(os.environ.get("JARVIS_SHIPMENTS_FILE", ROOT / "logs" / "shipments.json"))
ENABLED = os.environ.get("JARVIS_SHIPMENTS", "1").strip().lower() not in ("0", "off", "false", "no")
DHL_KEY = os.environ.get("DHL_API_KEY", "").strip()
TRACK17_KEY = os.environ.get("TRACK17_API_KEY", "").strip()
TRACK17_URL = os.environ.get("TRACK17_URL", "https://api.17track.net/track/v2.2").strip().rstrip("/")
REFRESH_S = 2 * 3600          # both free tiers are small; a parcel does not change every minute
KEEP_DAYS = 3                 # a delivered one stays this long, then it is forgotten
HOURS = (7, 22)               # no entries, no announcements outside these hours
# Same-day services and grocery runs: an order, but nothing to wait for in the calendar.
IGNORE = tuple(d.strip().lower() for d in os.environ.get(
    "JARVIS_SHIPMENTS_IGNORE",
    "uber.com,ubereats.com,goflink.com,wolt.com,lieferando.de,deliveroo.de,getir.com,gorillas.io,knuspr.de,"
    "picnic.app,rewe.de,flaschenpost.de").split(",") if d.strip())
CARRIERS = [("DHL", r"\bdhl\b|deutsche post"), ("Hermes", r"\bhermes\b"), ("DPD", r"\bdpd\b"), ("GLS", r"\bgls\b"),
            ("UPS", r"\bups\b"), ("FedEx", r"\bfedex\b"), ("Amazon", r"\bamazon\b"), ("GO!", r"\bgo!? express\b")]
# "Bestellt:" (ordered) too — Amazon names the delivery day in the order confirmation, not later
SHIP_SUBJECT = re.compile(r"versand|versendet|verschickt|unterwegs|auf dem weg|sendung|zugestellt|zustellung|"
                          r"geliefert|lieferung|paket|abhol|bestellt|shipped|dispatched|out for delivery|delivered|"
                          r"tracking|on its way|ordered|shipment|parcel|package|delivery|arriving", re.I)
TRACKING = [re.compile(r"(?:tracking|sendungs|paket|parcel|package)[ -]?(?:nummer|nr\.?|number|id|code)\s*[:#]?\s*([A-Z0-9]{8,35})\b", re.I),
            re.compile(r"\b(00340\d{15})\b"), re.compile(r"\b(JJD\d{16,22})\b"), re.compile(r"\b(1Z[0-9A-Z]{16})\b")]
# an order number holds a digit — "Bestellung ansehen" (view order) is a button, not a number
ORDER = re.compile(r"(?:Bestell(?:ung|nummer|nr\.?)\s*(?:nr\.?|nummer)?|\border\s*(?:no\.?|number|nr\.?)?)"
                   r"\s*[:#]?\s*#?\s*((?=[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9-]{3,})", re.I)
AMAZON_ORDER = re.compile(r"\b(\d{3}-\d{7}-\d{7})\b")
ITEM = re.compile(r"[„\"]([^“\"]{3,60}?)(?:\.\.\.|…)?[“\"]")
# ── when it comes: the carriers' own notices (DHL, DPD Predict, Hermes, GLS, UPS My Choice) ──
# A date only counts with a delivery word close by — "your order of 20.10." is no delivery day.
DAY_HINT = re.compile(r"voraussichtlich|erwartet|zustell|zugestellt|liefer|ankunft|ankommen|kommt|bringen|eintreff|"
                      r"trifft|delivery|deliver|arriv|expected|estimated|scheduled|zeitfenster", re.I)
MONTHS = {"januar": 1, "jan": 1, "january": 1, "februar": 2, "feb": 2, "february": 2, "märz": 3, "maerz": 3,
          "mär": 3, "march": 3, "mar": 3, "april": 4, "apr": 4, "mai": 5, "may": 5, "juni": 6, "jun": 6, "june": 6,
          "juli": 7, "jul": 7, "july": 7, "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9,
          "oktober": 10, "okt": 10, "october": 10, "oct": 10, "november": 11, "nov": 11, "dezember": 12, "dez": 12,
          "december": 12, "dec": 12}
WEEKDAYS = {"montag": 0, "dienstag": 1, "mittwoch": 2, "donnerstag": 3, "freitag": 4, "samstag": 5, "sonnabend": 5,
            "sonntag": 6, "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5,
            "sunday": 6}
_MON = "|".join(sorted(MONTHS, key=len, reverse=True))
_WDS = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
_NUM = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2}(?!\d))?")
_NAMED = re.compile(r"(?<!\d)(\d{1,2})\.?\s+(" + _MON + r")\.?(?:\s+(\d{4}))?\b", re.I)
_NAMED_EN = re.compile(r"\b(" + _MON + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+(\d{4}))?", re.I)
_REL = re.compile(r"(?<!guten\s)\b(übermorgen|heute|morgen|today|tomorrow)\b", re.I)
_WEEKDAY = re.compile(r"(?:\b(?:am|on|this|kommenden?|nächsten?)\s+|(?:zustellung|lieferung|ankunft|arriving|delivery|arrival)\s*:\s*)"
                      r"(" + _WDS + r")\b", re.I)
WINDOW = re.compile(r"(?:zwischen\s+)?(\d{1,2})(?:[:.](\d{2}))?\s*(?:uhr\s*)?(?:und|bis|-|–|and|to)\s*"
                    r"(\d{1,2})(?:[:.](\d{2}))?\s*(?:uhr|h\b)"
                    # Amazon writes "today 14:15 - 17:15" without "Uhr" (o'clock): both times with minutes then
                    r"|(\d{1,2}):(\d{2})\s*(?:[-–]|and|to|und|bis)\s*(\d{1,2}):(\d{2})", re.I)
CARRIER_DOMAINS = ("dhl.de", "dhl.com", "deutschepost.de", "dpd.de", "dpd.com", "hermesworld.com", "myhermes.de",
                   "hermes-europe.de", "gls-pakete.de", "gls-group.eu", "gls-group.com", "ups.com")
SHOP_IN_TEXT = re.compile(r"(?i:paket|sendung|bestellung|lieferung|parcel|package|order|delivery|shipment)\s+(?:von|from)\s+"
                          r"([A-ZÄÖÜ0-9][\w&.'\- ]{1,30}?)"
                          r"(?=\s+(?:kommt|ist|wird|wurde|hat|befindet|trifft|arrives|is|will|was|has)\b|[.,!:;]|$)")


def _near_hint(text, a, b):
    return bool(DAY_HINT.search(text[max(0, a - 80):b + 40]))


def _year(month, day, year, sent):
    if year:
        y = int(year)
        return y + 2000 if y < 100 else y
    y = sent.year
    try:
        if dt.date(y, month, day) < sent.date() - dt.timedelta(days=30):
            y += 1                        # "on 02.01." written in December
    except ValueError:
        pass
    return y


def expected_day(text, sent):
    """The delivery day a mail names, else None. sent: when the mail came — "tomorrow" is
    counted from there, not from today. Explicit dates beat "tomorrow", which beats "on Monday"."""
    found = []
    for m in _NUM.finditer(text):
        if _near_hint(text, m.start(), m.end()):
            d, mo = int(m.group(1)), int(m.group(2))
            found.append((0, m.start(), (_year(mo, d, m.group(3), sent), mo, d)))
    for m in _NAMED.finditer(text):
        if _near_hint(text, m.start(), m.end()):
            d, mo = int(m.group(1)), MONTHS[m.group(2).lower()]
            found.append((0, m.start(), (_year(mo, d, m.group(3), sent), mo, d)))
    for m in _NAMED_EN.finditer(text):
        if _near_hint(text, m.start(), m.end()):
            mo, d = MONTHS[m.group(1).lower()], int(m.group(2))
            found.append((0, m.start(), (_year(mo, d, m.group(3), sent), mo, d)))
    for m in _REL.finditer(text):
        if _near_hint(text, m.start(), m.end()):
            ahead = {"heute": 0, "today": 0, "morgen": 1, "tomorrow": 1, "übermorgen": 2}[m.group(1).lower()]
            day = sent.date() + dt.timedelta(days=ahead)
            found.append((1, m.start(), (day.year, day.month, day.day)))
    for m in _WEEKDAY.finditer(text):
        if _near_hint(text, m.start(), m.end()):
            ahead = (WEEKDAYS[m.group(1).lower()] - sent.weekday()) % 7
            day = sent.date() + dt.timedelta(days=ahead)
            found.append((2, m.start(), (day.year, day.month, day.day)))
    for _, _, (y, mo, d) in sorted(found):
        try:
            return dt.date(y, mo, d)
        except ValueError:
            continue
    return None


def time_window(text):
    """"between 10:15 and 11:15" -> "10:15–11:15", else ""."""
    for m in WINDOW.finditer(text):
        g = m.groups()
        h1, m1, h2, m2 = (g[0], g[1], g[2], g[3]) if g[0] else (g[4], g[5], g[6], g[7])
        h1, m1, h2, m2 = int(h1), int(m1 or 0), int(h2), int(m2 or 0)
        if h1 < 24 and h2 < 24 and m1 < 60 and m2 < 60 and (h1, m1) < (h2, m2) and _near_hint(text, m.start(), m.end()):
            return f"{h1:02d}:{m1:02d}–{h2:02d}:{m2:02d}"
    return ""


def from_carrier(addr):
    dom = (addr or "").rsplit("@", 1)[-1].lower()
    return any(dom == d or dom.endswith("." + d) for d in CARRIER_DOMAINS)
# "will be delivered tomorrow" is on its way, not delivered: only unambiguous wording counts
STATUS = [("delivered", r"wurde zugestellt|ist zugestellt|erfolgreich zugestellt|zugestellt am|^zugestellt|"
                         r"^geliefert|wurde geliefert|ist angekommen|^delivered\b|(?:has been|was|successfully) delivered"),
          # an attempted delivery: nobody was home — worth saying, not "on the way"
          ("attempt", r"versuchte zustellung|zustellversuch|zustellung wurde versucht|nicht zugestellt werden|"
                             r"nicht angetroffen|delivery attempt|tried to deliver|missed (?:your )?delivery"),
          # a Packstation as the delivery address is no "ready for pickup"
          ("pickup", r"abholbereit|zur abholung|kann .{0,25}abgeholt werden|liegt .{0,40}bereit|"
                          r"ready for pick ?up|ready for collection|can be collected|available for pick ?up"),
          ("today", r"heute in zustellung|^in zustellung|kommt heute|wird heute|zustellung heute|out for delivery|"
                    r"zustellung erfolgt heute|arriving today|arrives today|will arrive today"),
          ("on the way", r".")]
DHL_STATUS = {"delivered": "delivered", "transit": "on the way", "pre-transit": "announced", "failure": "problem"}


# ── reading mail ─────────────────────────────────────────────
def _dec(v):
    import email.header
    try:
        return str(email.header.make_header(email.header.decode_header(v or ""))).strip()
    except Exception:  # noqa: BLE001
        return (v or "").strip()


def _text(msg):
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


def ignored(addr):
    dom = (addr or "").rsplit("@", 1)[-1].lower()
    return any(dom == d or dom.endswith("." + d) for d in IGNORE)


def _mails(hours=None):
    """Shipping mails of the last `hours` (at most mailboxes.LOOKBACK_H). Shipments already known
    stay in the state file, so a short look back loses none of them."""
    import email
    import email.utils
    import mailboxes

    def read(acc, box):
        ids = mailboxes.recent(box, hours)
        if not ids:
            return []
        _, rows = box.fetch(b",".join(ids), "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
        wanted = []
        for row in rows:
            if not isinstance(row, tuple):
                continue
            head = email.message_from_bytes(row[1])
            subj, addr = _dec(head.get("Subject")), email.utils.parseaddr(head.get("From") or "")[1]
            if SHIP_SUBJECT.search(subj) and not ignored(addr) and not subj.lstrip().startswith("[TEST]"):
                wanted.append(row[0].split()[0])
        out = []
        for i in wanted:
            _, parts = box.fetch(i, "(BODY.PEEK[])")
            msg = email.message_from_bytes(parts[0][1])
            name, addr = email.utils.parseaddr(_dec(msg.get("From")))
            sent = email.utils.parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None
            out.append({"shop": name or addr.split("@")[-1], "addr": addr.lower(), "subject": _dec(msg.get("Subject")),
                        "date": sent.replace(tzinfo=None) if sent else dt.datetime.now(), "text": _text(msg),
                        "account": acc["name"]})
        return out

    results, errors = mailboxes.each(read)
    if not results and errors:
        raise RuntimeError("; ".join(errors))
    return [m for r in results for m in r]


# ── understanding ────────────────────────────────────────────
def parse(mail):
    """One shipping mail -> {key, shop, carrier, tracking, order, eta, status, ...} or None
    (promotion, no shipment). A shipment needs a tracking number or a named carrier."""
    import privacy
    text, subj = mail["text"], mail["subject"]
    both = subj + " " + text
    if privacy.is_work(subj, mail["shop"]):
        return None
    # the carrier from the text, or from the sender's address (noreply@dhl.de says "DHL" by itself)
    carrier = next((name for name, pat in CARRIERS if re.search(pat, both + " " + mail.get("addr", "").replace(".", " "),
                                                                re.I)), "")
    tracking = next((m.group(1) for rx in TRACKING for m in [rx.search(both)] if m and re.search(r"\d{6}", m.group(1))), "")
    if not (tracking or carrier):
        return None                      # "the success from the show" (Sendung) — a newsletter, no parcel
    amazon = "amazon" in (mail.get("addr", "") + " " + mail["shop"]).lower()
    if amazon and not tracking and not re.search(r"zustellung|versendet|geliefert|lieferung|unterwegs|deliver|"
                                                 r"dispatched|shipped|arriving|on its way", both, re.I):
        return None                      # Prime Video, an app, a password mail: nothing gets delivered
    order = AMAZON_ORDER.search(both) if amazon else None
    order = order or ORDER.search(both)
    status = next(name for name, pat in STATUS if re.search(pat, subj, re.I) or
                  (name != "on the way" and re.search(pat, text[:400], re.I)))
    eta = mail["date"].date() if status == "today" else expected_day(both, mail["date"])
    window = time_window(both)
    carrier_mail = from_carrier(mail.get("addr", ""))
    if carrier_mail:
        # the carrier's notice names the shop in the text ("your parcel from Fairway arrives tomorrow")
        named = SHOP_IN_TEXT.search(both)
        shop = named.group(1).strip() if named else ""
    else:
        shop = re.sub(r"\s*(?:team|shop|store|kundenservice|service|no-?reply|newsletter)\b.*$", "", mail["shop"],
                      flags=re.I).strip() or mail["shop"]
    item = ITEM.search(subj)
    if amazon:
        # each Amazon mail names the item; that is what one recognises in the calendar
        shop = "Amazon" + (f": {item.group(1).strip()}" if item else "")
        carrier = carrier if carrier != "Amazon" else "Amazon"
    key = tracking or f"{shop or carrier}|{order.group(1) if order else mail['subject'][:40]}"
    if amazon and not tracking:
        key = f"amazon|{order.group(1) if order else (item.group(1).strip() if item else subj[:40])}"
    return {"key": key, "shop": shop or f"Parcel ({carrier})", "carrier": carrier, "tracking": tracking,
            "order": order.group(1) if order else "", "eta": eta, "window": window, "status": status,
            "seen": mail["date"], "subject": subj[:90], "account": mail.get("account", ""),
            "from_carrier": carrier_mail, "shop_named": bool(shop)}


def merge(items, found):
    """Fold freshly parsed mails into the stored shipments: newer mails win for status, a
    named day beats none, a delivered one stays delivered."""
    for f in sorted(found, key=lambda x: x["seen"]):
        it = items.setdefault(f["key"], {"key": f["key"], "created": time.time()})
        for k in ("carrier", "tracking", "order", "account"):
            if f.get(k):
                it[k] = f[k]
        # The shop's own mail names the shop; a carrier's notice ("DHL Paket") must not rename it.
        if not it.get("shop") or (f.get("shop_named") and not (f.get("from_carrier") and it.get("shop_named"))):
            it["shop"], it["shop_named"] = f["shop"], f.get("shop_named", False)
        if not f.get("from_carrier") or not it.get("subject"):
            it["subject"] = f["subject"]
        if f["eta"]:
            it["eta"] = f["eta"].isoformat()
            it["window"] = f.get("window") or ""     # a new day without a window drops the old window
        elif f.get("window"):
            it["window"] = f["window"]
        if it.get("status") != "delivered":
            it["status"] = f["status"]
        it["seen"] = f["seen"].isoformat()
    return items


# ── DHL ──────────────────────────────────────────────────────
def dhl(tracking):
    """-> {status, eta (date|None), text} from DHL's tracking API, or None."""
    if not (DHL_KEY and tracking):
        return None
    url = "https://api-eu.dhl.com/track/shipments?" + urllib.parse.urlencode({"trackingNumber": tracking, "language": "en"})
    req = urllib.request.Request(url, headers={"DHL-API-Key": DHL_KEY, "accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"DHL HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:80]}
    s = (data.get("shipments") or [{}])[0]
    st = s.get("status") or {}
    when = s.get("estimatedTimeOfDelivery") or (s.get("estimatedDeliveryTimeFrame") or {}).get("estimatedFrom")
    eta = None
    if when:
        try:
            eta = dt.datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone().date()
        except ValueError:
            eta = None
    code = (st.get("statusCode") or "").lower()
    status = DHL_STATUS.get(code, "on the way")
    if re.search(r"abhol|packstation|filiale|pick ?up", (st.get("description") or "") + " " + (st.get("status") or ""), re.I):
        status = "pickup"
    return {"status": status, "eta": eta, "text": (st.get("description") or st.get("status") or "")[:120]}


# ── 17TRACK: one service for every carrier ───────────────────
# Registering a number uses up quota (once per parcel); asking about it later does not.
T17_STATUS = {"delivered": "delivered", "outfordelivery": "today", "availableforpickup": "pickup",
              "intransit": "on the way", "inforeceived": "announced", "exception": "problem",
              "deliveryfailure": "problem", "notfound": "on the way", "expired": "on the way"}


def _t17(path, body):
    req = urllib.request.Request(f"{TRACK17_URL}/{path}", data=json.dumps(body).encode(), method="POST",
                                 headers={"17token": TRACK17_KEY, "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def track17(tracking, registered=False):
    """-> {status, eta, text, carrier, registered} or {error} or None (no key)."""
    if not (TRACK17_KEY and tracking):
        return None
    try:
        if not registered:
            r = _t17("register", [{"number": tracking}])
            rejected = (r.get("data") or {}).get("rejected") or []
            # "already registered" is fine — that number is ours from an earlier round
            if rejected and not re.search(r"regist", json.dumps(rejected), re.I):
                return {"error": "17TRACK: " + json.dumps(rejected, ensure_ascii=False)[:100]}
        r = _t17("gettrackinfo", [{"number": tracking}])
    except urllib.error.HTTPError as e:
        return {"error": f"17TRACK HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"17TRACK: {str(e)[:80]}"}
    acc = ((r.get("data") or {}).get("accepted") or [{}])[0]
    info = acc.get("track_info") or {}
    latest = info.get("latest_status") or {}
    code = re.sub(r"[^a-z]", "", (latest.get("status") or "").lower())
    status = T17_STATUS.get(code, "on the way")
    eta = None
    est = (info.get("time_metrics") or {}).get("estimated_delivery_date") or {}
    when = est.get("from") or est.get("to")
    if when:
        try:
            eta = dt.datetime.fromisoformat(str(when).replace("Z", "+00:00")).date()
        except ValueError:
            eta = None
    if status == "today":
        eta = dt.date.today()
    event = info.get("latest_event") or {}
    providers = (info.get("tracking") or {}).get("providers") or [{}]
    carrier = ((providers[0] or {}).get("provider") or {}).get("name") or ""
    return {"status": status, "eta": eta, "text": (event.get("description") or latest.get("status") or "")[:120],
            "carrier": carrier, "registered": True}


def refresh(items, now=None):
    """Live status for what is on its way: 17TRACK for every carrier, else DHL's own API."""
    now = now or time.time()
    for it in items.values():
        if not it.get("tracking") or it.get("status") == "delivered":
            continue
        if now - it.get("live_at", 0) < REFRESH_S:
            continue
        if TRACK17_KEY:
            r, src = track17(it["tracking"], registered=it.get("t17_registered", False)), "17TRACK"
        elif it.get("carrier") == "DHL" and DHL_KEY:
            r, src = dhl(it["tracking"]), "DHL"
        else:
            continue
        it["live_at"] = now
        if not r or r.get("error"):
            it["live_error"] = (r or {}).get("error", "")
            continue
        it.pop("live_error", None)
        if r.get("registered"):
            it["t17_registered"] = True
        it["status"], it["live_text"], it["live_src"] = r["status"], r["text"], src
        if r.get("carrier") and not it.get("carrier"):
            it["carrier"] = r["carrier"]
        if r["eta"]:
            it["eta"] = r["eta"].isoformat()
    return items


# ── state ────────────────────────────────────────────────────
def _load():
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"items": {}, "auto": []}


def _save(d):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, PATH)


def _title(it):
    done = it.get("status") == "delivered"
    head = "✓ delivered: " if done else "📦 "
    # "Amazon: BioBizz · Amazon" says it twice: the carrier only when it is not the shop
    carrier = it.get("carrier") if it.get("carrier") and not it["shop"].lower().startswith(it["carrier"].lower()) else ""
    return (f"{head}{it['shop']}" + (f" · {carrier}" if carrier else "")
            + (f" {it['window']}" if it.get("window") and not done else ""))


def _notes(it):
    parts = []
    if it.get("tracking"):
        parts.append(f"Tracking number {it['tracking']}" + (f" ({it['carrier']})" if it.get("carrier") else ""))
    if it.get("order"):
        parts.append(f"Order {it['order']}")
    if it.get("live_text"):
        parts.append(f"{it.get('live_src', 'Status')}: {it['live_text']}")
    parts.append(f"From the mail “{it.get('subject', '')}” — entered by Jarvis.")
    return "\n".join(parts)


def _day(iso):
    d = dt.date.fromisoformat(iso)
    return "Mon Tue Wed Thu Fri Sat Sun".split()[d.weekday()] + f" {d:%b} {d.day}"


# ── the calendar side ────────────────────────────────────────
def _already_there(cal, it, title, eta):
    """The calendar is read before an entry goes in: one that day under a similar title (the
    user's own "Parcel Amazon") means it is there already. Remembered per day, read once."""
    if it.get("cal_there") != eta:
        try:
            if cal.check(title, eta, allday=True)["same"]:
                it["cal_there"] = eta
        except Exception:  # noqa: BLE001 — calendar unreadable: add_event fails the same way and retries
            pass
    return it.get("cal_there") == eta


def sync(d, now=None, say=None):
    """Brings the calendar in line with the shipments; say(text, key) announces."""
    import icloud_calendar as cal
    now = now or dt.datetime.now()
    say = say or (lambda text, key: None)
    if not HOURS[0] <= now.hour < HOURS[1]:
        return d
    # Old entries whose delete failed (iCloud down, timeout): retried every round until gone,
    # so a moved delivery never leaves its original day behind in the calendar.
    left = []
    for url in d.get("orphans", []):
        try:
            cal.delete_event(url)
        except Exception:  # noqa: BLE001
            left.append(url)
    d["orphans"] = left
    for it in list(d["items"].values()):
        eta, title = it.get("eta"), _title(it)
        has = it.get("cal_url")
        if it.get("no_calendar"):
            eta = None                    # the user took its entry out: keep tracking, no new entry
        if it.get("status") == "delivered" and it.get("delivered_at") is None:
            it["delivered_at"] = time.time()
        if not eta:
            continue
        # A day already past gets no new entry: orders from weeks ago whose "delivered" mail lies
        # outside the scanned days looked like they were still on their way.
        if eta < now.date().isoformat() and not has:
            continue
        if has and it.get("cal_date") == eta and it.get("cal_title") == title:
            pass
        elif not has and it.get("status") != "delivered" and _already_there(cal, it, title, eta):
            pass                          # the user's own entry that day: no second one
        else:
            if has:
                try:
                    cal.delete_event(has)     # already gone counts as deleted
                except Exception:  # noqa: BLE001 — still there: keep it to retry, write the new one anyway
                    if has not in d.setdefault("orphans", []):
                        d["orphans"].append(has)
            if it.get("status") == "delivered" and not has:
                continue                  # delivered before it ever got an entry: nothing to tidy up
            try:
                _, url = cal.add_event(title, eta, None, "", _notes(it), allday=True, return_url=True)
            except Exception as e:  # noqa: BLE001
                it["cal_error"] = str(e)[:80]
                continue
            moved = has and it.get("cal_date") != eta
            it.update(cal_url=url, cal_date=eta, cal_title=title)
            d.setdefault("auto", []).append({"key": it["key"], "url": url, "what": f"{_day(eta)} {title}",
                                             "at": time.time()})
            d["auto"] = d["auto"][-40:]
            if not has:
                say(f"Sir, I have entered the delivery from {it['shop']} for {_day(eta)}.", f"ship-new-{it['key']}")
            elif moved:
                say(f"Sir, the delivery from {it['shop']} moves to {_day(eta)}. I have moved the entry.",
                    f"ship-move-{it['key']}-{eta}")
        today = now.date().isoformat()
        if it.get("status") == "attempt" and it.get("attempt_said") != it.get("seen"):
            say(f"Sir, delivery of the parcel from {it['shop']} was attempted, but nobody opened. "
                "Check the mail for where it is now or when the next attempt comes.", f"ship-attempt-{it['key']}")
            it["attempt_said"] = it.get("seen")
        if it.get("status") == "pickup" and it.get("pickup_said") != today:
            say(f"Sir, your parcel from {it['shop']} is ready for pickup.", f"ship-pickup-{it['key']}")
            it["pickup_said"] = today
        elif eta == today and it.get("status") != "delivered" and it.get("reminded") != today:
            window = it.get("window", "")
            when = f" between {window.replace('–', ' and ')}" if window else ""
            say(f"Sir, your parcel from {it['shop']} arrives today" + (f" with {it['carrier']}" if it.get("carrier") else "")
                + when + ".", f"ship-today-{it['key']}-{today}")
            it["reminded"] = today
    # forget what was delivered a while ago (its "✓" entry stays in the calendar), and what was
    # due days ago without ever getting an entry — it arrived, the mail about it just is not here
    cutoff = time.time() - KEEP_DAYS * 86400
    stale = (now.date() - dt.timedelta(days=KEEP_DAYS)).isoformat()
    d["items"] = {k: v for k, v in d["items"].items()
                  if not (v.get("delivered_at") and v["delivered_at"] < cutoff)
                  and not (v.get("eta") and v["eta"] < stale and not v.get("cal_url"))}
    return d


def check(now=None, mails=None):
    """One background round: mails -> state -> DHL -> calendar and announcements."""
    import events
    d = _load()
    found = [p for p in (parse(m) for m in (_mails() if mails is None else mails)) if p]
    merge(d.setdefault("items", {}), found)
    refresh(d["items"])
    sync(d, now=now, say=lambda text, key: events.publish("say", text, key=key))
    _save(d)
    return d


def active(d=None):
    d = d or _load()
    return [it for it in d.get("items", {}).values() if it.get("status") != "delivered"]


def describe(d=None):
    items = active(d)
    if not items:
        return "No shipments on the way, Sir — at least according to the mailbox."
    rows = []
    for it in sorted(items, key=lambda x: x.get("eta") or "9999"):
        when = (f", expected {_day(it['eta'])}" + (f" {it['window']}" if it.get("window") else "")
                if it.get("eta") else ", no delivery day yet")
        how = f" with {it['carrier']}" if it.get("carrier") else ""
        state = {"pickup": ", ready for pickup", "today": ", out for delivery today",
                 "attempt": ", delivery was attempted"}.get(it.get("status"), "")
        rows.append(f"{it['shop']}{how}{when}{state}" + (f" ({it['live_text']})" if it.get("live_text") else ""))
    note = "" if (TRACK17_KEY or DHL_KEY) else (" I can only name delivery days once they are in a mail "
                                                "or TRACK17_API_KEY is set.")
    return "On the way: " + "; ".join(rows) + "." + note


def status_text():
    try:
        return describe(check())
    except Exception as e:  # noqa: BLE001
        return describe() + f" (mailbox not readable right now: {str(e)[:80]})"


def undo(hours=24):
    import icloud_calendar as cal
    d = _load()
    cutoff = time.time() - float(hours or 24) * 3600
    recent = [a for a in d.get("auto", []) if a.get("at", 0) >= cutoff]
    gone = []
    for a in recent:
        try:
            cal.delete_event(a["url"])
            gone.append(a)
        except Exception:  # noqa: BLE001
            pass
    urls = {a["url"] for a in gone}
    d["auto"] = [a for a in d.get("auto", []) if a["url"] not in urls]
    for it in d.get("items", {}).values():
        if it.get("cal_url") in urls:
            # keep tracking, but no entry: a "no thanks" for the calendar, not for the parcel
            it.update(cal_url=None, cal_title=None, cal_date=None, no_calendar=True)
    _save(d)
    return gone


def start():
    if ENABLED:
        import background
        background.loop("shipments", 1800, check)
