"""
The conversation state machine of the voice engine.

    IDLE → LISTENING → THINKING → SPEAKING → (INTERRUPTED → LISTENING | RESUMING → SPEAKING) → IDLE

Every change is logged with timestamp and trigger (logs/voice_state.jsonl) and handed to the
listeners (the WebSocket session tells the HUD, the metrics measure the gaps). Transitions that
make no sense (SPEAKING from IDLE) raise, so a logic error shows up in a test and not as a mute
Jarvis. Plain thread-safe object; no threads of its own.
"""
import json
import os
import pathlib
import threading
import time
import jarvis_log

log = jarvis_log.get(__name__)

IDLE, LISTENING, THINKING, SPEAKING, INTERRUPTED, RESUMING = (
    "IDLE", "LISTENING", "THINKING", "SPEAKING", "INTERRUPTED", "RESUMING")
STATES = (IDLE, LISTENING, THINKING, SPEAKING, INTERRUPTED, RESUMING)

ALLOWED = {
    IDLE: {LISTENING, THINKING, SPEAKING},          # THINKING: a typed question; SPEAKING: a notice read aloud
    LISTENING: {THINKING, IDLE, SPEAKING},          # SPEAKING: a notice while nobody talks
    THINKING: {SPEAKING, LISTENING, IDLE},          # LISTENING: cancelled / nothing to say
    SPEAKING: {INTERRUPTED, LISTENING, IDLE, THINKING},
    INTERRUPTED: {RESUMING, LISTENING, IDLE, THINKING, SPEAKING},   # SPEAKING: a false alarm, no pause happened
    RESUMING: {SPEAKING, INTERRUPTED, LISTENING, IDLE},
}

ROOT = pathlib.Path(__file__).resolve().parents[2]


class Machine:
    def __init__(self, name="voice", log_path=None, clock=time.time):
        self.name = name
        self.state = IDLE
        self.since = clock()
        self.clock = clock
        self.history = []            # the last transitions (for tests and the debug view)
        self.listeners = []
        self.lock = threading.RLock()
        self.log_path = pathlib.Path(log_path) if log_path else None
        if self.log_path and not self.log_path.is_absolute():
            self.log_path = ROOT / self.log_path

    def on_change(self, fn):
        """fn(old, new, why, at) — called inside the lock, keep it short."""
        self.listeners.append(fn)
        return fn

    def can(self, new):
        return new in ALLOWED[self.state]

    def go(self, new, why=""):
        """Switch state; returns the time spent in the old state. Same state = no-op."""
        if new not in STATES:
            raise ValueError(f"unknown state {new}")
        with self.lock:
            old = self.state
            if new == old:
                return 0.0
            if new not in ALLOWED[old]:
                raise RuntimeError(f"{self.name}: {old} → {new} not allowed ({why})")
            now = self.clock()
            spent = now - self.since
            self.state, self.since = new, now
            entry = {"at": now, "from": old, "to": new, "why": why[:120], "spent_ms": int(spent * 1000)}
            self.history.append(entry)
            del self.history[:-200]
            self._write(entry)
            for fn in list(self.listeners):
                try:
                    fn(old, new, why, now)
                except Exception as e:  # noqa: BLE001 — a listener must not break the engine
                    log.warning("state listener failed: %s", str(e)[:100])
            return spent

    def force(self, new, why="reset"):
        """Back to a known state whatever the rules (errors, session end). Logged like any change."""
        with self.lock:
            if new in ALLOWED[self.state] or new == self.state:
                return self.go(new, why)
            old, now = self.state, self.clock()
            spent = now - self.since
            self.state, self.since = new, now
            entry = {"at": now, "from": old, "to": new, "why": ("forced: " + why)[:120], "spent_ms": int(spent * 1000)}
            self.history.append(entry)
            del self.history[:-200]
            self._write(entry)
            for fn in list(self.listeners):
                try:
                    fn(old, new, why, now)
                except Exception as e:  # noqa: BLE001
                    log.warning("state listener failed: %s", str(e)[:100])
            return spent

    def is_(self, *states):
        return self.state in states

    def elapsed(self):
        return self.clock() - self.since

    def _write(self, entry):
        if not self.log_path:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(dict(entry, session=self.name,
                                         ts=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry["at"])))) + "\n")
        except OSError:
            pass
