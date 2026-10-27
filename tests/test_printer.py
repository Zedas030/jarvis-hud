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
    values = {"druckstatus": status, "druckfortschritt": "0", "verbleibende_zeit": "0.0",
              "endzeit": "unknown", "startzeit": "2026-10-24T18:00:00+00:00", "aktuelle_schicht": "0",
              "gesamtzahl_der_schichten": "0", "name_der_aufgabe": "Halter", "aktueller_arbeitsschritt": "idle",
              "temperatur_der_duse": "22", "zieltemperatur_der_duse": "0", "druckbetttemperatur": "18",
              "zieltemperatur_vom_druckbett": "0", "temperatur_im_druckraum": "21"}
    values.update((k, v) for k, v in over.items() if k not in ("online", "druckfehler", "hms_fehler", "hms_attr"))
    out =[{"entity_id": f"sensor.{P}_{k}", "state": v, "attributes": {}} for k, v in values.items()]
    out += [{"entity_id": f"binary_sensor.{P}_online", "state": over.get("online", "on"), "attributes": {}},
            {"entity_id": f"binary_sensor.{P}_druckfehler", "state": over.get("druckfehler", "off"), "attributes": {}},
            {"entity_id": f"binary_sensor.{P}_hms_fehler", "state": over.get("hms_fehler", "off"),
             "attributes": over.get("hms_attr") or {"Count": 1 if over.get("hms_fehler") == "on" else 0}},
            {"entity_id": f"sensor.{P}_ams_1_slot_1", "state": "Bambu PLA Basic",
             "attributes": {"remain": 12, "empty": False}},
            {"entity_id": f"sensor.{P}_ams_1_slot_2", "state": "Bambu PLA Silk", "attributes": {"remain": -1}},
            {"entity_id": f"sensor.{P}_ams_1_slot_4", "state": "?", "attributes": {"empty": True}},
            {"entity_id": f"sensor.{P}_ams_1_luftfeuchtigkeit", "state": "29", "attributes": {}},
            {"entity_id": "light.wohnzimmer", "state": "on", "attributes": {}}]
    return out


RUNNING = dict(druckfortschritt="42", verbleibende_zeit="1.3333", aktuelle_schicht="120",
               gesamtzahl_der_schichten="300", zieltemperatur_der_duse="220", temperatur_der_duse="219")
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
        self.assertEqual(printer.describe(states()), "Der Drucker ist frei, Sir.")

    def test_printing(self):
        text = printer.describe(states("running", **RUNNING))
        self.assertIn("druckt „Halter“: 42 Prozent, Schicht 120 von 300", text)
        self.assertIn("Noch etwa 1 Stunde 20 Minuten", text)

    def test_paused_with_reason(self):
        text = printer.describe(states("pause", aktueller_arbeitsschritt="paused_filament_runout", **RUNNING))
        self.assertIn("pausiert", text)
        self.assertIn("das Filament ist leer", text)

    def test_offline_and_error(self):
        self.assertEqual(printer.describe(states(online="off")), "Der Drucker ist offline, Sir.")
        self.assertIn("Bambu-App", printer.describe(states("failed", hms_fehler="on")))

    def test_filament(self):
        text = printer.describe(states(), " welches filament ist im ams ")
        self.assertIn("Slot 1 Bambu PLA Basic (12 Prozent übrig)", text)
        self.assertIn("Slot 2 Bambu PLA Silk,", text)          # no remain reading: no percent
        self.assertIn("Slot 4 leer", text)
        self.assertIn("Luftfeuchtigkeit 29 Prozent", text)

    def test_temperatures(self):
        text = printer.describe(states("running", **RUNNING), " wie heiß ist die düse ")
        self.assertIn("Düse 219 Grad von 220", text)


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
        self.assertEqual(said, ["Sir, der 3D-Druck „Halter“ ist fertig."])

    def test_failed(self):
        said = self.run_through(states("running"), states("failed"))
        self.assertIn("fehlgeschlagen", said[0])

    def test_pause_with_reason_but_not_by_hand(self):
        said = self.run_through(states("running"), states("pause", aktueller_arbeitsschritt="paused_first_layer_error"))
        self.assertEqual(said, ["Sir, der 3D-Drucker hat angehalten: die erste Schicht sieht fehlerhaft aus."])
        self.said.clear()
        printer._LAST.clear()
        self.assertEqual(self.run_through(states("running"), states("pause", aktueller_arbeitsschritt="paused_user")), [])

    def test_error_once_per_rise(self):
        said = self.run_through(states("running"), states("running", hms_fehler="on", hms_attr=REAL),
                                states("running", hms_fehler="on", hms_attr=REAL))
        self.assertEqual(said, ["Sir, der 3D-Drucker meldet einen Fehler: The AMS1 slot 2 filament has run out."])

    def test_first_layer_scan_is_no_error(self):
        said = self.run_through(states("running"), states("running", hms_fehler="on", hms_attr=SCAN),
                                states("running"), states("running", hms_fehler="on", hms_attr=INFO))
        self.assertEqual(said, [])
        self.assertNotIn("Fehler", printer.describe(states("running", hms_fehler="on", hms_attr=SCAN, **RUNNING)))

    def test_real_error_during_scan_still_comes(self):
        both = {"Count": 2, **SCAN, "2-Error": REAL["1-Error"], "2-Severity": "common"}
        said = self.run_through(states("running"), states("running", hms_fehler="on", hms_attr=SCAN),
                                states("running", hms_fehler="on", hms_attr=both))
        self.assertEqual(len(said), 1)
        self.assertIn("filament has run out", said[0])

    def test_error_without_details_skipped_once_per_print(self):
        said = self.run_through(states("running"), states("running", hms_fehler="on"), states("running"),
                                states("running", hms_fehler="on"))
        self.assertEqual(said, ["Sir, der 3D-Drucker meldet einen Fehler. Die Details stehen in der Bambu-App."])
        self.said.clear()               # a new print: its scan is skipped again
        self.run_through(states("running", startzeit="2026-10-25T09:00:00+00:00"),
                         states("running", hms_fehler="on", startzeit="2026-10-25T09:00:00+00:00"))
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
        for q in ("Wie weit ist der Drucker?", "Ist der 3D-Drucker fertig?", "Welches Filament ist im AMS?",
                  "Was druckt der Bambu gerade?", "Wie heiß ist die Düse?"):
            self.assertIsNotNone(printer.handle(router._norm(q)), q)

    def test_not_for_other_meanings(self):
        for q in ("Mach das Licht am Drucker aus", "Druck mir das PDF aus", "Wie hoch ist der Luftdruck?",
                  "Stopp den Drucker"):
            self.assertIsNone(printer.handle(router._norm(q)), q)

    def test_model_path_gets_the_home_tools(self):
        self.assertIn("home", router.llm_route("Erklär mir, warum das Filament im AMS feucht wird")["groups"])


if __name__ == "__main__":
    unittest.main()
