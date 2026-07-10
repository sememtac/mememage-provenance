"""Keep the test suite off the operator's real chain.

Several tests write chunk_state / chain config through ``mememage.chains``.
Most patch the roots themselves, but a miss is silent and destructive: the
suite then rewrites ``~/.mememage/chains/<chain>/chunk_state.json`` on the
developer's machine. That is exactly how the canonical chain's star counter
came to read 100 while only 33 stars had ever been conceived — a test fixture
leaked its ``outer_position: 100`` into the live chain, and every later mint
inherited the lie.

The counter is not cosmetic: it stamps ``outer_position``, the constellation
index (Bayer letter), and the decoder/truth chunk indices into every soul. A
polluted counter writes those wrong numbers into permanent, published records.

So: redirect BOTH chain roots to a throwaway directory for the whole session.
``MEMEMAGE_ROOT`` is read at call time by some helpers, while ``CHAINS_ROOT``
is a module-level constant bound at import — patching one without the other
still points at the real chain, so both are patched here.

Tests that want their own root still patch on top of this; they just no longer
*have* to for safety.
"""
import os
import tempfile
from pathlib import Path

import pytest

from mememage import chains

_REAL_ROOT = chains.MEMEMAGE_ROOT


@pytest.fixture(scope="session", autouse=True)
def _isolate_mememage_root():
    with tempfile.TemporaryDirectory(prefix="mememage-tests-") as tmp:
        root = Path(tmp)
        (root / "received").mkdir(parents=True, exist_ok=True)
        real_root, real_chains = chains.MEMEMAGE_ROOT, chains.CHAINS_ROOT
        chains.MEMEMAGE_ROOT = root
        chains.CHAINS_ROOT = root / "chains"
        try:
            yield root
        finally:
            chains.MEMEMAGE_ROOT = real_root
            chains.CHAINS_ROOT = real_chains


@pytest.fixture(scope="session", autouse=True)
def _guard_real_chain_untouched(_isolate_mememage_root):
    """Fail loudly if the real chain state is modified during the run.

    A backstop for the redirect above: if some code path resolves the real
    path anyway (a cached constant, an absolute path, an env var), we want a
    red test, not a quietly corrupted chain.
    """
    state = _REAL_ROOT / "chains" / "mememage" / "chunk_state.json"
    before = state.read_bytes() if state.exists() else None
    yield
    after = state.read_bytes() if state.exists() else None
    if before != after:
        pytest.fail(
            f"THE TEST SUITE MODIFIED THE REAL CHAIN STATE at {state}.\n"
            "Some test resolved the real chunk_state path despite the session "
            "root redirect. Find it and patch its roots — a polluted counter "
            "stamps wrong outer_position / constellation_index / chunk indices "
            "into permanently published souls."
        )
