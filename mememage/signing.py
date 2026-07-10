"""Ed25519 digital signing for mememage conceptions.

Optional feature — requires `pip install mememage[sign]`.
If no key exists, conceptions are unsigned (integrity only).
If a key exists, conceptions are automatically signed (integrity + authenticity).

Key storage:
    ~/.mememage/private.key      — Ed25519 private key (keep safe, never share)
    ~/.mememage/public.key       — Ed25519 public key (publish everywhere)
    ~/.mememage/creator.txt      — Creator display name
    ~/.mememage/revocation.cert  — Pre-signed revocation certificate (store offline)
    ~/.mememage/keychain/        — Key history (old keys after rotation)

The signature covers: identifier + content_hash
Anyone with the public key can verify. Nobody without the private key can sign.

Key lifecycle:
    keygen  → generates key pair + revocation certificate
    rotate  → generates new key, signs succession record with old key, uploads to IA
    revoke  → publishes pre-signed revocation certificate to IA
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

KEY_DIR = Path("~/.mememage").expanduser()


# Path "constants" are now resolved through the active profile so the
# single-key signing surface keeps working transparently in a
# multi-profile world. Inside this module we use the ``_p_*`` helpers
# directly; ``__getattr__`` makes the legacy uppercase names still
# resolve for external callers (``signing.PRIVATE_KEY_PATH``).


def _p_private_key():
    from mememage import profiles
    return profiles.private_key_path()


def _p_public_key():
    from mememage import profiles
    return profiles.public_key_path()


def _p_creator():
    from mememage import profiles
    return profiles.creator_path()


def _p_revocation():
    from mememage import profiles
    return profiles.revocation_path()


def _p_keychain_dir():
    from mememage import profiles
    return profiles.keychain_dir()


_PROFILE_PATH_ATTRS = {
    "PRIVATE_KEY_PATH": _p_private_key,
    "PUBLIC_KEY_PATH":  _p_public_key,
    "CREATOR_PATH":     _p_creator,
    "REVOCATION_PATH":  _p_revocation,
    "KEYCHAIN_DIR":     _p_keychain_dir,
}


def __getattr__(name):
    """PEP 562 module __getattr__ — legacy uppercase names resolve
    through the active profile. Inside this module use ``_p_*()``
    directly; ``__getattr__`` only fires on external attribute access."""
    if name in _PROFILE_PATH_ATTRS:
        return _PROFILE_PATH_ATTRS[name]()
    raise AttributeError(f"module 'mememage.signing' has no attribute {name!r}")


def _get_ed25519():
    """Lazy import Ed25519 from cryptography library."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
        from cryptography.hazmat.primitives import serialization
        return Ed25519PrivateKey, Ed25519PublicKey, serialization
    except ImportError:
        return None, None, None


def is_signing_available():
    """Check if the cryptography library is installed."""
    pk, _, _ = _get_ed25519()
    return pk is not None


def has_key():
    """Check if a private key exists."""
    return _p_private_key().exists()


def get_creator_name():
    """Get the creator name (set at keygen)."""
    cp = _p_creator()
    if cp.exists():
        return cp.read_text().strip() or None
    return None


def keygen(force=False, name=None):
    """Generate an Ed25519 key pair.

    Returns (fingerprint, public_key_hex, private_key_path).
    Raises if key already exists (unless force=True).
    """
    Ed25519PrivateKey, _, serialization = _get_ed25519()
    if Ed25519PrivateKey is None:
        raise RuntimeError(
            "Signing requires the cryptography library. "
            "Install with: pip install mememage[sign]"
        )

    priv_path = _p_private_key()
    pub_path = _p_public_key()
    creator_path = _p_creator()
    rev_path = _p_revocation()
    if priv_path.exists() and not force:
        raise FileExistsError(
            f"Key already exists at {priv_path}. "
            "Use --force to overwrite (WARNING: old signed records won't verify with new key)."
        )

    # Create the key's directory (active profile's dir, not the legacy
    # ~/.mememage root — but mkdir on the profile dir is what we want).
    priv_path.parent.mkdir(parents=True, exist_ok=True)

    # Generate key pair
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    # Serialize private key (PEM format)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    # Serialize public key (raw bytes, hex-encoded for portability)
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    public_hex = public_bytes.hex()

    # Fingerprint: first 8 hex chars of SHA-256 of public key, colon-separated pairs
    fingerprint_raw = hashlib.sha256(public_bytes).hexdigest()[:16]
    fingerprint = ":".join(fingerprint_raw[i:i+4] for i in range(0, 16, 4))

    # Write private key (restrictive permissions)
    priv_path.write_bytes(private_pem)
    os.chmod(str(priv_path), 0o600)

    # Write public key (hex-encoded, shareable)
    pub_path.write_text(public_hex)

    # Write creator name (if provided)
    if name:
        creator_path.write_text(name.strip())

    # Pre-sign revocation certificate — store offline as emergency kill switch.
    # If the private key is ever compromised, publish this to revoke the key.
    # The attacker can't forge this because the timestamp proves it was created
    # at keygen time, before any compromise.
    revocation = {
        "action": "revoke",
        "key_fingerprint": fingerprint,
        "public_key": public_hex,
        "creator_name": name.strip() if name else None,
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reason": "Pre-signed revocation certificate (emergency use only)",
    }
    revocation_msg = json.dumps(revocation, sort_keys=True, separators=(",", ":")).encode("utf-8")
    revocation["signature"] = private_key.sign(revocation_msg).hex()
    rev_path.write_text(json.dumps(revocation, indent=2))
    os.chmod(str(rev_path), 0o600)

    log.info("Key pair generated. Fingerprint: %s", fingerprint)
    return fingerprint, public_hex, str(priv_path)


