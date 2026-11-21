"""Timers: set, check, cancel and ring once, on a fake clock."""

import unittest

from brain import timers as T


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class TimerTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.t = T.Timers(clock=self.clock)

    def test_set_check_and_ring_once(self):
        self.assertEqual(self.t.ability({"action": "set", "minutes": 10, "label": "pasta"}),
                         "Timer set (pasta): 10 minutes.")
        self.clock.now += 125
        self.assertEqual(self.t.check(), "pasta: 7 minutes 55 seconds left.")
        self.assertEqual(self.t.due(), [])
        self.clock.now += 600
        done = self.t.due()
        self.assertEqual([d["label"] for d in done], ["pasta"])
        self.assertEqual(self.t.due(), [])          # rings once
        self.assertEqual(self.t.check(), "No timers running.")

    def test_cancel_by_label_soonest_and_all(self):
        self.t.set(60, "tea")
        self.t.set(30, "eggs")
        self.t.set(90)
        self.assertEqual(self.t.cancel("TEA"), "Cancelled the tea timer.")
        self.assertEqual(self.t.cancel(), "Cancelled the eggs timer.")      # the soonest
        self.assertIn("No timer called pizza", self.t.cancel("pizza"))
        self.assertEqual(self.t.cancel("all"), "Cancelled 1 timer.")

    def test_limits(self):
        self.assertTrue(self.t.ability({"action": "set"}).startswith("Not done"))
        self.assertTrue(self.t.ability({"action": "set", "hours": 25}).startswith("Not done"))
        self.assertTrue(self.t.ability({"action": "set", "minutes": "abc"}).startswith("Not done"))
        self.assertTrue(self.t.ability({"action": "explode"}).startswith("Not done"))
        for _ in range(T.MAX_TIMERS):
            self.t.set(5)
        self.assertIn("already", self.t.ability({"action": "set", "seconds": 5}))

    def test_spoken(self):
        self.assertEqual(T.spoken(3661), "1 hour 1 minute 1 second")
        self.assertEqual(T.spoken(0.2), "0 seconds")


class SoonestTests(unittest.TestCase):
    def test_soonest_for_the_screen(self):
        clock = Clock()
        t = T.Timers(clock=clock)
        self.assertIsNone(t.soonest())
        t.set(300, "tea")
        t.set(60, "eggs")
        self.assertEqual(t.soonest(), {"left": 60, "label": "eggs"})
        clock.now += 59.6
        self.assertEqual(t.soonest(), {"left": 1, "label": "eggs"})   # never 0 while running
