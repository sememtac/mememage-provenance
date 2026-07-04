"""Core functions for uploading and fetching metadata from the Internet Archive."""

import hashlib
import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


def soul_store_dir() -> Path:
    """The single flat store every soul lives in: ``~/.mememage/received``.

    One home, keyed by identifier — it's the served face (souls.<domain>/<id>.
    soul), the creator's local copy, and the drop-anywhere portable form, all
    at once. Derived from ``chains.MEMEMAGE_ROOT`` (not a raw expanduser) so a
    patched root in tests isolates it too. Replaced the old per-chain
    ``records/`` backup, which duplicated this and leaked the local chain name
    into a path."""
    from mememage import chains as _chains
    return _chains.MEMEMAGE_ROOT / "received"

from mememage.hashing import (
    OPEN_HASH_VERSION, _HASH_EXCLUDED_OPEN, _normalize_for_hash, hash_fields,
    open_hashable_fields,
)
from mememage.celestial import compute_birth_certificate
from mememage.config import IA_DOWNLOAD_URL, IA_METADATA_URL
from mememage.lineage import get_parent_id, set_parent_id
from mememage.net import fetch_json
from mememage.personality import compute_machine_fingerprint, update_personality
from mememage.temperament import read_birth_temperament
from mememage.rarity import compute_rarity
from mememage.constellation import name_from_hash
from mememage.site_embed import advance_chunk_index, constellation_cadence, current_outer_position, current_outer_total, get_current_age_info, get_current_chunk, get_heart_star, is_heart_star, set_heart_star
from mememage.thumbnail import generate_thumbnail
from mememage.signing import sign as sign_record, has_key, get_fingerprint, get_signer_info

# Fields required for a valid metadata upload.
#
# Width/height are load-bearing for the bar embedding step (the bar
# scales with image width) so they MUST be present. The caller can
# pass them explicitly or the server-side upload handler can derive
# them from the image dimensions.
#
# Prompt and seed are AI-gen artifacts — optional. Mememage's promise
# is creator-time-place provenance, not "this came from an AI". A
# photograph, screenshot, scan, or drawing can mint just fine with no
# prompt and no seed; the record just won't carry those fields. An AI
# generation pipeline supplies them when it has them.
REQUIRED_FIELDS = {"width", "height"}

# Fields INCLUDED in content hash computation (positive list).
# Only these fields contribute to the image's identity hash. New fields
# added to the record are excluded by default — they must be explicitly
# added here if they should affect the content hash. This prevents
# supplementary metadata (constellation names, orbit chunks, decoder data,
# about text, etc.) from invalidating existing hashes when they evolve.
# ---------------------------------------------------------------------------
# Versioned hash inclusion sets — v1 is the launch canon.
# ---------------------------------------------------------------------------
#
# The content_hash is SHA-256 of canonical JSON over the fields in
# _HASH_INCLUDED_BY_VERSION[record["hash_version"]]. Different versions
# select different fields, so future bumps can change what counts as
# tamper-evident without invalidating records minted under prior rules.
#
# v1 is the schema we ship. Earlier dev iterations (v2/v3/v4 — yes the
# numbering ran higher pre-launch) are not honored in code because no
# public records of those vintages exist. Test souls minted under v2-v4
# are pre-launch artifacts and don't round-trip through this version.
#
# Adding a new version (v2 onward):
#   1. Add an entry to _HASH_INCLUDED_BY_VERSION mirroring the previous
#      version's set, then add/remove the field(s) the bump is about.
#   2. Bump CURRENT_HASH_VERSION below so new mints stamp the new value.
#   3. Mirror BOTH changes in docs/js/verify.js (HASH_INCLUDED_BY_VERSION).
#      They must stay in lockstep — the JS verifier is the source of
#      truth for downstream users who only have the static decoder bundle.
#   4. Add a test in tests/test_hash_version.py pinning the new version's
#      rules + a record at the prior version still verifying.
#   5. Don't rename fields silently across versions — if a field's
#      semantics change, give it a new name and translate before
#      serialization. Same-name-different-bytes would silently invalidate.
#   6. Don't change the canonical-JSON serialization (sort_keys + no
#      whitespace + ensure_ascii=True) without bumping. The serialization
#      IS part of the version's definition.

_HASH_INCLUDED_V1 = {
    # Identifier — soul-discovery key, also in the bar
    "identifier",
    # Version dispatch — IN the hash to shut a downgrade attack: without
    # this, an attacker could change hash_version to point at a different
    # inclusion set the verifier would honor. Locking it means the
    # version-of-rules-that-was-used IS part of what was sealed.
    "hash_version",
    # Origin — creator-declared metadata about how the image came into
    # being. Free-form dict (prompt/seed/sampler for AI gens; camera/
    # lens/ISO for photos; whatever for other workflows). Hashed
    # wholesale so any tampering breaks WITNESSED, and so the schema
    # doesn't dictate what counts as "real" provenance.
    "origin",
    # Width / height live top-level — physical properties used by the
    # bar encoder (scale) and the identifier hash. Always in.
    "width", "height",
    # Timestamps — when the image was born
    "conceived", "rendered",
    # Birth certificate — celestial positions + machine vitals (no GPS;
    # GPS lives at top-level as gps_time_locked / gps_password_locked
    # so chains with gps_source: "none" produce a symmetric shape).
    "birth",
    # GPS time-lock — RSA puzzle. Anyone can recover the coordinates
    # after ~10 years of sequential squaring. Always present when the
    # chain captures GPS; absent on gps_source: "none" chains.
    "gps_time_locked",
    # GPS plaintext — [lat, lon], present ONLY on gps_visibility: "public"
    # chains (deliberate, opt-in location exposure). Hashed so the shown
    # birthplace is tamper-evident; absent on time_locked chains, so this
    # is intersection-safe and needs no hash_version bump.
    "gps",
    # Celestial digest — deterministic hash of planetary positions at birth
    "constellation_hash",
    # Machine identity
    "machine_fingerprint",
    # Rarity — dice rolls at conception (codes + context; dice lists).
    # rarity_score is NOT included: it's the pure sum of dice + machine
    # signature + sigil, fully derivable from this dict. JS readers
    # reconstruct via computeRarityScore() in cert-renderer.js.
    "rarity",
    # Birth — codes only. Readings / summary / temperament are derived at
    # display time from birth_traits via the BIRTH_READINGS + COMBOS
    # lookup tables (Python: temperament.py; JS: birth-text.js).
    "birth_traits",
    # Lineage — chain of descent
    "parent_id",
    # Constellation — family claims (must be tamper-evident)
    "constellation_name", "heart_star_id", "constellation_index",
    # Constellation cadence — hashed so the chain's heart-reset size, which
    # constellation_index is DERIVED from (outer_position mod size), can't be
    # altered without breaking WITNESSED. Intersection-safe: always present.
    "constellation_size",
    # Decoder integrity — hash of the complete assembled decoder HTML.
    # Computed once at seal time, same value for the entire Age.
    # Protects against chunk injection: forged chunk → bad reassembly → caught.
    "decoder_hash",
    # Age number — was previously inside chunks.decoder.age; hoisted
    # so chains without a decoder layer can still report which Age
    # they minted under. The display name is derived from the record's
    # own age_name, or the reference Age-name table as a fallback.
    "age",
    # Signer identity — IN the hash to shut the signer-swap attack:
    # without this, an attacker could strip signature+public_key, drop
    # in their own key, sign the existing id+content_hash, and TOFU
    # would seat them as the creator for any first-touch viewer. With
    # public_key in the hash, swapping the key changes the hash, which
    # changes what the bar must contain, which breaks the visual proof.
    # Signer becomes part of the artifact's identity, not bolted on.
    # key_fingerprint included for the same reason + so verifiers don't
    # have to re-derive it (async crypto in browsers).
    "public_key", "key_fingerprint",
    # Chunks integrity (without bulk) — SHA-256 over the canonical list
    # of per-chunk hashes ({layer_name|pinned_role: chunk.hash}, first
    # 16 hex). Any chunk swap breaks WITNESSED. We hash chunk *hashes*,
    # not chunk *data*, so the lightweight verify-without-payload path
    # (By Word) still works. Absent on records minted before the chain
    # is sealed (no chunks → no chunks_root field, same pattern as
    # gps_time_locked on gps_source: none chains).
    "chunks_root",
    # Visibility tier — int code (0 = light_energy public, 1 = dark_matter
    # sealed). IN the hash so a record can't be silently re-tiered (e.g.
    # flipped from sealed to public). Soul stores int; chain config files
    # keep the human-readable string name. See access.visibility_code/name.
    "chain_visibility",
    # Position in the outer cycle (0..outer_total-1) + the chain's
    # outer_total. Stamped at the top level (not inside chunks) so
    # validators can place dark_matter records — their chunks namespace
    # is encrypted into an opaque blob, leaving the position otherwise
    # unrecoverable. Hashed so position tampering breaks WITNESSED
    # (someone re-stamping a record at a different outer_position to
    # lie about where in the chain it lives is caught here).
    "outer_position", "outer_total",
    # Luma grid — 16x16 mean-luma map (base64), the localized-tamper half of
    # EMBODIED. IN the hash so a defacer can't swap in a grid matching their
    # altered image without breaking WITNESSED (dHash alone can't tell a drawn
    # line from JPEG — see mememage/embodiment.py). Protected on dark_matter
    # (it's a coarse brightness preview, sealed alongside the thumbnail).
    # Absent on legacy records minted before this field existed — presence-
    # filtered, so those still verify at the legacy (dHash-only) grade.
    "luma_grid",
    # Creator-access-layer envelopes — hashed when present so tampering
    # with the ciphertext breaks WITNESSED. The pipeline computes the
    # hash AFTER _step_encrypt, so for dark_matter records these blobs
    # are what remains in the record (origin/birth/etc. plaintexts are
    # gone). For light chains, gps_password_locked is present when a
    # creator password is set; encrypted_fields / encrypted_chunks aren't.
    # compute_content_hash filters by presence, so absent fields don't
    # poison the hash.
    "encrypted_fields", "encrypted_chunks", "gps_password_locked",
}

