"""Rocky's character file and the speaker tuning his voice goes through."""

import unittest

import numpy as np

from brain import config, mouth, personality


class CharacterTests(unittest.TestCase):
    def test_prompt_is_complete(self):
        self.assertIn(config.HUMAN_NAME, personality.ROCKY_PROMPT)
        self.assertIn(f"One to {config.REPLY_MAX_SENTENCES} sentences", personality.ROCKY_PROMPT)
        self.assertIn("[neutral]", personality.ROCKY_PROMPT)
        self.assertIn(config.HUMAN_NAME, personality.SYSTEM_PROMPT)   # whoever is active

    def test_every_canned_line_exists(self):
        for lines in (personality.ROCKY_LINES, personality.LINES):
            for key in ("wake", "sleep", "track_on", "track_off"):
                self.assertTrue(lines.get(key), f"missing the {key} line")

    def test_voice_settings(self):
        self.assertEqual(len(config.TTS_VOICE_ID), 32)
        self.assertIn(f"hey {config.ROBOT_NAME.lower()}", config.WAKE_PHRASES)
        self.assertIn(config.ROBOT_NAME, config.STT_PROMPT)


class SpeakerTuningTests(unittest.TestCase):
    def test_equalizer_cuts_bass_and_lifts_presence(self):
        sr = mouth.SAMPLE_RATE
        t = np.arange(sr) / sr
        x = (0.3 * np.sin(2 * np.pi * 100 * t) + 0.3 * np.sin(2 * np.pi * 3000 * t)).astype(np.float32)
        y = mouth.Equalizer(300, 6.0).process(x)
        spec_x, spec_y = np.abs(np.fft.rfft(x)) ** 2, np.abs(np.fft.rfft(y)) ** 2
        f = np.fft.rfftfreq(len(x), 1 / sr)
        bass = lambda s: s[(f > 50) & (f < 150)].sum()
        top = lambda s: s[(f > 2900) & (f < 3100)].sum()
        self.assertLess(10 * np.log10(bass(spec_y) / bass(spec_x)), -15)
        self.assertGreater(10 * np.log10(top(spec_y) / top(spec_x)), 4)

    def test_equalizer_off_is_passthrough_and_chunks_join_cleanly(self):
        x = np.random.default_rng(1).normal(0, 0.1, 4000).astype(np.float32)
        self.assertTrue(np.array_equal(mouth.Equalizer(0, 0).process(x), x))
        whole = mouth.Equalizer(300, 6.0).process(x)
        eq = mouth.Equalizer(300, 6.0)
        chunked = np.concatenate([eq.process(x[:1234]), eq.process(x[1234:])])
        self.assertTrue(np.allclose(whole, chunked, atol=1e-6))

    def test_cone_protection_turns_down_only_the_lows(self):
        sr = mouth.SAMPLE_RATE
        t = np.arange(sr) / sr
        f = np.fft.rfftfreq(sr, 1 / sr)
        x = (0.4 * np.sin(2 * np.pi * 400 * t) + 0.1 * np.sin(2 * np.pi * 2500 * t)).astype(np.float32)
        pcm = (x * 32767).astype(np.int16).tobytes()
        saved = (config.TTS_CONE_LIMIT, config.TTS_AUTO_PRESENCE, config.TTS_PRESENCE_DB)
        try:
            config.TTS_AUTO_PRESENCE, config.TTS_PRESENCE_DB = False, 0.0
            out = {}
            for limit in (0.0, 0.02):
                config.TTS_CONE_LIMIT = limit
                lev = mouth.Leveler()
                y = np.frombuffer(b"".join(lev.process(pcm[i:i + 4096]) for i in range(0, len(pcm), 4096)) + lev.flush(),
                                  np.int16)
                s = np.abs(np.fft.rfft(y.astype(np.float32))) ** 2
                out[limit] = (s[(f > 390) & (f < 410)].sum(), s[(f > 2490) & (f < 2510)].sum())
            low_drop = 10 * np.log10(out[0.02][0] / out[0.0][0])
            high_drop = 10 * np.log10(out[0.02][1] / out[0.0][1])
            self.assertLess(low_drop - high_drop, -3)   # the lows come down relative to the highs
        finally:
            config.TTS_CONE_LIMIT, config.TTS_AUTO_PRESENCE, config.TTS_PRESENCE_DB = saved

    def test_auto_presence_lifts_a_dark_voice_only(self):
        sr = mouth.SAMPLE_RATE
        t = np.arange(sr * 3) / sr
        dark = (0.3 * np.sin(2 * np.pi * 500 * t) + 0.05 * np.sin(2 * np.pi * 2000 * t)).astype(np.float32)
        bright = (0.1 * np.sin(2 * np.pi * 500 * t) + 0.2 * np.sin(2 * np.pi * 2000 * t)).astype(np.float32)
        lifts = []
        for i, x in enumerate((dark, bright)):
            eq = mouth.Equalizer(300, 0.0, auto=True, voice=f"test-{i}")
            for k in range(0, len(x), 2048):
                eq.process(x[k:k + 2048])
            lifts.append(eq.learned)
        mouth._LEARNED_PRESENCE.pop("test-0", None), mouth._LEARNED_PRESENCE.pop("test-1", None)
        self.assertGreater(lifts[0], 3)
        self.assertEqual(lifts[1], 0)

    def test_leveler_reads_config_when_built(self):
        saved = (config.TTS_LEVEL, config.TTS_HIGHPASS_HZ, config.TTS_PRESENCE_DB)
        try:
            config.TTS_LEVEL, config.TTS_HIGHPASS_HZ, config.TTS_PRESENCE_DB = 0.22, 300.0, 6.0
            lev = mouth.Leveler()
            self.assertEqual(lev.level, 0.22)
            self.assertEqual(len(lev.eq.stages), 2)       # the bass cut
            self.assertIsNotNone(lev.eq.shelf)                # the presence lift
            config.TTS_HIGHPASS_HZ, config.TTS_PRESENCE_DB = 0.0, 0.0
            mouth._LEARNED_PRESENCE.clear()
            off = mouth.Leveler().eq
            self.assertEqual((len(off.stages), off.shelf), (0, None))
        finally:
            config.TTS_LEVEL, config.TTS_HIGHPASS_HZ, config.TTS_PRESENCE_DB = saved


if __name__ == "__main__":
    unittest.main()