def get_fingerprint():
    """Get the fingerprint of the current public key."""
    pk_path = _p_public_key()
    if not pk_path.exists():
        return None
    public_hex = pk_path.read_text().strip()
    public_bytes = bytes.fromhex(public_hex)
    fingerprint_raw = hashlib.sha256(public_bytes).hexdigest()[:16]
    return ":".join(fingerprint_raw[i:i+4] for i in range(0, 16, 4))


def get_signer_info():
    """Return (public_hex, fingerprint, creator_name) for the active key.

    Does NOT sign. Used by the mint pipeline to populate public_key /
    key_fingerprint / creator_name into the record *before* the
    content_hash step, so those fields can participate in the hash.
    Returns None if no key exists.
    """
    pk_path = _p_public_key()
    if not pk_path.exists():
        return None
    public_hex = pk_path.read_text().strip()
    public_bytes = bytes.fromhex(public_hex)
    fingerprint_raw = hashlib.sha256(public_bytes).hexdigest()[:16]
    fingerprint = ":".join(fingerprint_raw[i:i+4] for i in range(0, 16, 4))
    creator_name = get_creator_name()
    return (public_hex, fingerprint, creator_name)


def _build_signature_message(identifier, content_hash, thumbnail_hash=""):
    """Canonical bytes that the Ed25519 signature covers.

    Payload: ``identifier + \\0 + content_hash + \\0 + thumbnail_hash``.

    The thumbnail hash (SHA-256 hex of the record's ``thumbnail`` data
    URI, full 64-char) extends the signature to bind the post-mint
    portrait, closing the thumbnail-swap gap on AUTHENTICATED checks
    (otherwise only EMBODIED catches a swap, and only when the user
    drops the image).

    Empty string ``""`` for records with no thumbnail (keychain records
    — succession / revocation / alias — and any soul where thumbnail
    generation was skipped). Verifier computes the same value from the
    record's actual thumbnail field, so the empty-string case
    round-trips cleanly.
    """
    return f"{identifier}\x00{content_hash}\x00{thumbnail_hash}".encode("utf-8")


def sign(identifier, content_hash, thumbnail_hash=""):
    """Sign (identifier, content_hash, thumbnail_hash) with the private key.

    See ``_build_signature_message`` for the canonical payload format.
    ``thumbnail_hash`` is empty for records without a thumbnail
    (keychain records, records minted without Pillow, etc.).

    Returns (signature_hex, public_key_hex, fingerprint, creator_name) or None if no key.
    """
    priv_path = _p_private_key()
    if not priv_path.exists():
        return None

    Ed25519PrivateKey, _, serialization = _get_ed25519()
    if Ed25519PrivateKey is None:
        log.warning("Private key exists but cryptography library not installed")
        return None

    # Load private key
    private_pem = priv_path.read_bytes()
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    private_key = load_pem_private_key(private_pem, password=None)

    message = _build_signature_message(identifier, content_hash, thumbnail_hash)
    signature = private_key.sign(message)

    # Get public key
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    public_hex = public_bytes.hex()

    # Fingerprint
    fingerprint_raw = hashlib.sha256(public_bytes).hexdigest()[:16]
    fingerprint = ":".join(fingerprint_raw[i:i+4] for i in range(0, 16, 4))

    return signature.hex(), public_hex, fingerprint, get_creator_name()


def keychain_identifier(fingerprint):
    """IA identifier for a key's chain records (succession, revocation)."""
    clean = fingerprint.replace(":", "")
    return f"mememage-keychain-{clean}"


