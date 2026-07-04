"""GENESIS SCHEMA LOCK — freeze the .soul structure.

The .soul file is the permanent public artifact. After genesis we cannot
afford undesired drift in which fields exist, their order, or which are
tamper-evident. These tests are the lock: they pin

  1. the exact field UNIVERSE + on-disk ORDER (_SOUL_DISK_LAYOUT),
  2. the exact V1 HASH set (_HASH_INCLUDED_V1),
  3. Python <-> browser (verify.js) hash-set PARITY — so the decoder can
     never silently disagree with the server about what WITNESSED means,
  4. that every hashed field has a home in the layout (no orphan fields).

Any deliberate schema change must update the GOLDEN_* constants here in the
same commit — that edit is the review gate. An *accidental* add/remove/rename
/reorder fails CI instead of shipping.

If you're here because a test failed: that's the lock doing its job. Confirm
the change is intentional, bump hash_version if the HASH set changed (and
update verify.js + test_hash_version.py in lockstep), then update the golden
constants.
"""

import os
import re
import unittest

from mememage.core import _SOUL_DISK_LAYOUT, _HASH_INCLUDED_V1


# --- GOLDEN: the exact on-disk field order (cosmetic, but frozen). ----------
GOLDEN_LAYOUT = [
    "identifier", "content_hash", "hash_version", "parent_id", "creator_name",
    "rendered", "conceived", "age", "outer_position", "outer_total",
    "width", "height",
    "constellation_hash", "constellation_name", "constellation_index",
    "constellation_size", "heart_star_id",
    "decoder_hash", "machine_fingerprint", "key_fingerprint", "birth_traits",
    "chain_visibility", "rarity",
    "origin", "birth", "gps", "gps_time_locked", "gps_password_locked",
    "signature", "public_key", "encrypted_fields",
    "chunks", "chunks_root", "encrypted_chunks",
    "thumbnail", "luma_grid",
    # `about` is pinned LAST by _canonicalize_for_disk, not listed here.
]

# --- GOLDEN: the exact V1 content-hash inclusion set (tamper-evidence). -----
GOLDEN_HASH_V1 = {
    "identifier", "hash_version", "parent_id", "conceived", "rendered", "age",
    "width", "height", "origin", "birth", "birth_traits", "rarity",
    "constellation_hash", "constellation_name", "constellation_index",
    "constellation_size", "heart_star_id", "machine_fingerprint",
    "decoder_hash", "public_key", "key_fingerprint", "chunks_root",
    "chain_visibility", "outer_position", "outer_total",
    "gps", "gps_time_locked", "gps_password_locked",
    "luma_grid",
    "encrypted_fields", "encrypted_chunks",
}

# Real soul fields that are deliberately NOT hashed (with the reason).
#   content_hash  — it IS the hash
#   signature     — signs the hash (chicken-and-egg)
#   thumbnail     — bound via the Ed25519 signature payload instead
#   creator_name  — live display from the key (keychain rename), not a record claim
#   about         — Rosetta Stone, excluded so wording can evolve
#   chunks        — bulk data; chunks_root carries its integrity
KNOWN_UNHASHED = {
    "content_hash", "signature", "thumbnail", "creator_name", "about", "chunks",
}


def _verify_js_hash_set():
    """Parse HASH_INCLUDED_V1 out of docs/js/verify.js (comments stripped)."""
    path = os.path.join(os.path.dirname(__file__), "..", "docs", "js", "verify.js")
    src = open(path, encoding="utf-8").read()
    block = re.search(r"const HASH_INCLUDED_V1 = new Set\(\[(.*?)\]\);", src, re.S)
    assert block, "HASH_INCLUDED_V1 not found in verify.js"
    # Drop // line comments so field names mentioned in prose don't count.
    body = "\n".join(line.split("//", 1)[0] for line in block.group(1).splitlines())
    return set(re.findall(r"'([a-z_]+)'", body))


class SchemaLock(unittest.TestCase):
    def test_disk_layout_is_frozen(self):
        self.assertEqual(
            list(_SOUL_DISK_LAYOUT), GOLDEN_LAYOUT,
            "_SOUL_DISK_LAYOUT changed. If intentional, update GOLDEN_LAYOUT "
            "in this file in the same commit (this is the review gate).")

    def test_hash_set_is_frozen(self):
        self.assertEqual(
            set(_HASH_INCLUDED_V1), GOLDEN_HASH_V1,
            "_HASH_INCLUDED_V1 changed. A hash-set change is a WITNESSED-"
            "semantics change — bump hash_version, update verify.js + "
            "test_hash_version.py in lockstep, then GOLDEN_HASH_V1 here.")

    def test_python_js_hash_parity(self):
        # The browser decoder must agree with the server on what's hashed, or
        # genuine souls fail WITNESSED in the viewer (or forgeries pass).
        self.assertEqual(
            _verify_js_hash_set(), set(_HASH_INCLUDED_V1),
            "verify.js HASH_INCLUDED_V1 drifted from core.py _HASH_INCLUDED_V1. "
            "They MUST stay byte-for-byte equivalent.")

    def test_every_hashed_field_has_a_layout_home(self):
        # No hashed field may be absent from the disk layout (else it would
        # dangle in the alphabetical tail).
        missing = set(_HASH_INCLUDED_V1) - set(_SOUL_DISK_LAYOUT)
        self.assertEqual(missing, set(), f"hashed fields not in layout: {missing}")

    def test_field_universe_is_covered(self):
        # Every legitimate soul field — hashed or deliberately-unhashed — has a
        # layout home. Anything else a mint produces (a stray derived field
        # like song_name/distribution/rarity_score, a typo'd rename) would sit
        # OUTSIDE this union and get caught by an audit of a real soul.
        universe = set(_HASH_INCLUDED_V1) | KNOWN_UNHASHED
        not_placed = universe - set(_SOUL_DISK_LAYOUT) - {"about"}
        self.assertEqual(not_placed, set(),
                         f"known fields with no layout position: {not_placed}")

    def test_url_is_not_a_soul_field(self):
        # The soul is surface-agnostic: no blast URLs, ever. `url` lives on
        # state.primary_url (server-side handoff), never in the record.
        self.assertNotIn("url", _SOUL_DISK_LAYOUT)
        self.assertNotIn("url", _HASH_INCLUDED_V1)


if __name__ == "__main__":
    unittest.main()
