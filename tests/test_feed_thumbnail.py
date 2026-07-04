"""Feed thumbnails crop the bar off and frame an interesting square.

The catalog tile must never show the 2-row steganographic bar, and should
showcase a detailed region rather than a blind center crop. The bar is cropped
from the source BEFORE any scaling, so it can't reappear at any size.
"""

import io
import os
import tempfile
import unittest

try:
    from PIL import Image
    _HAVE_PIL = True
except Exception:
    _HAVE_PIL = False

import mememage.server as srv


@unittest.skipUnless(_HAVE_PIL, "Pillow required")
class FeedThumbnail(unittest.TestCase):
    def setUp(self):
        srv._feed_thumb_cache.clear()
        self.tmp = tempfile.mkdtemp()

    def _make(self, w, h, bar_rgb=(255, 0, 255)):
        """An image whose top half has detail and whose bottom 2 rows are a
        unique 'bar' colour, so we can assert the bar never survives."""
        img = Image.new("RGB", (w, h), (40, 40, 40))
        px = img.load()
        # Some structure in the upper region (so the smart crop has a target).
        for y in range(0, h - 2):
            for x in range(0, w):
                if (x // 8 + y // 8) % 2 == 0:
                    px[x, y] = (200, 180, 90)
        for y in range(h - 2, h):           # the 2-row bar
            for x in range(0, w):
                px[x, y] = bar_rgb
        p = os.path.join(self.tmp, f"{w}x{h}.png")
        img.save(p)
        return p

    def _thumb(self, path, size=120):
        data = srv._feed_thumb_bytes("id-" + os.path.basename(path), path, size=size)
        self.assertIsNotNone(data)
        return Image.open(io.BytesIO(data)).convert("RGB")

    def _bar_fraction(self, im, bar_rgb=(255, 0, 255)):
        px = im.load()
        hit = 0
        for y in range(im.height):
            for x in range(im.width):
                r, g, b = px[x, y]
                if abs(r - bar_rgb[0]) < 60 and abs(g - bar_rgb[1]) < 60 and abs(b - bar_rgb[2]) < 60:
                    hit += 1
        return hit / float(im.width * im.height)

    def test_output_is_square(self):
        for (w, h) in [(300, 120), (120, 300), (256, 256)]:
            im = self._thumb(self._make(w, h), size=100)
            self.assertEqual(im.size, (100, 100), f"{w}x{h}")

    def test_bar_removed_landscape(self):
        im = self._thumb(self._make(320, 120))
        self.assertLess(self._bar_fraction(im), 0.01)

    def test_bar_removed_portrait(self):
        # Portrait is the case that used to leak the bar (center cover-crop kept
        # full height). The square crop + bar removal must drop it entirely.
        im = self._thumb(self._make(120, 320))
        self.assertLess(self._bar_fraction(im), 0.01)

    def test_square_image_bar_removed(self):
        im = self._thumb(self._make(256, 256))
        self.assertLess(self._bar_fraction(im), 0.02)


if __name__ == "__main__":
    unittest.main()
