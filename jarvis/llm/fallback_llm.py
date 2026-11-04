"""
Stand-in brains when Claude is not available: a chain of free providers.

Used by the lean path in server.py when
  - the Claude CLI cannot be started (offline, not logged in),
  - the subscription limit is reached (rate limit "rejected" until its reset),
  - or a Claude call fails before it said a single word.

Chain (JARVIS_FALLBACK, in this order, default "groq,gemini,ollama"):
  groq    Groq free tier, gpt-oss-120b — fast, no training on your data.   GROQ_API_KEY
  gemini  Google AI Studio free tier, Gemini Flash.                      GEMINI_API_KEY
          The free tier may use prompts for training, so it never sees
          turns that touch mail, calendar or health (see PRIVATE below).
  ollama  local model, works offline.  ollama pull qwen2.5:7b-instruct

All three speak the OpenAI chat format with tool calls, so the stand-ins get
the same Jarvis tools as Claude: the tool groups the router picked, served by
jarvis_mcp.py and guarded by the same confirmation cards. A provider that hits
its own limit (HTTP 429) is skipped until its retry time; a bad key for hours.
Local commands (lights, timers, lists, weather ...) never needed an LLM anyway.

JARVIS_FALLBACK=groq,gemini,ollama | ollama | off
JARVIS_FALLBACK_PRIVATE  providers allowed to see private turns (default groq,ollama)
GROQ_API_KEY · GROQ_MODEL · GEMINI_API_KEY · GEMINI_MODEL · OLLAMA_URL · OLLAMA_MODEL
"""
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import hostos

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository
_raw = os.environ.get("JARVIS_FALLBACK", "groq,gemini,ollama").strip().lower()
ORDER = [] if _raw in ("", "off", "0", "no", "false") else [p.strip() for p in _raw.split(",") if p.strip()]
MODE = ",".join(ORDER) or "off"
MAX_TOKENS = int(os.environ.get("OLLAMA_MAX_TOKENS", os.environ.get("JARVIS_FALLBACK_MAX_TOKENS", "400")))
MAX_ROUNDS = int(os.environ.get("JARVIS_FALLBACK_TOOL_ROUNDS", "4"))
# Groups whose data must not reach a provider that may train on it.
PRIVATE_GROUPS = {"mail", "calendar", "health"}
PRIVATE_OK = {p.strip() for p in os.environ.get("JARVIS_FALLBACK_PRIVATE", "groq,ollama").lower().split(",")}
# Groups the stand-ins cannot serve: they need Claude Code built-ins or the HA MCP server.
UNSUPPORTED_GROUPS = {"pc", "ha_full"}
# Tools that change something. After one of these ran, no second provider takes
# over (it would repeat the action); after read-only tools a handover is safe.
import policy  # noqa: E402
import jarvis_log

