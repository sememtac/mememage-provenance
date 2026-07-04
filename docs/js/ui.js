// =====================================================================
// SUBTITLE ROTATION
// Trove lives in docs/js/theme.js (Theme.taglines.decoder) so the
// L1 voice can be reskinned per Age without touching this file.
// Default tagline is one entry in the trove → surfaces ~1/N visits.
// =====================================================================
(function _rotateSubtitle() {
  var sub = document.querySelector('.page-header .subtitle');
  var trove = (typeof Theme !== 'undefined') && Theme.taglines && Theme.taglines.decoder;
  if (!sub || !trove || !trove.length) return;
  sub.textContent = trove[Math.floor(Math.random() * trove.length)];
})();

// =====================================================================
// CONTENT HASH VERIFICATION
// computeContentHash, sortKeysDeep, sha256_16, and HASH_INCLUDED live
// in js/verify.js — loaded before this file, shared with the validator.
// =====================================================================

async function verifyRecord(record, barContentHash, knownIdentifier) {
  const storedHash = record.content_hash;
  var result;
  if (barContentHash && storedHash) {
    if (barContentHash !== storedHash)
      result = {status: 'tampered', detail: 'Bar hash does not match record hash — soul rejects the body'};
    else {
      const computed = await computeContentHash(record);
      if (computed === null)
        result = {status: 'bar_verified', detail: 'Bar hash matches record hash (crypto API unavailable)'};
      else if (computed === storedHash)
        result = {status: 'verified', detail: 'Hash match — body and soul joined, sealed by spirit'};
      else if (record.signature)
        result = {status: 'tampered', detail: 'Content modified after signing — hash field preserved but content changed'};
      else
        result = {status: 'bar_verified', detail: 'Bar hash matches record hash (legacy serialization)'};
    }
  } else if (barContentHash && !storedHash)
    result = {status: 'tampered', detail: 'Bar contains hash but record does not — possible replacement'};
  else if (!barContentHash && storedHash) {
    result = {status: 'unverified', detail: 'No spirit — soul only, bring body to witness'};
  } else
    result = {status: 'unverified', detail: 'No content hash — cannot witness'};

  // Signature verification (AUTHENTICATED check)
  // Skip if content is tampered — signature over corrupted data is meaningless
  result.signature = null; // true/false/null
  result.signatureDetail = '';
  result.tofu = null;
  if (record.signature && record.public_key && result.status !== 'tampered') {
    var identifier = record.identifier || knownIdentifier || record._identifier;
    var hash = record.content_hash || barContentHash;
    if (identifier && hash) {
      var thumbHash = await _thumbnailHashForSig(record);
      var sigOk = await verifySignature(identifier, hash, record.signature, record.public_key, thumbHash);
      result.signature = sigOk;
      result.keychain = null;
      if (sigOk === true) {
        result.signatureDetail = 'Ed25519 signature valid';

        // Keychain check — revocation or succession.
        //
        // peerRoot lets us also walk the keychain on the peer surface
        // the soul came from, not just IA. Chains that publish only
        // to peer mirrors (IA disabled in channels.json) still get
        // their keychain records discovered. peerKeychainRoot() returns
        // null for IA-sourced souls — those fall back to the existing
        // IA-only flow with no change.
        var peerRoot = (typeof peerKeychainRoot === 'function')
          ? peerKeychainRoot(record._source) : null;
        if (record.key_fingerprint) {
          // Keychain + alias enrichment is NETWORK (peer / IA) and only ever
          // DOWNGRADES the local verdict (revoked / rotated). The signature was
          // already verified locally above, so this must never block the cert.
          // Cap the whole block: if a degraded source (e.g. IA down) doesn't
          // answer in time, the cert renders with keychain undetermined — no
          // revocation found = active by default, the correct graceful
          // degradation. Per-fetch timeouts in verify.js bound each call;
          // this bounds the total.
          await Promise.race([
            (async function () {
              var kc = await checkKeychain(record.key_fingerprint, record.public_key, peerRoot);
              result.keychain = kc;
              if (kc.status === 'revoked') {
                result.signature = false;
                result.signatureDetail = 'Key REVOKED — ' + kc.detail;
              } else if (kc.status === 'rotated') {
                result.signatureDetail += ' (key rotated — ' + kc.detail + ')';
              }
              try {
                result.aliases = (typeof discoverAliases === 'function')
                  ? await discoverAliases(record.key_fingerprint, peerRoot) : [];
              } catch (e) { result.aliases = []; }
            })(),
            new Promise(function (resolve) { setTimeout(resolve, 7000); })
          ]);
        }

        // TOFU check (skip if revoked).
        //
        // Alias bridge: if this key is bidirectionally aliased to a key
        // already in TOFU, treat the new key as trusted under the same
        // name. The bidirectional alias is a cryptographic claim by
        // both sides — strong enough to extend trust without prompting.
        // One-way aliases do NOT auto-trust (B claims sibling-of-A
        // but A hasn't confirmed) — they're rendered with a softer
        // signal via the badge tooltip and stay 'new' here.
        if (result.signature === true && record.key_fingerprint) {
          var tofu = tofuStore();
          var tofuStatus = tofu.check(record.key_fingerprint, record.public_key);
          result.tofu = tofuStatus;
          if (tofuStatus === 'trusted') {
            var entry = tofu.get(record.key_fingerprint);
            // Self-rename: same pubkey, different declared name in the
            // record (e.g., dashboard "Save Name" changed the profile
            // label after first sighting). Pubkey is unchanged so this
            // isn't impersonation — the key updates its own display
            // name. The fingerprint+pubkey lock still anchors trust.
            var declared = (record.creator_name || '').trim();
            if (declared && declared !== entry.name) {
              tofu.set(record.key_fingerprint, declared, record.public_key);
              entry = tofu.get(record.key_fingerprint);
            }
            result.signatureDetail += ' — ' + entry.name + ' (trusted)';
          } else if (tofuStatus === 'new') {
            var trustedViaAlias = null;
            if (result.aliases && result.aliases.length) {
              for (var ai = 0; ai < result.aliases.length; ai++) {
                var a = result.aliases[ai];
                if (!a.bidirectional) continue;
                var sibling = tofu.get(a.alias_fingerprint);
                if (sibling) { trustedViaAlias = sibling; break; }
              }
            }
            if (trustedViaAlias) {
              // Pin the new fingerprint under the sibling's display
              // name so subsequent visits skip this check too. Inherits
              // trust via cryptographic alias chain, not a fresh TOFU.
              tofu.set(record.key_fingerprint, trustedViaAlias.name, record.public_key);
              result.tofu = 'trusted';
              result.signatureDetail += ' — ' + trustedViaAlias.name + ' (trusted via alias)';
            } else {
              result.signatureDetail += ' — new key, not yet named';
            }
          } else if (tofuStatus === 'conflict') {
            result.signatureDetail += ' — WARNING: different key for this fingerprint!';
          }
        }
      } else if (sigOk === false) {
        result.signatureDetail = 'Signature INVALID — possible forgery';
      } else {
        result.signatureDetail = 'Ed25519 not available in this browser';
      }
    }
  }

  return result;
}

// =====================================================================
// SOUL FILENAME HELPERS
// =====================================================================

function soulFilename(identifier, contentHash) {
  // The identifier is honest enough on its own. Old records on IA may
  // still be at {identifier}.{hash}.soul — the fetch probe handles them.
  // The contentHash arg is kept in the signature for backward compat.
  return identifier + '.soul';
}

