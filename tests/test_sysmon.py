"""
Live strip figures — real psutil, faked GPU; nothing slow.

    .venv\\Scripts\\python.exe -m unittest tests.test_sysmon -v
"""
import isolate  # noqa: F401  (must come first)
import time
import unittest

import infra
import sysmon


class Sysmon(unittest.TestCase):
    def setUp(self):
        self._gpu = infra.gpu
        sysmon._GPU.update(t=0.0, row=None)
        sysmon._PREV.update(t=0.0, rx=0, tx=0)

    def tearDown(self):
        infra.gpu = self._gpu

    def test_without_nvidia_the_gpu_fields_are_empty(self):
        infra.gpu = lambda: []
        s = sysmon.sample()
        self.assertEqual((s["gpu"], s["vram"], s["gpu_temp"]), (None, None, None))

    def test_cpu_ignores_other_psutil_callers(self):
        # The dashboard calls psutil.cpu_percent() too; its module-wide baseline made the strip show 0 %.
        import psutil
        infra.gpu = lambda: []
        sysmon.sample()
        sum(i * i for i in range(300000))        # a little real work
        psutil.cpu_percent(interval=None)        # what the dashboard does in between
        cpu = sysmon.sample()["cpu"]
        self.assertIsNotNone(cpu)
        self.assertTrue(0 <= cpu <= 100)

    def test_network_rate_needs_a_previous_sample(self):
        infra.gpu = lambda: []
        self.assertIsNone(sysmon.sample()["net_down"], "first call: nothing to compare with")
        time.sleep(0.05)
        self.assertGreaterEqual(sysmon.sample()["net_down"], 0)

    def test_gpu_is_not_asked_on_every_poll(self):
        calls = []
        infra.gpu = lambda: calls.append(1) or [dict(name="x", util=50.0, vram_used=4.0, vram_total=8.0, temp=60.0)]
        sysmon.sample()
        sysmon.sample()
        self.assertEqual(len(calls), 1)
        self.assertEqual(sysmon.sample()["vram"], 50)


if __name__ == "__main__":
    unittest.main()
