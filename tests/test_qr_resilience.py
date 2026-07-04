"""The phone-handoff QR is optional — it must never crash the request.

A Windows bundle once shipped with ``qrcode`` but no Pillow. ``qrcode``'s
default image factory imports PIL lazily inside ``make_image()``, so the
QR helper raised ModuleNotFoundError mid-upload, killing the request
thread with no response → the browser saw "Failed to fetch" on every
mint. The helper now swallows ANY failure and returns "" (the URL alone
hands off to a phone).
"""

import unittest
from unittest.mock import patch

from mememage import server


class QrResilienceTests(unittest.TestCase):
    def test_qr_returns_data_uri_when_deps_present(self):
        # Sanity: with Pillow installed (dev/test env) we get a PNG data URI.
        uri = server._generate_qr_data_uri("https://example.com/mint/abc")
        self.assertTrue(uri.startswith("data:image/png;base64,"))

    def test_qr_missing_pillow_does_not_raise(self):
        # Simulate qrcode-present / PIL-absent: make_image blows up the way
        # qrcode/image/pil.py does without Pillow. The helper must return ""
        # rather than propagate (which would 500 the upload with no body).
        import qrcode

        def _boom(*a, **k):
            raise ModuleNotFoundError("No module named 'PIL'")

        with patch.object(qrcode.QRCode, "make_image", _boom):
            self.assertEqual(
                server._generate_qr_data_uri("https://example.com/mint/abc"), "")

    def test_qr_missing_qrcode_module_does_not_raise(self):
        # Even the import guard path returns "" cleanly.
        import builtins
        real_import = builtins.__import__

        def _no_qrcode(name, *a, **k):
            if name == "qrcode":
                raise ImportError("No module named 'qrcode'")
            return real_import(name, *a, **k)

        with patch.object(builtins, "__import__", _no_qrcode):
            self.assertEqual(
                server._generate_qr_data_uri("https://example.com/mint/abc"), "")


if __name__ == "__main__":
    unittest.main()
