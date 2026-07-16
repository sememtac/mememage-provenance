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
still points at the real chain, so both are patched here. ``profiles`` has the
SAME import-bound roots (``ROOT`` / ``PROFILES_DIR`` / ``ACTIVE_FILE``), and a
missed profile redirect leaks the operator's real channel config — which is
how the test suite came to blast test souls to the live VPS: an unmocked
``upload_metadata`` read the real ``mememage.art`` http_push channel and PUT to
``mint.mememage.art`` for real, once per suite run.

A file guard can't see a NETWORK leak (the blast leaves no local trace), so
this also installs a socket guard that turns any test connection to the
operator's live hosts into a loud failure — covering the whole class, not just
the two modules we happen to know about today.

Tests that want their own root still patch on top of this; they just no longer
*have* to for safety.
"""
import os
import socket
import tempfile
from pathlib import Path

import pytest

from mememage import chains, profiles

_REAL_ROOT = chains.MEMEMAGE_ROOT

# Real infrastructure a unit test must NEVER touch. Substring match on the
# connect host, so subdomains (souls./mint.) and the bare IP are all covered.
_FORBIDDEN_HOSTS = ("mememage.art", "160.153.182.117")


@pytest.fixture(scope="session", autouse=True)
def _isolate_mememage_root():
    with tempfile.TemporaryDirectory(prefix="mememage-tests-") as tmp:
        root = Path(tmp)
        (root / "received").mkdir(parents=True, exist_ok=True)
        (root / "profiles").mkdir(parents=True, exist_ok=True)
        saved = {
            (chains, "MEMEMAGE_ROOT"): chains.MEMEMAGE_ROOT,
            (chains, "CHAINS_ROOT"): chains.CHAINS_ROOT,
            (profiles, "ROOT"): profiles.ROOT,
            (profiles, "PROFILES_DIR"): profiles.PROFILES_DIR,
            (profiles, "ACTIVE_FILE"): profiles.ACTIVE_FILE,
        }
        chains.MEMEMAGE_ROOT = root
        chains.CHAINS_ROOT = root / "chains"
        # profiles' roots are import-bound constants too; a temp profile tree
        # means load_channels() sees no real http_push channel, so nothing
        # blasts anywhere.
        profiles.ROOT = root
        profiles.PROFILES_DIR = root / "profiles"
        profiles.ACTIVE_FILE = root / "active_profile"
        try:
            yield root
        finally:
            for (mod, attr), val in saved.items():
                setattr(mod, attr, val)


@pytest.fixture(scope="session", autouse=True)
def _block_real_infra():
    """Fail loudly if a test opens a socket to the operator's live hosts.

    The chain-state guard below catches local-file pollution; this catches the
    network kind. Only the operator's own infrastructure is blocked — a test
    that legitimately mocks its HTTP still runs; a test that forgot to mock and
    would PUT to the real VPS raises here instead of silently polluting it.
    """
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo

    def _check(host):
        h = str(host).lower()
        for bad in _FORBIDDEN_HOSTS:
            if bad in h:
                raise AssertionError(
                    f"Test tried to reach live Mememage infrastructure ({host}). "
                    "An unmocked upload/blast is talking to the real VPS. Mock "
                    "the channel (patch channels.blast / the http_push upload) "
                    "or redirect the profile — do not PUT to production in a test."
                )

    def guarded_connect(self, address):
        if isinstance(address, tuple) and address:
            _check(address[0])
        return real_connect(self, address)

    def guarded_getaddrinfo(host, *args, **kwargs):
        _check(host)
        return real_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = guarded_connect
    socket.getaddrinfo = guarded_getaddrinfo
    try:
        yield
    finally:
        socket.socket.connect = real_connect
        socket.getaddrinfo = real_getaddrinfo


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
