"""Localized-tamper detection (the luma grid — EMBODIED, integrity half).

dHash is blind to a defacement (a drawn line and JPEG q50 are the same magnitude
to a perceptual hash — see test_dhash.py). The luma grid uses per-tile stats:
FLAT tiles carry a min/max detector (a mark drives a flat tile's extremes
outward; compression pulls them inward — opposite directions), ALL tiles carry a
mean detector for big defacement over texture. This locks down:

  1. honest recompression (JPEG q30-q85, multi-pass, downscale across methods)
     produces NO false positives,
  2. a small dark/bright mark (down to a couple px) on a flat region flags;
     bigger defacement (lines, boxes, text) flags anywhere,
  3. the global-retouch POLICY: a clipping brightness edit flags,
  4. Python <-> JS byte/verdict parity (mememage/embodiment.py <-> verify.js).

Needs Pillow. Parity test additionally needs Node; it skips cleanly without it.
"""
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest

try:
    from PIL import Image, ImageDraw
    import numpy as np
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from mememage import embodiment

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, "luma_grid_parity.cjs")
NODE = shutil.which("node")


def _save(img):
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    img.save(path)
    return path


def _scene(seed=1, w=1216, h=832):
    """Render-like: smooth mid-tone sky→snow gradient with subtle grain (real
    flat regions aren't perfectly flat), a bright radial GLOW (a high-stddev but
    smooth gradient — the sun-glow regime), and a textured dune band below."""
    rng = np.random.default_rng(seed)
    b = np.zeros((h, w, 3))
    for c in range(3):
        b[:, :, c] = np.linspace(150, 210, h)[:, None] * np.ones((1, w))
    b += rng.normal(0, 6, (h, w, 3))
    yy, xx = np.mgrid[0:h, 0:w]
    glow = np.exp(-((xx - 850) ** 2 + (yy - 220) ** 2) / (2 * 150.0 ** 2))   # smooth gradient
    for c in range(3):
        b[:, :, c] += 70 * glow
    for _ in range(200):
        cx, cy, r = rng.integers(0, w), rng.integers(int(h * 0.5), h), rng.integers(8, 40)
        m = (xx - cx) ** 2 + (yy - cy) ** 2 < r * r
        b[m] += rng.normal(0, 18, 3)
    return Image.fromarray(np.clip(b, 0, 255).astype(np.uint8), "RGB")


SKY = (340, 200)       # flat smooth sky
GLOW = (850, 220)      # bright smooth gradient (sun-glow regime — high stddev, low roughness)
DUNE = (600, 650)      # textured band


def _verdict(orig_img, dropped_img):
    po, pd = _save(orig_img), _save(dropped_img)
    try:
        return embodiment.is_tampered(embodiment.compute_luma_grid(po), pd)
    finally:
        os.unlink(po)
        os.unlink(pd)


def _jpeg(img, q, sub=2):
    bb = io.BytesIO()
    img.save(bb, "JPEG", quality=q, subsampling=sub)
    bb.seek(0)
    return Image.open(bb).convert("RGB")


def _downscale(img, f, method=Image.LANCZOS):
    w, h = img.size
    return img.resize((max(8, int(w * f)), max(8, int(h * f))), method)


def _dot(img, k, where=SKY, val=0):
    im = img.copy()
    x, y = where
    ImageDraw.Draw(im).rectangle([x, y, x + k - 1, y + k - 1], fill=(val, val, val))
    return im


@unittest.skipUnless(HAS_PIL, "Pillow + numpy required")
class LumaGridRobustness(unittest.TestCase):
    """Honest platform transforms must NOT flag (false ALTERED is the worst failure)."""

    def test_jpeg_passes(self):
        orig = _scene(2)
        for q in (30, 50, 70, 85):
            self.assertFalse(_verdict(orig, _jpeg(orig, q)), f"JPEG q{q} false-flagged")

    def test_downscale_passes(self):
        orig = _scene(3)
        for f in (0.45, 0.6, 0.8):
            for m in (Image.LANCZOS, Image.BICUBIC):
                self.assertFalse(_verdict(orig, _downscale(orig, f, m)),
                                 f"downscale {f}/{m} false-flagged")

    def test_multipass_recompress_passes(self):
        orig = _scene(4)
        shared = _jpeg(_downscale(_jpeg(_downscale(orig, 0.8, Image.BICUBIC), 60), 0.5), 55)
        self.assertFalse(_verdict(orig, shared), "double Discord round-trip false-flagged")

    def test_exposure_shift_passes(self):
        orig = _scene(5)
        a = np.asarray(orig, np.int16)
        for d in (15, -20):
            shifted = Image.fromarray(np.clip(a + d, 0, 255).astype("uint8"), "RGB")
            # A non-clipping uniform shift is absorbed by the exposure normalization.
            self.assertFalse(_verdict(orig, shifted), f"uniform exposure {d} false-flagged")

    def test_identical_is_clean(self):
        orig = _scene(6)
        self.assertFalse(_verdict(orig, orig))


