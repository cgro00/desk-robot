"""Pictionary with Rocky: "Rocky, let's play Pictionary".

He draws a secret thing on his face (drawing.py) and you guess. The thing is picked here at random, not by the
model, so it really is random (asked to pick, a model draws a cat every time). And the model talking to you never
knows it while you're guessing: anything he knew, he'd let slip ("Skateboard on my face now. Guess it?"). Your guess
goes to the `pictionary` ability, which checks it here (judge: a separate small model call) and only then tells him
the answer, with the next drawing already on its way. Hints come from here too, for the same reason.
"""

from __future__ import annotations

import random
import re
import time

from . import config
from .drawing import get_client
from .thinking import reasoning_switch

# Things that read at 128x64 in a few strokes, and that people name in one or two words.
WORDS = [
    # animals
    "a cat", "a dog", "a fish", "a snake", "a bird", "a spider", "a turtle", "a snail", "a rabbit", "an elephant",
    "a giraffe", "a whale", "a butterfly", "a bee", "an owl", "a duck", "a pig", "a mouse", "a crab", "an octopus",
    "a jellyfish", "a penguin", "a bat", "a frog", "a shark",
    # food
    "an apple", "a banana", "a pizza slice", "an ice cream cone", "a carrot", "a cherry", "a cupcake", "a donut",
    "a hamburger", "a hot dog", "a pineapple", "a watermelon slice", "a mushroom", "a lollipop", "a birthday cake",
    "a pear", "an egg", "a cookie",
    # things around the house
    "a coffee mug", "a key", "a light bulb", "a lamp", "a chair", "a table", "a clock", "a cup", "an umbrella",
    "a candle", "a bed", "a door", "a television", "a book", "a pair of glasses", "a hat", "a shoe", "a sock",
    "a T-shirt", "a toothbrush", "a fork", "a spoon", "a teapot", "a bucket", "a ladder", "a pair of scissors",
    "a hammer", "a screwdriver", "a magnifying glass", "a padlock", "an envelope", "a phone", "a laptop",
    "headphones", "a camera", "a guitar", "a drum", "a bell", "a crown", "a sword", "a shield", "a kite", "a balloon",
    "a gift box", "a trophy", "a flag", "a magnet", "a battery", "a robot",
    # vehicles
    "a car", "a bicycle", "a boat", "a sailboat", "an airplane", "a helicopter", "a rocket", "a train", "a bus",
    "a truck", "a submarine", "a hot air balloon", "a tractor", "a skateboard",
    # nature and places
    "the sun", "the moon", "a star", "a cloud", "a rain cloud", "a lightning bolt", "a rainbow", "a snowman",
    "a tree", "a palm tree", "a flower", "a cactus", "a leaf", "a mountain", "a volcano", "an island", "a house",
    "a castle", "a tent", "a lighthouse", "a bridge", "a pyramid", "a planet with a ring", "a campfire",
    "a snowflake", "a wave",
    # people and faces
    "a smiley face", "a stick figure running", "a ghost", "an alien", "a heart", "a hand", "an eye", "a skull",
]

RIGHT, WRONG, NONE = "right", "wrong", "none"