def rotate(name=None):
    """Rotate to a new key. Signs a succession record with the OLD key.

    Returns (new_fingerprint, succession_record, keychain_id).
    The caller is responsible for uploading the succession record to IA.
    """
    Ed25519PrivateKey, _, serialization = _get_ed25519()
    if Ed25519PrivateKey is None:
        raise RuntimeError("Signing requires the cryptography library.")
    priv_path = _p_private_key()
    if not priv_path.exists():
        raise FileNotFoundError("No existing key to rotate from. Use keygen first.")

    # Load old key
    old_pem = priv_path.read_bytes()
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    old_private = load_pem_private_key(old_pem, password=None)
    old_pub_bytes = old_private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    old_pub_hex = old_pub_bytes.hex()
    old_fp_raw = hashlib.sha256(old_pub_bytes).hexdigest()[:16]
    old_fingerprint = ":".join(old_fp_raw[i:i+4] for i in range(0, 16, 4))

    # Archive old key under the active profile's keychain dir
    kc_dir = _p_keychain_dir()
    kc_dir.mkdir(parents=True, exist_ok=True)
    archive_name = old_fingerprint.replace(":", "") + ".key"
    (kc_dir / archive_name).write_bytes(old_pem)

    # Generate new key
    new_fingerprint, new_pub_hex, _ = keygen(force=True, name=name or get_creator_name())

    # Sign succession record with OLD key
    succession = {
        "action": "succeed",
        "old_fingerprint": old_fingerprint,
        "old_public_key": old_pub_hex,
        "new_fingerprint": new_fingerprint,
        "new_public_key": new_pub_hex,
        "creator_name": name or get_creator_name(),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    succession_msg = json.dumps(succession, sort_keys=True, separators=(",", ":")).encode("utf-8")
    succession["signature"] = old_private.sign(succession_msg).hex()

    chain_id = keychain_identifier(old_fingerprint)
    log.info("Key rotated: %s → %s", old_fingerprint, new_fingerprint)
    return new_fingerprint, succession, chain_id


def get_revocation():
    """Load the pre-signed revocation certificate."""
    rev_path = _p_revocation()
    if not rev_path.exists():
        return None
    return json.loads(rev_path.read_text())


def verify(identifier, content_hash, signature_hex, public_key_hex, thumbnail_hash=""):
    """Verify a signature against a public key.

    Payload: ``identifier + \\0 + content_hash + \\0 + thumbnail_hash``
    (see ``_build_signature_message``). Callers pass the SHA-256 hex of
    the record's ``thumbnail`` field, or empty string if the record has
    no thumbnail.

    Returns True if valid, False if invalid, None if can't verify (no library).
    """
    _, Ed25519PublicKey, serialization = _get_ed25519()
    if Ed25519PublicKey is None:
        return None

    try:
        public_bytes = bytes.fromhex(public_key_hex)
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey as PubKey
        public_key = PubKey.from_public_bytes(public_bytes)

        message = _build_signature_message(identifier, content_hash, thumbnail_hash)
        signature = bytes.fromhex(signature_hex)

        public_key.verify(signature, message)
        return True
    except Exception:
        return False


def verify_keychain_record(record):
    """Verify a keychain record (succession or revocation).

    The signature covers the canonical JSON of all fields except 'signature'.
    Returns True if valid, False if invalid, None if can't verify.
    """
    _, Ed25519PublicKey, _ = _get_ed25519()
    if Ed25519PublicKey is None:
        return None

    try:
        sig_hex = record.get("signature")
        if not sig_hex:
            return False

        # Determine which key signed it
        if record.get("action") == "succeed":
            pub_hex = record["old_public_key"]
        else:
            pub_hex = record["public_key"]

        # Reconstruct the signed message (all fields except signature)
        verify_record = {k: v for k, v in record.items() if k != "signature"}
        msg = json.dumps(verify_record, sort_keys=True, separators=(",", ":")).encode("utf-8")

        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey as PubKey
        public_key = PubKey.from_public_bytes(bytes.fromhex(pub_hex))
        public_key.verify(bytes.fromhex(sig_hex), msg)
        return True
    except Exception:
        return False


def upload_keychain_record(record, chain_id, filename):
    """Mirror a keychain record (succession / revocation / alias) to
    every enabled channel.

    Uses ``channels.blast_keychain`` so chains with IA disabled still
    publish their keychain records to peer surfaces (http_push, etc.).
    Each channel decides its own URL pattern — IA writes to the
    canonical ``{IA_S3_URL}/{chain_id}/{filename}``; http_push derives
    a peer keychain URL from its souls base. Channels that don't fit
    the keychain model (Zenodo) silently skip.

    Logs which surfaces accepted the record. Raises ChannelUploadError
    if every enabled channel either skipped or failed — that's a real
    breakage the caller should surface (a keygen/rotate that lands
    nowhere defeats verifiability).
    """
    from mememage import channels as _channels
    import os as _os

    payload = json.dumps(record, indent=2).encode("utf-8")

    # Mirror self-signed records into our own ``received/keychain/``
    # directory so the dashboard sees them without depending on a
    # roundtrip back from a remote channel. Without this, Mac (the
    # initiator of a pair) would never see its own outbound alias
    # chip because Mac's channels publish outward but nothing arrives
    # inward via http_push (the peer can't reach back when behind NAT).
    from mememage import chains as _chains
    local_dir = _chains.keychain_dir() / chain_id
    try:
        local_dir.mkdir(parents=True, exist_ok=True)
        (local_dir / filename).write_bytes(payload)
    except Exception as e:
        log.warning("Local keychain mirror failed for %s/%s: %s",
                    chain_id, filename, e)

    # Channels are per-profile: the signing profile's own channels.json is
    # its publication set, so the keychain mirror fires every enabled channel
    # in it. The local mirror above is unaffected either way — that's the
    # signer's own filesystem, not "publication".
    chs = _channels.load_channels()
    results = _channels.blast_keychain(chs, chain_id, filename, payload)
    log.info("Keychain record %s/%s mirrored to %s",
             chain_id, filename, list(results.keys()))
