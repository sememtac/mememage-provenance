"""IA's 409 BucketAlreadyExists is usually our own retry, not a real collision.

Observed 2026-09-05 (mememage-5dcbe0788ceb572b): the .soul PUT carries
x-amz-auto-make-bucket, so it CREATES the item. Attempt 1 landed server-side
but the client saw a transient failure, so urlopen_with_retry re-sent the same
PUT and IA rejected it with 409 — the retry colliding with the item its own
first attempt had just created.

The raise aborted upload() before the .json and .png PUTs, leaving a record the
browser decoder cannot fetch from IA at all: IA sends no CORS header for .soul,
which is the entire reason the .json mirror exists.

These drive the real InternetArchiveChannel.upload with urlopen_with_retry
patched, asserting the recovery AND that a genuine foreign-owned name is still
refused.
"""

import json
import unittest
import urllib.error
from unittest.mock import patch

from mememage.channels import NamespaceBlocked
from mememage.channels.internet_archive import InternetArchiveChannel


def _http_error(code, body):
    import io as _io
    return urllib.error.HTTPError(
        url="https://s3.us.archive.org/x", code=code, msg="", hdrs={},
        fp=_io.BytesIO(body.encode()),
    )


_CONFLICT = (
    "<?xml version='1.0' encoding='UTF-8'?>\n<Error>"
    "<Code>BucketAlreadyExists</Code><Message>The requested bucket name is not "
    "available.</Message></Error>"
)
_FORBIDDEN = (
    "<?xml version='1.0' encoding='UTF-8'?>\n<Error><Code>AccessDenied</Code>"
    "<Message>You do not have permission.</Message></Error>"
)

SOUL = json.dumps({"identifier": "mememage-collide00001",
                   "chain_visibility": 0}).encode()


class _Recorder:
    """Stands in for urlopen_with_retry, scripting a response per call."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []          # (method, url, has_make_bucket)

    def __call__(self, req, **kw):
        self.calls.append((
            req.get_method(),
            req.full_url,
            req.get_header("X-amz-auto-make-bucket") is not None,
        ))
        outcome = self.script.pop(0) if self.script else b""
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def urls(self):
        return [u for _m, u, _b in self.calls]


class TestBucketAlreadyExists(unittest.TestCase):
    IDENT = "mememage-collide00001"

    def _channel(self):
        ch = InternetArchiveChannel({"id": "ia", "type": "internet_archive"})
        ch._read_credential = lambda name: "test-key"   # no env dependency
        return ch

    def test_409_recovers_and_still_uploads_json_mirror(self):
        # The regression. 409 on the creating PUT must not abort the upload:
        # the .json mirror is what the browser decoder can actually fetch.
        rec = _Recorder([_http_error(409, _CONFLICT), b"", b""])
        with patch("mememage.channels.internet_archive.urlopen_with_retry", rec):
            url = self._channel().upload(self.IDENT, SOUL)
        self.assertIn(self.IDENT, url)
        self.assertEqual(len(rec.calls), 3, "expected create, re-PUT, .json")
        self.assertTrue(rec.urls()[2].endswith(".json"),
                        "the .json mirror must still be uploaded after a 409")

    def test_409_reput_drops_the_bucket_creation_header(self):
        # The item already exists, so the retry must be a plain add — the same
        # shape as the .json/.png PUTs. Re-sending the creation header is what
        # IA rejected in the first place.
        rec = _Recorder([_http_error(409, _CONFLICT), b"", b""])
        with patch("mememage.channels.internet_archive.urlopen_with_retry", rec):
            self._channel().upload(self.IDENT, SOUL)
        self.assertTrue(rec.calls[0][2], "attempt 1 creates the item")
        self.assertFalse(rec.calls[1][2], "the re-PUT must not ask to make a bucket")

    def test_409_then_403_is_a_real_collision(self):
        # A name genuinely owned by another account. Re-roll the identifier
        # rather than overwrite a stranger's item.
        rec = _Recorder([_http_error(409, _CONFLICT), _http_error(403, _FORBIDDEN)])
        with patch("mememage.channels.internet_archive.urlopen_with_retry", rec):
            with self.assertRaises(NamespaceBlocked):
                self._channel().upload(self.IDENT, SOUL)

    def test_409_then_500_still_fails(self):
        rec = _Recorder([_http_error(409, _CONFLICT), _http_error(500, "boom")])
        with patch("mememage.channels.internet_archive.urlopen_with_retry", rec):
            with self.assertRaises(RuntimeError):
                self._channel().upload(self.IDENT, SOUL)

    def test_unrelated_409_is_not_swallowed(self):
        # Only BucketAlreadyExists gets the recovery. Another 409 must raise.
        rec = _Recorder([_http_error(409, "<Error><Code>SomethingElse</Code></Error>")])
        with patch("mememage.channels.internet_archive.urlopen_with_retry", rec):
            with self.assertRaises(RuntimeError):
                self._channel().upload(self.IDENT, SOUL)

    def test_taken_offline_403_still_raises_namespace_blocked(self):
        rec = _Recorder([_http_error(403, "This item is taken offline.")])
        with patch("mememage.channels.internet_archive.urlopen_with_retry", rec):
            with self.assertRaises(NamespaceBlocked):
                self._channel().upload(self.IDENT, SOUL)

    def test_clean_upload_is_unchanged(self):
        rec = _Recorder([b"", b""])
        with patch("mememage.channels.internet_archive.urlopen_with_retry", rec):
            self._channel().upload(self.IDENT, SOUL)
        self.assertEqual(len(rec.calls), 2, "create + .json, no extra PUT")


class TestRetryIsLogged(unittest.TestCase):
    """A retried write must leave a trace. The 2026-09-05 incident was hard to
    read precisely because attempt 1 was invisible in the log."""

    def test_retry_emits_a_warning(self):
        import urllib.request
        from mememage import net
        req = urllib.request.Request("https://example.com/x", data=b"x", method="PUT")
        with patch("mememage.net.time.sleep"), \
             patch("mememage.net.urllib.request.urlopen",
                   side_effect=[urllib.error.URLError("timed out"),
                                _CtxBody(b"ok")]):
            with self.assertLogs("mememage.net", level="WARNING") as cm:
                net.urlopen_with_retry(req, max_retries=2, base_delay=0)
        self.assertTrue(any("Retry 1/2" in m and "PUT" in m for m in cm.output),
                        cm.output)


class _CtxBody:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return self.body


if __name__ == "__main__":
    unittest.main()
