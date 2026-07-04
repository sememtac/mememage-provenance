// Regression harness for the dark-matter unlock hash bug.
//
// Bug: on a dark_matter chain the stored content_hash is computed AFTER
// encryption, so it covers the SEALED SHELL (encrypted blobs + public
// fields), NOT the plaintext. The validator's maybeUnlockRecord merges
// decrypted plaintext (birth/origin/rarity/...) back into the record for
// display. If the WITNESSED hash is then recomputed over that merged
// record, the inclusion set picks up the extra plaintext keys AND the
// leftover ciphertext blobs -> hash differs from stored -> false
// "Hash Mismatch" after unlock.
//
// Fix: every hash recompute must run over the as-stored sealed shell.
// validator.js stamps record._sealedOriginal in maybeUnlockRecord and
// routes the three recompute sites through _sealedShellFor(rec). This
// harness reproduces both hash paths against the REAL verify.js engine
// and asserts (a) the merged path mismatches and (b) the sealed-shell
// path matches.
//
// Run directly: node tests/darkmatter_unlock_hash_repro.cjs
// Exit 0 + "ALL PASS" on success; throws (exit 1) on any failed assertion.

const fs = require('fs');
const vm = require('vm');
const path = require('path');

const VERIFY_JS = path.join(__dirname, '..', 'docs', 'js', 'verify.js');

// Load the real verify.js hashing engine into an isolated context. verify.js
// is browser script (no module.exports) but the hash path only needs
// crypto.subtle / TextEncoder, both Node globals. We append an export hook.
const src = fs.readFileSync(VERIFY_JS, 'utf8');
const sandbox = { crypto, TextEncoder, console, atob: globalThis.atob, btoa: globalThis.btoa };
vm.createContext(sandbox);
vm.runInContext(
  src + '\n;globalThis.__api = { computeContentHash, _hashSetForRecord, sha256_16 };',
  sandbox
);
const { computeContentHash } = sandbox.__api;

// Mirror of validator.js's helper (one-liner, kept in lockstep — the pytest
// wrapper asserts the source still defines it and wires the three sites).
function _sealedShellFor(rec) { return (rec && rec._sealedOriginal) || rec; }

// ---- Synthetic dark_matter SEALED SHELL (as stored on IA) -------------
// Public + ciphertext fields only. NO plaintext birth/origin/rarity/
// birth_traits/gps_time_locked — those were stripped at _step_encrypt.
const sealedShell = {
  identifier: 'phoenix-deadbeefcafe0001',
  hash_version: 1,
  parent_id: 'phoenix-deadbeefcafe0000',
  conceived: '2026-05-30T12:00:00Z',
  rendered: '2026-05-30T11:59:00Z',
  age: 'Age of Aries',
  width: 1024,
  height: 1024,
  constellation_hash: '1111222233334444',
  constellation_name: 'Gilamul',
  constellation_index: 4,
  heart_star_id: 'phoenix-deadbeefcafe0000',
  decoder_hash: 'aaaabbbbccccdddd',
  machine_fingerprint: 'ffffeeee00001111',
  public_key: '9'.repeat(64),
  key_fingerprint: '86cb4ed6af3fd6c5',
  chunks_root: '5555666677778888',
  chain_visibility: 1,            // dark_matter
  outer_position: 12,
  outer_total: 365,
  encrypted_fields: 'BASE64SEALEDSOULBLOB==',
  encrypted_chunks: 'BASE64SEALEDCHUNKSBLOB==',
  gps_password_locked: 'BASE64GPSPWBLOB==',
};

// Plaintext that maybeUnlockRecord merges back in after a successful
// decrypt. Every one of these IS in HASH_INCLUDED_V1, so a naive recompute
// over the merged record would hash them on top of the ciphertext blobs.
const revealedPlaintext = {
  origin: { prompt: 'a phoenix over a flooded ocean', seed: 7, model: 'flux' },
  birth: { sun: { sign: 0, deg: 12.5 }, moon: { phase: 3, illum: 0.61 } },
  rarity: { celestial: 20, machine: 15, entropy: 5 },
  birth_traits: [2, 7, 11],
  gps_time_locked: 'TIMELOCKPUZZLEBLOB==',
};

function assert(cond, msg) { if (!cond) throw new Error('ASSERT FAILED: ' + msg); }

(async () => {
  // Stored hash = hash of the sealed shell (mirrors the Python pipeline:
  // content hash computed after _step_encrypt).
  const storedHash = await computeContentHash(sealedShell);
  assert(typeof storedHash === 'string' && storedHash.length === 16,
    'storedHash should be 16 hex chars, got ' + storedHash);

  // Sanity: re-hashing the sealed shell reproduces the stored hash.
  const reHashShell = await computeContentHash(sealedShell);
  assert(reHashShell === storedHash, 'sealed shell must hash to stored deterministically');

  // Simulate maybeUnlockRecord: non-mutating copy + merged plaintext +
  // _sealedOriginal pointing back at the untouched shell.
  const merged = Object.assign({}, sealedShell, revealedPlaintext);
  merged._unlocked = true;
  merged._sealedOriginal = sealedShell;

  // (a) THE BUG: hashing the merged/unlocked record must NOT match stored
  // (proves the plaintext keys actually perturb the inclusion set — i.e.
  // the bug is real and this test would catch a regression to it).
  const mergedHash = await computeContentHash(merged);
  assert(mergedHash !== storedHash,
    'merged-record hash unexpectedly matched stored — synthetic record is not exercising the bug');

  // (b) THE FIX: hashing via the sealed-shell path MUST match stored.
  const fixedHash = await computeContentHash(_sealedShellFor(merged));
  assert(fixedHash === storedHash,
    'sealed-shell hash (' + fixedHash + ') != stored (' + storedHash + ') — the fix does not reproduce the stored hash');

  // (c) Helper no-op for records without _sealedOriginal (non-dark / Audit):
  const lightRec = { identifier: 'mememage-0000000000000001', hash_version: 1, width: 512, height: 512 };
  assert(_sealedShellFor(lightRec) === lightRec, 'helper must return the record itself when no _sealedOriginal');

  console.log('ALL PASS');
  console.log('  stored   =', storedHash);
  console.log('  merged   =', mergedHash, '(differs -> bug reproduced)');
  console.log('  sealed   =', fixedHash, '(matches -> fix verified)');
})().catch((e) => { console.error(e.message || e); process.exit(1); });
