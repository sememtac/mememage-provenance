"""Port is config-driven: server.json `port` is the source of truth (the
installer writes it, the dashboard edits it, the server reads it), with an
explicit --port still overriding for ad-hoc runs."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import mememage.server as srv
from mememage.install import _persist_port_to_config


class TestPersistPortToConfig(unittest.TestCase):
    def test_writes_port(self):
        with tempfile.TemporaryDirectory() as home:
            with patch.dict(os.environ, {"HOME": home}):
                _persist_port_to_config(9443)
            data = json.loads((Path(home) / ".mememage" / "server.json").read_text())
            self.assertEqual(data["port"], 9443)

    def test_preserves_existing_keys(self):
        with tempfile.TemporaryDirectory() as home:
            cfg = Path(home) / ".mememage" / "server.json"
            cfg.parent.mkdir(parents=True)
            cfg.write_text(json.dumps({"domain": "x.example", "cert": "/c"}))
            with patch.dict(os.environ, {"HOME": home}):
                _persist_port_to_config(7000)
            data = json.loads(cfg.read_text())
            self.assertEqual(data["port"], 7000)
            self.assertEqual(data["domain"], "x.example")   # untouched
            self.assertEqual(data["cert"], "/c")


class TestServerReadsConfigPort(unittest.TestCase):
    def test_reads_port_from_config(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "server.json"
            cfg.write_text(json.dumps({"port": 9001}))
            with patch.object(srv, "SERVER_CONFIG_FILE", cfg):
                srv._server_config = None
                try:
                    self.assertEqual(srv._get_server_config().get("port"), 9001)
                finally:
                    srv._server_config = None


class TestDoctor(unittest.TestCase):
    """Deployment preflight. Bare-IP and no-domain cases are deterministic
    (no network); the trusted-domain path is exercised live elsewhere."""

    def _run(self, cfg):
        with patch.object(srv, "_get_server_config", lambda: cfg):
            return srv._run_doctor()

    def test_bare_ip_fails(self):
        r = self._run({"domain": "203.0.113.5", "port": 8444})
        self.assertEqual(r["summary"], "fail")
        dom = [c for c in r["checks"] if c["label"] == "Domain"][0]
        self.assertEqual(dom["status"], "fail")
        # a bare IP short-circuits DNS/TLS — nothing to resolve or trust
        labels = [c["label"] for c in r["checks"]]
        self.assertNotIn("DNS", labels)
        self.assertNotIn("TLS trusted", labels)

    def test_no_domain_warns(self):
        r = self._run({})
        dom = [c for c in r["checks"] if c["label"] == "Domain"][0]
        self.assertEqual(dom["status"], "warn")

    def test_token_gap_flagged_on_public_domain(self):
        import mememage.config as cfgmod
        # The doctor calls _load_dotenv() which would re-read the dev .env;
        # neutralize it so the env we set is what the check sees.
        # A non-IP domain triggers the public-host branch; .invalid never
        # resolves (so no real network), which is all we need for this check.
        with patch.object(cfgmod, "_load_dotenv", lambda *a, **k: None), \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MINT_API_TOKEN", None)
            r = self._run({"domain": "mememage-doctor-test.invalid"})
        auth = [c for c in r["checks"] if c["label"] == "Admin auth"][0]
        self.assertEqual(auth["status"], "fail")

    def test_bare_ipv6_fails(self):
        # inet_aton was IPv4-only, so a bare IPv6 domain slipped through as
        # "ok"; ipaddress + IPv6-aware host:port parsing now flags it.
        r = self._run({"domain": "2001:db8::1"})
        dom = [c for c in r["checks"] if c["label"] == "Domain"][0]
        self.assertEqual(dom["status"], "fail")

    def test_bracketed_ipv6_with_port_fails(self):
        r = self._run({"domain": "[2001:db8::1]:8444"})
        dom = [c for c in r["checks"] if c["label"] == "Domain"][0]
        self.assertEqual(dom["status"], "fail")

    def _run_surfaces(self, chans):
        import mememage.channels as chmod
        with patch.object(chmod, "load_channels", lambda: chans):
            return self._run({})

    def test_no_surface_fails(self):
        r = self._run_surfaces([])
        surf = [c for c in r["checks"] if c["label"] == "Surfaces"][0]
        self.assertEqual(surf["status"], "fail")

    def test_enabled_unconfigured_surface_fails(self):
        chans = [_FakeChan("ia", enabled=True, configured=False)]
        r = self._run_surfaces(chans)
        surf = [c for c in r["checks"] if c["label"] == "Surfaces"][0]
        self.assertEqual(surf["status"], "fail")

    def test_live_surface_ok(self):
        chans = [_FakeChan("self", enabled=True, configured=True),
                 _FakeChan("ia", enabled=False, configured=True)]
        r = self._run_surfaces(chans)
        surf = [c for c in r["checks"] if c["label"] == "Surfaces"][0]
        self.assertEqual(surf["status"], "ok")
        self.assertIn("self", surf["detail"])


class _FakeChan:
    def __init__(self, id, enabled, configured):
        self.id = id
        self.enabled = enabled
        self._configured = configured

    def is_configured(self):
        return self._configured


class TestDashboardTokenPath(unittest.TestCase):
    """The clean dashboard URL: GET /<MINT_API_TOKEN> serves the dashboard."""

    class _Fake:
        _dashboard_path_token = srv.MintHandler._dashboard_path_token

    def test_matches_single_segment_token(self):
        with patch.object(srv, "_load_mint_token", lambda: "applebananacherry"):
            h = self._Fake()
            self.assertTrue(h._dashboard_path_token("/applebananacherry"))
            # wrong token, extra segment, or a real route never match
            self.assertFalse(h._dashboard_path_token("/wrongtoken"))
            self.assertFalse(h._dashboard_path_token("/applebananacherry/x"))
            self.assertFalse(h._dashboard_path_token("/dashboard"))
            self.assertFalse(h._dashboard_path_token("/"))

    def test_no_token_configured_never_matches(self):
        with patch.object(srv, "_load_mint_token", lambda: None):
            h = self._Fake()
            self.assertFalse(h._dashboard_path_token("/anything"))


if __name__ == "__main__":
    unittest.main()
