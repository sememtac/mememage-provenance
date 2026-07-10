"""Tests for mememage.mint — mint orchestrator."""

import sys
import tempfile
import unittest
from unittest.mock import patch, ANY

from PIL import Image

from mememage.bar import extract_bar
from mememage.mint import MintResult, mint

# `mememage/__init__.py` does `from mememage.mint import mint`, so the name
# `mememage.mint` on the PACKAGE resolves to the FUNCTION, shadowing the
# submodule. mock's dotted-string resolution walks that attribute chain, and
# how it recovers changed after 3.11 — so `patch("mememage.mint.upload_metadata")`
# works on 3.12+ and raises `AttributeError: <function mint> does not have the
# attribute 'upload_metadata'` on 3.10/3.11 (it had CI red on both). Patch the
# module object straight out of sys.modules instead: unambiguous everywhere.
_MINT_MODULE = sys.modules["mememage.mint"]


def _make_test_image(width=1024, height=768):
    img = Image.new('RGB', (width, height), (100, 130, 160))
    f = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    img.save(f.name)
    f.close()
    return f.name


class TestMint(unittest.TestCase):
    @patch.object(_MINT_MODULE, "upload_metadata", return_value=("mememage-0001000000000001", "aabbccdd11223344", None, None))
    def test_mint_returns_result(self, _upload):
        path = _make_test_image()
        meta = {"prompt": "test", "seed": 1, "width": 1024, "height": 768}

        result = mint(meta, gps=(37.7, -122.4), image_path=path)

        self.assertIsInstance(result, MintResult)
        self.assertEqual(result.identifier, "mememage-0001000000000001")
        self.assertEqual(result.content_hash, "aabbccdd11223344")
        self.assertIn("mememage-0001000000000001", result.url)
        self.assertIn(".soul", result.url)
        self.assertEqual(result.image_path, path)

    @patch.object(_MINT_MODULE, "upload_metadata")
    def test_mint_encodes_bar(self, _upload):
        """Minted image should have a readable bar.

        The bar is embedded by the prepare_image hook mint() hands to
        upload_metadata (transactional: bar embedded just before the blast,
        not after). The stub invokes that hook so the test exercises the
        real embed path.
        """
        ident, chash = "mememage-0002000000000002", "1234567890abcdef"

        def fake_upload(metadata, gps, image_path,
                        password=None, chain_visibility=None, prepare_image=None):
            if prepare_image is not None:
                prepare_image(ident, chash)
            return (ident, chash, None, None)
        _upload.side_effect = fake_upload

        path = _make_test_image()
        meta = {"prompt": "bar test", "seed": 42, "width": 1024, "height": 768}

        result = mint(meta, gps=(40.7, -74.0), image_path=path)

        bar = extract_bar(path)
        self.assertIsNotNone(bar)
        self.assertEqual(bar[0], result.identifier)
        self.assertEqual(bar[1], result.content_hash)

    @patch.object(_MINT_MODULE, "upload_metadata", return_value=("mememage-0003000000000003", "ffff0000aaaa1111", None, None))
    def test_mint_passes_gps_to_upload(self, mock_upload):
        # Isolate from any password the user's real chain config /
        # .env might supply — mint() now routes through
        # chains.resolve_password which checks chain.json AND env.
        # Mocking the resolver to return None gives us a clean
        # baseline for the gps-only assertion.
        with patch("mememage.chains.resolve_password", return_value=None):
            path = _make_test_image()
            meta = {"prompt": "gps test", "seed": 99, "width": 512, "height": 512}

            mint(meta, gps=(51.5, -0.1), image_path=path)

            mock_upload.assert_called_once_with(
                meta, (51.5, -0.1), path,
                password=None, chain_visibility=None, prepare_image=ANY,
            )

    def test_mint_accepts_no_gps(self):
        """gps=None is a first-class mode (chain's gps_source: none).

        The mint pipeline must complete without coordinates; the
        record carries no ``gps_time_locked`` field and the cert renders
        a visible "BIRTHPLACE — NOT RECORDED" placeholder.
        """
        path = _make_test_image()
        meta = {"prompt": "no gps", "seed": 1, "width": 512, "height": 512}

        with patch("mememage.chains.resolve_password", return_value=None), \
             patch.object(_MINT_MODULE, "upload_metadata") as mock_upload:
            mock_upload.return_value = ("mememage-abc123", "deadbeefcafe1234", None, None)
            mint(meta, gps=None, image_path=path)
            mock_upload.assert_called_once_with(
                meta, None, path,
                password=None, chain_visibility=None, prepare_image=ANY,
            )


