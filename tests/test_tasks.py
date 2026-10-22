"""
Task engine: steps, checks, retries, time limits, cancellation, persistence,
no duplicate runs, local trigger phrases. All sources are faked.

    .venv\\Scripts\\python.exe -m unittest tests.test_tasks -v
"""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import events     # noqa: E402
import policy     # noqa: E402
import router     # noqa: E402
import runtime    # noqa: E402
import tasks      # noqa: E402


class Engine(unittest.TestCase):
    def setUp(self):
        if tasks.PATH.exists():
            tasks.PATH.unlink()
        self._recipes = dict(tasks.RECIPES)

    def tearDown(self):
        tasks.RECIPES.clear()
        tasks.RECIPES.update(self._recipes)

    def recipe(self, steps, name="test"):
        tasks.RECIPES[name] = ("Testprüfung", steps)
        return name

    def test_all_ok(self):
        r = self.recipe([("A", lambda: 1, lambda x: ("ok", "eins"), 0), ("B", lambda: 2, lambda x: ("ok", "zwei"), 0)])
        tid, msg = tasks.start(r, speak=False, wait=True)
        t = tasks.get(tid)
        self.assertEqual(t["status"], "COMPLETED")
        self.assertEqual([s["status"] for s in t["steps"]], ["ok", "ok"])
        self.assertIn("alle 2 Schritte in Ordnung", t["report"])

    def test_findings_with_recovery_and_no_repair(self):
        r = self.recipe([("Speicherplatz", lambda: 96, lambda x: ("fail", "96 % belegt"), 0),
                         ("Auslastung", lambda: 1, lambda x: ("ok", "fein"), 0)])
        tid, _ = tasks.start(r, speak=False, wait=True)
        rep = tasks.get(tid)["report"]
        self.assertIn("1 von 2 Schritten in Ordnung", rep)
        self.assertIn("Speicherplatz — Problem: 96 % belegt", rep)
        self.assertIn("ich lösche nichts selbst", rep)
        self.assertIn("ohne Ihre Freigabe", rep)

    def test_retry_then_success(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 2:
                raise ConnectionError("kurz weg")
            return "da"
        r = self.recipe([("Flaky", flaky, lambda x: ("ok", x), 1)])
        tid, _ = tasks.start(r, speak=False, wait=True)
        step = tasks.get(tid)["steps"][0]
        self.assertEqual((step["status"], step["attempts"]), ("ok", 2))

    def test_not_configured_is_not_retried(self):
        calls = {"n": 0}

        def missing():
            calls["n"] += 1
            raise LookupError("NAS nicht verbunden")
        r = self.recipe([("NAS", missing, lambda x: ("ok", ""), 3)])
        tid, _ = tasks.start(r, speak=False, wait=True)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(tasks.get(tid)["status"], "FAILED")
        self.assertIn("nicht verfügbar", tasks.get(tid)["steps"][0]["finding"])

    def test_timeout(self):
        r = self.recipe([("Langsam", lambda: time.sleep(2), lambda x: ("ok", ""), 0)])
        tid, _ = tasks.start(r, speak=False, wait=True, step_timeout=0.2)
        self.assertEqual(tasks.get(tid)["steps"][0]["status"], "timeout")
        self.assertEqual(tasks.get(tid)["status"], "TIMED_OUT")

    def test_shared_source_asked_once(self):
        calls = {"n": 0}

        def src():
            calls["n"] += 1
            return {"v": 1}
        r = self.recipe([("A", src, lambda x: ("ok", ""), 0), ("B", src, lambda x: ("ok", ""), 0)])
        tasks.start(r, speak=False, wait=True)
        self.assertEqual(calls["n"], 1)

    def test_cancel_and_no_duplicate(self):
        gate = threading.Event()
        r = self.recipe([("Warten", lambda: gate.wait(5), lambda x: ("ok", ""), 0),
                         ("Danach", lambda: 1, lambda x: ("ok", ""), 0)])
        tid, _ = tasks.start(r, speak=False)
        time.sleep(0.2)
        same, msg = tasks.start(r, speak=False)
        self.assertEqual(same, tid)
        self.assertIn("läuft schon", msg)
        self.assertIn("1 Prüfung", tasks.cancel())
        gate.set()
        for _ in range(50):
            if tasks.get(tid)["status"] == "CANCELLED":
                break
            time.sleep(0.1)
        t = tasks.get(tid)
        self.assertEqual(t["status"], "CANCELLED")
        self.assertEqual(t["steps"][1]["status"], "pending")

    def test_progress_events(self):
        r = self.recipe([("A", lambda: 1, lambda x: ("ok", ""), 0)])
        before = len(events._LOG)
        tasks.start(r, speak=True, wait=True)
        kinds = [e["t"] for e in events._LOG[before:]]
        self.assertIn("task", kinds)
        self.assertEqual(kinds[-1], "say")

    def test_check_error_does_not_crash(self):
        r = self.recipe([("Kaputt", lambda: None, lambda x: x["fehlt"], 0)])
        tid, _ = tasks.start(r, speak=False, wait=True)
        self.assertIn("Prüfung fehlgeschlagen", tasks.get(tid)["steps"][0]["finding"])


class Recipes(unittest.TestCase):
    def test_nas_checks(self):
        info = {"model": "DS920+", "ok": True, "disks": 4, "used_pct": 88, "used_tb": 7, "total_tb": 8, "temp": 41.0}
        self.assertEqual(tasks._nas_health(info)[0], "ok")
        self.assertEqual(tasks._nas_space(info)[0], "warn")
        self.assertEqual(tasks._nas_health(dict(info, ok=False))[0], "fail")

    def test_pi_services(self):
        r = {"services": [("docker", "active")], "containers": [("ha", "Up 2 days"), ("z2m", "Exited (1)")]}
        status, text = tasks._pi_services(r)
        self.assertEqual(status, "fail")
        self.assertIn("z2m", text)

    def test_unknown_recipe(self):
        self.assertIsNone(tasks.start("toaster")[0])


class Trigger(unittest.TestCase):
    def setUp(self):
        self._start = tasks.start
        self.started = []
        tasks.start = lambda recipe, speak=True, **kw: (self.started.append(recipe) or ("x", f"{recipe} gestartet"))

    def tearDown(self):
        tasks.start = self._start

    def test_phrases(self):
        for text, recipe in (("Prüf mein NAS", "nas"), ("Jarvis, prüfe den Pi", "pi"), ("Systemcheck", "system"),
                             ("Check mal das Netzwerk", "netz"), ("Prüf den PC", "pc")):
            self.assertEqual(router.route(text)["kind"], "local", text)
        self.assertEqual(self.started, ["nas", "pi", "system", "netz", "pc"])

    def test_questions_are_not_checks(self):
        self.assertEqual(router.route("Prüf mal, ob im Wohnzimmer das Licht an ist")["kind"], "llm")
        self.assertEqual(self.started, [])

    def test_read_only_classes(self):
        self.assertEqual(policy.action_class("task_run"), policy.READ)
        self.assertIn("task_cancel", runtime.NO_CONFIRM)


if __name__ == "__main__":
    unittest.main()