log = jarvis_log.get(__name__)
ACTING_TOOLS = policy.acting()
# Typographic spaces and hyphens some models emit; the voice chain reads them oddly.
_PLAIN = str.maketrans({" ": " ", " ": " ", " ": " ", "‑": "-", "‐": "-"})

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").strip().rstrip("/")
PROVIDERS = {
    "groq": dict(url="https://api.groq.com/openai/v1/chat/completions",
                 key=os.environ.get("GROQ_API_KEY", "").strip(),
                 model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b").strip(), label="Groq"),
    "gemini": dict(url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                   key=os.environ.get("GEMINI_API_KEY", "").strip(),
                   model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash").strip(), label="Gemini"),
    "ollama": dict(url=OLLAMA_URL + "/v1/chat/completions", key="",
                   model=os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct").strip(), label="Ollama"),
}
ORDER = [p for p in ORDER if p in PROVIDERS]
MODEL = PROVIDERS[ORDER[0]]["model"] if ORDER else ""     # first choice, for the startup banner

# name -> {"until": epoch, "why": text} while a provider is cooling down
_COOL = {}
_OLLAMA = {"at": 0.0, "ok": False, "why": ""}

NOTE = ("Gerade bist du im Notbetrieb auf {label} statt Claude. Nutze nur die angebotenen Werkzeuge. "
        "Kannst du etwas damit nicht nachsehen, sag das in einem Satz statt zu raten.")
NOTE_NO_TOOLS = ("Gerade bist du im Notbetrieb auf {label} statt Claude, ohne Werkzeuge: kein Web, keine Mails, "
                 "kein Kalender, kein Smart Home über dich. Licht, Timer, Listen und Wetter erledigt das System "
                 "selbst. Kannst du etwas nicht nachsehen, sag das in einem Satz statt zu raten.")


class ProviderError(Exception):
    """A provider failed before it said a word — the next one in the chain takes over."""


# ── availability ─────────────────────────────────────────────────────────────

def _ollama_ready(force=False):
    """Ollama running and the model pulled? Checked at most once a minute."""
    if not force and time.time() - _OLLAMA["at"] < 60:
        return _OLLAMA["ok"], _OLLAMA["why"]
    model, ok, why = PROVIDERS["ollama"]["model"], False, ""
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=2) as r:
            names = [m.get("name", "") for m in json.loads(r.read()).get("models", [])]
        # "qwen2.5" without a tag means "qwen2.5:latest" to Ollama
        ok = model in names or (":" not in model and any(n.split(":")[0] == model for n in names))
        why = "" if ok else f"Modell {model} fehlt (ollama pull {model})"
    except (urllib.error.URLError, OSError, ValueError) as e:
        why = f"Ollama nicht erreichbar ({e})"
    _OLLAMA.update(at=time.time(), ok=ok, why=why)
    return ok, why


def _ready(name):
    """(usable now?, reason if not)"""
    cool = _COOL.get(name)
    if cool and cool["until"] > time.time():
        return False, f"{cool['why']} — wieder ab {time.strftime('%H:%M', time.localtime(cool['until']))}"
    if name == "ollama":
        return _ollama_ready()
    if not PROVIDERS[name]["key"]:
        return False, f"kein {name.upper()}_API_KEY in .env"
    return True, ""


def chain(groups=(), private=False):
    """Providers to try for this turn, in order."""
    private = private or bool(PRIVATE_GROUPS & set(groups or ()))
    return [p for p in ORDER if _ready(p)[0] and (not private or p in PRIVATE_OK)]


def available(force=False):
    if force:
        _ollama_ready(force=True)
    return any(_ready(p)[0] for p in ORDER)


def status():
    rows = [dict(name=p, model=PROVIDERS[p]["model"], ok=ok, why=why)
            for p in ORDER for ok, why in [_ready(p)]]
    first = next((r for r in rows if r["ok"]), None)
    return {"mode": MODE, "model": first["model"] if first else MODEL, "ok": bool(first),
            "why": "" if first else "; ".join(f"{r['name']}: {r['why']}" for r in rows) or "aus",
            "chain": rows}


def _cool_down(name, seconds, why):
    _COOL[name] = {"until": time.time() + max(30, min(seconds, 6 * 3600)), "why": why}
    import metrics
    metrics.counter("fallback_cooldowns_total", provider=name, why=why.split(" ", 1)[0])
    log.warning("%s pausiert %d s: %s", name, int(_COOL[name]["until"] - time.time()), why)


def _retry_seconds(err, body):
    """How long a provider wants us to wait after a 429."""
    try:
        return float(err.headers.get("retry-after"))
    except (TypeError, ValueError, AttributeError):
        pass
    m = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', body)                 # Gemini
    if m:
        return float(m.group(1))
    m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?", body)  # Groq
    if m and any(m.groups()):
        h, mi, s = (float(x or 0) for x in m.groups())
        return h * 3600 + mi * 60 + s
    return 600


# ── tools: jarvis_mcp.py as a child, exactly like Claude Code runs it ────────

def _tool_names(groups):
    import jarvis_mcp
    names = set()
    for g in groups or ():
        names |= jarvis_mcp.GROUPS.get(g, set())
    return names


def tool_specs(groups):
    """OpenAI-style function definitions for the Jarvis tools of these groups."""
    import jarvis_mcp
    names = _tool_names(groups)
    specs = []
    for t in jarvis_mcp.TOOLS:
        if t["name"] not in names:
            continue
        fn = {"name": t["name"], "description": t["description"]}
        schema = t.get("inputSchema") or {}
        if schema.get("properties"):      # Gemini rejects an object schema without properties
            fn["parameters"] = schema
        specs.append({"type": "function", "function": fn})
    return specs


class McpClient:
    """Minimal stdio JSON-RPC client for jarvis_mcp.py."""

    def __init__(self, groups):
        port = os.environ.get("JARVIS_PORT", "8720")
        env = dict(os.environ, JARVIS_PERM_URL=f"http://127.0.0.1:{port}/api/permission/request",
                   JARVIS_TOOL_GROUPS=",".join(g for g in groups if g not in UNSUPPORTED_GROUPS) or "none")
        self.proc = subprocess.Popen([sys.executable, "-I", os.path.join(HERE, "jarvis", "tools", "jarvis_mcp.py")],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     env=env, text=True, encoding="utf-8", bufsize=1,
                                     **hostos.QUIET)
        self.lines = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self.next_id = 0
        self.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                "clientInfo": {"name": "jarvis-fallback", "version": "1.0"}})

    def _pump(self):
        try:
            for line in self.proc.stdout:
                self.lines.put(line)
        except (OSError, ValueError):     # pipe closed by close()
            pass
        self.lines.put(None)

    def rpc(self, method, params, timeout=30):
        self.next_id += 1
        mid = self.next_id
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": mid, "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()
        end = time.time() + timeout
        while True:
            try:
                line = self.lines.get(timeout=max(0.1, end - time.time()))
            except queue.Empty:
                raise TimeoutError(f"{method}: keine Antwort") from None
            if line is None:
                raise RuntimeError("Werkzeug-Server beendet")
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") == mid:
                if msg.get("error"):
                    raise RuntimeError(msg["error"].get("message") or "Fehler")
                return msg.get("result") or {}

    def call(self, name, args, timeout=60):
        res = self.rpc("tools/call", {"name": name, "arguments": args}, timeout)
        return "".join(c.get("text", "") for c in res.get("content") or [] if isinstance(c, dict))

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            self.proc.kill()
        self.proc.stdout.close()


