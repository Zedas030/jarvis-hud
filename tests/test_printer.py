"""
3D printer from Home Assistant (ha-bambulab) — states faked; no HA, no printer.

    .venv\\Scripts\\python.exe -m unittest tests.test_printer -v
"""
import isolate  # noqa: F401  (must come first)
import unittest

import briefing
import printer
import router

P = "x1c_00m09d490200379"


def states(status="idle", **over):
    values = {"print_status": status, "print_progress": "0", "remaining_time": "0.0",
              "end_time": "unknown", "start_time": "2026-10-24T18:00:00+00:00", "current_layer": "0",
              "total_layer_count": "0", "task_name": "Bracket", "current_stage": "idle",
              "nozzle_temperature": "22", "nozzle_target_temperature": "0", "bed_temperature": "18",
              "target_bed_temperature": "0", "chamber_temperature": "21"}
    values.update((k, v) for k, v in over.items() if k not in ("online", "print_error", "hms_errors", "hms_attr"))
    out =[{"entity_id": f"sensor.{P}_{k}", "state": v, "attributes": {}} for k, v in values.items()]
    out += [{"entity_id": f"binary_sensor.{P}_online", "state": over.get("online", "on"), "attributes": {}},
            {"entity_id": f"binary_sensor.{P}_print_error", "state": over.get("print_error", "off"), "attributes": {}},
            {"entity_id": f"binary_sensor.{P}_hms_errors", "state": over.get("hms_errors", "off"),
             "attributes": over.get("hms_attr") or {"Count": 1 if over.get("hms_errors") == "on" else 0}},
            {"entity_id": f"sensor.{P}_ams_1_slot_1", "state": "Bambu PLA Basic",
             "attributes": {"remain": 12, "empty": False}},
            {"entity_id": f"sensor.{P}_ams_1_slot_2", "state": "Bambu PLA Silk", "attributes": {"remain": -1}},
            {"entity_id": f"sensor.{P}_ams_1_slot_4", "state": "?", "attributes": {"empty": True}},
            {"entity_id": f"sensor.{P}_ams_1_humidity", "state": "29", "attributes": {}},
            {"entity_id": "light.living_room", "state": "on", "attributes": {}}]
    return out


RUNNING = dict(print_progress="42", remaining_time="1.3333", current_layer="120",
               total_layer_count="300", nozzle_target_temperature="220", nozzle_temperature="219")
# HMS attributes as ha-bambulab sets them
SCAN = {"Count": 1, "1-Error": "HMS_0C00_0300_0003_0008: The first layer inspection is in progress.",
        "1-Severity": "common"}
INFO = {"Count": 1, "1-Error": "HMS_0500_0400_0003_0001: Calibrating.", "1-Severity": "info"}
REAL = {"Count": 1, "1-Error": "The AMS1 slot 2 filament has run out.", "1-Severity": "serious"}


class Describe(unittest.TestCase):
    def test_found_by_its_status_sensor(self):
        p = printer.find(states())
        self.assertEqual((p["prefix"], p["status"], p["nozzle"]), (P, "idle", "22"))
        self.assertIsNone(printer.find([{"entity_id": "light.x", "state": "on"}]))

    def test_idle(self):
        self.assertEqual(printer.describe(states()), "The printer is free, Sir.")

    def test_printing(self):
        text = printer.describe(states("running", **RUNNING))
        self.assertIn("is printing “Bracket”: 42 percent, layer 120 of 300", text)
        self.assertIn("About 1 hour 20 minutes to go", text)

    def test_paused_with_reason(self):
        text = printer.describe(states("pause", current_stage="paused_filament_runout", **RUNNING))
        self.assertIn("is paused at", text)
        self.assertIn("the filament has run out", text)

    def test_offline_and_error(self):
        self.assertEqual(printer.describe(states(online="off")), "The printer is offline, Sir.")
        self.assertIn("Bambu app", printer.describe(states("failed", hms_errors="on")))

    def test_filament(self):
        text = printer.describe(states(), " which filament is in the ams ")
        self.assertIn("Slot 1 Bambu PLA Basic (12 percent left)", text)
        self.assertIn("Slot 2 Bambu PLA Silk,", text)          # no remain reading: no percent
        self.assertIn("Slot 4 empty", text)
        self.assertIn("Humidity 29 percent", text)

    def test_temperatures(self):
        text = printer.describe(states("running", **RUNNING), " how hot is the nozzle ")
        self.assertIn("nozzle 219 degrees of 220", text)


