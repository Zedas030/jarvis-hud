"""
The mail accounts Jarvis may read — one place for every module that opens a mailbox
(mail_inbox, dashboard, briefing, parcels, shipments, mail_events, drafts).

  .env, first account (as before):  MAIL_IMAP_HOST, MAIL_USER, MAIL_PASSWORD, optional MAIL_NAME
  more accounts:                     MAIL2_IMAP_HOST, MAIL2_USER, MAIL2_PASSWORD, MAIL2_NAME,
                                     MAIL3_... (up to MAIL9_); optional MAILn_IMAP_PORT (993)

  accounts()       -> [{key, name, host, port, user, password}]   only complete ones
  connect(acc)     -> logged-in IMAP4_SSL with INBOX selected read-only (nothing gets marked)
  each(fn)         -> fn(acc, box) for every account; one failing account does not stop
                      the others — its error comes back with the results
  recent(box)      -> ids of the mails from the last LOOKBACK_H hours (JARVIS_MAIL_LOOKBACK_HOURS,
                      default 24) — every module that looks back over a period uses this; it is
                      the upper limit, a caller may only ask for less

Read-only by default. Only mail_draft opens a box writable (APPEND to Drafts) and
mail_mark_read sets flags; both pass readonly=False themselves.
"""
import imaplib
import os
import time

# The user's choice: Jarvis never looks further back in the mail than this.
LOOKBACK_H = max(1, int(os.environ.get("JARVIS_MAIL_LOOKBACK_HOURS", "24")))


def _env(prefix, name, default=""):
    return os.environ.get(f"{prefix}{name}", default).strip()


def accounts():
    out = []
    for n in range(1, 10):
        prefix = "MAIL_" if n == 1 else f"MAIL{n}_"
        host, user, pw = _env(prefix, "IMAP_HOST"), _env(prefix, "USER"), _env(prefix, "PASSWORD")
        if not (host and user and pw):
            continue
        port = int(_env(prefix, "IMAP_PORT", "993") or 993)
        out.append({"key": str(n), "name": _env(prefix, "NAME") or user, "host": host, "port": port,
                    "user": user, "password": pw})
    return out


def configured():
    return bool(accounts())


def connect(acc, readonly=True, timeout=30):
    box = imaplib.IMAP4_SSL(acc["host"], acc["port"], timeout=timeout)
    try:
        box.login(acc["user"], acc["password"])
        box.select("INBOX", readonly=readonly)
    except Exception:
        try:
            box.logout()
        except Exception:  # noqa: BLE001
            pass
        raise
    return box


def each(fn, readonly=True, timeout=30):
    """-> (results, errors): results = [fn(acc, box) per account], errors = ["name: reason"]."""
    results, errors = [], []
    for acc in accounts():
        try:
            box = connect(acc, readonly=readonly, timeout=timeout)
        except Exception as e:  # noqa: BLE001 — wrong password in one account, the others still count
            errors.append(f"{acc['name']}: {str(e)[:80]}")
            continue
        try:
            results.append(fn(acc, box))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{acc['name']}: {str(e)[:80]}")
        finally:
            try:
                box.logout()
            except Exception:  # noqa: BLE001
                pass
    return results, errors


def recent(box, hours=None, criteria=()):
    """Ids of the mails received in the last `hours` (default and maximum LOOKBACK_H), oldest
    first; `criteria` narrow the search further ("UNSEEN"). IMAP SINCE only knows days, so the
    server narrows by date and the arrival time (INTERNALDATE) does the hours."""
    hours = min(float(hours or LOOKBACK_H), LOOKBACK_H)
    cutoff = time.time() - hours * 3600
    _, data = box.search(None, *criteria, "SINCE", time.strftime("%d-%b-%Y", time.localtime(cutoff)))
    ids = data[0].split() if data and data[0] else []
    if not ids:
        return []
    _, rows = box.fetch(b",".join(ids), "(INTERNALDATE)")
    keep = []
    for row in rows or []:
        raw = row[0] if isinstance(row, tuple) else row
        if not isinstance(raw, bytes):
            continue
        when = imaplib.Internaldate2tuple(raw)
        if when and time.mktime(when) >= cutoff:
            keep.append(raw.split()[0])
    return keep


def find(name_or_user):
    """The account a user names ("privat", "gmail", the address), else the first one."""
    want = (name_or_user or "").strip().lower()
    accs = accounts()
    for a in accs:
        if want and (want == a["key"] or want in a["name"].lower() or want in a["user"].lower()):
            return a
    return accs[0] if accs else None
