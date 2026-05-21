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
from native_app import build_export_cmd, MainWindow


def cmd_str(**kw):
    base = dict(src="in.mp4", out="out.mp4", crop=(10, 20, 300, 400),
                trim=(1.0, 4.0), out_size=720, crf=23, normalize=False, fade="none")
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

    def test_fade_both_emits_two_fades_with_duration(self):
        s = cmd_str(fade="both", trim=(0.0, 5.0), fade_dur=1.0)
        self.assertIn("afade=t=in:st=0:d=1.0", s)
        self.assertIn("afade=t=out", s)
        self.assertIn("d=1.0", s)

    def test_fade_none_has_no_afade(self):
        self.assertNotIn("afade", cmd_str(fade="none"))

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


class TestShort(unittest.TestCase):
    def test_short_keeps_under_limit(self):
        self.assertEqual(MainWindow._short("clip", 26), "clip")

    def test_short_truncates_with_ellipsis(self):
        out = MainWindow._short("x" * 40, 26)
        self.assertEqual(len(out), 26)
        self.assertTrue(out.endswith("…"))


if __name__ == "__main__":
    unittest.main()
