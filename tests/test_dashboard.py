"""Smoke tests for the Mememage Dashboard page.

Phase 1 (skeleton): verify the page loads, three tabs render with the
expected structure, and the tab switching toggles `.active` correctly.

Per-tab behavior (mint flow, payload manager, config) is tested in the
phases that add those features.

Run with: python -m pytest tests/test_dashboard.py -v
"""

import http.server
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

import pytest

from mememage import server

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

DOCS_DIR = os.path.join(os.path.dirname(__file__), "..", "docs")


def _start_server(directory, port=0):
    """Start a simple HTTP server in a background thread. Returns (server, port)."""
    handler = http.server.SimpleHTTPRequestHandler
    server = http.server.HTTPServer(
        ("127.0.0.1", port),
        lambda *a, **kw: handler(*a, directory=directory, **kw),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


@pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="Playwright not installed")
class TestDashboardSkeleton(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._server, cls._port = _start_server(DOCS_DIR)
        cls._base_url = f"http://127.0.0.1:{cls._port}/dashboard.html"
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()
        cls._server.shutdown()

    def test_page_loads_without_js_errors(self):
        """dashboard.html should load without JS errors."""
        page = self._browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(self._base_url)
        page.wait_for_load_state("networkidle")

        self.assertIn("Mememage", page.title())
        self.assertEqual(errors, [], f"JS errors on load: {errors}")
        page.close()

    def test_three_tabs_render(self):
        """All three tab labels should be visible (CSS uppercases them)."""
        page = self._browser.new_page()
        page.goto(self._base_url)
        page.wait_for_load_state("networkidle")

        labels = [t.strip().upper() for t in page.locator(".input-tab").all_inner_texts()]
        self.assertEqual(labels, ["CONCEIVE", "PAYLOAD", "CONFIG"])
        page.close()

    def test_mint_tab_active_on_load(self):
        """First tab (Mint) should be active by default."""
        page = self._browser.new_page()
        page.goto(self._base_url)
        page.wait_for_load_state("networkidle")

        active_panel_id = page.locator(".input-panel.active").get_attribute("id")
        self.assertEqual(active_panel_id, "tab-mint")
        page.close()

    def test_tab_switching_toggles_active(self):
        """Clicking the Payload tab should activate its panel."""
        page = self._browser.new_page()
        page.goto(self._base_url)
        page.wait_for_load_state("networkidle")

        page.click('[data-panel="tab-payload"]')
        # Allow TabBar's class toggle to settle.
        page.wait_for_timeout(200)

        active_tab = page.locator(".input-tab.active").inner_text().strip().upper()
        self.assertEqual(active_tab, "PAYLOAD")

        active_panel_id = page.locator(".input-panel.active").get_attribute("id")
        self.assertEqual(active_panel_id, "tab-payload")
        page.close()

    def test_subtitle_picks_from_trove(self):
        """The subtitle should be one of the entries in Theme.taglines.dashboard."""
        page = self._browser.new_page()
        page.goto(self._base_url)
        page.wait_for_load_state("networkidle")

        sub_text = page.locator(".page-header .subtitle").inner_text().strip()
        # Confirm the rotator picked from the right trove.
        in_trove = page.evaluate(
            "(text) => Theme.taglines.dashboard.indexOf(text) !== -1",
            sub_text,
        )
        self.assertTrue(in_trove, f"Subtitle {sub_text!r} not in Theme.taglines.dashboard")
        page.close()

    def test_portal_links_to_decoder_and_validator(self):
        """Footer portal links exist for decoder + validator. The href is
        templated ({{DECODER_URL}}/{{VALIDATOR_URL}}) and substituted by
        the mint server per deployment shape (souls host vs local /decoder)
        — that wiring is covered in test_decoder_serving; here we just
        assert the two labelled portals are present."""
        page = self._browser.new_page()
        page.goto(self._base_url)
        page.wait_for_load_state("networkidle")

        texts = page.locator(".dashboard-portals a").evaluate_all(
            "els => els.map(el => el.textContent.trim())"
        )
        self.assertIn("Decoder", texts)
        self.assertIn("Validator", texts)
        page.close()

    def test_mint_panel_starts_in_empty_state(self):
        """Mint tab opens to the drop zone (data-mint-state=empty)."""
        page = self._browser.new_page()
        page.goto(self._base_url)
        page.wait_for_load_state("networkidle")

        state = page.locator(".mint-panel").get_attribute("data-mint-state")
        self.assertEqual(state, "empty")
        # The drop zone for [data-mint-only="empty"] should be visible.
        self.assertTrue(page.locator('#mintDrop').is_visible())
        page.close()

    def test_mint_review_block_hidden_until_upload(self):
        """Reviewing/minting/done blocks aren't visible in empty state."""
        page = self._browser.new_page()
        page.goto(self._base_url)
        page.wait_for_load_state("networkidle")

        for state_name in ("reviewing", "minting", "done", "failed"):
            sel = f'[data-mint-only="{state_name}"]'
            self.assertFalse(
                page.locator(sel).is_visible(),
                f"[data-mint-only={state_name}] should be hidden in empty state",
            )
        page.close()

    def test_config_tab_structure(self):
        """Config panel exposes seven sections: a closed-by-default
        Diagnostics (deployment preflight), Server, Profiles, Identity,
        Chains, Channels, plus a closed-by-default Channel cleanup (the
        pre-genesis maintenance surface). Credentials were folded into
        Channels (channel-specific secrets live next to their channel);
        MINT_API_TOKEN moved into Server.
        """
        page = self._browser.new_page()
        page.goto(self._base_url)
        page.wait_for_load_state("networkidle")

        page.click('[data-panel="tab-config"]')
        page.wait_for_timeout(150)

        for body_id in ("configChains", "configProfiles", "configIdentity",
                        "configServer", "configChannels", "configChannelCleanup"):
            self.assertEqual(
                page.locator(f"#{body_id}").count(), 1,
                f"#{body_id} should be present in Config tab",
            )

        details = page.locator(".config-section")
        self.assertEqual(details.count(), 7)
        # Four open by default (Server, Identity, Chains, Surfaces). Diagnostics,
        # Profiles (advanced multi-machine, single-profile here), and Channel
        # cleanup ship closed.
        open_count = page.locator(".config-section[open]").count()
        self.assertEqual(open_count, 4)
        page.close()

    def test_payload_tab_structure(self):
        """Payload panel exposes the editor: chain bar, action bar (refresh/
        build/save/discard/seal), validation slot, three section editors
        (entries/layers/pinned), and the age-status footer."""
        page = self._browser.new_page()
        page.goto(self._base_url)
        page.wait_for_load_state("networkidle")

        page.click('[data-panel="tab-payload"]')
        page.wait_for_timeout(150)

        # Action buttons present.
        for btn_id in ("payloadRefreshBtn", "payloadBuildBtn",
                       "payloadSavePresetBtn", "payloadApplyBtn",
                       "payloadDiscardBtn", "payloadSealBtn",
                       "addEntryBtn", "addLayerBtn", "addPinnedBtn"):
            self.assertTrue(
                page.locator(f"#{btn_id}").is_visible(),
                f"#{btn_id} should be visible in Payload tab",
            )
        # Chain bar + validation slot + editor containers + age status exist.
        for cid in ("payloadChainBanner", "payloadDirty",
                    "payloadValidation",
                    "payloadEntries", "payloadLayersEditor", "payloadPinnedEditor",
                    "entriesCount", "layersCount", "pinnedCount",
                    "payloadAgeStatus"):
            self.assertEqual(
                page.locator(f"#{cid}").count(), 1,
                f"#{cid} should be present",
            )

        # Apply-to-chain + Discard start disabled (nothing to apply /
        # nothing to revert until the user edits). Save preset stays
        # enabled — it's always available as a snapshot action.
        self.assertTrue(page.locator("#payloadApplyBtn").is_disabled())
        self.assertTrue(page.locator("#payloadDiscardBtn").is_disabled())
        self.assertFalse(page.locator("#payloadSavePresetBtn").is_disabled())

        # Dirty marker starts hidden.
        self.assertTrue(page.locator("#payloadDirty").is_hidden())
        page.close()

    def test_token_placeholder_not_literal(self):
        """When served by http.server (no substitution), the placeholder
        check defeats the literal {{MINT_API_TOKEN}} string so the page
        doesn't try to authenticate with that gibberish."""
        page = self._browser.new_page()
        page.goto(self._base_url)
        page.wait_for_load_state("networkidle")

        # window._MINT_API_TOKEN should be undefined (the indexOf guard
        # rejects the untouched placeholder).
        token = page.evaluate("() => window._MINT_API_TOKEN")
        self.assertIsNone(
            token,
            f"Expected no token in test env, got {token!r}. The indexOf guard "
            "must reject literal {{MINT_API_TOKEN}} when served by http.server.",
        )
        page.close()


class TestSessionSecrets(unittest.TestCase):
    """sessions.json must not leak raw GPS at rest: completed sessions are
    scrubbed of coordinates before persistence (already sealed into the soul
    by then), and the file is written owner-only."""

    def test_scrub_drops_completed_gps_keeps_pending(self):
        sessions = {
            "tokDone": {"status": "completed", "gps": {"lat": 45.5, "lon": -122.6},
                        "metadata": {"prompt": "x"}, "created": 1},
            "tokLive": {"status": "minting", "gps": {"lat": 1.0, "lon": 2.0},
                        "created": 2},
        }
        out = server._scrub_completed_gps(sessions)
        self.assertNotIn("gps", out["tokDone"])
        self.assertTrue(out["tokDone"]["gps_recorded"])
        self.assertNotIn("45.5", json.dumps(out["tokDone"]))
        self.assertEqual(out["tokLive"]["gps"], {"lat": 1.0, "lon": 2.0})
        # Original in-memory dict is not mutated.
        self.assertEqual(sessions["tokDone"]["gps"], {"lat": 45.5, "lon": -122.6})

    def test_save_sessions_owner_only_and_scrubbed(self):
        import stat as _stat
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "sessions.json")
            with patch.object(server, "SESSIONS_FILE", Path(f)), \
                 patch.object(server, "_sessions", {
                     "t": {"status": "completed", "gps": {"lat": 9.9, "lon": 8.8},
                           "created": 1}}):
                server._save_sessions()
                mode = os.stat(f).st_mode
                self.assertEqual(_stat.S_IMODE(mode) & 0o077, 0)
                body = open(f).read()
                self.assertNotIn("9.9", body)
                self.assertIn("gps_recorded", body)


