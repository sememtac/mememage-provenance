"""chunk_state is a cache; the living chain is the truth.

chunk_state.outer_position tallies every mint ever *attempted* on a chain. A
mint whose record was later purged from its surface still leaves the counter
advanced, so the counter drifts permanently ahead of reality — and since the
dashboard renders it as "star N/365", it claims stars that don't exist.

These tests pin the repair path: walk the records from genesis, count the ones
that verify, and rebuild the counters from them.

Isolated onto a tmp MEMEMAGE_ROOT — must never touch the real chain.
"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from mememage import chains, core, site_embed


def _record(pos, ident, parent, heart, name="Testmul", cadence=12):
    rec = {
        "identifier": ident,
        "parent_id": parent,
        "hash_version": 1,
        "outer_position": pos,
        "outer_total": 365,
        "width": 1024, "height": 1024,
        "conceived": f"2026-07-0{1 + pos // 10}T0{pos % 10}:00:00Z",
        "constellation_index": pos % cadence,
        "constellation_size": cadence,
        "constellation_name": name,
        "heart_star_id": heart,
        "chain_visibility": 0,
    }
    rec["content_hash"] = core.compute_content_hash(rec)
    return rec


class TestChainReconcile(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "received").mkdir(parents=True)
        # BOTH roots must move. `MEMEMAGE_ROOT` is read at call time (walk_living
        # _chain), but `CHAINS_ROOT` is a module-level constant bound at import,
        # so patching only the former leaves chunk_state_file() pointing at the
        # REAL chain — these tests would then rewrite the operator's live state.
        self._patches = [
            patch.object(chains, "MEMEMAGE_ROOT", root),
            patch.object(chains, "CHAINS_ROOT", root / "chains"),
        ]
        for p in self._patches:
            p.start()
        self.root = root
        # Belt and braces: prove we are not aimed at the real chain before any
        # test writes a byte. If this ever trips, do NOT relax it.
        target = site_embed.chunk_state_file()
        assert str(target).startswith(str(root)), \
            f"test isolation broken: chunk_state_file() -> {target}"

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    def _write_chain(self, n, cadence=12):
        """n stars, genesis first, contiguous positions, correct hearts."""
        ids = ["mememage-%016x" % i for i in range(n)]
        heart = ids[0]
        for pos, ident in enumerate(ids):
            if pos % cadence == 0:
                heart = ident
            parent = None if pos == 0 else ids[pos - 1]
            rec = _record(pos, ident, parent, heart, cadence=cadence)
            (self.root / "received" / f"{ident}.soul").write_text(json.dumps(rec))
        return ids

    def test_walk_counts_only_verifying_records(self):
        ids = self._write_chain(5)
        self.assertEqual(len(site_embed.walk_living_chain()), 5)

        # corrupt one record's hash → it is no longer a star, and the chain
        # truncates at the break (its children are unreachable from genesis)
        path = self.root / "received" / f"{ids[3]}.soul"
        rec = json.loads(path.read_text())
        rec["width"] = 4096                      # hash no longer matches
        path.write_text(json.dumps(rec))
        self.assertEqual(len(site_embed.walk_living_chain()), 3)

    def test_drift_detected_and_reconciled(self):
        self._write_chain(33)
        state = {"inner_position": 3, "outer_position": 100,
                 "heart_star": {"identifier": "mememage-%016x" % 0,
                                "constellation_name": "Wrongmul"}}
        site_embed._save_chunk_state(state)

        drift = site_embed.chain_state_drift()
        self.assertIsNotNone(drift)
        self.assertEqual(drift["stars"], 33)
        self.assertEqual(drift["stored_outer"], 100)

        result = site_embed.reconcile_from_chain()
        self.assertEqual(result["stars"], 33)
        self.assertEqual(result["after"]["outer_position"], 33)
        self.assertEqual(result["after"]["inner_position"], 33 % 12)
        # heart star of the constellation the newest record belongs to (star 24)
        self.assertEqual(result["after"]["heart_star"], "mememage-%016x" % 24)

        self.assertIsNone(site_embed.chain_state_drift())

    def test_reconcile_is_idempotent(self):
        self._write_chain(7)
        site_embed.reconcile_from_chain()
        first = site_embed._load_chunk_state()
        site_embed.reconcile_from_chain()
        self.assertEqual(first, site_embed._load_chunk_state())

    def test_dry_run_writes_nothing(self):
        self._write_chain(9)
        site_embed._save_chunk_state({"outer_position": 99, "inner_position": 1})
        site_embed.reconcile_from_chain(dry_run=True)
        self.assertEqual(site_embed._load_chunk_state()["outer_position"], 99)

    def test_refuses_when_chain_has_a_gap(self):
        """A missing soul must NOT silently shrink the star count."""
        ids = self._write_chain(6)
        # delete star 3 and re-parent star 4 onto star 2, so the walk still
        # reaches the end but positions are 0,1,2,4,5 — a gap at 3.
        (self.root / "received" / f"{ids[3]}.soul").unlink()
        path = self.root / "received" / f"{ids[4]}.soul"
        rec = json.loads(path.read_text())
        rec["parent_id"] = ids[2]
        rec["content_hash"] = core.compute_content_hash(
            {k: v for k, v in rec.items() if k != "content_hash"})
        path.write_text(json.dumps(rec))

        with self.assertRaises(RuntimeError) as ctx:
            site_embed.reconcile_from_chain()
        self.assertIn("gaps", str(ctx.exception))

    def test_heart_dropped_when_next_star_opens_a_constellation(self):
        """After exactly `cadence` stars the next one is a new heart star."""
        self._write_chain(12, cadence=12)
        site_embed.reconcile_from_chain()
        state = site_embed._load_chunk_state()
        self.assertEqual(state["outer_position"], 12)
        self.assertNotIn("heart_star", state)

    def test_reconcile_backs_up_previous_state(self):
        self._write_chain(4)
        site_embed._save_chunk_state({"outer_position": 77, "inner_position": 5})
        site_embed.reconcile_from_chain()
        backups = list(site_embed.chunk_state_file().parent.glob("*pre-reconcile*"))
        self.assertTrue(backups, "reconcile must back up the prior state")
        self.assertEqual(json.loads(backups[0].read_text())["outer_position"], 77)


if __name__ == "__main__":
    unittest.main()
