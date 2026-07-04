"""The /api/souls receive face streams the body to disk (never the whole soul
in RAM) so large payload souls work, with a generous, disk-bounding cap.

Regression for the genesis-blocking wedge: a real 6.1 MB payload soul was 400'd
by a 4 MiB cap, and — because the reject fired before the body was drained — the
pushing side saw it as a TLS EOF (`_ssl.c:2406`), masking the real cause.
"""

import io
import tempfile
import unittest
from pathlib import Path

from mememage.server import SOUL_MAX_BYTES, _stream_body_to_file


class SoulSizeCap(unittest.TestCase):
    def test_cap_fits_large_payload_souls(self):
        # Streamed to disk, so generous — must clear real payload souls (MBs).
        self.assertGreaterEqual(SOUL_MAX_BYTES, 256 * 1024 * 1024)

    def test_cap_still_bounded(self):
        # Still bounded so an authed peer can't fill the disk unboundedly.
        self.assertLessEqual(SOUL_MAX_BYTES, 2 * 1024 * 1024 * 1024)


class StreamBodyToFile(unittest.TestCase):
    def _dest(self):
        return Path(tempfile.mkdtemp()) / "out"

    def test_writes_exact_bytes_and_detects_json(self):
        data = b'  {"a":1}' + b"x" * 5000
        dest = self._dest()
        n, head = _stream_body_to_file(io.BytesIO(data), len(data), dest, chunk_size=64)
        self.assertEqual(n, len(data))
        self.assertTrue(head)               # first non-ws byte is '{'
        self.assertEqual(dest.read_bytes(), data)

    def test_array_head_ok(self):
        _, head = _stream_body_to_file(io.BytesIO(b"[1,2]"), 5, self._dest())
        self.assertTrue(head)

    def test_non_json_head_rejected(self):
        _, head = _stream_body_to_file(io.BytesIO(b"<html>nope</html>"), 17, self._dest())
        self.assertFalse(head)

    def test_short_body_raises(self):
        # Client promised 100 bytes but sent 7 — must raise, not silently store.
        with self.assertRaises(ValueError):
            _stream_body_to_file(io.BytesIO(b'{"a":1}'), 100, self._dest())

    def test_streams_large_body(self):
        # ~5 MiB through 256 KiB chunks — writes the whole thing to disk.
        data = b"{" + b"0" * (5 * 1024 * 1024) + b"}"
        dest = self._dest()
        n, head = _stream_body_to_file(io.BytesIO(data), len(data), dest)
        self.assertEqual(n, len(data))
        self.assertTrue(head)
        self.assertEqual(dest.stat().st_size, len(data))


if __name__ == "__main__":
    unittest.main()