function parseSoulInput(input) {
  input = input.trim();

  // Direct URL to a .soul or .json file — fetch directly. Identifier
  // extraction goes through the shared codec grammar so any per-chain
  // prefix (dark-, phoenix-, …) is recognized, not just mememage.
  if (/^https?:\/\//.test(input) && /\.(soul|json)(\?.*)?$/.test(input)) {
    return { identifier: extractIdentifier(input), contentHash: null, directUrl: input.split('?')[0] };
  }

  // Base URL ending with / — treat as source base
  if (/^https?:\/\//.test(input) && input.endsWith('/')) {
    return { identifier: extractIdentifier(input), contentHash: null, sourceBase: input };
  }

  // URL with identifier (e.g., archive.org page)
  if (/^https?:\/\//.test(input)) {
    var m3 = extractIdentifier(input);
    if (m3) return { identifier: m3, contentHash: null };
    return { identifier: null, contentHash: null, directUrl: input };
  }

  // soul.json filename (e.g., mememage-xxx.hash.soul.json)
  if (input.endsWith('.soul.json')) {
    input = input.replace('.json', '');
  }

  // .soul filename (e.g., mememage-xxx.hash.soul)
  if (input.endsWith('.soul')) {
    var parts = input.replace('.soul', '').split('.');
    if (parts.length >= 2) {
      var hash = parts.pop();
      var id = parts.join('.');
      return { identifier: id, contentHash: hash };
    }
  }

  // Bare identifier or hex — must match strictly end-to-end. An
  // unanchored match would silently truncate trailing junk (e.g.
  // "<prefix>-90dccd7b1233896ft4t4t" → "<prefix>-90dccd7b1233896f")
  // and validate a record the user didn't actually type.
  // normalizeIdentifier accepts any <prefix>-<16hex> (a bare 16-hex
  // string is sugar for the default mememage chain).
  var id2 = normalizeIdentifier(input);
  if (!id2) return { identifier: null, contentHash: null };
  return { identifier: id2, contentHash: null };
}

// =====================================================================
// SOURCE-AGNOSTIC METADATA RESOLVER
// =====================================================================

async function resolveMetadata(identifier, contentHash) {
  // 1. User-configured sources (localStorage)
  var userSources = [];
  try { userSources = JSON.parse(localStorage.getItem('mememage-sources') || '[]'); } catch(e) {}

  // 2. Built-in sources (IA)
  // .json first (IA sends CORS headers for .json), then .soul, then legacy metadata.json
  var soulName = soulFilename(identifier, contentHash);
  var jsonName = contentHash
    ? identifier + '.' + contentHash + '.json'
    : identifier + '.json';
  var builtinSources = [
    'https://archive.org/download/' + identifier + '/' + jsonName,
    'https://archive.org/download/' + identifier + '/' + soulName,
    'https://archive.org/download/' + identifier + '/metadata.json',
  ];

  // CORS fallbacks
  var corsSources = [
    'https://cors.archive.org/download/' + identifier + '/' + jsonName,
    'https://cors.archive.org/download/' + identifier + '/' + soulName,
    'https://cors.archive.org/download/' + identifier + '/metadata.json',
  ];

  var allSources = [];
  // User sources first
  for (var i = 0; i < userSources.length; i++) {
    allSources.push(userSources[i].replace('{id}', identifier).replace('{hash}', contentHash || ''));
  }
  // Then built-in
  allSources = allSources.concat(corsSources).concat(builtinSources);

  for (var si = 0; si < allSources.length; si++) {
    try {
      var resp = await _fetchTimeout(allSources[si] + '?t=' + Date.now(), {cache: 'no-store'}, 5000);
      if (!resp.ok) continue;
      var record = await resp.json();
      if (!record || typeof record !== 'object') continue;
      // V1 records keep gen params under `origin`; fall back to flat
      // fields for legacy records. "Has origin metadata" is the marker.
      var _ro = record.origin || {};
      if (!_ro.prompt && !_ro.seed && !record.prompt && !record.seed) continue;

      if (contentHash) {
        var computed = await computeContentHash(record);
        if (computed && computed !== contentHash) continue;
      }

      record._source = allSources[si];
      return record;
    } catch (e) {
      continue;
    }
  }

  // Last resort: IA metadata API — discover soul file by listing item contents
  try {
    var metaResp = await _fetchTimeout('https://archive.org/metadata/' + identifier + '?t=' + Date.now(), {cache: 'no-store'}, 5000);
    if (metaResp.ok) {
      var metaData = await metaResp.json();
      if (metaData && metaData.files) {
        for (var fi = 0; fi < metaData.files.length; fi++) {
          var fname = metaData.files[fi].name;
          if (fname.endsWith('.soul') || fname.endsWith('.json')) {
            try {
              var fResp = await _fetchTimeout('https://archive.org/download/' + identifier + '/' + fname + '?t=' + Date.now(), {cache: 'no-store'}, 5000);
              if (!fResp.ok) continue;
              var fRecord = await fResp.json();
              if (fRecord && (fRecord.prompt || fRecord.seed)) {
                if (contentHash) {
                  var fComputed = await computeContentHash(fRecord);
                  if (fComputed && fComputed !== contentHash) continue;
                }
                fRecord._source = 'https://archive.org/download/' + identifier + '/' + fname;
                return fRecord;
              }
            } catch(e2) { continue; }
          }
        }
      }
    }
  } catch(e) {}

  return null;
}

// =====================================================================
// UI
// =====================================================================
const dropZone=document.getElementById('dropZone'),fileInput=document.getElementById('fileInput');
const preview=document.getElementById('preview'),previewImg=document.getElementById('previewImg');
const statusEl=document.getElementById('status'),barCard=document.getElementById('barCard');
const iaLinkBanner=document.getElementById('iaLinkBanner');

function showStatus(msg,type){statusEl.textContent=msg;statusEl.className='console-status visible '+type;}
function hideStatus(){statusEl.className='console-status';}

// Per-tab error slots. A new error on one tab wipes the others —
// stale cross-tab errors shouldn't linger when the current tab has
// spoken. Actual write/clear logic lives in PanelError (portal.js);
// this file just declares the DOM mapping for the decoder's tabs.
PanelError.configure({
  imagePanel:  { body: 'imageError' },
  lookupPanel: { head: 'lookupErrorHead', body: 'lookupErrorBody' },
  verifyPanel: { body: 'verifyStatus', errorClass: true }
});
function clearOtherTabErrors(activeId) { PanelError.clearOthers(activeId); }
function showPanelError(head, body) {
  var active = document.querySelector('.input-panel.active');
  if (!active) return;
  PanelError.set(active.id, head, body);
}

