// =====================================================================
// RENDER CERTIFICATE (HTML/CSS + Canvas sky band)
//
// Layout order (trading card style):
//   1. Header: Portrait, Brand, Title, Rarity Badge, Verification, Timestamp, Prompt, Lineage
//   2. Origin Parameters: grid (creator-declared metadata — anything
//      goes; AI-gen pipelines fill in prompt/seed/model, photographers
//      fill in camera/lens/iso, etc. Mirrors the soul's `origin` dict.)
//   3. Birth Temperament: name, summary, traits
//   4. Sky Die: celestial rarity traits, skyband visualization, GPS time-lock
//   5. Machine Die: machine vitals grid, fingerprint, machine rarity traits
//   6. Entropy Die: kernel entropy hex, entropy rarity traits
//   7. Footer
// =====================================================================

// --- Rarity tier lookup (reference-chain thresholds) ---
var RARITY_TIERS = [[88,'Legendary','#d44040'],[72,'Epic','#8a6210'],[55,'Very Rare','#5a2a8a'],[40,'Rare','#2a5090'],[25,'Uncommon','#2a7030'],[0,'Common','#606060']];

function getRarityTier(score) {
  for (var i = 0; i < RARITY_TIERS.length; i++) {
    if (score >= RARITY_TIERS[i][0]) return {name: RARITY_TIERS[i][1], color: RARITY_TIERS[i][2]};
  }
  return {name: 'Common', color: '#a0a0a0'};
}

// --- Bar reconstruction spec (embedded in every band's save metadata) ---
var BAR_SPEC = {
  bar_version: 1,
  magic: '0xAD4E',
  rs_parity_bytes: 6,
  band_width_px: 8,
  bands: ['magenta', 'yellow', 'cyan'],
  pixels_per_bit: {wide: 3, narrow: 2, width_threshold: 1024},
  brightness: {zero: 64, one: 192, threshold: 128},
  payload_format: 'url\\0content_hash_16hex',
  rows: 2
};

// --- Helpers ---
function _hexToRgb(hex) { return [parseInt(hex.slice(1,3),16), parseInt(hex.slice(3,5),16), parseInt(hex.slice(5,7),16)]; }
function _div(cls) { var d = document.createElement('div'); if (cls) d.className = cls; return d; }
function _divider() { return _div('plate-divider'); }

