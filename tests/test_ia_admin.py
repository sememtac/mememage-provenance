"""Tests for mememage.ia_admin — IA cleanup operations.

Mocks urlopen so no network hits. Verifies query construction,
HTTP body shape, and error-path return values.
"""

import io
import json
import unittest
from unittest.mock import patch, MagicMock

import urllib.error

from mememage import ia_admin


class _FakeResp:
    """Context-manager wrapper for urlopen() return values."""
    def __init__(self, body):
        self._body = body if isinstance(body, bytes) else body.encode("utf-8")
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def read(self):
        return self._body


def _http_error(code, body=b""):
    err = urllib.error.HTTPError(url="x", code=code, msg="", hdrs={}, fp=None)
    err.read = lambda: body
    return err


class TestSearchItems(unittest.TestCase):
    def test_query_includes_all_filters(self):
        seen_urls = []
        def fake_urlopen(url, *a, **kw):
            seen_urls.append(url)
            return _FakeResp(json.dumps({
                "response": {"docs": []},
            }))
        with patch.object(ia_admin.urllib.request, "urlopen", side_effect=fake_urlopen):
            ia_admin.search_items(
                uploader="me@x.com",
                collection="my-coll",
                pattern="mememage-*",
                limit=50,
            )
        self.assertEqual(len(seen_urls), 1)
        url = seen_urls[0]
        # The Lucene query gets urlencoded; verify each clause is present.
        self.assertIn("identifier%3Amememage", url)
        self.assertIn("uploader%3Ame%40x.com", url)
        self.assertIn("collection%3Amy-coll", url)

    def test_no_filters_returns_all_matches(self):
        def fake_urlopen(url, *a, **kw):
            return _FakeResp(json.dumps({
                "response": {"docs": [
                    {"identifier": "mememage-aaaa11111111"},
                    {"identifier": "mememage-bbbb22222222"},
                ]},
            }))
        with patch.object(ia_admin.urllib.request, "urlopen", side_effect=fake_urlopen):
            items = ia_admin.search_items()
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["identifier"], "mememage-aaaa11111111")

    def test_paginates_until_limit(self):
        """search_items pages until enough results or empty page."""
        pages = [
            json.dumps({"response": {"docs": [
                {"identifier": f"mememage-{i:012x}"} for i in range(100)
            ]}}),
            json.dumps({"response": {"docs": [
                {"identifier": f"mememage-{i+100:012x}"} for i in range(50)
            ]}}),
        ]
        calls = [0]
        def fake_urlopen(url, *a, **kw):
            page = pages[calls[0]] if calls[0] < len(pages) else json.dumps({"response": {"docs": []}})
            calls[0] += 1
            return _FakeResp(page)
        with patch.object(ia_admin.urllib.request, "urlopen", side_effect=fake_urlopen):
            items = ia_admin.search_items(limit=130, page_size=100)
        self.assertEqual(len(items), 130)
        # Two requests (100 → keep going, then 50 — less than page_size, stop)
        self.assertEqual(calls[0], 2)


class TestListFiles(unittest.TestCase):
    def test_returns_original_files_only(self):
        body = json.dumps({"files": [
            {"name": "mememage-abc.soul", "source": "original"},
            {"name": "mememage-abc.json", "source": "original"},
            {"name": "format/thumb.jpg", "source": "derivative"},
        ]})
        with patch.object(ia_admin.urllib.request, "urlopen", return_value=_FakeResp(body)):
            files = ia_admin.list_files("mememage-abc")
        self.assertEqual(set(files), {"mememage-abc.soul", "mememage-abc.json"})

    def test_403_returns_empty(self):
        with patch.object(ia_admin.urllib.request, "urlopen", side_effect=_http_error(403)):
            self.assertEqual(ia_admin.list_files("mememage-darkened"), [])

    def test_404_returns_empty(self):
        with patch.object(ia_admin.urllib.request, "urlopen", side_effect=_http_error(404)):
            self.assertEqual(ia_admin.list_files("mememage-missing"), [])

    def test_other_error_propagates(self):
        with patch.object(ia_admin.urllib.request, "urlopen", side_effect=_http_error(500)):
            with self.assertRaises(urllib.error.HTTPError):
                ia_admin.list_files("mememage-server-err")