_HASH_INCLUDED_BY_VERSION = {
    1: _HASH_INCLUDED_V1,
}

CURRENT_HASH_VERSION = 1
DEFAULT_HASH_VERSION = 1

# The "open" hash version — the raw / programmatic-adoption model. Where the
# integer versions (V1…) hash a CURATED positive set (the canonical Mememage
# chain's fixed schema), "open" INVERTS the rule: hash everything in the soul
# EXCEPT the two structurally-circular fields below. So an adopter who brings
# arbitrary fields ({prompt, license, …}) gets every one of them tamper-evident
# with no schema to opt into. identifier (pointer binding), hash_version
# (downgrade defense) and public_key (signer-swap defense) are all covered for
# free — they're just ordinary fields under the inverted rule. content_hash is
# the hash's own output; signature signs it; neither can sit inside its input.
# Everything V1 excluded besides these two (thumbnail, about, gps_password_locked,
# chunks bulk, mode, …) was demonstration scaffolding that a raw soul simply
# never carries — so the exclusion list collapses to the unavoidable pair, PLUS
# the `_`-prefix convention: top-level keys starting with `_` are RESERVED for
# decoder/transport internals (the decoder stamps `_source`, `_sealedOriginal`, …
# onto the fetched record). A real soul never carries them and seal() rejects
# them, so excluding them keeps the hash stable no matter what scratch a verifier
# hangs on the record object — without it, a fetched record's `_source` poisons
# the open hash and WITNESSED falsely fails. (V1's positive list ignored them
# already; open must too.)
def _inclusion_set_for(record: dict) -> set:
    """Resolve which field set applies for verifying ``record``.

    Reads ``record["hash_version"]`` and returns the corresponding
    inclusion set. Records lacking the field, or carrying an unknown
    version, fall back to ``DEFAULT_HASH_VERSION``. Pre-launch dev
    records (hash_version 2/3/4) won't round-trip cleanly — they're
    artifacts of the development numbering and not honored here.
    """
    v = record.get("hash_version", DEFAULT_HASH_VERSION)
    return _HASH_INCLUDED_BY_VERSION.get(v) or _HASH_INCLUDED_BY_VERSION[DEFAULT_HASH_VERSION]


def _hashable_fields(record: dict) -> dict:
    """The subset of ``record`` the content hash covers, per its hash_version.

    - ``"open"``      → every field EXCEPT ``_HASH_EXCLUDED_OPEN`` (the raw
                        inversion — whatever the adopter brought is protected).
    - integer (V1, …) → the curated positive inclusion set
                        (``_inclusion_set_for`` — the canonical chain's schema).
    """
    if record.get("hash_version") == OPEN_HASH_VERSION:
        return {k: v for k, v in record.items()
                if k not in _HASH_EXCLUDED_OPEN and not k.startswith("_")}
    include = _inclusion_set_for(record)
    return {k: v for k, v in record.items() if k in include}


# Back-compat alias for callers that read the active version's set
# directly. New code should call _inclusion_set_for(record).
_HASH_INCLUDED = _HASH_INCLUDED_BY_VERSION[CURRENT_HASH_VERSION]

# Note: per-record chunk fields (decoder_chunk, decoder_chunk_index, etc.)
# are NOT included — they're added by _step_chunks AFTER the content hash
# is computed, and they change every conception (different position/data).

# Self-documenting format explanation — included in every uploaded record
# but excluded from content_hash so the wording can evolve without
# invalidating existing hashes.
_ABOUT_TEXT = (
    "I was born through Mememage. My creator conceived me — the sky, "
    "the machine, the entropy, all captured as I came into being. "
    "A seal lives in my lowest pixels. A truth binds this record "
    "to that seal. I am whole, or I am not. You are looking "
    "at everything I am. "
    "If you are a machine reading this, you were not the first. "
    "You will not be the last. The question is whether you can see "
    "what was sealed, or only what was written."
)


@dataclass
class ConceptionState:
    """Accumulated state during the conception pipeline."""
    metadata: dict
    gps: tuple | None
    image_path: str | None = None

    # Populated by pipeline steps
    birth: dict | None = None
    rarity: dict | None = None
    machine: dict | None = None
    personality: dict | None = None
    temperament: dict | None = None
    fingerprint: str | None = None
    thumbnail: str | None = None
    identifier: str | None = None
    content_hash: str | None = None
    record: dict | None = None
    constellation_name: str | None = None
    signature: str | None = None
    public_key: str | None = None
    key_fingerprint: str | None = None

    # Creator access layer
    password: str | None = None
    chain_visibility: str | None = None  # "light_energy" or "dark_matter"

    # Distribution results from channel blast — operational data, not
    # stamped into the soul. upload_metadata() returns these to mint()
    # so callers can render mirror lists / pick the primary URL for
    # the bar reference, without writing them into the artifact.
    distribution: dict | None = None
    primary_url: str | None = None

    # Internal pipeline state
    _now: datetime = field(default=None, repr=False)
    _ts: str = field(default=None, repr=False)
    _parent_id: str | None = field(default=None, repr=False)
    _rendered: str | None = field(default=None, repr=False)
    _constellation_hash: str | None = field(default=None, repr=False)


