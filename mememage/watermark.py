"""Distributed DCT-domain watermark — the name in the flesh.

Embeds a 64-bit content hash into the image's DCT coefficients,
spread across the entire body. Survives JPEG compression (q30+)
and arbitrary cropping — any fragment large enough to carry the
signal still knows its own name.

The bar (spirit) carries direction and name in the lowest parts.
The watermark (body) carries only the name, everywhere.

Technique:
  1. Convert to YCbCr, operate on luminance only
  2. Divide into 8×8 blocks (aligned to JPEG's DCT grid)
  3. Select blocks deterministically via seeded PRNG (public seed)
  4. Embed each bit by forcing a mid-frequency DCT coefficient's sign
  5. Each bit is redundantly embedded across many blocks (majority vote)

The watermark is invisible (PSNR > 40dB) and carries 64 bits —
exactly the content hash. The body knows its own name.
"""

import math


def _get_Image():
    """Lazy import Pillow so the package can be imported without it."""
    from PIL import Image
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass
    return Image


def _get_numpy():
    """Lazy import numpy so the package can be imported without it."""
    import numpy as np
    return np

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WATERMARK_SEED = 0x4D454D45  # "MEME" — base seed, combined with content hash
PAYLOAD_BITS = 72            # 8 sync bits + 64 hash bits (the FULL content hash).
                             # MUST equal the tile size _TILE_W*_TILE_H (9*8=72) — each
                             # block in a tile carries one bit. Widened 64->72 (2026-06-24,
                             # pre-release, no migration) so the watermark carries the whole
                             # 16-hex content hash, not a 14-hex truncation.
STRENGTH = 25                # DCT coefficient strength (invisible at 37dB PSNR, survives q70+)

# Sync marker: first 8 bits of payload. Used to identify correct
# tile offset after cropping. Only the correct offset produces 0xAD.
_SYNC_MARKER = 0xAD
_SYNC_BITS = 8
_HASH_BITS = PAYLOAD_BITS - _SYNC_BITS  # 64 bits = 16 hex chars (full content hash)

# Pool of JPEG-safe mid-frequency DCT positions.
# All survive JPEG q30+ AND WebP (Discord pipeline), invisible to viewer.
# Sorted by empirical survival robustness (best first).
# (5,2) removed — 49.7% sign preservation through WebP (coin flip).
# Remaining 9 positions: 96-99% survival through both JPEG and WebP.
_COEFF_POOL = [
    (3, 3), (3, 5), (3, 4), (2, 5), (4, 3),
    (2, 4), (4, 2), (5, 3), (4, 4),
]

# Default position for legacy (v1) watermarks without per-image derivation
_EMBED_ROW = 4
_EMBED_COL = 3

# Avoid bottom 16px (bar territory) and outer 8px border (crop margin)
_MARGIN_PX = 8
_BAR_MARGIN_PX = 16

# Magic bytes for watermark detection (embedded in first 2 blocks)

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Precomputed numpy DCT basis matrix (lazy, cached)
# ---------------------------------------------------------------------------

_DCT_BASIS = None  # cached 8×8 float64 numpy array


def _get_dct_basis():
    """Return the 8×8 DCT-II orthonormal basis matrix C.

    C[u, x] = alpha(u) * cos(pi * (2x+1) * u / 16)
    DCT:  D = C @ block @ C.T
    IDCT: block = C.T @ D @ C
    """
    global _DCT_BASIS
    if _DCT_BASIS is not None:
        return _DCT_BASIS
    np = _get_numpy()
    C = np.zeros((8, 8), dtype=np.float64)
    for u in range(8):
        for x in range(8):
            C[u, x] = math.cos(math.pi * (2 * x + 1) * u / 16.0)
        if u == 0:
            C[u] *= 1.0 / math.sqrt(2.0)
    C *= math.sqrt(2.0 / 8.0)  # = sqrt(1/4) = 0.5, overall normalization
    _DCT_BASIS = C
    return C


# ---------------------------------------------------------------------------
# Per-image embedding pattern derivation
# ---------------------------------------------------------------------------

