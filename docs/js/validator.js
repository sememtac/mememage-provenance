// Starfield auto-initializes from js/starfield.js, reading data-theme
// off the #starfield canvas (yin = dark stars on light bg).

// =====================================================================
// SUBTITLE ROTATION
// Trove lives in docs/js/theme.js (Theme.taglines.validator) so the
// L1 voice can be reskinned per Age without touching this file.
// =====================================================================
(function _rotateSubtitle() {
  var sub = document.querySelector('.page-header .subtitle');
  var trove = (typeof Theme !== 'undefined') && Theme.taglines && Theme.taglines.validator;
  if (!sub || !trove || !trove.length) return;
  sub.textContent = trove[Math.floor(Math.random() * trove.length)];
})();

// === Constants ===
// SIG_ROWS, HEADER_BAND, etc. already defined by data.js

// === Reed-Solomon GF(2^8) — loaded from js/rs.js (gfMul, gfPow, rsDecode, ...) ===

// === Bar Codec === crc16, detectBar, extractBits, decodeFrame, decodePayload
// are loaded from js/codec.js. decodeFrame now always returns rsErrors and
// rsCapacity for forensic display. decodePayload returns {identifier,
// archive_id, content_hash} — the validator reads identifier and content_hash.

// === Tab switching ===
// showTab: programmatic entry used by link delegation, portal.init,
// and attack-lab toggles. Click-wiring for the three visible tabs
// goes through TabBar.wire() below; both paths converge on
// TabBar.activateById for the class toggling. Accepts short names
// ('img', 'cert', 'meta', 'attack') for backward compat.
function showTab(name){
  TabBar.activateById('tab-' + name);
  syncResultsVisibility(name);
}
TabBar.wire(function(panelId) {
  syncResultsVisibility(panelId.replace(/^tab-/, ''));
});

// Drag-to-scroll on the results sidebar — one mount covers Audit cert,
// Observatory record list, and any attack-lab cert that renders in the
// same container. See portal.js DragScroll for the gesture rules.
DragScroll.attach(document.getElementById('resultsWrap'));

// === Results sidebar management ===
function showResultsSidebar() {
  var rw = document.getElementById('resultsWrap');
  if (!rw) return;
  rw.classList.remove('dismissing');
  rw.classList.add('visible');
  var dm = document.querySelector('.panel-layout');
  if (dm) {
    // Fresh entry into compact mode — hold cert column offscreen
    // through the system box's width animation, then fade in.
    if (!dm.classList.contains('layout-active')) holdCertEntering(rw);
    dm.classList.add('layout-active');
  }
  if (window.innerWidth < 1200) scrollResultIntoView(rw);
}

function hideResultsSidebar(animate) {
  var rw = document.getElementById('resultsWrap');
  var dm = document.querySelector('.panel-layout');
  if (!rw || !rw.classList.contains('visible')) return;
  if (animate) {
    rw.classList.add('dismissing');
    rw.addEventListener('animationend', function() {
      rw.classList.remove('visible', 'dismissing');
      if (dm) dm.classList.remove('layout-active');
    }, { once: true });
  } else {
    rw.classList.remove('visible', 'dismissing');
    if (dm) dm.classList.remove('layout-active');
  }
}

// Clear every tab's results + left-panel input state except the one
// about to be populated. Called at the start of each tab's new query so
// stale state from one tab never sits next to fresh output from another.
function clearOtherResults(keep) {
  var divs = { img: 'imgResults', cert: 'certResults', meta: 'metaSidebarResults' };
  Object.keys(divs).forEach(function(k) {
    if (k === keep) return;
    var el = document.getElementById(divs[k]);
    if (el) { el.innerHTML = ''; el.style.display = ''; }
  });
  // Image tab — compact console (thumbnail + id + hash + status).
  if (keep !== 'img') {
    var con = document.getElementById('imgConsole');
    if (con) {
      con.classList.remove('visible', 'error-only');
      var t = document.getElementById('imgConsoleThumb'); if (t) t.src = '';
      var id = document.getElementById('imgConsoleId'); if (id) id.innerHTML = '';
      var h = document.getElementById('imgConsoleHash'); if (h) h.textContent = '';
      var s = document.getElementById('imgConsoleStatus');
      if (s) { s.textContent = ''; s.className = 'img-console-status'; }
    }
  }
  // Audit tab — identifier input + error slots + .how restored to default.
  if (keep !== 'cert') {
    var ai = document.getElementById('auditInput');
    if (ai) ai.value = '';
    if (typeof setAuditError === 'function') setAuditError('', '');
    if (typeof setHow === 'function' && _auditHowDefault !== null) setHow('default');
  }
  // Observatory tab — inline tab-error.
  if (keep !== 'meta') {
    var me = document.getElementById('metaError');
    if (me) { me.textContent = ''; me.style.color = ''; }
  }
}

function syncResultsVisibility(tabName) {
  var rw = document.getElementById('resultsWrap');
  if (!rw) return;
  var imgR = document.getElementById('imgResults');
  var certR = document.getElementById('certResults');
  var metaR = document.getElementById('metaSidebarResults');
  var hasImg = imgR && imgR.innerHTML.trim();
  var hasCert = certR && certR.innerHTML.trim();
  var hasMeta = metaR && metaR.innerHTML.trim();

  // Each tab owns ONE result slot. Visibility and compact-mode follow
  // the active tab's slot only — other tabs' results stay in DOM but
  // hidden, so switching back to them restores the result + compact.
  //   img  → imgResults
  //   cert → certResults
  //   meta → metaSidebarResults
  if (imgR)  imgR.style.display  = (tabName === 'img')  ? '' : 'none';
  if (certR) certR.style.display = (tabName === 'cert') ? '' : 'none';
  if (metaR) metaR.style.display = (tabName === 'meta') ? '' : 'none';

  if (tabName === 'attack') return;
  var activeHasContent =
    (tabName === 'img'  && !!hasImg)  ||
    (tabName === 'cert' && !!hasCert) ||
    (tabName === 'meta' && !!hasMeta);

  if (activeHasContent) {
    showResultsSidebar();
  } else if (rw.classList.contains('visible')) {
    hideResultsSidebar(true);
  }
}

// === Image UI ===
var dz=document.getElementById('dropZone'),imgResults=document.getElementById('imgResults');
DropZone.attach({
  zone: dz,
  accept: function(f) { return f.type.startsWith('image/'); },
  fileAccept: 'image/*',
  onFiles: analyze
});

// Clipboard paste routes by what was pasted (one global handler, not per-zone):
//   - .soul / .json files (one OR many) → Observatory (analyzeMeta)
//   - an image → Image tab (analyze the bar)
//   - plain text (an identifier) → left alone so it lands in Audit's input.
// Surfacing the right tab means a paste is visible wherever the user was.
document.addEventListener('paste', function(e) {
  if (!e.clipboardData) return;
  var files = Array.prototype.slice.call(e.clipboardData.files || []);
  var imgFile = null, soulFiles = [];
  files.forEach(function(f) {
    if (f.type && f.type.indexOf('image/') === 0) { if (!imgFile) imgFile = f; }
    else if (/\.(soul|json)$/i.test(f.name || '') || f.type === 'application/json') soulFiles.push(f);
  });
  if (!imgFile) {  // a screenshot paste may live only in items
    var it = Array.prototype.slice.call(e.clipboardData.items || [])
      .find(function(i) { return i.type && i.type.indexOf('image/') === 0; });
    if (it) imgFile = it.getAsFile();
  }
  if (soulFiles.length) {
    e.preventDefault();
    if (typeof showTab === 'function') showTab('meta');
    analyzeMeta(soulFiles);
    return;
  }
  if (imgFile) {
    e.preventDefault();
    if (typeof showTab === 'function') showTab('img');
    analyze(imgFile);
  }
});

// === Bar-image lightbox ===
// Click-to-enlarge, two flavors:
//   `.bar-zoom` — the bar region + scale/JPEG survival crops; tiny sources, so
//     an inspectable full-size view matters and pixels stay crisp (pixelated).
//   `.img-zoom` — the full Image preview; opens the full-resolution image
//     (from data-full) rendered smooth, since it's a photo, not bar pixels.
// Appended to <html> not <body> so the `body > *` position rule can't break the
// fixed centering.
(function () {
  var box = document.createElement('div');
  box.className = 'bar-lightbox';
  var img = document.createElement('img');
  img.className = 'bar-lightbox-img';
  img.alt = '';
  box.appendChild(img);
  document.documentElement.appendChild(box);
  function close() { box.classList.remove('open'); img.removeAttribute('src'); }
  box.addEventListener('click', close);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });
  document.addEventListener('click', function (e) {
    var t = e.target && e.target.closest && e.target.closest('.bar-zoom, .img-zoom');
    if (!t) return;
    var smooth = t.classList.contains('img-zoom');
    var src = (smooth && t.getAttribute('data-full')) || t.getAttribute('src');
    if (!src) return;
    img.src = src;
    img.classList.toggle('smooth', smooth);
    box.classList.add('open');
  });
})();

// === Reconstruct flow ===
// User drops the three saved band PNGs; the box reads each band's
// iTXt chunks (parent_id, parent_hash, fragment_id), confirms all
// three belong to the same certificate, and offers a one-click
// download of the canonical 2-row bar PNG. iTXt is authoritative;
// the pixel-bar fragment is a fallback for screenshotted bands but
// alone it can't prove cross-band parentage (each carries only its
// piece), so the iTXt path is required for now.
var _recState = { gen: null, sky: null, machine: null };
function _recSlot(fid) { return document.querySelector('.reconstruct-slot[data-slot="' + fid + '"]'); }
function _renderReconstruct() {
  var fids = Object.keys(_recState).filter(function(k) { return _recState[k]; });
  var pids = fids.map(function(k) { return _recState[k].parentId; });
  var allAgree = pids.length === 0 || pids.every(function(p) { return p === pids[0]; });
  ['gen', 'sky', 'machine'].forEach(function(fid) {
    var el = _recSlot(fid);
    var stateEl = el.querySelector('.reconstruct-slot-state');
    var f = _recState[fid];
    el.classList.remove('filled', 'mismatch');
    if (f) {
      el.classList.add(allAgree ? 'filled' : 'mismatch');
      stateEl.textContent = allAgree ? '\u2713' : '\u2717';
    } else {
      stateEl.innerHTML = '\u00b7 \u00b7 \u00b7';
    }
  });
  var statusEl = document.getElementById('reconstructStatus');
  var btn = document.getElementById('reconstructBtn');
  if (!allAgree) {
    statusEl.className = 'reconstruct-status error';
    statusEl.textContent = 'these relics belong to different souls.';
    btn.disabled = true;
    return;
  }
  statusEl.className = 'reconstruct-status';
  if (fids.length === 0) {
    statusEl.textContent = '';
  } else if (fids.length === 3) {
    statusEl.textContent = 'the relics are gathered. the spirit of ' + (pids[0] || '?') + ' can re-form.';
  } else {
    var missing = ['gen', 'sky', 'machine'].filter(function(f) { return !_recState[f]; });
    statusEl.textContent = 'gathered ' + fids.join(' + ') + '. drop ' + missing.join(' + ') + ' to call the spirit back.';
  }
  btn.disabled = !(fids.length === 3 && allAgree);
}
function _readFragmentFile(file) {
  return new Promise(function(resolve) {
    if (!file || !file.type || file.type.indexOf('image/') !== 0) { resolve(null); return; }
    var reader = new FileReader();
    reader.onload = function() {
      var chunks = (typeof readPngTextChunks === 'function') ? readPngTextChunks(reader.result) : {};
      var fid = chunks.fragment_id;
      var pid = chunks.parent_id;
      var phash = chunks.parent_hash;
      if (fid && pid && phash && (fid === 'gen' || fid === 'sky' || fid === 'machine')) {
        resolve({ fragmentId: fid, parentId: pid, parentHash: phash });
      } else {
        resolve(null);
      }
    };
    reader.onerror = function() { resolve(null); };
    reader.readAsArrayBuffer(file);
  });
}
function _ingestFragments(files) {
  var statusEl = document.getElementById('reconstructStatus');
  var arr = Array.from(files || []);
  if (!arr.length) return;
  var promises = arr.map(_readFragmentFile);
  Promise.all(promises).then(function(results) {
    var added = 0, rejected = 0;
    results.forEach(function(r) {
      if (r) { _recState[r.fragmentId] = r; added++; }
      else rejected++;
    });
    _renderReconstruct();
    if (added === 0 && rejected > 0) {
      statusEl.className = 'reconstruct-status error';
      statusEl.textContent = rejected === 1
        ? 'that PNG isn\u2019t a band relic.'
        : 'none of those PNGs are band relics.';
    }
  });
}
DropZone.attach({
  zone: document.getElementById('reconstructZone'),
  input: document.getElementById('reconstructInput'),
  accept: function(f) { return f.type.indexOf('image/') === 0; },
  multiple: true,
  onFiles: _ingestFragments
});
document.getElementById('reconstructBtn').addEventListener('click', function(e) {
  // Button lives inside the drop zone; stop the click from bubbling
  // up so the zone doesn't also fire its file-picker handler.
  e.stopPropagation();
  var any = _recState.gen || _recState.sky || _recState.machine;
  if (!any || typeof generateCanonicalBarPng !== 'function') return;
  generateCanonicalBarPng(any.parentId, any.parentHash).then(function(blob) {
    if (!blob) return;
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url; a.download = any.parentId + '.bar.png';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function() { URL.revokeObjectURL(url); }, 2000);
  });
});

// Raw bit brightness for forensic strip
function extractBitBrightness(px,w,h,ppb){ppb=ppb||PIXELS_PER_BIT;var v=[],dpr=w-HEADER_PIXELS-FOOTER_PIXELS,bpr=Math.floor(dpr/ppb);for(var row=0;row<SIG_ROWS;row++){var y=h-1-row;for(var b=0;b<bpr;b++){var cx=HEADER_PIXELS+b*ppb+Math.floor(ppb/2);var i=(y*w+cx)*4;v.push((px[i]+px[i+1]+px[i+2])/3);}}return v;}

// Run fn once the element with id `elId` is actually in the DOM. The forensic
// panel is inserted through PanelSwap, whose cross-fade path (when a result is
// already on screen — e.g. right after a Reliquary reform) DEFERS the insertion
// by a transition. A plain setTimeout(0) fill would then fire before its rows
// exist, get null from getElementById, and leave every row stuck on
// "analyzing…". Poll a few frames for the row to mount, then run (capped so it
// always runs even if the element never appears).
function _runWhenMounted(elId, fn) {
  var tries = 0;
  (function poll() {
    if (document.getElementById(elId) || tries++ > 120) { fn(); return; }
    requestAnimationFrame(poll);
  })();
}

