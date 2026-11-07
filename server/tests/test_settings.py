"""Dashboard settings: validation, the file, and what the robot gets. No network."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from brain import settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "settings.json"
        self.patch = patch.object(settings, "PATH", self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.dir.cleanup()

    def test_no_file_gives_defaults(self):
        self.assertEqual(settings.load(), settings.defaults())

    def test_update_saves_and_merges(self):
        settings.update({"clock": {"hour_format": "24"}})
        s = settings.update({"weather": {"lat": 40.71, "lon": -74.01, "units": "c"}})
        self.assertEqual(s["clock"]["hour_format"], 24)
        self.assertEqual(json.loads(self.path.read_text())["weather"]["units"], "c")
        self.assertEqual(settings.load(), s)

    def test_bad_values_are_refused(self):
        for changes in ({"screen": {"brightness": 500}}, {"clock": {"hour_format": 13}},
                        {"screen": {"night_start": "25:00"}}, {"weather": {"show": "yes"}},
                        {"clock": {"timezone": "Nowhere/Atlantis"}}, {"nope": {"x": 1}}):
            with self.assertRaises(ValueError, msg=changes):
                settings.update(changes)
        self.assertFalse(self.path.exists())

    def test_bad_file_values_fall_back(self):
        self.path.write_text(json.dumps({"screen": {"brightness": "loud", "night_mode": "dark"}}))
        s = settings.load()
        self.assertEqual(s["screen"]["brightness"], 80)
        self.assertEqual(s["screen"]["night_mode"], "dark")

    def test_robot_payload(self):
        s = settings.defaults()
        s["clock"]["timezone"] = "America/New_York"
        p = settings.robot_payload(s)
        self.assertEqual(p["tz"], "EST5EDT,M3.2.0,M11.1.0")
        self.assertFalse(p["wx"])                     # no location yet: no weather
        s["weather"].update(lat=40.71283, lon=-74.00597)
        p = settings.robot_payload(s)
        self.assertTrue(p["wx"])
        self.assertEqual((p["lat"], p["lon"]), (40.71, -74.01))   # rounded to ~1 km
        self.assertEqual(p["idle"], "clock")          # the clock unless you pick a game
        s["screen"]["idle_screen"] = "tetris"
        self.assertEqual(settings.robot_payload(s)["idle"], "tetris")
        self.assertEqual(settings.clean_value("screen", "idle_screen", "roam"), "roam")
        with self.assertRaises(ValueError):
            settings.clean_value("screen", "idle_screen", "snake")


if __name__ == "__main__":
    unittest.main()
