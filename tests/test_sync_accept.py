"""Tests for the config-sync receiver (POST /api/sync/accept).

The endpoint applies additively — anything the peer already has is
kept untouched, new entries are appended. Private keys, tokens, and
channel credentials NEVER cross the wire; the receiver explicitly
discards any credentials field in incoming channels.
"""

import http.client
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Harness:
    """Boots a MintHandler test server with isolated chains/channels
    paths + bearer. Reused across tests in this module."""

    def __init__(self, chains_root, channels_path, server_config_path, token="testtoken"):
        from mememage import chains as ch_mod
        from mememage import channels as chan_mod
        from mememage import server as srv

        self.token = token
        self.port = _free_port()

        self._patches = [
            patch.object(ch_mod, "MEMEMAGE_ROOT", chains_root.parent),
            patch.object(ch_mod, "CHAINS_ROOT", chains_root),
            patch.object(ch_mod, "CURRENT_CHAIN_FILE", chains_root.parent / "current_chain"),
            patch.object(chan_mod, "CHANNELS_PATH", channels_path),
            patch.object(srv, "SERVER_CONFIG_FILE", server_config_path),
            patch.dict(os.environ, {"MINT_API_TOKEN": token}),
        ]
        for p in self._patches:
            p.start()
        if hasattr(srv, "_cached_mint_token"):
            srv._cached_mint_token = None
        srv._server_config = None

        from http.server import ThreadingHTTPServer

        class _Reusable(ThreadingHTTPServer):
            allow_reuse_address = True
            daemon_threads = True

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


class TestSyncAccept(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mememage-sync-test-"))
        self.chains_root = self.tmp / "chains"
        self.channels_path = self.tmp / "channels.json"
        self.server_config = self.tmp / "server.json"
        self.chains_root.mkdir()
        self.harness = _Harness(self.chains_root, self.channels_path, self.server_config)

    def tearDown(self):
        self.harness.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_chain(self, cid, visibility="light_energy", name=None):
        d = self.chains_root / cid
        d.mkdir(parents=True, exist_ok=True)
        meta = {"id": cid, "visibility": visibility}
        if name:
            meta["name"] = name
        (d / "chain.json").write_text(json.dumps(meta), encoding="utf-8")

    def test_creates_new_chain(self):
        status, body = self.harness.request("POST", "/api/sync/accept", {
            "chains": [{"id": "anumel", "name": "Anumel", "visibility": "light_energy"}],
        })
        self.assertEqual(status, 200, body)
        self.assertIn("anumel", body["summary"]["chains"]["created"])
        # Verify chain.json landed on disk
        self.assertTrue((self.chains_root / "anumel" / "chain.json").exists())

    def test_skips_existing_chain(self):
        self._seed_chain("aries", name="Aries Pre-existing")
        status, body = self.harness.request("POST", "/api/sync/accept", {
            "chains": [{"id": "aries", "name": "Different name — should NOT overwrite"}],
        })
        self.assertEqual(status, 200, body)
        self.assertIn("aries", body["summary"]["chains"]["skipped"])
        self.assertEqual(body["summary"]["chains"]["created"], [])
        # Original chain.json untouched
        meta = json.loads((self.chains_root / "aries" / "chain.json").read_text())
        self.assertEqual(meta["name"], "Aries Pre-existing")

    def test_creates_channel_without_credentials(self):
        # Even if the sender tries to push credentials, the receiver
        # MUST strip them (env-var-only model).
        status, body = self.harness.request("POST", "/api/sync/accept", {
            "channels": [{
                "id": "mirror", "type": "http_push", "name": "Mirror",
                "enabled": True, "primary": False,
                "config": {"base_url": "https://peer.example/api/souls"},
                "credentials": {"bearer_token": "SUPER-SECRET-DO-NOT-PERSIST"},
            }],
        })
        self.assertEqual(status, 200, body)
        self.assertIn("mirror", body["summary"]["channels"]["created"])
        # Inspect the saved channels.json — credentials must be empty.
        saved = json.loads(self.channels_path.read_text(encoding="utf-8"))
        mirror = next(c for c in saved["channels"] if c["id"] == "mirror")
        self.assertEqual(mirror.get("credentials"), {})

    def test_existing_primary_keeps_priority(self):
        # If receiver already has a primary channel, an incoming
        # channel marked primary lands NON-primary (can't have two).
        self.channels_path.write_text(json.dumps({"channels": [
            {"id": "ia", "type": "internet_archive", "name": "IA",
             "enabled": True, "primary": True, "credentials": {}, "config": {}},
        ]}), encoding="utf-8")
        status, body = self.harness.request("POST", "/api/sync/accept", {
            "channels": [{
                "id": "mirror", "type": "http_push", "name": "Mirror",
                "enabled": True, "primary": True,
                "config": {"base_url": "https://peer.example/api/souls"},
            }],
        })
        self.assertEqual(status, 200, body)
        saved = json.loads(self.channels_path.read_text(encoding="utf-8"))
        mirror = next(c for c in saved["channels"] if c["id"] == "mirror")
        self.assertFalse(mirror["primary"])
        ia = next(c for c in saved["channels"] if c["id"] == "ia")
        self.assertTrue(ia["primary"])

    def test_webhook_skip_by_url_match(self):
        # Seed an existing webhook by writing the server config file.
        self.server_config.write_text(json.dumps({"webhooks": [
            {"url": "https://discord.com/api/webhooks/123/abc", "events": ["conceived"]},
        ]}), encoding="utf-8")
        status, body = self.harness.request("POST", "/api/sync/accept", {
            "webhooks": [
                {"url": "https://discord.com/api/webhooks/123/abc"},  # already there
                {"url": "https://hooks.slack.com/services/T/B/xyz"},  # new
            ],
        })
        self.assertEqual(status, 200, body)
        self.assertEqual(body["summary"]["webhooks"]["skipped"], 1)
        self.assertEqual(body["summary"]["webhooks"]["created"], 1)
        # Persisted to disk
        saved = json.loads(self.server_config.read_text(encoding="utf-8"))
        urls = [w["url"] for w in saved["webhooks"]]
        self.assertIn("https://discord.com/api/webhooks/123/abc", urls)
        self.assertIn("https://hooks.slack.com/services/T/B/xyz", urls)

    def test_unauthorized_without_token(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.harness.port, timeout=5)
        conn.request("POST", "/api/sync/accept", body="{}")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 401)


