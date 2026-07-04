"""Per-chain state directory management.

Until this module existed, all of Mememage's state lived flat at
``~/.mememage/`` — chain-specific files (sealed chunks, position counter,
lineage DB, local soul backups) mixed with creator-level files (private
key, public key, creator name, easter egg) and system-level files (server
config, TLS certs, sessions).

This module namespaces the **chain-specific** files under
``~/.mememage/chains/<chain_id>/`` so a single creator identity can run
multiple independent chains, each with its own Age cycle, position
counter, and payload configuration.

Files namespaced per chain:
    sealed_chunks.json, chunk_state.json (+ .bak), mememage.db (+ -wal, -shm),
    last_id.json (legacy), records/, chain.json (new — per-chain config)

Files that stay creator-level (unchanged):
    private.key, public.key, creator.txt, revocation.cert, keychain/,
    personality.json

Files that stay system-level (unchanged):
    server.json, certs/, sessions.json, uploads/, server.log

The active chain is identified by ``~/.mememage/current_chain`` (a one-line
text file containing the chain ID). Default is ``aries``.

Backward compatibility:
    ``chains.path(name)`` returns the new namespaced path when the chain
    directory exists, **or the legacy flat path** when it doesn't. This
    lets the codebase be refactored to call ``chains.path()`` without
    forcing immediate migration. Users opt in to migration via
    ``mememage chain migrate``; after that, paths resolve to the new
    location for every subsequent run.
"""

import hashlib
import hmac
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path


MEMEMAGE_ROOT = Path.home() / ".mememage"
CHAINS_ROOT = MEMEMAGE_ROOT / "chains"
CURRENT_CHAIN_FILE = MEMEMAGE_ROOT / "current_chain"

# ---------------------------------------------------------------------------
# Chain-password verifier (rung-1 secrets handling)
# ---------------------------------------------------------------------------
# The chain password is NEVER stored at rest. chain.json holds only a one-way
# PBKDF2 verifier so we can (a) record that a chain is gated and (b) reject a
# wrong password at mint time, without holding anything that can decrypt the
# soul. The actual password is supplied at runtime (per-mint override or
# MEMEMAGE_PASSWORD env) and held only in memory. Stdlib only.
_PW_VERIFIER_ITERS = 600_000  # matches access._PBKDF2_ITERATIONS (OWASP 2024)
log = logging.getLogger(__name__)


def _make_verifier(password):
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PW_VERIFIER_ITERS)
    return {"v": 1, "salt": salt.hex(), "iter": _PW_VERIFIER_ITERS, "hash": h.hex()}


def _check_verifier(password, verifier):
    try:
        salt = bytes.fromhex(verifier["salt"])
        iters = int(verifier.get("iter", _PW_VERIFIER_ITERS))
        h = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), salt, iters)
        return hmac.compare_digest(h.hex(), verifier["hash"])
    except Exception:
        return False


DEFAULT_CHAIN_ID = "default"
DEFAULT_CHAIN_NAME = "Untitled Chain"

# Constellation size — the single knob that derives the decoder cycle K,
# the heart-reset cadence, and the Bayer-letter span. Capped at 24: the
# full Greek alphabet (α..ω) is the Bayer designation space (``core._BAYER_LETTERS``),
# and 24 is also the practical ceiling for generating/rendering a legible
# constellation shape. Default 12 reproduces the historical behavior.
CONSTELLATION_SIZE_MIN = 1
CONSTELLATION_SIZE_MAX = 24  # == len(core._BAYER_LETTERS), the 24 Greek letters; keep in sync
DEFAULT_CONSTELLATION_SIZE = 12

# Identifier format: <prefix>-<16 hex chars>. Default prefix is the
# product name; a creator can override at chain-creation time to claim
# their own IA namespace (e.g. "phoenix-XXXX..."). The override is
# locked at creation — once set, never changes — so the chain's
# identifier shape is part of its identity.
DEFAULT_IDENTIFIER_PREFIX = "mememage"

# IA item names allow alphanumeric characters, periods, dashes, and
# underscores. Empirically (curl tests against archive.org/metadata)
# IA preserves case — MeMeMaGe-XXXX and mememage-XXXX route to
# different items, not the same one — so we let creators express
# whatever case they want. We forbid `.` in the prefix (would confuse
# readers parsing `<prefix>-<hash>`). Length capped at 10 so EVERY chain
# can mint down to 512x512 with the full 64-bit identifier: a 512px bar
# holds 44 bytes, and payload = prefix_len + 34, so prefix_len must be <=10.
# This makes "any chain mints down to 512px" a system invariant a user can't
# break with a long vanity prefix. (Capacity verified in tests/test_bar_*.py.)
import re as _re
_PREFIX_PATTERN = _re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,8}[A-Za-z0-9]$")
PREFIX_MIN_LEN = 3
PREFIX_MAX_LEN = 10


