// =====================================================================
// CODEC — Mememage bar decoder (v4 with RS, v3 fallback)
// =====================================================================
function crc16(d){let c=0xFFFF;for(const b of d){c^=b<<8;for(let i=0;i<8;i++)c=(c&0x8000)?((c<<1)^0x1021)&0xFFFF:(c<<1)&0xFFFF;}return c;}

// =====================================================================
// Identifier grammar — ONE source of truth for both pages (decoder By
// Word + validator Audit both call these).
//
// Canonical identifier = <prefix>-<16 lowercase hex>. The prefix is
// per-chain and case-PRESERVING (archive.org treats different cases as
// different items), [A-Za-z][A-Za-z0-9_-]*[A-Za-z0-9] — it can itself
// contain hyphens, so the trailing 16-hex suffix is what anchors the
// parse. 'mememage' is only the DEFAULT prefix, never an assumption:
// custom chains (dark-, phoenix-, …) must validate too.
// =====================================================================
var DEFAULT_PREFIX = 'mememage';
// Embedded: pull an identifier out of a URL / path / filename.
// Unanchored — the greedy prefix backtracks to the last hyphen before
// the 16-hex suffix.
var _ID_EMBED_RE = /[A-Za-z][A-Za-z0-9_-]*-[0-9a-f]{16}/;
// Strict whole-string: a bare identifier with no surrounding junk, so a
// trailing typo can't silently truncate to a valid record.
var _ID_BARE_RE = /^[A-Za-z][A-Za-z0-9_-]*-[0-9a-f]{16}$/;
var _ID_HEX16_RE = /^[0-9a-f]{16}$/i;

// Extract an identifier embedded in a URL or path; null if none.
function extractIdentifier(text){
  if(!text) return null;
  var m = String(text).match(_ID_EMBED_RE);
  return m ? m[0] : null;
}

// Validate/normalize a BARE user-typed identifier (not a URL). Accepts
// any <prefix>-<16hex>; a pure 16-hex string is sugar for the default
// chain (mememage-<hex>). Returns the canonical identifier or null.
function normalizeIdentifier(text){
  if(!text) return null;
  var s = String(text).trim();
  if(_ID_HEX16_RE.test(s)) s = DEFAULT_PREFIX + '-' + s.toLowerCase();
  return _ID_BARE_RE.test(s) ? s : null;
}

function detectBar(px,w,h){
  // Presence-only check — does the bottom row start with the M/Y/C
  // sequence at the original pixel scale? Cheap; used as a fast
  // gate before the more expensive band-width measurement. For a
  // scale-aware presence + measurement, use detectBarBands().
  if(h<2||w<50)return false;
  const y=h-1;
  const mid=Math.floor(HEADER_BAND/2);
  const im=(y*w+mid)*4;
  if(!(px[im]>130&&px[im+1]<120&&px[im+2]>130))return false;
  const iy=(y*w+HEADER_BAND+mid)*4;
  if(!(px[iy]>130&&px[iy+1]>130&&px[iy+2]<120))return false;
  const ic=(y*w+2*HEADER_BAND+mid)*4;
  if(!(px[ic]<120&&px[ic+1]>130&&px[ic+2]>130))return false;
  return true;
}

// Scale-aware band-width detector. Mirrors mememage/bar.py:_detect_bar.
// Walks the bottom row left→right looking for magenta → yellow → cyan
// runs, returning each run's pixel width. The widths reveal the scale
// factor — at 1:1 the bands are HEADER_BAND (8) px wide each; at a
// 0.75× resize they're ~6 px each; at 1.5× they're ~12 px each.
// Returns {m,y,c} or null if the M/Y/C sequence isn't present.
function detectBarBands(px,w,h){
  if(h<2||w<20) return null;
  const y=h-1;
  function rgbAt(x){var i=(y*w+x)*4;return [px[i],px[i+1],px[i+2]];}
  function isMagenta(x){var c=rgbAt(x);return c[0]>130&&c[1]<120&&c[2]>130;}
  function isYellow(x){var c=rgbAt(x);return c[0]>130&&c[1]>130&&c[2]<120;}
  function isCyan(x){var c=rgbAt(x);return c[0]<120&&c[1]>130&&c[2]>130;}

  // Scan magenta run from the left edge. The original bar is 8 px;
  // at 2× upscale it can run to 16, at 0.3× downscale to ~3. Stop at
  // 32 — beyond that we'd run into the data section even on a
  // pathologically upscaled image.
  var magenta_w=0;
  for(var x=0;x<Math.min(32,w);x++){
    if(isMagenta(x)) magenta_w++;
    else break;
  }
  if(magenta_w<3) return null;

  // Skip a 1-2px transition zone (JPEG smear / interpolation halo)
  // between bands, then measure yellow.
  var yellow_start=magenta_w;
  for(var x2=magenta_w;x2<Math.min(magenta_w+3,w);x2++){
    if(isYellow(x2)){ yellow_start=x2; break; }
  }
  var yellow_w=0;
  for(var x3=yellow_start;x3<Math.min(yellow_start+32,w);x3++){
    if(isYellow(x3)) yellow_w++;
    else break;
  }
  if(yellow_w<3) return null;

  var cyan_start=yellow_start+yellow_w;
  for(var x4=cyan_start;x4<Math.min(cyan_start+3,w);x4++){
    if(isCyan(x4)){ cyan_start=x4; break; }
  }
  var cyan_w=0;
  for(var x5=cyan_start;x5<Math.min(cyan_start+32,w);x5++){
    if(isCyan(x5)) cyan_w++;
    else break;
  }
  if(cyan_w<3) return null;

  return {m:magenta_w,y:yellow_w,c:cyan_w};
}

