"""Two-tier feed thumbnail cache — the fix for the slow IA-backed wall.

The live catalog on an IA-backed surface (server.json feed.source == "ia")
generates a 460px tile per conception. When the full local image has been
volume-culled, that tile is made by downloading the permanent full-resolution
PNG from the Internet Archive — a multi-MB fetch. Two failure modes made this
hammer IA:

  1. The in-memory cache ceiling (400) sat BELOW the default catalog_limit
     (500) and cleared WHOLESALE on overflow, so any wall past the limit
     re-generated every tile on the next scroll.
  2. The cache was memory-only, so every restart / eviction re-paid the IA
     download.

The two-tier cache (in-memory LRU + on-disk persistence) closes both. These
tests lock the behavior. Isolated by conftest (session-scoped temp root); no
network — the IA fetch is mocked and asserted to run at most once.
"""
import io
import shutil
import unittest
from unittest.mock import patch

from PIL import Image

from mememage import server


def _png_bytes(w=600, h=800, color=(20, 80, 160)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


class _CountingResp:
    """A urlopen stand-in that records how many times the IA PNG was read."""
    calls = 0

    def __init__(self, payload):
        self._payload = payload

    def read(self):
        type(self).calls += 1
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ThumbCacheBase(unittest.TestCase):
    def setUp(self):
        self._reset()

    def tearDown(self):
        self._reset()

    @staticmethod
    def _reset():
        server._feed_thumb_cache.clear()
        shutil.rmtree(server._feed_thumb_dir(), ignore_errors=True)


class TestDiskCacheSparesIA(ThumbCacheBase):
    def test_ia_png_fetched_at_most_once_across_evictions_and_restart(self):
        ident = "mememage-cache00000000a1"
        payload = _png_bytes()
        resp = _CountingResp(payload)

        # Local image is gone (culled) → the tile can only come from IA or cache.
        with patch.object(server, "_feed_image_path", return_value=None), \
             patch("urllib.request.urlopen", return_value=resp):
            first = server._ia_feed_thumb_bytes(ident)
            self.assertIsNotNone(first)
            self.assertEqual(_CountingResp.calls, 1)  # paid the IA download once

            # Memory eviction (simulates the LRU pushing this tile out, or a
            # cache clear). The disk copy must survive and serve the next hit.
            server._feed_thumb_cache.clear()
            second = server._ia_feed_thumb_bytes(ident)
            self.assertEqual(second, first)
            self.assertEqual(_CountingResp.calls, 1)  # NOT re-fetched from IA

            # Simulate a restart: both a fresh process's empty memory tier and
            # the persistent disk tier. Disk still spares IA.
            server._feed_thumb_cache.clear()
            third = server._ia_feed_thumb_bytes(ident)
            self.assertEqual(third, first)
            self.assertEqual(_CountingResp.calls, 1)

    def test_local_thumb_persists_to_disk_for_after_cull(self):
        # A tile generated from the local image while the image is present must
        # be written to disk, so it's already cached when the image later culls.
        ident = "mememage-cache00000000b2"
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = f"{tmp}/img.png"
            Image.new("RGB", (800, 1000), (10, 20, 30)).save(p)
            with patch.object(server, "_feed_image_path", return_value=p):
                server._ia_feed_thumb_bytes(ident)
        # Image path now gone; disk copy alone must satisfy the request with no
        # IA call at all.
        with patch.object(server, "_feed_image_path", return_value=None), \
             patch("urllib.request.urlopen",
                   side_effect=AssertionError("must not touch IA")):
            data = server._ia_feed_thumb_bytes(ident)
        self.assertIsNotNone(data)
        self.assertEqual(Image.open(io.BytesIO(data)).size, (460, 460))


class TestMemoryLRU(ThumbCacheBase):
    def test_eviction_is_incremental_not_wholesale_clear(self):
        # The bug: overflow cleared the ENTIRE cache. The fix evicts only the
        # oldest entries, so a full wall stays mostly resident.
        with patch.object(server, "_FEED_THUMB_MEM_MAX", 4):
            for i in range(6):
                server._feed_thumb_mem_put(f"id{i:02d}", bytes([i]))
            keys = list(server._feed_thumb_cache.keys())
        # 6 inserted, cap 4 → the 4 newest survive; NOT emptied to 0/1.
        self.assertEqual(keys, ["id02", "id03", "id04", "id05"])

    def test_ceiling_exceeds_default_catalog_limit(self):
        # The specific regression: ceiling must be >= the wall size so a full
        # default wall (catalog_limit 500) never overflows into eviction.
        self.assertGreaterEqual(server._FEED_THUMB_MEM_MAX,
                                server.CATALOG_LIMIT_DEFAULT)

    def test_recent_access_survives_eviction(self):
        with patch.object(server, "_FEED_THUMB_MEM_MAX", 3):
            for i in range(3):
                server._feed_thumb_mem_put(f"id{i}", bytes([i]))
            server._feed_thumb_mem_get("id0")          # touch the oldest
            server._feed_thumb_mem_put("id9", b"z")     # overflow by one
            keys = list(server._feed_thumb_cache.keys())
        self.assertIn("id0", keys)      # kept — recently used
        self.assertNotIn("id1", keys)   # evicted — now the oldest


class TestPathSafety(ThumbCacheBase):
    def test_traversal_identifiers_rejected(self):
        for bad in ("../../etc/passwd", "a/b", "a\\b", "", ".", ".."):
            self.assertIsNone(server._feed_thumb_path(bad))

    def test_normal_identifier_maps_under_cache_dir(self):
        p = server._feed_thumb_path("mememage-abcdef0123456789")
        self.assertIsNotNone(p)
        self.assertEqual(p.parent, server._feed_thumb_dir())
        self.assertTrue(p.name.endswith(".jpg"))


class TestDiskCull(ThumbCacheBase):
    def test_disk_dir_bounded_to_newest(self):
        import time
        with patch.object(server, "_FEED_THUMB_DISK_MAX", 2):
            for i in range(5):
                server._feed_thumb_disk_put(f"c{i}", b"y")
                time.sleep(0.01)  # distinct mtimes so "newest" is well-defined
            server._feed_thumb_disk_cull()
            left = sorted(p.stem for p in server._feed_thumb_dir().glob("*.jpg"))
        self.assertEqual(left, ["c3", "c4"])


if __name__ == "__main__":
    unittest.main()
