"""Which mic he listens through: the dashboard's choice, the robot coming and going, an unplugged computer mic,
and whether the robot is told to stream its mic. No audio devices: a stand-in for Ears."""

import asyncio
import unittest
from unittest.mock import patch

from brain import config, main, settings


class FakeEars:
    def __init__(self, missing=None):
        self.source = "mac"
        self.device = None
        self.device_missing = missing
        self.device_name = "MacBook Pro Microphone"
        self.has_mac_mic = True

    def set_source(self, source):
        self.source = source

    def apply_config(self):
        pass


class MicChoiceTests(unittest.TestCase):
    def setUp(self):
        self.saved = (config.MIC_SOURCE, config.MIC_DEVICE, main.ears, main.robot_socket)
        self.sent = []

        async def send(msg):
            self.sent.append(msg)
        self.patch = patch.object(main, "send_to_robot", send)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        config.MIC_SOURCE, config.MIC_DEVICE, main.ears, main.robot_socket = self.saved

    def choose(self, mic, device=""):
        s = settings.defaults(); s["rocky"].update(mic=mic, mic_device=device)
        main.apply_settings(s)

    def test_settings_map_to_config(self):
        self.choose("computer", "Scarlett Solo USB")
        self.assertEqual((config.MIC_SOURCE, config.MIC_DEVICE), ("mac", "Scarlett Solo USB"))
        with patch.dict("os.environ", {"MIC_DEVICE": "From env"}):
            self.choose("auto")
            self.assertEqual((config.MIC_SOURCE, config.MIC_DEVICE), ("auto", "From env"))
        with self.assertRaises(ValueError):
            settings.clean_value("rocky", "mic", "phone")

    def test_pick_source(self):
        main.ears = FakeEars()
        for mic, robot, want in (("auto", True, "robot"), ("auto", False, "mac"), ("robot", False, "robot"),
                                 ("computer", True, "mac")):
            self.choose(mic)
            main.robot_socket = object() if robot else None
            main.pick_mic_source()
            self.assertEqual(main.ears.source, want, (mic, robot))

    def test_unplugged_computer_mic_counts_as_auto(self):
        main.ears = FakeEars(missing="Scarlett Solo USB")
        self.choose("computer", "Scarlett Solo USB")
        main.robot_socket = object()
        main.pick_mic_source()
        self.assertEqual(main.ears.source, "robot")

    def test_robot_streams_only_when_listened_to(self):
        main.ears = FakeEars(); main.robot_socket = object()
        self.choose("computer"); main.pick_mic_source(); asyncio.run(main.update_robot_mic())
        self.choose("auto"); main.pick_mic_source(); asyncio.run(main.update_robot_mic())
        self.assertEqual(self.sent, [{"type": "mic", "on": False}, {"type": "mic", "on": True}])


if __name__ == "__main__":
    unittest.main()
