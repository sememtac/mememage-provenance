"""Smoke tests for the Mememage decoder site using Playwright.

Requires: pip install playwright && python -m playwright install chromium
Run with: python -m pytest tests/test_decoder_site.py -v
"""

import http.server
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


def _start_server(directory, port=0):
    """Start a simple HTTP server in a background thread. Returns (server, port)."""
    handler = http.server.SimpleHTTPRequestHandler
    server = http.server.HTTPServer(("127.0.0.1", port), lambda *a, **kw: handler(*a, directory=directory, **kw))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


@pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="Playwright not installed")
class TestDecoderSite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._server, cls._port = _start_server(DOCS_DIR)
        cls._base_url = f"http://127.0.0.1:{cls._port}"
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()
        cls._server.shutdown()

    def test_page_loads(self):
        """index.html should load without JS errors."""
        page = self._browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(self._base_url)
        page.wait_for_load_state("networkidle")

        self.assertIn("Mememage", page.title())
        self.assertEqual(errors, [], f"JS errors on load: {errors}")
        page.close()

    def test_drop_zone_visible(self):
        """The drop zone should be visible on page load."""
        page = self._browser.new_page()
        page.goto(self._base_url)
        page.wait_for_load_state("networkidle")

        drop_zone = page.locator("#dropZone")
        self.assertTrue(drop_zone.is_visible())
        page.close()

    def test_lookup_tab_works(self):
        """Switching to the Lookup tab should show the ID input."""
        page = self._browser.new_page()
        page.goto(self._base_url)
        page.wait_for_load_state("networkidle")

        # Click the Lookup tab
        page.click('[data-panel="lookupPanel"]')

        lookup_input = page.locator("#lookupInput")
        self.assertTrue(lookup_input.is_visible())
        page.close()

    def test_invalid_id_shows_error(self):
        """Looking up a nonexistent ID should show an error status."""
        page = self._browser.new_page()
        page.goto(self._base_url)
        page.wait_for_load_state("networkidle")

        # Well-formed but nonexistent identifier (16 hex). A short hex stub
        # like "mememage-00000000" now fails client-side format validation
        # (identifiers are exactly <prefix>-<16 hex>) before any fetch — this
        # input exercises the intended nonexistent-record fetch-failure path.
        page.click('[data-panel="lookupPanel"]')
        page.fill("#lookupInput", "mememage-0123456789abcdef")
        page.click("#lookupBtn")

        # Wait for the fetch to complete (3 retries × 2s delay = ~8s total)
        page.wait_for_timeout(10000)

        # Errors for the lookup panel are scoped to its own error elements
        # (#lookupErrorHead / #lookupErrorBody) rather than the global #status.
        head_text = page.locator("#lookupErrorHead").inner_text()
        body_text = page.locator("#lookupErrorBody").inner_text()
        error_text = (head_text + " " + body_text).lower()
        self.assertTrue(
            "failed" in error_text or "error" in error_text
            or "not" in error_text or "invalid" in error_text,
            f"Expected error message, got head={head_text!r}, body={body_text!r}"
        )
        page.close()

    def test_css_loaded(self):
        """External CSS should be loaded (not inline styles)."""
        page = self._browser.new_page()
        page.goto(self._base_url)
        page.wait_for_load_state("networkidle")

        # Check that the CSS file was loaded by verifying a styled element
        bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
        # Should be the dark background from mememage.css, not default white
        self.assertNotEqual(bg, "rgba(0, 0, 0, 0)", "CSS not loaded — body has no background")
        page.close()

    def test_js_modules_loaded(self):
        """All JS modules should be loaded (key functions should exist)."""
        page = self._browser.new_page()
        page.goto(self._base_url)
        page.wait_for_load_state("networkidle")

        # Check that key functions from each module are defined
        checks = {
            "data.js": "typeof BODIES !== 'undefined'",
            "codec.js": "typeof crc16 === 'function'",
            "cert-renderer.js": "typeof renderCert === 'function'",
            "sky-band.js": "typeof initSkyBand === 'function'",
            "ui.js": "typeof fetchFromSource === 'function'",
        }
        for module, check in checks.items():
            result = page.evaluate(check)
            self.assertTrue(result, f"{module} not loaded: {check} returned false")
        page.close()


if __name__ == "__main__":
    unittest.main()