class TestSyncExport(unittest.TestCase):
    """The export endpoint produces a JSON snapshot that re-imports
    cleanly via /api/sync/accept — the round-trip is the contract."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mememage-sync-export-"))
        self.chains_root = self.tmp / "chains"
        self.channels_path = self.tmp / "channels.json"
        self.server_config = self.tmp / "server.json"
        self.chains_root.mkdir()
        # Seed a chain + a channel so the export has something to ship.
        (self.chains_root / "aries").mkdir(parents=True)
        (self.chains_root / "aries" / "chain.json").write_text(
            json.dumps({"id": "aries", "name": "Aries", "visibility": "light_energy"}),
            encoding="utf-8")
        self.channels_path.write_text(json.dumps({"channels": [{
            "id": "self", "type": "http_push", "name": "Self",
            "enabled": True, "primary": True,
            "credentials": {"bearer_token": "should-NEVER-export"},
            "config": {"base_url": "https://localhost:8443/api/souls"},
        }]}), encoding="utf-8")
        self.harness = _Harness(self.chains_root, self.channels_path, self.server_config)

    def tearDown(self):
        self.harness.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_export_envelope_shape(self):
        status, body = self.harness.request("POST", "/api/sync/export", {})
        self.assertEqual(status, 200, body)
        # Envelope keys
        self.assertEqual(body.get("mememage_config_export"), 1)
        self.assertIn("exported_at", body)
        # Categories
        self.assertEqual([c["id"] for c in body["chains"]], ["aries"])
        self.assertEqual([c["id"] for c in body["channels"]], ["self"])

    def test_export_strips_credentials(self):
        # Even if the channel has credentials on disk, the export must
        # never include them. That's the defense-in-depth pattern the
        # accept side already enforces in reverse.
        _, body = self.harness.request("POST", "/api/sync/export", {})
        for c in body.get("channels", []):
            self.assertNotIn("credentials", c,
                f"credentials leaked into export for {c['id']!r}")

    def test_export_webhooks_opt_in(self):
        # Seed a webhook + verify it's only included when explicitly
        # requested (matches the dashboard's opt-in warning flow).
        self.server_config.write_text(json.dumps({"webhooks": [
            {"url": "https://discord.com/api/webhooks/123/secret_token",
             "events": ["conceived"]},
        ]}), encoding="utf-8")
        # Default — no webhooks key
        _, body = self.harness.request("POST", "/api/sync/export", {})
        self.assertNotIn("webhooks", body)
        # Opt in
        _, body = self.harness.request(
            "POST", "/api/sync/export",
            {"include": {"webhooks": True}},
        )
        self.assertEqual(len(body["webhooks"]), 1)
        self.assertEqual(
            body["webhooks"][0]["url"],
            "https://discord.com/api/webhooks/123/secret_token",
        )

    def test_export_then_accept_round_trips(self):
        # The whole point: a fresh host should be able to ingest the
        # export verbatim via /api/sync/accept.
        _, export = self.harness.request("POST", "/api/sync/export", {})
        # Set up a SECOND clean harness with empty state and POST the
        # export payload to its /api/sync/accept.
        tmp2 = Path(tempfile.mkdtemp(prefix="mememage-sync-roundtrip-"))
        (tmp2 / "chains").mkdir()
        try:
            harness2 = _Harness(tmp2 / "chains", tmp2 / "channels.json",
                                tmp2 / "server.json", token="testtoken2")
            try:
                status, body = harness2.request("POST", "/api/sync/accept", {
                    "chains": export["chains"],
                    "channels": export["channels"],
                })
                self.assertEqual(status, 200, body)
                self.assertIn("aries", body["summary"]["chains"]["created"])
                self.assertIn("self", body["summary"]["channels"]["created"])
            finally:
                harness2.stop()
        finally:
            import shutil
            shutil.rmtree(tmp2, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
