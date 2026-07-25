"""The access layer fails closed, and says why.

Core audit round 4 (2026-07-24). The cryptography itself held up: 16 envelope
mutations (flip / truncate / empty / drop across salt, iv, ct, tag) all failed
closed with no plaintext, tampering with the ciphertext reads ALTERED because the
hash covers the envelope, and a sealed record verifies without the password.

Two defects, both the round-2 shape — input handling, not maths:

  * `unlock` RETURNED a plausible dict for a record it had not opened.
    `unlock([])` gave `{}`; `unlock({"encrypted_fields": []})` gave the record
    back unchanged. A caller asking for the readable view could not tell that
    nothing was unlocked.
  * malformed envelopes escaped as whatever Python happened to raise —
    `TypeError("'NoneType' object is not subscriptable")`, `KeyError('salt')` —
    against a documented ValueError contract, telling the caller nothing.
"""
import copy
import json
import os
import unicodedata
import unittest

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

import mememage
from mememage import crypto

PW = "correct horse battery staple"
SECRET = "the prompt nobody should see"


def _img():
    return Image.new("RGB", (600, 120), (70, 90, 110))


@unittest.skipUnless(crypto.is_encryption_available(), "cryptography not installed")
class TestEnvelopeFailsClosed(unittest.TestCase):

    def setUp(self):
        self.env = crypto.encrypt_field(SECRET, PW)

    def test_every_mutation_is_rejected(self):
        for key in ("salt", "iv", "ct", "tag"):
            for how in ("flip", "truncate", "empty", "drop"):
                with self.subTest(field=key, mutation=how):
                    e = dict(self.env)
                    v = e[key]
                    if how == "flip":
                        e[key] = ("f" if v[0] != "f" else "0") + v[1:]
                    elif how == "truncate":
                        e[key] = v[:-2]
                    elif how == "empty":
                        e[key] = ""
                    else:
                        del e[key]
                    with self.assertRaises(ValueError):
                        crypto.decrypt_field(e, PW)

    def test_malformed_envelopes_raise_valueerror_not_internals(self):
        for bad in (None, [], "x", 7, {}, {"salt": "00"},
                    {"salt": "zz", "iv": "zz", "ct": "zz", "tag": "zz"},
                    {"salt": 1, "iv": 2, "ct": 3, "tag": 4}):
            with self.subTest(envelope=bad):
                with self.assertRaises(ValueError):
                    crypto.decrypt_field(bad, PW)

    def test_wrong_password_raises(self):
        with self.assertRaises(ValueError):
            crypto.decrypt_field(self.env, "wrong")

    def test_passwords_of_every_shape_round_trip(self):
        """No password policy: whatever the user brings must work."""
        for pw in ("", "pässwörd🔑", "x" * 5000, " leading and trailing "):
            with self.subTest(password=pw[:12]):
                e = crypto.encrypt_field(SECRET, pw)
                self.assertEqual(crypto.decrypt_field(e, pw), SECRET)


