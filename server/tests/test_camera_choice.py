"""Which camera he sees through: the dashboard's choice, the robot coming and going, and snapshots from this
computer's camera. No real camera: a stand-in for eyes.LocalCamera."""

import unittest
from unittest.mock import patch

from brain import config, main, settings


def _tiny_jpeg() -> bytes:
    """A 64-wide, 48-tall JPEG (asymmetric, so a turn shows up in the shape)."""
    import cv2
    import numpy as np
    img = np.zeros((48, 64, 3), np.uint8)
    img[:24] = 255        # top half white: up and down are told apart
    return cv2.imencode(".jpg", img)[1].tobytes()


class FakeCamera:
    """Stands in for eyes.LocalCamera."""
    instances = []

    def __init__(self, eyes):
        self.index = None
        self.running = False
        self.device_name = ""
        self.error = None
        self.stopped = False
        FakeCamera.instances.append(self)

    def start(self):
        self.running = True
        self.device_name = f"camera {self.index if self.index is not None else 0}"
        return self.device_name

    def stop(self):
        self.running = False
        self.stopped = True


class CameraChoiceTests(unittest.TestCase):
    def setUp(self):
        self.saved = (config.CAMERA_SOURCE, config.CAMERA_LOCAL_INDEX, main.camera, main.robot_socket)
        main.camera = None
        main.robot_socket = None
        FakeCamera.instances = []
        self.patch = patch.object(main, "LocalCamera", FakeCamera)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        config.CAMERA_SOURCE, config.CAMERA_LOCAL_INDEX, main.camera, main.robot_socket = self.saved

    def choose(self, source, index="", local_rotate=0):
        s = settings.defaults()
        s["vision"].update(camera=source, camera_index=index, camera_rotate=local_rotate)
        main.apply_settings(s)

    def test_camera_map_to_config(self):
        self.choose("mac", "1", 90)
        self.assertEqual((config.CAMERA_SOURCE, config.CAMERA_LOCAL_INDEX, config.CAMERA_LOCAL_ROTATE), ("mac", "1", 90))
        config.CAMERA_SOURCE = "mac"   # as if CAMERA_SOURCE=mac in .env with the dashboard untouched
        with patch.dict("os.environ", {"CAMERA_LOCAL_INDEX": "2"}):
            self.choose("robot")
            self.assertEqual((config.CAMERA_SOURCE, config.CAMERA_LOCAL_INDEX), ("mac", "2"))
        with self.assertRaises(ValueError):
            settings.clean_value("vision", "camera", "phone")

    def test_defaults_keep_the_robot_path_untouched(self):
        # Everything as a fresh checkout: no computer camera runs...
        main.apply_settings(settings.defaults())
        main.robot_socket = None
        main.pick_camera_source()
        self.assertIsNone(main.camera)
        # ...and the robot's frames keep turning by the robot's own setting (270° on a V2 head),
        # not by this computer's (0°): a 64-wide x 48-tall frame comes back tall.
        saved = (config.CAMERA_ROTATE, config.CAMERA_LOCAL_ROTATE)
        try:
            config.CAMERA_ROTATE, config.CAMERA_LOCAL_ROTATE = 270, 0
            main.eyes.jpeg, main.eyes.frame_at = b"", 0.0
            main.eyes.push_frame(_tiny_jpeg())
            import cv2
            import numpy as np
            got = cv2.imdecode(np.frombuffer(main.eyes.latest(max_age=5.0), np.uint8), cv2.IMREAD_COLOR)
            self.assertEqual(got.shape[:2], (64, 48))   # was 48 tall x 64 wide: the robot's turn happened
        finally:
            config.CAMERA_ROTATE, config.CAMERA_LOCAL_ROTATE = saved

    def test_camera_runs_only_for_this_computer_without_a_robot(self):
        for source, robot, want in (("mac", False, True), ("mac", True, False), ("robot", False, False)):
            main.camera = None
            config.CAMERA_SOURCE = "robot"   # so the previous choice can't carry over (see precedence note)
            self.choose(source)
            main.robot_socket = object() if robot else None
            main.pick_camera_source()
            self.assertEqual(main.camera is not None and main.camera.running, want, (source, robot))

    def test_robot_connecting_stops_it_disconnecting_starts_it(self):
        self.choose("mac")
        main.pick_camera_source()
        cam = FakeCamera.instances[0]
        main.robot_socket = object()
        main.pick_camera_source()
        self.assertTrue(cam.stopped)
        self.assertFalse(main.camera.running if main.camera else False)
        main.robot_socket = None
        main.pick_camera_source()
        self.assertTrue(main.camera.running)

    def test_index_change_reopens(self):
        self.choose("mac")
        main.pick_camera_source()
        self.choose("mac", "1")
        main.pick_camera_source()
        self.assertEqual(main.camera.index, 1)
        self.assertTrue(main.camera.running)

    def test_a_camera_that_wont_open_is_reported_not_fatal(self):
        class BrokenCamera(FakeCamera):
            def start(self):
                raise RuntimeError("no camera could be opened")

        with patch.object(main, "LocalCamera", BrokenCamera):
            self.choose("mac")
            main.pick_camera_source()   # must not raise
        self.assertIsNotNone(main.camera)
        self.assertFalse(main.camera.running)
        self.assertIn("no camera", main.camera.error)


if __name__ == "__main__":
    unittest.main()
