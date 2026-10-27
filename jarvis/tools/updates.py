"""
Updates for everything on the network Jarvis can reach — checking is free,
installing always needs a confirmation card (policy.py: update_install = CONFIRM).

  check()            one list with an id per pending update:
                       ha:update.<x>      Home Assistant update entities (FRITZ!Box, add-ons,
                                          device firmware HA knows) — only those HA can install
                       nas:dsm            DSM itself (checked; installed via DSM, see below)
                       nas:pkg:<id>       Synology packages with a newer version in the package centre
                       pi:apt             Debian packages on the Raspberry Pi
                       pi:pihole          Pi-hole core/web/FTL
                       pi:docker:<name>   a container on the Pi whose image tag points to a newer
                                          image (Home Assistant with its version numbers) — report only
                       fritz:<host>       FRITZ! devices HA does not know (JARVIS_FRITZ_REPEATERS):
                                          version from the device, latest from AVM's download server
                       fw:<maker-model>   devices HA lists without an update entity (Tapo plugs, …)
                                          whose firmware is older than the same model elsewhere in
                                          the house — report only, the maker's app installs
  install(target)    one id from that list. Long jobs (apt, Pi-hole) run on the Pi in the
                     background; install_status() / check() report how they went.

Not here, on purpose:
  - Fire TV: Amazon offers no way to start a system update from outside; it updates
    itself in standby (apps through the Appstore).
  - DSM itself: the update restarts the NAS and is only offered by the DSM web UI's
    own flow; Jarvis reports it and leaves the click to the user.
  - Docker containers: an image update can change behaviour; they are reported, pulled by hand.
  - FRITZ! repeaters and Tapo/Tuya/Growatt firmware: no local way to install without the
    device password or the maker's cloud; reported only.
Nothing here runs by itself — a background job may only report "updates waiting".
"""
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

import hostos

HA_URL = os.environ.get("HA_URL", "").strip().rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "").strip()
HA_VERSIONS = "https://version.home-assistant.io/stable.json"
# FRITZ! devices without their own HA integration (the box itself comes through HA).
FRITZ_HOSTS = [h.strip() for h in os.environ.get("JARVIS_FRITZ_REPEATERS", "").split(",") if h.strip()]
AVM_DOWNLOAD = "https://download.avm.de"
PI_LOG = "/tmp/jarvis-update.log"
# Fixed commands — the model only picks the target, never what runs.
PI_JOBS = {
    "pi:apt": "sudo DEBIAN_FRONTEND=noninteractive apt-get update -q && "
              "sudo DEBIAN_FRONTEND=noninteractive apt-get -y -q -o Dpkg::Options::=--force-confold upgrade",
    "pi:pihole": "sudo pihole -up",
}
# "[j]arvis_upd_job": the brackets keep pgrep from finding this very check command.
PI_CHECK = (
    "echo APT $(apt list --upgradable 2>/dev/null | tail -n +2 | wc -l); "
    "[ -f /var/run/reboot-required ] && echo REBOOT; "
    "pgrep -f '[j]arvis_upd_job' >/dev/null && echo RUNNING; "
    "command -v pihole >/dev/null && sudo -n pihole -v 2>/dev/null | sed 's/^/PIHOLE /'; "
    f"tail -n 3 {PI_LOG} 2>/dev/null | sed 's/^/LOG /'; "
    # running containers with the digests of the image they were pulled as
    "for c in $(docker ps --format '{{.Names}}' 2>/dev/null); do i=$(docker inspect -f '{{.Config.Image}}' $c); "
    "echo \"DOCKER $c|$i|$(docker image inspect -f '{{join .RepoDigests \",\"}}' $i 2>/dev/null)\"; done; true"
)


