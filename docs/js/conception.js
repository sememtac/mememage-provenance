// Conception page state machine.
//
// Drives /mint/<token>: GPS capture (or skip), POST conceive, poll
// status, render the conceived/failed state. Reads its config from
// the inline <script id="conception-config" type="application/json">
// block so the HTML can be a static file the server token-substitutes
// once at request time.
//
// States: pre → minting → conceived | failed.

(function() {
  'use strict';

  var configEl = document.getElementById('conception-config');
  if (!configEl) {
    console.error('conception-config block missing');
    return;
  }
  var config;
  try {
    config = JSON.parse(configEl.textContent || '{}');
  } catch (e) {
    console.error('conception-config parse failed', e);
    return;
  }

  var token = config.token || '';
  var gpsSource = config.gps_source || 'phone';
  var imageName = config.image_name || '';
  var metadata = config.metadata || {};

  // ===== Element refs =====
  var fileEl = document.getElementById('conceptionFile');
  var gpsLabelEl = document.getElementById('conceptionGpsLabel');
  var gpsValueEl = document.getElementById('conceptionGpsValue');
  var gpsHintEl = document.getElementById('conceptionGpsHint');
  var gpsBoxEl = document.getElementById('conceptionGps');
  var metaCountEl = document.getElementById('conceptionMetaCount');
  var metaBodyEl = document.getElementById('conceptionMetaBody');
  var confirmBtn = document.getElementById('conceptionConfirm');
  var pulseDotsEl = document.getElementById('conceptionPulseDots');
  var imageEl = document.getElementById('conceptionImage');
  var downloadImageBtn = document.getElementById('conceptionDownloadImage');
  var factsEl = document.getElementById('conceptionFacts');
  var surfacesEl = document.getElementById('conceptionSurfaces');
  var failBodyEl = document.getElementById('conceptionFailBody');
  var retryBtn = document.getElementById('conceptionRetry');

  // ===== Header populate =====
  if (fileEl) fileEl.textContent = imageName;

  // ===== Staged thumbnail =====
  // Fetch + display the staged image so the creator can verify
  // they're conceiving the right thing before tapping the button.
  // Server allows /api/mint/<token>/image in pending state.
  var thumbBtnEl = document.getElementById('conceptionThumbBtn');
  var thumbEl = document.getElementById('conceptionThumb');
  if (thumbBtnEl && thumbEl && token) {
    thumbEl.addEventListener('load', function() { thumbBtnEl.hidden = false; });
    thumbEl.addEventListener('error', function() { thumbBtnEl.hidden = true; });
    thumbEl.src = '/api/mint/' + encodeURIComponent(token) + '/image';
    // Click → full-size lightbox (mirrors decoder ui.js:870 pattern).
    thumbBtnEl.addEventListener('click', function() {
      if (!thumbEl.src) return;
      var overlay = document.createElement('div');
      overlay.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.88);display:flex;align-items:center;justify-content:center;cursor:pointer;padding:1.5rem;';
      var fullImg = document.createElement('img');
      fullImg.src = thumbEl.src;
      fullImg.style.cssText = 'max-width:92vw;max-height:92vh;object-fit:contain;border-radius:8px;box-shadow:0 4px 40px rgba(0,0,0,0.6);';
      overlay.appendChild(fullImg);
      overlay.addEventListener('click', function() { overlay.remove(); });
      document.addEventListener('keydown', function esc(e) {
        if (e.key === 'Escape') { overlay.remove(); document.removeEventListener('keydown', esc); }
      });
      document.body.appendChild(overlay);
    });
  }

  // ===== Origin fields render =====
  // Show prompt, seed, dimensions, sampler — anything in the staged
  // metadata. The full JSON would overwhelm a phone screen; pick the
  // commonly-meaningful keys, then dump the rest in a sub-detail.
  var metaKeys = Object.keys(metadata).sort();
  metaCountEl.textContent = metaKeys.length;
  metaKeys.forEach(function(k) {
    var row = document.createElement('div');
    row.className = 'conception-meta-row';
    var kEl = document.createElement('span');
    kEl.className = 'k';
    kEl.textContent = k;
    var vEl = document.createElement('span');
    vEl.className = 'v';
    var raw = metadata[k];
    vEl.textContent = (typeof raw === 'object') ? JSON.stringify(raw) : String(raw);
    row.appendChild(kEl);
    row.appendChild(vEl);
    metaBodyEl.appendChild(row);
  });

  // ===== State transitions =====
  // The sticky head label is borrowed from the "confirm" step; retitle it per
  // state so the conceived/failed result doesn't still read "Confirm conception".
  var HEAD_LABEL = {
    pre: 'Confirm conception',
    minting: 'Conceiving…',
    conceived: 'Conceived',
    failed: 'Conception failed',
  };
  function showState(name) {
    var states = document.querySelectorAll('.conception-state');
    states.forEach(function(s) {
      s.hidden = (s.getAttribute('data-state') !== name);
    });
    var headLabel = document.querySelector('.conception-head-label');
    if (headLabel && HEAD_LABEL[name]) headLabel.textContent = HEAD_LABEL[name];
    // The top "Target surfaces" strip is a PLAN — where the soul will go,
    // read from current config. That's honest before the point of no return
    // (pre / in-flight), but once conceived the truth is what was actually
    // captured, shown in the result's Surfaces list. Hide the plan post-
    // conception (and on failure) so it can't contradict the record.
    var planStrip = document.querySelector('.conception-channels');
    if (planStrip) planStrip.hidden = (name === 'conceived' || name === 'failed');
  }

  // ===== GPS branches =====
  var lat = null;
  var lon = null;
  var bestAcc = Infinity;
  var watchId = null;
  var ACCURACY_THRESHOLD = 20;  // meters — phone-mode gating

  function startGpsPhone() {
    if (!('geolocation' in navigator)) {
      gpsBoxEl.setAttribute('data-mode', 'phone');
      gpsLabelEl.textContent = 'Creator Location';
      gpsValueEl.textContent = 'Geolocation not supported';
      gpsValueEl.classList.add('conception-gps-failed');
      gpsHintEl.textContent = 'Open this page on a device with geolocation, or switch the chain GPS source in the dashboard.';
      return;
    }
    gpsBoxEl.setAttribute('data-mode', 'phone');
    gpsLabelEl.textContent = 'Creator Location';
    gpsValueEl.textContent = 'Acquiring satellite fix\u2026';
    gpsValueEl.classList.add('conception-gps-acquiring');
    gpsHintEl.textContent = 'needs \u00b1' + ACCURACY_THRESHOLD + 'm for phone-mode capture';

    watchId = navigator.geolocation.watchPosition(
      function(pos) {
        var acc = pos.coords.accuracy;
        if (acc < bestAcc) {
          lat = pos.coords.latitude;
          lon = pos.coords.longitude;
          bestAcc = acc;
        }
        if (bestAcc <= ACCURACY_THRESHOLD) {
          gpsValueEl.classList.remove('conception-gps-acquiring');
          gpsValueEl.textContent = lat.toFixed(6) + ', ' + lon.toFixed(6);
          gpsLabelEl.textContent = 'Creator Location (\u00b1' + Math.round(bestAcc) + 'm)';
          gpsHintEl.textContent = 'ready';
          confirmBtn.disabled = false;
        } else {
          gpsValueEl.textContent = 'Refining\u2026 \u00b1' + Math.round(acc) + 'm';
          gpsHintEl.textContent = 'needs \u00b1' + ACCURACY_THRESHOLD + 'm for phone-mode capture';
          confirmBtn.disabled = true;
        }
      },
      function() {
        gpsValueEl.classList.remove('conception-gps-acquiring');
        gpsValueEl.classList.add('conception-gps-failed');
        gpsValueEl.textContent = 'Location unavailable';
        gpsHintEl.textContent = 'Allow location access and reload, or switch this chain to machine/none.';
      },
      { enableHighAccuracy: true, timeout: 60000, maximumAge: 0 }
    );
  }

  function startGpsMachine() {
    gpsBoxEl.setAttribute('data-mode', 'machine');
    gpsLabelEl.textContent = 'Machine GPS (approximate)';
    gpsValueEl.textContent = 'Fetching server IP geolocation…';
    gpsHintEl.textContent = 'gps_source: machine — coarse location, no phone needed';
    confirmBtn.disabled = false;   // conceivable regardless of the preview
    // Live preview of the coordinates the server would use. The mint re-checks
    // fresh at conceive (same IP → matches); this is informational, never a gate.
    fetch('/api/mint/' + token + '/machine-gps')
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (d && typeof d.lat === 'number' && typeof d.lon === 'number') {
          gpsValueEl.textContent = d.lat.toFixed(4) + ', ' + d.lon.toFixed(4);
          gpsHintEl.textContent = 'gps_source: machine — server IP geolocation (re-checked at conceive)';
        } else {
          gpsValueEl.textContent = 'Server will fetch IP geolocation on conceive';
        }
      })
      .catch(function() {
        gpsValueEl.textContent = 'Server will fetch IP geolocation on conceive';
      });
  }

  function startGpsNone() {
    gpsBoxEl.setAttribute('data-mode', 'none');
    gpsLabelEl.textContent = 'Birthplace';
    gpsValueEl.textContent = 'Not recorded for this chain';
    gpsHintEl.textContent = 'gps_source: none \u2014 record will carry no time-lock puzzle';
    confirmBtn.disabled = false;
  }

  // ===== Conceive button =====
  confirmBtn.addEventListener('click', async function() {
    if (gpsSource === 'phone' && (lat === null || lon === null)) return;
    if (watchId !== null) navigator.geolocation.clearWatch(watchId);
    confirmBtn.disabled = true;

    var body = (gpsSource === 'phone') ? JSON.stringify({ lat: lat, lon: lon }) : '{}';
    try {
      var resp = await fetch('/api/mint/' + token, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body,
      });
      var data = await resp.json();
      if (data.error) {
        renderFailed(data.error);
        return;
      }
      showState('minting');
      animateDots();
      pollMintStatus();
    } catch (err) {
      renderFailed('Network error: ' + err.message);
    }
  });

  // Retry button — clears state and reloads the page.
  if (retryBtn) {
    retryBtn.addEventListener('click', function() { window.location.reload(); });
  }

  // ===== Pulse animation =====
  var dotsTimer = null;
  function animateDots() {
    var n = 0;
    dotsTimer = setInterval(function() {
      n = (n + 1) % 4;
      pulseDotsEl.textContent = '.'.repeat(n + 1);
    }, 400);
  }
  function stopDots() {
    if (dotsTimer) { clearInterval(dotsTimer); dotsTimer = null; }
    pulseDotsEl.textContent = '';
  }

  // ===== Poll for completion =====
  function pollMintStatus() {
    var poll = setInterval(async function() {
      try {
        var resp = await fetch('/api/mint/' + token + '/status');
        var data = await resp.json();
        if (data.status === 'completed') {
          clearInterval(poll);
          stopDots();
          renderConceived(data);
        } else if (data.status === 'failed') {
          clearInterval(poll);
          stopDots();
          renderFailed(data.error || 'Unknown error');
        }
      } catch (e) {
        // Network hiccup — keep polling
      }
    }, 1500);
  }

  // ===== Conceived state render =====
  function renderConceived(data) {
    showState('conceived');
    var ident = data.identifier || 'mememage';
    var imgUrl = '/api/mint/' + token + '/image';

    imageEl.src = imgUrl;

    // Facts list — identifier, hash, GPS, constellation if surfaced.
    factsEl.innerHTML = '';
    function addFact(label, val, opts) {
      opts = opts || {};
      var dt = document.createElement('dt');
      dt.textContent = label;
      var dd = document.createElement('dd');
      if (opts.code) {
        var c = document.createElement('code');
        c.textContent = val;
        dd.appendChild(c);
      } else {
        dd.textContent = val;
      }
      if (opts.dim) dd.className = 'conception-facts-dim';
      factsEl.appendChild(dt);
      factsEl.appendChild(dd);
    }

    addFact('Identifier', ident, { code: true });
    addFact('Content hash', data.content_hash || '(unsigned)', { code: true });

    if (data.gps && typeof data.gps.lat === 'number') {
      addFact('Birthplace', data.gps.lat.toFixed(6) + ', ' + data.gps.lon.toFixed(6) + ' (time-locked)');
    } else if (data.gps_source === 'none') {
      addFact('Birthplace', 'not recorded', { dim: true });
    } else {
      addFact('Birthplace', '\u2014', { dim: true });
    }

    // Image download
    _wireBlobDownload(downloadImageBtn, imgUrl, ident + '.png');

    // Surface buttons — one per channel that accepted the soul.
    // Below the success list, surface any channels that errored
    // mid-blast (partial failure: at least one channel succeeded
    // but others didn't). Lets the user see "IA timed out" without
    // having to scrape server logs. All-channel failures route to
    // the failed state, not here.
    surfacesEl.innerHTML = '';
    var dist = data.distribution || {};
    var distKeys = Object.keys(dist);
    var entries = distKeys.length
      ? distKeys.map(function(k) { return [k, dist[k]]; })
      : [['local', '/api/mint/' + token + '/soul']];

    // Label a captured surface by WHERE the soul landed (the URL's host),
    // not the internal channel slug — matches the plan strip's
    // display_surface(): localhost stays "localhost", a bare IP falls back
    // to the slug (we don't surface raw IPs), a relative/local URL keeps the
    // slug, and a real domain shows verbatim (e.g. soul-test.mememage.art).
    function _surfaceLabel(channelId, url) {
      if (!/^https?:\/\//i.test(url || '')) return channelId;
      try {
        var host = (new URL(url).hostname || '').toLowerCase();
        if (host === 'localhost' || host === '127.0.0.1' || host === '::1') return 'localhost';
        if (/^\d{1,3}(\.\d{1,3}){3}$/.test(host)) return channelId;
        if (host) return host;
      } catch (e) { /* unparseable URL */ }
      return channelId;
    }

    entries.forEach(function(e) {
      var label = e[0];
      var url = e[1];
      var row = document.createElement('div');
      row.className = 'conception-surface';
      var lab = document.createElement('span');
      lab.className = 'conception-surface-label';
      lab.textContent = _surfaceLabel(label, url);
      lab.title = label;  // the channel slug, for reference
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'conception-surface-dl';
      btn.textContent = 'Download soul';
      _wireBlobDownload(btn, url, ident + '.soul');
      row.appendChild(lab);
      row.appendChild(btn);
      surfacesEl.appendChild(row);
    });

    var errors = data.distribution_errors || {};
    var errorKeys = Object.keys(errors);
    if (errorKeys.length) {
      errorKeys.forEach(function(eid) {
        var row = document.createElement('div');
        row.className = 'conception-surface conception-surface-error';
        var lab = document.createElement('span');
        lab.className = 'conception-surface-label';
        lab.textContent = eid;
        var msg = document.createElement('span');
        msg.className = 'conception-surface-error-msg';
        msg.textContent = errors[eid] || 'unknown error';
        row.appendChild(lab);
        row.appendChild(msg);
        surfacesEl.appendChild(row);
      });
    }
  }

  // ===== Failed state render =====
  function renderFailed(message) {
    stopDots();
    showState('failed');
    failBodyEl.textContent = message;
  }

  // ===== Blob-download helper =====
  // Used by the image button and per-surface soul buttons. JS-driven
  // (rather than <a download>) because self-signed cert sessions don't
  // honor cert acceptance for programmatic <a download> clicks, and
  // we want consistent behavior across channels regardless of whose
  // cert they're using.
  //
  // For image blobs, prefer navigator.share on mobile so iOS opens
  // its Share Sheet (which exposes "Save Image" → Photos library).
  // The plain anchor-download path drops images into the Files app
  // on iOS, which isn't where users expect their minted photos.
  function _imageLongPressOverlay(blob) {
    // iOS fallback for image saving. navigator.share requires the
    // 5-second transient-user-activation window, which fetch+toBlob
    // can blow past on a slow network. Long-press on an <img> is
    // iOS's universal "Save to Photos" gesture and works regardless.
    var url = URL.createObjectURL(blob);
    var overlay = document.createElement('div');
    overlay.style.cssText =
      'position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,0.92);' +
      'display:flex;flex-direction:column;align-items:center;justify-content:center;' +
      'padding:1rem;gap:0.8rem;';
    var instr = document.createElement('p');
    instr.textContent = 'Long-press the image, then "Save to Photos."';
    instr.style.cssText =
      'color:#e8e8e8;font:600 0.9rem/1.35 system-ui,-apple-system,sans-serif;' +
      'text-align:center;margin:0;max-width:30rem;';
    var img = document.createElement('img');
    img.src = url;
    img.alt = 'Conceived image';
    img.style.cssText =
      'max-width:92vw;max-height:75vh;object-fit:contain;border-radius:6px;' +
      'box-shadow:0 6px 32px rgba(0,0,0,0.5);' +
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
    overlay.addEventListener('click', function(e) {
      if (e.target === overlay) dismiss();
    });
    overlay.appendChild(instr);
    overlay.appendChild(img);
    overlay.appendChild(close);
    document.body.appendChild(overlay);
  }

  function _wireBlobDownload(btn, srcUrl, filename) {
    if (!btn) return;
    btn.onclick = async function() {
      var prev = btn.textContent;
      btn.disabled = true;
      btn.textContent = 'Preparing\u2026';
      try {
        var r = await fetch(srcUrl);
        if (!r.ok) throw new Error('HTTP ' + r.status);
        var blob = await r.blob();

        // Image path: iOS uses a long-press overlay (works regardless
        // of user-activation state, which Web Share API loses if the
        // fetch takes too long); MOBILE non-iOS (Android Chrome, etc.)
        // uses navigator.share for the system save sheet; desktop
        // falls through to the anchor download below.
        //
        // navigator.share on DESKTOP (macOS Safari especially) has a
        // history of re-encoding image blobs through the system share
        // pipeline — Save to Photos/Files can rewrite a PNG as WEBP
        // depending on macOS version. The anchor-download path is
        // deterministic: server's Content-Type + Content-Disposition
        // are honored as-is. Desktop should never hit the share API.
        var isImage = (blob.type || '').indexOf('image/') === 0;
        var ua = navigator.userAgent || '';
        var iosUA = /iPad|iPhone|iPod/.test(ua) && !window.MSStream;
        var androidUA = /Android/i.test(ua);
        var mobileUA = iosUA || androidUA;
        if (isImage && iosUA) {
          _imageLongPressOverlay(blob);
          btn.textContent = prev;
          btn.disabled = false;
          return;
        }
        if (isImage && mobileUA) {
          try {
            var file = new File([blob], filename, { type: blob.type });
            if (navigator.canShare && navigator.canShare({ files: [file] })) {
              await navigator.share({ files: [file], title: 'Mememage' });
              btn.textContent = 'Shared';
              setTimeout(function() { btn.textContent = prev; btn.disabled = false; }, 1500);
              return;
            }
          } catch (shareErr) {
            // AbortError = user dismissed the sheet — leave the button
            // alone and let them try again. Any other error falls
            // through to the anchor download.
            if (shareErr && shareErr.name === 'AbortError') {
              btn.textContent = prev;
              btn.disabled = false;
              return;
            }
          }
        }

        var bUrl = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = bUrl;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        setTimeout(function() {
          URL.revokeObjectURL(bUrl);
          a.remove();
        }, 1000);
        btn.textContent = 'Downloaded';
        setTimeout(function() { btn.textContent = prev; btn.disabled = false; }, 1500);
      } catch (e) {
        btn.textContent = 'Failed: ' + e.message;
        setTimeout(function() { btn.textContent = prev; btn.disabled = false; }, 2500);
      }
    };
  }

  // ===== Boot =====
  if (gpsSource === 'phone') startGpsPhone();
  else if (gpsSource === 'machine') startGpsMachine();
  else startGpsNone();

  // If revisiting a completed session, jump straight to the result.
  (async function checkInitialStatus() {
    try {
      var resp = await fetch('/api/mint/' + token + '/status');
      var data = await resp.json();
      if (data.status === 'completed') {
        renderConceived(data);
      } else if (data.status === 'failed') {
        renderFailed(data.error || 'Unknown error');
      } else if (data.status === 'minting') {
        showState('minting');
        animateDots();
        pollMintStatus();
      }
    } catch (e) { /* network — let user drive */ }
  })();

})();
