// =====================================================================
// watermark.js — browser extractor for the distributed DCT watermark.
// Faithful port of mememage/watermark.py:extract_watermark (the read path
// only; embedding stays server-side at mint). The watermark carries the FULL
// 64-bit content hash (16 hex) spread across every eligible 8×8 block via a
// per-image DCT-coefficient sign. Extraction reads the SIGN of one mid-frequency
// coefficient per block and majority-votes — so 1-ULP cos differences vs Python
// can't flip a bit (sign-stable), and no byte-exact float parity is needed.
//
// Public:  extractWatermark(px, w, h, contentHash) -> diagnostics object | null
//   px:          RGBA Uint8ClampedArray (canvas getImageData().data)
//   contentHash: 16-hex content hash from the bar/soul (enables the fast,
//                per-image layout). Pass null for the legacy blind search.
//   returns: { hash, syncOk, confidence, mode, offsetX, offsetY,
//              coeffRow, coeffCol, blocks } or null if no watermark.
// =====================================================================

var WM_PAYLOAD_BITS = 72;      // 8 sync + 64 hash (full content hash). = WM_TILE_W*WM_TILE_H
var WM_SYNC_BITS = 8;
var WM_SYNC_MARKER = 0xAD;
var WM_HASH_BITS = WM_PAYLOAD_BITS - WM_SYNC_BITS;   // 64
var WM_SEED = 0x4D454D45;      // "MEME"
var WM_TILE_W = 9, WM_TILE_H = 8;
var WM_COEFF_POOL = [[3,3],[3,5],[3,4],[2,5],[4,3],[2,4],[4,2],[5,3],[4,4]];
var WM_EMBED_ROW = 4, WM_EMBED_COL = 3;   // legacy default position
var WM_BAR_MARGIN_PX = 16;
var WM_MIN_CONF_SYNC = 0.7, WM_MIN_CONF_NOSYNC = 0.65;

// 8×8 DCT-II orthonormal basis C[u][x] (mirror watermark.py:_get_dct_basis).
var _wmBasis = null;
function _wmDctBasis() {
  if (_wmBasis) return _wmBasis;
  var C = [];
  for (var u = 0; u < 8; u++) {
    C[u] = new Float64Array(8);
    for (var x = 0; x < 8; x++) C[u][x] = Math.cos(Math.PI * (2 * x + 1) * u / 16.0);
    if (u === 0) for (var k = 0; k < 8; k++) C[u][k] *= 1.0 / Math.sqrt(2.0);
  }
  var s = Math.sqrt(2.0 / 8.0);
  for (var uu = 0; uu < 8; uu++) for (var xx = 0; xx < 8; xx++) C[uu][xx] *= s;
  _wmBasis = C;
  return C;
}

// Luminance plane (BT.601), flat Float64Array indexed y*w+x.
function _wmLuminance(px, w, h) {
  var Y = new Float64Array(w * h);
  for (var i = 0, p = 0; i < w * h; i++, p += 4)
    Y[i] = 0.299 * px[p] + 0.587 * px[p + 1] + 0.114 * px[p + 2];
  return Y;
}

// Per-image params from the content hash (mirror _derive_embed_params). Uses
// direct hex parsing — NOT a SHA — so no SubtleCrypto needed.
function _wmDeriveParams(contentHash) {
  var seed = parseInt(contentHash.slice(0, 4), 16);
  var pos = WM_COEFF_POOL[seed % WM_COEFF_POOL.length];
  var tileSeed = (WM_SEED ^ parseInt(contentHash.slice(0, 8), 16)) >>> 0;
  return { row: pos[0], col: pos[1], tileSeed: tileSeed };
}

// Fisher-Yates over PAYLOAD_BITS with the same LCG as Python. The product
// 1664525*rng (rng<2^32) stays < 2^53 so it's exact; % 2^32 == Python's & mask.
function _wmBuildPerm(seed) {
  var perm = []; for (var k = 0; k < WM_PAYLOAD_BITS; k++) perm.push(k);
  var rng = seed >>> 0;
  for (var i = WM_PAYLOAD_BITS - 1; i > 0; i--) {
    rng = (1664525 * rng + 1013904223) % 4294967296;
    var j = rng % (i + 1);
    var t = perm[i]; perm[i] = perm[j]; perm[j] = t;
  }
  return perm;
}

