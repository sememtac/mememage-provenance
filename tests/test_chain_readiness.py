"""Tests for server._chain_readiness — the chain-badge semantic state.

Pins the Option-A precedence: a provenance-only chain (no payload) is
conceivable whether sealed or not, so it reads "nopayload" — NOT "notready" —
even before any Age is sealed. Only a payload-carrying chain that hasn't
sealed reads "notready". This must stay in lockstep with the conception gate
(server._require_chain_sealed); both delegate to ChainConfig.has_payload().
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestChainReadiness(unittest.TestCase):
    def setUp(self):
        from mememage import chains as ch_mod
        from mememage import channels as chan_mod

        self.tmp = Path(tempfile.mkdtemp(prefix="mememage-readiness-"))
        (self.tmp / "chains").mkdir()
        self._patches = [
            patch.object(ch_mod, "MEMEMAGE_ROOT", self.tmp),
            patch.object(ch_mod, "CHAINS_ROOT", self.tmp / "chains"),
            patch.object(ch_mod, "CURRENT_CHAIN_FILE", self.tmp / "current_chain"),
            patch.object(chan_mod, "CHANNELS_PATH", self.tmp / "channels.json"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _readiness(self, cid):
        from mememage import server
        return server._chain_readiness(cid)

    def _write_payload_config(self, cid):
        """Give a chain a real payload (a decoder layer with sources)."""
        from mememage import chains
        cfg = {
            "id": cid, "name": cid, "visibility": "light_energy",
            "schema_version": 1, "M": 12,
            "layers": [{"name": "decoder", "K": 12, "entry": "decoder"}],
            "pinned": [],
            "entries": {"decoder": {"sources": ["docs/"]}},
        }
        (chains.chain_dir(cid) / "chain.json").write_text(json.dumps(cfg))

    def test_provenance_only_unsealed_is_nopayload(self):
        from mememage import chains
        chains.create("prov", identifier_prefix="provx")
        chains.switch("prov")
        # No payload, no sealed Age — conceivable, just flagged.
        self.assertEqual(self._readiness("prov"), "nopayload")

    def test_payload_chain_unsealed_is_notready(self):
        from mememage import chains
        chains.create("load", identifier_prefix="loadx")
        chains.switch("load")
        self._write_payload_config("load")
        # Carries a payload but unsealed — must seal before conceiving.
        self.assertEqual(self._readiness("load"), "notready")

    def _write_seal(self, cid, data):
        """Write sealed_chunks.json for a chain (dict -> JSON, str -> raw)."""
        from mememage import chains
        p = chains.chain_dir(cid) / "sealed_chunks.json"
        p.write_text(data if isinstance(data, str) else json.dumps(data))

    def test_payload_chain_valid_seal_is_ready(self):
        from mememage import chains
        chains.create("loadok", identifier_prefix="loadok")
        chains.switch("loadok")
        self._write_payload_config("loadok")
        self._write_seal("loadok", {"age": 1, "decoder_hash": "abc"})
        # Payload + a real seal, manifest never built (no drift) -> ready.
        self.assertEqual(self._readiness("loadok"), "ready")

    def test_corrupt_seal_is_notready(self):
        # A seal file that won't parse must NOT read "ready" (it would fail
        # at mint). Validate-parses, not just exists.
        from mememage import chains
        chains.create("loadbad", identifier_prefix="loadbad")
        chains.switch("loadbad")
        self._write_payload_config("loadbad")
        self._write_seal("loadbad", "{ truncated / not valid json")
        self.assertEqual(self._readiness("loadbad"), "notready")

    def test_empty_seal_is_notready(self):
        # Parses, but no core Age fields -> not a real seal.
        from mememage import chains
        chains.create("loadmt", identifier_prefix="loadmt")
        chains.switch("loadmt")
        self._write_payload_config("loadmt")
        self._write_seal("loadmt", {})
        self.assertEqual(self._readiness("loadmt"), "notready")

    def test_default_chain_id_is_provenance_only(self):
        # The default chain id ("aries") used to silently inherit the
        # canonical Mememage payload via chain_config.load(), trapping every
        # new self-hoster behind "seal first". A fresh aries must now be
        # provenance-only so the very first conception just works.
        from mememage import chains, chain_config
        chains.create(chains.DEFAULT_CHAIN_ID)  # bare chain.json, no payload
        chains.switch(chains.DEFAULT_CHAIN_ID)
        self.assertFalse(chain_config.load(chains.DEFAULT_CHAIN_ID).has_payload())
        self.assertEqual(self._readiness(chains.DEFAULT_CHAIN_ID), "nopayload")


if __name__ == "__main__":
    unittest.main()
