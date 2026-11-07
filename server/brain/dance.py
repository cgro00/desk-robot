"""Dancing: four short routines for Rocky's neck, one picked at random when you ask him to dance.

Each routine is a list of moves on a beat: where the head goes (pan, tilt in head degrees from straight ahead,
pan + = his right, tilt + = up), for how many beats, and a face to switch to on the way. The player keeps time on
the clock rather than adding up sleeps, so a slow WiFi message doesn't drag the rhythm. The firmware's speed and
acceleration limits round the corners: a move the neck can't finish within its beat turns into a smooth sweep.

    groove     head bobs on the beat while it sways side to side (happy, then excited)
    robot      stiff poses held for a beat, one axis at a time, deadpan until the end
    sprinkler  ticks across in little steps, then one fast sweep back; three times
    disco      points up one way and down the other, a shimmy, a big finish looking up

He waits until he has finished saying his line, keeps still otherwise (body language and face tracking stand
aside), and ends straight ahead and level with a happy face.

The neck runs fast while he dances (DANCE_SPEED / DANCE_ACCEL, sent to the robot as `motion`): at its everyday
speed a short move creeps, and a servo told to creep steps and jitters; told to go fast it just goes, at its own
top speed, smoothly. The everyday limits come back when the dance ends or is cut short.
"""

from __future__ import annotations

import asyncio
import math
import random
import time
from collections.abc import Awaitable, Callable

from . import config

# (pan, tilt, beats, face or None). Kept well inside the neck's range: the dance is a lot of quick moves.
Move = tuple[float, float, float, str | None]
PAN_MAX, TILT_MIN, TILT_MAX = 60.0, -20.0, 28.0
DANCE_SPEED, DANCE_ACCEL = 450.0, 4000.0      # head deg/s, deg/s^2 for both axes: the servos' own top speed rules


def _groove() -> list[Move]:
    moves: list[Move] = []
    for i in range(16):                       # a nod down on the beat, back up on the "and"; a slow sway under it
        side = 18 * math.sin(i * math.pi / 4)
        face = "happy" if i == 0 else "excited" if i == 8 else None
        moves += [(side, -9, 0.5, face), (side, 3, 0.5, None)]
    return moves


def _robot() -> list[Move]:
    poses = [(-40, 0, 1), (-40, 15, 1), (0, 15, 0.5), (40, 15, 1), (40, -10, 1), (0, -10, 0.5), (0, 0, 1)]
    moves: list[Move] = [(0, 0, 1, "neutral")]
    for rep in range(2):
        moves += [(p, t, b, None) for p, t, b in poses]
        moves.append((0, 0, 1, None))         # freeze
    moves.append((0, 22, 1, "excited"))       # and the robot cracks a smile
    return moves


def _sprinkler() -> list[Move]:
    moves: list[Move] = [(45, 5, 1, "happy")]
    for rep in range(3):
        for i in range(1, 7):                 # tick, tick, tick across...
            moves.append((45 - i * 15, 5, 0.75, None))
        moves.append((45, 8, 2, "excited" if rep == 2 else None))   # ...and whoosh back
    return moves


def _disco() -> list[Move]:
    moves: list[Move] = [(0, 0, 0.5, "love")]
    for i in range(4):                        # point up to one side, down to the other
        moves += [(35, 22, 1.5, None), (-30, -12, 1.5, "excited" if i == 2 else None)]
    for i in range(8):                        # shimmy
        moves.append((7 if i % 2 else -7, 10, 0.25, None))
    moves.append((0, 26, 1.5, "excited"))     # big finish
    return moves


# name: (beats per minute, moves)
DANCES: dict[str, tuple[float, list[Move]]] = {
    "groove": (112, _groove()),
    "robot": (100, _robot()),
    "sprinkler": (110, _sprinkler()),
    "disco": (110, _disco()),
}


def length(name: str) -> float:
    """Seconds the routine takes."""
    bpm, moves = DANCES[name]
    return sum(b for _, _, b, _ in moves) * 60.0 / bpm


class Dancer:
    def __init__(self, send: Callable[[dict], Awaitable[object]], tracker=None) -> None:
        self.send = send                      # main.send_to_robot
        self.tracker = tracker                # its copy of the head's pose and motion
        self.ready: Callable[[], bool] = lambda: True             # main.py: his line has been said
        self.before: Callable[[], Awaitable[None]] | None = None  # main.py: body language / tracking stand aside
        self.tempo = 1.0                      # tests: > 1 plays faster
        self._task: asyncio.Task | None = None

    @property
    def dancing(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, name: str | None = None) -> str:
        """Start a routine (that one, or one at random). Returns its name."""
        if name not in DANCES:
            name = random.choice(list(DANCES))
        self.stop()
        self._task = asyncio.ensure_future(self._run(name))
        return name

    def stop(self) -> None:
        """Stop where he is (asleep, a look, his head moved by hand): nothing more is sent."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _go(self, pan: float, tilt: float, last: list[float | None]) -> None:
        pan = round(max(-PAN_MAX, min(PAN_MAX, pan)), 1)
        tilt = round(max(TILT_MIN, min(TILT_MAX, tilt)), 1)
        if pan != last[0]:
            await self.send({"type": "pan", "deg": pan})
        if tilt != last[1]:
            await self.send({"type": "tilt", "deg": tilt})
        last[0], last[1] = pan, tilt
        if self.tracker is not None:
            self.tracker.note_pose(pan=pan, tilt=tilt)

    async def _motion(self, fast: bool) -> None:
        """The neck's speed limits on the robot, and in the tracker's copy of its motion."""
        for axis, speed, accel in (("pan", config.TRACK_PAN_SPEED, config.TRACK_PAN_ACCEL),
                                   ("tilt", config.TRACK_TILT_SPEED, config.TRACK_TILT_ACCEL)):
            if fast:
                speed, accel = DANCE_SPEED, DANCE_ACCEL
            await self.send({"type": "motion", "axis": axis, "speed": speed, "accel": accel})
            if self.tracker is not None:
                m = self.tracker.motion[axis]
                m.speed, m.accel = speed, accel

    async def _run(self, name: str) -> None:
        give_up = time.time() + 10
        while not self.ready() and time.time() < give_up:   # his "Dance time!" first
            await asyncio.sleep(0.1)
        if self.before is not None:
            await self.before()
        bpm, moves = DANCES[name]
        beat = 60.0 / bpm / self.tempo
        loop = asyncio.get_running_loop()
        last: list[float | None] = [None, None]
        print(f"(dancing: {name}, {length(name) / self.tempo:.0f} s)")
        await self._motion(fast=True)
        try:
            at = loop.time()
            for pan, tilt, beats, face in moves:
                if face:
                    await self.send({"type": "emotion", "name": face})
                await self._go(pan, tilt, last)
                at += beats * beat
                await asyncio.sleep(max(0.0, at - loop.time()))
            await self._go(0.0, 0.0, last)                 # straight ahead and level, pleased with himself
            await asyncio.sleep(0.4 / self.tempo)          # (still fast for that last move)
        finally:
            await self._motion(fast=False)                 # everyday speeds, however it ended
        await self.send({"type": "emotion", "name": "happy"})
