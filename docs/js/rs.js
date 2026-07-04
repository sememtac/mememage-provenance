// Reed-Solomon codec over GF(2^8) — JavaScript port of mememage/rs.py
// Primitive polynomial: 0x11D, generator root: alpha = 0x02
// Used by Gen I bar frames for forward error correction.

const _PRIM = 0x11D;
const _GF_EXP = new Uint8Array(512);
const _GF_LOG = new Uint8Array(256);

(function initTables() {
  let x = 1;
  for (let i = 0; i < 255; i++) {
    _GF_EXP[i] = x;
    _GF_LOG[x] = i;
    x <<= 1;
    if (x & 0x100) x ^= _PRIM;
  }
  for (let i = 255; i < 512; i++) _GF_EXP[i] = _GF_EXP[i - 255];
})();

function gfMul(a, b) {
  if (a === 0 || b === 0) return 0;
  return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]];
}

function gfDiv(a, b) {
  if (b === 0) throw new Error('division by zero');
  if (a === 0) return 0;
  return _GF_EXP[(_GF_LOG[a] - _GF_LOG[b] + 255) % 255];
}

function gfPow(x, power) {
  if (x === 0) return 0;
  return _GF_EXP[(_GF_LOG[x] * power) % 255];
}

function gfPolyEval(poly, x) {
  let y = poly[0];
  for (let i = 1; i < poly.length; i++) y = gfMul(y, x) ^ poly[i];
  return y;
}

function syndromes(msg, nsym) {
  const s = [];
  for (let i = 0; i < nsym; i++) s.push(gfPolyEval(msg, _GF_EXP[i]));
  return s;
}

function berlekampMassey(synd, nsym) {
  let C = [1], B = [1], L = 0, m = 1, b = 1;
  for (let n = 0; n < nsym; n++) {
    let d = synd[n];
    for (let i = 1; i <= L && i < C.length; i++) d ^= gfMul(C[i], synd[n - i]);
    if (d === 0) { m++; continue; }
    if (2 * L <= n) {
      const T = C.slice();
      const coeff = gfDiv(d, b);
      const scaled = new Array(m).fill(0).concat(B.map(bi => gfMul(coeff, bi)));
      while (C.length < scaled.length) C.push(0);
      for (let i = 0; i < scaled.length; i++) C[i] ^= scaled[i];
      L = n + 1 - L; B = T; b = d; m = 1;
    } else {
      const coeff = gfDiv(d, b);
      const scaled = new Array(m).fill(0).concat(B.map(bi => gfMul(coeff, bi)));
      while (C.length < scaled.length) C.push(0);
      for (let i = 0; i < scaled.length; i++) C[i] ^= scaled[i];
      m++;
    }
  }
  if ((C.length - 1) * 2 > nsym) throw new Error('too many errors');
  return C;
}

function findErrors(errLoc, nmsg) {
  const errs = errLoc.length - 1, positions = [];
  for (let i = 0; i < nmsg; i++) {
    const xi = _GF_EXP[255 - i];
    let val = errLoc[0];
    for (let k = 1; k < errLoc.length; k++) val ^= gfMul(errLoc[k], gfPow(xi, k));
    if (val === 0) positions.push(i);
  }
  if (positions.length !== errs) throw new Error('cannot locate all errors');
  return positions;
}

function solveErrorValues(synd, positions) {
  const k = positions.length;
  if (k === 0) return {};
  // Build augmented matrix [A | S]
  const matrix = [];
  for (let i = 0; i < k; i++) {
    const row = [];
    for (let j = 0; j < k; j++) {
      const power = i * positions[j];
      row.push(power > 0 ? _GF_EXP[power % 255] : 1);
    }
    row.push(synd[i]);
    matrix.push(row);
  }
  // Gaussian elimination
  for (let col = 0; col < k; col++) {
    let pivot = null;
    for (let row = col; row < k; row++) { if (matrix[row][col] !== 0) { pivot = row; break; } }
    if (pivot === null) throw new Error('singular matrix');
    if (pivot !== col) { const tmp = matrix[col]; matrix[col] = matrix[pivot]; matrix[pivot] = tmp; }
    const inv = gfDiv(1, matrix[col][col]);
    for (let j = col; j <= k; j++) matrix[col][j] = gfMul(matrix[col][j], inv);
    for (let row = 0; row < k; row++) {
      if (row !== col && matrix[row][col] !== 0) {
        const factor = matrix[row][col];
        for (let j = col; j <= k; j++) matrix[row][j] ^= gfMul(factor, matrix[col][j]);
      }
    }
  }
  const result = {};
  for (let j = 0; j < k; j++) result[positions[j]] = matrix[j][k];
  return result;
}

function rsDecode(data, nsym) {
  if (nsym <= 0) return data;
  const msg = Array.from(data);
  const synd = syndromes(msg, nsym);
  if (Math.max(...synd) === 0) return msg.slice(0, msg.length - nsym);
  const errLoc = berlekampMassey(synd, nsym);
  const polyPositions = findErrors(errLoc, msg.length);
  const magnitudes = solveErrorValues(synd, polyPositions);
  const nmsg = msg.length;
  for (const [polyPos, mag] of Object.entries(magnitudes)) {
    msg[nmsg - 1 - parseInt(polyPos)] ^= mag;
  }
  const check = syndromes(msg, nsym);
  if (Math.max(...check) !== 0) throw new Error('RS decode failed');
  return msg.slice(0, msg.length - nsym);
}