function analyze(file){
  clearOtherResults('img');
  imgResults.innerHTML='';
  // Shared decode pipeline — js/image-decode.js. Returns the canvas +
  // raw pixel buffer so the forensic bits below (bar-region crop,
  // color-band measurement, brightness strip) can still read from
  // the same source data without re-decoding.
  decodeImageBar(file).then(function(res) {
    var w = res.width, h = res.height;
    var px = res.pixels;
    var detected = res.detected;
    var decoded = res.decoded;
    var barFrame = res.frame;
    var barPpb = res.ppb || 3;
    // The bar may be relocated/offset — decodeImageBar reports its bottom row.
    // Read EVERY bar measurement at barRow (effective height = barRow+1) so bit
    // confidence, band purity, etc. read the BAR, not the bottom/black fill. The
    // bar-pixel readers take (px,w,h) and read rows < h, so a reduced h reads a
    // higher row pair with no copy — the same trick the decoder/scan use.
    var barBottom = (res.barRow == null ? h - 1 : res.barRow);
    var barEffH = barBottom + 1;
    var barBright = detected ? extractBitBrightness(px, w, barEffH, barPpb) : null;

    // Bar region crop (bottom 16px, 4x zoom) — redraw from the canvas
    // already built by decodeImageBar so we don't hold onto a second
    // Image element.
    var barH=Math.min(16,h);
    // Crop the window ENDING at the bar's bottom row — h-1 when it sits at the
    // bottom (identical to the old crop), or wherever the vertical scan found a
    // relocated/offset bar (decodeImageBar reports res.barRow).
    var barTop=Math.max(0,barBottom-barH+1);
    var bc=document.createElement('canvas');bc.width=w;bc.height=barH*4;
    var bctx=bc.getContext('2d');bctx.imageSmoothingEnabled=false;
    bctx.drawImage(res.canvas,0,barTop,w,barH,0,0,w,barH*4);
    var barUri=bc.toDataURL('image/png');

    // Color band measurement + purity
    var bands={},bandRaw={};
    var idealBands={M:[255,0,255],Y:[255,255,0],C:[0,255,255]};
    if(h>=2&&w>=50){var y=barBottom,mid=Math.floor(HEADER_BAND/2);
      for(var bi=0;bi<3;bi++){var bpos=bi*HEADER_BAND+mid;var idx=(y*w+bpos)*4;var lbl=['M','Y','C'][bi];
        bands[lbl]='rgb('+px[idx]+','+px[idx+1]+','+px[idx+2]+')';bandRaw[lbl]=[px[idx],px[idx+1],px[idx+2]];}}

      // Error rule: when the image has no Mememage bar at all, mirror
      // decoder's error flow — inline status, no compact, no right-side
      // forensic panel. Anything forensic-worthy (bands present but
      // unreadable, etc.) still runs through the full report below.
      if(!detected){
        var imgConE = document.getElementById('imgConsole');
        imgConE.classList.remove('visible');
        imgConE.classList.add('error-only');
        var stE = document.getElementById('imgConsoleStatus');
        stE.className = 'img-console-status fail';
        stE.textContent = 'No Mememage bar in this image.';
        imgResults.innerHTML = '';
        var rwE = document.getElementById('resultsWrap');
        if (rwE && rwE.classList.contains('visible')) hideResultsSidebar(true);
        return;
      }

      // Thumbnail — sized to the panel (no upscaling) + high-quality downscale
      // so the "Image" preview at the bottom reads clean, not blocky.
      var tw=Math.min(w,760),th=Math.round(h*tw/w);
      var tc=document.createElement('canvas');tc.width=tw;tc.height=th;
      var tctx=tc.getContext('2d');tctx.imageSmoothingEnabled=true;tctx.imageSmoothingQuality='high';
      tctx.drawImage(res.canvas,0,0,tw,th);
      var thumbUri=tc.toDataURL('image/jpeg',0.92);
      // Full-resolution version for the click-to-enlarge lightbox. Images
      // already at/under the preview width reuse the thumb (it's native size);
      // larger ones render full-res at high quality so enlarging shows real
      // detail, JPEG to keep the data URL from ballooning.
      var fullUri = (w <= tw) ? thumbUri : res.canvas.toDataURL('image/jpeg', 0.95);

      // === Whole-image header (once) ===
      // Capture full-image buffers before the per-bar loop shadows px/w/h.
      var _opx = px, _ow = w, _oh = h;
      var _oname = escapeHtml(file.name);
      var _otype = escapeHtml(file.type || file.name.split('.').pop());
      // EVERY decodable bar, any placement (mirrors bar.py:extract_bars); each
      // gets its OWN full diagnostic panel below. Fall back to the primary
      // decode attempt when nothing decoded (bar detected but unreadable).
      var allBars = (typeof decodeAllBars === 'function') ? decodeAllBars(_opx, _ow, _oh) : [];
      var barList = allBars.length ? allBars : [{
        identifier: decoded ? decoded.identifier : null,
        content_hash: decoded ? decoded.content_hash : null,
        barRow: barBottom, x0: 0, x1: _ow - 1, fullWidth: true, ppb: barPpb, frame: barFrame }];

      var o='<div class="ev">';
      o+='<div class="ev-h '+(decoded?'both':'lost')+'"><span class="ev-t">'+_oname+'</span><span class="ev-b '+(decoded?'both':'lost')+'">'+(barList.length>1?barList.length+' bars':(decoded?'Bar Survived':'Bar Lost'))+'</span></div>';
      o+='<div class="ev-body">';
      o+='<div class="ev-sec">Image</div><div class="ev-g">';
      o+='<div class="ev-m"><div class="ev-ml">Size</div><div class="ev-mv">'+(file.size/1024).toFixed(0)+' KB</div></div>';
      o+='<div class="ev-m"><div class="ev-ml">Dimensions</div><div class="ev-mv">'+_ow+' × '+_oh+'</div></div>';
      o+='<div class="ev-m"><div class="ev-ml">Format</div><div class="ev-mv">'+_otype+'</div></div>';
      o+='<div class="ev-m"><div class="ev-ml">Bars detected</div><div class="ev-mv">'+barList.length+'</div></div>';
      o+='</div>';

      // === One full diagnostic panel per detected bar ===
      barList.forEach(function(bar, idx){
        // Crop the bar's region so an offset/pasted bar is analysed as if flush
        // at the bottom; the section code below (unchanged) reads px/w/h etc.,
        // which we SHADOW here to the crop. The watermark diff still uses the
        // whole image (_opx/_ow/_oh), keyed by THIS bar's hash.
        var _x0 = bar.fullWidth ? 0 : bar.x0, _x1 = bar.fullWidth ? _ow - 1 : bar.x1;
        var _crop = _cropPixels(_opx, _ow, _x0, _x1, bar.barRow);
        var px = _crop.px, w = _crop.w, h = _crop.h;
        var res = { canvas: (function(){ var c=document.createElement('canvas'); c.width=w; c.height=h; c.getContext('2d').putImageData(new ImageData(px, w, h), 0, 0); return c; })() };
        var decoded = bar.identifier ? { identifier: bar.identifier, content_hash: bar.content_hash } : null;
        var barFrame = bar.frame, barPpb = bar.ppb || 3;
        var barBottom = h - 1, barEffH = h;
        var barBright = extractBitBrightness(px, w, barEffH, barPpb);
        var barH = Math.min(16, h), barTop = Math.max(0, barBottom - barH + 1);
        var _bc = document.createElement('canvas'); _bc.width=w; _bc.height=barH*4;
        var _bctx=_bc.getContext('2d'); _bctx.imageSmoothingEnabled=false; _bctx.drawImage(res.canvas,0,barTop,w,barH,0,0,w,barH*4);
        var barUri = _bc.toDataURL('image/png');
        var bands={},bandRaw={},idealBands={M:[255,0,255],Y:[255,255,0],C:[0,255,255]};
        if(h>=2&&w>=50){var _by=barBottom,_bmid=Math.floor(HEADER_BAND/2);
          for(var _bbi=0;_bbi<3;_bbi++){var _bpos=_bbi*HEADER_BAND+_bmid;var _bidx=(_by*w+_bpos)*4;var _blbl=['M','Y','C'][_bbi];
            bands[_blbl]='rgb('+px[_bidx]+','+px[_bidx+1]+','+px[_bidx+2]+')';bandRaw[_blbl]=[px[_bidx],px[_bidx+1],px[_bidx+2]];}}
        var safeHash = escapeHtml(decoded ? decoded.content_hash : '');
        var safeId   = escapeHtml(decoded ? (decoded.identifier || '') : '');
        var _place = bar.fullWidth ? (bar.barRow >= _oh - 2 ? 'bottom edge (standard)' : 'row '+bar.barRow+', full width')
                                   : 'row '+bar.barRow+', x'+bar.x0+'–'+bar.x1+' (offset)';
        o+='<div style="border:1px solid #1a1a2a;border-radius:5px;margin:0.4rem 0;overflow:hidden;">';
        o+='<div onclick="var d=document.getElementById(\'barpanel-'+idx+'\');d.style.display=d.style.display===\'none\'?\'block\':\'none\';" style="padding:0.45rem 0.6rem;cursor:pointer;display:flex;align-items:center;gap:0.5rem;background:rgba(255,255,255,0.04);">';
        o+='<span style="font-size:0.7rem;color:'+(decoded?'#4ade80':'#f87171')+';">'+(decoded?'●':'○')+'</span>';
        o+='<span style="font-size:0.72rem;font-family:monospace;color:#d0d0d8;">'+(decoded?escapeHtml(decoded.identifier.slice(-16)):'unreadable')+'</span>';
        o+='<span style="font-size:0.58rem;color:#8a8a9a;margin-left:auto;">'+_place+'</span></div>';
        o+='<div id="barpanel-'+idx+'" style="display:'+(idx===0?'block':'none')+';padding:0.1rem 0.6rem 0.5rem;">';

      // Bar region
      o+='<div class="ev-sec">Bar Region ('+_place+', '+barH+'px, 4x zoom)</div>';
      o+='<img src="'+barUri+'" class="bar-img bar-zoom" alt="Bar region"/>';

      // Bar results
      o+='<div class="ev-sec">Bar</div><div class="ev-g">';
      if(decoded){
        o+='<div class="ev-m"><div class="ev-ml">Status</div><div class="ev-mv pass">SURVIVED</div></div>';
        o+='<div class="ev-m"><div class="ev-ml">Content Hash</div><div class="ev-mv pass">'+safeHash+'</div></div>';
        o+='<div class="ev-m"><div class="ev-ml">Identifier</div><div class="ev-mv">'+(safeId?'<a href="#" class="audit-link" data-id="'+safeId+'" style="color:inherit;text-decoration:underline;text-decoration-color:rgba(255,255,255,0.2);cursor:pointer;">'+safeId+'</a>':'\u2014')+'</div></div>';
        o+='<div class="ev-m"><div class="ev-ml">Soul</div><div class="ev-mv" style="font-size:0.68rem;">'+safeId+'.soul</div></div>';
      }else{
        o+='<div class="ev-m"><div class="ev-ml">Status</div><div class="ev-mv fail">LOST</div></div>';
        o+='<div class="ev-m w"><div class="ev-ml">Diagnosis</div><div class="ev-mv fail">'+(detected?'M/Y/C bands detected but data unreadable \u2014 compression destroyed brightness encoding':'No M/Y/C bands found \u2014 image cropped, resized, or not a Mememage image')+'</div></div>';
      }
      if(bands.M)o+='<div class="ev-m"><div class="ev-ml">M / Y / C</div><div class="ev-mv" style="font-size:0.68rem;">'+bands.M+' '+bands.Y+' '+bands.C+'</div></div>';
      o+='</div>';

      // Bar Bit Confidence
      if(barBright&&barBright.length>0){
        o+='<div class="ev-sec">Bar Bit Confidence</div>';
        o+='<div style="font-size:0.65rem;color:#8a8a9a;margin-bottom:0.3rem;">Green=clear 1, red=clear 0, yellow=fragile (near threshold 128)</div>';
        var bbl=Math.min(barBright.length,600);
        var bbc=document.createElement('canvas');bbc.width=bbl;bbc.height=1;var bbx=bbc.getContext('2d');var bbd=bbx.createImageData(bbl,1);
        for(var bi2=0;bi2<bbl;bi2++){var bv=barBright[bi2];var dist=Math.abs(bv-128)/128;var p=bi2*4;
          if(bv>=128){bbd.data[p]=Math.round(255*(1-dist));bbd.data[p+1]=Math.round(120+135*dist);bbd.data[p+2]=Math.round(60*(1-dist));}
          else{bbd.data[p]=Math.round(120+135*dist);bbd.data[p+1]=Math.round(255*(1-dist));bbd.data[p+2]=Math.round(60*(1-dist));}
          bbd.data[p+3]=255;}
        bbx.putImageData(bbd,0,0);
        var bbu=document.createElement('canvas');bbu.width=bbl*2;bbu.height=16;var bbux=bbu.getContext('2d');bbux.imageSmoothingEnabled=false;bbux.drawImage(bbc,0,0,bbl*2,16);
        o+='<img src="'+bbu.toDataURL('image/png')+'" class="bar-zoom" style="width:100%;image-rendering:pixelated;height:16px;border-radius:3px;"/>';
        var fragile=0;for(var bi3=0;bi3<barBright.length;bi3++)if(Math.abs(barBright[bi3]-128)<30)fragile++;
        o+='<div style="font-size:0.6rem;color:#8a8a9a;margin-top:0.2rem;">'+barBright.length+' bits at '+barPpb+'px/bit \u2014 <span style="color:'+(fragile>barBright.length*0.3?'#f87171':'#4ade80')+';">'+fragile+' fragile</span></div>';
      }

      // Watermark (distributed DCT layer — the body's knowledge of its own name).
      // Per-image extraction needs the content hash (from the bar) to locate the
      // coefficient + tile layout; without a bar only a legacy watermark is blind-
      // findable. Runs client-side (docs/js/watermark.js) — no backend.
      try {
        if (typeof extractWatermark === 'function') {
          // Whole-image extraction (the watermark spans the whole body, not the
          // bar crop) diffed against THIS bar's hash — the watermark carries the
          // content hash, so a match ties the pixels to this bar independently.
          var wmHash = decoded ? decoded.content_hash : null;
          var wm = extractWatermark(_opx, _ow, _oh, wmHash);
          o += '<div class="ev-sec">Watermark</div><div class="ev-g">';
          if (wm) {
            var wmMatch = decoded && wm.hash === decoded.content_hash;
            o += '<div class="ev-m"><div class="ev-ml">Status</div><div class="ev-mv pass">DETECTED</div></div>';
            o += '<div class="ev-m"><div class="ev-ml">Hash (watermark)</div><div class="ev-mv ' + (decoded ? (wmMatch ? 'pass' : 'fail') : '') + '" style="font-size:0.68rem;">' + escapeHtml(wm.hash) + '</div></div>';
            if (decoded) o += '<div class="ev-m"><div class="ev-ml">Matches bar</div><div class="ev-mv ' + (wmMatch ? 'pass' : 'fail') + '">' + (wmMatch ? 'YES' : 'NO') + '</div></div>';
            o += '<div class="ev-m"><div class="ev-ml">Confidence</div><div class="ev-mv">' + (wm.confidence * 100).toFixed(0) + '%</div></div>';
            o += '<div class="ev-m"><div class="ev-ml">Sync marker</div><div class="ev-mv">' + (wm.syncOk ? 'locked (0xAD)' : 'not found') + '</div></div>';
            o += '<div class="ev-m"><div class="ev-ml">Mode</div><div class="ev-mv" style="font-size:0.68rem;">' + escapeHtml(wm.mode) + '</div></div>';
            o += '<div class="ev-m"><div class="ev-ml">Tile offset</div><div class="ev-mv">' + wm.offsetX + ', ' + wm.offsetY + '</div></div>';
            o += '<div class="ev-m"><div class="ev-ml">DCT coeff</div><div class="ev-mv">(' + wm.coeffRow + ', ' + wm.coeffCol + ')</div></div>';
            o += '<div class="ev-m"><div class="ev-ml">Blocks voting</div><div class="ev-mv">' + wm.blocks + '</div></div>';
          } else {
            o += '<div class="ev-m"><div class="ev-ml">Status</div><div class="ev-mv">none detected</div></div>';
            o += '<div class="ev-m w"><div class="ev-ml">Note</div><div class="ev-mv" style="font-size:0.66rem;color:#8a8a9a;">Not configured, or did not survive.' + (decoded ? '' : ' Per-image needs the bar to locate.') + '</div></div>';
          }
          o += '</div>';
        }
      } catch (e) { /* watermark optional — never block the bar report */ }

      // Reed-Solomon Error Correction
      if(barFrame){
        o+='<div class="ev-sec">Reed-Solomon Error Correction</div><div class="ev-g">';
        var rsE=barFrame.rsErrors>=0?barFrame.rsErrors:'?',rsC=barFrame.rsCapacity;
        var rsCol=barFrame.rsErrors===0?'#4ade80':barFrame.rsErrors>0&&barFrame.rsErrors<rsC?'#facc15':'#f87171';
        o+='<div class="ev-m"><div class="ev-ml">Errors Corrected</div><div class="ev-mv" style="color:'+rsCol+';">'+rsE+' / '+rsC+' max</div></div>';
        o+='<div class="ev-m"><div class="ev-ml">Parity Bytes</div><div class="ev-mv">6 (GF(2\u2078))</div></div>';
        o+='<div class="ev-m w"><div class="ev-ml">Error Budget</div><div style="height:12px;background:rgba(40,40,50,0.5);border-radius:6px;margin-top:0.2rem;overflow:hidden;display:flex;">';
        if(barFrame.rsErrors>=0)for(var ri=0;ri<rsC;ri++)o+='<div style="flex:1;background:'+(ri<barFrame.rsErrors?rsCol:'rgba(60,60,70,0.4)')+';margin-right:1px;"></div>';
        o+='</div><div style="font-size:0.58rem;color:#8a8a9a;margin-top:0.15rem;">'+(barFrame.rsErrors===0?'Pristine':barFrame.rsErrors>0?rsE+' corrected, '+(rsC-barFrame.rsErrors)+' remaining':'CRC fallback')+'</div></div>';
        o+='</div>';
      }

      // Color Band Purity — exactly three bands, so lay them out 3-up in one
      // row instead of the default 2-col .ev-g (which leaves Cyan alone on a
      // second row). Each cell: label, swatch + %, thin purity bar.
      if(bandRaw.M){
        o+='<div class="ev-sec">Color Band Purity</div>';
        o+='<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:0.3rem;">';
        var bLabels={M:'Magenta',Y:'Yellow',C:'Cyan'};
        for(var bl of['M','Y','C']){var act=bandRaw[bl],ide=idealBands[bl];
          var dist2=Math.sqrt(Math.pow(act[0]-ide[0],2)+Math.pow(act[1]-ide[1],2)+Math.pow(act[2]-ide[2],2));
          var pur=Math.max(0,1-dist2/441.7),pPct=Math.round(pur*100);
          var pCol=pur>0.8?'#4ade80':pur>0.5?'#facc15':'#f87171';
          o+='<div class="ev-m" style="text-align:center;">';
          o+='<div class="ev-ml">'+bLabels[bl]+'</div>';
          o+='<div style="display:flex;align-items:center;justify-content:center;gap:0.35rem;margin:0.25rem 0;">';
          o+='<div style="width:10px;height:10px;border-radius:2px;background:rgb('+act[0]+','+act[1]+','+act[2]+');border:1px solid rgba(255,255,255,0.15);"></div>';
          o+='<span style="font-size:0.72rem;color:'+pCol+';font-weight:600;">'+pPct+'%</span></div>';
          o+='<div style="height:5px;background:rgba(40,40,50,0.5);border-radius:3px;overflow:hidden;"><div style="width:'+pPct+'%;height:100%;background:'+pCol+';"></div></div>';
          o+='</div>';}
        o+='</div>';
      }

      // Resilience panels — what the bar survives, three lenses:
      //   1. Bar Architecture — which width-adaptive layout this image uses
      //      (even-fill vs sequential) and the downscale floor that implies.
      //   2. Scale Survival   — actually downscale the image and re-read the
      //      bar to find the real floor. Meaningful now that even-fill makes
      //      downscale survival real (it scales the bits with the image); the
      //      old panel that reported LOST on every scale predated even-fill.
      //   3. JPEG Survival     — re-encode at falling quality; M/Y/C bands are
      //      DCT-block-aligned (8px = one JPEG block) and the luminance bits
      //      survive q50+ because JPEG keeps luma at full resolution.

      // === Bar Architecture + Scale Survival (derived instantly from width
      // + identifier; gated on a readable bar). ===
      if (decoded) {
        // Two layouts share one frame format (mememage/bar.py): even-fill
        // engages when the data region (width minus 48px of flush bands)
        // holds the whole frame at >=3px/bit; below the crossover the bar
        // falls back to the sequential split-row layout. We can name which
        // one this image uses from width + identifier length alone.
        // identifier = prefix + '-' + 16 hex => prefixLen = id.length - 17.
        // Packed frame (mememage/bar.py:embed_into): payload = [1B prefix_len]
        // [prefix][8B id][8B hash] = prefixLen + 17, then the Gen I frame adds an
        // 8B header (2 magic + 1 gen + 1 nsym + 2 payload_len + 2 CRC) and 6 RS
        // parity bytes => frame = prefixLen + 31 bytes => frameBits = that × 8.
        // (Was +48 — the pre-packed-format payload size; the packed refactor
        // shrank the frame, so the old constant over-stated the crossover and
        // mislabeled wide even-fill bars as SEQUENTIAL.)
        var fid = decoded.identifier || '';
        var prefixLen = Math.max(1, fid.length - 17);
        var frameBits = (prefixLen + 31) * 8;
        var crossoverW = frameBits * 3 + 48;
        var isEven = (w - 48) >= frameBits * 3;
        // Empirical downscale floor: a fat even-fill bit survives until it
        // shrinks below ~3.3 destination px (calibrated to the ~0.37x floor
        // documented at 4096px). Sequential bits already sit at the 3px
        // floor, so they have no downscale headroom. The Scale Survival
        // meter below measures the ACTUAL floor; this is the expected value.
        var floorAt = function(width){ var f = 3.3 * frameBits / (width - 48); return f > 1 ? 1 : f; };
        var fmtFloor = function(f){ return f >= 0.995 ? '≈1.0×' : ('~' + f.toFixed(2) + '×'); };

        o+='<div class="ev-sec">Bar Architecture</div>';
        var layoutCol = isEven ? '#4ade80' : '#facc15';
        o+='<div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.35rem;">';
        o+='<span style="font-size:0.7rem;padding:0.15rem 0.5rem;border-radius:3px;background:rgba(120,120,160,0.12);color:'+layoutCol+';font-weight:700;letter-spacing:0.04em;">'+(isEven?'EVEN-FILL':'SEQUENTIAL')+'</span>';
        o+='<span style="font-size:0.65rem;color:#8a8a9a;">'+w+'×'+h+' · crossover '+crossoverW+'px</span>';
        o+='</div>';
        if (isEven) {
          o+='<div style="font-size:0.62rem;color:#8a8a9a;margin-bottom:0.4rem;line-height:1.5;">Fat bits fill both rows and scale with the image, so it survives downscaling — bigger images go lower. Expected floor: <span style="color:#c0c0d0;font-weight:600;">'+fmtFloor(floorAt(w))+'</span>.</div>';
        } else {
          o+='<div style="font-size:0.62rem;color:#8a8a9a;margin-bottom:0.4rem;line-height:1.5;">Compact layout below the crossover.'
            +'<ul style="margin:0.3rem 0 0;padding-left:1.1rem;">'
            +'<li>JPEG-resilient</li>'
            +'<li>Bits at minimum width — <span style="color:#facc15;">not downscale-resilient</span></li>'
            +'<li>Mint at ≥'+crossoverW+'px wide for even-fill</li>'
            +'</ul></div>';
        }
        // Resolution ladder — how the downscale floor improves with mint
        // width for this image's prefix. Surfaces the resolution feature.
        var rungs = [crossoverW, 2048, 3072, 4096].filter(function(x){ return x >= crossoverW; });
        var rungSeen = {}; rungs = rungs.filter(function(x){ if(rungSeen[x])return false; rungSeen[x]=true; return true; });
        o+='<div style="font-size:0.55rem;color:#8a8a9a;margin-bottom:2px;">Downscale floor by mint width</div>';
        o+='<div style="border:1px solid rgba(120,120,140,0.18);border-radius:4px;overflow:hidden;margin-bottom:0.5rem;">';
        for (var ri = 0; ri < rungs.length; ri++) {
          var rw = rungs[ri];
          var here = Math.abs(rw - w) < 1;
          o+='<div style="display:flex;justify-content:space-between;padding:0.22rem 0.5rem;font-size:0.66rem;'+(ri%2?'background:rgba(40,40,60,0.06);':'')+(here?'background:rgba(74,158,74,0.10);':'')+'">';
          o+='<span style="color:'+(here?'#4ade80':'#a0a0b0')+';">'+rw+'px'+(here?' ← this image':'')+'</span>';
          o+='<span style="color:#c0c0d0;font-weight:600;">'+fmtFloor(floorAt(rw))+'</span></div>';
        }
        o+='</div>';

        // --- Scale Survival (resolution-aware, async) ---
        o+='<div class="ev-sec">Scale Survival</div>';
        if (isEven) {
          o+='<div style="font-size:0.62rem;color:#8a8a9a;margin-bottom:0.3rem;">Downscales the image and re-reads the bar — the measured floor for this image.</div>';
          var scales = [0.9, 0.75, 0.6, 0.5, 0.4, 0.3];
          // Placeholder rows + floor, filled async (mirrors JPEG Survival).
          // The per-scale work (downscale + re-read) is heavy — the even-fill
          // anchor sweep is LEGITIMATE here because the dims change, so unlike
          // JPEG survival we keep the full sweep (no `fast`). Running it
          // synchronously froze the panel render on large even-fill images;
          // deferring + yielding between scales keeps the UI alive and fills
          // each row progressively.
          scales.forEach(function(s, i){
            o += '<div id="scaleRow-' + idx + '-' + i + '" style="padding:0.4rem 0.5rem;background:rgba(40,40,60,0.05);border-left:3px solid rgba(120,120,140,0.3);border-radius:4px;margin-bottom:0.3rem;">';
            o += '<div style="display:flex;align-items:center;gap:0.6rem;">';
            o += '<span style="font-size:0.85rem;color:#c0c0d0;font-weight:700;">' + s.toFixed(2) + '×</span>';
            o += '<span style="font-size:0.65rem;color:#8a8a9a;">analyzing…</span>';
            o += '</div></div>';
          });
          o += '<div id="scaleFloor-' + idx + '" style="font-size:0.62rem;color:#8a8a9a;margin-bottom:0.3rem;">Measuring floor…</div>';
          // Defer the heavy re-reads until after the panel is in the DOM.
          _runWhenMounted('scaleRow-' + idx + '-0', async function(){
            var lowestOk = null;
            for (var sIdx = 0; sIdx < scales.length; sIdx++) {
              var s = scales[sIdx];
              var sw = Math.max(1, Math.round(w * s)), sh = Math.max(1, Math.round(h * s));
              var sok = false, sUri = null;
              try {
                var sc = document.createElement('canvas'); sc.width = sw; sc.height = sh;
                var sx = sc.getContext('2d'); sx.imageSmoothingEnabled = true; sx.imageSmoothingQuality = 'high';
                sx.drawImage(res.canvas, 0, 0, sw, sh);
                var spx = sx.getImageData(0, 0, sw, sh).data;
                var sres = extractBarScaleAware(spx, sw, sh);
                sok = !!sres;
                var sbH = Math.min(6, sh);
                // Where the bar IS if re-found; else where it SHOULD be (the
                // original row scaled by s) — never the bottom for an offset bar.
                var sRow = sres ? sres.bottomRow : Math.max(0, Math.min(sh - 1, Math.round(barBottom * s)));
                var sbc = document.createElement('canvas'); sbc.width = sw; sbc.height = sbH * 4;
                var sbx = sbc.getContext('2d'); sbx.imageSmoothingEnabled = false;
                sbx.drawImage(sc, 0, Math.max(0, sRow - sbH + 1), sw, sbH, 0, 0, sw, sbH * 4);
                sUri = sbc.toDataURL('image/png');
              } catch (e) { sok = false; }
              if (sok) lowestOk = s;
              var row = document.getElementById('scaleRow-' + idx + '-' + sIdx);
              if (row) {
                var sBg = sok ? 'rgba(74,158,74,0.08)' : 'rgba(180,60,60,0.06)';
                var sBd = sok ? 'rgba(74,158,74,0.5)' : 'rgba(180,60,60,0.4)';
                var html = '';
                html += '<div style="display:flex;align-items:center;gap:0.6rem;margin-bottom:0.3rem;">';
                html += '<span style="font-size:0.85rem;color:#c0c0d0;font-weight:700;">' + s.toFixed(2) + '×</span>';
                html += '<span style="font-size:0.65rem;color:#8a8a9a;">' + sw + '×' + sh + '</span>';
                html += '<span style="font-size:0.65rem;padding:0.1rem 0.4rem;border-radius:3px;background:' + (sok?'rgba(74,158,74,0.15)':'rgba(180,60,60,0.15)') + ';color:' + (sok?'#4ade80':'#f87171') + ';font-weight:600;">Bar ' + (sok?'SURVIVED':'LOST') + '</span>';
                html += '</div>';
                if (sUri) {
                  html += '<div style="font-size:0.55rem;color:#8a8a9a;margin-bottom:2px;">Bar region @ ' + s.toFixed(2) + '×</div>';
                  html += '<img src="' + sUri + '" class="bar-zoom" style="width:100%;image-rendering:pixelated;border-radius:3px;opacity:0.85;"/>';
                }
                row.style.background = sBg; row.style.borderLeftColor = sBd; row.innerHTML = html;
              }
              // Yield so the UI can paint between heavy scales.
              await new Promise(function(r){ setTimeout(r, 0); });
            }
            var floor = document.getElementById('scaleFloor-' + idx);
            if (floor) {
              var floorTxt = lowestOk ? ('survives down to ~'+lowestOk.toFixed(2)+'×') : ('lost even at 0.90× — unusually fragile, check the source');
              floor.innerHTML = 'Measured floor: <span style="font-weight:600;">'+floorTxt+'</span>.';
              floor.style.color = '#c0c0d0';
            }
          });
        } else {
          o+='<div style="font-size:0.62rem;color:#8a8a9a;margin-bottom:0.3rem;line-height:1.5;">Skipped — sequential bars sit at the minimum bit width, so they have no downscale headroom. Mint at ≥'+crossoverW+'px wide for even-fill’s resize resilience. JPEG Survival below still applies.</div>';
        }
      }

      o+='<div class="ev-sec">JPEG Survival</div>';
      o+='<div style="font-size:0.62rem;color:#8a8a9a;margin-bottom:0.3rem;">Re-encodes as JPEG at each quality and re-reads the bar (solid = survived, dashed = lost). Every platform JPEG-encodes uploads \u2014 what the bar is built to survive.</div>';
      if (barBottom < h - 1) {
        o+='<div style="font-size:0.6rem;color:#facc15;background:rgba(250,204,21,0.08);border-left:3px solid rgba(250,204,21,0.5);border-radius:4px;padding:0.4rem 0.5rem;margin-bottom:0.4rem;line-height:1.5;">Bar position changed (row '+barBottom+', not the bottom edge). JPEG survival below reflects that position.</div>';
      }
      var jpegLevels = [95, 85, 70, 50, 30];
      var jpegDone = 0;
      function jpegOneLevel(q, slotId) {
        // Async — canvas.toBlob is async on Safari. Process sequentially.
        return new Promise(function(resolve){
          res.canvas.toBlob(function(blob){
            if (!blob) { resolve({q:q, ok:false, dataUrl:null, dims:null}); return; }
            var url = URL.createObjectURL(blob);
            var im = new Image();
            // De-hang: a missing onerror used to leave the row stuck on
            // "analyzing" forever if the re-encoded blob failed to load
            // (large WebP sources tripped this). onerror + a try/catch in
            // onload guarantee every level resolves to a verdict.
            im.onerror = function(){
              try { URL.revokeObjectURL(url); } catch (e) {}
              resolve({q:q, ok:false, dataUrl:null, dims:null, blobSize:blob.size});
            };
            im.onload = function(){
              try {
                var jc = document.createElement('canvas');
                jc.width = im.width; jc.height = im.height;
                jc.getContext('2d').drawImage(im, 0, 0);
                var jpx = jc.getContext('2d').getImageData(0, 0, im.width, im.height).data;
                // Even-fill-aware decode — the same scale-aware extractor the
                // By Sight path uses (codec.js). The old sequential-only
                // extractBits(...,3/2) was blind to high-res even-fill bars
                // and reported LOST even at q95.
                // fast=true: JPEG preserves dimensions, so the bar is at scale
                // 1.0 / anchor (0,0) exactly — the downscale scale-sweep and the
                // even-fill 25-combo phase sweep are pure waste here (~20× the
                // decode cost, the cause of the "analyzing…" stall on images
                // whose bar survives JPEG). Verdict is identical; only the
                // wasted RS decodes are dropped.
                var jres = extractBarScaleAware(jpx, im.width, im.height, true, true);
                var ok = !!jres;
                // Bar region preview (4x zoom on the bar's rows, wherever found)
                var bH = Math.min(4, im.height);
                var jRow = jres ? jres.bottomRow : barBottom;   // JPEG keeps dims; bar stays at barBottom
                var bc = document.createElement('canvas');
                bc.width = im.width; bc.height = bH * 4;
                var bx = bc.getContext('2d');
                bx.imageSmoothingEnabled = false;
                bx.drawImage(jc, 0, Math.max(0, jRow - bH + 1), im.width, bH, 0, 0, im.width, bH * 4);
                URL.revokeObjectURL(url);
                resolve({q:q, ok:ok, dataUrl:bc.toDataURL('image/png'), dims:[im.width, im.height], blobSize:blob.size});
              } catch (e) {
                try { URL.revokeObjectURL(url); } catch (e2) {}
                resolve({q:q, ok:false, dataUrl:null, dims:null, blobSize:blob.size});
              }
            };
            im.src = url;
          }, 'image/jpeg', q / 100);
        });
      }
      // Render placeholder rows; fill them in async.
      jpegLevels.forEach(function(q){
        o += '<div id="jpegRow-' + idx + '-' + q + '" style="padding:0.4rem 0.5rem;background:rgba(40,40,60,0.05);border-left:3px solid rgba(120,120,140,0.3);border-radius:4px;margin-bottom:0.3rem;">';
        o += '<div style="display:flex;align-items:center;gap:0.6rem;margin-bottom:0.3rem;">';
        o += '<span style="font-size:0.85rem;color:#c0c0d0;font-weight:700;">q' + q + '</span>';
        o += '<span style="font-size:0.65rem;color:#8a8a9a;">analyzing\u2026</span>';
        o += '</div></div>';
      });
      // Fill the rows once they're actually mounted (PanelSwap may defer the
      // insertion via a cross-fade — see _runWhenMounted).
      _runWhenMounted('jpegRow-' + idx + '-95', async function(){
        for (var qi = 0; qi < jpegLevels.length; qi++) {
          var r = await jpegOneLevel(jpegLevels[qi]);
          var row = document.getElementById('jpegRow-' + idx + '-' + r.q);
          if (!row) continue;
          var rowBg = r.ok ? 'rgba(74,158,74,0.08)' : 'rgba(180,60,60,0.06)';
          var rowBdr = r.ok ? 'rgba(74,158,74,0.5)' : 'rgba(180,60,60,0.4)';
          var html = '';
          html += '<div style="display:flex;align-items:center;gap:0.6rem;margin-bottom:0.3rem;">';
          html += '<span style="font-size:0.85rem;color:#c0c0d0;font-weight:700;">q' + r.q + '</span>';
          if (r.blobSize) {
            html += '<span style="font-size:0.65rem;color:#8a8a9a;">' + Math.round(r.blobSize / 1024) + ' KB</span>';
          }
          html += '<span style="font-size:0.65rem;padding:0.1rem 0.4rem;border-radius:3px;background:' + (r.ok ? 'rgba(74,158,74,0.15)' : 'rgba(180,60,60,0.15)') + ';color:' + (r.ok ? '#4ade80' : '#f87171') + ';font-weight:600;">Bar ' + (r.ok ? 'SURVIVED' : 'LOST') + '</span>';
          html += '</div>';
          if (r.dataUrl) {
            html += '<div style="font-size:0.55rem;color:#8a8a9a;margin-bottom:2px;">Bar region (post-JPEG q' + r.q + ')</div>';
            html += '<img src="' + r.dataUrl + '" class="bar-zoom" style="width:100%;image-rendering:pixelated;border-radius:3px;opacity:0.85;"/>';
          }
          row.style.background = rowBg;
          row.style.borderLeftColor = rowBdr;
          row.innerHTML = html;
        }
      });

        o+='</div>';   // close #barpanel-idx (this bar's diagnostic body)
        o+='</div>';   // close the bar panel wrapper
      });               // close barList.forEach — one full panel per detected bar

      // Image thumbnail (whole image, once)
      o+='<div class="ev-sec">Image</div>';
      o+='<img src="'+thumbUri+'" class="img-zoom" data-full="'+fullUri+'" style="width:100%;border-radius:6px;margin:0.3rem 0;"/>';

      // External source link intentionally omitted — the audit-link on
      // the identifier above handles "drill into this record" without
      // tying the UI to any specific platform. The Source row in the
      // Audit report shows where the record actually lives.

      o+='</div></div>';
      // Cross-fade if the panel is already showing another tab's result.
      PanelSwap(document.getElementById('resultsWrap'), function() {
        imgResults.insertAdjacentHTML('afterbegin', o);
        document.getElementById('certResults').style.display = 'none';
        imgResults.style.display = '';
        showResultsSidebar();
      });

      // Compact console in the left panel — thumbnail + clickable id + status.
      var imgCon = document.getElementById('imgConsole');
      imgCon.classList.remove('error-only');
      document.getElementById('imgConsoleThumb').src = thumbUri;
      if (decoded) {
        document.getElementById('imgConsoleId').innerHTML = '<a href="#" class="audit-link" data-id="' + escapeHtml(decoded.identifier) + '">' + escapeHtml(decoded.identifier) + '</a>';
        document.getElementById('imgConsoleHash').textContent = decoded.content_hash;
        var st = document.getElementById('imgConsoleStatus');
        st.className = 'img-console-status ok';
        st.textContent = 'Bar survived. Body and soul readable.';
      } else {
        document.getElementById('imgConsoleId').textContent = '—';
        document.getElementById('imgConsoleHash').textContent = '';
        var st2 = document.getElementById('imgConsoleStatus');
        st2.className = 'img-console-status fail';
        st2.textContent = 'Bands present but data unreadable.';
      }
      imgCon.classList.add('visible');
  }).catch(function(err) {
    // Surface any throw inside the .then body instead of swallowing
    // silently — that's what made drag-drop look "dead" when the
    // forensic code had a bug downstream.
    console.error('[analyze] failed:', err);
  });
}
// === Metadata UI ===
var jdz=document.getElementById('jsonDrop'),jInp=document.getElementById('jsonInput'),metaResults=document.getElementById('metaResults');
DropZone.attach({
  zone: jdz, input: jInp, multiple: true,
  onFiles: analyzeMeta
});
var folderBtn=document.getElementById('folderBtn'),folderInp=document.getElementById('folderInput');
folderBtn.addEventListener('click',function(){folderInp.click();});
folderInp.addEventListener('change',function(){
  var souls=Array.from(folderInp.files).filter(function(f){return f.name.endsWith('.soul')||f.name.endsWith('.json');});
  if(souls.length)analyzeMeta(souls);
  folderInp.value='';
});

// Persistent chunk collection (survives across multiple drops).
//
// Two flavors of stored chunk:
//   - indexed — chunks that carry {index, total}. Stored as
//               {[role]: {total, chunks: {idx: entry}}}.
//   - single  — pinned chunks with no index. Stored as {[role]: entry}.
//
// Everything keys off the chunk *role name* (the key inside record.chunks),
// never a hardcoded list — whatever a chain emits is collected and rendered.
var collected = { indexed: {}, single: {} };

// Per-role display metadata for the download buttons + filters — derived
// entirely from the role name: a title-cased label and a color key (the role
// name itself, hashed to a stable hue by getRoleColor). The download name and
// file type are decided from the bytes at download time, not here.
function roleMeta(role) {
  return {
    label: role.replace(/[-_]/g, ' ').replace(/\b\w/g, function(c){ return c.toUpperCase(); }),
    color: role,
  };
}

// Deterministic per-role color resolver: a hue hashed from the role/filter
// name, same name → same color across sessions, so a chain author can trust
// their layer's color to be stable. 'all' (the no-filter view) is white.
function getRoleColor(name) {
  if (name === 'all' || !name) return '255,255,255';
  // Deterministic hash → hue. Keeps S/L in a band that reads well on
  // both yang and yin backgrounds.
  var h = 0;
  for (var i = 0; i < name.length; i++) h = ((h * 31) + name.charCodeAt(i)) >>> 0;
  var hue = h % 360;
  var s = 0.45, l = 0.62;
  var c = (1 - Math.abs(2 * l - 1)) * s;
  var x = c * (1 - Math.abs((hue / 60) % 2 - 1));
  var m = l - c / 2;
  var r1, g1, b1;
  if (hue < 60)       { r1 = c; g1 = x; b1 = 0; }
  else if (hue < 120) { r1 = x; g1 = c; b1 = 0; }
  else if (hue < 180) { r1 = 0; g1 = c; b1 = x; }
  else if (hue < 240) { r1 = 0; g1 = x; b1 = c; }
  else if (hue < 300) { r1 = x; g1 = 0; b1 = c; }
  else                { r1 = c; g1 = 0; b1 = x; }
  return Math.round((r1 + m) * 255) + ',' + Math.round((g1 + m) * 255) + ',' + Math.round((b1 + m) * 255);
}

// Visibility helpers — soul stores int codes (0=light_energy public,
// 1=dark_matter sealed) but legacy records / programmatic callers may
// still pass strings. Accept both shapes.
function _isDark(v)  { return v === 1 || v === 'dark_matter'; }
function _isLight(v) { return v === 0 || v === 'light_energy' || v == null; }
function _visName(v) {
  if (v === 1 || v === 'dark_matter') return 'dark_matter';
  if (v === 0 || v === 'light_energy') return 'light_energy';
  return '';
}

// Group records by their chain identity. Two independently sealed
// chains can both name their first Age the same thing; without a
// discriminator they collide. Prefers decoder_hash when present;
// otherwise derives a stable signature from the record's layer layout
// (role names + totals).
function chainDiscriminator(r) {
  if (r.decoder_hash) return r.decoder_hash.slice(0, 12);
  var ch = r.chunks && typeof r.chunks === 'object' ? r.chunks : null;
  if (!ch) return '_no_chunks';
  return Object.keys(ch).sort().map(function(k) {
    var e = ch[k];
    var t = e && typeof e.total === 'number' ? e.total : '?';
    return k + ':' + t;
  }).join('|');
}