// Per-image bimodal bit threshold — a scalar fallback for the asym curve (rescues
// pure-saturated content where the per-channel clamp shrinks the (R+G+B)/3
// margin). Otsu over the middle 60% of the bottom rows, returned as the MIDPOINT
// of the two class means. Returns null on a flat region. Mirrors bar.py:_otsu_threshold.
function otsuThreshold(px,w,h){
  if(w<5||h<1)return null;
  var x0=Math.floor(w*0.20),x1=Math.floor(w*0.80);if(x1<=x0)return null;
  var hist=new Array(256).fill(0),total=0,y0=Math.max(0,h-SIG_ROWS);
  for(var y=y0;y<h;y++)for(var x=x0;x<x1;x++){var i=(y*w+x)*4;
    hist[(Math.round((px[i]+px[i+1]+px[i+2])/3))&255]++;total++;}
  if(total===0)return null;
  var sumAll=0;for(var k=0;k<256;k++)sumAll+=k*hist[k];
  var sumB=0,wB=0,best=-1,thr=null;
  for(var t=0;t<256;t++){wB+=hist[t];if(wB===0)continue;var wF=total-wB;if(wF===0)break;
    sumB+=t*hist[t];var mB=sumB/wB,mF=(sumAll-sumB)/wF,v=wB*wF*(mB-mF)*(mB-mF);
    if(v>best){best=v;thr=(mB+mF)/2;}}
  return thr;
}

// ---- Asym row-3-copy camo helpers (shared encode + decode) ----------------
// Faithful ports of mememage/bar.py:_smooth1d / _hue_floor / _asym_center_columns
// / _asym_threshold_curve / _thr. The box blur (NOT a Gaussian) is what keeps
// the writer byte-exact across runtimes — see data.js ASYM_BOX_RADIUS.
function _smooth1d(values,radius){
  var n=values.length;
  if(n===0||radius<=0)return values.slice();
  var width=2*radius+1,out=new Array(n);
  for(var i=0;i<n;i++){
    var acc=0.0;
    for(var k=-radius;k<=radius;k++){
      var idx=i+k; idx=idx<0?0:(idx>=n?n-1:idx);
      acc+=values[idx];
    }
    out[i]=acc/width;
  }
  return out;
}
function _hueFloor(r,g,b,floor){
  // Saturation-capped lift toward luma `floor`: cap the multiplicative scale at
  // ASYM_SCALE_CAP (so near-black tints don't explode into a saturated, q80-fragile
  // pop), then top up additively (hue-neutral). Moderately-dark colour keeps its
  // hue; near-black goes neutral. Pure arithmetic — byte-exact. Mirror bar.py.
  var L=0.299*r+0.587*g+0.114*b;
  if(L>=floor)return [r,g,b];
  var s=(L>=2)?Math.min(floor/L,ASYM_SCALE_CAP):ASYM_SCALE_CAP;
  var r2=r*s,g2=g*s,b2=b*s;
  var L2=0.299*r2+0.587*g2+0.114*b2;
  if(L2<floor){var k=floor-L2;r2+=k;g2+=k;b2+=k;}
  return [Math.min(255.0,r2),Math.min(255.0,g2),Math.min(255.0,b2)];
}
function _asymCenterColumns(px,w,h){
  var y=h-SIG_ROWS-1; if(y<0)y=Math.max(0,h-1);
  var rr=new Array(w),gg=new Array(w),bb=new Array(w);
  for(var x=0;x<w;x++){var i=(y*w+x)*4;rr[x]=px[i];gg[x]=px[i+1];bb[x]=px[i+2];}
  rr=_smooth1d(rr,ASYM_BOX_RADIUS);gg=_smooth1d(gg,ASYM_BOX_RADIUS);bb=_smooth1d(bb,ASYM_BOX_RADIUS);
  var centerRgb=new Array(w),centerVal=new Array(w);
  for(var x2=0;x2<w;x2++){
    var c=_hueFloor(rr[x2],gg[x2],bb[x2],ASYM_FLOOR);
    // (R+G+B)/3, NOT luma — matches the decoder's per-pixel bit metric. Mirror bar.py.
    centerRgb[x2]=c; centerVal[x2]=(c[0]+c[1]+c[2])/3;
  }
  return {centerRgb:centerRgb,centerVal:centerVal};
}
function _asymThresholdCurve(px,w,h){
  var cl=_asymCenterColumns(px,w,h).centerVal,half=ASYM_DELTA/2.0,out=new Array(w);
  for(var x=0;x<w;x++)out[x]=cl[x]-half;
  return out;
}
function _thr(threshold,x){
  if(Array.isArray(threshold)){
    if(x>=0&&x<threshold.length)return threshold[x];
    return threshold.length?threshold[threshold.length-1]:RGB_THRESHOLD;
  }
  return threshold;
}

