"""Key rotation — succession records, key archival, chain verification."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests._profile_isolation import isolate_profiles


def _temp_key_dir():
    d = tempfile.mkdtemp(prefix="mememage_test_keys_")
    return Path(d)


class TestKeyRotation(unittest.TestCase):

    def setUp(self):
        self.key_dir = _temp_key_dir()
        self.profile_dir = isolate_profiles(self, self.key_dir)
        from mememage.signing import keygen
        self.original_fp, self.original_pub, _ = keygen(name="Original")

    def tearDown(self):
        shutil.rmtree(self.key_dir, ignore_errors=True)

    def test_rotate_produces_new_key(self):
        from mememage.signing import rotate, get_fingerprint
        new_fp, _, _ = rotate()
        self.assertNotEqual(new_fp, self.original_fp)
        self.assertEqual(get_fingerprint(), new_fp)

    def test_rotate_archives_old_key(self):
        from mememage.signing import rotate
        rotate()
        clean_fp = self.original_fp.replace(":", "")
        archived = self.profile_dir / "keychain" / f"{clean_fp}.key"
        self.assertTrue(archived.exists())

    def test_succession_record_signed_by_old_key(self):
        from mememage.signing import rotate, verify_keychain_record
        _, succession, _ = rotate()
        self.assertTrue(verify_keychain_record(succession))

    def test_succession_contains_both_fingerprints(self):
        from mememage.signing import rotate
        new_fp, succession, _ = rotate()
        self.assertEqual(succession["old_fingerprint"], self.original_fp)
        self.assertEqual(succession["new_fingerprint"], new_fp)

    def test_succession_contains_both_public_keys(self):
        from mememage.signing import rotate
        _, succession, _ = rotate()
        self.assertEqual(succession["old_public_key"], self.original_pub)
        self.assertNotEqual(succession["new_public_key"], self.original_pub)
        self.assertEqual(len(succession["new_public_key"]), 64)

    def test_old_signatures_still_verify(self):
        """Records signed with old key still verify using old public key."""
        from mememage.signing import sign, verify, rotate
        sig, pub, _, _ = sign("mememage-old", "hash_old_1234567")
        rotate()
        # Old signature still valid with old public key
        self.assertTrue(verify("mememage-old", "hash_old_1234567", sig, pub))

    def test_new_signatures_use_new_key(self):
        from mememage.signing import sign, rotate
        rotate()
        result = sign("mememage-new", "hash_new_1234567")
        _, new_pub, new_fp, _ = result
        self.assertNotEqual(new_pub, self.original_pub)

    def test_rotate_without_existing_key_fails(self):
        from mememage.signing import rotate
        os.remove(self.profile_dir / "private.key")
        with self.assertRaises(FileNotFoundError):
            rotate()

    def test_succession_chain_two_rotations(self):
        """old → mid → new: both succession records verify."""
        from mememage.signing import rotate, verify_keychain_record
        mid_fp, succession1, _ = rotate()
        new_fp, succession2, _ = rotate()
        self.assertTrue(verify_keychain_record(succession1))
        self.assertTrue(verify_keychain_record(succession2))
        self.assertEqual(succession1["new_fingerprint"], succession2["old_fingerprint"])

    def test_rotate_preserves_creator_name(self):
        from mememage.signing import rotate, get_creator_name
        rotate()
        self.assertEqual(get_creator_name(), "Original")

    def test_keychain_identifier_format(self):
        from mememage.signing import keychain_identifier
        result = keychain_identifier("86cb:4ed6:af3f:d6c5")
        self.assertEqual(result, "mememage-keychain-86cb4ed6af3fd6c5")
