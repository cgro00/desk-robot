"""Watching for something (brain/watch.py and the `watch_for` ability in main.py): when he says it happened, the
reminders, giving up, and keeping his head on the thing. No OpenAI calls: the Decisions API's answers are made up."""

import asyncio
import unittest
from unittest import mock

import openai

from brain import config, main, watch

FAST = {"WATCH_CHECK_EVERY": 0.0, "WATCH_REMIND_EVERY": 0.0, "WATCH_STREAK": 2, "WATCH_YES_PROB": 0.8, "WATCH_NO_PROB": 0.3,
        "WATCH_REMINDERS": 3, "WATCH_MAX_FAILURES": 3, "WATCH_URGENT_EVERY": 0.0, "WATCH_URGENT_REMINDERS": 10}


class WatcherTests(unittest.TestCase):
    def setUp(self):
        for name, value in FAST.items():
            patcher = mock.patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.said = []
        self.asked = []

    def watcher(self, answers, hold_reminders=False):
        """A watcher whose looks get `answers` in turn (a number, None for a refusal, or an exception to raise),
        then 0 for ever."""
        answers = list(answers)

        def decide(question, jpeg):
            self.asked.append(question)
            a = answers.pop(0) if answers else 0.0
            if isinstance(a, Exception):
                raise a
            return a

        w = watch.Watcher(decide)
        w.frame = lambda: b"jpeg"

        async def react(event):
            self.said.append(event)
            if hold_reminders and event == "remind" and len(self.said) == 2:
                w.heard()   # you answered after the first reminder

        w.react = react
        return w

    def run_watch(self, w, minutes=None, wait=0.3, urgent=False):
        async def go():
            w.start("the water is boiling", "Is the water at a rolling boil?", minutes, urgent)
            await asyncio.sleep(wait)
        asyncio.run(go())

    def test_it_happens_and_he_says_so_then_reminds(self):
        w = self.watcher([0.1, 0.3, 0.9, 0.95])
        self.run_watch(w)
        self.assertEqual(self.said, ["found", "remind", "remind", "remind"])
        self.assertFalse(w.active)
        self.assertFalse(w.reminding)
        self.assertEqual(self.asked, ["Is the water at a rolling boil?"] * 4)   # no looks after it happened

    def test_your_answer_stops_the_reminders(self):
        w = self.watcher([0.1, 0.9, 0.9], hold_reminders=True)
        self.run_watch(w)
        self.assertEqual(self.said, ["found", "remind"])

    def test_urgent_keeps_on_until_you_answer(self):
        w = self.watcher([0.1, 0.9, 0.9])
        answered = []

        async def react(event):
            self.said.append(event)
            answered.append(w.note(event))
            if len(self.said) == 7:
                w.heard()   # "okay okay, I'm coming!"
        w.react = react
        self.run_watch(w, urgent=True)
        self.assertEqual(self.said, ["found"] + ["remind"] * 6)
        self.assertFalse(w.reminding)
        self.assertIn("loud and urgent", answered[0])
        self.assertIn("more urgent", answered[1])
        self.assertIn("worried", answered[2])
        self.assertIn("panicking", answered[5])

    def test_urgent_stops_some_time(self):
        w = self.watcher([0.1, 0.9, 0.9])
        self.run_watch(w, urgent=True)
        self.assertEqual(self.said, ["found"] + ["remind"] * 10)   # WATCH_URGENT_REMINDERS
        self.assertIn("total panic", w.note("remind"))

    def test_no_reminder_over_you_talking(self):
        w = self.watcher([0.1, 0.9, 0.9])
        talking = [True]
        w.listening = lambda: talking[0]

        async def go():
            w.start("the water is boiling", "Boiling?", None, True)
            await asyncio.sleep(0.1)
            self.assertEqual(self.said, ["found"])   # you started answering: he waits
            w.heard()
            talking[0] = False
            await asyncio.sleep(0.1)
        asyncio.run(go())
        self.assertEqual(self.said, ["found"])

    def test_one_odd_frame_isnt_enough(self):
        w = self.watcher([0.1, 0.9, 0.2, 0.9, None, 0.9, 0.1])
        self.run_watch(w, wait=0.05)
        self.assertEqual(self.said, [])
        self.assertTrue(w.active)
        w.stop()

    def test_you_walking_out_isnt_someone_coming_in(self):
        # "is there a person?": yes while you leave, a no once you're gone, then yes when you're back
        w = self.watcher([1.0, 1.0, 0.95, 0.6, 0.02, 0.03, 0.97, 0.99])
        self.run_watch(w)
        self.assertEqual(self.said[:1], ["found"])
        self.assertEqual(len(self.asked), 8)        # found on the 8th look, not the 2nd

    def test_time_up(self):
        w = self.watcher([])
        w.clock = mock.Mock(side_effect=[0.0] + [10_000.0] * 50)   # started at 0; the next look is past the end
        self.run_watch(w, minutes=1, wait=0.05)
        self.assertEqual(self.said, ["timeout"])
        self.assertFalse(w.active)

    def test_no_internet_for_long_gives_up(self):
        w = self.watcher([OSError("no internet")] * 3)
        self.run_watch(w, wait=0.05)
        self.assertEqual(self.said, ["failed"])
        self.assertIn("stopped answering", w.note("failed"))

    def test_a_refused_key_gives_up_at_once(self):
        refused = openai.AuthenticationError("bad key", response=mock.Mock(status_code=401), body=None)
        w = self.watcher([refused])
        self.run_watch(w, wait=0.05)
        self.assertEqual(self.said, ["failed"])
        self.assertEqual(len(self.asked), 1)

    def test_no_looks_while_busy(self):
        w = self.watcher([0.9, 0.9])
        w.busy = lambda: True
        self.run_watch(w, wait=0.05)
        self.assertEqual(self.asked, [])
        w.stop()

    def test_stopped_says_nothing(self):
        w = self.watcher([0.9, 0.9])

        async def go():
            w.start("the cat is back", "Is there a cat?")
            w.stop()
            await asyncio.sleep(0.05)
        asyncio.run(go())
        self.assertEqual(self.said, [])
        self.assertFalse(w.active)

    def test_aims_before_each_look(self):
        w = self.watcher([0.1, 0.1, 0.1])
        aims = []

        async def aim():
            aims.append(len(self.asked))
        w.aim = aim
        self.run_watch(w, wait=0.02)
        w.stop()
        self.assertGreater(len(self.asked), 0)
        self.assertEqual(aims[:3], [0, 1, 2])   # each aim comes before its look

    def test_notes_say_what(self):
        w = watch.Watcher(lambda q, j: 0.0)
        w.event = "the water is boiling"
        for event in ("found", "remind", "timeout"):
            self.assertIn("the water is boiling", w.note(event))
            self.assertIn("Use no abilities", w.note(event))


