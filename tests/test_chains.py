"""Tests for mememage/chains.py — multi-chain state directory management."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _isolated_chains():
    """Return a chains module bound to a temp ~/.mememage/ root for testing."""
    import importlib
    from mememage import chains as chains_module

    tmp = Path(tempfile.mkdtemp(prefix="mememage_chains_test_"))
    importlib.reload(chains_module)
    chains_module.MEMEMAGE_ROOT = tmp
    chains_module.CHAINS_ROOT = tmp / "chains"
    chains_module.CURRENT_CHAIN_FILE = tmp / "current_chain"
    return chains_module, tmp


def setUpModule():
    """Snapshot the real chains module state before tests mutate it."""
    from mememage import chains as chains_module
    setUpModule._snapshot = (
        chains_module.MEMEMAGE_ROOT,
        chains_module.CHAINS_ROOT,
        chains_module.CURRENT_CHAIN_FILE,
    )


def tearDownModule():
    """Restore the real chains module state. Without this, later tests
    that resolve chain-scoped paths (site_embed seal_file, payload
    payload_dir, etc.) would follow stale pointers into now-deleted
    temp dirs.
    """
    import importlib
    from mememage import chains as chains_module
    importlib.reload(chains_module)
    if hasattr(setUpModule, "_snapshot"):
        chains_module.MEMEMAGE_ROOT, chains_module.CHAINS_ROOT, chains_module.CURRENT_CHAIN_FILE = setUpModule._snapshot


class TestCurrentChain(unittest.TestCase):
    def test_current_default_when_no_file(self):
        chains, tmp = _isolated_chains()
        self.assertEqual(chains.current(), chains.DEFAULT_CHAIN_ID)

    def test_current_reads_from_file(self):
        chains, tmp = _isolated_chains()
        tmp.mkdir(parents=True, exist_ok=True)
        (tmp / "current_chain").write_text("private_one\n")
        self.assertEqual(chains.current(), "private_one")

    def test_current_strips_whitespace(self):
        chains, tmp = _isolated_chains()
        tmp.mkdir(parents=True, exist_ok=True)
        (tmp / "current_chain").write_text("  spaced  \n\n")
        self.assertEqual(chains.current(), "spaced")

    def test_current_default_if_empty_file(self):
        chains, tmp = _isolated_chains()
        tmp.mkdir(parents=True, exist_ok=True)
        (tmp / "current_chain").write_text("")
        self.assertEqual(chains.current(), chains.DEFAULT_CHAIN_ID)


class TestPathResolution(unittest.TestCase):
    def test_path_uses_new_layout_when_chain_dir_exists(self):
        chains, tmp = _isolated_chains()
        (tmp / "chains" / chains.DEFAULT_CHAIN_ID).mkdir(parents=True)
        p = chains.path("sealed_chunks.json")
        self.assertEqual(p, tmp / "chains" / chains.DEFAULT_CHAIN_ID / "sealed_chunks.json")

    def test_path_falls_back_to_legacy_when_chain_dir_absent(self):
        chains, tmp = _isolated_chains()
        tmp.mkdir(parents=True, exist_ok=True)
        # Legacy file exists at root.
        (tmp / "sealed_chunks.json").write_text("{}")
        # Chain dir does NOT exist.
        p = chains.path("sealed_chunks.json")
        self.assertEqual(p, tmp / "sealed_chunks.json")

    def test_path_returns_new_for_fresh_writes(self):
        chains, tmp = _isolated_chains()
        # Neither chain dir nor legacy file exists — should return new path.
        p = chains.path("brand_new.json")
        self.assertEqual(p, tmp / "chains" / chains.DEFAULT_CHAIN_ID / "brand_new.json")


class TestCreateAndSwitch(unittest.TestCase):
    def test_create_writes_chain_json(self):
        chains, tmp = _isolated_chains()
        meta = chains.create("test_chain", visibility="light_energy", name="My Chain")
        self.assertEqual(meta["id"], "test_chain")
        self.assertEqual(meta["visibility"], "light_energy")
        self.assertEqual(meta["name"], "My Chain")
        # File present
        chain_json = tmp / "chains" / "test_chain" / "chain.json"
        self.assertTrue(chain_json.exists())
        saved = json.loads(chain_json.read_text())
        self.assertEqual(saved["id"], "test_chain")

    def test_create_rejects_invalid_visibility(self):
        chains, _ = _isolated_chains()
        with self.assertRaises(ValueError):
            chains.create("bad", visibility="invalid")

    def test_create_rejects_duplicate(self):
        chains, _ = _isolated_chains()
        chains.create("dupe")
        with self.assertRaises(FileExistsError):
            chains.create("dupe")

    def test_switch_updates_current_chain_file(self):
        chains, tmp = _isolated_chains()
        chains.create("alpha")
        chains.create("beta")
        chains.switch("beta")
        self.assertEqual(chains.current(), "beta")

    def test_switch_rejects_missing_chain(self):
        chains, _ = _isolated_chains()
        with self.assertRaises(FileNotFoundError):
            chains.switch("does_not_exist")


class TestListChains(unittest.TestCase):
    def test_list_empty_when_no_chains_dir(self):
        chains, _ = _isolated_chains()
        self.assertEqual(chains.list_chains(), [])

    def test_list_returns_all_chains(self):
        chains, _ = _isolated_chains()
        chains.create("alpha")
        chains.create("beta", visibility="dark_matter", name="Beta Display")
        result = chains.list_chains()
        ids = sorted(c["id"] for c in result)
        self.assertEqual(ids, ["alpha", "beta"])


class TestMigration(unittest.TestCase):
    def _seed_legacy(self, tmp):
        """Write a few legacy files at the root to simulate pre-migration state."""
        tmp.mkdir(parents=True, exist_ok=True)
        (tmp / "sealed_chunks.json").write_text('{"age":1}')
        (tmp / "chunk_state.json").write_text('{"inner_position":3}')
        (tmp / "records").mkdir()
        (tmp / "records" / "mememage-abc.soul").write_text("{}")

    def test_needs_migration_true_when_legacy_exists(self):
        chains, tmp = _isolated_chains()
        self._seed_legacy(tmp)
        self.assertTrue(chains.needs_migration())

    def test_needs_migration_false_when_chain_dir_exists(self):
        chains, tmp = _isolated_chains()
        self._seed_legacy(tmp)
        (tmp / "chains").mkdir()
        self.assertFalse(chains.needs_migration())

    def test_needs_migration_false_on_clean_install(self):
        chains, tmp = _isolated_chains()
        tmp.mkdir(parents=True, exist_ok=True)
        # Nothing exists — no migration needed.
        self.assertFalse(chains.needs_migration())

    def test_migrate_moves_legacy_files(self):
        chains, tmp = _isolated_chains()
        self._seed_legacy(tmp)
        result = chains.migrate()
        self.assertIn("sealed_chunks.json", result["moved_files"])
        self.assertIn("chunk_state.json", result["moved_files"])
        self.assertIn("records", result["moved_dirs"])
        # Files no longer at root
        self.assertFalse((tmp / "sealed_chunks.json").exists())
        self.assertFalse((tmp / "records").exists())
        # Files in new location
        _cid = chains.DEFAULT_CHAIN_ID
        self.assertTrue((tmp / "chains" / _cid / "sealed_chunks.json").exists())
        self.assertTrue((tmp / "chains" / _cid / "records" / "mememage-abc.soul").exists())
        # chain.json written
        self.assertTrue((tmp / "chains" / _cid / "chain.json").exists())
        # current_chain pointer set
        self.assertEqual(chains.current(), chains.DEFAULT_CHAIN_ID)
        # migration log written
        self.assertTrue((tmp / "migration.log").exists())

    def test_migrate_refuses_when_target_exists(self):
        chains, tmp = _isolated_chains()
        self._seed_legacy(tmp)
        (tmp / "chains" / chains.DEFAULT_CHAIN_ID).mkdir(parents=True)
        with self.assertRaises(FileExistsError):
            chains.migrate()

    def test_migrate_records_in_chain_json(self):
        chains, tmp = _isolated_chains()
        self._seed_legacy(tmp)
        chains.migrate(chain_id="aries", chain_name="Age of Aries", visibility="light_energy")
        chain_json = json.loads(
            (tmp / "chains" / "aries" / "chain.json").read_text()
        )
        self.assertEqual(chain_json["id"], "aries")
        self.assertEqual(chain_json["name"], "Age of Aries")
        self.assertEqual(chain_json["visibility"], "light_energy")
        self.assertTrue(chain_json["migrated_from_legacy"])


class TestChainPassword(unittest.TestCase):
    """Per-chain gating via a one-way verifier (rung-1). chain.json stores a
    PBKDF2 password_verifier, never the password value; the runtime value
    comes from a per-mint override or the MEMEMAGE_PASSWORD env var."""

    def test_set_password_stores_verifier_not_plaintext(self):
        chains, tmp = _isolated_chains()
        chains.create("aries", visibility="dark_matter", name="Test")
        chains.set_password("aries", "hunter2")
        meta = json.loads((tmp / "chains" / "aries" / "chain.json").read_text())
        self.assertNotIn("password", meta)
        self.assertIn("password_verifier", meta)
        self.assertNotIn("hunter2", json.dumps(meta))
        self.assertTrue(chains.has_password("aries"))
        self.assertIsNone(chains.get_password("aries"))

    def test_verify_password(self):
        chains, tmp = _isolated_chains()
        chains.create("aries", visibility="dark_matter")
        chains.set_password("aries", "correct horse")
        self.assertTrue(chains.verify_password("correct horse", "aries"))
        self.assertFalse(chains.verify_password("wrong", "aries"))
        chains.create("beta")
        self.assertIsNone(chains.verify_password("anything", "beta"))

    def test_clear_password_removes_verifier(self):
        chains, tmp = _isolated_chains()
        chains.create("aries", visibility="dark_matter")
        chains.set_password("aries", "first-pw")
        chains.set_password("aries", "")
        self.assertFalse(chains.has_password("aries"))
        meta = json.loads((tmp / "chains" / "aries" / "chain.json").read_text())
        self.assertNotIn("password", meta)
        self.assertNotIn("password_verifier", meta)

    def test_resolve_precedence_override_then_env_then_none(self):
        from unittest.mock import patch as _patch
        import os as _os
        chains, tmp = _isolated_chains()
        chains.create("aries", visibility="dark_matter")
        chains.set_password("aries", "seal")
        self.assertEqual(chains.resolve_password("aries", override="x"), "x")
        with _patch("mememage.config._load_dotenv"):
            prev = _os.environ.pop("MEMEMAGE_PASSWORD", None)
            try:
                _os.environ["MEMEMAGE_PASSWORD"] = "envpw"
                self.assertEqual(chains.resolve_password("aries"), "envpw")
                _os.environ.pop("MEMEMAGE_PASSWORD", None)
                self.assertIsNone(chains.resolve_password("aries"))
            finally:
                _os.environ.pop("MEMEMAGE_PASSWORD", None)
                if prev is not None:
                    _os.environ["MEMEMAGE_PASSWORD"] = prev

    def test_migrate_plaintext_to_verifier(self):
        chains, tmp = _isolated_chains()
        chains.create("aries", visibility="dark_matter")
        cj = tmp / "chains" / "aries" / "chain.json"
        meta = json.loads(cj.read_text())
        meta["password"] = "legacy-secret"
        cj.write_text(json.dumps(meta))
        res = chains.migrate_password("aries")
        self.assertTrue(res["migrated"])
        meta2 = json.loads(cj.read_text())
        self.assertNotIn("password", meta2)
        self.assertIn("password_verifier", meta2)
        self.assertTrue(chains.verify_password("legacy-secret", "aries"))
        self.assertFalse(chains.migrate_password("aries")["migrated"])

    def test_set_password_preserves_other_metadata(self):
        chains, tmp = _isolated_chains()
        meta_before = chains.create("aries", visibility="light_energy", name="My Chain")
        chains.set_password("aries", "k")
        meta_after = json.loads((tmp / "chains" / "aries" / "chain.json").read_text())
        for k in ("id", "name", "visibility", "created_at"):
            self.assertEqual(meta_before[k], meta_after[k])

    def test_set_password_refuses_missing_chain(self):
        chains, _ = _isolated_chains()
        with self.assertRaises(FileNotFoundError):
            chains.set_password("does-not-exist", "x")

    def test_password_file_owner_only_perms(self):
        chains, tmp = _isolated_chains()
        chains.create("aries", visibility="dark_matter")
        chains.set_password("aries", "secret")
        import stat as _stat
        mode = (tmp / "chains" / "aries" / "chain.json").stat().st_mode
        self.assertEqual(_stat.S_IMODE(mode) & 0o077, 0)


class TestIdentifierPrefix(unittest.TestCase):
    """Per-chain identifier prefix is set at creation, locked thereafter."""

    def test_default_when_not_specified(self):
        chains, _ = _isolated_chains()
        chains.create("aries")
        self.assertEqual(chains.get_identifier_prefix("aries"), "mememage")

    def test_custom_prefix_persists(self):
        chains, tmp = _isolated_chains()
        chains.create("phoenix", identifier_prefix="phoenix")
        self.assertEqual(chains.get_identifier_prefix("phoenix"), "phoenix")
        meta = json.loads((tmp / "chains" / "phoenix" / "chain.json").read_text())
        self.assertEqual(meta["identifier_prefix"], "phoenix")

    def test_default_not_stamped_to_disk(self):
        """A chain created without a prefix should not have the field
        on disk — absence means "use the default forever."."""
        chains, tmp = _isolated_chains()
        chains.create("aries")
        meta = json.loads((tmp / "chains" / "aries" / "chain.json").read_text())
        self.assertNotIn("identifier_prefix", meta)

    def test_invalid_prefix_rejected_at_creation(self):
        chains, _ = _isolated_chains()
        bad = [
            "AB",                # too short (even after normalize)
            "x" * 17,            # too long
            "-leading-dash",     # starts with dash
            "trailing-",         # ends with dash
            "has spaces",        # space inside
            "has.period",        # period not allowed
        ]
        for i, prefix in enumerate(bad):
            with self.assertRaises(ValueError, msg=f"prefix {prefix!r} should be rejected"):
                chains.create(f"chain-{i}", identifier_prefix=prefix)

    def test_case_is_preserved(self):
        """IA preserves identifier case (MeMeMaGe-XXXX and mememage-XXXX
        route to different items, verified empirically). Let creators
        keep their casing for expression — only whitespace is stripped."""
        chains, _ = _isolated_chains()
        chains.create("alpha", identifier_prefix="MeMeMaGe")
        self.assertEqual(chains.get_identifier_prefix("alpha"), "MeMeMaGe")
        chains.create("beta", identifier_prefix="Phoenix")
        self.assertEqual(chains.get_identifier_prefix("beta"), "Phoenix")
        chains.create("gamma", identifier_prefix="  spaced  ")
        self.assertEqual(chains.get_identifier_prefix("gamma"), "spaced")

    def test_starts_with_digit_rejected(self):
        chains, _ = _isolated_chains()
        with self.assertRaises(ValueError):
            chains.create("aries", identifier_prefix="1abc")

    def test_valid_prefixes_accepted(self):
        chains, _ = _isolated_chains()
        good = ["abc", "phoenix", "andy-chain", "my_chain", "abc12"]
        for i, prefix in enumerate(good):
            cid = f"chain{i}"
            chains.create(cid, identifier_prefix=prefix)
            self.assertEqual(chains.get_identifier_prefix(cid), prefix)

    def test_prefix_cap_is_10_for_512px_floor(self):
        """The 10-char cap is load-bearing: a 512x512 bar holds 44 bytes and
        payload = prefix_len + 34, so prefix_len must be <= 10 for every chain
        to mint down to 512px. 11+ chars would silently lock out small images,
        so creation must reject them at the boundary."""
        chains, _ = _isolated_chains()
        chains.create("ten", identifier_prefix="tenchars10")   # exactly 10 -> ok
        self.assertEqual(chains.get_identifier_prefix("ten"), "tenchars10")
        with self.assertRaises(ValueError):
            chains.create("eleven", identifier_prefix="elevenchars")  # 11 -> reject

    def test_get_falls_back_on_malformed_stored_value(self):
        """If chain.json somehow contains a corrupt prefix (manual
        edit, version skew), reader returns the default rather than
        propagating garbage into the identifier."""
        chains, tmp = _isolated_chains()
        chains.create("aries")
        chain_json = tmp / "chains" / "aries" / "chain.json"
        meta = json.loads(chain_json.read_text())
        meta["identifier_prefix"] = "BAD!!"
        chain_json.write_text(json.dumps(meta))
        self.assertEqual(chains.get_identifier_prefix("aries"), "mememage")

    def test_save_preserves_prefix(self):
        """chain_config.save() must NOT overwrite a chain's locked
        identifier_prefix — even if the caller's cfg lacks the field
        (which it always does today, since extras only catches it
        on round-trip, not on a freshly constructed ChainConfig).
        """
        chains, tmp = _isolated_chains()
        from mememage import chain_config

        # Force chain_config to look at our temp root.
        chains.create("phoenix", identifier_prefix="phoenix")
        with patch("mememage.chains.CHAINS_ROOT", chains.CHAINS_ROOT):
            cfg = chain_config.ChainConfig.default(
                chain_id="phoenix", chain_name="Phoenix",
                visibility="light_energy",
            )
            chain_config.save(cfg, chain_id="phoenix")

        meta = json.loads((tmp / "chains" / "phoenix" / "chain.json").read_text())
        self.assertEqual(meta["identifier_prefix"], "phoenix",
                         "save() must preserve the locked prefix on disk")

    def test_set_password_preserves_prefix(self):
        chains, tmp = _isolated_chains()
        chains.create("phoenix", visibility="dark_matter", identifier_prefix="phoenix")
        chains.set_password("phoenix", "secret")
        meta = json.loads((tmp / "chains" / "phoenix" / "chain.json").read_text())
        self.assertEqual(meta["identifier_prefix"], "phoenix")
        self.assertIn("password_verifier", meta)

    def test_rename_preserves_prefix(self):
        chains, tmp = _isolated_chains()
        chains.create("phoenix", identifier_prefix="phoenix", name="Old")
        chains.rename("phoenix", "New Name")
        meta = json.loads((tmp / "chains" / "phoenix" / "chain.json").read_text())
        self.assertEqual(meta["identifier_prefix"], "phoenix")
        self.assertEqual(meta["name"], "New Name")

    def test_compute_identifier_uses_prefix(self):
        from mememage.core import compute_identifier
        ident = compute_identifier({"prompt": "x"}, "2026-01-01T00:00:00Z",
                                   prefix="phoenix")
        self.assertTrue(ident.startswith("phoenix-"))
        # 16-hex suffix
        self.assertEqual(len(ident), len("phoenix-") + 16)

    def test_genesis_identifier_rolls_random_hex_under_prefix(self):
        # Genesis no longer occupies the fixed zeros slot — it rolls a
        # random 16-hex suffix under the chain's prefix, same shape as
        # every other identifier. Prefix is preserved (case included);
        # the suffix is random (two rolls differ) and never the zeros.
        from mememage.core import genesis_identifier
        for prefix in ("mememage", "phoenix", "abc", "MeMeMaGe"):
            ident = genesis_identifier(prefix)
            self.assertRegex(ident, r"^" + prefix + r"-[0-9a-f]{16}$")
            self.assertNotEqual(ident, prefix + "-0000000000000000")
        self.assertNotEqual(genesis_identifier("mememage"),
                            genesis_identifier("mememage"))


class TestGenesisPin(unittest.TestCase):
    """A chain can pin its genesis to a fixed slot (genesis_identifier in
    chain.json) — used to reclaim a recovered namespace like
    mememage-0000000000000000. Validated, locked-once, tolerant on read."""

    def test_validate_accepts_canonical_and_zeros(self):
        from mememage import chains
        chains.validate_genesis_identifier("mememage-0000000000000000", "mememage")
        chains.validate_genesis_identifier("phoenix-47f11bad5dcc9ad2", "phoenix")

    def test_validate_rejects_bad_shapes(self):
        from mememage import chains
        bad = [
            ("mememage-000000000000000", "mememage"),    # 15 hex (too short)
            ("mememage-00000000000000000", "mememage"),  # 17 hex (too long)
            ("mememage-zzzzzzzzzzzzzzzz", "mememage"),    # non-hex
            ("mememage-ABCDEF0123456789", "mememage"),    # upper-hex not allowed
            ("phoenix-0000000000000000", "mememage"),     # prefix mismatch
            ("0000000000000000", "mememage"),             # no prefix/sep
        ]
        for ident, prefix in bad:
            with self.assertRaises(ValueError, msg=f"{ident!r} should be rejected"):
                chains.validate_genesis_identifier(ident, prefix)

    def test_get_returns_pin_when_set(self):
        chains, tmp = _isolated_chains()
        chains.create("aries")
        chain_json = tmp / "chains" / "aries" / "chain.json"
        meta = json.loads(chain_json.read_text())
        meta["genesis_identifier"] = "mememage-0000000000000000"
        chain_json.write_text(json.dumps(meta))
        self.assertEqual(chains.get_genesis_identifier("aries"),
                         "mememage-0000000000000000")

    def test_get_none_when_unset(self):
        chains, _ = _isolated_chains()
        chains.create("aries")
        self.assertIsNone(chains.get_genesis_identifier("aries"))

    def test_get_falls_back_to_none_on_prefix_mismatch(self):
        # Pin's prefix must match the chain's prefix, else it's ignored
        # (genesis falls back to the random roll, not a malformed mint).
        chains, tmp = _isolated_chains()
        chains.create("phoenix", identifier_prefix="phoenix")
        chain_json = tmp / "chains" / "phoenix" / "chain.json"
        meta = json.loads(chain_json.read_text())
        meta["genesis_identifier"] = "mememage-0000000000000000"  # wrong prefix
        chain_json.write_text(json.dumps(meta))
        self.assertIsNone(chains.get_genesis_identifier("phoenix"))

    def test_save_preserves_pin(self):
        # chain_config.save() (a payload Apply) must not wipe the locked pin.
        chains, tmp = _isolated_chains()
        from mememage import chain_config
        chains.create("aries")
        chain_json = tmp / "chains" / "aries" / "chain.json"
        meta = json.loads(chain_json.read_text())
        meta["genesis_identifier"] = "mememage-0000000000000000"
        chain_json.write_text(json.dumps(meta))
        with patch("mememage.chains.CHAINS_ROOT", chains.CHAINS_ROOT):
            cfg = chain_config.ChainConfig.default(
                chain_id="aries", chain_name="Aries", visibility="light_energy",
            )
            chain_config.save(cfg, chain_id="aries")
        meta2 = json.loads(chain_json.read_text())
        self.assertEqual(meta2["genesis_identifier"], "mememage-0000000000000000",
                         "save() must preserve the locked genesis pin on disk")


class TestResetToGenesis(unittest.TestCase):
    """reset_state(to_genesis=True) severs the lineage thread so the next
    mint is a genesis (parent_id null) — required for a pinned genesis to
    actually land on its slot. Plain reset_state leaves lineage intact."""

    def _seed_lineage(self, tmp, chain_id, last_id):
        import sqlite3
        db = tmp / "chains" / chain_id / "mememage.db"
        with sqlite3.connect(str(db)) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS lineage "
                         "(id INTEGER PRIMARY KEY CHECK (id=1), last_archive_id TEXT NOT NULL)")
            conn.execute("INSERT OR REPLACE INTO lineage (id, last_archive_id) VALUES (1, ?)",
                         (last_id,))
            conn.commit()

    def _read_lineage(self, tmp, chain_id):
        import sqlite3
        db = tmp / "chains" / chain_id / "mememage.db"
        if not db.exists():
            return None
        with sqlite3.connect(str(db)) as conn:
            row = conn.execute("SELECT last_archive_id FROM lineage WHERE id=1").fetchone()
            return row[0] if row else None

    def test_to_genesis_clears_lineage(self):
        chains, tmp = _isolated_chains()
        chains.create("aries")
        self._seed_lineage(tmp, "aries", "mememage-aa8194d91f1da238")
        out = chains.reset_state("aries", to_genesis=True)
        self.assertEqual(out["lineage_cleared"], "mememage-aa8194d91f1da238")
        self.assertIsNone(self._read_lineage(tmp, "aries"),
                          "next mint must see no parent → genesis")

    def test_default_reset_preserves_lineage(self):
        chains, tmp = _isolated_chains()
        chains.create("aries")
        self._seed_lineage(tmp, "aries", "mememage-aa8194d91f1da238")
        out = chains.reset_state("aries")  # to_genesis defaults False
        self.assertIsNone(out["lineage_cleared"])
        self.assertEqual(self._read_lineage(tmp, "aries"), "mememage-aa8194d91f1da238",
                         "plain reset must NOT touch the lineage thread")

    def test_clear_lineage_returns_none_when_absent(self):
        chains, _ = _isolated_chains()
        chains.create("aries")
        from mememage import lineage
        self.assertIsNone(lineage.clear_lineage("aries"))


if __name__ == "__main__":
    unittest.main()
