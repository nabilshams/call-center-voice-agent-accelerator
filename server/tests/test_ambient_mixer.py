"""Tier-3 tests for the DSP ambient mixer.

``AmbientMixer`` is a compact DSP object that (a) loads a background WAV
into a normalised float buffer, (b) yields fixed-size 16-bit PCM chunks
looped seamlessly around the buffer, and (c) applies a soft-clip stage
to avoid harsh distortion. These tests exercise the deterministic parts
without hitting real audio I/O beyond the shipped ``office.wav`` /
``callcenter.wav`` fixtures already in the repo.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

from app.handler.ambient_mixer import (
    BYTES_PER_SAMPLE,
    SAMPLE_RATE,
    AmbientMixer,
)


# =========================================================================
# Preset validation + is_enabled
# =========================================================================


class PresetValidationTests(unittest.TestCase):
    def test_unknown_preset_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            AmbientMixer("bogus")
        # Error message should list the valid presets so callers can fix themselves.
        self.assertIn("bogus", str(ctx.exception))
        self.assertIn("none", str(ctx.exception))

    def test_none_preset_is_disabled(self):
        mixer = AmbientMixer("none")
        self.assertFalse(mixer.is_enabled())
        self.assertIsNone(mixer._noise_buffer)

    def test_office_preset_loads_buffer(self):
        mixer = AmbientMixer("office")
        self.assertTrue(mixer.is_enabled())
        self.assertIsInstance(mixer._noise_buffer, np.ndarray)
        self.assertGreater(len(mixer._noise_buffer), 0)

    def test_call_center_preset_loads_buffer(self):
        mixer = AmbientMixer("call_center")
        self.assertTrue(mixer.is_enabled())
        self.assertIsInstance(mixer._noise_buffer, np.ndarray)

    def test_default_preset_is_office(self):
        # Signature default matters -- callers pass no argument and expect
        # a working mixer.
        mixer = AmbientMixer()
        self.assertEqual(mixer.preset, "office")
        self.assertTrue(mixer.is_enabled())


# =========================================================================
# get_ambient_only_chunk -- byte count + silence contract
# =========================================================================


class AmbientChunkTests(unittest.TestCase):
    def test_disabled_mixer_returns_pure_silence(self):
        mixer = AmbientMixer("none")
        # 100 ms at 24 kHz mono = 4800 bytes.
        size = 4800
        chunk = mixer.get_ambient_only_chunk(size)
        self.assertEqual(len(chunk), size)
        self.assertEqual(chunk, b"\x00" * size)

    def test_enabled_mixer_returns_non_silent_chunk_of_exact_size(self):
        mixer = AmbientMixer("office")
        size = 4800
        chunk = mixer.get_ambient_only_chunk(size)
        self.assertEqual(len(chunk), size)
        # Non-silent: at least one byte must be non-zero.
        self.assertNotEqual(chunk, b"\x00" * size)

    def test_chunk_produces_valid_pcm16_bytes(self):
        mixer = AmbientMixer("office")
        size = 4800  # 2400 samples of int16
        chunk = mixer.get_ambient_only_chunk(size)
        arr = np.frombuffer(chunk, dtype=np.int16)
        self.assertEqual(len(arr), size // BYTES_PER_SAMPLE)
        # Soft-clip caps peak below full-scale; every sample must be inside int16.
        self.assertTrue(np.all(np.abs(arr) < 32768))

    def test_ambient_gain_keeps_output_well_below_peak(self):
        # With ambient_gain=0.20 and soft-clip threshold 0.95, the RMS of
        # a chunk should stay comfortably below full-scale int16.
        mixer = AmbientMixer("office")
        chunk = mixer.get_ambient_only_chunk(24000)  # 0.5 s
        arr = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
        rms = float(np.sqrt(np.mean(arr ** 2)))
        # Should be nowhere near full scale (32767).
        self.assertLess(rms, 5000.0, f"ambient too loud: rms={rms}")

    def test_odd_byte_count_truncates_to_whole_samples(self):
        # An odd chunk size loses the trailing byte via integer division.
        mixer = AmbientMixer("office")
        odd_size = 4801
        chunk = mixer.get_ambient_only_chunk(odd_size)
        # The mixer computes num_samples = chunk_size_bytes // BYTES_PER_SAMPLE,
        # then serialises those samples back to bytes. Result: 4800 bytes.
        self.assertEqual(len(chunk), odd_size - 1)


# =========================================================================
# _get_noise_chunk -- looping behaviour
# =========================================================================


class NoiseLoopingTests(unittest.TestCase):
    def test_reads_do_not_go_past_buffer_end(self):
        mixer = AmbientMixer("office")
        # Ask for many small chunks that together are far longer than the
        # buffer; internal position must wrap around without crashing.
        buffer_len = len(mixer._noise_buffer)
        total_samples = buffer_len * 3 + 137  # explicitly cross the boundary
        out = mixer._get_noise_chunk(total_samples)
        self.assertEqual(len(out), total_samples)
        # Position should have wrapped and now be at (total % buffer_len).
        self.assertEqual(mixer._noise_position, total_samples % buffer_len)

    def test_position_advances_on_sub_buffer_read(self):
        mixer = AmbientMixer("office")
        n = 1024
        mixer._get_noise_chunk(n)
        self.assertEqual(mixer._noise_position, n)
        mixer._get_noise_chunk(n)
        self.assertEqual(mixer._noise_position, 2 * n)

    def test_zero_chunk_when_buffer_is_none(self):
        # If the buffer is somehow None (preset='none' path), the helper
        # returns silence rather than raising.
        mixer = AmbientMixer("none")
        out = mixer._get_noise_chunk(512)
        self.assertEqual(len(out), 512)
        self.assertTrue(np.all(out == 0.0))

    def test_looping_is_seamless_across_wrap(self):
        # Two consecutive reads that straddle the wrap point should return
        # the same bytes as one big read of the combined length.
        mixer = AmbientMixer("office")
        buffer_len = len(mixer._noise_buffer)

        # Force position near the end.
        mixer._noise_position = buffer_len - 100
        combined = mixer._get_noise_chunk(400)  # crosses the wrap

        mixer._noise_position = buffer_len - 100
        first = mixer._get_noise_chunk(60)
        second = mixer._get_noise_chunk(340)
        self.assertTrue(np.allclose(combined, np.concatenate([first, second])))


# =========================================================================
# _soft_clip math
# =========================================================================


class SoftClipTests(unittest.TestCase):
    def test_soft_clip_leaves_small_signals_nearly_untouched(self):
        mixer = AmbientMixer("none")
        x = np.array([0.0, 0.1, -0.1, 0.2, -0.2], dtype=np.float32)
        y = mixer._soft_clip(x, threshold=0.95)
        # tanh(0.1/0.95)*0.95 ~ 0.099 -- essentially linear regime.
        self.assertTrue(np.allclose(y, x, atol=0.01))

    def test_soft_clip_bounds_large_signals(self):
        mixer = AmbientMixer("none")
        x = np.array([5.0, -5.0, 10.0, -10.0], dtype=np.float32)
        y = mixer._soft_clip(x, threshold=0.95)
        # Every value must sit at-or-below the threshold in magnitude.
        # (tanh saturates to 1.0 in float32 for large inputs, so the
        # output can equal the threshold exactly, not strictly less.)
        self.assertTrue(np.all(np.abs(y) <= 0.95))
        # And the sign is preserved.
        self.assertTrue(np.all(np.sign(y) == np.sign(x)))

    def test_soft_clip_asymptote_is_threshold(self):
        mixer = AmbientMixer("none")
        # Very large input -> output approaches the threshold from below.
        y = mixer._soft_clip(np.array([1000.0], dtype=np.float32), threshold=0.95)
        self.assertAlmostEqual(float(y[0]), 0.95, places=3)


# =========================================================================
# Synthetic-noise fallback (when audio file is missing / corrupt)
# =========================================================================


class SyntheticNoiseFallbackTests(unittest.TestCase):
    def test_missing_file_falls_back_to_synthetic_noise(self):
        # Patch PRESETS to point at a nonexistent filename; construction
        # should succeed via the synthetic fallback rather than raising.
        with patch.dict(
            AmbientMixer.PRESETS,
            {"office": {"file": "does_not_exist_anywhere.wav"}},
            clear=False,
        ):
            mixer = AmbientMixer("office")
        self.assertTrue(mixer.is_enabled())
        # Buffer is real (synthetic) and non-empty.
        self.assertIsInstance(mixer._noise_buffer, np.ndarray)
        self.assertGreater(len(mixer._noise_buffer), 0)

    def test_synthetic_noise_shape_and_range(self):
        mixer = AmbientMixer("none")
        noise = mixer._generate_synthetic_noise(duration_sec=1.0)
        self.assertEqual(len(noise), SAMPLE_RATE)
        self.assertEqual(noise.dtype, np.float32)
        # Normalised into ~[-0.1, +0.1] range.
        self.assertLessEqual(float(np.max(np.abs(noise))), 0.1 + 1e-6)


if __name__ == "__main__":
    unittest.main()
