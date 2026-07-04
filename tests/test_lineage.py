"""Tests for mememage.lineage — lineage tracking and chain walking."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import mememage.lineage as lineage


class TestGetParentId(unittest.TestCase):
    def test_returns_none_when_no_file(self):
        """Should return None when the state file does not exist."""
        path = Path("/tmp/_mememage_test_nonexistent/last_id.json")
        result = lineage.get_parent_id(state_file=path)
        self.assertIsNone(result)


class TestSetAndGetParentId(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._state_file = Path(self._tmpdir) / "last_id.json"

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_roundtrip(self):
        """set_parent_id then get_parent_id should return the same value."""
        lineage.set_parent_id("mememage-abcd1234", state_file=self._state_file)
        result = lineage.get_parent_id(state_file=self._state_file)
        self.assertEqual(result, "mememage-abcd1234")

    def test_creates_directory(self):
        """set_parent_id should create the parent directory if it doesn't exist."""
        nested = Path(self._tmpdir) / "sub" / "deep" / "last_id.json"
        lineage.set_parent_id("mememage-00001111", state_file=nested)
        self.assertTrue(nested.parent.exists())
        self.assertEqual(lineage.get_parent_id(state_file=nested), "mememage-00001111")


class TestFetchLineageChain(unittest.TestCase):
    """Tests for fetch_lineage_chain with mocked fetch_json."""

    @patch("mememage.lineage.fetch_json")
    def test_walks_chain(self, mock_fetch):
        """Walk a chain of 3 records linked by parent_id."""
        record_c = {
            "timestamp": "2026-01-03T00:00:00Z",
            "prompt": "third image prompt",
            "parent_id": "mememage-bbbb2222",
        }
        record_b = {
            "timestamp": "2026-01-02T00:00:00Z",
            "prompt": "second image prompt",
            "parent_id": "mememage-aaaa1111",
        }
        record_a = {
            "timestamp": "2026-01-01T00:00:00Z",
            "prompt": "first image prompt",
        }

        mock_fetch.side_effect = [record_c, record_b, record_a]

        chain = lineage.fetch_lineage_chain("mememage-cccc3333")

        self.assertEqual(len(chain), 3)
        self.assertEqual(chain[0]["identifier"], "mememage-cccc3333")
        self.assertEqual(chain[0]["parent_id"], "mememage-bbbb2222")
        self.assertEqual(chain[1]["identifier"], "mememage-bbbb2222")
        self.assertEqual(chain[1]["parent_id"], "mememage-aaaa1111")
        self.assertEqual(chain[2]["identifier"], "mememage-aaaa1111")
        self.assertIsNone(chain[2]["parent_id"])

    @patch("mememage.lineage.fetch_json")
    def test_stops_at_genesis(self, mock_fetch):
        """Chain should stop when a record has no parent_id."""
        genesis = {
            "timestamp": "2026-01-01T00:00:00Z",
            "prompt": "genesis prompt",
        }

        mock_fetch.return_value = genesis
        chain = lineage.fetch_lineage_chain("mememage-genesis1")

        self.assertEqual(len(chain), 1)
        self.assertEqual(chain[0]["identifier"], "mememage-genesis1")
        self.assertIsNone(chain[0].get("parent_id"))
        self.assertEqual(chain[0]["prompt_preview"], "genesis prompt")
        self.assertEqual(mock_fetch.call_count, 1)


class TestChainSwitchIsolation(unittest.TestCase):
    """Regression: lineage DB must resolve per call, not at import.

    The bug: lineage.py used to bind ``DB_PATH = chains.path("mememage.db")``
    at module load. After the server boots and the user switches chains
    via the dashboard, lineage kept reading the original chain's database,
    so a mint on chain B would receive chain A's last mint as parent_id.
    """

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        # Two pretend chains, each with their own dir.
        self._chain_a_dir = self._tmpdir / "chains" / "a"
        self._chain_b_dir = self._tmpdir / "chains" / "b"
        self._chain_a_dir.mkdir(parents=True)
        self._chain_b_dir.mkdir(parents=True)
        # Stub chains.path: returns <chain_dir>/<name> based on a
        # mutable "active" pointer so the test can flip mid-flight.
        self._active = "a"
        def _path(name, chain_id=None):
            cid = chain_id or self._active
            base = self._chain_a_dir if cid == "a" else self._chain_b_dir
            return base / name
        self._patch = patch("mememage.lineage.chains.path", side_effect=_path)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_set_writes_to_active_chain_db(self):
        # Write on A, switch to B, write on B. Each chain reads back
        # only its own value — they must not bleed across.
        self._active = "a"
        lineage.set_parent_id("mememage-aaaa1111")
        self._active = "b"
        lineage.set_parent_id("mememage-bbbb2222")

        self._active = "a"
        self.assertEqual(lineage.get_parent_id(), "mememage-aaaa1111")
        self._active = "b"
        self.assertEqual(lineage.get_parent_id(), "mememage-bbbb2222")

    def test_get_returns_none_on_fresh_chain(self):
        self._active = "a"
        lineage.set_parent_id("mememage-aaaa1111")
        self._active = "b"
        self.assertIsNone(lineage.get_parent_id())


if __name__ == "__main__":
    unittest.main()
