"""Tests for the webhook payload shape + template substitution.

_notify_conceived assembles a context dict including the rich fields
(chain_id, constellation, rarity_tier, gps_source, distribution,
creator_name, key_fingerprint, etc.) that templates render via
{{key}} substitution. Pin both the assembly and the render.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mememage.server import _notify_conceived, _render_webhook_template


class _StubResult:
    """Minimal stand-in for mememage.mint.MintResult."""
    def __init__(self, identifier, content_hash, url, image_path, distribution):
        self.identifier = identifier
        self.content_hash = content_hash
        self.url = url
        self.image_path = image_path
        self.distribution = distribution


class TestWebhookTemplateRender(unittest.TestCase):
    """The {{key}} substitution + JSON-safe escaping."""

    def test_basic_substitution(self):
        tpl = '{"content": "Conceived {{identifier}}"}'
        out = _render_webhook_template(tpl, {"identifier": "mememage-abc123"})
        self.assertEqual(out, '{"content": "Conceived mememage-abc123"}')

    def test_unknown_keys_render_empty(self):
        # Templates referencing a key we didn't provide get an empty
        # substitution rather than a crash — webhook firing is
        # best-effort and shouldn't block conception.
        tpl = '{{missing_key}} after'
        out = _render_webhook_template(tpl, {"other": "x"})
        self.assertEqual(out, " after")

    def test_json_special_chars_escaped(self):
        # Quotes / newlines / backslashes in the value must be
        # JSON-escaped so the rendered string is valid inside a
        # JSON template's string context.
        tpl = '{"content": "{{name}}"}'
        out = _render_webhook_template(tpl, {"name": 'has "quote" and\nnewline'})
        # Body should parse as JSON and the content should round-trip.
        parsed = json.loads(out)
        self.assertEqual(parsed["content"], 'has "quote" and\nnewline')

    def test_whitespace_in_brace_tolerated(self):
        tpl = "{{ identifier }} and {{content_hash}}"
        out = _render_webhook_template(tpl, {
            "identifier": "id", "content_hash": "hash",
        })
        self.assertEqual(out, "id and hash")


class TestNotifyConceivedPayload(unittest.TestCase):
    """The payload dict passed to _fire_webhooks must include every
    rich-context field templates can reference."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mememage-webhook-test-")
        self.records_dir = Path(self.tmpdir) / "records"
        self.records_dir.mkdir(parents=True)

        # Synthetic soul on disk — _notify_conceived reads this to
        # build the rich context. Cover the fields the templates
        # surface.
        self.identifier = "mememage-test12345678"
        self.soul = {
            "identifier": self.identifier,
            "content_hash": "abcdef0123456789",
            "creator_name": "Test Creator",
            "key_fingerprint": "1234:5678:abcd:ef01",
            "constellation_name": "Anumel",
            "constellation_index": 0,  # records carry int; surface renders as α
            "rarity_score": 48,
            "chain_visibility": "light_energy",
        }
        soul_path = self.records_dir / f"{self.identifier}.soul"
        soul_path.write_text(json.dumps(self.soul), encoding="utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_payload_includes_rich_context_fields(self):
        result = _StubResult(
            identifier=self.identifier,
            content_hash="abcdef0123456789",
            url="https://example.com/soul",
            image_path="/tmp/image.png",
            distribution={"ia": "https://example.com/soul",
                          "Mirror": "https://peer.example/soul"},
        )

        # Capture the payload by patching _fire_webhooks.
        captured = {}

        def _capture(event, data):
            captured["event"] = event
            captured["data"] = data

        with patch("mememage.server._fire_webhooks", _capture), \
             patch("mememage.core.soul_store_dir",
                   return_value=self.records_dir), \
             patch("mememage.chains.current",
                   return_value="test_chain"), \
             patch("mememage.chains.get_gps_source",
                   return_value="phone"):
            _notify_conceived(result)

        self.assertEqual(captured["event"], "conceived")
        data = captured["data"]
        # Mandatory identity fields
        self.assertEqual(data["identifier"], self.identifier)
        self.assertEqual(data["content_hash"], "abcdef0123456789")
        # Rich context — every field a template can reference
        self.assertEqual(data["chain_id"], "test_chain")
        self.assertEqual(data["chain_visibility"], "light_energy")
        self.assertEqual(data["creator_name"], "Test Creator")
        self.assertEqual(data["key_fingerprint"], "1234:5678:abcd:ef01")
        self.assertEqual(data["constellation"], "Anumel")
        self.assertEqual(data["constellation_star"], "\u03b1")
        self.assertEqual(data["rarity_score"], 48)
        self.assertIn(data["rarity_tier"], ("Common", "Uncommon", "Rare",
                                            "Very Rare", "Epic", "Legendary"))
        self.assertEqual(data["gps_source"], "phone")
        # Distribution rendered as multiline label: url block
        self.assertIn("ia: https://example.com/soul", data["distribution"])
        self.assertIn("Mirror: https://peer.example/soul", data["distribution"])

    def test_distribution_falls_back_to_url_when_empty(self):
        # Pre-channels-framework records or single-channel mints with
        # no result.distribution should still render something in the
        # template — fall back to the canonical URL.
        result = _StubResult(
            identifier=self.identifier,
            content_hash="abcdef0123456789",
            url="https://canonical.example/soul",
            image_path="/tmp/image.png",
            distribution=None,
        )
        captured = {}

        def _capture(event, data):
            captured["data"] = data

        with patch("mememage.server._fire_webhooks", _capture), \
             patch("mememage.core.soul_store_dir",
                   return_value=self.records_dir), \
             patch("mememage.chains.current",
                   return_value="test_chain"), \
             patch("mememage.chains.get_gps_source",
                   return_value="none"):
            _notify_conceived(result)

        self.assertEqual(captured["data"]["distribution"],
                         "https://canonical.example/soul")

    def test_missing_soul_doesnt_crash(self):
        # Soul file missing on disk — payload still fires with the
        # rich-context fields blank. Webhook firing must never block
        # the mint pipeline.
        result = _StubResult(
            identifier="mememage-nosoulonfile",
            content_hash="abcdef0123456789",
            url="https://example/soul",
            image_path="/tmp/img.png",
            distribution={},
        )
        captured = {}

        def _capture(event, data):
            captured["data"] = data

        with patch("mememage.server._fire_webhooks", _capture), \
             patch("mememage.core.soul_store_dir",
                   return_value=self.records_dir), \
             patch("mememage.chains.current",
                   return_value="empty_chain"), \
             patch("mememage.chains.get_gps_source",
                   return_value="machine"):
            _notify_conceived(result)

        data = captured["data"]
        self.assertEqual(data["identifier"], "mememage-nosoulonfile")
        self.assertEqual(data["creator_name"], "")
        self.assertEqual(data["constellation"], "")
        self.assertEqual(data["chain_id"], "empty_chain")
        self.assertEqual(data["gps_source"], "machine")

    def test_template_renders_rich_context_field(self):
        # Full pipeline: payload through template → JSON-parseable
        # output containing the substituted value.
        result = _StubResult(
            identifier=self.identifier,
            content_hash="abcdef0123456789",
            url="https://example/soul",
            image_path="/tmp/img.png",
            distribution={"ia": "https://example/soul"},
        )
        captured = {}

        def _capture(event, data):
            # Render a Discord-shaped template against the same data
            # the webhooks framework would pass through.
            tpl = '{"content": "\u2728 {{creator_name}} conceived {{constellation}} {{constellation_star}} ({{rarity_tier}})"}'
            captured["rendered"] = _render_webhook_template(tpl, data)

        with patch("mememage.server._fire_webhooks", _capture), \
             patch("mememage.core.soul_store_dir",
                   return_value=self.records_dir), \
             patch("mememage.chains.current",
                   return_value="test_chain"), \
             patch("mememage.chains.get_gps_source",
                   return_value="phone"):
            _notify_conceived(result)

        parsed = json.loads(captured["rendered"])
        self.assertIn("Test Creator", parsed["content"])
        self.assertIn("Anumel", parsed["content"])
        self.assertIn("\u03b1", parsed["content"])


if __name__ == "__main__":
    unittest.main()
