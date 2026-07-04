// =====================================================================
// decodeImageBar — shared image → bar-decode pipeline for By Sight
// (decoder) and the Image tab (validator). Handles the parts both
// pages need identically:
//
//   1. Load the file as an <img>, draw to a canvas, read pixels.
//   2. Sweep threshold candidates (Otsu, absolute 128, asym row-3 curve) ×
//      layouts (even-fill, then sequential at scale 1:1 + band-swept scales).
//   3. decodePayload on the first frame that passes CRC + Reed-Solomon.
//   Mirrors codec.js:extractBarScaleAware (this variant also returns the frame
//   for the validator's forensic report).
//
// Returns a structured result with the canvas + raw pixels so each
// page can run its own post-decode logic (decoder fetches + renders a
// cert; validator builds a forensic report).
//
// Depends on global primitives from js/codec.js: detectBar, extractBits,
// decodeFrame, decodePayload.
//
// Shape of the resolved value:
//   {
//     ok: bool                     — true if decoded payload is usable
//     detected: bool               — true if bar bands were detected
//     frame: {payload, …} | null   — the decoded frame if RS succeeded
//     decoded: {identifier, content_hash} | null
//     ppb: number                  — pixels-per-bit that worked (3 or 2)
//     canvas: HTMLCanvasElement    — the source canvas (for EMBODIED)
//     objUrl: string               — object URL (revoke when done)
//     pixels: Uint8ClampedArray    — raw RGBA data (for forensic work)
//     width, height: number
//     error: string | null         — error string on any failure
//   }
// =====================================================================
// --- Full-canvas band search — locate a relocated/pasted bar anywhere ---
// Mirrors bar.py's _bar_hspan / _scan_anywhere. The M/Y/C↔C/Y/M bands are a
// "data begins/ends here" fiducial, so a bar whose canvas was extended
// (side/top/bottom margins) or that was pasted into a larger image can still be
// found: scan a row's colour runs for the header (M,Y,C) and footer (C,Y,M)
// ANYWHERE, crop to that span, and decode it flush. CRC + RS reject false band
// matches. Runs only after the fast + edge-anchored vertical scans fail.
var _HSPAN_MIN_RUN = 3;

function _rowColorRuns(px, w, y) {
  var runs = [], x = 0, base = y * w * 4;
  function cls(i) {
    var r = px[base + i * 4], g = px[base + i * 4 + 1], b = px[base + i * 4 + 2];
    if (r > 130 && g < 120 && b > 130) return 'M';
    if (r > 130 && g > 130 && b < 120) return 'Y';
    if (r < 120 && g > 130 && b > 130) return 'C';
    return '.';
  }
  while (x < w) {
    var c = cls(x);
    if (c === '.') { x++; continue; }
    var j = x + 1;
    while (j < w && cls(j) === c) j++;
    runs.push([c, x, j - x]);
    x = j;
  }
  return runs;
}

function _barHspan(px, w, h, y) {
  if (y < SIG_ROWS || y >= h || w < 2 * _HSPAN_MIN_RUN * 3) return null;
  var all = _rowColorRuns(px, w, y), runs = [];
  for (var i = 0; i < all.length; i++) if (all[i][2] >= _HSPAN_MIN_RUN) runs.push(all[i]);
  if (runs.length < 6) return null;
  function adjacent(k0, seq) {
    for (var k = 0; k < 3; k++) if (runs[k0 + k][0] !== seq[k]) return false;
    for (var g = 0; g < 2; g++) {
      var gap = runs[k0 + g + 1][1] - (runs[k0 + g][1] + runs[k0 + g][2]);
      if (gap < 0 || gap > 4) return false;
    }
    return true;
  }
  var x0 = null;
  for (var a = 0; a <= runs.length - 3; a++) if (adjacent(a, ['M', 'Y', 'C'])) { x0 = runs[a][1]; break; }
  if (x0 === null) return null;
  var x1 = null;
  for (var b = runs.length - 3; b >= 0; b--) if (adjacent(b, ['C', 'Y', 'M'])) { x1 = runs[b + 2][1] + runs[b + 2][2] - 1; break; }
  if (x1 === null || x1 - x0 + 1 < 2 * _HSPAN_MIN_RUN * 3) return null;
  return [x0, x1];
}

function _cropPixels(px, w, x0, x1, yBottom) {
  var cw = x1 - x0 + 1, ch = yBottom + 1;
  var out = new Uint8ClampedArray(cw * ch * 4);
  var rowBytes = cw * 4;
  for (var yy = 0; yy < ch; yy++) {
    var src = (yy * w + x0) * 4, dst = yy * rowBytes;
    for (var k = 0; k < rowBytes; k++) out[dst + k] = px[src + k];
  }
  return { px: out, w: cw, h: ch };
}