function extractBits(px,w,h,ppb,thr){
  // 1:1 (native pixel-scale) extraction. Averages ALL ppb columns of the bit
  // (value AND per-column threshold) for JPEG noise immunity. Mirrors the
  // scale==1 branch of mememage/bar.py:_decode_bits_at_scale.
  ppb=ppb||PIXELS_PER_BIT;if(thr===undefined)thr=RGB_THRESHOLD;
  var dataStart=HEADER_PIXELS,dataEnd=w-FOOTER_PIXELS;
  var bitsPerRow=Math.floor((dataEnd-dataStart)/ppb),bits=[];
  for(var row=0;row<SIG_ROWS;row++){var y=h-1-row;
    for(var b=0;b<bitsPerRow;b++){
      var x0=dataStart+b*ppb,acc=0,tacc=0,cnt=0;
      for(var dx=0;dx<ppb;dx++){
        var cx=x0+dx; if(cx>=dataEnd)break;
        var i=(y*w+cx)*4; acc+=(px[i]+px[i+1]+px[i+2])/3; tacc+=_thr(thr,cx); cnt++;
      }
      bits.push(cnt&&acc/cnt>=tacc/cnt?1:0);
    }
  }
  return bits;
}

// Scale-aware bit extraction. Mirrors mememage/bar.py:_decode_bits_at_scale.
// Given an assumed scale factor, infer where each bit's center pixel
// would have landed in the original layout, then map back to a pixel
// in the current (scaled) image and read its luminance.
function extractBitsAtScale(px,w,h,scale,ppb,thr){
  ppb=ppb||PIXELS_PER_BIT;if(thr===undefined)thr=RGB_THRESHOLD;
  if(Math.abs(scale-1.0)<0.01) return extractBits(px,w,h,ppb,thr);
  var orig_w=pyRound(w/scale);
  var orig_bits_per_row=Math.floor((orig_w-HEADER_PIXELS-FOOTER_PIXELS)/ppb);
  var bits=[];
  for(var row=0;row<SIG_ROWS;row++){
    var y=h-1-row;
    for(var b=0;b<orig_bits_per_row;b++){
      // Average the bit's full scaled span (value AND threshold). Mirrors the
      // scaled branch of mememage/bar.py:_decode_bits_at_scale.
      var sx0=pyRound((HEADER_PIXELS+b*ppb)*scale);
      var sx1=pyRound((HEADER_PIXELS+(b+1)*ppb)*scale);
      var end=Math.max(sx0+1,sx1),acc=0,tacc=0,cnt=0;
      for(var sx=sx0;sx<end;sx++){
        if(sx<0||sx>=w) break;
        var i=(y*w+sx)*4; acc+=(px[i]+px[i+1]+px[i+2])/3; tacc+=_thr(thr,sx); cnt++;
      }
      if(cnt===0) break;
      bits.push(acc/cnt>=tacc/cnt?1:0);
    }
  }
  return bits;
}