// Tab ownership for the left-panel evidence elements + the cert
// itself. Each query stamps its tab id; on tab switches, owners that
// don't match the active tab pick up .tab-scope-hidden (display:none).
// certWrap participates so the cert only renders on the tab that
// produced it — switching to a tab without its own cert collapses
// the layout back to default. Mirrors the validator's per-tab slot
// behavior in syncResultsVisibility.
TabScope.configure(['preview', 'barCard', 'status', 'iaLinkBanner', 'certWrap', 'consoleBarInfo', 'downloadSoulBtn']);
function setTabOwner(tabId) { TabScope.setOwner(tabId); }
function clearTabOwners() { TabScope.clear(); }
// Tracks any in-flight cert-leaving fade so a rapid second tab click
// (back into the cert before the fade finishes) can cancel cleanly.
var _certLeavingTimer = null;
function applyTabScope(activeId) {
  var cw = document.getElementById('certWrap');
  var dm = document.querySelector('.panel-layout');
  if (!cw || !dm) { TabScope.apply(activeId); return; }

  var wasActive = dm.classList.contains('layout-active');
  // Peek at what TabScope.apply WILL do so we can decide whether to
  // run the leaving fade before applying it. willBeHidden mirrors
  // TabScope's owner-check.
  var willBeHidden = cw.dataset.owner && cw.dataset.owner !== activeId;
  var willOwnActiveCert = cw.classList.contains('visible') && !willBeHidden;

  // Leaving compact mode on desktop — fade the cert out first, then
  // remove layout-active so the system box width animation runs after
  // the cert has cleared. Sequenced to avoid the cert covering the
  // still-wide system box during its shrink/grow animation.
  if (wasActive && !willOwnActiveCert && window.innerWidth >= 1200
      && cw.classList.contains('visible')) {
    if (_certLeavingTimer) clearTimeout(_certLeavingTimer);
    cw.classList.remove('cert-entering');
    cw.classList.add('cert-leaving');
    // Apply TabScope to the OTHER scoped elements immediately, but
    // keep certWrap visible until the fade ends.
    TabScope.apply(activeId);
    cw.classList.remove('tab-scope-hidden');
    _certLeavingTimer = setTimeout(function() {
      _certLeavingTimer = null;
      cw.classList.remove('cert-leaving');
      cw.classList.add('tab-scope-hidden');
      dm.classList.remove('layout-active');
    }, 300);
    return;
  }

  // Default flow — apply TabScope, then sync layout-active.
  if (_certLeavingTimer) {
    clearTimeout(_certLeavingTimer);
    _certLeavingTimer = null;
    cw.classList.remove('cert-leaving');
  }
  TabScope.apply(activeId);
  var ownsActiveCert = cw.classList.contains('visible')
    && !cw.classList.contains('tab-scope-hidden');
  if (ownsActiveCert) {
    // Fresh entry into compact mode — delay the cert fade-in until
    // the system box has finished its shrink animation.
    if (!wasActive) holdCertEntering(cw);
    dm.classList.add('layout-active');
  } else {
    dm.classList.remove('layout-active');
  }
}

function resetAll(){
  // Fade out cosmic audio before destroying the certificate
  if (typeof CosmicPlayer !== 'undefined') CosmicPlayer.dismiss();
  // Clear sample mode flag so real certs get full rendering
  window._sampleMode = false;
  // Clear the dropped-image canvas so a soul-only fetch (By Word,
  // example, etc.) can't inherit EMBODIED from a prior By Sight drop.
  // The portrait comparison in fetchAndRender gates on this being set;
  // By Sight's processImage reassigns it after calling resetAll, so
  // the image-drop path still gets EMBODIED evaluated.
  window._lastDecodedCanvas = null;
  var oldSoulBtn = document.getElementById('downloadSoulBtn');
  if (oldSoulBtn) oldSoulBtn.remove();

  barCard.querySelectorAll('.bar-field').forEach(function(f){f.remove();});
  barCard.classList.remove('visible');
  var barInfo = document.getElementById('consoleBarInfo');
  if (barInfo) barInfo.remove();
  var cw = document.getElementById('certWrap');
  // NOTE: do NOT remove .visible here. PanelSwap owns visibility during
  // a swap; removing it mid-callback lets renderCert see wasVisible=false
  // and re-add .visible, which fires panelFadeIn *on top of* PanelSwap's
  // intro — that's the double-intro bug. If renderFn produces no content
  // (error path), PanelSwap auto-dismisses the panel after renderFn.
  cw.innerHTML = '';
  iaLinkBanner.className='console-ia';
  iaLinkBanner.innerHTML = '';
  preview.className='console-preview';
  hideStatus();
  // Clear inline per-tab error text too.
  var imgErr = document.getElementById('imageError');
  if (imgErr) imgErr.textContent = '';
  var leH = document.getElementById('lookupErrorHead');
  var leB = document.getElementById('lookupErrorBody');
  if (leH) leH.innerHTML = '';
  if (leB) leB.innerHTML = '';
  // Clear every tab's left-panel input state so a new query from any tab
  // doesn't leave stale inputs in the others. The shared result area is
  // the source of truth once a cert renders; inputs only hold pending
  // queries. fetchAndRender repopulates lookupInput with the resolved
  // identifier after this clear, so the active tab still shows context.
  var li = document.getElementById('lookupInput');
  if (li) li.value = '';
  var vs = document.getElementById('verifyStatus');
  if (vs) vs.textContent = '';
  var viSlot = document.getElementById('verifyImageSlot');
  if (viSlot) viSlot.classList.remove('ready');
  var vjSlot = document.getElementById('verifyJsonSlot');
  if (vjSlot) vjSlot.classList.remove('ready');
  // Clear tab ownership — every new query re-stamps its owner below.
  clearTabOwners();
  // Example-active is set by the "see an example" flow and must be
  // re-applied inside that callback if it wants to survive. Every other
  // query (By Sight / By Word / By Soul) implicitly dismisses the
  // example panel by clearing this class here.
  var inSec = document.querySelector('.input-section');
  if (inSec) inSec.classList.remove('example-active');
  var tryLbl = document.getElementById('tryExampleLink');
  if (tryLbl) tryLbl.textContent = 'see an example';
  // NOTE: do NOT remove layout-active here. PanelSwap owns the right-
  // panel lifecycle during a swap; stripping layout-active mid-callback
  // flips the panel from fixed to static layout for the duration of the
  // fetch, then renderCert flips it back — a double layout shift that
  // reads as a flash. The empty-panel branch of PanelSwap.runIntro
  // handles layout-active removal for dismiss paths.
  window.scrollTo({top: 0, behavior: 'smooth'});
}

function displaySource(sourceUrl, identifier, contentHash) {
  var name = 'Unknown';
  if (!sourceUrl) name = '';
  else if (/^local:/i.test(sourceUrl)) name = 'Local file';
  else if (sourceUrl.includes('cors.archive.org') || sourceUrl.includes('archive.org')) name = 'Internet Archive';
  else { try { name = new URL(sourceUrl).hostname; } catch(e) { name = 'External'; } }
  return { name: name, soulName: soulFilename(identifier, contentHash) };
}

function renderBar(data) {
  // Identifier + hash rendered as a single inline-pill badge. Previously
  // these were two stacked centered lines; the user-facing read is that
  // they're one chunk of identity (link to discover, fingerprint to
  // verify), so one container with the two spaced by a gap reads cleaner
  // than two horizontal lines that visually divide the space.
  var barInfo = document.getElementById('consoleBarInfo');
  if (!barInfo) {
    barInfo = document.createElement('div');
    barInfo.id = 'consoleBarInfo';
    barInfo.style.cssText = 'padding:0.5rem 1.2rem;border-top:1px solid rgba(0,0,0,0.06);text-align:center;';
    statusEl.parentNode.insertBefore(barInfo, statusEl);
  }
  if (data.identifier) {
    var idHtml = '<a href="#" class="lookup-link" data-id="' + data.identifier + '" style="color:#2a2a32;text-decoration:none;border-bottom:1px dotted rgba(0,0,0,0.3);cursor:pointer;font-weight:600;">' + data.identifier + '</a>';
    var html =
      '<span style="' +
        'display:inline-flex;align-items:baseline;gap:0.55rem;' +
        'padding:0.3rem 0.85rem;' +
        'background:rgba(0,0,0,0.04);' +
        'border:1px solid rgba(0,0,0,0.08);' +
        'border-radius:999px;' +
        'font-family:ui-monospace,Menlo,monospace;font-size:0.78rem;' +
        'text-shadow:0 1px 0 rgba(255,255,255,0.35);' +
        'max-width:100%;flex-wrap:wrap;justify-content:center;' +
      '">' + idHtml;
    if (data.content_hash) {
      html +=
        '<span style="font-size:0.7rem;color:#5a5a64;">' + data.content_hash + '</span>';
    }
    html += '</span>';
    barInfo.innerHTML = html;
  }
  // Re-stamp tab ownership — barInfo may have been freshly created
  // after the calling tab's setTabOwner ran, so the original stamp
  // missed it. refresh() re-runs with the cached last-owner.
  TabScope.refresh();
}

