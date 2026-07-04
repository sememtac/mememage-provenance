"""Nested-cycle seal — several cadences on one track.

A chain can run multiple cycling layers of different lengths over one outer
cycle of M positions. Each layer of length K tiles floor(M / K) whole cycles
(positions 0 .. that product − 1), then freezes for the remainder; the outer
layer runs every position. Pinned content sits at chosen positions the chain
config declares — often the remainder positions a cycling layer leaves free.

All counters naturally arrive at 0 entering the next Age, so no reset is
needed. The exact layer count, cycle lengths, and pinned positions come from
the active chain's config — nothing here is hardcoded.
"""

import base64
import gzip
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from mememage.config import (
    DECODER_CHUNKS,
    OUTER_CYCLE,
    PROOF_CHUNKS,
    PROOF_CYCLE,
    TRUTH_CHUNKS,
)
from mememage.site_embed import (
    AGE_NAMES,
    _load_chunk_state,
    _load_seal,
    _save_chunk_state,
    is_cycle_complete,
)

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
TRUTH_PATH = Path(__file__).resolve().parent.parent / "docs" / "truth.md"
PROOF_PATH = Path(__file__).resolve().parent.parent / "docs" / "validator.html"


# ---------------------------------------------------------------------------
# Decoder HTML packing
# ---------------------------------------------------------------------------

_MIME_BY_EXT = {
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif":  "image/gif",
    ".svg":  "image/svg+xml",
    ".webp": "image/webp",
    ".ico":  "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf":  "font/ttf",
    ".otf":  "font/otf",
    ".mp3":  "audio/mpeg",
    ".ogg":  "audio/ogg",
    ".wav":  "audio/wav",
    ".mp4":  "video/mp4",
    ".webm": "video/webm",
    ".json": "application/json",
}


def _is_external(url: str) -> bool:
    """Return True for absolute / cross-origin / data URLs we shouldn't inline."""
    if not url:
        return True
    return url.startswith(("http://", "https://", "//", "data:", "mailto:", "#"))


def _inline_css_urls(css_text: str, css_dir: Path) -> str:
    """Rewrite url(...) references inside CSS to data URIs.

    Resolves relative paths against the CSS file's own directory.
    Skips external URLs and ones that fail to read.
    """
    def repl(m):
        raw = m.group(1).strip().strip('"').strip("'")
        if _is_external(raw):
            return m.group(0)
        asset_path = (css_dir / raw.split("?")[0].split("#")[0]).resolve()
        try:
            data = asset_path.read_bytes()
        except (OSError, FileNotFoundError):
            return m.group(0)
        mime = _MIME_BY_EXT.get(asset_path.suffix.lower(), "application/octet-stream")
        b64 = base64.b64encode(data).decode("ascii")
        return f"url(data:{mime};base64,{b64})"
    return re.sub(r'url\(([^)]+)\)', repl, css_text)


