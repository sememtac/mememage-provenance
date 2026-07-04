"""Tests for mememage.net — retry logic and fetch helpers."""

import unittest
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

from mememage.net import fetch_json, urlopen_with_retry


class TestUrlOpenWithRetry(unittest.TestCase):
    @patch("mememage.net.time.sleep")
    @patch("mememage.net.urllib.request.urlopen")
    def test_retries_on_500(self, mock_urlopen, mock_sleep):
        error = urllib.error.HTTPError(
            url="", code=500, msg="ISE", hdrs=None, fp=BytesIO(b"")
        )
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = b"ok"

        mock_urlopen.side_effect = [error, mock_resp]

        req = urllib.request.Request("https://example.com")
        result = urlopen_with_retry(req, max_retries=3, base_delay=0.1)
        assert result == b"ok"
        assert mock_urlopen.call_count == 2
        assert mock_sleep.call_count == 1

    @patch("mememage.net.time.sleep")
    @patch("mememage.net.urllib.request.urlopen")
    def test_retries_on_429(self, mock_urlopen, mock_sleep):
        error = urllib.error.HTTPError(
            url="", code=429, msg="Rate Limited", hdrs=None, fp=BytesIO(b"")
        )
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = b"ok"

        mock_urlopen.side_effect = [error, error, mock_resp]

        req = urllib.request.Request("https://example.com")
        result = urlopen_with_retry(req, max_retries=3, base_delay=0.1)
        assert result == b"ok"
        assert mock_sleep.call_count == 2

    @patch("mememage.net.urllib.request.urlopen")
    def test_no_retry_on_400(self, mock_urlopen):
        error = urllib.error.HTTPError(
            url="", code=400, msg="Bad Request", hdrs=None, fp=BytesIO(b"")
        )
        mock_urlopen.side_effect = error

        req = urllib.request.Request("https://example.com")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urlopen_with_retry(req, max_retries=3)
        assert ctx.exception.code == 400
        assert mock_urlopen.call_count == 1

    @patch("mememage.net.urllib.request.urlopen")
    def test_no_retry_on_403(self, mock_urlopen):
        error = urllib.error.HTTPError(
            url="", code=403, msg="Forbidden", hdrs=None, fp=BytesIO(b"")
        )
        mock_urlopen.side_effect = error

        req = urllib.request.Request("https://example.com")
        with self.assertRaises(urllib.error.HTTPError):
            urlopen_with_retry(req, max_retries=3)
        assert mock_urlopen.call_count == 1

    @patch("mememage.net.time.sleep")
    @patch("mememage.net.urllib.request.urlopen")
    def test_exhausts_retries_on_persistent_failure(self, mock_urlopen, mock_sleep):
        error = urllib.error.HTTPError(
            url="", code=503, msg="Unavailable", hdrs=None, fp=BytesIO(b"")
        )
        mock_urlopen.side_effect = error

        req = urllib.request.Request("https://example.com")
        with self.assertRaises(urllib.error.HTTPError):
            urlopen_with_retry(req, max_retries=2)
        # 1 initial + 2 retries = 3 total
        assert mock_urlopen.call_count == 3

    @patch("mememage.net.time.sleep")
    @patch("mememage.net.urllib.request.urlopen")
    def test_retries_on_network_error(self, mock_urlopen, mock_sleep):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = b"ok"

        mock_urlopen.side_effect = [OSError("Connection refused"), mock_resp]

        req = urllib.request.Request("https://example.com")
        result = urlopen_with_retry(req, max_retries=2, base_delay=0.1)
        assert result == b"ok"

    @patch("mememage.net.time.sleep")
    @patch("mememage.net.urllib.request.urlopen")
    def test_exponential_backoff_delays(self, mock_urlopen, mock_sleep):
        error = urllib.error.HTTPError(
            url="", code=502, msg="Bad Gateway", hdrs=None, fp=BytesIO(b"")
        )
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = b"ok"

        mock_urlopen.side_effect = [error, error, mock_resp]

        req = urllib.request.Request("https://example.com")
        urlopen_with_retry(req, max_retries=3, base_delay=1.0)

        delays = [c[0][0] for c in mock_sleep.call_args_list]
        assert delays[0] == 1.0   # 1.0 * 2^0
        assert delays[1] == 2.0   # 1.0 * 2^1


class TestFetchJson(unittest.TestCase):
    @patch("mememage.net.urlopen_with_retry")
    def test_returns_parsed_json(self, mock_urlopen):
        mock_urlopen.return_value = b'{"prompt": "hello"}'
        result = fetch_json("https://example.com/data.json")
        assert result == {"prompt": "hello"}

    @patch("mememage.net.urlopen_with_retry")
    def test_returns_none_on_404(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="", code=404, msg="Not Found", hdrs=None, fp=BytesIO(b"")
        )
        result = fetch_json("https://example.com/missing.json")
        assert result is None


if __name__ == "__main__":
    unittest.main()
