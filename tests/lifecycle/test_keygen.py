"""Key generation — file creation, permissions, revocation cert."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests._profile_isolation import isolate_profiles


def _temp_key_dir():
    d = tempfile.mkdtemp(prefix="mememage_test_keys_")
    return Path(d)


class TestKeygen(unittest.TestCase):

    def setUp(self):
        self.key_dir = _temp_key_dir()
        # Profile-aware: keygen writes into <key_dir>/profiles/default/
        # via the patched profile resolver. ``self.key_dir`` itself stays
        # as the conceptual root for parity with the old layout where
        # files lived directly under it.
        self.profile_dir = isolate_profiles(self, self.key_dir)

    def tearDown(self):
        shutil.rmtree(self.key_dir, ignore_errors=True)

    def test_keygen_creates_all_files(self):
        from mememage.signing import keygen
        keygen(name="Test")
        self.assertTrue((self.profile_dir / "private.key").exists())
        self.assertTrue((self.profile_dir / "public.key").exists())
        self.assertTrue((self.profile_dir / "creator.txt").exists())
        self.assertTrue((self.profile_dir / "revocation.cert").exists())

    def test_keygen_private_key_permissions(self):
        from mememage.signing import keygen
        keygen()
        mode = os.stat(self.profile_dir / "private.key").st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_keygen_public_key_is_64_hex(self):
        from mememage.signing import keygen
        _, pub_hex, _ = keygen()
        self.assertEqual(len(pub_hex), 64)
        int(pub_hex, 16)  # Must be valid hex

    def test_keygen_fingerprint_format(self):
        from mememage.signing import keygen
        fp, _, _ = keygen()
        parts = fp.split(":")
        self.assertEqual(len(parts), 4)
        for part in parts:
            self.assertEqual(len(part), 4)
            int(part, 16)  # Valid hex

    def test_keygen_refuses_overwrite(self):
        from mememage.signing import keygen
        keygen()
        with self.assertRaises(FileExistsError):
            keygen()

    def test_keygen_force_overwrites(self):
        from mememage.signing import keygen
        fp1, _, _ = keygen()
        fp2, _, _ = keygen(force=True)
        self.assertNotEqual(fp1, fp2)

    def test_keygen_stores_creator_name(self):
        from mememage.signing import keygen, get_creator_name
        keygen(name="Catmemes")
        self.assertEqual(get_creator_name(), "Catmemes")

    def test_keygen_no_name_no_creator_file(self):
        from mememage.signing import keygen, get_creator_name
        keygen()
        # creator.txt not written when no name given
        self.assertIsNone(get_creator_name())

    def test_revocation_cert_is_valid(self):
        from mememage.signing import keygen, get_revocation, verify_keychain_record
        keygen(name="Test")
        cert = get_revocation()
        self.assertIsNotNone(cert)
        self.assertEqual(cert["action"], "revoke")
        self.assertTrue(verify_keychain_record(cert))

    def test_revocation_cert_contains_fingerprint(self):
        from mememage.signing import keygen, get_revocation, get_fingerprint
        keygen()
        cert = get_revocation()
        self.assertEqual(cert["key_fingerprint"], get_fingerprint())

    def test_revocation_cert_has_timestamp(self):
        from mememage.signing import keygen, get_revocation
        keygen()
        cert = get_revocation()
        self.assertIn("created", cert)
        self.assertRegex(cert["created"], r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

    def test_revocation_cert_cannot_be_forged(self):
        """A different key cannot produce a valid revocation cert for this fingerprint."""
        from mememage.signing import keygen, get_revocation, verify_keychain_record
        keygen(name="Real")
        cert = get_revocation()

        # Generate attacker key and try to forge revocation
        keygen(force=True, name="Attacker")
        attacker_cert = get_revocation()

        # Swap fingerprint to target the original key
        attacker_cert["key_fingerprint"] = cert["key_fingerprint"]
        attacker_cert["public_key"] = cert["public_key"]
        # Signature is from attacker's key, but public_key is victim's
        self.assertFalse(verify_keychain_record(attacker_cert))