def inline_html(html_path: Path) -> str:
    """Pack any HTML file into one self-contained string.

    Walks the page and inlines every reference relative to the HTML's
    own directory:

      - ``<link rel="stylesheet" href=…>``   → ``<style>…</style>``
      - ``<script src=…>``                    → ``<script>…</script>``
      - ``<img src=…>``, ``<source src=…>``   → ``data:…;base64,…``
      - ``<link rel="icon" href=…>``          → data URI
      - ``url(…)`` inside inlined CSS         → data URI

    External URLs (http://, https://, data:, //) are left untouched.
    Missing files are left untouched (the page will render with a broken
    reference but the packer doesn't crash). Use ``inline_all`` for the
    decoder folder — it adds a runtime asset-map glob that ``inline_html``
    deliberately does not, so any HTML can be a packable entry.
    """
    html_path = Path(html_path)
    base = html_path.parent
    html = html_path.read_text(encoding="utf-8")

    # Stylesheets
    def repl_css(m):
        href = m.group(1).split("?")[0]
        if _is_external(href):
            return m.group(0)
        css_path = (base / href).resolve()
        try:
            css = css_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return m.group(0)
        css = re.sub(r'@import\s+url\([^)]+\);\s*', '', css)
        css = _inline_css_urls(css, css_path.parent)
        return f"<style>\n{css}\n</style>"
    html = re.sub(r'<link\s+rel="stylesheet"\s+href="([^"]+)"[^>]*>', repl_css, html)

    # Scripts
    def repl_js(m):
        src = m.group(1).split("?")[0]
        if _is_external(src):
            return m.group(0)
        js_path = (base / src).resolve()
        try:
            js = js_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return m.group(0)
        return f"<script>\n{js}\n</script>"
    html = re.sub(r'<script\s+src="([^"]+)"[^>]*></script>', repl_js, html)

    # Inline-able binary assets: <img>, <source>, <video poster>, link rel=icon
    def repl_asset(m, attr_name):
        url = m.group(1).split("?")[0].split("#")[0]
        if _is_external(url):
            return m.group(0)
        asset_path = (base / url).resolve()
        try:
            data = asset_path.read_bytes()
        except (OSError, FileNotFoundError):
            return m.group(0)
        mime = _MIME_BY_EXT.get(asset_path.suffix.lower(), "application/octet-stream")
        b64 = base64.b64encode(data).decode("ascii")
        return m.group(0).replace(m.group(1), f"data:{mime};base64,{b64}")
    html = re.sub(r'<img\s[^>]*?src="([^"]+)"[^>]*>',     lambda m: repl_asset(m, "src"),  html)
    html = re.sub(r'<source\s[^>]*?src="([^"]+)"[^>]*>',  lambda m: repl_asset(m, "src"),  html)
    html = re.sub(r'<video\s[^>]*?poster="([^"]+)"[^>]*>', lambda m: repl_asset(m, "poster"), html)
    html = re.sub(r'<link\s[^>]*?rel="icon"[^>]*?href="([^"]+)"[^>]*>', lambda m: repl_asset(m, "href"), html)
    html = re.sub(r'<link\s[^>]*?href="([^"]+)"[^>]*?rel="icon"[^>]*>', lambda m: repl_asset(m, "href"), html)

    # Runtime asset lookups: if any inlined JS calls `assetUrl(...)` or
    # references INLINE_ASSETS, build the same lookup map the decoder
    # packer does — globbed image files keyed by path relative to the
    # HTML's own directory. Only triggers when the page actually uses
    # the pattern, so simple pages stay small.
    if "assetUrl(" in html or "INLINE_ASSETS" in html:
        asset_map = {}
        IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
        for f in sorted(base.rglob("*")):
            if not f.is_file() or f.suffix.lower() not in IMG_EXTS:
                continue
            try:
                if f.stat().st_size > 2_000_000:
                    continue
                data = f.read_bytes()
            except OSError:
                continue
            mime = _MIME_BY_EXT.get(f.suffix.lower(), "application/octet-stream")
            rel = f.relative_to(base).as_posix()
            asset_map[rel] = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
        if asset_map:
            asset_js = "<script>var INLINE_ASSETS = " + json.dumps(asset_map) + ";</script>\n"
            html = html.replace("<script>", asset_js + "<script>", 1)

    return html


def inline_all(docs_dir: Path = DOCS_DIR) -> str:
    """Build a single self-contained HTML file from the split source files."""
    html = (docs_dir / "index.html").read_text(encoding="utf-8")

    for css_match in re.finditer(r'<link\s+rel="stylesheet"\s+href="([^"]+)"[^>]*>', html):
        css_path = docs_dir / css_match.group(1).split("?")[0]
        css_content = css_path.read_text(encoding="utf-8")
        css_content = re.sub(r'@import\s+url\([^)]+\);\s*', '', css_content)
        html = html.replace(css_match.group(0), f"<style>\n{css_content}\n</style>")

    for script_match in re.finditer(r'<script\s+src="([^"]+)"[^>]*></script>', html):
        # Strip the cache-bust query (?v=<sha>) before resolving the file —
        # mirrors the CSS branch above and inline_html's script branch.
        js_path = docs_dir / script_match.group(1).split("?")[0]
        js_content = js_path.read_text(encoding="utf-8")
        html = html.replace(script_match.group(0), f"<script>\n{js_content}\n</script>")

    asset_map = {}
    # Inline all image assets — planets, trait icons, etc.
    for asset_dir, prefix in [("planets", "planets"), ("img/traits", "img/traits")]:
        full_dir = docs_dir / asset_dir
        if full_dir.is_dir():
            for png in sorted(full_dir.glob("*.png")):
                png_data = png.read_bytes()
                b64 = base64.b64encode(png_data).decode("ascii")
                asset_map[f"{prefix}/{png.name}"] = f"data:image/png;base64,{b64}"

    # Inline the canonical example soul — the "see an example" button loads
    # this record; bundling it keeps standalone.html fully self-contained.
    example_soul = docs_dir / "samples" / "example.soul"
    if example_soul.is_file():
        soul_text = example_soul.read_text(encoding="utf-8")
        b64 = base64.b64encode(soul_text.encode("utf-8")).decode("ascii")
        asset_map["samples/example.soul"] = f"data:application/json;base64,{b64}"

    if asset_map:
        asset_js = "<script>var INLINE_ASSETS = " + json.dumps(asset_map) + ";</script>\n"
        html = html.replace("<script>", asset_js + "<script>", 1)

    return html


