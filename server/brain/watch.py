"""Watching for something: "Rocky, tell me when the water boils".

The model turns what you ask into one yes/no question about a camera picture (the `watch_for` ability: "Is the water
in the pot boiling: bubbling, or with steam pouring off it?"; asking for "large bubbles breaking all over the
surface" scored 0.0 on a boiling pot his camera saw from the side, 2026-11-20). Every WATCH_CHECK_EVERY
seconds the camera's newest frame goes to OpenAI's Decisions API (decisions.py) with that question. It needs to show
in the picture: from the counter, a boiling pot's bubbles are glare on a surface the camera sees nearly edge-on (0.00-0.12
on a pot boiling hard, with sharp stills too, 2026-11-22), so he can't tell from there. Once a look has
seen it not so (below WATCH_NO_PROB: you walking out of the room you asked him to watch isn't someone coming in), and
then WATCH_STREAK looks in a row at WATCH_YES_PROB or more, he tells you, in his own words: main.py hands the model a note
("what they asked you to watch for has happened"), like focus mode. If you don't answer, he reminds you every
WATCH_REMIND_EVERY seconds, up to WATCH_REMINDERS times. Something urgent (the model's `urgent`: water boiling, a pot
boiling over) he keeps telling you every WATCH_URGENT_EVERY seconds after he finishes, more panicked each time,
until you answer (anything you say), WATCH_URGENT_REMINDERS times at most. With nothing after the minutes asked for
(WATCH_MINUTES if none), he gives up and says so.

His head stays pointed where it was when he started: face tracking goes off, he doesn't doze off (that centers the
head), and if something turns it meanwhile (a `look`, a dance) it goes back before the next look. He talks with you
as usual while watching, and doesn't look while he's talking, thinking or moving. One watch at a time; focus mode
and sleep end it. Pictures go to OpenAI only while he's watching.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

import openai

from . import config
from . import decisions

TOOL = {
    "type": "function",
    "function": {
        "name": "watch_for",
        "description": (
            "Watch something with your camera and tell your human when it happens: water boiling, the cat coming "
            "back, a 3D print finishing, someone at the door. Use it when they ask you to tell them, or let them "
            "know, when something you can see happens (\"stop watching\" means this, with stop). You watch where "
            "your head points now: they've set you up facing it (\"this pot\", or they just pointed your head), "
            "so start right away; don't ask where it is or whether you can see it. Only if they say it's somewhere "
            "else (\"it's to your left\"), use look first to turn there, and watch_for after it, not together. You keep talking with them as usual meanwhile, and you'll be told "
            "when it happens. Before using it, tell them in one short line that you'll watch. With stop: stop "
            "watching."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "event": {
                    "type": "string",
                    "description": "What you're waiting for, in a few words, to tell them later: \"the water is boiling\".",
                },
                "question": {
                    "type": "string",
                    "description": (
                        "A short, plain yes/no question about ONE still picture: yes once it has happened. A photo "
                        "shows no movement or time, so ask about the state after it, never \"just\", \"entered\", "
                        "\"came back\", \"started\" or \"moving\". Name the signs that show from anywhere, and don't "
                        "demand details your camera may not see from its angle (bubbles on a surface it sees edge-on). "
                        "Someone enters the room -> \"Is there a person in the picture?\"; the cat comes back -> \"Is "
                        "there a cat in the picture?\"; the water boils -> \"Is the water in the pot boiling: "
                        "bubbling, or with steam pouring off it?\". (It only counts after a picture where it isn't "
                        "so, so you walking out of view doesn't.)"
                    ),
                },
                "urgent": {
                    "type": "boolean",
                    "description": (
                        "true when they'll need to act fast once it happens (water boiling, a pot boiling over, "
                        "smoke, milk rising), or they ask you to keep telling them until they answer: you then "
                        "repeat it every few seconds, more and more panicked, until they answer."
                    ),
                },
                "minutes": {"type": "number",
                            "description": f"Give up after this long, 1-180. Omit for {config.WATCH_MINUTES:.0f}."},
                "stop": {"type": "boolean", "description": "true: stop watching (no other fields needed)."},
            },
        },
    },
}


def _minutes_text(m: float) -> str:
    m = max(1, round(m))
    return "1 minute" if m == 1 else f"{m} minutes"


class Watcher:
    def __init__(self, decide: Callable[[str, bytes], float | None] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        # decide(question, jpeg): the probability the answer is yes (None: refused). Blocking; runs in an executor.
        self.decide = decide or (lambda question, jpeg: decisions.probability(
            decisions.ask([decisions.predicate("happened", question)], jpeg), "happened"))
        self.clock = clock
        # main.py plugs in:
        self.frame: Callable[[], bytes | None] = lambda: None    # the newest camera frame, or None
        self.busy: Callable[[], bool] = lambda: False            # he's talking, thinking or moving: don't look
        self.listening: Callable[[], bool] = lambda: False       # you're talking: no reminder over you
        self.aim: Callable[[], Awaitable[object]] | None = None  # head back to where it was pointed, if it moved
        self.react: Callable[[str], Awaitable[object]] | None = None   # "found", "remind", "timeout" or "failed"
        self.active = False        # looking
        self.reminding = False     # found it, and you haven't answered yet
        self.urgent = False        # then: again every few seconds, more panicked, until you answer
        self.run = 0
        self.event = self.question = ""
        self.minutes = 0.0
        self.started_at = self.ends_at = self.found_at = 0.0
        self.streak = self.reminders = self.checks = 0
        self.armed = False         # a look has seen it not so yet: only then does a yes count
        self.last_p: float | None = None
        self.error = ""

    # ── what main.py calls ──────────────────────────────────────────────────

    def start(self, event: str, question: str, minutes: float | None = None, urgent: bool = False) -> str:
        """Start watching (a new watch replaces any running). Returns the line for the model."""
        self.event, self.question, self.urgent = event, question, urgent
        self.minutes = max(1.0, min(180.0, float(minutes or config.WATCH_MINUTES)))
        self.run += 1
        self.active, self.reminding = True, False
        self.started_at = self.clock()
        self.ends_at = self.started_at + self.minutes * 60
        self.streak = self.reminders = self.checks = 0
        self.armed = False
        self.last_p, self.error = None, ""
        asyncio.ensure_future(self._go(self.run))
        print(f"watch: {_minutes_text(self.minutes)} for {event!r}{' (urgent)' if urgent else ''} — {question}")
        return (f"Watching for {event} (up to {_minutes_text(self.minutes)}), where your head points now. You'll be "
                "told when it happens. Your line before it was enough: say nothing more.")

    def stop(self) -> str:
        """Stop watching (and reminding). Returns what happened, for the model."""
        was = self.active
        self.active = self.reminding = False
        self.run += 1
        if not was:
            return "You weren't watching for anything."
        print(f"watch: stopped ({self.event})")
        return f"Stopped watching for {self.event}."

    def heard(self) -> None:
        """You said something: if he's told you it happened, that's your answer (no more reminders)."""
        if self.reminding:
            self.reminding = False
            print("watch: heard you, no more reminders")

    def note(self, event: str) -> str:
        """What the model is told, in place of a question, so he says it in his own words."""
        human = config.HUMAN_NAME
        if event == "found":
            how = "loud and urgent: they need to come now" if self.urgent else "clear and excited"
            return (f"(Watching, not something {human} said: what they asked you to watch for has happened: "
                    f"{self.event}. Tell them right now, {how}, in one or two short lines, in character. "
                    "Use no abilities.)")
        if event == "remind" and self.urgent:
            n = self.reminders
            mood = ("Say it again, more urgent." if n == 1 else
                    "Still no answer: you're getting worried. More urgent." if n <= 3 else
                    "Still nothing: you're panicking now." if n <= 6 else
                    f"STILL nothing, the {n + 1}th time you've said it: total panic, frantic, as dramatic as you can.")
            return (f"(Watching, not something {human} said: {self.event}, and {human} still hasn't answered you. "
                    f"{mood} The panic is in how you say it, not in what: say only what you saw ({self.event}), "
                    "never anything worse you didn't see (no smoke, fire, boiling over or dry pot). One short line, "
                    "different from your last ones, in character. Use no abilities.)")
        if event == "remind":
            ago = _minutes_text((self.clock() - self.found_at) / 60) if self.clock() - self.found_at >= 60 else \
                "a moment"
            return (f"(Watching: you told {human} {ago} ago that {self.event}, and they haven't answered. Remind "
                    "them, more urgently, in one short line, in character. Use no abilities.)")
        if event == "timeout":
            return (f"(Watching: you watched for {self.event} for {_minutes_text(self.minutes)} and it didn't "
                    "happen, so you've stopped watching. Tell them in one short line, in character. Use no "
                    "abilities.)")
        return (f"(Watching: you had to stop watching for {self.event}: {self.error}. Tell them in one short line, "
                "in character. Use no abilities.)")

    def judge(self, p: float | None) -> bool:
        """One look's probability -> whether it has happened: WATCH_STREAK looks in a row, once a look has seen it
        not so (armed). "Someone enters the room" is the same picture as you on your way out; and asked "is there
        a person?", that's a yes until you're gone."""
        self.last_p = p
        if not self.armed:
            if p is not None and p < config.WATCH_NO_PROB:
                self.armed = True
                print(f"watch: {p:.2f}, not so yet — now watching for it")
            return False
        self.streak = self.streak + 1 if p is not None and p >= config.WATCH_YES_PROB else 0
        return self.streak >= config.WATCH_STREAK

    # ── the looking ─────────────────────────────────────────────────────────

    async def _go(self, run: int) -> None:
        try:
            if await self._looks(run):
                await self._remind(run)
        except Exception as e:   # a bug in here mustn't leave him watching for good (and awake)
            if self.run == run:
                self.active = self.reminding = False
                print(f"watch: stopped by an error: {e}")

    async def _looks(self, run: int) -> bool:
        """Look until it happens (True) or the time's up or it can't go on (False, after saying so)."""
        loop = asyncio.get_running_loop()
        failures = 0
        while self.active and self.run == run:
            began = self.clock()
            if began >= self.ends_at:
                self.active = False
                print(f"watch: time's up, no {self.event}")
                await self._say("timeout", run)
                return False
            jpeg = None
            if not self.busy():
                if self.aim is not None:
                    await self.aim()
                jpeg = None if self.busy() else self.frame()
            if jpeg is not None:
                try:
                    p = await loop.run_in_executor(None, self.decide, self.question, jpeg)
                except (openai.AuthenticationError, openai.PermissionDeniedError) as e:
                    return await self._fail(run, f"OpenAI refused the key ({e.status_code})")
                except Exception as e:     # no internet, a slow answer, a hiccup: try the next look
                    failures += 1
                    self.error = str(e)[:160]
                    if failures == 1:
                        print(f"watch: a look failed ({self.error})")
                    if failures >= config.WATCH_MAX_FAILURES:
                        return await self._fail(run, "the internet or OpenAI stopped answering")
                else:
                    failures = 0
                    self.checks += 1
                    if self.run != run or not self.active:
                        return False
                    if self.judge(p):
                        print(f"watch: {p:.2f} x{self.streak} -> {self.event}!")
                        self.active = False
                        self.reminding = True
                        self.found_at = self.clock()
                        await self._say("found", run)
                        return True
                    if config.WATCH_LOG_EVERY_LOOK or (p is not None and p >= 0.5):
                        shown = "refused" if p is None else f"{p:.2f}"
                        print(f"watch: {shown} ({self.streak} in a row)" if self.armed else
                              f"watch: {shown} — already so: waiting until it isn't, first")
            await asyncio.sleep(max(0.0, config.WATCH_CHECK_EVERY - (self.clock() - began)))
        return False

    async def _remind(self, run: int) -> None:
        """Again every WATCH_REMIND_EVERY s until you answer (heard()), up to WATCH_REMINDERS times; urgent: every
        WATCH_URGENT_EVERY s after his last one ended, up to WATCH_URGENT_REMINDERS times."""
        every, most = ((config.WATCH_URGENT_EVERY, config.WATCH_URGENT_REMINDERS) if self.urgent else
                       (config.WATCH_REMIND_EVERY, config.WATCH_REMINDERS))
        while self.reminding and self.run == run and self.reminders < most:
            await asyncio.sleep(every)
            # Not in the middle of another answer, or over you (you may be answering him right now).
            while (self.busy() or self.listening()) and self.reminding and self.run == run:
                await asyncio.sleep(0.25)
            if not self.reminding or self.run != run:
                return
            self.reminders += 1
            await self._say("remind", run)
        self.reminding = False

    async def _fail(self, run: int, why: str) -> bool:
        self.active = False
        self.error = why
        print(f"watch: {why} — stopping")
        await self._say("failed", run)
        return False

    async def _say(self, event: str, run: int) -> None:
        if self.react is None or self.run != run:
            return
        for _ in range(60):           # let a conversation finish first (up to 30 s)
            if not self.busy():
                break
            await asyncio.sleep(0.5)
        try:
            await self.react(event)
        except Exception as e:        # a speaking hiccup mustn't break the watch
            print(f"watch: couldn't say it ({e})")
