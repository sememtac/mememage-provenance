// =====================================================================
// PNG Metadata Injection + Canvas Save Interception
//
// Two capabilities:
// 1. Inject tEXt chunks into PNG binary data (standard PNG spec)
// 2. Intercept right-click save on canvas elements to serve
//    metadata-rich PNGs while keeping the canvas animated
//
// Usage:
//   enableCanvasSave(canvas, {generation_params: JSON.stringify(data)})
//
// The user right-clicks, sees native "Save Image As...", and the saved
// PNG carries the metadata as standard tEXt chunks readable by any
// PNG-aware tool (Pillow, ComfyUI, ExifTool, etc).
// =====================================================================

// --- CRC-32 (PNG uses this for chunk checksums) ---
var _crcTable = null;
function _makeCrcTable() {
  _crcTable = new Uint32Array(256);
  for (var n = 0; n < 256; n++) {
    var c = n;
    for (var k = 0; k < 8; k++) {
      c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    }
    _crcTable[n] = c;
  }
}

function crc32(buf) {
  if (!_crcTable) _makeCrcTable();
  var crc = 0xFFFFFFFF;
  for (var i = 0; i < buf.length; i++) {
    crc = _crcTable[(crc ^ buf[i]) & 0xFF] ^ (crc >>> 8);
  }
  return (crc ^ 0xFFFFFFFF) >>> 0;
}

// --- PNG iTXt chunk builder (UTF-8 safe) ---
function buildTextChunk(keyword, text) {
  // iTXt chunk: keyword + \0 + compression_flag(0) + compression_method(0) + language_tag + \0 + translated_keyword + \0 + text
  var keyBytes = new TextEncoder().encode(keyword);
  var textBytes = new TextEncoder().encode(text);
  // keyword \0 0 0 \0 \0 text
  var data = new Uint8Array(keyBytes.length + 1 + 2 + 1 + 1 + textBytes.length);
  var off = 0;
  data.set(keyBytes, off); off += keyBytes.length;
  data[off++] = 0; // null separator after keyword
  data[off++] = 0; // compression flag (uncompressed)
  data[off++] = 0; // compression method
  data[off++] = 0; // language tag (empty, null terminated)
  data[off++] = 0; // translated keyword (empty, null terminated)
  data.set(textBytes, off);

  // Chunk: [4B length][4B type "iTXt"][data][4B CRC]
  var typeBytes = new Uint8Array([0x69, 0x54, 0x58, 0x74]); // "iTXt"
  var crcInput = new Uint8Array(4 + data.length);
  crcInput.set(typeBytes, 0);
  crcInput.set(data, 4);
  var checksum = crc32(crcInput);

  var chunk = new Uint8Array(12 + data.length);
  // Length (big-endian)
  chunk[0] = (data.length >>> 24) & 0xFF;
  chunk[1] = (data.length >>> 16) & 0xFF;
  chunk[2] = (data.length >>> 8) & 0xFF;
  chunk[3] = data.length & 0xFF;
  // Type
  chunk.set(typeBytes, 4);
  // Data
  chunk.set(data, 8);
  // CRC (big-endian)
  chunk[8 + data.length] = (checksum >>> 24) & 0xFF;
  chunk[9 + data.length] = (checksum >>> 16) & 0xFF;
  chunk[10 + data.length] = (checksum >>> 8) & 0xFF;
  chunk[11 + data.length] = checksum & 0xFF;

  return chunk;
}

// --- Inject tEXt chunks into PNG binary ---
function injectPngTextChunks(pngArrayBuffer, metadata) {
  // metadata: {key: value, ...} — each becomes a tEXt chunk
  // Inserts before IEND (last 12 bytes of any valid PNG)
  var src = new Uint8Array(pngArrayBuffer);
  var iendOffset = src.length - 12; // IEND chunk is always last, always 12 bytes

  // Build all text chunks
  var chunks = [];
  var totalLen = 0;
  for (var key in metadata) {
    var chunk = buildTextChunk(key, metadata[key]);
    chunks.push(chunk);
    totalLen += chunk.length;
  }

  // Assemble: [everything before IEND] + [text chunks] + [IEND]
  var result = new Uint8Array(src.length + totalLen);
  result.set(src.subarray(0, iendOffset), 0);
  var offset = iendOffset;
  for (var ci = 0; ci < chunks.length; ci++) {
    result.set(chunks[ci], offset);
    offset += chunks[ci].length;
  }
  result.set(src.subarray(iendOffset), offset);

  return result.buffer;
}