async function decodeImageBar(file) {
  var objUrl = URL.createObjectURL(file);
  var img = new Image();
  // Attach handlers BEFORE setting src so a cache-warm image can't
  // fire onload before the Promise wires up (theoretical for blob
  // URLs but harmless either way).
  var loadPromise = new Promise(function(resolve, reject) {
    img.onload = resolve;
    img.onerror = function() { reject(new Error('image load failed')); };
  });
  img.src = objUrl;
  try {
    await loadPromise;
  } catch (e) {
    URL.revokeObjectURL(objUrl);
    console.error('[image-decode] image load failed:', e);
    return { ok: false, detected: false, frame: null, decoded: null, error: 'image load failed' };
  }

  var canvas, px;
  try {
    canvas = document.createElement('canvas');
    canvas.width = img.width;
    canvas.height = img.height;
    // willReadFrequently: this canvas is read back more than once — the bar
    // decode below AND the downstream dHash/luma-grid checks (verify.js reuses
    // res.canvas). Without the flag Chrome keeps it GPU-backed and warns on the
    // second getImageData; setting it at the FIRST getContext picks the
    // CPU-backed fast path (a later getContext can't change an existing context).
    var ctx = canvas.getContext('2d', {willReadFrequently: true});
    ctx.drawImage(img, 0, 0);
    px = ctx.getImageData(0, 0, img.width, img.height).data;
  } catch (e) {
    URL.revokeObjectURL(objUrl);
    console.error('[image-decode] canvas read failed:', e);
    return { ok: false, detected: false, frame: null, decoded: null, error: 'canvas read failed (' + (e.message || e) + ')' };
  }

  var base = {
    canvas: canvas,
    objUrl: objUrl,
    pixels: px,
    width: img.width,
    height: img.height
  };

  var frame = null, usedPpb = 3, detected = false;
  var W = img.width, H = img.height;
  var barRow = H - 1;   // bottom row of the bar (h-1 at the bottom; the scan updates it)

  // Presence: M/Y/C bands at the bottom (the embed position). A decoded frame
  // (below) also proves presence even if band detection was fooled by the asym
  // data pixels masking the M/Y/C edges under heavy recompression.
  if (detectBar(px, W, H) || detectBarBands(px, W, H)) detected = true;

  // Fast path: read the bar at the bottom, where the encoder always writes it.
  var hit = _decodeFrameAtHeight(px, W, H);

  // Fallback: vertical scan — read the bar wherever its band signature appears,
  // in case it was relocated or content was appended below it AFTER minting. The
  // encoder never moves the bar; the scan only READS one that something else
  // moved. A cheap per-row band gate rejects most rows, and CRC+RS self-select
  // per candidate (passing a reduced h reads a higher row pair with no pixel
  // copying). Mirrors bar.py:extract_bar's scan fallback.
  if (!hit) {
    for (var b = H - 1; b >= SIG_ROWS && !hit; b--) {
      if (detectBar(px, W, b + 1)) { hit = _decodeFrameAtHeight(px, W, b + 1); if (hit) barRow = b; }
    }
  }

  // Last resort: the bar isn't bottom-anchored OR full-width — its canvas was
  // extended (margins) or it was pasted into a larger image. Find it by its
  // band signature anywhere, crop to that span, decode flush. Mirrors
  // bar.py:_scan_anywhere. Bounded to keep the UI responsive (it's a rare
  // fallback and a per-row full-width band scan is O(W·H)).
  if (!hit && W * H <= 16000000) {
    for (var ay = H - 1; ay >= SIG_ROWS && !hit; ay--) {
      var span = _barHspan(px, W, H, ay);
      if (span) {
        var cr = _cropPixels(px, W, span[0], span[1], ay);
        var chit = _decodeFrameAtHeight(cr.px, cr.w, cr.h);
        if (chit) { hit = chit; barRow = ay; }
      }
    }
  }
  if (hit) { frame = hit.frame; usedPpb = hit.ppb; detected = true; }
  if (!detected) {
    return Object.assign({ ok: false, detected: false, frame: null, decoded: null, error: 'No Mememage bar in this image.' }, base);
  }
  if (!frame) {
    return Object.assign({ ok: false, detected: true, frame: null, decoded: null, error: 'Bar detected but the payload is unreadable.' }, base);
  }

  var decoded = decodePayload(frame.payload);
  if (!decoded) {
    // Friendly nudge: if the payload starts with a band-fragment tag
    // byte (0x01 gen / 0x02 sky / 0x03 machine), it's a saved band
    // PNG, not a full image. Tell the user where to go instead of
    // "unreadable".
    var p = frame.payload;
    if (p && p.length >= 1 && (p[0] === 0x01 || p[0] === 0x02 || p[0] === 0x03)) {
      var fid = p[0] === 0x01 ? 'gen' : p[0] === 0x02 ? 'sky' : 'machine';
      return Object.assign({
        ok: false, detected: true, frame: frame, decoded: null, ppb: usedPpb, fragment: fid,
        error: 'This is the ' + fid + ' band of a saved certificate \u2014 drop it into the validator\u2019s reconstruct box to gather the bar.'
      }, base);
    }
    return Object.assign({ ok: false, detected: true, frame: frame, decoded: null, ppb: usedPpb, barRow: barRow, error: 'Bar detected but the payload is unreadable.' }, base);
  }

  return Object.assign({ ok: true, detected: true, frame: frame, decoded: decoded, ppb: usedPpb, barRow: barRow, error: null }, base);
}

