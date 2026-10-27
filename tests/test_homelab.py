"""
Homelab figures — Prometheus and Pi-hole are faked; no network.

    .venv\\Scripts\\python.exe -m unittest tests.test_homelab -v
"""
import isolate  # noqa: F401  (must come first)
import unittest

import homelab
import policy
import router


class Homelab(unittest.TestCase):
    def setUp(self):
        self._value, self._values, self._pihole = homelab.value, homelab.values, homelab.pihole_data

    def tearDown(self):
        homelab.value, homelab.values, homelab.pihole_data = self._value, self._values, self._pihole

    def test_tools_only_read(self):
        for t in ("homelab_status", "metrics_query", "pihole_status"):
            self.assertEqual(policy.action_class(t), policy.READ, t)

    def test_router_brings_homelab_tools(self):
        for text in ("Jarvis, wie schnell ist das Internet gerade?", "Wie viel hat der Pi-hole heute blockiert?",
                     "Wann war die CPU heute am höchsten?"):
            self.assertIn("homelab", router.llm_route(text)["groups"], text)

    def test_internet_uses_the_rate_gauge_not_the_wrapping_counter(self):
        seen = []
        homelab.value = lambda expr: seen.append(expr) or 1.0
        homelab.internet()
        self.assertTrue(any("fritz_wan_datarate_bytes" in e for e in seen))
        self.assertFalse(any("rate(fritz_wan_data_bytes_total" in e for e in seen),
                         "the byte counter wraps: rate() over it reported 26 Gbit/s")

    def test_one_dead_exporter_does_not_hide_the_rest(self):
        def value(expr):
            if "windows_" in expr:
                raise RuntimeError("windows_exporter down")
            return 42.0
        homelab.value = value
        homelab.values = lambda expr: []
        text = homelab.describe("alles")
        self.assertIn("pc: nicht lesbar", text)
        self.assertIn("pi: 42", text)

    def test_unknown_topic(self):
        self.assertIn("Unbekannt", homelab.describe("toaster"))

    def test_tile_survives_missing_pihole_password(self):
        homelab.value = lambda expr: 1.0

        def no_password():
            raise RuntimeError("PIHOLE_PASSWORD fehlt in .env")
        homelab.pihole_data = no_password
        t = homelab.tile()
        self.assertEqual((t["down"], t["ph_pct"]), (1.0, None))
        self.assertIn("PIHOLE_PASSWORD", t["ph_error"])

    def test_pihole_lists_only_blocked_domains(self):
        homelab.pihole_data = lambda: {"total": 1000, "blocked": 120, "pct": 12.0, "clients": 9, "list": 150000,
                                       "blocking": "enabled", "top_blocked": [("ads.example", 50)],
                                       "top_clients": [("handy", 400)]}
        text = homelab.pihole()
        self.assertIn("Filter an", text)
        self.assertIn("ads.example", text)
        self.assertNotIn("erlaubt", text)


if __name__ == "__main__":
    unittest.main()
