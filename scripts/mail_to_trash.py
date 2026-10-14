"""Move the given unread mails (matched by sender substring) into the trash folder.
Nothing is deleted permanently: mails are moved to the trash folder ("Papierkorb")."""
import imaplib, os, re, sys
from pathlib import Path

env = Path(__file__).resolve().parent.parent / ".env"
for line in env.read_text(encoding="utf-8").splitlines():
    m = re.match(r"\s*([A-Z_]+)\s*=\s*(.*)", line)
    if m:
        os.environ.setdefault(m.group(1), m.group(2).strip().strip('"').strip("'"))

senders = [s.lower() for s in sys.argv[1:]]
box = imaplib.IMAP4_SSL(os.environ.get("MAIL_IMAP_HOST", "").strip(), 993, timeout=20)
box.login(os.environ["MAIL_USER"].strip(), os.environ["MAIL_PASSWORD"].strip())

trash = None
for raw in box.list()[1]:
    line = raw.decode(errors="replace")
    name = line.rsplit(' "/" ', 1)[-1].strip().strip('"')
    if "\\Trash" in line or name.lower() in ("papierkorb", "trash"):
        trash = name
        break
if not trash:
    sys.exit("Kein Papierkorb-Ordner gefunden.")

box.select("INBOX")
moved = 0
for uid in box.uid("search", None, "UNSEEN")[1][0].split():
    hdr = box.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (FROM)])")[1][0][1].decode(errors="replace").lower()
    if any(s in hdr for s in senders):
        typ, _ = box.uid("MOVE", uid, f'"{trash}"')
        if typ != "OK":
            box.uid("COPY", uid, f'"{trash}"')
            box.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
            box.expunge()
        moved += 1
print(f"{moved} Mails nach '{trash}' verschoben.")
box.logout()