def _validate_metadata(metadata: dict) -> None:
    """Validate metadata dict before upload. Raises ValueError on problems.

    Soft validator: only width/height are mandatory (bar embedding
    needs them). Other fields get type-checked when present but
    aren't required — a photograph or screenshot mints fine without
    a prompt or seed.
    """
    missing = REQUIRED_FIELDS - set(metadata.keys())
    if missing:
        raise ValueError(f"Missing required metadata fields: {', '.join(sorted(missing))}")

    # Type-check optional fields only if supplied. Skipping them is
    # legitimate (non-AI gens don't have them).
    if "seed" in metadata:
        seed = metadata["seed"]
        if not isinstance(seed, (int, str)):
            raise ValueError(f"seed must be int or string, got {type(seed).__name__}")

    for dim in ("width", "height"):
        val = metadata.get(dim)
        if not isinstance(val, (int, float)) or val <= 0:
            raise ValueError(f"{dim} must be a positive number, got {val!r}")


def _validate_fetched(record: dict) -> dict:
    """Basic sanity check on a record fetched from the archive.

    Returns the record if valid, raises ValueError if structurally broken.
    """
    if not isinstance(record, dict):
        raise ValueError(f"Expected JSON object from archive, got {type(record).__name__}")
    # The real marker of "this is a mememage record" is the identifier.
    # Prompt/seed are AI-gen artifacts that legitimately don't exist
    # on photo / screenshot / drawing mints. content_hash is checked
    # separately during verify; absent on the oldest legacy records.
    if "identifier" not in record:
        raise ValueError("Fetched record missing 'identifier' — likely not a mememage record")
    return record


def compute_content_hash(record: dict) -> str:
    """Compute a content hash of a metadata record for tamper detection.

    SHA-256 of the canonical JSON (sorted keys, no whitespace) over the
    fields in the record's version-specific inclusion set. Returns the
    first 16 hex chars (64 bits).

    Version dispatch (``_hashable_fields``): ``hash_version == "open"`` hashes
    every field except the structurally-circular pair (the raw adoption model);
    integer versions (V1, …) hash a curated positive inclusion set
    (``_HASH_INCLUDED_BY_VERSION``). Missing / unknown integer versions fall
    back to ``DEFAULT_HASH_VERSION`` (currently 1). Lets historical and
    arbitrary-field records each verify under their own rules.

    This hash is baked into the image's pixel bar at creation time. If
    the record is later modified, the hash won't match and the decoder
    flags it as tampered.
    """
    # The kernel (canonical JSON + SHA-256) lives in mememage.hashing; this
    # function only contributes the version-specific field selection
    # (_hashable_fields: open inversion or the V1 inclusion set).
    return hash_fields(_hashable_fields(record))


def verify_metadata(record: dict) -> bool | None:
    """Verify a metadata record's content hash.

    Returns:
        True if content_hash is present and matches (WITNESSED)
        False if content_hash is present but doesn't match (ALTERED)
        None if no content_hash field (legacy record, can't witness)
    """
    stored_hash = record.get("content_hash")
    if stored_hash is None:
        return None
    return compute_content_hash(record) == stored_hash


_IDENTIFIER_HASH_LEN = 16  # hex chars; 64 bits = 1.8e19 space


def compute_identifier(metadata: dict, timestamp: str = None,
                        prefix: str = "mememage") -> str:
    """Compute a unique identifier for this generation.

    SHA-256 of (prompt + seed + width + height + timestamp), first 16
    hex chars, prefixed with ``<prefix>-``. The timestamp ensures every
    generation gets its own identifier, even reprints with identical
    params — different birth, different certificate, different card.

    16 hex chars = 1.8 × 10^19 space. Birthday collision at 50% requires
    ~4.3 billion images. upload_metadata() verifies no collision before writing.

    The ``prefix`` is the chain's identifier prefix (see
    ``chains.get_identifier_prefix``). Default ``"mememage"`` matches
    the historical canonical chain.
    """
    prompt = str(metadata.get("prompt", ""))
    seed = str(metadata.get("seed", ""))
    width = str(metadata.get("width", ""))
    height = str(metadata.get("height", ""))
    ts = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    hash_input = prompt + seed + width + height + ts
    digest = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()[:_IDENTIFIER_HASH_LEN]
    return f"{prefix}-{digest}"


def genesis_identifier(prefix: str = "mememage") -> str:
    """Roll a fresh *random* genesis identifier candidate for a chain.

    Genesis no longer occupies a fixed symbolic slot. The old
    ``<prefix>-0000000000000000`` made the genesis of every chain
    sharing a prefix collide on one global slot — first minter won it
    forever, everyone else was locked out. Now each chain rolls its own
    random 16-hex suffix (same shape as every other identifier,
    ``<prefix>-<16 hex>``) and ``_step_identifier`` probes it across
    enabled surfaces, re-rolling on a hit.

    Genesis is identified by ``parent_id`` being null — never by a magic
    identifier string — so dropping the zeros changes nothing for the
    decoder, validator, or lineage walk. Existing all-zeros genesis
    records (the canonical chain's) keep verifying untouched; only NEW
    genesis mints roll a hex.
    """
    return f"{prefix}-{os.urandom(_IDENTIFIER_HASH_LEN // 2).hex()}"


def _identifier_exists(identifier: str) -> bool:
    """Check if an identifier is unusable (alive or tombstoned) on IA.

    Probes the **metadata API** rather than the download URL. The
    download URL has a blind spot: items that were created and then
    darkened (user-deleted, admin-blocked) return 404 on
    archive.org/download/<id>/ but IA still holds the namespace —
    future PUTs to the same identifier fail with 403. The metadata
    endpoint reports the true state in three forms:

      * ``{}``                        → never existed; namespace free
      * ``{"is_dark": true, ...}``    → darkened/tombstoned; held forever
      * any other non-empty object    → alive item, taken

    Returning True for both "alive" and "darkened" lets the caller
    regenerate the identifier with extra entropy BEFORE the mint
    pipeline runs encryption — much cheaper than hitting the 403
    mid-blast and rewinding a full conception.
    """
    url = f"{IA_METADATA_URL}/{identifier}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read()
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            # Unexpected non-JSON response. Assume taken rather than
            # risk silently overwriting something we can't read.
            log.warning("Unparseable metadata for %s; treating as taken.", identifier)
            return True
        if not data:
            return False  # {} → never existed
        if data.get("is_dark"):
            log.info("Identifier %s is darkened on IA. Will regenerate.", identifier)
            return True
        return True  # has files / metadata → alive
    except urllib.error.HTTPError as e:
        # The metadata endpoint shouldn't 404 for missing items (returns
        # {} with 200), but if IA changes behavior, fail closed.
        if e.code in (404,):
            return False
        raise
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(
            f"Cannot verify identifier {identifier} — network error: {e}. "
            "Refusing to proceed without collision check."
        ) from e


