"""AUTHENTICATED — Ed25519 sign/verify round-trip and failure modes."""

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from tests._profile_isolation import isolate_profiles


def _temp_key_dir():
    d = tempfile.mkdtemp(prefix="mememage_test_keys_")
    return Path(d)


class TestSignVerifyRoundTrip(unittest.TestCase):
    """Core sign → verify cycle."""

    def setUp(self):
        self.key_dir = _temp_key_dir()
        self.profile_dir = isolate_profiles(self, self.key_dir)
        from mememage.signing import keygen
        keygen(name="TestArtist")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.key_dir, ignore_errors=True)

    def test_sign_and_verify_round_trip(self):
        from mememage.signing import sign, verify
        result = sign("mememage-abc123", "deadbeef12345678")
        self.assertIsNotNone(result)
        sig, pub, fp, name = result
        self.assertTrue(verify("mememage-abc123", "deadbeef12345678", sig, pub))

    def test_verify_false_for_wrong_identifier(self):
        from mememage.signing import sign, verify
        sig, pub, _, _ = sign("mememage-abc123", "deadbeef12345678")
        self.assertFalse(verify("mememage-WRONG", "deadbeef12345678", sig, pub))

    def test_verify_false_for_wrong_content_hash(self):
        from mememage.signing import sign, verify
        sig, pub, _, _ = sign("mememage-abc123", "deadbeef12345678")
        self.assertFalse(verify("mememage-abc123", "tampered_hash___", sig, pub))

    def test_verify_false_for_wrong_signature(self):
        from mememage.signing import sign, verify
        _, pub, _, _ = sign("mememage-abc123", "deadbeef12345678")
        fake_sig = "ff" * 64
        self.assertFalse(verify("mememage-abc123", "deadbeef12345678", fake_sig, pub))

    def test_verify_false_for_wrong_public_key(self):
        from mememage.signing import sign, verify
        sig, _, _, _ = sign("mememage-abc123", "deadbeef12345678")
        wrong_key = "ff" * 32
        self.assertFalse(verify("mememage-abc123", "deadbeef12345678", sig, wrong_key))

    def test_sign_returns_four_tuple(self):
        from mememage.signing import sign
        result = sign("mememage-test", "abcdef0123456789")
        self.assertEqual(len(result), 4)
        sig, pub, fp, name = result
        self.assertEqual(len(sig), 128)  # 64 bytes hex
        self.assertEqual(len(pub), 64)   # 32 bytes hex
        self.assertIn(":", fp)
        self.assertEqual(name, "TestArtist")

    def test_sign_returns_none_without_key(self):
        from mememage.signing import sign
        os.remove(self.profile_dir / "private.key")
        self.assertIsNone(sign("mememage-test", "abcdef0123456789"))

    def test_null_byte_separator_is_critical(self):
        """Signature over 'id + hash' (no \\0) must NOT verify against 'id + \\0 + hash'."""
        from mememage.signing import sign, verify
        sig, pub, _, _ = sign("mememage-abc123", "deadbeef12345678")
        # Manually sign without null byte
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        pk = load_pem_private_key((self.profile_dir / "private.key").read_bytes(), password=None)
        bad_msg = "mememage-abc123deadbeef12345678".encode("utf-8")
        bad_sig = pk.sign(bad_msg).hex()
        # This should fail because verify uses \0 separator
        self.assertFalse(verify("mememage-abc123", "deadbeef12345678", bad_sig, pub))

    def test_verify_empty_signature(self):
        from mememage.signing import verify
        self.assertFalse(verify("mememage-test", "abcdef", "", "ff" * 32))

    def test_verify_malformed_hex(self):
        from mememage.signing import verify
        self.assertFalse(verify("mememage-test", "abcdef", "ZZZZ", "ff" * 32))

    def test_verify_truncated_signature(self):
        from mememage.signing import sign, verify
        sig, pub, _, _ = sign("mememage-test", "abcdef0123456789")
        self.assertFalse(verify("mememage-test", "abcdef0123456789", sig[:64], pub))

    def test_signature_field_not_in_hash_included(self):
        """`signature` cannot be in the hash (chicken-and-egg: it signs
        the hash). `creator_name` is also excluded — a display claim
        tied to the key, not the record. `public_key` and
        `key_fingerprint` ARE in the V1 hash (signer-swap defense)."""
        from mememage.core import _HASH_INCLUDED
        for field in ("signature", "creator_name"):
            self.assertNotIn(field, _HASH_INCLUDED)
        for field in ("public_key", "key_fingerprint"):
            self.assertIn(field, _HASH_INCLUDED)


