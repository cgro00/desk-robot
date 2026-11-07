"""Body language (brain/body.py): the moves it sends, that he always comes back, and when he must keep still."""

import asyncio
import unittest
from unittest.mock import patch

from brain import body as body_mod, config
from brain.body import Body
from brain.tracker import Motion


class FakeTracker:
    def __init__(self, pan=0.0, tilt=0.0):
        self.pan, self.tilt = pan, tilt
        self.motion = {"pan": Motion(config.TRACK_PAN_SPEED, config.TRACK_PAN_ACCEL),
                       "tilt": Motion(config.TRACK_TILT_SPEED, config.TRACK_TILT_ACCEL)}


class Robot:
    def __init__(self):
        self.sent: list[tuple[str, float]] = []

    async def send(self, msg):
        self.sent.append((msg["type"], msg["deg"]))

    def last(self, axis):
        return next((d for a, d in reversed(self.sent) if a == axis), None)


def run(coro):
    return asyncio.run(coro)


@patch.object(body_mod, "SWAY_EVERY", (0.05, 0.1))
@patch.object(body_mod, "SWAY_REST", 0.05)
@patch.object(config, "BODY_LANGUAGE", True)
class BodyTests(unittest.TestCase):
    def make(self, pan=0.0, tilt=0.0):
        robot, tracker = Robot(), FakeTracker(pan, tilt)
        return robot, tracker, Body(robot.send, tracker)

    def test_talks_then_comes_back_to_rest(self):
        robot, tracker, b = self.make(pan=20.0, tilt=0.0)

        async def go():
            b.speaking("happy")
            await asyncio.sleep(1.2)
            b.quiet()
            await asyncio.sleep(0.2)
        run(go())
        self.assertGreater(len(robot.sent), 3, "the bounce and some sway")
        self.assertEqual(robot.last("pan") if robot.last("pan") is not None else 20.0, 20.0)
        self.assertEqual(robot.last("tilt"), 0.0)
        self.assertFalse(b._sent)
        # small moves only, around where he was
        for axis, deg in robot.sent:
            rest = 20.0 if axis == "pan" else 0.0
            self.assertLessEqual(abs(deg - rest), 10.0, (axis, deg))
        # the tracker's motion copy knows where the head was sent
        self.assertEqual(tracker.motion["tilt"].target, 0.0)

    def test_still_when_not_allowed(self):
        robot, _, b = self.make()
        b.allowed = lambda: False   # asleep, tracking, or no robot

        async def go():
            b.ponder()
            await asyncio.sleep(0.4)
            b.speaking("excited")
            await asyncio.sleep(0.6)
            b.quiet()
            await asyncio.sleep(0.1)
        run(go())
        self.assertEqual(robot.sent, [])

    def test_switched_off_sends_nothing(self):
        robot, _, b = self.make()

        async def go():
            b.speaking("excited")
            await asyncio.sleep(0.5)
            b.quiet()
            await asyncio.sleep(0.1)
        with patch.object(config, "BODY_LANGUAGE", False):
            run(go())
        self.assertEqual(robot.sent, [])

    def test_switched_off_mid_reply_still_comes_home(self):
        robot, _, b = self.make()

        async def go():
            b.speaking("sad")             # droops and stays drooped while talking
            await asyncio.sleep(0.5)
            config.BODY_LANGUAGE = False
            b.quiet()
            await asyncio.sleep(0.2)
        run(go())
        self.assertEqual(robot.last("tilt"), 0.0)

    def test_after_a_look_he_keeps_still_while_he_talks(self):
        robot, tracker, b = self.make()

        async def go():
            b.ponder()
            b.speaking("neutral")         # "Let me look..."
            await asyncio.sleep(0.3)
            async with b.hold():
                n = len(robot.sent)
                tracker.pan = 95.0        # the look turned him all the way right
                await asyncio.sleep(0.4)
                self.assertEqual(len(robot.sent), n, "nothing sent while the picture is taken")
            b.feel("curious")             # the real emotion arrives with the answer
            await asyncio.sleep(0.8)
            self.assertEqual(len(robot.sent), n, "and nothing while he talks about it: servos quiet")
            b.quiet()
            await asyncio.sleep(0.2)
            self.assertEqual(len(robot.sent), n, "already where the look left him")
        run(go())

    def test_look_before_he_speaks_also_keeps_still(self):
        robot, tracker, b = self.make()

        async def go():
            b.ponder()
            await asyncio.sleep(0.4)
            async with b.hold():          # the model looks first, then answers
                tracker.pan = -60.0
                n = len(robot.sent)
            b.speaking("excited")
            await asyncio.sleep(0.8)
            self.assertEqual(len(robot.sent), n)
            b.quiet()
            b.ponder()                    # the next question: body language again
            b.speaking("happy")
            await asyncio.sleep(0.4)
            self.assertGreater(len(robot.sent), n)
            b.quiet()
            await asyncio.sleep(0.2)
        run(go())

    def test_a_look_takes_the_thinking_glance_off_both_axes(self):
        robot, tracker, b = self.make()

        async def go():
            b.ponder()                    # glances up and aside
            await asyncio.sleep(0.4)
            self.assertGreater(robot.last("tilt"), 1.0)
            async with b.hold():          # a look that only turns pan
                tracker.pan = 95.0
            self.assertEqual(robot.last("tilt"), 0.0, "tilt back to level, where its servo can rest")
            self.assertEqual(tracker.motion["tilt"].target, 0.0, "and the look waits for it")
        run(go())

    def test_stop_sends_nothing_more(self):
        robot, _, b = self.make()

        async def go():
            b.speaking("excited")
            await asyncio.sleep(0.2)
            b.stop()                      # e.g. tracking just switched on
            n = len(robot.sent)
            await asyncio.sleep(0.6)
            return n
        n = run(go())
        self.assertEqual(len(robot.sent), n)

    def test_ponder_then_reply(self):
        robot, _, b = self.make()

        async def go():
            b.ponder()
            await asyncio.sleep(0.4)
            pondered = list(robot.sent)
            b.speaking("neutral")
            await asyncio.sleep(0.3)
            b.quiet()
            await asyncio.sleep(0.2)
            return pondered
        pondered = run(go())
        self.assertTrue(any(a == "tilt" and d > 0 for a, d in pondered), "glances up while thinking")
        self.assertEqual(robot.last("tilt"), 0.0)

    def test_new_emotion_mid_reply_changes_the_moves(self):
        robot, _, b = self.make()

        async def go():
            b.speaking("thinking")        # "Let me look..." before the look; the real emotion comes after it
            await asyncio.sleep(0.3)
            n = len(robot.sent)
            b.feel("sad")
            await asyncio.sleep(0.5)
            later = robot.sent[n:]
            b.quiet()
            await asyncio.sleep(0.2)
            return later
        later = run(go())
        self.assertIn(("tilt", -8.0), later, "the sad droop plays when the emotion arrives")
        self.assertEqual(robot.last("tilt"), 0.0)

    def test_stays_inside_the_neck_limits(self):
        robot, _, b = self.make(pan=config.TRACK_PAN_LIMIT, tilt=config.TRACK_TILT_MAX)

        async def go():
            b.speaking("excited")
            await asyncio.sleep(0.9)
            b.quiet()
            await asyncio.sleep(0.2)
        run(go())
        for axis, deg in robot.sent:
            if axis == "pan":
                self.assertLessEqual(abs(deg), config.TRACK_PAN_LIMIT)
            else:
                self.assertLessEqual(deg, config.TRACK_TILT_MAX)


if __name__ == "__main__":
    unittest.main()
