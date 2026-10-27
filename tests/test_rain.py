import isolate  # noqa: F401 — first: temp state, no house

import unittest

import dashboard


def day(*probs, mm=0.0):
    """One day's hours from midnight: (hour, probability %, mm)."""
    return [(h, p, mm) for h, p in enumerate(probs)]


class RainDay(unittest.TestCase):
    def test_each_stretch_with_its_own_chance(self):
        hours = day(*([0] * 6 + [30, 88, 100, 100, 93, 45] + [15] * 11 + [18]))
        hours[23] = (23, 18, 0.1)                       # a little rain, small chance: still a rain hour
        r = dashboard.rain_day(hours)
        self.assertEqual(r["when"], "6–12 Uhr (30–100 %), 23–24 Uhr (18 %)")
        self.assertEqual(r["times"], "6–12, 23–24")

    def test_amount_counts_even_at_low_chance(self):
        hours = day(*([0] * 21 + [12, 18, 24]))
        hours[21:] = [(h, p, 0.5) for h, p, _ in hours[21:]]
        self.assertEqual(dashboard.rain_day(hours)["when"], "21–24 Uhr (12–24 %)")
        self.assertEqual(dashboard.rain_day(hours)["mm"], 1.5)

    def test_a_trace_at_no_chance_is_not_rain(self):
        hours = day(*([0] * 13 + [4] + [0] * 10), mm=0.1)
        self.assertEqual(dashboard.rain_day(hours)["when"], "")

    def test_all_day(self):
        self.assertEqual(dashboard.rain_day(day(*([90] * 12 + [40] * 12)))["when"], "ganztägig (40–90 %)")

    def test_no_rain_hour(self):
        r = dashboard.rain_day(day(*([0] * 16 + [25] + [0] * 7)))
        self.assertEqual((r["when"], r["times"], r["peak"]), ("", "", 25))
        self.assertEqual(dashboard.rain_day([])["when"], "")


if __name__ == "__main__":
    unittest.main()