// Band-edge finders for the high-res even-fill layout. Mirror
// mememage/bar.py:_find_header_end / _find_footer_start. They return the
// inner edges of the flush bilateral bands so the decoder can anchor to both
// ends and even-divide the data region — no scale factor, so no drift.
// The data-adjacent edge is COMPUTED, not measured by running the cyan count
// into the data: asym camo data pixels can be cyan-hued and would extend the run
// past the true edge. mag_start (image edge) and cyan_start (bounded by yellow)
// never touch data, and span exactly two band widths. Mirrors bar.py.
function findHeaderEnd(px,w,y){
  function rgb(x){var i=(y*w+x)*4;return [px[i],px[i+1],px[i+2]];}
  function isM(x){var c=rgb(x);return c[0]>130&&c[1]<120&&c[2]>130;}
  function isY(x){var c=rgb(x);return c[0]>130&&c[1]>130&&c[2]<120;}
  function isC(x){var c=rgb(x);return c[0]<120&&c[1]>130&&c[2]>130;}
  var x=0,nm=0,ny=0,nc=0;
  while(x<w&&x<40&&!isM(x))x++;
  var magStart=x;
  while(x<w&&isM(x)){x++;nm++;}
  while(x<w&&x<60&&!isY(x))x++;
  while(x<w&&isY(x)){x++;ny++;}
  while(x<w&&x<80&&!isC(x))x++;
  var cyanStart=x;
  while(x<w&&isC(x)){x++;nc++;}
  if(nm<2||ny<2||nc<2)return null;
  var bandWidth=(cyanStart-magStart)/2.0;
  return pyRound(cyanStart+bandWidth);
}
function findFooterStart(px,w,y){
  function rgb(x){var i=(y*w+x)*4;return [px[i],px[i+1],px[i+2]];}
  function isM(x){var c=rgb(x);return c[0]>130&&c[1]<120&&c[2]>130;}
  function isY(x){var c=rgb(x);return c[0]>130&&c[1]>130&&c[2]<120;}
  function isC(x){var c=rgb(x);return c[0]<120&&c[1]>130&&c[2]>130;}
  var x=w-1,nm=0,ny=0,nc=0;
  while(x>=0&&x>w-40&&!isM(x))x--;
  var magStart=x;
  while(x>=0&&isM(x)){x--;nm++;}
  while(x>=0&&x>w-60&&!isY(x))x--;
  while(x>=0&&isY(x)){x--;ny++;}
  while(x>=0&&x>w-80&&!isC(x))x--;
  var cyanStart=x;
  while(x>=0&&isC(x)){x--;nc++;}
  if(nm<2||ny<2||nc<2)return null;
  var bandWidth=(magStart-cyanStart)/2.0;
  return pyRound(cyanStart-bandWidth)+1;
}

// High-res even-fill decode. Mirrors mememage/bar.py:_decode_even_fill.
// Anchors to both band edges, evenly divides [a,b] by the frame bit count
// (swept; CRC self-selects), and reads the two rows averaged (noise immunity)
// then the bottom row alone (survives a 1px bottom crop).
function decodeEvenFill(px,w,h,thr,fast){
  if(thr===undefined)thr=RGB_THRESHOLD;
  if(h<1||w<3*HEADER_PIXELS)return null;
  var y=h-1;
  var a0=findHeaderEnd(px,w,y);
  var b0=findFooterStart(px,w,y);
  if(a0===null||b0===null||(b0-a0)<8)return null;
  var readModes=(h>=2)?[[h-1,h-2],[h-1]]:[[h-1]];
  // Band-edge detection lands on an integer pixel, but after a downscale the
  // true sub-pixel edge can sit a pixel or two away — a shift that moves every
  // bit center the same way and flips enough bits to exceed RS at particular
  // scales (aliasing nulls; e.g. ~0.9x can fail while 0.92x and 0.88x pass).
  // Sweep a few integer phase offsets on each anchor; CRC self-selects. (0,0)
  // is tried first, so a clean image returns immediately and every previously-
  // decodable image still decodes — a strict superset of the single-anchor read.
  // fast: the phase sweep is pure DOWNSCALE-aliasing insurance — at preserved
  // dimensions (JPEG survival) the band edge doesn't move, so (0,0) is exact and
  // the 25-combo sweep is ~25× wasted RS decodes. Collapse to the single anchor.
  var OFF=fast?[0]:[0,-1,1,-2,2];
  for(var ia=0;ia<OFF.length;ia++){
    for(var ib=0;ib<OFF.length;ib++){
      var a=a0+OFF[ia],b=b0+OFF[ib],span=b-a;
      if(span<8)continue;
      for(var nBytes=EVENFILL_MIN_BYTES;nBytes<=EVENFILL_MAX_BYTES;nBytes++){
        var n=nBytes*8;
        for(var rm=0;rm<readModes.length;rm++){
          var rows=readModes[rm],bits=[],ok=true;
          for(var i=0;i<n;i++){
            var cx=Math.round(a+(i+0.5)*span/n);
            if(cx<0||cx>=w){ok=false;break;}
            var acc=0;
            for(var r=0;r<rows.length;r++){
              var idx=(rows[r]*w+cx)*4;
              acc+=(px[idx]+px[idx+1]+px[idx+2])/3;
            }
            bits.push((acc/rows.length)>=_thr(thr,cx)?1:0);
          }
          if(!ok)continue;
          var frame=decodeFrame(bits);
          // Validate via payload (locks the right n_bytes) but return the FRAME
          // so callers get rsErrors/rsCapacity for the forensic display.
          if(frame){var p=decodePayload(frame.payload);if(p)return frame;}
        }
      }
    }
  }
  return null;
}

