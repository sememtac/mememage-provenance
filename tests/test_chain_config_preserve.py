"""A payload Apply (chain_config.save) must NOT wipe chain.json fields it doesn't
model — password_verifier, gps_source, preset_name are owned by other surfaces.
Regression for: set a dark chain's password, set up the payload, password gone.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mememage import chain_config, chains


class SavePreservesUnmodeledFields(unittest.TestCase):
    def test_payload_apply_keeps_secret_gps_preset(self):
        root = Path(tempfile.mkdtemp())
        croot = root / "chains"
        cdir = croot / "darkchain"
        cdir.mkdir(parents=True)
        prior = {
            "id": "darkchain", "name": "Dark", "visibility": "dark_matter",
            "schema_version": 1, "M": 1,
            "layers": [{"name": "layer_1", "K": 1, "entry": "voice"}], "pinned": [],
            "entries": {"voice": {"sources": ["/tmp/x.wav"]}},
            "constellation_size": 12, "identifier_prefix": "dark",
            "created_at": "2026-01-01T00:00:00Z",
            "password_verifier": {"salt": "aa", "hash": "bb", "iter": 200000},
            "gps_source": "machine", "preset_name": "mypreset",
        }
        (cdir / "chain.json").write_text(json.dumps(prior))
        with patch.object(chains, "CHAINS_ROOT", croot):
            cfg = chain_config.load("darkchain")
            chain_config.save(cfg, "darkchain")
            after = json.loads((cdir / "chain.json").read_text())
        self.assertEqual(after.get("password_verifier"),
                         {"salt": "aa", "hash": "bb", "iter": 200000})  # was wiped
        self.assertEqual(after.get("gps_source"), "machine")
        self.assertEqual(after.get("preset_name"), "mypreset")
        self.assertEqual(after.get("identifier_prefix"), "dark")        # locked-once kept
        self.assertEqual(after.get("visibility"), "dark_matter")


if __name__ == "__main__":
    unittest.main()
