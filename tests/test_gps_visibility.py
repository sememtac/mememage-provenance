"""GPS visibility — a chain chooses time_locked (default) or public.

time_locked seals coordinates in the RSA puzzle; public ALSO stores plaintext
``gps`` so the cert shows the birthplace now. The plaintext is in the V1 hash,
so the shown location is tamper-evident.
"""

import importlib
import json
import tempfile
import unittest
from pathlib import Path


def _isolated_chains():
    from mememage import chains as chains_module
    tmp = Path(tempfile.mkdtemp(prefix="mememage_gpsvis_test_"))
    importlib.reload(chains_module)
    chains_module.MEMEMAGE_ROOT = tmp
    chains_module.CHAINS_ROOT = tmp / "chains"
    chains_module.CURRENT_CHAIN_FILE = tmp / "current_chain"
    return chains_module, tmp


def setUpModule():
    from mememage import chains as chains_module
    setUpModule._snap = (chains_module.MEMEMAGE_ROOT, chains_module.CHAINS_ROOT,
                         chains_module.CURRENT_CHAIN_FILE)


def tearDownModule():
    from mememage import chains as chains_module
    importlib.reload(chains_module)
    if hasattr(setUpModule, "_snap"):
        (chains_module.MEMEMAGE_ROOT, chains_module.CHAINS_ROOT,
         chains_module.CURRENT_CHAIN_FILE) = setUpModule._snap


class TestGpsVisibilityChainSetting(unittest.TestCase):
    def setUp(self):
        self.chains, self.tmp = _isolated_chains()
        self.chains.create("c1")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_is_time_locked(self):
        self.assertEqual(self.chains.get_gps_visibility("c1"), "time_locked")

    def test_set_and_get_public(self):
        self.chains.set_gps_visibility("c1", "public")
        self.assertEqual(self.chains.get_gps_visibility("c1"), "public")
        # Persisted to chain.json
        meta = json.loads((self.chains.CHAINS_ROOT / "c1" / "chain.json").read_text())
        self.assertEqual(meta["gps_visibility"], "public")

    def test_invalid_value_rejected(self):
        with self.assertRaises(ValueError):
            self.chains.set_gps_visibility("c1", "sometimes")

    def test_unknown_chain_raises(self):
        with self.assertRaises(FileNotFoundError):
            self.chains.set_gps_visibility("nope", "public")

    def test_legacy_chain_without_field_defaults(self):
        # A chain.json lacking gps_visibility reads as time_locked (private).
        (self.chains.CHAINS_ROOT / "c1" / "chain.json").write_text(
            json.dumps({"id": "c1", "visibility": "light_energy"}))
        self.assertEqual(self.chains.get_gps_visibility("c1"), "time_locked")


class TestGpsPlaintextHashed(unittest.TestCase):
    """The plaintext ``gps`` is in the V1 inclusion set, so it's covered by
    WITNESSED — tampering with the shown coordinates breaks the hash."""

    def _hash(self, **extra):
        from mememage.core import compute_content_hash
        rec = {"hash_version": 1, "identifier": "x-0000000000000000",
               "width": 1, "height": 1}
        rec.update(extra)
        return compute_content_hash(rec)

    def test_gps_changes_the_hash(self):
        # Present vs absent → different hash (gps is hashed, not ignored).
        self.assertNotEqual(self._hash(), self._hash(gps=[45.5, -122.6]))

    def test_tampering_gps_breaks_hash(self):
        # A one-coordinate change flips the hash → tamper-evident.
        self.assertNotEqual(self._hash(gps=[45.5, -122.6]),
                            self._hash(gps=[45.5, -122.7]))


if __name__ == "__main__":
    unittest.main()
