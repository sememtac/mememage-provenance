"""Creator Access Layer — password-based encryption for selective record access.

We are the lock, you bring your key.

The system doesn't manage passwords. It doesn't store them. It doesn't know
what the key looks like. At mint time, the creator types whatever they want.
We encrypt, store the ciphertext, forget.

Three pillars of creator sovereignty:
  Key (Ed25519)     — identity. The Father.
  Hash (SHA-256)    — integrity. The Spirit.
  Password (AES)    — access. The Son.

GPS is always password-encrypted (the creator's key to their own time capsule).
Chain visibility determines whether the rest of the soul is also encrypted:
  light_energy (code 0) — public record, GPS password-protected only
  dark_matter  (code 1) — private record, entire soul password-protected

Visibility on the SOUL is stored as an integer code (see VISIBILITY_*),
uniform with constellation_index / age / birth_traits. Chain CONFIG
files (chain.json) keep the human-readable string ("light_energy" /
"dark_matter") because users edit them by hand.
"""

import json
import logging
import os

log = logging.getLogger(__name__)

# Protected fields — encrypted on dark_matter chains.
# These contain the birth certificate, the sky, the machine reading,
# the generation recipe. The soul of the record.
#
# See docs/chunks-spec.md and docs/record-shapes.html for the full
# visibility map per chain visibility mode.
PROTECTED_FIELDS = {
    # Origin — the creator's full declaration of how this image came
    # into being. Free-form dict (prompt/seed/model for AI gens, camera/
    # lens/ISO for photos, etc). Sealed wholesale.
    "origin",
    # Width / height — physical properties. Sealed on dark_matter to
    # avoid leaking aspect ratio / orientation through the field set.
    "width", "height",
    # Birth certificate — celestial state + machine vitals
    "birth",
    # GPS time-lock puzzle envelope. (gps_password_locked is added AFTER
    # encryption and not listed here — by design it stays plain.)
    "gps_time_locked",
    # GPS plaintext coordinates — present only on gps_visibility:"public".
    # Sealed on dark chains too: otherwise a dark soul with public GPS would
    # leak the exact location in plaintext, defeating the darkness. The content
    # hash runs AFTER encryption, so on dark chains gps is gone before hashing
    # (its integrity rides encrypted_fields); light chains never reach this
    # deletion, so public GPS stays public there as intended.
    "gps",
    # Birth — trait codes (readings / temperament / summary derived at display)
    "birth_traits",
    # Rarity — dice rolls + machine signature + sigil. Score derived
    # at display time from these.
    "rarity",
    # Portrait — needed for EMBODIED, sealed under password
    "thumbnail",
    # Luma grid — 16x16 brightness map (localized-tamper half of EMBODIED). A
    # coarse preview of the image, so sealed alongside the thumbnail; the grid
    # check runs after unlock. Unlike thumbnail, it's IN the content hash, so
    # on dark chains its integrity rides encrypted_fields (the hash runs after
    # this deletion). See mememage/embodiment.py.
    "luma_grid",
    # Celestial hash — derived from `birth`, sealed alongside it
    "constellation_hash",
}

# Chain visibility codes — soul stores int, chain config + Python API
# still use the string name (user-edited file; legacy API surface).
# APPEND-ONLY: never reorder or rename codes; old records reference
# them by position.
VISIBILITY_LIGHT = 0  # light_energy — public record (default)
VISIBILITY_DARK = 1   # dark_matter — full-soul encryption

VISIBILITY_CODE_FOR = {"light_energy": VISIBILITY_LIGHT, "dark_matter": VISIBILITY_DARK}
VISIBILITY_NAME_FOR = {VISIBILITY_LIGHT: "light_energy", VISIBILITY_DARK: "dark_matter"}


def visibility_code(name_or_code) -> int:
    """Coerce a string name OR int code to the int code."""
    if isinstance(name_or_code, int):
        return name_or_code
    return VISIBILITY_CODE_FOR.get(name_or_code or "light_energy", VISIBILITY_LIGHT)


def visibility_name(code_or_name) -> str:
    """Coerce an int code OR string name to the canonical string name."""
    if isinstance(code_or_name, str):
        return code_or_name if code_or_name in VISIBILITY_CODE_FOR else "light_energy"
    return VISIBILITY_NAME_FOR.get(code_or_name, "light_energy")


# Field-encryption primitives live in the generic core (mememage.crypto). This
# provenance module layers the soul/chain/dark_matter policy on top of them.
from mememage.crypto import (  # noqa: F401  (re-exported for access-layer callers)
    _PBKDF2_ITERATIONS,
    _derive_key,
    decrypt_field,
    encrypt_field,
    is_encryption_available,
)


