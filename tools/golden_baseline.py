#!/usr/bin/env python3
"""Golden-baseline harness for the re-mint refactor — NO-REGRESSION proof.

The refactor (drop legacy centered bar scheme + Otsu/128 candidates, drop the
ASCII payload branch, drop pre-V1 soul fallbacks + legacy watermark path) must be
a BEHAVIORAL NO-OP for everything currently minted. This captures the exact
deterministic output of each touched surface BEFORE the refactor, then re-checks
it AFTER. Any drift = a regression.

Surfaces locked:
  * bar WRITER     — sha256 of the barred pixel bytes (embed_into is pure)
  * bar DECODER    — extract_bar through clean + 4 realistic transforms
  * content HASH   — compute_content_hash on representative V1 records
  * luma_grid      — compute_luma_grid on a fixed image (EMBODIED, must be kept)
  * raw API encode — api.encode end-to-end (identifier + hash + barred bytes)

Usage:
  python3 tools/golden_baseline.py capture   # write tests/golden_baseline.json
  python3 tools/golden_baseline.py check      # diff current vs golden (exit 1 on drift)
"""
import hashlib
import io
import json
import os
import sys

import numpy as np
from PIL import Image

from mememage import bar
from mememage import core

GOLDEN = os.path.join(os.path.dirname(__file__), "..", "tests", "golden_baseline.json")


# ---- deterministic test images (no external files) -------------------------

