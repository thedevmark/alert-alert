import os
import sys
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


if __name__ == "__main__":
    unittest.main()
