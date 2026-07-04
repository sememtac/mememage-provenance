"""Tests for /api/onboarding/status — first-run checklist.

The endpoint reads state from chains, channels, profiles, and the
records dirs. Each step is independent — make sure the dispatcher
reports them correctly across a freshly-empty install, a half-set-up
install, and a fully-configured one.
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
    def __init__(self, root, token="testtoken"):
        from mememage import chains as ch_mod
        from mememage import channels as chan_mod
        from mememage import profiles as prof_mod
        from mememage import server as srv

        self.root = root
        self.token = token
        self.port = _free_port()

        # Isolate from the dev machine's IA creds — load_channels()
        # auto-creates a default with IA enabled, and is_configured()
        # would return True if IA_ACCESS_KEY/IA_SECRET_KEY happen to
        # be set in the runner's env. Empty values keep the default
        # IA channel present-but-unconfigured for a clean baseline.
        env_isolation = {
            "MINT_API_TOKEN": token,
            "IA_ACCESS_KEY": "",
            "IA_SECRET_KEY": "",
        }
        self._patches = [
            patch.object(ch_mod, "MEMEMAGE_ROOT", root),
            patch.object(ch_mod, "CHAINS_ROOT", root / "chains"),
            patch.object(ch_mod, "CURRENT_CHAIN_FILE", root / "current_chain"),
            patch.object(chan_mod, "CHANNELS_PATH", root / "channels.json"),
            patch.object(prof_mod, "ROOT", root),
            patch.object(prof_mod, "PROFILES_DIR", root / "profiles"),
            patch.object(prof_mod, "ACTIVE_FILE", root / "active_profile"),
            patch.dict(os.environ, env_isolation),
        ]
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

    def request(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/api/onboarding/status",
                     headers={"Authorization": f"Bearer {self.token}"})
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
        return resp.status, data

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        for p in self._patches:
            p.stop()


class TestOnboardingStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mememage-onboarding-"))
        (self.tmp / "chains").mkdir()
        self.harness = _Harness(self.tmp)

    def tearDown(self):
        self.harness.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _step_by_id(self, body, sid):
        return next(s for s in body["steps"] if s["id"] == sid)

    def test_fresh_install_everything_pending(self):
        # No profiles, no chains, no channels — every step pending.
        status, body = self.harness.request()
        self.assertEqual(status, 200)
        self.assertFalse(body["complete"])
        for s in body["steps"]:
            self.assertFalse(s["done"], f"{s['id']} unexpectedly done")

    def test_chain_step_done_when_chain_exists(self):
        (self.tmp / "chains" / "aries").mkdir(parents=True)
        (self.tmp / "chains" / "aries" / "chain.json").write_text(
            json.dumps({"id": "aries", "visibility": "light_energy"}), encoding="utf-8")
        status, body = self.harness.request()
        self.assertEqual(status, 200)
        chain = self._step_by_id(body, "chain")
        self.assertTrue(chain["done"])
        self.assertIn("aries", chain["detail"])

    def test_first_conception_done_when_soul_in_store(self):
        # Souls live in the shared flat store now (<root>/received), not a
        # per-chain records/ dir — any soul there means the user has conceived.
        store = self.tmp / "received"
        store.mkdir(parents=True)
        (store / "mememage-abc.soul").write_text("{}", encoding="utf-8")
        status, body = self.harness.request()
        first = self._step_by_id(body, "first_conception")
        self.assertTrue(first["done"])

    def test_distribution_done_when_channel_configured(self):
        # Manually seed channels.json with a self-contained http_push
        # channel — base_url is the only required field for that type's
        # is_configured() check.
        (self.tmp / "channels.json").write_text(json.dumps({"channels": [{
            "id": "mirror", "type": "http_push", "name": "Mirror",
            "enabled": True, "primary": True,
            "credentials": {},
            "config": {"base_url": "https://peer.example/api/souls"},
        }]}), encoding="utf-8")
        status, body = self.harness.request()
        dist = self._step_by_id(body, "distribution")
        self.assertTrue(dist["done"])
        self.assertIn("mirror", dist["detail"])

    def test_returns_step_metadata_for_jump_targets(self):
        status, body = self.harness.request()
        # The dashboard uses tab + anchor to jump on click — make sure
        # every step carries them.
        for s in body["steps"]:
            self.assertIn("tab", s, f"{s['id']} missing tab")
            self.assertIn(s["tab"], ("tab-mint", "tab-config"),
                          f"{s['id']} has unknown tab {s['tab']}")

    def test_unauthorized(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.harness.port, timeout=5)
        conn.request("GET", "/api/onboarding/status")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 401)


if __name__ == "__main__":
    unittest.main()
