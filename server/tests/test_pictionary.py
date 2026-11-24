"""Pictionary (brain/pictionary.py and the `pictionary` ability in main.py): picking what to draw, the score, when a
game is over, checking guesses, and what the model is told (never the answer before it's revealed). No model calls
and no robot: drawing, judging and hints are faked."""

import asyncio
import time
import unittest
from unittest import mock

from brain import config, drawing, main, pictionary, thinking
from tests.test_computer import FakeClient, _chunk


class GameTests(unittest.TestCase):
    def setUp(self):
        self.game = pictionary.Game()

    def test_no_repeats_until_everything_is_drawn(self):
        seen = []
        for _ in range(len(pictionary.WORDS)):
            word = self.game.pick()
            self.game.drawn(word)
            seen.append(word)
        self.assertEqual(sorted(seen), sorted(pictionary.WORDS))
        self.game.drawn(self.game.pick())   # all used: starts over instead of running dry
        self.assertEqual(len(self.game.used), 1)

    def test_score(self):
        for result in ("right", "wrong", "right", "none"):
            self.game.drawn(self.game.pick())
            self.game.score(result)
        self.assertEqual((self.game.right, self.game.rounds), (2, 3))
        self.assertEqual(self.game.score_line(), "your human got 2 of 3 right")

    def test_a_guess_counts_once(self):
        self.game.drawn(self.game.pick())
        self.game.score("right")
        self.game.score("right")   # no drawing waiting for a guess any more
        self.assertEqual((self.game.right, self.game.rounds), (1, 1))

    def test_walk_away_and_the_game_is_over(self):
        self.assertFalse(self.game.playing)
        self.game.drawn(self.game.pick())
        self.assertTrue(self.game.playing)
        self.game.last = time.time() - config.PICTIONARY_IDLE_S - 1
        self.assertFalse(self.game.playing)

    def test_the_next_shares_no_word_with_the_last(self):
        self.game.drawn("a palm tree")
        self.game.score("wrong")
        self.game.used = {w for w in pictionary.WORDS if w not in ("a tree", "a cat")}
        self.assertEqual(self.game.pick(), "a cat")

    def test_naming_it(self):
        self.assertTrue(pictionary.names("a door", "Answer is door-shaped thing, Casey, yes?"))
        self.assertTrue(pictionary.names("a pair of glasses", "Glasses!"))
        self.assertTrue(pictionary.names("a leaf", "Trees drop them in autumn... leaves!"))
        self.assertTrue(pictionary.names("a cherry", "Cherries, yes?"))
        self.assertTrue(pictionary.names("a bus", "Big bus."))
        self.assertTrue(pictionary.names("a pair of glasses", "You wear a glass thing."))
        self.assertFalse(pictionary.names("a T-shirt", "I don't know, guess."))
        self.assertFalse(pictionary.names("a door", "Guess it, Casey."))
        self.assertFalse(pictionary.names(None, "door"))
        self.assertFalse(pictionary.names("a smiley face", "Look close at my face, Casey."))
        self.assertTrue(pictionary.names("a smiley face", "A smiley, yes?"))

    def test_words_are_unique(self):
        self.assertEqual(len(pictionary.WORDS), len(set(pictionary.WORDS)))

    def test_the_drawing_has_no_labels(self):
        self.assertIn(drawing.GAME_RULES, drawing.prompt_for("a cat", drawing.GAME_RULES))
        self.assertNotIn(drawing.GAME_RULES, drawing.prompt_for("a cat"))


class JudgeTests(unittest.TestCase):
    def test_saying_it_needs_no_model(self):
        with mock.patch.object(pictionary, "_ask", side_effect=AssertionError("asked the model")):
            self.assertTrue(pictionary.judge("a magnet", "Is it a magnet?"))
            self.assertTrue(pictionary.judge("a pair of glasses", "glasses!"))
            self.assertTrue(pictionary.judge("a hot dog", "hot dogs"))

    def test_the_model_decides_the_rest(self):
        with mock.patch.object(pictionary, "_ask", return_value="Yes") as ask:
            self.assertTrue(pictionary.judge("a bicycle", "a bike"))
        self.assertIn("a bicycle", ask.call_args[0][0])
        with mock.patch.object(pictionary, "_ask", return_value="no"):
            self.assertFalse(pictionary.judge("a giraffe", "a toaster"))

    def test_no_model_means_wrong(self):
        with mock.patch.object(pictionary, "_ask", side_effect=OSError("offline")):
            self.assertFalse(pictionary.judge("a bicycle", "a bike"))

    def test_a_hint_that_names_it_is_dropped(self):
        with mock.patch.object(pictionary, "_ask", return_value="It is a big gray elephant."):
            self.assertEqual(pictionary.hint("an elephant", []), "")
        with mock.patch.object(pictionary, "_ask", return_value="It never forgets."):
            self.assertEqual(pictionary.hint("an elephant", []), "It never forgets.")