async function fetchFromSource(baseUrl, identifier, contentHash) {
  // {id} templating — base URLs may include {id} as a placeholder for
  // the record identifier, which lets a single input express both
  // self-host ("https://yourhost.com/") and per-item layouts like IA's
  // ("https://archive.org/download/{id}/"). Expand first, then
  // normalize trailing slash, then append the filename variants.
  var expanded = baseUrl.replace(/\{id\}/g, identifier);
  if (!expanded.endsWith('/')) expanded = expanded + '/';

  // Probe order: simple form first (canonical self-host name), then
  // mint-pipeline hashed form if we know the hash (archive.org's
  // actual file naming). Every miss is silent — CORS, network, 404 all
  // fall through to the next candidate.
  var candidates = [
    expanded + identifier + '.soul',
    expanded + identifier + '.json'
  ];
  if (contentHash) {
    candidates.push(expanded + identifier + '.' + contentHash + '.soul');
    candidates.push(expanded + identifier + '.' + contentHash + '.json');
  }
  for (var i = 0; i < candidates.length; i++) {
    try {
      var resp = await _fetchTimeout(candidates[i] + '?t=' + Date.now(), {cache: 'no-store'}, 5000);
      if (!resp.ok) continue;
      var record = await resp.json();
      if (!record || typeof record !== 'object') continue;
      // Accept any record with a recognizable Mememage field — don't
      // reject on missing `prompt` (protected records strip it, and
      // some flows have it behind the password layer).
      var _ro2 = record.origin || {};
      if (!_ro2.prompt && !_ro2.seed && !record.prompt && !record.seed && !record.content_hash) continue;
      if (contentHash) {
        var computed = await computeContentHash(record);
        if (computed && computed !== contentHash) continue;
      }
      record._source = candidates[i];
      return record;
    } catch (e) {
      // continue — most likely CORS, mixed content, or network
    }
  }
  return null;
}

async function fetchAndRender(identifier, barContentHash, directUrl, sourceBase) {
  // During fetch: don't add anything below the panel (no console-status
  // "Searching..." and no IA banner). Both contribute to the system-
  // box height, and the user sees the box grow briefly then shrink
  // when the cert renders. Status + IA banner are painted after a
  // successful render instead.
  var meta = null;
  var offlineMode = SourceConfig.getMode('source') === 'offline';

  // Offline cache first — always consulted. Folder-picker, prior By
  // Soul drops, and Observatory drops all populate OfflineRecords.
  if (!directUrl && identifier) {
    var cached = OfflineRecords.get(identifier);
    if (cached) meta = cached;
  }

  // Network path — only if user hasn't explicitly picked Offline mode.
  if (!meta && !offlineMode) {
    // Direct URL — fetch exactly that
    if (directUrl) {
      try {
        var resp = await _fetchTimeout(directUrl + '?t=' + Date.now(), {cache: 'no-store'}, 5000);
        if (resp.ok) {
          meta = await resp.json();
          if (meta) meta._source = directUrl;
        }
      } catch(e) {}
    }

    // Source base URL — expand {id} template + probe filename variants.
    if (!meta && sourceBase && identifier) {
      meta = await fetchFromSource(sourceBase, identifier, barContentHash);
    }

    // Archive.org fallback — /metadata/ API for filename discovery.
    if (!meta && identifier && sourceBase && /archive\.org/.test(sourceBase)) {
      meta = await resolveMetadata(identifier, barContentHash);
    }
  }

  if (!meta) {
    var soul = soulFilename(identifier, barContentHash);
    var lookupInput = document.getElementById('lookupInput');
    if (lookupInput && identifier) lookupInput.value = identifier;
    hideStatus();
    iaLinkBanner.className = 'console-ia';
    iaLinkBanner.innerHTML = '';
    if (offlineMode) {
      // Offline-mode miss: user picked Offline but the identifier
      // isn't in the cache. Point them at loading a folder or
      // switching back to Online.
      showPanelError(
        'Not in the offline cache.',
        'Identifier <strong>' + escapeHtml(identifier) + '</strong> isn\u2019t among ' +
        OfflineRecords.count() + ' record(s) currently loaded. ' +
        'Load a different folder under <em>Source</em>, or switch to <em>Online</em>.'
      );
    } else {
      // Online miss: probe links + CORS hint.
      var base = (sourceBase || (directUrl ? directUrl.replace(/\/[^/]*$/, '') : ''));
      var probeHtml = buildProbeLinks(base, identifier, barContentHash);
      showPanelError(
        'Could not find soul automatically.',
        probeHtml
          ? 'Open file in a new tab to verify:<br>' + probeHtml
          : 'Self-hosting? Your server needs <code>Access-Control-Allow-Origin: *</code>.'
      );
    }
    return false;
  }

  // Verify authenticity
  var v = await verifyRecord(meta, barContentHash, identifier);

  // Portrait comparison (EMBODIED check) — only when image was dropped
  v.portrait = null; // {match: true/false/null, distance, threshold}
  if (window._lastDecodedCanvas && meta.thumbnail) {
    v.portrait = await comparePortrait(window._lastDecodedCanvas, meta.thumbnail, meta.luma_grid);
  }

  // TOFU naming — first time seeing a valid signature. If the record
  // carries a creator_name, auto-trust silently. If not, store the key
  // as anonymous trust (no prompt). We don't interrogate the viewer
  // for a name — the cert's job is to show the 3 badges (WITNESSED /
  // AUTHENTICATED / EMBODIED), not pull them into key management.
  // Native prompt() especially on iOS displays the fingerprint, which
  // is exactly the "key display" the cert is designed to hide behind
  // the AUTHENTICATED badge.
  if (v.signature === true && v.tofu === 'new' && meta.key_fingerprint) {
    var suggestedName = meta.creator_name || '';
    if (suggestedName) {
      tofuStore().set(meta.key_fingerprint, suggestedName, meta.public_key);
      v.signatureDetail = 'Ed25519 signature valid — ' + suggestedName + ' (trusted)';
      v.tofu = 'trusted';
    } else {
      // Unnamed key — silently TOFU-trust. The cert still renders the
      // AUTHENTICATED badge; "(unnamed)" goes into the tooltip if a
      // viewer hovers, no dialog.
      tofuStore().set(meta.key_fingerprint, '', meta.public_key);
      v.signatureDetail = 'Ed25519 signature valid (unnamed key — trusted on first use)';
      v.tofu = 'trusted';
    }
  }

  meta._verification = v;
  meta._identifier = identifier;
  meta._content_hash = meta.content_hash || barContentHash || null;

  renderCert(meta);

  if (v.status === 'verified') {
    showStatus('Full metadata recovered. The body and soul are joined.', 'success');
  } else if (v.status === 'tampered') {
    showStatus('WARNING: Record may have been altered. ' + v.detail, 'error');
  } else {
    showStatus('Full metadata recovered.', 'success');
  }

  if (meta._source) {
    var src = displaySource(meta._source, identifier, barContentHash);
    if (src.name) {
      var srcDiv = document.createElement('div');
      srcDiv.style.cssText = 'font-size:0.62rem;color:#5a5a64;text-align:center;margin-top:0.2rem;';
      var detailsUrl = 'https://archive.org/details/' + identifier;
      if (src.name === 'Internet Archive') {
        srcDiv.innerHTML = 'Recovered from <a href="' + detailsUrl + '" target="_blank" style="color:#2a4a6a;text-decoration:none;">' + src.name + '</a>';
      } else {
        srcDiv.textContent = 'Recovered from ' + src.name;
      }
      statusEl.appendChild(srcDiv);
    }
  }

  // Download Soul button — saves the in-memory soul JSON without
  // re-fetching (source may be unreachable due to CORS, or use a
  // different file extension). Skipped in the example flow — the
  // example cert is read-only.
  var existingSoulBtn = document.getElementById('downloadSoulBtn');
  if (existingSoulBtn) existingSoulBtn.remove();
  var _inExampleFlow = document.querySelector('.input-section').classList.contains('example-active');
  if (!_inExampleFlow && identifier && meta.content_hash) {
    var soulFile = soulFilename(identifier, meta.content_hash);
    var soulBtn = document.createElement('button');
    soulBtn.id = 'downloadSoulBtn';
    soulBtn.textContent = 'Download Soul';
    soulBtn.className = 'download-soul-btn';
    soulBtn.addEventListener('click', function() {
      try {
        var clean = {};
        Object.keys(meta).forEach(function(k) {
          if (k.charAt(0) !== '_') clean[k] = meta[k];
        });
        var blob = new Blob([JSON.stringify(clean, null, 2)], {type: 'application/json'});
        var a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = soulFile;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(function() { URL.revokeObjectURL(a.href); }, 1000);
      } catch (e) {
        console.error('Save soul failed:', e);
      }
    });
    // Sit between "Recovered from …" (last child of #status) and
    // the "see an example" link — slotted directly before
    // #tryExample at input-section level so both the source line
    // above and the example/portal links below stay in their
    // native positions.
    var inputSection = document.querySelector('.input-section');
    var tryEx = document.getElementById('tryExample');
    if (inputSection && tryEx && tryEx.parentElement === inputSection) {
      inputSection.insertBefore(soulBtn, tryEx);
    } else if (inputSection) {
      inputSection.appendChild(soulBtn);
    }
  }

  // Re-stamp tab ownership — soulBtn was just created after the
  // calling tab's setTabOwner ran. refresh() picks it up.
  TabScope.refresh();

  return true;
}

