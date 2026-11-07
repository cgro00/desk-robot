"""Rocky's voice going to the robot: paced, so it never runs far ahead of what the speaker is playing. No robot."""

import asyncio
import json
import time
import unittest
from unittest.mock import patch

from brain import main


class FakeRobot:
    def __init__(self):
        self.audio_at: list[tuple[float, int]] = []   # (time sent, bytes of audio sent so far)
        self.total = 0

    async def send(self, msg):
        if isinstance(msg, bytes):
            self.total += len(msg) - 1
            self.audio_at.append((time.monotonic(), self.total))
        elif json.loads(msg).get("type") == "speak_end":
            main.robot_speak_done.set()               # the robot says it finished playing


class PacingTests(unittest.TestCase):
    def test_audio_runs_at_most_a_little_ahead(self):
        robot = FakeRobot()

        async def run():
            reply = main.SpokenReply(asyncio.get_running_loop(), main.Timeline(time.time()))
            second = 32000
            for _ in range(15):                       # 1.5 s of audio, all ready at once (the voice is fast)
                reply.audio.put(b"\0" * (second // 10))
            reply.audio.put(None)
            t0 = time.monotonic()
            await reply._play_robot(reply.audio.get())
            return t0

        with patch.object(main, "robot_socket", robot), patch.object(main, "ROBOT_AUDIO_AHEAD_S", 0.5):
            t0 = asyncio.run(run())
        self.assertEqual(robot.total, 48000)
        for at, sent in robot.audio_at:               # never more than 0.5 s ahead of the clock (+ a frame of slack)
            self.assertLessEqual(sent / 32000 - (at - t0), 0.5 + 0.1 + 0.02)
        self.assertGreater(robot.audio_at[-1][0] - t0, 0.8)   # the last of it went out near its time, not at once


if __name__ == "__main__":
    unittest.main()
