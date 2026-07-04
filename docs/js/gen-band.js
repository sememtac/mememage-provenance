// =====================================================================
// GENERATION PARAMETERS BAND (Canvas)
//
// Background: subtle noise grain (the latent space noise that
// diffusion models begin with — creation starts from static).
// =====================================================================

function initGenBand(canvas, W, H, genParams, entropyHex, barSpec, barFragment, tierColor, rarityScore, parentId, parentHash) {
  var ctx = canvas.getContext('2d');

  // Layout
  var COL = 3, PAD = 20, GAP = 6, CELL_H = 44;

  // Entropy-seeded PRNG
  var seed = 0;
  if (entropyHex) for (var i = 0; i < 16 && i < entropyHex.length; i++) seed = (seed * 31 + entropyHex.charCodeAt(i)) & 0x7FFFFFFF;
  function rng() { seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF; return seed / 0x7FFFFFFF; }

  // Cell positions — pack cells with `span` of 1, 2, or 3 columns, then
  // PROPORTIONALLY STRETCH each row to fill the full width. Without the
  // stretch a lone span-2 cell (e.g. a single long catch-all field) fills only
  // 2/3 of the row, leaving a ragged gap. With it, every row reaches both edges
  // with cells sized in proportion to their spans — an even grid no matter what
  // field mix cert-renderer hands us.
  var cells = new Array(genParams.length);
  var gridW = W - PAD * 2;
  // 1. Group params into rows by the same wrap rule.
  var rowsArr = [];
  var cur = [], curSpan = 0;
  for (var ci = 0; ci < genParams.length; ci++) {
    var span = Math.min(COL, Math.max(1, genParams[ci].span || 1));
    if (curSpan + span > COL) { rowsArr.push(cur); cur = []; curSpan = 0; }
    cur.push({ idx: ci, span: span });
    curSpan += span;
    if (curSpan >= COL) { rowsArr.push(cur); cur = []; curSpan = 0; }
  }
  if (cur.length) rowsArr.push(cur);
  // 2. Lay each row out across the full width, sized by span share.
  var rows = rowsArr.length;
  var gridH = rows * CELL_H + Math.max(0, rows - 1) * GAP;
  var gridY = Math.floor((H - gridH) / 2);
  for (var r = 0; r < rows; r++) {
    var rc = rowsArr[r], n = rc.length, sumSpan = 0;
    for (var k = 0; k < n; k++) sumSpan += rc[k].span;
    var avail = gridW - (n - 1) * GAP;   // width left for cells after the gaps
    var x = PAD;
    for (var j = 0; j < n; j++) {
      // Last cell in the row absorbs rounding so it lands flush on the edge.
      var cw = (j === n - 1) ? (PAD + gridW - x)
                             : Math.round(avail * rc[j].span / sumSpan);
      cells[rc[j].idx] = {
        x: x, y: gridY + r * (CELL_H + GAP), w: cw, h: CELL_H, hover: 0
      };
      x += cw + GAP;
    }
  }

  // Mouse
  var mx = -1, my = -1;
  var mxPrev = -1, myPrev = -1;
  var mouseDelta = 0;        // accumulated magnitude this frame
  var swimAccum = 0;         // accumulated rotation driver (radians)
  var swimVel = 0;           // current rotational velocity (momentum)
  var swimDirX = 0, swimDirY = 0; // normalized movement direction (smoothed)
  var cursorGlow = 0;        // 0 = invisible, 1 = full brightness — fades in on move, out on stop
  canvas.addEventListener('mousemove', function(e) {
    var r = canvas.getBoundingClientRect();
    mx = (e.clientX - r.left) / r.width * W;
    my = (e.clientY - r.top) / r.height * H;
    if (mxPrev >= 0) {
      var ddx = mx - mxPrev, ddy = my - myPrev;
      mouseDelta += Math.sqrt(ddx * ddx + ddy * ddy);
    }
    mxPrev = mx; myPrev = my;
  });
  canvas.addEventListener('mouseleave', function() {
    mx = -1; my = -1; mxPrev = -1; myPrev = -1; mouseDelta = 0;
  });

  // Parse tier color into RGB for background tinting
  function hexToRgb(hex) {
    var r = parseInt(hex.slice(1, 3), 16), g = parseInt(hex.slice(3, 5), 16), b = parseInt(hex.slice(5, 7), 16);
    return [r, g, b];
  }
  var tc = hexToRgb(tierColor || '#a0a0a0');
  // Very dark shade of the rarity color
  var bgR = Math.floor(tc[0] * 0.14), bgG = Math.floor(tc[1] * 0.14), bgB = Math.floor(tc[2] * 0.14);
  var bgMidR = Math.floor(tc[0] * 0.19), bgMidG = Math.floor(tc[1] * 0.19), bgMidB = Math.floor(tc[2] * 0.19);

  // Cell colors — shared rarity tint (variant C) from cert-renderer.
  var _cc = rarityCellColors(tierColor);
  var CELL_FILL_BASE = _cc.base, CELL_STROKE_BASE = _cc.baseStroke;
  var CELL_FILL_HOVER = _cc.hoverFill, CELL_STROKE_HOVER = _cc.hoverStroke;

  // Voronoi noise at low resolution, upscaled
  var VOR_SCALE = 4;
  var VOR_W = Math.ceil(W / VOR_SCALE);
  var VOR_H = Math.ceil(H / VOR_SCALE);
  var vorCanvas = document.createElement('canvas');
  vorCanvas.width = VOR_W; vorCanvas.height = VOR_H;
  var vorCtx = vorCanvas.getContext('2d');

  // Seed points
  // --- Data-driven voronoi parameters ---
  // Derive from seed (unique per image) and rarity score
  var _seedVal = 0;
  for (var gpi = 0; gpi < genParams.length; gpi++) {
    if (genParams[gpi].l.toLowerCase() === 'seed') {
      _seedVal = parseInt(genParams[gpi].v, 10) || 0;
      break;
    }
  }

  // Extract 4 independent values from the seed's digits
  var seedStr = '' + Math.abs(_seedVal);
  while (seedStr.length < 10) seedStr = '0' + seedStr;
  var s1 = parseInt(seedStr.slice(0, 3), 10) / 999;  // 0-1
  var s2 = parseInt(seedStr.slice(3, 6), 10) / 999;  // 0-1
  var s3 = parseInt(seedStr.slice(6, 8), 10) / 99;   // 0-1
  var s4 = parseInt(seedStr.slice(8, 10), 10) / 99;   // 0-1

  // Rarity normalized (must be computed before use)
  var rarityNorm = Math.min(1, (rarityScore || 0) / 80);

  // NUM_POINTS ← seed (base identity) + rarity (density from drop rate curve)
  var seedBase = 10 + Math.floor(s1 * 12); // 10-22 from seed (identity)
  var rarityBonus = Math.floor(rarityNorm * rarityNorm * 30); // 0 (common) → 30 (legendary)
  var NUM_POINTS = seedBase + rarityBonus;

  // Velocity ← seed digits (0.1-0.5, each image drifts at its own pace)
  var baseVelocity = 0.1 + s2 * 0.4;

  // Edge sharpness ← seed (s3) sets base thickness, rarity sharpens further
  var baseEdge = 22 + s3 * 8; // 22-30 base (thinner borders)
  var EDGE_DIVISOR = baseEdge - rarityNorm * 10; // common: 22-30 (very thin), legendary: 12-20 (defined)

  // Repulse strength ← rarity (rarer = tissue reacts more dramatically)
  var REPULSE_STRENGTH = 4 + rarityNorm * 6; // 4 (gentle) → 10 (dramatic)

  // Cell brightness ← rarity (rarer = more luminous membrane, but always subtle)
  var vorBrightMult = 0.15 + rarityNorm * 0.15; // 0.15 (visible) → 0.30 (clear)

  var vorPoints = [];
  for (var vp = 0; vp < NUM_POINTS; vp++) {
    vorPoints.push({
      x: rng() * VOR_W, y: rng() * VOR_H,
      vx: (rng() - 0.5) * baseVelocity,
      vy: (rng() - 0.5) * baseVelocity * 0.7
    });
  }

  var vorFrame = 0;
  var subPoints = []; // shared between renderVoronoi and tick
  var currentVelMag = 0; // shared velocity magnitude for arc/glow suppression
  var subPresence = 0;   // 0 = gone, 1 = fully present — smooth fade in/out
  var PRESENCE_FADE_IN = 0.06;
  var PRESENCE_FADE_OUT = 0.04 - rarityNorm * 0.03; // common: 0.04 (quick), legendary: 0.01 (lingers)

  // Cascade firing state — each subcell has an activation level that decays
  var SUB_MAX = 15; // max possible subcells
  var subFire = [];  // 0 = dormant, 1 = fully fired
  for (var sf = 0; sf < SUB_MAX; sf++) subFire.push(0);
  var cascadePending = []; // queue: [index, framesUntilFire]
  var FIRE_DECAY = 0.06 - rarityNorm * 0.045; // common: 0.06 (brief flash) → legendary: 0.015 (long burn)
  var ARC_DIST_MAX = 30;   // max distance (voronoi-space) for synaptic connection
  var cascadeFired = false; // true once cascade triggers per pause — resets on movement

  // --- Regional breathing: overlapping zones with independent slow cycles ---
  var BREATH_ZONES = [];
  var numZones = 3 + Math.floor(rng() * 3); // 3-5 zones
  for (var bz = 0; bz < numZones; bz++) {
    BREATH_ZONES.push({
      cx: rng() * VOR_W,               // center x (voronoi space)
      cy: rng() * VOR_H,               // center y
      radius: VOR_W * (0.25 + rng() * 0.3), // coverage radius
      period: 8 + rng() * 10,          // 8-18 second cycle
      phase: rng() * 6.2832,           // random start phase
      amp: 0.03 + rng() * 0.03         // 0.03-0.06 brightness boost at peak
    });
  }

  // Precompute per-pixel gaussian falloff for each breath zone. The zones
  // are static (cx/cy/radius set once), so the spatial falloff is constant —
  // only the per-frame intensity changes. Lifts ~67k Math.exp() calls per
  // frame out of the inner pixel loop.
  var breathFalloffs = [];
  for (var bzInit = 0; bzInit < BREATH_ZONES.length; bzInit++) {
    var bzI = BREATH_ZONES[bzInit];
    var falloff = new Float32Array(VOR_W * VOR_H);
    var inv = 1 / (bzI.radius * bzI.radius * 0.5);
    for (var byInit = 0; byInit < VOR_H; byInit++) {
      for (var bxInit = 0; bxInit < VOR_W; bxInit++) {
        var bdxI = bxInit - bzI.cx, bdyI = byInit - bzI.cy;
        falloff[byInit * VOR_W + bxInit] = Math.exp(-(bdxI * bdxI + bdyI * bdyI) * inv);
      }
    }
    breathFalloffs.push(falloff);
  }

  // --- Spontaneous firing: random edge flickers ---
  var spontFire = []; // per voronoi point: 0 = quiet, >0 = flicker brightness
  for (var sfi = 0; sfi < NUM_POINTS; sfi++) spontFire.push(0);
  var SPONT_DECAY = 0.06;   // fast fade
  var SPONT_CHANCE = 0.025;  // ~1.5 flickers/sec at 60fps across all points

  // Mouse position in voronoi space (smoothed for elegant response)
  var vorMx = -1, vorMy = -1;
  var vorMxSmooth = -1, vorMySmooth = -1;
  var REPULSE_RADIUS = 22;
  var SMOOTH_FACTOR = 0.08;

  // Reusable per-frame warp lookup arrays (prevents GC churn).
  var warpRowDx = new Float32Array(VOR_H);
  var warpColDy = new Float32Array(VOR_W);

  function renderVoronoi() {
    // Move points
    for (var vp = 0; vp < vorPoints.length; vp++) {
      var p = vorPoints[vp];
      p.x += p.vx; p.y += p.vy;
      if (p.x < 0 || p.x > VOR_W) p.vx = -p.vx;
      if (p.y < 0 || p.y > VOR_H) p.vy = -p.vy;
    }

    // Spontaneous firing: random neurons flicker
    if (Math.random() < SPONT_CHANCE) {
      var target = Math.floor(Math.random() * NUM_POINTS);
      if (spontFire[target] < 0.1) spontFire[target] = 0.5 + Math.random() * 0.5;
    }
    for (var sfd = 0; sfd < NUM_POINTS; sfd++) {
      if (spontFire[sfd] > 0) { spontFire[sfd] -= SPONT_DECAY; if (spontFire[sfd] < 0) spontFire[sfd] = 0; }
    }

    // Precompute breathing values for this frame (seconds-based)
    var breathTime = vorFrame / 60; // approximate seconds
    var breathVals = [];
    for (var bzi = 0; bzi < BREATH_ZONES.length; bzi++) {
      var bz = BREATH_ZONES[bzi];
      var cycle = Math.sin(breathTime * 6.2832 / bz.period + bz.phase);
      var intensity = (cycle * 0.5 + 0.5); // 0-1
      breathVals.push(intensity * bz.amp);
    }

    // Smooth mouse position for elegant tissue response
    vorMx = mx >= 0 ? mx / VOR_SCALE : -1;
    vorMy = my >= 0 ? my / VOR_SCALE : -1;
    if (vorMx >= 0) {
      if (vorMxSmooth < 0) { vorMxSmooth = vorMx; vorMySmooth = vorMy; }
      vorMxSmooth += (vorMx - vorMxSmooth) * SMOOTH_FACTOR;
      vorMySmooth += (vorMy - vorMySmooth) * SMOOTH_FACTOR;
    } else if (subPresence < 0.005) {
      // Only kill smooth position after presence has fully faded
      vorMxSmooth = -1; vorMySmooth = -1;
    }

    var adjX = [], adjY = [];
    for (var vp = 0; vp < vorPoints.length; vp++) {
      var p = vorPoints[vp];
      if (vorMxSmooth >= 0) {
        var dmx = p.x - vorMxSmooth, dmy = p.y - vorMySmooth;
        var dist = Math.sqrt(dmx * dmx + dmy * dmy);
        if (dist < REPULSE_RADIUS && dist > 0.5) {
          var force = (1 - dist / REPULSE_RADIUS) * REPULSE_STRENGTH;
          adjX.push(p.x + (dmx / dist) * force);
          adjY.push(p.y + (dmy / dist) * force);
        } else {
          adjX.push(p.x);
          adjY.push(p.y);
        }
      } else {
        adjX.push(p.x);
        adjY.push(p.y);
      }
    }

    // Domain warp: sine offset makes cell borders organic
    var warpFreq = 0.12 + s4 * 0.1;
    var warpAmp = 0.6 + rarityNorm * 0.6;
    var warpPhase = vorFrame * 0.002;

    // Subdivision: place points around cursor to split existing cells
    // Driven by mouse movement delta — movement makes subcells swim
    subPoints = [];
    var subMaxR = 15 + rarityNorm * 8;

    // Convert accumulated mouse delta into rotational velocity
    var deltaScale = 0.04;   // px of mouse movement → radians of swim (violent)
    swimVel += mouseDelta * deltaScale;
    swimVel *= 0.88;         // slower decay — longer coast, more momentum
    swimAccum += swimVel * 0.016; // integrate velocity into rotation
    // Cursor glow: fade in when moving, fade out when stopped
    if (mouseDelta > 0.5) {
      cursorGlow = Math.min(1, cursorGlow + 0.08); // fade in ~12 frames
    } else {
      cursorGlow = Math.max(0, cursorGlow - 0.02); // fade out ~50 frames (slow)
    }
    mouseDelta = 0;          // consume this frame's delta

    if (vorMxSmooth >= 0) {
      var subCount = 5 + Math.floor(rarityNorm * 5);
      var subMinR = 3;
      var t = swimAccum; // rotation driven by mouse movement, not clock
      var velMag = Math.abs(swimVel);
      currentVelMag = velMag;
      for (var si = 0; si < subCount; si++) {
        var baseAngle = si * 2.39996; // golden angle base
        // Wobble scales hard with velocity — violent displacement on fast moves
        var wobbleAmp = Math.min(1.5, velMag * 1.2 + 0.05);
        var angleWobble = Math.sin(t * (0.8 + si * 0.3)) * wobbleAmp;
        var radiusWobble = Math.sin(t * (0.5 + si * 0.2) + si) * (wobbleAmp * 0.7);
        var ga = baseAngle + t * (0.5 + si * 0.12) + angleWobble;
        var baseR = subMinR + (subMaxR - subMinR) * Math.sqrt((si + 1) / subCount);
        // Radius contracts with velocity — cells collapse inward on fast moves
        var velCollapse = 1 / (1 + velMag * 0.6);
        var r = baseR * (1 + radiusWobble) * velCollapse;
        var spx = vorMxSmooth + Math.cos(ga) * r;
        var spy = vorMySmooth + Math.sin(ga) * r;
        adjX.push(spx);
        adjY.push(spy);
        subPoints.push({x: spx * VOR_SCALE, y: spy * VOR_SCALE});
      }
    }

    // --- Cascade firing: stillness triggers once per pause ---
    if (velMag > 0.3) cascadeFired = false; // reset when sifting resumes
    if (subPoints.length > 0 && velMag < 0.15 && vorMxSmooth >= 0 && !cascadeFired) {
      cascadeFired = true;
      // Find subcell closest to cursor — it fires first
      var closestSub = 0, closestDist = 99999;
      for (var csi = 0; csi < subPoints.length; csi++) {
        var csdx = subPoints[csi].x / VOR_SCALE - vorMxSmooth;
        var csdy = subPoints[csi].y / VOR_SCALE - vorMySmooth;
        var csd = csdx * csdx + csdy * csdy;
        if (csd < closestDist) { closestDist = csd; closestSub = csi; }
      }
      // Fire the closest if it's not already lit
      if (subFire[closestSub] < 0.3) {
        subFire[closestSub] = 1;
        // Queue neighbors to fire in cascade with staggered delay
        for (var qi = 0; qi < subPoints.length; qi++) {
          if (qi === closestSub) continue;
          var qdx = subPoints[qi].x - subPoints[closestSub].x;
          var qdy = subPoints[qi].y - subPoints[closestSub].y;
          var qdist = Math.sqrt(qdx * qdx + qdy * qdy) / VOR_SCALE;
          if (qdist < ARC_DIST_MAX) {
            var delay = Math.floor(qdist * 1.5) + 2; // closer = fires sooner
            cascadePending.push([qi, delay]);
          }
        }
      }
    }

    // Process cascade queue
    var nextPending = [];
    for (var pi = 0; pi < cascadePending.length; pi++) {
      cascadePending[pi][1]--;
      if (cascadePending[pi][1] <= 0) {
        var idx = cascadePending[pi][0];
        if (idx < subPoints.length && subFire[idx] < 0.5) subFire[idx] = 1;
      } else {
        nextPending.push(cascadePending[pi]);
      }
    }
    cascadePending = nextPending;

    // Decay all firing states
    for (var fi = 0; fi < SUB_MAX; fi++) {
      if (subFire[fi] > 0) {
        subFire[fi] -= FIRE_DECAY;
        if (subFire[fi] < 0) subFire[fi] = 0;
      }
    }

    var totalPoints = adjX.length; // includes base + subdivision points

    var imgData = vorCtx.createImageData(VOR_W, VOR_H);
    var px = imgData.data;

    // Warp offset depends only on (vy, warpPhase) for wx and (vx, warpPhase)
    // for wy — precompute once per frame instead of VOR_W*VOR_H times.
    for (var wpy = 0; wpy < VOR_H; wpy++) {
      warpRowDx[wpy] = Math.sin(wpy * warpFreq + warpPhase) * warpAmp;
    }
    var _warpPhase13 = warpPhase * 1.3;
    for (var wpx = 0; wpx < VOR_W; wpx++) {
      warpColDy[wpx] = Math.sin(wpx * warpFreq + _warpPhase13) * warpAmp;
    }

    for (var vy = 0; vy < VOR_H; vy++) {
      var _rowDx = warpRowDx[vy];
      for (var vx = 0; vx < VOR_W; vx++) {
        var wx = vx + _rowDx;
        var wy = vy + warpColDy[vx];
        var d1 = 99999, d2 = 99999, nearest = 0;
        for (var vp = 0; vp < totalPoints; vp++) {
          var dx = wx - adjX[vp], dy = wy - adjY[vp];
          var d = dx * dx + dy * dy;
          if (d < d1) { d2 = d1; d1 = d; nearest = vp; }
          else if (d < d2) { d2 = d; }
        }
        var sd1 = Math.sqrt(d1), sd2 = Math.sqrt(d2);
        var edge = sd2 - sd1;
        var edgeVal = Math.max(0, 1 - edge / EDGE_DIVISOR);

        // Edge-lit: bright edges, dark interiors (default state)
        var sharpEdge = edgeVal * edgeVal * edgeVal;
        var softAura = edgeVal * 0.3;
        var edgeBright = sharpEdge + softAura;

        // Cell-lit: bright interiors, dark edges (cursor area)
        var cellDepth = sd2 > 0 ? sd1 / sd2 : 0;
        var cellBright = (1 - cellDepth) * 0.5 * (1 - edgeVal * 0.6);

        // Blend based on cursor proximity × cursor glow (fades in on move, out on stop)
        var blend = 0; // 0 = cell-lit (far), 1 = edge-lit (near)
        if (vorMxSmooth >= 0 && cursorGlow > 0.01) {
          var cdx = vx - vorMxSmooth, cdy = vy - vorMySmooth;
          var cdist = Math.sqrt(cdx * cdx + cdy * cdy);
          var blendRadius = subMaxR + 5;
          blend = Math.max(0, 1 - cdist / blendRadius);
          blend = blend * blend * cursorGlow; // smooth falloff × glow opacity
        }

        var brightness = cellBright * (1 - blend) + edgeBright * blend;

        // Regional breathing — overlapping gaussian zones pulse slowly.
        // Spatial falloff precomputed at init; only the per-frame intensity
        // (breathVals) changes.
        var breathBoost = 0;
        var _pxIdx = vy * VOR_W + vx;
        for (var bzi = 0; bzi < BREATH_ZONES.length; bzi++) {
          breathBoost += breathVals[bzi] * breathFalloffs[bzi][_pxIdx];
        }

        // Spontaneous edge flicker — nearest base point's fire state boosts edges
        var spontBoost = 0;
        if (nearest < NUM_POINTS && spontFire[nearest] > 0) {
          spontBoost = spontFire[nearest] * edgeVal * 0.12;
        }

        brightness += breathBoost + spontBoost;

        var idx = _pxIdx * 4;
        px[idx] = Math.floor(bgR + tc[0] * brightness * vorBrightMult);
        px[idx + 1] = Math.floor(bgG + tc[1] * brightness * vorBrightMult);
        px[idx + 2] = Math.floor(bgB + tc[2] * brightness * vorBrightMult);
        px[idx + 3] = 255;
      }
    }
    vorCtx.putImageData(imgData, 0, 0);
  }

  renderVoronoi();

  // Animation loop — continuous for smooth hover, but lightweight
  var visible = true;
  if (typeof IntersectionObserver !== 'undefined') {
    new IntersectionObserver(function(entries) { visible = entries[0].isIntersecting; }, {threshold: 0}).observe(canvas);
  }

  function tick() {
    if (!visible) { setTimeout(tick, 200); return; }

    // Voronoi background (every frame during hover for responsive tissue, every 3 frames idle)
    vorFrame++;
    if (mx >= 0 || subPresence > 0.01 || vorFrame % 3 === 0) renderVoronoi();
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(vorCanvas, 0, 0, W, H);

    // Subcell presence — ramps up on hover, fades gracefully on leave
    if (mx >= 0) {
      subPresence += (1 - subPresence) * PRESENCE_FADE_IN;
    } else {
      subPresence -= subPresence * PRESENCE_FADE_OUT;
      if (subPresence < 0.005) subPresence = 0;
    }

    // Synaptic arcs + neuron glow — driven by fire state, not stillness
    // Stillness gates whether NEW firing can begin (in renderVoronoi).
    // Once fired, the neurons fade on their own via FIRE_DECAY (rarity-driven).
    // subPresence handles graceful disappearance when cursor leaves entirely.
    if (subPoints.length > 1 && subPresence > 0.01) {
      var arcThresh = ARC_DIST_MAX * VOR_SCALE; // in canvas-space
      for (var ai = 0; ai < subPoints.length; ai++) {
        for (var aj = ai + 1; aj < subPoints.length; aj++) {
          var adx = subPoints[aj].x - subPoints[ai].x;
          var ady = subPoints[aj].y - subPoints[ai].y;
          var adist = Math.sqrt(adx * adx + ady * ady);
          if (adist > arcThresh) continue;
          // Arc brightness = max of both endpoints' fire state × proximity
          var arcFire = Math.max(subFire[ai] || 0, subFire[aj] || 0);
          var proxFade = 1 - adist / arcThresh;
          var arcAlpha = arcFire * proxFade * 0.2 * subPresence;
          if (arcAlpha < 0.005) continue;
          // Curved arc — control point offset perpendicular to midpoint
          var midX = (subPoints[ai].x + subPoints[aj].x) / 2;
          var midY = (subPoints[ai].y + subPoints[aj].y) / 2;
          var perpX = -ady / adist * (6 + arcFire * 4);
          var perpY = adx / adist * (6 + arcFire * 4);
          ctx.strokeStyle = 'rgba(' + tc[0] + ',' + tc[1] + ',' + tc[2] + ',' + arcAlpha.toFixed(3) + ')';
          ctx.lineWidth = 0.3 + arcFire * 0.7;
          ctx.beginPath();
          ctx.moveTo(subPoints[ai].x, subPoints[ai].y);
          ctx.quadraticCurveTo(midX + perpX, midY + perpY, subPoints[aj].x, subPoints[aj].y);
          ctx.stroke();
        }
      }

      // Neuron glow — each subcell pulses with its firing state
      for (var ni = 0; ni < subPoints.length; ni++) {
        var fire = subFire[ni] || 0;
        var baseAlpha = fire * 0.08 * subPresence;
        var fireAlpha = fire * 0.25 * subPresence;
        var totalAlpha = baseAlpha + fireAlpha;
        var radius = 1.2 + fire * 1.5;
        // Outer glow (fired neurons radiate)
        if (fire > 0.1) {
          var glowR = radius + fire * 4;
          var grad = ctx.createRadialGradient(subPoints[ni].x, subPoints[ni].y, radius, subPoints[ni].x, subPoints[ni].y, glowR);
          grad.addColorStop(0, 'rgba(' + tc[0] + ',' + tc[1] + ',' + tc[2] + ',' + (fire * 0.15 * subPresence).toFixed(3) + ')');
          grad.addColorStop(1, 'rgba(' + tc[0] + ',' + tc[1] + ',' + tc[2] + ',0)');
          ctx.fillStyle = grad;
          ctx.beginPath();
          ctx.arc(subPoints[ni].x, subPoints[ni].y, glowR, 0, 6.2832);
          ctx.fill();
        }
        // Core dot
        ctx.fillStyle = 'rgba(' + tc[0] + ',' + tc[1] + ',' + tc[2] + ',' + totalAlpha.toFixed(3) + ')';
        ctx.beginPath();
        ctx.arc(subPoints[ni].x, subPoints[ni].y, radius, 0, 6.2832);
        ctx.fill();
      }
    }

    ctx.textAlign = 'center';

    for (var ci = 0; ci < cells.length; ci++) {
      var c = cells[ci];
      var hit = mx >= c.x && mx <= c.x + c.w && my >= c.y && my <= c.y + c.h;
      c.hover += hit ? 0.03 : -0.02;
      if (c.hover < 0) c.hover = 0;
      if (c.hover > 1) c.hover = 1;
      var h = c.hover;

      // Default cell (variant C: rarity-tinted, low intensity)
      ctx.fillStyle = CELL_FILL_BASE;
      ctx.beginPath(); ctx.roundRect(c.x, c.y, c.w, c.h, 6); ctx.fill();
      ctx.strokeStyle = CELL_STROKE_BASE;
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.roundRect(c.x, c.y, c.w, c.h, 6); ctx.stroke();

      // Hover additive
      if (h > 0.01) {
        ctx.fillStyle = CELL_FILL_HOVER(h);
        ctx.beginPath(); ctx.roundRect(c.x, c.y, c.w, c.h, 6); ctx.fill();
        ctx.strokeStyle = CELL_STROKE_HOVER(h);
        ctx.lineWidth = 1 + h * 0.5;
        ctx.beginPath(); ctx.roundRect(c.x, c.y, c.w, c.h, 6); ctx.stroke();
      }

      // Label
      ctx.font = '500 8px "JetBrains Mono", monospace';
      ctx.fillStyle = 'rgba(255,255,255,' + (0.45 + h * 0.35) + ')';
      ctx.fillText(genParams[ci].l.toUpperCase(), c.x + c.w / 2, c.y + 18);

      // Value
      ctx.font = '400 11px "JetBrains Mono", monospace';
      ctx.fillStyle = 'rgba(255,255,255,' + (0.7 + h * 0.3) + ')';
      var val = genParams[ci].v;
      if (ctx.measureText(val).width > c.w - 24) {
        while (val.length > 3 && ctx.measureText(val + '...').width > c.w - 24) val = val.slice(0, -1);
        val += '...';
      }
      ctx.fillText(val, c.x + c.w / 2, c.y + 34);
    }

    ctx.textAlign = 'left';
    setTimeout(tick, 16);
  }

  tick();

  // Save metadata
  if (typeof enableCanvasSave === 'function') {
    var genJson = {};
    for (var gi = 0; gi < genParams.length; gi++) {
      var key = genParams[gi].l.toLowerCase().replace(/ /g, '_');
      var rawVal = genParams[gi].v;
      if (key === 'size' && rawVal.indexOf('\u00d7') >= 0) {
        var dims = rawVal.split('\u00d7');
        genJson['width'] = parseInt(dims[0].trim(), 10);
        genJson['height'] = parseInt(dims[1].trim(), 10);
        continue;
      }
      var numVal = parseFloat(rawVal);
      genJson[key] = (!isNaN(numVal) && numVal.toString() === rawVal) ? numVal : rawVal;
    }
    var saveMeta = { generation_params: JSON.stringify(genJson), Software: 'Mememage' };
    if (barSpec) saveMeta.bar_spec = JSON.stringify(barSpec);
    if (barFragment !== undefined && barFragment !== null) saveMeta.bar_payload_1 = barFragment;
    if (parentId)   saveMeta.parent_id   = parentId;
    if (parentHash) saveMeta.parent_hash = parentHash;
    saveMeta.fragment_id = 'gen';
    var fragBytes = (typeof fragmentBytes === 'function') ? fragmentBytes(barFragment, FRAGMENT_TAG_GEN) : null;
    enableCanvasSave(canvas, saveMeta, fragBytes);
  }
}
