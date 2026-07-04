"""Forgery scenarios — signature attacks, record tampering, body swaps."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._profile_isolation import isolate_profiles

from PIL import Image
import numpy as np


def _temp_key_dir():
    return Path(tempfile.mkdtemp(prefix="mememage_test_keys_"))


def _make_textured_image(width=1024, height=1024, seed=42):
    """Create a realistic textured image (gradient + noise)."""
    rng = np.random.RandomState(seed)
    base = np.zeros((height, width, 3), dtype=np.uint8)
    for c in range(3):
        gradient = np.linspace(30 + c * 20, 200 + c * 10, width, dtype=np.float64)
        layer = np.tile(gradient, (height, 1))
        layer += rng.normal(0, 15, (height, width))
        base[:, :, c] = np.clip(layer, 0, 255).astype(np.uint8)
    path = tempfile.mktemp(suffix=".png")
    Image.fromarray(base).save(path)
    return path


class TestSignatureForgery(unittest.TestCase):
    """Attacker tries to forge or reuse signatures."""

    def setUp(self):
        self.key_dir = _temp_key_dir()
        self.profile_dir = isolate_profiles(self, self.key_dir)
        from mememage.signing import keygen
        keygen(name="RealArtist")

    def tearDown(self):
        shutil.rmtree(self.key_dir, ignore_errors=True)

    def test_random_signature_fails(self):
        from mememage.signing import sign, verify
        _, pub, _, _ = sign("mememage-test", "abcdef0123456789")
        random_sig = os.urandom(64).hex()
        self.assertFalse(verify("mememage-test", "abcdef0123456789", random_sig, pub))

    def test_signature_from_different_key_fails(self):
        from mememage.signing import sign, verify, keygen
        sig_a, pub_a, _, _ = sign("mememage-test", "abcdef0123456789")
        keygen(force=True, name="Attacker")
        _, pub_b, _, _ = sign("mememage-test", "abcdef0123456789")
        # A's signature with B's public key
        self.assertFalse(verify("mememage-test", "abcdef0123456789", sig_a, pub_b))

    def test_reuse_signature_on_different_record(self):
        from mememage.signing import sign, verify
        sig, pub, _, _ = sign("mememage-record-a", "hash_a_12345678")
        # Replay sig from record A onto record B
        self.assertFalse(verify("mememage-record-b", "hash_b_12345678", sig, pub))

    def test_reuse_signature_different_hash(self):
        from mememage.signing import sign, verify
        sig, pub, _, _ = sign("mememage-test", "original_hash___")
        self.assertFalse(verify("mememage-test", "tampered_hash___", sig, pub))


class TestRecordTampering(unittest.TestCase):
    """Attacker modifies record fields."""

    def test_tamper_prompt_breaks_witnessed(self):
        # V1: gen params live under origin (hashed wholesale).
        from mememage.core import compute_content_hash
        record = {"origin": {"prompt": "original", "seed": 42}, "width": 1024, "height": 1024}
        record["content_hash"] = compute_content_hash(record)
        record["origin"]["prompt"] = "STOLEN"
        self.assertNotEqual(compute_content_hash(record), record["content_hash"])

    def test_tamper_seed_breaks_witnessed(self):
        from mememage.core import compute_content_hash
        record = {"origin": {"prompt": "test", "seed": 42}, "width": 1024, "height": 1024}
        record["content_hash"] = compute_content_hash(record)
        record["origin"]["seed"] = 99999
        self.assertNotEqual(compute_content_hash(record), record["content_hash"])

    def test_tamper_rarity_breaks_witnessed(self):
        # V1: rarity dict is what's hashed (not the derived rarity_score).
        # Tampering the dice still breaks WITNESSED.
        from mememage.core import compute_content_hash
        record = {"prompt": "t", "seed": 1, "width": 1024, "height": 1024,
                  "rarity": {"celestial": [{"trait": "x", "points": 5}]}}
        record["content_hash"] = compute_content_hash(record)
        record["rarity"]["celestial"][0]["points"] = 999
        self.assertNotEqual(compute_content_hash(record), record["content_hash"])

    def test_tamper_excluded_field_preserves_witnessed(self):
        from mememage.core import compute_content_hash
        record = {"prompt": "t", "seed": 1, "width": 1024, "height": 1024}
        record["content_hash"] = compute_content_hash(record)
        record["_about"] = "ATTACKER WAS HERE"
        record["decoder_chunk"] = "fake chunk data"
        self.assertEqual(compute_content_hash(record), record["content_hash"])

    def test_attacker_recomputes_hash_but_signature_fails(self):
        """Attacker changes prompt and recomputes hash — WITNESSED passes but AUTHENTICATED fails."""
        from mememage.core import compute_content_hash
        from mememage.signing import sign, verify

        key_dir = _temp_key_dir()
        isolate_profiles(self, key_dir)

        from mememage.signing import keygen
        keygen(name="Real")

        # Original record (V1: origin nest)
        record = {"origin": {"prompt": "sunset", "seed": 42}, "width": 1024, "height": 1024}
        original_hash = compute_content_hash(record)
        record["content_hash"] = original_hash
        sig, pub, fp, _ = sign("mememage-test1234567890", original_hash)

        # Attacker changes prompt and recomputes hash
        record["origin"]["prompt"] = "STOLEN ART"
        tampered_hash = compute_content_hash(record)
        record["content_hash"] = tampered_hash

        # WITNESSED would pass (hash matches new content)
        self.assertEqual(compute_content_hash(record), tampered_hash)
        # AUTHENTICATED fails (signature covers original hash)
        self.assertFalse(verify("mememage-test1234567890", tampered_hash, sig, pub))

        # isolate_profiles registered the patch teardown via addCleanup,
        # so it'll fire at test exit. We just clean the tmp dir.
        shutil.rmtree(key_dir, ignore_errors=True)


class TestBodySwap(unittest.TestCase):
    """Attacker puts real bar on wrong image."""

    def test_bar_from_image_a_extracts_on_image_b(self):
        """Bar encoded from record A can be read from image B — but that's the point of EMBODIED."""
        from mememage.bar import embed_bar, extract_bar
        img_a = _make_textured_image(seed=1)
        img_b = _make_textured_image(seed=2)

        embed_bar(img_a, "mememage-a7cd8f0010140ac9", "aabbccdd11223344")
        result_a = extract_bar(img_a)
        self.assertIsNotNone(result_a)

        # Attacker encodes same bar into different image
        embed_bar(img_b, "mememage-a7cd8f0010140ac9", "aabbccdd11223344")
        result_b = extract_bar(img_b)
        self.assertIsNotNone(result_b)
        self.assertEqual(result_a, result_b)  # Same bar data
        # But images are different — EMBODIED would catch this

        os.unlink(img_a)
        os.unlink(img_b)

    def test_watermark_from_a_not_in_b(self):
        """Per-image watermark layout means A's watermark doesn't extract from B."""
        from mememage.watermark import embed_watermark, extract_watermark
        img_a = _make_textured_image(seed=10)
        img_b = _make_textured_image(seed=20)
        content_hash = "aabbccdd11223344"

        embed_watermark(img_a, content_hash)
        # Extract from image B with A's hash — should NOT match
        wm_b = extract_watermark(img_b, content_hash)
        # B was never watermarked, so extraction should fail or return garbage
        wm_a = extract_watermark(img_a, content_hash)
        self.assertIsNotNone(wm_a)
        # wm_b might return something but it shouldn't match
        if wm_b:
            self.assertNotEqual(wm_a, wm_b)

        os.unlink(img_a)
        os.unlink(img_b)


