"""The JS decoder must recover the M/Y/C anchors on a chroma-diluted bar.

At 0.5x the 8px bands fall to 4px, and JPEG 4:2:0 chroma subsampling smears
them past the strict absolute colour cutoffs — so findHeaderEnd returned null
and the browser could not read ANY half-size copy, even though the data bits
were intact. bar.py fixed this with a soft channel-ordering fallback; this
test pins the codec.js port of it.

Python does the real work (embed -> 0.5x downscale -> real JPEG) and hands the
raw RGBA of the bottom rows to the real codec.js, so the harness cannot lie
about what the browser sees. Photo-like content is required: a smooth gradient
does NOT smear enough to defeat the strict predicates, so a gradient would make
this test vacuous. The harness re-runs strict-only and asserts it FAILS, which
keeps the test honest if the fallback is ever removed.

Drives tests/bar_soft_anchor.cjs. Skips cleanly without Node or Pillow.
"""
import io
import json
import math
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
HARNESS = os.path.join(HERE, "bar_soft_anchor.cjs")
NODE = shutil.which("node")

IDENT = "mememage-a3f8c2d1e5b60718"
HASH = "0f1e2d3c4b5a6978"
CROP_ROWS = 6   # 2 bar rows + the reference row above + margin


def _photo(w, h):
    """Photo-like content: smooth low-frequency structure + per-pixel grain.

    A pure gradient leaves the bands clean enough for the strict predicates.
    """
    img = Image.new("RGB", (w, h))
    px = img.load()
    rnd = random.Random(42)
    for y in range(h):
        for x in range(w):
            base = 120 + 60 * math.sin(x * 0.013) * math.cos(y * 0.017)
            t = rnd.randint(-18, 18)
            px[x, y] = (int(max(0, min(255, base + t))),
                        int(max(0, min(255, base * 0.8 + t))),
                        int(max(0, min(255, base * 0.6 + 40 + t))))
    return img


def _half_scale_jpeg(img, quality):
    w, h = img.size
    small = img.resize((w // 2, h // 2), Image.LANCZOS)
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=quality)   # default 4:2:0 subsampling
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


@unittest.skipUnless(NODE and HAS_PIL, "Node.js + Pillow required")
class TestBarJsSoftAnchor(unittest.TestCase):
    def test_js_decodes_half_scale_jpeg(self):
        strict_only = (bar._BAND_PREDICATE_PASSES[0],)
        cases = []
        for quality in (80, 70):
            barred = bar.embed_into(_photo(2048, 1152), IDENT, HASH)
            out = _half_scale_jpeg(barred, quality)
            w, h = out.size
            crop = out.crop((0, h - CROP_ROWS, w, h))

            # The case must be DISCRIMINATING: strict-only must fail on it,
            # otherwise the JS assertion below proves nothing.
            original = bar._BAND_PREDICATE_PASSES
            try:
                bar._BAND_PREDICATE_PASSES = strict_only
                self.assertIsNone(
                    bar.extract_bar(crop),
                    f"q{quality} case is vacuous: strict predicates already decode it")
            finally:
                bar._BAND_PREDICATE_PASSES = original
            self.assertEqual(bar.extract_bar(crop), (IDENT, HASH),
                             f"Python soft fallback failed on its own 0.5x q{quality} output")

            rgba = []
            for y in range(CROP_ROWS):
                for x in range(w):
                    r, g, b = crop.getpixel((x, y))[:3]
                    rgba.extend((r, g, b, 255))
            cases.append({"name": f"0.5x q{quality}", "w": w, "h": CROP_ROWS,
                          "rgba": rgba, "identifier": IDENT, "content_hash": HASH})

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(cases, f)
            path = f.name
        try:
            r = subprocess.run([NODE, HARNESS, path], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
            self.assertIn("SOFT-ANCHOR TESTS PASSED", r.stdout)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
