// =====================================================================
// access.js — Creator Access Layer (browser side)
//
// Shared helper for decrypting password-protected fields in a mememage
// record. Mirrors mememage/access.py on the Python side:
//   - PBKDF2 key derivation (600000 iterations, SHA-256)
//   - AES-256-GCM decryption
//   - Envelope format: { salt, iv, ct, tag } — all hex strings
//
// Consumers:
//   - cert-renderer.js (decoder)  — GPS unlock UI on the certificate
//   - validator.js                — GPS unlock in the Audit tab
//
// Returning structured results keeps each caller free to render the
// plaintext into its own DOM shape (decoder uses .gps-unlock-coords,
// validator uses .ev-g).
//
// The helper requires SubtleCrypto. iOS Safari 11+, Chrome 37+,
// Firefox 34+. Returns { ok: false, error: 'unsupported' } if the API
// is missing so callers can degrade gracefully.
// =====================================================================
var Access = (function() {
  var PBKDF2_ITERATIONS = 600000;

  function hex2bytes(h) {
    var m = h.match(/.{2}/g);
    if (!m) return new Uint8Array(0);
    return new Uint8Array(m.map(function(b) { return parseInt(b, 16); }));
  }

  function hasSubtle() {
    return typeof crypto !== 'undefined'
      && crypto.subtle
      && typeof crypto.subtle.importKey === 'function';
  }

  // Decrypt an AES-256-GCM envelope with a password-derived key.
  // Returns a Promise that resolves to { ok, plaintext, error }.
  async function decryptEnvelope(envelope, password) {
    if (!hasSubtle()) return { ok: false, error: 'SubtleCrypto unavailable' };
    if (!envelope || !envelope.salt || !envelope.iv || !envelope.ct || !envelope.tag) {
      return { ok: false, error: 'Malformed envelope' };
    }
    if (!password) return { ok: false, error: 'Empty password' };
    try {
      var salt = hex2bytes(envelope.salt);
      var iv = hex2bytes(envelope.iv);
      var ct = hex2bytes(envelope.ct);
      var tag = hex2bytes(envelope.tag);
      var km = await crypto.subtle.importKey(
        'raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveKey']
      );
      var key = await crypto.subtle.deriveKey(
        { name: 'PBKDF2', salt: salt, iterations: PBKDF2_ITERATIONS, hash: 'SHA-256' },
        km, { name: 'AES-GCM', length: 256 }, false, ['decrypt']
      );
      var combined = new Uint8Array(ct.length + tag.length);
      combined.set(ct);
      combined.set(tag, ct.length);
      var plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: iv }, key, combined);
      return { ok: true, plaintext: new TextDecoder().decode(plain) };
    } catch (e) {
      // GCM auth failures surface as generic exceptions — almost always
      // a wrong password from the creator's perspective.
      return { ok: false, error: 'Wrong password' };
    }
  }

  // Convenience wrapper for GPS envelopes. The plaintext is 'lat,lon'.
  async function decryptGps(envelope, password) {
    var res = await decryptEnvelope(envelope, password);
    if (!res.ok) return res;
    var parts = res.plaintext.split(',');
    return { ok: true, lat: parts[0] || '', lon: parts[1] || '' };
  }

  // Convenience wrapper for the encrypted_fields envelope. The plaintext
  // is canonical-JSON of all PROTECTED_FIELDS as one dict.
  async function decryptSoul(envelope, password) {
    var res = await decryptEnvelope(envelope, password);
    if (!res.ok) return res;
    try {
      return { ok: true, soul: JSON.parse(res.plaintext) };
    } catch (e) {
      return { ok: false, error: 'Soul JSON parse failed' };
    }
  }

  // Convenience wrapper for the encrypted_chunks envelope. The plaintext
  // is canonical-JSON of the chunks namespace dict (decoder, truth, ...).
  async function decryptChunks(envelope, password) {
    var res = await decryptEnvelope(envelope, password);
    if (!res.ok) return res;
    try {
      return { ok: true, chunks: JSON.parse(res.plaintext) };
    } catch (e) {
      return { ok: false, error: 'Chunks JSON parse failed' };
    }
  }

  return {
    decryptEnvelope: decryptEnvelope,
    decryptGps: decryptGps,
    decryptSoul: decryptSoul,
    decryptChunks: decryptChunks
  };
})();
