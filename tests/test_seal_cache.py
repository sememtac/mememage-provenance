"""_load_seal memoization: a large-payload seal (hundreds of MB) is parsed once
and reused across the several calls a conception makes, instead of re-read +
re-parsed each time (which multiplied memory until a small box OOM'd). The cache
is keyed on file identity, so a re-seal busts it.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import mememage.site_embed as se


class SealCache(unittest.TestCase):
    def setUp(self):
        se._seal_cache["key"] = None
        se._seal_cache["data"] = None
        self.d = Path(tempfile.mkdtemp())
        self.f = self.d / "sealed_chunks.json"

    def _seal(self, obj):
        self.f.write_text(json.dumps(obj))

    def test_memoized_returns_same_object(self):
        self._seal({"layer_chunks": {"a": 1}})
        with patch.object(se, "seal_file", return_value=self.f):
            s1 = se._load_seal()
            s2 = se._load_seal()
        self.assertEqual(s1, {"layer_chunks": {"a": 1}})
        self.assertIs(s1, s2)  # cached — NOT re-parsed

    def test_busts_when_seal_file_changes(self):
        self._seal({"layer_chunks": {"a": 1}})
        with patch.object(se, "seal_file", return_value=self.f):
            s1 = se._load_seal()
            # Re-seal with a different-size payload → new (mtime, size) → bust.
            self._seal({"layer_chunks": {"a": 1, "b": 2, "c": 3}})
            s2 = se._load_seal()
        self.assertEqual(s1, {"layer_chunks": {"a": 1}})
        self.assertEqual(s2, {"layer_chunks": {"a": 1, "b": 2, "c": 3}})
        self.assertIsNot(s1, s2)

    def test_none_when_unsealed(self):
        with patch.object(se, "seal_file", return_value=self.d / "nope.json"):
            self.assertIsNone(se._load_seal())


if __name__ == "__main__":
    unittest.main()
