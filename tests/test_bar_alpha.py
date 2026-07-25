"""The bar embed keeps an alpha channel, and normalizes every other mode to RGB.

An alpha channel is the caller's data: a texture's mask, a cut-out's
transparency. The embed used to convert every source to RGB, which dropped that
data silently — and it diverged from the JS SDK, which always kept alpha and
wrote alpha 255 only on the bar rows. These tests lock the corrected contract:

  * a source with alpha (RGBA / LA / La / PA / RGBa / P+transparency) keeps it,
    and only the 2 bar rows go opaque;
  * a source without alpha (RGB / L / CMYK / 1 / …) still becomes RGB, because
    the bar's bands are coloured;
  * the caller's image is never mutated;
  * Python and the JS SDK produce byte-identical RGBA output.

Needs Pillow. The parity test also needs Node, and skips cleanly without it.
"""
import json
import os
import random
import shutil
import subprocess
import tempfile
import unittest

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from mememage import bar

HERE = os.path.dirname(os.path.abspath(__file__))
SDK = os.path.join(HERE, "..", "packaging", "js", "src", "index.js")
NODE = shutil.which("node")

ID = "mememage-0123456789abcdef"
CH = "fedcba9876543210"
W, H = 512, 64


def _noisy_rgba(w=W, h=H, seed=3):
    """Textured RGBA with a real (non-uniform) alpha mask."""
    rng = random.Random(seed)
    img = Image.new("RGBA", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = (rng.randint(40, 220), rng.randint(40, 220), rng.randint(40, 220),
                        0 if (x + y) % 3 else 200)
    return img


@unittest.skipUnless(HAS_PIL, "Pillow not installed")
class TestBarAlpha(unittest.TestCase):

    def test_rgba_keeps_alpha_outside_the_bar(self):
        src = _noisy_rgba()
        out = bar.embed_into(src, ID, CH)

        self.assertEqual(out.mode, "RGBA")
        self.assertEqual(bar.extract_bar(out), (ID, CH))

        a_src, a_out = src.split()[3], out.split()[3]
        for y in range(H - 2):                       # content rows: alpha untouched
            for x in range(0, W, 5):
                self.assertEqual(a_out.getpixel((x, y)), a_src.getpixel((x, y)),
                                 f"alpha changed at ({x}, {y})")
        for y in (H - 2, H - 1):                     # bar rows: opaque
            for x in range(0, W, 5):
                self.assertEqual(a_out.getpixel((x, y)), 255)

    def test_alpha_modes_become_rgba(self):
        """Includes 'La', which PIL cannot convert to RGBA in one step."""
        rgba = _noisy_rgba()
        for mode in ("LA", "La", "PA", "RGBa"):
            with self.subTest(mode=mode):
                out = bar.embed_into(rgba.convert(mode), ID, CH)
                self.assertEqual(out.mode, "RGBA")
                self.assertEqual(bar.extract_bar(out), (ID, CH))

    def test_palette_with_transparency_becomes_rgba(self):
        src = _noisy_rgba().convert("RGB").convert("P")
        src.info["transparency"] = 0
        out = bar.embed_into(src, ID, CH)
        self.assertEqual(out.mode, "RGBA")
        self.assertEqual(bar.extract_bar(out), (ID, CH))

    def test_modes_without_alpha_become_rgb(self):
        """The bands are coloured, so greyscale / palette / CMYK must normalize."""
        rgba = _noisy_rgba()
        for mode in ("RGB", "L", "P", "CMYK", "1"):
            with self.subTest(mode=mode):
                out = bar.embed_into(rgba.convert(mode), ID, CH)
                self.assertEqual(out.mode, "RGB")
                self.assertEqual(bar.extract_bar(out), (ID, CH))

    def test_caller_image_is_not_mutated(self):
        src = _noisy_rgba()
        before = src.tobytes()
        out = bar.embed_into(src, ID, CH)
        self.assertIsNot(out, src)
        self.assertEqual(src.mode, "RGBA")
        self.assertEqual(src.tobytes(), before)

    def test_encode_writes_an_rgba_png_that_still_verifies(self):
        from mememage import api
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "texture.png")
            _noisy_rgba().save(path)
            rec = api.encode(path, {"asset": "rock_albedo"})
            self.assertEqual(Image.open(rec.image_path).mode, "RGBA")
            self.assertTrue(api.verify(rec.image_path, rec.record).match)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    @unittest.skipUnless(NODE, "node not installed")
    def test_js_sdk_produces_identical_rgba_bytes(self):
        """Python is the reference; the JS SDK must match it on all 4 channels."""
        src = _noisy_rgba()
        py = bar.embed_into(src, ID, CH)

        script = """
        const { readFileSync, writeFileSync } = await import("node:fs");
        const { embedBarPayload, packPayload } = await import(process.argv[1]);
        const inp = JSON.parse(readFileSync(process.argv[2], "utf8"));
        const px = Uint8ClampedArray.from(inp.rgba);
        embedBarPayload(px, inp.w, inp.h, packPayload(inp.identifier, inp.content_hash));
        writeFileSync(process.argv[3], JSON.stringify(Array.from(px)));
        """
        d = tempfile.mkdtemp()
        try:
            inp = os.path.join(d, "in.json")
            outp = os.path.join(d, "out.json")
            with open(inp, "w") as f:
                json.dump({"rgba": list(src.tobytes()), "w": W, "h": H,
                           "identifier": ID, "content_hash": CH}, f)
            codec = os.path.join(os.path.dirname(SDK), "codec.js")
            res = subprocess.run(
                [NODE, "--input-type=module", "-e", script, codec, inp, outp],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(res.returncode, 0, res.stderr)
            with open(outp) as f:
                js_bytes = bytes(json.load(f))
            self.assertEqual(js_bytes, py.tobytes(),
                             "JS and Python barred RGBA bytes differ")
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