def normalize_identifier_prefix(prefix: str) -> str:
    """Strip whitespace from a user-supplied prefix. Case is preserved.

    IA preserves identifier case (verified empirically), so we don't
    case-fold — a creator who picks ``MeMeMaGe`` gets ``MeMeMaGe-XXXX``
    on disk, in the cert, in URLs. Only leading/trailing whitespace is
    stripped since it's almost always an accidental paste artifact.
    """
    if not isinstance(prefix, str):
        return prefix  # let validate raise the right error
    return prefix.strip()


def validate_identifier_prefix(prefix: str) -> None:
    """Raise ValueError if ``prefix`` is not a legal chain identifier prefix.

    Rules (chosen to be IA-safe + readable, case-preserving):
      - 3 to 10 characters (so 512x512 always fits — see cap note above)
      - letters (any case), digits, ``-``, ``_``
      - must start with a letter
      - must end with a letter or digit (no trailing dash/underscore)
    """
    if not isinstance(prefix, str):
        raise ValueError(f"identifier_prefix must be a string, got {type(prefix).__name__}")
    if len(prefix) < PREFIX_MIN_LEN or len(prefix) > PREFIX_MAX_LEN:
        raise ValueError(
            f"identifier_prefix must be {PREFIX_MIN_LEN}-{PREFIX_MAX_LEN} chars, got {len(prefix)}"
        )
    if not _PREFIX_PATTERN.match(prefix):
        raise ValueError(
            f"identifier_prefix {prefix!r} isn't supported. The prefix is a short "
            "ASCII namespace tag: Latin letters, digits, - and _ (start with a letter, "
            "end with a letter or digit). Non-Latin scripts (Chinese, Farsi, accented "
            "Latin, and so on) aren't supported yet, because the prefix must be valid as "
            "an Internet Archive item name, a filename, and a URL. Your record's identity "
            "is the content hash, which is script-independent."
        )


def get_identifier_prefix(chain_id: str | None = None) -> str:
    """Return the chain's identifier prefix, falling back to the default.

    The prefix is read from ``chain.json`` (top-level ``identifier_prefix``
    field). Chains created before this feature shipped — or chains that
    accepted the default at creation — have no field and resolve to
    ``DEFAULT_IDENTIFIER_PREFIX``. Reading is best-effort: any IO or
    parse error returns the default rather than crashing the mint.
    """
    import json as _json
    cid = chain_id or current()
    meta_path = chain_dir(cid) / "chain.json"
    if not meta_path.exists():
        return DEFAULT_IDENTIFIER_PREFIX
    try:
        data = _json.loads(meta_path.read_text(encoding="utf-8"))
    except (_json.JSONDecodeError, OSError):
        return DEFAULT_IDENTIFIER_PREFIX
    prefix = data.get("identifier_prefix")
    if not isinstance(prefix, str) or not prefix:
        return DEFAULT_IDENTIFIER_PREFIX
    try:
        validate_identifier_prefix(prefix)
    except ValueError:
        # Stored value is malformed — refuse to use it. Fall back to
        # the default rather than minting under an invalid prefix.
        return DEFAULT_IDENTIFIER_PREFIX
    return prefix


# Length of an identifier's hash suffix (16 hex chars). Mirrors
# core._IDENTIFIER_HASH_LEN; kept here to avoid an import cycle at module load.
_GENESIS_SUFFIX_LEN = 16
_HEX_DIGITS = frozenset("0123456789abcdef")


def validate_genesis_identifier(identifier: str, prefix: str) -> None:
    """Raise ValueError if ``identifier`` isn't a canonical genesis slot.

    A pinned genesis must be ``<prefix>-<16 lower-hex>`` — the same shape
    every identifier in the chain takes — AND its prefix must match the
    chain's own ``identifier_prefix``, so the bar's packed payload stays
    self-consistent. All-zeros (``0000000000000000``) is a legal suffix:
    it's the canonical chain's historical genesis on the Internet Archive.
    """
    if not isinstance(identifier, str):
        raise ValueError(
            f"genesis_identifier must be a string, got {type(identifier).__name__}"
        )
    pre, sep, suf = identifier.rpartition("-")
    if not sep or pre != prefix:
        raise ValueError(
            f"genesis_identifier {identifier!r} must start with the chain "
            f"prefix {prefix!r} followed by '-'"
        )
    if len(suf) != _GENESIS_SUFFIX_LEN or any(c not in _HEX_DIGITS for c in suf):
        raise ValueError(
            f"genesis_identifier {identifier!r} suffix must be exactly "
            f"{_GENESIS_SUFFIX_LEN} lower-hex chars"
        )


