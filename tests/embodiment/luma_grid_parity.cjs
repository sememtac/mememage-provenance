// Parity harness for the luma grid (localized-tamper half of EMBODIED). Loads
// the REAL docs/js/verify.js under Node and runs lumaTileStatsFromSquareData /
// lumaEvaluate for the cases in the JSON at argv[2], so the pytest wrapper
// (tests/embodiment/test_luma_grid.py) can assert the browser math equals
// mememage/embodiment.py. The two must never drift.
//
// Input  JSON: { squares: [{side, data:[rgba...]}],
//                frames:  [{w, h, data:[rgba...]}],   // full-frame (non-square)
//                evalCases:[{ref:{mean,min,max,flat[]}, drop:{mean,min,max}}] }
// Output JSON: { stats: [...], frameStats: [...], evals: [{markMax, highMax}] }
//
// Run: node tests/embodiment/luma_grid_parity.cjs <cases.json>
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const _candidates = [
  path.join(__dirname, '..', '..', 'verify.js'),
  path.join(__dirname, '..', '..', 'docs', 'js', 'verify.js'),
];
const VERIFY_JS = _candidates.find((p) => fs.existsSync(p)) || _candidates[1];
const src = fs.readFileSync(VERIFY_JS, 'utf8');

const sandbox = { crypto, TextEncoder, console, atob: globalThis.atob, btoa: globalThis.btoa };
vm.createContext(sandbox);
vm.runInContext(
  src + '\n;globalThis.__api = { lumaTileStatsFromSquareData, lumaTileStatsFromData, lumaEvaluate };',
  sandbox,
  { filename: 'verify.js' }
);
const { lumaTileStatsFromSquareData, lumaTileStatsFromData, lumaEvaluate } = sandbox.__api;

const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = { stats: [], frameStats: [], evals: [] };

for (const sq of (cases.squares || [])) {
  const s = lumaTileStatsFromSquareData(Uint8ClampedArray.from(sq.data), sq.side);
  out.stats.push({mean: Array.from(s.mean), min: Array.from(s.min), max: Array.from(s.max)});
}
for (const fr of (cases.frames || [])) {
  const s = lumaTileStatsFromData(Uint8ClampedArray.from(fr.data), fr.w, fr.h);
  out.frameStats.push({mean: Array.from(s.mean), min: Array.from(s.min), max: Array.from(s.max)});
}
for (const c of (cases.evalCases || [])) {
  const ref = {
    mean: Uint8Array.from(c.ref.mean), min: Uint8Array.from(c.ref.min),
    max: Uint8Array.from(c.ref.max), flat: Uint8Array.from(c.ref.flat),
  };
  const drop = {
    mean: Uint8Array.from(c.drop.mean), min: Uint8Array.from(c.drop.min), max: Uint8Array.from(c.drop.max),
  };
  out.evals.push(lumaEvaluate(ref, drop));
}
process.stdout.write(JSON.stringify(out));