@unittest.skipUnless(HAS_PIL, "Pillow + numpy required")
class LumaGridTamper(unittest.TestCase):
    """Defacement MUST flag — including tiny marks on flat regions."""

    def test_tiny_dark_dots_on_flat_flag(self):
        orig = _scene(7)
        for k in (1, 2, 3, 4):          # min/max detector catches down to 1px on flat
            self.assertTrue(_verdict(orig, _dot(orig, k, SKY, 0)),
                            f"{k}x{k} dark dot on flat sky must flag")

    def test_dark_dot_on_smooth_gradient_flags(self):
        # The sun-glow regime: high stddev but low roughness. Roughness gating
        # (not stddev) admits it, so a mark here is caught. This is the exact
        # case stddev-gating missed.
        orig = _scene(7)
        for k in (2, 3):
            self.assertTrue(_verdict(orig, _dot(orig, k, GLOW, 0)),
                            f"{k}x{k} dot on a smooth glow gradient must flag")

    def test_bright_dot_on_flat_flags(self):
        orig = _scene(7)
        self.assertTrue(_verdict(orig, _dot(orig, 3, SKY, 255)), "white dot on flat must flag")

    def test_grey_line_flags(self):
        orig = _scene(8)
        ed = orig.copy()
        w, h = orig.size
        ImageDraw.Draw(ed).line([(80, 120), (w - 80, h - 120)], fill=(40, 40, 40), width=6)
        self.assertTrue(_verdict(orig, ed))

    def test_pasted_box_on_texture_flags(self):
        # Buried in the textured dune band — must clear the HIGH (mean) floor.
        orig = _scene(8)
        ed = _dot(orig, 70, DUNE, 10)
        self.assertTrue(_verdict(orig, ed))


@unittest.skipUnless(HAS_PIL, "Pillow + numpy required")
class LumaGridMarginCoverage(unittest.TestCase):
    """Full-frame regression: defacement in the MARGINS must flag. A portrait's
    bottom strip (a moved bar, a black fill) used to fall OUTSIDE the center-cropped
    square and pass EMBODIED — the exact gap a hand-defaced image exposed."""

    def _portrait(self, seed=1, w=768, h=1344):
        rng = np.random.default_rng(seed)
        b = np.zeros((h, w, 3))
        for c in range(3):
            b[:, :, c] = np.linspace(120, 200, h)[:, None] * np.ones((1, w))
        b += rng.normal(0, 5, (h, w, 3))
        return Image.fromarray(np.clip(b, 0, 255).astype("uint8"), "RGB")

    def test_bottom_margin_defacement_flags(self):
        # y 1100-1300 is below the old center square (y 288..1056 for 768x1344).
        orig = self._portrait()
        ed = orig.copy()
        ImageDraw.Draw(ed).rectangle([0, 1100, orig.width - 1, 1300], fill=(0, 0, 0))
        self.assertTrue(_verdict(orig, ed), "bottom-margin black fill must flag")

    def test_top_margin_defacement_flags(self):
        orig = self._portrait()
        ed = orig.copy()
        ImageDraw.Draw(ed).rectangle([0, 40, orig.width - 1, 240], fill=(255, 255, 255))
        self.assertTrue(_verdict(orig, ed), "top-margin white fill must flag")

    def test_portrait_honest_jpeg_clean(self):
        orig = self._portrait(2)
        self.assertFalse(_verdict(orig, _jpeg(orig, 70)), "honest jpeg on a portrait must NOT flag")


@unittest.skipUnless(HAS_PIL, "Pillow + numpy required")
class LumaGridRetouchPolicy(unittest.TestCase):
    def test_clipping_brightness_flags(self):
        # A brightness edit that clips is non-uniform -> not absorbed -> flags.
        orig = _scene(7)
        a = np.asarray(orig, np.int16)
        bright = Image.fromarray(np.clip(a + 80, 0, 255).astype("uint8"), "RGB")
        self.assertTrue(_verdict(orig, bright), "clipping brightness retouch must flag")


@unittest.skipUnless(HAS_PIL, "Pillow + numpy required")
class LumaGridFormat(unittest.TestCase):
    def test_blob_size(self):
        p = _save(_scene(1))
        try:
            import base64
            self.assertEqual(len(base64.b64decode(embodiment.compute_luma_grid(p))),
                             embodiment.STORED_BYTES)   # 1024*3 + 128 + 1 version = 3201
        finally:
            os.unlink(p)

    def test_legacy_blob_skips(self):
        import base64
        # 256/1152 = old 16x16 / mean+flat; 3200 = old unversioned CENTER-SQUARE
        # grid (graceful: degrade to dHash-only, never cry wolf on full-frame).
        for n in (256, 1152, 3200):
            self.assertIsNone(embodiment.unpack(base64.b64encode(bytes(n)).decode()))

    def test_wrong_version_skips(self):
        import base64
        # 3201 bytes but a non-2 version byte -> not the current format -> None.
        blob = bytes(embodiment.BLOB_BYTES) + bytes([99])
        self.assertIsNone(embodiment.unpack(base64.b64encode(blob).decode()))


