// =====================================================================
// MMRng — shared deterministic seeded RNG used by every module that
// needs reproducible randomness (constellation layouts, starfield
// generation, audio drift, etc).
//
// Single source of truth so a fix to seeding behavior propagates
// to every caller. Was duplicated across cosmic-planetarium.js,
// cosmic-starfield.js, cosmic-audio.js — kept drifting subtly
// (some had `if (!s) s = 1` fallbacks, some didn't).
//
// Public API:
//   MMRng.make(seed)   → function returning [0, 1) reals (LCG)
//   MMRng.strSeed(str) → 31-bit unsigned int derived from a string
//
// Algorithm: classic LCG (constants from glibc), 31-bit state. Not
// cryptographic. Deterministic per seed.
// =====================================================================
var MMRng = (function() {
  'use strict';

  function make(seed) {
    var s = seed | 0;
    if (!s) s = 1;
    return function() {
      s = (s * 1103515245 + 12345) & 0x7FFFFFFF;
      return s / 0x7FFFFFFF;
    };
  }

  function strSeed(str) {
    var h = 0;
    var s = String(str || '');
    for (var i = 0; i < s.length; i++) {
      h = (h * 31 + s.charCodeAt(i)) & 0x7FFFFFFF;
    }
    return h || 1;
  }

  return { make: make, strSeed: strSeed };
})();
