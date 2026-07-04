"""Tests for the Creator Access Layer — password-based encryption."""

import json
import unittest


class TestEncryption(unittest.TestCase):
    """Core encryption/decryption round-trips."""

    def test_encrypt_decrypt_field(self):
        from mememage.access import encrypt_field, decrypt_field
        plaintext = "hello, world"
        password = "test-password-123"
        envelope = encrypt_field(plaintext, password)
        self.assertIn("salt", envelope)
        self.assertIn("iv", envelope)
        self.assertIn("ct", envelope)
        self.assertIn("tag", envelope)
        result = decrypt_field(envelope, password)
        self.assertEqual(result, plaintext)

    def test_wrong_password_raises(self):
        from mememage.access import encrypt_field, decrypt_field
        envelope = encrypt_field("secret", "correct")
        with self.assertRaises(ValueError):
            decrypt_field(envelope, "wrong")

    def test_different_passwords_different_ciphertext(self):
        from mememage.access import encrypt_field
        e1 = encrypt_field("same text", "password1")
        e2 = encrypt_field("same text", "password2")
        self.assertNotEqual(e1["ct"], e2["ct"])

    def test_same_password_different_salts(self):
        from mememage.access import encrypt_field
        e1 = encrypt_field("same text", "same-password")
        e2 = encrypt_field("same text", "same-password")
        # Random salt means different ciphertext each time
        self.assertNotEqual(e1["salt"], e2["salt"])

    def test_unicode_plaintext(self):
        from mememage.access import encrypt_field, decrypt_field
        text = "Aries 24.1° — 日本語 🌙"
        envelope = encrypt_field(text, "unicode-test")
        self.assertEqual(decrypt_field(envelope, "unicode-test"), text)

    def test_empty_password(self):
        from mememage.access import encrypt_field, decrypt_field
        envelope = encrypt_field("data", "")
        self.assertEqual(decrypt_field(envelope, ""), "data")

    def test_long_plaintext(self):
        from mememage.access import encrypt_field, decrypt_field
        text = "x" * 100_000
        envelope = encrypt_field(text, "long-test")
        self.assertEqual(decrypt_field(envelope, "long-test"), text)


class TestGPS(unittest.TestCase):
    """GPS encryption convenience functions."""

    def test_encrypt_decrypt_gps(self):
        from mememage.access import encrypt_gps, decrypt_gps
        lat, lon = 45.523100, -122.676500
        envelope = encrypt_gps(lat, lon, "gps-password")
        result_lat, result_lon = decrypt_gps(envelope, "gps-password")
        self.assertAlmostEqual(result_lat, lat, places=6)
        self.assertAlmostEqual(result_lon, lon, places=6)

    def test_gps_wrong_password(self):
        from mememage.access import encrypt_gps, decrypt_gps
        envelope = encrypt_gps(45.0, -122.0, "correct")
        with self.assertRaises(ValueError):
            decrypt_gps(envelope, "wrong")


class TestSoulEncryption(unittest.TestCase):
    """Encrypt/decrypt the full soul fields blob."""

    def test_encrypt_decrypt_soul(self):
        from mememage.access import encrypt_soul, decrypt_soul
        fields = {
            # V1: gen params nested under `origin`
            "origin": {"prompt": "A vast ocean under amber light", "seed": 42},
            "birth": {"sun": "Aries 24°", "moon": "Pisces 8°"},
            "rarity": {"celestial": [{"trait": "x", "points": 15}]},
            "birth_traits": [4],  # sure_footed
        }
        envelope = encrypt_soul(fields, "soul-password")
        result = decrypt_soul(envelope, "soul-password")
        self.assertEqual(result["origin"], fields["origin"])
        self.assertEqual(result["birth"], fields["birth"])
        self.assertEqual(result["rarity"], fields["rarity"])


