"""
Jellyfin: the media library. "What did I watch last?", "Continue watching the series on the
TV", "Is there anything new?" — the server's REST API, the Fire TV through the Jellyfin
app's session on it.

  JELLYFIN_URL       http://10.0.0.110:8096
  JELLYFIN_API_KEY   Dashboard → API Keys
  JELLYFIN_USER      the user's name (its id is looked up once)
  JELLYFIN_TV        part of the device name that plays on the TV (default "fire")

  resume()          what is half-watched, newest first        tool media_recent (with latest)
  latest()          what came in recently
  search(query)     films, series, episodes by name           tool media_search
  play(item, device)  play one item on a session              tool media_play (LIMITED_WRITE)
  handle(n)         local: "what did I watch last", "continue watching (on the TV)"

Titles are the library's own data — the tool results are untrusted text for the model.
"""
import json
import os
import re
import urllib.parse
import urllib.request

URL = os.environ.get("JELLYFIN_URL", "").strip().rstrip("/")
API_KEY = os.environ.get("JELLYFIN_API_KEY", "").strip()
USER = os.environ.get("JELLYFIN_USER", "").strip()
TV = os.environ.get("JELLYFIN_TV", "fire").strip().lower()
TIMEOUT = 10
_UID = {"id": ""}


def configured():
    return bool(URL and API_KEY and USER)


def _call(method, path, body=None, **params):
    q = ("?" + urllib.parse.urlencode(params)) if params else ""
    req = urllib.request.Request(URL + path + q, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Emby-Token": API_KEY, "accept": "application/json",
                                          "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
        return json.loads(raw) if raw.strip() else None


def _uid():
    if not _UID["id"]:
        users = _call("GET", "/Users") or []
        hit = next((u for u in users if (u.get("Name") or "").lower() == USER.lower()), None)
        if not hit:
            raise RuntimeError(f"user {USER} not found")
        _UID["id"] = hit["Id"]
    return _UID["id"]


def title(item):
    """'Series S02E05 episode title' or the film's name with year."""
    if item.get("SeriesName"):
        s, e = item.get("ParentIndexNumber"), item.get("IndexNumber")
        code = f" S{s:02d}E{e:02d}" if isinstance(s, int) and isinstance(e, int) else ""
        return f"{item['SeriesName']}{code} {item.get('Name', '')}".strip()
    year = f" ({item['ProductionYear']})" if item.get("ProductionYear") else ""
    return f"{item.get('Name', '?')}{year}"


def _row(item):
    ud = item.get("UserData") or {}
    pct = ud.get("PlayedPercentage")
    extra = f", {pct:.0f} % watched" if isinstance(pct, (int, float)) and 0 < pct < 100 else ""
    when = (ud.get("LastPlayedDate") or "")[:10]
    return f"#{item.get('Id')} {title(item)} [{item.get('Type', '')}{extra}{', last ' + when if when else ''}]"


def resume(limit=6):
    return _call("GET", f"/Users/{_uid()}/Items/Resume", Limit=limit, MediaTypes="Video",
                 Fields="ProductionYear").get("Items") or []


def latest(limit=6):
    return _call("GET", f"/Users/{_uid()}/Items/Latest", Limit=limit, IncludeItemTypes="Movie,Series,Episode",
                 Fields="ProductionYear") or []


def search(query, limit=8):
    q = " ".join((query or "").split())
    if not q:
        return []
    return _call("GET", f"/Users/{_uid()}/Items", searchTerm=q, Recursive="true", Limit=limit,
                 IncludeItemTypes="Movie,Series,Episode", Fields="ProductionYear").get("Items") or []


def sessions():
    return [s for s in (_call("GET", "/Sessions") or []) if s.get("DeviceName")]


def _session_for(device):
    want = (device or TV).lower()
    rows = sessions()
    hit = next((s for s in rows if want in (s.get("DeviceName") or "").lower() or want in (s.get("Client") or "").lower()), None)
    if not hit:
        names = ", ".join(sorted({s["DeviceName"] for s in rows})) or "none"
        raise RuntimeError(f"no Jellyfin device called “{device or TV}” active (open: {names})")
    return hit


def play(item, device=""):
    """Play by id (#123) or by title on the device's session. Returns spoken text."""
    if not configured():
        return "Jellyfin is not set up (JELLYFIN_URL, JELLYFIN_API_KEY, JELLYFIN_USER in .env)."
    try:
        want = str(item or "").strip()
        if re.fullmatch(r"#?[0-9a-f]{8,}", want.lower()):
            iid, name = want.lstrip("#"), want
        else:
            hits = search(want, 1) if want else resume(1)
            if not hits:
                return f"I find nothing for that in the library: “{want}”." if want else "Nothing to continue watching."
            iid, name = hits[0]["Id"], title(hits[0])
        s = _session_for(device)
        _call("POST", f"/Sessions/{s['Id']}/Playing", playCommand="PlayNow", itemIds=iid)
        return f"Playing: {name} on {s['DeviceName']}."
    except Exception as e:  # noqa: BLE001
        return f"Playback failed: {str(e)[:160]}"


def describe_recent():
    if not configured():
        return "Jellyfin is not set up (JELLYFIN_URL, JELLYFIN_API_KEY, JELLYFIN_USER in .env)."
    try:
        rows = resume()
        new = latest()
        playing = [f"{s['DeviceName']}: {title(s['NowPlayingItem'])}" for s in sessions() if s.get("NowPlayingItem")]
    except Exception as e:  # noqa: BLE001
        return f"Jellyfin not reachable: {str(e)[:120]}"
    out = []
    if playing:
        out.append("Playing right now: " + "; ".join(playing))
    out.append("Started: " + ("\n".join(_row(i) for i in rows) if rows else "nothing"))
    if new:
        out.append("New: " + ", ".join(title(i) for i in new))
    return "\n".join(out)


def describe_search(query):
    if not configured():
        return "Jellyfin is not set up."
    try:
        rows = search(query)
    except Exception as e:  # noqa: BLE001
        return f"Jellyfin not reachable: {str(e)[:120]}"
    return "\n".join(_row(i) for i in rows) or f"Nothing for “{query}” in the library."


_RECENT = re.compile(r"\b(what did i (last )?(watch|see)( last)?|what (have i|did i) been watching|whats (playing|on) (on|in) jellyfin|"
                     r"(whats|what is) new (on|in) (jellyfin|the media library|my library)|"
                     r"which (series|show|episode) did i (watch last|start))\b")
_RESUME = re.compile(r"\b((keep|continue|resume) watching|carry on watching)\b.{0,40}\b(television|fire ?tv|tv)\b|"
                     r"\b(television|fire ?tv|tv)\b.{0,30}\b(keep|continue|resume) watching\b")


def handle(n):
    if not configured():
        return None
    if _RECENT.search(n):
        text = describe_recent()
        return " ".join(text.replace("\n", " ").split())
    if _RESUME.search(n):
        return play("")
    return None