// Top-level scale-aware extractor. Tries the high-res even-fill layout first
// (both-ends-anchored, drift-free), then 1:1, then sweeps candidate scales
// derived from the measured band widths. Returns the first
// {identifier, content_hash} that decodes cleanly, or null.
// Mirrors mememage/bar.py:extract_bar.
function _extractBarAtBottom(px,w,h,fast){
  // Decode the bar at the bottom 2 rows (the embed position). The px array may
  // be taller than h — only rows < h are read — so the scan in
  // extractBarScaleAware passes a reduced h to read a relocated bar with NO copy.
  // Threshold candidates: the asym per-column curve (PRIMARY) + Otsu's per-image
  // bimodal midpoint and the absolute 128 as scalar FALLBACKS that rescue hard
  // content where the asym curve's per-channel clamp eats the delta margin (e.g.
  // pure-saturated backgrounds). CRC + RS self-select; the post-RS CRC re-check
  // guards miscorrections. Mirrors bar.py:_extract_at_bottom.
  var thrs=[];
  try{ thrs.push(_asymThresholdCurve(px,w,h)); }catch(e){}
  var ot=otsuThreshold(px,w,h); if(ot!==null)thrs.push(ot);
  thrs.push(RGB_THRESHOLD);

  // Band detection only ADDS the resized-scale sweep. Scale 1:1 is ALWAYS tried
  // (it isn't needed for a native-scale read, and band detection can fail on a
  // heavily-recompressed asym bar even when the 1:1 sequential read decodes
  // cleanly — CRC+RS guards false positives). Mirrors bar.py:extract_bar.
  // fast: skip the ±8% band-derived scale sweep. The sweep recovers a bar whose
  // pixels-per-bit changed under an UNKNOWN resize (the By-Sight path). Callers
  // that re-read at PRESERVED dimensions (JPEG survival — same w×h) know the bar
  // is at scale 1.0 exactly, so the sweep is pure waste (~17× the decode cost).
  // Verdict is identical at preserved dims; only the wasted attempts are dropped.
  var bands=detectBarBands(px,w,h);
  var scaleCands=[1.0];
  if(!fast && bands && Math.abs((bands.m+bands.y+bands.c)/3/HEADER_BAND-1.0)>=0.05){
    var raw_scale=(bands.m+bands.y+bands.c)/3/HEADER_BAND;
    // Band-width measurement has ±5% noise from JPEG / interpolation, so sweep
    // ±8% in 1% steps around the estimate. CRC self-selects the right one.
    for(var off=-8;off<=8;off++){
      var s=Math.round((raw_scale+off*0.01)*1000)/1000;
      if(s>0.3 && s<3.0 && Math.abs(s-1.0)>=0.005 && scaleCands.indexOf(s)<0) scaleCands.push(s);
    }
  }

  for(var ti=0;ti<thrs.length;ti++){
    var thr=thrs[ti];
    // High-res even-fill layout — full-width, both-ends anchored.
    var ef=decodeEvenFill(px,w,h,thr,fast);
    if(ef){var efp=decodePayload(ef.payload);if(efp)return efp;}
    // Sequential layout — scale 1:1 first (common case), then swept scales.
    // Sweep px/bit widest-first (encoder picks the widest that fits); CRC/RS selects.
    for(var ci=0;ci<scaleCands.length;ci++){
      for(var ppb=PIXELS_PER_BIT_MAX;ppb>=PIXELS_PER_BIT_NARROW;ppb--){
        var bits=extractBitsAtScale(px,w,h,scaleCands[ci],ppb,thr);
        var frame=decodeFrame(bits);
        if(!frame) continue;
        var p=decodePayload(frame.payload);
        if(p) return p;
      }
    }
  }
  return null;
}

function extractBarScaleAware(px,w,h,scan,fast){
  // Read the bar at the bottom (fast path), then fall back to a vertical scan
  // that finds it wherever its M/Y/C band signature appears — so a relocated /
  // offset bar still decodes, and the validator's Scale/JPEG survival re-reads
  // pick it up. Passing a reduced h reads a higher row pair with NO pixel copy.
  // scan defaults on. CRC+RS self-select per candidate row. Mirrors
  // bar.py:extract_bar / image-decode.js:decodeImageBar.
  // bottomRow = the bar's bottom row (h-1 at the bottom; the matched row when
  // scanned) so callers can crop the actual bar region instead of the bottom.
  var r=_extractBarAtBottom(px,w,h,fast);
  if(r){ r.bottomRow=h-1; return r; }
  if(scan!==false){
    for(var b=h-1;b>=SIG_ROWS;b--){
      if(detectBar(px,w,b+1)){
        var rr=_extractBarAtBottom(px,w,b+1,fast);
        if(rr){ rr.bottomRow=b; return rr; }
      }
    }
  }
  return null;
}

