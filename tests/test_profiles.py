"""Tests for the multi-key profile module.

The module is the foundation of multi-key support — every other layer
(server endpoints, dashboard UI, CLI subcommands) leans on these
behaviors, so the unit tests are intentionally thorough about
migration, isolation, and the lifecycle round-trips.

All tests are filesystem-isolated: each one redirects ``profiles.ROOT``
into a tmp directory so we never touch the user's real
``~/.mememage/``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---- Fixtures ------------------------------------------------------------


@pytest.fixture
def isolated_root(tmp_path, monkeypatch):
    """Redirect all profile path resolution into a tmp dir for the test.

    Patches the module-level constants on both ``profiles`` and
    ``signing`` so the auto-migration logic, signing keygen, and the
    PEP-562 lazy attributes all point at the same fake root.
    """
    fake_root = tmp_path / "dot-mememage"
    fake_root.mkdir()
    from mememage import profiles, signing
    monkeypatch.setattr(profiles, "ROOT", fake_root)
    monkeypatch.setattr(profiles, "PROFILES_DIR", fake_root / "profiles")
    monkeypatch.setattr(profiles, "ACTIVE_FILE", fake_root / "active_profile")
    monkeypatch.setattr(signing, "KEY_DIR", fake_root)
    yield fake_root


# ---- Auto-migration -----------------------------------------------------


def test_migration_moves_legacy_files(isolated_root):
    """First call to any profile resolver should move legacy files
    from the flat layout into profiles/default/ atomically."""
    from mememage import profiles

    # Stage legacy state mimicking pre-upgrade ~/.mememage/ contents.
    (isolated_root / "private.key").write_bytes(b"FAKE_PRIVATE")
    (isolated_root / "public.key").write_text("FAKE_PUBLIC_HEX")
    (isolated_root / "creator.txt").write_text("Test User")
    (isolated_root / "revocation.cert").write_text("{}")
    (isolated_root / "keychain").mkdir()
    (isolated_root / "keychain" / "old.key").write_bytes(b"OLD")

    # Trigger migration by asking for active id.
    assert profiles.active_id() == "default"

    default = isolated_root / "profiles" / "default"
    assert default.is_dir()
    assert (default / "private.key").read_bytes() == b"FAKE_PRIVATE"
    assert (default / "public.key").read_text() == "FAKE_PUBLIC_HEX"
    assert (default / "creator.txt").read_text() == "Test User"
    assert (default / "keychain" / "old.key").read_bytes() == b"OLD"

    # Legacy files are MOVED, not copied — no leftovers.
    assert not (isolated_root / "private.key").exists()
    assert not (isolated_root / "keychain").exists()

    # active_profile pointer is stamped.
    assert (isolated_root / "active_profile").read_text().strip() == "default"


def test_migration_is_idempotent(isolated_root):
    """Calling profile resolvers multiple times must not re-migrate or
    clobber existing profile files."""
    from mememage import profiles

    (isolated_root / "private.key").write_bytes(b"FAKE")
    profiles.active_id()  # first call migrates
    profiles.active_id()  # second call: should be a no-op

    default = isolated_root / "profiles" / "default"
    assert (default / "private.key").read_bytes() == b"FAKE"
    assert not (isolated_root / "private.key").exists()


def test_no_migration_when_already_migrated(isolated_root):
    """If profiles/ already exists, leave any stray legacy files alone
    (the user may have intentional copies — we don't second-guess)."""
    from mememage import profiles

    (isolated_root / "profiles" / "default").mkdir(parents=True)
    (isolated_root / "profiles" / "default" / "private.key").write_bytes(b"ACTIVE")
    # A stray legacy file should NOT get clobbered or migrated.
    (isolated_root / "private.key").write_bytes(b"LEGACY")

    profiles.active_id()

    assert (isolated_root / "private.key").read_bytes() == b"LEGACY"
    assert (isolated_root / "profiles" / "default" / "private.key").read_bytes() == b"ACTIVE"


# ---- Path resolution ----------------------------------------------------


def test_path_helpers_target_active_profile(isolated_root):
    """The five path helpers must resolve to the active profile's
    files, not the legacy root."""
    from mememage import profiles

    paths = [
        profiles.private_key_path(),
        profiles.public_key_path(),
        profiles.creator_path(),
        profiles.revocation_path(),
        profiles.keychain_dir(),
    ]
    expected_root = isolated_root / "profiles" / "default"
    for p in paths:
        assert p.parent == expected_root, f"{p} should be under {expected_root}"


def test_signing_legacy_attrs_resolve_through_active_profile(isolated_root):
    """``signing.PRIVATE_KEY_PATH`` etc. must keep working via PEP 562
    so external callers (server.py, decoder JS) don't break."""
    from mememage import signing, profiles

    expected = profiles.private_key_path()
    assert signing.PRIVATE_KEY_PATH == expected
    assert signing.PUBLIC_KEY_PATH == profiles.public_key_path()
    assert signing.CREATOR_PATH == profiles.creator_path()
    assert signing.REVOCATION_PATH == profiles.revocation_path()
    assert signing.KEYCHAIN_DIR == profiles.keychain_dir()


def test_signing_legacy_attrs_follow_active_switch(isolated_root):
    """After switch_active, signing.PRIVATE_KEY_PATH should resolve to
    the NEW profile's file. This is the core invariant of the
    multi-profile design: existing callers transparently track the
    active key."""
    from mememage import profiles, signing
    if not signing.is_signing_available():
        pytest.skip("cryptography not installed")

    profiles.create("default", name="Primary")
    profiles.create("scratch", name="Scratch")
    profiles.set_active("scratch")

    expected = isolated_root / "profiles" / "scratch" / "private.key"
    assert signing.PRIVATE_KEY_PATH == expected


# ---- Profile lifecycle ---------------------------------------------------


def test_create_generates_independent_keys(isolated_root):
    """Two newly-created profiles must have independent keys — no
    shared seed, no shared fingerprint. ``create`` activates the
    new profile as a side effect (keygen() runs against the active
    profile's dir), which is exactly the behavior we want."""
    from mememage import profiles, signing
    if not signing.is_signing_available():
        pytest.skip("cryptography not installed")

    a = profiles.create("alpha", name="Alpha")
    b = profiles.create("bravo", name="Bravo")

    assert a["fingerprint"] != b["fingerprint"], "Two profiles must have distinct fingerprints"
    assert a["public_key"] != b["public_key"]


def test_list_profiles_reports_active_marker(isolated_root):
    from mememage import profiles, signing
    if not signing.is_signing_available():
        pytest.skip("cryptography not installed")

    profiles.create("one", name="One")
    profiles.create("two", name="Two")
    profiles.set_active("two")

    rows = profiles.list_profiles()
    by_id = {r["id"]: r for r in rows}
    assert by_id["two"]["is_active"] is True
    assert by_id["one"]["is_active"] is False
    assert by_id["one"]["name"] == "One"


def test_set_active_rejects_missing_profile(isolated_root):
    from mememage import profiles
    with pytest.raises(FileNotFoundError):
        profiles.set_active("does-not-exist")


def test_remove_archives_profile(isolated_root):
    from mememage import profiles, signing
    if not signing.is_signing_available():
        pytest.skip("cryptography not installed")

    # Create two profiles; first becomes active automatically. We need
    # to remove a NON-active profile so switch before the remove call.
    profiles.create("removable", name="X")
    profiles.create("keeper", name="K")  # become active
    assert profiles.active_id() == "keeper"

    res = profiles.remove("removable")
    assert "archived" in res
    # Originals gone, archive present, ready for re-import.
    assert not (isolated_root / "profiles" / "removable").exists()
    archive = Path(res["archived"])
    assert archive.exists()
    assert (archive / "private.key").exists()


def test_remove_refuses_active(isolated_root):
    from mememage import profiles, signing
    if not signing.is_signing_available():
        pytest.skip("cryptography not installed")

    profiles.create("only", name="Only")
    profiles.set_active("only")
    with pytest.raises(ValueError):
        profiles.remove("only")


def test_invalid_profile_id_rejected(isolated_root):
    from mememage import profiles
    for bad in ("UPPERCASE", "has space", "path/traversal", "", "a" * 41):
        with pytest.raises(ValueError):
            profiles.create(bad)


# ---- Import existing key -------------------------------------------------


def test_import_pem_ed25519_key(isolated_root):
    """A standard openssl-style PEM private key should import cleanly
    and produce a profile equivalent to a freshly-keygen'd one."""
    from mememage import profiles, signing
    if not signing.is_signing_available():
        pytest.skip("cryptography not installed")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization

    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    info = profiles.import_key("imported", "Imported User", pem)
    assert info["fingerprint"] is not None
    prof = isolated_root / "profiles" / "imported"
    assert (prof / "private.key").exists()
    assert (prof / "public.key").exists()
    assert (prof / "creator.txt").read_text() == "Imported User"
    # Imported key also gets a pre-signed revocation cert.
    rev = json.loads((prof / "revocation.cert").read_text())
    assert rev["key_fingerprint"] == info["fingerprint"]
    assert "signature" in rev


def test_import_rejects_non_ed25519(isolated_root):
    from mememage import profiles, signing
    if not signing.is_signing_available():
        pytest.skip("cryptography not installed")
    # Random bytes are not a valid PEM.
    with pytest.raises(ValueError):
        profiles.import_key("trash", "Trash", b"-----BEGIN GARBAGE-----\nnotbase64\n-----END GARBAGE-----")


# ---- Aliases -------------------------------------------------------------


def test_sign_alias_produces_verifiable_record(isolated_root):
    """Aliasing creates a signed record naming another profile. The
    signature must verify against the active profile's public key."""
    from mememage import profiles, signing
    if not signing.is_signing_available():
        pytest.skip("cryptography not installed")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    profiles.create("primary", name="Primary")
    profiles.create("sidecar", name="Sidecar")
    profiles.set_active("primary")  # primary is active, signs alias to sidecar

    record = profiles.sign_alias("sidecar")
    assert record["action"] == "alias"
    assert "signature" in record
    assert record["alias_fingerprint"] != record["signer_fingerprint"]

    # The signature must verify against the active profile's public key.
    pub_hex = record["signer_public_key"]
    pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
    # Reconstruct the signed message: everything except 'signature',
    # canonical-JSON encoded.
    msg_dict = {k: v for k, v in record.items() if k != "signature"}
    msg = json.dumps(msg_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")
    pub.verify(bytes.fromhex(record["signature"]), msg)


def test_sign_alias_refuses_self(isolated_root):
    from mememage import profiles, signing
    if not signing.is_signing_available():
        pytest.skip("cryptography not installed")
    profiles.create("solo", name="Solo")
    profiles.set_active("solo")
    with pytest.raises(ValueError):
        profiles.sign_alias("solo")