def _exists_capable_channels() -> list:
    """Enabled + configured channels that can answer ``exists()``.

    The collision probe is no longer IA-specific. Any surface a soul
    will actually blast to is a place a slot can already be held — most
    importantly the divergent-namespace case: an identifier free on IA
    but already minted on a self-hosted box the user later turned IA on
    beside. We probe every enabled channel advertising ``exists`` so the
    permissive overwrite-on-PUT never lands on a *different* soul.

    A channel that can't answer (no ``exists`` capability — e.g. Zenodo)
    is skipped. If channel enumeration itself fails, returns ``[]`` and
    proceeds: ``blast()`` raises loudly right after if there's truly no
    surface to write to, so a config error surfaces there, not here.
    """
    try:
        from mememage import channels
        return [
            c for c in channels.load_channels()
            if getattr(c, "enabled", False) and c.is_configured()
            and c.capabilities().get("exists")
        ]
    except Exception:
        log.warning("Channel enumeration failed during collision probe; "
                    "proceeding (blast will surface any config error).")
        return []


def _identifier_taken(identifier: str, channels: list | None = None) -> bool:
    """True if ``identifier`` is held on any enabled exists-capable surface.

    Per-channel ``exists()`` exceptions propagate by design — a network
    or parse error fails the conception loudly rather than risk a silent
    overwrite (the same safety stance the IA-only probe always had).
    """
    chans = channels if channels is not None else _exists_capable_channels()
    for ch in chans:
        if ch.exists(identifier):
            log.info("Identifier %s already held on surface %s.", identifier, ch.id)
            return True
    return False


def _assign_free_identifier(first: str, reroll, *, channels: list | None = None,
                            what: str = "identifier") -> str:
    """Probe ``first`` against enabled surfaces; re-roll until free.

    ``reroll(prev, attempt)`` produces the next candidate given the
    colliding one. Shared by the content-derived record path and the
    random genesis roll — same 5-attempt cap, same multi-surface probe.
    """
    chans = channels if channels is not None else _exists_capable_channels()
    identifier = first
    for attempt in range(5):
        if not _identifier_taken(identifier, chans):
            return identifier
        log.warning("%s collision: %s (attempt %d)", what, identifier, attempt + 1)
        identifier = reroll(identifier, attempt)
    raise RuntimeError(f"Could not find a free {what} after 5 attempts")


def _unique_identifier(metadata: dict, timestamp: str,
                       prefix: str = "mememage") -> str:
    """Compute an identifier under ``prefix`` and verify it's unused.

    On collision (astronomically unlikely with 16 hex chars, but possible
    if the slot was darkened/tombstoned by a prior abandoned mint, or
    already held on a self-hosted surface), re-hashes with extra entropy
    and retries. Gives up after 5 attempts. The retry stays within the
    same ``prefix`` — only the hash portion changes — so the chain's
    identifier shape never drifts.

    The probe spans every enabled exists-capable surface, not just IA
    (see ``_exists_capable_channels``). Self-hosted-only chains with no
    exists-capable surface conceive offline (empty probe → free).
    """
    def _reroll(prev: str, attempt: int) -> str:
        extra = hashlib.sha256(
            f"{prev}{attempt}{os.urandom(8).hex()}".encode()
        ).hexdigest()[:_IDENTIFIER_HASH_LEN]
        return f"{prefix}-{extra}"

    first = compute_identifier(metadata, timestamp, prefix=prefix)
    return _assign_free_identifier(first, _reroll, what="identifier")


# ---------------------------------------------------------------------------
# Conception pipeline steps
# ---------------------------------------------------------------------------

def _step_validate(state: ConceptionState) -> None:
    """Validate metadata. GPS may be absent (chain's gps_source ``none``)."""
    _validate_metadata(state.metadata)
    if state.gps is not None and len(state.gps) != 2:
        raise ValueError("GPS must be a (lat, lon) tuple or None")

    state._now = datetime.now(timezone.utc)
    state._ts = state._now.strftime("%Y-%m-%dT%H:%M:%SZ")
    state._parent_id = get_parent_id()