class AbilityTests(unittest.TestCase):
    def setUp(self):
        self.started = []
        patcher = mock.patch.object(main.watcher, "start",
                                    lambda event, question, minutes=None, urgent=False:
                                    self.started.append((event, question, minutes, urgent)) or "watching")
        patcher.start()
        self.addCleanup(patcher.stop)

    def ability(self, **args):
        return asyncio.run(main.watch_ability(args))

    def test_needs_what_to_watch_for(self):
        with mock.patch.object(main.eyes, "latest", lambda: b"jpeg"):
            text, _ = self.ability(event="the water is boiling")
        self.assertIn("event and question", text)
        self.assertEqual(self.started, [])

    def test_needs_a_camera_picture(self):
        with mock.patch.object(main.eyes, "latest", lambda: None):
            text, _ = self.ability(event="x", question="y?")
        self.assertIn("no picture", text)

    def test_starts_where_the_head_points_with_tracking_off(self):
        tracker = mock.Mock(enabled=True, pan=-30.0, tilt=-10.0)

        async def set_tracking(on, announce=True):
            tracker.enabled = on
        with mock.patch.object(main.eyes, "latest", lambda: b"jpeg"), mock.patch.object(main, "tracker", tracker), \
                mock.patch.object(main, "set_tracking", set_tracking):
            result = self.ability(event="the water is boiling", question="Boiling?", minutes=10, urgent=True)
        self.assertEqual(result, ("watching", None, True))
        self.assertFalse(tracker.enabled)
        self.assertEqual(main.watch_pose, (-30.0, -10.0))
        self.assertEqual(self.started, [("the water is boiling", "Boiling?", 10, True)])

    def test_stop(self):
        with mock.patch.object(main.watcher, "stop", lambda: "Stopped watching for the cat."):
            self.assertEqual(self.ability(stop=True), ("Stopped watching for the cat.", None, True))


