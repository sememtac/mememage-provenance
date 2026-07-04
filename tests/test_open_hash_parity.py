"""The "open" hash version — Python ↔ browser parity + model properties.

"open" is the raw / programmatic-adoption hash model: hash EVERY field of the
soul except the two structurally-circular ones (content_hash, signature), so an
adopter's arbitrary fields are all tamper-evident with no schema to opt into.
This pins:

  1. Py↔JS parity — core.compute_content_hash == verify.js computeContentHash
     for the same records, across every value type (str/int/float/bool/null/
     nested dict/list/unicode), via the REAL verify.js engine under node.
  2. The model: arbitrary fields (and identifier/hash_version/public_key) are
     covered; content_hash/signature are not; V1 records are unaffected.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

import pytest

from mememage import core

HERE = os.path.dirname(os.path.abspath(__file__))
CJS = os.path.join(HERE, "open_hash_parity.cjs")
_NODE = shutil.which("node")

# A spread of open-hash records exercising every JSON value type + the security
# properties. Record 5 carries the excluded pair (content_hash/signature) to
# prove they don't leak into the hash on either side.
RECORDS = [
    {"identifier": "mememage-abc1234567890def", "hash_version": "open",
     "prompt": "a quiet river", "license": "CC-BY-4.0"},
    {"hash_version": "open", "by": "andy", "tags": ["river", "calm"],
     "nested": {"camera": "iphone", "iso": 100}},
    {"hash_version": "open", "ratio": 1.0, "half": 0.5, "third": 0.3333333333,
     "count": 7, "flag": True, "none_field": None},
    {"hash_version": "open", "identifier": "phoenix-0000",
     "public_key": "aa", "key_fingerprint": "bb", "unicode": "café ☕ 日本"},
    {"hash_version": "open", "prompt": "p",
     "content_hash": "ffffffffffffffff", "signature": "de" * 64},
    # signed open soul (public_key/key_fingerprint/creator_name/signature)
    {"hash_version": "open", "identifier": "mememage-03cda7e76f53a8cc",
     "prompt": "signed work", "public_key": "aa" * 32,
     "key_fingerprint": "e8fd:e47e:7f44:1c03", "creator_name": "andy",
     "signature": "be" * 64, "content_hash": "2056a95aab605136"},
    # decoder-polluted: `_`-prefixed scratch the verifier hangs on the record
    # (must hash identically to the same soul WITHOUT them).
    {"hash_version": "open", "prompt": "p", "by": "andy",
     "_source": "https://souls.example.com/", "_sealedOriginal": {"x": 1},
     "_verified": True},
]


@pytest.mark.skipif(_NODE is None, reason="node not installed")
class TestOpenHashParity(unittest.TestCase):
    def test_py_js_parity(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            json.dump(RECORDS, f, ensure_ascii=False)
            path = f.name
        try:
            res = subprocess.run([_NODE, CJS, path], capture_output=True,
                                 text=True, timeout=30)
            self.assertEqual(res.returncode, 0,
                             f"node harness failed:\n{res.stderr}")
            js_hashes = json.loads(res.stdout)
        finally:
            os.unlink(path)

        self.assertEqual(len(js_hashes), len(RECORDS))
        for rec, jsh in zip(RECORDS, js_hashes):
            pyh = core.compute_content_hash(rec)
            self.assertEqual(
                pyh, jsh,
                f"Py↔JS open-hash mismatch for {rec}: py={pyh} js={jsh}")


class TestOpenHashModel(unittest.TestCase):
    """Node-free guards on the open model's properties (Python side)."""

    def test_every_field_is_protected(self):
        rec = {"hash_version": "open", "prompt": "a", "license": "b",
               "identifier": "x", "nested": {"k": 1}, "tags": ["t"]}
        h = core.compute_content_hash(rec)
        # Mutate each field; the hash must change (tamper-evident).
        for k in ("prompt", "license", "identifier", "hash_version", "nested", "tags"):
            r = dict(rec)
            v = r[k]
            if isinstance(v, str):
                r[k] = v + "X"
            elif isinstance(v, dict):
                r[k] = {**v, "extra": 9}
            elif isinstance(v, list):
                r[k] = v + ["z"]
            self.assertNotEqual(core.compute_content_hash(r), h,
                                f"{k} is not protected by the open hash")

    def test_circular_pair_excluded(self):
        rec = {"hash_version": "open", "prompt": "a", "by": "andy"}
        h = core.compute_content_hash(rec)
        r = dict(rec)
        r["content_hash"] = h           # the hash's own output
        r["signature"] = "ff" * 64      # signs the hash
        self.assertEqual(core.compute_content_hash(r), h,
                         "content_hash/signature leaked into the open hash")

    def test_underscore_keys_excluded(self):
        # `_`-prefixed keys are decoder/transport internals — never hashed, so a
        # verifier can stamp _source/_sealedOriginal on the record without
        # breaking WITNESSED (the bug an audited core soul hit).
        rec = {"hash_version": "open", "prompt": "a", "by": "andy"}
        h = core.compute_content_hash(rec)
        polluted = dict(rec, _source="https://x/", _sealedOriginal={"k": 1},
                        _verified=True)
        self.assertEqual(core.compute_content_hash(polluted), h,
                         "`_`-prefixed scratch poisoned the open hash")

    def test_public_key_covered_signer_swap(self):
        base = {"hash_version": "open", "prompt": "a"}
        a = dict(base, public_key="aa" * 32)
        b = dict(base, public_key="bb" * 32)
        self.assertNotEqual(core.compute_content_hash(a),
                            core.compute_content_hash(b),
                            "public_key not covered → signer-swap undetected")

    def test_v1_chain_unaffected(self):
        # The canonical chain's positive-list model still ignores non-whitelisted
        # fields — the open model must not bleed into integer versions.
        v1 = {"identifier": "m", "hash_version": 1, "parent_id": None,
              "width": 512, "height": 512, "random_extra": "ignored"}
        h = core.compute_content_hash(v1)
        v1b = dict(v1, random_extra="CHANGED")
        self.assertEqual(core.compute_content_hash(v1b), h,
                         "V1 positive-list now hashing a non-whitelisted field")


if __name__ == "__main__":
    unittest.main()