def get_genesis_identifier(chain_id: str | None = None) -> str | None:
    """Return the chain's pinned genesis identifier, or None if unset.

    When ``chain.json`` carries ``genesis_identifier``, the genesis mint
    (``parent_id is None``) occupies that exact slot verbatim instead of
    rolling a random hex (``core.genesis_identifier``). This reclaims a
    specific namespace — e.g. the canonical chain's historical
    ``mememage-0000000000000000`` on the Internet Archive — and the mint
    pipeline SKIPS the collision re-roll for it (the creator owns the slot,
    a deliberate self-overwrite). Locked-once chain identity, like
    :func:`get_identifier_prefix`.

    Best-effort: any IO/parse error, or a value that doesn't match this
    chain's ``<prefix>-<16 hex>`` shape, returns None — genesis then falls
    back to the random roll rather than minting under a malformed pin.
    """
    import json as _json
    cid = chain_id or current()
    meta_path = chain_dir(cid) / "chain.json"
    if not meta_path.exists():
        return None
    try:
        data = _json.loads(meta_path.read_text(encoding="utf-8"))
    except (_json.JSONDecodeError, OSError):
        return None
    gid = data.get("genesis_identifier")
    if not isinstance(gid, str) or not gid:
        return None
    try:
        validate_genesis_identifier(gid, get_identifier_prefix(cid))
    except ValueError:
        return None
    return gid


# Files at the root of ~/.mememage/ that belong to a chain.
# Used by migrate() to know what to move and by path() for the legacy
# backward-compat fallback.
CHAIN_FILES = (
    "sealed_chunks.json",
    "chunk_state.json",
    "chunk_state.json.bak",
    "mememage.db",
    "mememage.db-wal",  # SQLite WAL companion (only present in WAL mode)
    "mememage.db-shm",
    "last_id.json",     # legacy lineage file
)

# Subdirectories at the root of ~/.mememage/ that belong to a chain.
CHAIN_DIRS = ("records",)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Active chain resolution
# ---------------------------------------------------------------------------

def current() -> str:
    """Return the active chain ID.

    Resolution order:
      1. ``~/.mememage/current_chain`` if set
      2. The single existing chain on disk (when there's exactly one,
         no need to be explicit — "the only chain you have" is the
         active one)
      3. ``DEFAULT_CHAIN_ID`` ("aries") as the ultimate fallback for
         fresh installs

    Never raises, never triggers migration; safe to call at import time.
    Step 2 fixes a long-standing trap where users who renamed/created
    a single non-aries chain still got "aries" returned, masking config
    errors as "chain not found" downstream.
    """
    if CURRENT_CHAIN_FILE.exists():
        try:
            cid = CURRENT_CHAIN_FILE.read_text(encoding="utf-8").strip()
            if cid:
                return cid
        except OSError:
            pass
    # Single-chain shortcut: if exactly one chain exists on disk, use
    # it. Multi-chain installs without current_chain set fall through
    # to DEFAULT_CHAIN_ID — better to be wrong loudly (subsequent ops
    # fail clearly) than silently pick one of N chains.
    try:
        if CHAINS_ROOT.is_dir():
            real_chains = [
                d.name for d in CHAINS_ROOT.iterdir()
                if d.is_dir() and not d.name.startswith(".")
                and (d / "chain.json").exists()
            ]
            if len(real_chains) == 1:
                return real_chains[0]
    except OSError:
        pass
    return DEFAULT_CHAIN_ID


def chain_dir(chain_id: str | None = None) -> Path:
    """Return the directory for the given (or current) chain.

    Returns the path even if the directory doesn't exist; callers can
    mkdir if they need it. Used together with ``path()`` for the legacy
    fallback.
    """
    cid = chain_id or current()
    return CHAINS_ROOT / cid


def path(name: str, chain_id: str | None = None) -> Path:
    """Resolve a chain-scoped file path with legacy fallback.

    If the chain directory exists, return ``CHAINS_ROOT/<id>/name``.
    Otherwise, if a legacy file at ``MEMEMAGE_ROOT/name`` exists, return
    that — this lets unmigrated installations keep working.
    For brand-new writes (neither directory nor legacy file exist),
    returns the new path so the new write goes there.

    The fallback is intentionally narrow: it only triggers when the chain
    directory is **absent**. Once migration has run (or a fresh chain
    has been created), reads and writes always go through the new path.
    """
    cid = chain_id or current()
    target_dir = CHAINS_ROOT / cid
    new_path = target_dir / name

    if target_dir.is_dir():
        return new_path

    # Chain directory doesn't exist. Fall back to legacy if such a file
    # exists at the root. This keeps reads working pre-migration.
    legacy_path = MEMEMAGE_ROOT / name
    if legacy_path.exists():
        return legacy_path
    return new_path


# ---------------------------------------------------------------------------
# Chain lifecycle
# ---------------------------------------------------------------------------

def list_chains() -> list[dict]:
    """Return metadata for every chain currently on disk."""
    if not CHAINS_ROOT.is_dir():
        return []
    out = []
    for d in sorted(CHAINS_ROOT.iterdir()):
        if not d.is_dir():
            continue
        meta_path = d / "chain.json"
        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                meta = {}
        meta.setdefault("id", d.name)
        out.append(meta)
    return out