@unittest.skipUnless(crypto.is_encryption_available(), "cryptography not installed")
class TestKdfCostTravelsWithTheEnvelope(unittest.TestCase):
    """The iteration count is stored per envelope, not assumed from a constant.

    Before this, `_PBKDF2_ITERATIONS` was code-only: raising it (when OWASP next
    raises its figure) would have stranded every existing sealed record, with no
    way to know what cost a record was sealed at. Same reasoning as hash_version
    travelling with a record.
    """

    def test_new_envelopes_record_the_cost(self):
        env = crypto.encrypt_field(SECRET, PW)
        self.assertEqual(env["iterations"], crypto._PBKDF2_ITERATIONS)

    def test_legacy_envelope_without_the_field_still_opens(self):
        env = crypto.encrypt_field(SECRET, PW)
        del env["iterations"]                     # as sealed before the field existed
        self.assertEqual(crypto.decrypt_field(env, PW), SECRET)

    def test_a_non_default_cost_is_honoured(self):
        """Prove the stored value is USED, not decoration."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        salt, iv = os.urandom(16), os.urandom(12)
        key = crypto._derive_key(PW, salt, 1000)
        blob = AESGCM(key).encrypt(iv, SECRET.encode(), None)
        env = {"salt": salt.hex(), "iv": iv.hex(), "ct": blob[:-16].hex(),
               "tag": blob[-16:].hex(), "iterations": 1000}
        self.assertEqual(crypto.decrypt_field(env, PW), SECRET)
        del env["iterations"]                     # now it must NOT open at the default
        with self.assertRaises(ValueError):
            crypto.decrypt_field(env, PW)

    def test_a_hostile_count_is_refused(self):
        """A billion iterations would hang whoever opens the record."""
        env = crypto.encrypt_field(SECRET, PW)
        for bad in (0, -1, "600000", True, 1.5, None, 10 ** 9):
            with self.subTest(iterations=bad):
                with self.assertRaises(ValueError):
                    crypto.decrypt_field(dict(env, iterations=bad), PW)

    def test_the_legacy_default_is_frozen(self):
        """600k is what opens every pre-field envelope; it must never move."""
        self.assertEqual(crypto._LEGACY_ITERATIONS, 600_000)


@unittest.skipUnless(crypto.is_encryption_available(), "cryptography not installed")
class TestPasswordsAreNfcNormalized(unittest.TestCase):
    """The same visible passphrase must open a record whatever form it arrives in.

    "café" is 4 code points composed (NFC) and 5 decomposed (NFD), and macOS input
    paths and filesystems produce either — so before this, a password that looked
    identical failed to unlock on another machine. NFC is what RFC 8265's
    OpaqueString profile prescribes for passwords.

    Blast radius when this landed: ZERO. All 69 sealed records in the chain used an
    ASCII passphrase, and NFC is the identity function on ASCII.
    """

    NFC = unicodedata.normalize("NFC", "café-naïve-ñ")
    NFD = unicodedata.normalize("NFD", "café-naïve-ñ")

    def test_the_two_forms_are_different_strings(self):
        """Guard the premise — if these ever compare equal the test proves nothing."""
        self.assertNotEqual(self.NFC, self.NFD)
        self.assertLess(len(self.NFC), len(self.NFD))

    def test_sealed_nfc_opens_with_nfd(self):
        env = crypto.encrypt_field(SECRET, self.NFC)
        self.assertEqual(crypto.decrypt_field(env, self.NFD), SECRET)

    def test_sealed_nfd_opens_with_nfc(self):
        env = crypto.encrypt_field(SECRET, self.NFD)
        self.assertEqual(crypto.decrypt_field(env, self.NFC), SECRET)

    def test_ascii_passwords_are_untouched(self):
        """Why no existing record broke: normalization is identity on ASCII."""
        for pw in ("hunter2", "correct horse battery staple", "", "p@ss w0rd!"):
            with self.subTest(password=pw[:14]):
                self.assertEqual(crypto.normalize_password(pw), pw)

    def test_a_wrong_password_is_still_wrong(self):
        env = crypto.encrypt_field(SECRET, self.NFC)
        with self.assertRaises(ValueError):
            crypto.decrypt_field(env, "cafe-naive-n")     # visually similar, not the same

    def test_the_chain_verifier_normalizes_too(self):
        """The gate and the lock must never disagree about what the password is."""
        from mememage import chains
        v = chains._make_verifier(self.NFD)
        self.assertTrue(chains._check_verifier(self.NFC, v))
        self.assertFalse(chains._check_verifier("nope", v))


@unittest.skipUnless(HAS_PIL and crypto.is_encryption_available(), "needs Pillow + cryptography")
class TestSealedRecordProperties(unittest.TestCase):

    def setUp(self):
        self.r = mememage.encode(_img(), {"prompt": SECRET, "public": "ok"},
                                 password=PW, private=["prompt"])

    def test_plaintext_is_gone_and_public_fields_remain(self):
        self.assertNotIn("prompt", self.r.record)
        self.assertEqual(self.r.record.get("public"), "ok")
        self.assertNotIn(SECRET, json.dumps(self.r.record))

    def test_verifies_without_the_password(self):
        self.assertTrue(mememage.verify(self.r.image, self.r.record).match)

    def test_unlock_round_trips(self):
        self.assertEqual(mememage.unlock(self.r.record, PW)["prompt"], SECRET)

    def test_ciphertext_tamper_reads_altered(self):
        t = copy.deepcopy(self.r.record)
        ct = t["encrypted_fields"]["ct"]
        t["encrypted_fields"]["ct"] = ("f" if ct[0] != "f" else "0") + ct[1:]
        v = mememage.verify(self.r.image, t)
        self.assertFalse(v.match)
        self.assertTrue(v.supported, "a tampered envelope is ALTERED, not unsupported")

    def test_envelope_swap_reads_altered(self):
        """An attacker replacing the sealed payload wholesale must not verify."""
        t = copy.deepcopy(self.r.record)
        t["encrypted_fields"] = crypto.encrypt_field(json.dumps({"prompt": "innocent"}), "otherpw")
        self.assertFalse(mememage.verify(self.r.image, t).match)

    def test_empty_password_still_seals(self):
        r = mememage.encode(_img(), {"a": "1"}, password="")
        self.assertTrue(mememage.is_encrypted(r.record))
        self.assertNotIn("a", r.record)


@unittest.skipUnless(HAS_PIL and crypto.is_encryption_available(), "needs Pillow + cryptography")
class TestUnlockNeverReturnsAnUnopenedRecord(unittest.TestCase):

    def test_non_dict_records_raise(self):
        for bad in (None, [], "not a record", 7):
            with self.subTest(record=bad):
                with self.assertRaises(ValueError):
                    mememage.unlock(bad, PW)

    def test_malformed_envelope_raises_instead_of_returning(self):
        for env in ("nope", [], 7, {}):
            with self.subTest(envelope=env):
                with self.assertRaises(ValueError):
                    mememage.unlock({"encrypted_fields": env}, PW)

    def test_unsealed_record_is_returned_unchanged(self):
        plain = {"identifier": "mememage-0123456789abcdef", "a": "1"}
        self.assertEqual(mememage.unlock(plain, PW), plain)

    def test_wrong_password_raises(self):
        r = mememage.encode(_img(), {"prompt": SECRET}, password=PW)
        with self.assertRaises(ValueError):
            mememage.unlock(r.record, "wrong")


if __name__ == "__main__":
    unittest.main()