function decodeFrame(bits){
  const bytes=[];for(let i=0;i+7<bits.length;i+=8){let v=0;for(let j=0;j<8;j++)v=(v<<1)|bits[i+j];bytes.push(v);}
  // Gen I frame: [0xAD4E][gen=1][nsym][payload_len BE][CRC-16][RS codeword]
  if(bytes.length<8||bytes[0]!==0xAD||bytes[1]!==0x4E)return null;
  const gen=bytes[2];
  if(gen!==1)return null;
  const nsym=bytes[3];
  const pLen=(bytes[4]<<8)|bytes[5],crc=(bytes[6]<<8)|bytes[7];
  const cwLen=pLen+nsym;
  if(bytes.length<8+cwLen)return null;
  const codeword=bytes.slice(8,8+cwLen);
  const rsCapacity=Math.floor(nsym/2);
  try{
    const decoded=rsDecode(codeword,nsym);
    // Verify CRC after RS to catch rare MISCORRECTIONS (>nsym/2 errors that land
    // near a different valid codeword). Without this, a wrong bit-read during the
    // threshold/offset sweep can RS-"correct" into a magic-prefixed garbage frame
    // and be accepted, returning a bogus identifier. Mirror bar.py:_try_decode_frame.
    if(crc16(rsEncodeSimple(Array.from(decoded),nsym))!==crc) return null;
    // Count payload bytes that RS actually corrected (for forensic display)
    let rsErrors=0;
    for(let i=0;i<pLen;i++)if(codeword[i]!==decoded[i])rsErrors++;
    return{gen:1,payload:new Uint8Array(decoded),rsErrors,rsCapacity};
  }catch(e){
    if(crc16(codeword)!==crc)return null;
    // RS failed; CRC fallback — rsErrors=-1 signals "no correction data available"
    return{gen:1,payload:new Uint8Array(codeword.slice(0,pLen)),rsErrors:-1,rsCapacity};
  }
}

// =====================================================================
// ENCODER — for Save Certificate (encodes bar into composite image)
// =====================================================================
function rsEncodeSimple(data, nsym) {
  // Simple RS encode: append nsym parity bytes. Uses the same GF(2^8) as rs.js.
  // Generator polynomial for nsym symbols
  var gen = [1];
  for (var i = 0; i < nsym; i++) {
    var next = new Array(gen.length + 1).fill(0);
    for (var j = 0; j < gen.length; j++) {
      next[j] ^= gen[j];
      next[j + 1] ^= gfMul(gen[j], gfPow(2, i));
    }
    gen = next;
  }
  // Polynomial division
  var msg = data.slice();
  for (var k = 0; k < nsym; k++) msg.push(0);
  for (var m = 0; m < data.length; m++) {
    var coef = msg[m];
    if (coef !== 0) {
      for (var n = 1; n < gen.length; n++) {
        msg[m + n] ^= gfMul(gen[n], coef);
      }
    }
  }
  return data.concat(msg.slice(data.length));
}

function encodeFrame(payloadBytes) {
  // Gen I frame: [0xAD][0x4E][gen=1][nsym=6][payload_len BE][CRC-16][RS codeword]
  var nsym = 6;
  var pLen = payloadBytes.length;
  var payload = Array.from(payloadBytes);
  var codeword = rsEncodeSimple(payload, nsym);
  var crc = crc16(codeword);
  var header = [0xAD, 0x4E, 1, nsym, (pLen >> 8) & 0xFF, pLen & 0xFF, (crc >> 8) & 0xFF, crc & 0xFF];
  return header.concat(codeword);
}

// =====================================================================
// BAR WRITER — a FAITHFUL port of mememage/bar.py:embed_into. This is the
// single source of truth for writing a bar in the browser (Save Certificate,
// reliquary reconstruct, band-PNG save). It MUST stay byte-for-byte identical
// to the Python writer — enforced by tests/bar_encode_parity.cjs +
// tests/test_bar_js_parity.py. The bar is the technique; when it evolves, both
// sides change in lockstep and the parity test fails on any drift.
// =====================================================================

// Python round() uses banker's rounding (half-to-even); JS Math.round rounds
// half UP. They diverge at .5 in dominant-color and even-fill math, so the
// port needs this to match Python exactly.
function pyRound(x) {
  var f = Math.floor(x);
  var d = x - f;
  if (d < 0.5) return f;
  if (d > 0.5) return f + 1;
  return (f % 2 === 0) ? f : f + 1;  // exactly .5 -> nearest even
}
function _setPx(px, w, x, y, rgb) {
  var i = (y * w + x) * 4;
  px[i] = rgb[0]; px[i+1] = rgb[1]; px[i+2] = rgb[2]; px[i+3] = 255;
}


