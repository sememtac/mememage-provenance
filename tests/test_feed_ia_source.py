"""The IA-backed feed: a permanent wall sourced from the Internet Archive.

Default feed is local + ephemeral (culls with the image). A creator who
anchors their canonical chain on IA can opt a surface into a permanent wall
via server.json feed.source == "ia": the list is the IA namespace intersected
with their OWN living chain (so dev/test husks in the namespace never leak
onto the wall), and images redirect to IA's permanent copies.

No network: the IA scrape is mocked. Isolated by conftest.
"""
import unittest
from unittest.mock import patch

from mememage import server


class TestFeedSource(unittest.TestCase):
    def test_default_is_local(self):
        with patch.object(server, "_get_server_config", return_value={}):
            self.assertEqual(server._feed_source(), ("local", None))

    def test_ia_opt_in_reads_prefix(self):
        cfg = {"feed": {"source": "ia", "prefix": "phoenix"}}
        with patch.object(server, "_get_server_config", return_value=cfg):
            self.assertEqual(server._feed_source(), ("ia", "phoenix"))

    def test_ia_without_prefix_falls_back_to_chain(self):
        cfg = {"feed": {"source": "ia"}}
        with patch.object(server, "_get_server_config", return_value=cfg), \
             patch("mememage.chains.get_identifier_prefix", return_value="mememage"):
            self.assertEqual(server._feed_source(), ("ia", "mememage"))

    def test_malformed_feed_config_is_local(self):
        for bad in ({"feed": "ia"}, {"feed": {"source": "local"}}, {"feed": {}}):
            with patch.object(server, "_get_server_config", return_value=bad):
                self.assertEqual(server._feed_source()[0], "local")


class TestIaFeedItems(unittest.TestCase):
    def setUp(self):
        server._ia_feed_cache["items"] = None
        server._ia_feed_cache["at"] = 0.0

    def tearDown(self):
        server._ia_feed_cache["items"] = None
        server._ia_feed_cache["at"] = 0.0

    def test_ordered_by_chain_position_newest_first(self):
        # The wall is the operator's living chain, ordered by outer_position
        # (the lineage), NEWEST first. The dict is intentionally NOT in position
        # order, to prove the sort key is outer_position.
        positions = {"mememage-real0000000000a1": 1,
                     "mememage-real0000000000b2": 3,
                     "mememage-real0000000000c3": 2}
        with patch.object(server, "_living_chain_positions", return_value=positions):
            items = server._ia_feed_items()
        self.assertEqual([it["identifier"] for it in items],
                         ["mememage-real0000000000b2",   # pos 3
                          "mememage-real0000000000c3",   # pos 2
                          "mememage-real0000000000a1"])  # pos 1 (last)

    def test_enumerates_from_chain_walk_not_ia_scrape(self):
        # A new star present in the chain walk appears WITHOUT any IA call —
        # this is what makes a fresh mint show instantly instead of after IA
        # indexes it. (The feed no longer scrapes IA at all.)
        self.assertFalse(hasattr(server, "_ia_scrape_identifiers"),
                         "feed must not depend on the IA scrape anymore")
        positions = {"mememage-brandnew0000000a": 5}
        with patch.object(server, "_living_chain_positions", return_value=positions):
            ids = [it["identifier"] for it in server._ia_feed_items()]
        self.assertEqual(ids, ["mememage-brandnew0000000a"])

    def test_invalidate_clears_cache_for_instant_refresh(self):
        first = {"mememage-aaaa0000000000a1": 0}
        with patch.object(server, "_living_chain_positions", return_value=first):
            self.assertEqual(len(server._ia_feed_items()), 1)
        # a new star lands; without invalidation the short cache would hide it
        both = {"mememage-aaaa0000000000a1": 0, "mememage-bbbb0000000000b2": 1}
        server._invalidate_ia_feed()
        with patch.object(server, "_living_chain_positions", return_value=both):
            self.assertEqual(len(server._ia_feed_items()), 2)

    def test_public_feed_uses_ia_when_opted_in(self):
        positions = {"mememage-aaaa0000000000a1": 0}
        with patch.object(server, "_get_server_config",
                          return_value={"feed": {"source": "ia", "prefix": "mememage"}}), \
             patch.object(server, "_living_chain_positions", return_value=positions):
            feed = server._public_feed()
        self.assertEqual([it["identifier"] for it in feed], ["mememage-aaaa0000000000a1"])

    def test_membership_gate_rejects_outsiders(self):
        # An arbitrary archive.org id must NOT be a feed member (no open redirect)
        positions = {"mememage-aaaa0000000000a1": 0}
        with patch.object(server, "_get_server_config",
                          return_value={"feed": {"source": "ia", "prefix": "mememage"}}), \
             patch.object(server, "_living_chain_positions", return_value=positions):
            self.assertTrue(server._ia_feed_member("mememage-aaaa0000000000a1"))
            self.assertFalse(server._ia_feed_member("some-other-archive-item"))
            self.assertFalse(server._ia_feed_member("mememage-notinchain00000"))

    def test_walk_failure_yields_empty_not_crash(self):
        with patch.object(server, "_living_chain_positions", return_value={}):
            self.assertEqual(server._ia_feed_items(), [])

    def test_living_chain_positions_excludes_dark_matter(self):
        # dark-matter records have no IA image, so they must not reach the wall
        records = [
            {"identifier": "mememage-light000000000a", "outer_position": 0, "chain_visibility": 0},
            {"identifier": "mememage-dark0000000000b", "outer_position": 1, "chain_visibility": 1},
        ]
        with patch("mememage.site_embed.walk_living_chain", return_value=records):
            pos = server._living_chain_positions()
        self.assertIn("mememage-light000000000a", pos)
        self.assertNotIn("mememage-dark0000000000b", pos)