class Watch(unittest.TestCase):
    def setUp(self):
        printer._LAST.clear()
        self.said = []
        self.say = lambda key, text: self.said.append((key, text))

    def run_through(self, *rounds):
        for s in rounds:
            printer.watch(s, self.say)
        return [t for _, t in self.said]

    def test_first_round_only_remembers(self):
        self.assertEqual(self.run_through(states("finish")), [])

    def test_done(self):
        said = self.run_through(states("running", **RUNNING), states("running", **RUNNING), states("finish"))
        self.assertEqual(said, ["Sir, the 3D print “Bracket” is finished."])

    def test_failed(self):
        said = self.run_through(states("running"), states("failed"))
        self.assertIn("failed", said[0])

    def test_pause_with_reason_but_not_by_hand(self):
        said = self.run_through(states("running"), states("pause", current_stage="paused_first_layer_error"))
        self.assertEqual(said, ["Sir, the 3D printer has stopped: the first layer looks faulty."])
        self.said.clear()
        printer._LAST.clear()
        self.assertEqual(self.run_through(states("running"), states("pause", current_stage="paused_user")), [])

    def test_error_once_per_rise(self):
        said = self.run_through(states("running"), states("running", hms_errors="on", hms_attr=REAL),
                                states("running", hms_errors="on", hms_attr=REAL))
        self.assertEqual(said, ["Sir, the 3D printer reports an error: The AMS1 slot 2 filament has run out."])

    def test_first_layer_scan_is_no_error(self):
        said = self.run_through(states("running"), states("running", hms_errors="on", hms_attr=SCAN),
                                states("running"), states("running", hms_errors="on", hms_attr=INFO))
        self.assertEqual(said, [])
        self.assertNotIn("error", printer.describe(states("running", hms_errors="on", hms_attr=SCAN, **RUNNING)))

    def test_real_error_during_scan_still_comes(self):
        both = {"Count": 2, **SCAN, "2-Error": REAL["1-Error"], "2-Severity": "common"}
        said = self.run_through(states("running"), states("running", hms_errors="on", hms_attr=SCAN),
                                states("running", hms_errors="on", hms_attr=both))
        self.assertEqual(len(said), 1)
        self.assertIn("filament has run out", said[0])

    def test_error_without_details_skipped_once_per_print(self):
        said = self.run_through(states("running"), states("running", hms_errors="on"), states("running"),
                                states("running", hms_errors="on"))
        self.assertEqual(said, ["Sir, the 3D printer reports an error. The details are in the Bambu app."])
        self.said.clear()               # a new print: its scan is skipped again
        self.run_through(states("running", start_time="2026-10-25T09:00:00+00:00"),
                         states("running", hms_errors="on", start_time="2026-10-25T09:00:00+00:00"))
        self.assertEqual(self.said, [])

    def test_never_switches_anything(self):
        import inspect
        src = inspect.getsource(printer)
        self.assertNotIn("home_control", src)
        self.assertNotIn("/api/services", src)


class Routing(unittest.TestCase):
    def setUp(self):
        self.real = briefing._ha_states
        briefing._ha_states = lambda: states("running", **RUNNING)

    def tearDown(self):
        briefing._ha_states = self.real

    def test_printer_questions_are_local(self):
        for q in ("How far along is the printer?", "Is the 3D printer finished?", "Which filament is in the AMS?",
                  "What is the Bambu printing right now?", "How hot is the nozzle?"):
            self.assertIsNotNone(printer.handle(router._norm(q)), q)

    def test_not_for_other_meanings(self):
        for q in ("Turn off the light by the printer", "Print out the PDF for me", "How high is the air pressure?",
                  "Stop the printer"):
            self.assertIsNone(printer.handle(router._norm(q)), q)

    def test_model_path_gets_the_home_tools(self):
        self.assertIn("home", router.llm_route("Explain to me why the filament in the AMS gets damp")["groups"])


if __name__ == "__main__":
    unittest.main()