// --- Read tEXt + iTXt chunks from a PNG ArrayBuffer ---
// Returns a flat keyword -> text map. Used by the validator's
// reconstruct flow to read parent_id / parent_hash / fragment_id
// from saved band PNGs without decoding pixel bars.
function readPngTextChunks(arrayBuffer) {
  var src = new Uint8Array(arrayBuffer);
  var out = {};
  // PNG signature is 8 bytes — chunks start at offset 8.
  if (src.length < 16) return out;
  var pos = 8;
  while (pos + 8 < src.length) {
    var len = (src[pos] * 0x1000000) + (src[pos+1] << 16) + (src[pos+2] << 8) + src[pos+3];
    var type = String.fromCharCode(src[pos+4], src[pos+5], src[pos+6], src[pos+7]);
    if (type === 'IEND') break;
    var dataStart = pos + 8;
    if ((type === 'iTXt' || type === 'tEXt') && len > 0 && dataStart + len <= src.length) {
      var data = src.subarray(dataStart, dataStart + len);
      // keyword runs to first null
      var nullIdx = -1;
      for (var i = 0; i < data.length; i++) {
        if (data[i] === 0) { nullIdx = i; break; }
      }
      if (nullIdx > 0) {
        var keyword = '';
        for (var k = 0; k < nullIdx; k++) keyword += String.fromCharCode(data[k]);
        var textBytes;
        if (type === 'tEXt') {
          textBytes = data.subarray(nullIdx + 1);
        } else {
          // iTXt: skip compression_flag (1B) + compression_method (1B),
          // then language_tag (null-term), then translated_keyword (null-term).
          var p = nullIdx + 3;
          while (p < data.length && data[p] !== 0) p++;
          p++;
          while (p < data.length && data[p] !== 0) p++;
          p++;
          textBytes = data.subarray(p);
        }
        try {
          out[keyword] = new TextDecoder('utf-8').decode(textBytes);
        } catch (e) {
          out[keyword] = '';
        }
      }
    }
    pos = dataStart + len + 4; // data + CRC
  }
  return out;
}

// Fragment tags for per-band bar reconstruction. Each band's bar
// carries a 1-byte tag prefix so a screenshotted band still self-
// identifies even when iTXt chunks have been stripped. Combining
// the three fragments (in order) reconstructs the canonical bar
// payload `mememage-XXXX\0<hash>`.
var FRAGMENT_TAG_GEN = 0x01;
var FRAGMENT_TAG_SKY = 0x02;
var FRAGMENT_TAG_MACHINE = 0x03;
function fragmentBytes(text, tag) {
  if (!text && text !== '') return null;
  var enc = new TextEncoder().encode(text);
  var out = new Uint8Array(enc.length + 1);
  out[0] = tag;
  out.set(enc, 1);
  return out;
}