function processImage(file){
  var certWrap = document.getElementById('certWrap');
  // Cross-fade: if a prior cert is on screen, outtro it before the new
  // flow replaces the panel. The async body runs inside the callback
  // and PanelSwap waits for the new cert to render before the intro.
  PanelSwap(certWrap, async function() {
    resetAll();
    // Errors go to the inline #imageError element inside #imagePanel
    // (mirrors validator's error-only img-console): no right panel,
    // no compact, no layout shift. The shared console-status is
    // reserved for the success path so the transition doesn't flash
    // a taller "Reading pixels..." banner before the compact error.
    var imgErr = document.getElementById('imageError');
    var setImgError = function(msg) {
      if (imgErr) imgErr.textContent = msg;
      if (msg) clearOtherTabErrors('imagePanel');
    };
    setImgError('');

    // Shared decode pipeline — load image, draw to canvas, run
    // detectBar + extractBits + decodeFrame + decodePayload. Returns
    // {ok, detected, frame, decoded, canvas, objUrl, error, …}.
    var res = await decodeImageBar(file);
    if (!res.ok) { setImgError(res.error); return; }
    var decoded = res.decoded;
    var canvas = res.canvas;
    var objUrl = res.objUrl;
    // No transient "Decoding..." status — it flashes below the panel
    // and grows the system box briefly. Success status lands after the
    // cert renders instead.

    // Success path — now safe to show the preview image (compact triggers
    // activate from this point). Stash canvas for EMBODIED portrait check.
    // Stamp tab ownership so this state hides when the user switches away.
    setTabOwner('imagePanel');
    previewImg.src = objUrl;
    preview.className = 'console-preview visible';
    previewImg.style.cursor = 'pointer';
    window._lastDecodedCanvas = canvas;

    renderBar(decoded);

    // Source config — shared across By Sight + By Word via the
    // 'source' prefix. Single URL field; fetchFromSource expands {id}.
    // Empty field → fall back to the placeholder default. The greyed-out
    // text the user sees IS the value they get when they clear the field;
    // leaving the value as "" would silently break the lookup.
    var imgSourceEl = document.getElementById('imageSource');
    var imgSourceBase = (imgSourceEl && imgSourceEl.value.trim()) || SOURCE_DEFAULT;

    // Swap the drop-hint to "Fetching…" for the network wait — same
    // pattern as By Word's .lookup-hint + validator's .how. Visible on
    // the first drop (before compact mode kicks in); subsequent drops
    // have the hint collapsed, same behavior as the other surfaces.
    // try/finally guarantees restore on success and error paths alike.
    var hintEl = document.querySelector('#dropZone .drop-hint');
    var hintOriginal = hintEl ? hintEl.innerHTML : '';
    if (hintEl) hintEl.innerHTML = '<em style="color:var(--text-muted);">Fetching\u2026</em>';
    try {
      // 20s hard timeout so a silent network hang (iOS Safari can
      // leave fetches pending without firing error events in some
      // CORS/mixed-content paths) doesn't leave the user staring at
      // "Fetching…" indefinitely. Race the real work against a
      // rejection that surfaces the failure to the catch handler.
      await Promise.race([
        fetchAndRender(decoded.identifier, decoded.content_hash || null, null, imgSourceBase),
        new Promise(function(_, reject) {
          setTimeout(function() { reject(new Error('Fetch timed out after 20s')); }, 20000);
        })
      ]);
    } catch (err) {
      console.error('[processImage] fetch failed:', err);
      setImgError('Fetch failed: ' + (err && err.message ? err.message : 'unknown error') + '. Check the Source URL and CORS.');
    } finally {
      if (hintEl) hintEl.innerHTML = hintOriginal;
    }
  });
}

