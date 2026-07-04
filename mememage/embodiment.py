"""Localized tamper detection — the luma grid (EMBODIED, integrity half).

The portrait dHash (signing/verify) answers "is this the same body?" — it is
deliberately coarse so it survives JPEG, resize, the bar, and platform
recompression. That coarseness is also its blind spot: a perceptual hash and
JPEG q50 are the *same magnitude* of change, so dHash cannot tell a blatant
defacement (a drawn line, a pasted box, a stamped dot) from honest
recompression. EMBODIED therefore needs a second, orthogonal signal that catches
a *localized* edit without flagging a *global* recompression.

That signal is the **luma grid**: a GRIDxGRID partition of the **full frame**
(non-square tiles for non-square images), with per-tile statistics computed from
the full-resolution image at mint and stored (hashed) in the soul. At verify the
dropped image's tiles are recomputed and compared. Full-frame, NOT center-cropped,
so a defacement in the margins (a portrait's top/bottom strips, a landscape's
sides) is covered — the center-crop's blind spot. A crop changes the frame, so a
cropped image reads ALTERED (strict — a crop IS an alteration); an honest resize
preserves aspect and the proportional tiling still aligns, so it stays clean.

**Two detectors, by tile type:**

1. FLAT tiles (original luma stddev < FLAT_STD — smooth sky, gradients, flat
   colour) carry a **min/max** detector. A dark mark drops the tile's MIN; a
   bright mark raises its MAX. This is extremely robust: compression and resize
   pull a flat tile's extremes *inward* (averaging tames them), while a mark
   drives them *outward* — opposite directions — so honest transforms can't fake
   a mark. Empirically a 1px-4px black dot on flat sky scores ~150 while
   JPEG q35 + downscale 0.45-0.8x stay <=20. Catches a mark down to a couple of
   pixels, any contrast that's actually visible, with a huge margin.

2. ALL tiles carry a **mean** detector vs HIGH_THRESHOLD, for big defacement
   buried entirely in busy *texture*, where the flat detector doesn't apply
   (textured tiles ring under resize; only large marks clear the HIGH floor).

Both are exposure-normalized: the global median mean-shift (g) is subtracted, so
a uniform brightness change is absorbed but a localized mark survives. By POLICY
a clipping / non-uniform retouch still flags — it IS a pixel modification, and
platforms recompress, they don't retouch.

**Honest floor:** a mark hidden inside matching high-frequency texture (not
visible -> not defacement) needs to be large; on flat regions detection is
near-pixel. The bottom tile row is excluded — it holds the 2px bar, which the
pre-bar reference grid doesn't have.

**Parity:** all tile math is integer-boundary area-average / min / max over the
source pixels (NO library resize — PIL vs canvas resampling is the parity trap);
luma = 0.299R + 0.587G + 0.114B; half-up rounding (int(x+0.5) == JS
floor(x+0.5)). JS twin: docs/js/verify.js; byte/verdict parity locked by
tests/embodiment/luma_grid_parity.cjs.

**Stored blob (base64), GRID=32:**
    [1024 mean][1024 min][1024 max][128 flat-bits]  = 3200 bytes (~4.3KB b64)
Older 1152-byte (mean+flat) and 256-byte (16x16) grids are not the current
format; unpack() returns None for them and the caller falls back to dHash-only
EMBODIED (re-mint to get the min/max detector).
"""

from __future__ import annotations

import base64

GRID = 32
MEAN_BYTES = GRID * GRID                       # 1024
MIN_BYTES = GRID * GRID                         # 1024
MAX_BYTES = GRID * GRID                         # 1024
FLAT_BYTES = (GRID * GRID + 7) // 8             # 128
BLOB_BYTES = MEAN_BYTES + MIN_BYTES + MAX_BYTES + FLAT_BYTES   # 3200 (data)

# Format version, appended as a trailing byte so the FULL-FRAME grid is
# distinguishable from the old (unversioned, 3200-byte) CENTER-SQUARE grid. The
# semantics are identical bytes but a different *region*, so size alone can't
# tell them apart — without this an old soul's center-square grid would mismatch
# the new full-frame dropped stats and cry wolf (the ORIGINAL image reads
# ALTERED) until re-mint. v2 grids unpack; older/other -> None -> dHash-only.
GRID_VERSION = 2
STORED_BYTES = BLOB_BYTES + 1                   # 3201 (data + version byte)

