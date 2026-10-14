"""
JARVIS — a memory HUD driven by Claude Code.

    python3 server.py

The brain is your own `claude` CLI, so this inherits your subscription, your
tools, your MCP servers and your CLAUDE.md. The memory is a folder of markdown
you can open in Obsidian. Nothing is hosted; it binds to localhost only.
"""
import json
import mimetypes
import os
import pathlib
import secrets
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = pathlib.Path(__file__).resolve().parent
UI = ROOT / "ui"

_env = ROOT / ".env"
if _env.exists():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# Supervisor: the HUD runs as a child process. When the code changes the child
# exits with RESTART_CODE once nobody is talking, and is started again here.
RESTART_CODE = 3
if __name__ == "__main__" and os.environ.get("JARVIS_CHILD") != "1":
    import subprocess
    child_env = dict(os.environ, JARVIS_CHILD="1")
    while True:
        try:
            rc = subprocess.call([sys.executable, str(pathlib.Path(__file__).resolve())] + sys.argv[1:],
                                 env=child_env)
        except KeyboardInterrupt:
            sys.exit(0)
        if rc != RESTART_CODE:
            sys.exit(rc)
        print("\n  Code geändert — Neustart mit frischer Sitzung.\n", flush=True)
        child_env["JARVIS_OPEN"] = "0"   # the open page reloads itself

import commands    # noqa: E402
import icloud_calendar  # noqa: E402
import billing  # noqa: E402
import briefing    # noqa: E402
import dashboard   # noqa: E402
import memory      # noqa: E402
import router      # noqa: E402
import runtime     # noqa: E402
import voice       # noqa: E402

PORT = int(os.environ.get("JARVIS_PORT", "8720"))
API_TOKEN = secrets.token_urlsafe(32)
BOOT_ID = secrets.token_hex(4)          # the page reloads itself when this changes
LAST_POST = [time.time()]               # any talking, listening or speaking counts as activity
RUN_LOCK = threading.Lock()
MAX_JSON = 1024 * 1024
MAX_AUDIO = 12 * 1024 * 1024
class _Session:
    """The Claude session id, persisted in state.json so a restart resumes the
    same conversation instead of starting from a blank slate."""

    @property
    def id(self):
        sid = commands.load().get("session_id")
        return sid if runtime.valid_session(sid) else None

    def set(self, sid):
        commands.update_state(lambda d: d.__setitem__("session_id", sid))

    def __getitem__(self, key):
        return self.id

    def __setitem__(self, key, value):
        self.set(value)


SESSION = _Session()

# Every turn re-sends the whole session, so a long one burns the usage limit.
# Start a fresh session once it gets heavy or after a pause; long-term memory
# and the "zuletzt besprochen" excerpt from the diary carry over regardless.
# A fresh session already weighs ~40k (persona, memory, tool definitions).
SESSION_MAX_TOKENS = int(os.environ.get("JARVIS_SESSION_MAX_TOKENS", "80000"))
SESSION_IDLE_MIN = int(os.environ.get("JARVIS_SESSION_IDLE_MIN", "20"))

# ── confirmation gate ──────────────────────────────────────────
# jarvis_mcp.py (Claude's --permission-prompt-tool) POSTs every gated tool call
# here; the request blocks until the user answers on the HUD card or it times out.
PERM_TOKEN = secrets.token_urlsafe(32)
os.environ["JARVIS_PERM_TOKEN"] = PERM_TOKEN
PERM_TIMEOUT = int(os.environ.get("JARVIS_PERM_TIMEOUT", "75"))
PENDING = {}                       # id -> {"event", "allow"}
ACTIVE = {"emit": None}            # emitter of the run currently streaming
EMIT_LOCK = threading.Lock()


def _prewarm_soon():
    """Have a Claude process ready for the next question. If this turn changed
    long-term memory, the old process is replaced now — while JARVIS is still
    talking — instead of on the next question."""
    def work():
        try:
            if runtime.LEAN:   # the most common lean setup: Haiku with memory tools
                runtime.lean_prewarm(router.MODEL_SIMPLE, ["memory"], commands.lean_system(["memory"]))
                return
            sid = SESSION["id"]
            runtime.prewarm(sid, commands.system_block(fresh=sid is None))
        except Exception as e:  # noqa: BLE001
            print(f"  prewarm failed: {e}", flush=True)
    threading.Thread(target=work, daemon=True).start()