function lookupById(input, pushHistory){
  var certWrap = document.getElementById('certWrap');
  // Swap the .lookup-hint prose box text to "Fetching…" for the
  // duration of the fetch — mirrors validator's setHow('loading', …)
  // which targets the same structural role (.how prose box under the
  // input form). Visible on the first fetch (before compact mode
  // kicks in); subsequent fetches the box is collapsed — same as
  // validator, consistent story. try/finally guarantees restore.
  var hintEl = document.querySelector('.lookup-hint');
  var hintOriginal = hintEl ? hintEl.innerHTML : '';
  PanelSwap(certWrap, async function() {
    try {
    resetAll();
    // Swap in "Fetching…" after resetAll (which doesn't touch the hint
    // directly, but being explicit). Dim styling so it reads as status,
    // not prose.
    if (hintEl) hintEl.innerHTML = '<em style="color:var(--text-muted);">Fetching\u2026</em>';
    var parsed = parseSoulInput(input);
    if (!parsed.identifier && !parsed.directUrl) {
      setTabOwner('lookupPanel');
      showPanelError(
        'Invalid identifier.',
        'Expected <strong>&lt;prefix&gt;-&lt;hex&gt;</strong> (e.g. mememage-…), or a URL containing one.'
      );
      return;
    }
    setTabOwner('lookupPanel');
    if(parsed.identifier && pushHistory!==false) history.pushState({id: parsed.identifier},'','#');

    // Source config — single URL field. If the user pasted a full
    // URL, parseSoulInput already set directUrl and that wins. If they
    // only typed an identifier, use the configured Source base URL.
    // {id} templating in the base is expanded by fetchFromSource.
    // Empty field → placeholder default. See note at imgSourceBase above.
    var sourceEl = document.getElementById('lookupSource');
    var base = (sourceEl && sourceEl.value.trim()) || SOURCE_DEFAULT;

    var directUrl = parsed.directUrl;
    var sourceBase = parsed.sourceBase;
    if (!directUrl && parsed.identifier && base) {
      sourceBase = sourceBase || base;
    }

    // Mixed-content pre-check: https pages can't fetch http resources.
    // Browsers block silently, so explain it here before fetch fails.
    var checkUrl = directUrl || sourceBase || '';
    if (location.protocol === 'https:' && /^http:\/\//i.test(checkUrl)) {
      // Top-level navigation isn't blocked by mixed content — clicking
      // the probe link opens the file directly in a new tab, where the
      // user can save it and drop it into By Soul.
      var mcBase = sourceBase || (directUrl ? directUrl.replace(/\/[^/]*$/, '') : '');
      showPanelError(
        'Mixed content blocked for ' + escapeHtml(parsed.identifier),
        'This page is served over HTTPS, but the source is HTTP \u2014 browsers block this silently.<br>' +
        'Open in a new tab (mixed-content rules don\u2019t apply to top-level navigation):<br>' +
        buildProbeLinks(mcBase, parsed.identifier, parsed.contentHash) + '<br>' +
        'Save and drop it into <em>By Soul</em>.'
      );
      return;
    }

    // By Word is always a soul-only fetch (no image, no bar, no body).
    // Render the resulting cert as a sample — bands, music player, GPS,
    // and save button are skipped, matching the example cert. The full
    // cert is reserved for paths where the body is present: By Sight
    // (image dropped, bar + watermark read) and By Soul (image + soul
    // file paired). renderCert consumes the flag once and resets it.
    window._sampleMode = true;
    await fetchAndRender(parsed.identifier, parsed.contentHash, directUrl, sourceBase);
    } finally {
      if (hintEl) hintEl.innerHTML = hintOriginal;
    }
  });
}

// Image lightbox — click a thumbnail to view it full size in a shadow-box
// overlay. Shared by the By Sight console preview AND the By Soul body preview
// so both behave identically (the By Soul preview used to be a dead image).
function attachLightbox(imgEl) {
  if (!imgEl) return;
  imgEl.style.cursor = 'zoom-in';
  imgEl.addEventListener('click', function() {
    if (!imgEl.src) return;
    var overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.85);display:flex;align-items:center;justify-content:center;cursor:pointer;padding:2rem;';
    var fullImg = document.createElement('img');
    fullImg.src = imgEl.src;
    fullImg.style.cssText = 'max-width:90vw;max-height:90vh;object-fit:contain;border-radius:8px;box-shadow:0 4px 40px rgba(0,0,0,0.5);';
    overlay.appendChild(fullImg);
    overlay.addEventListener('click', function() { overlay.remove(); });
    document.addEventListener('keydown', function esc(e) { if (e.key === 'Escape') { overlay.remove(); document.removeEventListener('keydown', esc); } });
    document.body.appendChild(overlay);
  });
}
attachLightbox(previewImg);
attachLightbox(document.getElementById('verifyPreviewImg'));

// Back/forward navigation
window.addEventListener('popstate', e => {
  if(e.state && e.state.id) lookupById(e.state.id, false);
});

// Tabs — class toggling is shared; the onChange hook reapplies tab
// scoping so evidence elements (preview, bar-card, status, iaLinkBanner,
// certWrap) hide when their owner tab isn't active and reappear on
// return. Preserves state until another query overwrites it.
TabBar.wire(function(activeId) { applyTabScope(activeId); });

// Delegated click handler for .lookup-link anchors — pre-fill By Word
// input + switch to that tab. Tab scoping hides the prior By Sight
// evidence (preview, cert, status, etc.) automatically; state is
// preserved so returning to By Sight restores everything until a new
// query runs. Mirrors validator's .audit-link behavior.
LinkClick.delegate('.lookup-link', function(id) {
  var input = document.getElementById('lookupInput');
  if (input) input.value = id;
  TabBar.activateById('lookupPanel');
  applyTabScope('lookupPanel');
});

// By Sight drop zone — bound fileInput + paste support.
DropZone.attach({
  zone: dropZone,
  input: fileInput,
  accept: function(f) { return f.type.startsWith('image/'); },
  onFiles: processImage
});
document.addEventListener('paste',e=>{
  // Clipboard paste routes by what was pasted (mirrors the validator):
  //   - an image  → By Sight (decode the bar)
  //   - a .soul    → By Soul (the soul slot)
  //   - already in By Soul → fill its slots in place (image=body, soul=meta),
  //     don't yank the user to another tab.
  // A plain TEXT paste (an identifier) has neither a file nor an image, so we
  // bail and let it land in the focused input — that's By Word / Audit's job.
  if(!e.clipboardData)return;
  var files=Array.from(e.clipboardData.files||[]);
  var imgFile=files.find(f=>f.type&&f.type.indexOf('image/')===0);
  if(!imgFile){var it=Array.from(e.clipboardData.items||[]).find(i=>i.type&&i.type.indexOf('image/')===0);if(it)imgFile=it.getAsFile();}
  var soulFile=files.find(f=>!(f.type&&f.type.indexOf('image/')===0)&&(/\.(soul|json)$/i.test(f.name||'')||f.type==='application/json'));
  if(!imgFile&&!soulFile)return;  // text paste — leave it alone
  e.preventDefault();
  var vp=document.getElementById('verifyPanel');
  if(vp&&vp.classList.contains('active')){
    if(imgFile)processVerifyImage(imgFile);
    if(soulFile)processVerifyJson(soulFile);
    return;
  }
  if(imgFile){TabBar.activateById('imagePanel');processImage(imgFile);}
  else if(soulFile){TabBar.activateById('verifyPanel');processVerifyJson(soulFile);}
});
document.getElementById('lookupBtn').addEventListener('click',()=>lookupById(document.getElementById('lookupInput').value));
document.getElementById('lookupInput').addEventListener('keydown',e=>{if(e.key==='Enter')lookupById(e.target.value);});

// Single Source URL, shared across By Sight + By Word. Default points
// at IA's download directory with {id} as a templating placeholder —
// the decoder expands {id} to the record's identifier before fetching.
// Self-hosters leave {id} out (`https://yourhost.com/` etc.) and the
// decoder appends `{id}.soul` directly. Two UIs registered under the
// same 'source' prefix; SourceConfig mirrors edits between them.
// Storage key: mememage-source-url.
// SOURCE_DEFAULT is defined in data.js (shared with validator).
function _sourceLabelFor(inputId) {
  var input = document.getElementById(inputId);
  if (!input) return null;
  return input.parentElement ? input.parentElement.querySelector('label') : null;
}
function _sourceWrapFor(inputId) {
  var input = document.getElementById(inputId);
  if (!input) return null;
  var details = input.closest('.lookup-source');
  return details || null;
}
function _sourceModeFor(details) {
  return details ? details.querySelector('[data-source-mode-select]') : null;
}
var _lookupWrap = _sourceWrapFor('lookupSource');
var _imageWrap = _sourceWrapFor('imageSource');
SourceConfig.init({
  prefix: 'source',
  baseEl: document.getElementById('lookupSource'),
  defaultUrl: SOURCE_DEFAULT,
  placeholder: SOURCE_DEFAULT,
  resetEl: _sourceLabelFor('lookupSource'),
  modeEl: _sourceModeFor(_lookupWrap),
  modeContainer: _lookupWrap
});
SourceConfig.init({
  prefix: 'source',
  baseEl: document.getElementById('imageSource'),
  defaultUrl: SOURCE_DEFAULT,
  placeholder: SOURCE_DEFAULT,
  resetEl: _sourceLabelFor('imageSource'),
  modeEl: _sourceModeFor(_imageWrap),
  modeContainer: _imageWrap
});

