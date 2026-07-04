"""Byte-for-byte parity: the JS bar WRITER (docs/js/codec.js embedBarPayload)
must produce pixels identical to the Python canonical writer
(mememage/bar.py embed_into).

The bar is the technique; it evolves (centered brightness, even-fill, …). Two
writers — Python (mint) and JS (Save Certificate / reliquary / band PNG) — must
never drift. This test builds the same image in both languages, embeds a bar,
and asserts the bottom two rows match exactly. If they diverge, fix the writer
that's wrong (Python bar.py is canonical) until this passes.

Needs Node + Pillow. Skips cleanly if either is missing.
"""
import json
import os
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
HARNESS = os.path.join(HERE, "bar_encode_parity.cjs")
NODE = shutil.which("node")

# Cases exercise: even-fill (wide) + sequential ppb=3/2 (medium/narrow); dark /
# mid / bright / tinted backgrounds (centered-brightness across regimes); a
# 2-row strip (whole-image-mean dominant, the reliquary case); a non-uniform
# 'stripe' fill (fractional dominant → banker's-rounding path); and a longer
# identifier (different payload length → different layout boundary).
ID = "mememage-0123456789abcdef"
H = "fedcba9876543210"
LONG_ID = "phoenix-aabbccddeeff0011"   # different length shifts the frame/layout
CASES = [
    {"w": 1600, "h": 80, "fill": "uniform", "rgb": [10, 12, 16], "identifier": ID, "content_hash": H},
    {"w": 1600, "h": 80, "fill": "uniform", "rgb": [128, 128, 128], "identifier": ID, "content_hash": H},
    {"w": 1600, "h": 80, "fill": "uniform", "rgb": [222, 218, 230], "identifier": ID, "content_hash": H},
    {"w": 1392, "h": 40, "fill": "uniform", "rgb": [30, 60, 70], "identifier": ID, "content_hash": H},
    {"w": 900,  "h": 80, "fill": "uniform", "rgb": [20, 24, 28], "identifier": ID, "content_hash": H},
    {"w": 520,  "h": 80, "fill": "uniform", "rgb": [40, 90, 120], "identifier": ID, "content_hash": H},
    {"w": 760,  "h": 2,  "fill": "uniform", "rgb": [128, 128, 128], "identifier": ID, "content_hash": H},
    {"w": 1000, "h": 50, "fill": "stripe",  "rgb": [0, 0, 0], "identifier": ID, "content_hash": H},
    {"w": 1600, "h": 60, "fill": "uniform", "rgb": [15, 18, 22], "identifier": LONG_ID, "content_hash": H},
]


def _fill_pixel(mode, x, y, rgb):
    # MUST match bar_encode_parity.cjs:fillPixel exactly.
    if mode == "uniform":
        return tuple(rgb)
    return ((x % 7) + 20, (y % 5) + 40, ((x + y) % 11) + 60)


def _py_bar_rows(case):
    """Build the case image in Pillow, embed_into, return the bottom-2-row RGB
    as a flat list (matching the harness's output order)."""
    w, h = case["w"], case["h"]
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = _fill_pixel(case["fill"], x, y, case["rgb"])
    error = None
    try:
        barred = bar.embed_into(img, case["identifier"], case["content_hash"])
    except ValueError as e:
        return None, str(e)
    bp = barred.load()
    flat = []
    for y in range(h - 2, h):
        for x in range(w):
            r, g, b = bp[x, y][:3]
            flat.extend((r, g, b))
    return flat, error


@unittest.skipUnless(HAS_PIL, "Pillow required")
@unittest.skipUnless(NODE, "Node.js required for JS parity")
class TestBarWriterParity(unittest.TestCase):
    def test_js_writer_matches_python(self):
        # Run the JS harness once over all cases.
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(CASES, f)
            cases_path = f.name
        try:
            proc = subprocess.run([NODE, HARNESS, cases_path],
                                  capture_output=True, text=True, timeout=60)
        finally:
            os.unlink(cases_path)
        self.assertEqual(proc.returncode, 0, f"harness failed:\n{proc.stderr}")
        js = json.loads(proc.stdout)
        self.assertEqual(len(js), len(CASES))

        for i, case in enumerate(CASES):
            py_pixels, py_err = _py_bar_rows(case)
            js_err = js[i].get("error")
            label = f"case {i} ({case['w']}x{case['h']} {case['fill']} {case['rgb']})"
            # Errors (payload-too-large) must agree on both sides.
            if py_err or js_err:
                self.assertTrue(py_err and js_err, f"{label}: error mismatch py={py_err!r} js={js_err!r}")
                continue
            js_pixels = js[i]["pixels"]
            self.assertEqual(len(py_pixels), len(js_pixels), f"{label}: length mismatch")
            if py_pixels != js_pixels:
                # Pinpoint the first divergence for a useful failure message.
                for k, (a, b) in enumerate(zip(py_pixels, js_pixels)):
                    if a != b:
                        px_idx = k // 3
                        row = h_off = px_idx // case["w"]
                        col = px_idx % case["w"]
                        self.fail(f"{label}: first pixel diff at row {row} col {col} "
                                  f"channel {k % 3}: py={a} js={b}")
                self.fail(f"{label}: pixels differ")


if __name__ == "__main__":
    unittest.main()