function _wmBitIndex(bx, by, ox, oy, perm) {
  var gx = Math.floor(bx / 8), gy = Math.floor(by / 8);
  var tx = (gx + ox) % WM_TILE_W, ty = (gy + oy) % WM_TILE_H;
  return perm[ty * WM_TILE_W + tx];
}

// Eligible 8×8 block origins (mirror _get_all_blocks): skip the bottom bar margin.
function _wmAllBlocks(w, h) {
  var blocks = [];
  var maxBx = ((w / 8) | 0) * 8 - 8;
  var yLimit = (((h - WM_BAR_MARGIN_PX) / 8) | 0) * 8 - 8;
  if (yLimit < 0 || maxBx < 0) return blocks;
  for (var by = 0; by <= yLimit; by += 8)
    for (var bx = 0; bx <= maxBx; bx += 8) blocks.push([bx, by]);
  return blocks;
}

// One mid-frequency DCT coefficient for a block: (C @ block @ C^T)[r][c].
function _wmBlockCoeff(Y, w, bx, by, C, r, c) {
  // dct[r][c] = sum_i C[r][i] * (sum_j block[i][j] * C[c][j])
  var Cr = C[r], Cc = C[c], acc = 0.0;
  for (var i = 0; i < 8; i++) {
    var rowBase = (by + i) * w + bx, inner = 0.0;
    for (var j = 0; j < 8; j++) inner += Y[rowBase + j] * Cc[j];
    acc += Cr[i] * inner;
  }
  return acc;
}

function _wmPayloadBitsToHash(bits) {
  var sync = 0;
  for (var i = 0; i < WM_SYNC_BITS; i++) sync = (sync << 1) | bits[i];
  var syncOk = (sync === WM_SYNC_MARKER);
  // 64-bit hash → 16 hex. Build as two 32-bit halves to stay exact.
  var hi = 0, lo = 0;
  for (var k = WM_SYNC_BITS; k < WM_SYNC_BITS + 32; k++) hi = (hi * 2 + bits[k]);
  for (var m = WM_SYNC_BITS + 32; m < WM_PAYLOAD_BITS; m++) lo = (lo * 2 + bits[m]);
  function hex8(v) { var s = (v >>> 0).toString(16); return '00000000'.slice(s.length) + s; }
  return { hash: hex8(hi) + hex8(lo), syncOk: syncOk };
}

function _wmExtractAtOffset(coeffs, blocks, ox, oy, perm) {
  var votes = [];
  for (var b = 0; b < WM_PAYLOAD_BITS; b++) votes.push([0, 0]);
  for (var k = 0; k < blocks.length; k++) {
    var bx = blocks[k][0], by = blocks[k][1];
    var bi = _wmBitIndex(bx, by, ox, oy, perm);
    if (coeffs[k] > 0) votes[bi][1]++; else votes[bi][0]++;
  }
  var bits = [], margin = 0.0;
  for (var v = 0; v < WM_PAYLOAD_BITS; v++) {
    var tot = votes[v][0] + votes[v][1];
    if (tot === 0) return null;
    margin += Math.abs(votes[v][1] - votes[v][0]) / tot;
    bits.push(votes[v][1] > votes[v][0] ? 1 : 0);
  }
  var hr = _wmPayloadBitsToHash(bits);
  return { hash: hr.hash, syncOk: hr.syncOk, confidence: margin / WM_PAYLOAD_BITS };
}

// Luminance variance of an 8×8 block (the perceptual gate). Mirror bar... watermark.py.
function _wmBlockVariance(Y, w, bx, by) {
  var sum = 0, sq = 0;
  for (var i = 0; i < 8; i++) {
    var base = (by + i) * w + bx;
    for (var j = 0; j < 8; j++) { var v = Y[base + j]; sum += v; sq += v * v; }
  }
  var mean = sum / 64;
  return sq / 64 - mean * mean;
}