// Assign _ageKey / _ageName to a record. Called once for every parsed
// soul before row HTML is generated, so filter/grid lookups match.
function assignAgeKey(r) {
  var displayName = AgeNames.forRecord(r) || '_';
  r._ageName = displayName;
  r._ageKey = chainDiscriminator(r) + '#' + displayName;
}

function sortRoles(roles) {
  // Stable, deterministic ordering across drops: alphabetical by role name.
  return roles.slice().sort();
}

async function verifyChunkHash(data,expectedHash){
  if(!expectedHash||!data)return null;
  // _sha256_bytes (verify.js) gracefully falls back to a pure-JS
  // SHA-256 when crypto.subtle is unavailable (iOS Safari with
  // self-signed cert, file://, etc.).
  try{var view=await _sha256_bytes(new TextEncoder().encode(data));
    var hex=Array.from(view).map(function(b){return b.toString(16).padStart(2,'0');}).join('').slice(0,12);
    return hex===expectedHash;}catch(e){return null;}
}

async function gunzipBytes(base64){
  var bytes=Uint8Array.from(atob(base64),function(c){return c.charCodeAt(0);});
  var ds=new DecompressionStream('gzip');
  var writer=ds.writable.getWriter();writer.write(bytes);writer.close();
  var reader=ds.readable.getReader();var chunks=[];
  while(true){var r=await reader.read();if(r.done)break;chunks.push(r.value);}
  var total=chunks.reduce(function(a,c){return a+c.length;},0);
  var out=new Uint8Array(total);var off=0;
  for(var i=0;i<chunks.length;i++){out.set(chunks[i],off);off+=chunks[i].length;}
  return out;
}

async function gunzip(base64){
  return new TextDecoder().decode(await gunzipBytes(base64));
}

async function assembleChunks(store,count){
  // Defensive: a layer can declare total=0 (malformed or empty). Bail
  // before constructing an empty string and feeding it to gunzip.
  if (!count || count <= 0) return null;
  var parts=[];for(var i=0;i<count;i++){if(!store[i])return null;parts.push(store[i].data);}
  // Every layer's chunks are gzip+base64 encoded — concat → atob → gunzip.
  return await gunzip(parts.join(''));
}

// Binary-safe variant: returns Uint8Array. Use this for non-text layer
// payloads (images, PDFs, anything that isn't valid UTF-8) — gunzip()
// would TextDecoder-mangle bytes and bloat the result.
async function assembleChunksBytes(store, count) {
  var parts = [];
  for (var i = 0; i < count; i++) {
    if (!store[i]) return null;
    parts.push(store[i].data);
  }
  return await gunzipBytes(parts.join(''));
}

// Sniff file type from the first bytes so an unknown-role chain payload
// downloads with a sensible extension + mime. Falls back to .bin /
// application/octet-stream for genuinely opaque data.
function sniffBinaryType(bytes) {
  if (!bytes || bytes.length < 4) return null;
  var b0 = bytes[0], b1 = bytes[1], b2 = bytes[2], b3 = bytes[3];
  if (b0 === 0x89 && b1 === 0x50 && b2 === 0x4E && b3 === 0x47) return {mime: 'image/png',  ext: 'png'};
  if (b0 === 0xFF && b1 === 0xD8 && b2 === 0xFF)               return {mime: 'image/jpeg', ext: 'jpg'};
  if (b0 === 0x47 && b1 === 0x49 && b2 === 0x46 && b3 === 0x38) return {mime: 'image/gif',  ext: 'gif'};
  if (b0 === 0x25 && b1 === 0x50 && b2 === 0x44 && b3 === 0x46) return {mime: 'application/pdf', ext: 'pdf'};
  if (b0 === 0x52 && b1 === 0x49 && b2 === 0x46 && b3 === 0x46 &&
      bytes.length >= 12 && bytes[8] === 0x57 && bytes[9] === 0x45 &&
      bytes[10] === 0x42 && bytes[11] === 0x50)                 return {mime: 'image/webp', ext: 'webp'};
  // Text-likely (UTF-8 BOM, ASCII prefix that looks like text)
  if (b0 === 0xEF && b1 === 0xBB && b2 === 0xBF)               return {mime: 'text/plain', ext: 'txt'};
  // SVG / HTML / XML
  try {
    var head = new TextDecoder('utf-8', {fatal: false}).decode(bytes.slice(0, Math.min(128, bytes.length))).trim().toLowerCase();
    if (head.indexOf('<?xml') === 0 || head.indexOf('<svg') === 0) return {mime: 'image/svg+xml', ext: 'svg'};
    if (head.indexOf('<!doctype html') === 0 || head.indexOf('<html') === 0) return {mime: 'text/html', ext: 'html'};
  } catch (e) {}
  return null;
}

// Does this base64 string decode to a COMPLETE gzip member (magic 1f 8b)?
// A whole payload chunk (a file, gzip+base64) does; a fragment of a larger
// base64 string does not. This is how a role's chunks are classified with no
// role names: every chunk a complete gzip → they're separate files; otherwise
// they're fragments to concatenate and then decode.
function _isGzipB64(s) {
  if (!s || s.length < 4) return false;
  try {
    var head = atob(s.slice(0, 8));
    return head.charCodeAt(0) === 0x1f && head.charCodeAt(1) === 0x8b;
  } catch (e) { return false; }
}

function _saveBlob(bytes, name, mime) {
  var b = new Blob([bytes], {type: mime || 'application/octet-stream'});
  var a = document.createElement('a');
  a.href = URL.createObjectURL(b);
  a.download = name;
  a.click();
}

// Pick a download name for a blob: prefer a seal-recorded filename, else the
// role name + a sniffed extension (falling back to json/txt for text, bin for
// opaque bytes). idx (when set) suffixes multi-file downloads.
function _downloadName(bytes, storedName, role, idx) {
  if (storedName) return storedName;
  var sniffed = sniffBinaryType(bytes);
  var ext = sniffed && sniffed.ext;
  if (!ext) {
    try {
      var t = new TextDecoder('utf-8', {fatal: true}).decode(bytes.slice(0, 512)).trim();
      ext = (t.charAt(0) === '{' || t.charAt(0) === '[') ? 'json' : 'txt';
    } catch (e) { ext = 'bin'; }
  }
  return role + (idx != null ? '-' + (idx + 1) : '') + '.' + ext;
}

// Download one role's payload, deciding form entirely from the data:
//   - every chunk a complete gzip file  → separate downloads (one per chunk)
//   - a single complete gzip file       → one download
//   - fragments                         → concatenate, then gunzip if the join
//                                          is gzip, else treat as plain text
// chunkList is the chunks in index order (a single pinned role = one element).
async function downloadRoleChunks(role, chunkList) {
  var list = chunkList.filter(function(c) { return c && c.data; });
  if (!list.length) return;
  var separate = list.every(function(c) { return _isGzipB64(c.data); });
  if (separate && list.length > 1) {
    for (var i = 0; i < list.length; i++) {
      var b = await gunzipBytes(list[i].data);
      var sn = sniffBinaryType(b);
      _saveBlob(b, _downloadName(b, list[i].filename, role, i), (sn && sn.mime) || 'application/octet-stream');
    }
    return;
  }
  if (separate) {
    var b0 = await gunzipBytes(list[0].data);
    var s0 = sniffBinaryType(b0);
    _saveBlob(b0, _downloadName(b0, list[0].filename, role, null), (s0 && s0.mime) || 'application/octet-stream');
    return;
  }
  // Fragments — reassemble in order.
  var joined = list.map(function(c) { return c.data; }).join('');
  var storedName = '';
  for (var j = 0; j < list.length; j++) { if (list[j].filename) { storedName = list[j].filename; break; } }
  if (_isGzipB64(joined)) {
    var bytes = await gunzipBytes(joined);
    var sn2 = sniffBinaryType(bytes);
    _saveBlob(bytes, _downloadName(bytes, storedName, role, null), (sn2 && sn2.mime) || 'application/octet-stream');
  } else {
    var enc = new TextEncoder().encode(joined);
    _saveBlob(enc, _downloadName(enc, storedName, role, null), 'text/plain');
  }
}

// HASH_INCLUDED, sortKeysDeep, sha256_16 — loaded from js/verify.js

// Dark Matter unlock state.
//
// Parsed records get cached here so the user can enter a chain password
// after the initial drop — we re-run the render pipeline against the
// cached records with passwords applied, instead of asking them to re-drop.
//
// _chainPasswords is keyed by chainDiscriminator(record) so a mixed drop
// containing two independently-locked dark matter chains can be unlocked
// one at a time. Passwords are kept only in memory (closing the tab
// forgets them — mirrors decoder GPS unlock).
var _observatoryCache = [];
var _chainPasswords = {};

// Attempt to decrypt a dark_matter record using a stored chain password.
// Returns a new record with plaintext fields merged back, or the original
// if no password is stored or decryption fails. Does not mutate the
// caller's record — the cache stays the encrypted ciphertext shell.
// True when a record carries any encrypted envelope — a canonical dark-matter
// chain (encrypted_fields + encrypted_chunks) OR a raw core soul sealed with
// seal(password=…) (encrypted_fields, no chain). The unlock surface keys off
// THIS, not chain_visibility, so both unlock the same way.
function _isSealed(record) {
  return !!(record && (record.encrypted_fields || record.encrypted_chunks));
}

// Decrypt a record's envelopes with `pw`, returning a NEW merged record
// (plaintext fields in, the sealed shell kept under _sealedOriginal so the
// hash still runs over the as-stored ciphertext) — or null if nothing decrypts
// (wrong password, no Access, no envelopes). Never mutates the input.
async function _decryptRecord(record, pw) {
  if (!record || typeof Access === 'undefined' || !pw || !_isSealed(record)) return null;
  var unlocked = Object.assign({}, record);
  var anyOk = false;
  if (record.encrypted_fields) {
    var soulRes = await Access.decryptSoul(record.encrypted_fields, pw);
    if (soulRes.ok && soulRes.soul) {
      Object.keys(soulRes.soul).forEach(function(k) { unlocked[k] = soulRes.soul[k]; });
      anyOk = true;
    }
  }
  if (record.encrypted_chunks) {
    var chunksRes = await Access.decryptChunks(record.encrypted_chunks, pw);
    if (chunksRes.ok && chunksRes.chunks) {
      unlocked.chunks = chunksRes.chunks;
      anyOk = true;
    }
  }
  if (!anyOk) return null;
  unlocked._unlocked = true;
  // The WITNESSED/verdict hash must run over the record AS STORED — the stored
  // content_hash covers the sealed shell (ciphertext + public fields). We
  // merged plaintext for DISPLAY only; hashing the merged record would poison
  // it. _sealedShellFor() routes every hash recompute back to this original.
  unlocked._sealedOriginal = record;
  return unlocked;
}

async function maybeUnlockRecord(record) {
  // Fire for any sealed record — dark-matter chain OR a core seal(password=…)
  // soul — using a password stashed by chainDiscriminator (so a mixed drop
  // unlocks one chain at a time).
  if (!record || !(_isDark(record.chain_visibility) || _isSealed(record))) return record;
  var unlocked = await _decryptRecord(record, _chainPasswords[chainDiscriminator(record)]);
  return unlocked || record;
}

// The original sealed record to hash for verification. Returns the pre-merge
// shell stamped by maybeUnlockRecord when present, else the record itself
// (non-dark records, sealed-but-not-unlocked records, and Audit-tab records
// are already their own shell). Hashing the result reproduces the stored
// content_hash exactly as the decoder's computeContentHash does.
function _sealedShellFor(rec) { return (rec && rec._sealedOriginal) || rec; }

// Single source for the Dark Matter unlock control. The unlock is
// chain-level: entering the password in ANY sealed record stashes it under
// _chainPasswords[chainKey], and the re-render unlocks every record in that
// chain at once (the .dm-unlock-btn handler is keyed by chain, not record).
// idPrefix keeps the input/error element ids unique per call site.
function _dmUnlockHTML(chainKey, idPrefix) {
  var inId = idPrefix + '-pw', errId = inId + '-err';
  return '<div class="ev-sec">Unlock</div>'
    + '<div style="font-size:0.62rem;color:#8a8a9a;margin-bottom:0.3rem;">Sealed (Dark Matter). Enter the chain password to decrypt the soul + chunks locally and verify.</div>'
    + '<div style="display:flex;gap:0.3rem;align-items:center;">'
    + '<input id="' + inId + '" type="password" placeholder="Creator password" style="flex:1;background:#0a0a12;color:#c8c8d4;border:1px solid #2a2a40;border-radius:4px;padding:0.3rem 0.5rem;font-size:0.75rem;font-family:inherit;">'
    + '<button data-dm-chain="' + escapeHtml(chainKey) + '" data-dm-input="' + inId + '" data-dm-err="' + errId + '" class="dm-unlock-btn" style="padding:0.3rem 0.8rem;background:rgba(200,176,128,0.12);border:1px solid rgba(200,176,128,0.3);color:#c8b080;border-radius:4px;cursor:pointer;font-size:0.72rem;font-weight:600;letter-spacing:0.05em;">Unlock</button>'
    + '</div>'
    + '<div id="' + errId + '" style="font-size:0.6rem;color:#f87171;margin-top:0.25rem;min-height:0.7rem;"></div>';
}

async function analyzeMeta(files){
  // Render Observatory results in the sidebar, not inline
  clearOtherResults('meta');
  var metaSidebar = document.getElementById('metaSidebarResults');
  var metaErr = document.getElementById('metaError');
  if (metaErr) metaErr.textContent = '';
  metaResults.innerHTML='';
  // Defer opening the sidebar. Parsing the files might turn up zero
  // valid souls, in which case we show an inline error in the tab and
  // the sidebar should stay closed. Show a transient "Analyzing..."
  // note inside the tab's error slot — it flips to the error text or
  // is cleared when valid records render.
  if (metaErr) { metaErr.textContent = 'Analyzing ' + files.length + ' file(s)\u2026'; metaErr.style.color = '#8a8a94'; }
  if (metaSidebar) metaSidebar.innerHTML = '';
  var recs=[];
  // Normalize display filename: strip trailing .json → .soul. IA mirrors
  // store records as .json for CORS; from the user's perspective the
  // file *is* a .soul, so the record list should say so.
  function toSoulName(name){ return name.replace(/\.json$/, '.soul'); }
  for(var i=0;i<files.length;i++){try{var t=await files[i].text();var m=JSON.parse(t);m._fn=toSoulName(files[i].name);recs.push(m);
    // Also stash in the offline cache so Audit can resolve the
    // identifier locally without a network trip.
    if (m && m.identifier) OfflineRecords.add(m, m._fn);
  }catch(e){recs.push({_fn:toSoulName(files[i].name),_err:e.message});}}
  // Cache for re-render after Dark Matter unlock.
  _observatoryCache = recs;
  _chainPasswords = {};
  await _renderObservatoryFromCache();
}