def info(chain_id: str | None = None) -> dict:
    """Return the chain.json metadata for the given (or current) chain.

    Returns an empty dict if no chain.json exists.
    """
    cid = chain_id or current()
    meta_path = CHAINS_ROOT / cid / "chain.json"
    if not meta_path.exists():
        return {"id": cid}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"id": cid}


def create(chain_id: str, *, visibility: str = "light_energy",
           name: str | None = None,
           identifier_prefix: str | None = None) -> dict:
    """Create a new chain directory with an initial chain.json.

    The new chain starts empty — no sealed Age, no records. Run
    ``site_pack.seal()`` against it to begin Age 1.

    ``identifier_prefix`` is a one-time choice at creation. If omitted,
    the chain uses :data:`DEFAULT_IDENTIFIER_PREFIX` (``mememage``) and
    no field is written to ``chain.json`` — reads fall back to the
    default. If supplied, the prefix is validated, written to disk,
    and **locked**: subsequent ``chain_config.save()`` calls preserve
    whatever is already on disk for this field, so the chain's
    identifier shape stays stable for the life of the chain.

    Raises:
        ValueError: if ``visibility`` or ``identifier_prefix`` is invalid.
        FileExistsError: if the chain already exists.
    """
    if visibility not in ("light_energy", "dark_matter"):
        raise ValueError(f"visibility must be 'light_energy' or 'dark_matter', got {visibility!r}")
    if identifier_prefix is not None:
        identifier_prefix = normalize_identifier_prefix(identifier_prefix)
        validate_identifier_prefix(identifier_prefix)
    target = CHAINS_ROOT / chain_id
    if target.exists():
        raise FileExistsError(f"Chain {chain_id!r} already exists at {target}")
    target.mkdir(parents=True)
    meta = {
        "id": chain_id,
        "name": name or chain_id,
        "visibility": visibility,
        "created_at": _utc_now(),
        "schema_version": 1,
    }
    if identifier_prefix is not None:
        meta["identifier_prefix"] = identifier_prefix
    (target / "chain.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    try:
        os.chmod(str(target / "chain.json"), 0o600)  # may hold password_verifier: owner-only
    except OSError:
        pass
    # Stage an empty Payload/<id>/ so the dashboard's first Build click
    # has a place to land. site_pack.seal() also requires it later.
    try:
        from mememage import payload as _payload
        _payload.payload_dir(chain_id).mkdir(parents=True, exist_ok=True)
    except Exception:
        # Best-effort — chain creation still succeeds if the project tree
        # isn't writable here (e.g. running from a packaged install).
        pass
    return meta


def switch(chain_id: str) -> None:
    """Set the active chain by writing ``~/.mememage/current_chain``.

    Raises FileNotFoundError if the chain doesn't exist.
    """
    if not (CHAINS_ROOT / chain_id).is_dir():
        raise FileNotFoundError(f"Chain {chain_id!r} not found in {CHAINS_ROOT}")
    MEMEMAGE_ROOT.mkdir(parents=True, exist_ok=True)
    CURRENT_CHAIN_FILE.write_text(chain_id + "\n", encoding="utf-8")


def set_password(chain_id, password):
    """Set or clear the chain password -- stores a one-way VERIFIER, never the
    value (rung-1). Empty/None clears it. The verifier records that the chain
    is gated and lets mint reject a wrong password, but cannot decrypt the
    soul, so a leaked chain.json no longer exposes a usable key. The real
    password is supplied at mint time (override or MEMEMAGE_PASSWORD env) and
    held only in memory."""
    target = CHAINS_ROOT / chain_id
    if not target.exists():
        raise FileNotFoundError(f"Chain {chain_id!r} not found")
    meta_path = target / "chain.json"
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            meta = {}
    meta.pop("password", None)  # never persist plaintext; clear any legacy value
    if password:
        meta["password_verifier"] = _make_verifier(password)
    else:
        meta.pop("password_verifier", None)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    try:
        os.chmod(str(meta_path), 0o600)
    except OSError:
        pass
    return {"id": chain_id, "password_set": bool(password)}


def get_password(chain_id=None):
    """DEPRECATED. The password is no longer stored. Returns only a lingering
    legacy plaintext value (pre-rung-1) for migration; None otherwise."""
    pw = info(chain_id).get("password")
    return pw if isinstance(pw, str) and pw else None


def has_password(chain_id=None):
    """Is this chain gated? True when a verifier exists (or legacy plaintext
    lingers). Never exposes the secret."""
    m = info(chain_id)
    return isinstance(m.get("password_verifier"), dict) or bool(m.get("password"))


def verify_password(password, chain_id=None):
    """Does the password match the chain seal? True/False when a verifier
    exists, None when there is none to check against. Callers treat False as a
    hard reject, None/True as proceed."""
    ver = info(chain_id).get("password_verifier")
    if isinstance(ver, dict):
        return _check_verifier(password or "", ver)
    return None


def resolve_password(chain_id=None, override=None):
    """Canonical resolver. Precedence (rung-1: value never from chain.json):
      1. override -- per-mint value the client supplies (creator brings key)
      2. MEMEMAGE_PASSWORD env (from .env) -- unattended-server path, in memory
      3. DEPRECATED legacy plaintext still in chain.json, read with a one-time
         warning so existing chains work until migrate_password converts them
      4. None (light_energy stays public; dark_matter fails clearly at mint)
    """
    if override:
        return override
    try:
        from mememage.config import _load_dotenv
        _load_dotenv()
    except Exception:
        pass
    env_pw = os.environ.get("MEMEMAGE_PASSWORD") or None
    if env_pw:
        return env_pw
    legacy = info(chain_id).get("password")
    if isinstance(legacy, str) and legacy:
        log.warning(
            "Chain %r still stores a plaintext password in chain.json "
            "(deprecated). Run chains.migrate_password() and supply the "
            "password via MEMEMAGE_PASSWORD or per mint.",
            chain_id or "(active)",
        )
        return legacy
    return None


def migrate_password(chain_id):
    """Convert a legacy plaintext chain.json password into a verifier and
    delete the plaintext. Idempotent. Returns {id, migrated: bool}."""
    target = CHAINS_ROOT / chain_id
    if not target.exists():
        raise FileNotFoundError(f"Chain {chain_id!r} not found")
    meta_path = target / "chain.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"id": chain_id, "migrated": False}
    legacy = meta.get("password")
    if not (isinstance(legacy, str) and legacy):
        return {"id": chain_id, "migrated": False}
    meta["password_verifier"] = _make_verifier(legacy)
    meta.pop("password", None)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    try:
        os.chmod(str(meta_path), 0o600)
    except OSError:
        pass
    log.info("Migrated chain %r password to a verifier (plaintext removed).", chain_id)
    return {"id": chain_id, "migrated": True}


def migrate_all_passwords():
    """Run migrate_password for every chain. Returns per-chain results."""
    out = []
    for c in list_chains():
        cid = c.get("id")
        if cid:
            try:
                out.append(migrate_password(cid))
            except Exception as e:
                out.append({"id": cid, "migrated": False, "error": str(e)})
    return out


def get_gps_source(chain_id: str | None = None) -> str:
    """Return the chain's configured GPS source.

    Three values: ``phone`` (default, today's flow), ``machine`` (server-
    side IP geolocation), ``none`` (no GPS captured). Missing or invalid
    values fall back to ``phone`` so legacy chains keep their existing
    behavior. See ``mememage.gps`` for the mode semantics.
    """
    from mememage.gps import GPS_SOURCES, DEFAULT_GPS_SOURCE
    src = info(chain_id).get("gps_source")
    return src if src in GPS_SOURCES else DEFAULT_GPS_SOURCE


def set_gps_source(chain_id: str, gps_source: str) -> dict:
    """Persist the chain's GPS source mode to ``chain.json``.

    Raises ``ValueError`` for unknown modes — the dashboard radio caps
    the input but the API is also reachable by CLI / scripts, so we
    validate at the boundary.
    """
    from mememage.gps import GPS_SOURCES
    if gps_source not in GPS_SOURCES:
        raise ValueError(
            f"gps_source must be one of {GPS_SOURCES}, got {gps_source!r}"
        )
    target = CHAINS_ROOT / chain_id
    if not target.exists():
        raise FileNotFoundError(f"Chain {chain_id!r} not found")
    meta_path = target / "chain.json"
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            meta = {}
    meta["gps_source"] = gps_source
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return {"id": chain_id, "gps_source": gps_source}


def get_gps_visibility(chain_id: str | None = None) -> str:
    """Return the chain's GPS visibility: ``time_locked`` (default) or
    ``public``. Time-locked seals coordinates in the RSA puzzle (private
    now, provable in ~10 years); public ALSO stores plaintext so the cert
    shows the location immediately. Missing/invalid → ``time_locked`` so
    legacy chains stay private. See ``mememage.gps``."""
    from mememage.gps import GPS_VISIBILITIES, DEFAULT_GPS_VISIBILITY
    vis = info(chain_id).get("gps_visibility")
    return vis if vis in GPS_VISIBILITIES else DEFAULT_GPS_VISIBILITY


def set_gps_visibility(chain_id: str, gps_visibility: str) -> dict:
    """Persist the chain's GPS visibility to ``chain.json``.

    Validated at the boundary (the dashboard caps input, but the API is
    reachable by CLI/scripts). Only affects FUTURE conceptions — already-
    minted records keep whatever they were stored with (you cannot
    un-time-lock a record without solving its puzzle)."""
    from mememage.gps import GPS_VISIBILITIES
    if gps_visibility not in GPS_VISIBILITIES:
        raise ValueError(
            f"gps_visibility must be one of {GPS_VISIBILITIES}, got {gps_visibility!r}"
        )
    target = CHAINS_ROOT / chain_id
    if not target.exists():
        raise FileNotFoundError(f"Chain {chain_id!r} not found")
    meta_path = target / "chain.json"
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            meta = {}
    meta["gps_visibility"] = gps_visibility
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return {"id": chain_id, "gps_visibility": gps_visibility}


def get_constellation_size(chain_id: str | None = None) -> int:
    """Return the chain's constellation size (1..12), defaulting to 12.

    One field drives three things: the decoder cycle K, the heart-reset
    cadence (a new heart star every N records), and the Bayer-letter span
    (α..). Missing/invalid values clamp to :data:`DEFAULT_CONSTELLATION_SIZE`
    so legacy chains behave exactly as before."""
    n = info(chain_id).get("constellation_size")
    if isinstance(n, int) and CONSTELLATION_SIZE_MIN <= n <= CONSTELLATION_SIZE_MAX:
        return n
    return DEFAULT_CONSTELLATION_SIZE


def set_constellation_size(chain_id: str, n) -> dict:
    """Persist the chain's constellation size to ``chain.json``.

    The value is coerced to an int and clamped into ``[1, 12]`` (12 = the
    Bayer alphabet cap). Only affects FUTURE Ages — the value is snapshotted
    into the seal, so a change takes effect on the next ``seal()``; the
    current Age keeps the cadence it was sealed with."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        raise ValueError(f"constellation_size must be an integer, got {n!r}")
    n = max(CONSTELLATION_SIZE_MIN, min(CONSTELLATION_SIZE_MAX, n))
    target = CHAINS_ROOT / chain_id
    if not target.exists():
        raise FileNotFoundError(f"Chain {chain_id!r} not found")
    meta_path = target / "chain.json"
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            meta = {}
    meta["constellation_size"] = n
    # Single source of truth: the decoder layer's cycle K IS the
    # constellation size (one full decoder per constellation). If this chain
    # authored a decoder layer in chain.json, keep its K aligned so the
    # Payload tab and chain_config never drift from this value. seal() also
    # rebinds it as a safety net; this keeps the on-disk value honest in the
    # meantime. Only the layer literally named "decoder" is governed.
    layers = meta.get("layers")
    if isinstance(layers, list):
        for ly in layers:
            if isinstance(ly, dict) and ly.get("name") == "decoder":
                ly["K"] = n
                break
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return {"id": chain_id, "constellation_size": n}


# Watermark presets — a chain-level image setting (read live at mint time,
# like GPS), NOT seal-shaped, so it's toggleable anytime and lives with the
# per-chain settings rather than the payload config. The preset -> numeric
# (strength, variance) mapping stays in chain_config._WATERMARK_PRESETS, which
# mint.py consults via chain_config.load().watermark_params(); here we only
# read/write the chosen preset on chain.json.
# The watermark is a simple on/off toggle now. "subtle"/"standard" are accepted
# as LEGACY aliases (pre-release chains that wrote them) and read back as "on".
WATERMARK_PRESETS = ("off", "on", "subtle", "standard")
# Default is ON: a chain with no watermark key in chain.json watermarks. Only an
# explicit {"preset":"off"} opts out (see get_watermark / chain_config).


def get_watermark(chain_id: str | None = None) -> str:
    """Return the chain's watermark setting normalized to ``off`` or ``on``.

    **Default is ON.** Stored on ``chain.json`` as ``{"watermark": {"preset": ...}}``.
    Only an explicit ``{"preset": "off"}`` reads as ``off``; absent (unset) or any
    other preset (``on``, legacy ``subtle``/``standard``) reads as ``on``."""
    wm = info(chain_id).get("watermark")
    if isinstance(wm, dict) and wm.get("preset") == "off":
        return "off"
    return "on"


def set_watermark(chain_id: str, preset: str) -> dict:
    """Persist the chain's watermark setting to ``chain.json`` (immediate — the
    next mint reads it live, no seal needed). The default is ON, so ``off`` is
    stored EXPLICITLY as ``{"preset": "off"}`` to opt out — removing the key
    would fall back to the on default. ``on`` stores ``{"preset": "on"}``
    (legacy names still accepted)."""
    if preset not in WATERMARK_PRESETS:
        raise ValueError(f"watermark must be one of {WATERMARK_PRESETS}, got {preset!r}")
    target = CHAINS_ROOT / chain_id
    if not target.exists():
        raise FileNotFoundError(f"Chain {chain_id!r} not found")
    meta_path = target / "chain.json"
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            meta = {}
    meta["watermark"] = {"preset": preset}   # persist off explicitly (default is on)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return {"id": chain_id, "watermark": preset}


def rename(chain_id: str, new_name: str) -> dict:
    """Update the chain's display ``name`` in chain.json.

    Only the display name changes. The chain id, visibility, created_at
    and any other metadata are preserved. Visibility is locked at
    creation and cannot be changed — that contract is enforced here by
    omission.

    Raises FileNotFoundError if the chain doesn't exist, ValueError if
    the new name is empty.
    """
    new_name = (new_name or "").strip()
    if not new_name:
        raise ValueError("Chain name cannot be empty.")
    target = CHAINS_ROOT / chain_id
    if not target.is_dir():
        raise FileNotFoundError(f"Chain {chain_id!r} not found in {CHAINS_ROOT}")
    meta_path = target / "chain.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            meta = {"id": chain_id}
    else:
        meta = {"id": chain_id}
    meta["name"] = new_name
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def reset_state(chain_id: str, *, clear_records: bool = False,
                to_genesis: bool = False) -> dict:
    """Reset a chain's mint position back to the genesis (heart-star) slot
    by zeroing ``chunk_state.json``. Preserves ``chain.json``,
    ``sealed_chunks.json`` (the chain stays sealed at its current Age),
    and by default the chain's souls in the flat store (past mints stay).

    Backs up the prior ``chunk_state.json`` to
    ``chunk_state.json.pre-reset-<timestamp>`` next to it so the
    previous position trail is recoverable if needed.

    Used for: rebuilding test chains, recovering from corrupted
    chunk_state where the heart_star reference was lost, or starting
    a fresh constellation lineage inside an already-sealed Age.

    ``to_genesis=True`` ALSO clears the lineage thread (``mememage.db``)
    so the very next mint is conceived as a **genesis** (``parent_id``
    null) rather than a child of the last mint. Without this, zeroing the
    position alone leaves ``get_parent_id()`` pointing at the prior mint,
    so the next conception is a child — never the genesis. This is the
    "start the chain completely over" switch (e.g. before a full re-mint
    that should reclaim a pinned ``genesis_identifier`` slot). The prior
    lineage id is returned in ``out["lineage_cleared"]`` for rollback.

    Caveat: souls already published to remote surfaces (IA, peers) cannot be
    deleted here — they remain published. ``clear_records=True`` only purges
    this chain's LOCAL souls from the flat store, by identifier prefix.
    """
    target = CHAINS_ROOT / chain_id
    if not target.is_dir():
        raise FileNotFoundError(f"Chain {chain_id!r} not found in {CHAINS_ROOT}")
    out = {"chain_id": chain_id, "backed_up": None, "records_cleared": False,
           "lineage_cleared": None}
    chunk_state_path = target / "chunk_state.json"
    if chunk_state_path.exists():
        # Back up the existing state with a timestamp suffix so the
        # user can roll back if they reset by accident.
        stamp = _utc_now().replace(":", "").replace("-", "").split("+")[0]
        backup = chunk_state_path.with_name(f"chunk_state.json.pre-reset-{stamp}")
        shutil.copy2(str(chunk_state_path), str(backup))
        out["backed_up"] = str(backup)
    # Write a clean zero-position state. Match the shape of the live
    # state file (mememage.site_embed treats missing keys as 0 anyway,
    # but writing the keys explicitly makes the file self-documenting).
    fresh = {
        "inner_position": 0,
        "outer_position": 0,
        "proof_position": 0,
        "cycle_complete": False,
    }
    chunk_state_path.write_text(json.dumps(fresh, indent=2), encoding="utf-8")
    try:
        os.chmod(str(chunk_state_path), 0o600)
    except OSError:
        pass
    if to_genesis:
        # Sever the lineage thread so the next mint is conceived as a genesis
        # (parent_id null), not a child of the last mint. The prior id is
        # returned for rollback (re-set with lineage.set_parent_id if needed).
        from mememage import lineage as _lineage
        out["lineage_cleared"] = _lineage.clear_lineage(chain_id)
    if clear_records:
        # Souls live in the shared flat store now, not a per-chain dir. Purge
        # this chain's LOCAL souls by its identifier prefix — the deliberate,
        # opt-in equivalent of the old per-chain wipe. (Chains sharing a prefix
        # are caught together; this is an explicit destructive flag.) Published
        # copies on remote surfaces are untouched — use the cleanup tool.
        prefix = get_identifier_prefix(chain_id)
        store = Path(os.path.expanduser("~/.mememage/received"))
        purged = 0
        if store.is_dir():
            for p in store.glob(f"{prefix}-*.soul"):
                try:
                    p.unlink()
                    purged += 1
                except OSError:
                    pass
        out["records_cleared"] = purged
    return out


def _dir_size(path: Path) -> int:
    """Total bytes of all files under ``path`` — for reporting freed disk."""
    total = 0
    try:
        for f in path.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def remove(chain_id: str) -> int:
    """Permanently delete a chain and free its disk — delete means delete.

    Removes the chain dir (``uploads/`` source files, ``sealed_chunks.json``,
    chunk state, lineage) and its Payload staging dir. Not recoverable; the
    caller is responsible for confirming the destructive action. Refuses the
    active chain (switch first). Returns the number of bytes freed.

    Saved presets (``payload_presets/<name>``) and published souls in
    ``received/`` are independent of a chain and are NOT touched — clean those
    via the preset delete and the prefix-scoped purge respectively.

    Raises FileNotFoundError if the chain doesn't exist.
    """
    target = CHAINS_ROOT / chain_id
    if not target.is_dir():
        raise FileNotFoundError(f"Chain {chain_id!r} not found in {CHAINS_ROOT}")
    if chain_id == current():
        raise RuntimeError(
            f"Refusing to remove the active chain {chain_id!r}. "
            f"Switch to a different chain first."
        )
    freed = _dir_size(target)
    # Payload staging dir (if any). Best-effort — missing/unwritable Payload
    # doesn't block the delete.
    try:
        from mememage import payload as _payload
        pdir = _payload.payload_dir(chain_id)
        if pdir.is_dir():
            freed += _dir_size(pdir)
            shutil.rmtree(pdir, ignore_errors=True)
    except Exception:
        pass
    shutil.rmtree(target)
    return freed


# ---------------------------------------------------------------------------
# Migration (legacy flat layout → chains/<id>/)
# ---------------------------------------------------------------------------

def needs_migration() -> bool:
    """Return True iff legacy chain-state files exist at ~/.mememage/ root
    AND no ``chains/`` directory has been created yet.
    """
    if CHAINS_ROOT.is_dir():
        return False
    for f in CHAIN_FILES:
        if (MEMEMAGE_ROOT / f).exists():
            return True
    for d in CHAIN_DIRS:
        if (MEMEMAGE_ROOT / d).is_dir():
            return True
    return False


def migrate(chain_id: str = DEFAULT_CHAIN_ID,
            chain_name: str = DEFAULT_CHAIN_NAME,
            visibility: str = "light_energy") -> dict:
    """Move legacy state at ~/.mememage/ root into chains/<chain_id>/.

    Idempotent: refuses to overwrite an existing chain directory. Writes
    ``chain.json`` with default metadata and ``current_chain`` pointing
    to the migrated chain. Records the move in
    ``~/.mememage/migration.log``.

    Returns a dict describing what moved.
    """
    target = CHAINS_ROOT / chain_id
    if target.exists():
        raise FileExistsError(
            f"Refusing to migrate: chain directory already exists at {target}. "
            f"Move or remove it first."
        )

    target.mkdir(parents=True, exist_ok=True)
    moved_files = []
    moved_dirs = []

    for f in CHAIN_FILES:
        src = MEMEMAGE_ROOT / f
        if src.exists():
            shutil.move(str(src), str(target / f))
            moved_files.append(f)

    for d in CHAIN_DIRS:
        src = MEMEMAGE_ROOT / d
        if src.is_dir():
            shutil.move(str(src), str(target / d))
            moved_dirs.append(d)

    # Write chain.json with the minimal default config.
    meta = {
        "id": chain_id,
        "name": chain_name,
        "visibility": visibility,
        "created_at": _utc_now(),
        "schema_version": 1,
        "migrated_from_legacy": True,
    }
    (target / "chain.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    try:
        os.chmod(str(target / "chain.json"), 0o600)  # may hold password_verifier: owner-only
    except OSError:
        pass

    # Set the active chain pointer.
    CURRENT_CHAIN_FILE.write_text(chain_id + "\n", encoding="utf-8")

    # Migrate the project's flat Payload/ staging area into Payload/<id>/.
    # Best-effort: if anything's already at Payload/<id>/, skip rather than
    # clobber. Move files via shutil so cross-device renames work.
    moved_payload = []
    try:
        from mememage import payload as _payload
        flat_root = _payload.PAYLOAD_ROOT
        new_dir = _payload.payload_dir(chain_id)
        if flat_root.is_dir() and not new_dir.exists():
            new_dir.mkdir(parents=True, exist_ok=True)
            for item in flat_root.iterdir():
                # Skip nested chain dirs (already-migrated layouts).
                if item.is_dir() and (item / "manifest.json").exists():
                    continue
                if item == new_dir:
                    continue
                dst = new_dir / item.name
                if dst.exists():
                    continue
                shutil.move(str(item), str(dst))
                moved_payload.append(item.name)
    except Exception:
        pass

    # Append to migration log (creates on first run).
    log_path = MEMEMAGE_ROOT / "migration.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_entry = (
        f"{_utc_now()} migrated legacy layout to chains/{chain_id}/:\n"
        + "\n".join(f"  - {f}" for f in moved_files)
        + ("\n" if moved_files else "")
        + "\n".join(f"  - {d}/" for d in moved_dirs)
        + ("\n" if moved_dirs else "")
        + "\n".join(f"  - Payload/{p} -> Payload/{chain_id}/{p}" for p in moved_payload)
        + ("\n" if moved_payload else "")
    )
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(log_entry)

    return {
        "chain_id": chain_id,
        "moved_files": moved_files,
        "moved_dirs": moved_dirs,
        "moved_payload": moved_payload,
        "target": str(target),
    }
