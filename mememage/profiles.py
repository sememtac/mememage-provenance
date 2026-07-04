"""Multi-key profiles — one human, many keys.

Each profile is an independent Ed25519 identity living in its own
subdirectory under ``~/.mememage/profiles/<id>/``. One profile at a
time is "active" — that's the identity whose key signs the next mint,
the rotation/revocation operations target, and the dashboard's
Identity section displays.

Profiles are deliberately independent: no shared seed, no shared
fingerprint, no automatic cross-signing. Two profiles become "the
same human" only when one signs an *alias record* naming the other
and that record is published to IA. This mirrors how SSH ``id_ed25519``
keys work — each machine has its own key, the human asserts the link
through public, signed channels.

Why this matters: a user can run the mint server on a remote machine
(VPS, friend's host) with a *scoped* profile whose key never touches
their laptop. If the remote host is compromised, revoking the remote
profile leaves every record signed by the laptop profile clean.

See ``docs/plans/multi-key-profiles.md`` for the full design.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


ROOT = Path("~/.mememage").expanduser()
PROFILES_DIR = ROOT / "profiles"
ACTIVE_FILE = ROOT / "active_profile"
DEFAULT_PROFILE_ID = "default"


# ----- Auto-migration ------------------------------------------------------

# Files we relocate from the old flat layout into profiles/default/ on
# first access. Paths are relative to ``~/.mememage/``; entries can be
# files OR directories — directories migrate via ``shutil.move``.
_LEGACY_FILES = (
    "private.key",
    "public.key",
    "creator.txt",
    "revocation.cert",
)
_LEGACY_DIRS = ("keychain",)


def _legacy_layout_present() -> bool:
    """True if any legacy single-key file/dir exists at ``~/.mememage/``
    AND no profiles directory has been created yet."""
    if PROFILES_DIR.exists():
        return False
    for f in _LEGACY_FILES:
        if (ROOT / f).exists():
            return True
    for d in _LEGACY_DIRS:
        if (ROOT / d).exists():
            return True
    return False


def _migrate_legacy() -> None:
    """Move ``~/.mememage/<file>`` → ``~/.mememage/profiles/default/<file>``
    for every legacy single-key file. Idempotent — safe to call when
    nothing needs migrating.

    Atomicity: each move is a single ``rename`` (within the same
    filesystem) so a partial-migration on crash leaves *some* files in
    the legacy location and *some* in the new — the next import re-runs
    migration and finishes the job. We don't try to be smart about
    cross-FS moves; ``~/.mememage`` is always one FS in practice.
    """
    default = PROFILES_DIR / DEFAULT_PROFILE_ID
    default.mkdir(parents=True, exist_ok=True)
    moved = []
    for f in _LEGACY_FILES:
        src = ROOT / f
        if not src.exists():
            continue
        dst = default / f
        if dst.exists():
            # Both versions exist — the destination wins (means a partial
            # migration ran previously). Leave the legacy file in place
            # rather than overwrite, so we don't lose data we can't see.
            log.warning("Migration: %s already exists at %s; leaving legacy %s untouched",
                        f, dst, src)
            continue
        src.rename(dst)
        moved.append(f)
    for d in _LEGACY_DIRS:
        src = ROOT / d
        if not src.exists():
            continue
        dst = default / d
        if dst.exists():
            log.warning("Migration: %s already exists at %s; leaving legacy %s untouched",
                        d, dst, src)
            continue
        shutil.move(str(src), str(dst))
        moved.append(d + "/")
    if moved:
        log.info("Migrated legacy identity to profile 'default': %s", ", ".join(moved))
    # Stamp the active profile pointer if it doesn't exist yet — the
    # legacy single-key layout always meant "this one identity is
    # active," so default is the right starting point.
    if not ACTIVE_FILE.exists():
        ACTIVE_FILE.write_text(DEFAULT_PROFILE_ID + "\n", encoding="utf-8")


def _ensure_root() -> None:
    """Migrate legacy state if present; ensure PROFILES_DIR exists."""
    if _legacy_layout_present():
        _migrate_legacy()
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)


# ----- Active profile pointer ---------------------------------------------


def active_id() -> str:
    """ID of the currently-active profile.

    Falls back to ``DEFAULT_PROFILE_ID`` if the pointer file is missing
    or empty — this matches the "single-key behavior" pre-migration.
    """
    _ensure_root()
    if not ACTIVE_FILE.exists():
        return DEFAULT_PROFILE_ID
    val = ACTIVE_FILE.read_text(encoding="utf-8").strip()
    return val or DEFAULT_PROFILE_ID


def set_active(profile_id: str) -> None:
    """Change which profile signs the next mint.

    Raises ``FileNotFoundError`` if the named profile doesn't exist on
    disk — we never want to silently activate a missing profile and
    leave the user wondering why signing fails.
    """
    if not (PROFILES_DIR / profile_id).is_dir():
        raise FileNotFoundError(f"Profile not found: {profile_id}")
    ROOT.mkdir(parents=True, exist_ok=True)
    ACTIVE_FILE.write_text(profile_id + "\n", encoding="utf-8")
    log.info("Active profile set to %s", profile_id)


# ----- Profile path resolution --------------------------------------------


def profile_dir(profile_id: Optional[str] = None) -> Path:
    """Directory holding the given profile's files. ``None`` resolves
    to the active profile. Doesn't enforce existence — callers that
    need that should use ``exists()`` on the result."""
    _ensure_root()
    return PROFILES_DIR / (profile_id or active_id())


# These five helpers are the signing-module-facing API. ``signing.py``
# resolves its path constants through these so any caller still using
# ``signing.PRIVATE_KEY_PATH`` keeps working — it just gets the active
# profile's file instead of the old single global one.


def private_key_path(profile_id: Optional[str] = None) -> Path:
    return profile_dir(profile_id) / "private.key"


def public_key_path(profile_id: Optional[str] = None) -> Path:
    return profile_dir(profile_id) / "public.key"


def creator_path(profile_id: Optional[str] = None) -> Path:
    return profile_dir(profile_id) / "creator.txt"


def revocation_path(profile_id: Optional[str] = None) -> Path:
    return profile_dir(profile_id) / "revocation.cert"


def keychain_dir(profile_id: Optional[str] = None) -> Path:
    return profile_dir(profile_id) / "keychain"


# ----- Profile metadata ---------------------------------------------------


def profile_info(profile_id: str) -> dict:
    """Summary of one profile — exposed by the dashboard list endpoint.

    Returns ``{id, name, fingerprint, has_revocation_cert, created,
    is_active}``. Missing files surface as ``None`` fields
    so the caller can render a partially-initialized profile gracefully
    (e.g. one created but not yet keygen'd).
    """
    p = PROFILES_DIR / profile_id
    info = {
        "id": profile_id,
        "name": None,
        "fingerprint": None,
        "public_key": None,
        "has_private_key": False,
        "has_revocation_cert": False,
        "created": None,
        "is_active": profile_id == active_id(),
    }
    if not p.is_dir():
        return info
    cp = p / "creator.txt"
    if cp.exists():
        info["name"] = cp.read_text(encoding="utf-8").strip() or None
    info["has_private_key"] = (p / "private.key").exists()
    info["has_revocation_cert"] = (p / "revocation.cert").exists()
    try:
        info["created"] = int(p.stat().st_ctime)
    except OSError:
        pass
    pk_file = p / "public.key"
    if pk_file.exists():
        try:
            info["public_key"] = pk_file.read_text(encoding="utf-8").strip() or None
            if info["public_key"]:
                import hashlib
                raw = bytes.fromhex(info["public_key"])
                fp_raw = hashlib.sha256(raw).hexdigest()[:16]
                info["fingerprint"] = ":".join(fp_raw[i:i+4] for i in range(0, 16, 4))
        except Exception:
            pass
    return info


def list_profiles() -> list[dict]:
    """All profiles on disk, sorted by id. Active marker is per-row;
    callers don't have to cross-reference."""
    _ensure_root()
    out = []
    if not PROFILES_DIR.exists():
        return out
    for child in sorted(PROFILES_DIR.iterdir()):
        if not child.is_dir():
            continue
        # Skip dot-prefixed bookkeeping directories (.removed archive,
        # potential .tmp staging, etc.) — they aren't real profiles.
        if child.name.startswith("."):
            continue
        out.append(profile_info(child.name))
    return out


# ----- Lifecycle ----------------------------------------------------------


# Profile IDs become directory names — restrict the character set so a
# malicious or sloppy id can't escape PROFILES_DIR or collide with the
# active_profile file. Mirrors typical username constraints.
_VALID_ID = "abcdefghijklmnopqrstuvwxyz0123456789-_"


def _validate_id(profile_id: str) -> None:
    if not profile_id or len(profile_id) > 40:
        raise ValueError("Profile id must be 1-40 characters.")
    for c in profile_id.lower():
        if c not in _VALID_ID:
            raise ValueError(
                f"Profile id may contain only lowercase letters, digits, "
                f"hyphen, underscore. Got: {profile_id!r}"
            )
    if profile_id != profile_id.lower():
        raise ValueError("Profile id must be lowercase.")


def create(profile_id: str, name: Optional[str] = None) -> dict:
    """Generate a new profile with a freshly-minted Ed25519 keypair.

    Raises ``FileExistsError`` if the profile id is taken — we never
    silently clobber an existing profile because that would discard
    keys still needed to verify old records.
    """
    _validate_id(profile_id)
    _ensure_root()
    if (PROFILES_DIR / profile_id).exists():
        raise FileExistsError(f"Profile already exists: {profile_id}")
    # Import locally so the module load is light when signing is
    # unavailable (cryptography not installed).
    from mememage import signing
    if not signing.is_signing_available():
        raise RuntimeError("Signing requires the cryptography library.")
    # We temporarily route signing.keygen at this profile's directory
    # by setting active before keygen runs. Save the prior active so we
    # can restore if keygen fails midway.
    prior = active_id()
    (PROFILES_DIR / profile_id).mkdir(parents=True, exist_ok=True)
    set_active(profile_id)
    try:
        fingerprint, public_hex, _path = signing.keygen(force=False, name=name)
    except Exception:
        # Roll back: tear down the half-built profile and restore prior active.
        try:
            shutil.rmtree(PROFILES_DIR / profile_id)
        except OSError:
            pass
        if (PROFILES_DIR / prior).is_dir():
            set_active(prior)
        raise
    return profile_info(profile_id)


def add_peer(profile_id: str, public_key_hex: str,
             name: Optional[str] = None) -> dict:
    """Create a public-only profile entry from a peer's public key hex.

    Used by the pairing flow: when host A receives host B's identity
    via /api/profiles/pair, it stashes B's public key on disk so
    sign_alias can name it without needing B's private key. Profiles
    created this way have ``has_private_key=False`` and can be
    aliased *to* (target side) but never sign anything themselves.

    Raises ``FileExistsError`` if the profile id is taken, ``ValueError``
    if the public key hex isn't 32 bytes of valid Ed25519 public.
    """
    _validate_id(profile_id)
    _ensure_root()
    if (PROFILES_DIR / profile_id).exists():
        raise FileExistsError(f"Profile already exists: {profile_id}")
    pk_hex = (public_key_hex or "").strip().lower()
    try:
        raw = bytes.fromhex(pk_hex)
    except ValueError:
        raise ValueError("public_key must be valid hex")
    if len(raw) != 32:
        raise ValueError(f"Ed25519 public key must be 32 bytes (got {len(raw)})")

    prof = PROFILES_DIR / profile_id
    prof.mkdir(parents=True, exist_ok=True)
    (prof / "public.key").write_text(pk_hex)
    if name:
        (prof / "creator.txt").write_text(name.strip())
    return profile_info(profile_id)


def import_key(profile_id: str, name: Optional[str], pem_bytes: bytes) -> dict:
    """Import an existing Ed25519 private key (PEM) as a new profile.

    Accepts the formats produced by:
      - ``openssl genpkey -algorithm Ed25519``
      - ``ssh-keygen -t ed25519`` (OpenSSH format)
      - The output of ``mememage keygen`` itself

    Raises:
      ``FileExistsError`` if the profile id is taken
      ``ValueError`` if the PEM doesn't decode as an Ed25519 private key
    """
    _validate_id(profile_id)
    _ensure_root()
    if (PROFILES_DIR / profile_id).exists():
        raise FileExistsError(f"Profile already exists: {profile_id}")
    from mememage import signing
    if not signing.is_signing_available():
        raise RuntimeError("Signing requires the cryptography library.")
    Ed25519PrivateKey, _Ed25519PublicKey, serialization = signing._get_ed25519()

    # Try the standard PEM loader first; fall back to the OpenSSH
    # loader for ssh-keygen-produced keys.
    try:
        from cryptography.hazmat.primitives.serialization import (
            load_pem_private_key, load_ssh_private_key
        )
    except ImportError:
        raise RuntimeError("cryptography is missing PEM loaders.")
    private_obj = None
    last_err: Optional[Exception] = None
    for loader in (load_pem_private_key, load_ssh_private_key):
        try:
            private_obj = loader(pem_bytes, password=None)
            break
        except Exception as e:
            last_err = e
    if private_obj is None:
        raise ValueError(f"Could not parse key: {last_err}")
    # Confirm it's actually Ed25519.
    if not isinstance(private_obj, Ed25519PrivateKey):
        raise ValueError("Imported key is not Ed25519.")

    # Build the profile directory and write the files in the same shape
    # as keygen() does.
    prof = PROFILES_DIR / profile_id
    prof.mkdir(parents=True, exist_ok=True)
    pem_out = private_obj.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_bytes = private_obj.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    pub_hex = pub_bytes.hex()
    import hashlib
    import os
    fp_raw = hashlib.sha256(pub_bytes).hexdigest()[:16]
    fingerprint = ":".join(fp_raw[i:i+4] for i in range(0, 16, 4))

    (prof / "private.key").write_bytes(pem_out)
    os.chmod(str(prof / "private.key"), 0o600)
    (prof / "public.key").write_text(pub_hex)
    if name:
        (prof / "creator.txt").write_text(name.strip())

    # We also generate a pre-signed revocation cert for the imported
    # key, so the user has a kill switch from the moment they bring
    # the key into mememage. Mirrors keygen()'s behavior.
    revocation = {
        "action": "revoke",
        "key_fingerprint": fingerprint,
        "public_key": pub_hex,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    msg = json.dumps(revocation, sort_keys=True, separators=(",", ":")).encode("utf-8")
    revocation["signature"] = private_obj.sign(msg).hex()
    (prof / "revocation.cert").write_text(json.dumps(revocation, indent=2))
    os.chmod(str(prof / "revocation.cert"), 0o600)

    log.info("Imported key as profile %s (fingerprint %s)", profile_id, fingerprint)
    return profile_info(profile_id)


def remove(profile_id: str) -> dict:
    """Archive a profile under ``profiles/.removed/<id>-<timestamp>/``.

    Never deletes — old records signed by the removed key still need
    to verify, and the archived files let the user re-import if they
    change their mind. Refuses to remove the currently-active profile
    (the caller must switch to a different one first).
    """
    _validate_id(profile_id)
    _ensure_root()
    if profile_id == active_id():
        raise ValueError("Cannot remove the active profile. Switch first.")
    src = PROFILES_DIR / profile_id
    if not src.is_dir():
        raise FileNotFoundError(f"Profile not found: {profile_id}")
    # Capture fingerprint before archiving — we use it to clean up
    # alias records pointing TO this profile from other chains.
    fp_clean = ""
    try:
        info = profile_info(profile_id)
        fp = info.get("fingerprint") or ""
        fp_clean = fp.replace(":", "")
    except Exception:
        pass

    removed_dir = PROFILES_DIR / ".removed"
    removed_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    archived = removed_dir / f"{profile_id}-{stamp}"
    shutil.move(str(src), str(archived))
    log.info("Archived profile %s -> %s", profile_id, archived)

    # Local alias cleanup: when the user removes a profile they also
    # expect its chips to disappear from the dashboard. Walk the local
    # received-keychain dir and delete:
    #   1. alias-<removed_fp>.json files in OTHER chains — those are
    #      aliases FROM other profiles targeting the removed one, and
    #      would otherwise render as orphan chips with no resolvable
    #      target name.
    #   2. The mememage-keychain-<removed_fp>/ dir itself — aliases the
    #      removed profile SIGNED, which dangle now that the signer
    #      is gone from active rotation.
    # Public IA copies persist (we can't retract a signed claim), so
    # any verifier walking IA still finds the alias — only the local
    # view is reconciled with the user's intent.
    if fp_clean:
        received_root = Path(os.path.expanduser("~/.mememage/received/keychain"))
        if received_root.is_dir():
            removed_chain = received_root / f"mememage-keychain-{fp_clean}"
            if removed_chain.is_dir():
                try:
                    shutil.rmtree(str(removed_chain))
                    log.info("Removed local keychain dir %s", removed_chain)
                except Exception as e:
                    log.warning("Failed to clean keychain dir %s: %s", removed_chain, e)
            stale_filename = f"alias-{fp_clean}.json"
            for chain_dir in received_root.iterdir():
                if not chain_dir.is_dir():
                    continue
                stale = chain_dir / stale_filename
                if stale.is_file():
                    try:
                        stale.unlink()
                        log.info("Removed stale alias %s", stale)
                    except Exception as e:
                        log.warning("Failed to remove %s: %s", stale, e)

    return {"archived": str(archived)}


# ----- Alias records ------------------------------------------------------


def sign_alias(other_profile_id: str) -> dict:
    """The ACTIVE profile signs an alias record naming ``other_profile_id``
    as a sibling identity.

    Returns the record dict — the caller is responsible for uploading
    it to IA (mirrors the rotate/revoke pattern). Verifiers walking the
    active profile's keychain see the alias and can cross-check the
    other key's keychain for the matching reverse-alias.

    Raises ``FileNotFoundError`` if either profile is missing.
    """
    from mememage import signing
    if not signing.is_signing_available():
        raise RuntimeError("Signing requires the cryptography library.")
    a_id = active_id()
    if a_id == other_profile_id:
        raise ValueError("Active profile cannot alias itself.")
    a_info = profile_info(a_id)
    b_info = profile_info(other_profile_id)
    if not a_info.get("fingerprint"):
        raise FileNotFoundError(f"Active profile {a_id!r} has no key.")
    if not b_info.get("fingerprint"):
        raise FileNotFoundError(f"Profile {other_profile_id!r} has no key.")

    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    private_obj = load_pem_private_key(
        private_key_path(a_id).read_bytes(),
        password=None,
    )
    record = {
        "action": "alias",
        "signer_fingerprint": a_info["fingerprint"],
        "signer_public_key": a_info["public_key"],
        "alias_fingerprint": b_info["fingerprint"],
        "alias_public_key": b_info["public_key"],
        # creator_name is the signer's name (existing field — kept for
        # backward compatibility with older records).
        "creator_name": a_info.get("name") or "",
        # alias_creator_name is the TARGET's name as known by the
        # signer. Lets verifiers render "this signer is also known as
        # <name>" without needing to walk the target's keychain to
        # find its name. New field as of 2026-05-18 — older records
        # don't have it; verifiers fall back to alias_fingerprint
        # truncated when missing.
        "alias_creator_name": b_info.get("name") or "",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    msg = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
    record["signature"] = private_obj.sign(msg).hex()
    return record
