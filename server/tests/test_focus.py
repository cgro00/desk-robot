"""Focus mode (brain/focus.py): when a phone counts, telling off, approval, the end of a session, and the Decisions
API's answers (no calls to it here: the looks are made up)."""

import asyncio
import unittest
from unittest import mock

import openai

from brain import config, decisions
from brain.focus import Focus


def judged(f, probs, every=3.0, start=0.0):
    """Feed one look per `every` seconds; the events that came out, by look."""
    return [f.judge(p, start + i * every) for i, p in enumerate(probs)]


class JudgeTests(unittest.TestCase):
    def test_a_glance_is_fine(self):
        f = Focus()
        self.assertEqual(judged(f, [0.95, 0.9, 0.1, 0.95, 0.9, 0.2]), [None] * 6)
        self.assertEqual(f.nags, 0)

    def test_three_looks_in_a_row_tell_you_off(self):
        f = Focus()
        self.assertEqual(judged(f, [0.9, 0.85, 0.95]), [None, None, "nag"])
        self.assertEqual((f.pickups, f.nags), (1, 1))

    def test_keeping_at_it_tells_you_off_again_after_the_wait(self):
        f = Focus()
        events = judged(f, [0.9] * 14)    # 42 s on the phone, a look every 3 s
        nags = [i for i, e in enumerate(events) if e == "nag"]
        self.assertEqual(nags, [2, 12])  # at 6 s, then 30 s later
        self.assertEqual((f.pickups, f.nags), (1, 2))

    def test_putting_it_down_gets_approval_once(self):
        f = Focus()
        events = judged(f, [0.9, 0.9, 0.9, 0.1, 0.05, 0.1, 0.0])
        self.assertEqual(events, [None, None, "nag", None, "praise", None, None])

    def test_unsure_looks_neither_count_nor_put_it_down(self):
        f = Focus()
        events = judged(f, [0.9, 0.9, 0.9, 0.5, 0.1, 0.6, 0.1, 0.1])
        self.assertEqual(events, [None, None, "nag", None, None, None, None, "praise"])

    def test_a_new_pickup_after_putting_it_down(self):
        f = Focus()
        judged(f, [0.9, 0.9, 0.9, 0.0, 0.0])
        judged(f, [0.9, 0.9, 0.9], start=100.0)
        self.assertEqual((f.pickups, f.nags), (2, 2))

    def test_a_refused_look_changes_nothing(self):
        f = Focus()
        self.assertEqual(judged(f, [0.9, 0.9, None, 0.9]), [None, None, None, "nag"])


class NoteTests(unittest.TestCase):
    def setUp(self):
        self.f = Focus(clock=lambda: 0.0)
        self.f.minutes, self.f.started_at, self.f.ends_at = 25.0, 0.0, 25 * 60.0

    def test_each_telling_off_is_stronger(self):
        notes = []
        for n in (1, 2, 4):
            self.f.nags = n
            notes.append(self.f.note("nag"))
        self.assertIn("put it down", notes[0])
        self.assertIn("firmer", notes[1])
        self.assertIn("4 times", notes[2])
        for n in notes:
            self.assertIn(config.HUMAN_NAME, n)
            self.assertIn("25 minutes left", n)

    def test_the_end_says_how_it_went(self):
        self.assertIn("never picked up", self.f.note("done"))
        self.f.pickups = 1
        self.assertIn("once", self.f.note("done"))
        self.f.pickups = 3
        self.assertIn("3 times", self.f.note("done"))

    def test_notes_dont_trip_the_camera_words(self):
        # a camera phrase would make main.py treat the note as a question about seeing and describe the room
        from brain.main import wants_camera
        for event in ("nag", "praise", "start", "stopped", "done"):
            self.assertFalse(wants_camera(self.f.note(event)), event)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def run_session(answers, minutes=1.0, busy=lambda: False, frame=b"jpeg"):
    """A session of made-up looks, 15 s of fake time apart (FOCUS_NAG_EVERY 30 s): what he reacted with."""
    clock = Clock()
    it = iter(answers)
    reacted = []

    def take_frame():
        clock.t += 15.0
        return frame

    def decide(jpeg):
        a = next(it, 0.0)
        if isinstance(a, Exception):
            raise a
        return {"on_phone": {"type": "predicate", "name": "on_phone", "probability": a}}

    async def react(event):
        reacted.append(event)

    f = Focus(decide=decide, clock=clock)
    f.frame, f.busy, f.react = take_frame, busy, react

    async def go():
        with mock.patch.object(config, "FOCUS_CHECK_EVERY", 0.0), mock.patch.object(config, "FOCUS_STREAK", 2):
            f.start(minutes)
            for _ in range(500):
                if not f.active and reacted[-1:] == ["done"] or f.error.startswith("OpenAI"):
                    break
                await asyncio.sleep(0)
    asyncio.run(go())
    return f, reacted


