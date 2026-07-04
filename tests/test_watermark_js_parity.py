"""Parity: the browser watermark extractor (docs/js/watermark.js) must read the
same content hash Python embeds — clean and through JPEG. Extraction is
sign-based (the bit is the sign of one DCT coefficient), so unlike the bar WRITER
this needs no byte-exact float parity; it just needs the same bits → same hash.
"""
import io
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("PIL")
pytest.importorskip("numpy")
from PIL import Image

from mememage.watermark import embed_watermark, extract_watermark

NODE = shutil.which("node")
ROOT = Path(__file__).resolve().parent.parent
WM_JS = ROOT / "docs" / "js" / "watermark.js"
CH = "8b1ac9a3c04f6492"  # full 16-hex content hash

_HARNESS = """
const fs=require("fs"),vm=require("vm");
const sb={Math,Array,Float64Array,Uint8ClampedArray,Uint8Array,console,parseInt,isNaN};
vm.createContext(sb);
vm.runInContext(fs.readFileSync(process.argv[2],"utf8"),sb,{filename:"watermark.js"});
const buf=fs.readFileSync(process.argv[3]);
const w=buf.readUInt32BE(0),h=buf.readUInt32BE(4);
sb.PX=new Uint8ClampedArray(buf.buffer,buf.byteOffset+8,w*h*4);sb.W=w;sb.H=h;sb.CH=process.argv[4];
const r=vm.runInContext("extractWatermark(PX,W,H,CH)",sb);
process.stdout.write(r?r.hash:"NULL");
"""


def _img(w=896, h=640, seed=7):
    import numpy as np
    yy, xx = np.mgrid[0:h, 0:w]
    a = np.stack([(120 + 80 * np.sin(xx / 23.0) + yy * 30 // h),
                  (140 + 60 * np.cos(yy / 19.0)),
                  (110 + 50 * np.sin((xx + yy) / 17.0))], -1).clip(0, 255)
    rng = np.random.default_rng(seed)
    a = np.clip(a + rng.integers(-18, 18, a.shape), 0, 255).astype("uint8")
    return Image.fromarray(a, "RGB")


def _dump(img, path):
    import numpy as np
    w, h = img.size
    path.write_bytes(struct.pack(">II", w, h) + img.convert("RGBA").tobytes())


def _js_extract(rgba_path, content_hash, tmp_path):
    harness = tmp_path / "h.cjs"
    harness.write_text(_HARNESS)
    out = subprocess.run([NODE, str(harness), str(WM_JS), str(rgba_path), content_hash],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


@pytest.mark.skipif(NODE is None, reason="node not available")
@pytest.mark.parametrize("quality", [None, 80, 70])
def test_js_extracts_same_hash_as_python(tmp_path, quality):
    p = tmp_path / "wm.png"
    _img().save(p)
    embed_watermark(str(p), CH)
    img = Image.open(p).convert("RGB")
    if quality is not None:
        b = io.BytesIO(); img.save(b, "JPEG", quality=quality, subsampling=2); b.seek(0)
        img = Image.open(b).convert("RGB")

    # Python reference (write to disk for the file-based API)
    rp = tmp_path / "r.png"; img.save(rp)
    assert extract_watermark(str(rp), CH) == CH, "python extract failed (test setup)"

    # JS must agree
    rgba = tmp_path / "f.bin"; _dump(img, rgba)
    assert _js_extract(rgba, CH, tmp_path) == CH
