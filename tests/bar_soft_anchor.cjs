// The JS band-edge finders must recover the M/Y/C anchors on a bar whose bands
// have been chroma-diluted by a 0.5x downscale + JPEG (strict absolute cutoffs
// fail there; the soft channel-ordering fallback recovers them). Mirrors
// bar.py:_BAND_PREDICATE_PASSES. Pixels come from Python (real embed + real
// downscale + real JPEG) via a JSON dump on argv[2].
const fs=require("fs"), path=require("path");
const DOCS=path.join(__dirname,"..","docs","js");
global.SIG_ROWS=2;global.HEADER_BAND=8;global.HEADER_PIXELS=24;global.FOOTER_PIXELS=24;global.RS_NSYM=6;
global.ASYM_ENCODE=true;global.ASYM_DELTA=40;global.ASYM_FLOOR=50;global.ASYM_BOX_RADIUS=34;global.ASYM_SCALE_CAP=2.0;
global.RGB_THRESHOLD=128;global.PIXELS_PER_BIT=3;global.PIXELS_PER_BIT_NARROW=2;global.PIXELS_PER_BIT_MAX=6;
global.BAR_DELTA=64;global.LOCAL_CONTEXT_ROWS=6;global.EVENFILL_MIN_BYTES=33;global.EVENFILL_MAX_BYTES=64;
eval(fs.readFileSync(path.join(DOCS,"rs.js"),"utf8"));
eval(fs.readFileSync(path.join(DOCS,"codec.js"),"utf8"));

const cases=JSON.parse(fs.readFileSync(process.argv[2],"utf8"));
let fail=0;

// 1) soft predicates classify chroma-smeared band pixels the strict ones miss.
//    (values measured from a real 0.5x + q80 4:2:0 round-trip)
const P=BAND_PREDICATE_PASSES;
if(P.length!==2){console.error("FAIL: expected strict+soft passes, got",P.length);fail=1;}
if(P[0].y([241,233,122])){console.error("FAIL: strict yellow should reject smeared pixel");fail=1;}
if(!P[1].y([241,233,122])){console.error("FAIL: soft yellow should accept smeared pixel");fail=1;}
if(P[0].c([127,202,171])){console.error("FAIL: strict cyan should reject smeared pixel");fail=1;}
if(!P[1].c([127,202,171])){console.error("FAIL: soft cyan should accept smeared pixel");fail=1;}
// soft must stay selective: neutral / skin-ish pixels match nothing
for(const px of [[128,128,128],[250,250,250],[220,180,150]]){
  if(P[1].m(px)||P[1].y(px)||P[1].c(px)){console.error("FAIL: soft matched a non-band pixel",px);fail=1;}
}

// 2) end-to-end: decode the Python-produced downscaled+JPEG'd pixels.
for(const c of cases){
  const px=Uint8ClampedArray.from(c.rgba);
  const got=extractBarScaleAware(px,c.w,c.h);
  const ok=got&&got.identifier===c.identifier&&got.content_hash===c.content_hash;
  if(!ok){
    console.error(`FAIL: ${c.name} (${c.w}x${c.h}) -> ${JSON.stringify(got)}`);
    fail=1;
  }
}

// 3) the case must be DISCRIMINATING: with only the strict pass installed, the
//    very same pixels must FAIL. Otherwise (2) proves nothing and this test
//    would keep passing if the soft fallback were deleted.
const soft=BAND_PREDICATE_PASSES;
BAND_PREDICATE_PASSES=[soft[0]];
for(const c of cases){
  const px=Uint8ClampedArray.from(c.rgba);
  if(extractBarScaleAware(px,c.w,c.h)){
    console.error(`FAIL: ${c.name} decodes with STRICT predicates alone — test is vacuous`);
    fail=1;
  }
}
BAND_PREDICATE_PASSES=soft;
console.log(fail?"SOFT-ANCHOR TESTS FAILED":"SOFT-ANCHOR TESTS PASSED");
process.exit(fail);
