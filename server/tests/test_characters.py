"""Characters made in the dashboard: checking, the prompt they become, and the acting cues of Drama 3 voices."""

import unittest

from brain import characters, config, faces, mouth, personality, thinking

VOICE = "0123456789abcdef0123456789abcdef"


def sample(**over):
    ch = {"name": "Juniper", "voice_id": f"https://fish.audio/m/{VOICE}/", "tts_model": "drama-3",
          "prompt": "You are Juniper, an old sea captain who calls {human} first mate and distrusts gadgets.",
          "examples": "[happy] Ahoy, {human}!", "wake_words": ["Hey Juniper", "hey junipur"],
          "lines": {"wake": "Aye?", "sleep": "Going below.", "track_on": "Eyes on you, {human}.", "track_off": "At ease."}}
    ch.update(over)
    return ch


class CleanTest(unittest.TestCase):
    def test_voice_id_from_the_address(self):
        self.assertEqual(characters.clean(sample())["voice_id"], VOICE)

    def test_hey_name_is_always_a_wake_word(self):
        words = characters.clean(sample(wake_words=["hi juniper"]))["wake_words"]
        self.assertEqual(words[0], "hey juniper")
        self.assertIn("hi juniper", words)

    def test_rejects(self):
        for bad in (dict(name="Rocky"), dict(name="J"), dict(voice_id="nope"), dict(tts_model="s9"),
                    dict(prompt="short"), dict(lines={"wake": "Aye?"}), dict(wake_words=["juniper"])):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                characters.clean(sample(**bad))

    def test_examples_start_with_a_face(self):
        f = lambda s: characters._face_first(s, faces.EMOTIONS)
        self.assertEqual(f("[excited, talking fast] Hah!"), "[excited] [talking fast] Hah!")
        self.assertEqual(f("[sighs] Long day."), "[neutral] [sighs] Long day.")
        self.assertEqual(f("[happy] Yes."), "[happy] Yes.")


class PromptTest(unittest.TestCase):
    def test_character_prompt_has_the_robot_rules(self):
        p = personality._character_prompt(characters.clean(sample()))
        self.assertIn("You are Juniper", p)
        self.assertIn(f"Ahoy, {config.HUMAN_NAME}!", p)
        self.assertNotIn("{human}", p)
        self.assertIn("chosen from: neutral, happy", p)
        self.assertIn("`look`", p)


class ActingTest(unittest.TestCase):
    def setUp(self):
        self._acting = config.TTS_ACTING
        config.TTS_ACTING = True

    def tearDown(self):
        config.TTS_ACTING = self._acting

    def test_cues_kept_for_the_voice_and_dropped_for_the_page(self):
        self.assertEqual(mouth.clean_for_acting("[small laugh] Oh, you again!!"), "[small laugh] Oh, you again!")
        self.assertEqual(mouth.without_cues("[sighs] Long <slow>day</slow>."), "Long day .")

    def test_a_tag_split_across_sentences_is_closed_in_each(self):
        self.assertEqual(mouth.clean_for_acting("<whisper>Go on."), "<whisper> Go on.</whisper>")
        self.assertTrue(mouth.clean_for_acting("without me.</whisper>").startswith("<whisper>"))

    def test_face_found_whichever_comes_first(self):
        b = thinking.RobotBrain.__new__(thinking.RobotBrain)
        for text, emotion, rest in (("[happy] [small laugh] Yes.", "happy", "[small laugh] Yes."),
                                    ("[sighs] [sad] Oh no.", "sad", "[sighs] Oh no."),
                                    ("[excited, talking fast] Hah!", "excited", "[talking fast] Hah!"),
                                    ("[sighs] Long day.", "neutral", "[sighs] Long day.")):
            b.emotion = "neutral"
            self.assertEqual(b._take_emotion_tag(text, final=False), (rest, True))
            self.assertEqual(b.emotion, emotion)

    def test_rocky_unchanged(self):
        config.TTS_ACTING = False
        self.assertEqual(mouth.without_cues("[sighs] hi"), "[sighs] hi")
        b = thinking.RobotBrain.__new__(thinking.RobotBrain)
        b.emotion = "neutral"
        self.assertEqual(b._take_emotion_tag("[sighs] hi", final=False), ("hi", True))


if __name__ == "__main__":
    unittest.main()
