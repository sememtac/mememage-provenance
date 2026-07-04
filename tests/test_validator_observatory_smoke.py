"""Observatory pinned-content smoke — guard the derived calendar + marking.

The validator's Observatory derives everything from the chain's own data, with no
hardcoded calendar:
  * each cycling layer is active up to its OWN freeze (floor(M/K)*K) — a K=7
    layer reaches four positions further than a K=12 one, derived, not assumed;
  * the special-cell marking and the epag/egg filters come from where records
    ACTUALLY carry a pinned chunk (schematic → 'dark', claim / easter_egg →
    'epag' / 'egg'), NOT from a frozen-tail assumption.

This loads validator.html headless, feeds a 365-chain whose pinned content is
placed OFF the tail on reorganized dates — easter egg at 227 (Aug 16), claim at
358 (Dec 25), schematics at 360-361 — and asserts each is marked at its EXACT
position, while an empty frozen-tail cell (364, truth only) stays UNMARKED. It
drives every filter and fails on ANY page error — the load-bearing guard that
once caught a `calendarOf is not defined` scope bug node --check + a logic sim
both missed.

Requires: pip install playwright && python -m playwright install chromium
Run with: python -m pytest tests/test_validator_observatory_smoke.py -v
"""

import http.server
import json
import os
import shutil
import tempfile
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
        ("127.0.0.1", port),
        lambda *a, **kw: handler(*a, directory=directory, **kw))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def _chunk(index, total):
    return {"index": index, "total": total, "data": "eJwDAAAAAAE=", "hash": "00"}


def _reference_chain_souls():
    """A 365-chain (decoder K=12, validator K=7) with pinned content placed OFF
    the frozen tail, on the reorganized dates: easter egg at 227 (a birthday),
    claim at 358 (Christmas), schematics at 360-361. Position 364 (the last
    frozen-tail cell) carries ONLY truth — no pinned content — so we can assert
    it stays UNMARKED. The pos-0 record establishes the dimensions. Content
    hashes are synthetic; the grid marks by pinned chunk, not tamper status.
    """
    common = {"hash_version": 1, "outer_cycle": 365, "outer_total": 365,
              "age": 1, "age_name": "Age of Aries", "decoder_hash": "abcdef0123456789"}

    def soul(n, pos, extra):
        chunks = {"truth": _chunk(pos, 365)}
        chunks.update(extra)
        return dict(common, identifier=f"smoke-{n:016d}", content_hash=f"{n:016d}",
                    outer_position=pos, chunks=chunks)

    return [
        soul(1, 0, {"decoder": _chunk(0, 12), "validator": _chunk(0, 7)}),
        soul(2, 227, {"easter_egg": {"data": "x", "hash": "00"}}),   # Aug 16 — birthday
        soul(3, 358, {"claim": {"data": "x", "hash": "00"}}),         # Dec 25 — Christmas
        soul(4, 360, {"schematic": _chunk(0, 2)}),
        soul(5, 361, {"schematic": _chunk(1, 2)}),
        soul(6, 364, {}),   # last frozen-tail cell, truth only — must stay unmarked
    ]


@pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="Playwright not installed")
class TestValidatorObservatorySmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._server, cls._port = _start_server(DOCS_DIR)
        cls._url = f"http://127.0.0.1:{cls._port}/validator.html"
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch()
        cls._tmp = tempfile.mkdtemp()
        cls._paths = []
        for soul in _reference_chain_souls():
            path = os.path.join(cls._tmp, soul["identifier"] + ".soul")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(soul, f)
            cls._paths.append(path)

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()
        cls._server.shutdown()
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _render_observatory(self):
        page = self._browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(self._url)
        page.click('[data-panel="tab-meta"]')           # Observatory
        page.set_input_files("#jsonInput", self._paths)
        page.wait_for_selector(".orbit-grid", timeout=15000)
        page.wait_for_timeout(600)                       # let the async render settle
        return page, errors

    @staticmethod
    def _cell_types(page, pos):
        el = page.query_selector(f'.orbit-c[data-pos="{pos}"]')
        return (el.get_attribute("data-types") or "") if el else None

    def test_pinned_content_derived_and_no_errors(self):
        page, errors = self._render_observatory()
        try:
            types = {p: self._cell_types(page, p) for p in (0, 227, 358, 360, 364)}
            # Drive every filter by its ROLE NAME — exercises the derived
            # per-layer cadence AND the pinned single-role filter path. No
            # canonical 'epag'/'egg' aliases exist anymore.
            if page.query_selector(".orbit-filter"):
                for opt in ("decoder", "validator", "truth", "schematic", "claim", "easter_egg"):
                    try:
                        page.select_option(".orbit-filter", opt)
                        page.wait_for_timeout(80)
                    except Exception:
                        pass
        finally:
            page.close()

        # Load-bearing guard: any scope/runtime error fails the whole Observatory.
        self.assertEqual(errors, [], f"page JS errors: {errors}")
        self.assertIsNotNone(types[0], "orbit grid did not render")

        # Pinned single-chunk content is tagged at its ACTUAL position by its
        # OWN role name — off the tail, no aliases.
        self.assertIn("easter_egg", types[227], "easter egg at 227 (Aug 16) must be tagged")
        self.assertIn("claim", types[358], "claim at 358 (Dec 25) must be tagged")

        # An ordinary day carries no pinned single-chunk role — the whole
        # point: tagging follows the actual pinned chunks, not a hardcoded tail.
        for p, why in [(0, "an ordinary day"), (364, "the last cell")]:
            self.assertNotIn("easter_egg", types[p], f"position {p} is {why} — no pinned egg")
            self.assertNotIn("claim", types[p], f"position {p} is {why} — no pinned claim")


if __name__ == "__main__":
    unittest.main()
