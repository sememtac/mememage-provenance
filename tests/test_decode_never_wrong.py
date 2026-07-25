"""The decoder is either right or silent — it must never return WRONG data.

A wrong-answer decode is the one true false-PASS path in the codec: it would
hand a verifier someone else's identifier and content hash. Reed-Solomon alone
cannot guarantee this, because errors BEYOND its correction capacity (nsym//2 =
3 bytes) can land near a different valid codeword and "correct" into it. The
16-bit CRC re-checked AFTER RS is what catches those (bar.py:_try_decode_frame).
The browser decoder once lacked that re-check and accepted miscorrections.

These tests hold the invariant at both levels:
  * frame level — corrupt k bytes of the codeword, for k inside and beyond RS
    capacity, and assert the result is the true payload or None. Never other.
  * image level — an image carrying no bar must never decode to something.

Audit measurement (2026-07-24): 40,000 corrupted frames -> 12,000 correct,
28,000 rejected, 0 wrong. The residual risk is a miscorrection that also
collides with the CRC, about 1 in 65,536 per miscorrection event — and even then
the wrong hash fails the record check downstream, so a codec false-PASS cannot
become a false VERIFIED.
"""
import math
import random
import struct
import unittest

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from mememage import bar
from mememage.rs import rs_encode

ID, CH = "mememage-0123456789abcdef", "fedcba9876543210"


def _frame():
    payload = bar._pack_payload(ID, CH)
    nsym = bar._RS_NSYM
    codeword = rs_encode(payload, nsym)
    return (bar._FRAME_MAGIC + struct.pack("B", bar._FRAME_GEN) + struct.pack("B", nsym)
            + struct.pack(">H", len(payload)) + struct.pack(">H", bar._crc16(codeword))
            + codeword), nsym


def _bits(bs):
    return [(b >> p) & 1 for b in bs for p in range(7, -1, -1)]


def _photo(w, h, seed=0):
    rnd = random.Random(seed)
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(h):
        base = 60 + 120 * math.sin(y / 23.0 + seed)
        for x in range(w):
            v = base + 60 * math.sin(x / 31.0 + seed) + rnd.randint(-18, 18)
            c = max(0, min(255, int(v)))
            px[x, y] = (c, max(0, min(255, int(v * 0.8))), max(0, min(255, int(v * 0.6 + 30))))
    return im


class TestFrameDecodeNeverWrong(unittest.TestCase):

    def test_within_rs_capacity_always_recovers(self):
        frame, nsym = _frame()
        rnd = random.Random(7)
        for k in range(1, nsym // 2 + 1):
            with self.subTest(errors=k):
                for _ in range(200):
                    f = bytearray(frame)
                    for pos in rnd.sample(range(8, len(frame)), k):
                        f[pos] ^= rnd.randrange(1, 256)
                    self.assertEqual(bar._try_decode_frame(_bits(f)), (ID, CH))

    def test_beyond_rs_capacity_never_returns_wrong_data(self):
        """The CRC-after-RS re-check is what makes this hold."""
        frame, nsym = _frame()
        rnd = random.Random(8)
        for k in range(nsym // 2 + 1, 11):
            with self.subTest(errors=k):
                for _ in range(200):
                    f = bytearray(frame)
                    for pos in rnd.sample(range(8, len(frame)), k):
                        f[pos] ^= rnd.randrange(1, 256)
                    got = bar._try_decode_frame(_bits(f))
                    self.assertIn(got, (None, (ID, CH)),
                                  f"decoder returned WRONG data with {k} byte errors: {got}")

    def test_corrupt_header_is_rejected(self):
        frame, _ = _frame()
        for pos in range(0, 8):
            with self.subTest(header_byte=pos):
                f = bytearray(frame)
                f[pos] ^= 0xFF
                self.assertIn(bar._try_decode_frame(_bits(f)), (None, (ID, CH)))


@unittest.skipUnless(HAS_PIL, "Pillow not installed")
class TestImageDecodeNeverWrong(unittest.TestCase):

    def test_images_without_a_bar_do_not_decode(self):
        for seed in range(8):
            with self.subTest(seed=seed):
                self.assertIsNone(bar.extract_bar(_photo(640, 200, seed + 100)))

    def test_pure_noise_does_not_decode(self):
        rnd = random.Random(3)
        im = Image.new("RGB", (800, 200))
        px = im.load()
        for y in range(200):
            for x in range(800):
                px[x, y] = (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
        self.assertIsNone(bar.extract_bar(im))

    def test_degenerate_dimensions_return_none_not_a_crash(self):
        for label, im in (("1x1", Image.new("RGB", (1, 1))),
                          ("2px tall", Image.new("RGB", (900, 2), (30, 30, 30))),
                          ("1px wide", Image.new("RGB", (1, 400), (30, 30, 30))),
                          ("3px tall", _photo(900, 3, 1))):
            with self.subTest(case=label):
                self.assertIsNone(bar.extract_bar(im))

    def test_decode_is_deterministic(self):
        im = bar.embed_into(_photo(900, 60, 9), ID, CH)
        self.assertEqual(len({bar.extract_bar(im) for _ in range(5)}), 1)

    def test_heavily_corrupted_bar_is_none_never_wrong(self):
        im = bar.embed_into(_photo(900, 60, 2), ID, CH)
        rnd = random.Random(5)
        px = im.load()
        for _ in range(900):
            px[rnd.randrange(900), rnd.choice([58, 59])] = (
                rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
        self.assertIn(bar.extract_bar(im), (None, (ID, CH)))


if __name__ == "__main__":
    unittest.main()
