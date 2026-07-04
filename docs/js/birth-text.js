// =====================================================================
// BIRTH TEXT LOOKUPS — reconstruct readable strings from trait codes.
// =====================================================================
// The soul stores `birth_traits` as an array of *integer codes* — the
// positions in the TRAIT_NAMES table below. The reading per trait, the
// overall temperament, and the one-line summary are all derived at
// display time from these tables — never persisted.
//
// Mirror of mememage/temperament.py (BIRTH_TRAIT_CODES, BIRTH_CONDITIONS,
// TEMPERAMENT_COMBOS, TEMPERAMENT_SINGLES). When trait codes change in
// Python, mirror them here. APPEND-ONLY: removing or reordering an
// existing entry would mute historical records that reference it by
// integer position.
// =====================================================================

(function (root) {

  // Integer code → trait name. Position IS the soul record's value.
  // APPEND-ONLY. Mirrors mememage/temperament.py:BIRTH_TRAIT_CODES.
  var TRAIT_NAMES = [
    'contested',       // 0
    'yielding',        // 1
    'uncontested',     // 2
    'stumbling',       // 3
    'sure_footed',     // 4
    'reaching',        // 5
    'speculative',     // 6
    'cautious',        // 7
    'restless',        // 8
    'loosening_grip',  // 9
    'holding_tight',   // 10
    'in_flux',         // 11
    'entangled',       // 12
    'unraveled',       // 13
    'forged_in_fire',  // 14
    'under_pressure',  // 15
    'in_silence',      // 16
    'last_light',      // 17
    'untethered',      // 18
    'night_owl',       // 19
    'dawn',            // 20
  ];

  function traitName(code) {
    if (typeof code === 'string') return code; // tolerate legacy/test data
    if (typeof code !== 'number') return null;
    return TRAIT_NAMES[code] || null;
  }

  // name → reading string
  var BIRTH_READINGS = {
    contested:      'The CPU was contested — threads jostling at the moment of birth',
    yielding:       'The CPU was yielding — a brief window of cooperation',
    uncontested:    'The CPU was at ease — a calm moment between storms',
    stumbling:      'The machine stumbled — a page fault at the exact moment of conception',
    sure_footed:    'Sure-footed — the memory was aligned',
    reaching:       'The machine was reaching — hard faults echoed through the birth',
    speculative:    'Speculative surge — the OS was racing ahead of the program',
    cautious:       'The OS was cautious — taking no risks with memory',
    restless:       'The OS was restless — speculating but unsure',
    loosening_grip: 'Loosening grip — the machine was letting go of memory',
    holding_tight:  'Holding tight — every page was precious, nothing to spare',
    in_flux:        'Memory in flux — the machine was deciding what to keep',
    entangled:      'Entangled — the file descriptors aligned at a round number',
    unraveled:      'Unraveled — the connections were fraying at the edges',
    forged_in_fire: 'Forged in fire — the machine was overwhelmed',
    under_pressure: 'Born under pressure — the system was straining',
    in_silence:     'Born in silence — the machine was barely conscious',
    last_light:     'Born in the last light — power was fading',
    untethered:     'Born untethered — free from the wall',
    night_owl:      'Born in the small hours — the world was asleep',
    dawn:           'Born at first light',
  };

  // Trait-combination → (temperament, summary). First match wins.
  // Each entry: [Set<requiredTraitCodes>, temperament, summary].
  var TEMPERAMENT_COMBOS = [
    // Turbulent
    [['contested','stumbling','speculative'], 'A violent birth',     'CPU contested, memory stumbling, OS speculating — chaos at every level'],
    [['contested','stumbling'],                'A turbulent birth',   'The CPU fought for time while the machine stumbled on its memory'],
    [['contested','speculative'],              'A reckless birth',    'The CPU was contested and the OS was gambling on every page'],
    [['stumbling','loosening_grip'],           'A birth in collapse', 'Memory was faulting and the OS was letting go — the floor giving way'],
    [['forged_in_fire','contested'],           'Born in the furnace', 'Load was crushing and every thread fighting for survival'],
    [['speculative','loosening_grip'],         'An unstable birth',   'The OS was speculating while releasing memory — a system on the edge'],
    [['reaching','in_flux'],                   'A grasping birth',    'Hard faults and shifting memory — the machine was reaching for something'],
    [['under_pressure','holding_tight'],       'A clenched birth',    'Under pressure but refusing to release — every page held in a fist'],
    [['contested','reaching'],                 'A strained birth',    'CPU contested and hard faults rippling — two pressures at once'],
    // Calm
    [['uncontested','sure_footed','cautious'], 'A perfect birth',     'CPU clear, memory solid, OS confident — everything aligned'],
    [['uncontested','sure_footed'],            'A clean birth',       'No contention, no faults — the machine was certain'],
    [['uncontested','cautious'],               'A deliberate birth',  'CPU clear and OS cautious — nothing left to chance'],
    [['sure_footed','holding_tight'],          'A steadfast birth',   'Memory aligned and nothing released — the machine was solid'],
    [['in_silence','sure_footed'],             'A meditative birth',  'The machine was barely awake and every page was ready'],
    [['yielding','restless'],                  'A restless birth',    'The CPU was yielding but the OS was restless — a quiet tension'],
    [['yielding','cautious'],                  'A patient birth',     'The CPU yielded and the OS held back — both waiting for the right moment'],
    // Night
    [['night_owl','contested'],                'A fever dream',       'The small hours with the CPU contested — a restless machine at night'],
    [['night_owl','uncontested'],              'A lucid dream',       'The small hours with a clear CPU — a dreaming machine at peace'],
    [['night_owl','stumbling'],                'A nightmare',         'The small hours with the machine stumbling — tossing in its sleep'],
    [['dawn','sure_footed'],                   'A first breath',      "Born at dawn with every page in place — the day's first creation"],
    [['dawn','speculative'],                   'An eager dawn',       'Born at first light with the OS already racing ahead'],
    // Frequent real-world pairs
    [['contested','restless'],                 'An agitated birth',   "CPU contested and OS restless — the machine couldn't settle"],
    [['contested','unraveled'],                'A fraying birth',     'CPU contested and connections fraying — pressure from all sides'],
    [['sure_footed','yielding'],               'A graceful birth',    'Memory solid and CPU yielding — a moment of elegant coordination'],
    [['in_flux','restless'],                   'A shifting birth',    'Memory in flux and OS restless — nothing was fixed in place'],
    [['reaching','restless'],                  'A searching birth',   'Hard faults and a restless OS — the machine was reaching for something'],
    [['contested','in_flux'],                  'A roiling birth',     'CPU contested while memory shifted beneath — turbulence at every layer'],
    [['sure_footed','restless'],               'A paradox birth',     'Memory was solid but the OS was restless — certainty and unease at once'],
    [['uncontested','restless'],               'A watchful birth',    'CPU clear but the OS restless — calm on the surface, stirring below'],
    // Entanglement
    [['entangled','contested'],                'A knotted birth',     'Connections aligned and threads contested — deeply tangled'],
    [['unraveled','loosening_grip'],           'An unraveling birth', 'Connections fraying and memory releasing — the machine was coming apart'],
    [['entangled','holding_tight'],            'A taut birth',        'Everything connected and nothing released — a system wound tight'],
    [['unraveled','restless'],                 'A dissolving birth',  'Connections fraying and OS restless — the machine was losing its shape'],
  ];

  // Single-trait temperament fallbacks (when no combo matches).
  var TEMPERAMENT_SINGLES = {
    contested:      'A contested birth',
    yielding:       'A yielding birth',
    uncontested:    'An uncontested birth',
    stumbling:      'A stumbling birth',
    sure_footed:    'A sure-footed birth',
    reaching:       'A reaching birth',
    speculative:    'A speculative birth',
    cautious:       'A cautious birth',
    restless:       'A restless birth',
    loosening_grip: 'A loosening birth',
    holding_tight:  'A clenched birth',
    in_flux:        'A birth in flux',
    entangled:      'An entangled birth',
    unraveled:      'An unraveled birth',
    forged_in_fire: 'A volatile birth',
    under_pressure: 'A strained birth',
    in_silence:     'A silent birth',
    last_light:     'A dying birth',
    untethered:     'An untethered birth',
    night_owl:      'A nocturnal birth',
    dawn:           'A dawn birth',
  };

  // Reconstruct {readings, temperament, summary} from a trait code array.
  // Accepts ints (current soul format) or strings (legacy/test data).
  // Returns the same shape the old persisted fields had.
  function readBirthTexts(traits) {
    var names = (traits || []).map(traitName)
                              .filter(function (n) { return !!n; });
    var readings = names.map(function (n) { return BIRTH_READINGS[n]; })
                        .filter(function (r) { return !!r; });
    var traitSet = {};
    names.forEach(function (n) { traitSet[n] = true; });
    var temperament = null;
    var summary = null;
    for (var i = 0; i < TEMPERAMENT_COMBOS.length; i++) {
      var combo = TEMPERAMENT_COMBOS[i];
      var required = combo[0];
      var allMatch = true;
      for (var j = 0; j < required.length; j++) {
        if (!traitSet[required[j]]) { allMatch = false; break; }
      }
      if (allMatch) {
        temperament = combo[1];
        summary = combo[2];
        break;
      }
    }
    if (!temperament && names.length) {
      temperament = TEMPERAMENT_SINGLES[names[0]] || null;
    }
    if (!temperament) {
      temperament = 'A serene birth';
      summary = 'All was calm — the machine had nothing to prove';
    }
    if (!summary) {
      summary = readings[0] || 'All was calm — the machine had nothing to prove';
    }
    return { readings: readings, temperament: temperament, summary: summary };
  }

  root.BirthText = {
    TRAIT_NAMES: TRAIT_NAMES,
    READINGS: BIRTH_READINGS,
    COMBOS: TEMPERAMENT_COMBOS,
    SINGLES: TEMPERAMENT_SINGLES,
    name: traitName,
    read: readBirthTexts,
  };
})(typeof window !== 'undefined' ? window : this);