class SessionTests(unittest.TestCase):
    def test_a_session_runs_to_the_end(self):
        f, reacted = run_session([0.9, 0.9, 0.0, 0.0])
        self.assertEqual(reacted, ["nag", "praise", "done"])
        self.assertFalse(f.active)
        self.assertEqual(f.pickups, 1)

    def test_no_picture_no_look(self):
        with mock.patch("builtins.print"):
            f, reacted = run_session([], minutes=1.0, frame=None)
        self.assertEqual(f.checks, 0)

    def test_a_refused_key_stops_it(self):
        resp = mock.Mock(status_code=401, headers={}, request=mock.Mock())
        err = openai.AuthenticationError("bad key", response=resp, body=None)
        with mock.patch("builtins.print"):
            f, reacted = run_session([err])
        self.assertFalse(f.active)
        self.assertIn("OPENAI_API_KEY", f.error)
        self.assertEqual(reacted, [])

    def test_a_failed_look_is_skipped(self):
        with mock.patch("builtins.print"):
            f, reacted = run_session([openai.APIConnectionError(request=mock.Mock()), 0.9, 0.9])
        self.assertEqual(reacted, ["nag", "done"])

    def test_a_bug_ends_the_session_instead_of_leaving_it_on(self):
        f = Focus(decide=lambda j: {}, clock=lambda: 0.0)
        f.frame = lambda: 1 / 0

        async def go():
            with mock.patch("builtins.print"):
                f.start(25)
                for _ in range(20):
                    await asyncio.sleep(0)
        asyncio.run(go())
        self.assertFalse(f.active)
        self.assertIn("focus mode stopped", f.error)

    def test_stop_says_how_it_went(self):
        f = Focus(decide=lambda j: {}, clock=lambda: 0.0)

        async def go():
            with mock.patch("builtins.print"):
                f.start(25)
                f.pickups = 2
                return f.stop()
        self.assertIn("2 times", asyncio.run(go()))
        self.assertFalse(f.active)
        self.assertEqual(f.stop(), "No focus session was running.")

    def test_minutes_are_kept_in_range(self):
        f = Focus(decide=lambda j: {}, clock=lambda: 0.0)

        async def go():
            with mock.patch("builtins.print"):
                f.start(9999)
                a = f.minutes
                f.start(None)
                b = f.minutes
                f.stop()
                return a, b
        self.assertEqual(asyncio.run(go()), (180.0, config.FOCUS_MINUTES))


class QuietDuringSessionTests(unittest.TestCase):
    """During a session he answers nothing he hears (brain/main.py), except "hey Rocky, stop focus"."""

    def hear(self, text):
        from brain import main
        asked = []

        async def converse(question, *a, **kw):
            asked.append((question, kw.get("interruptible", True)))
            return True

        async def go():
            with mock.patch.object(main, "converse", converse), mock.patch("builtins.print"):
                main.focus.start(25)
                await main._handle_heard((text, 0.0, 1.0, 1.0))
                active = main.focus.active
                main.focus.stop()
                return active
        return asyncio.run(go()), asked

    def test_nothing_said_is_answered(self):
        name = config.ROBOT_NAME
        for text in (f"hey {name}, what's the weather?", "did you see the game last night", f"{name}, track me",
                     f"hey {name}, go to sleep"):
            active, asked = self.hear(text)
            self.assertTrue(active, text)
            self.assertEqual(asked, [], text)

    def test_stop_focus_ends_it(self):
        active, asked = self.hear(f"Hey {config.ROBOT_NAME}, stop focus mode.")
        self.assertFalse(active)
        self.assertEqual(len(asked), 1)
        self.assertIn("ended the focus session early", asked[0][0])
        self.assertFalse(asked[0][1])          # talk nearby can't cut the summary off

    def test_stop_focus_needs_his_name(self):
        active, asked = self.hear("okay stop focus")
        self.assertTrue(active)
        self.assertEqual(asked, [])

    def test_a_wave_does_nothing(self):
        from brain import main

        async def go():
            with mock.patch.object(main, "robot_socket", object()), mock.patch.object(main, "say") as say, \
                    mock.patch("builtins.print"):
                main.focus.start(25)
                await main.gesture_seen("wave")
                main.focus.stop()
                return say.called
        self.assertFalse(asyncio.run(go()))


class AnswerTests(unittest.TestCase):
    def test_answers_by_name(self):
        reply = {"model": "gpt-6-luna", "answers": [
            {"type": "predicate", "name": "on_phone", "probability": 0.93},
            {"type": "refusal", "name": "other", "refusal": "no"}]}
        a = decisions.named_answers(reply)
        self.assertEqual(decisions.probability(a, "on_phone"), 0.93)
        self.assertIsNone(decisions.probability(a, "other"))
        self.assertIsNone(decisions.probability(a, "missing"))
        self.assertEqual(decisions.named_answers({"error": "x"}), {})


if __name__ == "__main__":
    unittest.main()
