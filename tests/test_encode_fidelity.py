"""An encode is lossless everywhere except the 2 bar rows.

The bar changes 2 rows of pixels. Everything else about the image — its pixels,
text chunks, EXIF (camera, date, orientation), physical size, and colour profile
— is the caller's data, and an encode must hand it all back. Where that is
impossible, encode REFUSES instead of returning a different image.

These tests came from an audit (2026-07-24) that found the opposite: encode was
wiping EXIF, dropping PNG text chunks on the `api.encode` path only, losing DPI,
flattening animations to one frame, and turning a 16-bit greyscale image into a
near-white one (Pillow's I;16 -> RGB clips at 255 instead of scaling). A
provenance tool must never silently return an image that differs from the one it
was given, so each of those is now either preserved or refused.

Needs Pillow.
"""
import os
import shutil
import tempfile
import unittest

try:
    from PIL import Image, PngImagePlugin
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

import mememage
from mememage import bar

FIELDS = {"probe": "fidelity"}
ID, CH = "mememage-0123456789abcdef", "fedcba9876543210"


def _base(w=600, h=120, seed=1):
    import random
    r = random.Random(seed)
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = (r.randint(30, 220), r.randint(30, 220), r.randint(30, 220))
    return im


@unittest.skipUnless(HAS_PIL, "Pillow not installed")
class TestEncodeRefusesWhatItCannotDoLosslessly(unittest.TestCase):

    def test_refuses_16bit_greyscale(self):
        """Pillow clips I;16 -> RGB at 255, so the whole image would go white."""
        src = Image.new("I;16", (600, 120))
        px = src.load()
        for y in range(120):
            for x in range(600):
                px[x, y] = (x * 97 + y * 31) % 65536
        with self.assertRaises(ValueError) as cm:
            bar.embed_into(src, ID, CH)
        self.assertIn("8-bit", str(cm.exception))

    def test_refuses_high_depth_modes(self):
        for mode in ("I", "I;16", "I;16B", "F"):
            with self.subTest(mode=mode):
                with self.assertRaises(ValueError):
                    bar.embed_into(Image.new(mode, (600, 120)), ID, CH)

    def test_refuses_animation(self):
        d = tempfile.mkdtemp()
        try:
            for ext in (".png", ".gif"):
                with self.subTest(ext=ext):
                    p = os.path.join(d, "anim" + ext)
                    frames = [_base(seed=i) for i in range(1, 4)]
                    frames[0].save(p, save_all=True, append_images=frames[1:], duration=100)
                    with self.assertRaises(ValueError) as cm:
                        mememage.encode(p, FIELDS, out=os.path.join(d, "out" + ext + ".png"))
                    self.assertIn("frame", str(cm.exception))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_still_images_are_not_refused(self):
        """The guard must not fire on an ordinary image."""
        self.assertIsNotNone(bar.embed_into(_base(), ID, CH))


@unittest.skipUnless(HAS_PIL, "Pillow not installed")
class TestEncodePreservesMetadata(unittest.TestCase):

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _p(self, name):
        return os.path.join(self.d, name)

    def test_exif_survives_including_orientation(self):
        src = self._p("photo.jpg")
        im = _base()
        ex = im.getexif()
        ex[274] = 6                       # Orientation
        ex[271] = "MememageCam"           # Make
        ex[306] = "2026:07:24 12:00:00"   # DateTime
        im.save(src, "JPEG", quality=95, exif=ex)

        rec = mememage.encode(src, FIELDS)
        out = dict(Image.open(rec.image_path).getexif())
        self.assertEqual(out.get(274), 6, "orientation lost — the photo would display unrotated")
        self.assertEqual(out.get(271), "MememageCam")
        self.assertEqual(out.get(306), "2026:07:24 12:00:00")

    def test_png_text_chunks_survive_on_both_write_paths(self):
        """They used to disagree: embed_bar kept them, api.encode dropped them."""
        info = PngImagePlugin.PngInfo()
        info.add_text("parameters", "a cat, seed 42")
        info.add_itxt("XML:com.adobe.xmp", "<x:xmpmeta/>")

        a = self._p("api.png")
        _base().save(a, pnginfo=info)
        mememage.encode(a, FIELDS, out=self._p("api_out.png"))
        api_text = dict(Image.open(self._p("api_out.png")).text)

        b = self._p("bar.png")
        _base().save(b, pnginfo=info)
        bar.embed_bar(b, ID, CH)
        bar_text = dict(Image.open(b).text)

        for name, text in (("api.encode", api_text), ("bar.embed_bar", bar_text)):
            with self.subTest(path=name):
                self.assertEqual(text.get("parameters"), "a cat, seed 42")
                self.assertEqual(text.get("XML:com.adobe.xmp"), "<x:xmpmeta/>")

    def test_dpi_survives(self):
        src = self._p("dpi.png")
        _base().save(src, dpi=(300, 300))
        mememage.encode(src, FIELDS, out=self._p("dpi_out.png"))
        dpi = Image.open(self._p("dpi_out.png")).info.get("dpi")
        self.assertIsNotNone(dpi)
        self.assertAlmostEqual(dpi[0], 300, places=2)

    def test_icc_profile_survives(self):
        icc_path = next((c for c in (
            "/System/Library/ColorSync/Profiles/Display P3.icc",
            "/System/Library/ColorSync/Profiles/sRGB Profile.icc",
        ) if os.path.exists(c)), None)
        if not icc_path:
            self.skipTest("no system ICC profile available")
        icc = open(icc_path, "rb").read()
        src = self._p("icc.png")
        _base().save(src, icc_profile=icc)
        mememage.encode(src, FIELDS, out=self._p("icc_out.png"))
        self.assertEqual(Image.open(self._p("icc_out.png")).info.get("icc_profile"), icc)

    def test_only_the_two_bar_rows_change(self):
        src = _base(600, 120)
        out = bar.embed_into(src, ID, CH)
        changed = [y for y in range(120)
                   if any(src.getpixel((x, y)) != out.getpixel((x, y)) for x in range(600))]
        self.assertEqual(changed, [118, 119])


if __name__ == "__main__":
    unittest.main()
