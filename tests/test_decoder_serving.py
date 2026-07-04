"""Self-hosted decoder/validator serving + the public/admin face split.

The mint server serves the public decoder (index.html) and validator
(validator.html) from docs/, injecting an absolute souls read base so a
self-served copy defaults its record Source to THIS deployment's surface
instead of the Internet Archive.

Face split:
  * souls.<domain> (Host == souls_domain) is the PUBLIC decode face —
    decoder / validator / raw souls only, everything admin 404s, and
    the root "/" is the decoder.
  * the admin mint host (and single-domain installs) route normally —
    "/" → dashboard login; /decoder redirects to the souls face when a
    souls_domain is configured, else serves the decoder as a fallback.

These are public routes — no MINT_API_TOKEN required, and the admin
token must never be injected into them.
"""

import http.client
import os
import socket
import threading
import unittest
from unittest.mock import patch


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _RawHarness:
    """Boots MintHandler and returns (status, location, body) raw."""

    def __init__(self, token="testtoken123"):
        from mememage import server as srv
        self.token = token
        self.port = _free_port()
        self._patches = [patch.dict(os.environ, {"MINT_API_TOKEN": token})]
        for p in self._patches:
            p.start()
        if hasattr(srv, "_cached_mint_token"):
            srv._cached_mint_token = None
        from http.server import ThreadingHTTPServer

        class _Reusable(ThreadingHTTPServer):
            allow_reuse_address = True
            daemon_threads = True

        srv.MintHandler.log_message = lambda *a, **kw: None
        self.server = _Reusable(("127.0.0.1", self.port), srv.MintHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def get(self, path, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": host} if host else {}
        conn.request("GET", path, headers=headers)  # no auth — public route
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, resp.getheader("Location"), body

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        for p in self._patches:
            p.stop()


class _ConfigBase(unittest.TestCase):
    """Pins _get_server_config so souls_domain routing is deterministic
    regardless of the dev machine's ~/.mememage/server.json."""

    SERVER_CONFIG = {}

    @classmethod
    def setUpClass(cls):
        cls.h = _RawHarness()
        from mememage import server as srv
        cls._cfg_patch = patch.object(srv, "_get_server_config",
                                      return_value=cls.SERVER_CONFIG)
        cls._cfg_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls._cfg_patch.stop()
        cls.h.stop()


class TestSingleDomain(_ConfigBase):
    """No souls_domain → /decoder is served directly on the only host."""
    SERVER_CONFIG = {}

    def test_decoder_served_without_auth(self):
        status, _, body = self.h.get("/decoder")
        self.assertEqual(status, 200)
        self.assertIn("MEMEMAGE", body)

    def test_souls_base_injected_and_marker_gone(self):
        status, _, body = self.h.get("/decoder")
        self.assertEqual(status, 200)
        self.assertNotIn("<!--MEMEMAGE_SOULS_BASE-->", body)
        self.assertIn("window.MEMEMAGE_SOULS_BASE=", body)
        self.assertIn("/api/souls/", body)  # falls back to <host>/api/souls/

    def test_admin_token_not_injected(self):
        status, _, body = self.h.get("/decoder")
        self.assertNotIn(self.h.token, body)

    def test_validator_and_filename_aliases(self):
        for path in ("/validator", "/index.html", "/validator.html"):
            status, _, body = self.h.get(path)
            self.assertEqual(status, 200, path)
            self.assertIn("window.MEMEMAGE_SOULS_BASE=", body)
            self.assertNotIn("<!--MEMEMAGE_SOULS_BASE-->", body)

    def test_dashboard_links_to_local_decoder(self):
        # No souls_domain → dashboard Decoder/Validator links point at the
        # inline-served /decoder + /validator on this same host.
        status, _, body = self.h.get("/dashboard?token=" + self.h.token)
        self.assertEqual(status, 200)
        self.assertIn('href="/decoder"', body)
        self.assertIn('href="/validator"', body)
        self.assertNotIn('href="index.html"', body)  # the broken relative link is gone


class TestMintFaceHidesDecoder(_ConfigBase):
    """souls_domain set + request on the admin host → the decoder is not
    a path here at all (404). The decode face lives only on the souls
    host; the admin host stays admin-only."""
    SERVER_CONFIG = {"souls_domain": "souls.example.com"}

    # A real public admin host (NOT loopback — loopback is the desktop case,
    # which serves the decoder inline; see TestLocalServesDecoder).
    ADMIN_HOST = "mint.example.com"

    def test_decoder_404s_on_admin_host(self):
        status, _, _ = self.h.get("/decoder", host=self.ADMIN_HOST)
        self.assertEqual(status, 404)

    def test_validator_404s_on_admin_host(self):
        status, _, _ = self.h.get("/validator", host=self.ADMIN_HOST)
        self.assertEqual(status, 404)

    def test_root_serves_public_catalog(self):
        # Admin host root is now the PUBLIC catalog (the wall of conceived-image
        # tiles) — served inline, not a redirect. The dashboard login moved to
        # /dashboard. The catalog page carries the feed grid container.
        status, _, body = self.h.get("/")
        self.assertEqual(status, 200)
        self.assertIn('id="feedGrid"', body)

    def test_dashboard_links_to_souls_face(self):
        # souls_domain set + PUBLIC admin host → dashboard Decoder/Validator
        # links cross to the public souls host, not the (now 404) /index.html.
        status, _, body = self.h.get("/dashboard?token=" + self.h.token,
                                     host=self.ADMIN_HOST)
        self.assertEqual(status, 200)
        self.assertIn('href="https://souls.example.com/"', body)
        self.assertIn('href="https://souls.example.com/validator"', body)


class TestLocalServesDecoder(_ConfigBase):
    """A souls_domain is configured, but a LOCAL/loopback request (desktop,
    LAN, Tailscale) has no separate public souls host to reach — so the decoder
    and validator are served INLINE there, not 404'd. Without this the desktop
    tray's Open Decoder / Open Validator (which hit 127.0.0.1) dead-end."""
    SERVER_CONFIG = {"souls_domain": "souls.example.com"}

    def test_decoder_served_on_loopback(self):
        status, _, body = self.h.get("/decoder", host="127.0.0.1:%d" % self.h.port)
        self.assertEqual(status, 200)
        self.assertIn("MEMEMAGE", body)

    def test_validator_served_on_loopback(self):
        status, _, body = self.h.get("/validator", host="127.0.0.1:%d" % self.h.port)
        self.assertEqual(status, 200)

    def test_decoder_served_on_localhost_name(self):
        status, _, _ = self.h.get("/decoder", host="localhost")
        self.assertEqual(status, 200)

    def test_decoder_served_on_tailscale(self):
        status, _, _ = self.h.get("/decoder", host="mac-mini.tailnet.ts.net")
        self.assertEqual(status, 200)

    def test_decoder_still_404s_on_public_admin_host(self):
        # The deferral still holds for a real public admin host.
        status, _, _ = self.h.get("/decoder", host="mint.example.com")
        self.assertEqual(status, 404)

    def test_dashboard_links_local_on_loopback(self):
        # On a local request the dashboard's Decoder/Validator links point at
        # the LOCAL decoder (/decoder), not the remote souls host — the user is
        # on their own box; bouncing to souls.<domain> would leave localhost.
        status, _, body = self.h.get(
            "/dashboard?token=" + self.h.token, host="127.0.0.1:%d" % self.h.port)
        self.assertEqual(status, 200)
        self.assertIn('href="/decoder"', body)
        self.assertIn('href="/validator"', body)
        self.assertNotIn("souls.example.com", body)


class TestSoulsFace(_ConfigBase):
    """Request on the souls host (Host == souls_domain) → public decode
    face: root is the decoder, admin is hidden."""
    SERVER_CONFIG = {"souls_domain": "souls.example.com"}
    HOST = "souls.example.com"

    def test_root_serves_decoder(self):
        status, _, body = self.h.get("/", host=self.HOST)
        self.assertEqual(status, 200)
        self.assertIn("window.MEMEMAGE_SOULS_BASE=", body)
        self.assertIn("https://souls.example.com/", body)

    def test_validator_path(self):
        status, _, body = self.h.get("/validator", host=self.HOST)
        self.assertEqual(status, 200)
        self.assertIn("window.MEMEMAGE_SOULS_BASE=", body)

    def test_admin_hidden(self):
        # The dashboard / config must not be reachable on the public face.
        for path in ("/dashboard", "/api/config", "/api/profiles"):
            status, _, _ = self.h.get(path, host=self.HOST)
            self.assertEqual(status, 404, path)

    def test_unknown_path_404(self):
        status, _, _ = self.h.get("/mint/new", host=self.HOST)
        self.assertEqual(status, 404)


class TestLocalDesktopMode(_ConfigBase):
    """Desktop/local mode (MEMEMAGE_LOCAL=1): self-URLs advertise the
    loopback host over http, ignoring any stale configured public domain.
    On localhost, http is a secure context, so GPS still works."""
    SERVER_CONFIG = {"domain": "stale.example.com"}  # must be IGNORED in local mode

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._env = patch.dict(os.environ,
                              {"MEMEMAGE_LOCAL": "1", "MEMEMAGE_SCHEME": "http"})
        cls._env.start()

    @classmethod
    def tearDownClass(cls):
        cls._env.stop()
        super().tearDownClass()

    def test_souls_base_is_loopback_http(self):
        host = "127.0.0.1:%d" % self.h.port
        status, _, body = self.h.get("/decoder", host=host)
        self.assertEqual(status, 200)
        # http + the loopback host the browser connected to, NOT the
        # stale configured domain.
        self.assertIn('window.MEMEMAGE_SOULS_BASE="http://%s/api/souls/"' % host, body)
        self.assertNotIn("stale.example.com", body)


class TestServeHelpers(unittest.TestCase):
    def test_find_free_port_bindable(self):
        import socket
        from mememage.server import _find_free_port
        p = _find_free_port("127.0.0.1", 8765)
        self.assertTrue(8765 <= p < 8765 + 50)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", p))  # the returned port is actually free
        finally:
            s.close()

    def test_external_scheme_env_driven(self):
        from mememage.server import _external_scheme
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MEMEMAGE_SCHEME", None)
            self.assertEqual(_external_scheme(), "https")  # safe default
            os.environ["MEMEMAGE_SCHEME"] = "http"
            self.assertEqual(_external_scheme(), "http")


if __name__ == "__main__":
    unittest.main()
