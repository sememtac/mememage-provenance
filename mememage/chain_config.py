"""Chain payload configuration — load, validate, and consume chain.json.

A chain's ``chain.json`` defines:

    - **Entries**  — named sources (one or more file paths, typeless bytes)
    - **Layers**   — N parametric cycles, each with K_i and an assigned entry
    - **Pinned**   — specific positions where layers don't cover, populated
                     from entries directly

This module is the runtime mirror of the spec in ``CHAIN_PAYLOAD_CONFIG.md``.
``site_pack.seal()`` reads from a ``ChainConfig`` instead of the hardcoded
DECODER_CHUNKS / PROOF_CHUNKS / TRUTH_CHUNKS / schematic-and-claim layout.

Backward compatibility:
    ``ChainConfig.default()`` returns a config that produces byte-identical
    seal output to the previous hardcoded behavior. Existing chains
    without a chain.json or with a chain.json missing the payload fields
    fall through to this default.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 2  # bumped when chunk_type was collapsed (gzip+base64 everywhere)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Entry:
    """A named bundle of source files.

    An entry is just a list of paths. No type, no encoding hint — the
    layer or pinned-position that references the entry decides how to
    consume the bytes. When ``sources`` has multiple paths, they are
    read in order and concatenated (with a single newline byte between
    them, matching the existing schematic-combine convention).

    For "site"-chunked layers, ``sources`` should be a single directory
    path; the build step packs the directory via ``inline_all()`` and
    treats the result as the entry's bytes.
    """
    name: str
    sources: list[str]

    def to_dict(self) -> dict:
        return {"sources": list(self.sources)}

    @classmethod
    def from_dict(cls, name: str, d: dict) -> "Entry":
        if not isinstance(d, dict):
            raise ValueError(f"Entry {name!r} must be a dict, got {type(d).__name__}")
        # Accept new ``sources: [...]`` or legacy ``source: "..."`` shape.
        if "sources" in d:
            sources = d["sources"]
            if not isinstance(sources, list):
                raise ValueError(f"Entry {name!r}: 'sources' must be a list")
        elif "source" in d:
            sources = [d["source"]]
        else:
            raise ValueError(f"Entry {name!r} missing 'sources'")
        if not sources:
            raise ValueError(f"Entry {name!r}: sources must not be empty")
        # Legacy ``type`` field is silently ignored on read — the chunk
        # type now lives on the layer.
        return cls(name=name, sources=list(sources))


@dataclass
class Layer:
    """A cycle of length K. The assigned entry's bytes are gzip+base64
    encoded, then chunked across the K positions by character count.

    ``reserved`` carves out N positions per cycle for cycle-level metadata
    (today's "6+1 cycle-seal" is reserved=1 on a K=7 layer — the 7th slot
    is reserved for the seal hash, not for a data chunk).

    All layers chunk identically (gzip → base64 → split). The entry's
    content is opaque bytes — readers concat chunks, base64-decode,
    gunzip to recover the original payload.
    """
    name: str
    K: int
    entry: str
    reserved: int = 0

    def chunks_per_cycle(self) -> int:
        return max(0, self.K - self.reserved)

    def to_dict(self) -> dict:
        d = {"name": self.name, "K": self.K, "entry": self.entry}
        if self.reserved:
            d["reserved"] = self.reserved
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Layer":
        if not isinstance(d, dict):
            raise ValueError(f"Layer must be a dict, got {type(d).__name__}")
        name = d.get("name")
        K = d.get("K")
        entry = d.get("entry")
        reserved = int(d.get("reserved", 0))
        if not name:
            raise ValueError("Layer missing 'name'")
        if not isinstance(K, int) or K < 1:
            raise ValueError(f"Layer {name!r} has invalid K={K!r}; must be a positive int")
        if not entry:
            raise ValueError(f"Layer {name!r} missing 'entry'")
        if reserved < 0 or reserved >= K:
            raise ValueError(
                f"Layer {name!r}: reserved={reserved} must satisfy 0 <= reserved < K={K}"
            )
        return cls(name=name, K=K, entry=entry, reserved=reserved)


@dataclass
class Pinned:
    """A specific record position carrying fixed content.

    ``entries`` references one or more entry names. When multiple entries
    are listed, their concatenated bytes are stored at the position. To
    keep entries SEPARATE at the same position, create two pinned rows
    with different roles (today's ``claim`` and ``easter_egg`` at 364).
    """
    position: int
    role: str
    entries: list[str]

    def to_dict(self) -> dict:
        return {"position": self.position, "role": self.role, "entries": list(self.entries)}

    @classmethod
    def from_dict(cls, d: dict) -> "Pinned":
        if not isinstance(d, dict):
            raise ValueError(f"Pinned entry must be a dict, got {type(d).__name__}")
        position = d.get("position")
        role = d.get("role")
        entries = d.get("entries")
        if not isinstance(position, int) or position < 0:
            raise ValueError(f"Pinned position must be a non-negative int, got {position!r}")
        if not role:
            raise ValueError(f"Pinned position {position} missing 'role'")
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"Pinned role {role!r} must list at least one entry")
        # Legacy ``combine`` and ``separator`` fields are silently ignored
        # on read — concatenation is now implicit when multiple entries
        # are listed.
        return cls(position=position, role=role, entries=list(entries))


# Watermark preset → (strength, variance_threshold) mapping. The watermark is a
# simple on/off backup now — strength 16, gate 40 is the one tuned setting:
# invisible in practice (PSNR ~46dB, above the visibility threshold) AND reliable
# (survives q70 + a Discord double-reshare on both smooth and textured images).
# The old subtle(12,50) was invisible but flaky; standard(25,20) was robust but
# its 8×8 artifact showed. (16,40) strictly dominates both. The two old preset
# names are kept as aliases so pre-release chains that wrote them still load.
_WATERMARK_PRESETS: dict[str, tuple[int, int]] = {
    "off": (0, 0),
    "on": (16, 40),
    "subtle": (16, 40),    # legacy alias -> the single watermark
    "standard": (16, 40),  # legacy alias -> the single watermark
}


def _validate_watermark(value: Any) -> dict | None:
    """Normalize a watermark config value to {'preset': str} or None.

    None means UNSET — the chain takes the **default, which is ON**. An
    explicit {'preset': 'off'} is preserved (and persisted) so a chain can
    opt OUT of the default. 'on' (and legacy 'subtle'/'standard') are on.
    Anything else raises ValueError.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(
            f"watermark must be a dict or null, got {type(value).__name__}"
        )
    preset = value.get("preset", "off")
    if preset not in _WATERMARK_PRESETS:
        raise ValueError(
            f"watermark preset must be one of {sorted(_WATERMARK_PRESETS)}, got {preset!r}"
        )
    return {"preset": preset}


@dataclass
class ChainConfig:
    """A chain's full payload configuration."""
    id: str
    name: str
    visibility: str
    M: int
    layers: list[Layer]
    pinned: list[Pinned]
    entries: dict[str, Entry]
    schema_version: int = SCHEMA_VERSION
    watermark: dict | None = None  # None = unset -> default ON; {"preset":"off"} opts out; {"preset":"on"} (legacy subtle/standard) on
    # Stars per constellation (1..24) — the heart-reset cadence, the Bayer
    # span, and the decoder layer's chunk count (one decoder chunk per star).
    # Part of the payload config so it stages through Apply -> next seal like
    # M / layers. Default 12. See mememage.chains.CONSTELLATION_SIZE_*.
    constellation_size: int = 12
    extras: dict[str, Any] = field(default_factory=dict)  # forward-compat fields

    # ----- Convenience lookups -----

    def entry(self, name: str) -> Entry:
        if name not in self.entries:
            raise KeyError(f"Entry {name!r} not defined in this chain config")
        return self.entries[name]

    def layer(self, name: str) -> Layer:
        for ly in self.layers:
            if ly.name == name:
                return ly
        raise KeyError(f"Layer {name!r} not defined in this chain config")

    def has_payload(self) -> bool:
        """True if this chain actually carries a payload to distribute.

        A chain is *provenance-only* when no layer- or pinned-referenced
        entry resolves to any real source. The blank/fresh config (one
        decoder layer, an empty entry) is provenance-only. Provenance-only
        chains conceive freely without a sealed Age — their souls prove
        origin and simply carry no chunks. A chain becomes payload-carrying
        the moment any referenced entry has sources, at which point a sealed
        Age is required so each record gets its chunk assignments.

        This is the single source of truth shared by the conception gate
        (``server._require_chain_sealed``) and the chain badge
        (``server._chain_readiness``) so the two can never disagree about
        whether a chain can be conceived against.
        """
        referenced = {ly.entry for ly in self.layers}
        referenced |= {e for fz in self.pinned for e in fz.entries}
        return any(
            self.entries.get(name) is not None and bool(self.entries[name].sources)
            for name in referenced
        )

    # ----- Serialization -----

    def to_dict(self) -> dict:
        out = {
            "id": self.id,
            "name": self.name,
            "visibility": self.visibility,
            "schema_version": self.schema_version,
            "M": self.M,
            "layers": [ly.to_dict() for ly in self.layers],
            "pinned": [fz.to_dict() for fz in self.pinned],
            "entries": {n: e.to_dict() for n, e in self.entries.items()},
            "constellation_size": self.constellation_size,
        }
        if self.watermark is not None:
            out["watermark"] = self.watermark
        out.update(self.extras)
        return out

    def watermark_params(self) -> tuple[int, int] | None:
        """Return (strength, variance_threshold) for the active preset, or None.

        None return means watermarking is off — the mint pipeline skips
        embed_watermark. The **default is ON**: an unset watermark (None)
        uses the 'on' preset. A chain opts out only by explicitly setting
        {'preset': 'off'}.
        """
        if self.watermark is None:
            return _WATERMARK_PRESETS["on"]        # default ON
        preset = self.watermark.get("preset", "off")
        if preset == "off":
            return None
        return _WATERMARK_PRESETS[preset]

    @classmethod
    def from_dict(cls, d: dict) -> "ChainConfig":
        if not isinstance(d, dict):
            raise ValueError(f"chain.json must be a dict, got {type(d).__name__}")
        cid = d.get("id")
        if not cid:
            raise ValueError("chain.json missing 'id'")
        name = d.get("name", cid)
        visibility = d.get("visibility", "light_energy")
        if visibility not in ("light_energy", "dark_matter"):
            raise ValueError(f"visibility must be 'light_energy' or 'dark_matter', got {visibility!r}")
        M = d.get("M")
        if not isinstance(M, int) or M < 1:
            raise ValueError(f"M must be a positive int, got {M!r}")

        # Entries are required if layers or pinned reference them; default
        # config provides them, so missing entries triggers a fall-through
        # to the default below.
        entries_raw = d.get("entries", {})
        if not isinstance(entries_raw, dict):
            raise ValueError(f"'entries' must be a dict, got {type(entries_raw).__name__}")
        entries = {n: Entry.from_dict(n, e) for n, e in entries_raw.items()}

        layers_raw = d.get("layers", [])
        if not isinstance(layers_raw, list):
            raise ValueError(f"'layers' must be a list, got {type(layers_raw).__name__}")
        layers = [Layer.from_dict(ly) for ly in layers_raw]

        # New key is "pinned"; fall back to legacy "frozen" so existing
        # chain.json files still load.
        pinned_raw = d.get("pinned", d.get("frozen", []))
        if not isinstance(pinned_raw, list):
            raise ValueError(f"'pinned' must be a list, got {type(pinned_raw).__name__}")
        pinned = [Pinned.from_dict(fz) for fz in pinned_raw]

        schema_version = int(d.get("schema_version", SCHEMA_VERSION))

        watermark = _validate_watermark(d.get("watermark"))

        # Constellation size — clamp to [1, 24] (the Bayer/Greek cap), default
        # 12. Tolerant on read (absent / out-of-range -> default) so legacy
        # chain.json without the field, or written by an older client, loads.
        cs = d.get("constellation_size")
        constellation_size = cs if isinstance(cs, int) and 1 <= cs <= 24 else 12

        # Stash anything we didn't consume so we don't drop fields on round-trip.
        # Both "pinned" and the legacy "frozen" key are known so a legacy
        # chain.json doesn't re-emit "frozen" into extras alongside "pinned".
        known = {"id", "name", "visibility", "schema_version", "M",
                 "layers", "pinned", "frozen", "entries", "watermark", "constellation_size"}
        extras = {k: v for k, v in d.items() if k not in known}

        cfg = cls(
            id=cid, name=name, visibility=visibility, M=M,
            layers=layers, pinned=pinned, entries=entries,
            schema_version=schema_version, watermark=watermark,
            constellation_size=constellation_size, extras=extras,
        )
        cfg.validate()
        return cfg

    # ----- Validation -----

    def validate(self) -> None:
        """Raise ValueError if the config is internally inconsistent.

        - M ≥ max(K_i)
        - layer/pinned entry references resolve to defined entries
        - pinned positions in [0, M-1]
        - no duplicate (position, role) pinned entries

        Entries are typeless — they carry bytes. Every layer chunks
        identically (gzip → base64 → split). Pinned positions just
        concatenate referenced entries' bytes and store the same way.
        """
        if not self.layers and not self.pinned:
            raise ValueError("Chain config must define at least one layer or pinned position")

        if not isinstance(self.constellation_size, int) or not (1 <= self.constellation_size <= 24):
            raise ValueError(
                f"constellation_size must be an int in [1, 24], got {self.constellation_size!r}"
            )

        max_K = max((ly.K for ly in self.layers), default=0)
        if max_K > self.M:
            raise ValueError(
                f"M={self.M} smaller than the longest cycle K={max_K}; the longest layer must fit within an Age"
            )

        # Entry references on layers
        for ly in self.layers:
            if ly.entry not in self.entries:
                raise ValueError(
                    f"Layer {ly.name!r} references entry {ly.entry!r} which is not defined"
                )

        # Pinned positions
        seen_roles = set()
        for fz in self.pinned:
            if fz.position < 0 or fz.position >= self.M:
                raise ValueError(
                    f"Pinned position {fz.position} out of range [0, {self.M - 1}] for role {fz.role!r}"
                )
            key = (fz.position, fz.role)
            if key in seen_roles:
                raise ValueError(f"Duplicate pinned role at position {fz.position}: {fz.role!r}")
            seen_roles.add(key)
            for e_name in fz.entries:
                if e_name not in self.entries:
                    raise ValueError(
                        f"Pinned role {fz.role!r} references entry {e_name!r} which is not defined"
                    )

    # ----- Defaults -----

    @classmethod
    def blank(cls, chain_id: str, chain_name: str | None = None,
              visibility: str = "light_energy") -> "ChainConfig":
        """Return a minimal viable config: one decoder layer with an
        empty entry. Users fill in their own sources/layers/pinned
        positions via the dashboard. This is the new fallback for
        fresh chains that have no payload fields in chain.json.

        Why not return an empty config?  ``payload.build()`` and
        ``site_pack.seal()`` both require at least one layer with at
        least one valid entry to do anything useful. The single
        decoder layer + empty entry keeps the dashboard renderable
        and the user can replace/extend from there.
        """
        return cls(
            id=chain_id,
            name=chain_name or chain_id,
            visibility=visibility,
            M=12,  # one decoder cycle — small, sensible starting cadence
            schema_version=SCHEMA_VERSION,
            layers=[
                Layer(name="decoder", K=12, entry="decoder"),
            ],
            pinned=[],
            entries={
                "decoder": Entry("decoder", []),
            },
        )

    @classmethod
    def default(cls, chain_id: str = "example", chain_name: str = "Example Chain",
                visibility: str = "light_energy") -> "ChainConfig":
        """An illustrative nested-cycle configuration, ready to populate.

        Three cycling layers of different lengths plus a few pieces of pinned
        content at chosen positions — a template you can copy and repoint. The
        first two layers build a self-contained web page from a directory /
        file; the rest read from a ``payload/`` staging directory you populate
        at build time.

        Not auto-loaded by ``load()`` for any chain id — every fresh chain
        gets ``ChainConfig.blank()`` (provenance-only). This just shows the
        shape a fully-populated chain can take.
        """
        return cls(
            id=chain_id,
            name=chain_name,
            visibility=visibility,
            M=365,
            schema_version=SCHEMA_VERSION,
            layers=[
                Layer(name="decoder",   K=12,  entry="decoder"),
                Layer(name="validator", K=7,   entry="validator", reserved=1),
                Layer(name="truth",     K=365, entry="truth"),
            ],
            pinned=[
                Pinned(position=360, role="asset-0", entries=["asset0"]),
                Pinned(position=361, role="asset-1", entries=["asset1"]),
                Pinned(position=362, role="asset-2", entries=["asset2"]),
                Pinned(position=363, role="asset-3", entries=["asset3"]),
                Pinned(position=364, role="claim",   entries=["claim"]),
                Pinned(position=364, role="extra",   entries=["extra"]),
            ],
            entries={
                "decoder":   Entry("decoder",   ["docs/"]),
                "validator": Entry("validator", ["docs/validator.html"]),
                "truth":     Entry("truth",     ["payload/truth.md"]),
                "asset0":    Entry("asset0",    ["payload/asset0"]),
                "asset1":    Entry("asset1",    ["payload/asset1"]),
                "asset2":    Entry("asset2",    ["payload/asset2"]),
                "asset3":    Entry("asset3",    ["payload/asset3"]),
                "claim":     Entry("claim",     ["payload/claim"]),
                "extra":     Entry("extra",     ["payload/extra"]),
            },
        )


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load(chain_id: str | None = None) -> ChainConfig:
    """Load the chain config for the given (or current) chain.

    Returns a provenance-only ``blank()`` config (no payload, conceivable
    without a sealed Age) if:
      - No chain.json exists yet
      - chain.json exists but lacks the payload fields (only has id/visibility/
        name — a fresh chain awaiting setup)

    Otherwise parses + validates chain.json and returns it. A chain only
    carries a payload once the user configures one (Payload tab → explicit
    layers/entries written to chain.json).
    """
    from mememage import chains

    cid = chain_id or chains.current()
    chain_meta_path = chains.chain_dir(cid) / "chain.json"

    if not chain_meta_path.exists():
        meta = chains.info(cid)
        # A chain with no config is provenance-only — every id, including
        # "aries". (Earlier this special-cased "aries" into the canonical
        # Mememage payload, which silently handed every new self-hoster a
        # payload-carrying chain they never configured — and then blocked
        # conception on "seal first". The canonical config is Mememage's
        # own content; it lives in ChainConfig.default() and is materialized
        # explicitly into mememage.art's chain.json, not auto-loaded here.)
        return ChainConfig.blank(
            chain_id=cid,
            chain_name=meta.get("name") or cid,
            visibility=meta.get("visibility", "light_energy"),
        )

    try:
        d = json.loads(chain_meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise RuntimeError(f"chain.json at {chain_meta_path} is unreadable: {e}")

    # chain.json carries only identity metadata (no layers / entries) —
    # treat it as a fresh, provenance-only chain awaiting setup. The user
    # adds a payload via the Payload tab (which writes explicit
    # layers/entries) if and when they want one. No id is special-cased.
    if "layers" not in d and "entries" not in d:
        cid_eff = d.get("id", cid)
        cfg = ChainConfig.blank(
            chain_id=cid_eff,
            chain_name=d.get("name", cid_eff),
            visibility=d.get("visibility", "light_energy"),
        )
        # Watermark is a live per-chain setting (set in Config, not the
        # Payload tab), so it can exist on an otherwise-provenance chain.json
        # that has no layers/entries. Honor it here so mint.py — which reads
        # watermark_params() off load() — actually applies it.
        cfg.watermark = _validate_watermark(d.get("watermark"))
        return cfg

    return ChainConfig.from_dict(d)


def save(cfg: ChainConfig, chain_id: str | None = None) -> Path:
    """Write the config to ``chains/<id>/chain.json`` (overwriting).

    **Locked-once fields are preserved from disk.** Some chain.json
    fields are part of the chain's identity and must never change after
    the chain is created — ``identifier_prefix`` and ``created_at``
    today, possibly more later. ``save`` reads the existing file (if
    any), grabs those fields, and re-stamps them into the output —
    overriding whatever the caller's ``cfg`` happens to carry. This
    enforces the contract at the IO layer so no dashboard edit or
    programmatic mistake can drift the chain's identity.
    """
    from mememage import chains
    cid = chain_id or cfg.id
    chain_meta_path = chains.chain_dir(cid) / "chain.json"
    chain_meta_path.parent.mkdir(parents=True, exist_ok=True)

    out = cfg.to_dict()

    # Preserve disk-owned fields from the existing chain.json, if it exists.
    # ``cfg.to_dict()`` models ONLY the payload + chain identity (layers, pinned,
    # entries, visibility, M, constellation_size, …). Everything else in
    # chain.json is owned by OTHER surfaces and a payload Apply must never wipe
    # it: ``password_verifier`` (set_password), ``gps_source`` (gps config),
    # ``preset_name`` (preset apply), and any future chain-property field. So
    # merge the caller's cfg OVER the prior file — unmodeled fields survive —
    # then re-stamp the disk-authoritative ones the caller can never change:
    #   - identifier_prefix / created_at / genesis_identifier: locked-once
    #     chain identity. (genesis_identifier isn't modeled by to_dict, so the
    #     {**prior, **out} merge already preserves it; listing it here makes the
    #     lock explicit and force-stamps the disk value over any stray cfg key.)
    #   - watermark: a Config-tab setting the Payload editor doesn't carry, so
    #     its disk value stays authoritative regardless of what cfg holds.
    # Reading is best-effort: a parse error means no prior commitment.
    disk_authoritative = ("identifier_prefix", "created_at", "genesis_identifier", "watermark")
    if chain_meta_path.exists():
        try:
            prior = json.loads(chain_meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            prior = {}
        out = {**prior, **out}   # cfg wins for what it models; disk keeps the rest
        for key in disk_authoritative:
            if key in prior:
                out[key] = prior[key]
            else:
                # Disk doesn't have the field; don't manufacture one from the
                # caller's cfg either, so the absence stays meaningful.
                out.pop(key, None)

    chain_meta_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return chain_meta_path