# A tile is "smooth" if its high-frequency content — the mean absolute
# adjacent-pixel luma difference — is below this. Smooth tiles (flat colour AND
# smooth gradients like a sun's glow) carry the sensitive min/max detector: they
# don't RING under resize/JPEG, so a mark's min/max swing can't be faked.
# Roughness, NOT stddev: a gradient has high stddev but is smooth (low
# adjacent-diff), and excluding it by stddev was the bug that let a mark on a
# bright glow slip. Measured: gradients ~3, busy texture ~32; min/max stays
# compression-safe (<5) for any tile under ~12, so 10 admits gradients with a
# wide margin and rejects texture/edges. The threshold is computed at mint and
# the bit stored, so changing it is Python-only — but the stored bit changes, so
# it needs a re-mint to take effect.
SMOOTH_ROUGHNESS = 10.0

# Flat-tile min/max deviation (0-255 luma units, exposure-normalized) above which
# the tile holds a localized mark. Worst honest transform measured ~20; a visible
# mark scores 100+. 40 sits with margin on both sides. Shared with verify.js
# LUMA_MARK — change in lockstep.
MARK_THRESHOLD = 40.0

# Any-tile mean-residual floor — for defacement buried ENTIRELY in busy texture
# (the flat detector doesn't apply there). Above the worst aggressive-downscale
# ringing (~19 at 0.4x on a razor edge); textured boxes/fills score 45+.
HIGH_THRESHOLD = 24.0

# The bottom tile row holds the 2px steganographic bar. The stored grid is
# computed PRE-bar (the bar carries the content hash the grid is part of, so it
# can't exist yet), but a verified image HAS the bar. Now that the grid is
# full-frame, the bottom tile row is present for EVERY aspect (portraits no
# longer crop it out), so this skip always applies. Drop it.
SKIP_BOTTOM_ROWS = 1


def _get_Image():
    from PIL import Image  # lazy — Pillow is an optional extra
    return Image


def _round8(x):
    v = int(x + 0.5)
    return 255 if v > 255 else (0 if v < 0 else v)


def tile_stats(image_path: str):
    """Per-tile (mean, min, max) as uint8 byte arrays plus the smooth bitmap
    (bit set when the tile's high-frequency roughness < SMOOTH_ROUGHNESS), over
    the FULL FRAME (non-square tiles for non-square images). Row-major.

    Full-frame (not center-cropped) so a defacement in the MARGINS — top/bottom
    strips on a portrait, side strips on a landscape — is covered. The tradeoff:
    a crop changes the frame, so a cropped image no longer maps tile-for-tile and
    reads ALTERED (strict — a crop IS an alteration). Resize preserves aspect, so
    the proportional tiling still aligns and an honest resize stays clean."""
    img = _get_Image().open(image_path).convert("RGB")
    px = img.load()
    w, h = img.size
    mean = bytearray(MEAN_BYTES)
    mn = bytearray(MIN_BYTES)
    mx = bytearray(MAX_BYTES)
    smooth = bytearray(FLAT_BYTES)
    for ty in range(GRID):
        y0 = (ty * h) // GRID
        y1 = ((ty + 1) * h) // GRID
        for tx in range(GRID):
            x0 = (tx * w) // GRID
            x1 = ((tx + 1) * w) // GRID
            # Gather the tile's luma rows so we can compute mean/min/max AND the
            # roughness (mean absolute adjacent-pixel difference, H + V).
            rows = []
            total = 0.0
            lo = 1e9
            hi = -1e9
            count = 0
            for y in range(y0, y1):
                row = []
                for x in range(x0, x1):
                    r, g, b = px[x, y]
                    lum = r * 0.299 + g * 0.587 + b * 0.114
                    row.append(lum)
                    total += lum
                    if lum < lo:
                        lo = lum
                    if lum > hi:
                        hi = lum
                    count += 1
                rows.append(row)
            i = ty * GRID + tx
            if count:
                mean[i] = _round8(total / count)
                mn[i] = _round8(lo)
                mx[i] = _round8(hi)
                hsum = hcount = 0.0
                for row in rows:
                    for j in range(len(row) - 1):
                        hsum += abs(row[j] - row[j + 1])
                        hcount += 1
                vsum = vcount = 0.0
                for a in range(len(rows) - 1):
                    ra, rb = rows[a], rows[a + 1]
                    for j in range(len(ra)):
                        vsum += abs(ra[j] - rb[j])
                        vcount += 1
                h_rough = hsum / hcount if hcount else 0.0
                v_rough = vsum / vcount if vcount else 0.0
                if (h_rough + v_rough) / 2.0 < SMOOTH_ROUGHNESS:
                    smooth[i >> 3] |= 1 << (i & 7)
    return bytes(mean), bytes(mn), bytes(mx), bytes(smooth)


