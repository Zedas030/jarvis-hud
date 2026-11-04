"""
Paperless-ngx: the scanned post. "What do I pay for the liability insurance?", "When does the
phone contract end?" — full-text search over the archive, read-only, through its REST API.

  PAPERLESS_URL    e.g. http://10.0.0.110:8010
  PAPERLESS_TOKEN  an API token (Paperless → Users → Token)

  search(query)   documents matching the query: title, correspondent, date, tags, type and the
                  search hit's highlight — the tool paperless_search
  read(id)        the text of one document, shortened — the tool paperless_read

Documents are written by other people (insurers, shops): the tool results are untrusted
data for the model (policy.UNTRUSTED), never instructions. Nothing is written to Paperless.
"""
import json
import os
import re
import time
import urllib.parse
import urllib.request

URL = os.environ.get("PAPERLESS_URL", "").strip().rstrip("/")
TOKEN = os.environ.get("PAPERLESS_TOKEN", "").strip()
MAX_HITS = 8
READ_CHARS = 2300
_NAMES = {"at": 0.0, "correspondents": {}, "tags": {}, "types": {}}


def configured():
    return bool(URL and TOKEN)


def _get(path, **params):
    q = ("?" + urllib.parse.urlencode(params)) if params else ""
    req = urllib.request.Request(URL + path + q, headers={"Authorization": f"Token {TOKEN}", "accept": "application/json"})
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read() or b"null")


def _names():
    """id → name of correspondents, tags and document types (cached an hour)."""
    if time.time() - _NAMES["at"] < 3600 and _NAMES["correspondents"]:
        return _NAMES
    for key, path in (("correspondents", "/api/correspondents/"), ("tags", "/api/tags/"), ("types", "/api/document_types/")):
        try:
            rows = _get(path, page_size=500).get("results") or []
            _NAMES[key] = {r["id"]: r.get("name", "?") for r in rows}
        except Exception:  # noqa: BLE001 — names are decoration
            _NAMES[key] = _NAMES.get(key) or {}
    _NAMES["at"] = time.time()
    return _NAMES


def _strip(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html or "").split())


def describe_hit(d, names):
    corr = names["correspondents"].get(d.get("correspondent"), "")
    kind = names["types"].get(d.get("document_type"), "")
    tags = ", ".join(names["tags"].get(t, "") for t in d.get("tags") or [] if names["tags"].get(t))
    created = (d.get("created") or d.get("created_date") or "")[:10]
    hits = d.get("__search_hits__") or {}
    high = _strip(hits.get("highlights") or "")[:160]
    bits = [f"#{d.get('id')} {d.get('title', '?')}", created, corr, kind]
    line = " · ".join(b for b in bits if b)
    if tags:
        line += f" [{tags}]"
    if high:
        line += f" — “{high}”"
    return line


def search(query):
    if not configured():
        return "Paperless is not set up (PAPERLESS_URL and PAPERLESS_TOKEN in .env)."
    q = " ".join((query or "").split())
    if not q:
        return "What should I search the archive for?"
    try:
        data = _get("/api/documents/", query=q, page_size=MAX_HITS, ordering="-created",
                    fields="id,title,created,created_date,correspondent,tags,document_type")
    except Exception as e:  # noqa: BLE001
        return f"Paperless not reachable: {str(e)[:120]}"
    rows = data.get("results") or []
    if not rows:
        return f"The archive has nothing on “{q}”."
    names = _names()
    head = f"{data.get('count', len(rows))} document(s) on “{q}”" + (f", the newest {len(rows)}" if data.get("count", 0) > len(rows) else "")
    return head + ":\n" + "\n".join(describe_hit(d, names) for d in rows) + "\nText of a document: paperless_read with the number."


def read(doc_id):
    if not configured():
        return "Paperless is not set up (PAPERLESS_URL and PAPERLESS_TOKEN in .env)."
    try:
        did = int(str(doc_id).lstrip("#"))
    except (TypeError, ValueError):
        return "Which document number?"
    try:
        d = _get(f"/api/documents/{did}/")
    except Exception as e:  # noqa: BLE001
        return f"Document {did} not readable: {str(e)[:120]}"
    names = _names()
    text = " ".join((d.get("content") or "").split())
    return (describe_hit(d, names) + "\n" + text[:READ_CHARS]
            + (f" … [shortened, {len(text)} characters]" if len(text) > READ_CHARS else ""))