class FakeTracker:
    def __init__(self, pan=0.0, tilt=0.0):
        self.pan, self.tilt, self.enabled, self.tracking = pan, tilt, False, False

    def note_pose(self, pan=None, tilt=None):
        self.pan = self.pan if pan is None else pan
        self.tilt = self.tilt if tilt is None else tilt


class HeadTests(unittest.TestCase):
    """Pointing him at the pot and keeping him there (the kitchen test, 2026-11-20)."""

    def setUp(self):
        self.sent = []

        async def send(payload):
            self.sent.append(payload)
            return True

        self.tracker = FakeTracker()
        for name, value in (("send_to_robot", send), ("tracker", self.tracker), ("body", None), ("watch_pose", None)):
            patcher = mock.patch.object(main, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        main.watcher.active = main.watcher.reminding = False
        self.addCleanup(setattr, main.watcher, "active", False)

    def look(self, direction, degrees=None):
        args = {"direction": direction, "see": False}
        if degrees is not None:
            args["degrees"] = degrees
        asyncio.run(main.look(args))
        return self.tracker.pan, self.tracker.tilt

    def test_degrees_are_how_much_further(self):
        self.assertEqual(self.look("down"), (0, -20))
        self.assertEqual(self.look("down", 10), (0, -30))         # "a little more down": down, not up to -10
        self.assertEqual(self.look("up", 20), (0, -10))
        self.assertEqual(self.look("left", 15), (-15, -10))
        self.assertEqual(self.look("left", 15), (-30, -10))

    def test_the_same_way_again_goes_further(self):
        self.look("down")
        self.assertEqual(self.look("down"), (0, -20 - main.LOOK_AGAIN_DEG))   # not the same -20 again
        self.assertEqual(self.look("up"), (0, 20))                             # the other way: a normal look

    def test_never_past_the_limits(self):
        self.assertEqual(self.look("down", 500)[1], config.TRACK_TILT_MIN)
        self.assertEqual(self.look("right", 500)[0], config.TRACK_PAN_LIMIT)

    def test_the_watch_follows_where_you_point_him(self):
        main.watch_pose = (0.0, 0.0)
        main.watcher.active = True
        self.look("down", 20)
        self.assertEqual(main.watch_pose, (0.0, -20.0))
        self.sent.clear()
        asyncio.run(main.watch_aim())
        self.assertEqual(self.sent, [])                            # already there: not lifted back to where it began

    def heard(self, text, held=False, watching=False):
        main.watcher.active = watching
        said = []

        async def converse(note, *a, **k):
            said.append(note)
            return True

        async def nothing(*a, **k):
            return None

        with mock.patch.object(main, "head_held", held), mock.patch.object(main, "converse", converse), \
                mock.patch.object(main, "wake_up", nothing), mock.patch.object(main, "say", nothing), \
                mock.patch.object(main, "set_tracking", nothing), mock.patch.object(main, "awake_until", 1e12):
            now = 1.0
            asyncio.run(main._handle_heard((text, now, now, now)))
        return said

    def test_his_name_brings_his_eyes_up_only_when_not_posed(self):
        self.heard("Hey Rocky, what time is it?")
        self.assertIn({"type": "tilt", "deg": 0}, self.sent)
        for held, watching in ((True, False), (False, True)):
            self.sent.clear()
            self.heard("Hey Rocky, can you tell me when this pot is boiling?", held=held, watching=watching)
            self.assertNotIn({"type": "tilt", "deg": 0}, self.sent)

    def test_stop_watching_stops_the_watch(self):
        main.watcher.event = "the water is boiling"
        with mock.patch.object(main.watcher, "stop", mock.Mock()) as stop:
            said = self.heard("Rocky, stop watching.", watching=True)
        stop.assert_called_once()
        self.assertIn("stop watching for the water is boiling", said[0])

    def test_stop_watching_without_a_watch_is_tracking(self):
        with mock.patch.object(main.watcher, "stop", mock.Mock()) as stop:
            self.heard("Rocky, stop watching.")
        stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
