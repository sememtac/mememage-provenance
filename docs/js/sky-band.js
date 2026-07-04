// =====================================================================
// SKY BAND CANVAS ANIMATION
// =====================================================================
function initSkyBand(canvas, SKY_W, SKY_H, PLANET_DATA, SKY_READING, KERNEL_ENTROPY, machineData, ageTier, rarityScore, celestialTraits, birthTemperament, tierColor) {
  // Cell colors — shared rarity tint (variant C) from cert-renderer.
  var _cc = rarityCellColors(tierColor);
  var CELL_FILL_BASE = _cc.base, CELL_STROKE_BASE = _cc.baseStroke;
  var CELL_FILL_HOVER = _cc.hoverFill, CELL_STROKE_HOVER = _cc.hoverStroke;
  // Time decay affects meteor speed and brightness
  var decayMult = {fresh:1, young:0.9, aged:0.7, vintage:0.45, ancient:0.2};
  var meteorSpeedMult = decayMult[ageTier] || 1;
  var meteorAlphaMult = decayMult[ageTier] || 1;

  // Rarity affects meteor count and trail length
  var rarityMeteorBonus = 0;
  var rarityTrailMult = 1;
  if (rarityScore >= 80) { rarityMeteorBonus = 10; rarityTrailMult = 1.5; } // legendary
  else if (rarityScore >= 70) { rarityMeteorBonus = 5; rarityTrailMult = 1.3; } // epic
  else if (rarityScore >= 60) { rarityMeteorBonus = 3; rarityTrailMult = 1.15; } // very rare
  else if (rarityScore >= 46) { rarityMeteorBonus = 1; } // rare
  var ctx = canvas.getContext('2d');

  // Entropy-seeded PRNG
  var entropyHex = KERNEL_ENTROPY.replace(/\s/g, '');
  var entropyBytes = [];
  for (var i = 0; i < entropyHex.length; i += 2) {
    entropyBytes.push(parseInt(entropyHex.substr(i, 2), 16) || 0);
  }
  var entropySeed = 0;
  for (var i = 0; i < entropyBytes.length; i++) entropySeed = (entropySeed * 31 + entropyBytes[i]) & 0x7FFFFFFF;

  function seededRandom() {
    entropySeed |= 0; entropySeed = entropySeed + 0x6D2B79F5 | 0;
    var t = Math.imul(entropySeed ^ entropySeed >>> 15, 1 | entropySeed);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  }

  // === Soul-driven meteor parameters ===

  // 1. Sun sign element → color temperature
  var sunSign = '';
  for (var pi = 0; pi < PLANET_DATA.length; pi++) { if (PLANET_DATA[pi].name === 'sun') sunSign = PLANET_DATA[pi].sign; }
  var FIRE_SIGNS = {Aries:1, Leo:1, Sagittarius:1};
  var WATER_SIGNS = {Cancer:1, Scorpio:1, Pisces:1};
  var EARTH_SIGNS = {Taurus:1, Virgo:1, Capricorn:1};
  // AIR_SIGNS: Gemini, Libra, Aquarius (default)
  var elementColors;
  if (FIRE_SIGNS[sunSign]) {
    elementColors = [[255, 180, 80], [255, 140, 60], [255, 200, 120], [255, 160, 90], [220, 120, 60]]; // warm amber/orange
  } else if (WATER_SIGNS[sunSign]) {
    elementColors = [[140, 180, 255], [120, 200, 240], [180, 220, 255], [100, 160, 220], [160, 240, 255]]; // cool blue/white
  } else if (EARTH_SIGNS[sunSign]) {
    elementColors = [[200, 220, 140], [180, 200, 120], [220, 200, 100], [160, 180, 100], [240, 220, 160]]; // green/gold
  } else {
    elementColors = [[200, 180, 240], [220, 200, 255], [180, 160, 220], [240, 220, 255], [160, 180, 200]]; // silver/violet
  }
  var METEOR_COLORS = elementColors;

  // 2. Moon phase → brightness multiplier
  var moonPhaseStr = '';
  for (var pi = 0; pi < PLANET_DATA.length; pi++) { if (PLANET_DATA[pi].name === 'moon' && PLANET_DATA[pi].phase) moonPhaseStr = PLANET_DATA[pi].phase; }
  var moonBright = 0.7; // default
  if (moonPhaseStr.indexOf('Full') >= 0) moonBright = 1.0;
  else if (moonPhaseStr.indexOf('Gibbous') >= 0) moonBright = 0.85;
  else if (moonPhaseStr.indexOf('Quarter') >= 0) moonBright = 0.65;
  else if (moonPhaseStr.indexOf('Crescent') >= 0) moonBright = 0.5;
  else if (moonPhaseStr.indexOf('New') >= 0) moonBright = 0.35;

  // 3. Angular spread → direction spread (tight = focused, wide = scattered)
  // Driven from entropy (no born-data field for angular_spread yet).
  var dirSpread = 0.3 + (entropyBytes[2] || 128) / 255 * 0.7; // 0.3-1.0 (focused to scattered)

  // 4. Machine state → behavior. V1 stores load as [f,f,f] and
  // net_rx/net_tx/mem_free as raw bytes; legacy strings still parse via
  // bytesValue / formatLoad's parse path.
  var loadRaw = machineData.load;
  var loadVal = 1;
  if (Array.isArray(loadRaw) && loadRaw.length) {
    loadVal = parseFloat(loadRaw[0]) || 1;
  } else if (typeof loadRaw === 'string' && loadRaw) {
    loadVal = parseFloat(loadRaw.split('/')[0].trim()) || 1;
  }
  var freeBytes = (typeof bytesValue === 'function') ? bytesValue(machineData.mem_free)
                                                     : (parseInt(machineData.mem_free) || 500);
  var freeMB = freeBytes > 1000 ? freeBytes / (1024 * 1024) : freeBytes;  // legacy "500" was MB; bytes path divides
  var netRx = (typeof bytesValue === 'function') ? bytesValue(machineData.net_rx)
                                                  : (parseFloat(machineData.net_rx) || 50);
  var netTx = (typeof bytesValue === 'function') ? bytesValue(machineData.net_tx)
                                                  : (parseFloat(machineData.net_tx) || 20);
  var uptimeSec = parseInt(machineData.uptime_seconds) || 86400;

  // CPU load → turbulence (micro-oscillation amplitude)
  var turbulence = Math.min(1, loadVal / 5); // 0-1

  // Network rx/tx → vertical bias (-1 = downward, +1 = upward)
  var vertBias = 0;
  if (netRx + netTx > 0) vertBias = (netTx - netRx) / (netRx + netTx) * 0.3;

  // Uptime → trail fade speed (longer uptime = trails linger)
  var trailPersist = Math.min(2, uptimeSec / 604800 + 0.5); // 0.5-2.0

  // 5. Birth temperament → meteor style
  var tempLower = (birthTemperament || '').toLowerCase();
  var meteorStyle = 'steady'; // default

  // Volatile group — fast, steep, burst-prone
  if (tempLower.indexOf('turbulent') >= 0 || tempLower.indexOf('violent') >= 0 ||
      tempLower.indexOf('fever') >= 0 || tempLower.indexOf('volatile') >= 0) {
    meteorStyle = 'volatile';
  }
  // Serene group — slow, wide arcs, gentle
  else if (tempLower.indexOf('serene') >= 0 || tempLower.indexOf('clean') >= 0 ||
           tempLower.indexOf('perfect') >= 0 || tempLower.indexOf('quiet') >= 0 ||
           tempLower.indexOf('contemplat') >= 0) {
    meteorStyle = 'serene';
  }
  // Electric group — rapid, flickering, chaotic
  else if (tempLower.indexOf('electric') >= 0 || tempLower.indexOf('paradox') >= 0 ||
           tempLower.indexOf('knotted') >= 0 || tempLower.indexOf('entangled') >= 0) {
    meteorStyle = 'electric';
  }
  // Erratic group — shifting, unpredictable
  else if (tempLower.indexOf('shifting') >= 0 || tempLower.indexOf('fraying') >= 0 ||
           tempLower.indexOf('grasping') >= 0 || tempLower.indexOf('restless') >= 0) {
    meteorStyle = 'erratic';
  }
  // else steady (default)

  var styleSpeedMult = {serene: 0.6, steady: 1.0, volatile: 1.5, electric: 1.8, erratic: 1.1};
  var styleCountMult = {serene: 0.6, steady: 1.0, volatile: 1.2, electric: 0.8, erratic: 0.9};
  var styleTrailMult = {serene: 1.5, steady: 1.0, volatile: 0.7, electric: 0.5, erratic: 1.2};

  // Color pool weighted by machine state (original logic + element colors)
  var colorPool = [0, 1, 2, 3, 4];
  if (loadVal > 3) { colorPool.push(2); colorPool.push(2); }
  if (freeMB < 200) { colorPool.push(3); colorPool.push(3); }

  var baseSpawnByte = entropyBytes[0] || 128;
  var meteorTargetCount = Math.round((10 + Math.floor(baseSpawnByte / 25) + rarityMeteorBonus) * styleCountMult[meteorStyle]);

  // 6. Spawn pattern — temperament + entropy driven
  var spawnPattern = 'steady';
  if (meteorStyle === 'volatile') spawnPattern = 'burst';
  else if (meteorStyle === 'serene') spawnPattern = 'sparse';
  else if (meteorStyle === 'electric') spawnPattern = 'waves';
  else if (meteorStyle === 'erratic') {
    // Erratic alternates patterns — pick from entropy
    var patterns = ['steady', 'waves', 'burst', 'sparse'];
    spawnPattern = patterns[(entropyBytes[3] || 0) % 4];
  } else {
    var patternByte = entropyBytes[3] || 128;
    if (patternByte < 60) spawnPattern = 'steady';
    else if (patternByte < 120) spawnPattern = 'waves';
    else if (patternByte < 180) spawnPattern = 'burst';
    else spawnPattern = 'sparse';
  }

  var burstChance = 0;
  if (spawnPattern === 'burst') burstChance = 0.02;
  else if (spawnPattern === 'waves') burstChance = 0.005;
  var spawnCooldown = 0; // frames to wait before next spawn

  var oc = {sun:[255,200,80], moon:[200,210,230], mercury:[180,180,190], venus:[220,210,180], mars:[220,120,100], jupiter:[210,190,150], saturn:[200,190,160]};

  // Planet image loading
  var planetSizes = { sun: 26, moon: 18, mercury: 9, venus: 12, mars: 11, jupiter: 20, saturn: 16 };
  var planetYBand = { sun: 0.22, moon: 0.28, mercury: 0.42, venus: 0.18, mars: 0.38, jupiter: 0.32, saturn: 0.25 };
  var planetDefs = [];
  var planetSnapCanvas = null;
  var planetSnapReady = false;

  var olY, olH, ringCX, ringCY, ringR;
  var cellLayout = [];
  var cellRects = [];
  var cellHover = [];
  var zodiacPositions = [];
  var zodiacGlow = [0.12,0.12,0.12,0.12,0.12,0.12,0.12,0.12,0.12,0.12,0.12,0.12];
  var hoveredPlanet = null;

  var MS = 500;
  var mets = [];

  // Zodiac glyph image loading
  var zodiacNames = ['aries','taurus','gemini','cancer','leo','virgo','libra','scorpio','sagittarius','capricorn','aquarius','pisces'];
  var zodiacImgs = [];
  var zodiacImgsReady = false;

  function loadZodiacImages(callback) {
    var loaded = 0;
    var total = zodiacNames.length;
    for (var i = 0; i < total; i++) {
      (function(idx) {
        var img = new Image();
        // crossOrigin must be set BEFORE src so the request is made
        // with the CORS preflight. Without it, the image (even if
        // same-origin) marks any canvas it's drawn to as tainted —
        // breaking the save-cert PNG pipeline that needs to read
        // pixels back via getImageData. GitHub Pages + mememage.art
        // both send Access-Control-Allow-Origin: * for static assets.
        img.crossOrigin = 'anonymous';
        img.onload = function() {
          zodiacImgs[idx] = img;
          loaded++;
          if (loaded >= total) { zodiacImgsReady = true; callback(); }
        };
        img.onerror = function() {
          zodiacImgs[idx] = null; // fallback to text
          loaded++;
          if (loaded >= total) { zodiacImgsReady = true; callback(); }
        };
        img.src = assetUrl('planets/zodiac_' + zodiacNames[idx] + '.png');
      })(i);
    }
    if (total === 0) callback();
  }

  function loadPlanets(callback) {
    var padding = 0.06;
    planetDefs = [];
    for (var i = 0; i < PLANET_DATA.length; i++) {
      var p = PLANET_DATA[i];
      var xFrac = padding + (p.lon / 360) * (1 - padding * 2);
      planetDefs.push({
        name: p.name,
        x: xFrac,
        y: planetYBand[p.name] || 0.3,
        size: planetSizes[p.name] || 10,
        src: assetUrl('planets/' + p.name + '.png')
      });
    }

    var loaded = 0;
    var total = planetDefs.length;
    for (var i = 0; i < planetDefs.length; i++) {
      (function(pd) {
        var img = new Image();
        // crossOrigin before src — keeps the sky-band canvas
        // un-tainted so save-cert can read pixels back.
        img.crossOrigin = 'anonymous';
        img.onload = function() {
          pd.img = img;
          loaded++;
          if (loaded >= total) callback();
        };
        img.onerror = function() {
          loaded++;
          if (loaded >= total) callback();
        };
        img.src = pd.src;
      })(planetDefs[i]);
    }
    if (total === 0) callback();
  }

  // Sky background tinted by rarity (hoisted for animation loop access).
  // Common is pure neutral grey — earlier values had B > R = G which
  // read as a blue cast on calibrated displays. Other tiers keep hue.
  var _skyBgs = {common:['#141414','#1c1c1c','#101010'],uncommon:['#0e1810','#142018','#0a120c'],rare:['#0e1018','#141a24','#0a0e14'],veryrare:['#140e18','#1a1420','#100a14'],epic:['#18140e','#201a12','#12100a'],legendary:['#140a0a','#1c1012','#100808']};
  var _skyTier = rarityScore>=80?'legendary':rarityScore>=70?'epic':rarityScore>=60?'veryrare':rarityScore>=46?'rare':rarityScore>=35?'uncommon':'common';
  var _skyC = _skyBgs[_skyTier];
  var _stars = [];

  function renderPlanetSnapshot() {
    planetSnapCanvas = document.createElement('canvas');
    // Mirror the main canvas's DPR scaling so the static planet grid +
    // text layer composites 1:1 onto the hi-DPI main canvas instead of
    // being upscaled from logical px (what made cell text look blurry
    // on mobile). Draw coords stay in logical space via ctx.scale.
    var _dpr = window.devicePixelRatio || 1;
    planetSnapCanvas.width = Math.round(SKY_W * _dpr);
    planetSnapCanvas.height = Math.round(SKY_H * _dpr);
    var pctx = planetSnapCanvas.getContext('2d');
    pctx.scale(_dpr, _dpr);
    var skyGrad = pctx.createLinearGradient(0, 0, 0, SKY_H);
    skyGrad.addColorStop(0, _skyC[0]);
    skyGrad.addColorStop(0.5, _skyC[1]);
    skyGrad.addColorStop(1, _skyC[2]);
    pctx.fillStyle = skyGrad;
    pctx.fillRect(0, 0, SKY_W, SKY_H);

    // Stars (seeded) — stored for twinkling
    var rng = 42;
    function fakeRand() { rng = (rng * 16807 + 0) % 2147483647; return rng / 2147483647; }
    _stars = [];
    for (var i = 0; i < 160; i++) {
      _stars.push({
        x: fakeRand() * SKY_W,
        y: fakeRand() * SKY_H * 0.85,
        r: 0.4 + fakeRand() * 1.0,
        baseA: (0.3 + fakeRand() * 0.5) * 0.64,
        speed: 0.5 + fakeRand() * 2.0,
        phase: fakeRand() * Math.PI * 2,
        warm: fakeRand() > 0.7
      });
    }
    pctx.globalAlpha = 1;
    for (var si = 0; si < _stars.length; si++) {
      var st = _stars[si];
      pctx.fillStyle = st.warm ? 'rgba(255,240,200,' + st.baseA + ')' : 'rgba(220,230,255,' + st.baseA + ')';
      pctx.beginPath();
      pctx.arc(st.x, st.y, st.r, 0, Math.PI * 2);
      pctx.fill();
    }

    // Draw planet images
    for (var i = 0; i < planetDefs.length; i++) {
      var pd = planetDefs[i];
      if (!pd.img) continue;
      var px = pd.x * SKY_W;
      var py = pd.y * SKY_H;
      var imgSize = pd.size * 2;
      if (pd.name === 'saturn') imgSize = pd.size * 3;

      pctx.save();
      pctx.globalAlpha = 0.55;
      pctx.drawImage(pd.img, px - imgSize / 2, py - imgSize / 2, imgSize, imgSize);
      pctx.restore();
    }

    // --- Frosted glass data cells ---
    var skyCellPad = 6;
    var skyGridX = 12;
    var skyGridW = SKY_W - 24;
    var skyCellW = (skyGridW - 2 * skyCellPad) / 3;
    var skyCellH = 52;
    var skyGridY = 12;

    // Build cell layout
    cellLayout = [];
    var nonMoonPlanets = [];
    var moonPlanet = null;
    for (var i = 0; i < PLANET_DATA.length; i++) {
      if (PLANET_DATA[i].name === 'moon') moonPlanet = PLANET_DATA[i];
      else nonMoonPlanets.push(PLANET_DATA[i]);
    }
    for (var i = 0; i < Math.min(3, nonMoonPlanets.length); i++) {
      cellLayout.push({p: nonMoonPlanets[i], col: i, row: 0, span: 1});
    }
    if (moonPlanet) {
      cellLayout.push({p: moonPlanet, col: 0, row: 1, span: 3});
    }
    for (var i = 3; i < nonMoonPlanets.length; i++) {
      cellLayout.push({p: nonMoonPlanets[i], col: i - 3, row: 2, span: 1});
    }

    cellRects = [];
    for (var ci = 0; ci < cellLayout.length; ci++) {
      var cl = cellLayout[ci];
      var p = cl.p;
      var cX = skyGridX + cl.col * (skyCellW + skyCellPad);
      var cY = skyGridY + cl.row * (skyCellH + skyCellPad);
      var cW = cl.span === 3 ? skyGridW : skyCellW;

      cellRects.push({ x: cX, y: cY, w: cW, h: skyCellH });

      pctx.fillStyle = CELL_FILL_BASE;
      roundRect(pctx, cX, cY, cW, skyCellH, 6);
      pctx.fill();
      pctx.strokeStyle = CELL_STROKE_BASE;
      pctx.lineWidth = 1;
      roundRect(pctx, cX, cY, cW, skyCellH, 6);
      pctx.stroke();

      pctx.textAlign = 'left';
      pctx.font = symFont('400', 11);
      pctx.fillStyle = 'rgba(255,255,255,0.6)';
      pctx.fillText(p.sym, cX + 8, cY + skyCellH / 2 + 4);

      pctx.font = font('500', 6.5);
      pctx.fillStyle = 'rgba(255,255,255,0.35)';
      pctx.fillText(p.label.toUpperCase(), cX + 26, cY + 14);

      var valText = p.sign + ' ' + p.deg.toFixed(1) + '\u00b0';
      if (p.phase) valText += ' \u2014 ' + p.phase;
      // Shrink to fit cell — on narrow canvases "Capricorn 15.2°"
      // overflows the default 8.5px into the neighbor cell.
      var _valW = cW - 30;  // left offset 26 + right padding 4
      var _vs = 8.5;
      pctx.font = font('300', _vs);
      while (pctx.measureText(valText).width > _valW && _vs > 5.5) {
        _vs -= 0.5;
        pctx.font = font('300', _vs);
      }
      pctx.fillStyle = 'rgba(255,255,255,0.75)';
      pctx.fillText(valText, cX + 26, cY + 28);
    }

    // --- Orbit line (sine wave) ---
    olY = skyGridY + 3 * (skyCellH + skyCellPad) + 10;
    var olX = 16;
    var olW = SKY_W - 32;
    olH = 30;
    var olMid = olY + olH / 2;

    pctx.strokeStyle = 'rgba(255,255,255,0.08)';
    pctx.lineWidth = 0.8;
    pctx.beginPath();
    for (var i = 0; i <= olW; i++) {
      var t = i / olW;
      var x = olX + i;
      var y = olMid + Math.sin(t * Math.PI * 2.5 + 0.5) * olH * 0.4;
      if (i === 0) pctx.moveTo(x, y);
      else pctx.lineTo(x, y);
    }
    pctx.stroke();

    // Second harmonic
    pctx.strokeStyle = 'rgba(255,255,255,0.04)';
    pctx.lineWidth = 0.5;
    pctx.beginPath();
    for (var i = 0; i <= olW; i++) {
      var t = i / olW;
      var x = olX + i;
      var y = olMid + Math.sin(t * Math.PI * 2.5 + 0.5) * olH * 0.4 + Math.sin(t * Math.PI * 5 + 1) * 3;
      if (i === 0) pctx.moveTo(x, y);
      else pctx.lineTo(x, y);
    }
    pctx.stroke();

    // Plot planets on sine wave
    var orbitColors = {
      sun: [255,200,80], moon: [200,210,230], mercury: [180,180,190],
      venus: [220,210,180], mars: [220,120,100], jupiter: [210,190,150], saturn: [200,190,160]
    };

    for (var i = 0; i < PLANET_DATA.length; i++) {
      var p = PLANET_DATA[i];
      var t = p.lon / 360;
      var px = olX + t * olW;
      var py = olMid + Math.sin(t * Math.PI * 2.5 + 0.5) * olH * 0.4;
      var c = orbitColors[p.name] || [255,255,255];
      var dotR = p.name === 'sun' ? 4 : (p.name === 'jupiter' ? 3.5 : (p.name === 'moon' ? 3 : 2.5));

      var glow = pctx.createRadialGradient(px, py, 0, px, py, dotR * 3);
      glow.addColorStop(0, 'rgba('+c[0]+','+c[1]+','+c[2]+',0.2)');
      glow.addColorStop(1, 'rgba('+c[0]+','+c[1]+','+c[2]+',0)');
      pctx.fillStyle = glow;
      pctx.beginPath();
      pctx.arc(px, py, dotR * 3, 0, Math.PI * 2);
      pctx.fill();

      pctx.fillStyle = 'rgba('+c[0]+','+c[1]+','+c[2]+',0.7)';
      pctx.beginPath();
      pctx.arc(px, py, dotR, 0, Math.PI * 2);
      pctx.fill();

      pctx.fillStyle = 'rgba('+c[0]+','+c[1]+','+c[2]+',0.9)';
      pctx.beginPath();
      pctx.arc(px, py, dotR * 0.4, 0, Math.PI * 2);
      pctx.fill();

      pctx.textAlign = 'center';
      pctx.font = symFont('400', 6);
      pctx.fillStyle = 'rgba('+c[0]+','+c[1]+','+c[2]+',0.5)';
      pctx.fillText(p.sym, px, py - dotR - 4);
    }

    // --- Orrery ring ---
    var signSyms = ['\u2648','\u2649','\u264A','\u264B','\u264C','\u264D','\u264E','\u264F','\u2650','\u2651','\u2652','\u2653'];
    var signNames = ['Aries','Taurus','Gemini','Cancer','Leo','Virgo','Libra','Scorpio','Sagittarius','Capricorn','Aquarius','Pisces'];

    ringCX = SKY_W / 2;
    ringCY = olY + olH + 48;
    ringR = 42;

    var ringGlow = pctx.createRadialGradient(ringCX, ringCY, ringR * 0.8, ringCX, ringCY, ringR * 1.6);
    ringGlow.addColorStop(0, 'rgba(255,255,255,0.02)');
    ringGlow.addColorStop(1, 'rgba(255,255,255,0)');
    pctx.fillStyle = ringGlow;
    pctx.beginPath(); pctx.arc(ringCX, ringCY, ringR * 1.6, 0, Math.PI * 2); pctx.fill();

    pctx.strokeStyle = 'rgba(255,255,255,0.1)';
    pctx.lineWidth = 0.8;
    pctx.beginPath(); pctx.arc(ringCX, ringCY, ringR, 0, Math.PI * 2); pctx.stroke();

    pctx.strokeStyle = 'rgba(255,255,255,0.04)';
    pctx.lineWidth = 0.5;
    pctx.beginPath(); pctx.arc(ringCX, ringCY, ringR * 0.55, 0, Math.PI * 2); pctx.stroke();

    pctx.strokeStyle = 'rgba(255,255,255,0.04)';
    pctx.lineWidth = 0.4;
    pctx.setLineDash([2, 3]);
    pctx.beginPath(); pctx.arc(ringCX, ringCY, ringR * 0.75, 0, Math.PI * 2); pctx.stroke();
    pctx.setLineDash([]);

    // Sign divisions + symbols
    for (var s = 0; s < 12; s++) {
      var a = (s * 30 - 90) * Math.PI / 180;
      pctx.strokeStyle = 'rgba(255,255,255,0.06)';
      pctx.lineWidth = 0.4;
      pctx.beginPath();
      pctx.moveTo(ringCX + Math.cos(a) * ringR * 0.9, ringCY + Math.sin(a) * ringR * 0.9);
      pctx.lineTo(ringCX + Math.cos(a) * ringR * 1.03, ringCY + Math.sin(a) * ringR * 1.03);
      pctx.stroke();

      var ma = ((s * 30 + 15) - 90) * Math.PI / 180;
      var zx = ringCX + Math.cos(ma) * (ringR + 11);
      var zy = ringCY + Math.sin(ma) * (ringR + 11);
      // Store positions for interactive drawing in tick loop
      zodiacPositions.push({x: zx, y: zy, idx: s, sym: signSyms[s]});
    }

    // Planet dots on orrery ring
    for (var i = 0; i < PLANET_DATA.length; i++) {
      var p = PLANET_DATA[i];
      var a = (p.lon - 90) * Math.PI / 180;
      var orbitR = ringR * 0.75;
      var px = ringCX + Math.cos(a) * orbitR;
      var py = ringCY + Math.sin(a) * orbitR;
      var c = oc[p.name] || [255,255,255];
      var dr = p.name === 'sun' ? 4 : (p.name === 'jupiter' ? 3.2 : (p.name === 'moon' ? 3 : 2.2));

      var gl = pctx.createRadialGradient(px, py, 0, px, py, dr * 3);
      gl.addColorStop(0, 'rgba('+c[0]+','+c[1]+','+c[2]+',0.25)');
      gl.addColorStop(1, 'rgba('+c[0]+','+c[1]+','+c[2]+',0)');
      pctx.fillStyle = gl;
      pctx.beginPath(); pctx.arc(px, py, dr * 3, 0, Math.PI * 2); pctx.fill();

      pctx.fillStyle = 'rgba('+c[0]+','+c[1]+','+c[2]+',0.75)';
      pctx.beginPath(); pctx.arc(px, py, dr, 0, Math.PI * 2); pctx.fill();

      pctx.fillStyle = 'rgba('+c[0]+','+c[1]+','+c[2]+',0.95)';
      pctx.beginPath(); pctx.arc(px, py, dr * 0.35, 0, Math.PI * 2); pctx.fill();
    }

    // Center dot
    pctx.fillStyle = 'rgba(255,255,255,0.06)';
    pctx.beginPath(); pctx.arc(ringCX, ringCY, 2, 0, Math.PI * 2); pctx.fill();

    pctx.textBaseline = 'alphabetic';

    // --- Celestial reading ---
    if (SKY_READING) {
      pctx.textAlign = 'center';
      pctx.font = font('300', 8, true);
      pctx.fillStyle = 'rgba(255,255,255,0.35)';
      var readLines = wrapText(pctx, SKY_READING, SKY_W - 40);
      var ry = ringCY + ringR + 36;
      for (var i = 0; i < readLines.length; i++) {
        pctx.fillText(readLines[i], SKY_W / 2, ry + i * 12);
      }
    }

    planetSnapReady = true;
  }

  // --- SHOOTING STARS ---
  function makeMet() {
    var r = seededRandom;
    var depth = r();
    var x0 = SKY_W + 40 + (r() - 0.5) * 40;
    var y0 = -20 + (r() - 0.5) * 30;
    // Direction spread: focused (0.3) = narrow angle range, scattered (1.0) = wide
    var angCenter = 0.5; // base direction
    var ang = angCenter + (r() - 0.5) * dirSpread * 1.6 + 0.2;
    // Direction influenced by angular spread + network vertical bias
    ang += vertBias; // shift angle by network bias
    var dist = SKY_W * 1.2 + r() * SKY_W * 0.4;
    var x2 = x0 - Math.cos(ang) * dist;
    var y2 = y0 + Math.sin(ang) * dist;
    var mx = (x0 + x2) / 2, my = (y0 + y2) / 2;
    // Turbulence adds jitter to control point
    var jitter = turbulence * 30;
    var cpx = mx + (r() - 0.5) * (40 + jitter);
    var cpy = my - 10 - r() * (30 + jitter);
    var pts = [];
    for (var i = 0; i <= MS; i++) {
      var t = i / MS, it = 1 - t;
      pts.push({x: it * it * x0 + 2 * it * t * cpx + t * t * x2, y: it * it * y0 + 2 * it * t * cpy + t * t * y2});
    }
    var startHead = MS * 0.05 + r() * MS * 0.08;
    var initTrail = [];
    for (var i = Math.max(0, Math.floor(startHead) - 10); i <= Math.floor(startHead); i++) {
      if (i <= MS) initTrail.push(pts[i]);
    }

    var colorIdx = colorPool[Math.floor(r() * colorPool.length)];
    var mc = METEOR_COLORS[colorIdx];

    // Apply soul-driven modifiers
    var baseSpeed = (0.01 + depth * 0.3) * meteorSpeedMult * styleSpeedMult[meteorStyle];
    var baseTail = (120 + depth * 180) * rarityTrailMult * styleTrailMult[meteorStyle] * trailPersist;
    var baseAlpha = (0.06 + depth * 0.3) * meteorAlphaMult * moonBright;

    return {
      pts: pts,
      head: startHead,
      speed: baseSpeed,
      tailLen: baseTail,
      maxR: 0.2 + depth * 0.5,
      maxAlpha: baseAlpha,
      trail: initTrail,
      color: mc,
      turbulence: turbulence * depth,
      flicker: meteorStyle === 'electric' ? 0.3 : (meteorStyle === 'erratic' ? 0.15 : 0),
      style: meteorStyle,
    };
  }

  // Mouse tracking
  var mouseX = -1, mouseY = -1;
  canvas.addEventListener('mousemove', function(e) {
    var rect = canvas.getBoundingClientRect();
    var scaleX = SKY_W / rect.width;
    var scaleY = SKY_H / rect.height;
    mouseX = (e.clientX - rect.left) * scaleX;
    mouseY = (e.clientY - rect.top) * scaleY;
  });
  canvas.addEventListener('mouseleave', function() { mouseX = -1; mouseY = -1; });

  // Offscreen sky canvas for compositing. Mirror the main canvas's
  // DPR so the per-frame star / meteor draws stay pixel-crisp when
  // composited back to the hi-DPI main canvas.
  var offCanvas = document.createElement('canvas');
  var _offDpr = window.devicePixelRatio || 1;
  offCanvas.width = Math.round(SKY_W * _offDpr);
  offCanvas.height = Math.round(SKY_H * _offDpr);
  var offCtx = offCanvas.getContext('2d');
  offCtx.scale(_offDpr, _offDpr);

  function initAnimation() {
    // Seed initial meteors staggered
    for (var i = 0; i < 15; i++) {
      var met = makeMet();
      met.head = MS * 0.05 + (i / 15) * MS * 0.6;
      met.trail = [];
      for (var j = Math.max(0, Math.floor(met.head) - 20); j <= Math.floor(met.head); j++) {
        if (j <= MS) met.trail.push({x: met.pts[j].x, y: met.pts[j].y});
      }
      mets.push(met);
    }

    tick();
  }

  function tick() {
    // Stop if canvas removed from DOM
    if (!document.contains(canvas)) return;

    // Advance meteors
    for (var mi = mets.length - 1; mi >= 0; mi--) {
      var met = mets[mi];
      for (var e = 0; e < 2; e++) {
        met.head += met.speed;
        var idx = Math.min(Math.floor(met.head), MS);
        if (idx >= 0 && idx <= MS) {
          // Turbulence jitter on trail points
          var jx = met.turbulence ? (Math.random() - 0.5) * met.turbulence * 4 : 0;
          var jy = met.turbulence ? (Math.random() - 0.5) * met.turbulence * 4 : 0;
          met.trail.push({x: met.pts[idx].x + jx, y: met.pts[idx].y + jy});
        }
      }
      while (met.trail.length > met.tailLen) met.trail.shift();
      if (met.head >= MS + 10) {
        if (met.trail.length > 3) met.trail.splice(0, 3);
        else mets.splice(mi, 1);
      }
    }
    // Spawn — pattern-driven
    if (spawnCooldown > 0) spawnCooldown--;

    if (spawnPattern === 'steady') {
      // Constant drip — one at a time, steady pace
      if (mets.length < meteorTargetCount && spawnCooldown <= 0) {
        mets.push(makeMet());
        spawnCooldown = 3 + Math.floor(seededRandom() * 5); // 3-7 frame gap
      }
    } else if (spawnPattern === 'waves') {
      // Rhythmic surges — spawn several, pause, repeat
      if (mets.length < meteorTargetCount && spawnCooldown <= 0) {
        var waveSize = 2 + Math.floor(seededRandom() * 3);
        for (var wi = 0; wi < waveSize; wi++) mets.push(makeMet());
        spawnCooldown = 15 + Math.floor(seededRandom() * 25); // longer pause between waves
      }
    } else if (spawnPattern === 'burst') {
      // Sudden clusters — long quiet, then explosion
      if (mets.length < meteorTargetCount * 0.5 && spawnCooldown <= 0) {
        var burstSize = 4 + Math.floor(seededRandom() * 8);
        for (var bsi = 0; bsi < burstSize; bsi++) mets.push(makeMet());
        spawnCooldown = 30 + Math.floor(seededRandom() * 40); // long gap after burst
      }
    } else {
      // Sparse — occasional singles with long gaps
      if (mets.length < meteorTargetCount && spawnCooldown <= 0) {
        mets.push(makeMet());
        spawnCooldown = 8 + Math.floor(seededRandom() * 15); // long gaps
      }
    }

    // Full opaque background fill (rarity-tinted)
    offCtx.fillStyle = _skyC[0];
    offCtx.fillRect(0, 0, SKY_W, SKY_H);

    // Re-stamp planet snapshot. Explicit logical size on drawImage so
    // the DPR-scaled snap canvas maps 1:1 onto the DPR-scaled offscreen
    // (2-arg drawImage uses intrinsic pixel size, which would double-
    // scale since both ctxs are pre-scaled by dpr).
    if (planetSnapReady) offCtx.drawImage(planetSnapCanvas, 0, 0, SKY_W, SKY_H);

    // Twinkling stars — draw over the snapshot with animated alpha
    var _now = Date.now() * 0.001;
    for (var si2 = 0; si2 < _stars.length; si2++) {
      var st = _stars[si2];
      var twinkle = st.baseA * 0.8 * (0.5 + 0.5 * Math.sin(_now * st.speed + st.phase));
      if (twinkle < 0.1) continue;
      offCtx.globalAlpha = twinkle;
      offCtx.fillStyle = st.warm ? '#fff0c8' : '#dce6ff';
      offCtx.beginPath();
      offCtx.arc(st.x, st.y, st.r, 0, Math.PI * 2);
      offCtx.fill();
      // Subtle glow on brighter stars
      if (twinkle > 0.4 && st.r > 0.8) {
        offCtx.globalAlpha = twinkle * 0.15;
        offCtx.beginPath();
        offCtx.arc(st.x, st.y, st.r * 3, 0, Math.PI * 2);
        offCtx.fill();
      }
    }
    offCtx.globalAlpha = 1;

    // --- Zodiac glyphs: glow on mouse proximity ---
    var zSize = 13;
    var glowRadius = 20; // pixels — tight per-glyph
    for (var zi = 0; zi < zodiacPositions.length; zi++) {
      var zp = zodiacPositions[zi];
      var baseAlpha = 0.12;
      var targetAlpha = baseAlpha;
      if (mouseX >= 0) {
        var dx = mouseX - zp.x;
        var dy = mouseY - zp.y;
        var dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < glowRadius) {
          targetAlpha = baseAlpha + (1.0 - baseAlpha) * (1 - dist / glowRadius);
        }
      }
      // Smooth lerp
      zodiacGlow[zi] += (targetAlpha - zodiacGlow[zi]) * 0.08;
      var za = zodiacGlow[zi];

      if (zodiacImgs[zp.idx]) {
        // Brighten dark PNG glyphs: draw to temp canvas, composite as white
        if (!zodiacImgs[zp.idx]._bright) {
          var tc = document.createElement('canvas');
          tc.width = zodiacImgs[zp.idx].width;
          tc.height = zodiacImgs[zp.idx].height;
          var tctx = tc.getContext('2d');
          tctx.drawImage(zodiacImgs[zp.idx], 0, 0);
          tctx.globalCompositeOperation = 'source-in';
          tctx.fillStyle = '#ffffff';
          tctx.fillRect(0, 0, tc.width, tc.height);
          zodiacImgs[zp.idx]._bright = tc;
        }
        offCtx.save();
        offCtx.globalAlpha = za;
        offCtx.drawImage(zodiacImgs[zp.idx]._bright, zp.x - zSize / 2, zp.y - zSize / 2, zSize, zSize);
        offCtx.restore();
      } else {
        offCtx.textAlign = 'center'; offCtx.textBaseline = 'middle';
        offCtx.font = font('400', 6);
        offCtx.fillStyle = 'rgba(255,255,255,' + za + ')';
        offCtx.fillText(zp.sym, zp.x, zp.y);
      }
    }

    // --- Interactive hover: smooth fade cells + planet glow ---
    hoveredPlanet = null;
    var anyCursorHit = false;

    while (cellHover.length < cellRects.length) cellHover.push(0);

    for (var ci = 0; ci < cellRects.length; ci++) {
      var cr = cellRects[ci];
      var hit = mouseX >= 0 && mouseX >= cr.x && mouseX <= cr.x + cr.w && mouseY >= cr.y && mouseY <= cr.y + cr.h;
      cellHover[ci] += hit ? 0.03 : -0.02;
      cellHover[ci] = Math.max(0, Math.min(1, cellHover[ci]));

      if (hit) {
        hoveredPlanet = cellLayout[ci] ? cellLayout[ci].p.name : null;
        anyCursorHit = true;
      }

      if (cellHover[ci] > 0.01) {
        var h = cellHover[ci];
        offCtx.fillStyle = CELL_FILL_HOVER(h);
        roundRect(offCtx, cr.x, cr.y, cr.w, cr.h, 6);
        offCtx.fill();
        offCtx.strokeStyle = CELL_STROKE_HOVER(h);
        offCtx.lineWidth = 1 + h * 0.5;
        roundRect(offCtx, cr.x, cr.y, cr.w, cr.h, 6);
        offCtx.stroke();
      }
    }

    // Hovered planet glow on orbit line + orrery ring
    if (hoveredPlanet) {
      for (var pi = 0; pi < PLANET_DATA.length; pi++) {
        var p = PLANET_DATA[pi];
        if (p.name !== hoveredPlanet) continue;
        var pc = oc[p.name] || [255,255,255];

        var olT = p.lon / 360;
        var olPx = 16 + olT * (SKY_W - 32);
        var olMidY = (olY + olH / 2);
        var olPy = olMidY + Math.sin(olT * Math.PI * 2.5 + 0.5) * olH * 0.4;
        offCtx.fillStyle = 'rgba('+pc[0]+','+pc[1]+','+pc[2]+',0.4)';
        offCtx.beginPath(); offCtx.arc(olPx, olPy, 6, 0, Math.PI * 2); offCtx.fill();

        var a = (p.lon - 90) * Math.PI / 180;
        var rpx = ringCX + Math.cos(a) * ringR * 0.75;
        var rpy = ringCY + Math.sin(a) * ringR * 0.75;
        offCtx.fillStyle = 'rgba('+pc[0]+','+pc[1]+','+pc[2]+',0.35)';
        offCtx.beginPath(); offCtx.arc(rpx, rpy, 6, 0, Math.PI * 2); offCtx.fill();
      }
    }

    canvas.style.cursor = anyCursorHit ? 'pointer' : 'default';

    // Draw meteor trails from stored positions (opaque redraw, not fade wash)
    for (var mi = 0; mi < mets.length; mi++) {
      var met = mets[mi];
      var trail = met.trail;
      if (trail.length < 2) continue;
      var lp = met.head / MS;
      // Lifecycle fade in three stages:
      //   - lp < 0.15: fade in (0 → 1)
      //   - 0.15 ≤ lp ≤ 1: full brightness while traveling
      //   - head ≥ MS: fade out via the trail-shrink in tick() —
      //     trail.length / tailLen drops smoothly from 1 → 0 as
      //     splice(0, 3) eats the tail. Two mechanisms running in
      //     sequence (lp-based then trail-based) caused a flash at
      //     head = MS because the trail-based factor jumped back to
      //     ~1 right after lp-based reached 0. One mechanism = clean.
      var lf;
      if (lp < 0.15) lf = lp / 0.15;
      else if (met.head >= MS) lf = Math.max(0, met.trail.length / met.tailLen);
      else lf = 1;
      for (var ti = 0; ti < trail.length; ti++) {
        var tp = ti / trail.length;
        var a = tp * met.maxAlpha * lf * 1.5;
        var r = 0.3 + tp * met.maxR;
        if (a < 0.005) continue;
        var mc = met.color || [255,255,255];
        offCtx.fillStyle = 'rgba('+mc[0]+','+mc[1]+','+mc[2]+',' + Math.min(a, 0.5) + ')';
        offCtx.beginPath();
        offCtx.arc(trail[ti].x, trail[ti].y, r, 0, Math.PI * 2);
        offCtx.fill();
      }
    }

    // Composite to visible canvas — explicit size, same reason as the
    // planetSnap composite above.
    ctx.drawImage(offCanvas, 0, 0, SKY_W, SKY_H);

    // Celestial rarity traits at the bottom of the band. If the joined
    // line fits, render it on one line at 8px (original look). If not
    // — typical on narrow canvases where two conjunction strings don't
    // fit side-by-side — stack them one per line so each trait can be
    // read comfortably instead of shrinking the font past legibility.
    if (celestialTraits && celestialTraits.length) {
      var _ttW = SKY_W - 20;
      var joined = celestialTraits.join(' \u00b7 ');
      ctx.font = '400 8px "JetBrains Mono", monospace';
      var lines = ctx.measureText(joined).width <= _ttW
        ? [joined]
        : celestialTraits.slice();
      ctx.textAlign = 'center';
      ctx.fillStyle = 'rgba(136, 152, 184, 0.7)';
      var lineH = 11;
      var startY = SKY_H - 10 - (lines.length - 1) * lineH;
      for (var li = 0; li < lines.length; li++) {
        ctx.fillText(lines[li], SKY_W / 2, startY + li * lineH);
      }
      ctx.textAlign = 'left';
    }

    setTimeout(tick, 16);
  }

  // --- INIT ---
  document.fonts.ready.then(function() {
    setTimeout(function() {
      var readyCount = 0;
      var totalLoaders = 2;
      function onLoaderDone() {
        readyCount++;
        if (readyCount >= totalLoaders) {
          renderPlanetSnapshot();
          initAnimation();
        }
      }
      loadPlanets(onLoaderDone);
      loadZodiacImages(onLoaderDone);
    }, 200);
  });
}
