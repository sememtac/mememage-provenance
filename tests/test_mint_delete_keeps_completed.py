"""DELETE /api/mint/<token> must KEEP completed conceptions.

The "Conceive another" / reset flow fires this DELETE. A completed
conception's staged image + session are what the public catalog renders,
so dropping them here withdraws a just-conceived image from the feed —
the bug where minting two images only showed the newest, because
conceiving the 2nd deleted the 1st's session+image.

Contract:
  * completed session  → kept (image + session survive); ?purge=1 overrides
  * pending / failed    → deleted + staged image unlinked (cleanup of drafts)
  * minting             → refused (409)
"""

import http.client
import json
import os
import socket
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestMintDeleteKeepsCompleted(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from mememage import server as srv
        cls.srv = srv
        cls.token_admin = "testtoken123"
        cls.port = _free_port()
        cls._patches = [
            patch.dict(os.environ, {"MINT_API_TOKEN": cls.token_admin}),
            # Don't touch the real ~/.mememage/sessions.json.
            patch.object(srv, "_save_sessions", lambda: None),
        ]
        for p in cls._patches:
            p.start()
        if hasattr(srv, "_cached_mint_token"):
            srv._cached_mint_token = None

        class _Reusable(ThreadingHTTPServer):
            allow_reuse_address = True
            daemon_threads = True

        srv.MintHandler.log_message = lambda *a, **kw: None
        cls.server = _Reusable(("127.0.0.1", cls.port), srv.MintHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.tmpdir = tempfile.mkdtemp(prefix="mememage-mintdel-")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for p in cls._patches:
            p.stop()
        import shutil
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def setUp(self):
        self.srv._sessions.clear()

    def _stage_image(self, name):
        p = Path(self.tmpdir) / name
        p.write_bytes(b"\x89PNG\r\n\x1a\n fake")
        return str(p)

    def _put_session(self, token, status, image_path, identifier="mememage-deadbeef0000"):
        self.srv._sessions[token] = {
            "status": status,
            "image_path": image_path,
            "result": {"identifier": identifier, "image_path": image_path},
            "created": 1,
        }

    def _delete(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("DELETE", path, headers={"Authorization": f"Bearer {self.token_admin}"})
        resp = conn.getresponse()
        data = resp.read()
        try:
            return resp.status, json.loads(data) if data else {}
        except json.JSONDecodeError:
            return resp.status, {"_raw": data}

    def test_completed_is_kept(self):
        img = self._stage_image("done.png")
        self._put_session("tokCompleted", "completed", img)
        status, body = self._delete("/api/mint/tokCompleted")
        self.assertEqual(status, 200)
        self.assertTrue(body.get("kept"))
        # Session AND staged image survive — the catalog still has them.
        self.assertIn("tokCompleted", self.srv._sessions)
        self.assertTrue(os.path.exists(img))

    def test_completed_purge_override_removes(self):
        img = self._stage_image("purge.png")
        self._put_session("tokPurge", "completed", img)
        status, body = self._delete("/api/mint/tokPurge?purge=1")
        self.assertEqual(status, 200)
        self.assertNotIn("tokPurge", self.srv._sessions)
        self.assertFalse(os.path.exists(img))

    def test_pending_is_deleted(self):
        img = self._stage_image("draft.png")
        self._put_session("tokPending", "pending", img)
        status, body = self._delete("/api/mint/tokPending")
        self.assertEqual(status, 200)
        self.assertFalse(body.get("kept"))
        self.assertNotIn("tokPending", self.srv._sessions)
        self.assertFalse(os.path.exists(img))

    def test_failed_is_deleted(self):
        img = self._stage_image("failed.png")
        self._put_session("tokFailed", "failed", img)
        status, body = self._delete("/api/mint/tokFailed")
        self.assertEqual(status, 200)
        self.assertNotIn("tokFailed", self.srv._sessions)
        self.assertFalse(os.path.exists(img))

    def test_minting_is_refused(self):
        img = self._stage_image("inflight.png")
        self._put_session("tokMinting", "minting", img)
        status, body = self._delete("/api/mint/tokMinting")
        self.assertEqual(status, 409)
        # Untouched — still minting.
        self.assertIn("tokMinting", self.srv._sessions)
        self.assertTrue(os.path.exists(img))


if __name__ == "__main__":
    unittest.main()