// Re-render the Observatory from the cached parse, applying any chain
// passwords entered since the initial drop. Split out so the "Unlock"
// button handler can call it without re-reading files.
async function _renderObservatoryFromCache() {
  var recs = _observatoryCache;
  var metaSidebar = document.getElementById('metaSidebarResults');
  var metaErr = document.getElementById('metaError');

  var valid=recs.filter(function(r){return !r._err;});
  // Error rule: if nothing parsed, show the failure inline and leave
  // the sidebar closed (we never opened it — deferred until valid).
  if (valid.length === 0) {
    if (metaSidebar) metaSidebar.innerHTML = '';
    if (metaErr) {
      var firstErr = recs.length ? recs[0]._err : 'unknown';
      metaErr.textContent = recs.length > 1
        ? 'No valid soul files. ' + recs.length + ' files failed to parse.'
        : 'Not a valid soul file: ' + firstErr;
      metaErr.style.color = '';  // revert to .tab-error's red
    }
    return;
  }
  // At least one valid parse — now we can open the sidebar and clear
  // the transient "Analyzing..." note from the error slot.
  if (metaErr) { metaErr.textContent = ''; metaErr.style.color = ''; }
  showResultsSidebar();
  // De-duplicate by identifier so dropping the same soul twice doesn't
  // double-count chunks or double-render rows. Keep first occurrence.
  var _seenIds = {};
  valid = valid.filter(function(r) {
    var id = r.identifier;
    if (!id) return true; // no identifier — pass through (we can't dedupe what we can't key)
    if (_seenIds[id]) return false;
    _seenIds[id] = true;
    return true;
  });
  // Stamp every record with its (chain, age) key BEFORE attempting
  // unlock — maybeUnlockRecord keys passwords by chainDiscriminator,
  // which assignAgeKey computes once and caches on the record.
  valid.forEach(assignAgeKey);

  // Apply any chain passwords entered since the initial drop. For each
  // dark_matter record whose chain has a stored password we replace the
  // entry in `valid` with a copy that has plaintext fields merged back
  // (encrypted_fields → soul fields; encrypted_chunks → chunks dict).
  // _observatoryCache stays the original ciphertext shell so a wrong
  // password can be retried without re-dropping.
  for (var ui = 0; ui < valid.length; ui++) {
    valid[ui] = await maybeUnlockRecord(valid[ui]);
  }

  // Propagate age_name across each chain. Frozen-tail and pinned-only
  // records carry no decoder chunk and therefore no
  // age_name in assignAgeKey's first pass — they'd otherwise form
  // their own untitled tab and sort ahead of the named records.
  // Adopt the age_name from any chain peer that has one so the whole
  // chain renders as a single Age.
  var _chainAgeNames = {};
  valid.forEach(function(r) {
    if (r._ageName && r._ageName !== '_') {
      var ck = chainDiscriminator(r);
      _chainAgeNames[ck] = r._ageName;
    }
  });
  valid.forEach(function(r) {
    if (r._ageName === '_') {
      var ck = chainDiscriminator(r);
      if (_chainAgeNames[ck]) {
        r._ageName = _chainAgeNames[ck];
        r._ageKey = ck + '#' + r._ageName;
      }
    }
  });

  valid.sort(function(a,b){
    // Group by chain first (preserves multi-chain isolation in mixed drops).
    var ack = a._ageKey ? a._ageKey.split('#')[0] : '';
    var bck = b._ageKey ? b._ageKey.split('#')[0] : '';
    if (ack !== bck) return ack < bck ? -1 : 1;
    // Within a chain: outer_position is the canonical record order.
    // Universal across cycling (with decoder) and pinned-only records,
    // so the frozen-tail positions render between the last cycling
    // position and the next age, not ahead of position 0.
    var ap = (a.outer_position != null) ? a.outer_position : Infinity;
    var bp = (b.outer_position != null) ? b.outer_position : Infinity;
    if (ap !== bp) return ap - bp;
    // Last-resort fallback for records without outer_position: truth
    // or decoder chunk index, then identifier.
    var at=getChunk(a,'truth')||{}, bt=getChunk(b,'truth')||{};
    var ai=at.index!=null?at.index:0, bi=bt.index!=null?bt.index:0;
    if (ai !== bi) return ai - bi;
    return (a.identifier || '') < (b.identifier || '') ? -1 : 1;
  });

  // Compute hashes for all valid records
  for(var vi=0;vi<valid.length;vi++){
    var r=valid[vi];var _shellA=_sealedShellFor(r);var stored=r.content_hash||null;
    var _setA=_hashSetForRecord(_shellA);
    var hashable={};Object.keys(_shellA).filter(function(k){return _setA.has(k);}).sort().forEach(function(k){hashable[k]=_shellA[k];});
    try {
      r._computed = await sha256_16(hashable);
    } catch (e) {
      r._computed = null;
      // Surface the actual reason — most often "crypto.subtle is
      // undefined" because the validator was loaded in an insecure
      // context (file:// or http:// on a non-localhost host). Show
      // it to the user instead of the bare "unavailable" verdict.
      r._computed_error = (e && e.message) || String(e);
      if (typeof crypto === 'undefined' || !crypto.subtle) {
        r._computed_error = 'crypto.subtle unavailable — '
          + 'the validator must be loaded over HTTPS (or localhost). '
          + 'You opened it via ' + location.protocol + '//' + location.hostname
          + ' which doesn\u2019t qualify as a secure context for the Web Crypto API.';
      }
      console.warn('[validator] hash compute failed:', e);
    }
    r._match=stored&&r._computed&&stored===r._computed;
    // Sealed = dark_matter record where soul/chunks ciphertext is still
    // present and we haven't unlocked it. A plain hash mismatch on a
    // sealed record is expected (the stored hash covers plaintext we
    // can't see) — flag it separately so the UI can distinguish
    // "we can't tell" from "we verified tampering".
    r._sealed = _isDark(r.chain_visibility)
                && !r._unlocked
                && (!!r.encrypted_fields || !!r.encrypted_chunks);
  }

  // Accumulate chunks from these records — generic walk over each record's
  // chunks dict. Indexed chunks (have index+total) go into collected.indexed,
  // single pinned chunks go into collected.single. The role name is whatever
  // key the chain used inside record.chunks — no names are assumed.
  for (var ci = 0; ci < valid.length; ci++) {
    var cr = valid[ci];
    var chDict = cr.chunks && typeof cr.chunks === 'object' ? cr.chunks : null;
    // Iterate the nested chunks dict if present.
    if (chDict) {
      Object.keys(chDict).forEach(function(role) {
        var entry = chDict[role];
        if (!entry || entry.data === undefined) return;
        if (entry.index !== undefined && entry.total !== undefined) {
          if (!collected.indexed[role]) {
            collected.indexed[role] = { total: entry.total, chunks: {} };
          }
          // Trust the newest record's total in case earlier was stale.
          collected.indexed[role].total = entry.total;
          collected.indexed[role].chunks[entry.index] = {
            data: entry.data, hash: entry.hash || null, verified: null,
            // Carry the original filename through (single-file payload layers
            // stamp it on every chunk) so reassembly restores it instead of
            // <role>.bin. Dropping it here was why the download stayed .bin.
            filename: entry.filename || null,
          };
        } else {
          collected.single[role] = {
            data: entry.data, hash: entry.hash || null,
            text: entry.text || null, image: entry.image || null,
            verified: null,
          };
        }
      });
    }
  }
  // Verify chunk hashes asynchronously. Walk every collected role.
  for (var ir in collected.indexed) {
    var store = collected.indexed[ir].chunks;
    for (var idx in store) {
      var c = store[idx];
      if (c.hash && c.verified === null) c.verified = await verifyChunkHash(c.data, c.hash);
    }
  }
  for (var sr in collected.single) {
    var sg = collected.single[sr];
    if (sg.hash && sg.verified === null) sg.verified = await verifyChunkHash(sg.data, sg.hash);
  }

  var html='';

  _gpsRecords=valid;

  // === Orbit Inspector placeholder (built after innerHTML set) ===
  // The orbit inspector lives OUTSIDE the scrollable body so the
  // carousel + grid + filter row stay pinned at the top of the panel
  // and only the records + chain panels beneath scroll. CSS turns
  // metaSidebarResults into a flex column when the inspector is
  // populated.
  html+='<div id="orbitInspector"></div>';
  html+='<div class="meta-body">';

  // === Compact record table — one row per record, click to expand ===
  html+='<div class="ev"><div class="ev-h" style="background:rgba(80,80,100,0.08);border-left:3px solid rgba(80,80,100,0.3);"><span class="ev-t">Records ('+recs.length+')</span><span style="font-size:0.6rem;color:#8a8a9a;">click row to expand</span></div><div class="ev-body" style="padding:0;">';

  // Error rows first
  for(var ei=0;ei<recs.length;ei++){var er=recs[ei];if(!er._err)continue;
    html+='<div style="padding:0.4rem 0.8rem;border-bottom:1px solid #1a1a2a;"><span style="color:#f87171;font-size:0.75rem;">'+escapeHtml(er._fn)+' \u2014 '+escapeHtml(er._err)+'</span></div>';}

  // Valid record rows
  for(var ri=0;ri<valid.length;ri++){
    var r=valid[ri];
    var rBadgeCol=r._sealed?'#c8b080':r._match?'#4ade80':r.content_hash?'#f87171':'#4a4a60';
    // Sealed records get a small lock glyph (U+1F512 + VS-15 to coax
    // text-style rendering on platforms that default to color emoji),
    // keeping the badge column visually homogeneous with ✓ / ✗ / —.
    var rBadge=r._sealed?'\uD83D\uDD12\uFE0E':r._match?'\u2713':r.content_hash?'\u2717':'\u2014';
    // Split the record's chunks by structure, no role names: INDEXED chunks
    // (have index/total) are cycling layers — one row each; SINGLE chunks
    // (no index) are pinned content — their own rows. Labels come from
    // roleMeta (title-cased from the role name).
    var rLayers = [];
    var rSingles = [];
    if (r.chunks && typeof r.chunks === 'object') {
      Object.keys(r.chunks).sort().forEach(function(role) {
        var e = r.chunks[role];
        if (!e || typeof e !== 'object') return;
        if (e.index !== undefined && e.total !== undefined) rLayers.push({role: role, entry: e});
        else if (e.data !== undefined) rSingles.push({role: role, entry: e});
      });
    }
    // Grid anchor + compact index: the record's outer position places it on
    // the grid; the compact label lists each layer's index. First layer's
    // index is the fallback anchor for records without an outer_position.
    var di_ = rLayers.length ? rLayers[0].entry.index : null;
    var _outerPos = (r.outer_position != null) ? r.outer_position : di_;
    var ageName = AgeNames.forRecord(r) || '';
    var hasPinned = rSingles.length > 0;

    // Compact row — white labels, green only for verified badge
    // Records can come from user-dropped .soul files in the Observatory
    // tab — every field is attacker-controllable. Escape on every
    // interpolation. _h is a local alias for portal.js's escapeHtml.
    var _h = escapeHtml;
    html+='<div id="rec-'+(_outerPos!=null?_outerPos:ri)+'" data-identifier="'+_h(r.identifier||'')+'" data-age="'+_h(ageName)+'" data-age-key="'+_h(r._ageKey||'')+'" data-con="'+_h(r.constellation_name||'')+'" data-chunk="'+(di_!=null?+di_:'')+'" style="border-bottom:1px solid #1a1a2a;">';
    html+='<div class="meta-row" onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display===\'none\'?\'block\':\'none\';" style="padding:0.35rem 0.8rem;cursor:pointer;display:flex;align-items:center;gap:0.5rem;transition:background 0.1s;" onmouseover="this.style.background=\'rgba(255,255,255,0.03)\'" onmouseout="this.style.background=\'none\'">';
    html+='<span style="font-size:0.72rem;color:'+rBadgeCol+';min-width:1rem;">'+rBadge+'</span>';
    html+='<span style="font-size:0.7rem;font-family:monospace;min-width:10rem;color:#d0d0d8;">'+_h(r.identifier?r.identifier.slice(-16):(r._fn||'').slice(-16))+'</span>';
    // Compact position label: one token per cycling layer — the first letter
    // of the layer's role name + its chunk index (e.g. a "truth" layer at
    // index 5 → "T5"), joined for a scannable per-record fingerprint.
    var _posLabel = rLayers.map(function(l) {
      return (l.role.charAt(0).toUpperCase() || '?') + l.entry.index;
    }).join(' ');
    if (_posLabel) html+='<span style="font-size:0.6rem;color:'+(hasPinned?'#8a8a9a':'#6a6a80')+';">'+_h(_posLabel)+'</span>';
    if(r.constellation_name)html+='<span style="font-size:0.58rem;color:#8a8a9a;margin-left:auto;">'+_h(r.constellation_name)+'</span>';
    html+='</div>';

    // Expandable detail (hidden by default)
    html+='<div class="meta-detail" style="display:none;padding:0.5rem 0.8rem;background:rgba(24,24,28,0.6);">';

    var _shellB=_sealedShellFor(r);
    var stored=r.content_hash||null;
    var _setB=_hashSetForRecord(_shellB);
    var hashable={};Object.keys(_shellB).filter(function(k){return _setB.has(k);}).sort().forEach(function(k){hashable[k]=_shellB[k];});
    var computed=null;try{computed=await sha256_16(hashable);}catch(e){}
    var match=stored&&computed&&stored===computed;
    var sealed=r._sealed;
    // Sealed wins over hash state: a dark_matter shell never matches the
    // plaintext hash, so the "may be modified" verdict would be both
    // technically correct and badly misleading. Sealed gets its own
    // class + badge; the hash state is re-evaluated post-unlock.
    //
    // computed-failed case (e.g. crypto.subtle unavailable on
    // insecure-context loads) gets its own badge so the badge column
    // doesn't say "Hash Mismatch" while the verdict explains
    // "Cannot verify" — those two used to disagree visually.
    var computeFailed = stored && !computed;
    var cls = sealed ? 'sealed'
            : match ? 'both'
            : computeFailed ? 'bar-only'
            : stored ? 'lost'
            : 'bar-only';
    var badge = sealed ? 'Sealed'
              : match ? 'Verified'
              : computeFailed ? 'Cannot verify'
              : stored ? 'Hash Mismatch'
              : 'No Hash';

    html+='<div class="ev"><div class="ev-h '+cls+'"><span class="ev-t">'+_h(r._fn)+'</span><span class="ev-b '+cls+'">'+badge+'</span></div><div class="ev-body">';

    // Identity
    html+='<div class="ev-sec">Identity</div><div class="ev-g">';
    var safeRid = _h(r.identifier||'');
    html+='<div class="ev-m"><div class="ev-ml">Identifier</div><div class="ev-mv">'+(r.identifier?'<a href="#" class="audit-link" data-id="'+safeRid+'" style="color:inherit;text-decoration:underline;text-decoration-color:rgba(255,255,255,0.2);cursor:pointer;">'+safeRid+'</a>':'\u2014')+'</div></div>';
    html+='<div class="ev-m"><div class="ev-ml">Conceived</div><div class="ev-mv">'+_h(r.conceived||r.timestamp||'\u2014')+'</div></div>';
    var safeParent = _h(r.parent_id||'');
    var parentCell = r.parent_id
      ? '<a href="#" class="audit-link" data-id="'+safeParent+'" style="color:inherit;text-decoration:underline;text-decoration-color:rgba(255,255,255,0.2);cursor:pointer;">'+safeParent+'</a>'
      : 'none (genesis)';
    html+='<div class="ev-m"><div class="ev-ml">Parent</div><div class="ev-mv">'+parentCell+'</div></div>';
    if(r.chain_visibility!==undefined&&r.chain_visibility!==null)html+='<div class="ev-m"><div class="ev-ml">Chain</div><div class="ev-mv" style="color:'+(_isDark(r.chain_visibility)?'#8080a0':'#d4b87b')+';">'+(_isDark(r.chain_visibility)?'Dark Matter (private)':'Light Energy (public)')+'</div></div>';
    var _rPrompt = (r.origin && r.origin.prompt) || r.prompt;   // V1 reads from origin
    if(_rPrompt)html+='<div class="ev-m w"><div class="ev-ml">Prompt</div><div class="ev-mv" style="font-style:italic;font-size:0.72rem;word-break:break-word;">'+_h(_rPrompt)+'</div></div>';
    html+='</div>';

    // Hash verification
    html+='<div class="ev-sec">Content Hash</div><div class="ev-g">';
    // When sealed, neutralize the red Stored/Computed coloring — the
    // mismatch is expected, not evidence of tampering.
    var hashCellCls=sealed?'sealed':match?'pass':'fail';
    html+='<div class="ev-m"><div class="ev-ml">Stored</div><div class="ev-mv '+(sealed?'sealed':match?'pass':stored?'fail':'')+'">'+_h(stored||'none')+'</div></div>';
    html+='<div class="ev-m"><div class="ev-ml">Computed</div><div class="ev-mv '+(sealed?'sealed':match?'pass':computed?'fail':'')+'">'+_h(computed||'unavailable')+'</div></div>';
    var verdictText = sealed
      ? 'SEALED \u2014 unlock to verify'
      : match
        ? 'Untampered \u2014 hashes match'
        : stored && computed
          ? 'MISMATCH \u2014 record may be modified'
          : 'Cannot verify' + (r._computed_error ? ' (' + r._computed_error + ')' : '');
    html+='<div class="ev-m w"><div class="ev-ml">Verdict</div><div class="ev-mv '+(sealed?'sealed':match?'pass':'fail')+'">'+_h(verdictText)+'</div></div>';
    html+='</div>';

    // Dark Matter unlock — inline in the detail card, directly under the
    // SEALED verdict so "unlock to verify" has somewhere to act. The unlock
    // is chain-level: the .dm-unlock-btn handler keys the password by
    // chainDiscriminator into _chainPasswords and re-renders, so entering it
    // in ANY sealed record of a chain unlocks every record in that chain.
    if(sealed && !r._unlocked){
      html+=_dmUnlockHTML(chainDiscriminator(r), 'dm-card-'+ri);
    }

    // Field audit
    html+='<div class="ev-sec">Field Audit</div>';
    html+='<div style="display:flex;flex-wrap:wrap;gap:3px;margin:0.3rem 0;">';
    var allK=Object.keys(r).filter(function(k){return k[0]!=='_';}).sort();
    for(var ki=0;ki<allK.length;ki++){var k=allK[ki];var inH=HASH_INCLUDED.has(k);html+='<span style="font-size:0.58rem;padding:0.1rem 0.3rem;border-radius:3px;background:'+(inH?'rgba(255,255,255,0.06)':'rgba(255,255,255,0.02)')+';color:'+(inH?'#a0a0a8':'#505058')+';font-family:monospace;">'+_h(k)+'</span>';}
    html+='</div><div style="font-size:0.6rem;color:#8a8a9a;">'+Object.keys(hashable).length+' hashed, '+(allK.length-Object.keys(hashable).length)+' excluded</div>';

    // Generation Parameters
    var genF=[['seed','Seed'],['width','Width'],['height','Height'],['steps','Steps'],['cfg','CFG'],['guidance','Guidance'],['denoise','Denoise'],['sampler','Sampler'],['scheduler','Scheduler'],['unet','Model'],['mode','Mode']];
    var hasGen=genF.some(function(g){return r[g[0]]!==undefined;});
    if(hasGen){
      html+='<div class="ev-sec">Generation Parameters</div><div class="ev-g">';
      for(var gi=0;gi<genF.length;gi++){if(r[genF[gi][0]]!==undefined)html+='<div class="ev-m"><div class="ev-ml">'+genF[gi][1]+'</div><div class="ev-mv">'+_h(r[genF[gi][0]])+'</div></div>';}
      if(r.lora)html+='<div class="ev-m"><div class="ev-ml">LoRA</div><div class="ev-mv">'+_h(r.lora)+(r.lora_strength!==undefined?' ('+_h(r.lora_strength)+')':'')+'</div></div>';
      html+='</div>';
    }

    // Cycle position — one row per cycling layer (index / total), then any
    // pinned single-chunk roles, plus Age / decoder_hash / constellation.
    // Every label comes from roleMeta (title-cased from the role name), so a
    // chain with any layer names renders the same way.
    if(rLayers.length || rSingles.length){
      html+='<div class="ev-sec">Cycle Position</div><div class="ev-g">';
      rLayers.forEach(function(l) {
        var lbl = roleMeta(l.role).label;
        var total = l.entry.total || '?';
        html+='<div class="ev-m"><div class="ev-ml">'+_h(lbl)+'</div><div class="ev-mv">'+_h(l.entry.index)+' / '+_h(total)+'</div></div>';
        if (l.entry.version) {
          html+='<div class="ev-m"><div class="ev-ml">'+_h(lbl)+' Version</div><div class="ev-mv" style="font-size:0.68rem;">'+_h(l.entry.version)+'</div></div>';
        }
      });
      var _ageN=AgeNames.forRecord(r); if(_ageN)html+='<div class="ev-m"><div class="ev-ml">Age</div><div class="ev-mv">'+_h(_ageN)+'</div></div>';
      if(r.decoder_hash)html+='<div class="ev-m"><div class="ev-ml">Decoder Hash</div><div class="ev-mv" style="font-size:0.68rem;">'+_h(r.decoder_hash)+'</div></div>';
      if(r.constellation_name)html+='<div class="ev-m"><div class="ev-ml">Constellation</div><div class="ev-mv">'+_h(r.constellation_name)+'</div></div>';
      if(r.heart_star_id){
        var safeHS=_h(r.heart_star_id);
        var hsCell=r.heart_star_id===r.identifier
          ? safeHS
          : '<a href="#" class="audit-link" data-id="'+safeHS+'" style="color:inherit;text-decoration:underline;text-decoration-color:rgba(255,255,255,0.2);cursor:pointer;">'+safeHS+'</a>';
        html+='<div class="ev-m"><div class="ev-ml">Heart Star</div><div class="ev-mv" style="font-size:0.68rem;">'+hsCell+'</div></div>';
      }
      rSingles.forEach(function(s) {
        var _pos = (s.entry.position != null) ? s.entry.position : (r.outer_position != null ? r.outer_position : '—');
        html+='<div class="ev-m"><div class="ev-ml">'+_h(roleMeta(s.role).label)+'</div><div class="ev-mv" style="color:rgb('+getRoleColor(s.role)+');">pinned @ '+_h(_pos)+'</div></div>';
      });
      html+='</div>';
    }

    // Birth Certificate — Celestial
    if(r.birth){
      var birth=r.birth;
      var bodies=['sun','moon','mercury','venus','mars','jupiter','saturn'];
      var bNames={sun:'Sun',moon:'Moon',mercury:'Mercury',venus:'Venus',mars:'Mars',jupiter:'Jupiter',saturn:'Saturn'};
      var hasCelestial=bodies.some(function(b){return !!birth[b];});
      if(hasCelestial){
        html+='<div class="ev-sec">Celestial State at Birth</div><div class="ev-g">';
        for(var ci2=0;ci2<bodies.length;ci2++){var cb=bodies[ci2];if(birth[cb]){var extra=cb==='moon'&&birth.moon_phase?' ('+_h(formatMoonPhase(birth.moon_phase))+')':'';html+='<div class="ev-m"><div class="ev-ml">'+bNames[cb]+'</div><div class="ev-mv">'+_h(formatPosition(birth[cb]))+extra+'</div></div>';}}
        if(birth.angular_spread)html+='<div class="ev-m"><div class="ev-ml">Angular Spread</div><div class="ev-mv">'+_h(birth.angular_spread)+'\u00b0</div></div>';
        if(r.constellation_hash)html+='<div class="ev-m"><div class="ev-ml">Constellation Hash</div><div class="ev-mv" style="font-size:0.68rem;">'+_h(r.constellation_hash)+'</div></div>';
        html+='</div>';
      }

      // Machine State
      if(birth.machine){
        var m=birth.machine;
        html+='<div class="ev-sec">Machine State at Birth</div><div class="ev-g">';
        var mF=[
          ['cpu','CPU',null],
          ['cores','Cores',formatCores],
          ['gpu','GPU',null],
          ['ram','RAM',formatRam],
          ['mem_active','Active',formatBytes],
          ['mem_compressed','Compressed',formatBytes],
          ['mem_free','Free',formatBytes],
          ['load','Load',formatLoad],
          ['power','Power',formatPower],
          ['disk_io','Disk I/O',formatDiskIO],
          ['net_rx','Net \u2193',formatBytes],
          ['net_tx','Net \u2191',formatBytes],
          ['uptime_seconds','Uptime',formatUptime],
          ['page_faults','Page Faults',formatPageFaults],
          ['ctx_switches','Ctx Switches',formatCtxSwitches]
        ];
        for(var mi=0;mi<mF.length;mi++){
          var mk=mF[mi][0], ml=mF[mi][1], mfmt=mF[mi][2];
          var mv=m[mk];
          if(mv===undefined||mv===null) continue;
          var disp = mfmt ? mfmt(mv) : ''+mv;
          if(disp==='') continue;
          html+='<div class="ev-m"><div class="ev-ml">'+ml+'</div><div class="ev-mv" style="font-size:0.7rem;">'+_h(disp)+'</div></div>';
        }
        html+='</div>';
        if(m.entropy){html+='<div class="ev-sec">Kernel Entropy</div><div class="ev-m w" style="margin:0.3rem 0;"><div class="ev-mv" style="font-size:0.55rem;word-break:break-all;line-height:1.5;color:#8898b8;">'+_h(m.entropy)+'</div></div>';}
      }

      // GPS Time-Lock — present only when the chain captured GPS.
      // Chains with gps_source: none publish records without
      // gps_time_locked; show an honest placeholder rather than skip.
      if(r.gps_time_locked){
        var gps=r.gps_time_locked;
        html+='<div class="ev-sec">Birthplace \u2014 Time-Locked</div><div class="ev-g">';
        if(gps.ct)html+='<div class="ev-m w"><div class="ev-ml">Ciphertext</div><div class="ev-mv" style="font-size:0.52rem;word-break:break-all;">'+_h(gps.ct)+'</div></div>';
        if(gps.N)html+='<div class="ev-m w"><div class="ev-ml">RSA Modulus N</div><div class="ev-mv" style="font-size:0.52rem;word-break:break-all;">'+_h(gps.N)+'</div></div>';
        if(gps.T)html+='<div class="ev-m"><div class="ev-ml">Squarings</div><div class="ev-mv">'+_h(typeof gps.T==='number'?gps.T.toLocaleString():gps.T)+'</div></div>';
        if(gps.e)html+='<div class="ev-m"><div class="ev-ml">RSA e</div><div class="ev-mv">'+_h(gps.e)+'</div></div>';
        html+='</div>';
      } else {
        html+='<div class="ev-sec">Birthplace \u2014 Not Recorded</div>';
        html+='<div class="ev-m w" style="margin:0.3rem 0;"><div class="ev-mv" style="font-size:0.65rem;color:#8090a0;font-style:italic;line-height:1.5;">No GPS captured at conception \u2014 this chain omits location.</div></div>';
      }

      // GPS Password Unlock
      if(r.gps_password_locked){
        html+='<div class="ev-sec">GPS \u2014 Password Unlock</div>';
        html+='<div style="display:flex;gap:0.5rem;align-items:center;">';
        html+='<input type="password" class="gps-pw-input" id="gps-pw-'+ri+'" placeholder="Creator password" style="flex:1;background:#0a0a12;color:#c8c8d4;border:1px solid #2a2a40;border-radius:4px;padding:0.3rem 0.5rem;font-size:0.75rem;font-family:inherit;">';
        html+='<button onclick="unlockGPS('+ri+')" style="padding:0.3rem 0.8rem;background:rgba(46,196,160,0.1);border:1px solid rgba(46,196,160,0.25);color:#2ec4a0;border-radius:4px;cursor:pointer;font-size:0.72rem;font-weight:600;">Unlock</button>';
        html+='</div>';
        html+='<div id="gps-result-'+ri+'" style="margin-top:0.3rem;"></div>';
      }
    }

    // Rarity — V1 derives the score from the dice dict.
    var _rs = (typeof RarityScore !== 'undefined')
      ? RarityScore.fromRecord(r) : (r.rarity_score || 0);
    if(r.rarity || r.rarity_score!==undefined){
      html+='<div class="ev-sec">Rarity</div><div class="ev-g">';
      var rs=_rs;var rTier=rs>=88?'Legendary':rs>=72?'Epic':rs>=55?'Very Rare':rs>=40?'Rare':rs>=25?'Uncommon':'Common';
      var rCol=rs>=88?'#f87171':rs>=72?'#facc15':rs>=55?'#c084fc':rs>=40?'#60a5fa':rs>=25?'#4ade80':'#a0a0a0';
      html+='<div class="ev-m"><div class="ev-ml">Score</div><div class="ev-mv" style="color:'+rCol+';font-weight:700;">'+rs+' \u2014 '+rTier+'</div></div>';
      if(r.machine_fingerprint)html+='<div class="ev-m"><div class="ev-ml">Fingerprint</div><div class="ev-mv">'+_h(r.machine_fingerprint)+'</div></div>';
      if(r.rarity&&typeof r.rarity==='object'){for(var rd of['celestial','machine','entropy']){var rT=r.rarity[rd];if(rT&&rT.length)html+='<div class="ev-m"><div class="ev-ml">'+rd.charAt(0).toUpperCase()+rd.slice(1)+'</div><div class="ev-mv" style="font-size:0.7rem;">'+_h(rT.map(function(t){return t.trait+' (+'+t.points+')';}).join(', '))+'</div></div>';}}
      html+='</div>';
    }

    // Birth Temperament + Trait Medals
    // V1 records carry only birth_traits (codes); readings/summary/
    // temperament are reconstructed from birth-text.js. Fall back to
    // any persisted strings on V4-era records.
    var rBirth = (typeof BirthText !== 'undefined' && r.birth_traits)
      ? BirthText.read(r.birth_traits) : null;
    var rTemp = (rBirth && rBirth.temperament) || r.birth_temperament;
    var rSummary = (rBirth && rBirth.summary) || r.birth_summary;
    if(rTemp){
      html+='<div class="ev-sec">Birth Temperament</div><div class="ev-g">';
      var hasMedals=r.birth_traits&&r.birth_traits.length&&typeof BIRTH_TRAITS!=='undefined';
      html+='<div class="ev-m w"><div class="ev-ml">'+_h(rTemp)+'</div>'+(!hasMedals&&rSummary?'<div class="ev-mv" style="font-style:italic;font-size:0.72rem;">'+_h(rSummary)+'</div>':'')+'</div>';
      if(hasMedals){
        html+='<div class="ev-m w" style="padding:0.5rem;">';
        for(var bti=0;bti<r.birth_traits.length;bti++){
          var btName=(typeof BirthText!=='undefined')?BirthText.name(r.birth_traits[bti]):null;
          var btDef=btName?BIRTH_TRAITS[btName]:null;
          if(btDef&&btName){
            html+='<div style="display:flex;align-items:center;gap:0.5rem;margin:0.25rem 0;">';
            // btName is resolved from the trait code via the trusted
            // BIRTH_TRAITS table; values are constants. Escape on
            // principle in case the table grows.
            html+='<img src="'+assetUrl('img/traits/'+btName+'.png')+'" style="width:24px;height:24px;object-fit:contain;flex-shrink:0;" alt="'+_h(btDef.name)+'">';
            html+='<span style="font-size:0.72rem;color:#c0c0cc;"><strong style="color:#d0d0d8;">'+_h(btDef.name)+'</strong> \u2014 '+_h(btDef.desc)+'</span>';
            html+='</div>';
          }else{
            html+='<div style="font-size:0.72rem;color:#8a8a94;margin:0.25rem 0;">'+_h(btName?btName.replace(/_/g,' '):'trait #'+r.birth_traits[bti])+'</div>';
          }
        }
        html+='</div>';
      }
      html+='</div>';
    }

    html+='</div></div>'; // close ev-body + ev (evidence card)
    html+='</div>'; // close meta-detail
    html+='</div>'; // close record row wrapper
  }
  html+='</div></div>'; // close ev-body + ev card

  // === Chain & Constellation — one panel per chain ===
  // For mixed-chain drops, each chain renders independently so its
  // Parent Chain links don't get conflated with other chains' records
  // and each chain's Constellations / Age can be read in isolation.
  // Single-chain drops still render one panel titled "Chain & Constellation".
  if(valid.length>1){
    var chainGroups = {};
    var chainGroupOrder = [];
    valid.forEach(function(r) {
      var ck = r._ageKey ? r._ageKey.split('#')[0] : chainDiscriminator(r);
      if (!chainGroups[ck]) {
        chainGroups[ck] = { recs: [], displayName: r._ageName || '_' };
        chainGroupOrder.push(ck);
      }
      chainGroups[ck].recs.push(r);
    });
    var GREEK_PANEL = ('\u03b1\u03b2\u03b3\u03b4\u03b5\u03b6\u03b7\u03b8\u03b9\u03ba\u03bb\u03bc'
                     + '\u03bd\u03be\u03bf\u03c0\u03c1\u03c3\u03c4\u03c5\u03c6\u03c7\u03c8\u03c9').split('');
    function _panelLabel(i){return i<GREEK_PANEL.length?GREEK_PANEL[i]:('c'+i);}

    chainGroupOrder.forEach(function(chainKey) {
      var group = chainGroups[chainKey];
      var chainRecs = group.recs;
      var displayName = group.displayName === '_' ? 'Age I' : group.displayName;
      var chainHeader = chainGroupOrder.length > 1
        ? 'Chain · ' + chainKey.slice(0, 18) + (chainKey.length > 18 ? '\u2026' : '') + ' · ' + displayName
        : 'Chain & Constellation';
      html += '<div class="ev"><div class="ev-h" style="background:rgba(80,80,100,0.06);border-left:3px solid rgba(80,80,100,0.2);"><span class="ev-t">' + escapeHtml(chainHeader) + '</span></div><div class="ev-body">';

      var anyDark = chainRecs.some(function(r) { return _isDark(r.chain_visibility); });
      // Chain-level "unlocked" status only: the unlock control lives in
      // each sealed record's detail card (chain-keyed: unlocking any one
      // record unlocks every record in the chain). We surface the
      // chain-wide confirmation here once all sealed records have decrypted.
      if (anyDark && chainRecs.every(function(r) { return r._unlocked || !_isDark(r.chain_visibility); })) {
        html += '<div class="ev-sec">Dark Matter</div>';
        html += '<div style="display:flex;gap:0.4rem;align-items:center;font-size:0.65rem;color:#c8b080;">'
             +  '<span style="padding:0.1rem 0.4rem;border-radius:3px;background:rgba(200,176,128,0.12);border:1px solid rgba(200,176,128,0.3);">\u00b7 unlocked \u00b7</span>'
             +  '<span style="color:#6a6a80;">soul + chunks decrypted in-memory</span>'
             +  '</div>';
      }

      // Parent Chain — walk the parent_id graph so records render in
      // genesis → descendants order. Each "root" (genesis, or a record
      // whose parent is external to this drop) starts its own contiguous
      // run of descendants. Without this the list ordering depends on
      // the input file iteration which reads as random.
      var idSet = {}; chainRecs.forEach(function(r){if(r.identifier)idSet[r.identifier]=r;});
      var childrenOf = {};  // parent_id → [records whose parent_id matches]
      var roots = [];       // records with no parent_id OR external parent_id
      chainRecs.forEach(function(r) {
        var pid = r.parent_id;
        if (!pid || !idSet[pid]) { roots.push(r); return; }
        if (!childrenOf[pid]) childrenOf[pid] = [];
        childrenOf[pid].push(r);
      });
      // Deterministic ordering within a parent's children: by outer_position
      // when available, then by identifier as a tiebreaker. Keeps two
      // descendants of the same parent (rare in a clean chain) stable.
      function _ord(a, b) {
        var ap = (a.outer_position != null) ? a.outer_position : Infinity;
        var bp = (b.outer_position != null) ? b.outer_position : Infinity;
        if (ap !== bp) return ap - bp;
        return (a.identifier || '') < (b.identifier || '') ? -1 : 1;
      }
      roots.sort(_ord);
      Object.keys(childrenOf).forEach(function(k) { childrenOf[k].sort(_ord); });
      // DFS from each root, capping depth to chainRecs.length so a
      // pathological cycle (shouldn't happen — parent_id is set once at
      // mint) can't loop forever.
      var ordered = [];
      var visited = {};
      function walk(r, depth) {
        if (!r || visited[r.identifier] || depth > chainRecs.length) return;
        visited[r.identifier] = true;
        ordered.push(r);
        var kids = r.identifier && childrenOf[r.identifier];
        if (kids) for (var ki = 0; ki < kids.length; ki++) walk(kids[ki], depth + 1);
      }
      roots.forEach(function(rt) { walk(rt, 0); });
      // Safety net: any record not reachable from a root (orphan loop
      // entry) still gets rendered so we never silently drop data.
      chainRecs.forEach(function(r) {
        if (r.identifier && !visited[r.identifier]) ordered.push(r);
      });

      var chainOk = 0, chainExt = 0;
      html += '<div class="ev-sec">Parent Chain</div><details><summary style="font-size:0.65rem;color:#6a6a80;cursor:pointer;">' + chainRecs.length + ' links</summary><div style="font-size:0.65rem;margin-top:0.3rem;">';
      for (var ci = 0; ci < ordered.length; ci++) {
        var cr = ordered[ci], pid = cr.parent_id;
        var ok2 = !pid || !!idSet[pid];
        var ext = pid && !idSet[pid];
        if (ok2) chainOk++;
        if (ext) chainExt++;
        var col = !pid ? '#8898b8' : ok2 ? '#4ade80' : '#facc15';
        html += '<div style="padding:0.1rem 0;display:flex;gap:0.3rem;align-items:center;">';
        html += '<span style="width:5px;height:5px;border-radius:50%;background:' + col + ';"></span>';
        html += '<span style="color:#8888a0;font-family:monospace;font-size:0.58rem;">' + escapeHtml((cr.identifier || cr._fn || '').slice(-14)) + '</span>';
        html += '<span style="color:' + col + ';font-size:0.55rem;">' + (!pid ? 'genesis' : escapeHtml(ok2 ? '\u2190' + pid.slice(-10) : '\u2190' + pid.slice(-10) + ' (ext)')) + '</span></div>';
      }
      html += '</div></details>';
      html += '<div style="font-size:0.62rem;color:#8a8a9a;margin-top:0.2rem;"><span style="color:#4ade80;">' + chainOk + ' valid</span>' + (chainExt ? ' <span style="color:#facc15;">' + chainExt + ' external</span>' : '') + '</div>';

      // Constellations — only this chain's
      var conMap = {};
      chainRecs.forEach(function(r2) {
        var cn = r2.constellation_name || '_none';
        if (!conMap[cn]) conMap[cn] = {recs: [], heart: null, positions: new Set(), sizeK: 0, smallestLayerK: 0};
        conMap[cn].recs.push(r2);
        // Cadence (K) is the record's declared inner-cycle size; completion
        // counts distinct constellation positions.
        if (typeof r2.constellation_size === 'number' && r2.constellation_size > 0) conMap[cn].sizeK = r2.constellation_size;
        var _cidx = (typeof r2.constellation_index === 'number') ? r2.constellation_index
                  : (r2.outer_position != null ? r2.outer_position : r2._gridPos);
        if (_cidx != null) conMap[cn].positions.add(_cidx);
        var ch2 = r2.chunks && typeof r2.chunks === 'object' ? r2.chunks : null;
        if (ch2) Object.keys(ch2).forEach(function(role) {
          var ent = ch2[role];
          if (!ent || ent.index === undefined || typeof ent.total !== 'number' || ent.total <= 0) return;
          conMap[cn].smallestLayerK = conMap[cn].smallestLayerK === 0 ? ent.total : Math.min(conMap[cn].smallestLayerK, ent.total);
        });
        if (r2.heart_star_id) conMap[cn].heart = r2.heart_star_id;
      });
      var conNames = Object.keys(conMap).filter(function(n){return n !== '_none';});
      if (conNames.length > 0) {
        html += '<div class="ev-sec">Constellations (' + conNames.length + ')</div>';
        for (var cni = 0; cni < conNames.length; cni++) {
          var cn2 = conNames[cni], cd = conMap[cn2];
          var conK = cd.sizeK || cd.smallestLayerK || cd.recs.length;
          cd.recs.sort(function(a, b) {
            var ap = (a.outer_position != null) ? a.outer_position : (a._gridPos != null ? a._gridPos : 0);
            var bp = (b.outer_position != null) ? b.outer_position : (b._gridPos != null ? b._gridPos : 0);
            return ap - bp;
          });
          var present = cd.positions.size || cd.recs.length;
          var cc = present === conK;
          html += '<div style="margin:0.3rem 0;padding:0.25rem 0.4rem;background:rgba(60,60,80,0.08);border-left:2px solid ' + (cc ? 'rgba(74,158,74,0.4)' : 'rgba(180,160,60,0.3)') + ';border-radius:3px;">';
          html += '<div style="display:flex;justify-content:space-between;"><span style="font-size:0.7rem;color:#c0c0d0;font-weight:600;">' + escapeHtml(cn2) + '</span><span style="font-size:0.55rem;color:' + (cc ? '#4ade80' : '#facc15') + ';">' + present + '/' + conK + '</span></div>';
          html += '<div style="display:flex;flex-wrap:wrap;gap:2px;margin-top:0.15rem;">';
          for (var cri = 0; cri < cd.recs.length; cri++) {
            var cr2 = cd.recs[cri];
            var isH = cr2.identifier && cr2.identifier === cd.heart;
            var pos2 = (cr2.outer_position != null) ? cr2.outer_position
                     : (cr2._gridPos != null ? cr2._gridPos : cri);
            var letterIdx = ((pos2 % conK) + conK) % conK;
            // V1 records carry constellation_index (int); fall back to
            // computed letterIdx for sibling records that don't have it
            // (e.g. records before they were minted with the new field).
            var idxFromRec = (typeof cr2.constellation_index === 'number') ? cr2.constellation_index : letterIdx;
            var starLetter = _panelLabel(idxFromRec);
            html += '<span style="font-size:0.52rem;padding:0.05rem 0.25rem;border-radius:2px;background:rgba(80,80,100,0.12);color:' + (isH ? '#d4b87b' : '#4a4a60') + ';">' + escapeHtml(starLetter) + '</span>';
          }
          html += '</div></div>';
        }
      }

      // Age — single line for this chain
      var ageNames = {};
      chainRecs.forEach(function(r2) {
        var an = AgeNames.forRecord(r2);
        if (an) ageNames[an] = true;
      });
      var ageList = Object.keys(ageNames);
      var ageColor, ageSuffix, ageLabel;
      if (ageList.length === 0) {
        ageColor = '#8a8a9a';
        ageSuffix = ' (no age declared)';
        ageLabel = displayName;
      } else if (ageList.length === 1) {
        ageColor = '#4ade80';
        ageSuffix = ' (consistent)';
        ageLabel = ageList[0];
      } else {
        ageColor = '#f87171';
        ageSuffix = ' (mixed)';
        ageLabel = ageList.join(', ');
      }
      html += '<div class="ev-sec">Age</div>';
      html += '<div style="font-size:0.7rem;color:' + ageColor + ';">' + escapeHtml(ageLabel) + '<span style="opacity:0.6">' + ageSuffix + '</span></div>';

      html += '</div></div>'; // close ev-body + ev card
    });
  }
  html += '</div>'; // close .meta-body (scrollable region)

  // Render into sidebar
  if (metaSidebar) {
    metaSidebar.innerHTML = html;
  } else {
    metaResults.innerHTML = html;
  }

  // === Build orbit inspector ===
  buildOrbitInspector(valid, collected);

  // Wire Dark Matter unlock buttons. Each button knows its chain key and
  // the input id to pull the password from; on submit we stash the
  // password and re-run the render pipeline from cache. Wrong-password
  // failures surface inline beneath the input — no alert.
  var unlockBtns = document.querySelectorAll('.dm-unlock-btn');
  for (var ub = 0; ub < unlockBtns.length; ub++) {
    (function(btn) {
      var run = async function() {
        // Guard against double-fire: the input's Enter handler and the
        // button's click handler both call run(); without this, a fast
        // Enter→click sequence (or two Enters) could fire concurrent
        // re-renders. The flag is cleared in `finally` so a thrown
        // exception during render doesn't leave the button stuck.
        if (btn.disabled) return;
        var chainKey = btn.getAttribute('data-dm-chain');
        var inputId = btn.getAttribute('data-dm-input');
        var errId = btn.getAttribute('data-dm-err');
        var input = document.getElementById(inputId);
        var errEl = document.getElementById(errId);
        var pw = input ? input.value : '';
        if (!pw) {
          if (errEl) errEl.textContent = 'Enter a password.';
          return;
        }
        if (errEl) errEl.textContent = '';
        btn.disabled = true; btn.textContent = '\u2026';
        try {
          _chainPasswords[chainKey] = pw;
          // Probe one record from this chain to surface wrong-password
          // before we burn the re-render. Without this the chain just
          // re-renders unchanged and the user has no signal.
          var probe = _observatoryCache.find(function(r) {
            return !r._err && _isDark(r.chain_visibility)
                && chainDiscriminator(r) === chainKey;
          });
          var probeOk = false;
          if (probe && probe.encrypted_fields) {
            var pr = await Access.decryptSoul(probe.encrypted_fields, pw);
            probeOk = pr.ok;
          } else if (probe && probe.encrypted_chunks) {
            var pc = await Access.decryptChunks(probe.encrypted_chunks, pw);
            probeOk = pc.ok;
          }
          if (!probeOk) {
            delete _chainPasswords[chainKey];
            if (errEl) errEl.textContent = 'Wrong password.';
            return;
          }
          await _renderObservatoryFromCache();
          // Successful render replaces this button's DOM node — no need
          // to restore state. finally{} below still runs in case the
          // re-render threw before swap.
        } catch (e) {
          delete _chainPasswords[chainKey];
          if (errEl) errEl.textContent = 'Unlock failed: ' + (e && e.message ? e.message : 'unknown error');
        } finally {
          // Only restore button state if it's still in the DOM (the
          // re-render swaps the whole panel — orphaned button would no
          // longer matter). isConnected is on Element since Chrome 51 /
          // Safari 10 / Firefox 49 — safe for our target browsers.
          if (btn.isConnected) {
            btn.disabled = false;
            btn.textContent = 'Unlock';
          }
        }
      };
      btn.addEventListener('click', run);
      var input = document.getElementById(btn.getAttribute('data-dm-input'));
      if (input) input.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') { e.preventDefault(); run(); }
      });
    })(unlockBtns[ub]);
  }
}

