"""chains.remove permanently DELETES (no silent archive) and frees disk —
"delete means delete". Reports bytes freed; refuses the active chain.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mememage import chains


class ChainDelete(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.chains_root = self.root / "chains"
        self.chains_root.mkdir()

    def _chain(self, cid, size=5000):
        d = self.chains_root / cid
        (d / "uploads").mkdir(parents=True)
        (d / "uploads" / "big.bin").write_bytes(b"x" * size)
        (d / "chain.json").write_text("{}")
        return d

    def test_delete_removes_dir_and_reports_freed(self):
        with patch.object(chains, "CHAINS_ROOT", self.chains_root), \
             patch.object(chains, "current", return_value="other"):
            d = self._chain("test", 5000)
            freed = chains.remove("test")
        self.assertFalse(d.exists())          # actually gone, not archived
        self.assertGreaterEqual(freed, 5000)  # bytes freed reported

    def test_no_archive_dir_created(self):
        with patch.object(chains, "CHAINS_ROOT", self.chains_root), \
             patch.object(chains, "current", return_value="other"):
            self._chain("test")
            chains.remove("test")
        # The old code moved to ~/.mememage/archive/ — nothing of the sort now.
        self.assertFalse((chains.MEMEMAGE_ROOT / "archive" / "chains" / "test").exists())

    def test_refuses_active_chain(self):
        with patch.object(chains, "CHAINS_ROOT", self.chains_root), \
             patch.object(chains, "current", return_value="active"):
            self._chain("active")
            with self.assertRaises(RuntimeError):
                chains.remove("active")

    def test_missing_raises(self):
        with patch.object(chains, "CHAINS_ROOT", self.chains_root), \
             patch.object(chains, "current", return_value="other"):
            with self.assertRaises(FileNotFoundError):
                chains.remove("nope")


if __name__ == "__main__":
    unittest.main()
