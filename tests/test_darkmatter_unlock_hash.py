"""Regression guard for the dark-matter unlock hash bug.

On a dark_matter chain the stored content_hash is computed AFTER encryption,
so it covers the SEALED SHELL (encrypted blobs + public fields), not the
plaintext. The validator's `maybeUnlockRecord` merges decrypted plaintext
back for DISPLAY; the WITNESSED hash recompute must keep running over the
as-stored sealed shell or it falsely reports "Hash Mismatch" after unlock.

Two layers of defense here:
  1. test_node_repro — runs tests/darkmatter_unlock_hash_repro.cjs, which
     loads the real docs/js/verify.js hashing engine and asserts the merged
     record mismatches the stored hash while the sealed-shell path matches.
     Skipped where node is unavailable.
  2. test_validator_wires_sealed_shell — node-free static guard that
     validator.js defines `_sealedShellFor`, stamps `_sealedOriginal` in
     `maybeUnlockRecord`, and routes all three hash-recompute sites through
     the helper. Catches a regression that drops the wiring even in CI
     without a browser/node.
"""

import os
import re
import shutil
import subprocess

import pytest

HERE = os.path.dirname(__file__)
REPO = os.path.join(HERE, "..")
REPRO = os.path.join(HERE, "darkmatter_unlock_hash_repro.cjs")
VALIDATOR_JS = os.path.join(REPO, "docs", "js", "validator.js")

_NODE = shutil.which("node")


@pytest.mark.skipif(_NODE is None, reason="node not installed")
def test_node_repro():
    """The real verify.js engine: merged hash mismatches, sealed-shell matches."""
    result = subprocess.run(
        [_NODE, REPRO],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        "node repro failed:\nstdout:\n" + result.stdout + "\nstderr:\n" + result.stderr
    )
    assert "ALL PASS" in result.stdout, result.stdout


def test_validator_wires_sealed_shell():
    """Static guard: the unlock-hash fix is wired at all the load-bearing sites."""
    with open(VALIDATOR_JS, encoding="utf-8") as fh:
        src = fh.read()

    # Helper exists.
    assert "function _sealedShellFor(rec)" in src, "_sealedShellFor helper missing"

    # maybeUnlockRecord stamps the sealed original onto the merged copy.
    assert "unlocked._sealedOriginal = record" in src, (
        "maybeUnlockRecord must stamp _sealedOriginal on the unlocked copy"
    )

    # All recompute sites route their hash input through the sealed shell —
    # either a `_shellX = _sealedShellFor(...)` local (witnessed + observatory)
    # or the inline `computeContentHash(_sealedShellFor(...))` (the Audit tab,
    # which now defers to the canonical hash so it's open/hash_version-aware).
    shell_inputs = (re.findall(r"_shell[A-Z]\s*=\s*_sealedShellFor\(", src)
                    + re.findall(r"computeContentHash\(_sealedShellFor\(", src))
    assert len(shell_inputs) >= 3, (
        "expected >=3 _sealedShellFor()-derived hash inputs (the recompute "
        "sites); found %d: %r" % (len(shell_inputs), shell_inputs)
    )

    # And no recompute site hashes the raw merged record via _hashSetForRecord
    # on a bare `r`/`rec` instead of the shell. Every _hashSetForRecord call
    # should take a _shell* argument.
    raw_calls = re.findall(r"_hashSetForRecord\((?!_shell)", src)
    assert not raw_calls, (
        "a _hashSetForRecord call does not run over a _sealedShellFor() shell: %r"
        % (raw_calls,)
    )
