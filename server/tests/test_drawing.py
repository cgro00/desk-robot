"""Drawing on his face (brain/drawing.py): reading the model's strokes, cleaning them up, the pictures that make it
appear line by line, and the bit layout the robot draws from. No model calls here: the strokes are made up."""

import base64
import json
import unittest

import numpy as np

from brain import config, drawing
from brain.main import fw_at_least

HOUSE = [{"rect": [30, 20, 60, 40]}, {"poly": [30, 20, 60, 2, 90, 20]}, {"rect": [54, 40, 14, 20], "fill": True},
         {"circle": [105, 12, 6]}, {"line": [0, 62, 127, 62]}]


class ParseTests(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(drawing.parse(json.dumps({"strokes": HOUSE})), HOUSE)

    def test_fenced_and_chatty(self):
        text = "Here you go!\n```json\n" + json.dumps({"strokes": HOUSE}) + "\n```"
        self.assertEqual(drawing.parse(text), HOUSE)

    def test_broken_json_keeps_the_whole_strokes(self):
        text = '{"strokes": [{"line": [0, 0, 10, 10]}, {"circle": [20, 20, 5]} {"rect": [1, 2, 3, 4]}, {"poly": [1, 2,'
        self.assertEqual(drawing.parse(text), [{"line": [0, 0, 10, 10]}, {"circle": [20, 20, 5]},
                                               {"rect": [1, 2, 3, 4]}])

    def test_nothing_usable(self):
        self.assertEqual(drawing.parse("I can't draw that."), [])


class PromptTests(unittest.TestCase):
    def test_diagrams_get_the_diagram_rules(self):
        for subject in ("a schematic for a blinking LED", "a simple Circuit with a battery", "a wiring diagram"):
            self.assertIn(drawing.DIAGRAM_RULES, drawing.prompt_for(subject))

    def test_everything_else_does_not(self):
        for subject in ("a cat", "a spaceship", "a circus tent", "Rocky waving"):
            self.assertNotIn(drawing.DIAGRAM_RULES, drawing.prompt_for(subject))
            self.assertIn(subject, drawing.prompt_for(subject))


class CleanTests(unittest.TestCase):
    def test_good_strokes_pass(self):
        out = drawing.clean(HOUSE)
        self.assertEqual(len(out), 5)
        self.assertTrue(out[2]["fill"])
        self.assertEqual(out[1]["poly"], [30, 20, 60, 2, 90, 20])

    def test_junk_is_dropped(self):
        junk = [{"line": [1, 2, 3]}, {"poly": [1, 2, 3]}, {"circle": ["a", 2, 3]}, {"rect": [1, 2, 3, True]},
                {"triangle": [1, 2, 3, 4, 5, 6]}, "line", None, {"circle": [1, 2, float("nan")]}]
        self.assertEqual(drawing.clean(junk), [])

    def test_numbers_are_rounded_and_kept_near_the_screen(self):
        out = drawing.clean([{"line": [10.6, -5000, 99999, 3.2]}])
        self.assertEqual(out[0]["line"], [11, -64, 191, 3])

    def test_too_many_strokes_are_cut(self):
        many = [{"line": [i, 0, i, 10]} for i in range(200)]
        self.assertEqual(len(drawing.clean(many)), config.DRAW_MAX_STROKES)


class FrameTests(unittest.TestCase):
    def setUp(self):
        self.strokes = drawing.clean(HOUSE)

    def test_it_appears_bit_by_bit_and_ends_whole(self):
        pics = drawing.frames(self.strokes, 20)
        self.assertEqual(len(pics), 20)
        lit = [int((p > 0).sum()) for p in pics]
        self.assertEqual(lit, sorted(lit))            # never loses a line
        self.assertLess(lit[0], lit[-1] / 5)          # starts small
        whole = drawing.frames(self.strokes, 1)[0]
        self.assertTrue(np.array_equal(pics[-1], whole))
        self.assertEqual(pics[-1].shape, (64, 128))

    def test_a_filled_box_is_filled_at_the_end(self):
        whole = drawing.frames(self.strokes, 1)[0]
        self.assertEqual(whole[50, 60], 255)          # inside the door
        self.assertEqual(whole[30, 40], 0)            # inside the house: only outlined

    def test_off_screen_bits_dont_break_it(self):
        pics = drawing.frames(drawing.clean([{"circle": [0, 0, 100]}, {"line": [-60, -60, 190, 190]}]), 5)
        self.assertEqual(len(pics), 5)


class XbmTests(unittest.TestCase):
    def test_bit_layout_matches_the_firmware(self):
        img = np.zeros((64, 128), np.uint8)
        img[0, 0] = img[0, 9] = img[1, 0] = img[63, 127] = 255
        bits = base64.standard_b64decode(drawing.xbm(img))
        self.assertEqual(len(bits), 1024)
        self.assertEqual(bits[0], 0b00000001)        # (0, 0): first byte, lowest bit
        self.assertEqual(bits[1], 0b00000010)        # (9, 0): second byte, bit 1
        self.assertEqual(bits[16], 0b00000001)       # (0, 1): next row starts 16 bytes on
        self.assertEqual(bits[1023], 0b10000000)     # (127, 63): last byte, top bit
        self.assertEqual(sum(bin(b).count("1") for b in bits), 4)

    def test_a_message_fits_easily(self):
        whole = drawing.frames(drawing.clean(HOUSE), 1)[0]
        msg = json.dumps({"type": "drawing", "xbm": drawing.xbm(whole), "ms": 10000})
        self.assertLess(len(msg), 1500)


class FirmwareVersionTests(unittest.TestCase):
    def test_versions(self):
        self.assertTrue(fw_at_least("0.4.4", (0, 4, 4)))
        self.assertTrue(fw_at_least("0.5.0", (0, 4, 4)))
        self.assertTrue(fw_at_least("1.0", (0, 4, 4)))
        self.assertFalse(fw_at_least("0.4.3", (0, 4, 4)))
        self.assertFalse(fw_at_least("", (0, 4, 4)))
        self.assertFalse(fw_at_least("?", (0, 4, 4)))


if __name__ == "__main__":
    unittest.main()