function buildOrbitInspector(records, collected) {
  var el = document.getElementById('orbitInspector');
  if (!el || !records.length) return;

  // Greek letters for column labels (Bayer designation). Cover up to
  // 24 columns; fall back to "c0/c1/…" for K > 24.
  var GREEK = ('\u03b1\u03b2\u03b3\u03b4\u03b5\u03b6\u03b7\u03b8\u03b9\u03ba\u03bb\u03bc'
             + '\u03bd\u03be\u03bf\u03c0\u03c1\u03c3\u03c4\u03c5\u03c6\u03c7\u03c8\u03c9').split('');
  function colLabel(i) { return i < GREEK.length ? GREEK[i] : ('c' + i); }

  // Per-Age M / K_inner / row-col dimensions are computed inside
  // render() from that Age's records only. This lets two chains
  // (e.g. a 365-position year-chain and a 3-record fox chain)
  // render side-by-side with different grid shapes. ``inferAgeDims``
  // takes the records for a single Age and returns its derived layout.
  function inferAgeDims(ageRecs) {
    // M (grid size) = the declared outer cycle; K (columns) = the declared
    // inner-cycle cadence when a record carries one, else the smallest layer
    // total. Both come from record data — no layer names, no magic sizes.
    var m = 0, innerK = 0, smallestLayerK = 0;
    ageRecs.forEach(function(r) {
      if (typeof r.outer_cycle === 'number') m = Math.max(m, r.outer_cycle);
      if (typeof r.outer_total === 'number') m = Math.max(m, r.outer_total);
      if (typeof r.outer_position === 'number') m = Math.max(m, r.outer_position + 1);
      if (typeof r.constellation_size === 'number' && r.constellation_size > 0) innerK = r.constellation_size;
      var ch = r.chunks && typeof r.chunks === 'object' ? r.chunks : null;
      if (ch) Object.keys(ch).forEach(function(role) {
        var entry = ch[role];
        if (!entry || typeof entry.total !== 'number' || entry.total <= 0) return;
        m = Math.max(m, entry.total);
        smallestLayerK = smallestLayerK === 0 ? entry.total : Math.min(smallestLayerK, entry.total);
      });
    });
    if (!m) m = 1;
    var k = innerK || smallestLayerK || m;
    if (k > m) k = m;
    var rows = Math.max(1, Math.ceil(m / k));
    return {
      M: m, K: k, COLS: k, ROWS: rows,
      smallestK: smallestLayerK || k,
    };
  }

  // Group by (chain, age). The grouping key combines a chain
  // discriminator (decoder_hash if present, else a signature derived
  // from the record's layer layout) with the age_name. Without this,
  // two independently sealed chains that both happen to call their
  // first Age the same name would collide into one bucket and the
  // grid would silently overwrite cells. The carousel still displays
  // human-readable age_name; the key is internal.
  // (chainDiscriminator is hoisted to module scope so analyzeMeta can
  // precompute r._ageKey before building row HTML — that HTML needs the
  // key for filterRecords to match rows to the current Age.)
  var ages = {}, ageOrder = [];
  records.forEach(function(r) {
    // r._ageKey / r._ageName already assigned by analyzeMeta.
    var key = r._ageKey || (chainDiscriminator(r) + '#' + (r._ageName || '_'));
    var displayName = r._ageName || '_';
    if (!ages[key]) {
      ages[key] = { byPos: {}, recs: [], displayName: displayName };
      ageOrder.push(key);
    }
    ages[key].recs.push(r);
    // Grid position = the record's declared outer position. Fallback for a
    // record without one: any cycling (indexed) layer's chunk index.
    var ti = (r.outer_position != null) ? r.outer_position : null;
    if (ti == null && r.chunks && typeof r.chunks === 'object') {
      var _names = Object.keys(r.chunks);
      for (var _ni = 0; _ni < _names.length; _ni++) {
        var _e = r.chunks[_names[_ni]];
        if (_e && typeof _e.index === 'number' && typeof _e.total === 'number') { ti = _e.index; break; }
      }
    }
    if (ti != null) ages[key].byPos[ti] = r;
    r._gridPos = ti;
  });

  // Disambiguate age labels when multiple chains share the same age_name
  // (e.g. five chains all naming their first Age alike). Without this, the
  // carousel renders five identical tabs and the user can't tell which
  // is which. We suffix each duplicate with a short chain hash.
  var ageDisplayCounts = {};
  ageOrder.forEach(function(k) {
    var nm = ages[k].displayName;
    ageDisplayCounts[nm] = (ageDisplayCounts[nm] || 0) + 1;
  });
  ageOrder.forEach(function(k) {
    var nm = ages[k].displayName;
    if (ageDisplayCounts[nm] > 1) {
      var sig = k.split('#')[0].slice(0, 6);
      ages[k].displayName = (nm === '_' ? 'Age I' : nm) + ' · ' + sig;
    }
  });

  var curAge = ageOrder[0], curSector = 0, mode = 'orbit', curFilter = 'all';
  var selectedCons = new Set(); // empty = show all in age
  var _ageAnimating = false;
  var SLOT_W = 110; // must match CSS .orbit-age width

  function mk(tag, cls) { var e = document.createElement(tag); if (cls) e.className = cls; return e; }
  function hx(h, a) { return 'rgba('+parseInt(h.slice(1,3),16)+','+parseInt(h.slice(3,5),16)+','+parseInt(h.slice(5,7),16)+','+a+')'; }

  function focusRecord(record) {
    document.querySelectorAll('[data-identifier]').forEach(function(row) {
      var d = row.querySelector('.meta-detail');
      if (d) d.style.display = 'none';
    });
    var target = document.querySelector('[data-identifier="' + record.identifier + '"]');
    if (target) {
      var d = target.querySelector('.meta-detail');
      if (d) d.style.display = 'block';
      // Observatory split-scroll: records live inside .meta-body now,
      // which owns the scrollbar. Prefer that over .results-wrap so
      // clicks on grid cells scroll the inner body, not the (locked)
      // outer panel. Fall back to the panel for Image / Audit tabs.
      var mb = document.querySelector('#metaSidebarResults > .meta-body');
      var rw = document.getElementById('resultsWrap');
      if (mb && mb.contains(target)) {
        var targetTop = target.offsetTop - 4;
        mb.scrollTo({ top: targetTop, behavior: 'smooth' });
      } else if (rw && window.getComputedStyle(rw).position === 'fixed') {
        var h = el.offsetHeight || 0;
        var targetTop = target.offsetTop - h - 4;
        rw.scrollTo({ top: targetTop, behavior: 'smooth' });
      } else {
        var h = el.offsetHeight || 0;
        var top = target.getBoundingClientRect().top + window.scrollY - h - 4;
        window.scrollTo({ top: top, behavior: 'smooth' });
      }
      target.style.background = 'rgba(74,158,74,0.08)';
      setTimeout(function() { target.style.background = ''; }, 2000);
    }
  }

  function render() {
    el.innerHTML = '';
    var ad = ages[curAge];

    // Per-Age dimensions — each Age in the inspector renders with its
    // own grid shape, so a fox-only Age I (3×1) can sit beside a
    // larger year-chain (its own M×K) without one bleeding into the other.
    var dims = inferAgeDims(ad.recs);
    var chainM = dims.M;
    var chainKInner = dims.K;
    var TOTAL_COLS = dims.COLS;
    var TOTAL_ROWS = dims.ROWS;

    // Indexed-role set for this Age. Used during cell tagging so every
    // cell — including empty ones — is marked as "in" each observed
    // layer's cycle. Without this, custom-layer filters only highlight
    // cells where a record happens to sit, breaking the dashed-cycle
    // boundary cadence the curated filters get for free.
    var ageRoleKeys = {};
    // Per-layer freeze, DERIVED from each cycling layer's own K: a K-chunk
    // layer tiles floor(M/K) whole cycles (positions 0 .. that product − 1)
    // then freezes for the remainder. Nothing is assumed about which layer
    // it is or where it stops — a K=7 layer freezes wherever 7 stops
    // dividing M, a K=12 layer wherever 12 does.
    var layerFreeze = {};   // role → first frozen position
    ad.recs.forEach(function(r) {
      var ch = r.chunks && typeof r.chunks === 'object' ? r.chunks : null;
      if (!ch) return;
      Object.keys(ch).forEach(function(role) {
        var e = ch[role];
        if (!e || e.index === undefined || typeof e.total !== 'number' || e.total <= 0) return;
        ageRoleKeys[role] = true;
        if (chainM > 0) layerFreeze[role] = Math.floor(chainM / e.total) * e.total;
      });
    });

    // Pinned content positions, DERIVED from where records carry a SINGLE
    // (non-indexed) chunk. Indexed layers cycle across the grid; a single
    // chunk is pinned to one position. The author can place a pinned role on
    // any position and the Observatory marks + filters it exactly there.
    var pinnedAt = {};   // gridPos → { roles: {roleName: true} }
    ad.recs.forEach(function(r) {
      var p = r._gridPos;
      if (p == null || !r.chunks || typeof r.chunks !== 'object') return;
      Object.keys(r.chunks).forEach(function(role) {
        var e = r.chunks[role];
        if (!e || e.index !== undefined || e.data === undefined) return;   // single chunks only
        var slot = pinnedAt[p] || (pinnedAt[p] = { roles: {} });
        slot.roles[role] = true;
      });
    });
    // Aggregate pinned content up to rows — for the row marker + the
    // sector-mode filter collapse. Row index = floor(pos / TOTAL_COLS).
    var rowPin = {};   // row → { roles: {roleName: true} }
    Object.keys(pinnedAt).forEach(function(ps) {
      var ri2 = Math.floor((+ps) / TOTAL_COLS);
      var rp = rowPin[ri2] || (rowPin[ri2] = { roles: {} });
      Object.keys(pinnedAt[ps].roles).forEach(function(role) { rp.roles[role] = true; });
    });

    // Row → constellation name map (uses cached _gridPos from above).
    // rowHasRec distinguishes a row that holds records-but-no-constellation
    // (the calendar remainder — dark days + epagomenal tail) from a genuinely
    // empty future row; they get different labels below.
    var rowCon = {}, rowHeart = {}, rowHasRec = {};
    ad.recs.forEach(function(r) {
      if (r._gridPos != null) {
        var row = Math.floor(r._gridPos / TOTAL_COLS);
        rowHasRec[row] = true;
        if (r.constellation_name) rowCon[row] = r.constellation_name;
        if (r.heart_star_id && r.identifier === r.heart_star_id) rowHeart[row] = true;
      }
    });

    // Resolve a grouping key back to its human-readable display name.
    // Multi-chain drops use (chainSig, age_name) as the key; the user
    // sees the age_name only.
    function ageLabel(k) {
      var info = ages[k];
      var nm = info ? info.displayName : '_';
      return nm === '_' ? 'Age I' : nm;
    }

    // === Age label — single-age drops show a static centered label;
    // multi-age drops get the full carousel with ◀ ▶ for navigation. ===
    if (ageOrder.length === 1) {
      var single = mk('div', 'orbit-ages');
      var lbl = mk('div', 'orbit-age center');
      lbl.textContent = ageLabel(curAge);
      single.appendChild(lbl);
      el.appendChild(single);
    } else if (ageOrder.length > 1) {
      var ap = mk('div', 'orbit-ages');
      var curIdx = ageOrder.indexOf(curAge);
      var n = ageOrder.length;
      var atLeft = curIdx === 0, atRight = curIdx === n - 1;

      // Left arrow (dimmed at start)
      var la = mk('span', 'orbit-arrow');
      la.textContent = '\u25c0';
      if (atLeft) la.style.opacity = '0.15';
      ap.appendChild(la);

      // Window (overflow hidden, shows 5 labels)
      var win = mk('div', 'orbit-ages-window');
      var track = mk('div', 'orbit-ages-track');

      // Build 7 slots: center ± 3. Out-of-bounds slots are empty placeholders.
      for (var offset = -3; offset <= 3; offset++) {
        var ai = curIdx + offset;
        var absOff = Math.abs(offset);
        var p = mk('div', 'orbit-age');
        if (ai >= 0 && ai < n) {
          p.textContent = ageLabel(ageOrder[ai]);
          if (absOff === 0) p.classList.add('center');
          else if (absOff === 1) {
            p.classList.add('near-1');
            (function(off) { p.onclick = function() { shiftAge(off); }; })(offset);
          }
          else if (absOff === 2) {
            p.classList.add('near-2');
            (function(off) { p.onclick = function() { shiftAge(off); }; })(offset);
          }
          else p.classList.add('off');
        } else {
          p.classList.add('off'); // empty slot beyond edges
        }
        track.appendChild(p);
      }

      // Position: center label (slot 3 of 7) centered in 5-slot window → offset = -1 slot
      track.style.transform = 'translateX(-' + SLOT_W + 'px)';

      win.appendChild(track);
      ap.appendChild(win);

      var ra2 = mk('span', 'orbit-arrow');
      ra2.textContent = '\u25b6';
      if (atRight) ra2.style.opacity = '0.15';
      ap.appendChild(ra2);

      // Animate by `steps` slots — clamped to edges, no wrap
      function shiftAge(steps) {
        if (_ageAnimating || steps === 0) return;
        var dest = curIdx + steps;
        if (dest < 0 || dest >= n) return; // at edge, do nothing
        _ageAnimating = true;
        track.classList.add('sliding');
        var target = (-1 + (-steps)) * SLOT_W;
        track.style.transform = 'translateX(' + target + 'px)';
        track.addEventListener('transitionend', function handler() {
          track.removeEventListener('transitionend', handler);
          _ageAnimating = false;
          curAge = ageOrder[dest];
          curSector = 0;
          selectedCons.clear();
          render();
        }, { once: true });
      }

      la.onclick = function() { shiftAge(-1); };
      ra2.onclick = function() { shiftAge(1); };
      el._shiftAge = shiftAge;

      el.appendChild(ap);
    }

    // === Controls row ===
    var ctl = mk('div', 'orbit-controls');

    var ob = mk('button', 'orbit-vbtn' + (mode === 'orbit' ? ' active' : ''));
    ob.textContent = 'Expanded';
    var sb = mk('button', 'orbit-vbtn' + (mode === 'sector' ? ' active' : ''));
    sb.textContent = 'Compact';
    ob.onclick = function() { mode = 'orbit'; fog(); sel.onchange(); restoreFocus(); ob.classList.add('active'); sb.classList.remove('active'); };
    sb.onclick = function() { mode = 'sector'; fog(); sel.onchange(); restoreFocus(); sb.classList.add('active'); ob.classList.remove('active'); };
    ctl.appendChild(ob);
    ctl.appendChild(sb);

    var sel = mk('select', 'orbit-filter');
    // Build the dropdown from the roles actually present in this Age's
    // records — one option per role, labeled from the role name. Sorted
    // for a stable order across drops.
    var observedRoles = {};
    ad.recs.forEach(function(r) {
      var ch = r.chunks && typeof r.chunks === 'object' ? r.chunks : null;
      if (ch) Object.keys(ch).forEach(function(role) { observedRoles[role] = true; });
    });
    var opts = {all: 'All'};
    Object.keys(observedRoles).sort().forEach(function(role) {
      opts[role] = roleMeta(role).label;
    });
    for (var k in opts) { var o = document.createElement('option'); o.value = k; o.textContent = opts[k]; sel.appendChild(o); }
    // If the previously-selected filter is gone from this set, fall back to "all".
    sel.value = opts[curFilter] ? curFilter : 'all';
    if (sel.value !== curFilter) curFilter = sel.value;
    ctl.appendChild(sel);

    var hashOk = ad.recs.filter(function(r) { return r._match; }).length;
    var supplied = Object.keys(ad.byPos).length;
    var stIn = mk('span', '');
    stIn.style.cssText = 'font-size:0.46rem;color:#5a5a6a;';
    stIn.textContent = supplied + '/' + chainM;
    ctl.appendChild(stIn);

    el.appendChild(ctl);

    // === Grid ===
    var gridWrap = mk('div', 'orbit-grid');
    var tbl = mk('table', 'orbit-tbl');
    // For narrow grids (small K_inner), the default ``width: 100%``
    // stretches each cell to fill the container — three cells eat the
    // whole row. Constrain to ~36px per cell + label width and center
    // the table so a small Age renders compact instead of sprawling.
    if (TOTAL_COLS < 12) {
      var CELL_PX = 36;
      var LABEL_PX = 72;
      tbl.style.width = (LABEL_PX + TOTAL_COLS * CELL_PX) + 'px';
      tbl.style.margin = '0 auto';
    }

    // Header row
    var hdr = document.createElement('tr');
    var th0 = document.createElement('td');
    th0.style.cssText = 'max-width:68px;';
    hdr.appendChild(th0);
    for (var c = 0; c < TOTAL_COLS; c++) {
      var th = mk('td', 'orbit-hdr');
      th.textContent = colLabel(c);
      hdr.appendChild(th);
    }
    tbl.appendChild(hdr);

    // Data rows
    var rowEls = [];
    for (var ri = 0; ri < TOTAL_ROWS; ri++) {
      var tr = document.createElement('tr');
      tr.className = 'orbit-row';
      var base = ri * TOTAL_COLS;
      var cn = rowCon[ri];
      if (cn && selectedCons.has(cn)) tr.classList.add('row-selected');
      // A row is "special" if it actually holds pinned (single-chunk)
      // content, derived from pinnedAt — no frozen-tail assumption. The
      // pinned cells carry the styling; the row gets a marker.
      var special = !!rowPin[ri];

      // Label cell
      var lbl = mk('td', 'orbit-lbl');
      if (cn) {
        lbl.textContent = cn;
        lbl.classList.add('has-name');
        if (selectedCons.has(cn)) lbl.classList.add('selected');
        (function(name, lblEl, rowEl) {
          lblEl.onclick = function() {
            // Pure toggle — no sector focus change
            if (selectedCons.has(name)) {
              selectedCons.delete(name);
            } else {
              selectedCons.add(name);
            }
            // Update all label + row highlights
            tbl.querySelectorAll('.orbit-lbl.has-name').forEach(function(l) {
              l.classList.toggle('selected', selectedCons.has(l.textContent));
            });
            rowEls.forEach(function(tr, i) {
              tr.classList.toggle('row-selected', !!rowCon[i] && selectedCons.has(rowCon[i]));
            });
            tbl.classList.toggle('has-selection', selectedCons.size > 0);
            fog();
            sel.onchange();
            restoreFocus();
            filterRecords();
          };
        })(cn, lbl, tr);
      } else if (special) {
        lbl.textContent = '\u25c6';   // marker: this row holds pinned content
        lbl.style.color = '#8a7050';
      } else if (rowHasRec[ri]) {
        // Holds records but none belong to a constellation: the calendar
        // remainder \u2014 positions that fall outside the constellation cadence
        // (a solar Age's dark days + epagomenal tail). Name it for what it is,
        // not a bare ordinal that reads like "constellation 31".
        lbl.textContent = 'void';
        lbl.classList.add('void-row');
        var _last = Math.min(base + TOTAL_COLS, chainM) - 1;
        lbl.title = 'Outside the constellation cycle \u00b7 position' +
                    (_last > base ? 's ' + base + '\u2013' + _last : ' ' + base);
      } else {
        // A genuinely empty future row \u2014 keep the ordinal as a position
        // reference, but dim it so it recedes behind the named rows.
        lbl.textContent = (ri + 1);
        lbl.classList.add('empty-row');
      }
      tr.appendChild(lbl);

      // K_inner cells per row — the "constellation" cycle.
      for (var ci = 0; ci < TOTAL_COLS; ci++) {
        var pos = base + ci;
        var td = document.createElement('td');
        if (pos >= chainM) { tr.appendChild(td); continue; }

        var cell = mk('div', 'orbit-c');
        cell.dataset.pos = pos;
        cell.dataset.row = ri;

        var rec = ad.byPos[pos];

        if (rec) {
          cell.classList.add('supplied');
          // Sealed records have _match === false by design (the stored
          // hash covers plaintext we can't see). Skip the red .tampered
          // styling so they don't read as forged when they're just locked.
          if (rec._match === false && !rec._sealed) cell.classList.add('tampered');
          cell.textContent = colLabel(ci);
          if (ci === 0 && rowHeart[ri]) cell.classList.add('heart');
          (function(record, row, cellEl) {
            var _savedBg = '';
            cellEl.addEventListener('mouseenter', function() {
              if (cellEl.classList.contains('focused')) return;
              cellEl.style.filter = 'brightness(1.4)';
              cellEl.style.boxShadow = '0 0 6px rgba(255,255,255,0.15)';
            });
            cellEl.addEventListener('mouseleave', function() {
              if (cellEl.classList.contains('focused')) return;
              cellEl.style.filter = '';
              cellEl.style.boxShadow = '';
            });
            cellEl.onclick = function() {
              // Only allow selection if cell is in the active filter
              var types = cellEl.dataset.types || '';
              if (curFilter !== 'all' && types.indexOf(curFilter) < 0) return;
              curSector = row;
              fog();
              // Clear old focus
              tbl.querySelectorAll('.orbit-c.focused').forEach(function(c) {
                c.classList.remove('focused');
                c.style.background = ''; c.style.boxShadow = ''; c.style.filter = '';
                c.style.borderColor = ''; c.style.borderTop = ''; c.style.borderRight = ''; c.style.borderBottom = ''; c.style.borderLeft = '';
              });
              // Re-apply filter to restore all cells (cadence borders, colors)
              sel.onchange();
              // Apply focus with filter-colored glow on top
              var fc = getRoleColor(curFilter);
              cellEl.classList.add('focused');
              cellEl.style.background = 'rgba(' + fc + ',0.65)';
              cellEl.style.borderColor = 'rgba(' + fc + ',0.9)';
              cellEl.style.boxShadow = '0 0 10px rgba(' + fc + ',0.5), 0 0 3px rgba(' + fc + ',0.8)';
              focusRecord(record);
            };
          })(rec, ri, cell);
        } else {
          cell.textContent = '\u00b7';
        }

        // Cell filter membership, both derived: (1) each cycling layer up to
        // its own freeze; (2) pinned content at its actual position.
        var types = '';
        // Each cycling layer is active from position 0 up to its OWN
        // derived freeze; tag every cell (empty ones too) so the dashed
        // cadence pattern draws. No layer name or freeze position is
        // hardcoded — a layer reaches into the tail iff its own K takes it
        // there (e.g. a K=7 layer cycles four positions past a K=12 one).
        Object.keys(ageRoleKeys).forEach(function(key) {
          var frz = layerFreeze[key];
          if (frz === undefined) return;
          if (pos < frz) types += ' ' + key;
        });
        // Pinned single-chunk roles — tagged at the ACTUAL position where the
        // chunk sits (from pinnedAt), by the role's own name. No frozen-tail
        // assumption: a pinned role on any position lights that exact cell.
        var _pin = pinnedAt[pos];
        if (_pin) Object.keys(_pin.roles).forEach(function(role) { types += ' ' + role; });
        cell.dataset.types = types.trim();

        td.appendChild(cell);
        tr.appendChild(td);
      }

      tbl.appendChild(tr);
      rowEls.push(tr);
    }

    gridWrap.appendChild(tbl);
    el.appendChild(gridWrap);

    // === Stats line (per-age counts) ===
    // Per-Age rollup. We walk ad.recs and bucket every observed chunk role
    // — no hardcoded list. The same generic loop the global collector
    // uses, scoped to this Age's records.
    var stats = mk('div', 'orbit-stats');
    var ageIndexed = {};  // role → {total, indices}
    var ageSingle  = {};  // role → true
    function bucketEntry(role, entry) {
      if (!entry || entry.data === undefined) return;
      if (entry.index !== undefined && entry.total !== undefined) {
        if (!ageIndexed[role]) ageIndexed[role] = { total: entry.total, indices: {} };
        ageIndexed[role].total = entry.total;
        ageIndexed[role].indices[entry.index] = true;
      } else {
        ageSingle[role] = true;
      }
    }
    ad.recs.forEach(function(r) {
      var ch = r.chunks && typeof r.chunks === 'object' ? r.chunks : null;
      if (ch) Object.keys(ch).forEach(function(role) { bucketEntry(role, ch[role]); });
    });
    // Sealed records can't be verified without a password — count them
    // separately so "0/3 verified" doesn't read as failure when the
    // real state is "3 sealed, awaiting unlock".
    var sealedCount = ad.recs.filter(function(r) { return r._sealed; }).length;
    var verifiable = ad.recs.length - sealedCount;
    var verifyClass = verifiable === 0 ? '' : (hashOk === verifiable ? 'pass' : 'warn');
    stats.innerHTML =
      '<span>' + ad.recs.length + ' stars</span>' +
      (verifiable > 0
        ? '<span class="' + verifyClass + '">' + hashOk + '/' + verifiable + ' verified</span>'
        : '') +
      (sealedCount > 0
        ? '<span class="sealed">' + sealedCount + ' sealed</span>'
        : '');
    el.appendChild(stats);

    // === Reassembly downloads ===
    // The button bar is driven entirely by what's observed in this Age's
    // records — one button per role, labeled + colored from the role name.
    // Indexed roles only appear when the count matches the chunk-declared
    // total; single roles appear as soon as one is present.
    var indexedRoles = sortRoles(Object.keys(ageIndexed));
    var singleRoles  = sortRoles(Object.keys(ageSingle));
    var hasComplete  = indexedRoles.some(function(r) {
      return Object.keys(ageIndexed[r].indices).length === ageIndexed[r].total;
    }) || singleRoles.length > 0;
    if (hasComplete) {
      var ra = mk('div', 'orbit-assembly');
      // Filter colors come from getRoleColor(): curated role/filter
      // names map to the curated palette, anything else gets a stable
      // hue hashed from the role name itself.
      function dlBtnColored(label, color, onclick) {
        var b = mk('button', 'orbit-vbtn');
        b.textContent = '\u2913 ' + label;
        var c = getRoleColor(color);
        b.style.borderColor = 'rgba(' + c + ',0.4)';
        b.style.color = 'rgb(' + c + ')';
        b.style.background = 'rgba(' + c + ',0.08)';
        b.onmouseenter = function() { b.style.background = 'rgba(' + c + ',0.18)'; };
        b.onmouseleave = function() { b.style.background = 'rgba(' + c + ',0.08)'; };
        b.onclick = onclick;
        ra.appendChild(b);
      }
      // Indexed-role downloads — a role's chunks in index order. The form
      // (separate files vs one reassembled file, binary vs text) is decided
      // from the bytes by downloadRoleChunks, so no role is special-cased.
      indexedRoles.forEach(function(role) {
        var bucket = ageIndexed[role];
        // Defensive: layers with total=0 (malformed seal, empty layer)
        // shouldn't surface a download button.
        if (!bucket || !bucket.total || bucket.total <= 0) return;
        if (Object.keys(bucket.indices).length !== bucket.total) return;
        var globalBucket = collected.indexed[role];
        if (!globalBucket) return;
        var meta = roleMeta(role);
        dlBtnColored(meta.label, meta.color, async function() {
          var list = [];
          for (var i = 0; i < bucket.total; i++) list.push(globalBucket.chunks[i]);
          await downloadRoleChunks(role, list);
        });
      });
      // Single-role downloads — one pinned chunk per role.
      singleRoles.forEach(function(role) {
        var entry = collected.single[role];
        if (!entry) return;
        var meta = roleMeta(role);
        dlBtnColored(meta.label, meta.color, async function() {
          await downloadRoleChunks(role, [entry]);
        });
      });
      el.appendChild(ra);
    }

    el.classList.add('visible');

    // === Fog of war ===
    function fog() {
      var hasSel = selectedCons.size > 0;
      rowEls.forEach(function(tr, i) {
        tr.classList.remove('collapsed', 'near');
        if (mode !== 'sector') return;
        var base = i * TOTAL_COLS;
        var isCon = !!rowCon[i];
        var isSpecial = !!rowPin[i];   // row holds pinned content (derived)
        var hasRec = false;
        for (var ci = 0; ci < TOTAL_COLS && base + ci < chainM; ci++) {
          if (ad.byPos[base + ci]) { hasRec = true; break; }
        }

        if (!hasRec) {
          tr.classList.add('collapsed');
          return;
        }

        // Filter-aware collapse, derived (no frozen-tail boundary): a
        // cycling-layer filter hides rows past that layer's own freeze (no
        // chunks there); a pinned filter hides rows lacking its content.
        var _lf = layerFreeze[curFilter];
        if (_lf !== undefined) {
          // Cycling-layer filter — hide rows past that layer's own freeze.
          if (base >= _lf) { tr.classList.add('collapsed'); return; }
        } else if (curFilter !== 'all') {
          // Pinned (single) role filter — hide rows that don't hold it.
          if (!(rowPin[i] && rowPin[i].roles && rowPin[i].roles[curFilter])) { tr.classList.add('collapsed'); return; }
        }

        // Constellation selection filter — collapse unselected constellations.
        // Rows holding pinned content (with records) always stay visible.
        if (hasSel) {
          if (isCon && selectedCons.has(rowCon[i])) {
            // Selected constellation — stay visible
          } else if (isSpecial && hasRec) {
            // Special row with records — stay visible
          } else {
            tr.classList.add('collapsed');
          }
        }
      });
    }

    // === Filter handler — with cadence boundary outlines ===
    // Derived filter cycle lengths + offsets. Custom layer filters
    // derive their (length, offset) from the observed chunks at
    // render time — see resolveFilterCycle() below.
    function resolveFilterCycle(filterKey) {
      // Cycling (indexed) role: the cadence length is its observed chunk
      // total, drawn from position 0. Prefer this Age's observed total so a
      // chain of any inner K draws its own cadence boundaries.
      var bucket = ageIndexed[filterKey];
      if (bucket && bucket.total > 0) return { len: bucket.total, off: 0 };
      // Pinned (single) role: no cadence — find the record carrying this
      // role's chunk and treat it as a cycle of 1 at that exact position.
      for (var i = 0; i < ad.recs.length; i++) {
        var r = ad.recs[i];
        var ch = r.chunks && typeof r.chunks === 'object' ? r.chunks : null;
        if (ch && ch[filterKey] !== undefined) {
          return { len: 1, off: (r._gridPos != null ? r._gridPos : 0) };
        }
      }
      return { len: chainM, off: 0 };
    }

    function resolveFilterColor(filterKey) {
      // Convert getRoleColor's "R,G,B" to "#RRGGBB" so the hx() helper works.
      var rgb = getRoleColor(filterKey).split(',');
      function h(n) { var s = parseInt(n, 10).toString(16); return s.length < 2 ? '0' + s : s; }
      return '#' + h(rgb[0]) + h(rgb[1]) + h(rgb[2]);
    }

    // Compute which edges of a cell are group boundaries. cols and
    // wrap come from the per-Age dimensions so chains of any shape
    // (any M or inner K) get correct cadence borders.
    function gEdge(p, groupLen) {
      if (!groupLen || groupLen <= 0) return {t:false,b:false,l:false,r:false};
      var g = Math.floor(p / groupLen), c = p % TOTAL_COLS;
      var t = false, b = false, l = false, r = false;
      if (p % groupLen === 0) l = true; else if (c === 0) l = true;
      if (p % groupLen === groupLen - 1) r = true; else if (c === TOTAL_COLS - 1) r = true;
      var above = p - TOTAL_COLS;
      if (above < 0 || Math.floor(above / groupLen) !== g) t = true;
      var below = p + TOTAL_COLS;
      if (below >= chainM || Math.floor(below / groupLen) !== g) b = true;
      return {t:t, b:b, l:l, r:r};
    }

    // Tampered cells always get red override, regardless of filter
    function applyTamperedOverride() {
      tbl.querySelectorAll('.orbit-c.tampered').forEach(function(c) {
        if (c.style.opacity === '0.06') return; // outside current filter — leave dimmed
        c.style.background = 'rgba(196,123,123,0.3)';
        c.style.color = '#c47b7b';
      });
    }

    sel.onchange = function() {
      curFilter = sel.value;
      // Update sector collapse for filter-aware rows
      fog();
      // Re-color focused cell glow to match new filter
      var focusedEl = tbl.querySelector('.orbit-c.focused');
      if (focusedEl) {
        var fc = getRoleColor(curFilter);
        focusedEl.style.background = 'rgba(' + fc + ',0.65)';
        focusedEl.style.borderColor = 'rgba(' + fc + ',0.9)';
        focusedEl.style.boxShadow = '0 0 10px rgba(' + fc + ',0.5), 0 0 3px rgba(' + fc + ',0.8)';
      }
      var cells = tbl.querySelectorAll('.orbit-c');
      if (curFilter === 'all') {
        cells.forEach(function(c) {
          var row = c.closest('.orbit-row');
          if (row && row.classList.contains('collapsed')) {
            c.style.border = 'none'; c.style.borderTop = ''; c.style.borderRight = ''; c.style.borderBottom = ''; c.style.borderLeft = '';
            c.style.background = ''; c.style.opacity = ''; c.style.color = '';
            return;
          }
          // Returning to "all": clear every inline style a filter may have
          // set so the cell falls back to its CSS appearance — identical to
          // first render. Pinned cells look like any other cell here; their
          // distinction shows only when you filter by that role.
          c.style.opacity = '';
          c.style.background = '';
          c.style.color = '';
          c.style.border = '';
          c.style.borderTop = ''; c.style.borderRight = ''; c.style.borderBottom = ''; c.style.borderLeft = '';
        });
        applyTamperedOverride();
        applySelectionHighlight();
        return;
      }
      var col = resolveFilterColor(curFilter);
      var cyc = resolveFilterCycle(curFilter);
      var cn = cyc.len, co = cyc.off;
      cells.forEach(function(c) {
        // Skip cells in collapsed rows (sector mode)
        var row = c.closest('.orbit-row');
        if (row && row.classList.contains('collapsed')) {
          c.style.border = 'none'; c.style.borderTop = ''; c.style.borderRight = ''; c.style.borderBottom = ''; c.style.borderLeft = '';
          c.style.background = ''; c.style.opacity = ''; c.style.color = '';
          return;
        }
        var types = c.dataset.types || '';
        var p = parseInt(c.dataset.pos);
        var inC = types.indexOf(curFilter) >= 0;
        var sup = c.classList.contains('supplied');
        if (!inC) {
          c.style.opacity = '0.06';
          c.style.background = 'rgba(25,25,35,0.15)';
          c.style.border = 'none';
          c.style.borderTop = ''; c.style.borderRight = ''; c.style.borderBottom = ''; c.style.borderLeft = '';
          c.style.color = 'rgba(50,50,60,0.15)';
        } else {
          c.style.opacity = '1';
          c.style.background = sup ? hx(col, 0.3) : 'rgba(25,25,35,0.15)';
          c.style.color = sup ? col : hx(col, 0.2);
          // Cadence boundaries — solid for supplied, dashed for empty
          var e = gEdge(p - co, cn);
          var eb = sup ? '2px solid ' + hx(col, 0.8) : '2px dashed ' + hx(col, 0.4);
          var ib = 'none';
          c.style.borderTop = e.t ? eb : ib;
          c.style.borderRight = e.r ? eb : ib;
          c.style.borderBottom = e.b ? eb : ib;
          c.style.borderLeft = e.l ? eb : ib;
        }
      });
      applyTamperedOverride();
      applySelectionHighlight();
    };

    function applySelectionHighlight() {
      if (selectedCons.size === 0) return;
      tbl.querySelectorAll('.orbit-row:not(.row-selected):not(.collapsed) .orbit-c.supplied').forEach(function(c) {
        if (!c.classList.contains('focused')) {
          c.style.opacity = '0.35';
        }
      });
      tbl.querySelectorAll('.orbit-row:not(.row-selected):not(.collapsed) .orbit-lbl').forEach(function(l) {
        l.style.opacity = '0.35';
      });
    }

    function restoreFocus() {
      var f = tbl.querySelector('.orbit-c.focused');
      if (!f) return;
      var fc = getRoleColor(curFilter);
      f.style.background = 'rgba(' + fc + ',0.65)';
      f.style.borderColor = 'rgba(' + fc + ',0.9)';
      f.style.boxShadow = '0 0 10px rgba(' + fc + ',0.5), 0 0 3px rgba(' + fc + ',0.8)';
      f.style.color = '#fff';
      f.style.opacity = '1';
    }

    fog();

    // Auto-focus first populated sector in sector mode
    if (mode === 'sector') {
      for (var si = 0; si < TOTAL_ROWS; si++) {
        if (rowCon[si]) { curSector = si; fog(); break; }
      }
    }

    // Re-apply active filter (persists across age changes)
    if (curFilter !== 'all') sel.onchange();

    // Filter records to current age + selected constellations
    filterRecords();
  }

  function filterRecords() {
    // Match on the chain-discriminated grouping key (data-age-key), not
    // the human-readable display name, so two chains that share a
    // displayName don't filter into each other's bucket.
    var useCon = selectedCons.size > 0;
    var recRows = document.querySelectorAll('[data-age]');
    var visCount = 0;
    recRows.forEach(function(row) {
      var ageMatch = row.dataset.ageKey === curAge;
      var conMatch = !useCon || selectedCons.has(row.dataset.con);
      if (ageMatch && conMatch) {
        row.style.display = '';
        visCount++;
      } else {
        row.style.display = 'none';
        var d = row.querySelector('.meta-detail');
        if (d) d.style.display = 'none';
      }
    });
    var recHeader = document.querySelector('.ev-t');
    if (recHeader && recHeader.textContent.indexOf('Records') === 0) {
      recHeader.textContent = 'Records (' + visCount + ')';
    }
  }

  render();
}

