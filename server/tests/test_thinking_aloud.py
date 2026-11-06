"""The backstop for the model thinking out loud about its instructions (thinking._thinking_aloud): those sentences
are never spoken and are left out of his memory; his own way of talking ("Question, Sam: ...") is untouched."""

import contextlib
import io
import unittest

from brain.thinking import _thinking_aloud, _without_thinking

LEAKED = [
    'The question is "what is this?" and the detector note says a thumbs down, but that is a gesture sign, and the '
    'question is about seeing.',
    "I should look to get a picture before describing anything.",
    "The note says one person very close.",
    "The user wants a sad face.",
    "This is a question about a physical part and polarity, which needs me to see the capacitor.",
    "This is a knowledge question about a part, so I answer from what I know without looking.",
]
ROCKY = [
    "Question, Sam: is puzzle cube?", "Orange and white faces, black grid lines.", "Answer: eight legs.",
    "Good question, human.", "Let me look.", "The picture is dark, Sam.", "Note: battery low, careful.",
]


class ThinkingAloudTests(unittest.TestCase):
    def test_leaks_are_not_said(self):
        with contextlib.redirect_stdout(io.StringIO()):
            for s in LEAKED:
                self.assertTrue(_thinking_aloud(s), s)
            for s in ROCKY:
                self.assertFalse(_thinking_aloud(s), s)

    def test_memory_keeps_only_what_he_would_say(self):
        text = '[curious] The question is "what is this?" and the detector note says a thumbs down. Let me look.'
        self.assertEqual(_without_thinking(text), "Let me look.")
        self.assertEqual(_without_thinking("I should look to get a picture."), "")


if __name__ == "__main__":
    unittest.main()
