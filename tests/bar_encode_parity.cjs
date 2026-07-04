// Parity harness for the bar WRITER. Loads the REAL docs/js/codec.js (+ rs.js)
// under Node and runs embedBarPayload for each case in the JSON array at
// argv[2], returning the resulting bottom-2-row pixels. The pytest wrapper
// (tests/test_bar_js_parity.py) builds the identical image in Python, runs
// bar.embed_into, and asserts the pixels are byte-for-byte identical — so the
// JS writer can never silently drift from the Python canonical writer.
//
// Run: node tests/bar_encode_parity.cjs <cases.json>   (prints a JSON array)
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const DOCS = path.join(__dirname, '..', 'docs', 'js');
const ALT = path.join(__dirname, 'js');  // core repo ships codec/rs under tests/js? fall back

function loadJs(name) {
  for (const base of [DOCS, ALT, path.join(__dirname, '..')]) {
    const p = path.join(base, name);
    if (fs.existsSync(p)) return fs.readFileSync(p, 'utf8');
  }
  throw new Error('cannot find ' + name);
}

const sandbox = { Math, Array, Uint8ClampedArray, console, parseInt, isNaN, String, TextEncoder };
vm.createContext(sandbox);
// Inject the codec constants the writer depends on (normally from data.js).
vm.runInContext(
  'var SIG_ROWS=2,HEADER_BAND=8,HEADER_PIXELS=24,FOOTER_PIXELS=24,PIXELS_PER_BIT=3,' +
  'PIXELS_PER_BIT_NARROW=2,PIXELS_PER_BIT_MAX=6,BAR_DELTA=64,LOCAL_CONTEXT_ROWS=6,RS_NSYM=6,RGB_THRESHOLD=128,' +
  'ASYM_ENCODE=true,ASYM_DELTA=40,ASYM_FLOOR=50,ASYM_BOX_RADIUS=34,ASYM_SCALE_CAP=2.0;',
  sandbox
);
vm.runInContext(loadJs('rs.js'), sandbox, { filename: 'rs.js' });
vm.runInContext(loadJs('codec.js'), sandbox, { filename: 'codec.js' });

const embedBarPayload = sandbox.embedBarPayload;

// Deterministic fills — MUST match the Python side exactly.
function fillPixel(mode, x, y, rgb) {
  if (mode === 'uniform') return rgb;
  // 'stripe' — a non-uniform pattern so the rows-above-bar mean is fractional,
  // exercising the banker's-rounding (pyRound) path in _dominantColor.
  return [(x % 7) + 20, (y % 5) + 40, ((x + y) % 11) + 60];
}

const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = cases.map(function (c) {
  const w = c.w, h = c.h;
  const px = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const rgb = fillPixel(c.fill, x, y, c.rgb);
      const i = (y * w + x) * 4;
      px[i] = rgb[0]; px[i + 1] = rgb[1]; px[i + 2] = rgb[2]; px[i + 3] = 255;
    }
  }
  // Pack the payload exactly as bar.py:embed_into does, via codec.js:packPayload.
  const payload = sandbox.packPayload(c.identifier, c.content_hash);
  let error = null;
  try {
    embedBarPayload(px, w, h, payload);
  } catch (e) {
    error = String(e && e.message || e);
  }
  // Return RGB of the bottom 2 rows (the only rows the writer touches).
  const rows = [];
  for (let y = h - 2; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      rows.push(px[i], px[i + 1], px[i + 2]);
    }
  }
  return { error: error, pixels: rows };
});
process.stdout.write(JSON.stringify(out));