// =====================================================================
// AUDIT — full forensic report on a record fetched by identifier
// =====================================================================
// Each audited identifier is a browser-history step so back/forward can
// traverse the chain the user clicked through (parent / heart-star /
// identifier links). _auditFromPop suppresses the push when WE re-audit
// in response to a popstate, so back/forward don't re-stack entries.
var _auditFromPop = false;
window.addEventListener('popstate', function(e) {
  if (e.state && e.state.auditId) {
    document.getElementById('auditInput').value = e.state.auditId;
    showTab('cert');
    _auditFromPop = true;
    runAudit();
  }
});
document.getElementById('auditInput').addEventListener('keydown', function(e) {
  if (e.key === 'Enter') runAudit();
});
// Wire the Audit button (was onclick="runAudit()" in validator.html;
// inline handler removed to keep all event wiring in JS).
document.getElementById('auditBtn').addEventListener('click', runAudit);
// Audit source config — shared impl in SourceConfig (portal.js) so
// the decoder's By Word mirrors this exactly with prefix='lookup'.
(function() {
  var input = document.getElementById('auditSource');
  var wrap = input ? input.closest('.lookup-source') : null;
  SourceConfig.init({
    prefix: 'source',
    baseEl: input,
    defaultUrl: SOURCE_DEFAULT,
    placeholder: SOURCE_DEFAULT,
    resetEl: input && input.parentElement ? input.parentElement.querySelector('label') : null,
    modeEl: wrap ? wrap.querySelector('[data-source-mode-select]') : null,
    modeContainer: wrap
  });
})();
// Offline records — folder picker + shared cache. Audit's runAudit
// hits the cache before the network; Observatory drops also populate
// it automatically (per the for-loop in analyzeMeta).
OfflineRecords.bindUI();

// Delegated click handler for audit-link anchors — shared LinkClick
// owns the preventDefault/dataset boilerplate; callback handles the
// page-specific side effects (prefill + switch to Audit tab).
LinkClick.delegate('.audit-link', function(id) {
  document.getElementById('auditInput').value = id;
  showTab('cert');
  runAudit();  // load the clicked record (+ pushes a history step for back/forward)
});

// Snapshot the default .how help text so we can restore it after a
// "Fetching..." state resolves. The .how box is help-only now —
// errors live in the auditErrorHead + auditErrorBody slots; .how
// hides (via :has CSS) when head is non-empty.
var _auditHowDefault = null;
(function(){ var h = document.querySelector('#tab-cert .how'); if (h) _auditHowDefault = h.innerHTML; })();
function setHow(state, html) {
  var h = document.querySelector('#tab-cert .how');
  if (!h) return;
  if (state === 'default') h.innerHTML = _auditHowDefault || '';
  else h.innerHTML = html || '';
  // Any non-error state implicitly clears the error slots.
  if (state !== 'error') setAuditError('', '');
}
// Configure PanelError for the validator's tabs (shared helper in
// portal.js owns the DOM writes + cross-tab clearing). Observatory's
// metaError is a single-slot tab; Audit has the two-slot head/body.
// Image tab has its own console widget (imgConsole), not a plain
// error slot — it isn't routed through PanelError.
PanelError.configure({
  cert: { head: 'auditErrorHead', body: 'auditErrorBody' },
  meta: { body: 'metaError' }
});
function setAuditError(head, body) {
  PanelError.set('cert', head, body);
  // A bad audit result exits compact mode. Leaving the right sidebar
  // up with empty innards reads as "something rendered" when nothing
  // did — a fresh query that failed shouldn't inherit the prior
  // query's layout real estate. The dismiss animation also gives the
  // user an unmistakable signal that the previous cert is gone.
  if (head) {
    var out = document.getElementById('certResults');
    if (out) out.innerHTML = '';
    if (typeof hideResultsSidebar === 'function') hideResultsSidebar(true);
  }
}

function runAudit() {
  var input = document.getElementById('auditInput').value.trim();
  var out = document.getElementById('certResults');
  if (!input) return;
  clearOtherResults('cert');
  // Don't open the sidebar until we have a real record. A "Fetching..."
  // flash replaced by an inline error on fail leaves a blank sidebar
  // flash — keep the sidebar closed and show status inline in .how.
  out.innerHTML = '';
  setHow('loading', '<span style="color:#8a8a94;">Fetching...</span>');
  // The .how box above already shows "Fetching…" during the fetch.
  // No-op stopSpin kept for the terminal paths below (early return,
  // ok, fail) that call it — setHow('default') handles the actual
  // restore when the audit succeeds; fail() writes over .how via
  // setAuditError → hideResultsSidebar chain; mixed-content early
  // return writes its own error headline. Hence no button state to
  // manage here.
  function stopSpin() {}

  // Parse identifier through the shared codec grammar (any per-chain
  // prefix, not just mememage). URL inputs can have the id embedded
  // anywhere; bare inputs must match strictly end-to-end so junk like
  // "<prefix>-<hex>ff99bad" can't sneak past by silent truncation.
  var identifier = /^https?:\/\//.test(input)
    ? extractIdentifier(input)
    : normalizeIdentifier(input);
  if (!identifier) {
    setAuditError(
      'Invalid identifier.',
      'Expected <strong>&lt;prefix&gt;-&lt;hex&gt;</strong> (e.g. mememage-…), or a URL containing one.'
    );
    stopSpin();
    return;
  }

  // History step (back/forward traversal). Push a new entry for a fresh
  // identifier; skip when this run came from a popstate (else back/forward
  // would re-stack). Same value as current state → no-op (don't dupe).
  if (!_auditFromPop && (!history.state || history.state.auditId !== identifier)) {
    history.pushState({ auditId: identifier }, '', '#');
  }
  _auditFromPop = false;

  // Source config — single URL field with {id} templating. Expand
  // {id} before probing so "https://archive.org/download/{id}/" and
  // "https://yourhost.com/" share one code path.
  // Empty field → placeholder default (SOURCE_DEFAULT). The greyed
  // hint the user sees IS the value they get when they clear the field.
  var sourceEl = document.getElementById('auditSource');
  var base = (sourceEl && sourceEl.value.trim()) || SOURCE_DEFAULT;
  var expanded = base.replace(/\{id\}/g, identifier).replace(/\/+$/, '');
  var isArchiveOrg = /archive\.org/.test(base);
  var offlineMode = SourceConfig.getMode('source') === 'offline';

  // Mixed-content pre-check (online only) — https pages can't fetch
  // http resources. Browsers block silently.
  if (!offlineMode && location.protocol === 'https:' && /^http:\/\//i.test(base)) {
    out.innerHTML = '';
    setAuditError(
      'Mixed content blocked for ' + identifier,
      'Source is HTTP but page is HTTPS \u2014 browsers block that silently.<br>' +
      'Open file in a new tab to verify:<br>' +
      buildProbeLinks(base, identifier, null)
    );
    stopSpin();
    return;
  }

  function fail() {
    out.innerHTML = '';
    if (offlineMode) {
      setAuditError(
        'Not in the offline cache.',
        'Identifier <strong>' + identifier + '</strong> isn\u2019t among ' +
        OfflineRecords.count() + ' record(s) loaded. ' +
        'Load a different folder under <em>Source</em>, or switch to <em>Online</em>.'
      );
    } else {
      var probeHtml = buildProbeLinks(base, identifier, null);
      setAuditError(
        'Could not find record for ' + identifier,
        probeHtml
          ? 'Open file in a new tab to verify:<br>' + probeHtml
          : 'Self-hosting? Your server needs <code>Access-Control-Allow-Origin: *</code>.'
      );
    }
    stopSpin();
  }

  // Stamp the URL that actually produced the record onto _source, so
  // renderAudit can show a "Source" link. Spinner stops on terminal
  // success too.
  function ok(url) {
    return function(record) {
      if (record && typeof record === 'object') record._source = url;
      renderAudit(record, identifier, out);
      stopSpin();
    };
  }

  // Offline cache first — Observatory drops + folder-picker loads
  // populate OfflineRecords. If the identifier matches an already-
  // parsed record, skip the network entirely.
  var cached = OfflineRecords.get(identifier);
  if (cached) {
    renderAudit(cached, identifier, out);
    stopSpin();
    return;
  }

  // Offline mode miss — user explicitly picked Offline but the
  // identifier isn't in the cache. Bail out cleanly; no network.
  if (offlineMode) {
    fail();
    return;
  }

  // Probe the expanded URL for the canonical .soul / .json forms.
  // Current upload writes {identifier}.soul; older records on IA may
  // be at {identifier}.{hash}.soul — IA /metadata/ fallback covers
  // those when direct probes miss.
  var soulUrl = expanded + '/' + identifier + '.soul';
  var jsonUrl = expanded + '/' + identifier + '.json';
  function iaFallback() {
    // OPTIONAL CONVENIENCE PATH — only runs when the user pasted an
    // archive.org base URL and the direct .soul / .json probes missed.
    // Calls IA's /metadata/<identifier> resolver to discover the actual
    // filename (legacy records may be at {id}.{hash}.soul; new ones at
    // {id}.soul). Not load-bearing: the source field with {id}
    // templating is the canonical path. Non-IA hosts get a plain
    // fail() with probe-link guidance.
    if (!isArchiveOrg) { fail(); return; }
    var iaRoot = base.match(/^(https?:\/\/[^/]*archive\.org)/);
    iaRoot = iaRoot ? iaRoot[1] : 'https://archive.org';
    var resolvedDownload = null;
    fetch(iaRoot + '/metadata/' + identifier + '?t=' + Date.now(), {cache: 'no-store'})
      .then(function(r) { return r.ok ? r.json() : Promise.reject('not found'); })
      .then(function(meta) {
        if (!meta || !meta.files) return Promise.reject('no files');
        var soulFile = null;
        for (var i = 0; i < meta.files.length; i++) {
          var fn = meta.files[i].name;
          if (fn.endsWith('.json') || fn.endsWith('.soul')) { soulFile = fn; break; }
        }
        if (!soulFile) return Promise.reject('no soul file');
        resolvedDownload = iaRoot + '/download/' + identifier + '/' + soulFile;
        return fetch(resolvedDownload + '?t=' + Date.now(), {cache: 'no-store'});
      })
      .then(function(r) { return r.ok ? r.json() : Promise.reject('fetch failed'); })
      .then(function(record) { ok(resolvedDownload)(record); })
      .catch(fail);
  }

  fetch(soulUrl + '?t=' + Date.now(), {cache: 'no-store'})
    .then(function(r) { return r.ok ? r.json() : Promise.reject('soul'); })
    .then(ok(soulUrl))
    .catch(function() {
      fetch(jsonUrl + '?t=' + Date.now(), {cache: 'no-store'})
        .then(function(r) { return r.ok ? r.json() : Promise.reject('json'); })
        .then(ok(jsonUrl))
        .catch(iaFallback);
    });
}

function auditRow(label, value, cls) {
  // `.selectable` is the shared class recognized under .drag-scroll:
  // single-click selects the entire cell's content (user-select: all),
  // ready for Cmd+C. Same affordance as the cert's prompt/timestamp
  // fields so audit values don't feel like different material.
  //
  // escapeHtml on label + value: callers pass record fields straight
  // through (rec.creator_name, rec.identifier, rec.signature, etc.) and
  // a malicious record could ship script tags. Class names come from
  // a closed set of known constants, no escape needed.
  return '<div class="audit-row"><span class="audit-label">' + escapeHtml(label) + '</span><span class="audit-val selectable ' + (cls || '') + '">' + escapeHtml(value == null ? '' : value) + '</span></div>';
}

function auditSection(title, rows) {
  return '<div class="audit-section"><div class="audit-section-label">' + title + '</div>' + rows + '</div>';
}

// Top-level keys the structured audit sections already render. Everything else
// — a core soul's own fields (license, author, …), arbitrary/EXIF metadata — is
// shown generically by the "Soul Fields" section, so nothing is silently
// dropped just because it isn't a reference-chain field.
var _AUDIT_COVERED = {
  identifier:1, content_hash:1, hash_version:1, conceived:1, rendered:1,
  creator_name:1, signature:1, public_key:1, key_fingerprint:1,
  parent_id:1, constellation_name:1, constellation_index:1, constellation_hash:1,
  constellation_size:1, heart_star_id:1, age:1, chunks:1, decoder_hash:1,
  chain_visibility:1, outer_position:1, outer_total:1, luma_grid:1,
  origin:1, prompt:1, seed:1, steps:1, cfg:1, cfg_scale:1, guidance:1,
  model:1, unet:1, lora:1, loras:1, lora_strength:1, width:1, height:1,
  birth:1, machine_fingerprint:1, birth_traits:1, birth_temperament:1,
  rarity:1, rarity_score:1, song_name:1, thumbnail:1, about:1, mode:1,
  gps:1, gps_time_locked:1, gps_password_locked:1,
  encrypted_fields:1, encrypted_chunks:1
};

// Render any value (string / number / boolean / array / object) as a readable,
// length-capped string. Arrays and dicts are JSON; long strings truncate.
function _auditFieldValue(v) {
  if (v == null) return '';
  if (typeof v === 'string') return v.length > 240 ? v.slice(0, 240) + '…' : v;
  if (typeof v === 'number' || typeof v === 'boolean') return '' + v;
  try { var s = JSON.stringify(v); return s.length > 240 ? s.slice(0, 240) + '…' : s; }
  catch (e) { return '' + v; }
}

// One luma-grid channel (mean|min|max, a 1024-long 0-255 array) → a 32×32
// grayscale PNG data URL. The stored grid IS a low-res luminance image of the
// original; the Audit tab renders it that way instead of dumping ~4 KB of
// base64. Native 32px, CSS-upscaled pixelated so the tiles read as tiles.
function _lumaChannelURL(arr) {
  var c = document.createElement('canvas'); c.width = 32; c.height = 32;
  var x = c.getContext('2d'); var d = x.createImageData(32, 32);
  for (var i = 0; i < 1024; i++) { var v = arr[i], p = i * 4; d.data[p] = v; d.data[p + 1] = v; d.data[p + 2] = v; d.data[p + 3] = 255; }
  x.putImageData(d, 0, 0);
  return c.toDataURL('image/png');
}

// The last record the Audit tab rendered — so the unlock handler can re-render
// it decrypted without re-fetching.
var _auditRec = null, _auditId = null, _auditOut = null;

// Unlock a sealed soul from the Audit tab: decrypt with the typed password and
// re-render. Works for a dark-matter chain OR a core seal(password=…) soul —
// _decryptRecord keys off the encrypted_fields/encrypted_chunks envelopes, not
// chain_visibility. WITNESSED is unaffected (it hashes the sealed shell).
async function auditUnlock() {
  var pwEl = document.getElementById('auditUnlockPw');
  if (!pwEl || !_auditRec) return;
  var errEl = document.getElementById('auditUnlockErr');
  var unlocked = await _decryptRecord(_auditRec, pwEl.value);
  if (!unlocked) {
    if (errEl) errEl.textContent = 'Wrong password — could not decrypt.';
    return;
  }
  if (typeof chainDiscriminator === 'function') {
    _chainPasswords[chainDiscriminator(_auditRec)] = pwEl.value;
  }
  renderAudit(unlocked, _auditId, _auditOut);
}

