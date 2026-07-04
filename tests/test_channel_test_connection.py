"""Channel test() — live reachability + auth probe for the dashboard.

The Test Connection button GETs a channel's write face (writing nothing)
and reports {ok, detail} so a user can tell a misconfigured surface
(wrong host, bad token) from a working one BEFORE a mint relies on it.

Capability semantics match the rest of the framework: a channel
advertises ``test`` only when it overrides the base method, so the
dashboard shows the button per channel. http_push implements it;
IA / Zenodo don't (yet).
"""

import unittest
import urllib.error
from unittest.mock import patch, MagicMock


class TestCapabilitiesTest(unittest.TestCase):
    def test_http_push_advertises_test(self):
        from mememage.channels.http_push import HttpPushChannel
        ch = HttpPushChannel({"id": "vps", "type": "http_push",
                              "config": {"base_url": "https://x/api/souls"}})
        self.assertTrue(ch.capabilities()["test"])

    def test_base_channel_does_not_advertise_test(self):
        from mememage.channels import Channel

        class Bare(Channel):
            TYPE = "bare"

        ch = Bare({"id": "x", "type": "bare"})
        self.assertFalse(ch.capabilities()["test"])
        with self.assertRaises(NotImplementedError):
            ch.test()


SOUL_LIST = b'{"items": [], "count": 0}'
PUBLIC_HTML = b"<!DOCTYPE html>\n<html><head><title>Mememage</title></head></html>"


def _http_error(code):
    return urllib.error.HTTPError(
        url="https://example.com", code=code, msg="x", hdrs=None, fp=None)


class TestHttpPushTest(unittest.TestCase):
    """test() probes the receive face WITHOUT the token first (proving auth is
    actually enforced) then WITH it (proving the token is the right one). A bare
    200 on an unauthenticated GET — what a misconfigured apex base_url produces
    — must NOT be reported as 'token accepted'."""

    def _build(self, **config):
        from mememage.channels.http_push import HttpPushChannel
        cfg = {"base_url": "https://example.com/api/souls",
               "accept_self_signed": True}
        cfg.update(config)
        return HttpPushChannel({
            "id": "vps", "type": "http_push", "name": "Peer",
            "enabled": True, "credentials": {}, "config": cfg,
        })

    def _server(self, *, anon, authed=None):
        """Build a urlopen_with_retry side_effect simulating a server.

        ``urlopen_with_retry`` returns the response **body bytes** on 2xx and
        raises HTTPError otherwise — the mock honors that contract. ``anon`` /
        ``authed`` are the response to the no-token / with-token GET: an int
        status (4xx/5xx → HTTPError, 2xx → empty body), a (status, body) tuple,
        or an Exception to raise.
        """
        def fake_open(req, **kw):
            has_auth = req.get_header("Authorization") is not None
            spec = authed if (has_auth and authed is not None) else anon
            if isinstance(spec, Exception):
                raise spec
            status, body = spec if isinstance(spec, tuple) else (spec, b"")
            if status >= 400:
                raise _http_error(status)
            return body.encode() if isinstance(body, str) else body
        return fake_open

    def _run(self, ch, server, bearer="secret-tok"):
        with patch.object(ch, "_resolve_bearer", return_value=bearer):
            with patch("mememage.channels.http_push.urlopen_with_retry",
                       side_effect=server):
                return ch.test()

    # --- the headline case the design exists for -------------------------

    def test_apex_public_page_is_not_ok(self):
        # base_url points at the apex: the unauthenticated GET 200s (public
        # catalog HTML). The token was never checked — must NOT pass.
        ch = self._build(base_url="https://example.com")
        res = self._run(ch, self._server(anon=(200, PUBLIC_HTML)))
        self.assertFalse(res["ok"])
        self.assertIn("/api/souls", res["detail"])

    def test_confirmed_soul_endpoint_is_ok(self):
        # Auth enforced (anon 401), token accepted (authed 200 + soul list).
        ch = self._build()
        res = self._run(ch, self._server(anon=401, authed=(200, SOUL_LIST)))
        self.assertTrue(res["ok"])
        self.assertIn("token was accepted", res["detail"].lower())

    def test_token_rejected_is_not_ok(self):
        # Auth enforced, but our token is wrong (authed also 401/403).
        ch = self._build()
        for code in (401, 403):
            res = self._run(ch, self._server(anon=401, authed=code))
            self.assertFalse(res["ok"], code)
            self.assertIn("rejected", res["detail"].lower())

    def test_auth_enforced_but_no_token_configured(self):
        ch = self._build()
        res = self._run(ch, self._server(anon=401), bearer=None)
        self.assertFalse(res["ok"])
        self.assertIn("requires a token", res["detail"].lower())

    def test_open_soul_endpoint_is_ok_but_flagged(self):
        # An unauthenticated soul endpoint (no token on the receiver) is
        # reachable — OK, but the detail flags that anyone can push.
        ch = self._build()
        res = self._run(ch, self._server(anon=(200, SOUL_LIST)))
        self.assertTrue(res["ok"])
        self.assertIn("isn't requiring a token", res["detail"].lower())

    # --- transport-level outcomes ---------------------------------------

    def test_404_is_not_ok(self):
        ch = self._build()
        res = self._run(ch, self._server(anon=404))
        self.assertFalse(res["ok"])
        self.assertIn("404", res["detail"])

    def test_5xx_is_not_ok(self):
        ch = self._build()
        res = self._run(ch, self._server(anon=500))
        self.assertFalse(res["ok"])

    def test_unreachable_is_not_ok(self):
        ch = self._build()
        res = self._run(ch, self._server(anon=urllib.error.URLError("dns")))
        self.assertFalse(res["ok"])
        self.assertIn("could not reach", res["detail"].lower())

    def test_missing_base_url_is_not_ok(self):
        ch = self._build(base_url="")
        with patch("mememage.channels.http_push.urlopen_with_retry") as net:
            res = ch.test()
        net.assert_not_called()
        self.assertFalse(res["ok"])

    def test_self_push_short_circuits_ok(self):
        ch = self._build()
        with patch.object(ch, "_is_self_push", return_value=True):
            with patch("mememage.channels.http_push.urlopen_with_retry") as net:
                res = ch.test()
        net.assert_not_called()
        self.assertTrue(res["ok"])

    def test_bearer_only_sent_on_authed_probe(self):
        # The token rides only the second (post-401) probe, never the first.
        ch = self._build()
        seen = []

        def fake_open(req, **kw):
            seen.append(req.get_header("Authorization"))
            if req.get_header("Authorization") is None:
                raise _http_error(401)   # anon → enforce auth
            return SOUL_LIST              # authed → accepted (body bytes)

        with patch.object(ch, "_resolve_bearer", return_value="secret-tok"):
            with patch("mememage.channels.http_push.urlopen_with_retry",
                       side_effect=fake_open):
                res = ch.test()
        self.assertTrue(res["ok"])
        self.assertEqual(seen, [None, "Bearer secret-tok"])


if __name__ == "__main__":
    unittest.main()
