"""
HUD data: notification centre (/api/notifications) and the PC row of the dashboard.

    .venv\\Scripts\\python.exe -m unittest tests.test_hud -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import emergency   # noqa: E402
import events      # noqa: E402
import proposals   # noqa: E402
import server      # noqa: E402


class Notifications(unittest.TestCase):
    def tearDown(self):
        emergency.resume()

    def test_shape_and_content(self):
        events.publish("say", "Die Waschmaschine ist fertig.", kind="appliance")
        pid = proposals.add("idea", "Schlaf-Ampel", "Sie fragen oft nach dem Schlaf.", source="suggestions")
        emergency.pause(hours=1)
        n = server.notifications()
        self.assertEqual(set(n), {"items", "incidents", "proposals", "paused_until", "persona", "tasks"})
        self.assertEqual(n["items"][0]["text"], "Die Waschmaschine ist fertig.")
        self.assertEqual(n["items"][0]["kind"], "appliance")
        self.assertIn(pid, [p["id"] for p in n["proposals"]])
        self.assertIsNotNone(n["paused_until"])
        proposals.decline(pid)

    def test_route_rows_in_ui(self):
        js = open(os.path.join(os.path.dirname(server.__file__), "ui", "app.js"), encoding="utf-8").read()
        for needle in ("case 'route':", "/api/notifications", "'Dieser PC'", "view-wall", "Wave.speech"):
            self.assertIn(needle, js)



class AbortedRequests(unittest.TestCase):
    """A request the browser gave up on is no error worth a traceback; anything else still is."""
    def report(self, exc):
        import contextlib
        import io
        srv = server.Server(("127.0.0.1", 0), server.Handler)
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                try:
                    raise exc
                except Exception:  # noqa: BLE001
                    srv.handle_error(None, ("127.0.0.1", 1))
        finally:
            srv.server_close()
        return err.getvalue()

    def test_browser_gone_is_quiet(self):
        for exc in (ConnectionAbortedError(10053, "abgebrochen"), ConnectionResetError(), BrokenPipeError()):
            self.assertEqual(self.report(exc), "", type(exc).__name__)

    def test_real_errors_still_show(self):
        self.assertIn("ValueError", self.report(ValueError("kaputt")))

if __name__ == "__main__":
    unittest.main()