class TestConceptionChainBinding(unittest.TestCase):
    """A pending conception is bound to the chain that was active when its
    session/ticket was created. Switching the active chain afterward must NOT
    redirect where that image conceives (regression: ticket 5EDBFDB7 minted
    into whatever chain was active at GPS-callback time)."""

    def test_session_creation_stamps_active_chain(self):
        # Both session-creating handlers must persist the active chain on the
        # session so the callback can resolve against it later.
        src = open(os.path.join(os.path.dirname(__file__), "..",
                                "mememage", "server.py")).read()
        # One shared _create_session helper stamps the active chain for
        # both the upload and programmatic paths.
        self.assertEqual(
            src.count('"chain": _chains_bind.current()'), 1,
            "_create_session must stamp the active chain onto the session")

    def test_callback_resolves_bound_chain_not_current(self):
        src = open(os.path.join(os.path.dirname(__file__), "..",
                                "mememage", "server.py")).read()
        # The callback must read the session's bound chain...
        self.assertIn('bound_chain = session.get("chain") or chains.current()', src)
        # ...and resolve chain metadata / gps / password against it, never
        # silently against chains.current() at callback time.
        self.assertIn("chain_info = chains.info(bound_chain)", src)
        self.assertIn("chains.get_gps_source(bound_chain)", src)
        self.assertIn("_held_password(bound_chain)", src)
        # And the mint runs with the bound chain active, restoring afterward.
        self.assertIn("chains.switch(bound_chain)", src)
        self.assertIn("chains.switch(_prev_chain)", src)


if __name__ == "__main__":
    unittest.main()
