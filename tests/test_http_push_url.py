"""http_push: PUT to the write face (base_url), but the soul's canonical
link + the conception-page display come from the clean READ face —
explicit public_url, else the box's souls_domain (self-push), else the box's
domain when the write target is a bare IP (self-push), else base_url.
"""

import unittest
from unittest.mock import patch

from mememage.channels.http_push import HttpPushChannel


def _chan(**config):
    cfg = {"id": "self", "type": "http_push", "name": "This server (self-push)",
           "enabled": True, "primary": True, "config": config}
    return HttpPushChannel(cfg)


def _server(souls_domain="", domain=""):
    """Patch the single server.json field reader so tests don't depend on the
    dev machine's real ~/.mememage/server.json."""
    fields = {"souls_domain": souls_domain, "domain": domain}
    return patch.object(HttpPushChannel, "_server_field",
                        side_effect=lambda k: fields.get(k, ""))


class TestReadBasePriority(unittest.TestCase):
    def test_explicit_public_url_wins(self):
        ch = _chan(base_url="http://10.0.0.5:8443/api/souls",
                   public_url="https://souls.example.com")
        self.assertEqual(ch._read_base(), "https://souls.example.com")

    def test_souls_domain_used_for_self_push(self):
        ch = _chan(base_url="http://10.0.0.5:8443/api/souls")
        with patch.object(ch, "_is_self_push", return_value=True), \
             _server(souls_domain="souls.example.com"):
            self.assertEqual(ch._read_base(), "https://souls.example.com")

    def test_souls_domain_ignored_for_non_self_peer(self):
        ch = _chan(base_url="http://10.0.0.5:8443/api/souls")
        with patch.object(ch, "_is_self_push", return_value=False), \
             _server(souls_domain="souls.example.com", domain="mint.example.com"):
            self.assertEqual(ch._read_base(), "http://10.0.0.5:8443/api/souls")

    def test_domain_fallback_for_ip_self_push(self):
        # No dedicated souls face, but a domain is configured and the write
        # target is a bare IP -> advertise the domain (it proxies the same
        # /api/souls path) instead of the raw IP:port.
        ch = _chan(base_url="https://10.0.0.5:8443/api/souls")
        with patch.object(ch, "_is_self_push", return_value=True), \
             _server(domain="mint.example.com"):
            self.assertEqual(ch._read_base(), "https://mint.example.com/api/souls")

    def test_domain_not_applied_when_base_already_a_domain(self):
        # If the write target is already a hostname (not an IP), leave it —
        # don't rewrite the host out from under the user.
        ch = _chan(base_url="https://my.host.example/api/souls")
        with patch.object(ch, "_is_self_push", return_value=True), \
             _server(domain="mint.example.com"):
            self.assertEqual(ch._read_base(), "https://my.host.example/api/souls")

    def test_souls_domain_beats_domain(self):
        ch = _chan(base_url="https://10.0.0.5:8443/api/souls")
        with patch.object(ch, "_is_self_push", return_value=True), \
             _server(souls_domain="souls.example.com", domain="mint.example.com"):
            self.assertEqual(ch._read_base(), "https://souls.example.com")

    def test_base_url_fallback(self):
        ch = _chan(base_url="http://10.0.0.5:8443/api/souls")
        with patch.object(ch, "_is_self_push", return_value=True), _server():
            self.assertEqual(ch._read_base(), "http://10.0.0.5:8443/api/souls")


class TestUploadSplitsWriteAndRead(unittest.TestCase):
    def test_put_to_base_url_but_return_public_url(self):
        ch = _chan(base_url="http://10.0.0.5:8443/api/souls",
                   public_url="https://souls.example.com")
        captured = {}

        def fake_urlopen(req, context=None):
            captured["put_url"] = req.full_url
            return None

        with patch("mememage.channels.http_push.urlopen_with_retry", fake_urlopen):
            returned = ch.upload("mememage-abc123", b"{}")

        # PUT went to the raw write face...
        self.assertEqual(captured["put_url"],
                         "http://10.0.0.5:8443/api/souls/mememage-abc123.soul")
        # ...but the canonical link is the clean read face.
        self.assertEqual(returned, "https://souls.example.com/mememage-abc123.soul")


class TestDisplaySurface(unittest.TestCase):
    def test_domain_shows_verbatim(self):
        ch = _chan(base_url="http://10.0.0.5:8443/api/souls",
                   public_url="https://souls.example.com")
        self.assertEqual(ch.display_surface(), "souls.example.com")

    def test_localhost_renders_as_localhost(self):
        ch = _chan(base_url="https://localhost:8443/api/souls")
        with patch.object(ch, "_is_self_push", return_value=True), _server():
            self.assertEqual(ch.display_surface(), "localhost")

    def test_bare_ip_falls_back_to_name(self):
        # We don't surface raw IPs — show the friendly name instead.
        ch = _chan(base_url="http://144.202.90.138:8443/api/souls")
        with patch.object(ch, "_is_self_push", return_value=True), _server():
            self.assertEqual(ch.display_surface(), "This server (self-push)")


if __name__ == "__main__":
    unittest.main()
