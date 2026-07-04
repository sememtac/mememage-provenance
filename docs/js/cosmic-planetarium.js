// =====================================================================
// COSMIC PLANETARIUM — modal 3D constellation viewer.
//
// Owns the planetarium chrome (name, mode toggle, hint, close, music
// player) AND the constellation overlay drawn into the page-level
// Starfield canvas. The starfield is one continuous backdrop —
// ambient drift becomes 3D cosmic engagement when the planetarium
// opens, and the constellation reveals itself onto the same pixels.
//
// Public API:
//   CosmicPlanetarium.open({
//     name: 'Tudemul',                   // constellation_name
//     hash: '6903883e84e12184',          // heart's content_hash (seed source)
//     currentStarIndex: 4,               // 0..11 (alpha..mu); -1 to skip pulse
//     heartRarity: 55,                   // 0-100 → spectral class for heart
//     currentRarity: 42,                 // 0-100 → spectral class + tier color
//     meta: certMeta                     // optional, for cosmic player
//   });
//   CosmicPlanetarium.close();
//
// Depends on: starfield.js (Starfield.camera + setOverlay), cosmic-
// starfield.js (CosmicStarfield.generate), cosmic-player.js (optional
// CosmicPlayer for music).
// =====================================================================

var CosmicPlanetarium = (function() {
  'use strict';

  // ─── Constants ───
  // Full 24-letter Greek alphabet (\u03b1..\u03c9, no final sigma) \u2014 the Bayer
  // designation space. Caps constellation_size at 24. Keep in sync with
  // the tables in core.py / server.py / cert-renderer.js / cosmic-player.js.
  var GREEK = ['\u03b1','\u03b2','\u03b3','\u03b4','\u03b5','\u03b6','\u03b7','\u03b8','\u03b9','\u03ba','\u03bb','\u03bc','\u03bd','\u03be','\u03bf','\u03c0','\u03c1','\u03c3','\u03c4','\u03c5','\u03c6','\u03c7','\u03c8','\u03c9'];
  var SPECTRAL = [
    { color: [255, 180, 100], min: 0  }, // K
    { color: [255, 240, 180], min: 20 }, // G
    { color: [255, 250, 230], min: 40 }, // F
    { color: [220, 230, 255], min: 52 }, // A
    { color: [170, 190, 255], min: 64 }, // B
    { color: [130, 160, 255], min: 74 }  // O
  ];
  var RARITY_TIERS = [
    [88, '#d44040'], [72, '#8a6210'], [55, '#5a2a8a'],
    [40, '#2a5090'], [25, '#2a7030'], [0, '#606060']
  ];
  var ZOOM_MIN = 0.5, ZOOM_MAX = 8.0;
  var TRANS_MS = 1500;     // cross-mode (cosmic ↔ earth)
  var RESET_MS = 700;      // same-mode "reset to default" gesture
  var OPEN_MS  = 1500;     // ease ambient camera → centered cosmic on open
  var FADE_MS  = 600;      // constellation alpha ramp on open/close

  // ─── State ───
  var modal = null;
  var nameEl = null, hintEl = null, modeBtns = null;
  var stars = [], edges = [];
  var velY = 0.00055, velX = 0;
  var viewMode = 'cosmic';
  var transTo = null, transStart = 0, transStartTY = 0, transStartTX = 0;
  var transDuration = TRANS_MS;
  var dragging = false;
  var dragX0 = 0, dragY0 = 0, theta0Y = 0, theta0X = 0;
  var activePointers = new Map();
  var pinchStartDist = 0, pinchStartZoom = 1;
  var ticker = null;
  var heartRgbCache = [255, 240, 200];
  var currentRgbCache = [245, 245, 250];
  var pulseRgb = [220, 220, 240];
  var spriteHeart = null, spriteCurrent = null;
  var currentStarIndex = -1;
  var openOpts = null;
  var isOpen = false;
  // Constellation alpha — ramps on open/close so the overlay fades in
  // and out alongside the modal chrome's CSS opacity transition.
  var overlayAlpha = 0;
  var overlayAlphaStart = 0;
  var overlayAlphaTarget = 0;
  var overlayAlphaT0 = 0;

  function cam() { return Starfield.camera; }

  // ─── Helpers ───
  // Seed/RNG are MMRng (rng.js); local aliases keep call sites short.
  var makeRng = MMRng.make;
  var strSeed = MMRng.strSeed;
  function wrapPi(t) {
    var w = ((t + Math.PI) % (Math.PI * 2) + Math.PI * 2) % (Math.PI * 2);
    return w - Math.PI;
  }
  function rotateX(p, c, s) { return { x: p.x, y: p.y * c - p.z * s, z: p.y * s + p.z * c }; }
  function rotateY(p, c, s) { return { x: p.x * c + p.z * s, y: p.y, z: -p.x * s + p.z * c }; }
  function spectralFor(score) {
    for (var i = SPECTRAL.length - 1; i >= 0; i--) if (score >= SPECTRAL[i].min) return SPECTRAL[i];
    return SPECTRAL[0];
  }
  function tierColorFor(score) {
    for (var i = 0; i < RARITY_TIERS.length; i++) if (score >= RARITY_TIERS[i][0]) return RARITY_TIERS[i][1];
    return RARITY_TIERS[5][1];
  }
  function brightenHex(hex, amount) {
    var r = parseInt(hex.slice(1, 3), 16);
    var g = parseInt(hex.slice(3, 5), 16);
    var b = parseInt(hex.slice(5, 7), 16);
    return [
      Math.round(r + (255 - r) * amount),
      Math.round(g + (255 - g) * amount),
      Math.round(b + (255 - b) * amount)
    ];
  }

  // ─── Constellation generation ───
  // generateLayout(seed) — canonical 2D layout for a constellation,
  // shared by the cert backdrop pattern and the planetarium overlay.
  // Returns 12 stars in [-0.5, 0.5]² space (heart at index 0) plus
  // the edge list (MST + RNG closures). Both surfaces seed off the
  // SAME string (constellation_hash) so the backdrop the user sees
  // on the cert matches the constellation they navigate inside the
  // planetarium.
  function generateLayout(seed, count) {
    // count = constellation_size (stars in this constellation). Clamped to
    // [1, 24] (the Bayer cap); defaults to 12 for records minted before
    // constellation_size was stamped. The shape grows with count — the cert
    // backdrop and the planetarium pass the record's constellation_size so
    // the drawn shape matches the constellation's actual member count.
    count = Math.max(1, Math.min(24, (count | 0) || 12));
    var rng = makeRng(strSeed(seed));
    var st = [];
    st.push({ x: (0.3 + rng() * 0.4) - 0.5, y: (0.2 + rng() * 0.6) - 0.5, isHeart: true });
    for (var i = 1; i < count; i++) {
      var placed = false, x = 0, y = 0;
      for (var attempt = 0; attempt < 50 && !placed; attempt++) {
        var anchor = st[Math.floor(rng() * st.length)];
        var ang = rng() * Math.PI * 2;
        var dist = 0.10 + rng() * 0.20;
        x = anchor.x + Math.cos(ang) * dist;
        y = anchor.y + Math.sin(ang) * dist;
        x = Math.max(-0.45, Math.min(0.45, x));
        y = Math.max(-0.45, Math.min(0.45, y));
        var ok = true;
        for (var j = 0; j < st.length; j++) {
          var dx = x - st[j].x, dy = y - st[j].y;
          if (dx*dx + dy*dy < 0.005) { ok = false; break; }
        }
        if (ok) placed = true;
      }
      st.push({ x: x, y: y, isHeart: false });
    }
    // Center the constellation around (0, 0) so it sits in the middle
    // of whatever surface renders it, regardless of where the random
    // walk happened to drift.
    var minX = st[0].x, maxX = st[0].x, minY = st[0].y, maxY = st[0].y;
    for (var c = 1; c < st.length; c++) {
      if (st[c].x < minX) minX = st[c].x;
      if (st[c].x > maxX) maxX = st[c].x;
      if (st[c].y < minY) minY = st[c].y;
      if (st[c].y > maxY) maxY = st[c].y;
    }
    var shiftX = -(minX + maxX) / 2;
    var shiftY = -(minY + maxY) / 2;
    for (var c2 = 0; c2 < st.length; c2++) {
      st[c2].x += shiftX;
      st[c2].y += shiftY;
    }
    return { stars: st, edges: buildEdges(st) };
  }

  function buildEdges(cStars) {
    var n = cStars.length;
    function cDst(a, b) { var dx = b.x - a.x, dy = b.y - a.y; return Math.sqrt(dx*dx + dy*dy); }
    var cEdges = [], cInTree = [true];
    for (var i = 1; i < n; i++) cInTree.push(false);
    function cDeg(idx) { var d = 0; for (var i = 0; i < cEdges.length; i++) if (cEdges[i][0] === idx || cEdges[i][1] === idx) d++; return d; }
    function cAngBtw(ax, ay, bx, by) {
      var dot = ax*bx + ay*by;
      var m = Math.sqrt(ax*ax + ay*ay) * Math.sqrt(bx*bx + by*by);
      if (m < 0.001) return 180;
      return Math.acos(Math.max(-1, Math.min(1, dot/m))) * 180 / Math.PI;
    }
    function cAngOk(a, b) {
      var abx = cStars[b].x - cStars[a].x, aby = cStars[b].y - cStars[a].y;
      for (var ei = 0; ei < cEdges.length; ei++) {
        var e0 = cEdges[ei][0], e1 = cEdges[ei][1];
        if (e0 === a || e1 === a) { var o = e0 === a ? e1 : e0; if (cAngBtw(abx, aby, cStars[o].x - cStars[a].x, cStars[o].y - cStars[a].y) < 35) return false; }
        if (e0 === b || e1 === b) { var o2 = e0 === b ? e1 : e0; if (cAngBtw(-abx, -aby, cStars[o2].x - cStars[b].x, cStars[o2].y - cStars[b].y) < 35) return false; }
      }
      return true;
    }
    var cELens = [];
    for (var step = 0; step < n - 1; step++) {
      var cands = [];
      for (var ci = 0; ci < n; ci++) {
        if (!cInTree[ci]) continue;
        for (var cj = 0; cj < n; cj++) {
          if (cInTree[cj]) continue;
          cands.push({ i: ci, j: cj, d: cDst(cStars[ci], cStars[cj]) });
        }
      }
      cands.sort(function(a, b) { return a.d - b.d; });
      var avg = 0;
      if (cELens.length > 0) { for (var cl = 0; cl < cELens.length; cl++) avg += cELens[cl]; avg /= cELens.length; }
      var maxEdge = cELens.length >= 3 ? avg * 3 : Infinity;
      var added = false;
      for (var k0 = 0; k0 < cands.length; k0++) {
        var c0 = cands[k0];
        if (cDeg(c0.i) >= 3) continue;
        if (!cAngOk(c0.i, c0.j)) continue;
        if (c0.d > maxEdge) continue;
        cInTree[c0.j] = true; cEdges.push([c0.i, c0.j]); cELens.push(c0.d); added = true; break;
      }
      if (!added) {
        for (var k1 = 0; k1 < cands.length; k1++) {
          if (cDeg(cands[k1].i) < 3 && cands[k1].d <= maxEdge) {
            cInTree[cands[k1].j] = true; cEdges.push([cands[k1].i, cands[k1].j]); cELens.push(cands[k1].d); added = true; break;
          }
        }
      }
      if (!added && cands.length) {
        cInTree[cands[0].j] = true; cEdges.push([cands[0].i, cands[0].j]); cELens.push(cands[0].d);
      }
    }
    var cAdj = [];
    for (var ai = 0; ai < n; ai++) cAdj.push([]);
    for (var ei2 = 0; ei2 < cEdges.length; ei2++) { cAdj[cEdges[ei2][0]].push(cEdges[ei2][1]); cAdj[cEdges[ei2][1]].push(cEdges[ei2][0]); }
    // ── Closed shapes ────────────────────────────────────────────────
    // The MST above is a tree — no loops. A bare spine reads as a
    // squiggle, not a constellation. To give constellations closed cells,
    // close the SHORTEST geometric gaps that form a small cycle
    // (triangle..pentagon). Sourcing closures from the shortest
    // non-adjacent pairs — not the near-empty relative-neighbourhood graph
    // the old code mined — reliably yields ~1-2 loops per constellation.
    // The distance gate keeps every closure tight (no strut spanning the
    // whole shape); the no-enclosed-star test keeps each cell clean.
    function deg(idx) { return cAdj[idx].length; }
    function findPath(f, t, mx) {
      var q = [[f, [f]]]; var v = {}; v[f] = true;
      while (q.length > 0) {
        var cur = q.shift(); var node = cur[0], path = cur[1];
        if (path.length > mx + 1) continue;
        if (node === t && path.length > 1) return path;
        for (var ni = 0; ni < cAdj[node].length; ni++) {
          var nx = cAdj[node][ni];
          if (!v[nx]) { v[nx] = true; q.push([nx, path.concat([nx])]); }
        }
      }
      return null;
    }
    function ptInPoly(px, py, poly) {
      var s = 0;
      for (var i = 0; i < poly.length; i++) {
        var jj = (i + 1) % poly.length;
        var c = (poly[jj].x - poly[i].x) * (py - poly[i].y) - (poly[jj].y - poly[i].y) * (px - poly[i].x);
        if (Math.abs(c) < 0.01) continue;
        if (s === 0) s = c > 0 ? 1 : -1;
        else if ((c > 0 ? 1 : -1) !== s) return false;
      }
      return true;
    }
    // Proper segment intersection (CCW test). The MST is already planar, so
    // crossings only ever come from closure edges cutting across existing
    // ones — reject those so the constellation stays planar (no crossings),
    // the way a real star chart reads.
    function ccw(a, b, c) { return (c.y - a.y) * (b.x - a.x) > (b.y - a.y) * (c.x - a.x); }
    function segCross(p1, p2, p3, p4) {
      return ccw(p1, p3, p4) !== ccw(p2, p3, p4) && ccw(p1, p2, p3) !== ccw(p1, p2, p4);
    }
    function crossesAny(a, b) {
      for (var ce = 0; ce < cEdges.length; ce++) {
        var u = cEdges[ce][0], w = cEdges[ce][1];
        if (u === a || u === b || w === a || w === b) continue;  // shares an endpoint — not a crossing
        if (segCross(cStars[a], cStars[b], cStars[u], cStars[w])) return true;
      }
      return false;
    }
    // Average spine edge length — gates how long a closure may be.
    var avgLen = 0;
    if (cELens.length) { for (var al = 0; al < cELens.length; al++) avgLen += cELens[al]; avgLen /= cELens.length; }
    // Every non-adjacent star pair, shortest first.
    var gaps = [];
    for (var gi = 0; gi < n; gi++) {
      for (var gj = gi + 1; gj < n; gj++) {
        if (cAdj[gi].indexOf(gj) >= 0) continue;
        gaps.push({ i: gi, j: gj, d: cDst(cStars[gi], cStars[gj]) });
      }
    }
    gaps.sort(function(a, b) { return a.d - b.d; });
    var MAX_CLOSURES = Math.max(1, Math.round(n / 6));  // ~1 for small, up to 4 at n=24
    var DIST_FACTOR = 1.7;   // only close gaps <= 1.7x the average spine edge
    var MAX_DEG = 4;         // a loop may push a node to degree 4 (spine cap is 3)
    var MAX_CYCLE = 5;       // triangle .. pentagon
    var closed = 0;
    for (var gk = 0; gk < gaps.length && closed < MAX_CLOSURES; gk++) {
      var g = gaps[gk];
      if (avgLen > 0 && g.d > avgLen * DIST_FACTOR) break;  // sorted asc → rest are longer
      if (deg(g.i) >= MAX_DEG || deg(g.j) >= MAX_DEG) continue;
      if (crossesAny(g.i, g.j)) continue;  // keep the graph planar — no crossing lines
      var cyc = findPath(g.i, g.j, MAX_CYCLE - 1);
      if (!cyc || cyc.length < 3 || cyc.length > MAX_CYCLE) continue;  // tri..pentagon only
      var poly = cyc.map(function(idx) { return cStars[idx]; });
      var enclosed = false;
      for (var ci2 = 0; ci2 < n; ci2++) {
        if (cyc.indexOf(ci2) >= 0) continue;
        if (ptInPoly(cStars[ci2].x, cStars[ci2].y, poly)) { enclosed = true; break; }
      }
      if (enclosed) continue;
      cEdges.push([g.i, g.j]);
      cAdj[g.i].push(g.j); cAdj[g.j].push(g.i);
      closed++;
    }
    return cEdges;
  }

  // ─── Sprite caching ───
  function makeSprite(rgbPrefix, baseSize) {
    var pad = baseSize * 4;
    var s = Math.ceil(pad * 2);
    var spr = document.createElement('canvas');
    spr.width = s; spr.height = s;
    var sx = spr.getContext('2d');
    var cx_ = s / 2, cy_ = s / 2;
    var g = sx.createRadialGradient(cx_, cy_, 0, cx_, cy_, baseSize * 4);
    g.addColorStop(0, rgbPrefix + ',0.25)');
    g.addColorStop(1, rgbPrefix + ',0)');
    sx.fillStyle = g;
    sx.beginPath(); sx.arc(cx_, cy_, baseSize * 4, 0, Math.PI * 2); sx.fill();
    sx.fillStyle = rgbPrefix + ',1)';
    sx.beginPath(); sx.arc(cx_, cy_, baseSize, 0, Math.PI * 2); sx.fill();
    return { canvas: spr, base: baseSize, half: pad };
  }
  function buildStarSprites() {
    spriteHeart = makeSprite('rgba(' + heartRgbCache.join(','), 4.5);
    spriteCurrent = makeSprite('rgba(' + currentRgbCache.join(','), 3.0);
  }

  // ─── DOM ───
  function buildDom() {
    if (modal) return;
    modal = document.createElement('div');
    modal.className = 'planetarium';
    modal.id = 'cosmicPlanetarium';
    modal.setAttribute('aria-hidden', 'true');
    modal.innerHTML = ''
      + '<div class="planetarium-name">'
      +   '<div class="planetarium-name-text"></div>'
      + '</div>'
      + '<div class="planetarium-mode">'
      +   '<button data-mode="cosmic" class="active">Cosmic</button>'
      +   '<button data-mode="earth">Earth</button>'
      + '</div>'
      + '<div class="planetarium-hint">drag to rotate · pinch or scroll to zoom · you are floating in space</div>'
      + '<button class="planetarium-close" aria-label="Close">&times;</button>';
    document.body.appendChild(modal);
    nameEl = modal.querySelector('.planetarium-name-text');
    hintEl = modal.querySelector('.planetarium-hint');
    modeBtns = modal.querySelectorAll('.planetarium-mode button');
    modal.querySelector('.planetarium-close').addEventListener('click', close);
    document.addEventListener('keydown', _onKey);
    modeBtns.forEach(function(b) {
      b.addEventListener('click', function() { setViewMode(b.dataset.mode); });
    });
    bindInput();
  }
  function _onKey(e) { if (e.key === 'Escape') close(); }

  function destroyDom() {
    if (!modal) return;
    document.removeEventListener('keydown', _onKey);
    if (modal.parentElement) modal.parentElement.removeChild(modal);
    modal = null; nameEl = null; hintEl = null; modeBtns = null;
  }

  // ─── Constellation overlay (drawn onto Starfield's canvas) ───
  // Called once per Starfield tick after the bg has rendered. Uses
  // Starfield's coordinate space (canvas pixels, not CSS pixels).
  function drawConstellation(ctx, info) {
    if (overlayAlpha <= 0) return;
    var c = info.camera;
    var W = info.W, H = info.H;
    var cx = info.cx, cy = info.cy;
    var scale = info.scale;
    var perspective = 2.4;
    var cy2 = Math.cos(c.thetaY), sy = Math.sin(c.thetaY);
    var cx2 = Math.cos(c.thetaX), sx = Math.sin(c.thetaX);

    var projected;
    if (c.mode === 'earth') {
      var visibleY = wrapPi(c.thetaY);
      var visibleX = c.thetaX;
      var pxPerRadX = scale * 0.85;
      var pxPerRadY = scale * 0.85;
      var anchorX = cx + visibleY * pxPerRadX;
      var anchorY = cy - visibleX * pxPerRadY;
      var fixedScale = scale * 0.75;
      projected = stars.map(function(p, i) {
        return { i: i, isHeart: p.isHeart,
          x: anchorX + p.x * fixedScale, y: anchorY + p.y * fixedScale,
          z: 0, f: 1.0 };
      });
    } else {
      projected = stars.map(function(p, i) {
        var r1 = rotateY(p, cy2, sy);
        var r2 = rotateX(r1, cx2, sx);
        var f = perspective / (perspective - r2.z);
        return { i: i, isHeart: p.isHeart,
          x: cx + r2.x * scale * f, y: cy + r2.y * scale * f,
          z: r2.z, f: f };
      });
    }

    ctx.save();
    // globalAlpha multiplies all subsequent draws — fade-in/fade-out
    // hook for the open/close ramp.
    ctx.globalAlpha = overlayAlpha;

    // Edges (back-to-front)
    var sortedEdges = edges.map(function(e) {
      var a = projected[e[0]], b = projected[e[1]];
      return { a: a, b: b, midZ: (a.z + b.z) * 0.5 };
    }).sort(function(a, b) { return a.midZ - b.midZ; });
    for (var i = 0; i < sortedEdges.length; i++) {
      var e = sortedEdges[i];
      var alpha = Math.max(0.1, Math.min(0.5, 0.35 + e.midZ * 0.25));
      ctx.strokeStyle = 'rgba(220, 220, 240, ' + alpha + ')';
      ctx.lineWidth = 0.6;
      ctx.beginPath();
      ctx.moveTo(e.a.x, e.a.y);
      ctx.lineTo(e.b.x, e.b.y);
      ctx.stroke();
    }

    // Stars
    var sortedStars = projected.slice().sort(function(a, b) { return a.z - b.z; });
    ctx.font = '10px -apple-system, system-ui, sans-serif';
    ctx.textAlign = 'left';
    for (var s = 0; s < sortedStars.length; s++) {
      var p = sortedStars[s];
      var brightness = 0.7 + p.z * 0.5;
      if (brightness < 0.4) brightness = 0.4; else if (brightness > 1) brightness = 1;
      var isKnown = p.isHeart || p.i === currentStarIndex;
      if (isKnown) {
        var spr = p.isHeart ? spriteHeart : spriteCurrent;
        var sprScale = p.f;
        var drawW = spr.canvas.width * sprScale;
        var drawH = spr.canvas.height * sprScale;
        // Compose per-star brightness with the master fade alpha.
        ctx.globalAlpha = brightness * overlayAlpha;
        ctx.drawImage(spr.canvas, p.x - drawW * 0.5, p.y - drawH * 0.5, drawW, drawH);
        ctx.globalAlpha = overlayAlpha;
        ctx.fillStyle = 'rgba(220,220,235,' + (0.6 + 0.3 * brightness) + ')';
        ctx.fillText(GREEK[p.i], p.x + spr.base * sprScale + 6, p.y + 3);
      } else {
        var dotR = 1.7 * p.f;
        ctx.fillStyle = 'rgba(170,175,195,' + (brightness * 0.55) + ')';
        ctx.beginPath(); ctx.arc(p.x, p.y, dotR, 0, Math.PI * 2); ctx.fill();
      }
    }

    // Radio pulse around the current star
    if (currentStarIndex >= 0 && currentStarIndex < projected.length) {
      var me = projected[currentStarIndex];
      if (me) {
        var pulseDur = 3000;
        var startR = (me.isHeart ? 4.5 : 3.0) * me.f * 1.6;
        var maxR = startR + 70;
        var nowMs = Date.now();
        var pr = pulseRgb[0], pg = pulseRgb[1], pb = pulseRgb[2];
        for (var po = 0; po < 2; po++) {
          var offsetMs = po * pulseDur * 0.5;
          var t = ((nowMs + offsetMs) % pulseDur) / pulseDur;
          var r = startR + t * (maxR - startR);
          var alphaP = (1 - t) * (1 - t) * 0.65;
          if (alphaP < 0.01) continue;
          ctx.strokeStyle = 'rgba(' + pr + ',' + pg + ',' + pb + ',' + alphaP + ')';
          ctx.lineWidth = 1.2;
          ctx.beginPath(); ctx.arc(me.x, me.y, r, 0, Math.PI * 2); ctx.stroke();
        }
      }
    }

    ctx.restore();
  }

  // ─── Camera ticker ───
  // Starfield's auto-drift only runs in ambient mode. While the
  // planetarium is engaged (cosmic/earth) this ticker advances camera
  // angles — auto-rotate, transition easing, velocity decay — and
  // updates the alpha ramp. Starfield's render tick reads the latest
  // values each frame.
  function tickCamera() {
    if (!isOpen) return;
    var c = cam();
    if (transTo) {
      var tt = Math.min(1, (Date.now() - transStart) / (transDuration || TRANS_MS));
      var eased = tt * tt * (3 - 2 * tt);
      var ty = wrapPi(transStartTY);
      c.thetaY = ty * (1 - eased);
      c.thetaX = transStartTX * (1 - eased);
      if (tt >= 1) { transTo = null; c.thetaY = 0; c.thetaX = 0; }
    } else if (!dragging) {
      c.thetaY += velY;
      c.thetaX += velX;
      velX *= 0.998;
    }
    // Alpha ramp
    if (overlayAlpha !== overlayAlphaTarget) {
      var at = Math.min(1, (Date.now() - overlayAlphaT0) / FADE_MS);
      var ae = at * at * (3 - 2 * at);
      overlayAlpha = overlayAlphaStart + (overlayAlphaTarget - overlayAlphaStart) * ae;
      if (at >= 1) overlayAlpha = overlayAlphaTarget;
    }
  }
  function startTicker() {
    if (ticker) return;
    function go() { tickCamera(); ticker = setTimeout(go, 40); }
    go();
  }
  function stopTicker() { if (ticker) { clearTimeout(ticker); ticker = null; } }
  function rampAlpha(target) {
    overlayAlphaStart = overlayAlpha;
    overlayAlphaTarget = target;
    overlayAlphaT0 = Date.now();
  }

  // ─── Input ───
  // Bound to the modal: it covers the viewport when visible, so all
  // pointer events that aren't on chrome (close / mode toggle) become
  // drag/pinch input for the camera.
  function bindInput() {
    modal.addEventListener('pointerdown', _onPointerDown);
    modal.addEventListener('pointermove', _onPointerMove);
    modal.addEventListener('pointerup', _onPointerEnd);
    modal.addEventListener('pointercancel', _onPointerEnd);
    modal.addEventListener('wheel', _onWheel, { passive: false });
  }
  function _onPointerDown(e) {
    // Let chrome handle its own clicks (button, mode toggle).
    if (e.target.closest && e.target.closest('.planetarium-close, .planetarium-mode')) return;
    activePointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    try { modal.setPointerCapture(e.pointerId); } catch (_) {}
    var c = cam();
    if (activePointers.size === 1) {
      dragging = true;
      modal.classList.add('dragging');
      dragX0 = e.clientX; dragY0 = e.clientY;
      theta0Y = c.thetaY; theta0X = c.thetaX;
      velY = 0; velX = 0;
    } else if (activePointers.size === 2) {
      dragging = false;
      var pts = []; activePointers.forEach(function(v) { pts.push(v); });
      var dxp = pts[1].x - pts[0].x, dyp = pts[1].y - pts[0].y;
      pinchStartDist = Math.sqrt(dxp * dxp + dyp * dyp);
      pinchStartZoom = c.zoomTarget;
    }
  }
  function _onPointerMove(e) {
    if (!activePointers.has(e.pointerId)) return;
    activePointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    var c = cam();
    if (activePointers.size === 1 && dragging) {
      var dx = e.clientX - dragX0, dy = e.clientY - dragY0;
      var sens = 0.005 / (c.zoom > 0.1 ? c.zoom : 0.1);
      c.thetaY = theta0Y + dx * sens;
      c.thetaX = theta0X - dy * sens;
      c.thetaX = Math.max(-Math.PI / 2.2, Math.min(Math.PI / 2.2, c.thetaX));
    } else if (activePointers.size === 2 && pinchStartDist > 0) {
      var pts2 = []; activePointers.forEach(function(v) { pts2.push(v); });
      var dxp2 = pts2[1].x - pts2[0].x, dyp2 = pts2[1].y - pts2[0].y;
      var dist2 = Math.sqrt(dxp2 * dxp2 + dyp2 * dyp2);
      var nz = pinchStartZoom * (dist2 / pinchStartDist);
      c.zoomTarget = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, nz));
    }
  }
  function _onPointerEnd(e) {
    if (!activePointers.has(e.pointerId)) return;
    activePointers.delete(e.pointerId);
    if (activePointers.size === 0) {
      if (dragging) {
        dragging = false;
        if (modal) modal.classList.remove('dragging');
        velY = (viewMode === 'earth') ? 0.00028 : 0.00055;
        velX = 0;
      }
      pinchStartDist = 0;
    } else if (activePointers.size === 1) {
      pinchStartDist = 0;
      var arr = []; activePointers.forEach(function(v) { arr.push(v); });
      var rem = arr[0];
      var c = cam();
      dragX0 = rem.x; dragY0 = rem.y;
      theta0Y = c.thetaY; theta0X = c.thetaX;
      dragging = true;
      if (modal) modal.classList.add('dragging');
    }
  }
  function _onWheel(e) {
    e.preventDefault();
    var c = cam();
    c.zoomTarget *= Math.exp(-e.deltaY * 0.0015);
    c.zoomTarget = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, c.zoomTarget));
  }

  // ─── View mode ───
  // Clicking either button always re-anchors the constellation to
  // the canonical default view (thetaY = thetaX = 0, zoom = 1) —
  // even if already in that mode. Acts as a "reset / go home"
  // gesture on top of the mode switch. The reset eases over a
  // shorter window than the cross-mode transition (700ms vs 1500ms)
  // so the response feels snappy rather than slow.
  function setViewMode(mode) {
    if (mode !== 'cosmic' && mode !== 'earth') return;
    var sameMode = (mode === viewMode);
    viewMode = mode;
    var c = cam();
    c.mode = mode;
    if (modeBtns) modeBtns.forEach(function(b) { b.classList.toggle('active', b.dataset.mode === mode); });
    transTo = mode;
    transStart = Date.now();
    transStartTY = c.thetaY;
    transStartTX = c.thetaX;
    transDuration = sameMode ? RESET_MS : TRANS_MS;
    velY = 0; velX = 0;
    c.zoomTarget = 1.0;
    if (mode === 'cosmic') {
      if (modal) modal.classList.remove('earth-mode');
      if (hintEl) hintEl.textContent = 'drag to rotate · pinch or scroll to zoom · you are floating in space';
    } else {
      if (modal) modal.classList.add('earth-mode');
      if (hintEl) hintEl.textContent = 'drag to look around · pinch or scroll to zoom · you are standing on Earth';
    }
    setTimeout(function() {
      if (viewMode === 'earth' && !dragging) velY = 0.00028;
      else if (viewMode === 'cosmic' && !dragging) velY = 0.00055;
    }, transDuration);
  }

  // ─── Cosmic player integration ───
  var playerResizeObserver = null;

  function updatePlayerOffset() {
    // Match the camera's cy offset to the player's visible height in
    // CSS pixels, halved (so the constellation centers in the area
    // above the player). Starfield converts CSS → canvas px internally.
    var p = document.querySelector('.cosmic-player');
    if (!p) { cam().cyOffsetCss = 0; return; }
    var rect = p.getBoundingClientRect();
    if (rect.height < 1) return;
    cam().cyOffsetCss = -rect.height / 2;
  }

  function updateHintPosition() {
    if (!modal) return;
    var p = document.querySelector('.cosmic-player');
    if (!p) return;
    var hint = modal.querySelector('.planetarium-hint');
    if (!hint) return;
    var rect = p.getBoundingClientRect();
    if (rect.height < 1) return;
    var distFromBottom = Math.round(window.innerHeight - rect.top);
    hint.style.bottom = (distFromBottom + 32) + 'px';
    updatePlayerOffset();
  }

  // Track an existing cert-page player that we MOVED (vs created
  // fresh) so dismiss can return it to its original parent without
  // tearing down the audio context — music keeps playing seamlessly
  // through the planetarium open/close.
  var _movedPlayer = null;
  var _movedPlayerOriginalParent = null;

  function injectPlayer() {
    if (!openOpts || !openOpts.meta) return;

    // Prefer to MOVE an existing player (cert page) rather than
    // dismiss + recreate. Dismissal would close the AudioContext and
    // stop playback; appendChild relocates the same DOM node so the
    // <audio>, AudioContext, and EQ analyser all keep going.
    var existing = document.querySelector('.cosmic-player');
    if (existing) {
      _movedPlayer = existing;
      _movedPlayerOriginalParent = existing.parentElement;
      document.body.appendChild(existing);
      existing.classList.add('in-planetarium');
      // EQ canvas reads its parent's width on window-resize only;
      // moving to body changes the effective width without firing a
      // resize. Fire one manually so the visualization re-centers.
      window.dispatchEvent(new Event('resize'));
    } else if (document.querySelector('.plate')) {
      // A cert plate is rendered but didn't inject a player — that's
      // a deliberate choice (chain-traversal partial cert, sample
      // preview, locked dark cert). Respect it: opening the
      // planetarium from one of those views shouldn't spawn the
      // player we just decided to suppress on the source cert.
      _movedPlayer = null;
      _movedPlayerOriginalParent = null;
    } else if (typeof CosmicPlayer !== 'undefined') {
      // No cert in the document at all — standalone planetarium
      // page (planetarium-preview.html, etc.). Create one fresh;
      // dismissPlayer will tear it down on close.
      CosmicPlayer.inject(document.body, openOpts.meta);
      var fresh = document.body.querySelector(':scope > .cosmic-player');
      if (!fresh) return;
      fresh.classList.add('in-planetarium');
      _movedPlayer = null;
      _movedPlayerOriginalParent = null;
    } else {
      return;
    }

    if (typeof getRarityTier === 'function') {
      var _ptm = openOpts.meta;
      var _pts = (typeof RarityScore !== 'undefined' && _ptm)
        ? RarityScore.fromRecord(_ptm) : ((_ptm && _ptm.rarity_score) || 0);
      var tier = getRarityTier(_pts);
      document.body.style.setProperty('--rarity-color', tier.color);
    }

    var p = document.body.querySelector(':scope > .cosmic-player');
    if (p && typeof ResizeObserver !== 'undefined') {
      playerResizeObserver = new ResizeObserver(updateHintPosition);
      playerResizeObserver.observe(p);
    }
    [50, 200, 500, 850, 1200, 1700, 2500].forEach(function(ms) {
      setTimeout(updateHintPosition, ms);
    });
  }
  function dismissPlayer() {
    if (playerResizeObserver) {
      try { playerResizeObserver.disconnect(); } catch (_) {}
      playerResizeObserver = null;
    }

    if (_movedPlayer) {
      // Return the player to its original parent. Removing the
      // .in-planetarium class flips its CSS positioning back to the
      // host context (sticky inside .plate, or absolute inside the
      // .panel-right-has-player slot) — same DOM node, no audio
      // context teardown, music continues uninterrupted.
      _movedPlayer.classList.remove('in-planetarium');
      if (_movedPlayerOriginalParent && _movedPlayerOriginalParent.isConnected) {
        _movedPlayerOriginalParent.appendChild(_movedPlayer);
      }
      // If the original parent has been torn down (e.g., a fresh
      // cert was loaded under us), leave the player in body and
      // let cosmic-player.js's MutationObserver re-home it on the
      // next class/childList change.
      _movedPlayer = null;
      _movedPlayerOriginalParent = null;
      // Re-fire resize so the EQ canvas reads its new (smaller)
      // host width and re-centers.
      window.dispatchEvent(new Event('resize'));
    } else {
      // Player was created for the planetarium (no cert host) —
      // dismiss it.
      if (typeof CosmicPlayer !== 'undefined' && typeof CosmicPlayer.dismiss === 'function') {
        CosmicPlayer.dismiss();
      }
      var leftover = document.body.querySelectorAll(':scope > .cosmic-player');
      for (var i = 0; i < leftover.length; i++) {
        if (leftover[i].parentElement) leftover[i].parentElement.removeChild(leftover[i]);
      }
    }

    document.body.style.removeProperty('--rarity-color');
  }

  // ─── Public API ───
  function open(opts) {
    opts = opts || {};
    openOpts = opts;
    if (typeof Starfield === 'undefined' || !Starfield.canvas) return;
    var name = opts.name || 'Constellation';
    var hash = opts.hash || '0000000000000000';
    buildDom();
    nameEl.textContent = name;
    // Single source of truth: generateLayout drives both 2D positions
    // and edges (cert backdrop also calls this with the same seed,
    // so the patterns match). Z is added per-star from the same hash.
    var layout = generateLayout(hash, opts.size);
    stars = layout.stars;
    edges = layout.edges;
    for (var zk = 0; zk < stars.length; zk++) {
      var perRng = makeRng(strSeed(hash + ':' + zk));
      stars[zk].z = stars[zk].isHeart ? 0 : (perRng() - 0.5) * 1.2;
    }

    // Reseed the page-level starfield with the constellation's seed
    // so the bg sky is unique to this constellation. Density tuned
    // for the cosmic mode experience.
    if (typeof CosmicStarfield !== 'undefined') {
      CosmicStarfield.generate(name + ':' + hash, {
        outerCount: 220, innerCount: 140
      });
    }

    var c = cam();
    // Capture current ambient camera state as the start of the open
    // transition. The constellation eases from wherever ambient drift
    // had settled into the centered (0,0) view alongside the modal
    // fade-in. Reads as: starfield was always here, the constellation
    // emerged.
    transTo = 'cosmic';
    transStart = Date.now();
    transStartTY = c.thetaY;
    transStartTX = c.thetaX;
    transDuration = OPEN_MS;
    velY = 0; velX = 0;
    c.zoomTarget = 1.0;
    // Flip bg projection mode immediately. Page UI is still opaque at
    // t=0 so the dome→cosmic switch is hidden behind it.
    c.mode = 'cosmic';
    viewMode = 'cosmic';
    overlayAlpha = 0;
    overlayAlphaTarget = 0;

    if (modeBtns) modeBtns.forEach(function(b) { b.classList.toggle('active', b.dataset.mode === 'cosmic'); });
    if (modal) modal.classList.remove('earth-mode');
    if (hintEl) hintEl.textContent = 'drag to rotate · pinch or scroll to zoom · you are floating in space';

    currentStarIndex = (typeof opts.currentStarIndex === 'number') ? opts.currentStarIndex : -1;

    var heartRarity = (typeof opts.heartRarity === 'number') ? opts.heartRarity : 0;
    var curRarity = (typeof opts.currentRarity === 'number') ? opts.currentRarity : 0;
    heartRgbCache = spectralFor(heartRarity).color.slice();
    currentRgbCache = spectralFor(curRarity).color.slice();
    pulseRgb = brightenHex(tierColorFor(curRarity), 0.3);
    buildStarSprites();

    modal.classList.remove('player-minimal');

    document.body.classList.add('planetarium-open');
    isOpen = true;
    Starfield.setOverlay(drawConstellation);
    startTicker();

    // Restore subtle auto-drift after the open transition finishes.
    setTimeout(function() {
      if (isOpen && viewMode === 'cosmic' && !dragging) velY = 0.00055;
    }, OPEN_MS);

    // Modal fades in after the page UI fades out — sequence the
    // chrome (name, mode toggle, hint) to appear with the
    // constellation, against the now-visible cosmic backdrop.
    setTimeout(function() {
      if (!modal) return;
      modal.classList.add('visible');
      modal.setAttribute('aria-hidden', 'false');
      injectPlayer();
      rampAlpha(1);
      document.dispatchEvent(new CustomEvent('cosmic-planetarium-open', { bubbles: true }));
    }, 500);
  }

  function close() {
    if (!modal) return;

    modal.classList.remove('visible');
    modal.setAttribute('aria-hidden', 'true');
    rampAlpha(0);
    dismissPlayer();

    // Page UI fades back in over the bg starfield (still in cosmic
    // mode while the modal is fading out).
    setTimeout(function() {
      document.body.classList.remove('planetarium-open');
    }, 500);

    // Once page UI is fully restored (and hides the bg again), flip
    // the bg back to ambient. Starfield's incremental ambient drift
    // continues thetaY from wherever cosmic left off — no snap.
    setTimeout(function() {
      isOpen = false;
      stopTicker();
      Starfield.clearOverlay();
      var c = cam();
      c.mode = 'ambient';
      // Match Starfield's ambient default — bigger zoom for page bg.
      c.zoomTarget = 1.5;
      c.cyOffsetCss = 0;
      transTo = null;
      // Reseed the bg back to the page's default theme variant.
      if (typeof CosmicStarfield !== 'undefined' && Starfield.theme) {
        CosmicStarfield.generate('ambient:' + Starfield.theme, {
          outerCount: 360, innerCount: 200,
          warmFreq: Starfield.theme === 'yin' ? 0 : 0.25
        });
      }
      destroyDom();
      // Re-anchor band canvas heights. The cert's gen/machine/sky
      // bands set inline canvas.style.height at render time
      // (cssH + 'px') to override the CSS .sky-band-container canvas
      // { height: auto } rule. If the browser's compositor recycled
      // the layer during the page-UI opacity transition the inline
      // height can read as "auto" again, which then computes from
      // the canvas pixel buffer (cssH * dpr) → canvas displays at
      // ~2× its expected height with content packed at the top.
      // canvas.height itself is untouched after init, so we can
      // recompute the original cssH from canvas.height / dpr.
      var _dpr = window.devicePixelRatio || 1;
      var bandCanvases = document.querySelectorAll('.sky-band-container canvas');
      for (var bi = 0; bi < bandCanvases.length; bi++) {
        var bc = bandCanvases[bi];
        if (bc.height > 0) bc.style.height = (bc.height / _dpr) + 'px';
      }
      document.dispatchEvent(new CustomEvent('cosmic-planetarium-close', { bubbles: true }));
    }, 1100);
  }

  // Listen for player toggle events: mirror minimal class onto modal
  // (so the hint can ride above the player), refresh hint position
  // and camera y-offset to track the player's height change.
  document.addEventListener('cosmic-player-toggle', function(e) {
    var min = !!(e.detail && e.detail.minimal);
    if (modal) modal.classList.toggle('player-minimal', min);
    setTimeout(updateHintPosition, 50);
    setTimeout(updateHintPosition, 250);
    setTimeout(updateHintPosition, 600);
  });

  return { open: open, close: close, generateLayout: generateLayout };
})();
