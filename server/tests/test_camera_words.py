"""Which questions take a camera picture (main.wants_camera): questions about seeing, not everyday words."""

import unittest

from brain.main import wants_camera


class CameraWordsTests(unittest.TestCase):
    def test_about_seeing(self):
        for q in ["What do you see?", "Hey Rocky, what can you see right now?", "Tell me what you see.",
                  "What am I holding?", "Rocky, look at this.", "Take a look at my desk.", "How do I look?",
                  "Who is that behind me?", "What color is this?", "Can you read the whiteboard?",
                  "What's on my screen?", "Do you notice anything different?", "How many fingers am I holding up?",
                  "Describe what's in front of you.", "What is this?", "Rocky, what's this?"]:
            self.assertTrue(wants_camera(q), q)

    def test_everyday_words_are_not(self):
        for q in ["Who is Grace?", "How many legs does a spider have?", "See you later, Rocky.",
                  "Let me see, what was I saying?", "Look, I had a rough day.", "I'm watching a movie tonight.",
                  "What is the view like on Erid?", "Who's your best friend?", "Can you notice a pattern here?",
                  "What is a weekend?", "What do you think about that?", "I'm looking at a new job."]:
            self.assertFalse(wants_camera(q), q)


if __name__ == "__main__":
    unittest.main()
