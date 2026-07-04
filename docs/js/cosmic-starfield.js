// =====================================================================
// COSMIC STARFIELD — shared 3D starfield engine.
//
// Generates a deterministic shell + volume of stars and renders them
// in two modes:
//   renderCosmic — full 3D rotation + perspective parallax (planetarium
//                  3D mode; eventually the page background's "engaged"
//                  state).
//   renderDome   — celestial-sphere dome projection (planetarium earth
//                  mode; stars at infinity, no parallax, gaze rotation
//                  translates them across the screen).
//
// State (the star array) lives inside the module; callers own camera
// state (thetaY, thetaX, scale, etc.) and pass it in per render call.
//
// Designed so the same engine drives:
//   - The planetarium overlay (engaged, dense, interactive)
//   - The decoder/validator page background (ambient, slow drift)
// by the caller varying counts, density, and per-frame state.
// =====================================================================

var CosmicStarfield = (function() {
  'use strict';

  var stars = [];

  // ─── Internal helpers ───
  // Seed/RNG come from MMRng (rng.js) — single source of truth so a
  // fix to seeding behavior propagates to every module that needs
  // reproducible randomness.
  var _makeRng = MMRng.make;
  var _strSeed = MMRng.strSeed;
  function _wrapPi(t) {
    var w = ((t + Math.PI) % (Math.PI * 2) + Math.PI * 2) % (Math.PI * 2);
    return w - Math.PI;
  }

  // ─── Generation ───
  // Each star carries its 3D position (cosmic mode), celestial
  // coordinates (dome mode), and visual props. Generated from a seeded
  // RNG so reopening the same scene produces the same sky.
  //
  // opts:
  //   outerCount  — distant shell (default 360)
  //   innerCount  — interior volume (default 380)
  //   shellRMin   — inner shell radius (default 1.5)
  //   shellRMax   — outer shell radius (default 2.1)
  //   volumeRMin  — interior min radius (default 0.7)
  //   volumeRMax  — interior max radius (default 1.45)
  //   warmFreq    — share of warm-tinted stars (default 0.25)
  function generate(seed, opts) {
    opts = opts || {};
    var outerCount = (opts.outerCount != null) ? opts.outerCount : 360;
    var innerCount = (opts.innerCount != null) ? opts.innerCount : 380;
    var shellRMin  = opts.shellRMin  || 1.5;
    var shellRMax  = opts.shellRMax  || 2.1;
    var volMin     = opts.volumeRMin || 0.7;
    var volMax     = opts.volumeRMax || 1.45;
    var warmFreq   = (opts.warmFreq != null) ? opts.warmFreq : 0.25;

    stars = [];
    var rng = _makeRng(_strSeed('cosmos:' + (seed || 'default')));

    function push(x, y, z, R, brightness, size, warm) {
      stars.push({
        x: x, y: y, z: z,
        azimuth: Math.atan2(x, z),
        elevation: Math.asin(y / R),
        brightness: brightness, size: size, warm: warm,
        // Per-star twinkle phase + speed, used by render functions
        // when a `time` opt is passed in. Subtle alpha modulation so
        // ambient mode reads as a living sky instead of a static dump.
        twinklePhase: rng() * Math.PI * 2,
        twinkleSpeed: 0.0008 + rng() * 0.0024
      });
    }

    // Outer shell — distant ambient sky surrounding the scene.
    // Brightness skewed slightly upward so the cosmos reads as
    // present, not faint. Most stars sit in the mid-bright range
    // with a handful of clear "hero" stars.
    for (var i = 0; i < outerCount; i++) {
      var phi = rng() * Math.PI * 2;
      var u = rng() * 2 - 1;
      var theta = Math.acos(u);
      var R = shellRMin + rng() * (shellRMax - shellRMin);
      push(
        R * Math.sin(theta) * Math.cos(phi),
        R * Math.sin(theta) * Math.sin(phi),
        R * Math.cos(theta),
        R,
        0.3 + rng() * 0.7,
        0.5 + rng() * 1.0,
        rng() < warmFreq
      );
    }

    // Inner volume — fills the space between any focal subject (e.g.
    // a constellation) and the outer shell. Cube-rooted radius gives
    // uniform volume distribution so stars don't pile up at the inner
    // boundary.
    var rMinCubed = volMin * volMin * volMin;
    var rMaxCubed = volMax * volMax * volMax;
    for (var j = 0; j < innerCount; j++) {
      var phi2 = rng() * Math.PI * 2;
      var u2 = rng() * 2 - 1;
      var theta2 = Math.acos(u2);
      var R2 = Math.cbrt(rMinCubed + rng() * (rMaxCubed - rMinCubed));
      push(
        R2 * Math.sin(theta2) * Math.cos(phi2),
        R2 * Math.sin(theta2) * Math.sin(phi2),
        R2 * Math.cos(theta2),
        R2,
        0.25 + rng() * 0.6,
        0.4 + rng() * 0.7,
        rng() < (warmFreq * 0.8)
      );
    }
  }

  function getStars() { return stars; }

  // ─── Render: cosmic (full 3D rotation + perspective parallax) ───
  // opts:
  //   cx, cy, W, H  — viewport center + dimensions
  //   scale         — px per world unit
  //   thetaY, thetaX — camera rotation
  //   perspective   — perspective denominator (default 2.4)
  function renderCosmic(ctx, opts) {
    var cx = opts.cx, cy = opts.cy;
    var W = opts.W, H = opts.H;
    var scale = opts.scale;
    var thetaY = opts.thetaY || 0, thetaX = opts.thetaX || 0;
    var perspective = opts.perspective || 2.4;
    var cy2 = Math.cos(thetaY), sy = Math.sin(thetaY);
    var cx2 = Math.cos(thetaX), sx = Math.sin(thetaX);
    for (var b = 0; b < stars.length; b++) {
      var bs = stars[b];
      // rotateY then rotateX, inlined
      var rx = bs.x * cy2 + bs.z * sy;
      var ry = bs.y;
      var rz = -bs.x * sy + bs.z * cy2;
      var ry2 = ry * cx2 - rz * sx;
      var rz2 = ry * sx + rz * cx2;
      var bf = perspective / (perspective - rz2);
      if (bf <= 0 || bf > 3.5) continue;
      var bgX = cx + rx * scale * bf;
      var bgY = cy + ry2 * scale * bf;
      if (bgX < -8 || bgX > W + 8 || bgY < -8 || bgY > H + 8) continue;
      var depthMix = rz2 + 0.5;
      var ba = bs.brightness * (depthMix < 0.215 ? 0.15 : (depthMix > 1.43 ? 1 : depthMix * 0.7));
      ctx.fillStyle = bs.warm
        ? 'rgba(255,240,210,' + ba + ')'
        : 'rgba(210,220,240,' + ba + ')';
      var sz = bs.size * (bf < 2 ? bf : 2);
      ctx.fillRect(bgX - sz * 0.5, bgY - sz * 0.5, sz, sz);
    }
  }

  // ─── Render: dome (celestial sphere, no parallax) ───
  // Stars treated as if at infinity. Gaze direction (thetaY, thetaX)
  // translates the star field across the screen — no perspective,
  // uniform sizes, what real stars look like from a fixed viewpoint.
  //
  // opts:
  //   cx, cy, W, H   — viewport center + dimensions
  //   scale          — px per world unit (sets pxPerRad for translation)
  //   thetaY, thetaX — gaze direction
  //   fovLimit       — half-FOV in radians (default π * 0.55, ~100°)
  //   invertPan      — if true, drag right -> sky follows right
  //                    (grab-the-sky direction); else head-turn direction
  //   theme          — 'yang' (light stars on dark, default) or 'yin'
  //                    (dark ink on a cream background)
  //   time           — current ms-ish time for twinkle modulation; if
  //                    omitted, no twinkle
  function renderDome(ctx, opts) {
    var cx = opts.cx, cy = opts.cy;
    var W = opts.W, H = opts.H;
    var scale = opts.scale;
    var thetaY = opts.thetaY || 0, thetaX = opts.thetaX || 0;
    var fovLimit = (opts.fovLimit != null) ? opts.fovLimit : Math.PI * 0.55;
    var invertPan = !!opts.invertPan;
    var theme = opts.theme || 'yang';
    var time = (opts.time != null) ? opts.time : null;
    var boost = (opts.brightnessBoost != null) ? opts.brightnessBoost : 1;
    var pxPerRad = scale * 0.85;
    // Fade only at the very edge of the FOV so bg stars cover the full
    // visible region. Off-screen culling handles the actual cutoff.
    var fadeStart = fovLimit * 0.92;
    var fadeRange = fovLimit - fadeStart;
    var sign = invertPan ? -1 : 1;
    for (var b = 0; b < stars.length; b++) {
      var bs = stars[b];
      var dAzi = _wrapPi(bs.azimuth - thetaY);
      if (dAzi > fovLimit || dAzi < -fovLimit) continue;
      var dEle = thetaX - bs.elevation;
      var bgX = cx + sign * dAzi * pxPerRad;
      var bgY = cy + sign * dEle * pxPerRad;
      if (bgX < -8 || bgX > W + 8 || bgY < -8 || bgY > H + 8) continue;
      var aDAzi = dAzi < 0 ? -dAzi : dAzi;
      var fade = aDAzi <= fadeStart ? 1 : Math.max(0, 1 - (aDAzi - fadeStart) / fadeRange);
      var twinkle = (time != null) ? (0.7 + 0.3 * Math.sin(time * bs.twinkleSpeed + bs.twinklePhase)) : 1;
      var ba = bs.brightness * fade * twinkle;
      if (theme === 'yin') {
        // Dark ink on cream needs more weight per star than light on
        // dark to read at the same perceptual density.
        ctx.fillStyle = 'rgba(0,0,0,' + Math.min(1, ba * 1.5 * boost) + ')';
      } else {
        var ya = Math.min(1, ba * boost);
        ctx.fillStyle = bs.warm
          ? 'rgba(255,240,210,' + ya + ')'
          : 'rgba(210,220,240,' + ya + ')';
      }
      var sz = bs.size;
      ctx.fillRect(bgX - sz * 0.5, bgY - sz * 0.5, sz, sz);
    }
  }

  return {
    generate: generate,
    getStars: getStars,
    renderCosmic: renderCosmic,
    renderDome: renderDome
  };
})();
