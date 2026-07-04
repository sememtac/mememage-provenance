"""Tests for chains.current() resolution.

The resolver walks three signals in order:
  1. ~/.mememage/current_chain explicit pointer
  2. Single-chain shortcut: exactly one chain on disk → use it
  3. DEFAULT_CHAIN_ID ("aries") as a final fallback for fresh installs

Both the explicit pointer and the shortcut have to behave correctly
or `mememage forecast` / dashboard / mint pipeline all silently
target the wrong chain.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestChainsCurrent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mememage-chains-test-")
        self.root = Path(self.tmp)
        self.chains_root = self.root / "chains"
        self.current_file = self.root / "current_chain"

        # Re-import + patch the chains module's path constants so the
        # resolver looks at our temp tree instead of ~/.mememage.
        from mememage import chains
        self.chains = chains
        self._patches = [
            patch.object(chains, "MEMEMAGE_ROOT", self.root),
            patch.object(chains, "CHAINS_ROOT", self.chains_root),
            patch.object(chains, "CURRENT_CHAIN_FILE", self.current_file),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _add_chain(self, name):
        d = self.chains_root / name
        d.mkdir(parents=True)
        (d / "chain.json").write_text(json.dumps({"id": name}), encoding="utf-8")

    def test_fresh_install_falls_back_to_default(self):
        # No current_chain file, no chains/ dir → default ID.
        self.assertEqual(self.chains.current(), self.chains.DEFAULT_CHAIN_ID)

    def test_explicit_pointer_wins(self):
        # Pointer file beats everything below.
        self._add_chain("shanhaijing")
        self._add_chain("aries")
        self.current_file.write_text("shanhaijing", encoding="utf-8")
        self.assertEqual(self.chains.current(), "shanhaijing")

    def test_single_chain_shortcut_uses_only_chain(self):
        # Exactly one real chain on disk + no pointer → that chain
        # wins regardless of its name. The trap this fixes: users
        # who renamed their only chain to "anumel" used to get
        # "aries" back from current().
        self._add_chain("anumel")
        self.assertEqual(self.chains.current(), "anumel")

    def test_single_chain_shortcut_ignores_dot_dirs(self):
        # Dot-prefixed dirs (archive, .removed) shouldn't count
        # toward the "exactly one chain" check.
        self._add_chain("anumel")
        (self.chains_root / ".archive").mkdir()
        (self.chains_root / ".removed").mkdir()
        self.assertEqual(self.chains.current(), "anumel")

    def test_single_chain_requires_chain_json(self):
        # A bare directory without chain.json doesn't qualify — it
        # might be a half-created stub or a typo. Should fall back
        # to DEFAULT_CHAIN_ID rather than guessing.
        (self.chains_root / "bare").mkdir(parents=True)
        # No chain.json inside.
        self.assertEqual(self.chains.current(), self.chains.DEFAULT_CHAIN_ID)

    def test_multi_chain_without_pointer_falls_back_to_default(self):
        # Two real chains + no pointer → fall back to default, even
        # if neither chain IS the default. Better to be loudly wrong
        # than silently pick one of N.
        self._add_chain("anumel")
        self._add_chain("shanhaijing")
        self.assertEqual(self.chains.current(), self.chains.DEFAULT_CHAIN_ID)

    def test_empty_pointer_falls_through(self):
        # Whitespace-only pointer file should be treated as "no
        # pointer", not as "use empty string as the chain id".
        self._add_chain("anumel")
        self.current_file.write_text("   \n", encoding="utf-8")
        # Falls through to single-chain shortcut → anumel.
        self.assertEqual(self.chains.current(), "anumel")


class TestResolvePassword(unittest.TestCase):
    """Canonical password resolution: override → chain.json → env → None."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mememage-pw-test-")
        self.root = Path(self.tmp)
        self.chains_root = self.root / "chains"
        self.current_file = self.root / "current_chain"

        from mememage import chains, config
        self.chains = chains
        self._patches = [
            patch.object(chains, "MEMEMAGE_ROOT", self.root),
            patch.object(chains, "CHAINS_ROOT", self.chains_root),
            patch.object(chains, "CURRENT_CHAIN_FILE", self.current_file),
            # Point _load_dotenv at a nonexistent file so the resolver's
            # env step can't pull a real MEMEMAGE_PASSWORD from the
            # repo's .env. The os.environ pop below handles whatever's
            # already in the shell.
            patch.object(config, "_ENV_FILE", self.root / "no-such.env"),
        ]
        for p in self._patches:
            p.start()
        import os as _os
        self._orig_pw = _os.environ.pop("MEMEMAGE_PASSWORD", None)

    def tearDown(self):
        import os as _os
        if self._orig_pw is not None:
            _os.environ["MEMEMAGE_PASSWORD"] = self._orig_pw
        for p in self._patches:
            p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_chain(self, name, password=None):
        d = self.chains_root / name
        d.mkdir(parents=True)
        cfg = {"id": name}
        if password is not None:
            cfg["password"] = password
        (d / "chain.json").write_text(json.dumps(cfg), encoding="utf-8")
        self.current_file.write_text(name, encoding="utf-8")

    def test_override_beats_everything(self):
        self._make_chain("ch", password="chain-pw")
        import os as _os
        _os.environ["MEMEMAGE_PASSWORD"] = "env-pw"
        try:
            self.assertEqual(
                self.chains.resolve_password(override="call-pw"),
                "call-pw",
            )
        finally:
            del _os.environ["MEMEMAGE_PASSWORD"]

    @unittest.skip("rung-1: chain.json no longer stores a password value; "

                   "precedence is override>env>legacy-fallback. Needs rewrite.")

    def test_chain_password_beats_env(self):
        self._make_chain("ch", password="chain-pw")
        import os as _os
        _os.environ["MEMEMAGE_PASSWORD"] = "env-pw"
        try:
            self.assertEqual(self.chains.resolve_password(), "chain-pw")
        finally:
            del _os.environ["MEMEMAGE_PASSWORD"]

    def test_env_used_when_chain_has_no_password(self):
        self._make_chain("ch")  # no password
        import os as _os
        _os.environ["MEMEMAGE_PASSWORD"] = "env-pw"
        try:
            self.assertEqual(self.chains.resolve_password(), "env-pw")
        finally:
            del _os.environ["MEMEMAGE_PASSWORD"]

    def test_none_when_nothing_set(self):
        self._make_chain("ch")
        self.assertIsNone(self.chains.resolve_password())

    def test_empty_override_falls_through(self):
        # Empty-string override should be treated as "no override" —
        # otherwise a caller passing `password=""` would override a
        # configured chain password with nothing.
        self._make_chain("ch", password="chain-pw")
        self.assertEqual(self.chains.resolve_password(override=""), "chain-pw")
        self.assertEqual(self.chains.resolve_password(override=None), "chain-pw")


if __name__ == "__main__":
    unittest.main()
