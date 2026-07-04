"""Tests for content_hash version dispatch.

The mint pipeline stamps record["hash_version"] at conception time.
verify-side dispatches on that field — NOT on the active version —
so future records keep verifying under their original rules even
as the active version moves forward.

v1 is the launch canon. Earlier dev iterations (v2/v3/v4 — yes the
numbering ran higher pre-launch) are not honored in code because no
public records of those vintages exist.

Pinning this contract now means the next bump (to v2) just adds a
new entry to _HASH_INCLUDED_BY_VERSION + bumps CURRENT_HASH_VERSION.
The verifier path stays untouched.
"""

import hashlib
import json
import unittest

from mememage.core import (
    CURRENT_HASH_VERSION,
    DEFAULT_HASH_VERSION,
    _HASH_INCLUDED_BY_VERSION,
    _inclusion_set_for,
    compute_content_hash,
)


def _hash_via_set(record: dict, include: set) -> str:
    """Compute the 16-char content hash by hand for a given set —
    bypasses _inclusion_set_for so tests can pin what each version's
    rules should produce.
    """
    hashable = {k: v for k, v in record.items() if k in include}
    canonical = json.dumps(hashable, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class TestHashVersionDispatch(unittest.TestCase):
    def test_current_is_v1(self):
        self.assertEqual(CURRENT_HASH_VERSION, 1)
        self.assertEqual(DEFAULT_HASH_VERSION, 1)

    def test_current_version_in_inclusion_map(self):
        self.assertIn(CURRENT_HASH_VERSION, _HASH_INCLUDED_BY_VERSION)

    def test_v1_set_has_known_fields(self):
        """Sanity-check the v1 inclusion set's contents — guards against
        accidental field removal during refactors.
        """
        v1 = _HASH_INCLUDED_BY_VERSION[1]
        for field in ("identifier", "birth", "decoder_hash",
                      "constellation_hash", "machine_fingerprint",
                      # Constellation cadence — hashed so the heart-reset
                      # size that constellation_index derives from is
                      # tamper-evident.
                      "constellation_size",
                      "parent_id", "rarity", "birth_traits",
                      # Creator-declared origin metadata (free-form dict
                      # — replaces the flat prompt/seed/cfg_scale/model
                      # gen-param fields from earlier V1).
                      "origin",
                      "width", "height", "conceived",
                      "gps_time_locked",
                      # Plaintext GPS — present only on gps_visibility:
                      # "public" chains, hashed so the shown location is
                      # tamper-evident (intersection-safe: absent elsewhere).
                      "gps",
                      # Top-level age — hoisted from chunks.decoder.age
                      # so chains without a decoder layer can still
                      # report their Age.
                      "age",
                      # Version dispatch — locked in to shut a
                      # hash_version downgrade attack.
                      "hash_version",
                      # Signer identity — locked in to shut a
                      # signer-swap attack (drop someone else's
                      # public_key in, re-sign, claim authorship).
                      "public_key", "key_fingerprint",
                      # Per-chunk hashes rolled into a single root
                      # (chunk swap → broken WITNESSED; verifier
                      # doesn't need to download chunks to check).
                      "chunks_root",
                      # Outer-cycle position metadata — top-level so
                      # dark_matter records (encrypted chunks) can be
                      # placed on the Observatory grid. Hashed so
                      # position tampering breaks WITNESSED.
                      "outer_position", "outer_total",
                      # Creator-access-layer envelopes — hashed when
                      # present so ciphertext tampering breaks WITNESSED.
                      # On dark_matter records these REPLACE the
                      # plaintext fields in the hash (hash runs after
                      # encryption, fields are gone, ciphertext is what
                      # remains). On light chains gps_password_locked is
                      # the only one typically present (when password
                      # set); the other two are absent and skipped.
                      "encrypted_fields", "encrypted_chunks",
                      "gps_password_locked"):
            self.assertIn(field, v1, f"v1 set missing {field}")
        # Fields that MUST NOT be in the hash
        for field in ("thumbnail", "signature",
                      "creator_name",
                      "distribution",
                      "about", "content_hash",
                      # Pre-launch dev fields explicitly dropped from v1
                      "birth_temperament", "birth_readings", "birth_summary",
                      "cfg", "unet", "mode",
                      # Now live inside `origin`, not flat
                      "prompt", "seed", "cfg_scale", "model", "steps",
                      "sampler", "scheduler", "guidance", "denoise",
                      # Derived from rarity dict — not persisted, not hashed
                      "rarity_score",
                      # Bulk chunk data is not hashed; chunk hashes
                      # are aggregated into chunks_root instead.
                      "chunks"):
            self.assertNotIn(field, v1, f"v1 set should not contain {field}")

    def test_inclusion_set_for_dispatch(self):
        # Known version → matching set
        self.assertIs(
            _inclusion_set_for({"hash_version": 1}),
            _HASH_INCLUDED_BY_VERSION[1],
        )
        # Missing version → default (v1)
        self.assertIs(
            _inclusion_set_for({}),
            _HASH_INCLUDED_BY_VERSION[DEFAULT_HASH_VERSION],
        )
        # Unknown future version → default (best-effort verification,
        # caller will see the mismatch if bytes disagree)
        self.assertIs(
            _inclusion_set_for({"hash_version": 99}),
            _HASH_INCLUDED_BY_VERSION[DEFAULT_HASH_VERSION],
        )

    def test_legacy_dev_versions_not_honored(self):
        """Pre-launch dev versions 2/3/4 are NOT in the inclusion map —
        any test souls left over from those iterations are pre-launch
        artifacts and explicitly don't round-trip through v1.
        """
        for v in (2, 3, 4):
            self.assertNotIn(v, _HASH_INCLUDED_BY_VERSION,
                             f"v{v} dev iteration shouldn't be honored")

    def test_v1_record_computes_under_v1_rules(self):
        record = {
            "hash_version": 1,
            "identifier": "mememage-deadbeefcafe",
            # V1: gen params nested under `origin` (free-form dict).
            "origin": {"prompt": "a cat on a hill", "seed": 42},
            "decoder_hash": "0123456789abcdef",
            # Field NOT in any version's set — must not contribute
            "thumbnail": "data:image/jpeg;base64,abc",
            # Field set by channels framework post-mint — must not contribute
            "distribution": {"ia": "https://example/soul"},
            # Self-doc — explicitly excluded from hash
            "about": "I was born through Mememage…",
            # Signing fields — sign the hash, not part of what's hashed
            "signature": "deadbeef" * 16,
            "public_key": "cafef00d" * 8,
            "key_fingerprint": "1234:5678:90ab:cdef",
        }
        expected = _hash_via_set(record, _HASH_INCLUDED_BY_VERSION[1])
        self.assertEqual(compute_content_hash(record), expected)

    def test_unknown_field_doesnt_affect_hash(self):
        base = {"hash_version": 1, "identifier": "mememage-abc", "seed": 1}
        with_extra = dict(base, ignored_field="anything")
        self.assertEqual(
            compute_content_hash(base),
            compute_content_hash(with_extra),
        )

    def test_dropped_v4_fields_excluded_from_v1(self):
        """birth_temperament / birth_readings / birth_summary / mode / cfg /
        unet were in the dev-era v4 set; v1 drops them. Ensure they
        don't sneak back into the hash via stale code paths."""
        v1 = _HASH_INCLUDED_BY_VERSION[1]
        for field in ("birth_temperament", "birth_readings", "birth_summary",
                      "mode", "cfg", "unet", "decoder_age_name",
                      "decoder_reassembly", "proof_day", "proof_cycle_length",
                      "rarity_score"):
            self.assertNotIn(field, v1)

    def test_synthetic_v2_dispatches_under_synthetic_rules(self):
        """Simulates "after we bump to v2" without committing to a real
        v2 shape — proves the dispatch mechanism works.
        """
        v2_set = _HASH_INCLUDED_BY_VERSION[1] | {"synthetic_v2_field"}
        _HASH_INCLUDED_BY_VERSION[2] = v2_set
        try:
            record = {
                "hash_version": 2,
                "identifier": "mememage-v2test",
                "synthetic_v2_field": "tamper-evident under v2",
                "seed": 7,
            }
            expected = _hash_via_set(record, v2_set)
            self.assertEqual(compute_content_hash(record), expected)

            # Same record stamped as v1 IGNORES synthetic_v2_field
            v1_record = dict(record, hash_version=1)
            v1_expected = _hash_via_set(v1_record, _HASH_INCLUDED_BY_VERSION[1])
            self.assertEqual(compute_content_hash(v1_record), v1_expected)

            # The two MUST differ — that's the whole point of versioning
            self.assertNotEqual(
                compute_content_hash(record),
                compute_content_hash(v1_record),
            )
        finally:
            _HASH_INCLUDED_BY_VERSION.pop(2, None)


if __name__ == "__main__":
    unittest.main()
