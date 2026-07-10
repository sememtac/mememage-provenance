"""The push_image feature: blast the conceived image alongside the soul so a
receiving surface shows it in its public feed at full quality.

Drives the REAL http_push channel against a REAL server instance: the channel's
upload() PUTs the soul and (when push_image is on) the image; the server's
receive face stores both; the feed merges the blasted-in image like a local
mint; DELETE withdraws both. Verifies the off and self-push paths stay
soul-only.
"""

import json
import os
import threading
import time
import unittest
import urllib.request

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from mememage import server as S
from mememage.channels.http_push import HttpPushChannel


def _wait_health(base, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if urllib.request.urlopen(base + "/health", timeout=1).status == 200:
                return True
        except Exception:
            time.sleep(0.2)
    return False


@unittest.skipUnless(HAS_PIL, "Pillow required")
class TestFeedImageBlast(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = S._find_free_port("127.0.0.1", 8765)
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.state = {"server": None}
        threading.Thread(
            target=lambda: S.run_server(
                host="127.0.0.1", port=cls.port, certfile=None, keyfile=None,
                open_browser=False,
                on_ready=lambda s: cls.state.update(server=s)),
            daemon=True).start()
        assert _wait_health(cls.base), "test server never came up"
        cls.token = S._load_mint_token()
        # Resolve the soul store the same way the code under test does, rather
        # than hardcoding ~/.mememage/received — that hardcoded path wrote test
        # souls and PNGs into the operator's REAL store (see tests/conftest.py).
        from mememage.core import soul_store_dir
        cls.rdir = str(soul_store_dir())

    @classmethod
    def tearDownClass(cls):
        srv = cls.state.get("server")
        if srv:
            srv.shutdown()

    def _channel(self, push_image, self_push=False):
        ch = HttpPushChannel({"id": "test", "config": {
            "base_url": self.base + "/api/souls", "push_image": push_image}})
        ch._is_self_push = lambda: self_push
        ch._resolve_bearer = lambda: self.token
        return ch

    def _soul_bytes(self, ident):
        return json.dumps({"identifier": ident, "content_hash": "0123456789abcdef",
                           "hash_version": "v1", "chain_visibility": 0,
                           "parent_id": None}).encode()

    def _image(self, ident):
        p = f"/tmp/{ident}.src.png"
        Image.new("RGB", (640, 360), (30, 110, 80)).save(p)
        return p

    def _cleanup(self, ident):
        req = urllib.request.Request(self.base + f"/api/souls/{ident}.soul",
                                     method="DELETE")
        req.add_header("Authorization", f"Bearer {self.token}")
        try:
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass

    def test_push_image_blasts_and_feeds(self):
        ident = "mememage-aaaa11112222bbbb"
        self.addCleanup(self._cleanup, ident)
        src = self._image(ident)
        self._channel(push_image=True).upload(ident, self._soul_bytes(ident), src)

        self.assertTrue(os.path.exists(f"{self.rdir}/{ident}.soul"), "soul not stored")
        self.assertTrue(os.path.exists(f"{self.rdir}/{ident}.png"), "image not stored")

        feed = json.loads(urllib.request.urlopen(self.base + "/api/feed", timeout=5).read())
        self.assertIn(ident, [e["identifier"] for e in feed["feed"]], "not in feed")

        tr = urllib.request.urlopen(self.base + f"/api/feed/thumb/{ident}", timeout=5)
        self.assertEqual(tr.status, 200)
        self.assertEqual(tr.headers.get("Content-Type"), "image/jpeg")

        fr = urllib.request.urlopen(self.base + f"/api/feed/full/{ident}", timeout=5)
        self.assertEqual(fr.headers.get("Content-Type"), "image/png")
        self.assertEqual(fr.read(), open(src, "rb").read(), "full image bytes differ")

    def test_push_image_off_is_soul_only(self):
        ident = "mememage-cccc33334444dddd"
        self.addCleanup(self._cleanup, ident)
        src = self._image(ident)
        self._channel(push_image=False).upload(ident, self._soul_bytes(ident), src)
        self.assertTrue(os.path.exists(f"{self.rdir}/{ident}.soul"), "soul not stored")
        self.assertFalse(os.path.exists(f"{self.rdir}/{ident}.png"),
                         "image blasted with push_image off")
        # soul present but no image → not a feed tile
        feed = json.loads(urllib.request.urlopen(self.base + "/api/feed", timeout=5).read())
        self.assertNotIn(ident, [e["identifier"] for e in feed["feed"]])

    def test_self_push_never_blasts_image(self):
        # Self-push short-circuits before any PUT (the local session already
        # feeds the staged image); even with push_image on, nothing is sent.
        ident = "mememage-eeee55556666ffff"
        self.addCleanup(self._cleanup, ident)
        src = self._image(ident)
        url = self._channel(push_image=True, self_push=True).upload(
            ident, self._soul_bytes(ident), src)
        self.assertTrue(url.endswith(f"{ident}.soul"))
        self.assertFalse(os.path.exists(f"{self.rdir}/{ident}.png"),
                         "self-push blasted an image")

    def test_delete_withdraws_image(self):
        ident = "mememage-7777888899990000"
        src = self._image(ident)
        self._channel(push_image=True).upload(ident, self._soul_bytes(ident), src)
        self.assertTrue(os.path.exists(f"{self.rdir}/{ident}.png"))
        req = urllib.request.Request(self.base + f"/api/souls/{ident}.soul",
                                     method="DELETE")
        req.add_header("Authorization", f"Bearer {self.token}")
        res = json.loads(urllib.request.urlopen(req, timeout=5).read())
        self.assertGreaterEqual(res.get("image_unlinked", 0), 1)
        self.assertFalse(os.path.exists(f"{self.rdir}/{ident}.png"),
                         "image survived soul deletion")


if __name__ == "__main__":
    unittest.main()