@unittest.skipUnless(HAS_PIL and NODE, "Pillow + Node required for parity")
class LumaGridParity(unittest.TestCase):
    """Python embodiment.py and JS verify.js must agree on stats and verdict."""

    def _run_js(self, payload):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(payload, f)
            cases = f.name
        try:
            return json.loads(subprocess.check_output([NODE, HARNESS, cases], timeout=60))
        finally:
            os.unlink(cases)

    def test_tile_stats_parity(self):
        squares, py = [], []
        for seed in (11, 22, 33):
            img = _scene(seed, w=256, h=256).convert("RGB")
            p = _save(img)
            try:
                mean, mn, mx, _flat = embodiment.tile_stats(p)
                py.append((list(mean), list(mn), list(mx)))
            finally:
                os.unlink(p)
            px = img.load()
            data = []
            for y in range(256):
                for x in range(256):
                    r, g, b = px[x, y]
                    data.extend((r, g, b, 255))
            squares.append({"side": 256, "data": data})
        js = self._run_js({"squares": squares})
        for i, (pm, js_s) in enumerate(zip(py, js["stats"])):
            self.assertEqual(pm[0], js_s["mean"], f"mean diverges sq{i}")
            self.assertEqual(pm[1], js_s["min"], f"min diverges sq{i}")
            self.assertEqual(pm[2], js_s["max"], f"max diverges sq{i}")

    def test_tile_stats_parity_nonsquare(self):
        # Full-frame parity: non-square images (w != h) must match too — the case
        # the center-crop never exercised. A portrait and a landscape.
        frames, py = [], []
        for seed, (w, h) in ((11, (256, 384)), (22, (384, 256))):
            img = _scene(seed, w=w, h=h).convert("RGB")
            p = _save(img)
            try:
                mean, mn, mx, _flat = embodiment.tile_stats(p)
                py.append((list(mean), list(mn), list(mx)))
            finally:
                os.unlink(p)
            px = img.load()
            data = []
            for y in range(h):
                for x in range(w):
                    r, g, b = px[x, y]
                    data.extend((r, g, b, 255))
            frames.append({"w": w, "h": h, "data": data})
        js = self._run_js({"frames": frames})
        for i, (pm, js_s) in enumerate(zip(py, js["frameStats"])):
            self.assertEqual(pm[0], js_s["mean"], f"mean diverges frame{i}")
            self.assertEqual(pm[1], js_s["min"], f"min diverges frame{i}")
            self.assertEqual(pm[2], js_s["max"], f"max diverges frame{i}")

    def test_evaluate_parity(self):
        import random
        rnd = random.Random(99)
        cases = []
        for _ in range(4):
            mean = [rnd.randint(0, 255) for _ in range(1024)]
            mn = [max(0, v - rnd.randint(0, 30)) for v in mean]
            mx = [min(255, v + rnd.randint(0, 30)) for v in mean]
            flat = [rnd.randint(0, 1) for _ in range(1024)]
            dmean = [min(255, max(0, v + rnd.randint(-40, 40))) for v in mean]
            dmin = [max(0, v - rnd.randint(0, 60)) for v in dmean]
            dmax = [min(255, v + rnd.randint(0, 60)) for v in dmean]
            cases.append({"ref": {"mean": mean, "min": mn, "max": mx, "flat": flat},
                          "drop": {"mean": dmean, "min": dmin, "max": dmax}})
        js = self._run_js({"evalCases": cases})
        for i, c in enumerate(cases):
            flat_bytes = _pack_bits(c["ref"]["flat"])
            mm, hm = embodiment.evaluate(
                bytes(c["ref"]["mean"]), bytes(c["ref"]["min"]), bytes(c["ref"]["max"]), flat_bytes,
                bytes(c["drop"]["mean"]), bytes(c["drop"]["min"]), bytes(c["drop"]["max"]))
            self.assertAlmostEqual(mm, js["evals"][i]["markMax"], places=9, msg=f"markMax {i}")
            self.assertAlmostEqual(hm, js["evals"][i]["highMax"], places=9, msg=f"highMax {i}")


def _pack_bits(flags):
    out = bytearray((len(flags) + 7) // 8)
    for i, f in enumerate(flags):
        if f:
            out[i >> 3] |= 1 << (i & 7)
    return bytes(out)


if __name__ == "__main__":
    unittest.main()