def run_tool(mcp, name, args):
    """One tool call; anything Claude would have to confirm goes through the same card."""
    import runtime
    if name not in runtime.NO_CONFIRM:
        perm_timeout = int(os.environ.get("JARVIS_PERM_TIMEOUT", "75")) + 30
        try:
            verdict = json.loads(mcp.call("approve", {"tool_name": f"mcp__jarvis__{name}", "input": args},
                                          timeout=perm_timeout) or "{}")
        except (ValueError, TimeoutError, RuntimeError) as e:
            return f"Bestätigung nicht möglich ({e}). Abgelehnt."
        if verdict.get("behavior") != "allow":
            return verdict.get("message") or "Der User hat diese Aktion nicht bestätigt."
    try:
        return mcp.call(name, args)
    except (TimeoutError, RuntimeError, OSError) as e:
        return f"Werkzeugfehler: {e}"


# ── one provider ─────────────────────────────────────────────────────────────

def _post(p, body, timeout=60):
    # Groq sits behind Cloudflare, which blocks the default "Python-urllib" agent (error 1010)
    headers = {"content-type": "application/json", "user-agent": "jarvis-hud/1.0"}
    if p["key"]:
        headers["authorization"] = "Bearer " + p["key"]
    req = urllib.request.Request(p["url"], data=json.dumps(body).encode(), method="POST", headers=headers)
    return urllib.request.urlopen(req, timeout=timeout)


def _request(name, body, stream=False):
    """POST with error classification: limits and bad keys cool the provider down."""
    p = PROVIDERS[name]
    model = body.pop("_model", None) or p["model"]
    try:
        return _post(p, dict(body, model=model, stream=stream), timeout=120 if name == "ollama" else 60)
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")[:2000]
        if e.code == 429:
            _cool_down(name, _retry_seconds(e, text), "Limit erreicht")
            raise ProviderError(f"{p['label']}: Limit erreicht") from None
        why = {401: "API-Key ungültig",
               403: "Zugriff gesperrt (Cloudflare)" if "1010" in text else "Zugriff verweigert",
               404: f"Modell {p['model']} nicht verfügbar ({name.upper()}_MODEL anpassen)"}.get(e.code)
        if why:
            _cool_down(name, 6 * 3600, why)
            raise ProviderError(f"{p['label']}: {why}") from None
        detail = " ".join(text.split())[:160]
        if e.code >= 500:
            _cool_down(name, 120, f"Serverfehler {e.code}")
        raise ProviderError(f"{p['label']}: HTTP {e.code} {detail}") from None
    except (urllib.error.URLError, OSError) as e:
        _cool_down(name, 60, "nicht erreichbar")
        raise ProviderError(f"{p['label']}: nicht erreichbar ({e})") from None


def _stream_text(resp):
    """Text pieces from an OpenAI-style SSE stream."""
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            ev = json.loads(data)
        except ValueError:
            continue
        for ch in ev.get("choices") or []:
            piece = (ch.get("delta") or {}).get("content")
            if piece:
                yield piece


