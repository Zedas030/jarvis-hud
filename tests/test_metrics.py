"""
Metrics registry and structured logging — no server, no network.

    .venv\\Scripts\\python.exe -m unittest tests.test_metrics -v
"""
import isolate  # noqa: F401  (must come first)
import json
import logging
import os
import pathlib
import time
import unittest

import jarvis_log
import metrics


class Registry(unittest.TestCase):
    def setUp(self):
        metrics.reset()

    def test_counter_gauge_histogram_in_exposition(self):
        metrics.counter("turns_total", route="local")
        metrics.counter("turns_total", 2, route="local")
        metrics.gauge("pool_processes", 3)
        metrics.observe("turn_ms", 350, route="llm")
        metrics.observe("turn_ms", 5000, route="llm")
        text = metrics.prometheus()
        self.assertIn('jarvis_turns_total{route="local"} 3', text)
        self.assertIn("# TYPE jarvis_turns_total counter", text)
        self.assertIn("jarvis_pool_processes 3", text)
        self.assertIn('jarvis_turn_ms_bucket{route="llm",le="400"} 1', text)
        self.assertIn('jarvis_turn_ms_bucket{route="llm",le="+Inf"} 2', text)
        self.assertIn('jarvis_turn_ms_sum{route="llm"} 5350', text)
        self.assertIn('jarvis_turn_ms_count{route="llm"} 2', text)
        self.assertIn("jarvis_uptime_seconds", text)
        self.assertTrue(text.endswith("\n"))

    def test_labels_are_escaped_and_shortened(self):
        metrics.counter("x_total", why='He said "no"\nreally', long="a" * 100)
        line = [ln for ln in metrics.prometheus().splitlines() if ln.startswith("jarvis_x_total{")][0]
        self.assertIn('why="He said \\"no\\" really"', line)
        self.assertIn('long="' + "a" * 60 + '"', line)

    def test_snapshot_has_averages(self):
        metrics.observe("job_ms", 100, job="a")
        metrics.observe("job_ms", 300, job="a")
        snap = metrics.snapshot()
        self.assertEqual(snap["histograms"]['job_ms{job="a"}']["avg"], 200)

    def test_timed_context_manager(self):
        with metrics.timed("step_ms", step="x"):
            time.sleep(0.01)
        self.assertGreaterEqual(metrics.snapshot()["histograms"]['step_ms{step="x"}']["sum"], 5)

    def test_a_bad_value_never_raises(self):
        metrics.observe("turn_ms", "not a number")     # swallowed: a metric must not break a turn
        metrics.gauge("g", object())
        self.assertNotIn("jarvis_g", metrics.prometheus())

    def test_tool_calls_counted_from_the_audit_log(self):
        day = time.strftime("%Y-%m-%d")
        path = pathlib.Path(os.environ["JARVIS_AUDIT_LOG"])
        rows = [dict(at=f"{day} 10:00:00", kind="tool", tool="home_control", cls="LIMITED_WRITE", ok=True),
                dict(at=f"{day} 10:00:01", kind="tool", tool="home_control", cls="LIMITED_WRITE", ok=False),
                dict(at=f"{day} 10:00:02", kind="card", tool="calendar_add", allow=True),
                dict(at="2020-01-01 10:00:00", kind="tool", tool="weather", cls="READ", ok=True)]
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        text = metrics.tool_calls_from_audit(path)
        self.assertIn('jarvis_tool_calls_total{cls="LIMITED_WRITE",ok="true"} 1', text)
        self.assertIn('jarvis_tool_calls_total{cls="LIMITED_WRITE",ok="false"} 1', text)
        self.assertNotIn('cls="READ"', text, "yesterday's calls stay out")
        self.assertEqual(metrics.tool_calls_from_audit(path.with_name("missing.jsonl")), "")


class Logging(unittest.TestCase):
    def test_json_lines_and_warning_counter(self):
        metrics.reset()
        target = jarvis_log.setup(console=False)
        self.assertEqual(pathlib.Path(target), pathlib.Path(os.environ["JARVIS_LOG_FILE"]))
        log = jarvis_log.get("jarvis.core.something")
        self.assertEqual(log.name, "something")
        log.info("hello %s", "world", extra={"turn": 3})
        log.warning("careful")
        try:
            raise ValueError("boom")
        except ValueError:
            log.error("failed", exc_info=True)
        for h in logging.getLogger().handlers:
            h.flush()
        rows = [json.loads(ln) for ln in pathlib.Path(target).read_text(encoding="utf-8").splitlines() if ln.strip()]
        info = [r for r in rows if r["msg"] == "hello world"][0]
        self.assertEqual((info["level"], info["logger"], info["turn"]), ("INFO", "something", 3))
        err = [r for r in rows if r["msg"] == "failed"][0]
        self.assertIn("ValueError: boom", err["exc"])
        snap = metrics.snapshot()["counters"]
        self.assertEqual(snap['log_events_total{level="warning",logger="something"}'], 1)
        self.assertEqual(snap['log_events_total{level="error",logger="something"}'], 1)
        self.assertNotIn('log_events_total{level="info",logger="something"}', snap)

    def test_setup_is_idempotent(self):
        a = jarvis_log.setup(console=False)
        n = len(logging.getLogger().handlers)
        self.assertEqual(jarvis_log.setup(console=False), a)
        self.assertEqual(len(logging.getLogger().handlers), n)


if __name__ == "__main__":
    unittest.main()