// Decode the bar whose bottom row is h-1. The px array may be taller than h —
// only rows < h are read, so the vertical scan in decodeImageBar passes a
// reduced h to read a bar at an arbitrary height with NO pixel copying. Returns
// {frame, ppb} or null. The non-scanning core; mirrors bar.py:_extract_at_bottom.
//
// Threshold candidates: the asym per-column curve (PRIMARY) + Otsu's per-image
// bimodal midpoint and the absolute 128 as scalar FALLBACKS that rescue hard
// content where the asym curve's per-channel clamp eats the delta margin (e.g.
// pure-saturated backgrounds). CRC + RS self-select; the post-RS CRC re-check
// guards miscorrections. Band detection only ADDS the resized-scale sweep —
// scale 1:1 is ALWAYS tried (band detection can fail on a heavily-recompressed
// asym bar even when the 1:1 read decodes cleanly).
function _decodeFrameAtHeight(px, w, h) {
  var thrs = [];
  try { thrs.push(_asymThresholdCurve(px, w, h)); } catch (e) {}
  var ot = otsuThreshold(px, w, h);
  if (ot !== null) thrs.push(ot);
  thrs.push(RGB_THRESHOLD);

  var bands = detectBarBands(px, w, h);
  var scales = [1.0];
  if (bands) {
    var raw_scale = (bands.m + bands.y + bands.c) / 3 / HEADER_BAND;
    if (Math.abs(raw_scale - 1.0) >= 0.05) {
      for (var off = -8; off <= 8; off++) {
        var s = Math.round((raw_scale + off * 0.01) * 1000) / 1000;
        if (s > 0.3 && s < 3.0 && Math.abs(s - 1.0) >= 0.005 && scales.indexOf(s) < 0) scales.push(s);
      }
    }
  }

  for (var ti = 0; ti < thrs.length; ti++) {
    var thr = thrs[ti];
    // High-res even-fill layout first (full-width, both-ends anchored).
    var efFrame = decodeEvenFill(px, w, h, thr);
    if (efFrame) return { frame: efFrame, ppb: 3 };
    // Sequential layout — scale 1:1 first (common case), then swept scales.
    // px/bit swept widest-first (encoder picks the widest that fits); CRC/RS selects.
    for (var si = 0; si < scales.length; si++) {
      for (var pb = PIXELS_PER_BIT_MAX; pb >= PIXELS_PER_BIT_NARROW; pb--) {
        var bits = extractBitsAtScale(px, w, h, scales[si], pb, thr);
        var fr = decodeFrame(bits);
        if (fr) return { frame: fr, ppb: pb };
      }
    }
  }
  return null;
}

// Find EVERY decodable bar in the image, each with its placement — the union
// of the edge-anchored vertical scan (bottom / different-height, full width)
// and the full-canvas band search (offset / pasted), de-duped by payload.
// Mirrors bar.py:extract_bars. Each entry:
//   { identifier, content_hash, barRow, x0, x1, fullWidth, ppb }
// Heavier than a single decode (it scans every row and doesn't stop at the
// first hit), so the validator runs it on a dropped image where the user is
// already waiting for a forensic report; bounded to <=16M px.
function decodeAllBars(px, W, H) {
  var out = [], seen = {};
  function add(hit, row, x0, x1, fullWidth) {
    if (!hit) return;
    var d = decodePayload(hit.frame.payload);
    if (!d) return;
    var key = d.identifier + '|' + d.content_hash;
    if (seen[key]) return;
    seen[key] = true;
    out.push({ identifier: d.identifier, content_hash: d.content_hash,
               barRow: row, x0: x0, x1: x1, fullWidth: fullWidth, ppb: hit.ppb,
               frame: hit.frame });
  }
  for (var b = H - 1; b >= SIG_ROWS; b--) {
    if (detectBar(px, W, b + 1)) add(_decodeFrameAtHeight(px, W, b + 1), b, 0, W - 1, true);
  }
  if (W * H <= 16000000) {
    for (var ay = H - 1; ay >= SIG_ROWS; ay--) {
      var span = _barHspan(px, W, H, ay);
      if (span) {
        var cr = _cropPixels(px, W, span[0], span[1], ay);
        add(_decodeFrameAtHeight(cr.px, cr.w, cr.h), ay, span[0], span[1], false);
      }
    }
  }
  return out;
}
