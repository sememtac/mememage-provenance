"""Single-instance guard for the desktop app.

Re-launching the double-click app (or launching again after closing the
browser) should focus the already-running server, not start a second one
on a new free port. Liveness is decided by an actual /health probe, so a
lock left by a hard-killed process (closed console window, force-quit)
self-heals instead of blocking future launches.
"""

import json
import socket
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestDesktopLock(unittest.TestCase):
    def setUp(self):
        from mememage import server
        self.server = server
        self.lock = Path(tempfile.mkdtemp()) / "desktop.lock"
        self._p = patch.object(server, "_DESKTOP_LOCK", self.lock)
        self._p.start()

    def tearDown(self):
        self._p.stop()

    def test_no_lock_returns_none(self):
        self.assertIsNone(self.server._desktop_already_running())

    def test_write_then_clear(self):
        self.server._write_desktop_lock(8765, "http")
        self.assertTrue(self.lock.exists())
        info = json.loads(self.lock.read_text())
        self.assertEqual(info["port"], 8765)
        self.server._clear_desktop_lock()
        self.assertFalse(self.lock.exists())

    def test_stale_lock_self_heals(self):
        # Lock points at a port with nothing listening → treated as stale,
        # returns None AND removes the lock so launch proceeds.
        self.lock.write_text(json.dumps({"port": _free_port(), "scheme": "http"}))
        self.assertIsNone(self.server._desktop_already_running())
        self.assertFalse(self.lock.exists())

    def test_alive_instance_returns_dashboard_url(self):
        # A real server answering /health → the guard returns its dashboard
        # URL (so the next launch opens it instead of starting a second).
        port = _free_port()

        class _Reusable(ThreadingHTTPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server.MintHandler.log_message = lambda *a, **k: None
        srv = _Reusable(("127.0.0.1", port), self.server.MintHandler)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            # Wait until it's actually answering before probing (in real use
            # the other instance has been up for a while; here it's brand new).
            import time
            import urllib.request
            for _ in range(60):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
                    break
                except Exception:
                    time.sleep(0.05)
            self.lock.write_text(json.dumps({"port": port, "scheme": "http"}))
            url = self.server._desktop_already_running()
            self.assertIsNotNone(url)
            self.assertIn(f"127.0.0.1:{port}", url)
            self.assertTrue(self.lock.exists())  # alive → lock kept
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
