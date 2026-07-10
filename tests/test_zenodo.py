"""Tests for Zenodo mirror integration."""

import json
import unittest
from unittest.mock import patch, MagicMock

from mememage.zenodo import upload_to_zenodo


def _mock_urlopen_responses(*responses):
    """Create a side_effect function that returns successive response bodies."""
    bodies = [json.dumps(r).encode("utf-8") for r in responses]
    return MagicMock(side_effect=bodies)


class TestUploadToZenodo(unittest.TestCase):
    @patch("mememage.zenodo.get_zenodo_config", return_value=(None, None))
    def test_returns_none_when_unconfigured(self, _):
        result = upload_to_zenodo("mememage-abc12345", {"prompt": "test"})
        assert result is None

    @patch("mememage.zenodo.urlopen_with_retry")
    @patch("mememage.zenodo.get_zenodo_config",
           return_value=("https://sandbox.zenodo.org", "fake-token"))
    def test_upload_returns_doi(self, _, mock_urlopen):
        mock_urlopen.side_effect = [
            # create deposition
            json.dumps({"id": 12345, "links": {"bucket": "https://sandbox.zenodo.org/api/files/bucket-id"}}).encode(),
            # upload file
            b"{}",
            # set metadata
            b"{}",
            # publish
            json.dumps({"doi": "10.5281/zenodo.12345"}).encode(),
        ]
        doi = upload_to_zenodo("mememage-abc12345", {"prompt": "test", "seed": 42})
        assert doi == "10.5281/zenodo.12345"
        assert mock_urlopen.call_count == 4

    @patch("mememage.zenodo.urlopen_with_retry")
    @patch("mememage.zenodo.get_zenodo_config",
           return_value=("https://zenodo.org", "real-token"))
    def test_uses_production_url(self, mock_config, mock_urlopen):
        mock_urlopen.side_effect = [
            json.dumps({"id": 1, "links": {"bucket": "https://zenodo.org/api/files/b"}}).encode(),
            b"{}",
            b"{}",
            json.dumps({"doi": "10.5281/zenodo.1"}).encode(),
        ]
        upload_to_zenodo("mememage-test1234", {"prompt": "test"})
        # First call should target production
        first_call_url = mock_urlopen.call_args_list[0][0][0].full_url
        assert "zenodo.org" in first_call_url
        assert "sandbox" not in first_call_url

    @patch("mememage.zenodo.urlopen_with_retry")
    @patch("mememage.zenodo.get_zenodo_config",
           return_value=("https://sandbox.zenodo.org", "fake-token"))
    def test_create_deposition_failure_raises(self, _, mock_urlopen):
        mock_urlopen.side_effect = RuntimeError("HTTP 400")
        with self.assertRaises(RuntimeError):
            upload_to_zenodo("mememage-abc12345", {"prompt": "test"})

    @patch("mememage.zenodo.urlopen_with_retry")
    @patch("mememage.zenodo.get_zenodo_config",
           return_value=("https://sandbox.zenodo.org", "fake-token"))
    def test_identifier_in_metadata(self, _, mock_urlopen):
        """The deposition metadata should reference the mememage identifier."""
        mock_urlopen.side_effect = [
            json.dumps({"id": 1, "links": {"bucket": "https://x/b"}}).encode(),
            b"{}",
            b"{}",
            json.dumps({"doi": "10.5281/zenodo.1"}).encode(),
        ]
        upload_to_zenodo("mememage-abc12345", {"prompt": "test"})
        # Third call is set_metadata (PUT)
        meta_call = mock_urlopen.call_args_list[2][0][0]
        body = json.loads(meta_call.data.decode("utf-8"))
        assert "mememage-abc12345" in body["metadata"]["title"]
        assert "mememage-abc12345" in body["metadata"]["keywords"]


class TestZenodoNonFatal(unittest.TestCase):
    """Verify that one channel's failure doesn't break the mint when
    another channel succeeds. With the channels framework, this is
    the at-least-one-succeeds contract of ``channels.blast()``."""

    @patch.dict("os.environ", {"IA_ACCESS_KEY": "k", "IA_SECRET_KEY": "s",
                                "ZENODO_ACCESS_TOKEN": "z"})
    @patch("mememage.core._save_local_backup")
    @patch("mememage.core._identifier_taken", return_value=False)
    @patch("mememage.core.compute_birth_certificate", return_value={})
    @patch("mememage.core.get_parent_id", return_value=None)
    @patch("mememage.core.compute_rarity", return_value={"score": 10, "celestial": [], "machine": [], "entropy": [], "sigil": None})
    @patch("mememage.core.compute_machine_fingerprint", return_value="abc123")
    @patch("mememage.core.read_birth_temperament", return_value={
        "temperament": "calm", "traits": [], "trait_codes": [], "readings": {}, "summary": "ok"
    })
    @patch("mememage.core.get_current_chunk", return_value=None)
    @patch("mememage.core.set_parent_id")
    @patch("mememage.core.advance_chunk_index")
    # IA succeeds, Zenodo fails — mint should still succeed.
    @patch("mememage.zenodo.upload_to_zenodo", side_effect=RuntimeError("Zenodo down"))
    @patch("mememage.channels.internet_archive.urlopen_with_retry", return_value=b"")
    def test_upload_succeeds_despite_one_channel_failure(self, mock_ia, mock_zen, *mocks):
        # Force-enable both channels for this test so Zenodo actually
        # runs and can fail. Default channels.json has Zenodo disabled.
        from mememage import channels as _channels
        original_load = _channels.load_channels

        def both_enabled():
            chs = original_load()
            for c in chs:
                if c.TYPE == "zenodo":
                    c.enabled = True
            return chs

        with patch.object(_channels, "load_channels", side_effect=both_enabled):
            from mememage.core import upload_metadata
            metadata = {"prompt": "test", "seed": 42, "width": 512, "height": 512}
            identifier, content_hash, _dist, _url = upload_metadata(metadata, gps=(34.0, -118.0))
            assert identifier.startswith("mememage-")
            assert len(content_hash) == 16


if __name__ == "__main__":
    unittest.main()
