"""Starfield must not flicker on mobile scroll.

Mobile browsers fire a 'resize' as the address bar shows/hides on scroll:
window.innerHeight changes, innerWidth does not. The starfield used to rebuild
the canvas on every such event, and reassigning canvas.width/height CLEARS the
backing store — one blank frame per scroll = the flicker.

The fix (docs/js/starfield.js resize): rebuild the backing store only when the
WIDTH changes; absorb height changes via CSS. This test drives a real browser,
changes only the viewport height, and asserts the canvas backing store is
untouched (no clear); a width change must still rebuild it.
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
    handler = http.server.SimpleHTTPRequestHandler
    server = http.server.HTTPServer(
        ("127.0.0.1", port), lambda *a, **kw: handler(*a, directory=directory, **kw))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


@pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="Playwright not installed")
class TestStarfieldMobileScroll(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._server, cls._port = _start_server(DOCS_DIR)
        cls._base = f"http://127.0.0.1:{cls._port}"
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()
        cls._server.shutdown()

    def _load(self):
        page = self._browser.new_page(viewport={"width": 390, "height": 700})
        page.goto(self._base + "/product.html")
        page.wait_for_load_state("networkidle")
        # starfield must be present and initialized
        page.wait_for_function(
            "() => { var c = document.getElementById('starfield');"
            " return c && c.width > 0 && c.height > 0; }")
        return page

    def _backing(self, page):
        return page.evaluate(
            "() => { var c = document.getElementById('starfield');"
            " return [c.width, c.height]; }")

    def test_height_only_change_does_not_rebuild_canvas(self):
        """The mobile address-bar case: same width, shorter viewport (scroll).
        The canvas backing store must be byte-for-byte unchanged — no clear."""
        page = self._load()
        before = self._backing(page)
        # simulate the address bar collapsing: height shrinks, width identical
        page.set_viewport_size({"width": 390, "height": 620})
        page.evaluate("() => window.dispatchEvent(new Event('resize'))")
        after = self._backing(page)
        self.assertEqual(before, after,
                         "height-only resize rebuilt the canvas (would flicker)")
        # and again, height GROWS (bar hides): still no rebuild
        page.set_viewport_size({"width": 390, "height": 760})
        page.evaluate("() => window.dispatchEvent(new Event('resize'))")
        self.assertEqual(self._backing(page), before,
                         "height grow rebuilt the canvas (would flicker)")
        page.close()

    def test_width_change_does_rebuild_canvas(self):
        """A real resize / rotation (width changes) must rebuild the backing
        store — otherwise the field would be the wrong shape."""
        page = self._load()
        before = self._backing(page)
        page.set_viewport_size({"width": 800, "height": 700})  # rotate to landscape
        page.evaluate("() => window.dispatchEvent(new Event('resize'))")
        after = self._backing(page)
        self.assertNotEqual(before[0], after[0],
                            "width change did not rebuild the canvas width")
        page.close()

    def test_no_console_errors_on_load(self):
        page = self._browser.new_page(viewport={"width": 390, "height": 700})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(self._base + "/product.html")
        page.wait_for_load_state("networkidle")
        page.close()
        self.assertEqual(errors, [], f"page errors: {errors}")


if __name__ == "__main__":
    unittest.main()
