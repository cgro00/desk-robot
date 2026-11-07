"""Focus mode: a focus session where Rocky keeps you off your phone.

You start one by voice ("Rocky, help me focus for 25 minutes": the model calls `focus_mode`) or from the dashboard's
Focus card. Every FOCUS_CHECK_EVERY seconds the camera's newest frame goes to OpenAI's Decisions API (decisions.py)
with one question: is the person using a phone? It answers with a probability.

A glance at a notification is fine: he only reacts after FOCUS_STREAK looks in a row at FOCUS_PHONE_PROB or more
(about 8 s). Then he turns to you and tells you off, and if you keep at it, again every FOCUS_NAG_EVERY seconds, more
dramatically each time. When the phone goes down he approves, and when the time is up he says how it went. What he
says comes from the language model, in his own character: main.py hands it a note ("they picked up their phone, the
second time") as if it were a question, so it works the same for every personality.

During a session he answers nothing he hears, "hey Rocky" included, so you can talk to people or to yourself: the
one exception is "hey Rocky, stop focus" (config.FOCUS_STOP_PHRASES), which ends it. Talk nearby doesn't pause the
looks or cut his lines off (main.py). No looks while he's talking or thinking, and his face stays up for the whole
session (he doesn't doze). Pictures go to OpenAI only while a session runs.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

import openai

from . import config
from . import decisions

PHONE = decisions.predicate(
    "on_phone",
    "Is the person in this desk camera picture actively using a phone: holding it and looking at it, or typing or "
    "scrolling on it? A phone lying on the desk, or one they are not looking at, does not count.")

TOOL = {
    "type": "function",
    "function": {
        "name": "focus_mode",
        "description": (
            "Start a focus session: for a set time you watch your human and tell them off whenever they pick up "
            "their phone, and answer nothing else said until it ends. Use it when they ask you to help them focus, "
            "keep them off their phone, or start a focus, study or work session. Say one short line first: that "
            "you're watching, and that you'll stay quiet otherwise."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "minutes": {"type": "number",
                            "description": f"How long, 1-180. Omit for {config.FOCUS_MINUTES:.0f} minutes."},
            },
        },
    },
}


def _minutes_text(m: float) -> str:
    m = max(1, round(m))
    return "1 minute" if m == 1 else f"{m} minutes"


class Focus:
    def __init__(self, decide: Callable[[bytes], dict] | None = None, clock: Callable[[], float] = time.time) -> None:
        self.decide = decide or (lambda jpeg: decisions.ask([PHONE], jpeg))   # blocking; runs in an executor
        self.clock = clock
        # main.py plugs in:
        self.frame: Callable[[], bytes | None] = lambda: None   # the newest camera frame, or None
        self.busy: Callable[[], bool] = lambda: False           # he's talking, thinking or listening: don't look
        self.react: Callable[[str], Awaitable[object]] | None = None   # "nag", "praise" or "done"
        self.active = False
        self.session = 0
        self.minutes = 0.0
        self.started_at = self.ends_at = 0.0
        self._reset()

    def _reset(self) -> None:
        self.pickups = 0           # times he caught you on it (a new pickup each time after you put it down)
        self.nags = 0              # times he told you off (more than pickups if you kept at it)
        self.streak = 0            # looks in a row on the phone
        self.calm = 0              # looks in a row with it put down, after he caught you
        self.caught = False
        self.last_nag_at = -1e9
        self.last_p: float | None = None
        self.last_check_at = 0.0
        self.checks = 0
        self.errors = 0
        self.error = ""

    # ── what main.py calls ──────────────────────────────────────────────────

    def start(self, minutes: float | None = None) -> str:
        """Start a session (a new one replaces any running). Returns the line for the model."""
        self.minutes = max(1.0, min(180.0, float(minutes or config.FOCUS_MINUTES)))
        self._reset()
        self.session += 1
        self.active = True
        self.started_at = self.clock()
        self.ends_at = self.started_at + self.minutes * 60
        asyncio.ensure_future(self._run(self.session))
        print(f"focus: {_minutes_text(self.minutes)} — watching for the phone")
        return (f"Focus session on: {_minutes_text(self.minutes)}. You'll call your human out if they pick up "
                "their phone, and answer nothing said until it ends (\"stop focus\" ends it early). Your line "
                "before it was enough: say nothing more.")

    def stop(self) -> str:
        """End the session early. Returns how it went, for the model."""
        if not self.active:
            return "No focus session was running."
        self.active = False
        elapsed = (self.clock() - self.started_at) / 60
        print(f"focus: stopped after {_minutes_text(elapsed)} ({self.pickups} pickups)")
        return f"Focus session stopped after {_minutes_text(elapsed)}. {self._pickups_text()}."

    def status(self) -> dict:
        """For the dashboard."""
        now = self.clock()
        return {"available": decisions.available(), "active": self.active, "minutes": self.minutes,
                "left_s": max(0.0, self.ends_at - now) if self.active else 0.0,
                "pickups": self.pickups, "nags": self.nags, "phone": self.last_p,
                "checked_ago": now - self.last_check_at if self.last_check_at else None,
                "checks": self.checks, "error": self.error}

    def note(self, event: str) -> str:
        """What the model is told, in place of a question, so he says the right thing in his own words."""
        human = config.HUMAN_NAME
        if event == "nag":
            left = _minutes_text((self.ends_at - self.clock()) / 60)
            tone = ("Tell them to put it down." if self.nags <= 1 else
                    "That's the second time: be firmer." if self.nags == 2 else
                    f"That's {self.nags} times now: be dramatic about it.")
            return (f"(Focus mode, not something {human} said: during the focus session they asked you to keep "
                    f"them on, they picked up their phone. About {left} left. {tone} One or two short lines, in "
                    "character. Use no abilities.)")
        if event == "praise":
            return (f"(Focus mode: {human} put the phone down and is back to work. One short line of approval, in "
                    "character. Use no abilities.)")
        if event == "start":
            return (f"({human} just started a {_minutes_text(self.minutes)} focus session from the dashboard. You'll "
                    "keep an eye on them and call them out if they pick up their phone, and you won't answer "
                    "anything said until it's over. Tell them so in one short line, in character. Use no abilities.)")
        if event == "stopped":
            elapsed = _minutes_text((self.clock() - self.started_at) / 60)
            return (f"({human} ended the focus session early, after {elapsed}. "
                    f"{self._pickups_text()}. One short line, in character. Use no abilities.)")
        return (f"(Focus mode: the {_minutes_text(self.minutes)} focus session {human} asked for is over. "
                f"{self._pickups_text()}. Tell them how it went in one or two short lines, in character. Use no "
                "abilities.)")

    def judge(self, p: float | None, now: float) -> str | None:
        """One look's probability that you're on your phone -> "nag", "praise", or None (nothing to say)."""
        self.last_p = p
        if p is None:
            return None
        if p >= config.FOCUS_PHONE_PROB:
            self.streak += 1
            self.calm = 0
            if self.streak >= config.FOCUS_STREAK and now - self.last_nag_at >= config.FOCUS_NAG_EVERY:
                if not self.caught:
                    self.pickups += 1
                self.caught = True
                self.nags += 1
                self.last_nag_at = now
                return "nag"
            return None
        self.streak = 0
        if self.caught and p < config.FOCUS_PUT_DOWN_PROB:
            self.calm += 1
            if self.calm >= 2:
                self.caught = False
                self.calm = 0
                return "praise"
        else:
            self.calm = 0
        return None

    # ── the looking ─────────────────────────────────────────────────────────

    def _pickups_text(self) -> str:
        n = self.pickups
        return ("They never picked up their phone" if n == 0 else
                "They picked up their phone once" if n == 1 else f"They picked up their phone {n} times")

    async def _run(self, session: int) -> None:
        try:
            await self._looks(session)
        except Exception as e:   # a bug in here mustn't leave the session "on" for good (face up, countdown at 0:00)
            if self.session == session:
                self.active = False
                self.error = f"focus mode stopped: {e}"[:160]
                print(f"focus: {self.error}")

    async def _looks(self, session: int) -> None:
        loop = asyncio.get_running_loop()
        failures = 0
        while self.active and self.session == session:
            began = self.clock()
            if began >= self.ends_at:
                self.active = False
                print(f"focus: time's up ({self.pickups} pickups)")
                for _ in range(60):           # let a conversation finish first (up to 30 s)
                    if not self.busy():
                        break
                    await asyncio.sleep(0.5)
                await self._say("done")
                return
            jpeg = None if self.busy() else self.frame()
            if jpeg is not None:
                try:
                    answers = await loop.run_in_executor(None, self.decide, jpeg)
                except (openai.AuthenticationError, openai.PermissionDeniedError) as e:
                    self.error = f"OpenAI refused the key ({e.status_code}): check OPENAI_API_KEY in server/.env"
                    print(f"focus: {self.error} — stopping")
                    self.active = False
                    return
                except Exception as e:    # no internet, a slow answer, a hiccup: try the next look
                    failures += 1
                    self.errors += 1
                    self.error = str(e)[:160]
                    if failures in (1, 10):
                        print(f"focus: a look failed ({self.error})")
                else:
                    failures = 0
                    self.error = ""
                    self.checks += 1
                    self.last_check_at = self.clock()
                    if self.session != session or not self.active:
                        return
                    p = decisions.probability(answers, "on_phone")
                    event = self.judge(p, self.last_check_at)
                    if p is not None and p >= 0.5 or event:
                        print(f"focus: phone {p:.2f}" + (f" ({self.streak} in a row)" if p is not None and p >= 0.5
                                                         and not event else "") + (f" -> {event}" if event else ""))
                    if event:
                        await self._say(event)
            await asyncio.sleep(max(0.0, config.FOCUS_CHECK_EVERY - (self.clock() - began)))

    async def _say(self, event: str) -> None:
        if self.react is None:
            return
        try:
            await self.react(event)
        except Exception as e:    # a speaking hiccup mustn't end the session
            print(f"focus: couldn't react ({e})")
