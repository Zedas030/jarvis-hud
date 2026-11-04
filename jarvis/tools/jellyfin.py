"""
Jellyfin: the media library. "Was habe ich zuletzt geschaut?", "Spiel die Serie auf dem
Fernseher weiter", "Gibt es Neues?" — the server's REST API, the Fire TV through the Jellyfin
app's session on it.

  JELLYFIN_URL       http://10.0.0.110:8096
  JELLYFIN_API_KEY   Dashboard → API-Schlüssel
  JELLYFIN_USER      the user's name (its id is looked up once)
  JELLYFIN_TV        part of the device name that plays on the TV (default "fire")

  resume()          what is half-watched, newest first        tool media_recent (with latest)
  latest()          what came in recently
  search(query)     films, series, episodes by name           tool media_search
  play(item, device)  play one item on a session              tool media_play (LIMITED_WRITE)
  handle(n)         local: "was habe ich zuletzt geschaut", "weiterschauen (auf dem fernseher)"

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
            raise RuntimeError(f"Benutzer {USER} nicht gefunden")
        _UID["id"] = hit["Id"]
    return _UID["id"]


def title(item):
    """'Serie S02E05 Folgentitel' or the film's name with year."""
    if item.get("SeriesName"):
        s, e = item.get("ParentIndexNumber"), item.get("IndexNumber")
        code = f" S{s:02d}E{e:02d}" if isinstance(s, int) and isinstance(e, int) else ""
        return f"{item['SeriesName']}{code} {item.get('Name', '')}".strip()
    year = f" ({item['ProductionYear']})" if item.get("ProductionYear") else ""
    return f"{item.get('Name', '?')}{year}"


def _row(item):
    ud = item.get("UserData") or {}
    pct = ud.get("PlayedPercentage")
    extra = f", {pct:.0f} % gesehen" if isinstance(pct, (int, float)) and 0 < pct < 100 else ""
    when = (ud.get("LastPlayedDate") or "")[:10]
    return f"#{item.get('Id')} {title(item)} [{item.get('Type', '')}{extra}{', zuletzt ' + when if when else ''}]"


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
        names = ", ".join(sorted({s["DeviceName"] for s in rows})) or "keine"
        raise RuntimeError(f"kein Jellyfin-Gerät namens „{device or TV}“ aktiv (offen: {names})")
    return hit


def play(item, device=""):
    """Play by id (#123) or by title on the device's session. Returns German text."""
    if not configured():
        return "Jellyfin ist nicht eingerichtet (JELLYFIN_URL, JELLYFIN_API_KEY, JELLYFIN_USER in .env)."
    try:
        want = str(item or "").strip()
        if re.fullmatch(r"#?[0-9a-f]{8,}", want.lower()):
            iid, name = want.lstrip("#"), want
        else:
            hits = search(want, 1) if want else resume(1)
            if not hits:
                return f"Dazu finde ich nichts in der Bibliothek: „{want}“." if want else "Nichts zum Weiterschauen."
            iid, name = hits[0]["Id"], title(hits[0])
        s = _session_for(device)
        _call("POST", f"/Sessions/{s['Id']}/Playing", playCommand="PlayNow", itemIds=iid)
        return f"Läuft: {name} auf {s['DeviceName']}."
    except Exception as e:  # noqa: BLE001
        return f"Abspielen fehlgeschlagen: {str(e)[:160]}"


def describe_recent():
    if not configured():
        return "Jellyfin ist nicht eingerichtet (JELLYFIN_URL, JELLYFIN_API_KEY, JELLYFIN_USER in .env)."
    try:
        rows = resume()
        new = latest()
        playing = [f"{s['DeviceName']}: {title(s['NowPlayingItem'])}" for s in sessions() if s.get("NowPlayingItem")]
    except Exception as e:  # noqa: BLE001
        return f"Jellyfin nicht erreichbar: {str(e)[:120]}"
    out = []
    if playing:
        out.append("Läuft gerade: " + "; ".join(playing))
    out.append("Angefangen: " + ("\n".join(_row(i) for i in rows) if rows else "nichts"))
    if new:
        out.append("Neu: " + ", ".join(title(i) for i in new))
    return "\n".join(out)


def describe_search(query):
    if not configured():
        return "Jellyfin ist nicht eingerichtet."
    try:
        rows = search(query)
    except Exception as e:  # noqa: BLE001
        return f"Jellyfin nicht erreichbar: {str(e)[:120]}"
    return "\n".join(_row(i) for i in rows) or f"Nichts zu „{query}“ in der Bibliothek."


_RECENT = re.compile(r"\b(was (habe|hab) ich zuletzt (geschaut|gesehen|geguckt)|was läuft (gerade )?(auf|bei) jellyfin|"
                     r"was (gibt es|gibts) neues (auf|bei|in) (jellyfin|der mediathek|meiner bibliothek)|"
                     r"welche (serie|folge) (habe|hab) ich (zuletzt|angefangen))\b")
_RESUME = re.compile(r"\b(weiter ?(schauen|gucken|sehen)|serie weiter|spiel\w* .{0,20}weiter)\b.{0,30}\b(fernseher|fire ?tv|tv)\b|"
                     r"\b(fernseher|fire ?tv|tv)\b.{0,30}\bweiter ?(schauen|gucken|sehen)\b")


def handle(n):
    if not configured():
        return None
    if _RECENT.search(n):
        text = describe_recent()
        return " ".join(text.replace("\n", " ").split())
    if _RESUME.search(n):
        return play("")
    return None
