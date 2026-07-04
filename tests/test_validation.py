"""Tests for metadata validation in mememage.core."""

import unittest

from mememage.core import _validate_fetched, _validate_metadata


class TestValidateMetadata(unittest.TestCase):
    def test_valid_metadata(self):
        """Should not raise for valid metadata."""
        meta = {"prompt": "test", "seed": 42, "width": 512, "height": 512}
        _validate_metadata(meta)  # should not raise

    def test_missing_prompt_ok(self):
        """Prompt is optional (non-AI-gen mints have no prompt)."""
        meta = {"seed": 42, "width": 512, "height": 512}
        _validate_metadata(meta)  # should not raise

    def test_missing_seed_ok(self):
        """Seed is optional (non-AI-gen mints have no seed)."""
        meta = {"prompt": "test", "width": 512, "height": 512}
        _validate_metadata(meta)  # should not raise

    def test_no_generation_metadata_ok(self):
        """A photograph or screenshot can mint with width+height only."""
        meta = {"width": 1920, "height": 1080}
        _validate_metadata(meta)  # should not raise

    def test_missing_dimensions(self):
        meta = {"prompt": "test", "seed": 42}
        with self.assertRaises(ValueError) as ctx:
            _validate_metadata(meta)
        self.assertIn("height", str(ctx.exception))
        self.assertIn("width", str(ctx.exception))

    def test_negative_width(self):
        meta = {"prompt": "test", "seed": 42, "width": -1, "height": 512}
        with self.assertRaises(ValueError) as ctx:
            _validate_metadata(meta)
        self.assertIn("width", str(ctx.exception))

    def test_zero_height(self):
        meta = {"prompt": "test", "seed": 42, "width": 512, "height": 0}
        with self.assertRaises(ValueError) as ctx:
            _validate_metadata(meta)
        self.assertIn("height", str(ctx.exception))

    def test_seed_as_string_ok(self):
        """Seed can be a string (some workflows use string seeds)."""
        meta = {"prompt": "test", "seed": "12345", "width": 512, "height": 512}
        _validate_metadata(meta)  # should not raise

    def test_seed_as_list_fails(self):
        meta = {"prompt": "test", "seed": [1, 2], "width": 512, "height": 512}
        with self.assertRaises(ValueError) as ctx:
            _validate_metadata(meta)
        self.assertIn("seed", str(ctx.exception))


class TestValidateFetched(unittest.TestCase):
    def test_valid_record(self):
        """Identifier is the marker of a mememage record. Prompt/seed
        are optional AI-gen payload."""
        record = {"identifier": "mememage-abc123def456", "prompt": "hello", "seed": 42}
        result = _validate_fetched(record)
        self.assertEqual(result, record)

    def test_minimal_record_ok(self):
        """A non-AI mint has no prompt, no seed — identifier alone is enough."""
        record = {"identifier": "mememage-abc123def456"}
        _validate_fetched(record)  # should not raise

    def test_missing_identifier_fails(self):
        record = {"content_hash": "deadbeef12345678", "prompt": "hello"}
        with self.assertRaises(ValueError):
            _validate_fetched(record)

    def test_empty_record_fails(self):
        with self.assertRaises(ValueError):
            _validate_fetched({})

    def test_non_dict_fails(self):
        with self.assertRaises(ValueError):
            _validate_fetched("not a dict")

    def test_non_mememage_record_fails(self):
        with self.assertRaises(ValueError):
            _validate_fetched({"title": "some random IA item"})


if __name__ == "__main__":
    unittest.main()
