// Regression: JS decodeFrame must REJECT a frame whose stored CRC doesn't match
// the RS-decoded payload (mirrors bar.py _try_decode_frame). Before this guard,
// a wrong bit-read during the threshold/offset sweep could RS-"correct" into a
// magic-prefixed garbage frame and be accepted -> bogus identifier in the browser.
const fs=require("fs"), path=require("path");
const DOCS=path.join(__dirname,"..","docs","js");
global.SIG_ROWS=2;global.HEADER_BAND=8;global.HEADER_PIXELS=24;global.FOOTER_PIXELS=24;global.RS_NSYM=6;
global.ASYM_ENCODE=true;global.ASYM_DELTA=40;global.ASYM_FLOOR=50;global.ASYM_BOX_RADIUS=34;global.ASYM_SCALE_CAP=2.0;
global.RGB_THRESHOLD=128;global.PIXELS_PER_BIT=3;global.PIXELS_PER_BIT_NARROW=2;global.PIXELS_PER_BIT_MAX=6;
global.BAR_DELTA=64;global.LOCAL_CONTEXT_ROWS=6;global.EVENFILL_MIN_BYTES=33;global.EVENFILL_MAX_BYTES=64;
eval(fs.readFileSync(path.join(DOCS,"rs.js"),"utf8"));
eval(fs.readFileSync(path.join(DOCS,"codec.js"),"utf8"));
function bitsOf(frame){const b=[];for(const byte of frame)for(let j=7;j>=0;j--)b.push((byte>>j)&1);return b;}
let fail=0;
// 1) a clean frame decodes
const payload=packPayload("mememage-3196ad08a663f269","447385017e790175");
const frame=encodeFrame(payload);
const ok=decodeFrame(bitsOf(frame));
const dec=ok&&decodePayload(ok.payload);
if(!dec||dec.identifier!=="mememage-3196ad08a663f269"||dec.content_hash!=="447385017e790175"){console.error("FAIL: clean frame did not decode:",JSON.stringify(dec));fail=1;}
// 2) corrupt the stored CRC (bytes 6,7) — codeword intact, so RS decodes to the
//    SAME payload, but the stored CRC is now wrong -> must be REJECTED (null).
const bad=frame.slice(); bad[6]^=0xFF; bad[7]^=0xAA;
const r=decodeFrame(bitsOf(bad));
if(r!==null){console.error("FAIL: frame with wrong CRC was ACCEPTED (the bug):",JSON.stringify(r));fail=1;}
console.log(fail?"DECODE-CRC TESTS FAILED":"DECODE-CRC TESTS PASSED");
process.exit(fail);
