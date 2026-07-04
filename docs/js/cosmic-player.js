/**
 * cosmic-player.js — Audio player widget for the Mememage certificate.
 *
 * Injected as the last child of the .plate div. Sticky at the bottom
 * of the viewport while scrolling, settles into the plate's bottom
 * edge when fully scrolled. The plate's clip-path handles corner rounding.
 *
 * Usage (from cert-renderer.js):
 *   CosmicPlayer.inject(plateElement, metaRecord);
 *
 * Depends on: cosmic-audio.js (CosmicAudio), cert-renderer.js (getRarityTier)
 */

var CosmicPlayer = (function() {
  'use strict';

  var FADE_IN = 2.5;
  var FADE_OUT = 1.2;

  var RARITY_COLORS = {
    common: [96,96,96], uncommon: [42,112,48], rare: [42,80,144],
    veryrare: [90,42,138], epic: [138,98,16], legendary: [212,64,64]
  };
  var RARITY_BRIGHT = {
    common: [160,160,160], uncommon: [80,200,95], rare: [80,150,240],
    veryrare: [160,90,235], epic: [230,175,50], legendary: [255,100,100]
  };

  // Full 24-letter Greek alphabet (\u03b1..\u03c9, no final sigma) \u2014 covers
  // constellation_size up to 24. Keep in sync with the other Bayer tables.
  var BAYER = ['\u03b1','\u03b2','\u03b3','\u03b4','\u03b5','\u03b6','\u03b7','\u03b8','\u03b9','\u03ba','\u03bb','\u03bc','\u03bd','\u03be','\u03bf','\u03c0','\u03c1','\u03c3','\u03c4','\u03c5','\u03c6','\u03c7','\u03c8','\u03c9'];

  // ─── Helpers ───

  function mix(rgb, base, pct) {
    return [
      Math.round(base[0] + (rgb[0] - base[0]) * pct),
      Math.round(base[1] + (rgb[1] - base[1]) * pct),
      Math.round(base[2] + (rgb[2] - base[2]) * pct)
    ];
  }

  function el(tag, cls) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    return e;
  }

  // V1 souls store birth.sun as {sign:int,deg:float} and
  // birth.moon_phase as {phase:int,illum:float}. Legacy pre-V1 records
  // still carry the old "Aries 24.3°" / "Full Moon (98.4%)" strings —
  // the shared signName / moonPhaseName helpers in data.js handle
  // both shapes, so these wrappers just delegate.
  function parseSunSign(sun) {
    if (!sun) return 'Aries';
    return (typeof signName === 'function') ? signName(sun)
      : (typeof sun === 'string' ? (sun.split(' ')[0] || 'Aries') : 'Aries');
  }

  function cleanMoonPhase(phase) {
    if (!phase) return 'Full Moon';
    return (typeof moonPhaseName === 'function') ? moonPhaseName(phase)
      : (typeof phase === 'string' ? (phase.split('(')[0].trim() || 'Full Moon') : 'Full Moon');
  }

  function parseTemperament(tempStr) {
    if (!tempStr) return 'serene';
    // "A reckless birth" → "reckless"
    var m = tempStr.match(/^A\s+(.+?)\s+birth$/i);
    return m ? m[1] : tempStr;
  }

  function rarityKey(tierName) {
    return tierName.toLowerCase().replace(/\s/g, '');
  }

  // ─── DOM Builder ───

  function buildDOM(starName, songName, subtitle, rKey) {
    var player = el('div', 'cosmic-player');
    player.dataset.rarity = rKey;

    var accent = el('div', 'player-accent');
    var inner = el('div', 'player-inner');
    var canvas = el('canvas', 'player-eq');
    var controls = el('div', 'player-controls');

    // Minimize/expand toggle — drawer-pull style at top-center.
    // Default chevron points down (V) to "send to minimal"; CSS
    // rotates it 180° (^) when player carries .minimal so the same
    // button signals "expand back up".
    var toggle = el('button', 'player-toggle');
    toggle.setAttribute('aria-label', 'Collapse player');
    toggle.innerHTML = '<svg width="14" height="7" viewBox="0 0 14 7" aria-hidden="true">'
      + '<path d="M1 1 L7 5.5 L13 1" fill="none" stroke="currentColor" '
      + 'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    toggle.addEventListener('click', function(e) {
      e.stopPropagation();
      // Lock during the two-phase animation so rapid clicks don't
      // queue a tangle of half-fired sequences.
      if (toggle._animating) return;
      toggle._animating = true;

      var willBeMinimal = !player.classList.contains('minimal');

      // Phase 1: flip the chevron now. CSS transition plays a clean
      // rotation before any panel layout starts.
      toggle.classList.toggle('flipped', willBeMinimal);

      // Phase 2: after the chevron rotation settles, kick off the
      // panel collapse/expand. Wait derived from CSS custom property
      // --player-toggle-phase-ms (defaults via root style; covers
      // chevron transition + small breathing buffer) so JS and CSS
      // can't drift.
      var rootCS = getComputedStyle(document.documentElement);
      var phaseMs = parseInt(rootCS.getPropertyValue('--player-toggle-phase-ms'), 10) || 380;
      setTimeout(function() {
        // Capture cert plate scroll state BEFORE the layout shifts.
        // Pin distance-from-bottom continuously through the cert
        // collapse/expand transition so the viewport tracks the
        // cert tail (browsers preserve scrollTop by default, which
        // would visibly scroll the user away as scroll content
        // changes).
        //
        // Scoped to the player's actual host slot — in planetarium
        // mode the player is a body-level overlay (.in-planetarium)
        // and there's no cert plate to pin. closest() returns null
        // there, so the scroll-pin block is a clean skip.
        var hostSlot = player.closest('.panel-right-has-player');
        var plate = hostSlot ? hostSlot.querySelector('.plate') : null;
        var distFromBottom = null;
        if (plate && plate.scrollHeight > plate.clientHeight) {
          distFromBottom = plate.scrollHeight - plate.scrollTop - plate.clientHeight;
        }

        var isMinimal = player.classList.toggle('minimal');
        toggle.setAttribute('aria-label', isMinimal ? 'Expand player' : 'Collapse player');
        // Surfaces hosting the player (cert plate, planetarium overlay)
        // can listen for this to reposition their own bottom-anchored
        // chrome (hints, fades, etc) so they ride above the player as
        // it collapses/expands.
        player.dispatchEvent(new CustomEvent('cosmic-player-toggle', {
          detail: { minimal: isMinimal }, bubbles: true
        }));

        if (plate && distFromBottom !== null) {
          var deadline = Date.now() + 600;
          var pin = function() {
            plate.scrollTop = Math.max(0, plate.scrollHeight - plate.clientHeight - distFromBottom);
            if (Date.now() < deadline) requestAnimationFrame(pin);
          };
          requestAnimationFrame(pin);
        }

        // Release the animation lock after the panel's own collapse
        // transition settles.
        setTimeout(function() { toggle._animating = false; }, 550);
      }, phaseMs);
    });

    // Play button
    var btn = el('button', 'player-btn');
    btn.id = 'cosmicPlayBtn';
    var playIcon = el('span', 'play-icon');
    playIcon.innerHTML = '&#9655;';
    var stopIcon = el('span', 'stop-icon');
    stopIcon.innerHTML = '&#9646;&#9646;';
    btn.appendChild(playIcon);
    btn.appendChild(stopIcon);

    // Labels
    var labelWrap = el('div', 'player-star-label');
    var nameEl = el('div', 'player-star-name');
    nameEl.textContent = starName;
    var songEl = el('div', 'player-song-name');
    songEl.textContent = songName;
    var subEl = el('div', 'player-star-sub');
    subEl.textContent = subtitle;
    labelWrap.appendChild(nameEl);
    labelWrap.appendChild(songEl);
    labelWrap.appendChild(subEl);

    // COSMIC button
    var vfx = el('button', 'player-vfx');
    vfx.textContent = 'COSMIC';
    vfx.title = 'COSMIC \u2014 deep frequencies, wide stereo. Use headphones.';

    // Volume
    var volWrap = el('div', 'player-volume-wrap');
    var volIcon = el('span', 'player-volume-icon');
    volIcon.innerHTML = '&#9835;';
    var volSlider = el('input', 'player-volume');
    volSlider.type = 'range';
    volSlider.min = '0';
    volSlider.max = '100';
    volSlider.value = '70';
    volWrap.appendChild(volIcon);
    volWrap.appendChild(volSlider);

    controls.appendChild(btn);
    controls.appendChild(labelWrap);
    controls.appendChild(vfx);
    controls.appendChild(volWrap);

    inner.appendChild(canvas);
    inner.appendChild(controls);
    player.appendChild(accent);
    player.appendChild(toggle);
    player.appendChild(inner);

    return {
      player: player, canvas: canvas, btn: btn, vfx: vfx,
      volIcon: volIcon, volSlider: volSlider,
      nameEl: nameEl, songEl: songEl, subEl: subEl
    };
  }

  // ─── Rarity Tinting ───

  function tint(dom, rKey) {
    var rgb = RARITY_COLORS[rKey] || RARITY_COLORS.common;
    var bright = RARITY_BRIGHT[rKey] || RARITY_BRIGHT.common;

    // Button styling handled by CSS — no inline overrides (would kill :hover)
    dom.nameEl.style.color = 'rgb(' + mix(rgb, [208,208,216], 0.25).join(',') + ')';
    dom.songEl.style.color = 'rgb(' + mix(rgb, [176,176,190], 0.25).join(',') + ')';
    dom.subEl.style.color = 'rgb(' + mix(rgb, [138,138,152], 0.25).join(',') + ')';
    dom.volIcon.style.color = 'rgb(' + mix(rgb, [90,90,104], 0.35).join(',') + ')';
    dom.volSlider.style.setProperty('--thumb-color', 'rgb(' + rgb[0] + ',' + rgb[1] + ',' + rgb[2] + ')');
    dom.volSlider.style.background = 'rgba(' + mix(rgb, [255,255,255], 0.2).join(',') + ',0.12)';
    dom.player.style.setProperty('--rarity-bright', 'rgb(' + bright[0] + ',' + bright[1] + ',' + bright[2] + ')');
    dom.player.style.setProperty('--rarity-glow-bright', 'rgba(' + bright[0] + ',' + bright[1] + ',' + bright[2] + ',0.3)');
  }

  // ─── Play Ring (bright rarity border on play button) ───

  function applyPlayRing(dom, rKey, on) {
    var bright = RARITY_BRIGHT[rKey] || RARITY_BRIGHT.common;
    var r = bright[0], g = bright[1], b = bright[2];
    if (on) {
      dom.btn.style.borderColor = 'rgb(' + r + ',' + g + ',' + b + ')';
      dom.btn.style.boxShadow = '0 1px 4px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.06), 0 0 12px rgba(' + r + ',' + g + ',' + b + ',0.35), 0 0 24px rgba(' + r + ',' + g + ',' + b + ',0.15)';
    } else {
      dom.btn.style.borderColor = '';
      dom.btn.style.boxShadow = '';
      dom.btn.style.color = '';
    }
  }

  // ─── EQ Visualization ───

  function startEq(canvas, state) {
    var dpr = window.devicePixelRatio || 1;
    var barCount = 128;
    var idlePhase = 0;
    var frameSkip = 0;

    function resize() {
      var rect = canvas.parentElement.getBoundingClientRect();
      if (rect.width > 0) {
        canvas.width = rect.width * dpr;
        canvas.height = 40 * dpr;
        canvas.style.width = rect.width + 'px';
        canvas.style.height = '40px';
      }
    }
    resize();
    window.addEventListener('resize', resize);

    var ctx = canvas.getContext('2d');

    function draw() {
      requestAnimationFrame(draw);

      // Throttle: when idle (not playing), draw every 3rd frame
      if (!state.playing) {
        frameSkip++;
        if (frameSkip % 3 !== 0) return;
      }

      var w = canvas.width;
      var h = canvas.height;
      if (w === 0) return;
      ctx.clearRect(0, 0, w, h);

      var rgb = RARITY_BRIGHT[state.rKey] || RARITY_BRIGHT.common;

      var halfCount = Math.floor(barCount / 2);
      var barW = w / barCount;
      var gap = Math.max(1, barW * 0.2);
      var barNet = barW - gap;
      var centerX = w / 2;

      // Build bar values — blend real FFT with synthetic spread
      var vals = new Array(halfCount);
      var hasAudio = state.analyser && state.playing;
      var avgEnergy = 0;

      if (hasAudio) {
        var freqData = new Uint8Array(state.analyser.frequencyBinCount);
        state.analyser.getByteFrequencyData(freqData);

        // Smear FFT into fewer "energy bands" — each band covers a wider frequency range
        // This stretches real audio content across more visual bars
        var bandCount = 12;
        var bands = new Array(bandCount);
        for (var b = 0; b < bandCount; b++) {
          var bStart = Math.floor(Math.pow(b / bandCount, 1.3) * freqData.length * 0.7);
          var bEnd = Math.floor(Math.pow((b + 1) / bandCount, 1.3) * freqData.length * 0.7);
          var sum = 0;
          for (var k = bStart; k < bEnd; k++) sum += freqData[k];
          bands[b] = sum / Math.max(1, bEnd - bStart) / 255;
        }

        // Overall energy for the synthetic ripple
        avgEnergy = (bands[0] + bands[1] + bands[2]) / 3;

        for (var i = 0; i < halfCount; i++) {
          var t = i / halfCount;
          // Map bar position to a band with interpolation — stretches real data wide
          var bandPos = t * (bandCount - 1);
          var bLow = Math.floor(bandPos);
          var bHigh = Math.min(bLow + 1, bandCount - 1);
          var bFrac = bandPos - bLow;
          var real = bands[bLow] * (1 - bFrac) + bands[bHigh] * bFrac;

          // Synthetic spread: energy ripples inward from edges
          var spread = avgEnergy * (1.0 - t * 0.5) * (0.5 + 0.5 * Math.sin(idlePhase * 1.2 + i * 0.25));

          // Blend: real FFT amplified, synthetic fills gaps
          var blend = real * 1.8 + spread * 0.6;
          blend = Math.max(blend, spread * (0.3 + t * 0.8));
          vals[i] = Math.min(blend, 1.0);
        }
      } else {
        // Idle breathing
        for (var i = 0; i < halfCount; i++) {
          var t = i / halfCount;
          var wave = Math.sin(idlePhase * 0.8 + i * 0.12) * Math.sin(idlePhase * 0.5 + i * 0.08);
          vals[i] = (0.05 + 0.035 * wave) * (1.0 - t * 0.5);
        }
      }

      for (var i = 0; i < halfCount; i++) {
        var t = i / halfCount;
        var val = vals[i];

        var barH = Math.max(1, val * h * 0.8);
        var y = h - barH;
        var xR = centerX + (i * barW) + gap / 2;
        var xL = centerX - ((i + 1) * barW) + gap / 2;
        var edgeFade = 1.0 - t * 0.25;

        for (var side = 0; side < 2; side++) {
          var x = side === 0 ? xR : xL;
          var grad = ctx.createLinearGradient(x, h, x, y);
          grad.addColorStop(0, 'rgba(' + rgb[0] + ',' + rgb[1] + ',' + rgb[2] + ',' + (0.65 * edgeFade) + ')');
          grad.addColorStop(0.4, 'rgba(' + rgb[0] + ',' + rgb[1] + ',' + rgb[2] + ',' + (0.3 * edgeFade) + ')');
          grad.addColorStop(1, 'rgba(' + rgb[0] + ',' + rgb[1] + ',' + rgb[2] + ',' + (0.06 * edgeFade) + ')');
          ctx.fillStyle = grad;
          var r2 = Math.min(barNet / 2, 1.5);
          ctx.beginPath();
          ctx.moveTo(x, h);
          ctx.lineTo(x, y + r2);
          ctx.quadraticCurveTo(x, y, x + r2, y);
          ctx.lineTo(x + barNet - r2, y);
          ctx.quadraticCurveTo(x + barNet, y, x + barNet, y + r2);
          ctx.lineTo(x + barNet, h);
          ctx.fill();
          if (val > 0.06) {
            ctx.fillStyle = 'rgba(' + rgb[0] + ',' + rgb[1] + ',' + rgb[2] + ',' + (0.55 * edgeFade) + ')';
            ctx.fillRect(x, y, barNet, Math.max(1, dpr));
          }
        }
      }
      idlePhase += 0.012;
    }
    draw();
  }

  // ─── Cleanup previous instance ───

  var _cleanup = null;

  function cleanup() {
    if (_cleanup) { _cleanup(); _cleanup = null; }
  }

  // Graceful dismiss — fade out audio, then close context after fade
  function dismiss() {
    if (!_cleanup) return;
    var fn = _cleanup;
    _cleanup = null;
    if (fn._state && fn._state.playing && fn._state.audioCtx) {
      // Fade the gain to zero, then close context after fade completes
      var s = fn._state;
      try {
        var now = s.audioCtx.currentTime;
        s.masterGain.gain.cancelScheduledValues(now);
        s.masterGain.gain.setValueAtTime(s.masterGain.gain.value, now);
        s.masterGain.gain.linearRampToValueAtTime(0, now + FADE_OUT);
      } catch(e) {}
      s.playing = false;
      // Close everything after the fade
      setTimeout(function() {
        if (s.cosmicAudio) { try { s.cosmicAudio.stop(); } catch(e) {} }
        if (s.audioCtx && s.audioCtx.state !== 'closed') {
          try { s.audioCtx.close(); } catch(e) {}
        }
      }, (FADE_OUT + 0.1) * 1000);
    } else {
      fn();
    }
  }

  // ─── Main inject ───

  function inject(plate, meta) {
    cleanup();

    // Parse record data
    var birth = meta.birth || {};
    var sign = parseSunSign(birth.sun);
    var moonPhase = cleanMoonPhase(birth.moon_phase);
    // V1 records carry only birth_traits; reconstruct the temperament
    // string via birth-text.js. Falls back to any inline persisted
    // string on V4-era records.
    var _bt = (typeof BirthText !== 'undefined' && meta.birth_traits)
      ? BirthText.read(meta.birth_traits) : null;
    var temperament = parseTemperament((_bt && _bt.temperament) || meta.birth_temperament);
    var rarityScore = (typeof RarityScore !== 'undefined')
      ? RarityScore.fromRecord(meta) : (meta.rarity_score || 0);
    var tier = getRarityTier(rarityScore);
    var rKey = rarityKey(tier.name);

    // Star name: Bayer designation or identifier. V1 stores
    // constellation_index as a top-level int (0=α, 1=β, …, 11=μ).
    // The record's top-level index is primary; fall back to any cycling
    // (indexed) layer's chunk index for records without one.
    var starName;
    if (meta.constellation_name) {
      var idx;
      if (typeof meta.constellation_index === 'number') {
        idx = meta.constellation_index;
      } else if (meta.chunks && typeof meta.chunks === 'object') {
        idx = 0;
        var _names = Object.keys(meta.chunks);
        for (var _i = 0; _i < _names.length; _i++) {
          var _e = meta.chunks[_names[_i]];
          if (_e && typeof _e.index === 'number' && typeof _e.total === 'number') { idx = _e.index; break; }
        }
      } else {
        idx = 0;
      }
      starName = (idx < BAYER.length ? BAYER[idx] : '') + ' ' + meta.constellation_name;
    } else {
      starName = meta._identifier || meta.content_hash || '?';
    }

    // Song name from record or computed
    var songName = meta.song_name || (typeof CosmicAudio !== 'undefined' ?
      CosmicAudio.songName(meta.content_hash || meta._content_hash || '') : '');

    // Subtitle
    var subtitle = sign + ' \u2022 ' + moonPhase + ' \u2022 ' + tier.name;

    // Build DOM
    var dom = buildDOM(starName, songName, subtitle, rKey);
    tint(dom, rKey);

    // Audio state (shared via closure)
    var state = {
      audioCtx: null, masterGain: null, analyser: null,
      cosmicAudio: null, playing: false, audioBuilt: false,
      cosmicMode: false, rKey: rKey
    };

    // Audio params for CosmicAudio.create()
    // hash individualizes the sound — without it, same params = same sound
    var resolvedHash = meta.content_hash || meta._content_hash || '';
    // audioParams.moonPhase is the human STRING ("Full Moon"), not
    // the V1 dict — CosmicAudio's mappings (element filter, dust
    // density, etc.) key on the name. cleanMoonPhase normalizes
    // either input shape to the bare name.
    var audioParams = {
      sign: sign,
      moonPhase: moonPhase || 'Full Moon',
      temperament: temperament,
      rarity: rarityScore,
      hash: resolvedHash
    };
    function getTargetVolume() {
      return (parseInt(dom.volSlider.value) / 100) * 0.65;
    }

    function buildAudio() {
      state.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      state.audioCtx.resume().then(function() {
        state.masterGain = state.audioCtx.createGain();
        state.masterGain.gain.value = 0;
        state.masterGain.connect(state.audioCtx.destination);

        state.analyser = state.audioCtx.createAnalyser();
        state.analyser.fftSize = 512;
        state.analyser.smoothingTimeConstant = 0.8;
        state.analyser.minDecibels = -90;
        state.analyser.maxDecibels = -10;
        state.masterGain.connect(state.analyser);

        state.cosmicAudio = CosmicAudio.create({
          sign: audioParams.sign,
          moonPhase: audioParams.moonPhase,
          temperament: audioParams.temperament,
          rarity: audioParams.rarity,
          hash: audioParams.hash,
          speaker: !state.cosmicMode
        });
        state.cosmicAudio.start(state.audioCtx, state.masterGain);
        state.audioBuilt = true;

        var now = state.audioCtx.currentTime;
        state.masterGain.gain.setValueAtTime(0, now);
        state.masterGain.gain.linearRampToValueAtTime(getTargetVolume(), now + FADE_IN);
      });
    }

    function fadeIn() {
      state.playing = true;
      if (!state.audioBuilt) {
        buildAudio();
      } else {
        state.audioCtx.resume().then(function() {
          var now = state.audioCtx.currentTime;
          state.masterGain.gain.cancelScheduledValues(now);
          state.masterGain.gain.setValueAtTime(0, now);
          state.masterGain.gain.linearRampToValueAtTime(getTargetVolume(), now + FADE_IN);
        });
      }
    }

    function fadeOut() {
      if (!state.audioCtx || !state.playing) return;
      state.playing = false;
      var now = state.audioCtx.currentTime;
      state.masterGain.gain.cancelScheduledValues(now);
      state.masterGain.gain.setValueAtTime(state.masterGain.gain.value, now);
      state.masterGain.gain.linearRampToValueAtTime(0, now + FADE_OUT);
      setTimeout(function() {
        if (state.audioCtx && state.audioCtx.state === 'running') {
          state.audioCtx.suspend();
        }
      }, (FADE_OUT + 0.1) * 1000);
    }

    function togglePlay() {
      if (state.playing) {
        fadeOut();
        dom.btn.classList.remove('playing');
        applyPlayRing(dom, rKey, false);
      } else {
        fadeIn();
        dom.btn.classList.add('playing');
        applyPlayRing(dom, rKey, true);
      }
    }

    function toggleCosmic() {
      state.cosmicMode = !state.cosmicMode;
      if (state.cosmicMode) {
        dom.vfx.classList.add('engaged');
        dom.vfx.classList.add('flash');
        setTimeout(function() { dom.vfx.classList.remove('flash'); }, 450);
      } else {
        dom.vfx.classList.remove('engaged');
      }

      if (state.playing && state.audioCtx) {
        var XFADE = 1.5;
        var oldCtx = state.audioCtx;
        var oldGain = state.masterGain;
        var oldCosmic = state.cosmicAudio;
        var now = oldCtx.currentTime;
        oldGain.gain.cancelScheduledValues(now);
        oldGain.gain.setValueAtTime(oldGain.gain.value, now);
        oldGain.gain.linearRampToValueAtTime(0, now + XFADE);
        setTimeout(function() {
          if (oldCosmic) oldCosmic.stop();
          try { oldCtx.close(); } catch(e) {}
        }, (XFADE + 0.1) * 1000);

        state.audioCtx = null;
        state.masterGain = null;
        state.analyser = null;
        state.cosmicAudio = null;
        state.audioBuilt = false;
        state.playing = false;
        fadeIn();
        dom.btn.classList.add('playing');
        applyPlayRing(dom, rKey, true);
      }
    }

    function setVolume(val) {
      if (!state.audioCtx || !state.playing) return;
      var v = (val / 100) * 0.65;
      state.masterGain.gain.setTargetAtTime(v, state.audioCtx.currentTime, 0.08);
      dom.volIcon.classList.toggle('muted', val == 0);
    }

    function toggleMute() {
      if (parseInt(dom.volSlider.value) > 0) {
        dom.volSlider.dataset.prev = dom.volSlider.value;
        dom.volSlider.value = 0;
        setVolume(0);
        dom.volIcon.classList.add('muted');
      } else {
        dom.volSlider.value = dom.volSlider.dataset.prev || 70;
        setVolume(dom.volSlider.value);
        dom.volIcon.classList.remove('muted');
      }
    }

    // Wire events
    dom.btn.addEventListener('click', togglePlay);
    dom.btn.addEventListener('touchend', function(e) { e.preventDefault(); togglePlay(); });
    dom.vfx.addEventListener('click', toggleCosmic);
    dom.volSlider.addEventListener('input', function() { setVolume(this.value); });
    dom.volIcon.addEventListener('click', toggleMute);

    // Append to plate
    plate.appendChild(dom.player);

    // Start EQ and reveal
    startEq(dom.canvas, state);
    setTimeout(function() {
      dom.player.classList.add('visible');
      window.dispatchEvent(new Event('resize'));
    }, 800);

    // Store cleanup with fadeOut reference for graceful dismiss
    _cleanup = function() {
      if (state.cosmicAudio) { state.cosmicAudio.stop(); state.cosmicAudio = null; }
      if (state.audioCtx && state.audioCtx.state !== 'closed') {
        try { state.audioCtx.close(); } catch(e) {}
      }
      state.audioCtx = null;
      state.masterGain = null;
      state.analyser = null;
      state.playing = false;
      state.audioBuilt = false;
    };
    _cleanup._fadeOut = fadeOut;
    _cleanup._state = state;
  }

  return { inject: inject, cleanup: cleanup, dismiss: dismiss };
})();

