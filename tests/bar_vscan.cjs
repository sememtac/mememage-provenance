// JS decoder vertical-scan: the scan fallback in image-decode.js:decodeImageBar
// must find a bar that was moved off the bottom (content appended below it after
// minting). Mirrors bar.py:extract_bar's bottom-fast-path + scan fallback.
// Validates BOTH layouts (sequential at 480px, even-fill at 1216px).
const fs = require("fs"), path = require("path");
const DOCS = path.join(__dirname, "..", "docs", "js");
global.SIG_ROWS=2;global.HEADER_BAND=8;global.HEADER_PIXELS=24;global.FOOTER_PIXELS=24;global.RS_NSYM=6;
global.ASYM_DELTA=40;global.ASYM_FLOOR=50;global.ASYM_BOX_RADIUS=34;global.ASYM_SCALE_CAP=2.0;
global.RGB_THRESHOLD=128;global.PIXELS_PER_BIT=3;global.PIXELS_PER_BIT_NARROW=2;global.PIXELS_PER_BIT_MAX=6;
global.EVENFILL_MIN_BYTES=33;global.EVENFILL_MAX_BYTES=64;
eval(fs.readFileSync(path.join(DOCS, "rs.js"), "utf8"));
eval(fs.readFileSync(path.join(DOCS, "codec.js"), "utf8"));
eval(fs.readFileSync(path.join(DOCS, "image-decode.js"), "utf8"));

function content(w, h, s) {
  const px = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const i = (y * w + x) * 4;
    px[i] = (x * 3 + s) & 255; px[i + 1] = (y * 5 + s) & 255;
    px[i + 2] = ((x + y) * 2 + s) & 255; px[i + 3] = 255;
  }
  return px;
}

// The production scan logic from decodeImageBar (replicated for a raw px array,
// since decodeImageBar itself needs a browser canvas).
function scanDecode(px, w, h) {
  let hit = _decodeFrameAtHeight(px, w, h);
  if (!hit) for (let b = h - 1; b >= SIG_ROWS && !hit; b--)
    if (detectBar(px, w, b + 1)) hit = _decodeFrameAtHeight(px, w, b + 1);
  return hit ? decodePayload(hit.frame.payload) : null;
}

const ID = "mememage-aa8194d91f1da238", H16 = "47f11bad5dcc9ad2";
let fail = 0;
const eq = (d) => d && d.identifier === ID && d.content_hash === H16;

for (const W of [480, 1216]) {          // sequential, then even-fill
  const HH = 300, PAD = 40, TH = HH + PAD;
  const px = content(W, HH, 0);
  embedBarPayload(px, W, HH, packPayload(ID, H16));     // bar at the bottom

  if (!eq(scanDecode(px, W, HH))) { console.error(`FAIL[${W}] bottom`); fail = 1; }

  const px2 = new Uint8ClampedArray(W * TH * 4);
  px2.set(px, 0);                                       // barred rows on top
  px2.set(content(W, PAD, 99), W * HH * 4);             // content appended below
  if (_decodeFrameAtHeight(px2, W, TH)) { console.error(`FAIL[${W}] bottom found moved bar`); fail = 1; }
  if (!eq(scanDecode(px2, W, TH))) { console.error(`FAIL[${W}] scan missed moved bar`); fail = 1; }

  // extractBarScaleAware (codec.js) — the validator's Scale/JPEG survival
  // re-reads use this; it must scan too. scan=false stays bottom-only.
  if (!eq(extractBarScaleAware(px2, W, TH))) { console.error(`FAIL[${W}] extractBarScaleAware missed moved bar`); fail = 1; }
  if (extractBarScaleAware(px2, W, TH, false)) { console.error(`FAIL[${W}] extractBarScaleAware scan=false found moved bar`); fail = 1; }

  // bottomRow must report WHERE the bar is, so the validator crops the preview
  // there (not the bottom). The moved bar's bottom row stays at HH-1 in px2.
  const rMoved = extractBarScaleAware(px2, W, TH);
  if (rMoved && rMoved.bottomRow !== HH - 1) { console.error(`FAIL[${W}] moved bottomRow=${rMoved.bottomRow}, expected ${HH - 1}`); fail = 1; }
  const rBot = extractBarScaleAware(px, W, HH);
  if (rBot && rBot.bottomRow !== HH - 1) { console.error(`FAIL[${W}] bottom bottomRow=${rBot.bottomRow}, expected ${HH - 1}`); fail = 1; }
}

console.log(fail ? "VSCAN TESTS FAILED" : "VSCAN TESTS PASSED");
process.exit(fail);
