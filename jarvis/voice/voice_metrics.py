"""
Metrics of the voice engine: per turn one line in logs/voice_metrics.jsonl, and the totals in
Prometheus text format for GET /metrics (Grafana on the Pi scrapes the HUD port).

  Turn(...)            collects the stamps of one exchange; turn.mark("stt_done") etc.
  record(turn)         closes it: durations per stage, counters, the line in the log
  event(name, **tags)  a counter without a turn (false barge-in by his own echo, cut-off user)
  prometheus()         the text exposition

Stages and the derived durations (ms):
  speech_end → endpoint_decided   wait_ms       how long the endpoint waited after the last word
  endpoint   → stt_done           stt_ms
  stt_done   → llm_first_token    llm_first_ms
  stt_done   → llm_first_sentence first_sentence_ms
  sentence   → tts_first_audio    tts_first_ms
  speech_end → first_audio_out    first_word_ms   the number the user feels
  barge_detect → output_paused    barge_react_ms
Counters: turns, local_turns, barge_ins by outcome (noise, backchannel, stop, utterance, resume),
          echo_false_alarms, user_cut_off (he answered, the user went on talking), late_first_word.
"""
import json
import pathlib
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
_LOCK = threading.Lock()
_COUNTERS = {}                   # (name, tagstring) -> int
_SUMS = {}                       # name -> [count, sum_ms, max_ms]
_LAST = {}                       # name -> last value (gauges for the HUD)
_LOG = {"path": None}
BUCKETS_MS = (100, 200, 400, 800, 1200, 2000, 4000, 8000)
_HIST = {}                       # name -> {bucket: count}


def configure(path):
    _LOG["path"] = pathlib.Path(path) if path else None
    if _LOG["path"] and not _LOG["path"].is_absolute():
        _LOG["path"] = ROOT / _LOG["path"]


def _tagstr(tags):
    return ",".join(f'{k}="{str(v)[:40]}"' for k, v in sorted(tags.items()))


def event(name, n=1, **tags):
    with _LOCK:
        key = (name, _tagstr(tags))
        _COUNTERS[key] = _COUNTERS.get(key, 0) + n


def observe(name, ms):
    if ms is None:
        return
    ms = float(ms)
    with _LOCK:
        c = _SUMS.setdefault(name, [0, 0.0, 0.0])
        c[0] += 1
        c[1] += ms
        c[2] = max(c[2], ms)
        _LAST[name] = ms
        h = _HIST.setdefault(name, {b: 0 for b in BUCKETS_MS} | {"inf": 0})
        for b in BUCKETS_MS:
            if ms <= b:
                h[b] += 1
        h["inf"] += 1


class Turn:
    """Timestamps of one exchange. mark() is idempotent per stage (the first one counts)."""
    def __init__(self, kind="voice", clock=time.time):
        self.kind = kind
        self.clock = clock
        self.t = {"start": clock()}
        self.info = {}

    def mark(self, stage, at=None):
        if stage not in self.t:
            self.t[stage] = at if at is not None else self.clock()
        return self.t[stage]

    def ms(self, a, b):
        if a in self.t and b in self.t:
            return int(round((self.t[b] - self.t[a]) * 1000))
        return None

    def durations(self):
        return {
            "wait_ms": self.ms("speech_end", "endpoint"),
            "stt_ms": self.ms("endpoint", "stt_done"),
            "llm_first_ms": self.ms("stt_done", "llm_first_token"),
            "first_sentence_ms": self.ms("stt_done", "llm_first_sentence"),
            "tts_first_ms": self.ms("llm_first_sentence", "tts_first_audio"),
            "first_word_ms": self.ms("speech_end", "first_audio_out"),
            "answer_ms": self.ms("stt_done", "llm_done"),
            "speak_ms": self.ms("first_audio_out", "speak_done"),
            "barge_react_ms": self.ms("barge_detect", "output_paused"),
        }


def record(turn, target_first_word_ms=800):
    d = turn.durations()
    for k, v in d.items():
        if v is not None:
            observe(k, v)
    event("turns", kind=turn.kind, route=str(turn.info.get("route", "?")))
    if d.get("first_word_ms") is not None and d["first_word_ms"] > target_first_word_ms:
        event("late_first_word")
    line = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": turn.kind, **{k: v for k, v in d.items() if v is not None},
            **{k: v for k, v in turn.info.items() if k != "text"}}
    _write(line)
    return line


def _write(line):
    p = _LOG["path"]
    if not p:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass


def snapshot():
    """For the HUD and tests: last values, averages and counters."""
    with _LOCK:
        return {"last": dict(_LAST),
                "avg": {k: round(v[1] / v[0]) for k, v in _SUMS.items() if v[0]},
                "max": {k: round(v[2]) for k, v in _SUMS.items()},
                "counters": {(k[0] + ("{" + k[1] + "}" if k[1] else "")): v for k, v in _COUNTERS.items()}}


def prometheus():
    out = []
    with _LOCK:
        names = sorted({k[0] for k in _COUNTERS})
        for n in names:
            out.append(f"# TYPE jarvis_voice_{n}_total counter")
            for (name, tags), v in sorted(_COUNTERS.items()):
                if name == n:
                    out.append(f"jarvis_voice_{n}_total{{{tags}}} {v}" if tags else f"jarvis_voice_{n}_total {v}")
        for n, (count, total, mx) in sorted(_SUMS.items()):
            base = f"jarvis_voice_{n}"
            out.append(f"# TYPE {base} histogram")
            acc = _HIST.get(n, {})
            for b in BUCKETS_MS:
                out.append(f'{base}_bucket{{le="{b}"}} {acc.get(b, 0)}')
            out.append(f'{base}_bucket{{le="+Inf"}} {acc.get("inf", count)}')
            out.append(f"{base}_sum {total:.0f}")
            out.append(f"{base}_count {count}")
            out.append(f"# TYPE {base}_last gauge")
            out.append(f"{base}_last {_LAST.get(n, 0):.0f}")
            out.append(f"# TYPE {base}_max gauge")
            out.append(f"{base}_max {mx:.0f}")
    return "\n".join(out) + "\n"


def reset():
    with _LOCK:
        _COUNTERS.clear()
        _SUMS.clear()
        _LAST.clear()
        _HIST.clear()
