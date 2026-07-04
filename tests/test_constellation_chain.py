"""Provenance-only chains must still form constellations.

Regression: advance_chunk_index() no-op'd when unsealed, so outer_position
froze at 0; get_heart_star() (which requires outer != 0) always returned None,
and every conception became its own heart star (heart_star_id == self,
constellation_index == 0). Now the outer position advances per mint even
without a seal, so records link into constellations of M (blank chains = 12).
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestProvenanceConstellation(unittest.TestCase):
    def setUp(self):
        from mememage import chains as ch_mod
        self.ch = ch_mod
        self.tmp = Path(tempfile.mkdtemp(prefix="mememage-const-"))
        (self.tmp / "chains").mkdir()
        self._patches = [
            patch.object(ch_mod, "MEMEMAGE_ROOT", self.tmp),
            patch.object(ch_mod, "CHAINS_ROOT", self.tmp / "chains"),
            patch.object(ch_mod, "CURRENT_CHAIN_FILE", self.tmp / "current_chain"),
        ]
        for p in self._patches:
            p.start()
        self.ch.create("prov")
        self.ch.switch("prov")

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unsealed_outer_position_advances(self):
        from mememage.site_embed import advance_chunk_index, current_outer_position
        self.assertEqual(current_outer_position(), 0)
        advance_chunk_index()
        self.assertEqual(current_outer_position(), 1)
        advance_chunk_index()
        self.assertEqual(current_outer_position(), 2)

    def test_conceptions_form_a_constellation(self):
        # Faithfully simulate each conception's heart-star handshake:
        # read position + heart BEFORE the mint, then (as _step_upload does)
        # set the heart if we're it and advance the counter.
        from mememage.site_embed import (set_heart_star, get_heart_star,
                                          advance_chunk_index, current_outer_position)
        rows = []  # (identifier, heart_star_id, constellation_index)
        for i in range(14):
            ident = f"id{i}"
            outer = current_outer_position()
            heart = get_heart_star()
            if heart:
                rows.append((ident, heart["identifier"], outer % 12))
            else:
                rows.append((ident, ident, 0))           # this record IS the heart
                set_heart_star(ident, "Const", "hash")
            advance_chunk_index()

        # Record 0 founds the constellation (its own heart, α).
        self.assertEqual(rows[0], ("id0", "id0", 0))
        # Records 1..11 are siblings of id0, Bayer β..μ (index 1..11).
        for i in range(1, 12):
            self.assertEqual(rows[i], (f"id{i}", "id0", i),
                             f"record {i} should be a sibling of id0 at index {i}")
        # Record 12 wraps the cycle (M=12) → a NEW heart star.
        self.assertEqual(rows[12], ("id12", "id12", 0))
        # Record 13 is a sibling of the new heart.
        self.assertEqual(rows[13], ("id13", "id12", 1))

    def test_outer_total_is_chain_M(self):
        # The blank chain's constellation cadence is 12 — provenance-only
        # records report it so the Observatory can place them on a 12-cell ring.
        from mememage.site_embed import current_outer_total
        self.assertEqual(current_outer_total(), 12)

    def _simulate(self, count):
        """Faithfully replay the heart-star handshake for `count` mints,
        returning (identifier, heart_star_id, constellation_index) per record.
        Uses the live cadence so it tracks whatever constellation_size is set."""
        from mememage.site_embed import (set_heart_star, get_heart_star,
                                          advance_chunk_index,
                                          current_outer_position,
                                          constellation_cadence)
        rows = []
        for i in range(count):
            ident = f"id{i}"
            outer = current_outer_position()
            heart = get_heart_star()
            if heart:
                rows.append((ident, heart["identifier"], outer % constellation_cadence()))
            else:
                rows.append((ident, ident, 0))  # this record IS the heart
                set_heart_star(ident, "Const", "hash")
            advance_chunk_index()
        return rows

    def test_constellation_size_three(self):
        # N=3: heart + β + γ, then a NEW heart at record 3.
        self.ch.set_constellation_size("prov", 3)
        from mememage.site_embed import constellation_cadence, current_outer_total
        self.assertEqual(constellation_cadence(), 3)
        self.assertEqual(current_outer_total(), 3)
        rows = self._simulate(7)
        self.assertEqual(rows[0], ("id0", "id0", 0))   # heart α
        self.assertEqual(rows[1], ("id1", "id0", 1))   # β
        self.assertEqual(rows[2], ("id2", "id0", 2))   # γ
        self.assertEqual(rows[3], ("id3", "id3", 0))   # new heart α
        self.assertEqual(rows[4], ("id4", "id3", 1))   # β
        self.assertEqual(rows[5], ("id5", "id3", 2))   # γ
        self.assertEqual(rows[6], ("id6", "id6", 0))   # new heart α

    def test_constellation_size_twelve_unchanged(self):
        # N=12 (default): unchanged from the historical 12-record cadence.
        self.ch.set_constellation_size("prov", 12)
        rows = self._simulate(14)
        self.assertEqual(rows[0], ("id0", "id0", 0))
        for i in range(1, 12):
            self.assertEqual(rows[i], (f"id{i}", "id0", i))
        self.assertEqual(rows[12], ("id12", "id12", 0))   # new heart at 12
        self.assertEqual(rows[13], ("id13", "id12", 1))

    def test_constellation_size_accessor_roundtrip(self):
        # Default is 12; set/get round-trips; out-of-range clamps into [1,24]
        # (24 = the full Greek/Bayer alphabet cap).
        self.assertEqual(self.ch.get_constellation_size("prov"), 12)
        self.ch.set_constellation_size("prov", 5)
        self.assertEqual(self.ch.get_constellation_size("prov"), 5)
        self.assertEqual(self.ch.set_constellation_size("prov", 99)["constellation_size"], 24)
        self.assertEqual(self.ch.get_constellation_size("prov"), 24)
        self.assertEqual(self.ch.set_constellation_size("prov", 0)["constellation_size"], 1)
        self.assertEqual(self.ch.get_constellation_size("prov"), 1)
        with self.assertRaises(ValueError):
            self.ch.set_constellation_size("prov", "not-a-number")
        with self.assertRaises(FileNotFoundError):
            self.ch.set_constellation_size("nope", 3)

    def test_set_constellation_size_syncs_decoder_layer_k(self):
        # Option A: the decoder layer's K mirrors constellation_size on disk,
        # so the Payload editor and the seal never drift from it. Only the
        # layer named "decoder" is governed.
        import json
        meta_path = self.ch.CHAINS_ROOT / "prov" / "chain.json"
        meta = json.loads(meta_path.read_text())
        meta["layers"] = [
            {"name": "decoder", "K": 12, "entry": "decoder"},
            {"name": "truth", "K": 365, "entry": "truth"},
        ]
        meta_path.write_text(json.dumps(meta))
        self.ch.set_constellation_size("prov", 7)
        out = json.loads(meta_path.read_text())
        layers = {ly["name"]: ly for ly in out["layers"]}
        self.assertEqual(layers["decoder"]["K"], 7)   # rebound
        self.assertEqual(layers["truth"]["K"], 365)   # untouched
        self.assertEqual(out["constellation_size"], 7)

    def test_watermark_accessor_roundtrip(self):
        # Live per-chain setting: ON by default (subtle/standard are legacy aliases);
        # off is an explicit opt-out (persisted, not key-removed). Invalid presets rejected.
        self.assertEqual(self.ch.get_watermark("prov"), "on")   # default ON
        self.ch.set_watermark("prov", "on")
        self.assertEqual(self.ch.get_watermark("prov"), "on")
        # legacy aliases are accepted and normalize to "on"
        self.ch.set_watermark("prov", "subtle")
        self.assertEqual(self.ch.get_watermark("prov"), "on")
        self.ch.set_watermark("prov", "standard")
        self.assertEqual(self.ch.get_watermark("prov"), "on")
        self.ch.set_watermark("prov", "off")
        self.assertEqual(self.ch.get_watermark("prov"), "off")
        with self.assertRaises(ValueError):
            self.ch.set_watermark("prov", "bogus")
        with self.assertRaises(FileNotFoundError):
            self.ch.set_watermark("nope", "on")

    def test_watermark_applies_on_provenance_chain(self):
        # A provenance chain (no layers/entries) must still surface its
        # watermark through chain_config.load().watermark_params() — that's
        # what mint.py reads. Regression for the blank-path bug.
        from mememage import chain_config
        self.assertEqual(chain_config.load("prov").watermark_params(), (16, 40))  # default ON
        self.ch.set_watermark("prov", "subtle")
        self.assertEqual(chain_config.load("prov").watermark_params(), (16, 40))
        self.ch.set_watermark("prov", "standard")
        self.assertEqual(chain_config.load("prov").watermark_params(), (16, 40))

    def test_save_preserves_watermark_from_disk(self):
        # watermark is Config-owned: a Payload Apply (chain_config.save) must
        # not clobber the live setting even if the cfg it writes lacks it.
        from mememage import chain_config
        self.ch.set_watermark("prov", "on")
        cfg = chain_config.load("prov")
        cfg.watermark = None  # simulate a Payload Apply that carries no watermark
        chain_config.save(cfg, "prov")
        self.assertEqual(self.ch.get_watermark("prov"), "on")


if __name__ == "__main__":
    unittest.main()