def _img(kind, w, h):
    yy, xx = np.mgrid[0:h, 0:w]
    if kind == "bright":
        a = np.full((h, w, 3), 200, np.uint8)
    elif kind == "dark":                      # exercises the hybrid floor
        a = np.dstack([np.full((h, w), 8), np.full((h, w), 10), np.full((h, w), 22)]).astype(np.uint8)
    elif kind == "darkred":                   # near-black tinted (the q80-bug case)
        a = np.dstack([(20 + xx % 30), np.full((h, w), 6), np.full((h, w), 8)]).clip(0, 255).astype(np.uint8)
    elif kind == "gradient":
        g = (xx * 255 // max(1, w - 1)).clip(0, 255)
        a = np.dstack([g, (g * 7 // 10), (255 - g)]).astype(np.uint8)
    else:  # "photo" — varied, seeded noise over a gradient
        rng = np.random.default_rng(12345)
        base = (90 + 70 * np.sin(xx / 80.0) + 40 * np.cos(yy / 50.0)).clip(0, 255)
        a = np.dstack([base, (base * 8 // 10), (base * 6 // 10 + 40)]).clip(0, 255).astype(np.uint8)
        a = np.clip(a.astype(int) + rng.integers(-15, 15, a.shape), 0, 255).astype(np.uint8)
    return Image.fromarray(a, "RGB")


# (kind, w, h, identifier, content_hash) — covers sequential + even-fill, custom
# prefix, and the dark-content hybrid floor.
BAR_CASES = [
    ("bright",   512,  512,  "mememage-0123456789abcdef", "fedcba9876543210"),
    ("dark",     768,  768,  "mememage-0123456789abcdef", "fedcba9876543210"),
    ("darkred",  832,  600,  "mememage-a1b2c3d4e5f60718", "0011223344556677"),
    ("photo",    1024, 768,  "phoenix-0123456789abcdef",  "fedcba9876543210"),
    ("photo",    1216, 832,  "mememage-3196ad08a663f269", "447385017e790175"),
    ("photo",    1344, 768,  "mememage-3196ad08a663f269", "447385017e790175"),
    ("dark",     2048, 1152, "tencharsxx-0123456789abcdef".replace("tencharsxx-", "tenchars12-"), "fedcba9876543210"),
]


def _jpeg(im, q):
    b = io.BytesIO(); im.save(b, "JPEG", quality=q); b.seek(0)
    return Image.open(b).convert("RGB")


def _scale(im, f):
    return im.resize((max(1, round(im.width * f)), max(1, round(im.height * f))), Image.LANCZOS)


TRANSFORMS = {
    "clean":     lambda im: im,
    "q80":       lambda im: _jpeg(im, 80),
    "q75>q55":   lambda im: _jpeg(_jpeg(im, 75), 55),
    "0.7x+q70":  lambda im: _jpeg(_scale(im, 0.7), 70),
    "0.66x+q50": lambda im: _jpeg(_scale(im, 0.66), 50),
}


def _sha(b):
    return hashlib.sha256(b).hexdigest()[:32]


def capture():
    out = {"bar": [], "hash": [], "luma_grid": None, "encode": None}

    for kind, w, h, ident, chash in BAR_CASES:
        src = _img(kind, w, h)
        try:
            barred = bar.embed_into(src, ident, chash)
        except Exception as e:                # payload-too-large etc. — lock the error too
            out["bar"].append({"case": [kind, w, h, ident], "error": f"{type(e).__name__}"})
            continue
        bytes_hash = _sha(np.asarray(barred).tobytes())
        robustness = {}
        for tn, tf in TRANSFORMS.items():
            got = bar.extract_bar(tf(barred))
            robustness[tn] = (got == (ident, chash))
        out["bar"].append({
            "case": [kind, w, h, ident, chash],
            "barred_sha": bytes_hash,
            "decode": robustness,
        })

    # content-hash over real + constructed V1 records
    recs = []
    received = os.path.expanduser("~/.mememage/received")
    if os.path.isdir(received):
        for fn in sorted(os.listdir(received))[:3]:
            if fn.endswith(".soul"):
                try:
                    recs.append((fn, json.load(open(os.path.join(received, fn)))))
                except Exception:
                    pass
    for name, rec in recs:
        ch = rec.get("content_hash")
        out["hash"].append({"name": name, "stored": ch, "computed": core.compute_content_hash(rec)})

    # luma_grid (EMBODIED) on a fixed image — must be byte-stable
    import tempfile
    from mememage.embodiment import compute_luma_grid
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        _img("photo", 1216, 832).save(tf.name)
        out["luma_grid"] = _sha(compute_luma_grid(tf.name).encode())
        os.unlink(tf.name)

    # raw API encode — deterministic end-to-end
    try:
        from mememage import api
        rec = api.encode(_img("photo", 1024, 768), {"title": "golden", "n": 7}, prefix="raw")
        out["encode"] = {
            "identifier": rec.record["identifier"],
            "content_hash": rec.record["content_hash"],
            "hash_version": rec.record["hash_version"],
            "barred_sha": _sha(np.asarray(rec.image).tobytes()),
            "decode": bar.extract_bar(rec.image) == (rec.record["identifier"], rec.record["content_hash"]),
        }
    except Exception as e:
        out["encode"] = {"error": f"{type(e).__name__}: {e}"}

    return out


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    current = capture()
    if mode == "capture":
        with open(GOLDEN, "w") as f:
            json.dump(current, f, indent=2, sort_keys=True)
        print(f"captured golden baseline -> {os.path.relpath(GOLDEN)}")
        print(f"  {len(current['bar'])} bar cases, {len(current['hash'])} hash cases, encode={'ok' if current['encode'] and 'error' not in current['encode'] else 'ERR'}")
        return 0
    # check
    if not os.path.exists(GOLDEN):
        print("no golden baseline — run `capture` first"); return 2
    golden = json.load(open(GOLDEN))
    if current == golden:
        print("GOLDEN MATCH — no regression in any captured surface")
        return 0
    print("GOLDEN DRIFT — regression detected:")
    cj, gj = json.dumps(current, sort_keys=True, indent=2).splitlines(), json.dumps(golden, sort_keys=True, indent=2).splitlines()
    import difflib
    for line in difflib.unified_diff(gj, cj, "golden", "current", lineterm="", n=1):
        print("  " + line)
    return 1


if __name__ == "__main__":
    sys.exit(main())