class TestApplyEncryption(unittest.TestCase):
    """Test the full apply_encryption flow on a record."""

    def _base_record(self):
        return {
            "identifier": "mememage-test123",
            "content_hash": "abcd1234abcd1234",
            # V1: gen params live under `origin`
            "origin": {"prompt": "A test prompt", "seed": 42},
            "width": 1024,
            "height": 1024,
            "birth": {"sun": "Aries 24°"},
            # GPS lives at top-level now (parallel to gps_password_locked
            # which apply_encryption adds when a password is provided)
            "gps_time_locked": {"ct": "xxx", "N": "yyy", "T": 10**18, "e": 3},
            "rarity": {"celestial": [], "machine": [], "entropy": [], "sigil": []},
            "birth_traits": [2, 4],  # uncontested, sure_footed
            "constellation_hash": "1234567890abcdef",
            "machine_fingerprint": "53834153",
        }

    def test_light_energy_gps_only(self):
        from mememage.access import apply_encryption
        record = self._base_record()
        gps = (45.5231, -122.6765)
        apply_encryption(record, gps, "my-password", "light_energy")

        # GPS encrypted
        self.assertIn("gps_password_locked", record)
        # Soul stores int code (0=light_energy, 1=dark_matter)
        self.assertEqual(record["chain_visibility"], 0)
        # Protected fields still plaintext on light chains
        self.assertEqual(record["origin"]["prompt"], "A test prompt")
        self.assertEqual(record["rarity"], {"celestial": [], "machine": [], "entropy": [], "sigil": []})

    def test_dark_matter_full_encryption(self):
        from mememage.access import apply_encryption, decrypt_soul, PROTECTED_FIELDS
        record = self._base_record()
        gps = (45.5231, -122.6765)
        apply_encryption(record, gps, "dark-password", "dark_matter")

        # GPS encrypted
        self.assertIn("gps_password_locked", record)
        self.assertEqual(record["chain_visibility"], 1)
        # Soul encrypted
        self.assertIn("encrypted_fields", record)
        # Protected fields DELETED from the record (full opacity — see
        # docs/chunks-spec.md "Why deletion not sentinels"). On V1,
        # the whole origin dict + birth + rarity + birth_traits go away.
        self.assertNotIn("origin", record)
        self.assertNotIn("rarity", record)
        self.assertNotIn("birth_traits", record)
        self.assertNotIn("birth", record)
        # Always-public fields still readable
        self.assertEqual(record["identifier"], "mememage-test123")
        self.assertEqual(record["content_hash"], "abcd1234abcd1234")

        # Decrypt and verify the sealed soul
        soul = decrypt_soul(record["encrypted_fields"], "dark-password")
        self.assertEqual(soul["origin"]["prompt"], "A test prompt")
        self.assertEqual(soul["rarity"], {"celestial": [], "machine": [], "entropy": [], "sigil": []})
        self.assertEqual(soul["origin"]["seed"], 42)

    def test_dark_matter_chunks_encryption(self):
        """encrypted_chunks envelope replaces the chunks namespace on
        dark_matter. Round-trip recovers the full nested chunks dict."""
        from mememage.access import apply_encryption, decrypt_chunks
        record = self._base_record()
        record["chunks"] = {
            "decoder": {"index": 5, "total": 12, "hash": "abc", "data": "<HTML>"},
            "truth":   {"index": 142, "total": 365, "hash": "def", "data": "<text>"},
        }
        apply_encryption(record, (0.0, 0.0), "dark-password", "dark_matter")

        # chunks namespace deleted, encrypted_chunks present
        self.assertNotIn("chunks", record)
        self.assertIn("encrypted_chunks", record)

        # Round-trip decrypt
        chunks = decrypt_chunks(record["encrypted_chunks"], "dark-password")
        self.assertEqual(chunks["decoder"]["index"], 5)
        self.assertEqual(chunks["decoder"]["data"], "<HTML>")
        self.assertEqual(chunks["truth"]["index"], 142)

    def test_light_energy_does_not_encrypt_chunks(self):
        """light_energy seals only GPS — chunks remain plaintext."""
        from mememage.access import apply_encryption
        record = self._base_record()
        record["chunks"] = {"decoder": {"index": 0, "data": "<HTML>"}}
        apply_encryption(record, (0.0, 0.0), "light-password", "light_energy")

        self.assertIn("chunks", record)
        self.assertNotIn("encrypted_chunks", record)
        self.assertIn("gps_password_locked", record)

    def test_no_password_no_encryption(self):
        from mememage.access import apply_encryption
        record = self._base_record()
        gps = (45.5231, -122.6765)
        # apply_encryption is not called when password is None
        # (handled in _step_encrypt), but if called with empty string:
        original_prompt = record["origin"]["prompt"]
        # This tests the module directly — pipeline skips when password is None
        apply_encryption(record, gps, "", "light_energy")
        self.assertIn("gps_password_locked", record)
        self.assertEqual(record["origin"]["prompt"], original_prompt)


