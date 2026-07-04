"""Dark chains must seal public-GPS coordinates.

The plaintext ``gps`` field (present only on gps_visibility:"public") was missing
from PROTECTED_FIELDS, so a dark-matter chain with public GPS leaked the exact
location in plaintext on an otherwise-sealed soul. It's now sealed: gone from the
visible record, recoverable only with the chain password. Light chains never hit
the dark-only deletion, so public GPS stays public there as intended.

The content hash runs AFTER encryption (encrypt -> hash), so on dark chains gps
is deleted before hashing — its integrity rides ``encrypted_fields`` — and verify
uses include-if-present, so both old and new souls verify cleanly.
"""

import unittest

from mememage.access import apply_encryption, decrypt_soul, is_encryption_available


def _record():
    return {
        "identifier": "dark-aaaabbbbccccdddd",
        "gps": [45.123456, -122.654321],
        "origin": {"prompt": "x"},
        "content_hash": "h",
    }


@unittest.skipUnless(is_encryption_available(), "cryptography not installed")
class DarkGpsSeal(unittest.TestCase):
    def test_dark_chain_seals_public_gps(self):
        rec = _record()
        apply_encryption(rec, gps=(45.123456, -122.654321), password="pw",
                         chain_visibility="dark_matter")
        self.assertNotIn("gps", rec)          # not leaked in plaintext
        self.assertIn("encrypted_fields", rec)

    def test_dark_gps_recoverable_with_password(self):
        rec = _record()
        apply_encryption(rec, gps=(45.123456, -122.654321), password="pw",
                         chain_visibility="dark_matter")
        restored = decrypt_soul(rec["encrypted_fields"], "pw")
        self.assertEqual(restored.get("gps"), [45.123456, -122.654321])

    def test_light_chain_keeps_public_gps(self):
        rec = _record()
        apply_encryption(rec, gps=(45.123456, -122.654321), password="pw",
                         chain_visibility="light_energy")
        self.assertIn("gps", rec)             # public stays public on light chains
        self.assertEqual(rec["gps"], [45.123456, -122.654321])


if __name__ == "__main__":
    unittest.main()
