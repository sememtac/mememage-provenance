// =====================================================================
// MACHINE VITALS BAND (Canvas)
//
// Background: circuit traces (thin lines forming a PCB-like grid —
// the silicon pathways the machine thinks through).
// Includes kernel entropy cell, machine/entropy traits, and sigil.
// =====================================================================

function initMachineBand(canvas, W, H, machineData, entropyHex, fingerprint, barSpec, barFragment, machineTraits, entropyTraits, sigilData, tierColor, aboutText, rarityScore, parentId, parentHash) {
  var ctx = canvas.getContext('2d');

  // Cell colors — shared rarity tint (variant C) from cert-renderer.
  var _cc = rarityCellColors(tierColor);
  var CELL_FILL_BASE = _cc.base, CELL_STROKE_BASE = _cc.baseStroke;
  var CELL_FILL_HOVER = _cc.hoverFill, CELL_STROKE_HOVER = _cc.hoverStroke;

  // Layout
  var COL = 3, PAD = 20, GAP = 6, CELL_H = 38;
  var LABEL_SIZE = 7, VALUE_SIZE = 9;
  var cellW = Math.floor((W - PAD * 2 - GAP * (COL - 1)) / COL);

  // Entropy-seeded PRNG
  var seed = 73;
  if (entropyHex) for (var i = 0; i < 16 && i < entropyHex.length; i++) seed = (seed * 37 + entropyHex.charCodeAt(i)) & 0x7FFFFFFF;
  function rng() { seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF; return seed / 0x7FFFFFFF; }

  // Cell positions — span-based packing, supports fractional spans.
  // Each row must sum to COL (3). A span of 1 = 1/3 row width,
  // 1.5 = half, 2 = two-thirds, 3 = full width.
  //
  // First pass: group cells into rows.
  var rowList = [];
  var curRow = [];
  var curSum = 0;
  var EPS = 0.001;
  for (var mi = 0; mi < machineData.length; mi++) {
    var s = Math.min(COL, Math.max(0.5, machineData[mi].span || 1));
    if (curSum + s > COL + EPS) {
      if (curRow.length) rowList.push(curRow);
      curRow = [];
      curSum = 0;
    }
    curRow.push({ l: machineData[mi].l, v: machineData[mi].v, span: s });
    curSum += s;
    if (Math.abs(curSum - COL) < EPS) {
      rowList.push(curRow);
      curRow = [];
      curSum = 0;
    }
  }
  if (curRow.length) rowList.push(curRow);

  // Second pass: compute per-cell x/y/w. Row width minus per-gap
  // between cells; each cell gets (span/COL) fraction of the content.
  var cells = [];
  for (var ri = 0; ri < rowList.length; ri++) {
    var r = rowList[ri];
    var availW = W - PAD * 2 - (r.length - 1) * GAP;
    var x = PAD;
    var y = PAD + ri * (CELL_H + GAP);
    for (var rci = 0; rci < r.length; rci++) {
      var cw = availW * (r[rci].span / COL);
      cells.push({
        x: x, y: y, w: cw, h: CELL_H,
        l: r[rci].l, v: r[rci].v, hover: 0
      });
      x += cw + GAP;
    }
  }
  // gridBottom = bottom edge of last cell + gap
  var lastCellBottom = cells.length > 0 ? cells[cells.length - 1].y + CELL_H : PAD;
  var gridBottom = lastCellBottom + GAP;

  // Entropy cell
  var entropyCell = null;
  if (entropyHex) {
    entropyCell = { x: PAD, y: gridBottom, w: W - PAD * 2, h: 48, hover: 0 };
  }
  // Identity + traits cell (below entropy)
  var allTraitsList = (machineTraits || []).concat(entropyTraits || []);
  var hasBottomCell = fingerprint || allTraitsList.length || sigilData;
  var bottomCell = null;
  if (hasBottomCell) {
    var bcY = (entropyCell ? entropyCell.y + entropyCell.h : gridBottom) + GAP;
    var bcH = 14;
    if (fingerprint) bcH += 12;
    if (allTraitsList.length) bcH += 12;
    if (sigilData) bcH += 12;
    bottomCell = { x: PAD, y: bcY, w: W - PAD * 2, h: bcH, hover: 0 };
  }

  // Precompute font fits for static text (FP, traits, sigil, entropy hex).
  // These strings are fixed at init, so the shrink-to-fit measureText
  // loops don't need to run every frame. Cached font strings are looked
  // up in the tick instead.
  function _fitFont(text, weight, maxW, maxPx, minPx) {
    var size = maxPx;
    ctx.font = weight + ' ' + size + 'px "JetBrains Mono", monospace';
    while (ctx.measureText(text).width > maxW && size > minPx) {
      size -= 0.5;
      ctx.font = weight + ' ' + size + 'px "JetBrains Mono", monospace';
    }
    return weight + ' ' + size + 'px "JetBrains Mono", monospace';
  }
  var _fpText = fingerprint ? 'FP: ' + fingerprint : '';
  var _traitText = allTraitsList.length ? allTraitsList.join(' \u00b7 ') : '';
  var _sigilText = sigilData ? '\u2728 Sigil \u2014 0xAD4E found in entropy' : '';
  var _fpFont = '', _traitFont = '', _sigilFont = '';
  if (bottomCell) {
    var _bottomMaxW = bottomCell.w - 16;
    if (fingerprint) _fpFont = _fitFont(_fpText, '300', _bottomMaxW, 7, 5);
    if (allTraitsList.length) _traitFont = _fitFont(_traitText, '400', _bottomMaxW, 8, 5);
    if (sigilData) _sigilFont = _fitFont(_sigilText, 'italic 400', _bottomMaxW, 8, 5);
  }
  var _entropyLine1 = '', _entropyLine2 = '', _entropyFont = '';
  if (entropyCell && entropyHex) {
    var _eClean = entropyHex.replace(/\s/g, '');
    var _eHalf = Math.ceil(_eClean.length / 2);
    _entropyLine1 = _eClean.slice(0, _eHalf).split('').join(' ');
    _entropyLine2 = _eClean.slice(_eHalf).split('').join(' ');
    var _eAvailW = entropyCell.w - 24;
    var _eTestLine = _entropyLine1.length > _entropyLine2.length ? _entropyLine1 : _entropyLine2;
    _entropyFont = _fitFont(_eTestLine, '300', _eAvailW, 8, 4);
  }

  // Mouse + touch. Cursor position drives the drip system below.
  var mx = -1, my = -1, cursorActive = false;
  function setCursor(cx, cy) {
    var r = canvas.getBoundingClientRect();
    mx = (cx - r.left) / r.width * W;
    my = (cy - r.top) / r.height * H;
    cursorActive = true;
  }
  canvas.addEventListener('mousemove', function(e) { setCursor(e.clientX, e.clientY); });
  canvas.addEventListener('mouseleave', function() { mx = -1; my = -1; cursorActive = false; });
  canvas.addEventListener('touchmove', function(e) {
    if (!e.touches || !e.touches[0]) return;
    setCursor(e.touches[0].clientX, e.touches[0].clientY);
  }, { passive: true });
  canvas.addEventListener('touchstart', function(e) {
    if (!e.touches || !e.touches[0]) return;
    setCursor(e.touches[0].clientX, e.touches[0].clientY);
  }, { passive: true });
  canvas.addEventListener('touchend', function() { cursorActive = false; });

  // Cursor drips — when the cursor hovers over the canvas, characters
  // spawn at that point and fall straight down. Reads like the cursor
  // punctured the matrix and code is leaking from the wound. Drips
  // continue falling after the cursor leaves; only spawning stops.
  var cursorDrips = [];
  var dripsMax = 400;
  var spawnThrottle = 0;
  function spawnDrip() {
    if (!cursorActive) return;
    if (mx < 0 || my < 0 || mx > W || my > H) return;
    // One char every other frame — reads as a single regular stream
    // rather than a dense cascade.
    spawnThrottle++;
    if (spawnThrottle % 2 !== 0) return;
    // Match the background rain's fall-speed envelope so the drip
    // reads as native to the column field (baseSpeed + variance,
    // both rarity-scaled — see rainCols below).
    var dripSpeed = baseSpeed + Math.random() * speedVariance;
    cursorDrips.push({
      x: mx + (Math.random() - 0.5) * 2,  // slight horizontal jitter
      y: my,
      char: rainSource[Math.floor(Math.random() * rainSource.length)],
      speed: dripSpeed,
      life: 1.0,
      fadeRate: 0.006 + Math.random() * 0.008,
      changeTimer: 0,
      changeRate: 4 + Math.floor(Math.random() * 5)
    });
    if (cursorDrips.length > dripsMax) cursorDrips.splice(0, cursorDrips.length - dripsMax);
  }

  function drawCursorDrips() {
    spawnDrip();
    if (cursorDrips.length === 0) return;
    ctx.font = '400 10px "JetBrains Mono", monospace';
    ctx.textAlign = 'center';
    for (var i = cursorDrips.length - 1; i >= 0; i--) {
      var d = cursorDrips[i];
      d.y += d.speed;
      d.life -= d.fadeRate;
      if (d.life <= 0 || d.y > H + 12) { cursorDrips.splice(i, 1); continue; }

      d.changeTimer++;
      if (d.changeTimer >= d.changeRate) {
        d.changeTimer = 0;
        d.char = rainSource[Math.floor(Math.random() * rainSource.length)];
      }

      // Tone: the trail sits just a whisker above the background rain's
      // trail alpha (baseAlphaTrail = 0.08-0.30). A small source pop on
      // the freshest chars anchors the cursor point without pulling the
      // eye off the data readout above.
      var trailAlpha = baseAlphaTrail + 0.04;  // a tad brighter than rain trail
      var alpha = Math.pow(d.life, 0.6) * trailAlpha;
      ctx.fillStyle = 'rgba(' + trR + ',' + trG + ',' + trB + ',' + alpha + ')';
      ctx.fillText(d.char, d.x, d.y);
      if (d.life > 0.85) {
        var pop = (d.life - 0.85) * 6.67;  // 0 → 1 over the top 15% of life
        ctx.fillStyle = 'rgba(' + trR + ',' + trG + ',' + trB + ',' + (pop * 0.35) + ')';
        ctx.fillText(d.char, d.x, d.y);
      }
    }
  }

  // Parse tier color for background tinting
  function hexToRgb(hex) {
    var r = parseInt(hex.slice(1, 3), 16), g = parseInt(hex.slice(3, 5), 16), b = parseInt(hex.slice(5, 7), 16);
    return [r, g, b];
  }
  var tc = hexToRgb(tierColor || '#a0a0a0');
  var bgR = Math.floor(tc[0] * 0.06), bgG = Math.floor(tc[1] * 0.06), bgB = Math.floor(tc[2] * 0.06);
  var bgMidR = Math.floor(tc[0] * 0.09), bgMidG = Math.floor(tc[1] * 0.09), bgMidB = Math.floor(tc[2] * 0.09);
  // Trace color: muted version of tier color
  var trR = Math.min(255, tc[0] + 60), trG = Math.min(255, tc[1] + 60), trB = Math.min(255, tc[2] + 60);

  // Static background: dark + circuit traces
  var bgCanvas = document.createElement('canvas');
  bgCanvas.width = W; bgCanvas.height = H;
  var bgCtx = bgCanvas.getContext('2d');

  // Background gradient (dark shade of rarity color)
  var bg = bgCtx.createLinearGradient(0, 0, 0, H);
  bg.addColorStop(0, 'rgb(' + bgR + ',' + bgG + ',' + bgB + ')');
  bg.addColorStop(0.5, 'rgb(' + bgMidR + ',' + bgMidG + ',' + bgMidB + ')');
  bg.addColorStop(1, 'rgb(' + bgR + ',' + bgG + ',' + bgB + ')');
  bgCtx.fillStyle = bg;
  bgCtx.fillRect(0, 0, W, H);

  // Rain source — the Rosetta Stone text, or hex fallback
  var rainSource = (aboutText || '').replace(/\s+/g, ' ');
  if (!rainSource) rainSource = '0123456789abcdef';

  // Rarity drives the matrix: common = sparse whisper, legendary = full revelation
  var rarityNorm = Math.min(1, (rarityScore || 0) / 80);
  var rr = rarityNorm * rarityNorm * rarityNorm; // cubic — legendary explodes away from everything else

  // Column density: common 10px (recognizably matrix), legendary 3px (overwhelming)
  var colSpacing = Math.max(3, Math.floor(10 - rr * 7));
  var numCols = Math.floor(W / colSpacing);
  var charH = 12;

  // Per-column length, speed, brightness all scale with rarity
  // Common floor is readable matrix; legendary ceiling is overwhelming
  var baseLen = 6 + Math.floor(rr * 20);        // 6-26 chars per column
  var lenVariance = 4 + Math.floor(rr * 14);    // +4-18 random
  var baseSpeed = 0.3 + rr * 1.4;               // 0.3-1.7 base fall speed
  var speedVariance = 0.4 + rr * 2.0;           // +0.4-2.4 random
  var baseAlphaHead = 0.22 + rr * 0.45;         // head char: 0.22-0.67
  var baseAlphaTrail = 0.08 + rr * 0.22;        // trail chars: 0.08-0.30

  // --- Parse entropy bytes for per-column behavior ---
  var entropyBytes = [];
  var eCleanHex = (entropyHex || '').replace(/\s/g, '');
  for (var ebi = 0; ebi < eCleanHex.length - 1; ebi += 2) {
    entropyBytes.push(parseInt(eCleanHex.slice(ebi, ebi + 2), 16));
  }
  var entropyMean = 0;
  for (var emi = 0; emi < entropyBytes.length; emi++) entropyMean += entropyBytes[emi];
  entropyMean = entropyBytes.length > 0 ? entropyMean / entropyBytes.length : 128;

  var rainCols = [];
  for (var ri = 0; ri < numCols; ri++) {
    // Each column gets an entropy byte (wraps around)
    var eByte = entropyBytes.length > 0 ? entropyBytes[ri % entropyBytes.length] : 128;
    var eNorm = eByte / 255; // 0-1, this column's entropy character
    var eDev = Math.abs(eByte - entropyMean) / 128; // 0-1, deviation from mean

    var startPos = Math.floor(rng() * rainSource.length);
    var colLen = baseLen + Math.floor(rng() * lenVariance);
    var chars = [];
    for (var ch = 0; ch < colLen; ch++) {
      chars.push(rainSource[(startPos + ch) % rainSource.length]);
    }

    // Entropy-driven drift: horizontal sine wobble, amplitude from byte deviation
    var driftAmp = eDev * (2 + rr * 3);       // 0-5px wobble, more at high rarity
    var driftFreq = 0.005 + eNorm * 0.015;    // each column wobbles at its own frequency
    var driftPhase = rng() * 6.2832;

    // Entropy-driven mutation: high bytes = fast scramble, low bytes = slow/stable
    // At high rarity, stable columns hold readable text; scrambled ones add chaos contrast
    var mutationBase = Math.max(1, Math.floor(8 - eNorm * 6)); // high byte = fast (1-2), low byte = slow (6-8)
    // Rarity makes legible columns even more stable (slower mutation = readable phrases)
    var legibilitySlowdown = Math.floor((1 - eNorm) * rr * 12); // low-entropy columns freeze at high rarity
    var changeRate = mutationBase + legibilitySlowdown;

    rainCols.push({
      x: ri * colSpacing + colSpacing / 2,
      xBase: ri * colSpacing + colSpacing / 2, // save original x for drift offset
      y: rng() * H,
      speed: baseSpeed + rng() * speedVariance,
      length: colLen,
      chars: chars,
      sourcePos: startPos,
      changeTimer: 0,
      changeRate: changeRate,
      driftAmp: driftAmp,
      driftFreq: driftFreq,
      driftPhase: driftPhase,
      eByte: eByte,
      eNorm: eNorm,
      eDev: eDev
    });
  }

  var rainFrame = 0;

  // Visibility observer
  var visible = true;
  if (typeof IntersectionObserver !== 'undefined') {
    new IntersectionObserver(function(entries) { visible = entries[0].isIntersecting; }, {threshold: 0}).observe(canvas);
  }

  function drawCell(c, labelText, valueText) {
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
    return h;
  }

  function tick() {
    if (!visible) { setTimeout(tick, 200); return; }

    ctx.drawImage(bgCanvas, 0, 0);

    // Hex rain
    rainFrame++;
    ctx.font = '300 9px "JetBrains Mono", monospace';
    ctx.textAlign = 'center';
    for (var ri = 0; ri < rainCols.length; ri++) {
      var rc = rainCols[ri];
      rc.y += rc.speed;
      if (rc.y - rc.length * charH > H) rc.y = -rc.length * charH * rng();

      // Entropy-driven horizontal drift
      rc.x = rc.xBase + Math.sin(rainFrame * rc.driftFreq + rc.driftPhase) * rc.driftAmp;

      rc.changeTimer++;
      if (rc.changeTimer >= rc.changeRate) {
        rc.changeTimer = 0;
        rc.sourcePos = (rc.sourcePos + 1) % rainSource.length;
        // Shift characters down, new char from source at head
        rc.chars.pop();
        rc.chars.unshift(rainSource[rc.sourcePos]);
      }
      for (var ch = 0; ch < rc.length; ch++) {
        var chy = rc.y - ch * charH;
        if (chy < -charH || chy > H + charH) continue;
        var fade = 1 - ch / rc.length;
        var alpha = ch === 0 ? baseAlphaHead : fade * baseAlphaTrail;

        // Flicker instability: high-deviation columns stutter
        if (rc.eDev > 0.3 && Math.random() < rc.eDev * 0.08) {
          alpha *= 1.8 + Math.random(); // bright flash
        }

        ctx.fillStyle = 'rgba(' + trR + ',' + trG + ',' + trB + ',' + Math.min(alpha, 0.85) + ')';
        ctx.fillText(rc.chars[ch], rc.x, chy);
      }
    }

    // Cursor-driven drip leak — layered above the static matrix rain,
    // below the cells. Each frame spawns 2 chars at the cursor that
    // fall straight down, fading as they go.
    drawCursorDrips();

    ctx.textAlign = 'center';

    // Machine vitals cells
    for (var ci = 0; ci < cells.length; ci++) {
      var c = cells[ci];
      var h = drawCell(c);

      ctx.font = '500 ' + LABEL_SIZE + 'px "JetBrains Mono", monospace';
      ctx.fillStyle = 'rgba(255,255,255,' + (0.45 + h * 0.35) + ')';
      ctx.fillText(c.l.toUpperCase(), c.x + c.w / 2, c.y + 13);

      ctx.font = '400 ' + VALUE_SIZE + 'px "JetBrains Mono", monospace';
      ctx.fillStyle = 'rgba(255,255,255,' + (0.7 + h * 0.3) + ')';
      var val = c.v;
      if (ctx.measureText(val).width > c.w - 16) {
        while (val.length > 3 && ctx.measureText(val + '...').width > c.w - 16) val = val.slice(0, -1);
        val += '...';
      }
      ctx.fillText(val, c.x + c.w / 2, c.y + 28);
    }

    // Entropy cell
    if (entropyCell) {
      var ec = entropyCell;
      var eh = drawCell(ec);

      ctx.font = '500 ' + LABEL_SIZE + 'px "JetBrains Mono", monospace';
      ctx.fillStyle = 'rgba(255,255,255,' + (0.45 + eh * 0.35) + ')';
      ctx.fillText('KERNEL ENTROPY', ec.x + ec.w / 2, ec.y + 12);

      // Entropy hex layout + font precomputed at init (static input).
      ctx.font = _entropyFont;
      ctx.fillStyle = 'rgba(255,255,255,' + (0.35 + eh * 0.3) + ')';
      ctx.fillText(_entropyLine1, ec.x + ec.w / 2, ec.y + 26);
      ctx.fillText(_entropyLine2, ec.x + ec.w / 2, ec.y + 38);
    }

    // Identity + traits cell
    if (bottomCell) {
      var bh = drawCell(bottomCell);
      // Count lines for vertical centering
      var lineCount = 0;
      if (fingerprint) lineCount++;
      if (allTraitsList.length) lineCount++;
      if (sigilData) lineCount++;
      var lineH = 13;
      var totalTextH = lineCount * lineH;
      var btY = bottomCell.y + (bottomCell.h - totalTextH) / 2 + lineH - 2;

      // FP, trait list, sigil caption — fonts precomputed at init.
      if (fingerprint) {
        ctx.font = _fpFont;
        ctx.fillStyle = 'rgba(255,255,255,' + (0.25 + bh * 0.3) + ')';
        ctx.fillText(_fpText, W / 2, btY);
        btY += lineH;
      }
      if (allTraitsList.length) {
        ctx.font = _traitFont;
        ctx.fillStyle = 'rgba(255,255,255,' + (0.45 + bh * 0.35) + ')';
        ctx.fillText(_traitText, W / 2, btY);
        btY += lineH;
      }
      if (sigilData) {
        ctx.font = _sigilFont;
        ctx.fillStyle = 'rgba(160, 140, 240,' + (0.6 + bh * 0.15) + ')';
        ctx.fillText(_sigilText, W / 2, btY);
      }
    }

    ctx.textAlign = 'left';
    setTimeout(tick, 16);
  }

  tick();

  // Save metadata
  if (typeof enableCanvasSave === 'function') {
    var vitalsJson = {};
    for (var vi = 0; vi < machineData.length; vi++) {
      var key = machineData[vi].l.toLowerCase().replace(/ /g, '_').replace('\u2193', 'rx').replace('\u2191', 'tx');
      vitalsJson[key] = machineData[vi].v;
    }
    if (fingerprint) vitalsJson.fingerprint = fingerprint;
    if (entropyHex) vitalsJson.entropy = entropyHex;
    var saveMeta = { machine_vitals: JSON.stringify(vitalsJson), Software: 'Mememage' };
    if (barSpec) saveMeta.bar_spec = JSON.stringify(barSpec);
    if (barFragment !== undefined && barFragment !== null) saveMeta.bar_payload_3 = barFragment;
    if (parentId)   saveMeta.parent_id   = parentId;
    if (parentHash) saveMeta.parent_hash = parentHash;
    saveMeta.fragment_id = 'machine';
    var fragBytes = (typeof fragmentBytes === 'function') ? fragmentBytes(barFragment, FRAGMENT_TAG_MACHINE) : null;
    enableCanvasSave(canvas, saveMeta, fragBytes);
  }
}