// Offline records — folder picker + shared cache. Each Source section
// has a "Load local folder" button (data-offline-pick) that populates
// OfflineRecords; fetchAndRender hits the cache before the network.
OfflineRecords.bindUI();

// =====================================================================
// OFFLINE VERIFY — image + .soul/.json pair, no internet required
// =====================================================================
var verifyImageDrop = document.getElementById('verifyImageDrop');
var verifyJsonDrop = document.getElementById('verifyJsonDrop');
var verifyImageInput = document.getElementById('verifyImageInput');
var verifyJsonInput = document.getElementById('verifyJsonInput');
var verifyStatusEl = document.getElementById('verifyStatus');

var verifyState = { barHash: null, barIdentifier: null, jsonMeta: null };

function updateVerifyStatus(msg, isError) {
  verifyStatusEl.textContent = msg;
  verifyStatusEl.classList.toggle('error', !!isError);
  if (isError) clearOtherTabErrors('verifyPanel');
}

function tryVerifyPair() {
  if (!verifyState.barHash || !verifyState.jsonMeta) return;
  var certWrap = document.getElementById('certWrap');
  var barHash = verifyState.barHash;
  var meta = verifyState.jsonMeta;

  // Hash-check first, outside PanelSwap. A mismatched pair shouldn't
  // be allowed to replace a valid cert the user was already viewing —
  // so we determine the verification status, gate on tampered, and
  // only enter the PanelSwap/resetAll/renderCert flow for a real match.
  computeContentHash(meta).then(async function(computed) {
    var storedHash = meta.content_hash || null;
    var vf;

    if (barHash === storedHash && computed === storedHash) {
      vf = {status: 'verified', detail: 'Hash match — body and soul joined, sealed by spirit'};
    } else if (barHash === storedHash && computed && computed !== storedHash) {
      vf = {status: 'tampered', detail: 'Content modified after creation — hash field preserved but content changed'};
    } else if (barHash === storedHash && !computed) {
      vf = {status: 'bar_verified', detail: 'Bar and stored hash match (crypto API unavailable for recompute)'};
    } else if (barHash && computed && barHash === computed) {
      vf = {status: 'verified', detail: 'Hash match — body and soul joined by recomputation'};
    } else if (barHash && storedHash && barHash !== storedHash) {
      vf = {status: 'tampered', detail: 'Hash mismatch — soul rejects the body'};
    } else if (barHash && !storedHash && computed && barHash !== computed) {
      vf = {status: 'tampered', detail: 'Hash mismatch — metadata may have been modified'};
    } else {
      vf = {status: 'bar_verified', detail: 'Offline witness (crypto API limited or legacy serialization)'};
    }

    // Mismatch — show the error inline in By Soul's status line and
    // leave the existing cert (if any) untouched. The user was looking
    // at something valid before; refusing to replace it with a tampered
    // render is the whole point of "body and soul don't belong together".
    if (vf.status === 'tampered') {
      updateVerifyStatus('These do not belong together. ' + vf.detail, true);
      return;
    }

    PanelSwap(certWrap, function() { return new Promise(function(resolveSwap) {
    resetAll();
    setTabOwner('verifyPanel');

    (async function() {
    // Signature check — skip if tampered (signature over corrupted data is meaningless)
    vf.signature = null;
    vf.signatureDetail = '';
    vf.tofu = null;
    if (meta.signature && meta.public_key && vf.status !== 'tampered') {
      var id = meta.identifier || verifyState.barIdentifier;
      var hash = meta.content_hash || barHash;
      if (id && hash) {
        var thumbHash = await _thumbnailHashForSig(meta);
        var sigOk = await verifySignature(id, hash, meta.signature, meta.public_key, thumbHash);
        vf.signature = sigOk;
        if (sigOk === true) {
          vf.signatureDetail = 'Ed25519 signature valid';
          if (meta.key_fingerprint) {
            var tofu = tofuStore();
            var ts = tofu.check(meta.key_fingerprint, meta.public_key);
            vf.tofu = ts;
            if (ts === 'trusted') {
              var e = tofu.get(meta.key_fingerprint);
              // Self-rename: same pubkey can update its own display name.
              var declared2 = (meta.creator_name || '').trim();
              if (declared2 && declared2 !== e.name) {
                tofu.set(meta.key_fingerprint, declared2, meta.public_key);
                e = tofu.get(meta.key_fingerprint);
              }
              vf.signatureDetail += ' — ' + e.name + ' (trusted)';
            }
            else if (ts === 'new') {
              // Silent TOFU — see the same-named block in offlineVerify()
              // above for rationale. No prompt(); the cert renders the 3
              // badges without dragging the viewer into key management.
              var sName = meta.creator_name || '';
              tofu.set(meta.key_fingerprint, sName, meta.public_key);
              vf.signatureDetail += sName
                ? (' — ' + sName + ' (trusted)')
                : ' — (unnamed key, trusted on first use)';
              vf.tofu = 'trusted';
            } else { vf.signatureDetail += ' — WARNING: key conflict!'; }
          }
        } else if (sigOk === false) { vf.signatureDetail = 'Signature INVALID — possible forgery'; }
        else { vf.signatureDetail = 'Ed25519 not available in browser'; }
      }
    }

    // Portrait check
    vf.portrait = null;
    if (verifyState.verifyCanvas && meta.thumbnail) {
      vf.portrait = await comparePortrait(verifyState.verifyCanvas, meta.thumbnail, meta.luma_grid);
    }

    meta._verification = vf;
    meta._content_hash = barHash;
    meta._identifier = meta.identifier || verifyState.barIdentifier || null;
    // By Soul HOLDS the body (the dropped image), so it has earned the full
    // body view — the gen/sky/machine canvas bands + BIRTHPLACE that cert
    // renderer gates on window._lastDecodedCanvas. By Sight sets this; By Soul
    // must too, else providing both halves still rendered a partial read.
    window._lastDecodedCanvas = verifyState.verifyCanvas || null;
    renderCert(meta);

    if (vf.status === 'verified') {
      showStatus('Body and soul are one. Witnessed.', 'success');
    } else {
      showStatus('Reunited. Witness partial.', 'success');
    }
    resolveSwap();
    })();
  }); });
  });
}

