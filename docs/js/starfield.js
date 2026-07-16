// =====================================================================
// STAR FIELD — single page-level cosmic backdrop, shared with the
// planetarium. Auto-initializes on any page with a <canvas id="starfield">.
//
// Owns one canvas. Owns the camera state (thetaY, thetaX, zoom, mode).
// Renders the bg starfield every tick. When a planetarium opens, it
// flips camera.mode to 'cosmic' and registers an overlay callback that
// gets called after the bg render each frame — the constellation draws
// onto the same pixels, so the starfield is one continuous backdrop
// instead of two stacked canvases swapping between modes.
//
// Public API:
//   Starfield.camera          // { thetaY, thetaX, zoom, zoomTarget,
//                              //   velY, velX, mode, cyOffsetCss }
//   Starfield.setOverlay(fn)  // fn(ctx, { camera, W, H, scale, theme,
//                              //          renderScale, cyOffsetPx })
//   Starfield.clearOverlay()
//   Starfield.canvas          // raw <canvas> reference
//   Starfield.theme           // 'yang' | 'yin'
//   Starfield.renderScale     // canvas-pixel-to-css-pixel ratio
//
// Mode values:
//   'ambient'  — slow incremental Y drift, dome projection (default).
//                Increments are additive so flipping into/out of cosmic
//                mode preserves thetaY without snapping.
//   'cosmic'   — full 3D rotation (planetarium); driven by user input
//                via an external ticker (cosmic-planetarium.js).
//   'earth'    — celestial dome (planetarium); driven by user gaze.
// =====================================================================
var Starfield = (function() {
  'use strict';

  var canvas = null, ctx = null, theme = 'yang';
  var overlay = null;
  var renderScale = 1;
  var MAX_LONG = 1800;
  var timer = null;            // pending tick timeout; null while paused (tab hidden)

  // Camera state shared with the planetarium. The planetarium reads
  // this for its constellation overlay AND mutates it on user input.
  // Ambient mode advances thetaY incrementally so the angle is
  // preserved when other modules flip mode in/out — no snap on close.
  var camera = {
    thetaY: 0, thetaX: 0,
    // Ambient default zoom > 1 — page-level starfield reads as a
    // bigger, more present sky on the decoder/validator. Planetarium
    // overrides to 1.0 on open and resets back to 1.5 on close.
    zoom: 1.5, zoomTarget: 1.5,
    velY: 0.00055,
    velX: 0,
    mode: 'ambient',
    // CSS-pixel y offset applied to the projection center. The
    // planetarium sets this negative to lift the constellation above
    // the cosmic player; bg render uses it too so bg + overlay share
    // the same rotation pivot.
    cyOffsetCss: 0
  };

  function init() {
    canvas = document.getElementById('starfield');
    if (!canvas || typeof CosmicStarfield === 'undefined') return false;
    ctx = canvas.getContext('2d', { alpha: true, desynchronized: true });
    theme = canvas.getAttribute('data-theme') === 'yin' ? 'yin' : 'yang';

    // Warmth per page theme is exposed in docs/js/theme.js so the
    // atmosphere can be reskinned per Age. Fallback values match the
    // vanilla skin in case theme.js is unavailable for any reason.
    var twf = (typeof Theme !== 'undefined' && Theme.starfield) || {};
    var warmFreq = theme === 'yin'
      ? (typeof twf.yinWarmFreq === 'number' ? twf.yinWarmFreq : 0)
      : (typeof twf.yangWarmFreq === 'number' ? twf.yangWarmFreq : 0.25);
    CosmicStarfield.generate('ambient:' + theme, {
      outerCount: 360, innerCount: 200,
      warmFreq: warmFreq
    });

    window.addEventListener('resize', resize);
    document.addEventListener('visibilitychange', onVisibility);
    resize();
    tick();
    return true;
  }

  // Pause the render loop while the tab is backgrounded — a hidden page
  // doesn't need to animate, and stopping the ticker frees CPU/memory on
  // long-lived tabs (browsers also throttle background timers anyway).
  // Resume when it returns to the foreground; the timer === null guard
  // prevents starting a second loop if one is already pending.
  function onVisibility() {
    if (!document.hidden && timer === null && canvas) tick();
  }

  var lastW = 0;
  function resize() {
    if (!canvas) return;
    // Mobile browsers fire 'resize' while you scroll, as the address bar shows
    // or hides: window.innerHeight (the VISUAL viewport) changes but the width
    // does not. Reassigning canvas.width/height CLEARS the backing store, so
    // honoring those events repaints the field on every scroll frame — the
    // flicker. Two changes fix it, without a debounce:
    //   1. Rebuild the backing store ONLY when the width actually changes
    //      (a real resize or rotation). A height-only 'resize' is ignored.
    //   2. Size to the LAYOUT viewport (documentElement.clientHeight), which
    //      is stable across the address-bar toggle, and let CSS own the display
    //      size (#starfield is position:fixed; height:100%), so the fixed
    //      canvas stretches to cover with no JS repaint.
    var W = window.innerWidth;
    if (W === lastW) return;
    lastW = W;
    var H = document.documentElement.clientHeight || window.innerHeight;
    var longest = Math.max(W, H);
    var s = longest > MAX_LONG ? (MAX_LONG / longest) : 1;
    canvas.width = Math.round(W * s);
    canvas.height = Math.round(H * s);
    renderScale = s;
  }

  function tick() {
    var W = canvas.width, H = canvas.height;

    if (camera.mode === 'ambient') {
      // Incremental drift — preserves thetaY across mode flips so the
      // bg never snaps when the planetarium hands camera back.
      camera.thetaY += 0.00055;
      // Ease thetaX back to 0; ambient is conceptually a horizontal
      // drift only, so any leftover X tilt from a recent planetarium
      // session relaxes out within a few seconds.
      camera.thetaX *= 0.95;
    }
    // Zoom always eases toward target (works for all modes)
    camera.zoom += (camera.zoomTarget - camera.zoom) * 0.12;

    var cyOffsetPx = (camera.cyOffsetCss || 0) * renderScale;
    var cx = W / 2;
    var cy = H / 2 + cyOffsetPx;
    var scale = Math.min(W, H) * 0.32 * camera.zoom;

    ctx.clearRect(0, 0, W, H);

    if (camera.mode === 'earth') {
      CosmicStarfield.renderDome(ctx, {
        cx: cx, cy: cy, W: W, H: H, scale: scale,
        thetaY: camera.thetaY, thetaX: camera.thetaX,
        invertPan: true, fovLimit: Math.PI,
        theme: theme, brightnessBoost: 2.0
      });
    } else if (camera.mode === 'cosmic') {
      CosmicStarfield.renderCosmic(ctx, {
        cx: cx, cy: cy, W: W, H: H, scale: scale,
        thetaY: camera.thetaY, thetaX: camera.thetaX,
        perspective: 2.4
      });
    } else {
      // Ambient (default page state) — dome projection, slow drift,
      // theme-aware colors, twinkle.
      CosmicStarfield.renderDome(ctx, {
        cx: cx, cy: cy, W: W, H: H, scale: scale,
        thetaY: camera.thetaY, thetaX: 0,
        theme: theme, time: Date.now(),
        brightnessBoost: 2.4
      });
    }

    // Overlay (constellation when planetarium is engaged)
    if (overlay) {
      try {
        overlay(ctx, {
          camera: camera, W: W, H: H, scale: scale,
          cx: cx, cy: cy, theme: theme,
          renderScale: renderScale, cyOffsetPx: cyOffsetPx
        });
      } catch (e) {}
    }

    // Reschedule only while visible; a hidden tab pauses here (timer set
    // to null) and is restarted by onVisibility when refocused.
    timer = document.hidden ? null : setTimeout(tick, 50);
  }

  function setOverlay(fn) { overlay = fn; }
  function clearOverlay() { overlay = null; }

  return {
    init: init,
    camera: camera,
    setOverlay: setOverlay,
    clearOverlay: clearOverlay,
    get canvas() { return canvas; },
    get theme() { return theme; },
    get renderScale() { return renderScale; }
  };
})();

if (document.readyState !== 'loading') Starfield.init();
else document.addEventListener('DOMContentLoaded', Starfield.init);
