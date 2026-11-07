"""Face packs: the built-in ones load, bad packs are refused, custom packs save and delete. No network."""

import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from brain import config, faces


class FacePackTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.patch = patch.object(faces, "CUSTOM_DIR", Path(self.dir.name) / "custom")
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.dir.cleanup()

    def test_builtin_packs_load_with_every_emotion(self):
        packs = {p["id"]: p for p in faces.list_packs()}
        self.assertIn("rocky", packs)
        for pid, p in packs.items():
            self.assertNotIn("error", p, pid)
            self.assertEqual(set(p["pack"]["emotions"]), set(faces.EMOTIONS), pid)

    def test_emotions_match_the_firmware(self):
        src = (Path(__file__).resolve().parents[2] / "firmware" / "src" / "face.cpp").read_text()
        names = re.search(r"kEmotionNames\[\] = \{(.*?)\};", src, re.S).group(1)
        self.assertEqual(re.findall(r'"(\w+)"', names), faces.EMOTIONS)
        self.assertEqual(config.EMOTIONS, faces.EMOTIONS)

    def test_bad_packs_are_refused(self):
        for bad in ({"emotions": {"grumpy": {}}}, {"emotions": {"happy": {"w": 500}}},
                    {"emotions": {"happy": {"fx": ["lasers"]}}}, {"mouth": "huge"}, {"glint": "yes"}, []):
            with self.assertRaises(ValueError, msg=bad):
                faces.clean(bad)

    def test_save_and_delete_custom(self):
        pid = faces.save_custom({"name": "My Face!", "emotions": {"happy": {"w": 40, "right": {"w": 30}}}})
        self.assertEqual(pid, "custom/my-face")
        self.assertEqual(faces.load(pid)["emotions"]["happy"]["right"], {"w": 30})
        self.assertEqual(faces.save_custom({"name": "My Face!"}), "custom/my-face-2")   # names don't collide
        with self.assertRaises(ValueError):
            faces.save_custom({"name": "x"}, "rocky")                                    # built-ins are read-only
        faces.delete_custom(pid)
        self.assertEqual(faces.load_or_default(pid)[0], "rocky")                        # gone: Rocky's own face

    def test_robot_message_drops_the_words(self):
        msg = faces.robot_message(faces.load("rocky"), save=False)
        self.assertEqual((msg["type"], msg["save"]), ("face", False))
        self.assertNotIn("description", msg["pack"])


if __name__ == "__main__":
    unittest.main()
