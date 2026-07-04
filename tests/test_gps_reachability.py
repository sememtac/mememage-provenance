"""Reachability-aware GPS sourcing for the desktop app.

The bundled desktop app binds loopback, so a phone can't reach it for GPS
capture. These tests cover the seam that fixes it:

  * ``gps.tailscale_ip`` / ``gps.lan_ip`` — detect this host's reachable
    interface addresses (stdlib source-IP trick), with CGNAT/RFC1918
    classification.
  * The server's capture/effective-source helpers — the PHONE-capture URL
    is resolved independently of the loopback decoder/souls base, and a
    ``phone`` chain falls back to ``machine`` GPS when no phone can reach
    the host.
  * ``_tailscale_https_fqdn`` parsing of ``tailscale status --json``.
  * ``_cert_not_expired`` never green-lights an expired cert.
"""

import os
import unittest
from unittest.mock import patch

from mememage import gps
from mememage import server


class ReachabilityDetectionTests(unittest.TestCase):
    """gps.tailscale_ip / lan_ip classification."""

    def test_cgnat_predicate(self):
        self.assertTrue(gps._in_cgnat("100.64.0.1"))
        self.assertTrue(gps._in_cgnat("100.91.240.84"))
        self.assertTrue(gps._in_cgnat("100.127.255.254"))
        # Just outside 100.64.0.0/10:
        self.assertFalse(gps._in_cgnat("100.63.255.255"))
        self.assertFalse(gps._in_cgnat("100.128.0.0"))
        self.assertFalse(gps._in_cgnat("192.168.0.1"))
        self.assertFalse(gps._in_cgnat("not-an-ip"))

    def test_private_predicate(self):
        self.assertTrue(gps._is_private("192.168.0.23"))
        self.assertTrue(gps._is_private("10.0.0.5"))
        self.assertTrue(gps._is_private("172.16.4.4"))
        self.assertFalse(gps._is_private("8.8.8.8"))
        self.assertFalse(gps._is_private("garbage"))

    def test_tailscale_ip_only_returns_cgnat(self):
        with patch.object(gps, "_source_ip_for", return_value="100.91.240.84"):
            self.assertEqual(gps.tailscale_ip(), "100.91.240.84")
        # Non-CGNAT source (e.g. tailscaled down → default route) → None
        with patch.object(gps, "_source_ip_for", return_value="192.168.0.23"):
            self.assertIsNone(gps.tailscale_ip())
        with patch.object(gps, "_source_ip_for", return_value=None):
            self.assertIsNone(gps.tailscale_ip())

    def test_lan_ip_filters_public_and_cgnat(self):
        with patch.object(gps, "_source_ip_for", return_value="192.168.0.23"):
            self.assertEqual(gps.lan_ip(), "192.168.0.23")
        # Public outbound IP → host is directly internet-facing, not LAN
        with patch.object(gps, "_source_ip_for", return_value="1.1.1.1"):
            self.assertIsNone(gps.lan_ip())
        # Tailscale CGNAT must not be mistaken for a LAN IP
        with patch.object(gps, "_source_ip_for", return_value="100.91.240.84"):
            self.assertIsNone(gps.lan_ip())


class _FakeHandler:
    """Minimal stand-in exposing the reachability helper methods unbound
    from MintHandler — avoids spinning up a real socket/handler."""

    _phone_reachable = server.MintHandler._phone_reachable
    _capture_base = server.MintHandler._capture_base
    _effective_gps_source = server.MintHandler._effective_gps_source

    def __init__(self, host):
        self._host = host

    def _external_host(self):
        return self._host