class TestSignatureScope(unittest.TestCase):
    """Signature interaction with the pipeline."""

    def setUp(self):
        self.key_dir = _temp_key_dir()
        self.profile_dir = isolate_profiles(self, self.key_dir)
        from mememage.signing import keygen
        keygen(name="PipelineTest")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.key_dir, ignore_errors=True)

    def test_signed_record_verifiable(self):
        """Pipeline hash + post-pipeline signature verify together.

        Signing happens post-mint in production (mint.py binds
        sha256(thumbnail) too); here we sign the metadata-only shape
        to prove the pipeline's identifier + content_hash sign and
        verify cleanly.
        """
        from mememage.core import (
            ConceptionState, _step_validate, _step_birth_certificate,
            _step_rarity, _step_identity, _step_build_record,
            _step_content_hash, _step_signer_setup,
        )
        from mememage.signing import sign, verify

        state = ConceptionState(
            metadata={"prompt": "test", "seed": 42, "width": 1024, "height": 1024},
            gps=(45.0, -122.0),
        )
        state._rendered = None
        _step_validate(state)
        _step_birth_certificate(state)
        _step_rarity(state)
        _step_identity(state)
        state.identifier = "mememage-test1234567890"
        _step_build_record(state)
        # V1: public_key + key_fingerprint go INTO the hash, so signer
        # info must be populated before _step_content_hash.
        _step_signer_setup(state)
        _step_content_hash(state)
        sig_result = sign(state.identifier, state.content_hash)

        self.assertIsNotNone(sig_result)
        sig_hex = sig_result[0]
        self.assertTrue(verify(
            state.identifier, state.content_hash,
            sig_hex, state.public_key,
        ))

    def test_unsigned_record_graceful(self):
        """Pipeline completes without signature when no key exists."""
        from mememage.core import (
            ConceptionState, _step_validate, _step_birth_certificate,
            _step_rarity, _step_identity, _step_build_record,
            _step_content_hash,
        )
        from mememage.signing import sign
        os.remove(self.profile_dir / "private.key")

        state = ConceptionState(
            metadata={"prompt": "test", "seed": 42, "width": 1024, "height": 1024},
            gps=(45.0, -122.0),
        )
        state._rendered = None
        _step_validate(state)
        _step_birth_certificate(state)
        _step_rarity(state)
        _step_identity(state)
        state.identifier = "mememage-unsigned123456"
        _step_build_record(state)
        _step_content_hash(state)

        self.assertIsNone(sign(state.identifier, state.content_hash))
        self.assertNotIn("signature", state.record)

    def test_signature_does_not_affect_content_hash(self):
        """Adding the signature itself doesn't change content_hash
        (it's the output of the hash; chicken-and-egg). Same for the
        display-only `creator_name`. But `public_key` /
        `key_fingerprint` DO affect the hash (V1 signer-swap defense)
        — they're populated before hashing, not after."""
        from mememage.core import compute_content_hash
        record = {
            "prompt": "test", "seed": 42, "width": 1024, "height": 1024,
            "birth": {"sun": "Aries"}, "rarity_score": 50,
        }
        hash_before = compute_content_hash(record)
        record["signature"] = "ff" * 64
        record["creator_name"] = "Attacker"
        hash_after = compute_content_hash(record)
        self.assertEqual(hash_before, hash_after)
        # Confirm the opposite for hashed signer fields:
        record["public_key"] = "ff" * 32
        record["key_fingerprint"] = "dead:beef:dead:beef"
        hash_with_signer = compute_content_hash(record)
        self.assertNotEqual(hash_before, hash_with_signer)
