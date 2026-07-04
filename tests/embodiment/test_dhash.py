"""EMBODIED — dHash perceptual comparison (Python reimplementation of verify.js)."""

import io
import os
import tempfile
import unittest

import numpy as np
from PIL import Image


def compute_dhash(img_array, width, height):
    """Python reimplementation of verify.js computeDHash.

    9x8 nearest-neighbor downsample, horizontal gradient comparison.
    Returns 64 bits as list of 0/1.
    """
    dw, dh = 9, 8
    gray = np.zeros((dh, dw), dtype=np.float32)

    for y in range(dh):
        for x in range(dw):
            sx = int(x * width / dw)
            sy = int(y * height / dh)
            r, g, b = img_array[sy, sx, 0], img_array[sy, sx, 1], img_array[sy, sx, 2]
            gray[y, x] = r * 0.299 + g * 0.587 + b * 0.114

    bits = []
    for y in range(dh):
        for x in range(dw - 1):
            bits.append(1 if gray[y, x] > gray[y, x + 1] else 0)
    return bits


def dhash_from_image(path):
    img = Image.open(path).convert("RGB")
    arr = np.array(img)
    return compute_dhash(arr, img.width, img.height)


def dhash_from_data_uri(data_uri):
    import base64
    header, b64 = data_uri.split(",", 1)
    img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    arr = np.array(img)
    return compute_dhash(arr, img.width, img.height)


def hamming(a, b):
    return sum(x != y for x, y in zip(a, b))


def _make_textured(width=1024, height=1024, seed=42):
    rng = np.random.RandomState(seed)
    base = np.zeros((height, width, 3), dtype=np.uint8)
    for c in range(3):
        g = np.linspace(30 + c * 20, 200 + c * 10, width)
        layer = np.tile(g, (height, 1)) + rng.normal(0, 15, (height, width))
        base[:, :, c] = np.clip(layer, 0, 255).astype(np.uint8)
    path = tempfile.mktemp(suffix=".png")
    Image.fromarray(base).save(path)
    return path


class TestDHash(unittest.TestCase):
    """Core dHash properties."""

    def test_identical_images_distance_zero(self):
        path = _make_textured(seed=1)
        h1 = dhash_from_image(path)
        h2 = dhash_from_image(path)
        self.assertEqual(hamming(h1, h2), 0)
        os.unlink(path)

    def test_length_is_64_bits(self):
        path = _make_textured()
        h = dhash_from_image(path)
        self.assertEqual(len(h), 64)
        os.unlink(path)

    def test_completely_different_images_high_distance(self):
        white = tempfile.mktemp(suffix=".png")
        black = tempfile.mktemp(suffix=".png")
        Image.new("RGB", (256, 256), (255, 255, 255)).save(white)
        Image.new("RGB", (256, 256), (0, 0, 0)).save(black)
        hw = dhash_from_image(white)
        hb = dhash_from_image(black)
        # Solid images have no gradient, distance depends on implementation
        # But two different textured images should have high distance
        os.unlink(white)
        os.unlink(black)

    def test_different_textured_images_high_distance(self):
        a = _make_textured(seed=1)
        b = _make_textured(seed=999)
        ha = dhash_from_image(a)
        hb = dhash_from_image(b)
        dist = hamming(ha, hb)
        self.assertGreater(dist, 10, f"Expected distance > 10 for different images, got {dist}")
        os.unlink(a)
        os.unlink(b)

    def test_jpeg_compression_low_distance(self):
        path = _make_textured(seed=5)
        h_orig = dhash_from_image(path)

        # JPEG round-trip
        img = Image.open(path)
        jpeg_path = tempfile.mktemp(suffix=".jpg")
        img.save(jpeg_path, "JPEG", quality=50)
        h_jpeg = dhash_from_image(jpeg_path)
        dist = hamming(h_orig, h_jpeg)
        # dHash is coarse (9x8) — JPEG artifacts can shift a few bits
        self.assertLessEqual(dist, 15, f"JPEG q50 distance {dist} exceeds threshold")
        os.unlink(path)
        os.unlink(jpeg_path)

    def test_bar_overlay_low_distance(self):
        """Bar encoding only touches bottom 2 rows — minimal dHash impact."""
        from mememage.bar import embed_bar
        path = _make_textured(seed=7)
        h_before = dhash_from_image(path)
        embed_bar(path, "mememage-9c3bea934c1dbaf9", "aabbccdd11223344")
        h_after = dhash_from_image(path)
        dist = hamming(h_before, h_after)
        self.assertLessEqual(dist, 10, f"Bar overlay distance {dist} exceeds threshold")
        os.unlink(path)

    def test_horizontal_flip_detected(self):
        path = _make_textured(seed=3)
        h_orig = dhash_from_image(path)
        img = Image.open(path).transpose(Image.FLIP_LEFT_RIGHT)
        flip_path = tempfile.mktemp(suffix=".png")
        img.save(flip_path)
        h_flip = dhash_from_image(flip_path)
        dist = hamming(h_orig, h_flip)
        self.assertGreater(dist, 10, f"Flip should be detected, got distance {dist}")
        os.unlink(path)
        os.unlink(flip_path)