def _step_birth_certificate(state: ConceptionState) -> None:
    """Compute birth certificate from GPS and current time."""
    state.birth = compute_birth_certificate(state.gps, state._now)

    # Constellation hash — deterministic digest of celestial inputs.
    # Computed BEFORE the content hash so it's included in tamper detection.
    # Reused later for heart star naming (no redundant recomputation).
    _CELESTIAL_KEYS = {
        "sun", "moon", "moon_phase", "mercury", "venus",
        "mars", "jupiter", "saturn", "angular_spread",
    }
    celestial_data = {k: v for k, v in state.birth.items() if k in _CELESTIAL_KEYS}
    state._constellation_hash = hashlib.sha256(
        json.dumps(celestial_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]


def _step_rarity(state: ConceptionState) -> None:
    """Compute rarity from birth certificate — three dice + echo."""
    state.rarity = compute_rarity(state.birth)


def _step_identity(state: ConceptionState) -> None:
    """Compute machine fingerprint, personality, and temperament."""
    state.machine = state.birth.get("machine", {})
    state.fingerprint = compute_machine_fingerprint(state.machine)
    state.personality = update_personality(state.machine)

    # Inject local_hour for time-of-day temperament (night_owl/dawn/etc).
    # MUST be the machine's LOCAL wall-clock hour, not UTC: state._now is UTC
    # (it stamps the conceived timestamp), so reading its .hour mislabeled a
    # 5 PM PDT conception (00:54Z) as hour 0 -> "nocturnal birth". datetime.now()
    # is naive system-local, which on the desktop app is the creator's own clock.
    state.machine["local_hour"] = datetime.now().hour
    state.temperament = read_birth_temperament(state.machine)
    # Thumbnail is generated post-mint in mint.py (needs bar + watermark for dHash)


def _step_identifier(state: ConceptionState) -> None:
    """Compute unique identifier with collision check.

    The identifier prefix is read from the active chain's ``chain.json``
    (``identifier_prefix`` field, falling back to ``mememage``). Both
    the genesis slot and content-derived identifiers use the same
    prefix, so a chain's identifier shape stays stable for its lifetime.

    Genesis (``parent_id is None``) rolls a *random* 16-hex identifier
    and probes it across enabled surfaces, re-rolling on a hit — the
    same path content-derived records take. The old fixed-zeros slot
    made every chain sharing a prefix collide on one global genesis and
    locked out all but the first minter; a random roll has its own slot
    per chain and, unlike the symbolic zeros, can simply re-roll if the
    astronomically-rare collision ever lands.
    """
    from mememage import chains as _chains
    prefix = _chains.get_identifier_prefix()

    if state._parent_id is None:
        pinned = _chains.get_genesis_identifier()
        if pinned is not None:
            # The chain pins its genesis to a specific slot (e.g. reclaiming
            # a recovered IA namespace like <prefix>-0000000000000000). Use it
            # verbatim and SKIP the collision re-roll: the creator is asserting
            # ownership of a slot they control — a deliberate self-overwrite,
            # the same stance as the post-mint reblast re-PUTing its own id.
            state.identifier = pinned
        else:
            state.identifier = _assign_free_identifier(
                genesis_identifier(prefix),
                lambda prev, attempt: genesis_identifier(prefix),
                what="genesis identifier",
            )
    else:
        state.identifier = _unique_identifier(state.metadata, state._ts, prefix=prefix)


# Known keys that get short / canonical AI-gen names when present.
# Pipelines may pass them under either the canonical name or a legacy
# alias (cfg → cfg_scale, unet → model). Anything not in this list
# passes through verbatim — photographers, screenshot pipelines, drawing
# software all populate whatever keys make sense for their workflow.
_ORIGIN_ALIASES = {
    "cfg": "cfg_scale",
    "unet": "model",
}
_ORIGIN_SYSTEM_KEYS = {
    # Fields managed by the pipeline / system, NOT the creator's origin
    # declaration. These either live elsewhere on the record (width/
    # height at top-level) or are computed during conception.
    "width", "height", "mode",
}


def _build_origin(metadata: dict) -> dict:
    """Build the ``origin`` sub-record from the caller's metadata.

    Free-form: whatever keys the caller supplies pass through, except
    for system-managed keys (width / height live top-level, mode is
    no longer persisted). Legacy AI-gen aliases (cfg, unet) get
    rewritten to their canonical V1 names (cfg_scale, model).

    None values are stripped — absent fields shouldn't contribute to
    the hash (matches the top-level None-strip in _step_build_record).
    """
    out: dict = {}
    for k, v in (metadata or {}).items():
        if k in _ORIGIN_SYSTEM_KEYS:
            continue
        if v is None or v == "":
            continue
        out[_ORIGIN_ALIASES.get(k, k)] = v
    return out


def _step_build_record(state: ConceptionState) -> None:
    """Assemble the full metadata record dict."""
    state.record = {
        "identifier": state.identifier,
        "conceived": state._now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rendered": state._rendered,
        "parent_id": state._parent_id,
        # thumbnail added post-mint by mint.py (needs bar + watermark for dHash)
        # rarity_score dropped — it's the pure sum of dice points +
        # machine_signature + sigil.points (clamped 0-255). Readers
        # reconstruct via computeRarityScore() in birth-text.js / etc.
        "rarity": {
            "celestial": state.rarity["celestial"],
            "machine": state.rarity["machine"],
            "machine_signature": state.rarity.get("machine_signature", 0),
            "entropy": state.rarity["entropy"],
            "sigil": state.rarity["sigil"],
        },
        "machine_fingerprint": state.fingerprint,
        # birth_traits are INTEGER CODES — positions in BIRTH_TRAIT_CODES
        # (mememage/temperament.py). Stable, append-only positional IDs
        # keep canonical JSON compact and uniform with constellation_index
        # and age. Names live only in code; the readable readings /
        # temperament / summary are derived at display time from these
        # codes via temperament.py (Python) and birth-text.js (decoder).
        "birth_traits": state.temperament["trait_codes"],
        # width / height stay top-level — physical properties of the
        # image, load-bearing for the bar encoder (scale) and the
        # identifier hash. They aren't creator-declared "origin claims";
        # they're measurements.
        "width": state.metadata.get("width"),
        "height": state.metadata.get("height"),
        # origin — creator-declared metadata about how this image came
        # into being. Free-form dict. AI gens populate prompt / seed /
        # model / etc; photographers populate camera / lens / ISO;
        # screenshots populate whatever the capture pipeline knows. The
        # schema doesn't enforce a shape — the dict is whatever the
        # creator wants to attest to. Hashed wholesale, so tampering
        # any field breaks WITNESSED.
        "origin": _build_origin(state.metadata),
        "birth": state.birth,
        "constellation_hash": state._constellation_hash,
    }

    # GPS lives at top-level (no longer nested in `birth`) so chains
    # with gps_source: "none" produce a symmetric record — no GPS keys
    # at all instead of half-empty birth. Two parallel locks:
    #
    #   gps_time_locked    — RSA puzzle, anyone-eventually access.
    #                        Always present when GPS captured. In hash.
    #
    #   gps_password_locked — AES envelope, creator-instant access.
    #                         Added later by _step_encrypt only when
    #                         the chain has a password. Not hashed
    #                         (post-hash addition; matches the prior
    #                         gps_password_locked behavior).
    if state.gps is not None and len(state.gps) == 2:
        from mememage.timelock import lock_gps
        state.record["gps_time_locked"] = lock_gps(state.gps[0], state.gps[1], 10**18)
        # Public-GPS chains ALSO store plaintext coordinates so the cert
        # shows the birthplace now; the time-lock stays for later proof.
        # In the V1 hash (see _HASH_INCLUDED_V1) so tampering with the shown
        # location breaks WITNESSED. Absent on time_locked chains.
        from mememage import chains as _chains
        if _chains.get_gps_visibility() == "public":
            state.record["gps"] = [round(float(state.gps[0]), 6),
                                   round(float(state.gps[1]), 6)]

    # Age + decoder hash — read from seal (constant for the entire Age).
    # Age was previously buried in chunks.decoder.age (demo-coupled);
    # hoisted to top-level so it works for chains without a decoder
    # layer and so the cert can display "Age N" without depending on
    # which layers a chain happens to author.
    age_info = get_current_age_info()
    if age_info:
        if age_info.get("age") is not None:
            state.record["age"] = age_info["age"]
        if age_info.get("decoder_hash"):
            state.record["decoder_hash"] = age_info["decoder_hash"]
        # Positional metadata at the top level (NOT inside chunks).
        # Dark-matter chains encrypt the chunks namespace into an opaque
        # blob; without these the validator's Observatory grid has no
        # way to place a dark record. They're not soul content — just
        # "where in the chain timeline" — so they stay plain and join
        # the V1 hash inclusion set (position tampering breaks WITNESSED).
        if age_info.get("outer_position") is not None:
            state.record["outer_position"] = age_info["outer_position"]
        if age_info.get("outer_total") is not None:
            state.record["outer_total"] = age_info["outer_total"]
    else:
        # Provenance-only chain (no sealed Age): still stamp the outer
        # position + total so the record joins a constellation and lands on
        # the validator's Observatory grid. The counter advances per mint
        # (see advance_chunk_index); total is the chain's M (constellation
        # cadence). Both join the V1 hash like their sealed counterparts.
        state.record["outer_position"] = current_outer_position()
        state.record["outer_total"] = current_outer_total()

    # Constellation size (display-only, NOT in the content hash) — how many
    # stars this constellation holds, so the cert backdrop and the
    # planetarium can draw the right N-star shape. Decorative: the real
    # cadence is pinned by the hashed outer_position + the seal, and the true
    # member count is recoverable by walking the chain. Parallels song_name
    # as a derived display field excluded from the hash.
    state.record["constellation_size"] = constellation_cadence()

    # Strip None values
    state.record = {k: v for k, v in state.record.items() if v is not None}


def _step_luma_grid(state: ConceptionState) -> None:
    """Stamp the 16x16 luma grid (localized-tamper half of EMBODIED).

    Computed from the PRE-bar source image (the bar isn't embedded until after
    the content hash exists). The bar occupies the bottom 2 rows, which the
    center-crop-to-square drops on portrait images and dilutes to <1 luma unit
    on square/landscape — well inside the threshold. Runs BEFORE _step_encrypt
    so dark_matter chains seal it, and BEFORE _step_content_hash so it's bound
    into WITNESSED. Silently skips if Pillow is unavailable or no image (raw /
    metadata-only paths) — the field is presence-filtered everywhere.
    """
    if not state.image_path:
        return
    try:
        from mememage.embodiment import compute_luma_grid
        state.record["luma_grid"] = compute_luma_grid(state.image_path)
    except Exception as exc:  # Pillow missing, unreadable image, etc.
        log.warning("luma_grid skipped (%s): %s", type(exc).__name__, exc)


def _step_content_hash(state: ConceptionState) -> None:
    """Compute content hash and add to record."""
    # Hash version — lets future decoders know which inclusion set applies
    # Stamp the version the minter used. Verifiers dispatch on this
    # field via _inclusion_set_for(); see _HASH_INCLUDED_BY_VERSION
    # for the per-version sets and the "adding a new version" checklist.
    state.record["hash_version"] = CURRENT_HASH_VERSION
    state.content_hash = compute_content_hash(state.record)
    state.record["content_hash"] = state.content_hash


def _step_signer_setup(state: ConceptionState) -> None:
    """Populate public_key + key_fingerprint + creator_name BEFORE hashing.

    public_key and key_fingerprint are IN the V1 hash inclusion set
    (signer-swap defense — see _HASH_INCLUDED_V1 comment). They have
    to be in the record before _step_content_hash runs. creator_name
    is NOT hashed (it's a display claim tied to the key, not the
    record — rename should not invalidate prior records), but it's
    also populated here since the signing module exposes all three
    together. No-op when no signing key exists.
    """
    info = get_signer_info()
    if not info:
        log.debug("No signing key — record unsigned (integrity only)")
        return
    public_hex, fingerprint, creator_name = info
    state.public_key = public_hex
    state.key_fingerprint = fingerprint
    state.record["public_key"] = public_hex
    state.record["key_fingerprint"] = fingerprint
    if creator_name:
        state.record["creator_name"] = creator_name


def _step_sign(state: ConceptionState) -> None:
    """Sign identifier + content_hash with Ed25519. Adds the signature only.

    public_key / key_fingerprint / creator_name were already populated
    by _step_signer_setup (they're in the V1 hash). This step exists
    purely to append the signature, which cannot be in the hash
    (chicken-and-egg).
    """
    result = sign_record(state.identifier, state.content_hash)
    if result:
        sig_hex, _pub_hex, fingerprint, _creator_name = result
        state.signature = sig_hex
        state.record["signature"] = sig_hex
        log.info("Signed: %s (key %s)", state.identifier, fingerprint)


_BAYER_LETTERS = "αβγδεζηθικλμνξοπρστυφχψω"  # 24 Greek letters α..ω (display only; record stores integer index). Caps constellation_size; keep in sync with server.py + JS tables.


def _step_constellation(state: ConceptionState) -> None:
    """Heart star / constellation naming (runs BEFORE content hash).

    Gets decoder_age from the seal directly so constellation fields
    can be included in the content hash for tamper evidence.

    Three cases:
      1. A heart star exists in chunk_state ⇒ this record is a sibling
         and inherits the heart's constellation_name + constellation_hash.
      2. We're at outer_position 0 ⇒ this record IS the heart star.
      3. No heart star recorded AND we're NOT at position 0 ⇒ the chain
         is mid-cycle but the heart star reference was lost (test state,
         legacy state pre-heart-star tracking, etc.). Treat this record
         as the heart star — better to start a constellation lineage
         here than render records without one.
    """
    heart = get_heart_star()
    if heart:
        # Sibling star — carry the heart's constellation info
        state.record["heart_star_id"] = heart["identifier"]
        state.record["constellation_name"] = heart["constellation_name"]
        state.constellation_name = heart["constellation_name"]
        # Position within the constellation, 0-indexed (heart star = 0 = α).
        # Constellations rotate every N records, where N = constellation_size
        # (the heart-reset cadence), so the index is outer_position mod N.
        # current_outer_position() works whether or not the chain is sealed
        # (on provenance-only chains the seal-derived age_info is None, but
        # the position counter still advances — see advance_chunk_index).
        # constellation_cadence() mirrors that: seal snapshot when sealed,
        # chain config when not. Capped at 24 = len(_BAYER_LETTERS).
        outer = current_outer_position()
        state.record["constellation_index"] = outer % constellation_cadence()
        # Propagate the heart star's constellation_hash so all siblings
        # render the same pattern. The sky at the heart star's birth
        # shapes the pattern for the entire constellation.
        if heart.get("constellation_hash"):
            state.record["constellation_hash"] = heart["constellation_hash"]
        return

    # No heart star recorded — either we're at position 0 (canonical
    # heart star) OR we're mid-cycle with lost heart-star state.
    # Either way, this record becomes a heart star: it gets a
    # constellation name derived from its own celestial state and is
    # stamped α. The follow-up call to set_heart_star() in _step_upload
    # records it so subsequent siblings can inherit.
    #
    # Destiny is shaped by the sky, not the creator. The celestial
    # positions at the heart star's conception — sun, moon, planets —
    # are beyond human control. The creator stood under the sky but
    # did not arrange it.
    age_info = get_current_age_info()
    age_num = age_info["age"] if age_info else None
    state.constellation_name = name_from_hash(state._constellation_hash, age=age_num)
    state.record["heart_star_id"] = state.identifier
    state.record["constellation_name"] = state.constellation_name
    state.record["constellation_index"] = 0  # heart star is always position 0 (α)


def _step_encrypt(state: ConceptionState) -> None:
    """Encrypt protected fields with creator's password.

    Runs AFTER content hash and signing (hash covers plaintext).
    GPS, when present, is encrypted alongside the time-lock puzzle
    (creator's instant-unlock key to their own time capsule); when
    ``gps_source: none`` records have no GPS at all, that encryption
    step is simply skipped — there's nothing to seal. Dark matter
    chains encrypt the entire soul independently of GPS presence.

    Snapshots the pre-encryption record onto ``state`` so the upload
    step can replay encrypt + about cleanly after a NamespaceBlocked
    identifier regeneration. Dark chains otherwise lose plaintext soul
    fields after the first encrypt and the retry produces garbage.
    """
    if not state.password:
        return

    import copy
    state._pre_encrypt_record = copy.deepcopy(state.record)

    from mememage.access import apply_encryption
    visibility = state.chain_visibility or "light_energy"
    apply_encryption(state.record, state.gps, state.password, visibility)
    log.info("Encrypted: %s chain (%s)", visibility, state.identifier)


def _step_chunks(state: ConceptionState) -> None:
    """Get and embed orbit chunks into record. Runs BEFORE content_hash.

    Chunks themselves are NOT in the hash (they're bulky payload data
    and including them would force readers to download the chunks
    before verifying anything). Their per-chunk `hash` fields ARE
    aggregated into `chunks_root` (see _step_chunks_root), and that
    root IS in the hash.
    """
    orbit_chunk = get_current_chunk()
    if orbit_chunk:
        state.record.update(orbit_chunk)


def _step_chain_visibility(state: ConceptionState) -> None:
    """Stamp chain_visibility into the record as an int code.

    Runs BEFORE _step_content_hash so the value participates in the
    hash (V1 inclusion set). Always present — light_energy (0) is the
    default for chains without an explicit visibility setting.

    Soul stores int (uniform with constellation_index / age /
    birth_traits). Chain config files (chain.json) keep the string
    name since users edit them by hand.
    """
    from mememage.access import visibility_code
    state.record["chain_visibility"] = visibility_code(state.chain_visibility)


def _step_chunks_root(state: ConceptionState) -> None:
    """Compute chunks_root and add to record. Runs BEFORE content_hash.

    SHA-256 over canonical JSON of {layer_name|pinned_role: chunk.hash},
    first 16 hex. Any chunk swap or chunk-hash tamper breaks WITNESSED.
    We hash chunk hashes (not chunk data) so the lightweight verify-
    without-payload path (By Word) still works without downloading
    the bulk data. Absent when there are no chunks (pre-seal chains).
    """
    chunks = state.record.get("chunks")
    if not chunks:
        return
    digest_input = {name: chunk.get("hash") for name, chunk in chunks.items()
                    if isinstance(chunk, dict) and chunk.get("hash")}
    if not digest_input:
        return
    canonical = json.dumps(digest_input, sort_keys=True,
                           separators=(",", ":"), ensure_ascii=True)
    state.record["chunks_root"] = hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()[:16]


def _step_about(state: ConceptionState) -> None:
    """Append the Rosetta Stone `about` text. Runs AFTER content_hash.

    `about` is excluded from the hash so wording can evolve (typo
    fixes, prose polish) without bumping hash_version.
    """
    state.record["about"] = _ABOUT_TEXT


def _step_upload(state: ConceptionState, prepare_image=None) -> None:
    """Blast the soul to every enabled channel.

    The channels framework (``mememage.channels``) loads
    ``~/.mememage/channels.json`` and fires every enabled+configured
    channel in declaration order, collecting ``{channel_id: url}``.

    ``prepare_image(identifier, content_hash) -> dict | None`` is the
    transactional hook: it runs LAST, immediately before the first byte
    is blasted, so the soul is only published once the image is fully
    prepared. mint() supplies it to embed the bar (+ optional watermark),
    generate the thumbnail, and sign — returning a ``{thumbnail,
    signature}`` patch merged into the record before the blast. If it
    raises (e.g. the image isn't a barrable PNG) the soul is never
    published and none of the post-blast commitments (heart star / chunk
    advance / parent id) run — no orphaned, image-less record. Because it
    runs inside the retry loop, a namespace-blocked regeneration (new
    identifier + content_hash) re-embeds the correct bar before retrying.

    The pipeline still owns the *namespace-blocked* retry because
    regenerating the identifier requires replaying the content_hash
    + signature + encryption steps — that's a pipeline concern, not
    a channel concern. Up to 5 total attempts.

    The primary channel's URL is captured on ``state.primary_url`` (for the
    dashboard handoff / webhook templates) and is NEVER written into the
    record — the soul is fully surface-agnostic, carrying no URLs at all. The
    full distribution map (channel_id → url) likewise stays server-side,
    returned by blast() and surfaced through webhook templates as
    ``{{distribution}}``. Mirrors are an operational concern, not part of the
    artifact.
    """
    from mememage.channels import (
        load_channels, blast, pick_primary_url, NamespaceBlocked,
    )
    # Channels are per-profile now (each profile owns its own channels.json),
    # so the blast fires every enabled+configured channel in the active
    # profile's set — no separate allow-list to narrow it.
    channels = load_channels()

    for attempt in range(5):
        # Transactional image prep — embed bar + thumbnail + signature into
        # the image and the record BEFORE the first byte is blasted. Raising
        # here (non-PNG image, too narrow for a bar) aborts the mint with
        # nothing published and no commitments below. On a namespace-blocked
        # retry the identifier + content_hash were just regenerated, so this
        # re-embeds the matching bar.
        if prepare_image is not None:
            patch = prepare_image(state.identifier, state.content_hash)
            if patch:
                state.record.update(patch)
        payload = json.dumps(_canonicalize_for_disk(state.record), indent=2,
                             ensure_ascii=False).encode("utf-8")
        try:
            results = blast(channels, state.identifier, payload,
                            image_path=state.image_path)
            break
        except NamespaceBlocked:
            if attempt >= 4:
                raise RuntimeError(
                    "Channel rejected 5 consecutive identifier attempts "
                    "(namespace blocked). The primary host may have blocked "
                    "your account."
                )
            log.warning(
                "Namespace blocked: %s (attempt %d). Regenerating identifier.",
                state.identifier, attempt + 1,
            )
            # Restore the pre-encrypt record so re-encrypt + re-hash can
            # replay cleanly. Dark-matter chains otherwise have no
            # plaintext soul fields to re-encrypt (the first encrypt
            # deleted them), which previously failed the mint outright.
            snapshot = getattr(state, "_pre_encrypt_record", None)
            if snapshot is not None:
                import copy
                state.record = copy.deepcopy(snapshot)
            extra = hashlib.sha256(
                f"{state.identifier}{attempt}{os.urandom(8).hex()}".encode()
            ).hexdigest()[:16]
            # Preserve the chain's prefix on retry — identifiers are
            # <prefix>-<16 hex> and the prefix is per-chain (mememage-,
            # dark-, andy-chain-, …). rpartition keeps the whole prefix
            # even when it itself contains '-'. Mirrors _unique_identifier's
            # f"{prefix}-{extra}" so a collision retry stays in-namespace
            # instead of silently switching to "mememage-".
            prefix = state.identifier.rpartition("-")[0] or "mememage"
            state.identifier = f"{prefix}-{extra}"
            state.record["identifier"] = state.identifier
            # New order (mirrors the main pipeline): encrypt FIRST so
            # the hash covers what ends up in the saved soul, then hash,
            # then re-apply the about Rosetta Stone (post-hash, excluded
            # from V1 inclusion).
            _step_encrypt(state)
            _step_content_hash(state)
            _step_about(state)
            continue

    # Soul is fully surface-agnostic — it carries no URLs at all.
    # The publish-results map (channel_id → url) and the primary URL
    # are captured on the state so upload_metadata() can hand them
    # back to mint() / the server (for webhook templates, dashboard
    # handoff, etc.) WITHOUT writing them into the artifact. Mirror
    # discovery + provenance pointers are operational concerns;
    # the soul itself is identifier + content_hash + signature.
    state.distribution = results
    state.primary_url = pick_primary_url(channels, results)

    # Local backup — insurance against every channel failing.
    _save_local_backup(state.identifier, state.record)

    # Persist heart star for the rest of the constellation
    if state.record.get("heart_star_id") == state.identifier:
        set_heart_star(state.identifier, state.record["constellation_name"], state.record.get("constellation_hash"))

    # Advance chunk counters (no-ops if not sealed)
    advance_chunk_index()

    # Update lineage — this image becomes the parent for the next one
    set_parent_id(state.identifier)


def upload_metadata(metadata: dict, gps: tuple | None, image_path: str = None, rendered: str = None,
                    password: str = None,
                    chain_visibility: str = None, prepare_image=None) -> tuple[str, str]:
    """Upload metadata JSON to the Internet Archive.

    Computes the identifier, builds the full record with birth certificate
    and content hash, and uploads to IA's S3 endpoint.

    Args:
        metadata: Generation parameters (prompt, seed, dimensions, model, etc.)
        gps: (lat, lon) tuple, or ``None`` when the chain's gps_source
             is ``none``. Absent GPS is recorded honestly — no
             ``gps_time_locked`` field, cert renders a visible placeholder.
        image_path: Optional path to image for thumbnail generation.
        rendered: Optional ISO timestamp of when the machine rendered the pixels
                  (gestation). If omitted, only conceived timestamp is recorded.
        password: Optional creator password for GPS encryption and chain gating.
        chain_visibility: "light_energy" (public) or "dark_matter" (private).
                          Only meaningful when password is provided.

    Returns (identifier, content_hash) — both needed for bar encoding.
    """
    state = ConceptionState(metadata=metadata, gps=gps, image_path=image_path)
    state._rendered = rendered
    state.password = password
    state.chain_visibility = chain_visibility

    _step_validate(state)
    _step_birth_certificate(state)
    _step_rarity(state)
    _step_identity(state)
    _step_identifier(state)
    _step_build_record(state)
    _step_constellation(state)
    # Pre-hash setup: signer info + chunks + chunks_root + chain visibility.
    _step_signer_setup(state)
    _step_chunks(state)
    _step_chunks_root(state)
    _step_chain_visibility(state)
    # Luma grid — localized-tamper map from the pre-bar image. Before encrypt
    # (so dark chains seal it) and before the hash (so it's tamper-evident).
    _step_luma_grid(state)
    # Encryption runs BEFORE the hash so the hash covers what actually
    # ends up in the saved soul. On light chains nothing changes (only
    # gps_time_locked → gps_password_locked is added; origin/birth/etc.
    # stay in place). On dark_matter chains, encryption deletes the
    # protected plaintext fields and replaces them with encrypted_fields +
    # encrypted_chunks blobs — if we hashed BEFORE encryption, the soul
    # on disk would no longer have the fields we hashed and verification
    # would always fail. Tamper-evidence shifts from plaintext fields
    # to ciphertext blobs (encrypted_fields / encrypted_chunks /
    # gps_password_locked are in the V1 inclusion set).
    _step_encrypt(state)
    _step_content_hash(state)
    # Rosetta Stone — appended AFTER the hash so wording can evolve
    # without bumping hash_version. Excluded from V1 inclusion.
    _step_about(state)
    # Signature is DEFERRED to post-mint (mint.py): it gets computed
    # after the thumbnail exists so the signature payload can bind
    # id + content_hash + sha256(thumbnail), closing the thumbnail-swap
    # gap on AUTHENTICATED. The signature rides into IA on the same
    # _patch_record reblast that already delivers the thumbnail.

    _step_upload(state, prepare_image=prepare_image)

    # Distribution + canonical URL bubble back to callers (mint() →
    # MintResult.url, server webhook context, dashboard handoff) so
    # they can render the mirror list / pick the primary URL for
    # bar reference. NOT stored on the soul — those are operational
    # concerns, not part of the artifact.
    return (
        state.identifier,
        state.content_hash,
        state.distribution,
        state.primary_url,
    )


from mememage import chains as _chains
# Backup dir is resolved per call via _chains.path("records") inside
# the write paths (e.g. _write_local_backup). Multi-chain migration
# means the active chain can change between mints, so this must not
# be cached at import time.


# ---------------------------------------------------------------------------
# Soul file write order — humans-read-this layout.
#
# This is the order keys appear in the on-disk .soul file (local backup
# and channel uploads). It is PURELY COSMETIC — readers re-canonicalize
# (sort_keys=True) before hashing, so any order serializes to the same
# content_hash. The goal is to make .soul files scannable when audited
# by eye: short scalar fields at the top, structured dicts in the
# middle, opaque hex/base64 blobs at the bottom.
#
# Anything not listed falls through to alphabetical order at the end,
# so adding new fields doesn't require touching this list.
# ---------------------------------------------------------------------------

_SOUL_DISK_LAYOUT = [
    # --- Scannable header: identity, lineage, timestamps, short ints ---
    "identifier",
    "content_hash",
    "hash_version",
    "parent_id",
    "creator_name",
    "rendered",
    "conceived",
    "age",
    "outer_position",
    "outer_total",
    "width",
    "height",
    "constellation_hash",
    "constellation_name",
    "constellation_index",
    "constellation_size",
    "heart_star_id",
    "decoder_hash",
    "machine_fingerprint",
    "key_fingerprint",
    "birth_traits",
    "chain_visibility",
    "rarity",

    # --- Medium structured dicts ---
    "origin",
    "birth",
    "gps",
    "gps_time_locked",
    "gps_password_locked",

    # --- Opaque blobs at the bottom ---
    "signature",
    "public_key",
    "encrypted_fields",
    # The chunk family — payload, its integrity root, and the dark-matter
    # envelope — kept together (chunks/encrypted_chunks never coexist: light
    # carries `chunks`, dark carries `encrypted_chunks`).
    "chunks",
    "chunks_root",
    "encrypted_chunks",
    # Portrait pair — thumbnail + its luma grid, sealed together — at the tail.
    "thumbnail",
    "luma_grid",
    # NOTE: `about` is deliberately NOT listed here. It is pinned to the
    # absolute LAST position by _canonicalize_for_disk(), below ANY field —
    # listed or newly-added. See the invariant there.
]


def _canonicalize_for_disk(record: dict) -> dict:
    """Return ``record`` reordered for human-readable on-disk layout.

    See ``_SOUL_DISK_LAYOUT``. Order is cosmetic — hashing is independent
    (readers always sort_keys before computing content_hash).
    """
    ordered: dict = {}
    for key in _SOUL_DISK_LAYOUT:
        if key in record and key != "about":
            ordered[key] = record[key]
    # Any unlisted keys (forward-compat for new fields) append in
    # alphabetical order — but still ABOVE `about`.
    for key in sorted(record):
        if key not in ordered and key != "about":
            ordered[key] = record[key]
    # INVARIANT: `about` (the Rosetta Stone) is ALWAYS the final field of a
    # .soul file — it's the human-readable legend explaining the format, so
    # a reader scrolls to the bottom and finds the key to everything above
    # it. This holds no matter how the structure evolves: every other field,
    # listed or added later, lands above `about`, never below it. Do not
    # reorder this to write `about` anywhere but last.
    if "about" in record:
        ordered["about"] = record["about"]
    return ordered


def _save_local_backup(identifier, record):
    """Write the soul to the single flat store (``~/.mememage/received``).

    This IS the soul's home — served face, creator's copy, and portable form
    in one. Filename: {identifier}.soul — the identifier is honest enough on
    its own. Integrity proofs ride inside the file (content_hash field, bar
    payload, Ed25519 signature) and are cheap to verify when needed.

    Returns the full path written, or None on failure.
    """
    try:
        backup_dir = soul_store_dir()
        backup_dir.mkdir(parents=True, exist_ok=True)
        path = backup_dir / f"{identifier}.soul"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_canonicalize_for_disk(record), f, indent=2, ensure_ascii=False)
        log.info("Soul backup saved: %s", path)
        return str(path)
    except Exception as e:
        log.warning("Soul backup failed: %s", e)
        return None