function _wmResult(r, syncMatched, ox, oy, mode, row, col, used) {
  return { hash: r.hash, syncOk: syncMatched, confidence: r.confidence, mode: mode,
           offsetX: ox, offsetY: oy, coeffRow: row, coeffCol: col, blocks: used };
}

function extractWatermark(px, w, h, contentHash) {
  var blocks = _wmAllBlocks(w, h);
  if (blocks.length < WM_PAYLOAD_BITS) return null;
  var C = _wmDctBasis();
  var Y = _wmLuminance(px, w, h);
  var row, col, perm, mode;
  if (contentHash) {
    var p = _wmDeriveParams(contentHash);
    row = p.row; col = p.col; perm = _wmBuildPerm(p.tileSeed); mode = 'per-image';
  } else {
    row = WM_EMBED_ROW; col = WM_EMBED_COL; perm = _wmBuildPerm(WM_SEED); mode = 'legacy';
  }
  // coefficient sign + variance per block (computed once)
  var coeffs = new Float64Array(blocks.length), vars = new Float64Array(blocks.length);
  for (var k = 0; k < blocks.length; k++) {
    coeffs[k] = _wmBlockCoeff(Y, w, blocks[k][0], blocks[k][1], C, row, col);
    vars[k] = _wmBlockVariance(Y, w, blocks[k][0], blocks[k][1]);
  }
  // Per-image: an offset whose hash EXACTLY matches the known content hash is
  // definitive (~1/2^64). But embedding gates OUT smooth blocks, so the extract
  // must too — otherwise the non-watermarked smooth blocks dilute the vote (esp.
  // smooth-heavy images through JPEG, where a bit flips). We don't know the chain's
  // gate, so sweep candidates and take the first that recovers the exact hash.
  var target = contentHash ? contentHash.slice(0, WM_HASH_BITS / 4) : null;
  var GATES = [40, 50, 30, 20, 0];
  var best = null;
  for (var gi = 0; gi < GATES.length; gi++) {
    var gate = GATES[gi], fc, fb;
    if (gate === 0) { fc = coeffs; fb = blocks; }
    else {
      fb = []; var fcArr = [];
      for (var b = 0; b < blocks.length; b++) if (vars[b] >= gate) { fb.push(blocks[b]); fcArr.push(coeffs[b]); }
      fc = fcArr;
    }
    if (fb.length < WM_PAYLOAD_BITS) continue;
    var bestSync = null, bestSyncConf = 0, bsOx = 0, bsOy = 0;
    var bestAny = null, bestAnyConf = 0, baOx = 0, baOy = 0, exact = null, exOx = 0, exOy = 0;
    for (var ox = 0; ox < WM_TILE_W && !exact; ox++) {
      for (var oy = 0; oy < WM_TILE_H; oy++) {
        var r = _wmExtractAtOffset(fc, fb, ox, oy, perm);
        if (!r) continue;
        if (target && r.hash === target) { exact = r; exOx = ox; exOy = oy; break; }
        if (r.syncOk && r.confidence > bestSyncConf) { bestSyncConf = r.confidence; bestSync = r; bsOx = ox; bsOy = oy; }
        if (r.confidence > bestAnyConf) { bestAnyConf = r.confidence; bestAny = r; baOx = ox; baOy = oy; }
      }
    }
    if (exact) return _wmResult(exact, true, exOx, exOy, mode, row, col, fb.length);  // definitive
    var cand = bestSync ? { r: bestSync, sync: true, ox: bsOx, oy: bsOy, conf: bestSyncConf, used: fb.length }
             : (bestAnyConf >= WM_MIN_CONF_NOSYNC ? { r: bestAny, sync: false, ox: baOx, oy: baOy, conf: bestAnyConf, used: fb.length } : null);
    if (cand && (!best || cand.conf > best.conf)) best = cand;
  }
  if (best) return _wmResult(best.r, best.sync, best.ox, best.oy, mode, row, col, best.used);
  return null;
}
