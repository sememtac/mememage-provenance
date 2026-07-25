"""A record that encode accepts must still verify after a JSON round-trip, and
verify must answer every malformed record with a verdict instead of a crash.

From the core audit (2026-07-24). Two defects, both silent:

  * `encode` accepted NON-STRING dict keys. `json.dumps` sorts the original int
    (2, 10) but writes the coerced string, so the record hashed in one order and
    re-hashed in another ("10" < "2") the moment it was saved and read back. The
    record stopped verifying against its own image — a self-inflicted ALTERED.
    (Same class as the JS SDK's F5 bug, but worse: it broke Python round-trips.)
  * `verify` raised AttributeError on a record that was not a dict (None from a
    failed fetch, a list from a JSON array, a raw string) and TypeError on an
    unhashable `hash_version`. The JS SDK already answered these with a verdict.

Verification must never raise on bad data: "I could not check this" is a result,
and it is not tamper evidence.
"""
import json
import unittest

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

import mememage
from mememage import hashing


def _img():
    return Image.new("RGB", (600, 120), (80, 90, 100))


@unittest.skipUnless(HAS_PIL, "Pillow not installed")
class TestRecordSurvivesAJsonRoundTrip(unittest.TestCase):

    def test_non_string_keys_are_refused(self):
        with self.assertRaises(ValueError) as cm:
            mememage.encode(_img(), {"m": {2: "two", 10: "ten"}})
        self.assertIn("keys must be strings", str(cm.exception))

    def test_non_string_keys_refused_at_any_depth(self):
        for fields in ({1: "top"}, {"a": {"b": {3: "deep"}}}, {"a": [{4: "in a list"}]}):
            with self.subTest(fields=fields):
                with self.assertRaises(ValueError):
                    mememage.encode(_img(), fields)

    def test_string_keyed_record_round_trips(self):
        """The numeric-looking keys that broke it, as strings: must stay stable."""
        r = mememage.encode(_img(), {"m": {"2": "two", "10": "ten"}, "n": 3})
        self.assertTrue(mememage.verify(r.image, r.record).match)
        reloaded = json.loads(json.dumps(r.record))
        self.assertTrue(mememage.verify(r.image, reloaded).match,
                        "record stopped verifying after a JSON round-trip")


@unittest.skipUnless(HAS_PIL, "Pillow not installed")
class TestVerifyNeverRaises(unittest.TestCase):

    def setUp(self):
        self.rec = mememage.encode(_img(), {"title": "t"})

    def test_non_dict_records_get_a_verdict(self):
        for bad in (None, [], "not a record", 7):
            with self.subTest(record=bad):
                v = mememage.verify(self.rec.image, bad)
                self.assertFalse(v.match)
                self.assertFalse(v.supported, "a caller mistake is not tamper evidence")
                self.assertIn("JSON object", v.reason)

    def test_unhashable_hash_version_gets_a_verdict(self):
        for hv in (["open"], {"v": "open"}, 1, None):
            with self.subTest(hash_version=hv):
                bad = dict(self.rec.record)
                bad["hash_version"] = hv
                v = mememage.verify(self.rec.image, bad)
                self.assertFalse(v.match)
                self.assertFalse(v.supported)

    def test_unhashable_values_get_a_verdict(self):
        bad = dict(self.rec.record)
        bad["m"] = {5: "int key"}
        v = mememage.verify(self.rec.image, bad)
        self.assertFalse(v.match)
        self.assertFalse(v.supported)
        self.assertIn("can't be hashed", v.reason)

    def test_a_real_mismatch_is_still_reported_as_altered(self):
        """Guard the opposite error: don't let 'unsupported' swallow real tampering."""
        tampered = dict(self.rec.record)
        tampered["title"] = "changed"
        v = mememage.verify(self.rec.image, tampered)
        self.assertFalse(v.match)
        self.assertTrue(v.supported, "a genuine hash mismatch must read as ALTERED")
        self.assertIn("hash mismatch", v.reason)


class TestHashKernelGuards(unittest.TestCase):

    def test_nan_and_infinity_refused(self):
        for v in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=v):
                with self.assertRaises(ValueError):
                    hashing.hash_fields({"v": v})

    def test_is_supported_hash_version_never_raises(self):
        for hv in (["open"], {"a": 1}, 1, None, "open"):
            with self.subTest(hash_version=hv):
                self.assertIsInstance(
                    hashing.is_supported_hash_version({"hash_version": hv}), bool)


if __name__ == "__main__":
    unittest.main()