async function processVerifyImage(file) {
  verifyState.barHash = null;
  verifyState.barIdentifier = null;
  document.getElementById('verifyImageSlot').classList.remove('ready');
  var _vPrev = document.getElementById('verifyPreview');
  if (_vPrev) _vPrev.hidden = true;  // clear any prior preview while we read
  updateVerifyStatus('Reading bar from image...');

  // Detect the bar with the SAME ladder as By Sight (decodeImageBar: fast
  // bottom read → vertical scan → full-canvas anywhere scan). This inline path
  // used to call extractBarScaleAware directly, which does only bottom +
  // vertical scan — so a bar that By Sight finds off-position or pasted into a
  // larger canvas failed By Soul with "no bar detected." The two paths must
  // agree: if By Sight decodes it, By Soul must too. (decodeImageBar's canvas
  // is created willReadFrequently, so the downstream dHash/luma-grid reads are
  // clean.) In a multi-bar image this takes the same primary bar By Sight does.
  var res = await decodeImageBar(file);
  if (!res || !res.decoded) {
    updateVerifyStatus(res && res.detected ? 'Bands present but data unreadable.'
                                           : 'No bar detected in image.', true);
    return;
  }
  var decoded = res.decoded;
  if (!decoded.content_hash) { updateVerifyStatus('No content hash in bar.', true); return; }

  verifyState.barHash = decoded.content_hash;
  verifyState.barIdentifier = decoded.identifier;
  verifyState.verifyCanvas = res.canvas;
  document.getElementById('verifyImageSlot').classList.add('ready');
  // Show what was dropped, beneath the Body/Soul slots.
  var _vImg = document.getElementById('verifyPreviewImg');
  if (_vPrev && _vImg) { _vImg.src = URL.createObjectURL(file); _vPrev.hidden = false; }
  updateVerifyStatus(verifyState.jsonMeta ? 'Reuniting body and soul…' : 'Body ready · ' + decoded.content_hash + ' · provide the soul');

  if (verifyState.jsonMeta) tryVerifyPair();
}

async function processVerifyJson(file) {
  verifyState.jsonMeta = null;
  document.getElementById('verifyJsonSlot').classList.remove('ready');
  updateVerifyStatus('Reading soul...');

  try {
    var text = await file.text();
    var meta = JSON.parse(text);
    if (!meta || typeof meta !== 'object') throw new Error('Not an object');
    verifyState.jsonMeta = meta;
    document.getElementById('verifyJsonSlot').classList.add('ready');
    updateVerifyStatus(verifyState.barHash ? 'Reuniting body and soul…' : 'Soul ready · provide the body');
    if (verifyState.barHash) tryVerifyPair();
  } catch (e) {
    updateVerifyStatus('Invalid soul file: ' + e.message, true);
  }
}

// By Soul drop zones — shared DropZone helper handles wiring + filtering.
DropZone.attach({
  zone: verifyImageDrop, input: verifyImageInput,
  accept: function(f) { return f.type.startsWith('image/'); },
  onFiles: processVerifyImage
});
DropZone.attach({
  zone: verifyJsonDrop, input: verifyJsonInput,
  accept: function(f) { return f.name.endsWith('.json') || f.name.endsWith('.soul') || f.type === 'application/json'; },
  onFiles: processVerifyJson
});

// Custom soul sources are still honored via localStorage
// ('mememage-sources' — a JSON array of URL templates with {id}/{hash}
// placeholders) but the in-footer management UI was removed. resolveMetadata
// still reads that key; power users can edit it directly if needed.

// =====================================================================
// Try Example — loads the local samples/example.soul (same record the
// validator's Attack Lab uses). No IA round-trip, so it works offline
// and the decoder/validator share one canonical example.
// =====================================================================
var EXAMPLE_ID = 'mememage-22dd171b5d648ec3';
var tryLink = document.getElementById('tryExampleLink');
if (tryLink) {
  var inputSection = document.querySelector('.input-section');
  // Cached fixture — mirrors validator attack-lab's `original`. Fetched
  // once per session; synthetic _verification is stamped so renderCert
  // skips the full fetchAndRender pipeline (no source resolution, no
  // hash/signature verify, no TOFU prompt — the example is a known
  // fixture, not a query that needs to be vetted).
  var _exampleSoul = null;

  async function loadExampleSoul() {
    var exampleUrl = (typeof assetPath === 'function')
      ? assetPath('samples/example.soul')
      : 'samples/example.soul';
    var res = await _fetchTimeout(exampleUrl, { cache: 'no-store' }, 8000);
    if (!res.ok) throw new Error('example.soul fetch ' + res.status);
    var soul = await res.json();
    soul._identifier = EXAMPLE_ID;
    soul._content_hash = soul.content_hash || null;
    soul._verification = {
      status: 'verified',
      detail: 'Example certificate.',
      signature: soul.signature ? true : null,
      signatureDetail: soul.signature ? 'Ed25519 signature valid.' : '',
      portrait: null
    };
    return soul;
  }

  async function activateExample() {
    inputSection.classList.add('example-active');
    tryLink.textContent = 'dismiss example';
    if (!_exampleSoul) {
      try { _exampleSoul = await loadExampleSoul(); }
      catch (e) { console.error('Example load failed:', e); return; }
    }
    var certWrap = document.getElementById('certWrap');
    PanelSwap(certWrap, function() {
      resetAll();
      // resetAll clears example-active + link text as part of its "new
      // query starts fresh" contract; re-apply for the example flow.
      inputSection.classList.add('example-active');
      tryLink.textContent = 'dismiss example';
      window._sampleMode = true;
      // Example cert isn't anchored to a real chain — disable chain traversal
      // (Bayer letter) + the stellarium so a click doesn't walk into nothing.
      // Real lookups (By Word / Audit) leave this unset and keep traversal.
      window._exampleMode = true;
      if (history.pushState) history.pushState({ id: EXAMPLE_ID }, '', '#');
      renderCert(_exampleSoul);
    });
  }

  function dismissExample() {
    inputSection.classList.remove('example-active');
    tryLink.textContent = 'see an example';
    var certWrap = document.getElementById('certWrap');
    PanelSwap(certWrap, function() { resetAll(); });
  }

  tryLink.addEventListener('click', function(e) {
    e.preventDefault();
    if (inputSection.classList.contains('example-active')) dismissExample();
    else activateExample();
  });
}

// Auto-lookup handoffs on page load.
//  - URL ?id= works CROSS-ORIGIN — the dashboard's mint-result "View
//    certificate" link, where the decoder is on a different host
//    (souls.<domain>) than the dashboard (mint.<domain>). The value may
//    be a bare identifier or a full .soul URL; lookupById handles both.
//  - localStorage works same-origin — the validator.html Audit tab.
(function() {
  try {
    var qid = new URLSearchParams(location.search).get('id');
    if (qid) { lookupById(qid); return; }
  } catch (e) { /* no URLSearchParams — fall through to localStorage */ }
  var handoff = localStorage.getItem('mememage-lookup');
  if (handoff) {
    localStorage.removeItem('mememage-lookup');
    lookupById(handoff);
  }
})();

// Portal transition — flip between decoder and validator (see js/portal.js)
Portal.init({
  sourceMarker: 'decoder',
  otherMarker:  'validator',

  applyIncomingTab: function(idx) {
    var panelNames = ['imagePanel', 'lookupPanel', 'verifyPanel'];
    if (panelNames[idx]) TabBar.activateById(panelNames[idx]);
  },

  getOutgoingTab: function() {
    var idx = 0;
    document.querySelectorAll('.input-tab').forEach(function(t, i) { if (t.classList.contains('active')) idx = i; });
    return idx;
  },

  reset: function() {
    if (typeof resetAll === 'function') resetAll();
    var verifyStatus = document.getElementById('verifyStatus');
    if (verifyStatus) verifyStatus.innerHTML = '';
  },

  dismissResults: function(done) {
    dismissPanel(document.getElementById('certWrap'), {
      beforeDismiss: function() {
        if (typeof CosmicPlayer !== 'undefined') CosmicPlayer.dismiss();
      }
    }, done);
  },
});

// Player reparenting (desktop) now lives in cosmic-player.js so both
// the decoder and validator pick it up without duplication.

