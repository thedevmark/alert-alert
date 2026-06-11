"""Regression tests for the pure export-command builder and small helpers.

These cover the ffmpeg pipeline corners that broke during development:
crop/scale/pad, audio mapping, normalize, fades (+ duration), the still-image
override, and the freeze-frame end-buffer. Run: python -m unittest discover tests
"""
import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import native_app
from native_app import build_export_cmd, safe_output_stem, fade_gain, MainWindow


def cmd_str(**kw):
    base = dict(src="in.mp4", out="out.mp4", crop=(10, 20, 300, 400),
                trim=(1.0, 4.0), out_size=720, crf=23, normalize=False)
    base.update(kw)
    return " ".join(build_export_cmd(**base))


class TestBuildExportCmd(unittest.TestCase):
    def test_video_crop_scale_pad_and_maps(self):
        s = cmd_str()
        self.assertIn("crop=300:400:10:20", s)
        self.assertIn("scale=720:720:force_original_aspect_ratio=decrease", s)
        self.assertIn("pad=720:720", s)
        self.assertIn("-map 0:v:0", s)
        self.assertIn("-ss 1.000", s); self.assertIn("-to 4.000", s)

    def test_normalize_adds_loudnorm(self):
        self.assertIn("loudnorm=I=-16:TP=-1.5", cmd_str(normalize=True))
        self.assertNotIn("loudnorm", cmd_str(normalize=False))

    def test_audio_fades_independent_in_and_out(self):
        s = cmd_str(trim=(0.0, 5.0), afade_in=0.25, afade_out=1.0)
        self.assertIn("afade=t=in:st=0:d=0.25", s)
        self.assertIn("afade=t=out:st=4.000:d=1.0", s)

    def test_audio_fade_in_only(self):
        s = cmd_str(trim=(0.0, 5.0), afade_in=0.5, afade_out=0.0)
        self.assertIn("afade=t=in:st=0:d=0.5", s)
        self.assertNotIn("afade=t=out", s)

    def test_no_fades_has_no_afade_or_vfade(self):
        s = cmd_str()
        self.assertNotIn("afade", s)
        self.assertNotIn("fade=t=in", s)
        self.assertNotIn("fade=t=out", s)

    def test_visual_fades_independent_in_and_out(self):
        s = cmd_str(trim=(0.0, 5.0), vfade_in=0.5, vfade_out=1.0)
        self.assertIn("fade=t=in:st=0:d=0.5", s)
        self.assertIn("fade=t=out:st=4.000:d=1.0", s)

    def test_visual_fade_out_accounts_for_end_buffer(self):
        # total = trim_len(5) + buffer(3) = 8, so fade-out starts at 8 - 1 = 7
        s = cmd_str(trim=(0.0, 5.0), end_buffer=3, vfade_out=1.0)
        self.assertIn("fade=t=out:st=7.000:d=1.0", s)

    def test_end_buffer_freezes_video_only(self):
        s = cmd_str(end_buffer=3)
        self.assertIn("tpad=stop_mode=clone:stop_duration=3", s)
        # apad on audio + tpad on video deadlocks ffmpeg — must not be paired
        self.assertNotIn("apad", s)

    def test_image_override_loops_and_covers_without_source_crop(self):
        s = cmd_str(image_src="pic.png")
        self.assertIn("-loop 1", s)
        self.assertIn("force_original_aspect_ratio=increase", s)  # cover-scale
        self.assertNotIn("crop=300:400", s)  # the draggable crop is ignored for stills

    def test_audio_override_adds_second_input_and_maps_it(self):
        s = cmd_str(audio_src="music.mp3")
        self.assertIn("music.mp3", s)
        self.assertIn("-map 1:a:0?", s)


class TestSafeOutputStem(unittest.TestCase):
    def test_blank_returns_empty(self):
        self.assertEqual(safe_output_stem(""), "")
        self.assertEqual(safe_output_stem("   "), "")

    def test_strips_invalid_chars_and_extension(self):
        self.assertEqual(safe_output_stem('my: clip?.mp4'), "my clip")

    def test_drops_path_components_no_traversal(self):
        self.assertEqual(safe_output_stem("a/b\\c"), "c")
        self.assertEqual(safe_output_stem("../../etc/passwd"), "passwd")

    def test_caps_length(self):
        self.assertLessEqual(len(safe_output_stem("x" * 200)), 60)


class TestFadeGain(unittest.TestCase):
    def test_no_fade_is_unity(self):
        self.assertEqual(fade_gain(2.0, 0.0, 5.0, 0.0, 0.0), 1.0)

    def test_fade_in_ramps_from_zero(self):
        self.assertEqual(fade_gain(0.0, 0.0, 5.0, 1.0, 0.0), 0.0)
        self.assertAlmostEqual(fade_gain(0.5, 0.0, 5.0, 1.0, 0.0), 0.5)
        self.assertEqual(fade_gain(2.0, 0.0, 5.0, 1.0, 0.0), 1.0)

    def test_fade_out_ramps_to_zero_at_end(self):
        self.assertAlmostEqual(fade_gain(4.5, 0.0, 5.0, 0.0, 1.0), 0.5)
        self.assertEqual(fade_gain(5.0, 0.0, 5.0, 0.0, 1.0), 0.0)

    def test_respects_trim_offset(self):
        # trim_in=10 -> at t=10 (start) fade-in gain is 0
        self.assertEqual(fade_gain(10.0, 10.0, 15.0, 1.0, 0.0), 0.0)
        self.assertAlmostEqual(fade_gain(10.5, 10.0, 15.0, 1.0, 0.0), 0.5)


class TestShort(unittest.TestCase):
    def test_short_keeps_under_limit(self):
        self.assertEqual(MainWindow._short("clip", 26), "clip")

    def test_short_truncates_with_ellipsis(self):
        out = MainWindow._short("x" * 40, 26)
        self.assertEqual(len(out), 26)
        self.assertTrue(out.endswith("…"))


if __name__ == "__main__":
    unittest.main()