class _EnvSandbox:
    """Save/restore the env keys these helpers read."""

    KEYS = ("MEMEMAGE_CAPTURE_BASE", "MEMEMAGE_LOCAL", "MEMEMAGE_SCHEME")

    def __enter__(self):
        self._saved = {k: os.environ.get(k) for k in self.KEYS}
        for k in self.KEYS:
            os.environ.pop(k, None)
        return self

    def __exit__(self, *a):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class CaptureDecouplingTests(unittest.TestCase):
    """The phone-capture base is decoupled from the loopback decoder host,
    and the effective GPS source is reachability-aware."""

    def test_loopback_only_desktop_is_unreachable_phone_machine_fallback(self):
        with _EnvSandbox():
            os.environ["MEMEMAGE_LOCAL"] = "1"
            os.environ["MEMEMAGE_SCHEME"] = "http"
            h = _FakeHandler("127.0.0.1:8765")
            self.assertFalse(h._phone_reachable())
            # capture base falls back to the (loopback) external host...
            self.assertEqual(h._capture_base(), "http://127.0.0.1:8765")
            # ...but a phone chain degrades to machine GPS rather than a
            # dead localhost QR.
            self.assertEqual(h._effective_gps_source("phone"), "machine")
            # machine / none are honored unchanged.
            self.assertEqual(h._effective_gps_source("machine"), "machine")
            self.assertEqual(h._effective_gps_source("none"), "none")

    def test_tailscale_capture_base_is_reachable_and_https(self):
        with _EnvSandbox():
            os.environ["MEMEMAGE_LOCAL"] = "1"
            os.environ["MEMEMAGE_SCHEME"] = "http"  # loopback face stays http
            os.environ["MEMEMAGE_CAPTURE_BASE"] = (
                "https://mac-mini.tailnet.ts.net:8765")
            h = _FakeHandler("127.0.0.1:8765")
            self.assertTrue(h._phone_reachable())
            # The capture base is the HTTPS ts.net endpoint, NOT loopback.
            self.assertEqual(
                h._capture_base(),
                "https://mac-mini.tailnet.ts.net:8765")
            # Reachable → phone chain stays phone.
            self.assertEqual(h._effective_gps_source("phone"), "phone")

    def test_vps_public_domain_is_reachable(self):
        with _EnvSandbox():
            # Not local, no capture base — a real domain is reachable.
            os.environ["MEMEMAGE_SCHEME"] = "https"
            h = _FakeHandler("mint.example.com")
            self.assertTrue(h._phone_reachable())
            self.assertEqual(h._capture_base(), "https://mint.example.com")
            self.assertEqual(h._effective_gps_source("phone"), "phone")


class TailscaleFqdnParsingTests(unittest.TestCase):
    """_tailscale_https_fqdn reads Self.DNSName + CertDomains."""

    def _run(self, status_json):
        import subprocess

        class _Done:
            returncode = 0
            stdout = status_json
            stderr = ""

        with patch.object(server, "_tailscale_cmd", return_value="/usr/bin/tailscale"), \
             patch.object(subprocess, "run", return_value=_Done()):
            return server._tailscale_https_fqdn()

    def test_https_enabled_returns_fqdn_without_trailing_dot(self):
        fqdn = self._run(
            '{"Self": {"DNSName": "node.tail1234.ts.net."}, '
            '"CertDomains": ["node.tail1234.ts.net"]}')
        self.assertEqual(fqdn, "node.tail1234.ts.net")

    def test_https_disabled_returns_none(self):
        # No CertDomains → HTTPS not enabled in the tailnet admin console.
        self.assertIsNone(self._run(
            '{"Self": {"DNSName": "node.tail1234.ts.net."}, "CertDomains": []}'))

    def test_no_tailscale_binary_returns_none(self):
        with patch.object(server, "_tailscale_cmd", return_value=None):
            self.assertIsNone(server._tailscale_https_fqdn())


class CertExpiryGuardTests(unittest.TestCase):
    """_cert_not_expired never green-lights a missing/expired cert."""

    def test_missing_file_is_not_valid(self):
        self.assertFalse(server._cert_not_expired("/nonexistent/cert.crt"))

    def test_garbage_is_not_valid(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".crt", delete=False) as f:
            f.write(b"not a certificate")
            path = f.name
        try:
            self.assertFalse(server._cert_not_expired(path))
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