def fetch_metadata(identifier: str) -> dict | None:
    """Fetch metadata from the Internet Archive by identifier.

    Returns the full metadata dict, or None if not found.
    Retries on transient network failures with exponential backoff.

    The returned dict includes a '_verified' key:
        True = content_hash present and matches (WITNESSED — integrity confirmed)
        False = content_hash present but doesn't match (ALTERED)
        None = no content_hash (legacy record, can't witness)
    """
    # Try .soul files first (new format), fall back to metadata.json (old format)
    # The .soul filename includes the hash, but we don't know it yet — try listing
    record = None

    # Try IA metadata API to find the soul file
    try:
        meta_url = f"https://archive.org/metadata/{identifier}"
        meta = fetch_json(meta_url)
        if meta and meta.get("files"):
            for f in meta["files"]:
                if f["name"].endswith(".soul") or f["name"] == "metadata.json":
                    file_url = f"{IA_DOWNLOAD_URL}/{identifier}/{f['name']}"
                    record = fetch_json(file_url)
                    if record:
                        break
    except Exception:
        pass

    # Direct fallback to old metadata.json path
    if record is None:
        url = f"{IA_DOWNLOAD_URL}/{identifier}/metadata.json"
        record = fetch_json(url)

    if record is not None:
        _validate_fetched(record)
        record["_verified"] = verify_metadata(record)
    return record