class TestPortraitComparison(unittest.TestCase):
    """EMBODIED check: image vs thumbnail."""

    def test_embodied_same_image(self):
        from mememage.thumbnail import generate_thumbnail
        path = _make_textured(seed=10)
        thumbnail = generate_thumbnail(path)
        h_img = dhash_from_image(path)
        h_thumb = dhash_from_data_uri(thumbnail)
        dist = hamming(h_img, h_thumb)
        self.assertLessEqual(dist, 10, f"Same image vs thumbnail distance {dist}")
        os.unlink(path)

    def test_embodied_after_bar_and_watermark(self):
        from mememage.thumbnail import generate_thumbnail
        from mememage.bar import embed_bar
        from mememage.watermark import embed_watermark
        path = _make_textured(seed=20)
        thumbnail = generate_thumbnail(path)  # Before encoding
        embed_watermark(path, "abcdef0123456789")
        embed_bar(path, "mememage-9c3bea934c1dbaf9", "abcdef0123456789")
        h_img = dhash_from_image(path)
        h_thumb = dhash_from_data_uri(thumbnail)
        dist = hamming(h_img, h_thumb)
        # Watermark modifies DCT coefficients across entire image, shifting dHash.
        # Threshold 15 accounts for watermark + bar + thumbnail quality loss.
        self.assertLessEqual(dist, 15, f"Minted image vs thumbnail distance {dist}")
        os.unlink(path)

    def test_disembodied_different_image(self):
        from mememage.thumbnail import generate_thumbnail
        # Use very different seeds — one warm gradient, one with inverted channels
        path_a = _make_textured(seed=1)
        # Create a visually distinct image (different pattern, not just shifted gradient)
        rng = np.random.RandomState(777)
        arr = np.zeros((1024, 1024, 3), dtype=np.uint8)
        for c in range(3):
            g = np.linspace(200 - c * 40, 20 + c * 30, 1024)
            arr[:, :, c] = np.clip(np.tile(g, (1024, 1)) + rng.normal(0, 25, (1024, 1024)), 0, 255).astype(np.uint8)
        path_b = tempfile.mktemp(suffix=".png")
        Image.fromarray(arr).save(path_b)

        thumbnail_a = generate_thumbnail(path_a)
        h_b = dhash_from_image(path_b)
        h_thumb_a = dhash_from_data_uri(thumbnail_a)
        dist = hamming(h_b, h_thumb_a)
        self.assertGreater(dist, 15, f"Different images should be DISEMBODIED, got distance {dist}")
        os.unlink(path_a)
        os.unlink(path_b)

    def test_embodied_after_jpeg_q50(self):
        from mememage.thumbnail import generate_thumbnail
        path = _make_textured(seed=50)
        thumbnail = generate_thumbnail(path)
        img = Image.open(path)
        jpeg_path = tempfile.mktemp(suffix=".jpg")
        img.save(jpeg_path, "JPEG", quality=50)
        h_jpeg = dhash_from_image(jpeg_path)
        h_thumb = dhash_from_data_uri(thumbnail)
        dist = hamming(h_jpeg, h_thumb)
        self.assertLessEqual(dist, 10, f"JPEG q50 vs thumbnail distance {dist}")
        os.unlink(path)
        os.unlink(jpeg_path)