def encrypt_gps(lat: float, lon: float, password: str) -> dict:
    """Encrypt GPS coordinates with password. The creator's key to their own time capsule."""
    return encrypt_field(f"{lat:.6f},{lon:.6f}", password)


def decrypt_gps(envelope: dict, password: str) -> tuple:
    """Decrypt GPS coordinates. Returns (lat, lon)."""
    text = decrypt_field(envelope, password)
    lat, lon = text.split(",")
    return float(lat), float(lon)


def encrypt_soul(record: dict, password: str) -> dict:
    """Encrypt protected fields from a record as a single JSON blob.

    Returns AES-256-GCM envelope of the serialized protected fields.
    """
    protected = {}
    for k in PROTECTED_FIELDS:
        if k in record:
            protected[k] = record[k]
    plaintext = json.dumps(protected, sort_keys=True, separators=(",", ":"))
    return encrypt_field(plaintext, password)


def decrypt_soul(envelope: dict, password: str) -> dict:
    """Decrypt the soul fields blob. Returns dict of protected fields."""
    plaintext = decrypt_field(envelope, password)
    return json.loads(plaintext)


def encrypt_chunks(chunks_namespace: dict, password: str) -> dict:
    """Encrypt the entire `chunks` namespace as a single AES-GCM envelope.

    Used on dark_matter chains to seal the chunk payload alongside the
    soul. The plaintext is canonical-JSON of the namespace dict.
    """
    plaintext = json.dumps(chunks_namespace, sort_keys=True, separators=(",", ":"))
    return encrypt_field(plaintext, password)


def decrypt_chunks(envelope: dict, password: str) -> dict:
    """Decrypt the chunks envelope. Returns the chunks namespace dict."""
    plaintext = decrypt_field(envelope, password)
    return json.loads(plaintext)


def apply_encryption(record: dict, gps: tuple | None, password: str,
                     chain_visibility: str = "light_energy") -> dict:
    """Apply encryption to a record in-place.

    Called AFTER content hash and signing are computed.
    The hash covers plaintext. Encryption replaces plaintext with ciphertext.

    Args:
        record: The full record dict (modified in place).
        gps: (lat, lon) raw coordinates, or ``None`` when the chain's
             ``gps_source`` is ``none``. When None, the ``gps_password_locked``
             envelope is skipped — there are no coordinates to seal.
        password: The creator's password.
        chain_visibility: "light_energy" (public) or "dark_matter" (private).

    Returns the modified record.
    """
    if not is_encryption_available():
        log.warning("cryptography library not installed — encryption skipped")
        return record

    # GPS password envelope — the creator's instant-unlock key. Parallel
    # to gps_time_locked (the RSA puzzle, set in core.py at hash time).
    # Two locks, same coordinates: one temporal (anyone, eventually),
    # one personal (creator, immediately). Only emitted when GPS was
    # captured AND the chain has a password; gps_source: "none" chains
    # get neither key.
    if gps is not None and len(gps) == 2:
        record["gps_password_locked"] = encrypt_gps(gps[0], gps[1], password)
    # chain_visibility may already be stamped (as int) by _step_chain_visibility
    # pre-hash. Re-stamp here for callers that bypass the pipeline (tests,
    # programmatic access). Always int on the soul.
    visibility = visibility_code(chain_visibility)
    record["chain_visibility"] = visibility

    # Dark matter: full opacity. Encrypt soul and chunks, then DELETE the
    # plaintext fields (not replaced with sentinels — the field set itself
    # would otherwise leak record structure: LoRA usage, singleton chunks,
    # creator workflow. See docs/chunks-spec.md "Why deletion not sentinels".)
    #
    # Gate on the normalized int code, never the raw arg: visibility_code()
    # accepts both the "dark_matter" string and the int 1, so a caller that
    # passes the code would otherwise mark the record dark (above) while this
    # branch — if it compared the raw string — silently skipped encryption,
    # publishing the soul in the clear. Gate and stamp must agree.
    if visibility == VISIBILITY_DARK:
        # Soul
        record["encrypted_fields"] = encrypt_soul(record, password)
        for k in PROTECTED_FIELDS:
            if k in record:
                del record[k]

        # Chunks (nested namespace, post chunks-spec migration)
        if "chunks" in record:
            record["encrypted_chunks"] = encrypt_chunks(record["chunks"], password)
            del record["chunks"]

    return record
