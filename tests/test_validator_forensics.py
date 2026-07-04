"""Validate the validator: drive its REAL verification engine against fixtures.

The validator is the forensic proof surface. This loads validator.html in a
headless browser and, for each tampered/clean variant of one real soul, calls
the page's OWN functions — computeContentHash() (drives the WITNESSED badge) and
verifySignature() (drives AUTHENTICATED / FORGED) — asserting the verdict matches
what the canonical Python core says it must be. If the browser ever disagrees
with the server's truth, this fails.

Requires: pip install playwright && python -m playwright install chromium
Run with: python -m pytest tests/test_validator_forensics.py -v

The companion tools/gen_validator_fixtures.py writes these same variants + a
manual CHECKLIST.md for eyeballing the on-screen display.
"""

import copy
import http.server
import json
import os
import threading
import unittest

import pytest

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

DOCS_DIR = os.path.join(os.path.dirname(__file__), "..", "docs")
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "validator_base.soul")
BAR_IMAGE = os.path.join(os.path.dirname(__file__), "fixtures", "validator_bar_image.png")


def _start_server(directory, port=0):
    handler = http.server.SimpleHTTPRequestHandler
    server = http.server.HTTPServer(
        ("127.0.0.1", port),
        lambda *a, **kw: handler(*a, directory=directory, **kw))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def build_variants(base):
    """The same five manipulations as tools/gen_validator_fixtures.py, with the
    verdict each MUST produce. (witnessed, signature) where signature is
    'valid' / 'invalid' / 'none'."""
    out = {}

    out["valid"] = (copy.deepcopy(base), True, "valid")

    altered = copy.deepcopy(base)
    altered["width"] = (altered.get("width") or 100) + 1   # a hashed field
    out["altered_field"] = (altered, False, None)           # WITNESSED fails first

    forged = copy.deepcopy(base)
    sig = forged["signature"]
    forged["signature"] = ("f" if sig[0] != "f" else "0") + sig[1:]
    out["forged_signature"] = (forged, True, "invalid")     # hash ok, sig broken

    unsigned = copy.deepcopy(base)
    unsigned.pop("signature", None)
    out["unsigned"] = (unsigned, True, "none")              # hash ok, no signature

    swapped = copy.deepcopy(base)
    pk = swapped["public_key"]
    swapped["public_key"] = ("0" if pk[0] != "0" else "1") + pk[1:]
    out["swapped_key"] = (swapped, False, None)             # public_key is hashed

    return out


_VERIFY_JS = """
async (rec) => {
  const computed = await computeContentHash(rec);
  const witnessed = computed === rec.content_hash;
  let sig = 'none';
  if (rec.signature && rec.public_key) {
    const th = await _thumbnailHashForSig(rec);
    const v = await verifySignature(rec.identifier, rec.content_hash,
                                    rec.signature, rec.public_key, th);
    sig = (v === true) ? 'valid' : (v === false ? 'invalid' : 'inconclusive');
  }
  return { witnessed, sig, computed, stored: rec.content_hash };
}
"""


@pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="Playwright not installed")
@pytest.mark.skipif(not os.path.exists(FIXTURE), reason="base soul fixture missing")
class TestValidatorForensics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._server, cls._port = _start_server(DOCS_DIR)
        cls._url = f"http://127.0.0.1:{cls._port}/validator.html"
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch()
        with open(FIXTURE, encoding="utf-8") as f:
            cls.base = json.load(f)
        cls.variants = build_variants(cls.base)

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()
        cls._server.shutdown()

    def _verify(self, record):
        """Run the validator's own verify functions on a record."""
        page = self._browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(self._url)
        page.wait_for_function("typeof computeContentHash === 'function' "
                               "&& typeof verifySignature === 'function'")
        result = page.evaluate(_VERIFY_JS, record)
        page.close()
        self.assertEqual(errors, [], f"page JS errors: {errors}")
        return result

    def _check(self, name):
        record, exp_witnessed, exp_sig = self.variants[name]
        r = self._verify(record)
        self.assertEqual(
            r["witnessed"], exp_witnessed,
            f"{name}: WITNESSED expected {exp_witnessed}, got {r['witnessed']} "
            f"(computed {r['computed']} vs stored {r['stored']})")
        if exp_sig is not None:
            self.assertEqual(
                r["sig"], exp_sig,
                f"{name}: signature expected {exp_sig}, got {r['sig']}")

    # Each fixture's verdict, from the validator's own engine:
    def test_valid_is_witnessed_and_authenticated(self):
        self._check("valid")           # WITNESSED ✓ + signature valid

    def test_altered_field_breaks_witnessed(self):
        self._check("altered_field")   # hash MISMATCH → ALTERED

    def test_forged_signature_keeps_hash_breaks_signature(self):
        self._check("forged_signature")  # WITNESSED ✓, signature INVALID

    def test_unsigned_is_witnessed_without_signature(self):
        self._check("unsigned")        # WITNESSED ✓, no signature

    def test_swapped_key_breaks_witnessed(self):
        # The public key is part of the content hash (signer-swap defense),
        # so swapping it must break WITNESSED, not just the signature.
        self._check("swapped_key")

    # Image tab — drop a real bar-bearing PNG and confirm the bar fields read
    # correctly: bar found, identifier decoded, Reed-Solomon pristine (clean
    # image), bit confidence rendered, even-fill architecture (wide image).
    @unittest.skipUnless(os.path.exists(BAR_IMAGE), "bar image fixture missing")
    def test_image_tab_decodes_bar_fields(self):
        import base64
        with open(BAR_IMAGE, "rb") as f:
            data_url = "data:image/png;base64," + base64.b64encode(f.read()).decode()
        page = self._browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(self._url)
        page.wait_for_function("typeof analyze === 'function'")
        # Inject the image as a File and run the validator's own analyze().
        page.evaluate("""async (dataUrl) => {
            const res = await fetch(dataUrl);
            const blob = await res.blob();
            analyze(new File([blob], 'bar.png', { type: 'image/png' }));
        }""", data_url)
        # The bar status renders into #imgResults; wait for the RS panel.
        page.wait_for_function(
            "() => { var r = document.getElementById('imgResults');"
            "return r && /Reed-Solomon/.test(r.textContent); }", timeout=15000)
        results = page.eval_on_selector("#imgResults", "el => el.textContent")
        body = page.eval_on_selector("body", "el => el.textContent")
        page.close()
        self.assertEqual(errors, [], f"page JS errors: {errors}")
        ident = self.base["identifier"]
        self.assertIn(ident, body, "bar identifier not decoded/shown")
        self.assertNotIn("No Mememage bar", body, "bar should be found")
        self.assertIn("Reed-Solomon", results)
        self.assertIn("Pristine", results)          # clean image → 0 RS errors
        self.assertIn("clear 1", results)           # bit-confidence legend
        self.assertIn("EVEN-FILL", results)         # 1600px wide → even-fill

    def _render_audit(self, record):
        """Drive the page's renderAudit() on a record; return its text once the
        async content-hash verdict (MATCH/MISMATCH) has landed."""
        page = self._browser.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(self._url)
        page.wait_for_function("typeof renderAudit === 'function'")
        page.evaluate(
            "(rec) => { var d = document.createElement('div'); d.id='AUDITT';"
            "document.body.appendChild(d); renderAudit(rec, rec.identifier, d); }",
            record)
        page.wait_for_function(
            "() => { var e = document.getElementById('auditHashResult');"
            "return e && /MATCH|MISMATCH/.test(e.textContent); }", timeout=10000)
        txt = page.eval_on_selector("#AUDITT", "el => el.textContent")
        page.close()
        self.assertEqual(errs, [], f"page JS errors: {errs}")
        return txt

    def test_audit_core_open_soul_generalized(self):
        # A core "open" soul: its hash WITNESSES (the audit uses the canonical
        # computeContentHash, open-aware), its own fields show under "Soul
        # Fields", and the canonical sections (Generation/Machine/Song) don't
        # render a wall of "?".
        from mememage import core
        rec = {"identifier": "mememage-deadbeefcafe0001", "hash_version": "open",
               "prompt": "a quiet river", "license": "CC-BY-4.0",
               "author": "andy", "camera": "iPhone 15"}
        rec["content_hash"] = core.compute_content_hash(rec)
        txt = self._render_audit(rec)
        self.assertIn("MATCH", txt)
        self.assertNotIn("MISMATCH", txt)
        self.assertIn("Soul Fields", txt)
        for v in ("CC-BY-4.0", "andy", "iPhone 15"):
            self.assertIn(v, txt, f"custom field {v} not shown")
        self.assertIn("every field is yours", txt)        # open schema note
        self.assertNotIn("Fingerprint", txt)              # no canonical machine
        self.assertNotIn("Modal Scale", txt)              # no song forensics

    def test_audit_sealed_soul_unlock(self):
        # A core seal(password=…) soul: WITNESSES sealed (no password), shows the
        # Sealed unlock surface, hides the private fields; on the right password
        # it decrypts via Access and re-renders revealed — WITNESSED throughout.
        try:
            from mememage import access
            if not access.is_encryption_available():
                self.skipTest("cryptography not available")
        except Exception:
            self.skipTest("access layer unavailable")
        import tempfile
        from PIL import Image
        import mememage
        pw = "hunter2"
        img = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
        Image.new("RGB", (1024, 576), (40, 90, 120)).save(img)
        result = mememage.encode(img, {"prompt": "secret prompt", "note": "private note"},
                                 password=pw)
        rec = result.record

        page = self._browser.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(self._url)
        page.wait_for_function("typeof renderAudit === 'function' "
                               "&& typeof auditUnlock === 'function'")
        page.evaluate("(rec) => { var d = document.createElement('div'); d.id='SU';"
                      "document.body.appendChild(d); renderAudit(rec, rec.identifier, d); }", rec)
        page.wait_for_function(
            "() => { var e = document.getElementById('auditHashResult');"
            "return e && /MATCH|MISMATCH/.test(e.textContent); }", timeout=10000)
        locked = page.eval_on_selector("#SU", "el => el.textContent")
        self.assertIn("MATCH", locked)
        self.assertNotIn("MISMATCH", locked)
        self.assertIn("Sealed", locked)
        self.assertNotIn("secret prompt", locked)      # private hidden while locked
        self.assertIsNotNone(page.query_selector("#auditUnlockPw"))

        # unlock with the right password → revealed, still WITNESSED
        page.fill("#auditUnlockPw", pw)
        page.evaluate("auditUnlock()")
        page.wait_for_function("() => /Unlocked/.test(document.getElementById('SU').textContent)",
                               timeout=10000)
        page.wait_for_function(
            "() => { var e = document.getElementById('auditHashResult');"
            "return e && /MATCH|MISMATCH/.test(e.textContent); }", timeout=10000)
        unlocked = page.eval_on_selector("#SU", "el => el.textContent")
        page.close()
        self.assertEqual(errs, [], f"page JS errors: {errs}")
        self.assertIn("secret prompt", unlocked)        # revealed
        self.assertIn("private note", unlocked)
        self.assertIn("MATCH", unlocked)
        self.assertNotIn("MISMATCH", unlocked)          # hash still over the shell

    def test_audit_canonical_soul_still_full(self):
        # The guards must not regress a canonical soul — it still renders its
        # rich sections (it carries the fields).
        txt = self._render_audit(self.base)
        self.assertIn("MATCH", txt)
        # the canonical base soul has a machine fingerprint + celestial data
        if self.base.get("machine_fingerprint"):
            self.assertIn("Fingerprint", txt)

    # And one UI-level check: the Observatory actually RENDERS the verdict.
    def test_observatory_renders_untampered_for_valid(self):
        import tempfile
        page = self._browser.new_page()
        page.goto(self._url)
        page.wait_for_load_state("networkidle")
        page.click('[data-panel="tab-meta"]')   # Observatory
        with tempfile.NamedTemporaryFile("w", suffix=".soul", delete=False) as tf:
            json.dump(self.variants["valid"][0], tf)
            path = tf.name
        try:
            page.set_input_files("#jsonInput", path)
            # hash check is async (SubtleCrypto) — wait for the rendered verdict
            page.wait_for_function(
                "/Untampered|hashes match/.test(document.body.textContent)",
                timeout=10000)
        finally:
            page.close()
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