class TestIaFeedThumbCrisp(unittest.TestCase):
    """The IA wall's tiles must be CRISP (460px, generated), not IA's tiny
    auto-thumbnail. Regression guard: a build once redirected to __ia_thumb.jpg
    and the tiles went blurry."""

    def setUp(self):
        server._feed_thumb_cache.clear()

    def tearDown(self):
        server._feed_thumb_cache.clear()

    @staticmethod
    def _make_png(path, w=800, h=1000):
        from PIL import Image
        img = Image.new("RGB", (w, h))
        px = img.load()
        for y in range(h):
            for x in range(w):
                px[x, y] = ((x * 7) % 256, (y * 5) % 256, (x + y) % 256)
        img.save(path)

    def test_thumb_from_local_image_is_crisp_460(self):
        import tempfile
        from PIL import Image
        import io
        with tempfile.TemporaryDirectory() as tmp:
            p = f"{tmp}/img.png"
            self._make_png(p)
            with patch.object(server, "_feed_image_path", return_value=p):
                data = server._ia_feed_thumb_bytes("mememage-abcdef0123456789")
            self.assertIsNotNone(data)
            im = Image.open(io.BytesIO(data))
            self.assertEqual(im.size, (460, 460))          # crisp, full-size tile
            self.assertEqual(im.format, "JPEG")

    def test_thumb_falls_back_to_ia_when_local_gone(self):
        import tempfile, io
        from PIL import Image
        # no local image; the permanent IA PNG is fetched once and thumbnailed
        buf = io.BytesIO()
        Image.new("RGB", (600, 800), (20, 80, 160)).save(buf, "PNG")
        png_bytes = buf.getvalue()

        class _Resp:
            def read(self_): return png_bytes
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False

        with patch.object(server, "_feed_image_path", return_value=None), \
             patch("urllib.request.urlopen", return_value=_Resp()):
            data = server._ia_feed_thumb_bytes("mememage-abcdef0123456789")
        self.assertIsNotNone(data)
        self.assertEqual(Image.open(io.BytesIO(data)).size, (460, 460))


if __name__ == "__main__":
    unittest.main()