def pack_site(docs_dir: Path = DOCS_DIR) -> tuple[str, str]:
    """Pack the decoder site into a gzipped, base64-encoded string.

    Returns (base64_payload, version_hash).
    """
    html = inline_all(docs_dir)
    gz_bytes = gzip.compress(html.encode("utf-8"), compresslevel=9)
    version_hash = hashlib.sha256(gz_bytes).hexdigest()[:12]
    b64 = base64.b64encode(gz_bytes).decode("ascii")
    return b64, version_hash


def _get_chunk(record: dict, chunk_type: str) -> dict | None:
    """Read a chunk by type from a record, handling both shapes.

    New nested shape:  record["chunks"][chunk_type] = {index, total, hash, data, ...}
    Legacy flat shape: record["{chunk_type}_chunk"], record["{chunk_type}_chunk_index"], etc.

    Returns a dict with normalized keys (`index`, `total`, `hash`, `data`,
    plus type-specific extras), or None if the record doesn't carry
    that chunk type.

    Drop the legacy fallback after the constellation re-mint completes
    — see commit #5 in chunks-spec.md.
    """
    # New shape
    chunks = record.get("chunks")
    if isinstance(chunks, dict) and chunk_type in chunks:
        return chunks[chunk_type]

    # Legacy fallback — flat fields. Reconstruct the nested dict shape.
    data_key = f"{chunk_type}_chunk"
    if data_key not in record:
        return None
    out = {"data": record[data_key]}
    # Common extras
    if f"{chunk_type}_chunk_index" in record:
        out["index"] = record[f"{chunk_type}_chunk_index"]
    if f"{chunk_type}_total_chunks" in record:
        out["total"] = record[f"{chunk_type}_total_chunks"]
    if f"{chunk_type}_chunk_hash" in record:
        out["hash"] = record[f"{chunk_type}_chunk_hash"]
    if f"{chunk_type}_version" in record:
        out["version"] = record[f"{chunk_type}_version"]
    # Decoder-only legacy keys
    if chunk_type == "decoder":
        if "decoder_age" in record:      out["age"] = record["decoder_age"]
        if "decoder_age_name" in record: out["age_name"] = record["decoder_age_name"]
        if "decoder_reassembly" in record: out["reassembly"] = record["decoder_reassembly"]
    # Schematic uses a different naming for index/total in legacy shape
    if chunk_type == "schematic":
        if "schematic_index" in record: out["index"] = record["schematic_index"]
        if "schematic_total" in record: out["total"] = record["schematic_total"]
    return out


