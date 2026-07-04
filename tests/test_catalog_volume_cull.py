"""Catalog is culled by VOLUME, not time.

Drafts (pending/failed) still age out on the 7-day TTL, but COMPLETED
conceptions — the wall of art — are bounded by COUNT: keep the newest
`catalog_limit`, evict the oldest (unlinking their staged images). 0 = unlimited.
"""

import os
import tempfile
import time
import unittest
from unittest.mock import patch

import mememage.server as srv


class CatalogVolumeCull(unittest.TestCase):
    def setUp(self):
        self._saved = dict(srv._sessions)
        srv._sessions.clear()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        srv._sessions.clear()
        srv._sessions.update(self._saved)

    def _img(self, name):
        p = os.path.join(self.tmp, name)
        with open(p, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
        return p

    def _completed(self, ident, created):
        img = self._img(ident + ".png")
        return img, {"status": "completed", "created": created,
                     "image_path": img,
                     "result": {"identifier": ident, "image_path": img}}

    def _limit(self, n):
        return patch.object(srv, "_get_server_config", lambda: {"catalog_limit": n})

    def test_keeps_newest_evicts_oldest(self):
        imgs = {}
        for i in range(10):
            img, s = self._completed("c-%02d" % i, created=1000 + i)
            srv._sessions["t-%02d" % i] = s
            imgs[i] = img
        with self._limit(3), patch.object(srv, "_save_sessions"):
            srv._cleanup_expired()
        # Newest 3 survive (created 1007/1008/1009).
        kept = {srv._sessions[t]["result"]["identifier"] for t in srv._sessions}
        self.assertEqual(kept, {"c-07", "c-08", "c-09"})
        # Evicted ones had their staged images unlinked; survivors keep theirs.
        for i in range(7):
            self.assertFalse(os.path.exists(imgs[i]), i)
        for i in (7, 8, 9):
            self.assertTrue(os.path.exists(imgs[i]), i)

    def test_zero_means_unlimited(self):
        for i in range(20):
            _img, s = self._completed("u-%02d" % i, created=1000 + i)
            srv._sessions["t-%02d" % i] = s
        with self._limit(0), patch.object(srv, "_save_sessions"):
            srv._cleanup_expired()
        self.assertEqual(len(srv._sessions), 20)  # nothing culled

    def test_completed_not_time_culled(self):
        # An OLD completed conception (older than the 7-day TTL) is kept while
        # under the volume limit — the wall no longer ages out.
        ancient = time.time() - 30 * 24 * 3600
        _img, s = self._completed("old", created=ancient)
        srv._sessions["t-old"] = s
        with self._limit(500), patch.object(srv, "_save_sessions"):
            srv._cleanup_expired()
        self.assertIn("t-old", srv._sessions)

    def test_drafts_still_time_reaped(self):
        now = time.time()
        # fresh pending kept; stale pending + stale failed reaped
        fresh = self._img("fresh.png")
        srv._sessions["fresh"] = {"status": "pending", "created": now,
                                  "image_path": fresh}
        stale_p = self._img("stale_p.png")
        srv._sessions["stale-pending"] = {"status": "pending",
                                          "created": now - 8 * 24 * 3600,
                                          "image_path": stale_p}
        stale_f = self._img("stale_f.png")
        srv._sessions["stale-failed"] = {"status": "failed",
                                         "created": now - 8 * 24 * 3600,
                                         "image_path": stale_f}
        with self._limit(500), patch.object(srv, "_save_sessions"):
            srv._cleanup_expired()
        self.assertIn("fresh", srv._sessions)
        self.assertNotIn("stale-pending", srv._sessions)
        self.assertNotIn("stale-failed", srv._sessions)
        self.assertFalse(os.path.exists(stale_p))
        self.assertFalse(os.path.exists(stale_f))


if __name__ == "__main__":
    unittest.main()