function renderAudit(rec, identifier, out) {
  var html = '';
  _auditRec = rec; _auditId = identifier; _auditOut = out;   // for auditUnlock

  // === IDENTITY ===
  var idRows = '';
  idRows += auditRow('Identifier', rec.identifier || identifier, 'audit-info');
  idRows += auditRow('Content Hash', rec.content_hash || 'missing', rec.content_hash ? '' : 'audit-fail');
  idRows += auditRow('Hash Version', rec.hash_version || '?');
  idRows += auditRow('Conceived', rec.conceived || 'unknown');
  if (rec.rendered) idRows += auditRow('Rendered', rec.rendered);
  if (rec.creator_name) idRows += auditRow('Creator', rec.creator_name, 'audit-info');
  html += auditSection('Identity', idRows);

  // === CONTENT HASH VERIFICATION ===
  var hashRows = '';
  var storedHash = rec.content_hash;
  if (storedHash) {
    // Use the canonical computeContentHash (verify.js) so the Audit verdict is
    // IDENTICAL to the WITNESSED badge: it dispatches on hash_version (V1
    // positive list OR the "open" everything-except model), excludes `_`-prefix
    // decoder scratch, and reads the dark-matter sealed shell. The old inline
    // path only knew the V1 positive list, so an "open" core soul always
    // mismatched.
    computeContentHash(_sealedShellFor(rec)).then(function(computed) {
      var hashEl = document.getElementById('auditHashResult');
      if (!hashEl) return;
      if (computed && computed === storedHash) {
        hashEl.innerHTML = auditRow('Computed', computed, 'audit-pass') + auditRow('Verdict', 'MATCH \u2014 record is internally consistent', 'audit-pass');
      } else {
        hashEl.innerHTML = auditRow('Computed', computed || '\u2014', 'audit-fail') + auditRow('Stored', storedHash, 'audit-fail') + auditRow('Verdict', 'MISMATCH \u2014 record may be altered', 'audit-fail');
      }
    });
    hashRows += auditRow('Stored Hash', storedHash);
    hashRows += '<div id="auditHashResult">' + auditRow('Computing...', '', 'audit-dim') + '</div>';
  } else {
    hashRows += auditRow('Status', 'No content hash in record', 'audit-warn');
  }
  html += auditSection('Content Hash', hashRows);

  // === SIGNATURE ===
  var sigRows = '';
  if (rec.signature) {
    sigRows += auditRow('Signature', rec.signature.slice(0, 16) + '...' + rec.signature.slice(-8));
    sigRows += auditRow('Public Key', rec.public_key || 'missing', rec.public_key ? '' : 'audit-fail');
    sigRows += auditRow('Fingerprint', rec.key_fingerprint || 'missing', rec.key_fingerprint ? 'audit-info' : 'audit-fail');
    if (rec.creator_name) sigRows += auditRow('Creator (TOFU)', rec.creator_name, 'audit-info');
    // Real Ed25519 verification — deferred update mirrors the content-hash
    // pattern above. verifySignature returns true | false | null (browser
    // can't verify Ed25519, e.g., very old Safari).
    sigRows += '<div id="auditSigResult">' + auditRow('Verifying...', '', 'audit-dim') + '</div>';
    var sigId = rec.identifier || identifier;
    var sigHash = rec.content_hash || '';
    _thumbnailHashForSig(rec).then(function(thumbHash) {
      return verifySignature(sigId, sigHash, rec.signature, rec.public_key, thumbHash);
    }).then(function(valid) {
      var sigEl = document.getElementById('auditSigResult');
      if (!sigEl) return;
      if (valid === true) {
        sigEl.innerHTML = auditRow('Verdict', 'VALID \u2014 signature verifies against public key', 'audit-pass');
      } else if (valid === false) {
        sigEl.innerHTML = auditRow('Verdict', 'INVALID \u2014 signature does not verify (forged or tampered)', 'audit-fail');
      } else {
        sigEl.innerHTML = auditRow('Verdict', 'Inconclusive \u2014 browser cannot verify Ed25519', 'audit-warn');
      }
    });
  } else {
    sigRows += auditRow('Status', 'UNSIGNED \u2014 no Ed25519 signature', 'audit-warn');
    sigRows += auditRow('Risk', 'Thumbnail and non-hashed fields are unprotected', 'audit-warn');
  }
  html += auditSection('Signature (Ed25519)', sigRows);

  // === SEALED === a soul with encrypted fields (dark-matter chain OR a core
  // seal(password=…)). WITNESSED is already confirmed above against the sealed
  // shell — no password needed; the password only DECRYPTS the private fields,
  // locally. Sent nowhere, stored nowhere.
  if (_isSealed(rec) && !rec._unlocked) {
    var sealRows =
        '<div class="audit-row"><span class="audit-val" style="color:#8a8a9a;font-size:0.66rem;line-height:1.5;">'
      + 'Private fields are encrypted. The record still WITNESSES without the password (the hash covers the sealed shell). Enter the password to decrypt and reveal them locally — it is never sent or stored.</span></div>'
      + '<div class="audit-row"><span class="audit-label">Password</span><span class="audit-val" style="display:flex;gap:0.3rem;align-items:center;flex-wrap:wrap;">'
      + '<input id="auditUnlockPw" type="password" placeholder="Creator password" style="flex:1;min-width:140px;background:#0a0a12;color:#c8c8d4;border:1px solid #2a2a40;border-radius:4px;padding:0.25rem 0.5rem;font-size:0.72rem;font-family:inherit;" onkeydown="if(event.key===\'Enter\')auditUnlock()">'
      + '<button onclick="auditUnlock()" style="background:#2a2a40;color:#c8c8d4;border:1px solid #3a3a55;border-radius:4px;padding:0.25rem 0.7rem;font-size:0.72rem;font-family:inherit;cursor:pointer;">Unlock</button>'
      + '<span id="auditUnlockErr" style="color:#e06c6c;font-size:0.66rem;"></span></span></div>';
    html += auditSection('Sealed', sealRows);
  } else if (rec._unlocked) {
    html += auditSection('Sealed', auditRow('Status', 'Unlocked — private fields decrypted locally', 'audit-pass'));
  }

  // === CHAIN POSITION ===
  var chainRows = '';
  if (rec.parent_id) {
    // Inline link — auditRow's signature escapes its value, so build
    // the row by hand to keep the audit-link wrapping the identifier.
    var _pe = escapeHtml(rec.parent_id);
    chainRows += '<div class="audit-row"><span class="audit-label">Parent</span><span class="audit-val selectable"><a href="#" class="audit-link" data-id="' + _pe + '" style="color:inherit;text-decoration:underline;text-decoration-color:rgba(255,255,255,0.2);cursor:pointer;">' + _pe + '</a></span></div>';
  } else {
    chainRows += auditRow('Parent', 'genesis (no parent)', 'audit-info');
  }
  if (rec.constellation_name) {
    chainRows += auditRow('Constellation', rec.constellation_name, 'audit-info');
    // V1 records carry constellation_index (0-23); map to the Greek Bayer
    // letter (\u03b1-\u03c9), matching the cert renderer's full 24-letter table. (Was
    // truncated at \u03bc/12 \u2014 stars 13-24 on a large chain rendered "?".)
    var _BAYER = '\u03b1\u03b2\u03b3\u03b4\u03b5\u03b6\u03b7\u03b8\u03b9\u03ba\u03bb\u03bc'
               + '\u03bd\u03be\u03bf\u03c0\u03c1\u03c3\u03c4\u03c5\u03c6\u03c7\u03c8\u03c9';
    var _ci = rec.constellation_index;
    var _starLabel = (typeof _ci === 'number' && _ci >= 0 && _ci < _BAYER.length)
      ? _BAYER[_ci] + ' (' + _ci + ')'
      : '?';
    chainRows += auditRow('Star', _starLabel);
    if (rec.heart_star_id && rec.heart_star_id !== rec.identifier) {
      var _he = escapeHtml(rec.heart_star_id);
      chainRows += '<div class="audit-row"><span class="audit-label">Heart Star</span><span class="audit-val selectable"><a href="#" class="audit-link" data-id="' + _he + '" style="color:inherit;text-decoration:underline;text-decoration-color:rgba(255,255,255,0.2);cursor:pointer;">' + _he + '</a></span></div>';
    } else {
      chainRows += auditRow('Heart Star', rec.heart_star_id || '?');
    }
    var isHeart = rec.heart_star_id === rec.identifier;
    chainRows += auditRow('Role', isHeart ? '\u03B1 Heart Star (first in constellation)' : 'Sibling', isHeart ? 'audit-info' : '');
  } else {
    chainRows += auditRow('Constellation', 'Not assigned', 'audit-dim');
  }
  html += auditSection('Chain Position', chainRows);

  // === CYCLE INTEGRITY ===
  // One row per cycling (indexed) layer, then any pinned single-chunk roles,
  // plus Age + decoder_hash + chain_visibility. Every label comes from
  // roleMeta (title-cased from the role name).
  var cycleRows = '';
  var auAgeName = AgeNames.forRecord(rec);
  if (auAgeName) cycleRows += auditRow('Age', auAgeName);
  if (rec.chunks && typeof rec.chunks === 'object') {
    Object.keys(rec.chunks).sort().forEach(function(role) {
      var e = rec.chunks[role];
      if (!e || typeof e !== 'object') return;
      var lbl = roleMeta(role).label;
      if (e.index !== undefined && e.total !== undefined) {
        cycleRows += auditRow(lbl + ' Chunk', (e.index + 1) + ' of ' + (e.total || '?'));
        if (e.version) cycleRows += auditRow(lbl + ' Version', e.version);
      } else if (e.data !== undefined) {
        cycleRows += auditRow(lbl, 'pinned');
      }
    });
  }
  if (rec.decoder_hash) cycleRows += auditRow('Decoder Hash', rec.decoder_hash);
  if (rec.chain_visibility !== undefined && rec.chain_visibility !== null) {
    cycleRows += auditRow('Visibility', _visName(rec.chain_visibility) || String(rec.chain_visibility), _isDark(rec.chain_visibility) ? 'audit-warn' : '');
  }
  if (cycleRows) html += auditSection('Cycle Position', cycleRows);

  // === GENERATION === (reference-chain origin params \u2014 only the
  // ones actually present. A core soul carries none of these; its own fields
  // show under "Soul Fields" below, so we never render a wall of "?".)
  var genRows = '';
  var _recOrigin = rec.origin || {};
  var _recPrompt = _recOrigin.prompt || rec.prompt;   // V1 reads from origin
  if (_recPrompt) genRows += auditRow('Prompt', _recPrompt);
  // V1 reads from rec.origin; fall back to flat fields for legacy records.
  var _ro = rec.origin || {};
  var _gSeed = _ro.seed !== undefined ? _ro.seed : rec.seed;
  var _gSteps = _ro.steps !== undefined ? _ro.steps : rec.steps;
  var _gCfg = _ro.cfg_scale !== undefined ? _ro.cfg_scale : (rec.cfg_scale !== undefined ? rec.cfg_scale : rec.cfg);
  var _gGuide = _ro.guidance !== undefined ? _ro.guidance : rec.guidance;
  var _gModel = _ro.model || rec.model || rec.unet;
  if (_gSeed != null) genRows += auditRow('Seed', _gSeed);
  if (rec.width || rec.height) genRows += auditRow('Size', (rec.width || '?') + ' \u00d7 ' + (rec.height || '?'));
  if (_gModel) genRows += auditRow('Model', _gModel);
  if (_gSteps != null || _gCfg != null || _gGuide != null) genRows += auditRow('Steps / CFG / Guidance', (_gSteps != null ? _gSteps : '?') + ' / ' + (_gCfg != null ? _gCfg : '?') + ' / ' + (_gGuide != null ? _gGuide : '?'));
  // LoRAs — modern format is the plural `loras` list ([name, weight] pairs);
  // older records used singular `lora` + `lora_strength`.
  var _loraSummary = '';
  if (Array.isArray(_ro.loras) && _ro.loras.length) {
    _loraSummary = _ro.loras.map(function(L) {
      if (Array.isArray(L)) return (L[1] != null) ? (L[0] + ' ×' + L[1]) : ('' + L[0]);
      if (L && typeof L === 'object') {
        var n = L.name || L.lora || L.file;
        var w = (L.strength !== undefined) ? L.strength : L.weight;
        return (w != null) ? (n + ' ×' + w) : ('' + n);
      }
      return '' + L;
    }).filter(Boolean).join(', ');
  } else if (_ro.lora) {
    _loraSummary = (_ro.lora_strength != null) ? (_ro.lora + ' ×' + _ro.lora_strength) : ('' + _ro.lora);
  }
  if (_loraSummary) genRows += auditRow('LoRA', _loraSummary);
  if (genRows) html += auditSection('Generation', genRows);

  // === CELESTIAL ===
  var birth = rec.birth || {};
  var celRows = '';
  if (birth.sun) celRows += auditRow('Sun', formatPosition(birth.sun));
  if (birth.moon) celRows += auditRow('Moon', formatPosition(birth.moon));
  if (birth.moon_phase) celRows += auditRow('Phase', formatMoonPhase(birth.moon_phase));
  if (birth.mercury) celRows += auditRow('Mercury', formatPosition(birth.mercury));
  if (birth.venus) celRows += auditRow('Venus', formatPosition(birth.venus));
  if (birth.mars) celRows += auditRow('Mars', formatPosition(birth.mars));
  if (birth.jupiter) celRows += auditRow('Jupiter', formatPosition(birth.jupiter));
  if (birth.saturn) celRows += auditRow('Saturn', formatPosition(birth.saturn));
  if (rec.constellation_hash) celRows += auditRow('Constellation Hash', rec.constellation_hash);
  if (celRows) html += auditSection('Celestial', celRows);

  // === MACHINE === (reference-chain machine reading — only when present)
  var machRows = '';
  if (rec.machine_fingerprint) machRows += auditRow('Fingerprint', rec.machine_fingerprint);
  var recBirth = (typeof BirthText !== 'undefined' && rec.birth_traits)
    ? BirthText.read(rec.birth_traits) : null;
  if ((recBirth && recBirth.temperament) || rec.birth_temperament)
  machRows += auditRow('Temperament',
    (recBirth && recBirth.temperament) || rec.birth_temperament || '?');
  if (rec.birth_traits && rec.birth_traits.length && typeof BIRTH_TRAITS !== 'undefined') {
    var traitHtml = '';
    for (var bti = 0; bti < rec.birth_traits.length; bti++) {
      var btName = (typeof BirthText !== 'undefined') ? BirthText.name(rec.birth_traits[bti]) : null;
      var btDef = btName ? BIRTH_TRAITS[btName] : null;
      if (btDef && btName) {
        traitHtml += '<div style="display:flex;align-items:center;gap:6px;margin:3px 0;">';
        // btName resolved from trait code via trusted lookup; values
        // are constants. Escape on principle.
        traitHtml += '<img src="' + assetUrl('img/traits/' + btName + '.png') + '" style="width:20px;height:20px;object-fit:contain;" alt="' + escapeHtml(btDef.name) + '">';
        traitHtml += '<span style="font-size:0.68rem;color:#c0c0cc;">' + escapeHtml(btDef.name) + ' \u2014 <span style="color:#8a8a94;">' + escapeHtml(btDef.desc) + '</span></span>';
        traitHtml += '</div>';
      }
    }
    if (traitHtml) machRows += '<div class="audit-row" style="flex-direction:column;align-items:flex-start;gap:2px;"><span class="audit-label">Traits</span>' + traitHtml + '</div>';
  } else if (rec.birth_traits) {
    machRows += auditRow('Traits', rec.birth_traits.join(' \u00b7 '));
  }
  var _recRs = (typeof RarityScore !== 'undefined')
    ? RarityScore.fromRecord(rec) : (rec.rarity_score);
  if (typeof _recRs === 'number') machRows += auditRow('Rarity', _recRs + '');
  if (machRows) html += auditSection('Machine', machRows);

  // === SONG FORENSICS ===
  var songRows = '';
  // Song name
  var songName = rec.song_name || (typeof CosmicAudio !== 'undefined' ? CosmicAudio.songName(rec.content_hash || '') : null);
  if (songName) songRows += auditRow('Song Name', songName, 'audit-info');

  // Derive musical properties from the record. birth.sun is a V1 dict
  // {sign:int, deg:float} on new records, legacy "Aries 24.3°" string
  // on old ones — signName handles both.
  var sign = birth.sun ? signName(birth.sun) : '?';
  var FIRE = {Aries:1,Leo:1,Sagittarius:1};
  var WATER = {Cancer:1,Scorpio:1,Pisces:1};
  var EARTH = {Taurus:1,Virgo:1,Capricorn:1};
  var element = FIRE[sign] ? 'Fire' : WATER[sign] ? 'Water' : EARTH[sign] ? 'Earth' : 'Air';
  var SCALE_NAMES = {Fire:'Dorian',Water:'Aeolian',Earth:'Mixolydian',Air:'Lydian'};
  var SCALE_INTERVALS = {
    Fire:  [0, 2, 3, 5, 7, 9, 10],
    Water: [0, 2, 3, 5, 7, 8, 10],
    Earth: [0, 2, 4, 5, 7, 9, 10],
    Air:   [0, 2, 4, 6, 7, 9, 11]
  };
  var NOTE_NAMES = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B'];
  var SIGN_FREQ = {
    Aries:55,Taurus:61.74,Gemini:65.41,Cancer:73.42,
    Leo:82.41,Virgo:87.31,Libra:98,Scorpio:55,
    Sagittarius:61.74,Capricorn:65.41,Aquarius:73.42,Pisces:82.41
  };
  var baseFreq = SIGN_FREQ[sign] || 55;

  // Key offset from hash (same algorithm as cosmic-audio.js)
  var hash = rec.content_hash || '';
  var keyOffset = 0;
  if (hash) {
    var kSeed = 0;
    for (var ki = 0; ki < hash.length; ki++) kSeed = (kSeed * 31 + hash.charCodeAt(ki)) & 0x7FFFFFFF;
    kSeed = (kSeed * 1103515245 + 12345) & 0x7FFFFFFF;
    keyOffset = Math.floor((kSeed / 0x7FFFFFFF) * 6);
  }
  var rootFreq = baseFreq * Math.pow(2, keyOffset / 12);
  var rootNote = NOTE_NAMES[Math.round(12 * Math.log2(rootFreq / 16.3516)) % 12];
  var rootOctave = Math.floor(Math.log2(rootFreq / 16.3516));

  songRows += auditRow('Element', element);
  songRows += auditRow('Modal Scale', SCALE_NAMES[element] + ' mode');
  songRows += auditRow('Scale Intervals', SCALE_INTERVALS[element].join(' '));

  // Build note names for the scale
  var scaleNotes = SCALE_INTERVALS[element].map(function(interval) {
    return NOTE_NAMES[(Math.round(12 * Math.log2(baseFreq / 16.3516)) + interval + keyOffset) % 12];
  });
  // Clickable scale notes — each note plays a piano tone. Seven
  // diatonic notes plus an 8th: the tonic an octave up, so melodies
  // can resolve home (Twinkle-style ascents, hymn cadences) without
  // falling off the top of the range. The 8th is the first note
  // doubled — same pitch class, double frequency.
  var noteHtml = scaleNotes.map(function(note, idx) {
    var freq = baseFreq * Math.pow(2, (SCALE_INTERVALS[element][idx] + keyOffset) / 12) * 4; // speaker octave
    return '<span class="audit-note" data-scale-note data-freq="' + freq.toFixed(2) + '" onclick="playAuditNote(this)" title="' + freq.toFixed(1) + ' Hz">' + note + '</span>';
  }).join(' ');
  var tonicFreq = baseFreq * Math.pow(2, (SCALE_INTERVALS[element][0] + keyOffset) / 12) * 4;
  var octaveFreq = tonicFreq * 2;
  noteHtml += ' <span class="audit-note audit-note-octave" data-scale-note data-freq="' + octaveFreq.toFixed(2) + '" onclick="playAuditNote(this)" title="Octave up — ' + octaveFreq.toFixed(1) + ' Hz">' + scaleNotes[0] + '\u2032</span>';
  // Play all button (chord stays 7-note diatonic; the octave tonic is
  // redundant for a stacked-chord sound and would muddy the blend).
  var allFreqs = SCALE_INTERVALS[element].map(function(interval) {
    return (baseFreq * Math.pow(2, (interval + keyOffset) / 12) * 4).toFixed(2);
  }).join(',');
  songRows += '<div class="audit-row"><span class="audit-label">Scale Notes</span><span class="audit-val"><span class="audit-rec-btn" id="auditRecBtn" onclick="toggleRecord()" title="Record (keys 1-8, 9=chord)">&#9679;</span> ' + noteHtml + ' <span class="audit-note" data-freqs="' + allFreqs + '" onclick="playAuditChord(this)" title="Play chord (key 9)" style="margin-left:4px;color:#facc15;">&#9835;</span> <span class="audit-play-btn disabled" id="auditPlayBtn" onclick="togglePlayback()" title="Play / stop recording">&#9655;</span></span></div>';
  songRows += auditRow('Base Root', sign + ' \u2192 ' + baseFreq + ' Hz');
  songRows += auditRow('Key Offset', '+' + keyOffset + ' semitones');
  songRows += '<div class="audit-row"><span class="audit-label">Actual Root</span><span class="audit-val"><span class="audit-note" data-freq="' + (rootFreq*4).toFixed(2) + '" onclick="playAuditNote(this)" title="Click to hear">' + rootNote + rootOctave + ' (' + rootFreq.toFixed(1) + ' Hz)</span></span></div>';

  // Speaker vs cosmic frequencies
  var speakerRoot = rootFreq * 4;
  var speakerNote = NOTE_NAMES[Math.round(12 * Math.log2(speakerRoot / 16.3516)) % 12];
  var speakerOctave = Math.floor(Math.log2(speakerRoot / 16.3516));
  songRows += '<div class="audit-row"><span class="audit-label">Speaker Mode</span><span class="audit-val"><span class="audit-note" data-freq="' + speakerRoot.toFixed(2) + '" onclick="playAuditNote(this)" title="Click to hear">' + speakerNote + speakerOctave + ' (' + speakerRoot.toFixed(1) + ' Hz)</span></span></div>';
  songRows += '<div class="audit-row"><span class="audit-label">Cosmic Mode</span><span class="audit-val"><span class="audit-note" data-freq="' + rootFreq.toFixed(2) + '" onclick="playAuditNote(this)" title="Click to hear">' + rootNote + rootOctave + ' (' + rootFreq.toFixed(1) + ' Hz)</span> + sub ' + (rootFreq/2).toFixed(1) + ' Hz</span></div>';

  // Temperament influence on audio — reconstruct from trait codes if
  // the persisted string isn't there (V1 records).
  var recBirth2 = (typeof BirthText !== 'undefined' && rec.birth_traits)
    ? BirthText.read(rec.birth_traits) : null;
  var temp = (recBirth2 && recBirth2.temperament) || rec.birth_temperament || '';
  var tempWord = temp.match(/^A\s+(.+?)\s+birth$/i);
  var audioTemp = tempWord ? tempWord[1] : temp;
  var modDesc = 'default';
  if (/serene|clean|perfect/.test(audioTemp)) modDesc = 'minimal modulation, very quiet noise';
  else if (/turbulent|fever/.test(audioTemp)) modDesc = 'fast modulation, prominent noise';
  else if (/electric|knotted/.test(audioTemp)) modDesc = 'medium modulation, wide detune';
  else modDesc = 'moderate modulation';
  songRows += auditRow('Temperament Effect', modDesc);

  // Moon influence — V1 stores illumination as 0..1 in birth.moon_phase.illum.
  // Legacy "Full Moon (98.4%)" string still parses via moonIllumPct.
  var moonBright = birth.moon_phase ? (moonIllumPct(birth.moon_phase) / 100) : 0.5;
  songRows += auditRow('Moon Brightness', (moonBright * 100).toFixed(0) + '% \u2192 filter cutoff & dust density');

  // Song forensics is a reference-chain showcase (modal scale derived from the
  // sky + hash). Skip it for a core soul that carries no celestial data.
  if (songRows && (birth.sun || rec.song_name)) html += auditSection('Song Forensics', songRows);

  // === SOUL FIELDS === (the record's own data, not in any section above \u2014
  // a core/open soul's custom fields, arbitrary or EXIF-style metadata, etc.)
  var otherRows = '';
  Object.keys(rec).sort().forEach(function(k) {
    if (_AUDIT_COVERED[k] || k.charAt(0) === '_') return;
    otherRows += auditRow(k, _auditFieldValue(rec[k]));
  });
  if (otherRows) html += auditSection('Soul Fields', otherRows);

  // === PORTRAIT (thumbnail — the record's face, rendered as the image it is) ===
  // Gate on data:image/ like verify.js does: never load a remote thumbnail, so
  // a hostile soul can't beacon the viewer's IP. escapeHtml the src as defense
  // in depth (valid base64 is untouched; stray <>&"' can't break the attribute).
  if (rec.thumbnail && typeof rec.thumbnail === 'string' && /^data:image\//.test(rec.thumbnail)) {
    var tnRows = '';
    tnRows += '<div style="padding:0.2rem 0 0.5rem;">'
      + '<div style="font-size:0.62rem;color:#8a8a9a;margin-bottom:0.45rem;line-height:1.5;">'
      + 'Stored portrait. EMBODIED’s dHash reference, sha256-bound into the signature. Swap it and AUTHENTICATED breaks.</div>'
      + '<img src="' + escapeHtml(rec.thumbnail) + '" alt="portrait" '
      + 'style="max-width:180px;max-height:180px;border-radius:6px;border:1px solid rgba(255,255,255,0.12);display:block;"/></div>';
    tnRows += auditRow('Size', rec.thumbnail.length + ' chars base64');
    html += auditSection('Portrait', tnRows);
  }

  // === LUMA GRID (EMBODIED tamper reference, rendered as the image it is) ===
  if (rec.luma_grid) {
    var _lg = (typeof decodeStoredGrid === 'function') ? decodeStoredGrid(rec.luma_grid) : null;
    var lgRows = '';
    if (_lg) {
      var _flat = 0; for (var _f = 0; _f < 1024; _f++) if (_lg.flat[_f]) _flat++;
      function _lgImg(arr, lbl) {
        return '<div style="text-align:center;">'
          + '<img src="' + _lumaChannelURL(arr) + '" width="104" height="104" alt="luma ' + lbl + '" '
          + 'style="image-rendering:pixelated;border-radius:4px;border:1px solid rgba(255,255,255,0.12);display:block;"/>'
          + '<div style="font-size:0.58rem;color:#8a8a9a;margin-top:3px;">' + lbl + '</div></div>';
      }
      lgRows += '<div style="padding:0.2rem 0 0.5rem;">'
        + '<div style="font-size:0.62rem;color:#8a8a9a;margin-bottom:0.45rem;line-height:1.5;">'
        + 'Full-frame per-tile luminance, 32 × 32. EMBODIED’s tamper reference: each tile holds mean / min / max; a local edit shifts a tile. WITNESSED binds it into the content hash.</div>'
        + '<div style="display:flex;gap:0.7rem;flex-wrap:wrap;">' + _lgImg(_lg.mean, 'mean') + _lgImg(_lg.min, 'min') + _lgImg(_lg.max, 'max') + '</div></div>';
      lgRows += auditRow('Tiles', '1024 (32 × 32) — ' + _flat + ' smooth (min/max-checked)');
      lgRows += auditRow('Format', 'v2 full-frame · ' + rec.luma_grid.length + ' chars base64');
    } else {
      lgRows += auditRow('Luma grid', 'Legacy or unversioned format — EMBODIED falls back to dHash-only', 'audit-dim');
    }
    html += auditSection('Luma Grid', lgRows);
  }

  // === FIELD COMPLETENESS ===
  var totalKeys = Object.keys(rec).length;
  var fieldRows = '';
  fieldRows += auditRow('Total Fields', totalKeys);
  if (rec.hash_version === 'open') {
    // Open souls have no canonical schema \u2014 every field IS the soul (hashed
    // except the circular pair). There's nothing to be "missing".
    fieldRows += auditRow('Schema', 'open \u2014 every field is yours, all hashed', 'audit-info');
  } else {
    // Reference-chain V1 expectations. origin (free-form dict) replaces flat
    // prompt/seed/etc.; birth_temperament is derived at display; rarity_score
    // is derived from the rarity dict.
    var expected = ['identifier', 'content_hash', 'conceived', 'origin', 'width', 'height', 'birth', 'rarity', 'birth_traits', 'machine_fingerprint'];
    if (rec.parent_id) expected.push('parent_id');  // genesis has no parent
    var missing = expected.filter(function(k) { return rec[k] === undefined || rec[k] === null; });
    fieldRows += auditRow('Expected Core', expected.length + ' fields');
    if (missing.length === 0) {
      fieldRows += auditRow('Missing', 'None \u2014 all core fields present', 'audit-pass');
    } else {
      fieldRows += auditRow('Missing', missing.join(', '), 'audit-warn');
    }
    if (rec.thumbnail) fieldRows += auditRow('Thumbnail', 'Present (' + rec.thumbnail.length + ' chars)');
    else fieldRows += auditRow('Thumbnail', 'Missing', 'audit-dim');
  }
  if (rec.song_name) fieldRows += auditRow('Song', rec.song_name);
  html += auditSection('Field Completeness', fieldRows);

  // === DISTRIBUTION === removed: souls are surface-agnostic and no
  // longer carry a mirror list. The primary `url` (same as the bar
  // pixel-encodes) is shown in the Links section below for the "where
  // this came from" pointer; discovery of additional mirrors is an
  // operational concern handled outside the artifact.

  // === SOURCE ===
  var linkRows = '';
  // Source link — whichever URL actually produced the record. For IA
  // items we swap the /download/ path for /details/ (nicer landing),
  // otherwise we link to the raw source URL so self-hosters can open
  // their own file. Falls back to the /details/ convention if _source
  // is missing (older records fetched before stamping was added).
  var rawSource = rec._source || '';
  var sourceHref;
  var sourceDisplay;
  var isLocal = /^local:/i.test(rawSource);
  var iaMatch = rawSource.match(/^(https?:\/\/[^/]*archive\.org)\/download\/([^/]+)/);
  if (isLocal) {
    // Locally-loaded record (Observatory drop or folder picker) — no
    // clickable URL. Render as plain text with "Local file" label.
    sourceHref = null;
    sourceDisplay = rawSource;
  } else if (iaMatch) {
    sourceHref = iaMatch[1] + '/details/' + iaMatch[2];
    sourceDisplay = 'archive.org/details/' + iaMatch[2];
  } else if (rawSource) {
    sourceHref = rawSource;
    try { sourceDisplay = new URL(rawSource).host + new URL(rawSource).pathname; }
    catch (e) { sourceDisplay = rawSource; }
  } else {
    sourceHref = 'https://archive.org/details/' + (rec.identifier || identifier);
    sourceDisplay = 'archive.org/details/' + (rec.identifier || identifier);
  }
  if (sourceHref) {
    linkRows += '<div class="audit-row"><span class="audit-label">Fetched from</span><a href="' + sourceHref + '" target="_blank" rel="noopener" class="audit-val audit-info" style="text-decoration:none;word-break:break-all;">' + sourceDisplay + ' \u2192</a></div>';
  } else {
    linkRows += '<div class="audit-row"><span class="audit-label">Fetched from</span><span class="audit-val audit-dim" style="word-break:break-all;">' + sourceDisplay + '</span></div>';
  }
  html += auditSection('Source', linkRows);

  out.innerHTML = html;
  // Success — show sidebar and restore .how to its default help text
  document.getElementById('imgResults').style.display = 'none';
  out.style.display = '';
  setHow('default');
  showResultsSidebar();
}