def _turn(name, messages, tools, mcp, usage, model=None):
    """Runs the tool loop on one provider. Yields events like runtime.run_lean."""
    body = {"messages": messages, "max_tokens": MAX_TOKENS, "temperature": 0.6}
    if model:
        body["_model"] = model
    if not tools:
        resp = _request(name, body, stream=True)
        with resp:
            yield from (dict(t="delta", text=piece.translate(_PLAIN)) for piece in _stream_text(resp))
        usage["api_calls"] += 1
        return
    for rnd in range(MAX_ROUNDS + 1):
        last = rnd == MAX_ROUNDS
        req = dict(body, tools=tools, tool_choice="none" if last else "auto")    # copy: _model is popped per request
        with _request(name, req) as resp:
            data = json.loads(resp.read())
        usage["api_calls"] += 1
        u = data.get("usage") or {}
        usage["input_tokens"] += u.get("prompt_tokens") or 0
        usage["output_tokens"] += u.get("completion_tokens") or 0
        msg = ((data.get("choices") or [{}])[0]).get("message") or {}
        calls = msg.get("tool_calls") or []
        if not calls or last:
            text = msg.get("content") or ""
            if text:
                yield dict(t="delta", text=text.translate(_PLAIN))
            return
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        allowed = {t["function"]["name"] for t in tools}
        for i, c in enumerate(calls):
            fn = c.get("function") or {}
            tname = fn.get("name") or ""
            try:
                args = fn.get("arguments") or {}
                args = json.loads(args) if isinstance(args, str) else args
            except ValueError:
                args = {}
            yield dict(t="tool", phase="use", name=f"mcp__jarvis__{tname}", input=json.dumps(args)[:90],
                       args=args if isinstance(args, dict) else None)
            result = run_tool(mcp, tname, args) if tname in allowed else f"Unbekanntes Werkzeug {tname}."
            usage["tool_result_chars"] += len(result)
            yield dict(t="tool", phase="result", ok=not result.startswith(("Werkzeugfehler", "Bestätigung")),
                       name=f"mcp__jarvis__{tname}")
            messages.append({"role": "tool", "tool_call_id": c.get("id") or f"call_{rnd}_{i}",
                             "name": tname, "content": result})


def run(system, prefix, message, groups=(), private=False):
    """Tries the chain until one provider answers. Yields:
    provider (who answers), tool, delta, usage — or error if all failed."""
    groups = [g for g in (groups or ()) if g not in UNSUPPORTED_GROUPS]
    candidates = chain(groups, private)
    if not candidates:
        yield dict(t="error", message="Kein Ersatz-Modell verfügbar: " + status()["why"])
        return
    tools = tool_specs(groups)
    mcp = None
    errors = []
    try:
        for name in candidates:
            p = PROVIDERS[name]
            model = p["model"]
            if name == "ollama":                 # the resource manager may pick a smaller model
                try:
                    import resources
                    model, why = resources.choose_ollama_model(p["model"])
                    if why:
                        yield dict(t="note", message=f"Ollama: {why}")
                except Exception:  # noqa: BLE001
                    pass
            note = (NOTE if tools else NOTE_NO_TOOLS).format(label=f"{p['label']} ({model})")
            # providers that may train on prompts get no profile, no name, no notes from memory
            sys_text, pre_text = (system, prefix) if name in PRIVATE_OK else redact(system, prefix)
            messages = [{"role": "system", "content": sys_text + "\n\n" + note},
                        {"role": "user", "content": (pre_text + "\n\n" + message) if pre_text else message}]
            if tools and mcp is None:
                mcp = McpClient(groups)
            usage = dict(input_tokens=0, output_tokens=0, api_calls=0, tool_result_chars=0)
            said = False      # spoke or changed something: a second provider would repeat it
            yield dict(t="provider", name=name, model=model, label=p["label"])
            try:
                for ev in _turn(name, messages, tools, mcp, usage, model if model != p["model"] else None):
                    said = said or ev.get("t") == "delta" or (
                        ev.get("t") == "tool" and ev["name"].rsplit("__", 1)[-1] in ACTING_TOOLS)
                    yield ev
            except (ProviderError, ValueError, KeyError, TypeError) as e:
                if said:
                    yield dict(t="error", message=f"{p['label']} brach mitten in der Antwort ab: {e}"[:300])
                    return
                errors.append(str(e))
                yield dict(t="note", message=f"{e} — nächster Ersatz")
                continue
            yield dict(t="usage", **usage)
            return
        yield dict(t="error", message="Alle Ersatz-Modelle fehlgeschlagen: " + "; ".join(errors))
    finally:
        if mcp:
            mcp.close()


def redact(system, prefix):
    """For providers that may train on prompts (Gemini free tier): no profile, no name, no notes from memory."""
    system = re.sub(r"Was du über den User weißt:.*?Weitere Notizen \(memory_read\):[^\n]*\n?", "", system, flags=re.S)
    system = re.sub(r"Was du über den User weißt:.*?(?=\n\n[^-\n]|\Z)", "", system, flags=re.S)
    system = re.sub(r"(?m)^Der User heißt .*$", "", system)
    prefix = re.sub(r"Passende Notizen:.*?(?=\n\n|</kontext>)", "", prefix or "", flags=re.S)
    return system, prefix


def claude_blocked(usage):
    """True while the last rate-limit event said 'rejected' and its reset is still ahead."""
    u = usage or {}
    if u.get("status") != "rejected":
        return False
    reset = u.get("resets") or 0
    return (reset and reset > time.time()) or (not reset and time.time() - (u.get("at") or 0) < 1800)
