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
        self.assertEqual(set(n), {"items", "incidents", "proposals", "paused_until", "persona"})
        self.assertEqual(n["items"][0]["text"], "Die Waschmaschine ist fertig.")
        self.assertEqual(n["items"][0]["kind"], "appliance")
        self.assertIn(pid, [p["id"] for p in n["proposals"]])
        self.assertIsNotNone(n["paused_until"])
        proposals.decline(pid)

    def test_route_rows_in_ui(self):
        js = open(os.path.join(os.path.dirname(server.__file__), "ui", "app.js"), encoding="utf-8").read()
        for needle in ("case 'route':", "/api/notifications", "'Dieser PC'", "view-wall", "Wave.speech"):
            self.assertIn(needle, js)


if __name__ == "__main__":
    unittest.main()
