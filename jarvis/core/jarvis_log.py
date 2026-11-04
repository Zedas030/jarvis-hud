"""
Structured logging for the whole server: one JSON line per event in logs/jarvis.log, warnings
and errors mirrored to the console, and every WARNING or worse counted as a metric
(jarvis_log_events_total{level,logger}) so a Grafana panel shows which module fails how often.

  setup(name="jarvis")   once per process (server.py main, jarvis_mcp.py main): file, console, metric hook
  get(module)            the logger of a module: log = jarvis_log.get(__name__) → log.warning("...")

Why a module of its own instead of logging.basicConfig: the file is JSON (grep-able and readable
by Loki/Promtail later), its path follows the test isolation (JARVIS_LOG_FILE), the console keeps
the short two-space style of the start banner, and the metric hook needs the registry. Rotation:
5 MB, three files kept — enough for weeks and never a full disk. The MCP server is its own
process per request, so it writes jarvis-mcp.log; two processes rotating one file would clash
on Windows.

Level: JARVIS_LOG_LEVEL (default INFO). Debug of a single module: JARVIS_LOG_DEBUG=voice_engine,router.
"""
import json
import logging
import logging.handlers
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
_STATE = {"ready": False, "path": None}


class JsonLine(logging.Formatter):
    def format(self, record):
        row = {"at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created)),
               "level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        if record.exc_info:
            row["exc"] = self.formatException(record.exc_info)[-2000:]
        for k, v in record.__dict__.items():      # extra={"turn": 3} lands as a field
            if k not in _STD and not k.startswith("_") and k not in row:
                try:
                    json.dumps(v)
                    row[k] = v
                except (TypeError, ValueError):
                    row[k] = str(v)[:200]
        return json.dumps(row, ensure_ascii=False)


_STD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class Console(logging.Formatter):
    def format(self, record):
        tag = "" if record.levelno < logging.WARNING else record.levelname.lower() + " "
        text = f"  {tag}{record.name}: {record.getMessage()}"
        if record.exc_info and record.levelno >= logging.ERROR:
            text += " — " + str(record.exc_info[1])[:200]
        return text


class CountEvents(logging.Handler):
    """WARNING and above become jarvis_log_events_total{level,logger}."""
    def emit(self, record):
        try:
            import metrics
            metrics.counter("log_events_total", level=record.levelname.lower(), logger=record.name)
        except Exception:  # noqa: BLE001
            pass


def path():
    p = os.environ.get("JARVIS_LOG_FILE", "").strip()
    return pathlib.Path(p) if p else ROOT / "logs" / "jarvis.log"


def setup(name="jarvis", console=True):
    """Idempotent. name picks the file: jarvis.log for the server, jarvis-mcp.log for the tool server."""
    if _STATE["ready"]:
        return _STATE["path"]
    root = logging.getLogger()
    level = getattr(logging, os.environ.get("JARVIS_LOG_LEVEL", "INFO").strip().upper(), logging.INFO)
    root.setLevel(level)
    target = path() if name == "jarvis" else path().with_name(f"{name}.log")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(target, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(JsonLine())
        root.addHandler(fh)
    except OSError:
        target = None
    if console:
        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(logging.WARNING)
        ch.setFormatter(Console())
        root.addHandler(ch)
    counting = CountEvents()
    counting.setLevel(logging.WARNING)
    root.addHandler(counting)
    for mod in (m.strip() for m in os.environ.get("JARVIS_LOG_DEBUG", "").split(",") if m.strip()):
        logging.getLogger(mod).setLevel(logging.DEBUG)
    # third-party chatter stays out of the file
    for noisy in ("websockets", "asyncio", "urllib3", "faster_whisper", "piper", "httpx", "httpcore",
                  "httpx2", "huggingface_hub", "filelock", "ctranslate2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _STATE.update(ready=True, path=target)
    return target


def get(module):
    return logging.getLogger(module.rsplit(".", 1)[-1])
