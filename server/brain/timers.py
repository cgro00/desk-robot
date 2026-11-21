"""Timers: "Rocky, set a timer for ten minutes", "how long is left?", "cancel the pasta timer".

The model calls one `timer` ability with an action (set / check / cancel). This module only keeps the list and
says when one is due; main.py does the ringing (wakes him, a surprised face, the spoken line). Timers live in
memory, so a server restart forgets them.
"""

from __future__ import annotations

import threading
import time

MAX_TIMERS = 10
MAX_SECONDS = 24 * 3600


def spoken(seconds: float) -> str:
    """125 -> "2 minutes 5 seconds", 3600 -> "1 hour". Rounded to whole seconds."""
    s = max(0, int(round(seconds)))
    h, rest = divmod(s, 3600)
    m, sec = divmod(rest, 60)
    parts = [f"{n} {unit}{'' if n == 1 else 's'}" for n, unit in ((h, "hour"), (m, "minute"), (sec, "second")) if n]
    return " ".join(parts) or "0 seconds"


class Timers:
    def __init__(self, clock=time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._items: list[dict] = []   # {"label", "due", "length"}

    def set(self, seconds: float, label: str = "") -> str:
        if not 1 <= seconds <= MAX_SECONDS:
            raise ValueError("a timer runs between 1 second and 24 hours")
        label = " ".join(str(label or "").split())[:40]
        with self._lock:
            if len(self._items) >= MAX_TIMERS:
                raise ValueError(f"already {MAX_TIMERS} timers running; cancel one first")
            self._items.append({"label": label, "due": self._clock() + seconds, "length": seconds})
        return f"Timer set{f' ({label})' if label else ''}: {spoken(seconds)}."

    def check(self) -> str:
        with self._lock:
            items = sorted(self._items, key=lambda t: t["due"])
        if not items:
            return "No timers running."
        now = self._clock()
        return " ".join(f"{t['label'] or 'Timer'}: {spoken(t['due'] - now)} left." for t in items)

    def cancel(self, label: str = "") -> str:
        """By label; with no label the only (or the soonest) one. "all" cancels every timer."""
        want = " ".join(str(label or "").split()).casefold()
        with self._lock:
            if not self._items:
                return "No timers running."
            if want in ("all", "every", "everything"):
                n = len(self._items)
                self._items.clear()
                return f"Cancelled {n} timer{'' if n == 1 else 's'}."
            if want:
                match = next((t for t in self._items if t["label"].casefold() == want), None) or \
                        next((t for t in self._items if want in t["label"].casefold()), None)
                if match is None:
                    names = ", ".join(t["label"] or "unnamed" for t in self._items)
                    return f"No timer called {label}. Running: {names}."
            else:
                match = min(self._items, key=lambda t: t["due"])
            self._items.remove(match)
        return f"Cancelled the {match['label'] or 'timer'}{' timer' if match['label'] else ''}."

    def soonest(self) -> dict | None:
        """{"left": whole seconds, "label": ...} of the next timer to ring, for the robot's screen; None if none."""
        with self._lock:
            if not self._items:
                return None
            t = min(self._items, key=lambda t: t["due"])
        return {"left": max(1, int(round(t["due"] - self._clock()))), "label": t["label"]}

    def due(self) -> list[dict]:
        """Take the timers that have run out (each once)."""
        now = self._clock()
        with self._lock:
            done = [t for t in self._items if t["due"] <= now]
            self._items = [t for t in self._items if t["due"] > now]
        return done

    def ability(self, args: dict) -> str:
        """The model's `timer` call. ValueError messages go back to the model as the result."""
        action = str(args.get("action") or "set")
        try:
            if action == "set":
                secs = float(args.get("hours") or 0) * 3600 + float(args.get("minutes") or 0) * 60 + \
                       float(args.get("seconds") or 0)
                return self.set(secs, str(args.get("label") or ""))
            if action == "check":
                return self.check()
            if action == "cancel":
                return self.cancel(str(args.get("label") or ""))
        except (TypeError, ValueError) as e:
            return f"Not done: {e}"
        return "Not done: action must be set, check or cancel"


TOOL = {
    "type": "function",
    "function": {
        "name": "timer",
        "description": (
            "Kitchen-timer style countdowns. set: start one when your human asks for a timer or to be reminded in "
            "some time (give hours/minutes/seconds; a short label if they name what it is for, like pasta or tea). "
            "check: how long is left. cancel: stop one (by label, \"all\", or no label for the soonest). You ring "
            "it yourself when it runs out. For set and cancel, say one short line as you call it (like 'Ten minutes, "
            "starting now.'); that is all. For check, call it first, then say what is left."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["set", "check", "cancel"]},
                "hours": {"type": "number"},
                "minutes": {"type": "number"},
                "seconds": {"type": "number"},
                "label": {"type": "string", "description": "what it is for, a word or two (optional)"},
            },
            "required": ["action"],
        },
    },
}
