"""Chunk injection for nested cycles.

Emits each record's chunks for its outer position: every cycling layer that's
still active there (a layer of length K is active up to floor(M / K) × K, then
frozen), the outer layer at every position, and any pinned content the chain
config places at that position. All counters naturally arrive at 0 entering
the next Age, so no reset is needed. The layer count, cycle lengths, and
pinned positions all come from the active chain's config.
"""

import hashlib
import json
import logging
import os
import tempfile
import threading
from pathlib import Path

log = logging.getLogger(__name__)

from mememage.config import (
    DECODER_CHUNKS, DECODER_RANGE, OUTER_CYCLE, PROOF_CHUNKS,
    PROOF_CYCLE, PROOF_RANGE, TRUTH_CHUNKS,
)

from mememage import chains
# Chain-scoped state paths resolve per call (not at import) so the
# active chain can be switched without restarting the server. Always
# go through these helpers — never snapshot the return value at module
# load, or chain switches will silently leak across boundaries.
def seal_file() -> Path:
    return chains.path("sealed_chunks.json")

def chunk_state_file() -> Path:
    return chains.path("chunk_state.json")

# The zodiac ages — each seal epoch is named after the next sign
AGE_NAMES = [
    "Age of Aries", "Age of Taurus", "Age of Gemini", "Age of Cancer",
    "Age of Leo", "Age of Virgo", "Age of Libra", "Age of Scorpio",
    "Age of Sagittarius", "Age of Capricorn", "Age of Aquarius", "Age of Pisces",
]

_DEFAULT_STATE = {
    "inner_position": 0,
    "outer_position": 0,
    "proof_position": 0,
    "cycle_complete": False,
}


# Parsed-seal cache (single entry). The seal can be hundreds of MB on a
# large-payload chain, and the mint calls _load_seal() several times per
# conception — re-reading + re-parsing each time multiplied memory until a
# small box OOM'd. Memoize the parsed dict keyed on the file's identity
# (path, mtime, size); a re-seal changes those and busts it. Mints are
# serialized by the server's _mint_lock, so the single entry never thrashes
# between concurrent conceptions on different chains.
_seal_cache = {"key": None, "data": None}
_seal_cache_lock = threading.Lock()