var _HEADER_COLORS = [[255,0,255],[255,255,0],[0,255,255]];   // M, Y, C
var _FOOTER_COLORS = [[0,255,255],[255,255,0],[255,0,255]];   // C, Y, M
function _paintBands(px, w, y) {
  for (var ci = 0; ci < 3; ci++)
    for (var p = 0; p < HEADER_BAND; p++)
      _setPx(px, w, ci * HEADER_BAND + p, y, _HEADER_COLORS[ci]);
  for (var ci2 = 0; ci2 < 3; ci2++)
    for (var p2 = 0; p2 < HEADER_BAND; p2++)
      _setPx(px, w, (w - FOOTER_PIXELS) + ci2 * HEADER_BAND + p2, y, _FOOTER_COLORS[ci2]);
}

// Mirror bar.py:_write_even_fill
function _writeEvenFill(px, w, h, bits, bitRgb) {
  var a = HEADER_PIXELS, b = w - FOOTER_PIXELS, span = b - a, n = bits.length;
  var rows = [h - 1, h - 2];
  for (var r = 0; r < rows.length; r++) {
    var y = rows[r];
    _paintBands(px, w, y);
    for (var i = 0; i < n; i++) {
      var x0 = a + pyRound(i * span / n);
      var x1 = a + pyRound((i + 1) * span / n);
      // Per-pixel bitRgb(bit, x) — the asym center varies by column; for the
      // centered scheme x is ignored so the value is constant across the bit.
      for (var x = x0; x < x1; x++) _setPx(px, w, x, y, bitRgb(bits[i], x));
    }
  }
}

// Mirror bar.py:_write_sequential
function _writeSequential(px, w, h, dataWidth, bits, bitRgb, payloadLen) {
  var totalDataPixels = SIG_ROWS * dataWidth;
  // Widest px/bit that fits (fatter = quieter + JPEG-tougher). Mirror bar.py.
  var ppb = null;
  for (var cand = PIXELS_PER_BIT_MAX; cand >= PIXELS_PER_BIT_NARROW; cand--) {
    var cap = Math.floor(Math.floor(totalDataPixels / cand) / 8) - 8 - RS_NSYM;
    if (payloadLen <= cap) { ppb = cand; break; }
  }
  if (ppb === null) throw new Error('Bar payload too large for image width');
  var bitsPerRow = Math.floor(dataWidth / ppb);
  for (var ro = 0; ro < SIG_ROWS; ro++) {
    var y = h - 1 - ro;
    _paintBands(px, w, y);
    var rowStart = ro * bitsPerRow;
    for (var bil = 0; bil < bitsPerRow; bil++) {
      var bi = rowStart + bil;
      var baseX = HEADER_PIXELS + bil * ppb;
      if (bi < bits.length) {
        for (var p = 0; p < ppb; p++) _setPx(px, w, baseX + p, y, bitRgb(bits[bi], baseX + p));
      } else {
        // Filler past the payload = "1" (asym copies the row above = invisible).
        for (var p2 = 0; p2 < ppb; p2++)
          if (baseX + p2 < w - FOOTER_PIXELS) _setPx(px, w, baseX + p2, y, bitRgb(1, baseX + p2));
      }
    }
  }
}

// Embed a bar carrying `payloadBytes` into the bottom 2 rows of an RGBA pixel
// buffer, IN PLACE. Faithful port of bar.py:embed_into. `payloadBytes` is a
// Uint8Array / array of bytes (e.g. TextEncoder().encode(id + "\0" + hash)).
function embedBarPayload(px, w, h, payloadBytes, forceSequential) {
  // Asym camo reads a reference row above the 2 bar rows, so the bar needs at
  // least one clean content row above it — 3px (1 reference + 2 data) is the
  // floor. Fail loud, matching mememage/bar.py:embed_into.
  // forceSequential: keep the sequential layout even on a wide image. Band
  // FRAGMENTS (tag-prefixed payloads, not canonical <prefix>-<hex>) use this:
  // the even-fill DECODER validates each candidate via decodePayload, which
  // only accepts canonical payloads, so a fragment written even-fill is
  // unreadable. Sequential's reader returns on CRC alone, so fragments decode
  // at any width. Canonical mints/cert-save never pass it (even-fill as before).
  if (h < SIG_ROWS + 1)
    throw new Error('Bar needs an image at least ' + (SIG_ROWS + 1) + 'px tall ('
      + SIG_ROWS + ' data rows + 1 reference row); got ' + h + 'px');
  var frame = encodeFrame(payloadBytes);
  var bits = [];
  for (var i = 0; i < frame.length; i++)
    for (var j = 7; j >= 0; j--) bits.push((frame[i] >> j) & 1);

  var dataWidth = w - HEADER_PIXELS - FOOTER_PIXELS;
  var isEvenFill = !forceSequential && dataWidth >= PIXELS_PER_BIT * bits.length;

  // Asym camo: each bit rides a PER-COLUMN center copying the smoothed, floored
  // content one row above. "1" = center (invisible), "0" = center-ASYM_DELTA,
  // filler = "1". The band-edge finders COMPUTE the data edge from the data-free
  // magenta/cyan span, so content-hued data can't fool even-fill anchoring.
  // Mirrors mememage/bar.py:embed_into.
  var centerRgb = _asymCenterColumns(px, w, h).centerRgb;
  var bitRgb = function (bit, x) {
    var c = centerRgb[x];
    if (bit) return [pyRound(c[0]), pyRound(c[1]), pyRound(c[2])];
    return [Math.max(0, pyRound(c[0] - ASYM_DELTA)),
            Math.max(0, pyRound(c[1] - ASYM_DELTA)),
            Math.max(0, pyRound(c[2] - ASYM_DELTA))];
  };

  if (isEvenFill) {
    _writeEvenFill(px, w, h, bits, bitRgb);
  } else {
    _writeSequential(px, w, h, dataWidth, bits, bitRgb, payloadBytes.length);
  }
  return true;
}