def _tts_status():
    if not os.environ.get("TTS_CHAIN", "").strip():
        return None
    import tts_chain
    return tts_chain.status()


# ── usage log ────────────────────────────────────────────────
USAGE_LOG = ROOT / "logs" / "usage.jsonl"


def log_usage(route, model, groups, usage, system_chars=0, prefix_chars=0, message="", reply="", ms=0):
    """One line per request. Token figures come from the API; the split into
    system prompt / context / tool results is from character counts (≈ chars/3)."""
    u = usage or {}
    row = dict(
        at=time.strftime("%Y-%m-%d %H:%M:%S"), route=route, model=model, groups=list(groups or []),
        input=u.get("input_tokens", 0), cache_read=u.get("cache_read", 0), cache_write=u.get("cache_write", 0),
        output=u.get("output_tokens", 0), api_calls=u.get("api_calls", 0),
        total_in=u.get("input_tokens", 0) + u.get("cache_read", 0) + u.get("cache_write", 0),
        est=dict(system=system_chars // 3, context=prefix_chars // 3,
                 tool_results=u.get("tool_result_chars", 0) // 3, reply=len(reply or "") // 3),
        ms=ms, message=(message or "")[:80])
    try:
        USAGE_LOG.parent.mkdir(exist_ok=True)
        with USAGE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return row


BRIEFING_LLM = os.environ.get("JARVIS_BRIEFING_LLM", "1").strip() not in ("0", "false", "no", "off")
BRIEFING_PROMPT = (commands.LEAN_PERSONA + "\n\nFormuliere aus den Fakten ein gesprochenes Tagesbriefing: "
                   "Begrüßung passend zur Tageszeit, dann Termine, Wetter mit Golf-Einschätzung, Speicher und "
                   "Solar, Mails; fast leere Batterien nur wenn gemeldet. Vier bis sechs kurze Sätze. Keine Börse, "
                   "keine Nachrichten. Nichts erfinden, was nicht in den Fakten steht.")


def _describe_call(tool, data):
    if not isinstance(data, dict):
        return str(data)[:400]
    if tool.endswith("calendar_add"):
        parts = [f"Neuer Termin: {data.get('title', '?')}",
                 f"{data.get('start', '?')}" + (f" bis {data['end']}" if data.get("end") else ""),
                 data.get("location") or "", data.get("calendar") or ""]
        return "\n".join(p for p in parts if p)
    # Plain-language "what and why" first, the raw command below — the user has to
    # know what he is approving without reading shell syntax.
    kind = {"Bash": "Befehl ausführen", "PowerShell": "Befehl ausführen", "Write": "Datei schreiben",
            "Edit": "Datei ändern", "Read": "Datei lesen", "Glob": "Dateien suchen",
            "Grep": "Dateien durchsuchen", "WebFetch": "Webseite abrufen"}.get(tool, tool)
    why = data.get("description") or ""
    for key in ("command", "file_path", "path", "url", "pattern"):
        if isinstance(data.get(key), str):
            head = f"{kind}: {why}" if why else kind
            return f"{head}\n\n{data[key]}"[:700]
    return json.dumps(data, ensure_ascii=False)[:600]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if os.environ.get("JARVIS_VERBOSE"):
            sys.stderr.write("  " + (fmt % args) + "\n")

    def _json(self, body, code=200):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _bytes(self, data, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read(self, limit):
        n = int(self.headers.get("Content-Length", 0))
        if n < 0 or n > limit:
            raise ValueError("body too large")
        return self.rfile.read(n) if n else b""

    def _host_ok(self):
        return self.headers.get("Host", "") in {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}

    def _origin_ok(self):
        return self.headers.get("Origin") in {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}

    def _token_ok(self):
        supplied = self.headers.get("X-Jarvis-Token", "")
        return bool(supplied) and secrets.compare_digest(supplied, API_TOKEN)

    def do_GET(self):
        if not self._host_ok():
            return self._json({"error": "invalid host"}, 403)
        p = urlparse(self.path).path

        if p == "/api/status":
            g = memory.build_graph()
            return self._json(dict(
                runtime=runtime.runtime_kind(), version=runtime.version(),
                model=runtime.MODEL or "Claude default", permission=runtime.PERMISSION,
                workdir=runtime.WORKDIR, vault=g["vault"], notes=g["total"],
                links=len(g["links"]), voice=voice.describe(),
                listener=voice.describe_stt(), stt=voice.stt_kind(),
                server_stt=voice.stt_kind() in ("whisper", "fish", "elevenlabs", "local"),
                server_voice=voice.available(), voice_name=voice.short_name(),
                tts=_tts_status(),
                usage=commands.load().get("usage"),
                billing=billing.summary(),
                session=SESSION["id"], boot=BOOT_ID))

        if p == "/api/graph":
            force = parse_qs(urlparse(self.path).query).get("force", ["0"])[0] == "1"
            return self._json(memory.build_graph(force=force))

        if p == "/api/live":
            return self._json(briefing.live())

        if p == "/api/dashboard":
            return self._json(dashboard.data())

        if p == "/api/note":
            title = parse_qs(urlparse(self.path).query).get("id", [""])[0].lower()
            for n in memory.build_graph()["nodes"]:
                if n["id"] == title:
                    return self._json(n)
            return self._json({"error": "not found"}, 404)

        rel = "index.html" if p == "/" else p.lstrip("/")
        f = (UI / rel).resolve()
        if not str(f).startswith(str(UI.resolve())) or not f.is_file():
            return self._bytes(b"not found", "text/plain", 404)
        data = f.read_bytes()
        if rel == "index.html":
            data = data.replace(b"__JARVIS_TOKEN__", API_TOKEN.encode())
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        return self._bytes(data, ctype)

    def do_POST(self):
        p = urlparse(self.path).path
        LAST_POST[0] = time.time()
        if p == "/api/permission/request":
            return self._permission_request()
        if not self._host_ok() or not self._origin_ok():
            return self._json({"error": "origin rejected"}, 403)
        if not self._token_ok():
            return self._json({"error": "unauthorized"}, 401)
        ctype = self.headers.get("Content-Type", "").split(";", 1)[0].lower()
        if p in {"/api/run", "/api/speak", "/api/new", "/api/cancel", "/api/balance"} and ctype != "application/json":
            return self._json({"error": "application/json required"}, 415)
        try:
            raw = self._read(MAX_AUDIO if p == "/api/listen" else MAX_JSON)
        except (TypeError, ValueError):
            return self._json({"error": "body too large"}, 413)

        if p == "/api/speak":
            try:
                text = (json.loads(raw or b"{}").get("text") or "").strip()
                data, mime, provider = voice.speak_full(text)
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Jarvis-Voice", provider)
                self.end_headers()
                self.wfile.write(data)
                return
            except Exception as e:  # noqa: BLE001
                return self._json({"error": str(e)[:200]}, 503)

        if p == "/api/listen":
            try:
                return self._json({"text": voice.transcribe(raw, self.headers.get("Content-Type", "audio/webm"))})
            except Exception as e:  # noqa: BLE001
                return self._json({"error": str(e)[:400], "text": ""}, 503)

        if p == "/api/balance":
            try:
                usd = float(str(json.loads(raw or b"{}").get("usd", "")).replace(",", ".").replace("$", ""))
            except (ValueError, json.JSONDecodeError):
                return self._json({"error": "Betrag ungültig"}, 400)
            return self._json(billing.set_balance(usd))

        if p == "/api/permission/answer":
            try:
                ans = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return self._json({"error": "bad json"}, 400)
            entry = PENDING.get(str(ans.get("id")))
            if not entry:
                return self._json({"error": "no such request (expired?)"}, 404)
            entry["allow"] = ans.get("allow") is True
            entry["event"].set()
            return self._json({"ok": True})

        if p == "/api/new":
            runtime.cancel_active()
            SESSION["id"] = None
            return self._json({"ok": True})

        if p == "/api/cancel":
            # Keep the session: Esc / an interruption must not wipe the conversation.
            stopped = runtime.cancel_active()
            return self._json({"ok": True, "stopped": stopped})

        if p == "/api/run":
            if not RUN_LOCK.acquire(blocking=False):
                return self._json({"error": "JARVIS is already processing a request"}, 409)
            try:
                return self._stream(raw)
            finally:
                RUN_LOCK.release()

        return self._json({"error": "no such endpoint"}, 404)

    def _permission_request(self):
        supplied = self.headers.get("X-Jarvis-Perm", "")
        if not (self.client_address[0] == "127.0.0.1"
                and supplied and secrets.compare_digest(supplied, PERM_TOKEN)):
            return self._json({"error": "unauthorized"}, 401)
        try:
            req = json.loads(self._read(MAX_JSON) or b"{}")
        except (ValueError, TypeError):
            return self._json({"allow": False})
        emit = ACTIVE["emit"]
        if not emit:   # nobody is watching the HUD -> nobody can confirm
            return self._json({"allow": False})
        rid = secrets.token_hex(6)
        entry = {"event": threading.Event(), "allow": False}
        PENDING[rid] = entry
        tool = str(req.get("tool_name", "?"))
        try:
            emit(dict(t="confirm", id=rid, tool=tool,
                      detail=_describe_call(tool, req.get("input")), timeout=PERM_TIMEOUT))
            entry["event"].wait(PERM_TIMEOUT)
            emit(dict(t="confirm_done", id=rid, allow=entry["allow"]))
        except OSError:
            entry["allow"] = False
        finally:
            PENDING.pop(rid, None)
        return self._json({"allow": entry["allow"]})

    def _lean(self, message, emit):
        """Lean path: local router first, otherwise one small stateless Claude call."""
        t0 = time.time()
        commands.update_state(lambda d: d.__setitem__("last_turn_at", time.time()))
        is_briefing = message.strip().lower().startswith(("/briefing", "tagesbriefing zum start"))
        if is_briefing:
            facts = briefing.briefing_facts()
            today = time.strftime("%Y-%m-%d")
            commands.update_state(lambda d: d.__setitem__("briefing_day", today))
            if not BRIEFING_LLM:
                text = "Ihr Tagesbriefing, Sir. " + " ".join(ln.lstrip("- ") for ln in facts.splitlines())
                emit(dict(t="delta", text=text))
                emit(dict(t="complete", ms=0))
                log_usage("briefing-local", None, [], {}, message=message, reply=text)
                return
            route = {"kind": "llm", "groups": [], "model": router.MODEL_SIMPLE, "reason": "briefing"}
            system = BRIEFING_PROMPT
            prefix = f"<kontext>\nJetzt: {time.strftime('%A, %d.%m.%Y, %H:%M Uhr')}\nFakten:\n{facts}\n</kontext>"
            message = "Tagesbriefing bitte."
        else:
            route = router.route(message, ha_full=bool(commands.ha_request(message)))
            if route["kind"] == "local":
                reply = route["reply"]
                emit(dict(t="note", message=f"lokal erledigt ({route['handler']}) — 0 Tokens"))
                emit(dict(t="delta", text=reply))
                emit(dict(t="complete", ms=int((time.time() - t0) * 1000)))
                try:
                    memory.log_turn(message, reply)
                except OSError:
                    pass
                commands.remember_turn(message, reply)
                log_usage("local:" + route["handler"], None, [], {}, message=message, reply=reply,
                          ms=int((time.time() - t0) * 1000))
                return
            system = commands.lean_system(route["groups"])
            prefix = commands.lean_prefix(message, route["groups"])
        emit(dict(t="note", message=f"{route['model']} · {', '.join(route['groups']) or 'ohne Werkzeuge'}"))
        hit = memory.top_match(message)
        if hit and not is_briefing:
            emit(dict(t="focus", id=hit))
        ACTIVE["emit"] = emit
        reply, usage, before = [], {}, memory.build_graph(force=True)["total"]
        try:
            for ev in runtime.run_lean(message, route["model"], route["groups"], system, prefix):
                if ev.get("t") == "delta":
                    reply.append(ev.get("text") or "")
                if ev.get("t") == "usage":
                    usage = ev
                if ev.get("t") == "ratelimit":   # remember for the usage panel after reloads
                    rl = dict(ev, at=time.time())
                    commands.update_state(lambda d: d.__setitem__("usage", rl))
                if ev.get("t") == "complete":
                    text = "".join(reply)
                    if not is_briefing:
                        try:
                            memory.log_turn(message, text)
                        except OSError as e:
                            emit(dict(t="note", message=f"Protokoll fehlgeschlagen: {e}"))
                    commands.remember_turn(message if not is_briefing else "(Tagesbriefing)", text)
                    if memory.build_graph(force=True)["total"] != before:
                        emit(dict(t="graph"))
                emit(ev)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            try:
                emit(dict(t="error", message=str(e)[:300]))
            except OSError:
                pass
        finally:
            ACTIVE["emit"] = None
            for entry in list(PENDING.values()):
                entry["event"].set()
            row = log_usage(("briefing" if is_briefing else "llm") + ":" + route.get("reason", ""),
                            route["model"], route["groups"], usage, len(system), len(prefix), message,
                            "".join(reply), int((time.time() - t0) * 1000))
            try:
                emit(dict(t="note", message=f"{row['total_in']:,} Tokens rein, {row['output']} raus, "
                                            f"{row['api_calls']} Aufruf(e)".replace(",", ".")))
            except OSError:
                pass

    def _stream(self, raw):
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "bad json"}, 400)
        message = (payload.get("message") or "").strip()
        if not message:
            return self._json({"error": "empty message"}, 400)
        if payload.get("fresh"):
            SESSION["id"] = None

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        def emit(ev):
            with EMIT_LOCK:   # the permission gate writes from another thread
                self.wfile.write((json.dumps(ev) + "\n").encode())
                self.wfile.flush()

        cmd = commands.handle(message, runner=lambda m: runtime.run(m, None, commands.context_block(m)))
        if cmd:
            if cmd.get("note"):
                emit(dict(t="note", message=cmd["note"]))
            if cmd.get("fresh"):
                SESSION["id"] = None
            if cmd.get("focus"):
                emit(dict(t="focus", id=str(cmd["focus"]).lower()))
            if cmd.get("graph"):
                emit(dict(t="graph"))
            if cmd.get("message") is None:
                delay = float(cmd.get("delay") or 0)
                for event in cmd.get("events", []):
                    if delay and event.get("phase") == "result":
                        time.sleep(delay)
                        delay = 0
                    emit(event)
                emit(dict(t="delta", text=cmd.get("reply", "Done.")))
                emit(dict(t="complete", ms=0))
                return
            message = cmd["message"]

        if runtime.LEAN:
            return self._lean(message, emit)

        last = commands.load().get("last_turn_at") or 0
        if SESSION["id"] and time.time() - last > SESSION_IDLE_MIN * 60:
            SESSION["id"] = None
            emit(dict(t="note", message="Neue Sitzung nach Pause (Gedächtnis bleibt)"))
        commands.update_state(lambda d: d.__setitem__("last_turn_at", time.time()))
        if not SESSION["id"]:
            runtime.HA["on"] = False   # a fresh session starts light
        want_ha = commands.ha_request(message)
        if want_ha is not None and want_ha != runtime.HA["on"]:
            runtime.HA["on"] = want_ha
            emit(dict(t="note", message="Home Assistant verbunden" if want_ha
                      else "Home Assistant getrennt"))
        heavy = False
        session = SESSION["id"]
        ACTIVE["emit"] = emit
        system = commands.system_block(fresh=session is None)
        prefix = commands.turn_prefix(message)
        hit = memory.top_match(message)
        if hit:
            emit(dict(t="focus", id=hit))
        reply, before = [], memory.build_graph(force=True)["total"]
        try:
            for ev in runtime.run(message, session, system, prefix):
                if ev.get("t") == "delta":
                    reply.append(ev.get("text") or "")
                if ev.get("t") == "ratelimit":   # remember for the usage panel after reloads
                    usage = dict(ev, at=time.time())
                    commands.update_state(lambda d: d.__setitem__("usage", usage))
                if ev.get("t") == "usage" and (ev.get("context_tokens") or 0) > SESSION_MAX_TOKENS:
                    heavy = True
                if ev.get("t") == "complete" and runtime.valid_session(ev.get("session_id")):
                    SESSION["id"] = None if heavy else ev["session_id"]
                    if heavy:
                        emit(dict(t="note", message="Sitzung zu schwer — die nächste startet frisch"))
                if ev.get("t") == "error":
                    SESSION["id"] = None
                if ev.get("t") == "complete":
                    try:
                        memory.log_turn(message, "".join(reply))
                    except OSError as e:
                        emit(dict(t="note", message=f"Protokoll fehlgeschlagen: {e}"))
                    # Claude may have written a memory note this turn: refresh the graph
                    if memory.build_graph(force=True)["total"] != before:
                        emit(dict(t="graph"))
                emit(ev)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            try:
                emit(dict(t="error", message=str(e)[:300]))
            except OSError:
                pass
        finally:
            ACTIVE["emit"] = None
            for entry in list(PENDING.values()):   # run ended: nothing left to confirm
                entry["event"].set()
            _prewarm_soon()


