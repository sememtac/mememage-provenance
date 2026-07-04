"""Tests for /api/channels* HTTP endpoints.

The channels framework itself is covered by tests/test_channels.py;
this file pins the dashboard's HTTP wrappers: list, types schema,
save (validation + persistence). Boots a MintHandler in a thread
with isolated config paths so the tests don't touch the user's
real ~/.mememage state.
"""

import http.client
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _ServerHarness:
    """Boots a MintHandler test server with isolated channels.json +
    bearer token. Used as setUpClass / tearDownClass helper."""

    def __init__(self, channels_path, token="testtoken123"):
        from mememage import channels as ch_mod
        from mememage import server as srv

        self.token = token
        self.port = _free_port()

        # Isolate channels.json so saves don't write into the user's home.
        self._patches = [
            patch.object(ch_mod, "CHANNELS_PATH", channels_path),
            patch.dict(os.environ, {"MINT_API_TOKEN": token}),
        ]
        for p in self._patches:
            p.start()

        # MintHandler reads the token lazily via _load_mint_token; bust
        # any cached value so the patch above takes effect.
        if hasattr(srv, "_cached_mint_token"):
            srv._cached_mint_token = None

        from http.server import ThreadingHTTPServer

        class _Reusable(ThreadingHTTPServer):
            allow_reuse_address = True
            daemon_threads = True

        # The handler logs every request to stderr — silence it during
        # tests by patching log_message on the class.
        srv.MintHandler.log_message = lambda *a, **kw: None
        self.server = _Reusable(("127.0.0.1", self.port), srv.MintHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Authorization": f"Bearer {self.token}"}
        body_bytes = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            body_bytes = json.dumps(body).encode("utf-8")
        conn.request(method, path, body=body_bytes, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        try:
            parsed = json.loads(data.decode("utf-8")) if data else {}
        except json.JSONDecodeError:
            parsed = {"_raw": data}
        return resp.status, parsed

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        for p in self._patches:
            p.stop()


class TestChannelsAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="mememage-channels-api-")
        cls.channels_path = Path(cls.tmpdir) / "channels.json"
        cls.harness = _ServerHarness(cls.channels_path)

    @classmethod
    def tearDownClass(cls):
        cls.harness.stop()
        # Clean up tmp
        import shutil
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_list_returns_default_channel_set(self):
        # GET /api/channels — auto-creates a default file (IA + Zenodo)
        # on first read; verify the wrapper returns it as JSON.
        status, body = self.harness.request("GET", "/api/channels")
        self.assertEqual(status, 200)
        self.assertIn("channels", body)
        self.assertIsInstance(body["channels"], list)
        ids = {c.get("id") for c in body["channels"]}
        # Default config seeds at least the internet_archive channel.
        self.assertTrue(any(c.get("type") == "internet_archive" for c in body["channels"]))

    def test_types_lists_registered_channel_schemas(self):
        # GET /api/channels/types — schema for every registered channel.
        status, body = self.harness.request("GET", "/api/channels/types")
        self.assertEqual(status, 200)
        self.assertIn("types", body)
        types = body["types"]
        type_names = {t.get("type") for t in types}
        # All three production channels register at import time.
        self.assertIn("internet_archive", type_names)
        self.assertIn("zenodo", type_names)
        self.assertIn("http_push", type_names)
        # Each entry has at least the schema fields the dashboard
        # consumes — display name, credential fields, config fields.
        for t in types:
            self.assertIn("display_name", t)
            self.assertIn("credential_fields", t)
            self.assertIn("config_fields", t)

    def test_save_persists_and_validates(self):
        # POST /api/channels — wholesale replace; validates dup id + dup primary.
        payload = {
            "channels": [
                {"id": "ia", "type": "internet_archive", "name": "Internet Archive",
                 "enabled": True, "primary": True, "credentials": {}, "config": {}},
                {"id": "mirror", "type": "http_push", "name": "Mirror",
                 "enabled": False, "primary": False, "credentials": {},
                 "config": {"base_url": "https://peer.example/api/souls"}},
            ]
        }
        status, body = self.harness.request("POST", "/api/channels", payload)
        self.assertEqual(status, 200, body)
        self.assertEqual(body.get("count"), 2)

        # Round-trip via GET — same shape comes back.
        status, body = self.harness.request("GET", "/api/channels")
        self.assertEqual(status, 200)
        ids = [c.get("id") for c in body["channels"]]
        self.assertEqual(ids, ["ia", "mirror"])

    def test_save_rejects_dup_id(self):
        payload = {
            "channels": [
                {"id": "dup", "type": "internet_archive", "name": "A",
                 "enabled": True, "primary": False, "credentials": {}, "config": {}},
                {"id": "dup", "type": "http_push", "name": "B",
                 "enabled": True, "primary": False, "credentials": {},
                 "config": {"base_url": "https://x/api/souls"}},
            ]
        }
        status, body = self.harness.request("POST", "/api/channels", payload)
        self.assertEqual(status, 400)
        self.assertIn("Duplicate", body.get("error", ""))

    def test_save_rejects_dup_primary(self):
        payload = {
            "channels": [
                {"id": "a", "type": "internet_archive", "name": "A",
                 "enabled": True, "primary": True, "credentials": {}, "config": {}},
                {"id": "b", "type": "http_push", "name": "B",
                 "enabled": True, "primary": True, "credentials": {},
                 "config": {"base_url": "https://x/api/souls"}},
            ]
        }
        status, body = self.harness.request("POST", "/api/channels", payload)
        self.assertEqual(status, 400)
        self.assertIn("primary", body.get("error", "").lower())

    def test_save_requires_id(self):
        payload = {"channels": [{"type": "internet_archive"}]}
        status, body = self.harness.request("POST", "/api/channels", payload)
        self.assertEqual(status, 400)
        self.assertIn("id", body.get("error", ""))

    def test_unauthorized_without_bearer(self):
        # Direct request without the Authorization header → 401.
        conn = http.client.HTTPConnection("127.0.0.1", self.harness.port, timeout=5)
        conn.request("GET", "/api/channels")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 401)


if __name__ == "__main__":
    unittest.main()