class TestContentHashWithEncryption(unittest.TestCase):
    """Content-hash + encryption interaction.

    Pipeline order (after the dark-matter-verification fix): encryption
    runs BEFORE the content hash. The hash covers what ends up in the
    saved soul — encrypted_fields / encrypted_chunks / gps_password_locked
    on dark chains, plaintext fields on light. Decryption is for the
    viewer's eyes, not for hash recomputation.
    """

    def _base_record(self):
        return {
            "identifier": "mememage-test123",
            "origin": {"prompt": "A test prompt", "seed": 42},
            "width": 1024,
            "height": 1024,
            "birth": {"sun": "Aries 24\u00b0"},
            "rarity": {"celestial": [], "machine": [], "entropy": [], "sigil": []},
            "birth_traits": [4],
            "constellation_hash": "1234567890abcdef",
            "machine_fingerprint": "53834153",
            "parent_id": None,
            "conceived": "2026-05-28T00:00:00Z",
            "chain_visibility": 1,  # dark_matter int code
        }

    def test_dark_matter_hash_recomputes_post_encrypt(self):
        """Encrypt → hash → save → recompute → matches. The core
        invariant the broken pre-encrypt hash order violated."""
        from mememage.core import compute_content_hash
        from mememage.access import apply_encryption

        record = self._base_record()
        apply_encryption(record, (45.0, -122.0), "test-pw", "dark_matter")
        # Plaintext is gone; ciphertext blobs are what gets hashed.
        self.assertNotIn("origin", record)
        self.assertNotIn("birth", record)
        self.assertIn("encrypted_fields", record)
        self.assertIn("gps_password_locked", record)
        record["content_hash"] = compute_content_hash(record)
        # Recompute from the same record (simulates load-from-disk).
        import json
        reloaded = json.loads(json.dumps(record))
        self.assertEqual(reloaded["content_hash"],
                         compute_content_hash(reloaded))

    def test_decryption_recovers_plaintext_but_does_not_affect_hash(self):
        """Decrypting the soul gives the creator back their data, but
        the hash itself was computed over the ciphertext and stays
        valid. Decrypted plaintext is for display, not for re-verifying."""
        from mememage.core import compute_content_hash
        from mememage.access import apply_encryption, decrypt_soul

        record = self._base_record()
        apply_encryption(record, (45.0, -122.0), "test-pw", "dark_matter")
        record["content_hash"] = compute_content_hash(record)
        # Decrypt — recovers the plaintext fields.
        soul = decrypt_soul(record["encrypted_fields"], "test-pw")
        self.assertEqual(soul["origin"]["prompt"], "A test prompt")
        self.assertEqual(soul["birth_traits"], [4])
        # The record's own stored hash is unaffected by decryption.
        self.assertEqual(record["content_hash"],
                         compute_content_hash(record))


if __name__ == "__main__":
    unittest.main()
