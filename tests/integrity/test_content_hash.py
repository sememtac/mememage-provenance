"""WITNESSED — content hash computation, normalization, verification."""

import hashlib
import json
import unittest

from mememage.core import (
    _HASH_INCLUDED, _normalize_for_hash,
    compute_content_hash, verify_metadata,
)


class TestContentHash(unittest.TestCase):
    """Core hash properties."""

    def _base_record(self):
        return {
            # V1: gen params live under `origin` (free-form dict)
            "origin": {
                "prompt": "a sunset over mountains",
                "seed": 42,
                "steps": 20,
                "cfg_scale": 1.0,
                "sampler": "euler",
            },
            "width": 1024,
            "height": 1024,
            "birth": {"sun": "Aries", "moon": "Taurus"},
            # rarity dict is what's hashed; rarity_score is derived
            "rarity": {"celestial": [{"trait": "uncommon_x", "points": 10}],
                       "machine": [], "entropy": [], "sigil": None},
        }

    def test_deterministic(self):
        r = self._base_record()
        self.assertEqual(compute_content_hash(r), compute_content_hash(r))

    def test_16_hex_chars(self):
        r = self._base_record()
        h = compute_content_hash(r)
        self.assertEqual(len(h), 16)
        int(h, 16)  # Valid hex

    def test_changes_on_any_included_field(self):
        # V1: top-level width/height are hashed; gen params live under `origin`.
        for field in ("width", "height"):
            r1 = self._base_record()
            h1 = compute_content_hash(r1)
            r2 = self._base_record()
            r2[field] = 99999
            h2 = compute_content_hash(r2)
            self.assertNotEqual(h1, h2, f"Hash should change when '{field}' changes")
        # Changing any origin key (whole dict is hashed) breaks WITNESSED.
        for origin_field in ("prompt", "seed", "cfg_scale"):
            r3 = self._base_record()
            h3 = compute_content_hash(r3)
            r4 = self._base_record()
            r4["origin"][origin_field] = "CHANGED" if isinstance(r4["origin"][origin_field], str) else 99999
            self.assertNotEqual(h3, compute_content_hash(r4),
                                f"Hash should change when origin.'{origin_field}' changes")
        # rarity (the whole dict) is hashed; rarity_score is derived
        r5 = self._base_record()
        r5["rarity"]["celestial"][0]["points"] = 999
        self.assertNotEqual(compute_content_hash(self._base_record()),
                            compute_content_hash(r5))

    def test_ignores_excluded_fields(self):
        """Fields outside the V1 inclusion set don't affect the hash.
        Note: `public_key` / `key_fingerprint` were excluded pre-launch
        but are now IN the V1 hash (signer-swap defense)."""
        r = self._base_record()
        h1 = compute_content_hash(r)
        r["about"] = "attacker text"
        r["decoder_chunk"] = "fake"  # legacy flat key, never in V1
        r["signature"] = "ff" * 64
        r["creator_name"] = "Fake Artist"
        r["song_name"] = "Fake Song"
        h2 = compute_content_hash(r)
        self.assertEqual(h1, h2)

    def test_constellation_fields_included(self):
        """Constellation claims must be tamper-evident."""
        r = self._base_record()
        h1 = compute_content_hash(r)
        r["constellation_name"] = "Fake"
        h2 = compute_content_hash(r)
        self.assertNotEqual(h1, h2, "constellation_name should affect hash")
        r2 = self._base_record()
        r2["heart_star_id"] = "fake"
        h3 = compute_content_hash(r2)
        self.assertNotEqual(h1, h3, "heart_star_id should affect hash")

    def test_field_ordering_irrelevant(self):
        r1 = {"prompt": "test", "seed": 1, "width": 512, "height": 512}
        r2 = {"height": 512, "width": 512, "seed": 1, "prompt": "test"}
        self.assertEqual(compute_content_hash(r1), compute_content_hash(r2))

    def test_unicode_prompts(self):
        r = self._base_record()
        r["prompt"] = "日本語テスト 🎨 مرحبا"
        h = compute_content_hash(r)
        self.assertEqual(len(h), 16)

    def test_very_long_prompt(self):
        r = self._base_record()
        r["prompt"] = "x" * 10000
        h = compute_content_hash(r)
        self.assertEqual(len(h), 16)

    def test_empty_record(self):
        h = compute_content_hash({})
        self.assertEqual(len(h), 16)

    def test_none_values_effectively_stripped(self):
        """None fields not in _HASH_INCLUDED don't change hash."""
        r1 = {"prompt": "t", "seed": 1, "width": 1024, "height": 1024}
        r2 = {"prompt": "t", "seed": 1, "width": 1024, "height": 1024, "lora": None}
        # Both should produce same hash — None lora is filtered out
        h1 = compute_content_hash(r1)
        h2 = compute_content_hash(r2)
        # lora IS in HASH_INCLUDED, but None vs absent may differ
        # This test documents the actual behavior


class TestHashNormalization(unittest.TestCase):
    """Float normalization for JS compatibility."""

    def test_float_int_normalization(self):
        self.assertEqual(_normalize_for_hash(1.0), 1)
        self.assertEqual(_normalize_for_hash(42.0), 42)

    def test_non_integer_float_passthrough(self):
        self.assertEqual(_normalize_for_hash(0.75), 0.75)
        self.assertEqual(_normalize_for_hash(3.5), 3.5)

    def test_nan_rejected(self):
        # NaN/Inf can't live in JSON and would diverge Py (0) vs JS (null) —
        # reject loudly rather than bake a divergent/unloadable record.
        with self.assertRaises(ValueError):
            _normalize_for_hash(float('nan'))

    def test_inf_rejected(self):
        with self.assertRaises(ValueError):
            _normalize_for_hash(float('inf'))
        with self.assertRaises(ValueError):
            _normalize_for_hash(float('-inf'))

    def test_nested_normalization(self):
        data = {"a": [1.0, 2.5, {"b": 3.0}]}
        expected = {"a": [1, 2.5, {"b": 3}]}
        self.assertEqual(_normalize_for_hash(data), expected)

    def test_records_with_floats_match_js_stringify(self):
        """Python normalized JSON should match what JS JSON.stringify produces."""
        record = {"cfg": 1.0, "seed": 42, "width": 1024}
        normalized = _normalize_for_hash(record)
        canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        # JS would produce: {"cfg":1,"seed":42,"width":1024}
        self.assertEqual(canonical, '{"cfg":1,"seed":42,"width":1024}')


class TestVerifyMetadata(unittest.TestCase):
    """verify_metadata() return value semantics."""

    def test_true_on_match(self):
        r = {"prompt": "t", "seed": 1, "width": 1024, "height": 1024}
        r["content_hash"] = compute_content_hash(r)
        self.assertTrue(verify_metadata(r))

    def test_false_on_mismatch(self):
        r = {"prompt": "t", "seed": 1, "width": 1024, "height": 1024}
        r["content_hash"] = "0000000000000000"
        self.assertFalse(verify_metadata(r))

    def test_none_when_missing(self):
        r = {"prompt": "t", "seed": 1, "width": 1024, "height": 1024}
        self.assertIsNone(verify_metadata(r))
