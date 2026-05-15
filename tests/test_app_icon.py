import io
import os
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

import scripts.generate_app_icon as gen


class TestCanvas(unittest.TestCase):
    def test_render_master_returns_rgba_at_supersample_size(self):
        master = gen.render_master()
        self.assertEqual(master.mode, "RGBA")
        self.assertEqual(master.size, (gen.RENDER_SIZE, gen.RENDER_SIZE))

    def test_outer_corner_is_outer_color(self):
        master = gen.render_master()
        # Top-left corner is fully transparent (outside the rounded rect)
        self.assertEqual(master.getpixel((0, 0))[3], 0)

    def test_canvas_center_is_inner_panel_color(self):
        master = gen.render_master()
        cx = cy = gen.RENDER_SIZE // 2
        r, g, b, a = master.getpixel((cx, cy))
        # Inner panel #162334 = (22, 35, 52); allow a couple-unit tolerance for AA
        self.assertEqual(a, 255)
        self.assertLess(abs(r - 22), 3)
        self.assertLess(abs(g - 35), 3)
        self.assertLess(abs(b - 52), 3)


class TestBangStems(unittest.TestCase):
    def test_left_stem_center_is_amber(self):
        master = gen.render_master()
        # Stem 1024-space spans x∈[296,400], y∈[180,440]. Center ~ (348, 310).
        x = int(gen._scale(348))
        y = int(gen._scale(310))
        r, g, b, _ = master.getpixel((x, y))
        self.assertLess(abs(r - 255), 3)
        self.assertLess(abs(g - 181), 3)
        self.assertLess(abs(b - 71), 3)

    def test_right_stem_center_is_ivory(self):
        master = gen.render_master()
        # Stem 1024-space spans x∈[624,728], y∈[180,440]. Center ~ (676, 310).
        x = int(gen._scale(676))
        y = int(gen._scale(310))
        r, g, b, _ = master.getpixel((x, y))
        self.assertLess(abs(r - 238), 3)
        self.assertLess(abs(g - 244), 3)
        self.assertLess(abs(b - 250), 3)


class TestEyes(unittest.TestCase):
    def test_left_eye_center_is_amber(self):
        master = gen.render_master()
        x = int(gen._scale(348))
        y = int(gen._scale(522))
        r, g, b, _ = master.getpixel((x, y))
        self.assertLess(abs(r - 255), 3)
        self.assertLess(abs(g - 181), 3)
        self.assertLess(abs(b - 71), 3)

    def test_right_eye_center_is_ivory(self):
        master = gen.render_master()
        x = int(gen._scale(676))
        y = int(gen._scale(522))
        r, g, b, _ = master.getpixel((x, y))
        self.assertLess(abs(r - 238), 3)
        self.assertLess(abs(g - 244), 3)
        self.assertLess(abs(b - 250), 3)

    def test_between_eyes_is_inner_panel(self):
        master = gen.render_master()
        # Midway between the eyes should still be inner-panel color
        x = int(gen._scale(512))
        y = int(gen._scale(522))
        r, g, b, _ = master.getpixel((x, y))
        self.assertLess(abs(r - 22), 3)
        self.assertLess(abs(g - 35), 3)
        self.assertLess(abs(b - 52), 3)


class TestSmile(unittest.TestCase):
    def test_smile_left_end_is_amber_ish(self):
        master = gen.render_master()
        # Smile path: M 320 730 Q 512 878 704 730, stroke 52, round cap.
        # Sample inside the left cap, which should be amber-side gradient.
        x = int(gen._scale(322))
        y = int(gen._scale(730))
        r, g, b, _ = master.getpixel((x, y))
        # Amber dominant: red >> blue
        self.assertGreater(r, 200)
        self.assertGreater(g, 140)
        self.assertLess(b, 120)

    def test_smile_right_end_is_ivory_ish(self):
        master = gen.render_master()
        x = int(gen._scale(702))
        y = int(gen._scale(730))
        r, g, b, _ = master.getpixel((x, y))
        # Ivory dominant: balanced high RGB
        self.assertGreater(r, 220)
        self.assertGreater(g, 220)
        self.assertGreater(b, 220)

    def test_smile_bottom_is_mixed_gradient(self):
        master = gen.render_master()
        # Smile centerline bottom is at y≈804 (Bezier midpoint); stroke half-width 26
        # puts the stroke's bottom edge near y=830. Sample y=820 — inside the stroke.
        x = int(gen._scale(512))
        y = int(gen._scale(820))
        r, g, b, a = master.getpixel((x, y))
        self.assertEqual(a, 255)
        # Mid-gradient should sit between amber (255,181,71) and ivory (238,244,250).
        self.assertGreater(r, 230)
        self.assertGreater(g, 200)
        self.assertLess(g, 244)


class TestOutputArtifacts(unittest.TestCase):
    """Render to a temp dir so tests never touch the committed assets."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.png_path = self.tmpdir / "logo.png"
        self.ico_path = self.tmpdir / "favicon.ico"
        master = gen.render_master()
        gen.write_png(master, self.png_path, gen.TARGET_SIZE)
        gen.write_ico(master, self.ico_path, gen.ICO_SIZES)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_png_is_correct_size_and_mode(self):
        with Image.open(self.png_path) as im:
            im.load()
            self.assertEqual(im.size, (1024, 1024))
            self.assertEqual(im.mode, "RGBA")

    def test_ico_contains_all_expected_sizes(self):
        # Parse the ICO header: 6-byte ICONDIR + 16-byte ICONDIRENTRY per image.
        data = self.ico_path.read_bytes()
        reserved, image_type, count = struct.unpack("<HHH", data[:6])
        self.assertEqual(reserved, 0)
        self.assertEqual(image_type, 1)  # 1 = ICO, 2 = CUR
        self.assertEqual(count, len(gen.ICO_SIZES))

        embedded_sizes = []
        for i in range(count):
            entry = data[6 + i * 16 : 6 + (i + 1) * 16]
            w, h = entry[0], entry[1]
            # 0 in the byte means 256 (ICO format quirk).
            embedded_sizes.append((w or 256, h or 256))
        for size in gen.ICO_SIZES:
            self.assertIn((size, size), embedded_sizes)

    def test_render_is_deterministic(self):
        a = io.BytesIO()
        b = io.BytesIO()
        gen.render_master().resize((256, 256), Image.LANCZOS).save(a, format="PNG", optimize=True)
        gen.render_master().resize((256, 256), Image.LANCZOS).save(b, format="PNG", optimize=True)
        self.assertEqual(a.getvalue(), b.getvalue())


if __name__ == "__main__":
    unittest.main()
