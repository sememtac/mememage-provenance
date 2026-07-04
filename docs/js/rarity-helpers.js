// =====================================================================
// RARITY HELPERS — reconstruct the derived rarity score from the record.
// =====================================================================
// Records store only the dice rolls (rarity.celestial/.machine/.entropy/
// .machine_signature/.sigil). The aggregate score is DERIVED, never
// persisted — so the scoring model can evolve forward and old records
// simply re-tier (their stored dice + content hash are untouched).
//
// Rarity v2 (luck-backbone): the bulk of the distribution comes from an
// entropy "luck" roll, plus a continuous "vigor" read of the machine —
// both deterministic from data already in the soul (born.machine), so
// they're recomputed here rather than stored.
//
// MUST mirror mememage/rarity.py. When the Python model changes, change
// this in lockstep (tests/test_rarity_parity.py pins them together).
// =====================================================================

(function (root) {
  var LUCK_MAX = 45;
  var LUCK_EXP = 2.6;
  var VIGOR_MAX = 25;
  var CELESTIAL_CAP = 15;
  var RARE_FLOOR = 40;
  // (cutoff, floor) — a rare independent roll floors luck into the top tiers.
  var LUCK_JACKPOT = [[0.9990, 90], [0.9955, 73], [0.9750, 56]];

  // Luck reads straight from the entropy hex (no hashing) so it reproduces the
  // Python value bit-for-bit: first 6 bytes → u, next 6 → j (both [0,1)).
  // 12 hex chars = 48 bits, within JS's safe-integer range.
  function _luck(entropyHex) {
    var clean = (entropyHex || '').replace(/\s/g, '').toLowerCase();
    if (clean.length < 24) return 0;
    var u = parseInt(clean.slice(0, 12), 16) / 281474976710656;   // 2^48
    var j = parseInt(clean.slice(12, 24), 16) / 281474976710656;
    if (isNaN(u) || isNaN(j)) return 0;
    var luck = Math.floor(LUCK_MAX * Math.pow(u, LUCK_EXP));
    for (var i = 0; i < LUCK_JACKPOT.length; i++) {
      if (j > LUCK_JACKPOT[i][0]) return Math.max(luck, LUCK_JACKPOT[i][1]);
    }
    return luck;
  }

  function _parseTps(diskIo) {
    if (!diskIo) return null;
    if (typeof diskIo === 'object') {
      return (typeof diskIo.tps === 'number') ? diskIo.tps : null;
    }
    var m = /([\d.]+)\s*tps/.exec(String(diskIo));
    return m ? parseFloat(m[1]) : null;
  }

  function _vigor(machine) {
    if (!machine) return 0;
    var nLoad = 0;
    var load = machine.load;
    if (Array.isArray(load) && load.length) {
      var cores = (machine.cores && machine.cores.total) || 1;
      var l1 = parseFloat(load[0]);
      if (!isNaN(l1)) nLoad = Math.min(1, l1 / Math.max(1, cores));
    }
    var tps = _parseTps(machine.disk_io);
    var nDisk = (tps != null) ? Math.min(1, tps / 400) : 0;
    var act = machine.mem_active || 0, comp = machine.mem_compressed || 0, free = machine.mem_free || 0;
    var total = act + comp + free;
    // Compression is the macOS/Linux pressure signal; Windows has no compressor
    // stat (comp 0) so fall back to the used fraction. Mirrors rarity.py.
    var nMem = total > 0 ? Math.min(1, (comp > 0 ? comp / total : act / total)) : 0;
    return Math.round(VIGOR_MAX * (0.5 * nLoad + 0.3 * nDisk + 0.2 * nMem));
  }

  function _sumPts(arr) {
    var s = 0;
    if (arr) for (var i = 0; i < arr.length; i++) {
      if (arr[i] && typeof arr[i].points === 'number') s += arr[i].points;
    }
    return s;
  }

  // compute(rarity, machine) — the derived score. ``machine`` (born.machine)
  // carries the entropy (for luck) and the vitals (for vigor); pass null for a
  // dice-only score (legacy callers).
  function compute(rarity, machine) {
    if (!rarity) return 0;
    machine = machine || {};
    var sum = _luck(machine.entropy || '');
    sum += _vigor(machine);
    if (typeof rarity.machine_signature === 'number') sum += rarity.machine_signature;
    sum += Math.min(CELESTIAL_CAP, _sumPts(rarity.celestial));
    sum += _sumPts(rarity.machine);
    sum += _sumPts(rarity.entropy);
    if (rarity.sigil && typeof rarity.sigil.points === 'number') sum += rarity.sigil.points;
    if (rarity.sigil) sum = Math.max(sum, RARE_FLOOR);
    if (sum < 0) sum = 0;
    if (sum > 255) sum = 255;
    return sum;
  }

  function fromRecord(record) {
    if (!record) return 0;
    if (typeof record.rarity_score === 'number') return record.rarity_score; // legacy
    var machine = (record.birth && record.birth.machine) || record.machine || {};
    return compute(record.rarity, machine);
  }

  root.RarityScore = { compute: compute, fromRecord: fromRecord };
})(typeof window !== 'undefined' ? window : this);