// =====================================================================
// CANONICAL BAR GENERATOR — builds a 2-row PNG of the canonical bar
// payload (mememage-XXXX\0<hash>) for the validator's reconstruct flow.
// Returns a Promise<Blob> of the bar PNG. Uses the same writer as everything
// else, so the strip is exactly what Python would produce for a 2-row image.
// =====================================================================
// Pack the bar payload to binary [prefix_len][prefix][8 id][8 hash]. Identifiers
// are canonical <prefix>-<16hex> + 16hex hash (the only form Mememage stamps).
// Mirror mememage/bar.py:_pack_payload.
var _HEXRE = /^[0-9a-f]+$/;
function packPayload(identifier, contentHash) {
  var dash = identifier.lastIndexOf('-');
  var pre = dash >= 0 ? identifier.slice(0, dash) : '';
  var idhex = dash >= 0 ? identifier.slice(dash + 1) : '';
  if (!(dash >= 0 && pre.length >= 3 && pre.length <= 10 && idhex.length === 16 && _HEXRE.test(idhex)
        && contentHash.length === 16 && _HEXRE.test(contentHash)))
    throw new Error('bar identifier must be canonical <prefix>-<16 hex> + 16-hex hash');
  var pe = new TextEncoder().encode(pre);
  var out = new Uint8Array(1 + pe.length + 16);
  out[0] = pe.length; out.set(pe, 1);
  for (var i = 0; i < 8; i++) out[1 + pe.length + i] = parseInt(idhex.substr(i * 2, 2), 16);
  for (var j = 0; j < 8; j++) out[1 + pe.length + 8 + j] = parseInt(contentHash.substr(j * 2, 2), 16);
  return out;
}

function generateCanonicalBarPng(identifier, contentHash) {
  var payloadBytes = packPayload(identifier, contentHash);
  var frame = encodeFrame(payloadBytes);
  var totalBits = frame.length * 8;
  // Size the strip at 2px/bit so the writer lands on the sequential layout.
  // Height = SIG_ROWS data rows + 1 REFERENCE row above them: the asym camo
  // encodes each bit relative to the row above the bar, so a bare 2-row strip
  // is undecodable (no reference) — and embedBarPayload now rejects h < 3. The
  // extra row stays neutral gray; the asym decoder re-predicts the "1" level
  // from it, so the strip verifies standalone when dropped back in. (bitsPerRow
  // still divides by SIG_ROWS — only the 2 data rows carry bits.)
  var bitsPerRow = Math.ceil(totalBits / SIG_ROWS);
  var w = HEADER_PIXELS + bitsPerRow * PIXELS_PER_BIT_NARROW + FOOTER_PIXELS;
  var h = SIG_ROWS + 1;
  var canvas = document.createElement('canvas');
  canvas.width = w; canvas.height = h;
  var ctx = canvas.getContext('2d');
  ctx.fillStyle = '#808080';  // neutral background + asym reference row
  ctx.fillRect(0, 0, w, h);
  var img = ctx.getImageData(0, 0, w, h);
  embedBarPayload(img.data, w, h, payloadBytes);
  ctx.putImageData(img, 0, 0);
  return new Promise(function(resolve) {
    canvas.toBlob(function(blob) { resolve(blob); }, 'image/png');
  });
}

function decodePayload(payload){
  // Packed binary — first byte is the prefix length (3-10). Mirror
  // mememage/bar.py:_parse_payload.
  var n=payload[0];
  if(!(n>=3&&n<=10&&payload.length>=1+n+16))return null;
  var prefix;
  try{ prefix=new TextDecoder('utf-8',{fatal:true}).decode(payload.slice(1,1+n)); }catch(e){ return null; }
  var hx=function(off){var s='';for(var i=0;i<8;i++){var b=payload[off+i];s+=(b<16?'0':'')+b.toString(16);}return s;};
  var ident=prefix+'-'+hx(1+n);
  return{identifier:ident,archive_id:ident,content_hash:hx(1+n+8)};
}
