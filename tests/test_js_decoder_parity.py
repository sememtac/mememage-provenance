"""CI parity gate: the shared JS decoder library (packaging/js) must stay
byte-compatible with the Python core.

This is the guard that lets the JS decoder be fire-and-forget. It regenerates the
parity vectors from the CURRENT core (bars via mememage.bar.embed_into, hashes via
mememage.core.compute_content_hash) into a temp dir, then runs the library's own
node parity suites against them. If a core change ever alters what the decoder must
read — a new bar layout, a changed hash — and the JS side no longer matches, THIS
test goes red. So a core change and its JS reader are checked together, in one CI, and
nobody has to remember to run the node tests by hand.

The JS decoder is read-only (no bar writer, no record minter), so this is the only
direction that can drift: does JS read what core wrote. Needs Node + Pillow; skips
cleanly if either is missing.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

try:
    import PIL  # noqa: F401  (gen-vectors.py needs Pillow to build the bar images)
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
JSTEST = os.path.join(ROOT, "packaging", "js", "test")
NODE = shutil.which("node")


@unittest.skipUnless(HAS_PIL, "Pillow required")
@unittest.skipUnless(NODE, "Node.js required for JS parity")
class TestJsDecoderParity(unittest.TestCase):
    def _run(self, args, **kw):
        return subprocess.run(args, capture_output=True, text=True, timeout=120, **kw)

    def test_js_decoder_matches_current_core(self):
        with tempfile.TemporaryDirectory() as td:
            vpath = os.path.join(td, "vectors.json")
            hpath = os.path.join(td, "hash-vectors.json")

            epath = os.path.join(td, "encode-vectors.json")
            cpath = os.path.join(td, "crypto-vectors.json")
            jsout = os.path.join(td, "js-crypto-out.json")

            # 1. regenerate vectors from the CURRENT core (this is the authority)
            for script, dst in (("gen-vectors.py", vpath), ("gen-hash-vectors.py", hpath),
                                ("gen-encode-vectors.py", epath), ("gen-crypto-vectors.py", cpath)):
                g = self._run([sys.executable, os.path.join(JSTEST, script), dst], cwd=ROOT)
                self.assertEqual(g.returncode, 0, f"{script} failed:\n{g.stderr}")

            # 2. the JS SDK must decode / verify / encode that core output identically
            for script, dst, what in (("decode-parity.mjs", vpath, "decode"),
                                      ("verify-parity.mjs", hpath, "verify"),
                                      ("encode-parity.mjs", epath, "encode")):
                r = self._run([NODE, os.path.join(JSTEST, script), dst])
                self.assertEqual(r.returncode, 0,
                                 f"JS {what} drifted from core:\n{r.stdout}\n{r.stderr}")

            # 3. encryption is bidirectional (random nonce → cross-decrypt, not byte-match):
            #    JS opens Python's envelopes/records, and Python opens what JS writes.
            cj = self._run([NODE, os.path.join(JSTEST, "crypto-parity.mjs"), cpath, jsout])
            self.assertEqual(cj.returncode, 0, f"JS encryption drifted from core:\n{cj.stdout}\n{cj.stderr}")
            jc = self._run([sys.executable, os.path.join(JSTEST, "check-js-crypto.py"), jsout], cwd=ROOT)
            self.assertEqual(jc.returncode, 0, f"Python can't open JS encryption:\n{jc.stdout}\n{jc.stderr}")

            # 4. the JS PNG WRITER (toPngBytes): a barred .png written entirely by
            #    JS must open in PIL and pass the Python core's own api.verify.
            png_path = os.path.join(td, "js-out.png")
            rec_path = os.path.join(td, "js-out-record.json")
            pw = self._run([NODE, os.path.join(JSTEST, "png-out.mjs"), png_path, rec_path])
            self.assertEqual(pw.returncode, 0, f"JS png-out failed:\n{pw.stdout}\n{pw.stderr}")
            import json as _json
            from mememage import api
            with open(rec_path, encoding="utf-8") as f:
                rec = _json.load(f)
            v = api.verify(png_path, rec)
            self.assertTrue(v.match, f"Python can't verify the JS-written PNG: {v.reason}")


if __name__ == "__main__":
    unittest.main()