def _derive_embed_params(content_hash):
    """Derive per-image embedding parameters from the content hash.

    Returns (embed_row, embed_col, tile_seed).

    Each image gets a different DCT coefficient position and tile permutation,
    making bulk statistical extraction impossible — an attacker can't average
    across images because each image has a unique embedding layout.

    The content hash is public (in the bar), so the decoder can always
    derive the same parameters. Transparency, not secrecy.
    """
    # Use first 4 hex chars of hash as derivation seed
    seed = int(content_hash[:4], 16)

    # Pick coefficient position from the JPEG-safe pool
    coeff_idx = seed % len(_COEFF_POOL)
    embed_row, embed_col = _COEFF_POOL[coeff_idx]

    # Derive tile seed by mixing content hash with base seed
    tile_seed = (WATERMARK_SEED ^ int(content_hash[:8], 16)) & 0xFFFFFFFF

    return embed_row, embed_col, tile_seed


def _build_tile_perm(seed):
    """Build a tile permutation from a seed (Fisher-Yates shuffle)."""
    perm = list(range(PAYLOAD_BITS))
    rng = seed
    for i in range(PAYLOAD_BITS - 1, 0, -1):
        rng = (1664525 * rng + 1013904223) & 0xFFFFFFFF
        j = rng % (i + 1)
        perm[i], perm[j] = perm[j], perm[i]
    return perm


# ---------------------------------------------------------------------------
# Position-based bit assignment (crop-invariant via tiling)
# ---------------------------------------------------------------------------

# The 72 payload bits are tiled across the image in a 9×8 block grid.
# Each tile is 9 blocks wide × 8 blocks tall = 72 blocks = 72 bits.
# The tile repeats every 72×64 pixels. Any crop larger than 72×64
# contains at least one full tile, providing votes for all 72 bits.
#
# Within each tile, the 72 bit indices are scrambled by a seeded
# permutation so the bit layout isn't trivially predictable.

_TILE_W = 9   # tile width in blocks (9 blocks × 8px = 72px)
_TILE_H = 8   # tile height in blocks
assert _TILE_W * _TILE_H == PAYLOAD_BITS  # one block per bit within a tile

# Precompute scrambled bit assignment within the tile
_TILE_PERM = list(range(PAYLOAD_BITS))
_rng_state = WATERMARK_SEED
for _i in range(PAYLOAD_BITS - 1, 0, -1):
    _rng_state = (1664525 * _rng_state + 1013904223) & 0xFFFFFFFF
    _j = _rng_state % (_i + 1)
    _TILE_PERM[_i], _TILE_PERM[_j] = _TILE_PERM[_j], _TILE_PERM[_i]


def _block_bit_index(bx, by, offset_x=0, offset_y=0, perm=None):
    """Map an 8×8 block to a payload bit index using tiled assignment.

    Uses ((grid_x + offset_x) % _TILE_W, (grid_y + offset_y) % _TILE_H) so
    the mapping repeats every 72×64px. The offset parameters allow the decoder
    to search for the correct tile phase after cropping.

    Args:
        perm: Tile permutation array. Uses default (legacy) if None.
    """
    grid_x = bx // 8
    grid_y = by // 8
    tile_x = (grid_x + offset_x) % _TILE_W
    tile_y = (grid_y + offset_y) % _TILE_H
    tile_idx = tile_y * _TILE_W + tile_x
    return (perm or _TILE_PERM)[tile_idx]


