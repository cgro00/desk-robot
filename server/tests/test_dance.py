"""Dancing (brain/dance.py): four routines on a beat, inside the neck's range, ending straight ahead and happy."""

import asyncio
import unittest

from brain import dance
from brain.dance import DANCES, Dancer


class Robot:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, msg):
        self.sent.append(msg)


def play(name=None, tempo=40.0, ready=lambda: True):
    robot = Robot()
    d = Dancer(robot.send)
    d.tempo = tempo                           # a 15 s routine in well under a second
    d.ready = ready

    async def go():
        picked = d.start(name)
        while d.dancing:
            await asyncio.sleep(0.01)
        return picked
    return asyncio.run(go()), robot.sent


class DanceTests(unittest.TestCase):
    def test_four_routines_of_about_fifteen_seconds(self):
        self.assertEqual(sorted(DANCES), ["disco", "groove", "robot", "sprinkler"])
        for name in DANCES:
            self.assertTrue(8 <= dance.length(name) <= 20, (name, dance.length(name)))

    def test_each_stays_in_range_and_ends_level_and_happy(self):
        for name in DANCES:
            picked, sent = play(name)
            self.assertEqual(picked, name)
            moves = [m for m in sent if m["type"] in ("pan", "tilt")]
            self.assertGreater(len(moves), 10, name)
            for m in moves:
                lo, hi = (-dance.PAN_MAX, dance.PAN_MAX) if m["type"] == "pan" else (dance.TILT_MIN, dance.TILT_MAX)
                self.assertTrue(lo <= m["deg"] <= hi, (name, m))
            last = {m["type"]: m["deg"] for m in moves}
            self.assertEqual((last["pan"], last["tilt"]), (0.0, 0.0), name)
            self.assertEqual(sent[-1], {"type": "emotion", "name": "happy"}, name)
            # fast neck for the dance, everyday speeds after it
            motion = [m for m in sent if m["type"] == "motion"]
            self.assertEqual([m["speed"] for m in motion[:2]], [dance.DANCE_SPEED] * 2, name)
            self.assertEqual({m["axis"]: m["speed"] for m in motion[2:]},
                             {"pan": dance.config.TRACK_PAN_SPEED, "tilt": dance.config.TRACK_TILT_SPEED}, name)
            first_move = next(i for i, m in enumerate(sent) if m["type"] in ("pan", "tilt"))
            self.assertTrue(all(m["type"] == "motion" for m in sent[:2]) and first_move >= 2, name)

    def test_a_random_one_when_none_is_named(self):
        seen = {play(tempo=400.0)[0] for _ in range(30)}
        self.assertTrue(seen <= set(DANCES))
        self.assertGreater(len(seen), 1)

    def test_waits_for_his_line(self):
        said = {"done": False}
        robot = Robot()
        d = Dancer(robot.send)
        d.tempo, d.ready = 40.0, lambda: said["done"]

        async def go():
            d.start("robot")
            await asyncio.sleep(0.3)
            before = len(robot.sent)
            said["done"] = True
            while d.dancing:
                await asyncio.sleep(0.01)
            return before
        self.assertEqual(asyncio.run(go()), 0)          # nothing moved while he was still talking
        self.assertTrue(robot.sent)

    def test_stop_sends_no_more_moves(self):
        robot = Robot()
        d = Dancer(robot.send)
        d.tempo = 2.0

        async def go():
            d.start("groove")
            await asyncio.sleep(0.3)
            d.stop()
            n = len(robot.sent)
            await asyncio.sleep(0.4)
            return n
        n = asyncio.run(go())
        after = robot.sent[n:]
        self.assertTrue(all(m["type"] == "motion" for m in after), after)   # no more moves or faces...
        # ...only the everyday speeds put back, though it was cut short
        speeds = [m for m in robot.sent if m["type"] == "motion"]
        self.assertEqual(speeds[-1]["speed"], dance.config.TRACK_TILT_SPEED)


if __name__ == "__main__":
    unittest.main()