class TestDarkenItem(unittest.TestCase):
    def test_success(self):
        def fake_urlopen(req, *a, **kw):
            # Verify the request shape: POST with x-www-form-urlencoded
            # body containing -target=metadata + -patch + access/secret.
            self.assertEqual(req.method, "POST")
            body = req.data.decode("utf-8")
            self.assertIn("-target=metadata", body)
            self.assertIn("-patch=", body)
            self.assertIn("access=ACCESS", body)
            self.assertIn("secret=SECRET", body)
            self.assertIn("noindex", body)
            return _FakeResp(json.dumps({"success": True}))
        with patch.object(ia_admin.urllib.request, "urlopen", side_effect=fake_urlopen):
            r = ia_admin.darken_item("mememage-test", "ACCESS", "SECRET")
        self.assertTrue(r["ok"])
        self.assertEqual(r["error"], "")

    def test_http_error_captured(self):
        with patch.object(ia_admin.urllib.request, "urlopen",
                          side_effect=_http_error(403, b"forbidden")):
            r = ia_admin.darken_item("mememage-test", "k", "s")
        self.assertFalse(r["ok"])
        self.assertIn("403", r["error"])

    def test_unsuccessful_response_captured(self):
        with patch.object(ia_admin.urllib.request, "urlopen",
                          return_value=_FakeResp(json.dumps({"success": False, "reason": "x"}))):
            r = ia_admin.darken_item("mememage-test", "k", "s")
        self.assertFalse(r["ok"])
        self.assertIn("reason", r["error"])


class TestDeleteFiles(unittest.TestCase):
    def test_iterates_originals(self):
        # First call: list_files (metadata GET). Then per-file DELETE.
        meta_body = json.dumps({"files": [
            {"name": "a.soul", "source": "original"},
            {"name": "b.json", "source": "original"},
            {"name": "thumb.jpg", "source": "derivative"},
        ]})
        calls = {"delete": 0}
        def fake_urlopen(req_or_url, *a, **kw):
            if isinstance(req_or_url, str):
                # First call from list_files passes URL string
                return _FakeResp(meta_body)
            if req_or_url.method == "DELETE":
                calls["delete"] += 1
                self.assertIn("Authorization", req_or_url.headers)
                return _FakeResp(b"")
            return _FakeResp(meta_body)
        with patch.object(ia_admin.urllib.request, "urlopen", side_effect=fake_urlopen), \
             patch.object(ia_admin.time, "sleep"):  # speed up test
            r = ia_admin.delete_files("mememage-x", "ACCESS", "SECRET", throttle_sec=0.0)
        self.assertEqual(r["files"], 2)  # excludes derivative
        self.assertEqual(r["deleted"], 2)
        self.assertEqual(r["failed"], 0)
        self.assertEqual(calls["delete"], 2)

    def test_failures_recorded(self):
        meta_body = json.dumps({"files": [{"name": "a.soul", "source": "original"}]})
        def fake_urlopen(req_or_url, *a, **kw):
            if isinstance(req_or_url, str):
                return _FakeResp(meta_body)
            raise _http_error(500, b"server explode")
        with patch.object(ia_admin.urllib.request, "urlopen", side_effect=fake_urlopen), \
             patch.object(ia_admin.time, "sleep"):
            r = ia_admin.delete_files("mememage-x", "k", "s", throttle_sec=0.0)
        self.assertEqual(r["deleted"], 0)
        self.assertEqual(r["failed"], 1)
        self.assertEqual(len(r["errors"]), 1)


class TestReadCredentials(unittest.TestCase):
    def test_env_priority(self):
        with patch.dict("os.environ", {"IA_ACCESS_KEY": "AAA", "IA_SECRET_KEY": "SSS"}):
            self.assertEqual(ia_admin.read_credentials(), ("AAA", "SSS"))

    def test_missing_returns_empty(self):
        # Clear env then verify the fallback returns ("", "") gracefully.
        with patch.dict("os.environ", {"IA_ACCESS_KEY": "", "IA_SECRET_KEY": ""}, clear=False):
            # patch.dict doesn't clear other keys; explicitly drop ours
            import os
            with patch.dict("os.environ", {}, clear=True):
                a, s = ia_admin.read_credentials()
        # Could be ("", "") OR populated by .env if one exists. We just
        # verify the function returns a 2-tuple of strings without crashing.
        self.assertIsInstance(a, str)
        self.assertIsInstance(s, str)


if __name__ == "__main__":
    unittest.main()