// === Piano note playback for audit ===
var _auditCtx = null;
function playAuditNote(el) {
  var freq = parseFloat(el.dataset.freq);
  if (!freq) return;

  // Capture into the recording buffer if we're rolling. Previously
  // only keyboard (keys 1-7) appended here; mouse clicks played sound
  // but left _recorded empty, which kept the play button disabled
  // forever. Derive noteIdx from the DOM order of scale notes in the
  // current audit result.
  if (_recording) {
    var notes = document.querySelectorAll('#certResults .audit-note[data-scale-note]');
    var idx = Array.prototype.indexOf.call(notes, el);
    if (idx >= 0) _recorded.push({ noteIdx: idx, time: performance.now() - _recStart });
  }

  if (!_auditCtx) _auditCtx = new (window.AudioContext || window.webkitAudioContext)();
  _auditCtx.resume();

  var ctx = _auditCtx;
  var now = ctx.currentTime;
  var dur = 2.5;
  var vol = 0.12;

  // Piano partials
  var partials = [
    {ratio: 1, amp: 1.0, decay: 1.0},
    {ratio: 2.0, amp: 0.3, decay: 0.6},
    {ratio: 3.0, amp: 0.1, decay: 0.3},
    {ratio: 4.0, amp: 0.05, decay: 0.15}
  ];

  for (var i = 0; i < partials.length; i++) {
    var p = partials[i];
    var osc = ctx.createOscillator();
    osc.type = 'sine';
    osc.frequency.value = freq * p.ratio;
    var env = ctx.createGain();
    var pVol = vol * p.amp;
    var pDur = dur * p.decay;
    env.gain.setValueAtTime(0, now);
    env.gain.linearRampToValueAtTime(pVol, now + 0.005);
    env.gain.linearRampToValueAtTime(pVol * 0.4, now + 0.08);
    env.gain.exponentialRampToValueAtTime(0.0001, now + pDur);
    osc.connect(env);
    env.connect(ctx.destination);
    osc.start(now);
    osc.stop(now + pDur + 0.1);
  }

  // Hammer noise
  var nLen = Math.floor(ctx.sampleRate * 0.04);
  var nBuf = ctx.createBuffer(1, nLen, ctx.sampleRate);
  var nd = nBuf.getChannelData(0);
  for (var j = 0; j < nLen; j++) nd[j] = (Math.random() * 2 - 1) * 0.3;
  var nSrc = ctx.createBufferSource();
  nSrc.buffer = nBuf;
  var nFilt = ctx.createBiquadFilter();
  nFilt.type = 'highpass'; nFilt.frequency.value = freq * 2; nFilt.Q.value = 2;
  var nEnv = ctx.createGain();
  nEnv.gain.setValueAtTime(vol * 0.2, now);
  nEnv.gain.exponentialRampToValueAtTime(0.0001, now + 0.03);
  nSrc.connect(nFilt); nFilt.connect(nEnv); nEnv.connect(ctx.destination);
  nSrc.start(now); nSrc.stop(now + 0.05);

  // Visual feedback
  el.classList.add('ringing');
  setTimeout(function() { el.classList.remove('ringing'); }, 1500);
}

function playAuditChord(el) {
  var freqs = el.dataset.freqs.split(',').map(parseFloat);
  if (!freqs.length) return;

  // Capture chord into recording buffer on mouse click too (not just
  // key 9). noteIdx=-1 is the sentinel for chord, same as the keydown
  // path uses.
  if (_recording) {
    _recorded.push({ noteIdx: -1, time: performance.now() - _recStart, chord: true });
  }

  if (!_auditCtx) _auditCtx = new (window.AudioContext || window.webkitAudioContext)();
  _auditCtx.resume();

  var ctx = _auditCtx;
  var now = ctx.currentTime;

  for (var i = 0; i < freqs.length; i++) {
    var freq = freqs[i];
    var when = now + i * 0.03; // slight stagger for natural piano feel
    var vol = 0.07;
    var dur = 4;

    var osc = ctx.createOscillator();
    osc.type = 'sine';
    osc.frequency.value = freq;
    var env = ctx.createGain();
    env.gain.setValueAtTime(0, when);
    env.gain.linearRampToValueAtTime(vol, when + 0.005);
    env.gain.linearRampToValueAtTime(vol * 0.35, when + 0.08);
    env.gain.exponentialRampToValueAtTime(0.0001, when + dur);
    osc.connect(env); env.connect(ctx.destination);
    osc.start(when); osc.stop(when + dur + 0.1);

    // Soft 2nd partial
    var osc2 = ctx.createOscillator();
    osc2.type = 'sine';
    osc2.frequency.value = freq * 2;
    var env2 = ctx.createGain();
    env2.gain.setValueAtTime(0, when);
    env2.gain.linearRampToValueAtTime(vol * 0.2, when + 0.005);
    env2.gain.exponentialRampToValueAtTime(0.0001, when + dur * 0.5);
    osc2.connect(env2); env2.connect(ctx.destination);
    osc2.start(when); osc2.stop(when + dur * 0.5 + 0.1);
  }

  // Light up all notes
  var row = el.parentElement;
  var notes = row.querySelectorAll('.audit-note');
  for (var n = 0; n < notes.length; n++) notes[n].classList.add('ringing');
  setTimeout(function() {
    for (var n = 0; n < notes.length; n++) notes[n].classList.remove('ringing');
  }, 3000);
}

// === Recording sequencer ===
var _recording = false;
var _recorded = [];
var _recStart = 0;

function toggleRecord() {
  var btn = document.getElementById('auditRecBtn');
  var playBtn = document.getElementById('auditPlayBtn');
  if (_recording) {
    // Stop recording — enable playback if anything was captured.
    _recording = false;
    btn.classList.remove('recording');
    btn.textContent = '\u25CF';
    if (_recorded.length > 0) {
      playBtn.classList.remove('disabled');
    }
  } else {
    // Start recording — wipe prior recording; play stays visible but
    // disabled so the record/play pair is always on screen.
    _recorded = [];
    _recStart = performance.now();
    _recording = true;
    btn.classList.add('recording');
    btn.textContent = '\u25A0';
    playBtn.classList.add('disabled');
  }
}

var _playbackTimers = [];

function stopPlayback() {
  for (var i = 0; i < _playbackTimers.length; i++) clearTimeout(_playbackTimers[i]);
  _playbackTimers = [];
  var playBtn = document.getElementById('auditPlayBtn');
  if (playBtn) {
    playBtn.classList.remove('playing');
    playBtn.textContent = '\u25B7';  // ▷ play
  }
}

// Play/stop toggle — the button's onclick. While playing, click stops
// (cancels pending timers); while stopped, click plays from the start.
// The record/play pair mirrors a tape-deck affordance: one button,
// two states, same glyph flip as the record button's ● ↔ ■.
function togglePlayback() {
  var playBtn = document.getElementById('auditPlayBtn');
  if (playBtn && playBtn.classList.contains('playing')) {
    stopPlayback();
  } else {
    playbackRecording();
  }
}

function playbackRecording() {
  if (!_recorded.length) return;
  stopPlayback();

  var playBtn = document.getElementById('auditPlayBtn');
  playBtn.classList.add('playing');
  playBtn.textContent = '\u25A0';  // ■ stop

  var notes = document.querySelectorAll('#certResults .audit-note[data-scale-note]');
  var chordBtn = document.querySelector('#certResults .audit-note[data-freqs]');
  for (var i = 0; i < _recorded.length; i++) {
    (function(entry) {
      var tid = setTimeout(function() {
        if (entry.chord && chordBtn) {
          playAuditChord(chordBtn);
        } else if (entry.noteIdx >= 0 && entry.noteIdx < notes.length) {
          playAuditNote(notes[entry.noteIdx]);
        }
      }, entry.time);
      _playbackTimers.push(tid);
    })(_recorded[i]);
  }

  var lastTime = _recorded[_recorded.length - 1].time;
  // Natural end of playback: reset to idle state (removes .playing
  // class, swaps glyph back to ▷) via the same stopPlayback helper
  // the toggle uses, so the button always lands in a consistent shape.
  var endTid = setTimeout(stopPlayback, lastTime + 2000);
  _playbackTimers.push(endTid);
}

// Keyboard 1-7 plays the scale notes, 8 plays chord
document.addEventListener('keydown', function(e) {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
  // Arrow keys cycle ages in orbit inspector
  if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
    var oi = document.getElementById('orbitInspector');
    if (oi && oi._shiftAge) { e.preventDefault(); oi._shiftAge(e.key === 'ArrowRight' ? 1 : -1); return; }
  }
  var key = parseInt(e.key);
  if (key >= 1 && key <= 9) {
    var notes = document.querySelectorAll('#certResults .audit-note[data-scale-note]');
    if (!notes.length) return;
    // Recording capture now lives inside playAuditNote / playAuditChord
    // so mouse clicks and keyboard presses share one code path.
    if (key === 9) {
      var chordBtn = document.querySelector('#certResults .audit-note[data-freqs]');
      if (chordBtn) playAuditChord(chordBtn);
    } else if (key <= notes.length) {
      playAuditNote(notes[key - 1]);
    }
  }
});

// =====================================================================
// ATTACK LAB — interactive forgery playground
//   - Activating replaces the tabs with the attack surface in-place
//     (same 323px box — no expansion).
//   - A real example.soul is loaded; the cert renders on the right.
//   - Editing any input flips WITNESSED / AUTHENTICATED / EMBODIED live.
// =====================================================================
(function(){
var link = document.getElementById('attackToggleLink');
var inputSection = document.querySelector('.input-section');
var panel = document.getElementById('tab-attack');
var resultsWrap = document.getElementById('resultsWrap');
if (!link || !panel || !resultsWrap) return;

var ATTACK_IDENTIFIER = 'mememage-22dd171b5d648ec3';
var PORTRAIT_THRESHOLD = 15;
var savedResultsHTML = null;
// True only if at least one result div had actual content when the attack
// lab opened — the bare innerHTML contains three empty stub divs
// (imgResults, certResults, metaSidebarResults) even on a fresh page, so
// we can't use innerHTML.trim() alone to decide whether to restore.
var savedHadContent = false;
var inited = false;
var original = null;   // { record, hash, sig, pub, thumbDHash }
var current = null;    // { record, sig, pub, userDHash }
var debounceTimer = null;

function hideDrop() { document.getElementById('atk-drop').style.display = 'flex'; }

async function loadOriginal() {
  var exampleUrl = (typeof assetPath === 'function')
    ? assetPath('samples/example.soul')
    : 'samples/example.soul';
  var res = await fetch(exampleUrl, { cache: 'no-store' });
  var soul = await res.json();
  // Identifier isn't stored in soul files; derive it here.
  soul.identifier = ATTACK_IDENTIFIER;

  // Build the tamperable record view (HASH_INCLUDED fields only — the
  // fields that actually move the hash). Other fields ride along as
  // passthroughs via `soul` for rendering.
  var hashable = {};
  Object.keys(soul).sort().forEach(function(k) {
    if (HASH_INCLUDED.has(k)) hashable[k] = soul[k];
  });

  // Thumbnail dHash — compute once against the record's stored portrait.
  var thumbDHash = await new Promise(function(resolve) {
    var img = new Image();
    img.onload = function() {
      var c = document.createElement('canvas');
      c.width = img.naturalWidth; c.height = img.naturalHeight;
      c.getContext('2d').drawImage(img, 0, 0);
      resolve(dHashFromCanvas(c));
    };
    img.onerror = function() { resolve(null); };
    // Only allow inline data: image URLs — block remote thumbnails so
    // a hostile soul can't beacon the viewer's IP via the dHash load.
    var _stb = (typeof soul.thumbnail === 'string' && /^data:image\//.test(soul.thumbnail)) ? soul.thumbnail : '';
    if (_stb) img.src = _stb; else resolve(null);
  });

  original = {
    soul: soul,
    record: hashable,
    hash: soul.content_hash,
    sig: soul.signature,
    pub: soul.public_key,
    thumbDHash: thumbDHash,
    lumaGrid: decodeStoredGrid(soul.luma_grid)   // {mean,min,max,flat} tamper reference
  };
  current = {
    record: JSON.parse(JSON.stringify(hashable)),
    sig: original.sig,
    pub: original.pub,
    userDHash: thumbDHash,    // starts matching
    userGrid: _gridStats(original.lumaGrid)   // {mean,min,max} of the stored ref
  };
}

// The dropped-image tile stats view {mean,min,max} of a decoded stored grid —
// used as the Attack Lab's starting (unmodified) image so it matches itself.
function _gridStats(g) {
  return g ? {mean: g.mean, min: g.min, max: g.max} : null;
}

function buildCertMeta(verification) {
  var m = Object.assign({}, original.soul);
  m._identifier = ATTACK_IDENTIFIER;
  m._content_hash = original.hash;
  m._verification = verification;
  return m;
}

function renderDHashGrid(el, bits, diffMask) {
  if (!el) return;
  if (!bits) { el.innerHTML = ''; return; }
  var h = '';
  for (var y = 0; y < 8; y++) {
    h += '<tr>';
    for (var x = 0; x < 8; x++) {
      var i = y * 8 + x;
      // Each grid shows its OWN bit (on/off) so "your image" and "original"
      // are visually distinct fingerprints. A differing cell adds `diff`,
      // which tints by this grid's bit (set vs unset) — so at a diff position
      // one grid reads bright, the other faded. (The old code rendered every
      // diff cell the same red in BOTH grids, making them look identical.)
      var cls = (bits[i] ? 'on' : 'off') + (diffMask && diffMask[i] ? ' diff' : '');
      h += '<td class="' + cls + '"></td>';
    }
    h += '</tr>';
  }
  el.innerHTML = h;
}

function updateDHashGrids(userBits, origBits) {
  var diffMask = null;
  if (userBits && origBits && userBits.length === origBits.length) {
    diffMask = [];
    for (var i = 0; i < userBits.length; i++) diffMask.push(userBits[i] !== origBits[i] ? 1 : 0);
  }
  renderDHashGrid(document.getElementById('atk-dhash-user'), userBits, diffMask);
  renderDHashGrid(document.getElementById('atk-dhash-orig'), origBits, diffMask);
}

function setStatus(currentHash, witnessed, authenticated, dist, embodied, jsonErr) {
  var el = document.getElementById('atk-status');
  if (!el) return;
  if (jsonErr) { el.innerHTML = '<span class="bad">Invalid JSON</span>'; return; }
  var h = 'hash <span class="' + (witnessed ? 'ok' : 'bad') + '">' + currentHash + '</span>';
  h += ' &middot; sig <span class="' + (authenticated === true ? 'neu' : authenticated === false ? 'bad' : '') + '">';
  h += authenticated === true ? 'valid' : authenticated === false ? 'forged' : 'n/a';
  h += '</span>';
  h += ' &middot; portrait <span class="' + (embodied ? 'neu' : 'bad') + '">' + (dist == null ? '\u2014' : (dist + '/64')) + '</span>';
  el.innerHTML = h;
}

function updateCertBadges(witnessed, authenticated, embodied, dist, embReason) {
  var bg = resultsWrap.querySelector('.verify-badge-group');
  if (!bg) return;
  bg.innerHTML = '';
  var w = document.createElement('div');
  w.className = 'verify-badge ' + (witnessed ? 'verify-verified' : 'verify-tampered');
  w.innerHTML = witnessed
    ? '<span class="verify-icon">\u2713</span> WITNESSED'
    : '<span class="verify-icon">\u2717</span> ALTERED';
  bg.appendChild(w);

  if (authenticated === true) {
    var a = document.createElement('div');
    a.className = 'verify-badge verify-authenticated';
    a.innerHTML = '<span class="verify-icon">\uD83D\uDD11</span> AUTHENTICATED';
    bg.appendChild(a);
  } else if (authenticated === false) {
    var a2 = document.createElement('div');
    a2.className = 'verify-badge verify-forged';
    a2.innerHTML = '<span class="verify-icon">\u2717</span> FORGED';
    bg.appendChild(a2);
  }

  if (embodied === true) {
    var e = document.createElement('div');
    e.className = 'verify-badge verify-embodied';
    e.innerHTML = '<span class="verify-icon">\u2B22</span> EMBODIED';
    e.title = 'Portrait match \u2014 dHash distance ' + dist + '/' + PORTRAIT_THRESHOLD;
    bg.appendChild(e);
  } else if (embodied === false) {
    var e2 = document.createElement('div');
    e2.className = 'verify-badge verify-disembodied';
    if (embReason === 'altered') {
      e2.innerHTML = '<span class="verify-icon">\u2B21</span> ALTERED';
      e2.title = 'Localized alteration detected \u2014 the image has been edited since conception';
    } else {
      e2.innerHTML = '<span class="verify-icon">\u2B21</span> DISEMBODIED';
      e2.title = 'Portrait mismatch \u2014 dHash distance ' + dist;
    }
    bg.appendChild(e2);
  }
}

async function recompute() {
  var recEl = document.getElementById('atk-record');
  var sigEl = document.getElementById('atk-sig');
  var pubEl = document.getElementById('atk-pub');
  if (!recEl || !original) return;

  var rec;
  try { rec = JSON.parse(recEl.value); }
  catch (e) { setStatus(null, false, null, null, false, true); return; }
  current.record = rec;
  current.sig = sigEl.value.trim();
  current.pub = pubEl.value.trim();

  // WITNESSED = the V1 content hash recomputes to the bar-carried hash —
  // exactly what the decoder checks. example.soul is a real V1 record, so this
  // is a faithful hash compare. (The editable record above holds only
  // HASH_INCLUDED fields, so any edit moves the hash and breaks WITNESSED.)
  var witnessed = (await computeContentHash(rec)) === original.hash;

  // Display hash — the stored bar hash when untampered, a drifting
  // JS-computed hash when tampered, so the user sees the hash break.
  var displayHash = witnessed
    ? original.hash
    : (await computeContentHash(rec));

  // AUTHENTICATED — mirror production: signature is meaningless over
  // tampered content, so auto-FORGED if WITNESSED fails. Otherwise
  // verify against the stored (bar-carried) hash.
  var authenticated = null;
  if (!witnessed) {
    authenticated = false;
  } else if (/^[0-9a-f]+$/i.test(current.sig) && /^[0-9a-f]+$/i.test(current.pub)) {
    // Signature payload binds the thumbnail (verify.js): id + content_hash +
    // sha256(thumbnail). The thumbnail isn't an editable field here (the record
    // textarea is HASH_INCLUDED-only), so verify against the STORED thumbnail —
    // a green baseline that breaks the moment WITNESSED or the sig/key is forged.
    var attackThumbHash = await _thumbnailHashForSig(original.soul);
    authenticated = await verifySignature(
      ATTACK_IDENTIFIER, original.hash, current.sig, current.pub, attackThumbHash
    );
  }

  var dist = (current.userDHash && original.thumbDHash)
    ? hammingDistance(current.userDHash, original.thumbDHash) : null;
  var embodied = dist != null ? (dist <= PORTRAIT_THRESHOLD) : null;

  // Localized-tamper half — compare the dropped image's grid against the
  // CURRENT (possibly hand-edited) record's grid. Swapping in a defaced image
  // breaks this; editing luma_grid to compensate breaks WITNESSED. Either way
  // the forger can't get all-green. Reason flips DISEMBODIED -> ALTERED.
  var embReason = null;
  var curStored = decodeStoredGrid(rec.luma_grid);
  if (curStored && current.userGrid) {
    var ev = lumaEvaluate(curStored, current.userGrid);
    if ((ev.markMax > LUMA_MARK || ev.highMax > LUMA_HIGH) && embodied !== false) {
      embodied = false;
      embReason = 'altered';
    }
  }

  updateCertBadges(witnessed, authenticated, embodied, dist, embReason);
  setStatus(displayHash, witnessed, authenticated, dist, embodied, false);
  updateDHashGrids(current.userDHash, original.thumbDHash);
}

function scheduleRecompute() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(recompute, 200);
}

function resetAll() {
  current = {
    record: JSON.parse(JSON.stringify(original.record)),
    sig: original.sig,
    pub: original.pub,
    userDHash: original.thumbDHash,
    userGrid: _gridStats(original.lumaGrid)
  };
  document.getElementById('atk-record').value = JSON.stringify(original.record, null, 2);
  document.getElementById('atk-sig').value = original.sig;
  document.getElementById('atk-pub').value = original.pub;
  document.getElementById('atk-drop-label').textContent = 'drop or click to swap image';
  document.getElementById('atk-file').value = '';
  recompute();
}

function loadUserImage(file) {
  var img = new Image();
  img.onload = function() {
    var c = document.createElement('canvas');
    c.width = img.naturalWidth; c.height = img.naturalHeight;
    c.getContext('2d').drawImage(img, 0, 0);
    current.userDHash = dHashFromCanvas(c);
    current.userGrid = computeTileStats(c);
    document.getElementById('atk-drop-label').textContent = file.name + ' \u2713';
    recompute();
  };
  img.src = URL.createObjectURL(file);
}

function wire() {
  document.getElementById('atk-record').addEventListener('input', scheduleRecompute);
  document.getElementById('atk-sig').addEventListener('input', scheduleRecompute);
  document.getElementById('atk-pub').addEventListener('input', scheduleRecompute);
  document.getElementById('atk-reset').addEventListener('click', resetAll);
  var drop = document.getElementById('atk-drop');
  var file = document.getElementById('atk-file');
  file.addEventListener('change', function() { if (file.files[0]) loadUserImage(file.files[0]); });
  drop.addEventListener('dragover', function(e) { e.preventDefault(); drop.style.borderColor = 'rgba(110,168,254,0.5)'; });
  drop.addEventListener('dragleave', function() { drop.style.borderColor = 'rgba(255,255,255,0.1)'; });
  drop.addEventListener('drop', function(e) {
    e.preventDefault();
    drop.style.borderColor = 'rgba(255,255,255,0.1)';
    var f = e.dataTransfer.files[0];
    if (f && f.type.startsWith('image/')) loadUserImage(f);
  });
}

async function activate() {
  link.textContent = 'hide attack lab';
  inputSection.classList.add('attack-active');
  savedResultsHTML = resultsWrap.innerHTML;
  savedHadContent = ['imgResults', 'certResults', 'metaSidebarResults'].some(function(id) {
    var el = document.getElementById(id);
    return el && el.innerHTML.trim().length > 0;
  });
  // NOTE: do NOT add panel-right-has-player — the sample cert has no
  // player, and that class triggers a 100vh plate height that leaves
  // empty purple space below the short cert content.

  if (!inited) {
    try { await loadOriginal(); } catch (err) {
      console.error('Attack Lab: failed to load example.soul', err);
      deactivate();
      return;
    }
    document.getElementById('atk-record').value = JSON.stringify(original.record, null, 2);
    document.getElementById('atk-sig').value = original.sig;
    document.getElementById('atk-pub').value = original.pub;
    wire();
    inited = true;
  }

  // Initial cert render — all three badges green.
  var verification = {
    status: 'verified',
    detail: 'Attack lab fixture.',
    signature: true,
    signatureDetail: 'Ed25519 signature valid.',
    portrait: { match: true, distance: 0, threshold: PORTRAIT_THRESHOLD }
  };
  // Sample mode: truncates the cert at Birth Temperament, hides bands,
  // GPS, save button, and music player. Consumed by renderCert.
  // Cross-fade: if resultsWrap was already showing (e.g., a prior query
  // result), outtro it before rendering the test cert; the intro runs
  // after renderCert replaces the contents.
  PanelSwap(resultsWrap, function() {
    window._sampleMode = true;
    // Attack-Lab example cert isn't anchored to a real chain — disable chain
    // traversal (Bayer letter) + the stellarium (buggy off a non-anchored
    // chain). Real lookups by identifier are unaffected.
    window._exampleMode = true;
    renderCert(buildCertMeta(verification), {
      target: resultsWrap, activateLayout: false, injectPlayer: false
    });
    resultsWrap.classList.add('visible');
  });
  var dm = document.querySelector('.panel-layout');
  if (dm) {
    if (!dm.classList.contains('layout-active')) holdCertEntering(resultsWrap);
    dm.classList.add('layout-active');
  }

  // Sync status line + badges with current edits (may be non-original
  // if user previously typed, then toggled off/on).
  recompute();
}

function deactivate() {
  link.textContent = 'test an attack';
  inputSection.classList.remove('attack-active');
  if (savedHadContent) {
    // Cross-fade back to prior results (image forensics / audit cert).
    var restore = savedResultsHTML;
    savedResultsHTML = null;
    savedHadContent = false;
    PanelSwap(resultsWrap, function() {
      resultsWrap.innerHTML = restore;
      resultsWrap.classList.remove('panel-right-has-player');
      resultsWrap.classList.add('visible');
    });
  } else {
    // Nothing to return to — dismiss the panel, deactivate the two-
    // panel layout, and reset the tabs to their compact default so the
    // system box no longer shows the attack-active layout.
    savedResultsHTML = null;
    savedHadContent = false;
    PanelSwap(resultsWrap, function() {
      // Clear stub divs so nothing visible remains during the fade-out.
      resultsWrap.innerHTML = '<div id="imgResults"></div><div id="certResults"></div><div id="metaSidebarResults"></div>';
      resultsWrap.classList.remove('panel-right-has-player', 'visible');
      var dm = document.querySelector('.panel-layout');
      if (dm) dm.classList.remove('layout-active');
    });
  }
}

link.addEventListener('click', function(e) {
  e.preventDefault();
  if (inputSection.classList.contains('attack-active')) deactivate();
  else activate();
});
})();

// GPS Password Unlock — AES-256-GCM decryption via Access helper.
var _gpsRecords=[];
async function unlockGPS(idx){
  var r=_gpsRecords[idx];if(!r||!r.gps_password_locked)return;
  var pw=document.getElementById('gps-pw-'+idx);if(!pw)return;
  var out=document.getElementById('gps-result-'+idx);if(!out)return;
  var res = await Access.decryptGps(r.gps_password_locked, pw.value);
  if (res.ok) {
    out.innerHTML='<div class="ev-g"><div class="ev-m"><div class="ev-ml">Latitude</div><div class="ev-mv pass">'+escapeHtml(res.lat)+'</div></div><div class="ev-m"><div class="ev-ml">Longitude</div><div class="ev-mv pass">'+escapeHtml(res.lon)+'</div></div></div>';
  } else {
    out.innerHTML='<span style="color:#f87171;font-size:0.7rem;">'+escapeHtml(res.error||'Wrong password')+'</span>';
  }
}

// Portal transition — flip between validator and decoder (see js/portal.js)
Portal.init({
  sourceMarker: 'validator',
  otherMarker:  'decoder',

  applyIncomingTab: function(idx) {
    var tabNames = ['img', 'cert', 'meta'];
    showTab(tabNames[idx] || 'img');
  },

  getOutgoingTab: function() {
    var idx = 0;
    var tabMap = { 'tab-img': 0, 'tab-cert': 1, 'tab-meta': 2 };
    document.querySelectorAll('.input-panel').forEach(function(t) {
      if (t.classList.contains('active') && tabMap[t.id] !== undefined) idx = tabMap[t.id];
    });
    return idx;
  },

  reset: function() {
    document.getElementById('imgResults').innerHTML = '';
    document.getElementById('certResults').innerHTML = '';
    var metaR = document.getElementById('metaResults'); if (metaR) metaR.innerHTML = '';
    var orbitEl = document.getElementById('orbitInspector'); if (orbitEl) orbitEl.innerHTML = '';
    var auditIn = document.getElementById('auditInput'); if (auditIn) auditIn.value = '';
    // Return the Image tab's drop zone to its default state — mirrors
    // decoder's resetAll() which clears the preview/console on departure.
    var imgCon = document.getElementById('imgConsole');
    if (imgCon) {
      imgCon.classList.remove('visible');
      var t = document.getElementById('imgConsoleThumb'); if (t) t.src = '';
      var idEl = document.getElementById('imgConsoleId'); if (idEl) idEl.innerHTML = '';
      var hEl = document.getElementById('imgConsoleHash'); if (hEl) hEl.textContent = '';
      var sEl = document.getElementById('imgConsoleStatus'); if (sEl) { sEl.textContent = ''; sEl.className = 'img-console-status'; }
    }
  },

  dismissResults: function(done) {
    dismissPanel(document.getElementById('resultsWrap'), {
      resetHtml: '<div id="imgResults"></div><div id="certResults"></div>'
    }, done);
  },
});

// Typewriter — loaded from js/typewriter.js (shared with decoder)