def _load_seal() -> dict | None:
    """Load the active chain's sealed chunks (memoized by file identity).

    Returns None if not yet sealed; raises RuntimeError if the file exists but
    is corrupt/unreadable. The returned dict is SHARED — callers MUST treat it
    read-only (verified: no caller mutates the seal; they read ``layer_chunks``
    and copy chunk data out)."""
    sf = seal_file()
    if not sf.exists():
        return None
    try:
        st = sf.stat()
    except OSError as e:
        raise RuntimeError(f"Seal file exists but is unreadable: {sf}: {e}") from e
    key = (str(sf), st.st_mtime_ns, st.st_size)
    with _seal_cache_lock:
        if _seal_cache["key"] == key:
            return _seal_cache["data"]
    # Parse OUTSIDE the lock — the read+decode is the slow part; don't serialize
    # every caller behind it. A concurrent double-parse on a cold cache is
    # benign (both produce the same dict; last writer wins).
    try:
        data = json.loads(sf.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Sealed chunks file corrupted or unreadable: %s", e)
        raise RuntimeError(
            f"Seal file exists but is corrupt or unreadable: {sf}: {e}"
        ) from e
    with _seal_cache_lock:
        _seal_cache["key"] = key
        _seal_cache["data"] = data
    return data


def _load_chunk_state() -> dict:
    """Load the active chain's chunk state (inner + outer positions).
    Returns default state if file doesn't exist (fresh).
    Raises RuntimeError if file exists but is corrupt/unreadable."""
    csf = chunk_state_file()
    if not csf.exists():
        return dict(_DEFAULT_STATE)
    try:
        state = json.loads(csf.read_text(encoding="utf-8"))
        # Migration: old single-counter state → nested
        if "chunk_index" in state and "inner_position" not in state:
            old_index = state.pop("chunk_index")
            state["inner_position"] = old_index % DECODER_CHUNKS
            state["outer_position"] = old_index % OUTER_CYCLE  # preserve progress
        return state
    except (json.JSONDecodeError, OSError) as e:
        raise RuntimeError(
            f"Chunk state file exists but is corrupt or unreadable: {csf}: {e}"
        ) from e


def _save_chunk_state(state: dict) -> None:
    """Atomically write chunk state for the active chain."""
    csf = chunk_state_file()
    csf.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(csf.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, str(csf))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def is_cycle_complete() -> bool:
    """Check if the current age's outer cycle has completed at least once."""
    seal = _load_seal()
    if seal is None:
        return False
    state = _load_chunk_state()
    return state.get("cycle_complete", False)


def get_current_age_info() -> dict | None:
    """Get info about the current age. Returns None if not sealed.

    All cycle counts come from the SEAL (which snapshots the chain's
    config at seal time), not the demo-flavored module constants. A
    chain with M=1 will report outer_total=1; a chain with no decoder
    layer will report inner_total=0 / decoder_hash=None.
    """
    seal = _load_seal()
    if seal is None:
        return None
    state = _load_chunk_state()
    inner = state.get("inner_position", 0)
    outer = state.get("outer_position", 0)
    outer_total = int(seal.get("outer_cycle") or OUTER_CYCLE)
    # inner_total reflects the decoder layer's K — but only if the seal
    # actually has a decoder layer in its snapshot. Older seals fell
    # back to the demo's DECODER_CHUNKS=12 constant for decoder_chunks
    # even on chains that never defined a decoder layer; cross-check
    # against the chain_config snapshot to skip those phantom values.
    inner_total = 0
    chain_cfg = seal.get("chain_config") or {}
    for ly in (chain_cfg.get("layers") or []):
        if ly.get("name") == "decoder":
            inner_total = int(ly.get("K", 0))
            break
    # Fallback for very old seals without chain_config snapshot.
    if inner_total == 0 and not chain_cfg:
        inner_total = int(seal.get("decoder_chunks") or 0)
    info = {
        "age": seal.get("age", 1),
        "age_name": seal.get("age_name", AGE_NAMES[0]),
        "version": seal["version"],
        "decoder_hash": seal.get("decoder_hash"),
        "inner_position": inner,
        "outer_position": outer,
        "inner_total": inner_total,
        "outer_total": outer_total,
        "decoder_cycles_complete": (outer // inner_total) if inner_total else 0,
        "truth_chunks_distributed": min(outer, seal.get("truth_chunks", outer_total)),
        "cycle_complete": state.get("cycle_complete", False),
    }
    return info


def is_heart_star() -> bool:
    """Check if the next conception will be the heart star (first in Age)."""
    seal = _load_seal()
    if seal is None:
        return False
    state = _load_chunk_state()
    return state.get("outer_position", 0) == 0


def current_outer_position() -> int:
    """The outer-cycle position for the NEXT conception — sealed or not.

    For sealed chains this matches ``get_current_age_info()['outer_position']``;
    for provenance-only chains it's the advancing counter in chunk_state (so
    constellation_index + the Observatory grid still work without a seal)."""
    return _load_chunk_state().get("outer_position", 0)


# The Bayer alphabet caps a constellation at 24 stars (the full Greek
# alphabet α..ω). The authoritative copy of these letters lives in
# ``core._BAYER_LETTERS``, but site_embed must NOT import core (core imports
# site_embed — circular), so the cap is duplicated here as a literal. Keep
# the two in sync.
_BAYER_MAX = 24


def constellation_cadence() -> int:
    """Records per constellation: the heart-reset cadence AND the Bayer-letter
    span (α..ω). Derived from the chain's ``constellation_size`` (1..24).

    Read from the SEAL snapshot when sealed (so the cadence is fixed for the
    whole Age — a dashboard change only takes effect on the next ``seal()``),
    and from the live chain config when not sealed. Old seals predating the
    snapshot fall back to the decoder layer's K (constellation_size drove
    decoder K at seal time), then to the chain config, then to 12."""
    seal = _load_seal()
    if seal is not None:
        n = seal.get("constellation_size")
        if isinstance(n, int) and 1 <= n <= _BAYER_MAX:
            return n
        # Pre-snapshot seal: derive from the decoder layer K it baked in.
        for ly in (seal.get("chain_config") or {}).get("layers") or []:
            if ly.get("name") == "decoder":
                k = ly.get("K")
                if isinstance(k, int) and 1 <= k <= _BAYER_MAX:
                    return k
                break
    try:
        from mememage import chains
        return chains.get_constellation_size()
    except Exception:
        return _BAYER_MAX


def current_outer_total() -> int:
    """Outer-cycle length: the seal's ``outer_cycle`` when sealed, else the
    chain's constellation cadence for provenance-only chains (the Observatory
    ring size — provenance records wrap their position every cadence)."""
    seal = _load_seal()
    if seal is not None:
        return int(seal.get("outer_cycle") or OUTER_CYCLE)
    return constellation_cadence()


def get_heart_star() -> dict | None:
    """Get the current constellation's heart star info."""
    state = _load_chunk_state()
    heart = state.get("heart_star")
    if heart and state.get("outer_position", 0) != 0:
        return heart
    return None


def set_heart_star(identifier: str, constellation_name: str, constellation_hash: str = None) -> None:
    """Record the heart star after the first conception in an Age."""
    state = _load_chunk_state()
    state["heart_star"] = {
        "identifier": identifier,
        "constellation_name": constellation_name,
        "constellation_hash": constellation_hash,
    }
    _save_chunk_state(state)


def _hash12(s: str) -> str:
    """First 12 hex chars of SHA-256 over a UTF-8 string."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def get_current_chunk() -> dict | None:
    """Get the chunks to embed in the next upload.

    Returns None if the site hasn't been sealed yet.

    Returns a dict with a single "chunks" namespace nesting the per-type
    chunk payloads. Reserved keys: decoder, truth, proof, schematic,
    claim, easter_egg. See docs/chunks-spec.md for the schema.

    Each type carries: index, total, hash (sha256[:12] of data), data,
    plus type-specific extras. Sunday proof is a seal record with no
    `data` field — the cross-week integrity bundle lives in `seal`.
    """
    seal = _load_seal()
    if seal is None:
        return None

    state = _load_chunk_state()
    outer = state.get("outer_position", 0) % OUTER_CYCLE

    chunks = {}

    # Uniform layer-chunk emission. Every layer the chain authored —
    # decoder, truth, proof, "candle", "fox", whatever — comes out
    # under its own name with the same shape: {version, index, total,
    # hash, data}. No special-case fields per layer name; demo-flavor
    # extras (age/age_name on decoder, cycle_length/day/seal on proof,
    # reassembly narrative) are gone — they were Mememage's demo
    # bleeding into the generic emission path. Age info lives at the
    # top of the record now (see core.py's _step_build_record).
    #
    # ``reserved`` (per-layer) holds the count of trailing Age
    # positions where this layer does NOT emit — used for layers that
    # leave room for pinned content at the tail of the outer cycle.
    outer_cycle = int(seal.get("outer_cycle") or OUTER_CYCLE)
    layer_chunks_seal = seal.get("layer_chunks", {}) or {}
    for layer_name, layer_data in layer_chunks_seal.items():
        K = layer_data.get("K", 0)
        reserved = layer_data.get("reserved", 0) or 0
        chunks_list = layer_data.get("chunks") or []
        if K < 1 or not chunks_list:
            continue
        if outer >= outer_cycle - reserved:
            continue  # layer reserves the tail positions of the Age
        idx = outer % K
        if idx >= len(chunks_list):
            continue
        data = chunks_list[idx]
        if not data:
            continue
        chunks[layer_name] = {
            "version": layer_data.get("version", ""),
            "index": idx,
            "total": K,
            "hash": _hash12(data),
            "data": data,
        }
        # Carry the original source filename (single-file layers) so the
        # validator restores it on reassembly instead of "<layer>.bin". Same on
        # every chunk of the layer; the validator reads it off any one. Outside
        # the hashed set (chunks_root covers the data hashes, not metadata).
        _fn = layer_data.get("filename")
        if _fn:
            chunks[layer_name]["filename"] = _fn

    # Pinned-chunk emission — driven by the chain config baked into the
    # seal. The author chose which role lands at which position; we
    # honor that without any hardcoded position assumptions.
    #
    # Schematic roles (e.g. "schematic-1") are normalized into a single
    # chunks["schematic"] entry with an `index` derived from the order
    # of schematic positions in the chain, so the validator UI's existing
    # schematic-collection logic keeps working regardless of which
    # positions the chain chose.
    #
    # New seals carry "pinned_chunks"; legacy "frozen_chunks" seals (and
    # older single-field seals) are rehydrated by _legacy_pinned_chunks.
    pinned_chunks_seal = (
        seal.get("pinned_chunks")
        or seal.get("frozen_chunks")
        or _legacy_pinned_chunks(seal)
    )
    schematic_sorted = sorted(
        [fc for fc in pinned_chunks_seal if _is_schematic_role(fc["role"])],
        key=lambda fc: fc["position"],
    )
    schematic_total = len(schematic_sorted)
    for sch_idx, fc in enumerate(schematic_sorted):
        if outer == fc["position"]:
            chunks["schematic"] = {
                "index": sch_idx,
                "total": schematic_total,
                "role": fc["role"],
                "hash": _hash12(fc["data"]),
                "data": fc["data"],
            }
    for fc in pinned_chunks_seal:
        if _is_schematic_role(fc["role"]):
            continue
        if outer == fc["position"]:
            chunks[fc["role"]] = {
                "role": fc["role"],
                "position": fc["position"],
                "hash": _hash12(fc["data"]),
                "data": fc["data"],
            }

    return {"chunks": chunks} if chunks else None


def _is_schematic_role(role: str) -> bool:
    """Return True if a pinned role represents a schematic chunk.

    The validator UI buckets all "schematic-*" / "schematic_*" roles
    into one schematic-download group, indexed by emission order.
    """
    return role.startswith("schematic-") or role.startswith("schematic_")


def _legacy_pinned_chunks(seal: dict) -> list[dict]:
    """Build a pinned_chunks list from an older seal that stored pinned data
    as separate top-level fields, rehydrating it into the current shape so
    site_embed has a single emission path.

    Positions come from seal["chain_config"]["pinned"] (or the legacy "frozen"
    key) when available; otherwise they fall back to the tail of the seal's
    own outer cycle — derived, never a hardcoded calendar.
    """
    _chain_cfg = seal.get("chain_config") or {}
    cfg_pinned = _chain_cfg.get("pinned", _chain_cfg.get("frozen", []))
    _M = int(seal.get("outer_cycle") or OUTER_CYCLE)
    out = []
    schematics = seal.get("schematic_chunks_data") or []
    if schematics:
        sch_positions = sorted(
            [fz for fz in cfg_pinned if _is_schematic_role(fz.get("role", ""))],
            key=lambda fz: fz["position"],
        )
        _tail = max(0, _M - len(schematics))
        for i, data in enumerate(schematics):
            if i < len(sch_positions):
                pos = sch_positions[i]["position"]
                role = sch_positions[i]["role"]
            else:
                pos = _tail + i
                role = f"schematic-{i + 1}"
            out.append({"position": pos, "role": role, "data": data})
    claim_data = seal.get("claim_data")
    if claim_data:
        pos = next((fz["position"] for fz in cfg_pinned if fz.get("role") == "claim"), _M - 1)
        out.append({"position": pos, "role": "claim", "data": claim_data})
    egg = seal.get("easter_egg_data")
    if egg:
        pos = next((fz["position"] for fz in cfg_pinned if fz.get("role") == "easter_egg"), _M - 1)
        out.append({"position": pos, "role": "easter_egg", "data": egg})
    return out


def advance_chunk_index() -> None:
    """Advance counters after a successful upload.

    Decoder (inner): advances for positions 0-359, frozen at 360+.
      360 / 12 = 30 exact. Naturally at 0 when outer reaches 360.
    Proof: advances for positions 0-363, frozen at 364.
      364 / 7 = 52 exact. Naturally at 0 when outer reaches 364.
    Outer: always advances. Wraps at 365 (Age complete).

    All counters arrive at 0 entering the next Age. No reset needed.
    """
    seal = _load_seal()
    state = _load_chunk_state()
    if seal is None:
        # Provenance-only chain (no sealed Age): no decoder / proof / Age
        # cycle, but the OUTER position still advances so records form a
        # constellation (heart star + Bayer-letter siblings) and link —
        # without this the position is frozen at 0, get_heart_star() always
        # returns None (its guard requires outer != 0), and every record
        # becomes its own heart star. Cycle at the chain's constellation
        # cadence (constellation_size; blank chains use 12). On wrap, drop the
        # heart star so the next record opens a fresh constellation — mirrors
        # the sealed chain's per-cadence heart-reset below.
        cycle = constellation_cadence()
        outer = state.get("outer_position", 0)
        next_outer = (outer + 1) % cycle
        if next_outer == 0:
            state.pop("heart_star", None)
        state["outer_position"] = next_outer
        _save_chunk_state(state)
        return

    inner = state.get("inner_position", 0)
    outer = state.get("outer_position", 0)
    proof = state.get("proof_position", 0)

    # Outer always advances
    next_outer = (outer + 1) % OUTER_CYCLE

    # Decoder advances only during active range (0-359)
    next_inner = ((inner + 1) % DECODER_CHUNKS) if outer < DECODER_RANGE else inner

    # Proof advances only during active range (0-363)
    next_proof = ((proof + 1) % PROOF_CYCLE) if outer < PROOF_RANGE else proof

    # Heart-reset cadence: a new constellation (new heart star) every N
    # records, where N = constellation_size. Drop the heart so the next
    # record opens a fresh constellation. Hearts land at outer multiples of
    # the cadence, so constellation_index = outer % N stays 0 at every heart.
    # (Replaces the former per-Age reset — that's now just the N | 365 case.)
    cadence = constellation_cadence()
    if next_outer % cadence == 0:
        state.pop("heart_star", None)

    # If outer wraps, the Age is complete
    if next_outer == 0 and outer == OUTER_CYCLE - 1:
        state["cycle_complete"] = True
        state.pop("heart_star", None)

    state["inner_position"] = next_inner
    state["outer_position"] = next_outer
    state["proof_position"] = next_proof
    _save_chunk_state(state)


# ---------------------------------------------------------------------------
# Reconcile: the living chain is the truth, chunk_state is only a cache
# ---------------------------------------------------------------------------

def walk_living_chain(records: dict | None = None) -> list:
    """Walk the local soul store from genesis and return the living chain.

    A "star" is a record that actually exists and verifies — not a tick of a
    counter. ``chunk_state`` counts every mint ever *attempted* on this chain,
    so a purged dev mint leaves the counter permanently ahead of reality;
    records that were deleted from their surface stop being stars, but the
    counter never learns. Only the records can answer "how many stars have I
    conceived", so this walks them: genesis (``parent_id is None``) forward
    through ``parent_id`` links.

    Returns the chain in birth order (genesis first). Records whose stored
    content_hash doesn't verify are excluded — a corrupt soul is not a star.
    When two files claim the same identifier (an old pre-V1 copy alongside the
    current one) the verifying copy wins.
    """
    from mememage import core

    if records is None:
        records = {}
        store = chains.MEMEMAGE_ROOT / "received"
        if not store.exists():
            return []
        for path in sorted(store.glob("*.soul")):
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            ident = rec.get("identifier")
            if not ident:
                continue
            rec.pop("_verified", None)
            ok = core.verify_metadata(rec) is True
            if ident not in records or (ok and not records[ident][1]):
                records[ident] = (rec, ok)

    prefix = chains.get_identifier_prefix()
    alive = {i: r for i, (r, ok) in records.items()
             if ok and i.rsplit("-", 1)[0] == prefix}
    kids: dict = {}
    for rec in alive.values():
        kids.setdefault(rec.get("parent_id"), []).append(rec)

    genesis = [r for r in alive.values() if r.get("parent_id") is None]
    if not genesis:
        return []
    if len(genesis) > 1:
        # A chain has exactly one genesis (parent_id is null). More than one
        # means a stray record is claiming it — a junk/test soul dropped into
        # the store, or a second chain's record under the same prefix. The
        # hash filter above already drops unverifiable impostors; if two
        # genuinely verify, take the eldest and say so rather than picking
        # arbitrarily (which would silently truncate or re-root the chain).
        genesis.sort(key=lambda r: r.get("conceived", ""))
        log.warning(
            "Multiple verifying genesis records under prefix %r: %s. "
            "Walking from the eldest (%s).",
            prefix, [r["identifier"] for r in genesis], genesis[0]["identifier"],
        )
    chain = []
    cur = genesis[0]
    while cur is not None:
        chain.append(cur)
        children = sorted(kids.get(cur["identifier"], []),
                          key=lambda r: r.get("conceived", ""))
        cur = children[0] if children else None
    return chain


def chain_state_drift() -> dict | None:
    """Compare ``chunk_state`` against the living chain. None when in sync.

    Returns ``{stars, stored_outer, expected_outer, stored_heart,
    expected_heart, contiguous, missing}`` when they disagree, so ``status``
    and the dashboard can say so out loud instead of printing a number that
    quietly isn't true.
    """
    chain = walk_living_chain()
    if not chain:
        return None
    stars = len(chain)
    positions = [r.get("outer_position") for r in chain]
    contiguous = positions == list(range(stars))
    missing = [] if contiguous else [
        p for p in range(max(x for x in positions if x is not None) + 1)
        if p not in positions
    ]

    state = _load_chunk_state()
    stored_outer = state.get("outer_position", 0)
    stored_heart = (state.get("heart_star") or {}).get("identifier")
    expected_heart = chain[-1].get("heart_star_id")

    if stored_outer == stars and stored_heart == expected_heart and contiguous:
        return None
    return {
        "stars": stars,
        "stored_outer": stored_outer,
        "expected_outer": stars,
        "stored_heart": stored_heart,
        "expected_heart": expected_heart,
        "contiguous": contiguous,
        "missing": missing,
    }


def reconcile_from_chain(dry_run: bool = False) -> dict:
    """Rebuild ``chunk_state`` from the living chain. Returns what changed.

    Every counter in chunk_state is derivable from the records themselves —
    ``outer_position`` is the star count (genesis is star 0, so after N stars
    the next position is N), the decoder's ``inner_position`` is
    ``N % DECODER_CHUNKS``, and the heart star is whatever the newest record
    says its heart star is. So drift is always repairable without guessing.

    Refuses on a non-contiguous chain: a gap means a soul is missing locally
    (fetch it from its surface first) and the star count would be a lie in the
    other direction. Backs the old state up before writing.
    """
    chain = walk_living_chain()
    if not chain:
        raise RuntimeError("No living chain found: no verifying genesis record "
                           f"in {chains.MEMEMAGE_ROOT / 'received'}")
    stars = len(chain)
    positions = [r.get("outer_position") for r in chain]
    if positions != list(range(stars)):
        highest = max(p for p in positions if p is not None)
        gaps = [p for p in range(highest + 1) if p not in positions]
        raise RuntimeError(
            f"Refusing to reconcile: the local chain has gaps at outer_position "
            f"{gaps}. {stars} souls walk from genesis but the newest claims "
            f"position {highest}. Fetch the missing soul(s) into "
            f"{chains.MEMEMAGE_ROOT / 'received'} first — otherwise the star "
            f"count would under-report the chain."
        )

    last = chain[-1]
    state = _load_chunk_state()
    before = {
        "outer_position": state.get("outer_position", 0),
        "inner_position": state.get("inner_position", 0),
        "heart_star": (state.get("heart_star") or {}).get("identifier"),
    }

    state["outer_position"] = stars % OUTER_CYCLE
    state["inner_position"] = (stars % DECODER_CHUNKS
                               if stars < DECODER_RANGE
                               else state.get("inner_position", 0))
    state["proof_position"] = (stars % PROOF_CYCLE
                               if stars < PROOF_RANGE
                               else state.get("proof_position", 0))
    # The heart star is a property of the constellation the newest record
    # belongs to — it says so itself. Drop it only when the next record opens
    # a fresh constellation (a heart lands on every cadence multiple).
    if stars % constellation_cadence() == 0:
        state.pop("heart_star", None)
    elif last.get("heart_star_id"):
        state["heart_star"] = {
            "identifier": last["heart_star_id"],
            "constellation_name": last.get("constellation_name"),
            "constellation_hash": last.get("constellation_hash"),
        }

    after = {
        "outer_position": state["outer_position"],
        "inner_position": state["inner_position"],
        "heart_star": (state.get("heart_star") or {}).get("identifier"),
    }
    if not dry_run:
        csf = chunk_state_file()
        if csf.exists():
            # Stamp the backup with the newest star's conceived time — a fact
            # read from the chain, so the name is deterministic (no clock read).
            stamp = (last.get("conceived") or "unknown").replace(":", "").replace("-", "")
            backup = csf.with_name(f"{csf.name}.pre-reconcile-{stamp}")
            backup.write_text(csf.read_text(encoding="utf-8"), encoding="utf-8")
        _save_chunk_state(state)
    return {"stars": stars, "before": before, "after": after,
            "last_star": last["identifier"], "dry_run": dry_run}