def _get_all_blocks(img_w, img_h):
    """Get all eligible 8×8 block positions in the image.

    Always skips the bottom bar margin — those blocks are either
    bar-encoded (not watermarked) or contain edge artifacts.
    This is consistent between embed and extract.
    """
    blocks = []
    max_bx = (img_w // 8) * 8 - 8
    y_limit = ((img_h - _BAR_MARGIN_PX) // 8) * 8 - 8

    if y_limit < 0 or max_bx < 0:
        return []

    for by in range(0, y_limit + 1, 8):
        for bx in range(0, max_bx + 1, 8):
            blocks.append((bx, by))

    return blocks


# ---------------------------------------------------------------------------
# Hash ↔ bits conversion
# ---------------------------------------------------------------------------

def _hash_to_payload_bits(content_hash):
    """Convert content hash to the payload: 8 sync bits + the FULL 64-bit hash.

    The full 16-hex content hash is carried (no truncation). The first 8 bits
    are the sync marker 0xAD, used to find the correct tile offset after cropping.
    """
    hex_chars = _HASH_BITS // 4  # 16
    hash_val = int(content_hash[:hex_chars], 16)

    bits = []
    # Sync marker (_SYNC_BITS)
    for i in range(_SYNC_BITS - 1, -1, -1):
        bits.append((_SYNC_MARKER >> i) & 1)
    # Hash (_HASH_BITS)
    for i in range(_HASH_BITS - 1, -1, -1):
        bits.append((hash_val >> i) & 1)

    return bits


def _payload_bits_to_hash(bits):
    """Extract content hash from the payload. Returns (hash_str, sync_ok)."""
    # Check sync marker
    sync = 0
    for i in range(_SYNC_BITS):
        sync = (sync << 1) | bits[i]
    sync_ok = (sync == _SYNC_MARKER)

    # Extract hash (_HASH_BITS → 16 hex chars)
    hash_val = 0
    for i in range(_SYNC_BITS, PAYLOAD_BITS):
        hash_val = (hash_val << 1) | bits[i]

    hex_chars = _HASH_BITS // 4  # 16
    return f"{hash_val:0{hex_chars}x}", sync_ok


# ---------------------------------------------------------------------------
# Image ↔ luminance helpers
# ---------------------------------------------------------------------------

def _get_luminance(img):
    """Extract luminance channel as 2D float array. Uses BT.601 weights."""
    np = _get_numpy()
    pixels = np.array(img, dtype=np.float64)
    # BT.601: Y = 0.299*R + 0.587*G + 0.114*B
    Y = 0.299 * pixels[:, :, 0] + 0.587 * pixels[:, :, 1] + 0.114 * pixels[:, :, 2]
    return Y


def _apply_luminance_delta(pixels, bx, by, delta_block):
    """Apply DCT changes back to RGB pixels by adjusting luminance.

    pixels: numpy array (H, W, 3), modified in place.
    delta_block: numpy array (8, 8), luminance delta to apply.
    """
    np = _get_numpy()
    # Broadcast delta to all 3 RGB channels and add
    region = pixels[by:by + 8, bx:bx + 8]
    region += delta_block[:, :, np.newaxis]
    # Clip happens once at the end for the whole image (faster)


# ---------------------------------------------------------------------------
# Embed / Extract
# ---------------------------------------------------------------------------

def _block_variance(Y, bx, by):
    """Luminance variance for an 8×8 block — used by the perceptual gate."""
    return float(Y[by:by + 8, bx:bx + 8].var())


def embed_watermark(image_path, content_hash, strength=STRENGTH, variance_threshold=0):
    """Embed 64-bit content hash as distributed DCT watermark.

    Every eligible 8×8 block in the image is watermarked. Each block
    is assigned a payload bit by its position (crop-invariant). The
    bit is encoded in the sign of a mid-frequency DCT coefficient.

    Modifies image in place. The watermark is invisible (PSNR > 40dB)
    and survives JPEG compression + arbitrary cropping.

    Args:
        image_path: Path to image file (overwritten in place).
        content_hash: 16 hex char content hash (64 bits).
        strength: DCT nudge magnitude. Default 25 (survives JPEG q70+).
            Lower = less visible, lower JPEG-survival floor.
        variance_threshold: Skip blocks below this luminance variance.
            0 disables the gate (all blocks embedded). Higher = more flat
            regions preserved, fewer redundant votes per payload bit.

    Returns:
        Number of blocks used, or 0 if image too small or gate too strict.
    """
    np = _get_numpy()
    bits = _hash_to_payload_bits(content_hash)

    # Per-image embedding parameters — each image gets a unique layout
    embed_row, embed_col, tile_seed = _derive_embed_params(content_hash)
    perm = _build_tile_perm(tile_seed)

    img = _get_Image().open(image_path)
    if img.mode == 'RGBA':
        img = img.convert('RGB')
    w, h = img.size

    all_blocks = _get_all_blocks(w, h)
    _MIN_BLOCKS = PAYLOAD_BITS * 3  # At least 3 votes per bit for error resilience
    if len(all_blocks) < _MIN_BLOCKS:
        return 0  # Image too small for reliable watermark

    Y = _get_luminance(img)
    pixels = np.array(img, dtype=np.float64)
    C = _get_dct_basis()
    CT = C.T

    blocks_used = 0
    for (bx, by) in all_blocks:
        # Perceptual gate: skip blocks too flat for the eye to absorb the nudge.
        # JPEG preserves DC + low coefficients well, so block variance computed
        # post-recompression agrees with pre-recompression for the extractor.
        if variance_threshold > 0 and _block_variance(Y, bx, by) < variance_threshold:
            continue

        bit_idx = _block_bit_index(bx, by, perm=perm)
        bit = bits[bit_idx]

        block = Y[by:by + 8, bx:bx + 8].copy()
        # DCT via matrix multiply: D = C @ block @ C.T
        dct = C @ block @ CT

        # Encode bit via additive nudge — preserves natural coefficient value.
        # Instead of forcing to a minimum magnitude (creates visible grid on
        # smooth gradients), we nudge the coefficient toward the target sign
        # by adding `strength` in the desired direction.
        coeff = dct[embed_row, embed_col]
        target_sign = 1.0 if bit == 1 else -1.0
        if coeff * target_sign >= strength:
            pass  # Already correct sign and strong enough
        else:
            # Nudge: push toward target sign, preserving some natural variation
            dct[embed_row, embed_col] = coeff + target_sign * float(strength)

        # IDCT via matrix multiply: block' = C.T @ D @ C
        modified = CT @ dct @ C
        delta = modified - block

        # Apply delta to RGB pixels
        _apply_luminance_delta(pixels, bx, by, delta)
        # Update luminance array for subsequent blocks (they read updated values)
        Y[by:by + 8, bx:bx + 8] = modified
        blocks_used += 1

    if blocks_used < PAYLOAD_BITS * 3:
        # Gate left too few blocks to vote reliably on all 64 bits — bail
        # rather than ship a partially-embedded image we'll struggle to read.
        return 0

    # Clip to valid range and convert back to uint8
    np.clip(pixels, 0, 255, out=pixels)
    result_img = _get_Image().fromarray(pixels.astype(np.uint8), 'RGB')

    # Preserve PNG metadata
    from PIL.PngImagePlugin import PngInfo
    try:
        original = _get_Image().open(image_path)
        pnginfo = PngInfo()
        if hasattr(original, 'text'):
            for key, value in original.text.items():
                if key.startswith('XML:'):
                    pnginfo.add_itxt(key, value)
                else:
                    pnginfo.add_text(key, value)
        original.close()
        result_img.save(image_path, pnginfo=pnginfo)
    except Exception:
        result_img.save(image_path)

    return blocks_used


def _extract_at_offset(dct_coeffs, all_blocks, offset_x, offset_y, perm=None):
    """Try extracting watermark at a specific tile offset.

    dct_coeffs: dict mapping (bx, by) → DCT coefficient value
    Returns (hash_14hex, sync_ok, confidence).
    sync_ok is True if the first 8 bits match the sync marker 0xAD.
    """
    votes = [[0, 0] for _ in range(PAYLOAD_BITS)]

    for (bx, by) in all_blocks:
        bit_idx = _block_bit_index(bx, by, offset_x, offset_y, perm=perm)
        coeff = dct_coeffs[(bx, by)]

        if coeff > 0:
            votes[bit_idx][1] += 1
        else:
            votes[bit_idx][0] += 1

    # Check all bits have at least one vote
    for v in votes:
        if v[0] + v[1] == 0:
            return None, False, 0.0

    # Majority vote
    result_bits = []
    total_margin = 0.0
    for v in votes:
        total = v[0] + v[1]
        margin = abs(v[1] - v[0]) / total if total > 0 else 0.0
        total_margin += margin
        result_bits.append(1 if v[1] > v[0] else 0)

    confidence = total_margin / PAYLOAD_BITS
    hash_str, sync_ok = _payload_bits_to_hash(result_bits)
    return hash_str, sync_ok, confidence


def extract_watermark(image_path, content_hash=None, variance_threshold=0):
    """Extract content hash from distributed DCT watermark.

    If content_hash is provided, uses per-image embedding parameters
    derived from that hash (fast, targeted extraction).

    If content_hash is None, falls back to legacy extraction using the
    default coefficient position and tile permutation (slower, searches
    all 64 tile offsets).

    Args:
        image_path: Path to image file.
        content_hash: Known content hash from bar (enables per-image extraction).
        variance_threshold: Skip blocks below this luminance variance.
            Must match the threshold used at embed time; JPEG-stable so
            embed and extract agree on which blocks were watermarked.
            0 disables the gate (legacy behavior — all blocks contribute).

    Returns:
        16 hex char content hash (64 bits), or None if no watermark detected.
    """
    np = _get_numpy()

    try:
        img = _get_Image().open(image_path)
        if img.mode == 'RGBA':
            img = img.convert('RGB')
    except Exception:
        return None

    w, h = img.size

    all_blocks = _get_all_blocks(w, h)
    if len(all_blocks) < PAYLOAD_BITS:
        return None

    Y = _get_luminance(img)
    C = _get_dct_basis()
    CT = C.T

    # Determine extraction parameters
    if content_hash:
        embed_row, embed_col, tile_seed = _derive_embed_params(content_hash)
        perm = _build_tile_perm(tile_seed)
    else:
        embed_row, embed_col = _EMBED_ROW, _EMBED_COL
        perm = _TILE_PERM

    # Compute DCT coefficients at the target position (skip flat blocks if gated)
    dct_coeffs = {}
    for (bx, by) in all_blocks:
        if variance_threshold > 0 and _block_variance(Y, bx, by) < variance_threshold:
            continue
        block = Y[by:by + 8, bx:bx + 8]
        dct = C @ block @ CT
        dct_coeffs[(bx, by)] = dct[embed_row, embed_col]

    if len(dct_coeffs) < PAYLOAD_BITS:
        return None

    # Search all tile offsets — the sync marker identifies the right one. But the
    # 8-bit sync collides (~1/256 per wrong offset, ~28% over 72 offsets), and a
    # false-sync offset can edge out the true one on confidence. In PER-IMAGE mode
    # we already know the content hash (we derived the layout from it), so an
    # offset whose recovered hash matches it EXACTLY (64-bit agreement, ~1/2^64
    # false-positive) is definitive — prefer it over any sync/confidence pick.
    target = content_hash[:_HASH_BITS // 4] if content_hash else None  # 16 hex
    best_sync_hash = None
    best_sync_confidence = 0.0
    best_nosync_hash = None
    best_nosync_confidence = 0.0

    gated_blocks = list(dct_coeffs.keys())
    for ox in range(_TILE_W):
        for oy in range(_TILE_H):
            result, sync_ok, confidence = _extract_at_offset(
                dct_coeffs, gated_blocks, ox, oy, perm=perm)
            if result is None:
                continue
            if target is not None and result == target:
                return result  # definitive per-image match — beats sync collisions
            if sync_ok and confidence > best_sync_confidence:
                best_sync_confidence = confidence
                best_sync_hash = result
            if confidence > best_nosync_confidence:
                best_nosync_confidence = confidence
                best_nosync_hash = result

    # Prefer sync-matched result with minimum confidence threshold
    if best_sync_hash is not None and best_sync_confidence >= 0.7:
        return best_sync_hash
    if best_sync_hash is not None:
        return best_sync_hash
    if best_nosync_confidence >= 0.65:
        return best_nosync_hash
    return None
