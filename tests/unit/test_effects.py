import unittest
import numpy as np
from vocascade.audio.effects import (
    apply_pitch_shift,
    apply_tremolo,
    apply_overdrive,
    apply_bitcrush,
    apply_stutter,
    apply_effect_chain,
)

class TestAudioEffects(unittest.TestCase):
    def setUp(self):
        # Create a simple 1-second sine wave at 440Hz, sample rate 32000
        self.sample_rate = 32000
        t = np.arange(self.sample_rate) / float(self.sample_rate)
        self.sine_wave = (np.sin(2 * np.pi * 440 * t) * 16384).astype(np.int16)

    def test_pitch_shift(self):
        # Zero shift should return original array
        out = apply_pitch_shift(self.sine_wave, 0.0)
        np.testing.assert_array_equal(out, self.sine_wave)

        # Positive shift should reduce length (since sample rate is fixed, but pitch goes up)
        # Wait, pitch_factor = 2 ** (semitones/12) -> > 1, so step is larger, length is smaller.
        out_up = apply_pitch_shift(self.sine_wave, 12.0)
        self.assertLess(len(out_up), len(self.sine_wave))

        # Negative shift should increase length
        out_down = apply_pitch_shift(self.sine_wave, -12.0)
        self.assertGreater(len(out_down), len(self.sine_wave))

    def test_tremolo(self):
        # Zero depth tremolo should return original array
        out = apply_tremolo(self.sine_wave, 10.0, 0.0)
        np.testing.assert_array_equal(out, self.sine_wave)

        # Active tremolo should modify the wave
        out_active = apply_tremolo(self.sine_wave, 10.0, 0.8)
        self.assertEqual(len(out_active), len(self.sine_wave))
        self.assertFalse(np.array_equal(out_active, self.sine_wave))
        # Tremolo shouldn't increase peak amplitude beyond original
        self.assertLessEqual(np.max(np.abs(out_active)), np.max(np.abs(self.sine_wave)))

    def test_overdrive(self):
        # Gain <= 1.0 should return original
        out = apply_overdrive(self.sine_wave, 1.0)
        np.testing.assert_array_equal(out, self.sine_wave)

        # Gain > 1.0 should apply overdrive
        out_clip = apply_overdrive(self.sine_wave, 5.0)
        self.assertEqual(len(out_clip), len(self.sine_wave))
        # The clipped signal should hit maximum or minimum limits (-32768, 32767)
        self.assertTrue(np.any(out_clip == 32767) or np.any(out_clip == -32768))

    def test_bitcrush(self):
        # 16-bit depth should return original
        out = apply_bitcrush(self.sine_wave, 16)
        np.testing.assert_array_equal(out, self.sine_wave)

        # 4-bit depth should reduce resolution
        out_crushed = apply_bitcrush(self.sine_wave, 4)
        self.assertEqual(len(out_crushed), len(self.sine_wave))
        # Crushing should result in fewer unique values
        self.assertLess(len(np.unique(out_crushed)), len(np.unique(self.sine_wave)))

    def test_stutter(self):
        # Zero repeats or chunk_ms should return original
        out = apply_stutter(self.sine_wave, 50.0, 0)
        np.testing.assert_array_equal(out, self.sine_wave)

        # Active stutter should repeat the segment
        wave_copy = self.sine_wave.copy()
        out_stutter = apply_stutter(wave_copy, 50.0, 2)
        self.assertEqual(len(out_stutter), len(self.sine_wave))
        # Segment starting at stutter_samples * 2 should equal the segment at stutter_samples
        stutter_samples = int(self.sample_rate * (50.0 / 1000.0))
        np.testing.assert_array_equal(
            out_stutter[stutter_samples : stutter_samples * 2],
            out_stutter[stutter_samples * 2 : stutter_samples * 3]
        )

    def test_effect_chain(self):
        pcm_bytes = self.sine_wave.tobytes()

        # Empty config should return original bytes
        out_empty = apply_effect_chain(pcm_bytes, {})
        self.assertEqual(out_empty, pcm_bytes)

        # Config with effects
        config = {
            "pitch_shift": -3.0,
            "tremolo_rate": 5.0,
            "tremolo_depth": 0.5,
            "overdrive_gain": 2.0,
            "bit_depth": 8,
            "stutter_ms": 20.0,
            "stutter_repeats": 2,
        }
        out_bytes = apply_effect_chain(pcm_bytes, config)
        self.assertNotEqual(out_bytes, pcm_bytes)
        self.assertTrue(len(out_bytes) > 0)