RESTART_QUIET_S = int(os.environ.get("JARVIS_RESTART_QUIET_S", "90"))


def _code_stamp():
    files = list(ROOT.glob("*.py")) + [f for f in UI.iterdir() if f.suffix in (".js", ".html", ".css")]
    return {str(f): f.stat().st_mtime for f in files if f.is_file()}


def _watch_code():
    """Restart (via the supervisor) after a code change, but only once nothing
    has been said, heard or confirmed for RESTART_QUIET_S seconds."""
    seen = _code_stamp()
    changed = False
    while True:
        time.sleep(5)
        try:
            changed = changed or _code_stamp() != seen
        except OSError:      # a file mid-save — look again next round
            continue
        idle = (ACTIVE["emit"] is None and not PENDING and not RUN_LOCK.locked()
                and time.time() - LAST_POST[0] > RESTART_QUIET_S)
        if changed and idle:
            print("\n  Code geändert — starte neu.", flush=True)
            SESSION["id"] = None       # new code, new conversation; memory carries over
            runtime.shutdown()
            runtime.lean_shutdown()
            os._exit(RESTART_CODE)


def main():
    for stream in (sys.stdout, sys.stderr):
        try:   # Windows consoles default to cp1252 and choke on the banner's box characters
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    moved, deleted = memory.housekeeping()
    if moved or deleted:
        print(f"  Aufgeräumt: {len(moved)} in den Papierkorb, {deleted} endgültig gelöscht", flush=True)

    def daily_housekeeping():
        while True:
            time.sleep(6 * 3600)
            try:
                memory.housekeeping()
            except OSError:
                pass
    threading.Thread(target=daily_housekeeping, daemon=True).start()

    kind = runtime.runtime_kind()
    g = memory.build_graph()
    brain = (f"Claude Code {runtime.version()} — your subscription, no API key"
             if kind == "claude" else "MOCK — claude CLI not reachable")
    print(f"""
  JARVIS · Claude Code Memory HUD
  ────────────────────────────────────────────────
  brain        {brain}
  model        {runtime.MODEL or 'Claude default'}
  vault        {g['total']} notes · {len(g['links'])} links
               {g['vault']}
  voice        {voice.describe()}
  listening    {voice.describe_stt()}
  workdir      {runtime.WORKDIR}
  open         http://localhost:{PORT}
""", flush=True)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    _prewarm_soon()
    threading.Thread(target=icloud_calendar.agenda_block, daemon=True).start()
    threading.Thread(target=voice.preload_local_stt, daemon=True).start()
    threading.Thread(target=billing.summary, daemon=True).start()
    if os.environ.get("TTS_CHAIN", "").strip():
        import tts_chain
        tts_chain.start()          # provider balances in the background
    briefing.start_watchdog()
    if os.environ.get("JARVIS_CHILD") == "1":    # only a supervised child can be restarted
        threading.Thread(target=_watch_code, daemon=True).start()
    if os.environ.get("JARVIS_OPEN", "1") != "0":
        url = f"http://127.0.0.1:{PORT}"
        browser = os.path.expandvars(os.environ.get("JARVIS_BROWSER", "").strip())
        if browser and os.path.exists(browser):   # e.g. a browser that should autoplay the voice
            import subprocess
            subprocess.Popen([browser, "--autoplay-policy=no-user-gesture-required", url])
        else:
            webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  down.")
        srv.shutdown()


if __name__ == "__main__":
    main()
