"""Public catalog feed: the front-door wall of recently-conceived images.
Qualifies completed conceptions (both light and dark surface — the image is
plaintext either way), minted image still on disk, AND soul still present
(removing the soul withdraws it). Identifier-only — the token never leaves.
"""
import os
import tempfile
import unittest
from unittest.mock import patch

import mememage.server as srv


class PublicFeed(unittest.TestCase):
    def setUp(self):
        self._saved = dict(srv._sessions)
        srv._sessions.clear()
        srv._feed_thumb_cache.clear()
        self.tmp = tempfile.mkdtemp()
        self.img = os.path.join(self.tmp, "img.png")
        with open(self.img, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")

    def tearDown(self):
        srv._sessions.clear()
        srv._sessions.update(self._saved)

    def _sess(self, ident, chain="lc", created=1, img=None):
        return {"status": "completed", "created": created, "chain": chain,
                "result": {"identifier": ident, "image_path": img or self.img}}

    def test_both_light_and_dark_surface(self):
        srv._sessions["t1"] = self._sess("light-1", chain="lc", created=2)
        srv._sessions["t2"] = self._sess("dark-1", chain="dc", created=3)
        with patch.object(srv, "_soul_on_surface", return_value=True):
            idents = [it["identifier"] for it in srv._public_feed()]
        self.assertEqual(idents, ["dark-1", "light-1"])  # both, newest-first

    def test_removed_soul_withdraws(self):
        srv._sessions["t1"] = self._sess("c-1")
        with patch.object(srv, "_soul_on_surface", return_value=False):
            self.assertEqual(srv._public_feed(), [])

    def test_culled_image_excluded(self):
        srv._sessions["b"] = self._sess("gone", img="/no/such.png")
        with patch.object(srv, "_soul_on_surface", return_value=True):
            self.assertEqual(srv._public_feed(), [])

    def test_image_path_gated_to_soul_present(self):
        srv._sessions["t1"] = self._sess("c-1")
        with patch.object(srv, "_soul_on_surface", return_value=True):
            self.assertEqual(srv._feed_image_path("c-1"), self.img)
        with patch.object(srv, "_soul_on_surface", return_value=False):
            self.assertIsNone(srv._feed_image_path("c-1"))  # withdrawn soul


    def test_paging_covers_all_newest_first(self):
        # The /api/feed handler pages by slicing _public_feed()[offset:offset+N].
        # Whatever the page size, walking offset must cover every conception
        # exactly once, newest-first — that's what makes infinite scroll
        # ("what you scroll is what you get") whole and dup-free.
        for i in range(25):
            srv._sessions["t%02d" % i] = self._sess("id-%02d" % i, created=i)
        with patch.object(srv, "_soul_on_surface", return_value=True):
            full = srv._public_feed()
        self.assertEqual(len(full), 25)
        self.assertEqual(full[0]["identifier"], "id-24")   # newest
        self.assertEqual(full[-1]["identifier"], "id-00")  # oldest
        seen, off, PAGE = [], 0, 10
        while True:
            page = full[off:off + PAGE]            # mirrors the handler slice
            if not page:
                break
            seen += [it["identifier"] for it in page]
            off += len(page)
        self.assertEqual(seen, [it["identifier"] for it in full])  # order kept
        self.assertEqual(len(set(seen)), 25)                       # no dupes

    def test_withdraw_deletes_image_and_drops_session(self):
        srv._sessions["t1"] = self._sess("c-1")
        self.assertTrue(os.path.exists(self.img))
        with patch.object(srv, "_save_sessions"):
            img_n, sess_n = srv._withdraw_conception_image("c-1")
        self.assertEqual((img_n, sess_n), (1, 1))
        self.assertFalse(os.path.exists(self.img))   # minted image reclaimed
        self.assertNotIn("t1", srv._sessions)         # session dropped


if __name__ == "__main__":
    unittest.main()