// Thumbnails come from .soul records and may be hostile. Only allow
// inline data: URLs — block remote URLs (http/https/blob) so a
// malicious record can't beacon the viewer's IP/Referer to a remote
// host when the cert renders. Returns '' if the value is not a safe
// data: image URL.
function _safeThumbnail(s) {
  return (typeof s === 'string' && /^data:image\//.test(s)) ? s : '';
}

// Variant C cell colors for canvas bands — brightened rarity tint
// (mixed toward white by 0.3 so dark hexes still read on the dark
// plate), low intensity. Returns base fill/stroke strings + hover
// builders. Each band's drawCell uses this so the cell visuals stay
// consistent across gen/machine/sky.
function rarityCellColors(tierColor) {
  var rgb = _hexToRgb(tierColor || '#a0a0a0');
  var br = Math.round(rgb[0] + (255 - rgb[0]) * 0.3);
  var bg = Math.round(rgb[1] + (255 - rgb[1]) * 0.3);
  var bb = Math.round(rgb[2] + (255 - rgb[2]) * 0.3);
  var tint = br + ',' + bg + ',' + bb;
  return {
    base:        'rgba(' + tint + ',0.07)',
    baseStroke:  'rgba(' + tint + ',0.18)',
    hoverFill:   function(h) { return 'rgba(' + tint + ',' + (h * 0.15) + ')'; },
    hoverStroke: function(h) { return 'rgba(' + tint + ',' + (h * 0.5)  + ')'; }
  };
}

// Set up a canvas for hi-DPI rendering. Canvas CSS width stays
// fluid (the caller sets style.width: 100%); we measure the actual
// rendered width at init time and allocate a DPR-scaled buffer for
// crisp text at any viewport. Band init functions draw in logical
// coordinates — this wrapper pre-scales the context so they stay
// agnostic to DPR. Call AFTER the canvas is attached to the DOM so
// clientWidth is accurate.
// =====================================================================
// Save live certificate plate as PNG.
// Uses html2canvas-pro to walk the live DOM with canvas primitives —
// no SVG foreignObject (which Chromium taints defensively regardless
// of payload). Output canvas is extended by 2 rows so the bar embed
// (embedBarPayload — the canonical codec.js writer) makes the saved
// PNG independently verifiable.
// =====================================================================
function _saveViaAnchor(blob, filename) {
  // Desktop fallback path — synthesizes an <a download> click. On iOS
  // this dumps the file into the Files app (not Photos), which is why
  // the primary path tries Web Share first.
  var u = URL.createObjectURL(blob);
  var a = document.createElement('a');
  a.href = u;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(function() { URL.revokeObjectURL(u); }, 1000);
}

function _saveViaLongPress(blob) {
  // iOS fallback for when navigator.share isn't available or has
  // lost user-activation (html2canvas can take longer than the 5-second
  // activation window, which causes the share API to refuse silently).
  // Long-press on an <img> is iOS's universal "Save to Photos" gesture
  // and works regardless of activation state.
  var url = URL.createObjectURL(blob);
  var overlay = document.createElement('div');
  overlay.style.cssText =
    'position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,0.92);' +
    'display:flex;flex-direction:column;align-items:center;justify-content:center;' +
    'padding:1rem;gap:0.8rem;';
  var instr = document.createElement('p');
  instr.textContent = 'Long-press the certificate, then "Save to Photos."';
  instr.style.cssText =
    'color:#e8e8e8;font:600 0.9rem/1.35 system-ui,-apple-system,sans-serif;' +
    'text-align:center;margin:0;max-width:30rem;';
  var img = document.createElement('img');
  img.src = url;
  img.alt = 'Mememage Certificate';
  img.style.cssText =
    'max-width:92vw;max-height:75vh;object-fit:contain;border-radius:6px;' +
    'box-shadow:0 6px 32px rgba(0,0,0,0.5);' +
    // Crucially: leave default touch behavior + long-press save enabled.
    '-webkit-touch-callout:default;user-select:none;';
  var close = document.createElement('button');
  close.textContent = 'Done';
  close.style.cssText =
    'background:rgba(255,255,255,0.12);color:#fff;border:1px solid rgba(255,255,255,0.35);' +
    'border-radius:999px;padding:0.5rem 1.4rem;font:600 0.82rem/1 system-ui,sans-serif;' +
    'letter-spacing:0.04em;cursor:pointer;';
  var dismiss = function() {
    overlay.remove();
    setTimeout(function() { URL.revokeObjectURL(url); }, 500);
  };
  close.addEventListener('click', dismiss);
  // Tap the empty backdrop (not the image) to dismiss
  overlay.addEventListener('click', function(e) {
    if (e.target === overlay) dismiss();
  });
  overlay.appendChild(instr);
  overlay.appendChild(img);
  overlay.appendChild(close);
  document.body.appendChild(overlay);
}

function _isIOS() {
  return /iPad|iPhone|iPod/.test(navigator.userAgent || '') && !window.MSStream;
}

function _isAndroid() {
  return /Android/i.test(navigator.userAgent || '');
}

function _saveLivePlate(plate, barId, barHash) {
  var SCALE = 2; // 2x for retina output

  return new Promise(function(resolve, reject) {
    if (typeof html2canvas !== 'function') {
      reject(new Error('html2canvas not loaded'));
      return;
    }

    // The override sheet patches rendering gaps in html2canvas-pro
    // that make the saved cert diverge from the live one, AND removes
    // elements that would leave layout-allocated whitespace in the
    // capture even with `ignoreElements` (which skips drawing but
    // doesn't collapse the element's box):
    //   - .plate::before rim — `mask-composite: exclude` not honored;
    //     unmasked gradient washes the plate top→bottom
    //   - .verify-badge box-shadow — drop-shadow approximation leaks
    //     past the rounded clip and looks like a boxy outline
    //   - .gps-unlock — interactive input + button; placeholder text
    //     clips and a static PNG can't be unlocked anyway
    //   - .save-cert-btn — would leave a button-shaped void at the
    //     bottom of the capture
    //   - .cosmic-player — injected as a child of .plate (sticky to
    //     the bottom of the scroll). ignoreElements skips drawing it
    //     but its height stayed in the layout, leaving empty black
    //     space at the bottom of the saved PNG.
    //
    // Inject this BEFORE reading scrollHeight so the plate measures
    // its post-override layout.
    var overrideStyle = document.createElement('style');
    overrideStyle.id = 'save-cert-overrides';
    overrideStyle.textContent =
      '.plate::before, .plate::after { display: none !important; }' +
      '.verify-badge { box-shadow: none !important; }' +
      '.gps-unlock { display: none !important; }' +
      '.save-cert-btn { display: none !important; }' +
      '.verify-mirrors-cluster { display: none !important; }' +
      '.cosmic-player { display: none !important; }' +
      // Live plate has 28px bottom padding to breathe with the player
      // sticky-snapped above it. With the player hidden in the saved
      // PNG, that breathing room reads as wasted space — tighten
      // hard so the footer text sits flush against the lower edge.
      '.plate { padding-bottom: 2px !important; }' +
      // Footer top margin/padding + line-height descender area
      // contribute another ~15px below the text — collapse them.
      '.plate-footer { margin-top: 2px !important; padding-top: 2px !important; }' +
      '.plate-footer-line, .plate-footer-italic { line-height: 1.2 !important; }';
    document.head.appendChild(overrideStyle);

    // Expand the live plate to its full (post-override) scrollHeight.
    // The cert is normally a viewport-clipped scroll container;
    // html2canvas captures rendered layout, so without this we'd get
    // only what's currently on screen.
    var prevHeight = plate.style.height;
    var prevMaxHeight = plate.style.maxHeight;
    var prevMinHeight = plate.style.minHeight;
    var prevOverflow = plate.style.overflow;
    plate.style.height = plate.scrollHeight + 'px';
    plate.style.maxHeight = 'none';
    plate.style.minHeight = '0';
    plate.style.overflow = 'visible';

    function _cleanup() {
      plate.style.height = prevHeight;
      plate.style.maxHeight = prevMaxHeight;
      plate.style.minHeight = prevMinHeight;
      plate.style.overflow = prevOverflow;
      if (overrideStyle.parentNode) {
        overrideStyle.parentNode.removeChild(overrideStyle);
      }
    }

    html2canvas(plate, {
      scale: SCALE,
      backgroundColor: '#0d0d14',
      useCORS: true,
      logging: false
      // No ignoreElements callback needed — every element that should
      // be excluded from the capture is already display:none via the
      // override sheet, which collapses both layout AND drawing.
    }).then(function(rendered) {
      _cleanup();
      try {
        // Extend by 2 rows so the bar lives at the bottom of the
        // final image, exactly like a freshly-minted file.
        var BAR_H = 2;
        var fW = rendered.width;
        var fH = rendered.height + BAR_H;
        var out = document.createElement('canvas');
        out.width = fW;
        out.height = fH;
        var o = out.getContext('2d', { willReadFrequently: true });
        o.fillStyle = '#0d0d14';
        o.fillRect(0, 0, fW, fH);
        o.drawImage(rendered, 0, 0);

        if (typeof embedBarPayload === 'function') {
          // Single canonical writer (codec.js) — the same asym-camouflaged,
          // layout-by-width bar the mint writes (canonical payload, so even-fill
          // is fine here). It picks the layout + ppb itself; the plate is tall,
          // so the cert's own bottom content row is the asym reference.
          var pb = (typeof packPayload === 'function')
            ? packPayload(barId, barHash)
            : new TextEncoder().encode(barId + '\x00' + barHash);
          var px = o.getImageData(0, 0, fW, fH);
          try {
            embedBarPayload(px.data, fW, fH, pb);
            o.putImageData(px, 0, 0);
          } catch (e) { /* too narrow for the payload — save without a bar */ }
        }

        out.toBlob(function(blob) {
          if (!blob) { reject(new Error('toBlob returned null')); return; }
          var filename = barId + '.certificate.png';
          var ios = _isIOS();

          // iOS path: html2canvas often takes longer than the 5-second
          // transient-user-activation window, so navigator.share silently
          // refuses by the time we get here. Skip straight to the
          // long-press overlay — that's iOS's native "save image"
          // gesture and works regardless of activation state.
          if (ios) {
            _saveViaLongPress(blob);
            resolve();
            return;
          }

          // Non-iOS MOBILE (Android Chrome) — Web Share API gives a proper
          // "save image" / "share" sheet. Gate on the mobile UA, NOT on
          // navigator.canShare alone: Windows Chrome/Edge ALSO report
          // canShare({files})===true, so an unguarded check hijacked the
          // desktop "Save Certificate" download with the OS share sheet.
          // Desktop (Windows/macOS/Linux) must always take the anchor
          // download path — it's deterministic and never re-encodes.
          if (_isAndroid()) {
            try {
              var probe = new File([blob], filename, { type: 'image/png' });
              if (navigator.canShare && navigator.canShare({ files: [probe] })) {
                navigator.share({ files: [probe], title: 'Mememage Certificate' })
                  .then(resolve)
                  .catch(function(err) {
                    if (err && err.name === 'AbortError') { resolve(); return; }
                    _saveViaAnchor(blob, filename);
                    resolve();
                  });
                return;
              }
            } catch (e) { /* File ctor or canShare unsupported — fall through */ }
          }

          // Desktop fallback — synthetic anchor click.
          _saveViaAnchor(blob, filename);
          resolve();
        }, 'image/png');
      } catch (err) {
        reject(err);
      }
    }).catch(function(err) {
      _cleanup();
      reject(err);
    });
  });
}

// Lightweight non-blocking toast. Used for save-cert failures and any
// other "user did a thing, here's quick feedback" surface. Auto-fades
// after ~3.5s; alert()'s synchronous block was unnecessary friction.
function _showToast(text) {
  var t = document.createElement('div');
  t.className = 'mm-toast';
  t.textContent = text;
  document.body.appendChild(t);
  // Force layout so the transition animates from opacity 0.
  t.offsetHeight;
  t.classList.add('mm-toast-visible');
  setTimeout(function() {
    t.classList.remove('mm-toast-visible');
    setTimeout(function() {
      if (t.parentNode) t.parentNode.removeChild(t);
    }, 400);
  }, 3500);
}

function _setupHiDpi(canvas, fallbackW, heightForWidth) {
  var dpr = window.devicePixelRatio || 1;
  var cssW = canvas.clientWidth || fallbackW;
  var cssH = typeof heightForWidth === 'function'
    ? heightForWidth(cssW)
    : heightForWidth;
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  canvas.style.height = cssH + 'px';
  canvas.getContext('2d').scale(dpr, dpr);
  return { w: cssW, h: cssH };
}
function _sectionLabel(text) { var d = _div('plate-section-label'); d.textContent = text; return d; }
function _copyable(el, text) {
  el.title = 'Click to copy';
  el.style.cursor = 'pointer';
  el.addEventListener('click', function() {
    navigator.clipboard.writeText(text).then(function() {
      el.style.borderColor = 'rgba(74,154,74,0.3)';
      setTimeout(function() { el.style.borderColor = ''; }, 800);
    });
  });
}

function _renderDieTraits(plate, dieData, tierColor) {
  if (!dieData || !dieData.length) return;
  var traits = _div('rarity-traits');
  traits.textContent = dieData.map(function(t) { return t.trait; }).join(' \u00b7 ');
  plate.appendChild(traits);
}

function _renderSigil(plate, sigil, tierColor) {
  if (!sigil) return;
  var sigilDiv = _div('rarity-traits');
  sigilDiv.style.cssText = 'margin-top:6px;color:' + tierColor + ';font-style:italic;';
  sigilDiv.textContent = '\u2728 Sigil \u2014 0xAD4E found in entropy';
  plate.appendChild(sigilDiv);
}

function renderCert(meta, options) {
  // options: { target: Element, activateLayout: bool, injectPlayer: bool }
  // Defaults preserve the decoder's original behavior — render into #certWrap,
  // activate the two-panel sidebar, inject the music player.
  options = options || {};
  var certWrap = options.target || document.getElementById('certWrap');
  var activateLayout = options.activateLayout !== false;
  var injectPlayer = options.injectPlayer !== false;

  // Anchor this record's identifier in history state so the browser
  // back button can traverse the chain. Without this, clicking β to
  // walk to the parent pushes a new entry but the previous entry
  // (the cert we just left) had no .state.id — popstate's handler
  // would skip it and the back button would feel broken.
  //
  // replaceState (not pushState) — every cert render REPLACES the
  // current entry's state. New pushState entries layer above. The
  // navigation flow then becomes:
  //   1. Cert A renders → replaceState({id: A})
  //   2. Click β → lookupById(B) → pushState({id: B})
  //   3. Cert B renders → replaceState({id: B}) (no-op, same value)
  //   4. Back button → popstate { id: A } → lookupById(A, false)
  //   5. Cert A renders again → replaceState({id: A})
  // Both backward and forward navigation now traverse the chain.
  try {
    var anchorId = meta._identifier || meta.identifier;
    if (anchorId && history.replaceState) {
      history.replaceState({id: anchorId}, '', '#');
    }
  } catch (e) { /* history API failures shouldn't block the cert */ }

  // Fade out any playing audio before destroying the certificate
  if (typeof CosmicPlayer !== 'undefined') CosmicPlayer.dismiss();

  // If the panel is already visible (e.g., PanelSwap is driving the
  // swap animation), don't toggle .visible — re-adding it re-triggers
  // @keyframes panelFadeIn and stacks with PanelSwap's intro, showing
  // as a double fade-in. Swap content in place and let PanelSwap own
  // the animation.
  var wasVisible = certWrap.classList.contains('visible');
  certWrap.innerHTML = '';
  if (!wasVisible) {
    // First reveal — clear only transient animation state; preserve
    // structural classes (panel-right, panel-right-has-player, etc.).
    certWrap.classList.remove('visible', 'dismissing');
  }

  var birth = meta.birth || {};
  var m = birth.machine || {};
  var rarity = meta.rarity || {};

  // --- Build data arrays from meta ---
  // The creator-declared metadata now lives in `meta.origin` (free-form
  // dict). Anything outside origin is system-managed (identity, birth,
  // rarity, chunks, etc.) and gets rendered in its own dedicated panel.
  var origin = meta.origin || {};
  var PROMPT = origin.prompt || meta.prompt || '';   // meta.prompt fallback for legacy V4-era records
  var TIMESTAMP = meta.conceived || meta.timestamp || '';

  var GEN_PARAMS = [];
  // span: 3 = full width, 2 = two-thirds, 1 = one-third of the grid.
  // Preferred layout for common generation fields (Seed/Size full-width,
  // Steps|CFG|Guidance on one row, etc). Anything in origin that's NOT one of these known
  // keys gets rendered after as a generic title-cased label/value pair —
  // photographer/screenshot/drawing pipelines can drop any custom field.
  var _claimedKeys = {prompt: 1};   // prompt is rendered separately above the panel
  function _push(key, label, value, span) {
    GEN_PARAMS.push({l: label, v: '' + value, span: span || undefined});
    _claimedKeys[key] = 1;
  }
  if (origin.seed !== undefined) _push('seed', 'Seed', origin.seed, 3);
  if (meta.width && meta.height) GEN_PARAMS.push({l: 'Size', v: meta.width + ' \u00d7 ' + meta.height, span: 3});
  // Numeric row (keep Steps / CFG / Guidance together visually)
  if (origin.steps !== undefined) _push('steps', 'Steps', origin.steps);
  if (origin.cfg_scale !== undefined) _push('cfg_scale', 'CFG', origin.cfg_scale);
  if (origin.guidance !== undefined) _push('guidance', 'Guidance', origin.guidance);
  // Second short row
  if (origin.denoise !== undefined) _push('denoise', 'Denoise', origin.denoise);
  if (origin.sampler) _push('sampler', 'Sampler', origin.sampler);
  if (origin.scheduler) _push('scheduler', 'Scheduler', origin.scheduler);
  if (origin.model) _push('model', 'Model', origin.model, 3);
  if (origin.lora) _push('lora', 'LoRA', origin.lora, 3);
  if (origin.lora_strength !== undefined) _push('lora_strength', 'LoRA Str', origin.lora_strength);
  // LoRAs, plural: a list, one entry per applied LoRA. Each entry is a
  // [name, weight] pair, a {name/strength} dict, or a bare name string. The
  // generic catch-all below skips arrays, so without this they'd vanish from
  // the certificate. One full-width row each.
  if (Array.isArray(origin.loras) && origin.loras.length) {
    for (var _li = 0; _li < origin.loras.length; _li++) {
      var _lora = origin.loras[_li], _ln, _lw;
      if (Array.isArray(_lora)) { _ln = _lora[0]; _lw = _lora[1]; }
      else if (_lora && typeof _lora === 'object') {
        _ln = _lora.name || _lora.lora || _lora.file;
        _lw = (_lora.strength !== undefined) ? _lora.strength : _lora.weight;
      } else { _ln = _lora; }
      if (_ln === undefined || _ln === null || _ln === '') continue;
      var _lval = (_lw !== undefined && _lw !== null && _lw !== '')
        ? (_ln + '  ×' + _lw) : ('' + _ln);
      var _llabel = (origin.loras.length > 1) ? ('LoRA ' + (_li + 1)) : 'LoRA';
      GEN_PARAMS.push({l: _llabel, v: _lval, span: 3});
    }
  }
  _claimedKeys['loras'] = 1;

  // Honest fallback: render ANY non-empty origin value, including arrays and
  // nested dicts. A list doesn't fit one cell, but silently dropping it would
  // make the certificate LIE about what the creator declared — the cert's
  // promise is that it shows whatever is in `origin`, no special-casing by
  // image source (AI gen / EXIF photo / hand-entered all flow through here).
  // Collections flatten to a compact readable string; empty ones become ''
  // (skipped, since there's genuinely nothing to show).
  function _flattenOriginValue(v) {
    if (v === null || v === undefined) return '';
    if (Array.isArray(v)) {
      return v.map(function(el) {
        if (Array.isArray(el)) return el.join(' ×');   // [name, weight] → "name ×weight"
        if (el && typeof el === 'object') return _flattenOriginValue(el);
        return '' + el;
      }).filter(function(s){ return s !== ''; }).join(', ');
    }
    if (typeof v === 'object') {
      return Object.keys(v).map(function(k) {
        var s = _flattenOriginValue(v[k]);
        return s === '' ? '' : (k + ': ' + s);
      }).filter(function(s){ return s !== ''; }).join(', ');
    }
    return '' + v;
  }

  // Generic catch-all: any other origin key the creator declared
  // (photographer / drawing / screenshot pipelines populate whatever
  // makes sense for their workflow — camera, lens, ISO, software, …).
  var _origKeys = Object.keys(origin).sort();
  for (var _ki = 0; _ki < _origKeys.length; _ki++) {
    var _k = _origKeys[_ki];
    if (_claimedKeys[_k]) continue;
    var _vs = _flattenOriginValue(origin[_k]);
    if (_vs === '') continue;  // nothing to show (null / empty / empty collection)
    // Title-case the key, then restore common acronyms so EXIF-derived
    // labels read "GPS Latitude" / "ISO", not "Gps Latitude" / "Iso".
    var _label = _k.replace(/[_-]/g, ' ').replace(/\b\w/g, function(c){return c.toUpperCase();})
      .replace(/\b(Gps|Iso|Id|Url|Rgb|Gpu|Cpu|Dpi|Hdr|Exif|Ai|Ram)\b/g, function(m){return m.toUpperCase();});
    // Pack short creator/EXIF fields tighter instead of one full-width row
    // each: a 3-char ISO or "f/1.8" no longer hogs a whole line, so a photo's
    // dozen small EXIF fields fill ~a third the rows. span 1 = one-third
    // (3 per row), 2 = two-thirds, 3 = full. gen-band truncates anything that
    // overflows a cell, so these thresholds only affect packing, not clipping.
    var _span = (_vs.length <= 16 && _label.length <= 16) ? 1
              : (_vs.length <= 34 ? 2 : 3);
    GEN_PARAMS.push({l: _label, v: _vs, span: _span});
  }

  // Build PLANET_DATA from birth
  var planetSymbols = {sun:'\u2609', moon:'\u263D', mercury:'\u263F', venus:'\u2640', mars:'\u2642', jupiter:'\u2643', saturn:'\u2644'};
  var planetLabels = {sun:'Sun', moon:'Moon', mercury:'Mercury', venus:'Venus', mars:'Mars', jupiter:'Jupiter', saturn:'Saturn'};
  var PLANET_DATA = [];
  for (var bi = 0; bi < BODIES.length; bi++) {
    var bk = BODIES[bi].k;
    var val = birth[bk];
    if (!val) continue;
    var lon = parseDegrees(val);  // handles dict + legacy string
    if (lon === null) continue;
    var pd = {
      name: bk, sym: planetSymbols[bk] || '', label: planetLabels[bk] || bk,
      sign: signName(val), deg: signDegree(val), lon: lon
    };
    if (bk === 'moon' && birth.moon_phase) {
      // Carry the formatted phase string ("First Quarter (81.2%)") so
      // downstream sky-band code can render directly without re-parsing.
      pd.phase = formatMoonPhase(birth.moon_phase);
    }
    PLANET_DATA.push(pd);
  }
  var hasSky = PLANET_DATA.length > 0;

  // Build MACHINE from birth.machine. `span` controls the grid layout
  // downstream in machine-band — 3 = full width, 1.5 = half row,
  // 1 = one of three. Row totals must sum to 3.
  //   [ CPU                           ] span 3
  //   [ Cores | Active | GPU          ] 1|1|1
  //   [ RAM | Compressed | Free       ] 1|1|1
  //   [ Load                          ] span 3
  //   [ Power | Speculative | Purgeable ] 1|1|1
  //   [ Disk I/O                      ] span 3
  //   [ Net ↑ | Net ↓                 ] 1.5|1.5
  var MACHINE = [];
  var machineFields = [
    {k:'cpu', l:'CPU', span: 3},
    {k:'cores', l:'Cores', fmt: formatCores},
    {k:'mem_active', l:'Active', fmt: formatBytes},
    {k:'gpu', l:'GPU', fmt: function(v){return (typeof v === 'number') ? v + ' cores' : String(v);}},
    {k:'ram', l:'RAM', fmt: formatRam},
    {k:'mem_compressed', l:'Compressed', fmt: formatBytes},
    {k:'mem_free', l:'Free', fmt: formatBytes},
    {k:'load', l:'Load', span: 3, fmt: formatLoad},
    {k:'power', l:'Power', fmt: formatPower},
    {k:'speculative_pages', l:'Speculative'},
    {k:'purgeable_pages', l:'Purgeable'},
    {k:'disk_io', l:'Disk I/O', span: 3, fmt: formatDiskIO},
    {k:'net_tx', l:'Net \u2191', span: 1.5, fmt: formatBytes},
    {k:'net_rx', l:'Net \u2193', span: 1.5, fmt: formatBytes}
  ];
  for (var fi = 0; fi < machineFields.length; fi++) {
    var f = machineFields[fi];
    var v = m[f.k];
    if (v === undefined || v === null) continue;
    MACHINE.push({l: f.l, v: f.fmt ? f.fmt(v) : '' + v, span: f.span || 1});
  }

  var KERNEL_ENTROPY = m.entropy || '';

  // Build SKY_READING from READINGS
  var skyReadings = [];
  for (var pi = 0; pi < PLANET_DATA.length; pi++) {
    var p = PLANET_DATA[pi];
    var r = '';
    if (p.name === 'moon') {
      var pn = (p.phase || '').split('(')[0].trim();
      r = READINGS.moon[pn] || '';
    } else {
      r = (READINGS[p.name] || {})[p.sign] || '';
    }
    if (r) skyReadings.push(r);
  }
  var SKY_READING = '';
  if (skyReadings.length > 0) SKY_READING = skyReadings[0];
  if (skyReadings.length > 1) SKY_READING += ' ' + skyReadings[1];

  // GPS data
  var GPS_CIPHER = '';
  var GPS_MODULUS = '';
  if (meta.gps_time_locked) {
    GPS_CIPHER = meta.gps_time_locked.ct || meta.gps_time_locked.ciphertext || '';
    if (meta.gps_time_locked.N) GPS_MODULUS = meta.gps_time_locked.N;
  }

  // ===================================================================
  // TIME DECAY — compute age tier
  // ===================================================================
  var ageTier = 'fresh';
  if (TIMESTAMP) {
    var ageSecs = (Date.now() - new Date(TIMESTAMP).getTime()) / 1000;
    if (ageSecs > 31536000)      ageTier = 'ancient';
    else if (ageSecs > 2592000)  ageTier = 'vintage';
    else if (ageSecs > 604800)   ageTier = 'aged';
    else if (ageSecs > 86400)    ageTier = 'young';
    else                         ageTier = 'fresh';
  }

  // Rarity tier
  // V1: rarity_score is derived from the dice dict, not persisted.
  // RarityScore.fromRecord also reads any legacy persisted value.
  var rarityScore = (typeof RarityScore !== 'undefined')
    ? RarityScore.fromRecord(meta) : (meta.rarity_score || 0);
  var tier = getRarityTier(rarityScore);
  var tierName = tier.name;
  var tierColor = tier.color;
  var rarityTier = tierName.toLowerCase().replace(' ', '');

  // Bar payload fragments for triptych reconstruction
  var barId = meta._identifier || '';
  var barHash = meta._content_hash || '';
  // Bar fragments — split the canonical bar payload (mememage-XXXX\0<hash>)
  // across the three bands. Combining gen + sky + machine in order
  // reconstructs the same payload that lives in the full-cert bar.
  // No URL prefix; the bar stays source-agnostic.
  var barFragments = {
    gen:     barId,         // "mememage-XXXXXXXXXXXX"
    sky:     '\x00',        // canonical null separator
    machine: barHash        // 16 hex
  };

  // ===================================================================
  // 1. HEADER: Portrait, Brand, Title, Rarity, Verification, Time, Prompt, Lineage
  // ===================================================================
  var plate = document.createElement('div');
  plate.className = 'plate plate-age-' + ageTier + ' plate-rarity-' + rarityTier;
  // Expose the rarity color as a CSS variable so descendants (GPS
  // section, etc.) can derive their own tints via color-mix() instead
  // of hardcoding a single hue per element.
  plate.style.setProperty('--rarity-color', tierColor);

  var plateBg = _div('plate-bg');
  plate.appendChild(plateBg);

  // plate-inner-highlight removed — was drawing a white line across the top
  // Brushed-metal grain canvas removed — the hairline pattern was a nice
  // idea (cf. fountain-pen-on-foil) but too busy under the rest of the
  // cert content. plateBg's rarity-tinted gradient carries the material
  // alone now. The CSS class .plate-grain still exists but no element
  // uses it; theme.css's L1 reskin lever remains structurally intact.

  // Constellation pattern — destiny map behind the header
  // Constellation pattern seed: constellation_hash (SHA-256 of celestial state, 64 bits)
  // The sky that witnessed the birth shapes the pattern. Every few minutes of real time
  // produces a different celestial snapshot, so every constellation is unique.
  // Fallback chain: constellation_hash → constellation_name → content_hash (legacy)
  var conSeed = meta.constellation_hash || meta.constellation_name || meta.content_hash || meta._content_hash || '';
  // Record's position within its constellation cycle ("which Bayer
  // letter is this record"). The record's top-level index (0-based) is the
  // primary source; fall back to any cycling layer's chunk index for records
  // that don't carry it.
  var myChunkIdx = (typeof meta.constellation_index === 'number') ? meta.constellation_index : -1;
  if (myChunkIdx < 0 && meta.chunks && typeof meta.chunks === 'object') {
    var _roles = Object.keys(meta.chunks);
    for (var _ri = 0; _ri < _roles.length; _ri++) {
      var _e = meta.chunks[_roles[_ri]];
      if (_e && typeof _e.index === 'number' && typeof _e.total === 'number') { myChunkIdx = _e.index; break; }
    }
  }
  var isHeartStar = meta.heart_star_id && meta.heart_star_id === meta._identifier;
  if (isHeartStar) myChunkIdx = 0;

  if (conSeed && typeof CosmicPlanetarium !== 'undefined' && CosmicPlanetarium.generateLayout) {
    var CON_W = 500, CON_H = 180;
    var conCanvas = document.createElement('canvas');
    conCanvas.width = CON_W; conCanvas.height = CON_H;
    // sqrt(2) scale — celestial dimension overflows the mortal plate
    conCanvas.style.cssText = 'position:absolute;top:10px;left:-5%;width:110%;height:auto;opacity:0.35;pointer-events:none;z-index:1';
    var conCtx = conCanvas.getContext('2d');

    // Same generator the planetarium uses — keyed off constellation_hash
    // so the cert backdrop pattern and the planetarium overlay
    // produce the IDENTICAL constellation shape. Layout returns
    // stars in [-0.5, 0.5]² centered space + edge list.
    var conLayout = CosmicPlanetarium.generateLayout(conSeed, meta.constellation_size);
    var cStars = conLayout.stars.map(function(s) {
      return { x: (s.x + 0.5) * CON_W, y: (s.y + 0.5) * CON_H };
    });
    var cEdges = conLayout.edges;

    // Draw order: etched groove lines, then stars on top
    // Three passes: dark shadow (shifted down), main groove (center), light edge (shifted up)

    // Etched groove: offset perpendicular to each edge for angle-independent etching
    // Light source from top — shadow on upper-left side, highlight on lower-right
    for (var cei = 0; cei < cEdges.length; cei++) {
      var x0 = cStars[cEdges[cei][0]].x, y0 = cStars[cEdges[cei][0]].y;
      var x1 = cStars[cEdges[cei][1]].x, y1 = cStars[cEdges[cei][1]].y;
      // Perpendicular normal (rotated 90 degrees, normalized)
      var dx = x1 - x0, dy = y1 - y0;
      var len = Math.sqrt(dx * dx + dy * dy);
      if (len < 0.1) continue;
      // Normal pointing toward the light (upper-left)
      var nx = -dy / len, ny = dx / len;
      // Ensure normal has a consistent "toward light" direction (upper side)
      if (ny > 0) { nx = -nx; ny = -ny; }
      var off = 0.8;
      // Dark shadow (offset toward light — this is the shadow inside the groove on the lit side)
      conCtx.strokeStyle = 'rgba(0,0,0,0.55)';
      conCtx.lineWidth = 0.6;
      conCtx.beginPath(); conCtx.moveTo(x0 + nx * off, y0 + ny * off); conCtx.lineTo(x1 + nx * off, y1 + ny * off); conCtx.stroke();
      // Bright highlight (offset away from light — bottom lip catches light)
      conCtx.strokeStyle = 'rgba(255,255,255,0.45)';
      conCtx.lineWidth = 0.7;
      conCtx.beginPath(); conCtx.moveTo(x0 - nx * off, y0 - ny * off); conCtx.lineTo(x1 - nx * off, y1 - ny * off); conCtx.stroke();
      // Main groove line (center)
      conCtx.strokeStyle = 'rgba(255,255,255,0.6)';
      conCtx.lineWidth = 0.5;
      conCtx.beginPath(); conCtx.moveTo(x0, y0); conCtx.lineTo(x1, y1); conCtx.stroke();
    }

    // 3. Stars — animated twinkle via setTimeout
    var _tcR = 160, _tcG = 160, _tcB = 160;
    if (tierColor) { _tcR = parseInt(tierColor.slice(1,3),16); _tcG = parseInt(tierColor.slice(3,5),16); _tcB = parseInt(tierColor.slice(5,7),16); }

    // Save the line canvas state (lines don't change)
    var lineSnapshot = conCtx.getImageData(0, 0, CON_W, CON_H);

    // Star twinkle parameters — each star gets a random phase and period.
    // Drives off cStars.length so a layout function that returns fewer
    // or more stars still animates correctly.
    var twinklePhase = [], twinklePeriod = [];
    for (var tsi = 0; tsi < cStars.length; tsi++) {
      twinklePhase.push(Math.random() * 6.2832);
      twinklePeriod.push(2000 + Math.random() * 4000); // 2-6 second cycle
    }

    function drawStars() {
      // Restore lines (clear stars from previous frame)
      conCtx.putImageData(lineSnapshot, 0, 0);

      var now = Date.now();
      for (var csi = 0; csi < cStars.length; csi++) {
        var cs = cStars[csi];
        var twinkle = 0.85 + 0.15 * Math.sin(now / twinklePeriod[csi] * 6.2832 + twinklePhase[csi]); // 0.85-1.0

        var shadowR, coreR, shadowPeak, shadowHold;
        if (csi === 0) { shadowR = 14; coreR = 4; shadowPeak = 0.9; shadowHold = 0.45; }
        else if (csi === myChunkIdx) { shadowR = 11; coreR = 3.5; shadowPeak = 0.9; shadowHold = 0.35; }
        else { shadowR = 7; coreR = 2.7; shadowPeak = 0.7; shadowHold = 0.25; }

        // Spherical dent — ball bearing pressed into metal
        var dentR = coreR + 7;

        // 1. Dark concavity (the hollow)
        var wellGrad = conCtx.createRadialGradient(cs.x, cs.y, coreR * 0.3, cs.x, cs.y, dentR);
        wellGrad.addColorStop(0, 'rgba(0,0,0,' + (shadowPeak * 0.9) + ')');
        wellGrad.addColorStop(0.4, 'rgba(0,0,0,' + (shadowHold * 0.6) + ')');
        wellGrad.addColorStop(1, 'rgba(0,0,0,0)');
        conCtx.fillStyle = wellGrad;
        conCtx.beginPath(); conCtx.arc(cs.x, cs.y, dentR, 0, 6.2832); conCtx.fill();

        // 2. Dark crescent on top (rim blocks light going into the hole)
        var rimGrad = conCtx.createRadialGradient(cs.x, cs.y - dentR * 0.35, dentR * 0.3, cs.x, cs.y, dentR);
        rimGrad.addColorStop(0, 'rgba(0,0,0,' + (shadowPeak * 0.5) + ')');
        rimGrad.addColorStop(0.5, 'rgba(0,0,0,' + (shadowPeak * 0.15) + ')');
        rimGrad.addColorStop(1, 'rgba(0,0,0,0)');
        conCtx.fillStyle = rimGrad;
        conCtx.beginPath(); conCtx.arc(cs.x, cs.y, dentR, 0, 6.2832); conCtx.fill();

        // 3. Light crescent on bottom (inner surface facing the light)
        var btmGrad = conCtx.createRadialGradient(cs.x, cs.y + dentR * 0.35, dentR * 0.3, cs.x, cs.y, dentR);
        btmGrad.addColorStop(0, 'rgba(255,255,255,' + (shadowPeak * 0.55) + ')');
        btmGrad.addColorStop(0.5, 'rgba(255,255,255,' + (shadowPeak * 0.2) + ')');
        btmGrad.addColorStop(1, 'rgba(255,255,255,0)');
        conCtx.fillStyle = btmGrad;
        conCtx.beginPath(); conCtx.arc(cs.x, cs.y, dentR, 0, 6.2832); conCtx.fill();

        // 4. Specular highlight — light pooling at the bottom of the bowl
        var specX = cs.x + dentR * 0.1, specY = cs.y + dentR * 0.2;
        var specR = dentR * 0.3;
        var specGrad = conCtx.createRadialGradient(specX, specY, 0, specX, specY, specR);
        specGrad.addColorStop(0, 'rgba(255,255,255,' + (shadowPeak * 0.25) + ')');
        specGrad.addColorStop(0.6, 'rgba(255,255,255,' + (shadowPeak * 0.06) + ')');
        specGrad.addColorStop(1, 'rgba(255,255,255,0)');
        conCtx.fillStyle = specGrad;
        conCtx.beginPath(); conCtx.arc(specX, specY, specR, 0, 6.2832); conCtx.fill();
        conCtx.beginPath(); conCtx.arc(cs.x, cs.y, dentR, 0, 6.2832); conCtx.fill();

        var isHeart = csi === 0;
        var isCurrent = csi === myChunkIdx;
        var isHeartAndCurrent = isHeart && isCurrent;
        var spikeR = isHeartAndCurrent ? _tcR : 255;
        var spikeG = isHeartAndCurrent ? _tcG : 250;
        var spikeB = isHeartAndCurrent ? _tcB : 230;

        if (isHeart) {
          var heartTwinkle = 0.85 + 0.15 * Math.sin(now / 3000 * 6.2832 + twinklePhase[0]);
          var spikeRotation = (now / 60000) * 6.2832; // one full rotation per 60 seconds
          // + spikes (cardinal, longer)
          conCtx.strokeStyle = 'rgba(' + spikeR + ',' + spikeG + ',' + spikeB + ',' + heartTwinkle + ')';
          conCtx.lineWidth = 1.5;
          var plusLen = 18;
          for (var sp = 0; sp < 4; sp++) {
            var spAng = sp * Math.PI / 2 + spikeRotation;
            conCtx.beginPath();
            conCtx.moveTo(cs.x + Math.cos(spAng) * (coreR + 1), cs.y + Math.sin(spAng) * (coreR + 1));
            conCtx.lineTo(cs.x + Math.cos(spAng) * plusLen, cs.y + Math.sin(spAng) * plusLen);
            conCtx.stroke();
          }
          // × spikes (diagonal, shorter)
          conCtx.strokeStyle = 'rgba(' + spikeR + ',' + spikeG + ',' + spikeB + ',' + (heartTwinkle * 0.8) + ')';
          conCtx.lineWidth = 1.0;
          var crossLen = 12;
          for (var sp2 = 0; sp2 < 4; sp2++) {
            var spAng2 = sp2 * Math.PI / 2 + Math.PI / 4 + spikeRotation;
            conCtx.beginPath();
            conCtx.moveTo(cs.x + Math.cos(spAng2) * (coreR + 1), cs.y + Math.sin(spAng2) * (coreR + 1));
            conCtx.lineTo(cs.x + Math.cos(spAng2) * crossLen, cs.y + Math.sin(spAng2) * crossLen);
            conCtx.stroke();
          }
          conCtx.fillStyle = isHeartAndCurrent ? 'rgba(' + _tcR + ',' + _tcG + ',' + _tcB + ',1)' : 'rgba(255,245,220,1)';
        } else {
          var coreBright = Math.round(200 + 55 * twinkle); // 247-255, never dim
          conCtx.fillStyle = 'rgba(' + coreBright + ',' + coreBright + ',' + coreBright + ',1)';
        }

        if (isCurrent) {
          conCtx.strokeStyle = 'rgba(' + _tcR + ',' + _tcG + ',' + _tcB + ',1)';
          conCtx.lineWidth = 2.5;
          conCtx.beginPath(); conCtx.arc(cs.x, cs.y, coreR + 5, 0, 6.2832); conCtx.stroke();
        }
        conCtx.beginPath(); conCtx.arc(cs.x, cs.y, coreR, 0, 6.2832); conCtx.fill();
      }
    }

    drawStars();
    // Twinkle loop — slow, cosmic pace
    (function twinkleLoop() {
      setTimeout(function() {
        drawStars();
        twinkleLoop();
      }, 80); // ~12fps — gentle, not flashy
    })();

    plate.appendChild(conCanvas);
  }

  // Portrait — the face is identity, shown on ANY entry path whenever a
  // usable (plaintext) thumbnail exists: By Sight/Soul (image dropped),
  // By Word lookup, or Greek-letter chain traversal. Official policy:
  // looking a record up by identifier or walking to it by Bayer letter
  // is enough to see whose star it is. Dark-matter records stay faceless
  // until unlock — _safeThumbnail rejects the ciphertext envelope, so a
  // locked dark record shows no portrait and the decrypted thumbnail
  // surfaces only once the chain password is supplied (auto-applied to
  // siblings during traversal). The heavier body view — gen/sky/machine
  // canvas bands, vitals, BIRTHPLACE — stays gated on a dropped image
  // (_imageWasPresent below); those are earned by holding the body, the
  // portrait is not. (Supersedes the earlier "stargazing — fingerprint,
  // not face" rule, which gated the portrait on image-present too.)
  var _imageWasPresent = !!window._lastDecodedCanvas;
  var safeThumb = _safeThumbnail(meta.thumbnail);
  if (safeThumb) {
    var portraitWrap = _div();
    portraitWrap.style.cssText = 'text-align:center;margin-bottom:12px;position:relative;z-index:3';
    var portraitRing = _div();
    portraitRing.style.cssText = 'display:inline-block;width:64px;height:64px;border-radius:50%;overflow:hidden;border:2px solid rgba(0,0,0,0.08);box-shadow:0 2px 8px rgba(0,0,0,0.1)';
    var portraitImg = document.createElement('img');
    portraitImg.src = safeThumb;
    portraitImg.style.cssText = 'width:100%;height:100%;object-fit:cover';
    portraitRing.appendChild(portraitImg);
    portraitWrap.appendChild(portraitRing);
    plate.appendChild(portraitWrap);
  }

  // Brand + Title + Rarity (integrated header)
  var header = _div('plate-header');
  var headerHtml = '<div class="plate-brand">M E M E M A G E</div><div class="plate-title">Certificate of Origin</div>';
  // V1: derive from rarity dict; rarityScore already computed up top.
  if (meta.rarity) {
    // Lighten rarity color toward white for readability against drop shadow
    var _rc = _hexToRgb(tierColor);
    var _lR = Math.min(255, _rc[0] + Math.round((255 - _rc[0]) * 0.4));
    var _lG = Math.min(255, _rc[1] + Math.round((255 - _rc[1]) * 0.4));
    var _lB = Math.min(255, _rc[2] + Math.round((255 - _rc[2]) * 0.4));
    headerHtml += '<div style="margin-top:8px"><span class="rarity-badge" style="color:rgb(' + _lR + ',' + _lG + ',' + _lB + ');">' + escapeHtml(tierName.toUpperCase()) + ' (' + rarityScore + ')</span></div>';
  }
  header.innerHTML = headerHtml;
  plate.appendChild(header);

  // Verification badges — hidden in sample mode
  var vf = meta._verification;
  if (vf && !isSample) {
    var badgeWrap = _div('verify-badge-group');

    // WITNESSED badge (hash integrity)
    var badgeClass = vf.status === 'bar_verified' ? 'verified' : vf.status;
    var badge = _div('verify-badge verify-' + badgeClass);
    if (vf.status === 'verified' || vf.status === 'bar_verified') {
      badge.innerHTML = '<span class="verify-icon">&#x2713;</span> WITNESSED';
      badge.title = 'Hash match \u2014 body and soul joined, sealed by spirit';
    } else if (vf.status === 'tampered') {
      badge.innerHTML = '<span class="verify-icon">&#x2717;</span> ALTERED';
      badge.title = 'Hash mismatch \u2014 soul rejects the body';
    } else if (vf.status === 'unverified') {
      badge.innerHTML = '<span class="verify-icon">&#x25CB;</span> BODILESS';
      badge.title = 'No spirit \u2014 soul only, bring body to witness';
    } else {
      badge.innerHTML = '<span class="verify-icon">&#x25CB;</span> BODILESS';
      badge.title = 'No spirit \u2014 soul only, bring body to witness';
    }

    // Souls are surface-agnostic — they no longer carry a list of
    // every mirror they landed on. The badge stays focused on
    // integrity / authenticity / embodiment; sovereignty is a
    // property of the system (any number of mirrors can serve any
    // soul) not something the soul itself advertises.

    badgeWrap.appendChild(badge);

    // AUTHENTICATED badge (Ed25519 signature)
    if (vf.signature === true) {
      var sigBadge = _div('verify-badge verify-authenticated');
      sigBadge.innerHTML = '<span class="verify-icon">&#x1F511;</span> AUTHENTICATED';

      // Tooltip "Also known as": comma-separated names only. People
      // identify each other by names, not key directionality. Only
      // bidirectional aliases qualify — those are mutual handshakes
      // (both private keys signed). One-way claims are soft data that
      // could mislead a viewer if rendered the same as confirmed ones
      // (anyone can publish "I'm also Anthropic" unilaterally), so
      // they stay confined to the expanded cluster panel below where
      // the forensic context makes the distinction legible.
      var aliases = (vf.aliases || []);
      var aliasTooltip = '';
      if (aliases.length) {
        var seen = {};
        var labels = [];
        var selfName = (meta.creator_name || '').trim().toLowerCase();
        aliases.forEach(function(a) {
          if (!a.bidirectional) return;
          var fp = a.alias_fingerprint || '';
          if (seen[fp]) return;
          seen[fp] = true;
          var rawName = (a.creator_name || '').trim();
          var isStub = /^key [0-9a-f]{6,16}$/i.test(rawName);
          var isSelfLabel = rawName && selfName && rawName.toLowerCase() === selfName;
          if (isStub || isSelfLabel || !rawName) return;
          labels.push(rawName);
        });
        if (labels.length) {
          aliasTooltip = '\nAlso known as: ' + labels.join(', ');
        }
      }
      sigBadge.title = (vf.signatureDetail || 'Ed25519 signature verified') + aliasTooltip;
      // No expandable alias panel — aliases live in the badge tooltip
      // ("Also known as: …") only. Keys + fingerprints are a system
      // concern, not a viewer concern.
      badgeWrap.appendChild(sigBadge);
    } else if (vf.signature === false) {
      var sigBadge2 = _div('verify-badge verify-forged');
      sigBadge2.innerHTML = '<span class="verify-icon">&#x2717;</span> FORGED';
      sigBadge2.title = vf.signatureDetail || 'Signature invalid \u2014 possible forgery';
      badgeWrap.appendChild(sigBadge2);
    } else if (vf.signature === null && meta.signature && meta.public_key) {
      // Record carries Ed25519 signature data but the browser couldn't
      // verify it (older Chrome on Windows is the common case — Ed25519
      // in SubtleCrypto only enabled by default in Chrome 137+, May
      // 2025). Surface a distinct badge so the user sees the cert IS
      // signed instead of getting silent absence — same identity weight
      // as AUTHENTICATED, just acknowledges the verification was
      // skipped on this browser.
      var sigBadge3 = _div('verify-badge verify-signed-unverified');
      sigBadge3.innerHTML = '<span class="verify-icon">&#x1F511;</span> SIGNED';
      sigBadge3.title = 'Ed25519 signature present but this browser could not verify it. Open in Safari 17+, Chrome 137+, or Firefox 128+ for cryptographic verification.';
      badgeWrap.appendChild(sigBadge3);
    }
    // signature === null AND no signature data on record means truly unsigned — no badge shown

    // EMBODIED badge (portrait/dHash match)
    if (vf.portrait) {
      if (vf.portrait.match === true) {
        var embBadge = _div('verify-badge verify-embodied');
        embBadge.innerHTML = '<span class="verify-icon">&#x2B22;</span> EMBODIED';
        embBadge.title = 'Portrait match \u2014 dHash distance ' + vf.portrait.distance + '/' + vf.portrait.threshold + ' (image is the original body)';
        badgeWrap.appendChild(embBadge);
      } else if (vf.portrait.match === false) {
        var embBadge2 = _div('verify-badge verify-disembodied');
        embBadge2.innerHTML = '<span class="verify-icon">&#x2B21;</span> DISEMBODIED';
        if (vf.portrait.reason === 'altered') {
          // dHash still matches (same body) but the luma grid flags a localized
          // edit \u2014 a drawn mark, pasted object, stamped text, or global retouch.
          embBadge2.innerHTML = '<span class="verify-icon">&#x2B21;</span> ALTERED';
          embBadge2.title = 'Localized alteration detected \u2014 the image has been edited since conception '
            + '(grid deviation ' + vf.portrait.gridScore + ')';
        } else {
          embBadge2.title = 'Portrait mismatch \u2014 dHash distance ' + vf.portrait.distance + ' (this image may not be the original)';
        }
        badgeWrap.appendChild(embBadge2);
      }
    }

    plate.appendChild(badgeWrap);

    // Dark-matter unlock — if the record's soul is encrypted AND the
    // viewer hasn't provided a password yet, surface a single input
    // right under the badges. The decoder cert is otherwise nearly
    // empty for dark records (every section's data lives in
    // encrypted_fields). One password unlocks: protected fields,
    // encrypted_chunks, and the encrypted thumbnail (which re-enables
    // the EMBODIED dHash comparison). Re-renders the cert in place.
    //
    // Password lives in sessionStorage keyed by decoder_hash so siblings
    // in the same constellation auto-unlock during one tab's lifetime —
    // never localStorage, forgotten on tab close (the CLAUDE.md contract:
    // "Enter password → protected fields appear → close the page →
    // forgotten").
    var _isDarkChain = (meta.chain_visibility === 1 || meta.chain_visibility === 'dark_matter');
    var _alreadyUnlocked = !!meta._unlocked;
    // Unlocked dark cert: small Re-lock control so the viewer can
    // explicitly drop the cached password (and we can re-test the
    // prompt without opening a fresh tab). Clicking it restores the
    // original encrypted record and re-renders, putting the password
    // prompt back.
    if (!isSample && _isDarkChain && _alreadyUnlocked && meta._encryptedSource) {
      var relockKey = (meta.decoder_hash || meta.heart_star_id || meta.identifier || '').slice(0, 24);
      var relockRow = _div('dm-relock-row');
      var relockBtn = document.createElement('button');
      relockBtn.type = 'button';
      relockBtn.className = 'dm-relock-btn';
      relockBtn.title = 'Forget the chain password for this tab session';
      relockBtn.textContent = '\u{1F512} Lock';
      relockBtn.addEventListener('click', function() {
        try { sessionStorage.removeItem('mememage-pw-' + relockKey); } catch (e) {}
        renderCert(meta._encryptedSource, options);
      });
      relockRow.appendChild(relockBtn);
      plate.appendChild(relockRow);
    }
    if (!isSample && _isDarkChain && meta.encrypted_fields && !_alreadyUnlocked
        && typeof Access !== 'undefined') {
      var _chainKey = (meta.decoder_hash || meta.heart_star_id || meta.identifier || '').slice(0, 24);
      var _storedPw = '';
      try { _storedPw = sessionStorage.getItem('mememage-pw-' + _chainKey) || ''; }
      catch (e) {}

      var unlockSoulRow = _div('dm-unlock');
      var unlockSoulLabel = _div('dm-unlock-label');
      unlockSoulLabel.textContent = '\u{1F512} Dark matter \u2014 unlock with creator password';
      unlockSoulRow.appendChild(unlockSoulLabel);

      var pwRow = _div('dm-unlock-row');
      var pwInput = document.createElement('input');
      pwInput.type = 'password';
      pwInput.className = 'dm-unlock-pw';
      pwInput.placeholder = 'password';
      pwInput.autocomplete = 'off';
      pwRow.appendChild(pwInput);
      var unlockBtn = document.createElement('button');
      unlockBtn.type = 'button';
      unlockBtn.className = 'dm-unlock-btn';
      unlockBtn.textContent = 'Unlock';
      pwRow.appendChild(unlockBtn);
      unlockSoulRow.appendChild(pwRow);

      var unlockErr = _div('dm-unlock-err');
      unlockSoulRow.appendChild(unlockErr);

      plate.appendChild(unlockSoulRow);

      async function _doDmUnlock(pw) {
        if (!pw) {
          unlockErr.textContent = 'Enter the chain password.';
          return;
        }
        unlockErr.textContent = '';
        unlockBtn.disabled = true; var prev = unlockBtn.textContent;
        unlockBtn.textContent = 'Decrypting\u2026';
        try {
          var soulRes = await Access.decryptSoul(meta.encrypted_fields, pw);
          if (!soulRes.ok) {
            unlockErr.textContent = soulRes.error || 'Wrong password.';
            return;
          }
          var unlocked = Object.assign({}, meta);
          Object.keys(soulRes.soul || {}).forEach(function(k) {
            unlocked[k] = soulRes.soul[k];
          });
          // Encrypted thumbnail → swap in plaintext so EMBODIED can run.
          if (meta.thumbnail && typeof meta.thumbnail === 'object' && meta.thumbnail.ct) {
            var thumbRes = await Access.decryptEnvelope(meta.thumbnail, pw);
            if (thumbRes.ok) unlocked.thumbnail = thumbRes.plaintext;
          }
          // Encrypted chunks (if present) — needed for any chain that
          // distributes its layer chunk bytes through
          // dark-matter records.
          if (meta.encrypted_chunks) {
            var chunksRes = await Access.decryptChunks(meta.encrypted_chunks, pw);
            if (chunksRes.ok) unlocked.chunks = chunksRes.chunks;
          }
          // EMBODIED dHash check — only meaningful when the viewer
          // actually dropped an image (window._lastDecodedCanvas).
          // Chain-traversal renders (no canvas) stay as partial certs
          // with no EMBODIED, mirroring light-chain traversal. For
          // image-drop renders the comparison runs now that we have
          // the plaintext thumbnail and stamps verification.portrait
          // so the badge renderer below picks it up.
          if (window._lastDecodedCanvas && typeof unlocked.thumbnail === 'string'
              && unlocked.thumbnail && typeof comparePortrait === 'function') {
            try {
              var portrait = await comparePortrait(window._lastDecodedCanvas, unlocked.thumbnail, unlocked.luma_grid);
              var vfCopy = Object.assign({}, (unlocked._verification || {}));
              vfCopy.portrait = portrait;
              unlocked._verification = vfCopy;
            } catch (e) {
              // Portrait comparison failure is non-fatal — leave the
              // existing _verification untouched so WITNESSED /
              // AUTHENTICATED still display.
            }
          }
          unlocked._unlocked = true;
          // Stash a reference to the pre-unlock record so the Re-lock
          // button can restore the encrypted view without rebuilding
          // the envelope from scratch.
          unlocked._encryptedSource = meta;
          // Cache the password for sibling records (sessionStorage —
          // per-tab, forgotten on close).
          try { sessionStorage.setItem('mememage-pw-' + _chainKey, pw); } catch (e) {}
          // Re-render into the same target. Same options the caller
          // used; if none provided, defaults match the decoder.
          renderCert(unlocked, options);
        } catch (e) {
          unlockErr.textContent = 'Decryption failed: ' + (e && e.message ? e.message : 'unknown error');
        } finally {
          unlockBtn.disabled = false;
          unlockBtn.textContent = prev;
        }
      }
      unlockBtn.addEventListener('click', function() { _doDmUnlock(pwInput.value); });
      pwInput.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') { e.preventDefault(); _doDmUnlock(pwInput.value); }
      });
      // Auto-try the cached password from a previous sibling unlock in
      // this tab. Quiet — if it fails (different chain that happens to
      // share the same key prefix, password rotated), the input just
      // sits there waiting for the viewer's input. No error toast.
      if (_storedPw) {
        setTimeout(function() {
          (async function() {
            var r = await Access.decryptSoul(meta.encrypted_fields, _storedPw);
            if (r.ok) _doDmUnlock(_storedPw);
          })();
        }, 0);
      }
    }

    // Alias-cluster panel removed — aliases surface only as the
    // AUTHENTICATED badge's tooltip ("Also known as: …"). Keys and
    // fingerprints are system mechanics, not part of what the viewer
    // is asked to read. The cert's job is three badges: WITNESSED,
    // AUTHENTICATED, EMBODIED. Anything else clutters the surface.

    // Distribution cluster removed: souls are surface-agnostic. The
    // primary URL (meta.url, same as the bar pixel-encodes) still
    // tells viewers where this record originally came from; mirror
    // discovery is an operational concern, not part of the artifact.
  }

  plate.appendChild(_div('plate-divider-short'));

  // Timestamp
  if (TIMESTAMP) {
    var ts = _div('plate-timestamp selectable');
    ts.textContent = TIMESTAMP;
    plate.appendChild(ts);
  }

  // Prompt
  if (PROMPT) {
    plate.appendChild(_divider());
    var prompt = _div('plate-prompt selectable');
    prompt.textContent = '\u201C' + PROMPT + '\u201D';
    plate.appendChild(prompt);
    plate.appendChild(_divider());
  }

  // Constellation — name opens the 3D planetarium for this constellation.
  // Falls back to heart-star navigation if the planetarium module
  // isn't loaded (e.g., on minimal pages). Sample certs (Attack Lab,
  // "see an example") render the name as plain text — the planetarium
  // wouldn't anchor to a real chain there.
  if (meta.constellation_name) {
    var conDiv = _div('lineage-text');
    // EXAMPLE / Attack-Lab certs aren't anchored to a real chain, so the Bayer
    // letter (-> parent) and the stellarium walk into nothing and are buggy.
    // Disable both on example certs; real lookups (By Word / Audit) keep them.
    var _exampleCert = window._exampleMode;
    // Constellation name opens the planetarium (stellarium) on every REAL cert
    // — drop-image, By Soul, or chain-traversal lookup — since the stargazing
    // view is meaningful regardless of how the viewer arrived. On example certs
    // it's plain text (the planetarium can't anchor to a non-existent chain).
    var conNameEl = document.createElement(_exampleCert ? 'span' : 'a');
    conNameEl.textContent = meta.constellation_name;
    if (!_exampleCert) {
      conNameEl.href = '#';
      conNameEl.addEventListener('click', function(e) {
      e.preventDefault();
      if (typeof CosmicPlanetarium !== 'undefined') {
        CosmicPlanetarium.open({
          name: meta.constellation_name,
          // Per-star Z depths derive from this seed. constellation_hash is
          // identical across siblings, so the constellation looks the same
          // from any star in it.
          hash: meta.constellation_hash || meta._content_hash || meta.content_hash || '',
          // constellation_size drives how many stars the layout draws so the
          // planetarium shape matches the constellation's member count.
          size: meta.constellation_size,
          currentStarIndex: (typeof myChunkIdx === 'number' && myChunkIdx >= 0) ? myChunkIdx : -1,
          // Heart's rarity drives the heart sprite's spectral class.
          // Denormalize to meta.heart_rarity in production; until then
          // default to 0 so the heart glows K-class orange.
          heartRarity: meta.heart_rarity || 0,
          currentRarity: rarityScore,
          meta: meta
        });
      } else if (meta.heart_star_id && meta.heart_star_id !== meta._identifier) {
        lookupById(meta.heart_star_id);
        window.scrollTo({top: 0, behavior: 'smooth'});
      }
      });
    }
    // Bayer designation — Greek letters by birth order.
    // Letter navigates to parent (one step back), name navigates to
    // heart star. Full 24-letter Greek alphabet covers K up to 24;
    // beyond that, no letter is drawn (rare for any practical chain).
    var BAYER = ('\u03b1\u03b2\u03b3\u03b4\u03b5\u03b6\u03b7\u03b8\u03b9\u03ba\u03bb\u03bc'
               + '\u03bd\u03be\u03bf\u03c0\u03c1\u03c3\u03c4\u03c5\u03c6\u03c7\u03c8\u03c9').split('');
    if (myChunkIdx >= 0 && myChunkIdx < BAYER.length) {
      if (meta.parent_id && !_exampleCert) {
        // Greek letter links to parent (the previous star in the chain). This
        // holds at the heart star (α) too: a heart star begins its
        // CONSTELLATION but not the CHAIN — its parent_id points into the
        // PREVIOUS constellation, so linking it is what lets you walk back
        // across constellation boundaries all the way to genesis. Only genesis
        // (parent_id null) has no previous.
        var bayerLink = document.createElement('a');
        bayerLink.href = '#';
        bayerLink.className = 'bayer-letter';
        // Only the letter is the link — the separating space stays outside so
        // the underline doesn't run under it.
        bayerLink.textContent = BAYER[myChunkIdx];
        bayerLink.title = 'Previous: ' + meta.parent_id;
        bayerLink.addEventListener('click', function(e) {
          e.preventDefault();
          lookupById(meta.parent_id);
          window.scrollTo({top: 0, behavior: 'smooth'});
        });
        conDiv.appendChild(bayerLink);
        conDiv.appendChild(document.createTextNode(' '));
      } else {
        // Not a link: genesis (no parent, the beginning of the chain) OR an
        // example/Attack-Lab cert (chain traversal disabled).
        var bayerSpan = document.createElement('span');
        bayerSpan.className = 'bayer-letter';
        bayerSpan.textContent = BAYER[myChunkIdx];
        conDiv.appendChild(bayerSpan);
        conDiv.appendChild(document.createTextNode(' '));
      }
    }
    conDiv.appendChild(conNameEl);
    plate.appendChild(conDiv);
  }

  // Lineage — hidden in the constellation name click.
  // The heart star link IS the chain. The raw identifier stays in the data, not the display.

  // ===================================================================
  // 2. BIRTH TEMPERAMENT
  // ===================================================================
  // Records carry only birth_traits (codes); the human-readable
  // temperament + summary are reconstructed from the lookup tables in
  // birth-text.js. Falls back to any persisted birth_temperament for
  // legacy V4 dev-era records that still have the strings inline.
  var birthTexts = (typeof BirthText !== 'undefined' && meta.birth_traits)
    ? BirthText.read(meta.birth_traits)
    : null;
  var derivedTemperament = (birthTexts && birthTexts.temperament) || meta.birth_temperament;
  var derivedSummary     = (birthTexts && birthTexts.summary)     || meta.birth_summary;
  if (derivedTemperament) {
    plate.appendChild(_sectionLabel('BIRTH TEMPERAMENT'));

    var tempCell = _div('temperament-cell');

    var tempName = _div('plate-temperament-name selectable');
    tempName.textContent = derivedTemperament;
    tempCell.appendChild(tempName);

    if (derivedSummary) {
      var tempSummary = _div('plate-temperament-summary selectable');
      tempSummary.textContent = derivedSummary;
      tempCell.appendChild(tempSummary);
    }

    if (meta.birth_traits && meta.birth_traits.length) {
      var tempTraits = _div('trait-badge-group');
      for (var ti = 0; ti < meta.birth_traits.length; ti++) {
        // Soul stores integer codes; resolve to name via BirthText.
        var traitName = (typeof BirthText !== 'undefined')
          ? BirthText.name(meta.birth_traits[ti])
          : null;
        var traitDef = (traitName && typeof BIRTH_TRAITS !== 'undefined') ? BIRTH_TRAITS[traitName] : null;
        var badge = document.createElement('span');
        badge.className = 'trait-badge';
        if (traitDef && traitName) {
          badge.dataset.metal = traitDef.metal || 'silver';
          var imgUrl = assetUrl('img/traits/' + traitName + '.png');
          var img = document.createElement('img');
          img.src = imgUrl;
          img.alt = traitDef.name;
          img.className = 'trait-img';
          // If the icon 404s, swap to the readable text fallback so the
          // badge doesn't render as a broken image symbol. Capture the
          // current iteration's traitDef in an IIFE — `var` has function
          // scope, so without this the onerror callback fires later
          // with whatever the last loop iteration left in traitDef.
          img.onerror = (function(name) {
            return function() {
              var b = this.parentElement;
              if (!b) return;
              this.remove();
              b.classList.add('trait-badge-text');
              b.textContent = name;
              b.style.removeProperty('--trait-mask');
            };
          })(traitDef.name);
          badge.style.setProperty('--trait-mask', 'url(' + imgUrl + ')');
          badge.appendChild(img);
          badge.title = traitDef.name + ' \u2014 ' + traitDef.desc;
        } else {
          // Unknown code (newer record from a future trait list) —
          // surface the raw code so it's at least visible.
          badge.classList.add('trait-badge-text');
          badge.textContent = traitName
            ? traitName.replace(/_/g, ' ').replace(/\b\w/g, function(c){return c.toUpperCase();})
            : ('trait #' + meta.birth_traits[ti]);
          badge.title = badge.textContent;
        }
        tempTraits.appendChild(badge);
      }
      tempCell.appendChild(tempTraits);
    }

    plate.appendChild(tempCell);
  }

  // Sample mode: stop after Birth Temperament — the spirit reveals the rest
  var isSample = window._sampleMode;
  window._exampleMode = false; // consume example flag (chain-nav/stellarium disable)
  if (isSample) {
    window._sampleMode = false; // consume flag
    plate.classList.add('plate-sample');
  }
  // Chain-traversal partial cert (no image dropped) gets the same
  // compact sizing as the sample cert — content is just badges +
  // identifier + constellation + soul fields; no portrait, bands,
  // BIRTHPLACE, or player. Without this the plate stretches to fill
  // the viewport with a lot of empty space at the bottom.
  if (!isSample && !_imageWasPresent) {
    plate.classList.add('plate-traversal');
  }

  // ===================================================================
  // 3. ORIGIN PARAMETERS (canvas band)
  // ===================================================================
  // The three canvas bands (ORIGIN PARAMETERS, MACHINE AT CONCEPTION,
  // SKY AT THE MOMENT OF CREATION) are the body of the cert — visible
  // only when the viewer dropped an image. Chain traversal (By Word /
  // chain-link clicks / popstate back) gets the partial cert: badges,
  // identifier, navigation, no bands. Mirrors the portrait gating
  // above — stargazing shows fingerprint, not face / not vitals /
  // not sky.
  if (GEN_PARAMS.length > 0 && !isSample && _imageWasPresent) {
    plate.appendChild(_sectionLabel('ORIGIN PARAMETERS'));

    var genWrap = _div('sky-band-wrap');
    var genContainer = _div('sky-band-container');
    // Max logical width; actual canvas buffer width is measured post-mount
    // so the band always matches the plate's real content area.
    var GEN_W = 604;
    // Count rows accounting for span 1/2/3 cells in a 3-col grid.
    // Mirrors the packing in gen-band.js so canvas height matches.
    var _gpCol = 0, _gpRow = 0;
    for (var _gpi = 0; _gpi < GEN_PARAMS.length; _gpi++) {
      var _sp = Math.min(3, Math.max(1, GEN_PARAMS[_gpi].span || 1));
      if (_gpCol + _sp > 3) { _gpCol = 0; _gpRow++; }
      _gpCol += _sp;
      if (_gpCol >= 3) { _gpCol = 0; _gpRow++; }
    }
    var genRows = _gpRow + (_gpCol > 0 ? 1 : 0);
    var GEN_H = Math.max(80, genRows * 50 + 30);
    var genCanvas = document.createElement('canvas');
    // Fluid CSS sizing so band width matches the plate. _setupHiDpi
    // measures actual rendered width in the setTimeout below.
    genCanvas.style.width = '100%';
    genContainer.appendChild(genCanvas);
    genWrap.appendChild(genContainer);
    plate.appendChild(genWrap);

    setTimeout(function() {
      if (typeof initGenBand !== 'function') return;
      var dims = _setupHiDpi(genCanvas, GEN_W, GEN_H);
      initGenBand(genCanvas, dims.w, dims.h, GEN_PARAMS, KERNEL_ENTROPY, BAR_SPEC, barFragments.gen, tierColor, rarityScore, barId, barHash);
    }, 0);
  }

  // ===================================================================
  // 4. MACHINE DIE: vitals canvas band, machine rarity traits
  // ===================================================================
  if (MACHINE.length > 0 && !isSample && _imageWasPresent) {
    plate.appendChild(_sectionLabel('MACHINE AT CONCEPTION'));

    var machWrap = _div('sky-band-wrap');
    var machContainer = _div('sky-band-container');
    var MACH_W = 604;
    // Compute row count using same span-based packing as machine-band.
    // Each row's spans sum to 3 (full width).
    var machRowSum = 0, machRows = 0;
    var MACH_EPS = 0.001;
    for (var mi = 0; mi < MACHINE.length; mi++) {
      var ms = Math.min(3, Math.max(0.5, MACHINE[mi].span || 1));
      if (machRowSum + ms > 3 + MACH_EPS) {
        if (machRowSum > 0) machRows++;
        machRowSum = 0;
      }
      machRowSum += ms;
      if (Math.abs(machRowSum - 3) < MACH_EPS) { machRows++; machRowSum = 0; }
    }
    if (machRowSum > 0) machRows++;
    // Extra height: entropy cell + identity/traits cell
    var extraH = 0;
    if (KERNEL_ENTROPY) extraH += 54; // entropy cell + gap
    // Identity+traits cell
    var bottomCellH = 14;
    if (meta.machine_fingerprint) bottomCellH += 12;
    var machTraitCount = (rarity.machine || []).length + (rarity.entropy || []).length;
    if (machTraitCount > 0) bottomCellH += 12;
    if (rarity.sigil || rarity.echo) bottomCellH += 12;
    extraH += bottomCellH + 6; // cell + gap
    var MACH_H = Math.max(80, machRows * 44 + 30 + extraH);
    var machCanvas = document.createElement('canvas');
    machCanvas.style.width = '100%';
    machContainer.appendChild(machCanvas);
    machWrap.appendChild(machContainer);
    plate.appendChild(machWrap);

    var machineTraits = (rarity.machine || []).map(function(t) { return t.trait; });
    var entropyTraits = (rarity.entropy || []).map(function(t) { return t.trait; });
    var sigilData = rarity.sigil || rarity.echo || null;

    setTimeout(function() {
      if (typeof initMachineBand !== 'function') return;
      var dims = _setupHiDpi(machCanvas, MACH_W, MACH_H);
      initMachineBand(machCanvas, dims.w, dims.h, MACHINE, KERNEL_ENTROPY, meta.machine_fingerprint, BAR_SPEC, barFragments.machine, machineTraits, entropyTraits, sigilData, tierColor, meta.about || '', rarityScore, barId, barHash);
    }, 0);
  }

  // ===================================================================
  // 5. SKY DIE: celestial traits + skyband
  // ===================================================================
  if (hasSky && !isSample && _imageWasPresent) {
    plate.appendChild(_sectionLabel('SKY AT THE MOMENT OF CREATION'));

    var skyWrap = _div('sky-band-wrap');
    var skyContainer = _div('sky-band-container');
    var SKY_W = 604;
    var skyCanvas = document.createElement('canvas');
    skyCanvas.style.width = '100%';
    skyContainer.appendChild(skyCanvas);
    skyWrap.appendChild(skyContainer);
    plate.appendChild(skyWrap);

    var celestialTraits = (rarity.celestial || []).map(function(t) { return t.trait; });
    // Sky band picks an atmosphere from the temperament — reuse the
    // derived value computed above (works for V1 trait-code records and
    // V4-era records that still ship the string inline).
    var birthTemp = derivedTemperament || '';
    // Sky-band height stays fixed at 390 regardless of canvas width.
    // Its graphical elements (zodiac wheel, orbit ring, meteor trails)
    // are positioned in absolute logical coordinates from the top —
    // scaling H proportionally with W pushes them off-canvas on mobile.
    // Keeping 390 means mobile gets a portrait-aspect sky (narrower
    // but same tall) with every graphical element intact.
    // Reserve extra height for:
    // (1) multi-line trait footer when there are multiple celestial
    //     traits — on narrow canvases they stack one per line (see
    //     sky-band.js) and need +11px each to not overlap the reading.
    // (2) the celestial reading wrapping to more lines on narrow
    //     canvases — text that fits on 2 lines at 604px wraps to 4
    //     on ~295px. Reserve ~12px per extra anticipated line below
    //     500px canvas width.
    setTimeout(function() {
      var dims = _setupHiDpi(skyCanvas, SKY_W, function(w) {
        var readingExtra = w < 500 ? 36 : (w < 600 ? 12 : 0);
        var traitExtra = Math.max(0, celestialTraits.length - 1) * 11;
        return 390 + readingExtra + traitExtra;
      });
      initSkyBand(skyCanvas, dims.w, dims.h, PLANET_DATA, SKY_READING, KERNEL_ENTROPY, m, ageTier, rarityScore, celestialTraits, birthTemp, tierColor);
    }, 0);

    if (typeof enableCanvasSave === 'function') {
      var skyMeta = {};
      for (var si = 0; si < PLANET_DATA.length; si++) {
        var sp = PLANET_DATA[si];
        skyMeta[sp.name] = sp.sign + ' ' + sp.deg.toFixed(1) + '\u00b0';
      }
      if (birth.moon_phase) skyMeta.moon_phase = formatMoonPhase(birth.moon_phase);
      if (birth.angular_spread) skyMeta.angular_spread = '' + birth.angular_spread;
      enableCanvasSave(skyCanvas, {
        celestial_positions: JSON.stringify(skyMeta),
        bar_spec: JSON.stringify(BAR_SPEC),
        bar_payload_2: barFragments.sky,
        parent_id: barId,
        parent_hash: barHash,
        fragment_id: 'sky',
        Software: 'Mememage'
      }, (typeof fragmentBytes === 'function') ? fragmentBytes(barFragments.sky, FRAGMENT_TAG_SKY) : null);
    }
  }

  // ===================================================================
  // 6. BIRTHPLACE — TIME-LOCKED
  // ===================================================================
  // Same body-vs-stargazing gate as the bands above. The encrypted
  // GPS envelope + RSA modulus + password unlock UI is part of the
  // body view — earned by dropping the image. Chain traversal shows
  // navigation + soul fields without the birthplace block (matches
  // the sample cert format the user pointed at).
  if (meta.gps && Array.isArray(meta.gps) && meta.gps.length === 2 && !isSample && _imageWasPresent) {
    // Public-GPS chain (gps_visibility: "public") \u2014 coordinates shown in
    // the clear by the creator's deliberate choice. The time-lock is still
    // present in the record for independent proof later.
    plate.appendChild(_sectionLabel('BIRTHPLACE'));
    var pgLat = '' + meta.gps[0], pgLon = '' + meta.gps[1];
    var pgC = _div('gps-container');
    var pgRgb = _hexToRgb(tierColor || '#a0a0a0');
    pgC.style.background = 'linear-gradient(180deg, rgb(' + Math.floor(pgRgb[0]*0.08) + ',' + Math.floor(pgRgb[1]*0.08) + ',' + Math.floor(pgRgb[2]*0.08) + ') 0%, rgb(' + Math.floor(pgRgb[0]*0.12) + ',' + Math.floor(pgRgb[1]*0.12) + ',' + Math.floor(pgRgb[2]*0.12) + ') 50%, rgb(' + Math.floor(pgRgb[0]*0.08) + ',' + Math.floor(pgRgb[1]*0.08) + ',' + Math.floor(pgRgb[2]*0.08) + ') 100%)';
    var pgCoords = _div('gps-unlock-coords');
    pgCoords.innerHTML =
      '<div><span class="gps-unlock-k">LAT</span> <span class="gps-unlock-v">' + escapeHtml(pgLat) + '</span></div>' +
      '<div><span class="gps-unlock-k">LON</span> <span class="gps-unlock-v">' + escapeHtml(pgLon) + '</span></div>';
    pgC.appendChild(pgCoords);
    var pgMap = document.createElement('a');
    pgMap.className = 'gps-map-link';
    pgMap.href = 'https://www.openstreetmap.org/?mlat=' + encodeURIComponent(pgLat) + '&mlon=' + encodeURIComponent(pgLon) + '#map=12/' + encodeURIComponent(pgLat) + '/' + encodeURIComponent(pgLon);
    pgMap.target = '_blank'; pgMap.rel = 'noopener';
    pgMap.textContent = 'View on map \u2197';
    pgC.appendChild(pgMap);
    var pgNote = _div('gps-footnote');
    pgNote.textContent = 'Shown by the creator\u2019s choice. The coordinates are also sealed in a ~10-year time-lock for independent proof.';
    pgC.appendChild(pgNote);
    plate.appendChild(pgC);
  } else if (GPS_CIPHER && !isSample && _imageWasPresent) {
    plate.appendChild(_sectionLabel('BIRTHPLACE \u2014 TIME-LOCKED *'));

    var gpsContainer = _div('gps-container');
    var gtc = _hexToRgb(tierColor || '#a0a0a0');
    gpsContainer.style.background = 'linear-gradient(180deg, rgb(' + Math.floor(gtc[0]*0.08) + ',' + Math.floor(gtc[1]*0.08) + ',' + Math.floor(gtc[2]*0.08) + ') 0%, rgb(' + Math.floor(gtc[0]*0.12) + ',' + Math.floor(gtc[1]*0.12) + ',' + Math.floor(gtc[2]*0.12) + ') 50%, rgb(' + Math.floor(gtc[0]*0.08) + ',' + Math.floor(gtc[1]*0.08) + ',' + Math.floor(gtc[2]*0.08) + ') 100%)';

    var cipherLabel = _div('gps-mod-label');
    cipherLabel.textContent = 'Encrypted GPS Coordinates \u2014 click to copy';
    gpsContainer.appendChild(cipherLabel);

    var gpsCipher = _div('gps-cipher');
    gpsCipher.textContent = GPS_CIPHER;
    _copyable(gpsCipher, GPS_CIPHER);
    gpsContainer.appendChild(gpsCipher);

    if (GPS_MODULUS) {
      var modLabel = _div('gps-mod-label');
      modLabel.textContent = 'RSA Modulus N (2048-bit) \u2014 click to copy';
      gpsContainer.appendChild(modLabel);

      var modBlock = _div('gps-modulus expanded');
      modBlock.textContent = GPS_MODULUS;
      _copyable(modBlock, GPS_MODULUS);
      gpsContainer.appendChild(modBlock);
    }

    var footnote = _div('gps-footnote');
    var tExp = meta.gps_time_locked && meta.gps_time_locked.t ? meta.gps_time_locked.t.toExponential(0) : '?';
    var pLen = meta.gps_time_locked && (meta.gps_time_locked.len || meta.gps_time_locked.plaintext_length) || '?';
    footnote.innerHTML = '* ' + escapeHtml('' + tExp) + ' sequential squarings of 2 mod N, SHA-256 the result, XOR with ciphertext. First ' + escapeHtml('' + pLen) + ' bytes = GPS.';
    gpsContainer.appendChild(footnote);

    // Password-based GPS unlock — the creator can reveal their own
    // GPS instantly by entering the password set at conception time,
    // no need to wait 10 years for the time-lock puzzle to finish.
    // Only rendered when the record actually carries an AES envelope.
    if (meta.gps_password_locked) {
      var unlockWrap = _div('gps-unlock');
      var unlockLabel = _div('gps-mod-label');
      unlockLabel.textContent = 'Creator password \u2014 unlock instantly';
      unlockWrap.appendChild(unlockLabel);

      var unlockRow = _div('gps-unlock-row');
      var pwInput = document.createElement('input');
      pwInput.type = 'password';
      pwInput.className = 'gps-pw';
      pwInput.placeholder = 'password';
      unlockRow.appendChild(pwInput);

      var unlockBtn = document.createElement('button');
      unlockBtn.type = 'button';
      unlockBtn.className = 'gps-unlock-btn';
      unlockBtn.textContent = 'Unlock';
      unlockRow.appendChild(unlockBtn);
      unlockWrap.appendChild(unlockRow);

      var resultSlot = _div('gps-unlock-result');
      unlockWrap.appendChild(resultSlot);
      gpsContainer.appendChild(unlockWrap);

      var envRef = meta.gps_password_locked;
      async function doUnlock() {
        var pw = pwInput.value;
        if (!pw) { resultSlot.innerHTML = '<span class="gps-unlock-err">Enter password</span>'; return; }
        unlockBtn.disabled = true;
        resultSlot.innerHTML = '<span class="gps-unlock-pending">Decrypting\u2026</span>';
        var res = await Access.decryptGps(envRef, pw);
        if (res.ok) {
          resultSlot.innerHTML =
            '<div class="gps-unlock-coords">' +
              '<div><span class="gps-unlock-k">LAT</span> <span class="gps-unlock-v">' + escapeHtml(res.lat) + '</span></div>' +
              '<div><span class="gps-unlock-k">LON</span> <span class="gps-unlock-v">' + escapeHtml(res.lon) + '</span></div>' +
            '</div>';
        } else {
          resultSlot.innerHTML = '<span class="gps-unlock-err">' + escapeHtml(res.error || 'Wrong password') + '</span>';
        }
        unlockBtn.disabled = false;
      }
      unlockBtn.addEventListener('click', doUnlock);
      pwInput.addEventListener('keydown', function(e) { if (e.key === 'Enter') doUnlock(); });
    }

    plate.appendChild(gpsContainer);
  } else if (!isSample && _imageWasPresent && birth && Object.keys(birth).length > 0) {
    // Honest placeholder for records minted on chains with
    // ``gps_source: none`` (or any record lacking ``gps_time_locked``).
    // The cert acknowledges the absence rather than silently hiding
    // the section — like a camera that didn't write location to EXIF.
    // Also gated on image-was-present so traversal stays clean.
    plate.appendChild(_sectionLabel('BIRTHPLACE \u2014 NOT RECORDED'));
    var noGps = _div('gps-container gps-container-empty');
    var gtc2 = _hexToRgb(tierColor || '#a0a0a0');
    noGps.style.background = 'linear-gradient(180deg, rgb(' +
      Math.floor(gtc2[0]*0.05) + ',' + Math.floor(gtc2[1]*0.05) + ',' + Math.floor(gtc2[2]*0.05) +
      ') 0%, rgb(' +
      Math.floor(gtc2[0]*0.08) + ',' + Math.floor(gtc2[1]*0.08) + ',' + Math.floor(gtc2[2]*0.08) +
      ') 100%)';
    var noGpsBody = _div('gps-empty-body');
    noGpsBody.innerHTML =
      '<p class="gps-empty-line">No GPS coordinates were captured at conception.</p>' +
      '<p class="gps-empty-hint">This soul carries the sky but not the place. ' +
      'The creator\u2019s chain is configured to omit location data \u2014 ' +
      'like a camera that doesn\u2019t write GPS to EXIF.</p>';
    noGps.appendChild(noGpsBody);
    plate.appendChild(noGps);
  }

  // ===================================================================
  // 8. FOOTER
  // ===================================================================
  var footer = _div('plate-footer');
  if (hasSky) {
    footer.innerHTML = '<div class="plate-footer-line">Celestial positions computed via Meeus algorithms (J2000.0 epoch)</div>';
  }
  footer.innerHTML += '<div class="plate-footer-italic">The cosmos bears witness to this.</div>';

  plate.appendChild(footer);

  // ===================================================================
  // SAVE CERTIFICATE — composite canvases + encode bar
  // ===================================================================
  // Gate: only render Save on a full body view — image was dropped
  // (window._lastDecodedCanvas set, so portrait + canvas bands +
  // BIRTHPLACE + player all rendered) AND, on dark chains, the soul
  // has been unlocked. Saving a partial cert (traversal stargazing
  // shell, or locked dark prompt) would produce a confusing image —
  // either mostly-empty plate or the unlock prompt baked in. Earned
  // alongside the rest of the body view.
  var _stillLocked = (meta.chain_visibility === 1 || meta.chain_visibility === 'dark_matter')
                     && meta.encrypted_fields && !meta._unlocked;
  if (barId && barHash && !isSample && _imageWasPresent && !_stillLocked) {
    var saveBtn = document.createElement('button');
    saveBtn.textContent = 'Save Certificate';
    saveBtn.className = 'save-cert-btn save-cert-rarity-' + rarityTier;

    // Stardust particle system
    var sparkCanvas = document.createElement('canvas');
    sparkCanvas.className = 'sparkle-canvas';
    saveBtn.appendChild(sparkCanvas);
    var _sparkles = [], _sparkRAF = null, _sparkActive = false;
    function initSparkles() {
      var rect = saveBtn.getBoundingClientRect();
      sparkCanvas.width = Math.round(rect.width * 2);
      sparkCanvas.height = Math.round(rect.height * 2);
      sparkCanvas.style.width = '100%';
      sparkCanvas.style.height = '100%';
      _sparkles = [];
      for (var i = 0; i < 20; i++) {
        _sparkles.push({
          x: Math.random() * sparkCanvas.width,
          y: Math.random() * sparkCanvas.height,
          vx: (Math.random() - 0.5) * 0.15,
          vy: -0.06 - Math.random() * 0.15,
          r: 1 + Math.random() * 1.5,
          life: Math.random(),
          speed: 0.002 + Math.random() * 0.004,
          warm: Math.random() > 0.3
        });
      }
    }
    function animSparkles() {
      if (!_sparkActive) return;
      var sctx = sparkCanvas.getContext('2d');
      sctx.clearRect(0, 0, sparkCanvas.width, sparkCanvas.height);
      for (var i = 0; i < _sparkles.length; i++) {
        var p = _sparkles[i];
        p.life += p.speed;
        if (p.life > 1) p.life -= 1;
        p.x += p.vx;
        p.y += p.vy;
        // Wrap around
        if (p.y < -2) { p.y = sparkCanvas.height + 2; p.x = Math.random() * sparkCanvas.width; }
        if (p.x < -2) p.x = sparkCanvas.width + 2;
        if (p.x > sparkCanvas.width + 2) p.x = -2;
        // Pulse: fade in, bright, fade out
        var a = Math.sin(p.life * Math.PI);
        a = a * a; // sharper peak
        if (a < 0.05) continue;
        var col = p.warm ? '255,240,160' : '255,250,220';
        // Glow
        sctx.globalAlpha = a * 0.2;
        sctx.fillStyle = 'rgb(' + col + ')';
        sctx.beginPath();
        sctx.arc(p.x, p.y, p.r * 3, 0, Math.PI * 2);
        sctx.fill();
        // Core
        sctx.globalAlpha = a * 0.9;
        sctx.beginPath();
        sctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        sctx.fill();
      }
      sctx.globalAlpha = 1;
      _sparkRAF = requestAnimationFrame(animSparkles);
    }
    saveBtn.addEventListener('mouseenter', function() {
      initSparkles();
      _sparkActive = true;
      animSparkles();
    });
    saveBtn.addEventListener('mouseleave', function() {
      _sparkActive = false;
      if (_sparkRAF) cancelAnimationFrame(_sparkRAF);
    });

    saveBtn.addEventListener('click', function() {
      _saveLivePlate(plate, barId, barHash).catch(function(err) {
        console.error('Save certificate failed:', err);
        _showToast('Save failed — see console for details.');
      });
    });
    plate.appendChild(saveBtn);
  }

  // Inject cosmic audio player as the plate's bottom edge. Skipped in
  // sample mode (truncated preview cert) and in chain-traversal
  // partial certs (the song belongs with the body view — earned by
  // dropping the image, not by clicking a chain link). Same gate as
  // the three canvas bands + portrait + BIRTHPLACE block above.
  if (injectPlayer && !isSample && _imageWasPresent
      && typeof CosmicPlayer !== 'undefined' && birth && birth.sun) {
    CosmicPlayer.inject(plate, meta);
  }

  certWrap.appendChild(plate);
  // Only add .visible on first reveal — re-adding it mid-swap re-triggers
  // panelFadeIn and double-fades with PanelSwap's intro animation.
  if (!wasVisible) {
    certWrap.classList.add('visible');
    if (window.innerWidth < 1200 && typeof scrollResultIntoView === 'function') {
      scrollResultIntoView(certWrap);
    }
  }
  // Drag-to-scroll on the plate (scrollable when player is injected).
  // Sample mode doesn't scroll, but the helper is idempotent and only
  // has visible effect when there's overflow — safe to attach always.
  if (typeof DragScroll !== 'undefined') DragScroll.attach(plate);
  // Activate side-by-side layout on desktop
  if (activateLayout) {
    var desktopMain = document.querySelector('.panel-layout');
    if (desktopMain) {
      // Fresh entry into compact mode — hold the cert offscreen
      // through the system box's width animation, then fade in.
      if (!desktopMain.classList.contains('layout-active')) holdCertEntering(certWrap);
      desktopMain.classList.add('layout-active');
    }
  }

  // ===================================================================
  // Specular drift + shine effects
  // ===================================================================
  var shineConfigs = {
    'plate-rarity-uncommon': {
      grad: 'linear-gradient(105deg, transparent 43%, rgba(180,220,180,0.02) 47%, rgba(180,220,180,0.04) 50%, rgba(180,220,180,0.02) 53%, transparent 57%)',
      minDelay: 24, maxDelay: 45, dur: [5, 8], opa: [0.2, 0.4]
    },
    'plate-rarity-rare': {
      grad: 'linear-gradient(105deg, transparent 43%, rgba(140,170,220,0.02) 47%, rgba(140,170,220,0.05) 50%, rgba(140,170,220,0.02) 53%, transparent 57%)',
      minDelay: 20, maxDelay: 40, dur: [5, 7], opa: [0.2, 0.4]
    },
    'plate-rarity-veryrare': {
      grad: 'linear-gradient(105deg, transparent 43%, rgba(180,140,240,0.02) 47%, rgba(180,140,240,0.05) 50%, rgba(180,140,240,0.02) 53%, transparent 57%)',
      minDelay: 18, maxDelay: 36, dur: [4, 7], opa: [0.2, 0.4]
    },
    'plate-rarity-epic': {
      grad: 'linear-gradient(105deg, transparent 43%, rgba(240,200,100,0.02) 47%, rgba(255,220,120,0.06) 50%, rgba(240,200,100,0.02) 53%, transparent 57%)',
      minDelay: 16, maxDelay: 32, dur: [4, 7], opa: [0.2, 0.4]
    },
    'plate-rarity-legendary': {
      grad: 'linear-gradient(105deg, transparent 44%, rgba(255,140,120,0.01) 47%, rgba(255,170,150,0.025) 50%, rgba(255,140,120,0.01) 53%, transparent 56%)',
      minDelay: 25, maxDelay: 50, dur: [5, 9], opa: [0.15, 0.3]
    }
  };

  function _rand(a, b) { return a + Math.random() * (b - a); }

  var shineCfg = null;
  for (var sk in shineConfigs) { if (plate.classList.contains(sk)) { shineCfg = shineConfigs[sk]; break; } }
  if (shineCfg) {
    (function scheduleShine() {
      var delay = _rand(shineCfg.minDelay, shineCfg.maxDelay) * 1000;
      setTimeout(function() {
        var el = _div('shine');
        var dur = _rand(shineCfg.dur[0], shineCfg.dur[1]);
        var angle = 105 + (Math.random() - 0.5) * 30;
        el.style.background = shineCfg.grad.replace('105deg', angle + 'deg');
        el.style.opacity = _rand(shineCfg.opa[0], shineCfg.opa[1]);
        el.style.animation = 'sweep ' + dur + 's ease-in-out forwards';
        el.style.top = '0';
        el.style.height = plate.scrollHeight + 'px';
        plate.appendChild(el);
        setTimeout(function() { el.remove(); }, dur * 1000 + 100);
        scheduleShine();
      }, delay);
    })();
  }
}

// escapeHtml lives in portal.js (shared with validator); keeping the
// local definition out of here avoids the redeclaration if a future
// consumer also picks it up.