def luma_grid_bytes(image_path: str) -> bytes:
    """The full stored blob: mean + min + max + smooth bitmap."""
    mean, mn, mx, flat = tile_stats(image_path)
    return mean + mn + mx + flat + bytes([GRID_VERSION])


def compute_luma_grid(image_path: str) -> str:
    """Mint-time entry point: base64 of the luma-grid blob, for the soul."""
    return base64.b64encode(luma_grid_bytes(image_path)).decode("ascii")


def dropped_stats(image_path: str):
    """(mean, min, max) uint8 byte arrays for the verify/dropped image — same
    tile math, no flatness (it comes from the stored reference)."""
    mean, mn, mx, _flat = tile_stats(image_path)
    return mean, mn, mx


def unpack(blob_b64: str):
    """Decode a stored grid to (mean, min, max, flat) byte arrays, or None if it
    isn't the current full-frame format (legacy center-square / pre-min-max /
    malformed — caller falls back to dHash-only EMBODIED, never cries wolf)."""
    try:
        raw = base64.b64decode(blob_b64)
    except Exception:
        return None
    if len(raw) != STORED_BYTES or raw[BLOB_BYTES] != GRID_VERSION:
        return None
    a, b, c = MEAN_BYTES, MEAN_BYTES + MIN_BYTES, MEAN_BYTES + MIN_BYTES + MAX_BYTES
    return raw[:a], raw[a:b], raw[b:c], raw[c:BLOB_BYTES]


def _median(values):
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return float(s[mid]) if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def evaluate(ref_mean, ref_min, ref_max, smooth, drop_mean, drop_min, drop_max):
    """(mark_max, high_max): the worst SMOOTH-tile min/max mark signal (exposure-
    normalized) and the worst all-tile mean residual. Bottom row(s) excluded.
    Twin of JS lumaEvaluate."""
    n = len(ref_mean)
    scored = n - SKIP_BOTTOM_ROWS * GRID
    g = _median([drop_mean[i] - ref_mean[i] for i in range(scored)])
    mark_max = 0.0
    high_max = 0.0
    for i in range(scored):
        resid = abs((drop_mean[i] - ref_mean[i]) - g)
        if resid > high_max:
            high_max = resid
        if (smooth[i >> 3] >> (i & 7)) & 1:
            dark = (ref_min[i] - drop_min[i]) + g     # mark darker than expected
            bright = (drop_max[i] - ref_max[i]) - g   # mark brighter than expected
            s = dark if dark > bright else bright
            if s > mark_max:
                mark_max = s
    return mark_max, high_max


def is_tampered(stored_b64: str, image_path: str,
                mark: float = MARK_THRESHOLD, high: float = HIGH_THRESHOLD) -> bool:
    """True if the dropped image is locally altered vs the stored grid."""
    unpacked = unpack(stored_b64)
    if unpacked is None:
        return False
    ref_mean, ref_min, ref_max, smooth = unpacked
    d_mean, d_min, d_max = dropped_stats(image_path)
    mark_max, high_max = evaluate(ref_mean, ref_min, ref_max, smooth, d_mean, d_min, d_max)
    return mark_max > mark or high_max > high