class TestMultiArtist(unittest.TestCase):
    """Two artists with independent keys cannot interfere with each other."""

    def test_two_artists_independent(self):
        from mememage.signing import verify
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization

        # Artist A
        key_a = Ed25519PrivateKey.generate()
        pub_a = key_a.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        ).hex()
        # Signature payload: id + \0 + content_hash + \0 + thumbnail_hash.
        # Thumbnail hash empty here (no thumbnail in the test record).
        msg = "mememage-shared1234567\x00samehash12345678\x00".encode()
        sig_a = key_a.sign(msg).hex()

        # Artist B
        key_b = Ed25519PrivateKey.generate()
        pub_b = key_b.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        ).hex()
        sig_b = key_b.sign(msg).hex()

        # Each verifies with their own key
        self.assertTrue(verify("mememage-shared1234567", "samehash12345678", sig_a, pub_a))
        self.assertTrue(verify("mememage-shared1234567", "samehash12345678", sig_b, pub_b))
        # Cross-verification fails
        self.assertFalse(verify("mememage-shared1234567", "samehash12345678", sig_a, pub_b))
        self.assertFalse(verify("mememage-shared1234567", "samehash12345678", sig_b, pub_a))

    def test_100_keys_unique_fingerprints(self):
        import hashlib
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization

        fingerprints = set()
        for _ in range(100):
            key = Ed25519PrivateKey.generate()
            pub = key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
            )
            fp_raw = hashlib.sha256(pub).hexdigest()[:16]
            fp = ":".join(fp_raw[i:i+4] for i in range(0, 16, 4))
            fingerprints.add(fp)
        self.assertEqual(len(fingerprints), 100)