class AbilityTests(unittest.TestCase):
    """The ability as the model uses it, with the robot, the drawing and the judge faked."""

    def setUp(self):
        main.game.reset()
        self.drawn = []

        async def put_drawing(subject, hold, rules=""):
            self.drawn.append((subject, hold, rules))
            return 20

        async def nothing():
            pass

        for target, name, fake in ((main, "put_drawing", put_drawing), (main, "clear_drawing", nothing),
                                   (main, "cant_draw", lambda: None),
                                   (pictionary, "judge", lambda secret, guess: guess == "RIGHT"),
                                   (pictionary, "hint", lambda secret, before: f"hint {len(before) + 1}")):
            patcher = mock.patch.object(target, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def play(self, *turns):
        """Each turn's answer to the model, with the drawing it started done before the next turn."""
        async def run():
            out = []
            for args in turns:
                out.append(await main.pictionary_ability(args))
                await asyncio.sleep(0.01)
            return out
        return asyncio.run(run())

    def secrets(self):
        return [d[0] for d in self.drawn]

    def test_he_never_knows_the_drawing_up(self):
        replies = self.play({"action": "start"}, {"action": "hint"}, {"action": "guess", "guess": "a toaster"},
                            {"action": "guess", "guess": "RIGHT"}, {"action": "give_up"}, {"action": "stop"})
        first, second, third, fourth = self.secrets()
        self.assertTrue(replies[0][2])                 # start: his line before it was the reply
        self.assertNotRegex(replies[0][0], rf"\b{first}\b")
        self.assertNotRegex(replies[1][0], rf"\b{first}\b")         # a hint isn't the answer
        self.assertIn("hint 1", replies[1][0])
        self.assertIn(f"WRONG: it was {first}", replies[2][0])
        self.assertNotRegex(replies[2][0], rf"\b{second}\b")        # ...and nothing about the next one
        self.assertIn(f"RIGHT: it was {second}", replies[3][0])
        self.assertIn(f"gave up: it was {third}", replies[4][0])
        self.assertIn(f"was {fourth}", replies[5][0])  # stop: the last one, and the score
        self.assertIn("1 of 3", replies[5][0])
        self.assertEqual(self.drawn[0][1:], (config.PICTIONARY_HOLD_S, drawing.GAME_RULES))
        self.assertEqual(len(set(self.secrets())), 4)
        self.assertFalse(main.game.playing)

    def test_start_means_a_new_game(self):
        self.play({"action": "start"}, {"action": "guess", "guess": "RIGHT"}, {"action": "start"})
        self.assertEqual(main.game.rounds, 0)

    def test_no_game_on(self):
        text, _ = self.play({"action": "guess", "guess": "a cat"})[0]
        self.assertIn("game is over", text)
        self.assertEqual(self.drawn, [])

    def test_no_face_screen(self):
        with mock.patch.object(main, "cant_draw", lambda: "No body connected."):
            text, _ = self.play({"action": "start"})[0]
        self.assertEqual(text, "No body connected.")
        self.assertEqual(self.drawn, [])
        self.assertFalse(main.game.playing)


class BrainTests(unittest.TestCase):
    """The reply loop's side: no words before a guess is checked, and never the drawing up now."""

    def brain(self, rounds, secret="a door"):
        b = thinking.RobotBrain({"pictionary": lambda a: ("Their guess is WRONG: it was a door.", None)},
                                unsayable=lambda s: pictionary.names(secret, s), wordless=lambda: {"pictionary"})
        b.client = FakeClient(rounds)
        return b

    def test_words_before_a_guess_are_not_said(self):
        b = self.brain([[_chunk("[sad] Checking your guess. Wrong, it was a spoon. "),
                         _chunk(call=("pictionary", '{"action": "guess", "guess": "toaster"}'))],
                        [_chunk("[sad] Not toaster. It was a door. Next one coming!")]], secret="a cat")
        self.assertEqual(list(b.reply("is it a toaster?")), ["Not toaster.", "It was a door.", "Next one coming!"])
        self.assertEqual(b.history[1]["content"], "")    # nor remembered as said

    def test_other_talk_mid_game_is_said(self):
        b = self.brain([[_chunk("[happy] Ha! Screen is tiny. "), _chunk("Guess anyway!")]])
        self.assertEqual(list(b.reply("you suck at drawing")), ["Ha!", "Screen is tiny.", "Guess anyway!"])

    def test_never_the_drawing_up_now(self):
        b = self.brain([[_chunk("[thinking] Hint: you knock on it. Answer is door-shaped thing, yes? "),
                         _chunk("Guess now.")]])
        self.assertEqual(list(b.reply("hint?")), ["Hint: you knock on it.", "Guess now."])


class NoFaceBetweenDrawingsTests(unittest.TestCase):
    def test_the_next_drawing_replaces_the_last_without_the_face(self):
        sent = []

        async def send(payload):
            sent.append(payload)
            return True

        async def run():
            with mock.patch.object(main, "send_to_robot", send), \
                 mock.patch.object(drawing, "strokes_for", lambda subject, rules="": drawing.clean([{"line": [0, 0, 9, 9]}])), \
                 mock.patch.object(config, "DRAW_ANIMATE_S", 0.02), mock.patch.object(config, "DRAW_FRAMES", 3):
                await main.put_drawing("a cat", 60)
                await asyncio.sleep(0.05)
                await main.put_drawing("a dog", 60)
                await asyncio.sleep(0.05)
                await main.clear_drawing()

        asyncio.run(run())
        self.assertEqual([p.get("ms") for p in sent], [60000] * 6 + [0])   # the face only at the very end


if __name__ == "__main__":
    unittest.main()