def chunk_payload(b64: str, target_chunks: int) -> list[str]:
    """Split base64 string into exactly target_chunks pieces.

    Pads with empty strings if the payload is short enough that ceiling
    division produces fewer pieces than requested. Always returns a list
    of exactly ``target_chunks`` entries so downstream code can index
    positions by cycle slot without bounds-checking.
    """
    if target_chunks < 1:
        return []
    if not b64:
        return [""] * target_chunks
    chunk_size = -(-len(b64) // target_chunks)
    chunks = [b64[i:i + chunk_size] for i in range(0, len(b64), chunk_size)]
    while len(chunks) < target_chunks:
        chunks.append("")
    return chunks


# ---------------------------------------------------------------------------
# Universal entry encoding — gzip → base64
# ---------------------------------------------------------------------------
#
# Every layer chunks identically: gzip the raw bytes, base64-encode, then
# split into K - reserved pieces by character count. Every pinned position
# stores the same gzip+base64 encoding of its concatenated entries. The
# reader path is symmetric: concat chunks → atob → gunzip → bytes.

def _encode_entry(raw: bytes) -> tuple[str, str, str]:
    """Return (base64-of-gzip, version, hash) for an entry's raw bytes.

    ``version`` is sha256(gzipped bytes)[:12] — content identity of the
    compressed payload, stable across the chain.
    ``hash`` is sha256(base64 string)[:16] — what readers verify after
    reassembly.

    ``mtime=0`` makes the gzip header (and therefore version/hash/chunks)
    DETERMINISTIC — the same bytes always encode identically. Without it gzip
    stamps the current wall-clock second, so re-encoding the same payload a
    second later changed the hash, which broke seal_drift (it could never match
    the sealed hash) and made seals irreproducible. Forward-only: existing
    souls/seals keep their own self-consistent hashes.
    """
    gz = gzip.compress(raw, compresslevel=9, mtime=0)
    b64 = base64.b64encode(gz).decode("ascii")
    version = hashlib.sha256(gz).hexdigest()[:12]
    h = hashlib.sha256(b64.encode("utf-8")).hexdigest()[:16]
    return b64, version, h


# ---------------------------------------------------------------------------
# Nested cycle seal
# ---------------------------------------------------------------------------

def _source_filename(entry):
    """Original filename for a single-file-sourced entry, so reassembly can
    restore it (e.g. ``UGC1_vocals.wav``) instead of ``<layer>.bin``. Multi-
    source entries are concatenations with no single name → None. Built-in
    roles (decoder, truth, …) carry their own filename in the validator, so a
    directory-sourced basename here is harmless (it's ignored for known roles).
    """
    import os
    srcs = [s for s in (entry.sources or []) if s]
    if len(srcs) == 1:
        return os.path.basename(srcs[0].rstrip("/\\")) or None
    return None


def _chunk_layer(layer, entry, payload_module):
    """Read a layer's entry bytes and chunk by character count.

    Universal encoding: gzip → base64 → split. Same rule for every
    layer; the entry's bytes are opaque.

    Returns dict: version, hash, chunks list, raw_size, filename (when the
    entry is a single file — carried so reassembly restores the real name).
    """
    raw = payload_module.get_artifact_bytes(entry.name)
    b64, version, h = _encode_entry(raw)
    out = {
        "version": version,
        "hash": h,
        "chunks": chunk_payload(b64, layer.chunks_per_cycle()),
        "raw_size": len(raw),
    }
    fn = _source_filename(entry)
    if fn:
        out["filename"] = fn
    return out


def _read_pinned(fz, cfg, payload_module):
    """Read a pinned position's content from Payload.

    Multi-entry rows are concatenated (with a newline byte between
    entries). The combined bytes are gzip+base64 encoded, same as every
    layer chunk — readers decode uniformly via atob+gunzip regardless
    of role.
    """
    parts = []
    for ename in fz.entries:
        entry = cfg.entry(ename)
        parts.append(payload_module.get_artifact_bytes(entry.name))
    combined = b"\n".join(parts) if len(parts) > 1 else parts[0]
    b64, _version, _h = _encode_entry(combined)
    return b64


def seal_drift():
    """Layer names whose current built content differs from what's sealed.

    A non-empty result means the active chain's seal is STALE — the payload has
    changed since it was sealed, and those changes won't reach any conception
    until a re-seal (conceptions read the frozen Age, not the live config). This
    is exactly the trap where someone edits/adds a payload after sealing and the
    souls keep carrying the old one.

    Content-based (compares the per-layer entry hash the seal already stores), so
    it's robust: conceiving a record never false-flags it, only a real payload
    change does. Empty list = in sync, or the chain isn't sealed yet. Best-effort
    per layer — an unreadable build doesn't crash the check.
    """
    seal = _load_seal()
    if not seal:
        return []
    from mememage import chain_config as _cc, payload as _payload
    cfg = _cc.load()
    sealed = seal.get("layer_chunks", {}) or {}
    current = {ly.name for ly in cfg.layers}
    drift = []
    for layer in cfg.layers:
        s = sealed.get(layer.name)
        if s is None:
            drift.append(layer.name)          # layer added since the seal
            continue
        try:
            raw = _payload.get_artifact_bytes(cfg.entry(layer.entry).name)
            _, _, h = _encode_entry(raw)
            if h != s.get("hash"):
                drift.append(layer.name)       # content changed since the seal
        except Exception:
            pass
    for name in sealed:
        if name not in current:
            drift.append(name)                 # layer removed since the seal
    return drift


def _seal_layer_entry(layer, out):
    """Assemble a layer's ``sealed_chunks.json`` entry. Carries ``filename``
    through from ``_chunk_layer`` (single-file layers) so reassembly restores the
    real name instead of ``<layer>.bin``. (The seal rebuilds each layer dict
    rather than storing _chunk_layer's verbatim — this is where the name was
    being dropped before.)"""
    entry = {
        "K": layer.K,
        "reserved": layer.reserved,
        "version": out["version"],
        "hash": out["hash"],
        "chunks": out["chunks"],
    }
    if out.get("filename"):
        entry["filename"] = out["filename"]
    return entry


def seal(docs_dir: Path = DOCS_DIR, truth_path: Path = TRUTH_PATH, proof_path: Path = PROOF_PATH) -> dict:
    """Run the full seal pipeline. Creates a new Age with nested cycles.

    Inputs are read exclusively from the Payload/ staging directory — whatever
    is in Payload at seal time is exactly what enters the chain. The shape
    of the seal (number of layers, cycle lengths K_i, pinned positions) is
    driven by the active chain's ``chain.json`` payload config; missing or
    incomplete config falls through to ``ChainConfig.default()`` which
    reproduces the previous hardcoded layout byte-for-byte.

    The docs_dir, truth_path, proof_path arguments are retained for backward
    compatibility but are no longer consulted at seal time.
    """
    from mememage import payload as _payload
    from mememage import chain_config as _chain_config

    _payload.require_ready()

    cfg = _chain_config.load()

    # Bind the decoder cycle to the chain's constellation_size: the decoder
    # HTML splits into exactly N chunks so one full decoder reassembles every
    # constellation (heart star + N-1 Bayer siblings). constellation_size is
    # the single source of truth — it drives decoder K here, never the
    # reverse. (M, the Age length, stays a separate knob; ideally a multiple
    # of N but not required.)
    constellation_size = cfg.constellation_size
    for _ly in cfg.layers:
        if _ly.name == "decoder":
            _ly.K = constellation_size
            break

    existing = _load_seal()
    if existing is not None:
        if not is_cycle_complete():
            current_age = existing.get("age", 1)
            current_name = existing.get("age_name", AGE_NAMES[0])
            state = _load_chunk_state()
            outer = state.get("outer_position", 0)
            raise RuntimeError(
                f"Cannot begin new Age: {current_name} (Age {current_age}) "
                f"has not completed its outer cycle. "
                f"Currently at {outer}/{cfg.M}. "
                f"Generate {cfg.M - outer} more images, then seal again."
            )
        next_age = existing.get("age", 1) + 1
    else:
        next_age = 1

    age_name = AGE_NAMES[(next_age - 1) % len(AGE_NAMES)]

    # Chunk every layer via the config-driven loop.
    layer_out = {}
    for layer in cfg.layers:
        entry = cfg.entry(layer.entry)
        layer_out[layer.name] = _chunk_layer(layer, entry, _payload)

    # Process every pinned position. ``pinned_chunks`` is the generic
    # storage: a list of {position, role, data} dicts, one per pinned
    # entry. Position is honored as-is by site_embed (no hardcoded
    # "easter_egg at 364" rules) so the chain author can place any role
    # at any position. The legacy fields below (schematic_chunks_data,
    # claim_data, easter_egg_data) are kept so older readers continue
    # working — new emission paths prefer ``pinned_chunks``.
    pinned_chunks = []
    pinned_by_role = {}
    for fz in cfg.pinned:
        data = _read_pinned(fz, cfg, _payload)
        pinned_chunks.append({"position": fz.position, "role": fz.role, "data": data})
        pinned_by_role.setdefault(fz.role, []).append(data)

    # Decoder convenience aliases (used in the return info + standalone path).
    decoder_out  = layer_out.get("decoder",  {"version": None, "hash": None, "chunks": [], "raw_size": 0})
    truth_out    = layer_out.get("truth",    {"version": None, "hash": None, "chunks": [], "raw_size": 0})
    proof_out    = layer_out.get("validator",{"version": None, "hash": None, "chunks": [], "raw_size": 0})

    # Legacy convenience: schematic chunks ordered by position. Readers
    # that still consume schematic_chunks_data (older browser builds, the
    # pre-pinned_chunks emitter) keep working. New emitter reads from
    # pinned_chunks directly.
    schematic_chunks_data = [
        fc["data"] for fc in sorted(
            [fc for fc in pinned_chunks if fc["role"].startswith("schematic")],
            key=lambda fc: fc["position"],
        )
    ]

    # Single-position pinned content.
    claim_data = next(iter(pinned_by_role.get("claim", [])), None)
    easter_egg_data = next(iter(pinned_by_role.get("easter_egg", [])), None)
    # easter_egg_data is now a base64-of-gzip string (same as every
    # other pinned entry). The hash is taken over that string so verifiers
    # can confirm the chunk byte-for-byte before decompressing.
    easter_egg_hash = (
        hashlib.sha256(easter_egg_data.encode("ascii")).hexdigest()[:12]
        if easter_egg_data else None
    )

    # Get the layer's cycle config for the seal_data shape.
    decoder_layer = next((l for l in cfg.layers if l.name == "decoder"), None)
    truth_layer = next((l for l in cfg.layers if l.name == "truth"), None)
    proof_layer = next((l for l in cfg.layers if l.name == "validator"), None)

    # Fallback to 0 (not the demo's DECODER_CHUNKS / TRUTH_CHUNKS /
    # PROOF_* constants) when a chain doesn't define a decoder, truth,
    # or validator layer. A custom chain with one arbitrary layer
    # shouldn't be reported as if it had a 12-chunk decoder.
    decoder_K = decoder_layer.K if decoder_layer else 0
    truth_K = truth_layer.K if truth_layer else 0
    proof_data_K = proof_layer.chunks_per_cycle() if proof_layer else 0
    proof_cycle_K = proof_layer.K if proof_layer else 0

    seal_data = {
        "age": next_age,
        "age_name": age_name,
        "version": decoder_out["version"],
        "decoder_hash": decoder_out["hash"],
        "truth_version": truth_out["version"],
        "proof_version": proof_out["version"],
        "proof_hash": proof_out["hash"],
        "decoder_chunks": decoder_K,
        "truth_chunks": truth_K,
        "proof_chunks": proof_data_K,
        "proof_cycle": proof_cycle_K,
        "outer_cycle": cfg.M,
        # Constellation cadence for the whole Age — snapshotted so a
        # dashboard change only takes effect on the NEXT seal. Drives the
        # heart-reset cadence + Bayer-letter span (see
        # site_embed.constellation_cadence). Equals the decoder layer K above.
        "constellation_size": constellation_size,
        # Stored separately for independent access (canonical fields,
        # named for legacy readers).
        "decoder_chunks_data": decoder_out["chunks"],
        "truth_chunks_data": truth_out["chunks"],
        "proof_chunks_data": proof_out["chunks"],
        # Generic layer storage — every layer keyed by its name. Allows
        # site_embed to emit arbitrary-named layers (any K, any role)
        # without hardcoded decoder/truth/proof mappings. Each entry:
        # {K, reserved, version, hash, chunks}.
        "layer_chunks": {
            ly.name: _seal_layer_entry(ly, layer_out[ly.name]) for ly in cfg.layers
        },
        # Generic pinned-chunk storage (position-flexible). Each entry is
        # {position, role, data}; site_embed iterates and emits at the
        # declared position regardless of role.
        "pinned_chunks": pinned_chunks,
        # Legacy fields — kept for backward compat with older readers
        # and for tests that mock seal_data directly.
        "schematic_chunks_data": schematic_chunks_data,
        "claim_data": claim_data,
        "easter_egg_data": easter_egg_data,
        "easter_egg_hash": easter_egg_hash,
        # Backward compat
        "total_chunks": cfg.M,
        # Forward-looking: full layer/pinned structure for verifiers that
        # want to know the chain's authored configuration without inferring
        # from the legacy field names. Kept compact (no chunks_data — those
        # are still in the top-level fields above).
        "chain_config": {
            "id": cfg.id,
            "name": cfg.name,
            "visibility": cfg.visibility,
            "M": cfg.M,
            "schema_version": cfg.schema_version,
            "layers": [ly.to_dict() for ly in cfg.layers],
            "pinned": [fz.to_dict() for fz in cfg.pinned],
        },
    }

    # Resolve at call time so a chain switch in the dashboard takes
    # effect for the next seal without a server restart.
    from mememage.site_embed import seal_file as _seal_file
    sf = _seal_file()
    sf.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(sf.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(seal_data, f)
        os.replace(tmp, str(sf))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    # Initialize state for the new age (all counters start at 0)
    _save_chunk_state({"inner_position": 0, "outer_position": 0, "cycle_complete": False})

    info = {
        "age": next_age,
        "age_name": age_name,
        "version": decoder_out["version"],
        "decoder_hash": decoder_out["hash"],
        "truth_version": truth_out["version"],
        "decoder_chunks": decoder_K,
        "truth_chunks": truth_K,
        "outer_cycle": cfg.M,
        "total_payload_kb": round(sum(len(c) for c in decoder_out["chunks"]) / 1024, 1),
        "truth_text_chars": truth_out["raw_size"],
        "standalone_path": str(_payload.STANDALONE_PATH),
    }
    return info


# ---------------------------------------------------------------------------
# Reassembly
# ---------------------------------------------------------------------------

def reassemble_decoder(identifier: str, max_depth: int = 50,
                       target_age: int | None = None) -> str:
    """Walk the chain and reassemble the decoder from embedded chunks.

    Only needs 12 records — one complete inner cycle.
    """
    from mememage.net import fetch_json
    from mememage.config import IA_DOWNLOAD_URL

    print(f"Walking chain from {identifier}...")

    ages = {}
    age_meta = {}
    current_id = identifier
    visited = 0

    while current_id and visited < max_depth:
        url = f"{IA_DOWNLOAD_URL}/{current_id}/metadata.json"
        try:
            record = fetch_json(url)
        except Exception as e:
            print(f"  Failed to fetch {current_id}: {e}")
            break
        if record is None:
            break

        visited += 1

        decoder = _get_chunk(record, "decoder")
        if decoder:
            age = decoder.get("age", 1)
            idx = decoder["index"]
            version = decoder["version"]
            chunk = decoder["data"]
            chunk_hash = decoder.get("hash")
            total = decoder["total"]
            age_name = decoder.get("age_name", f"Age {age}")

            if age not in ages:
                ages[age] = {}
                age_meta[age] = {
                    "version": version, "total": total, "name": age_name,
                    # decoder_hash stays at top level (cross-Age constant,
                    # in _HASH_INCLUDED — not part of chunks)
                    "decoder_hash": record.get("decoder_hash"),
                }

            if idx not in ages[age]:
                ages[age][idx] = {
                    "data": chunk,
                    "hash": chunk_hash,
                    "source": current_id,
                }
                print(f"  [{visited}] {current_id} → {age_name}, chunk {idx}/{total}")

        current_id = record.get("parent_id")

    if not ages:
        raise RuntimeError(f"No decoder chunks found in {visited} records")

    complete_ages = [a for a, c in sorted(ages.items()) if len(c) >= age_meta[a]["total"]]

    print(f"\nFound {len(ages)} age(s): " +
          ", ".join(f"{age_meta[a]['name']} ({len(ages[a])}/{age_meta[a]['total']})"
                    for a in sorted(ages)))

    if target_age is not None:
        if target_age not in ages:
            raise RuntimeError(f"Age {target_age} not found in chain")
        selected = target_age
    elif complete_ages:
        selected = max(complete_ages)
    else:
        for age_num in sorted(ages, reverse=True):
            total = age_meta[age_num]["total"]
            missing = [i for i in range(total) if i not in ages[age_num]]
            raise RuntimeError(
                f"No complete age found. {age_meta[age_num]['name']} is closest: "
                f"missing chunks {missing} ({len(missing)}/{total})."
            )

    found = ages[selected]
    meta = age_meta[selected]
    version = meta["version"]
    total = meta["total"]

    missing = [i for i in range(total) if i not in found]
    if missing:
        raise RuntimeError(f"{meta['name']} missing chunks: {missing}.")

    for idx in range(total):
        chunk_info = found[idx]
        if chunk_info["hash"]:
            actual = hashlib.sha256(chunk_info["data"].encode("utf-8")).hexdigest()[:12]
            if actual != chunk_info["hash"]:
                raise RuntimeError(
                    f"{meta['name']} chunk {idx} integrity failure: "
                    f"expected {chunk_info['hash']}, got {actual}"
                )

    b64 = "".join(found[i]["data"] for i in range(total))
    gz_bytes = base64.b64decode(b64)

    actual_version = hashlib.sha256(gz_bytes).hexdigest()[:12]
    if actual_version != version:
        raise RuntimeError(f"Payload hash mismatch: expected {version}, got {actual_version}")

    # Also verify decoder_hash (SHA-256 of base64 string, 16 hex chars)
    # if any record in the chain carries it. This is the per-record fingerprint
    # that lets anyone verify a decoder obtained from any source.
    actual_decoder_hash = hashlib.sha256(b64.encode("utf-8")).hexdigest()[:16]
    if meta.get("decoder_hash") and actual_decoder_hash != meta["decoder_hash"]:
        raise RuntimeError(
            f"Decoder hash mismatch: expected {meta['decoder_hash']}, "
            f"got {actual_decoder_hash}"
        )

    html = gzip.decompress(gz_bytes).decode("utf-8")
    print(f"\nReassembled {meta['name']} successfully:")
    print(f"  Version:  {version}")
    print(f"  Chunks:   {total}")
    print(f"  Size:     {len(html)} bytes ({len(html)/1024:.1f} KB)")
    return html


def reassemble_truth(records: list[dict]) -> str:
    """Reassemble the truth text from a list of metadata records.

    Needs 365 truth chunks from one Age. Same decode path as decoder
    reassembly: concat → base64-decode → gunzip → utf-8.
    """
    truth_chunks = {}  # version -> idx -> chunk dict
    for r in records:
        truth = _get_chunk(r, "truth")
        if not truth:
            continue
        v = truth.get("version", "unknown")
        if v not in truth_chunks:
            truth_chunks[v] = {}
        idx = truth["index"]
        if idx not in truth_chunks[v]:
            truth_chunks[v][idx] = truth

    if not truth_chunks:
        raise RuntimeError("No truth chunks found in records")

    version = max(truth_chunks, key=lambda v: len(truth_chunks[v]))
    found = truth_chunks[version]
    total = next(iter(found.values()))["total"]

    missing = [i for i in range(total) if i not in found]
    if missing:
        raise RuntimeError(
            f"Truth reassembly incomplete: {len(found)}/{total} chunks. "
            f"Missing: {missing[:20]}{'...' if len(missing) > 20 else ''}"
        )

    for idx in range(total):
        chunk = found[idx]
        actual = hashlib.sha256(chunk["data"].encode("utf-8")).hexdigest()[:12]
        expected = chunk.get("hash")
        if expected and actual != expected:
            raise RuntimeError(f"Truth chunk {idx} integrity failure")

    b64 = ''.join(found[i]["data"] for i in range(total))
    gz_bytes = base64.b64decode(b64)
    return gzip.decompress(gz_bytes).decode("utf-8")


def extract_easter_egg(records: list[dict]) -> dict | None:
    """Extract the easter egg from records, if present.

    The egg is stored gzip+base64 like every other pinned entry.
    """
    for r in records:
        egg = _get_chunk(r, "easter_egg")
        if not egg or not egg.get("data"):
            continue
        try:
            gz_bytes = base64.b64decode(egg["data"])
            text = gzip.decompress(gz_bytes).decode("utf-8")
            return json.loads(text)
        except (ValueError, OSError, json.JSONDecodeError, TypeError):
            continue
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """CLI entry point."""
    import sys

    usage = (
        "Usage:\n"
        "  python -m mememage.site_pack                      # dry run\n"
        "  python -m mememage.site_pack seal                  # begin new Age\n"
        "  python -m mememage.site_pack reassemble ID         # rebuild decoder from chain\n"
        "  python -m mememage.site_pack reassemble --full ID  # decoder + truth\n"
        "  python -m mememage.site_pack status                # show cycle info\n"
    )

    cmd = sys.argv[1] if len(sys.argv) > 1 else None

    if cmd == "seal":
        try:
            info = seal()
        except (RuntimeError, ValueError) as e:
            print(f"Error: {e}")
            sys.exit(1)
        print(f"Sealed: {info['age_name']} (Age {info['age']})")
        print(f"  Decoder:       {info['decoder_chunks']} chunks"
              f" ({info['total_payload_kb']} KB, hash={info['decoder_hash']})")
        print(f"  Truth:         {info['truth_chunks']} chunks"
              f" ({info['truth_text_chars']} chars)")
        print(f"  Outer cycle:   {info['outer_cycle']} conceptions per Age")
        print(f"  Decoder cycles: {info['outer_cycle'] // info['decoder_chunks']} per Age")
        print(f"  Standalone:    {info['standalone_path']}")

    elif cmd == "reassemble":
        if len(sys.argv) < 3:
            print("Error: reassemble requires an identifier")
            print(usage)
            sys.exit(1)
        identifier = sys.argv[2]
        if identifier == "--full":
            # Full reassembly mode
            if len(sys.argv) < 4:
                print("Error: --full requires an identifier")
                sys.exit(1)
            print("Full reassembly (decoder + truth) not yet implemented via CLI.")
            print("Use reassemble_decoder() and reassemble_truth() from Python.")
            sys.exit(1)
        if "-" not in identifier:
            identifier = f"mememage-{identifier}"
        # Default to the CANONICAL filename: the reassembled decoder is a
        # self-contained index.html that cross-links to validator.html by
        # relative href. Saved as index.html next to a reassembled
        # validator.html, the DECODER↔VALIDATOR portal works with no edits.
        # Pass an explicit name to override.
        output = sys.argv[3] if len(sys.argv) > 3 else "index.html"

        try:
            html = reassemble_decoder(identifier)
        except RuntimeError as e:
            print(f"Error: {e}")
            sys.exit(1)
        Path(output).write_text(html, encoding="utf-8")
        print(f"  Written to:   {output}")

    elif cmd == "status":
        from mememage.site_embed import get_current_age_info
        info = get_current_age_info()
        if info is None:
            print("No age sealed yet. Run 'seal' to begin the first Age.")
        else:
            inner = info["inner_position"]
            outer = info["outer_position"]
            dec_cycles = info["decoder_cycles_complete"]
            truth_done = info["truth_chunks_distributed"]

            if info["cycle_complete"]:
                progress = "complete — ready to advance"
            else:
                progress = f"{outer}/{OUTER_CYCLE}"

            print(f"{info['age_name']} (Age {info['age']})")
            print(f"  Decoder version: {info['version']}")
            if info.get("decoder_hash"):
                print(f"  Decoder hash:    {info['decoder_hash']}")
            print(f"  Inner cycle:     {inner}/{DECODER_CHUNKS} (decoder)")
            print(f"  Outer cycle:     {progress}")
            print(f"  Decoder cycles:  {dec_cycles}/30 complete")
            print(f"  Truth chunks:    {truth_done}/{TRUTH_CHUNKS} distributed")

    else:
        # Dry run
        b64, version = pack_site()
        decoder_chunks = chunk_payload(b64, DECODER_CHUNKS)
        decoder_hash = hashlib.sha256(b64.encode("utf-8")).hexdigest()[:16]

        truth_text = TRUTH_PATH.read_text(encoding="utf-8")
        truth_b64, _v, _h = _encode_entry(truth_text.encode("utf-8"))
        truth_chunks = chunk_payload(truth_b64, TRUTH_CHUNKS)
        truth_round = gzip.decompress(base64.b64decode(''.join(truth_chunks))).decode("utf-8")
        truth_ok = truth_round == truth_text

        print(f"Dry run (use 'seal' to begin an Age)")
        print(f"  Decoder:        {DECODER_CHUNKS} chunks"
              f" ({round(len(b64) / 1024, 1)} KB, hash={decoder_hash})")
        print(f"  Truth:          {TRUTH_CHUNKS} chunks"
              f" ({len(truth_text)} chars, roundtrip {'OK' if truth_ok else 'FAILED'})")
        print(f"  Outer cycle:    {OUTER_CYCLE} conceptions")
        print(f"  Decoder cycles: {OUTER_CYCLE // DECODER_CHUNKS} per Age")


if __name__ == "__main__":
    main()
