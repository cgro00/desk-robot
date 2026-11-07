"""Perception's note to the model and its wave, from hand-made detector state. No models, no camera."""

import time
import unittest
from types import SimpleNamespace

import numpy as np

from brain import perception as P


def face(**kw):
    f = {"at": time.time(), "smiling": False, "mouth_open": False, "eyes_closed": False, "looking": True,
         "distance_m": 1.1, "yaw": 3, "pitch": -2}
    f.update(kw)
    return f


class NoteTests(unittest.TestCase):
    def fresh(self):
        p = P.Perception(eyes=None)
        p.ready = True
        p._frame_at = time.time()
        return p

    def test_off_or_stale_says_nothing(self):
        p = P.Perception(eyes=None)
        self.assertEqual(p.describe(), "")              # models not loaded
        p.ready = True
        p._frame_at = time.time() - 10
        self.assertEqual(p.describe(), "")              # no live picture

    def test_a_face(self):
        p, now = self.fresh(), time.time()
        p._people.append((now, 1))
        p._face = face(smiling=True)
        self.assertEqual(p.describe(),
                         "(What your camera's face detector notices right now, may be wrong: "
                         "one person about 1 m away, looking at you, smiling.)")
        p._face = face(distance_m=0.3, looking=False)
        self.assertIn("one person very close, looking away", p.describe())

    def test_no_face_no_note(self):
        # it only sees faces turned to the camera, so it never claims "no one in view": the picture shows the rest
        self.assertEqual(self.fresh().describe(), "")

    def test_summary_has_no_objects_or_gestures(self):
        p, now = self.fresh(), time.time()
        p._waved_at = now
        s = p.summary()
        self.assertTrue(s["waving"])
        for gone in ("objects", "holding", "gesture"):
            self.assertNotIn(gone, s)


class FakeHands:
    """The gesture recognizer: an open palm whose wrist is at the x positions given, one per look."""
    def __init__(self, xs, category="Open_Palm"):
        self.xs, self.category = list(xs), category

    def recognize(self, frame):
        x = self.xs.pop(0)
        lm = [SimpleNamespace(x=x, y=0.5), SimpleNamespace(x=x + 0.05, y=0.6)]
        return SimpleNamespace(hand_landmarks=[lm], gestures=[[SimpleNamespace(category_name=self.category, score=0.9)]])


class WaveTests(unittest.TestCase):
    def run_looks(self, hands, awake=False):
        import cv2

        class Unused:
            def __getattr__(self, name):
                raise AssertionError("the face model ran while asleep")

        waves = []
        p = P.Perception(eyes=None, on_gesture=waves.append)
        p._gest, p._faces = hands, Unused()
        p.awake = lambda: awake
        for _ in range(len(hands.xs)):
            p._look(np.zeros((48, 64, 3), np.uint8), time.time(), cv2)
        return waves, p

    def test_an_open_hand_moving_side_to_side_is_a_wave(self):
        waves, p = self.run_looks(FakeHands([0.40, 0.50, 0.40, 0.50, 0.40]))
        self.assertEqual(waves, ["wave"])
        self.assertEqual([b[0] for b in p.boxes], ["hand"])

    def test_a_still_hand_or_another_sign_is_not(self):
        self.assertEqual(self.run_looks(FakeHands([0.40] * 6))[0], [])
        self.assertEqual(self.run_looks(FakeHands([0.40, 0.50, 0.40, 0.50, 0.40], "Thumb_Up"))[0], [])


if __name__ == "__main__":
    unittest.main()
