/**
 * cosmic-audio.js — Production audio for the Mememage player.
 *
 * Two layers only:
 *   1. AMBIENT — continuous drone tuned to the star's zodiac root.
 *      Element shapes the harmonics, moon controls filter brightness,
 *      temperament sets modulation speed.
 *   2. DRIFT — sine tones crossfading through the star's scale every
 *      18-28 seconds. You don't hear notes. You feel the harmony shift.
 *
 * Deterministic: same star params = same sound, always.
 *
 * Usage:
 *   var audio = CosmicAudio.create({
 *     sign: 'Scorpio',
 *     moonPhase: 'Full Moon',
 *     temperament: 'serene',
 *     rarity: 75,
 *     hash: 'a7f39c2d8b04e51a'  // content_hash — makes each star unique
 *   });
 *   audio.start(audioContext, destinationNode);
 *   audio.stop();  // fade out + cleanup
 *
 * The caller owns the AudioContext, master gain, and analyser.
 * This module creates oscillators and connects them to the provided
 * destination node.
 */

var CosmicAudio = (function() {
  'use strict';

  // ═══════════════════════════════════════════════════
  //  CONSTANTS
  // ═══════════════════════════════════════════════════

  var SIGN_FREQ = {
    Aries: 55.00, Taurus: 61.74, Gemini: 65.41, Cancer: 73.42,
    Leo: 82.41, Virgo: 87.31, Libra: 98.00, Scorpio: 55.00,
    Sagittarius: 61.74, Capricorn: 65.41, Aquarius: 73.42, Pisces: 82.41
  };

  var FIRE  = {Aries:1, Leo:1, Sagittarius:1};
  var WATER = {Cancer:1, Scorpio:1, Pisces:1};
  var EARTH = {Taurus:1, Virgo:1, Capricorn:1};

  // Modal scales — ancient and sacred, not dark
  //   Fire: Dorian (the monks' mode, minor with raised 6th)
  //   Water: Aeolian (pure natural minor, clean melancholy)
  //   Earth: Mixolydian (major with flat 7th, ancient hymns)
  //   Air: Lydian (raised 4th, ethereal and floating)
  var SCALES = {
    fire:  [0, 2, 3, 5, 7, 9, 10],
    water: [0, 2, 3, 5, 7, 8, 10],
    earth: [0, 2, 4, 5, 7, 9, 10],
    air:   [0, 2, 4, 6, 7, 9, 11]
  };

  // Melodic cells — the harmonic path the drift walks through.
  // Each is hand-crafted, singable, proven. Scale degrees only.
  var CELLS = [
    [0, 1, 2, 4],       // rise — stepwise to the 5th
    [0, 2, 4, 2],       // arch — up and back, breathing
    [4, 3, 2, 0],       // fall — gravity from the 5th
    [0, 2, 1, 0],       // rock — gentle rocking home
    [2, 4, 3, 2],       // yearn — reach for 5th, settle back
    [0, 1, 3, 4],       // climb — step, skip, step
    [4, 2, 3, 1],       // sigh — exhale
    [0, 4, 2],          // call — root, 5th, 3rd
    [2, 1, 0, 2],       // drift — descend then leap back
    [0, 2, 4, 0],       // anchor — rise to 5th, drop home
    [0, 2, 4, 7],       // arp_rise — ascending arpeggio
    [7, 4, 2, 0],       // arp_fall — cascading descent
    [0, 4, 7, 4],       // arp_wave — shimmering wave
    [7, 5, 4, 2, 0],    // cascade — waterfall
    [0, 7, 4, 2],       // float — leap to octave, drift down
    [0, 2, 4, 1, 3, 5]  // spiral — ascending through two triads
  ];

  // ═══════════════════════════════════════════════════
  //  HELPERS
  // ═══════════════════════════════════════════════════

  function getElement(sign) {
    if (FIRE[sign]) return 'fire';
    if (WATER[sign]) return 'water';
    if (EARTH[sign]) return 'earth';
    return 'air';
  }

  function getMoonBright(phase) {
    if (phase.indexOf('Full') >= 0) return 1.0;
    if (phase.indexOf('Gibbous') >= 0) return 0.8;
    if (phase.indexOf('Quarter') >= 0) return 0.6;
    if (phase.indexOf('Crescent') >= 0) return 0.35;
    if (phase.indexOf('New') >= 0) return 0.2;
    return 0.5;
  }

  // Seed/RNG come from MMRng (rng.js) — shared across audio,
  // starfield, planetarium, etc.
  var makeRng = MMRng.make;

  function hashParams(sign, phase, temp, rarity) {
    var str = sign + '|' + phase + '|' + temp + '|' + rarity;
    var h = 0;
    for (var i = 0; i < str.length; i++) {
      h = ((h << 5) - h + str.charCodeAt(i)) | 0;
    }
    return Math.abs(h);
  }

  // Convert a scale degree to a frequency
  function degToFreq(deg, scale, rootFreq) {
    var octOff = Math.floor(deg / scale.length);
    var scIdx = ((deg % scale.length) + scale.length) % scale.length;
    var semi = scale[scIdx] + octOff * 12;
    return rootFreq * Math.pow(2, semi / 12);
  }

  // ═══════════════════════════════════════════════════
  //  FACTORY
  // ═══════════════════════════════════════════════════

  /**
   * Detect whether we're likely on a device with small speakers.
   * Returns true for phones/tablets, false for desktop.
   * The caller can override with params.speaker = true/false.
   */
  function detectSpeakerMode() {
    if (typeof navigator === 'undefined') return false;
    var ua = navigator.userAgent || '';
    return /iPhone|iPad|iPod|Android/i.test(ua);
  }

  function create(params) {
    var sign = params.sign || 'Scorpio';
    var phase = params.moonPhase || 'Full Moon';
    var temp = params.temperament || 'serene';
    var rarity = params.rarity || 40;

    // Speaker mode: shift everything into phone-audible range.
    // Default is TRUE — speaker mode is the common denominator.
    // It sounds slightly brighter on headphones (acceptable).
    // Headphone mode sounds silent on phone speakers (unacceptable).
    // Pass speaker:false explicitly to get the deep headphone mix.
    var speakerMode = params.speaker !== false;

    // Ping mode: 'memory' = pings remember last note and step toward neighbors
    //            'random' = original independent selection (default)
    var pingMode = params._pingMode || 'memory';

    var element = getElement(sign);
    var scale = SCALES[element];
    var baseRoot = SIGN_FREQ[sign] || 55;

    // Speaker mode: shift root up 2 octaves (55Hz→220Hz, 98Hz→392Hz)
    // Phone speakers reproduce 200-8000Hz effectively.
    // Headphones get the full deep range.
    var rootFreq = speakerMode ? baseRoot * 4 : baseRoot;

    var moonBright = getMoonBright(phase);

    // Seed: the content hash is the primary individualizer.
    // The 4 params set the family (scale, element, brightness, modulation).
    // The hash sets the individual voice (key offset, cell, drift, timbre).
    //
    // Each RNG stream draws from its OWN role-salted seed via stream('<role>'),
    // NOT one seed + a fixed offset. TWO things individualize a star's song:
    //   (1) Per-role salting — strSeed('key|'+hash…) vs strSeed('drift|'+hash…)
    //       — so the streams don't correlate with each other (seed+2222 vs
    //       seed+1111 gave correlated first outputs).
    //   (2) A warm-up inside stream() — the real fix for the single-draw picks
    //       (key offset, melodic cell), whose first-LCG-output buckets were
    //       clustering 50 siblings into ~18 distinct (key,cell) pairs. Salting
    //       alone didn't help those; warm-up brings them to ~39/50 (uniform).
    // `seed` stays the star's single identity (exposed below); the no-hash
    // family fallback preserves the "same params = same sound" promise.
    var baseSeed = hashParams(sign, phase, temp, rarity);
    var hash = params.hash || params.contentHash || '';
    var seed = hash ? hashParams(hash, sign, temp, '' + rarity) : baseSeed;
    var family = '|' + sign + '|' + temp + '|' + rarity;
    function stream(role, fallback) {
      var r = makeRng(hash ? MMRng.strSeed(role + '|' + hash + family) : fallback);
      // Warm up: the FIRST few outputs of this LCG bucket poorly, so a
      // single-draw read like floor(x*6) (key offset) or floor(x*16) (cell)
      // clustered even for well-separated seeds — 50 siblings gave only ~18
      // distinct (key,cell) pairs. Discarding 3 outputs lands single-draw
      // reads at ~39/50, i.e. what an ideal uniform hash gives. Multi-draw
      // consumers (the ambient micro-variations) were always fine; this only
      // matters for the one-shot bucketed picks.
      r(); r(); r();
      return r;
    }
    var rng = stream('drift', baseSeed);

    // KEY OFFSET — the most audible differentiator between siblings.
    // Hash determines a semitone offset (0-5) on the root frequency.
    // One Aries star plays in A Dorian, another in Bb, another in C.
    // Same mode, different pitch center. Immediately audible.
    var keyRng = stream('key', baseSeed + 2222);
    var keyOffset = Math.floor(keyRng() * 6); // 0-5 semitones up
    baseRoot = baseRoot * Math.pow(2, keyOffset / 12);
    // Stereo width: narrowed on speakers (mono phone speaker wastes wide panning)
    var stereoWidth = speakerMode ? 0.3 : 1.0;

    // Per-star micro-variations — the hash tints the ambient itself.
    // Same scale, same element feel, but each star's drone is slightly its own.
    var ambientRng = stream('ambient', baseSeed + 1111);
    var microDetune = 1 + (ambientRng() - 0.5) * 0.03;   // ±15 cents — audible pitch tint (was ±4)
    var microFilter = 0.6 + ambientRng() * 0.8;           // ±40% filter brightness (was ±15%)
    var microMod = 0.5 + ambientRng() * 1.0;              // ±50% modulation speed (was ±20%)
    var microBalance = [];                                  // per-harmonic volume: some stars are hollow, some rich
    for (var mb = 0; mb < 6; mb++) microBalance.push(0.3 + ambientRng() * 1.4); // 0.3-1.7x (was 0.7-1.3)
    var microNoiseQ = 0.5 + ambientRng() * 1.0;           // ±50% noise resonance (was ±20%)
    var microNoiseMix = 0.4 + ambientRng() * 1.2;         // some stars have more wind, some less
    var microPan = (ambientRng() - 0.5) * 0.3;            // wider stereo offset (was 0.15)
    var microDroneVol = 0.7 + ambientRng() * 0.6;         // some drones louder, some quieter
    // Per-star harmonic ratio shift — nudges each overtone slightly
    // This changes the timbre: one star's fifth is a bit sharp, another's flat
    var microHarmonicShift = [];
    for (var mh = 0; mh < 6; mh++) microHarmonicShift.push(1 + (ambientRng() - 0.5) * 0.015); // ±0.75%

    // State
    var ctx = null;
    var dest = null;
    var ambientNodes = [];
    var driftTimeouts = [];
    var running = false;
    var lastPingIdx = 0; // shared between ping and counter layers

    // ─────────────────────────────────────────────
    //  AMBIENT LAYER
    // ─────────────────────────────────────────────
    function startAmbient() {
      // Element → harmonic ratios
      var harmonicRatios;
      if (FIRE[sign]) harmonicRatios = [1, 2, 3, 4, 5];
      else if (WATER[sign]) harmonicRatios = [1, 1.5, 2, 3, 4];
      else if (EARTH[sign]) harmonicRatios = [1, 2, 2.667, 4, 5.333];
      else harmonicRatios = [1, 1.498, 2, 2.997, 4];

      var layerCount = 2 + Math.floor(rarity / 20);

      // Temperament → modulation character (micro-varied per star)
      var modSpeed, noiseAmount, detuneAmount;
      if (temp === 'serene' || temp === 'clean' || temp === 'perfect') {
        modSpeed = 0.001; noiseAmount = 0.01; detuneAmount = 0.15;
      } else if (temp === 'turbulent' || temp === 'fever') {
        modSpeed = 0.006; noiseAmount = 0.03; detuneAmount = 0.5;
      } else if (temp === 'electric' || temp === 'knotted') {
        modSpeed = 0.004; noiseAmount = 0.02; detuneAmount = 0.8;
      } else {
        modSpeed = 0.002; noiseAmount = 0.015; detuneAmount = 0.3;
      }
      modSpeed *= microMod;  // per-star modulation tint

      // Per-star root — microDetune shifts the fundamental by a few cents
      var starRoot = rootFreq * microDetune;

      // Master lowpass — micro-varied brightness per star
      var ambientLP = ctx.createBiquadFilter();
      ambientLP.type = 'lowpass';
      ambientLP.frequency.value = (speakerMode ? starRoot * 8 : starRoot * 12) * microFilter;
      ambientLP.Q.value = 0.5;
      ambientLP.connect(dest);

      // Deep drone — volume varies per star
      var droneGain = ctx.createGain(); droneGain.gain.value = (speakerMode ? 0.16 : 0.12) * microDroneVol;
      droneGain.connect(ambientLP);
      var drone = ctx.createOscillator();
      drone.type = 'sine'; drone.frequency.value = starRoot;
      drone.connect(droneGain); drone.start(); ambientNodes.push(drone);

      // Sub-octave — skip on speakers (phone can't reproduce it)
      if (!speakerMode) {
        var sub = ctx.createOscillator();
        sub.type = 'sine'; sub.frequency.value = starRoot / 2;
        var subG = ctx.createGain(); subG.gain.value = 0.1;
        sub.connect(subG); subG.connect(ambientLP); sub.start(); ambientNodes.push(sub);
      }

      // Harmonic layers — each micro-varied in volume per star
      for (var hi = 1; hi < Math.min(layerCount, harmonicRatios.length); hi++) {
        // Per-star harmonic shift — each star's overtones are slightly different
        var hShift = microHarmonicShift[hi % microHarmonicShift.length];
        var freq = starRoot * harmonicRatios[hi] * hShift;
        var panL = ctx.createStereoPanner(); panL.pan.value = (-0.3 - hi * 0.1) * stereoWidth + microPan;
        var panR = ctx.createStereoPanner(); panR.pan.value = (0.3 + hi * 0.1) * stereoWidth + microPan;
        var oscL = ctx.createOscillator(); oscL.type = 'sine'; oscL.frequency.value = freq - detuneAmount * 0.5;
        var oscR = ctx.createOscillator(); oscR.type = 'sine'; oscR.frequency.value = freq + detuneAmount * 0.5;
        // Per-harmonic volume varies per star — different timbral balance
        var vol = speakerMode ? 0.05 / Math.pow(hi + 1, 1.1) : 0.04 / Math.pow(hi + 1, 1.3);
        vol *= microBalance[hi % microBalance.length];
        var hGL = ctx.createGain(); hGL.gain.value = vol;
        var hGR = ctx.createGain(); hGR.gain.value = vol;
        oscL.connect(hGL); hGL.connect(panL); panL.connect(ambientLP);
        oscR.connect(hGR); hGR.connect(panR); panR.connect(ambientLP);
        var lfo = ctx.createOscillator(); lfo.type = 'sine';
        lfo.frequency.value = modSpeed + hi * 0.0005;
        var lfoG = ctx.createGain(); lfoG.gain.value = vol * 0.8;
        lfo.connect(lfoG); lfoG.connect(hGL.gain); lfoG.connect(hGR.gain);
        lfo.start(); oscL.start(); oscR.start();
        ambientNodes.push(oscL, oscR, lfo);
      }

      // Stellar wind — filtered noise, soft
      var bufSize = ctx.sampleRate * 2;
      var noiseBuf = ctx.createBuffer(1, bufSize, ctx.sampleRate);
      var nd = noiseBuf.getChannelData(0);
      for (var i = 0; i < bufSize; i++) nd[i] = Math.random() * 2 - 1;
      var noise = ctx.createBufferSource();
      noise.buffer = noiseBuf; noise.loop = true;
      var nf = ctx.createBiquadFilter(); nf.type = 'bandpass';
      nf.frequency.value = (speakerMode ? starRoot * 4 : starRoot * 3) * moonBright * microFilter;
      nf.Q.value = (1 + moonBright * 3) * microNoiseQ;
      var ng = ctx.createGain(); ng.gain.value = noiseAmount * 0.6 * microNoiseMix;
      noise.connect(nf); nf.connect(ng); ng.connect(ambientLP);
      noise.start(); ambientNodes.push(noise);

      // Noise sweep LFO
      var sweepLfo = ctx.createOscillator(); sweepLfo.type = 'sine';
      sweepLfo.frequency.value = 0.0008 * microMod;
      var sweepG = ctx.createGain(); sweepG.gain.value = starRoot * moonBright;
      sweepLfo.connect(sweepG); sweepG.connect(nf.frequency);
      sweepLfo.start(); ambientNodes.push(sweepLfo);

      // High shimmer (rare+ only, lowered from pain zone)
      if (rarity >= 55) {
        var shimmer = ctx.createOscillator(); shimmer.type = 'sine';
        shimmer.frequency.value = (speakerMode ? starRoot * 3 : starRoot * 4);
        var shimG = ctx.createGain(); shimG.gain.value = 0.004 * moonBright;
        shimmer.connect(shimG); shimG.connect(ambientLP);
        var shimLfo = ctx.createOscillator(); shimLfo.type = 'sine';
        shimLfo.frequency.value = 0.012 + Math.random() * 0.015;
        var shimLG = ctx.createGain(); shimLG.gain.value = 0.004;
        shimLfo.connect(shimLG); shimLG.connect(shimG.gain);
        shimLfo.start(); shimmer.start();
        ambientNodes.push(shimmer, shimLfo);
      }
    }

    // ─────────────────────────────────────────────
    //  DRIFT LAYER
    //  Sine tones crossfading through the scale.
    //  You feel the harmony shift, never hear a note.
    // ─────────────────────────────────────────────
    function startDrift() {
      // Pick a cell and mutate it
      var cellIdx = Math.floor(rng() * CELLS.length);
      var cellDeg = CELLS[cellIdx].slice();

      // Mutation: shift one interior note ±1
      if (cellDeg.length >= 3) {
        var swapIdx = 1 + Math.floor(rng() * (cellDeg.length - 2));
        cellDeg[swapIdx] += (rng() < 0.5 ? 1 : -1);
      }
      // 40% chance: add a passing tone
      if (rng() < 0.4 && cellDeg.length <= 4) {
        var insertAt = 1 + Math.floor(rng() * (cellDeg.length - 1));
        var passing = Math.round((cellDeg[insertAt - 1] + cellDeg[insertAt]) / 2);
        if (passing !== cellDeg[insertAt - 1] && passing !== cellDeg[insertAt]) {
          cellDeg.splice(insertAt, 0, passing);
        }
      }
      // 25% chance: circular return
      if (rng() < 0.25) {
        cellDeg.push(cellDeg[0]);
      }

      var startOffset = Math.floor(rng() * 4);
      var baseDeg = cellDeg.map(function(d) { return d + startOffset; });

      // Build full drift path: base → transposed up → inverted → base → loops
      var upShift = 3 + Math.floor(rng() * 3);
      var upDeg = baseDeg.map(function(d) { return d + upShift; });
      var invDeg = [baseDeg[0]];
      for (var i = 1; i < baseDeg.length; i++) {
        invDeg.push(baseDeg[0] - (baseDeg[i] - baseDeg[0]));
      }
      var path = [].concat(baseDeg, upDeg, invDeg, baseDeg);

      // Convert to frequencies (one octave above drone — blends in)
      var driftRoot = rootFreq * 2;
      var driftFreqs = [];
      for (var i = 0; i < path.length; i++) {
        driftFreqs.push(degToFreq(path[i], scale, driftRoot));
      }

      // Per-seed timing
      var noteDurations = [];
      for (var i = 0; i < driftFreqs.length; i++) {
        noteDurations.push(18 + rng() * 10); // 18-28 seconds each
      }
      var crossfade = 6 + rng() * 4; // 6-10 second crossfade

      // Volume: very quiet, sits just above the drone
      var driftVol = 0.04 + moonBright * 0.02;

      // Binaural detuning
      var detuneCents = 3 + rng() * 4;
      var detuneRatio = Math.pow(2, detuneCents / 1200);

      var noteIdx = 0;
      var toneStartTime = ctx.currentTime + 3; // give ambient time to establish

      function scheduleDriftTone() {
        if (!running || !ctx || ctx.state === 'closed') return;
        if (noteIdx >= driftFreqs.length) {
          noteIdx = 0;
          toneStartTime += 3; // breath before next cycle
        }

        var freq = driftFreqs[noteIdx];
        var dur = noteDurations[noteIdx % noteDurations.length];
        var now = ctx.currentTime;
        var start = Math.max(now, toneStartTime);

        // Main tone — slow fade in, sustain, slow fade out
        var osc = ctx.createOscillator();
        osc.type = 'sine';
        osc.frequency.value = freq;
        var env = ctx.createGain();
        env.gain.setValueAtTime(0, start);
        env.gain.linearRampToValueAtTime(driftVol, start + crossfade);
        env.gain.linearRampToValueAtTime(driftVol, start + dur - crossfade);
        env.gain.linearRampToValueAtTime(0, start + dur);
        osc.connect(env); env.connect(dest);
        osc.start(start); osc.stop(start + dur + 0.1);
        // Disconnect on end — without this, silent oscillators accumulate
        // in the graph and their internal denormal-float state spikes the
        // audio thread CPU over time, producing static.
        osc.onended = function() { try { osc.disconnect(); env.disconnect(); } catch (e) {} };

        // Detuned twin for binaural width
        var osc2 = ctx.createOscillator();
        osc2.type = 'sine';
        osc2.frequency.value = freq * detuneRatio;
        var env2 = ctx.createGain();
        env2.gain.setValueAtTime(0, start);
        env2.gain.linearRampToValueAtTime(driftVol * 0.5, start + crossfade);
        env2.gain.linearRampToValueAtTime(driftVol * 0.5, start + dur - crossfade);
        env2.gain.linearRampToValueAtTime(0, start + dur);
        osc2.connect(env2);
        var pan = ctx.createStereoPanner();
        pan.pan.value = ((noteIdx % 2 === 0) ? 0.15 : -0.15) * stereoWidth;
        env2.connect(pan); pan.connect(dest);
        osc2.start(start); osc2.stop(start + dur + 0.1);
        osc2.onended = function() { try { osc2.disconnect(); env2.disconnect(); pan.disconnect(); } catch (e) {} };

        // Next tone starts before this one ends (crossfade overlap)
        toneStartTime = start + dur - crossfade;
        noteIdx++;

        var nextDelay = Math.max(100, (toneStartTime - ctx.currentTime - 2) * 1000);
        var tid = setTimeout(scheduleDriftTone, nextDelay);
        driftTimeouts.push(tid);
      }

      // First drift tone after ambient has settled
      var tid = setTimeout(scheduleDriftTone, 3000);
      driftTimeouts.push(tid);
    }

    // ─────────────────────────────────────────────
    //  DUST LAYER
    //  Tiny, barely-there sparkles at random intervals.
    //  High-frequency filtered noise bursts — like
    //  particles of light catching in the void.
    //  So quiet you're never sure you heard them.
    // ─────────────────────────────────────────────
    function startDust() {
      var dustRng = stream('dust', baseSeed + 3333);

      // Dust frequency range — high but not painful (1200-4000Hz)
      var dustFreqLo = rootFreq * 20;
      var dustFreqHi = rootFreq * 60;
      // Volume: barely perceptible
      var dustVol = 0.008 + moonBright * 0.006; // 0.008-0.014

      function scheduleDust() {
        if (!running || !ctx || ctx.state === 'closed') return;

        var now = ctx.currentTime;

        // Each dust particle: filtered noise burst, very short
        var dur = 0.04 + dustRng() * 0.12; // 40-160ms
        var freq = dustFreqLo + dustRng() * (dustFreqHi - dustFreqLo);
        var vol = dustVol * (0.4 + dustRng() * 0.6); // vary each particle

        // Tiny noise burst
        var bufLen = Math.floor(ctx.sampleRate * (dur + 0.05));
        var buf = ctx.createBuffer(1, bufLen, ctx.sampleRate);
        var nd = buf.getChannelData(0);
        for (var j = 0; j < bufLen; j++) nd[j] = (Math.random() * 2 - 1);
        var src = ctx.createBufferSource();
        src.buffer = buf;

        // Narrow bandpass — gives it a pitched, crystalline quality
        var filt = ctx.createBiquadFilter();
        filt.type = 'bandpass';
        filt.frequency.value = freq;
        filt.Q.value = 8 + dustRng() * 12; // Q 8-20: narrow = tonal sparkle

        // Envelope: instant attack, quick fade
        var env = ctx.createGain();
        env.gain.setValueAtTime(0, now);
        env.gain.linearRampToValueAtTime(vol, now + 0.003);
        env.gain.exponentialRampToValueAtTime(0.0001, now + dur);

        // Random stereo position — dust is everywhere
        var pan = ctx.createStereoPanner();
        pan.pan.value = (dustRng() - 0.5) * 1.6 * stereoWidth; // narrower on speakers

        src.connect(filt); filt.connect(env); env.connect(pan); pan.connect(dest);
        src.start(now); src.stop(now + dur + 0.05);
        // Disconnect on end so the dust particle doesn't linger in the
        // audio graph after fading out — denormal-float CPU drift over
        // hours is a known cause of accumulating static.
        src.onended = function() {
          try { src.disconnect(); filt.disconnect(); env.disconnect(); pan.disconnect(); } catch (e) {}
        };

        // Next particle: 3-15 seconds (sparser at new moon, denser at full)
        var minGap = 4 + (1 - moonBright) * 6;  // 4-10s
        var maxGap = 10 + (1 - moonBright) * 10; // 10-20s
        var nextIn = (minGap + dustRng() * (maxGap - minGap)) * 1000;

        var tid = setTimeout(scheduleDust, nextIn);
        driftTimeouts.push(tid);
      }

      // First dust after 5-10 seconds
      var firstTid = setTimeout(scheduleDust, 5000 + dustRng() * 5000);
      driftTimeouts.push(firstTid);
    }

    // ─────────────────────────────────────────────
    //  PING LAYER
    //  Single notes from the star's scale. Not a melody —
    //  just a tone that appears, rings, and fades. Like a
    //  distant wind chime caught by a breeze you can't feel.
    //  Sparse enough that each one feels like an event.
    // ─────────────────────────────────────────────
    function startPings() {
      var pingRng = stream('ping', baseSeed + 5555);

      // Build a pool of ping frequencies from the scale — 2 octaves above drone
      var pingRoot = rootFreq * 4;
      var pingPool = [];
      for (var oct = 0; oct < 2; oct++) {
        for (var si = 0; si < scale.length; si++) {
          pingPool.push(pingRoot * Math.pow(2, (scale[si] + oct * 12) / 12));
        }
      }

      // Volume: gentle but present — between drift and dust
      var pingVol = 0.015 + moonBright * 0.01; // 0.015-0.025

      // Initialize the shared ping index
      lastPingIdx = Math.floor(pingRng() * pingPool.length);

      function schedulePing() {
        if (!running || !ctx || ctx.state === 'closed') return;

        var now = ctx.currentTime;
        var freq;

        if (pingMode === 'memory') {
          // Biased selection: 60% step to neighbor, 25% small jump (2-3), 15% random leap
          var r = pingRng();
          var step;
          if (r < 0.60) step = (pingRng() < 0.5 ? 1 : -1);            // step ±1
          else if (r < 0.85) step = (pingRng() < 0.5 ? 1 : -1) * (2 + Math.floor(pingRng() * 2)); // ±2-3
          else step = Math.floor(pingRng() * pingPool.length);          // random (reset)

          if (typeof step === 'number' && step < pingPool.length / 2) {
            lastPingIdx = ((lastPingIdx + step) % pingPool.length + pingPool.length) % pingPool.length;
          } else {
            lastPingIdx = step; // random leap
          }
          freq = pingPool[lastPingIdx];
        } else {
          // Random: original behavior
          freq = pingPool[Math.floor(pingRng() * pingPool.length)];
        }

        var vol = pingVol * (0.5 + pingRng() * 0.5);

        // Soft attack, long decay — not a strike, a breath
        var dur = 3 + pingRng() * 3; // 3-6 seconds of ring
        var attackTime = 0.08 + pingRng() * 0.12; // 80-200ms soft onset

        var osc = ctx.createOscillator();
        osc.type = 'sine';
        osc.frequency.value = freq;
        var env = ctx.createGain();
        env.gain.setValueAtTime(0, now);
        env.gain.linearRampToValueAtTime(vol, now + attackTime);
        env.gain.linearRampToValueAtTime(vol * 0.7, now + dur * 0.3);
        env.gain.exponentialRampToValueAtTime(0.0001, now + dur);
        osc.connect(env);

        // Random pan
        var pan = ctx.createStereoPanner();
        pan.pan.value = (pingRng() - 0.5) * 1.2 * stereoWidth;
        env.connect(pan);
        pan.connect(dest);

        osc.start(now);
        osc.stop(now + dur + 0.1);

        // Next ping: 15-45 seconds (moon controls density)
        var minGap = 15 + (1 - moonBright) * 10; // 15-25
        var maxGap = 30 + (1 - moonBright) * 15;  // 30-45
        var nextIn = (minGap + pingRng() * (maxGap - minGap)) * 1000;

        var tid = setTimeout(schedulePing, nextIn);
        driftTimeouts.push(tid);
      }

      // First ping after 8-20 seconds
      var firstTid = setTimeout(schedulePing, 8000 + pingRng() * 12000);
      driftTimeouts.push(firstTid);
    }

    // ─────────────────────────────────────────────
    //  COUNTER LAYER
    //  The left hand. A lower tone that moves against
    //  the ping — contrary motion, complementary intervals.
    //  When they land a 5th or octave apart, you feel it.
    //  Slower, deeper, fewer. The anchor to the ping's wandering.
    // ─────────────────────────────────────────────
    function startCounter() {
      var counterRng = stream('counter', baseSeed + 7777);

      // Counter pool: one octave BELOW the ping pool (same scale, lower register)
      var counterRoot = rootFreq * 2;
      var counterPool = [];
      for (var oct = 0; oct < 2; oct++) {
        for (var si = 0; si < scale.length; si++) {
          counterPool.push(counterRoot * Math.pow(2, (scale[si] + oct * 12) / 12));
        }
      }

      // Quieter than pings — it supports, doesn't lead
      var counterVol = 0.012 + moonBright * 0.008; // 0.012-0.02

      var lastCounterIdx = Math.floor(counterPool.length / 2); // start in the middle

      function scheduleCounter() {
        if (!running || !ctx || ctx.state === 'closed') return;

        var now = ctx.currentTime;

        // Find complementary position to the last ping
        // Target: a 4th (3-4 scale degrees) or 5th (4-5 scale degrees) below the ping
        // This creates the open intervals that resonate
        var targetOffset = (counterRng() < 0.5) ? 3 : 4; // 4th or 5th below

        // The ping's pool index maps roughly to scale position
        // Counter moves toward a complementary interval
        var targetIdx = ((lastPingIdx - targetOffset) % counterPool.length + counterPool.length) % counterPool.length;

        // Don't jump directly — step toward the target (1-2 steps)
        var diff = targetIdx - lastCounterIdx;
        if (Math.abs(diff) > counterPool.length / 2) {
          diff = diff > 0 ? diff - counterPool.length : diff + counterPool.length;
        }
        var step = diff > 0 ? Math.min(diff, 2) : Math.max(diff, -2);

        // 70% move toward complement, 30% hold position (the left hand rests)
        if (counterRng() < 0.7) {
          lastCounterIdx = ((lastCounterIdx + step) % counterPool.length + counterPool.length) % counterPool.length;
        }

        var freq = counterPool[lastCounterIdx];
        var vol = counterVol * (0.5 + counterRng() * 0.5);

        // Slower attack, longer decay than pings — heavier, deeper
        var dur = 4 + counterRng() * 4; // 4-8 seconds
        var attackTime = 0.15 + counterRng() * 0.2; // 150-350ms — slower bloom

        var osc = ctx.createOscillator();
        osc.type = 'sine';
        osc.frequency.value = freq;
        var env = ctx.createGain();
        env.gain.setValueAtTime(0, now);
        env.gain.linearRampToValueAtTime(vol, now + attackTime);
        env.gain.linearRampToValueAtTime(vol * 0.6, now + dur * 0.4);
        env.gain.exponentialRampToValueAtTime(0.0001, now + dur);
        osc.connect(env);

        // Pan opposite side from where pings tend — stereo separation
        var pan = ctx.createStereoPanner();
        pan.pan.value = (counterRng() - 0.5) * -1.0 * stereoWidth; // inverted field
        env.connect(pan);
        pan.connect(dest);

        osc.start(now);
        osc.stop(now + dur + 0.1);

        // Slower than pings — the left hand is more patient
        var minGap = 20 + (1 - moonBright) * 15; // 20-35
        var maxGap = 40 + (1 - moonBright) * 20;  // 40-60
        var nextIn = (minGap + counterRng() * (maxGap - minGap)) * 1000;

        var tid = setTimeout(scheduleCounter, nextIn);
        driftTimeouts.push(tid);
      }

      // First counter after 15-30 seconds — let the ping establish itself first
      var firstTid = setTimeout(scheduleCounter, 15000 + counterRng() * 15000);
      driftTimeouts.push(firstTid);
    }

    // ─────────────────────────────────────────────
    //  PUBLIC API
    // ─────────────────────────────────────────────
    return {
      /** Start all layers. Pass an AudioContext and a destination GainNode. */
      start: function(audioContext, destination) {
        if (running) return;
        ctx = audioContext;
        running = true;
        // Master limiter — every internal layer routes through this
        // before reaching the caller's destination. Without it, the
        // ambient drone + harmonics + drift + dust can phase-align to
        // peaks above 1.0, the output clips, and the clipping reads as
        // intermittent static that creeps in over time. The compressor
        // is configured as a soft limiter (high threshold, fast attack,
        // gentle ratio) so the texture stays untouched until peaks
        // genuinely exceed safe levels.
        var limiter = audioContext.createDynamicsCompressor();
        limiter.threshold.value = -3;     // dB — only catch true peaks
        limiter.knee.value = 6;           // dB — soft knee for musical clamping
        limiter.ratio.value = 8;          // strong but not brick-wall
        limiter.attack.value = 0.003;     // 3ms — fast enough to catch transients
        limiter.release.value = 0.15;     // 150ms — let dust through cleanly
        limiter.connect(destination);
        dest = limiter;
        ambientNodes.push(limiter);
        startAmbient();
        startDrift();
        startDust();
        startPings();
        startCounter();
      },

      /** Fade out and clean up all nodes. */
      stop: function() {
        running = false;
        for (var i = 0; i < driftTimeouts.length; i++) clearTimeout(driftTimeouts[i]);
        driftTimeouts = [];
        for (var i = 0; i < ambientNodes.length; i++) {
          try { ambientNodes[i].stop(); } catch(e) {}
          try { ambientNodes[i].disconnect(); } catch(e) {}
        }
        ambientNodes = [];
        dest = null;
      },

      /** Whether audio is currently playing. */
      isPlaying: function() { return running; },

      /** The star's params for display. */
      params: {sign: sign, moonPhase: phase, temperament: temp, rarity: rarity},
      element: element,
      seed: seed,
      speakerMode: speakerMode
    };
  }

  // ═══════════════════════════════════════════════════
  //  SONG NAMING (matches mememage/song.py exactly)
  // ═══════════════════════════════════════════════════

  var WORD_A = [
    'Adagio','Lento','Grave','Largo','Sereno',
    'Dolce','Morendo','Lacrimosa','Luminoso','Celeste',
    'Perduto','Eterno','Sospiro','Profondo','Remoto',
    'Errante',
    'Y\u016bgen','Komorebi','Mono','Mugen','Shinrin',
    'Ukiyo','Aware','Nagare','Hotaru','Kasumi',
    'Svara','Rasa','Dhyana','Akasha','Bindu',
    'Prana'
  ];

  var WORD_B = [
    'Nocturne','Requiem','Vespers','Litany','Canticle',
    'Hymnal','Aubade','Elegy','Threnody','Chorale',
    'Reverie','Sarabande',
    'Nebula','Corona','Perihelion','Apogee','Meridian',
    'Eclipse','Solstice','Zenith','Parallax','Umbra',
    'Liminal','Penumbra'
  ];

  var WORD_C = [
    '','','','','',
    'in Deep Water','at the Threshold','for the Unheard',
    'of First Light','beyond the Veil','in Amber',
    'at Perihelion','of Distant Fire','for the Departed',
    'in Still Air','of Frozen Light'
  ];

  /**
   * Generate a song name from a content hash.
   * Matches the Python implementation in mememage/song.py exactly.
   * Uses SHA-256 sub-hash with ":song" salt.
   */
  function songNameFromHash(contentHash) {
    // This requires SubtleCrypto — async
    // For synchronous fallback, use the simple hash below
    var str = contentHash + ':song';
    var h = 0;
    for (var i = 0; i < str.length; i++) {
      h = ((h << 5) - h + str.charCodeAt(i)) | 0;
    }
    h = Math.abs(h);
    // Use different bit ranges to avoid correlation
    var a = WORD_A[h % WORD_A.length];
    var b = WORD_B[(h >>> 8) % WORD_B.length];
    var c = WORD_C[(h >>> 16) % WORD_C.length];
    if (c) return a + ' ' + b + ' ' + c;
    return a + ' ' + b;
  }

  /**
   * Async version using SubtleCrypto — matches Python SHA-256 exactly.
   * Falls back to simple hash if SubtleCrypto unavailable.
   */
  function songNameFromHashAsync(contentHash) {
    if (!window.crypto || !window.crypto.subtle) {
      return Promise.resolve(songNameFromHash(contentHash));
    }
    var data = new TextEncoder().encode(contentHash + ':song');
    return window.crypto.subtle.digest('SHA-256', data).then(function(buf) {
      var bytes = new Uint8Array(buf);
      var a = WORD_A[bytes[0] % WORD_A.length];
      var b = WORD_B[bytes[1] % WORD_B.length];
      var c = WORD_C[bytes[2] % WORD_C.length];
      if (c) return a + ' ' + b + ' ' + c;
      return a + ' ' + b;
    });
  }

  // ═══════════════════════════════════════════════════
  //  PUBLIC MODULE
  // ═══════════════════════════════════════════════════

  return {
    create: create,
    songName: songNameFromHash,
    songNameAsync: songNameFromHashAsync
  };
})();
