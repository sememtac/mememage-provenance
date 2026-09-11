"""_feed_full must not hand the lightbox to IA for an image IA does not have.

Observed 2026-08-14: a mint's IA leg failed with 503 SlowDown, so the soul and
image never reached the Archive. The tile still rendered — _ia_feed_thumb_bytes
prefers the local image — but clicking it 404ed, because _feed_full redirected
to archive.org unconditionally. Feed membership is decided by a LOCAL chain
walk, so it cannot tell whether IA actually holds the file.

These drive the real _feed_full against a stub request object, asserting the
routing decision only (local vs redirect vs 404) and, importantly, that the
_ia_feed_member authorization gate still runs first.
"""

import unittest
from unittest.mock import patch

from mememage import server as S


class _StubRequest:
    """Records what _feed_full decided, without a socket."""

    def __init__(self):
        self.redirected_to = None
        self.error_code = None
        self.body = None
        self.headers_sent = {}
        self.status = None
        self.wfile = self

    def _redirect(self, url, code=302):
        self.redirected_to = url

    def send_error(self, code):
        self.error_code = code

    def send_response(self, code):
        self.status = code

    def send_header(self, k, v):
        self.headers_sent[k] = v

    def end_headers(self):
        pass

    def write(self, data):
        self.body = data


class TestFeedFullFallback(unittest.TestCase):
    IDENT = "mememage-feedfull0001"

    def _run(self, *, source, member, local_path):
        req = _StubRequest()
        with patch.object(S, "_feed_source", return_value=(source, "mememage")), \
             patch.object(S, "_ia_feed_member", return_value=member), \
             patch.object(S, "_feed_image_path", return_value=local_path):
            S.MintHandler._feed_full(req, self.IDENT)
        return req

    def test_ia_wall_serves_local_image_when_present(self):
        # The regression. IA never got the PNG (failed blast / derive lag),
        # but this box holds it — serve it instead of redirecting into a 404.
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".png")
        with os.fdopen(fd, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\nlocalbytes")
        try:
            req = self._run(source="ia", member=True, local_path=path)
        finally:
            os.unlink(path)
        self.assertIsNone(req.redirected_to, "must not redirect when local exists")
        self.assertEqual(req.status, 200)
        self.assertEqual(req.body, b"\x89PNG\r\n\x1a\nlocalbytes")
        self.assertEqual(req.headers_sent.get("Content-Type"), "image/png")

    def test_ia_wall_redirects_when_local_image_is_culled(self):
        # The bandwidth intent, preserved: the long tail still goes to IA.
        req = self._run(source="ia", member=True, local_path=None)
        self.assertEqual(
            req.redirected_to,
            f"{S._IA_DOWNLOAD}/{self.IDENT}/{self.IDENT}.png",
        )
        self.assertIsNone(req.error_code)

    def test_ia_wall_gate_still_runs_before_local_delivery(self):
        # AUTHORIZATION, not just delivery. _ia_feed_member is the only thing
        # excluding dark-matter records (_feed_image_path admits them by
        # design), so a non-member must 404 even with the image on disk —
        # never serve an unlisted conception to a guessed identifier.
        req = self._run(source="ia", member=False, local_path="/tmp/should-not-be-read.png")
        self.assertEqual(req.error_code, 404)
        self.assertIsNone(req.redirected_to)
        self.assertIsNone(req.status)

    def test_local_feed_never_redirects_to_ia(self):
        req = self._run(source="local", member=False, local_path=None)
        self.assertEqual(req.error_code, 404)
        self.assertIsNone(req.redirected_to)


if __name__ == "__main__":
    unittest.main()
