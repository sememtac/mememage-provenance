"""EXIF -> origin-fields prefill (mememage.exif).

Unit-tests the value formatting helpers (no image needed) and an end-to-end
extraction from an in-memory JPEG carrying real EXIF. Best-effort: the module
returns {} without Pillow, so the integration test skips if Pillow is absent.
"""

import unittest

from mememage import exif


class FormatHelpers(unittest.TestCase):
    def test_clean_strips_and_skips_binary(self):
        self.assertEqual(exif._clean("  Canon \x00"), "Canon")
        self.assertEqual(exif._clean(b"Nikon"), "Nikon")
        self.assertIsNone(exif._clean(b"\x01\x02\x03"))   # binary -> skipped
        self.assertIsNone(exif._clean(""))
        self.assertEqual(exif._clean(("a", "b")), "a, b")

    def test_snake(self):
        self.assertEqual(exif._snake("ResolutionUnit"), "resolution_unit")
        self.assertEqual(exif._snake("ExifImageWidth"), "exif_image_width")
        self.assertEqual(exif._snake("ISOSpeedRatings"), "iso_speed_ratings")

    def test_gps_decimal(self):
        # 45°31'23.16" N -> 45.523100
        self.assertEqual(exif._gps_decimal((45, 31, 23.16), "N"), "45.523100")
        # West/South are negative
        self.assertTrue(exif._gps_decimal((122, 40, 35.4), "W").startswith("-122."))
        self.assertIsNone(exif._gps_decimal(None, "N"))


class ExtractIntegration(unittest.TestCase):
    def _img_with_exif(self):
        from PIL import Image
        import io
        img = Image.new("RGB", (8, 8), "white")
        ex = Image.Exif()
        ex[271] = "Fujifilm"            # Make
        ex[272] = "X-T4"               # Model
        ex[305] = "Capture One"        # Software
        ex[315] = "Jane Doe"           # Artist
        ex[270] = "a quiet street"     # ImageDescription
        ex[274] = 1                    # Orientation (long-tail tag)
        # Exif sub-IFD (camera/exposure)
        ex[0x8769] = {
            33437: 2.8,                # FNumber
            33434: 0.004,              # ExposureTime (1/250)
            34855: 400,                # ISOSpeedRatings
            37386: 35.0,               # FocalLength
            36867: "2024:06:01 12:00:00",  # DateTimeOriginal
            42036: "XF 35mm f/2",      # LensModel
        }
        buf = io.BytesIO()
        img.save(buf, format="JPEG", exif=ex)
        buf.seek(0)
        return buf

    def test_extracts_clean_common_fields(self):
        try:
            import PIL  # noqa: F401
        except Exception:
            self.skipTest("Pillow not installed")
        import tempfile, os
        buf = self._img_with_exif()
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(buf.read()); path = f.name
        try:
            out = exif.extract_origin_fields(path)
        finally:
            os.unlink(path)
        # IFD0 clean names
        self.assertEqual(out.get("camera"), "Fujifilm X-T4")
        self.assertEqual(out.get("creator"), "Jane Doe")
        self.assertEqual(out.get("software"), "Capture One")
        self.assertEqual(out.get("description"), "a quiet street")
        # long-tail tag comes in under snake_case
        self.assertIn("orientation", out)
        # unset curated tag must NOT leak as the string "None"
        self.assertNotIn("copyright", out)
        self.assertNotEqual(out.get("camera"), "None")
        # IFD pointer offsets must NOT leak in as data
        self.assertNotIn("gps_info", out)
        self.assertNotIn("exif_offset", out)
        # sub-IFD formatted fields (only assert if Pillow wrote the sub-IFD)
        if "aperture" in out:
            self.assertEqual(out["aperture"], "f/2.8")
            self.assertEqual(out["shutter"], "1/250")
            self.assertEqual(out["iso"], "400")
            self.assertEqual(out["focal_length"], "35mm")
            self.assertEqual(out["lens"], "XF 35mm f/2")
            self.assertEqual(out["captured"], "2024:06:01 12:00:00")

    def test_no_exif_returns_empty(self):
        try:
            import PIL  # noqa: F401
        except Exception:
            self.skipTest("Pillow not installed")
        from PIL import Image
        import io, tempfile, os
        buf = io.BytesIO(); Image.new("RGB", (8, 8)).save(buf, format="PNG"); buf.seek(0)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(buf.read()); path = f.name
        try:
            self.assertEqual(exif.extract_origin_fields(path), {})
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
