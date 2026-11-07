"""Speech-boundary regressions; no microphone or downloaded models needed."""

import unittest
from unittest.mock import patch

import numpy as np

from brain import config
from brain.ears import Segmenter
from brain.turn import VAD_CHUNK


class SegmenterSensitivityTests(unittest.TestCase):
    def setUp(self):
        self.settings = patch.multiple(
            config,
            VAD_THRESHOLD=0.4,
            TURN_PAUSE_SECONDS=0.2,
            TURN_RECHECK_SECONDS=0.6,
            TURN_MAX_SILENCE=2.5,
            CANCEL_MIN_SPEECH=0.3,
        )
        self.settings.start()
        self.addCleanup(self.settings.stop)
        self.probability = 0.0
        self.starts = []
        self.segmenter = Segmenter(
            lambda chunk: self.probability,
            judge=None,
            on_speech_start=lambda: self.starts.append(True),
        )
        self.chunk = np.zeros(VAD_CHUNK, dtype=np.float32)

    def feed(self, probability, count=1):
        self.probability = probability
        turns = []
        for _ in range(count):
            turn = self.segmenter.push(self.chunk)
            if turn is not None:
                turns.append(turn)
        return turns

    def test_recent_sums_up_the_last_moment(self):
        self.assertEqual(self.segmenter.recent(0.25), (0.0, 0.0, False))
        self.feed(0.1, 3)
        level, prob, voice = self.segmenter.recent(0.25)
        self.assertAlmostEqual(prob, 0.1)
        self.assertFalse(voice)
        self.feed(0.45)                     # a voice starts...
        self.feed(0.3)                      # ...and a softer syllable still counts once it has
        self.feed(0.05)
        level, prob, voice = self.segmenter.recent(0.25)
        self.assertAlmostEqual(prob, 0.45)  # the highest score in the window, not the latest
        self.assertTrue(voice)

    def test_soft_syllables_continue_a_turn_but_do_not_start_one(self):
        self.assertEqual(self.feed(0.3, 20), [])
        self.assertFalse(self.segmenter.speaking)
        self.feed(0.45)
        self.assertTrue(self.segmenter.speaking)
        self.assertEqual(self.feed(0.3, 20), [])
        self.assertEqual(self.segmenter.quiet_run, 0)
        self.assertTrue(self.segmenter.talking)
        self.assertEqual(len(self.starts), 1)

        turns = self.feed(0.05, self.segmenter.pause_chunks)
        self.assertEqual(len(turns), 1)
        self.assertFalse(self.segmenter.speaking)
        self.feed(0.3, 20)
        self.assertFalse(self.segmenter.speaking)

    def test_live_threshold_changes_affect_an_open_turn(self):
        self.feed(0.45)
        config.VAD_THRESHOLD = 0.6
        self.feed(0.3)
        self.assertEqual(self.segmenter.quiet_run, 1)
        config.VAD_THRESHOLD = 0.4
        self.feed(0.3)
        self.assertEqual(self.segmenter.quiet_run, 0)

    def test_lowest_console_threshold_still_allows_silence_to_end_a_turn(self):
        config.VAD_THRESHOLD = 0.1
        self.feed(0.2, 20)
        self.assertTrue(self.segmenter.speaking)
        self.assertEqual(len(self.feed(0.0, self.segmenter.pause_chunks)), 1)
        self.assertFalse(self.segmenter.speaking)


if __name__ == "__main__":
    unittest.main()
