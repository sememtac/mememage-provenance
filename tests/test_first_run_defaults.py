"""Tests for the first-run seeder.

`_seed_first_run_defaults` runs at server boot and seeds minimum
config (default profile, default chain, self-push channel) when
each is missing. Already-configured installs are no-ops. Failures
in one section never block the others or block startup.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _isolated_root():
    return Path(tempfile.mkdtemp(prefix="mememage-first-run-"))


class TestSeedDefaults(unittest.TestCase):
    def setUp(self):
        self.root = _isolated_root()
        from mememage import chains, profiles
        from mememage import channels as ch_mod
        self.chains = chains
        self.profiles = profiles
        self.ch_mod = ch_mod
        self._patches = [
            patch.object(chains, "MEMEMAGE_ROOT", self.root),
            patch.object(chains, "CHAINS_ROOT", self.root / "chains"),
            patch.object(chains, "CURRENT_CHAIN_FILE", self.root / "current_chain"),
            patch.object(profiles, "ROOT", self.root),
            patch.object(profiles, "PROFILES_DIR", self.root / "profiles"),
            patch.object(profiles, "ACTIVE_FILE", self.root / "active_profile"),
            patch.object(ch_mod, "CHANNELS_PATH", self.root / "channels.json"),
            # Stash a known self-host so the seeder's channel-creation
            # path has a concrete base_url to write.
            patch.dict(os.environ, {
                "MEMEMAGE_SELF_HOST": "127.0.0.1",
                "USER": "alice",
            }),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _seed(self):
        from mememage.server import _seed_first_run_defaults
        _seed_first_run_defaults(port=8443, scheme="https")

    def test_fresh_install_seeds_profile_chain_channel(self):
        self._seed()
        # Profile
        profs = [p for p in self.profiles.list_profiles() if p.get("has_private_key")]
        self.assertEqual(len(profs), 1)
        self.assertEqual(profs[0]["id"], "default")
        self.assertEqual(profs[0]["name"], "alice")  # from $USER
        # Chain
        chains = self.chains.list_chains()
        self.assertEqual(len(chains), 1)
        self.assertEqual(chains[0]["id"], self.chains.DEFAULT_CHAIN_ID)
        # Channel
        saved = json.loads((self.root / "channels.json").read_text(encoding="utf-8"))
        ids = [c["id"] for c in saved["channels"]]
        self.assertIn("self", ids)
        self_ch = next(c for c in saved["channels"] if c["id"] == "self")
        self.assertEqual(self_ch["type"], "http_push")
        self.assertTrue(self_ch["primary"])
        self.assertIn("127.0.0.1", self_ch["config"]["base_url"])

    def test_existing_profile_skips_seeder(self):
        # Pre-seed a profile, then run the seeder — should NOT create
        # a second "default" or touch the existing one.
        (self.root / "profiles" / "andy").mkdir(parents=True)
        (self.root / "profiles" / "andy" / "creator.txt").write_text("Andy", encoding="utf-8")
        # Need a private.key file for has_private_key to be True
        (self.root / "profiles" / "andy" / "private.key").write_text("STUB", encoding="utf-8")
        (self.root / "active_profile").write_text("andy", encoding="utf-8")

        self._seed()

        profs = [p for p in self.profiles.list_profiles() if p.get("has_private_key")]
        self.assertEqual(len(profs), 1)
        self.assertEqual(profs[0]["id"], "andy")
        # No "default" was created
        self.assertFalse((self.root / "profiles" / "default").exists())

    def test_existing_chain_skips_chain_seed(self):
        (self.root / "chains" / "anumel").mkdir(parents=True)
        (self.root / "chains" / "anumel" / "chain.json").write_text(
            json.dumps({"id": "anumel", "visibility": "light_energy"}),
            encoding="utf-8",
        )

        self._seed()

        chains = self.chains.list_chains()
        ids = [c["id"] for c in chains]
        self.assertIn("anumel", ids)
        # No "aries" was created because anumel already exists
        self.assertNotIn(self.chains.DEFAULT_CHAIN_ID, ids)

    def test_existing_http_push_channel_skips_channel_seed(self):
        # User-configured http_push channel — seeder must respect it.
        (self.root / "channels.json").write_text(json.dumps({"channels": [{
            "id": "my-mirror", "type": "http_push", "name": "Mirror",
            "enabled": True, "primary": True,
            "credentials": {},
            "config": {"base_url": "https://other.example/api/souls"},
        }]}), encoding="utf-8")

        self._seed()

        saved = json.loads((self.root / "channels.json").read_text(encoding="utf-8"))
        ids = [c["id"] for c in saved["channels"]]
        self.assertIn("my-mirror", ids)
        # No "self" channel was added
        self.assertNotIn("self", ids)

    def test_seeder_demotes_existing_primary_when_adding_self(self):
        # Default channels.json has IA + Zenodo, IA marked primary.
        # Seeder appends self as primary → IA must lose primary.
        (self.root / "channels.json").write_text(json.dumps({"channels": [
            {"id": "ia", "type": "internet_archive", "name": "Internet Archive",
             "enabled": True, "primary": True, "credentials": {}, "config": {}},
            {"id": "zenodo", "type": "zenodo", "name": "Zenodo",
             "enabled": False, "primary": False, "credentials": {}, "config": {}},
        ]}), encoding="utf-8")

        self._seed()

        saved = json.loads((self.root / "channels.json").read_text(encoding="utf-8"))
        ia = next(c for c in saved["channels"] if c["id"] == "ia")
        self_ch = next(c for c in saved["channels"] if c["id"] == "self")
        self.assertFalse(ia["primary"])
        self.assertTrue(self_ch["primary"])

    def test_seed_handles_missing_self_host_gracefully(self):
        # MEMEMAGE_SELF_HOST not set — the self channel SEED is skipped (it's
        # gated on a server context), but profile + chain seeders still run.
        # (Reconciliation of an EXISTING self channel is unconditional — see
        # test_reconciles_stale_self_push_base_url — this only covers seeding.)
        with patch.dict(os.environ, {"MEMEMAGE_SELF_HOST": ""}):
            self._seed()
        self.assertTrue((self.root / "profiles" / "default").exists())
        self.assertTrue((self.root / "chains" / self.chains.DEFAULT_CHAIN_ID).exists())
        if (self.root / "channels.json").exists():
            saved = json.loads((self.root / "channels.json").read_text(encoding="utf-8"))
            ids = [c["id"] for c in saved.get("channels", [])]
            self.assertNotIn("self", ids)

    def test_reconciles_stale_self_push_base_url(self):
        # A self channel frozen with a pre-TLS, public-host base_url (the
        # bootstrap-ordering bug: seed runs http://<public-ip> before
        # vps-setup installs the cert) must be HEALED to loopback + the
        # current scheme on the next boot — else the self-push PUT hangs
        # (plain HTTP to a TLS port, or an unreachable public IP with no NAT
        # hairpin) and cascades into a server wedge.
        (self.root / "channels.json").write_text(json.dumps({"channels": [
            {"id": "self", "type": "http_push", "name": "This server (self-push)",
             "enabled": True, "primary": True, "credentials": {},
             "config": {"base_url": "http://203.0.113.5:8443/api/souls"}},
        ]}), encoding="utf-8")
        self._seed()  # scheme="https", port=8443
        saved = json.loads((self.root / "channels.json").read_text(encoding="utf-8"))
        self_ch = next(c for c in saved["channels"] if c["id"] == "self")
        self.assertEqual(self_ch["config"]["base_url"],
                         "https://127.0.0.1:8443/api/souls")
        self.assertTrue(self_ch["config"]["accept_self_signed"])


if __name__ == "__main__":
    unittest.main()
