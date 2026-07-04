"""Payload filename preservation: a single-file payload layer records the source
filename at seal time, carries it on every emitted chunk, and the validator
restores it on reassembly (so "upload a .wav, get a .wav back" instead of
layer_1.bin). The name rides outside the hashed set — chunks_root covers the
chunk DATA hashes, not metadata — so this is hash-neutral.
"""

import unittest
from unittest.mock import patch

import mememage.site_pack as sp
import mememage.site_embed as se


class _Entry:
    def __init__(self, sources, name="voice"):
        self.sources = sources
        self.name = name


class SourceFilename(unittest.TestCase):
    def test_single_file(self):
        self.assertEqual(
            sp._source_filename(_Entry(["/home/x/.mememage/.../UGC1_vocals.wav"])),
            "UGC1_vocals.wav")

    def test_multi_source_is_none(self):
        self.assertIsNone(sp._source_filename(_Entry(["/a/x.txt", "/a/y.txt"])))

    def test_empty_is_none(self):
        self.assertIsNone(sp._source_filename(_Entry([])))
        self.assertIsNone(sp._source_filename(_Entry([""])))

    def test_trailing_slash_dir(self):
        # A directory-ish source resolves to its basename (harmless — built-in
        # roles ignore it, custom file layers are the case that matters).
        self.assertEqual(sp._source_filename(_Entry(["/a/voice/"])), "voice")


class ChunkCarriesFilename(unittest.TestCase):
    def _emit(self, layer_data, outer=0):
        seal = {"outer_cycle": 12, "layer_chunks": {"layer_1": layer_data}}
        with patch.object(se, "_load_seal", return_value=seal), \
             patch.object(se, "_load_chunk_state", return_value={"outer_position": outer}):
            return se.get_current_chunk()

    def test_filename_on_emitted_chunk(self):
        out = self._emit({"K": 3, "reserved": 0, "version": "g1",
                          "chunks": ["AAA", "BBB", "CCC"], "filename": "UGC1_vocals.wav"})
        ch = out["chunks"]["layer_1"]
        self.assertEqual(ch["filename"], "UGC1_vocals.wav")
        self.assertEqual(ch["index"], 0)
        self.assertEqual(ch["data"], "AAA")

    def test_no_filename_field_when_seal_lacks_it(self):
        out = self._emit({"K": 3, "reserved": 0,
                          "chunks": ["AAA", "BBB", "CCC"]}, outer=2)
        ch = out["chunks"]["layer_1"]
        self.assertNotIn("filename", ch)   # back-compat: older seals
        self.assertEqual(ch["index"], 2)


class SealCarriesFilename(unittest.TestCase):
    """END-TO-END: a full seal() must write the filename into sealed_chunks.json.
    The earlier tests checked _chunk_layer in isolation and passed while the real
    seal dropped the name in its layer_chunks reassembly — this closes that gap.
    """

    def test_seal_writes_filename_into_layer_chunks(self):
        import json
        import tempfile
        from pathlib import Path
        from mememage.chain_config import ChainConfig, Layer, Entry
        from mememage.site_pack import seal, DOCS_DIR

        tmp = tempfile.mkdtemp()
        seal_path = Path(tmp) / "sealed_chunks.json"
        state_path = Path(tmp) / "chunk_state.json"
        cfg = ChainConfig.blank("ftest", "F Test")
        cfg.entries["voice"] = Entry.from_dict("voice", {"sources": ["/x/y/UGC1_vocals.wav"]})
        cfg.layers.append(Layer.from_dict({"name": "secret", "K": 3, "entry": "voice"}))
        with patch("mememage.site_embed.seal_file", return_value=seal_path), \
             patch("mememage.site_pack._load_seal", return_value=None), \
             patch("mememage.site_embed.chunk_state_file", return_value=state_path), \
             patch("mememage.chain_config.load", return_value=cfg), \
             patch("mememage.payload.require_ready"), \
             patch("mememage.payload.get_artifact_bytes", return_value=b"x" * 64):
            seal(DOCS_DIR)
        seal_data = json.loads(seal_path.read_text())
        self.assertEqual(
            seal_data["layer_chunks"]["secret"]["filename"], "UGC1_vocals.wav")

    def test_seal_layer_entry_omits_filename_when_absent(self):
        class _L:
            K, reserved, name = 3, 0, "decoder"
        entry = sp._seal_layer_entry(_L(), {"version": "g", "hash": "h", "chunks": ["a"]})
        self.assertNotIn("filename", entry)


class SealDrift(unittest.TestCase):
    """Stale-seal guard: detect when the payload changed since the seal so the
    dashboard can warn before someone conceives the old payload again.
    """

    class _E:
        def __init__(self, n): self.name = n

    class _L:
        def __init__(self, n, e): self.name = n; self.entry = e

    class _Cfg:
        def __init__(self, lys): self.layers = lys
        def entry(self, n): return SealDrift._E(n)

    def _cfg(self, pairs):
        return self._Cfg([self._L(n, e) for n, e in pairs])

    def test_no_drift_when_hash_matches(self):
        _, _, h = sp._encode_entry(b"payload-bytes")
        with patch.object(sp, "_load_seal", return_value={"layer_chunks": {"secret": {"hash": h}}}), \
             patch("mememage.chain_config.load", return_value=self._cfg([("secret", "voice")])), \
             patch("mememage.payload.get_artifact_bytes", return_value=b"payload-bytes"):
            self.assertEqual(sp.seal_drift(), [])

    def test_drift_when_content_changed(self):
        _, _, h = sp._encode_entry(b"OLD")
        with patch.object(sp, "_load_seal", return_value={"layer_chunks": {"secret": {"hash": h}}}), \
             patch("mememage.chain_config.load", return_value=self._cfg([("secret", "voice")])), \
             patch("mememage.payload.get_artifact_bytes", return_value=b"NEW-DIFFERENT"):
            self.assertEqual(sp.seal_drift(), ["secret"])

    def test_drift_when_layer_added(self):
        with patch.object(sp, "_load_seal", return_value={"layer_chunks": {}}), \
             patch("mememage.chain_config.load", return_value=self._cfg([("secret", "voice")])), \
             patch("mememage.payload.get_artifact_bytes", return_value=b"x"):
            self.assertEqual(sp.seal_drift(), ["secret"])

    def test_unsealed_chain_is_never_stale(self):
        with patch.object(sp, "_load_seal", return_value=None):
            self.assertEqual(sp.seal_drift(), [])


class EncodeDeterminism(unittest.TestCase):
    """_encode_entry must be byte-for-byte deterministic (gzip mtime=0), else
    seal_drift can never match a sealed hash and seals are irreproducible.
    """

    def test_gzip_mtime_header_is_zeroed(self):
        import base64
        gz = base64.b64decode(sp._encode_entry(b"x")[0])
        # gzip header bytes 4-8 are the mtime (little-endian). Must be zero.
        self.assertEqual(gz[4:8], b"\x00\x00\x00\x00")

    def test_same_bytes_encode_identically(self):
        self.assertEqual(sp._encode_entry(b"same payload"), sp._encode_entry(b"same payload"))


if __name__ == "__main__":
    unittest.main()
