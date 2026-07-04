"""Startup sweeps that reclaim large orphaned payload files.

- ``_cleanup_orphan_payload_uploads``: drop files in a chain's uploads/ that no
  chain entry references (the server-side safety net for the dashboard's
  client-side delete). References gathered across ALL chains, so a shared file
  is never removed; an unparseable chain.json keeps its own uploads.
- ``_cleanup_stale_part_files``: reap leftover .part stream temps older than an
  hour (only a hard process kill mid-stream leaks one), age-gated so a live
  upload's temp is never raced.
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from mememage.server import (
    _cleanup_orphan_payload_uploads,
    _cleanup_stale_part_files,
)


class OrphanPayloadSweep(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_orphan_removed_referenced_and_part_kept(self):
        cdir = self.root / "a"
        up = cdir / "uploads"
        up.mkdir(parents=True)
        keep = up / "keep.bin"; keep.write_bytes(b"x" * 10)
        orphan = up / "orphan.bin"; orphan.write_bytes(b"x" * 10)
        part = up / "incoming.part"; part.write_bytes(b"x" * 10)
        (cdir / "chain.json").write_text(
            json.dumps({"entries": {"e": {"sources": [str(keep)]}}}))
        _cleanup_orphan_payload_uploads(self.root)
        self.assertTrue(keep.exists())     # referenced → kept
        self.assertFalse(orphan.exists())  # unreferenced → removed
        self.assertTrue(part.exists())     # .part left for the part sweep

    def test_cross_chain_reference_respected(self):
        # chainB references a file that physically lives in chainA's uploads.
        a = self.root / "a"; aup = a / "uploads"; aup.mkdir(parents=True)
        shared = aup / "shared.bin"; shared.write_bytes(b"x" * 10)
        (a / "chain.json").write_text(json.dumps({"entries": {}}))
        b = self.root / "b"; bup = b / "uploads"; bup.mkdir(parents=True)
        (b / "chain.json").write_text(
            json.dumps({"entries": {"e": {"sources": [str(shared)]}}}))
        _cleanup_orphan_payload_uploads(self.root)
        self.assertTrue(shared.exists())   # referenced anywhere → kept

    def test_unparseable_chain_keeps_its_uploads(self):
        c = self.root / "c"; up = c / "uploads"; up.mkdir(parents=True)
        prot = up / "protected.bin"; prot.write_bytes(b"x" * 10)
        (c / "chain.json").write_text("{not valid json")
        _cleanup_orphan_payload_uploads(self.root)
        self.assertTrue(prot.exists())     # conservative when we can't parse

    def test_missing_root_is_noop(self):
        _cleanup_orphan_payload_uploads(self.root / "nope")  # must not raise


class StalePartSweep(unittest.TestCase):
    def test_old_part_removed_young_and_plain_kept(self):
        d = Path(tempfile.mkdtemp())
        old = d / "old.part"; old.write_bytes(b"x")
        young = d / "young.part"; young.write_bytes(b"x")
        plain = d / "real.bin"; plain.write_bytes(b"x")
        aged = time.time() - 7200  # 2h old, past the 1h cutoff
        os.utime(old, (aged, aged))
        _cleanup_stale_part_files([d])
        self.assertFalse(old.exists())     # >1h → reaped
        self.assertTrue(young.exists())    # fresh → kept (could be in-flight)
        self.assertTrue(plain.exists())    # non-.part untouched

    def test_missing_root_is_noop(self):
        _cleanup_stale_part_files([Path(tempfile.mkdtemp()) / "nope"])  # no raise


if __name__ == "__main__":
    unittest.main()