# ── Home Assistant ───────────────────────────────────────────
def _ha(path, body=None):
    req = urllib.request.Request(HA_URL + path, data=json.dumps(body).encode() if body is not None else None,
                                 method="POST" if body is not None else "GET",
                                 headers={"Authorization": f"Bearer {HA_TOKEN}", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"null")


def _ha_updates(states=None):
    """-> [(id, name, installed, latest, installable)] for entities reporting an update."""
    if states is None:
        if not (HA_URL and HA_TOKEN):
            return []
        states = _ha("/api/states")
    out = []
    for s in states:
        if not s.get("entity_id", "").startswith("update.") or s.get("state") != "on":
            continue
        a = s.get("attributes") or {}
        out.append(("ha:" + s["entity_id"], a.get("friendly_name") or s["entity_id"], a.get("installed_version"),
                    a.get("latest_version"), bool(int(a.get("supported_features") or 0) & 1)))
    return out


# Every device in HA's registry, read through the template renderer (REST has no registry call).
DEVICES_TEMPLATE = (
    "{%- set ns = namespace(ids=[], out=[]) -%}{%- for s in states -%}{%- set i = device_id(s.entity_id) -%}"
    "{%- if i and i not in ns.ids -%}{%- set ns.ids = ns.ids + [i] -%}"
    "{%- set ns.out = ns.out + [[device_attr(i, 'manufacturer'), device_attr(i, 'model'), "
    "device_attr(i, 'name_by_user') or device_attr(i, 'name'), device_attr(i, 'sw_version')]] -%}"
    "{%- endif -%}{%- endfor -%}{{ ns.out | tojson }}"
)


def _ha_devices():
    if not (HA_URL and HA_TOKEN):
        return []
    req = urllib.request.Request(HA_URL + "/api/template", data=json.dumps({"template": DEVICES_TEMPLATE}).encode(),
                                 headers={"Authorization": f"Bearer {HA_TOKEN}", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def firmware_drift(devices):
    """[(maker, model, name, sw)] -> [(maker, model, newest, [(name, sw)])] for models where some
    devices run older firmware than another one of the same model. HA knows no "latest" for
    these, but the newest one in the house is a floor the others can reach."""
    groups = {}
    for maker, model, name, sw in devices:
        # FRITZ's tracker entries copy whatever the client reported; they are not the device itself.
        if not (maker and model and sw) or "tracked device" in model.lower():
            continue
        groups.setdefault((maker, model), []).append(((name or "").strip(), sw))
    out = []
    for (maker, model), devs in groups.items():
        newest = max((sw for _n, sw in devs), key=_nums)
        behind = sorted((d for d in devs if _newer(newest, d[1])), key=lambda d: _nums(d[1]))
        if behind:
            out.append((maker, model, newest, behind))
    return sorted(out)


def _short(sw):
    return (sw or "").split(" ")[0]        # "1.4.6 Build 260309 Rel.093810" -> "1.4.6"


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower().replace("!", "")).strip("-")


# ── FRITZ! devices outside HA ────────────────────────────────
def parse_boxinfo(xml):
    """jason_boxinfo.xml (readable without login) -> (name, '08.25') or None."""
    name = re.search(r"<j:Name>([^<]+)</j:Name>", xml or "")
    ver = re.search(r"<j:Version>\d+\.(\d+\.\d+)[^<]*</j:Version>", xml or "")
    return (name.group(1).strip(), ver.group(1)) if name and ver else None


def avm_listing_url(name):
    slug = _slug(name)                     # "FRITZ!Repeater 1200 AX" -> "fritzrepeater-1200-ax"
    group = "fritzbox" if slug.startswith("fritzbox") else "fritzwlan"
    return f"{AVM_DOWNLOAD}/{group}/{slug}/deutschland/fritz.os/"


def avm_latest(listing):
    """Newest '08.25' among the .image files of a download.avm.de folder listing."""
    found = re.findall(r'href="[^"]*?-(\d+\.\d+)\.image"', listing or "")
    return max(found, key=_nums) if found else None


def _fritz_rows():
    rows, notes = [], []
    for host in FRITZ_HOSTS:
        try:
            with urllib.request.urlopen(f"http://{host}/jason_boxinfo.xml", timeout=8) as r:
                info = parse_boxinfo(r.read().decode(errors="replace"))
            if not info:
                notes.append(f"{host} nennt keine FRITZ!-Version")
                continue
            with urllib.request.urlopen(avm_listing_url(info[0]), timeout=15) as r:
                latest = avm_latest(r.read().decode(errors="replace"))
            if not latest:
                notes.append(f"AVM nennt keine Version für {info[0]}")
            elif _newer(latest, info[1]):
                rows.append(f"fritz:{host}: {info[0]} {info[1]} → {latest} (nur Anzeige: kommt über das Mesh der "
                            "FRITZ!Box oder in der Oberfläche des Geräts)")
        except Exception as e:  # noqa: BLE001
            notes.append(f"FRITZ!-Gerät {host} nicht lesbar ({e})")
    return rows, notes


# ── Docker images on the Pi ──────────────────────────────────
MANIFESTS = ", ".join(["application/vnd.oci.image.index.v1+json",
                       "application/vnd.docker.distribution.manifest.list.v2+json",
                       "application/vnd.docker.distribution.manifest.v2+json",
                       "application/vnd.oci.image.manifest.v1+json"])


def split_image(image):
    """'ghcr.io/a/b:stable' -> ('ghcr.io', 'a/b', 'stable'); Docker Hub names get their full form."""
    ref, _, tag = image.rpartition(":") if ":" in image.split("/")[-1] else (image, "", "latest")
    first = ref.split("/")[0]
    if "/" in ref and ("." in first or ":" in first or first == "localhost"):
        return first, ref[len(first) + 1:], tag
    return "registry-1.docker.io", ref if "/" in ref else "library/" + ref, tag


def _registry_digest(image):
    """Digest the tag points to now. Anonymous pull token, asked for the way the registry's
    401 says (works for Docker Hub, ghcr.io, lscr.io, quay.io); HEAD does not count as a pull."""
    reg, name, tag = split_image(image)
    url = f"https://{reg}/v2/{name}/manifests/{tag}"
    head = {"Accept": MANIFESTS}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD", headers=head), timeout=10) as r:
            return r.headers.get("Docker-Content-Digest")
    except urllib.error.HTTPError as e:
        if e.code != 401:
            raise
        auth = dict(re.findall(r'(\w+)="([^"]*)"', e.headers.get("WWW-Authenticate", "")))
    q = urllib.parse.urlencode({k: v for k, v in auth.items() if k in ("service", "scope")}
                               or {"scope": f"repository:{name}:pull"})
    with urllib.request.urlopen(f"{auth['realm']}?{q}", timeout=10) as r:
        tok = json.loads(r.read())
    head["Authorization"] = "Bearer " + (tok.get("token") or tok.get("access_token") or "")
    with urllib.request.urlopen(urllib.request.Request(url, method="HEAD", headers=head), timeout=10) as r:
        return r.headers.get("Docker-Content-Digest")


def _ha_core_versions():
    """(running, latest stable) of Home Assistant itself, for a container without supervisor."""
    cur = _ha("/api/config").get("version")
    # The version service answers Python's default user agent with 403.
    with urllib.request.urlopen(urllib.request.Request(HA_VERSIONS, headers={"User-Agent": "jarvis-hud"}),
                                timeout=10) as r:
        return cur, (json.loads(r.read()).get("homeassistant") or {}).get("default")


def docker_rows(containers, digest=None):
    """[(name, image, {digests})] -> rows for containers whose tag now points elsewhere."""
    digest = digest or _registry_digest
    rows, notes = [], []
    for name, image, have in containers:
        if "@" in image:
            continue                       # pinned to a digest: never moves
        try:
            now = digest(image)
        except Exception as e:  # noqa: BLE001
            notes.append(f"Image von {name} nicht prüfbar ({e})")
            continue
        if not now or now in have:
            continue
        what = f"Container {name} ({image}) hat ein neueres Image"
        if "home-assistant" in image:
            try:
                cur, latest = _ha_core_versions()
                if cur and latest:
                    what = f"Home Assistant {cur} → {latest} (Docker-Image {image})"
            except Exception:  # noqa: BLE001
                pass
        rows.append(f"pi:docker:{name}: {what} (nur Anzeige: das Image ziehe ich nicht selbst, "
                    "der Container startet dabei neu)")
    return rows, notes


# ── Synology ─────────────────────────────────────────────────
def _nas_session():
    import jarvis_mcp as j
    if not (j.SYNO_USER and j.SYNO_PASSWORD):
        return None, None
    sid = j._syno({"api": "SYNO.API.Auth", "version": 6, "method": "login", "account": j.SYNO_USER,
                   "passwd": j.SYNO_PASSWORD, "session": "jarvis", "format": "sid"})["sid"]
    return j, sid


def _nas_logout(j, sid):
    try:
        j._syno({"api": "SYNO.API.Auth", "version": 6, "method": "logout", "session": "jarvis"}, sid)
    except Exception:  # noqa: BLE001
        pass


def _nas_packages(j, sid):
    """-> {id: (name, installed, latest, server entry, volume)} for packages with a newer version."""
    inst = j._syno({"api": "SYNO.Core.Package", "version": 2, "method": "list",
                    "additional": json.dumps(["installed_info"])}, sid).get("packages") or []
    srv = j._syno({"api": "SYNO.Core.Package.Server", "version": 2, "method": "list",
                   "blforcereload": "false", "blloadothers": "false"}, sid).get("packages") or []
    latest = {p["id"]: p for p in srv if isinstance(p, dict) and p.get("id")}
    out = {}
    for p in inst:
        s = latest.get(p.get("id"))
        if s and s.get("version") and s["version"] != p.get("version") and _newer(s["version"], p.get("version")):
            path = ((p.get("additional") or {}).get("installed_info") or {}).get("path") or "/volume1/@appstore"
            out[p["id"]] = (p.get("name") or p["id"], p.get("version"), s["version"], s, "/" + path.strip("/").split("/")[0])
    return out


def _nums(v):
    return [int(x) for x in re.findall(r"\d+", v or "")]


def _newer(a, b):
    """'3.1.2-1025' newer than '3.1.1-1024'? Compares the numbers, not the text."""
    return _nums(a) > _nums(b)


# ── Raspberry Pi ─────────────────────────────────────────────
def _pi(cmd, timeout=60):
    import jarvis_mcp as j
    if not j.PI_HOST:
        return None
    out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", f"{j.PI_USER}@{j.PI_HOST}", cmd],
                         capture_output=True, text=True, timeout=timeout,
                         **hostos.QUIET)
    return out


def _pi_state():
    """-> dict(apt=n, reboot=bool, pihole_update=bool|None, running=bool, log=str) or None."""
    out = _pi(PI_CHECK)
    if out is None or (out.returncode != 0 and not out.stdout.strip()):
        return None
    return parse_pi(out.stdout)


def parse_pi(text):
    lines = (text or "").splitlines()
    docker = []
    for l in lines:
        if l.startswith("DOCKER "):
            name, image, digests = (l[7:].split("|") + ["", ""])[:3]
            docker.append((name, image, {d.split("@", 1)[1] for d in digests.split(",") if "@" in d}))
    apt = next((int(m.group(1)) for m in (re.match(r"APT (\d+)", l) for l in lines) if m), 0)
    pihole_lines = [l[7:] for l in lines if l.startswith("PIHOLE ")]
    # "Core version is v6.1 (Latest: v6.2)" — an update only where the two differ.
    pairs = [re.search(r"v?(\d[\w.-]*)\s*\(Latest:\s*v?(\d[\w.-]*)\)", l, re.I) for l in pihole_lines]
    pihole = None if not pihole_lines else any(p and p.group(1) != p.group(2) for p in pairs)
    return {"apt": apt, "reboot": "REBOOT" in lines, "running": "RUNNING" in lines, "pihole_update": pihole,
            "docker": docker, "log": "\n".join(l[4:] for l in lines if l.startswith("LOG "))[-300:]}


# ── public ───────────────────────────────────────────────────
def check():
    rows, notes = [], []
    try:
        for uid, name, cur, new, can in _ha_updates():
            rows.append(f"{uid}: {name} {cur} → {new}" + ("" if can else " (nur Anzeige, Home Assistant kann es nicht installieren)"))
    except Exception as e:  # noqa: BLE001
        notes.append(f"Home Assistant nicht lesbar ({e})")
    try:
        for maker, model, newest, behind in firmware_drift(_ha_devices()):
            names = ", ".join(f"{n} {_short(sw)}" for n, sw in behind)
            rows.append(f"fw:{_slug(maker + ' ' + model)}: {maker} {model} – {len(behind)} mit älterer Firmware als "
                        f"{_short(newest)}: {names} (nur Anzeige: Update in der App des Herstellers)")
    except Exception as e:  # noqa: BLE001
        notes.append(f"Geräteliste von Home Assistant nicht lesbar ({e})")
    fr, fn = _fritz_rows()
    rows += fr
    notes += fn
    try:
        j, sid = _nas_session()
        if j is None:
            notes.append("NAS nicht verbunden")
        else:
            try:
                dsm = (j._syno({"api": "SYNO.Core.Upgrade.Server", "version": 2, "method": "check",
                                "user_reading": "true", "need_auto_smallupdate": "true"}, sid).get("update") or {})
                if dsm.get("available"):
                    rows.append(f"nas:dsm: DSM-Update {dsm.get('version', '')} — installiere ich nicht selbst, "
                                "das NAS startet dabei neu: DSM → Systemsteuerung → Update und Wiederherstellung")
                for pid, (name, cur, new, _s, _v) in _nas_packages(j, sid).items():
                    rows.append(f"nas:pkg:{pid}: NAS-Paket {name} {cur} → {new}")
            finally:
                _nas_logout(j, sid)
    except Exception as e:  # noqa: BLE001
        notes.append(f"NAS nicht lesbar ({e})")
    try:
        pi = _pi_state()
        if pi is None:
            notes.append("Pi nicht erreichbar")
        else:
            if pi["running"]:
                notes.append("Auf dem Pi läuft gerade ein Update")
            if pi["apt"]:
                rows.append(f"pi:apt: Raspberry Pi, {pi['apt']} Pakete")
            if pi["pihole_update"]:
                rows.append("pi:pihole: Pi-hole hat eine neue Version")
            if pi["reboot"]:
                notes.append("Der Pi möchte nach früheren Updates neu starten (mache ich nicht selbst)")
            dr, dn = docker_rows(pi["docker"])
            rows += dr
            notes += dn
    except Exception as e:  # noqa: BLE001
        notes.append(f"Pi nicht lesbar ({e})")
    head = ("Verfügbare Updates (installieren nur mit Karte, Ziel = id vor dem Doppelpunkt):\n" + "\n".join(rows)
            if rows else "Alles aktuell, Sir — keine Updates gefunden.")
    tail = ("\nHinweise: " + "; ".join(notes)) if notes else ""
    return (head + tail + "\nFire TV aktualisiert sich selbst im Standby; das kann ich nicht auslösen.")[:3500]


def install(target):
    target = (target or "").strip()
    if target.startswith("ha:update."):
        entity = target[3:]
        match = [u for u in _ha_updates() if u[0] == target]
        if not match:
            return f"Für {entity} meldet Home Assistant kein Update."
        if not match[0][4]:
            return f"{match[0][1]}: Home Assistant kann dieses Update nicht selbst installieren."
        _ha("/api/services/update/install", {"entity_id": entity})
        return (f"{match[0][1]}: Installation von {match[0][3]} gestartet. Das Gerät kann dabei kurz neu starten — "
                "„Gibt es Updates?“ zeigt danach, ob es durch ist.")
    if target.startswith("nas:pkg:"):
        return _nas_install(target[8:])
    if target == "nas:dsm":
        return "Das DSM-Update installiere ich nicht selbst — das NAS startet dabei neu. Bitte im DSM unter Update starten."
    if target.startswith("pi:docker:"):
        return (f"Container {target[10:]}: das neue Image ziehe ich nicht selbst — es kann das Verhalten ändern. "
                "Auf dem Pi das Image neu ziehen (docker pull) und den Container neu anlegen.")
    if target.startswith("fritz:"):
        return ("Das FRITZ!-Gerät aktualisiere ich nicht selbst. In der FRITZ!Box unter Heimnetz → Mesh das Update "
                "anstoßen oder in der Oberfläche des Geräts unter System → Update.")
    if target.startswith("fw:"):
        return "Diese Firmware kann ich nicht installieren — das geht nur in der App des Herstellers (bei TP-Link: Tapo)."
    if target in PI_JOBS:
        st = _pi_state()
        if st is None:
            return "Pi nicht erreichbar."
        if st["running"]:
            return "Auf dem Pi läuft schon ein Update."
        job = PI_JOBS[target]
        # setsid + nohup: survives the ssh session; the marker in the command line makes pgrep find it.
        out = _pi(f"setsid nohup sh -c ': jarvis_upd_job; ({job}) > {PI_LOG} 2>&1; echo \"fertig, Code $?\" >> {PI_LOG}' "
                  "> /dev/null 2>&1 < /dev/null & echo started", timeout=20)
        if out is None or "started" not in (out.stdout or ""):
            return f"Konnte das Update auf dem Pi nicht starten: {(out.stderr if out else '')[:200]}"
        return ("Läuft auf dem Pi im Hintergrund (" + ("Pakete" if target == "pi:apt" else "Pi-hole") +
                "), dauert ein paar Minuten. Pi-hole und DNS können kurz aussetzen. „Wie läuft das Update?“ zeigt den Stand.")
    return f"Unbekanntes Ziel „{target}“ — erst „Gibt es Updates?“ fragen, dann eine id daraus nehmen."


def _call(j, sid, params):
    """Like jarvis_mcp._syno, but the error is only the DSM code ("120" = invalid parameter),
    so the step it failed at can be named."""
    q = urllib.parse.urlencode(dict(params, _sid=sid))
    with urllib.request.urlopen(f"{j.SYNO_URL}/webapi/entry.cgi?{q}", timeout=60) as r:
        data = json.loads(r.read())
    if not data.get("success"):
        raise RuntimeError(f"DSM-Fehler {(data.get('error') or {}).get('code', '?')}")
    return data.get("data") or {}


def package_steps(pid, s, volume):
    """The package centre's update, as synology-api's easy_install does it (plain values):
    download the .spk, poll its task, ask the download task for the file it wrote, then
    install that file — force replaces the version that is there and starts it again.
    Earlier tries and DSM's answer: everything in one call / JSON-quoted values -> 120
    (invalid parameter) at the download; method "upgrade" with the task id -> 4501."""
    api = {"api": "SYNO.Core.Package.Installation", "version": 1}
    download = dict(api, method="install", operation="install", type=0, blqinst="false", name=pid,
                    url=s.get("link", ""), checksum=s.get("md5", ""), filesize=str(s.get("size", "")))
    status = lambda tid: dict(api, method="status", task_id=tid)  # noqa: E731
    fetched = lambda tid: {"api": "SYNO.Core.Package.Installation.Download", "version": 1,  # noqa: E731
                           "method": "check", "taskid": tid}
    install = lambda path: dict(api, method="install", type=0, volume_path=volume, path=path,  # noqa: E731
                                check_codesign="true", force="true", installrunpackage="true",
                                extra_values=json.dumps({}))
    return download, status, fetched, install


def _nas_install(pid):
    j, sid = _nas_session()
    if j is None:
        return "NAS nicht verbunden."
    step = "Paketliste"
    try:
        pk = _nas_packages(j, sid).get(pid)
        if not pk:
            return f"Für das NAS-Paket {pid} gibt es kein Update."
        name, cur, new, s, volume = pk
        download, status, fetched, install = package_steps(pid, s, volume)
        step = "Herunterladen"
        tid = _call(j, sid, download).get("taskid")
        if not tid:
            return f"NAS-Paket {name}: DSM hat keinen Download-Auftrag zurückgegeben. Im Paketzentrum nachsehen."
        done = False
        for _ in range(60):                       # download: up to ~3 min
            time.sleep(3)
            if _call(j, sid, status(tid)).get("finished"):
                done = True
                break
        if not done:
            return f"NAS-Paket {name}: Download kam nicht zu Ende. Im Paketzentrum nachsehen."
        step = "Download prüfen"
        path = _call(j, sid, fetched(tid)).get("filename")
        if not path:
            return f"NAS-Paket {name}: DSM nennt keine heruntergeladene Datei. Im Paketzentrum nachsehen."
        step = "Installieren"
        _call(j, sid, install(path))
        for _ in range(40):                       # install and start: up to ~2 min
            time.sleep(3)
            now = {p["id"]: p.get("version") for p in j._syno(
                {"api": "SYNO.Core.Package", "version": 2, "method": "list"}, sid).get("packages") or []}
            if now.get(pid) == new:
                return f"NAS-Paket {name}: {cur} → {new} installiert und läuft."
        return (f"NAS-Paket {name}: DSM hat die Installation angenommen, Version {new} meldet sich aber noch nicht. "
                "In ein paar Minuten noch einmal „Gibt es Updates?“ fragen.")
    except Exception as e:  # noqa: BLE001
        # Say plainly whose fault a 120 is — Jarvis once blamed the NAS for our own parameters.
        why = " Das heißt: DSM versteht unseren Aufruf nicht — ein Fehler in Jarvis, nicht am NAS." if "120" in str(e) else ""
        return (f"NAS-Paket {pid}: DSM hat beim Schritt „{step}“ abgelehnt ({e}).{why} "
                "Im Paketzentrum geht es von Hand.")
    finally:
        _nas_logout(j, sid)


def install_status():
    pi = _pi_state()
    if pi is None:
        return "Pi nicht erreichbar."
    if pi["running"]:
        return "Das Update auf dem Pi läuft noch. Letzte Zeilen:\n" + pi["log"]
    return ("Auf dem Pi läuft kein Update. Letzte Zeilen des letzten Laufs:\n" + (pi["log"] or "—")
            + ("\nDer Pi möchte neu starten." if pi["reboot"] else ""))