// =====================================================================
// PLAYER REPARENTING — on desktop, lift the audio player out of its
// content container (the .plate, which scrolls internally) and into
// the fixed .panel-right-has-player slot so it can anchor at the
// viewport bottom as a glass bar. Works on any page that has a
// .cosmic-player AND a .panel-right-has-player slot.
//
// Stateless: each tick checks where the player currently lives and
// corrects it if it's in the wrong container. Prior versions cached a
// _moved flag that went stale when resetAll wiped certWrap.innerHTML,
// leaving the next cert's new player stuck inside the scrolling plate.
// =====================================================================
(function() {
  function checkReparent() {
    var player = document.querySelector('.cosmic-player');
    if (!player) return;
    // Planetarium owns the player while it's body-level
    // (.in-planetarium). Don't fight its positioning — the
    // planetarium puts the player back to its original parent on
    // close, at which point this re-homing logic runs again.
    if (player.classList.contains('in-planetarium')) return;
    var slot = document.querySelector('.panel-right-has-player');
    if (!slot) return;
    var layout = document.querySelector('.panel-layout');
    var isDesktop = window.innerWidth >= 1200 &&
      layout && layout.classList.contains('layout-active');

    if (isDesktop) {
      // Desktop: player belongs as a direct child of the slot so
      // position: absolute; bottom: 0 anchors it to the panel bottom.
      if (player.parentElement !== slot) slot.appendChild(player);
    } else {
      // Mobile / narrow: player stays as the last child of the plate.
      // The plate carries padding-bottom on mobile, which extends the
      // containing block past the player's natural position and gives
      // position: sticky the range it needs to ride the viewport bottom
      // through the cert scroll.
      var plate = slot.querySelector('.plate');
      if (plate && player.parentElement !== plate) plate.appendChild(player);
    }
  }

  var observer = new MutationObserver(function() { requestAnimationFrame(checkReparent); });
  observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
  window.addEventListener('resize', checkReparent);
})();