class TestPatchRecord(unittest.TestCase):
    """Regression tests for _patch_record's channels-aware path.

    The thumbnail-injection bug (2026-05-18) slipped through because
    _patch_record used a direct IA call. When IA was disabled in
    channels.json, the patch threw before saving locally, so the
    thumbnail was lost. These tests pin the invariants:

      1. Local backup is always saved (even if every channel fails)
      2. Patch is reblasted through channels.blast, never via a
         direct IA call
      3. Channel failures don't propagate — the patch is still
         considered successful at the local-backup level
    """

    def test_patch_saves_local_backup_even_when_all_channels_fail(self):
        import json, os
        from mememage.mint import _patch_record

        with tempfile.TemporaryDirectory() as tmp:
            ident = "mememage-30931c7a19351b0e"
            # Seed a fake records dir with a pre-existing soul
            records_dir = tempfile.mkdtemp(prefix="rec-")
            soul_path = os.path.join(records_dir, f"{ident}.soul")
            with open(soul_path, "w") as f:
                json.dump({"identifier": ident, "content_hash": "deadbeef"}, f)

            # Mock chains.path to return our temp records dir
            from pathlib import Path
            with patch.object(_MINT_MODULE, "soul_store_dir", return_value=Path(records_dir)), \
                 patch("mememage.core.soul_store_dir", return_value=Path(records_dir)), \
                 patch("mememage.channels.load_channels", return_value=[]), \
                 patch("mememage.channels.blast",
                       side_effect=__import__("mememage.channels", fromlist=["x"]).ChannelUploadError("no channels")):
                _patch_record(ident, "deadbeef", {"thumbnail": "BASE64DATA"})

            # Local backup must carry the thumbnail despite blast failing
            with open(soul_path) as f:
                patched = json.load(f)
            self.assertEqual(patched.get("thumbnail"), "BASE64DATA")

    def test_patch_never_calls_get_ia_keys_directly(self):
        """Regression pin: _patch_record must NOT use get_ia_keys
        directly — it would re-introduce the channel-bypass bug.
        Channels handle their own auth via the registered classes."""
        import json, os
        from mememage.mint import _patch_record

        with tempfile.TemporaryDirectory() as tmp:
            ident = "mememage-8074b20fb47e1d1d"
            records_dir = tempfile.mkdtemp(prefix="rec-")
            soul_path = os.path.join(records_dir, f"{ident}.soul")
            with open(soul_path, "w") as f:
                json.dump({"identifier": ident}, f)

            from pathlib import Path
            with patch.object(_MINT_MODULE, "soul_store_dir", return_value=Path(records_dir)), \
                 patch("mememage.core.soul_store_dir", return_value=Path(records_dir)), \
                 patch("mememage.channels.load_channels", return_value=[]), \
                 patch("mememage.channels.blast", return_value={}) as mock_blast, \
                 patch("mememage.config.get_ia_keys") as mock_ia:
                _patch_record(ident, None, {"thumbnail": "X"})

            # Channels path was taken; IA-direct path was NOT
            self.assertTrue(mock_blast.called, "_patch_record must call channels.blast")
            self.assertFalse(mock_ia.called, "_patch_record must NOT call get_ia_keys directly")


class TestTransactionalMint(unittest.TestCase):
    """No image, no record.

    Regression for the partial-mint orphan (dark-70bcca0baf3cf5b1): the
    record used to be published BEFORE the bar was embedded, so a failed
    embed_bar (e.g. a non-PNG image) left a thumbnail-less, unsigned soul
    on the surface and advanced the chain's parent pointer. The transactional
    prepare_image hook runs the embed immediately before the blast; if it
    raises, nothing is published and none of the post-blast commitments
    (blast / local backup / heart star / chunk advance / parent id) run.
    """

    def test_prepare_image_failure_publishes_nothing(self):
        from mememage import core

        state = core.ConceptionState(metadata={}, gps=None)
        state.identifier = "dark-deadbeefdeadbeef"
        state.content_hash = "aabbccddeeff0011"
        state.record = {
            "identifier": state.identifier,
            "content_hash": state.content_hash,
        }
        state.image_path = "/tmp/not-a-real.png"

        def boom(identifier, content_hash):
            raise ValueError("Bar encoding requires PNG format")

        with patch("mememage.channels.load_channels", return_value=[]), \
             patch("mememage.channels.blast") as mock_blast, \
             patch("mememage.core._save_local_backup") as mock_backup, \
             patch("mememage.core.set_parent_id") as mock_parent, \
             patch("mememage.core.advance_chunk_index") as mock_advance, \
             patch("mememage.core.set_heart_star") as mock_heart:
            with self.assertRaises(ValueError):
                core._step_upload(state, prepare_image=boom)

            mock_blast.assert_not_called()
            mock_backup.assert_not_called()
            mock_parent.assert_not_called()
            mock_advance.assert_not_called()
            mock_heart.assert_not_called()

    def test_prepare_image_patch_rides_the_first_blast(self):
        """The thumbnail + signature land in the FIRST (and only) blast —
        no separate post-publish reblast."""
        from mememage import core

        state = core.ConceptionState(metadata={}, gps=None)
        state.identifier = "dark-deadbeefdeadbeef"
        state.content_hash = "aabbccddeeff0011"
        state.record = {
            "identifier": state.identifier,
            "content_hash": state.content_hash,
        }
        state.image_path = "/tmp/whatever.png"

        def prep(identifier, content_hash):
            return {"thumbnail": "THUMBDATA", "signature": "SIGHEX"}

        captured = {}

        def fake_blast(channels, identifier, payload, **kwargs):
            captured["payload"] = payload
            return {}

        with patch("mememage.channels.load_channels", return_value=[]), \
             patch("mememage.channels.blast", side_effect=fake_blast) as mock_blast, \
             patch("mememage.core._save_local_backup"), \
             patch("mememage.core.set_parent_id"), \
             patch("mememage.core.advance_chunk_index"), \
             patch("mememage.core.set_heart_star"):
            core._step_upload(state, prepare_image=prep)

            self.assertEqual(mock_blast.call_count, 1)
            body = captured["payload"].decode("utf-8")
            self.assertIn("THUMBDATA", body)
            self.assertIn("SIGHEX", body)
            # The record itself carries the patch (single-upload, no reblast).
            self.assertEqual(state.record.get("thumbnail"), "THUMBDATA")
            self.assertEqual(state.record.get("signature"), "SIGHEX")


if __name__ == "__main__":
    unittest.main()