// --- Canvas save interception ---
function enableCanvasSave(canvas, metadata, barPayloadBytes) {
  // On right-click: swap canvas for a metadata-rich PNG img,
  // let the browser's native save dialog work, then swap back.
  // If barPayloadBytes is provided, the saved PNG also carries a
  // pixel bar in its bottom 2 rows (Mememage's standard codec) so
  // the fragment survives screenshot/copy where iTXt would be lost.
  if (!canvas || !metadata) return;

  // Pre-generate the metadata-rich PNG and keep it ready
  var _savedBlobUrl = null;
  var _savedImg = null;

  function _prepareSaveImg() {
    var srcCanvas = canvas;
    // If we have a bar fragment payload AND the codec is loaded, copy
    // the live canvas to an offscreen buffer and embed the bar in the
    // bottom 2 data rows (asym reads the band content one row above as its
    // reference) before PNG encoding. The live canvas is untouched so the
    // on-screen render stays visually clean.
    if (barPayloadBytes && typeof embedBarPayload === 'function') {
      try {
        srcCanvas = document.createElement('canvas');
        srcCanvas.width = canvas.width;
        srcCanvas.height = canvas.height;
        var sctx = srcCanvas.getContext('2d');
        sctx.drawImage(canvas, 0, 0);
        var img = sctx.getImageData(0, 0, srcCanvas.width, srcCanvas.height);
        // Canonical writer (codec.js), forced to the SEQUENTIAL layout: these
        // are band fragments (tag-prefixed, non-canonical payloads). The
        // even-fill decoder only validates canonical payloads, so a fragment
        // written even-fill on a wide (HiDPI) band would be unreadable;
        // sequential decodes at any width. iTXt still carries the fragment too.
        embedBarPayload(img.data, srcCanvas.width, srcCanvas.height, barPayloadBytes, true);
        sctx.putImageData(img, 0, 0);
      } catch (e) {
        // Bar embed failed (e.g. canvas too narrow for fragment at 2px/bit)
        // — fall back to plain canvas. iTXt chunks still carry the fragment.
        srcCanvas = canvas;
      }
    }
    srcCanvas.toBlob(function(blob) {
      var reader = new FileReader();
      reader.onload = function() {
        var enriched = injectPngTextChunks(reader.result, metadata);
        var enrichedBlob = new Blob([enriched], {type: 'image/png'});
        // Defer revocation of the previous URL — if the user just kicked
        // off a download, Chrome is still fetching from it. Immediate
        // revoke surfaces as a "Check internet connection" error and
        // forces the user to retry. 10s is well past any reasonable
        // local download.
        var prevUrl = _savedBlobUrl;
        if (prevUrl) setTimeout(function() { URL.revokeObjectURL(prevUrl); }, 10000);
        _savedBlobUrl = URL.createObjectURL(enrichedBlob);

        if (!_savedImg) {
          _savedImg = document.createElement('img');
          _savedImg.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;z-index:10;cursor:default;display:none;';
          var parent = canvas.parentElement;
          if (getComputedStyle(parent).position === 'static') {
            parent.style.position = 'relative';
          }
          parent.appendChild(_savedImg);
        }
        // When the bar is embedded, hide the bottom 2 rows from the
        // visible overlay via CSS clip. The saved PNG (img.src) still
        // contains the full image including the bar — the right-click
        // → Save reads from the underlying bytes, not the clipped
        // display. So the user never sees the bar in the band, but the
        // file they save carries it.
        if (barPayloadBytes && canvas.height > 0) {
          var _barClipPct = (2 / canvas.height) * 100;
          _savedImg.style.clipPath = 'inset(0 0 ' + _barClipPct.toFixed(3) + '% 0)';
        } else {
          _savedImg.style.clipPath = '';
        }
        _savedImg.src = _savedBlobUrl;
      };
      reader.readAsArrayBuffer(blob);
    }, 'image/png');
  }

  // Refresh the save image periodically so it stays current with the canvas
  _prepareSaveImg();
  setInterval(_prepareSaveImg, 3000);

  canvas.addEventListener('contextmenu', function(e) {
    if (!_savedImg || !_savedBlobUrl) return;

    // Show the pre-rendered img (matches recent canvas state, no flash)
    _savedImg.style.display = 'block';
    canvas.style.visibility = 'hidden';

    function cleanup() {
      canvas.style.visibility = 'visible';
      _savedImg.style.display = 'none';
      document.removeEventListener('click', cleanup);
      document.removeEventListener('keydown', cleanupKey);
    }
    function cleanupKey(ev) { if (ev.key === 'Escape') cleanup(); }
    setTimeout(function() {
      document.addEventListener('click', cleanup);
      document.addEventListener('keydown', cleanupKey);
    }, 100);
  });
}