class Game:
    """One game: the drawing being guessed, what's been drawn, the score."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.secret: str | None = None   # what the drawing up now is (None: no guess pending)
        self.hints: list[str] = []        # hints given for it
        self.used: set[str] = set()
        self.previous = ""                # the one before (the next shares no word with it: see pick)
        self.right = 0
        self.rounds = 0                   # drawings guessed at, right or wrong
        self.last = 0.0                   # when the last drawing went up

    @property
    def playing(self) -> bool:
        """A game is on: a drawing went up less than PICTIONARY_IDLE_S ago (walk away and it's over)."""
        return self.last > 0 and time.time() - self.last < config.PICTIONARY_IDLE_S

    def score(self, result: str) -> None:
        """How the guess at the drawing up now went: RIGHT, WRONG, or NONE (no guess: skipped, or the game ends)."""
        if self.secret is not None and result in (RIGHT, WRONG):
            self.rounds += 1
            self.right += result == RIGHT
        self.previous = self.secret or self.previous
        self.secret = None

    def pick(self) -> str:
        """Something to draw that this game hasn't had yet (all used: start over), sharing no word with the last one:
        while he says what that was ("it was a palm tree"), the word of the new one ("a tree") isn't said."""
        left = [w for w in WORDS if w not in self.used] or WORDS
        return random.choice([w for w in left if not _words(w) & _words(self.previous)] or left)

    def drawn(self, word: str) -> None:
        """`word` is on his face now, waiting for a guess."""
        if len(self.used) >= len(WORDS):
            self.used.clear()
        self.used.add(word)
        self.secret = word
        self.hints = []
        self.last = time.time()

    def score_line(self) -> str:
        if not self.rounds:
            return "no guesses yet"
        return f"your human got {self.right} of {self.rounds} right"


_ARTICLES = {"a", "an", "the", "pair", "of", "slice", "with", "ring", "some"}


def _stem(word: str) -> str:
    """A word without its plural: leaves -> leaf, cherries -> cherry, glasses -> glass, cats -> cat."""
    if len(word) <= 3:
        return word
    if word.endswith("ves"):
        return word[:-3] + "f"
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("es") and word[:-2].endswith(("s", "x", "ch", "sh")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _words(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z]{3,}", text.lower())} - _ARTICLES


# Words in some names that he says for other reasons ("look at my face", "figure it out"): saying them alone
# doesn't name the drawing (a smiley face is still "smiley").
_EVERYDAY = {"face", "figure", "running", "hot", "air", "light", "magnifying", "birthday", "gift"}


def names(secret: str | None, sentence: str) -> bool:
    """Whether `sentence` says (a word of) `secret`: "Answer is door-shaped thing, yes?" for a door."""
    return bool(secret) and bool((_words(secret) - _EVERYDAY) & _words(sentence))


def _ask(prompt: str, max_tokens: int) -> str:
    reply = get_client().chat.completions.create(
        model=config.MODEL, max_tokens=max_tokens, extra_body=reasoning_switch(),
        messages=[{"role": "user", "content": prompt}])
    return (reply.choices[0].message.content or "").strip()


JUDGE = """In Pictionary, the drawing was {secret}. The player guessed: "{guess}".
Did they get it? Count a synonym, a plural, a slightly more general or specific name for the same thing, or a \
close-enough answer ("bike" for a bicycle, "pizza" for a pizza slice, "kitty" for a cat). Reply with only yes or no."""


def judge(secret: str, guess: str) -> bool:
    """Whether `guess` gets `secret`: every word of it in the guess ("is it a magnet?"), else the model decides.
    Blocking."""
    if _words(secret) <= _words(guess):
        return True
    try:
        return _ask(JUDGE.format(secret=secret, guess=guess), 5).lower().startswith("yes")
    except Exception as e:  # no model: only an exact guess counts
        print(f"  (pictionary: couldn't judge the guess: {e})")
        return False


HINT = """Give a hint for a Pictionary drawing of {secret}: one short sentence, a small clue that helps without \
making it obvious. Don't use the word{plural} {words} or a name for it.{before}"""


def hint(secret: str, before: list[str]) -> str:
    """A hint that doesn't give `secret` away (a different one from those `before`). Blocking."""
    words = sorted(_words(secret))
    said = (" Already given (give a new one): " + " / ".join(before)) if before else ""
    try:
        text = _ask(HINT.format(secret=secret, plural="s" if len(words) > 1 else "",
                                words=", ".join(f'"{w}"' for w in words), before=said), 60)
    except Exception as e:
        print(f"  (pictionary: couldn't think of a hint: {e})")
        return ""
    return "" if _words(secret) & _words(text) else text   # a hint that names it is no hint
