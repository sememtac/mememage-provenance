"""Tests for mememage core functions."""

import hashlib
import json
import unittest
from io import BytesIO
from unittest.mock import patch, MagicMock

from mememage.core import (
    compute_content_hash,
    compute_identifier,
    fetch_metadata,
    upload_metadata,
    verify_metadata,
)


class TestComputeIdentifier(unittest.TestCase):
    def test_deterministic_with_same_timestamp(self):
        meta = {"prompt": "a red canyon", "seed": 12345, "width": 768, "height": 1344}
        ts = "2026-04-03T12:00:00Z"
        id1 = compute_identifier(meta, timestamp=ts)
        id2 = compute_identifier(meta, timestamp=ts)
        assert id1 == id2

    def test_different_timestamp_different_id(self):
        meta = {"prompt": "a red canyon", "seed": 12345, "width": 768, "height": 1344}
        id1 = compute_identifier(meta, timestamp="2026-04-03T12:00:00Z")
        id2 = compute_identifier(meta, timestamp="2026-04-03T12:00:01Z")
        assert id1 != id2

    def test_hash_correctness(self):
        meta = {"prompt": "hello", "seed": 42, "width": 768, "height": 1344}
        ts = "2026-04-03T12:00:00Z"
        expected_hash = hashlib.sha256(f"hello427681344{ts}".encode()).hexdigest()[:16]
        assert compute_identifier(meta, timestamp=ts) == f"mememage-{expected_hash}"

    def test_different_inputs_different_ids(self):
        meta1 = {"prompt": "cat", "seed": 1, "width": 512, "height": 512}
        meta2 = {"prompt": "dog", "seed": 1, "width": 512, "height": 512}
        ts = "2026-01-01T00:00:00Z"
        assert compute_identifier(meta1, timestamp=ts) != compute_identifier(meta2, timestamp=ts)

    def test_missing_fields_dont_crash(self):
        identifier = compute_identifier({}, timestamp="2026-01-01T00:00:00Z")
        assert identifier.startswith("mememage-")


class TestUploadMetadata(unittest.TestCase):
    # IA upload moved into mememage.channels.internet_archive after the
    # channels-framework refactor. We patch the channel's network call
    # + drive credentials via env vars (the same path the live channel
    # uses).
    @patch.dict("os.environ", {"IA_ACCESS_KEY": "test_access", "IA_SECRET_KEY": "test_secret"})
    @patch("mememage.core.advance_chunk_index")
    @patch("mememage.core.get_current_chunk", return_value=None)
    @patch("mememage.core.set_parent_id")
    @patch("mememage.core.get_parent_id", return_value=None)
    @patch("mememage.core._identifier_taken", return_value=False)
    @patch("mememage.core.compute_birth_certificate", return_value={"sun": "Aries 10°"})
    @patch("mememage.channels.internet_archive.urlopen_with_retry", return_value=b"")
    def test_upload_returns_identifier_and_hash(self, _urlopen, _b, _exists, _gp, _sp, _gc, _ac):
        # IA-only channel set + no store write, isolated from the network.
        from mememage.channels.internet_archive import InternetArchiveChannel
        ia = InternetArchiveChannel({"id": "ia", "type": "internet_archive",
                                     "enabled": True, "primary": True})
        meta = {"prompt": "a red canyon", "seed": 12345, "width": 768, "height": 1344}
        with patch("mememage.channels.load_channels", return_value=[ia]), \
             patch("mememage.core._save_local_backup", return_value=None):
            identifier, content_hash, _dist, _url = upload_metadata(meta, gps=(37.7, -122.4))
        assert len(content_hash) == 16
        assert all(c in "0123456789abcdef" for c in content_hash)

    @patch.dict("os.environ", {"IA_ACCESS_KEY": "test_access", "IA_SECRET_KEY": "test_secret"})
    @patch("mememage.core.advance_chunk_index")
    @patch("mememage.core.get_current_chunk", return_value=None)
    @patch("mememage.core.set_parent_id")
    @patch("mememage.core.get_parent_id", return_value=None)
    @patch("mememage.core._identifier_taken", return_value=False)
    @patch("mememage.core.compute_birth_certificate", return_value={"sun": "Aries 10°"})
    @patch("mememage.channels.internet_archive.urlopen_with_retry", return_value=b"")
    def test_upload_sends_correct_url(self, mock_urlopen, _b, _exists, _gp, _sp, _gc, _ac):
        # Isolate to an IA-only channel set: the self-push surface no longer
        # makes an HTTP call (it writes the local store directly), so drive the
        # blast through IA — whose urlopen we mock — and skip the store write.
        from mememage.channels.internet_archive import InternetArchiveChannel
        ia = InternetArchiveChannel({"id": "ia", "type": "internet_archive",
                                     "enabled": True, "primary": True})
        meta = {"prompt": "test", "seed": 1, "width": 512, "height": 512}
        with patch("mememage.channels.load_channels", return_value=[ia]), \
             patch("mememage.core._save_local_backup", return_value=None):
            identifier, _hash, _dist, _url = upload_metadata(meta, gps=(40.7, -74.0))

        # mock_urlopen is called once for the .soul PUT and once for
        # the .json mirror. Check the first call carried the right
        # auth + identifier.
        req = mock_urlopen.call_args_list[0][0][0]
        assert identifier in req.full_url
        assert req.get_method() == "PUT"
        assert "LOW test_access:test_secret" in req.get_header("Authorization")


class TestFetchMetadata(unittest.TestCase):
    @patch(
        "mememage.core.fetch_json",
        return_value={"identifier": "mememage-abc12345", "prompt": "hello", "seed": 42},
    )
    def test_fetch_returns_dict(self, _mock):
        result = fetch_metadata("mememage-abc12345")
        assert result["prompt"] == "hello"
        assert result["seed"] == 42
        # No content_hash → _verified should be None (legacy)
        assert result["_verified"] is None

    @patch("mememage.core.fetch_json", return_value=None)
    def test_fetch_returns_none_on_404(self, _mock):
        result = fetch_metadata("mememage-nonexist")
        assert result is None


class TestContentHash(unittest.TestCase):
    def test_excludes_content_hash_field(self):
        """Adding content_hash to a record should not change the hash."""
        record = {"prompt": "test", "seed": 42}
        h1 = compute_content_hash(record)
        record["content_hash"] = h1
        h2 = compute_content_hash(record)
        assert h1 == h2

    def test_excludes_about_field(self):
        """Adding _about to a record should not change the hash."""
        record = {"prompt": "test", "seed": 42}
        h1 = compute_content_hash(record)
        record["_about"] = "Mememage format explanation"
        h2 = compute_content_hash(record)
        assert h1 == h2

    def test_different_records_different_hashes(self):
        # V1: gen params live under `origin`. Different prompts in
        # origin → different hashes.
        r1 = {"origin": {"prompt": "cat", "seed": 1}}
        r2 = {"origin": {"prompt": "dog", "seed": 1}}
        assert compute_content_hash(r1) != compute_content_hash(r2)

    def test_sensitive_to_any_field_change(self):
        # rarity (the dict) is in the V1 inclusion set; rarity_score
        # is derived and no longer hashed. Tampering the dice itself
        # is what breaks the hash.
        record = {"prompt": "test", "seed": 42, "rarity": {"celestial": [{"trait": "x", "points": 5}]}}
        h1 = compute_content_hash(record)
        record["rarity"]["celestial"][0]["points"] = 999
        h2 = compute_content_hash(record)
        assert h1 != h2


class TestVerifyMetadata(unittest.TestCase):
    def test_verified_when_hash_matches(self):
        record = {"prompt": "test", "seed": 42}
        record["content_hash"] = compute_content_hash(record)
        assert verify_metadata(record) is True

    def test_tampered_when_hash_mismatches(self):
        record = {"prompt": "test", "seed": 42}
        record["content_hash"] = "0000000000000000"
        assert verify_metadata(record) is False

    def test_none_when_no_hash(self):
        record = {"prompt": "test", "seed": 42}
        assert verify_metadata(record) is None

    def test_tampered_after_field_modification(self):
        record = {"prompt": "test", "seed": 42, "rarity": {"celestial": [{"trait": "x", "points": 5}]}}
        record["content_hash"] = compute_content_hash(record)
        # Simulate tampering — modify the rarity dice (in hash)
        record["rarity"]["celestial"][0]["points"] = 999
        assert verify_metadata(record) is False

    @patch("mememage.core.fetch_json")
    def test_fetch_sets_verified_true(self, mock_fetch):
        # identifier is in _HASH_INCLUDED so the test record needs one
        # to match the content_hash after compute_content_hash. Mirrors
        # what real records look like.
        record = {"identifier": "mememage-test1234", "prompt": "test", "seed": 42}
        record["content_hash"] = compute_content_hash(record)
        mock_fetch.return_value = record
        result = fetch_metadata("mememage-test1234")
        assert result["_verified"] is True

    @patch("mememage.core.fetch_json")
    def test_fetch_sets_verified_false_on_tamper(self, mock_fetch):
        record = {"identifier": "mememage-test1234", "prompt": "test", "seed": 42}
        record["content_hash"] = "0000000000000000"
        mock_fetch.return_value = record
        result = fetch_metadata("mememage-test1234")
        assert result["_verified"] is False


class TestIdentifierExistsTombstone(unittest.TestCase):
    """The pre-flight collision check must catch darkened items.

    Internet Archive holds the namespace for items that were created
    and then darkened (user-deleted, admin-blocked). Those items
    return 404 on the download URL — masking the tombstone — but the
    metadata API reports ``{"is_dark": true, ...}`` and IA refuses
    future PUTs to the slot with 403. Probing the metadata API here
    lets _unique_identifier regenerate with extra entropy BEFORE
    encryption + chunk binding run.
    """

    def _http_error(self, code):
        import urllib.error
        return urllib.error.HTTPError(
            url="x", code=code, msg="", hdrs={}, fp=None,
        )

    def _mock_metadata_body(self, body_bytes):
        """Return a context-manager-like mock matching urlopen's API."""
        from unittest.mock import MagicMock
        m = MagicMock()
        m.__enter__.return_value.read.return_value = body_bytes
        m.__exit__.return_value = False
        return m

    def test_darkened_treated_as_taken(self):
        # The actual case that bit us: darkened items return {"is_dark": true}
        # on the metadata API even though the download URL gives 404.
        from mememage.core import _identifier_exists
        body = b'{"is_dark": true, "created": 1780085640, "dir": "/22/items/x"}'
        with patch("mememage.core.urllib.request.urlopen",
                   return_value=self._mock_metadata_body(body)):
            self.assertTrue(_identifier_exists("mememage-darkened1"))

    def test_alive_is_taken(self):
        from mememage.core import _identifier_exists
        body = b'{"created": 1780085640, "files": [{"name": "x.json"}]}'
        with patch("mememage.core.urllib.request.urlopen",
                   return_value=self._mock_metadata_body(body)):
            self.assertTrue(_identifier_exists("mememage-alive00001"))

    def test_empty_metadata_means_available(self):
        # Never-existed identifiers return {} from the metadata endpoint.
        from mememage.core import _identifier_exists
        with patch("mememage.core.urllib.request.urlopen",
                   return_value=self._mock_metadata_body(b"{}")):
            self.assertFalse(_identifier_exists("mememage-freeident1"))

    def test_404_treated_as_available(self):
        # Defensive: if IA ever changes the metadata endpoint to 404
        # instead of returning {}, we still treat it as free.
        from mememage.core import _identifier_exists
        with patch("mememage.core.urllib.request.urlopen",
                   side_effect=self._http_error(404)):
            self.assertFalse(_identifier_exists("mememage-freeident2"))

    def test_unparseable_body_fails_closed(self):
        # If we can't read the response, treat as taken rather than
        # PUT into something we can't reason about.
        from mememage.core import _identifier_exists
        with patch("mememage.core.urllib.request.urlopen",
                   return_value=self._mock_metadata_body(b"<html>nope</html>")):
            self.assertTrue(_identifier_exists("mememage-garbage1"))

    def test_other_http_codes_propagate(self):
        import urllib.error
        from mememage.core import _identifier_exists
        with patch("mememage.core.urllib.request.urlopen",
                   side_effect=self._http_error(500)):
            with self.assertRaises(urllib.error.HTTPError):
                _identifier_exists("mememage-server-err")


class TestGenesisRoll(unittest.TestCase):
    """Genesis (parent_id null) rolls a random <prefix>-<16 hex> and
    probes it across enabled surfaces, re-rolling on collision. No fixed
    zeros slot, no refuse — genesis is identified by parent_id null, so
    it can simply re-roll like any other identifier. The prefix comes
    from the active chain."""

    def setUp(self):
        # Genesis-roll tests cover the UNPINNED path. Force no pin so the
        # real on-disk chain.json (which may pin a slot, e.g. the canonical
        # chain reclaiming mememage-0000000000000000) can't leak in here.
        p = patch("mememage.chains.get_genesis_identifier", return_value=None)
        p.start()
        self.addCleanup(p.stop)

    def _genesis_state(self):
        from mememage.core import ConceptionState
        state = ConceptionState(metadata={"prompt": "p"}, gps=(45.5, -122.6),
                                image_path="/x.png")
        state._parent_id = None
        return state

    def test_rolls_hex_not_zeros(self):
        from mememage.core import _step_identifier
        state = self._genesis_state()
        with patch("mememage.core._identifier_taken", return_value=False), \
             patch("mememage.chains.get_identifier_prefix", return_value="mememage"):
            _step_identifier(state)
        self.assertRegex(state.identifier, r"^mememage-[0-9a-f]{16}$")
        self.assertNotEqual(state.identifier, "mememage-0000000000000000")

    def test_rerolls_on_collision(self):
        # First roll taken, second free → genesis assigns the second.
        from mememage.core import _step_identifier
        state = self._genesis_state()
        with patch("mememage.core._identifier_taken", side_effect=[True, False]), \
             patch("mememage.chains.get_identifier_prefix", return_value="mememage"):
            _step_identifier(state)
        self.assertRegex(state.identifier, r"^mememage-[0-9a-f]{16}$")

    def test_gives_up_after_five_collisions(self):
        from mememage.core import _step_identifier
        state = self._genesis_state()
        with patch("mememage.core._identifier_taken", return_value=True), \
             patch("mememage.chains.get_identifier_prefix", return_value="mememage"):
            with self.assertRaises(RuntimeError):
                _step_identifier(state)

    def test_uses_custom_chain_prefix(self):
        from mememage.core import _step_identifier
        state = self._genesis_state()
        with patch("mememage.core._identifier_taken", return_value=False), \
             patch("mememage.chains.get_identifier_prefix", return_value="phoenix"):
            _step_identifier(state)
        self.assertRegex(state.identifier, r"^phoenix-[0-9a-f]{16}$")

    def test_offline_when_no_exists_capable_surface(self):
        # Self-hosted-only chain with nothing to probe → empty surface
        # list → genesis assigns its first roll, no network call.
        from mememage.core import _step_identifier
        state = self._genesis_state()
        with patch("mememage.core._exists_capable_channels", return_value=[]), \
             patch("mememage.chains.get_identifier_prefix", return_value="mememage"):
            _step_identifier(state)
        self.assertRegex(state.identifier, r"^mememage-[0-9a-f]{16}$")


class TestGenesisPin(unittest.TestCase):
    """A chain can PIN its genesis to a specific slot (genesis_identifier in
    chain.json). The genesis mint then occupies it verbatim and SKIPS the
    collision re-roll — the creator owns the slot (e.g. reclaiming the
    recovered IA namespace mememage-0000000000000000). See Path B."""

    def _genesis_state(self):
        from mememage.core import ConceptionState
        state = ConceptionState(metadata={"prompt": "p"}, gps=(45.5, -122.6),
                                image_path="/x.png")
        state._parent_id = None
        return state

    def test_pin_used_verbatim(self):
        from mememage.core import _step_identifier
        state = self._genesis_state()
        with patch("mememage.chains.get_genesis_identifier",
                   return_value="mememage-0000000000000000"), \
             patch("mememage.chains.get_identifier_prefix", return_value="mememage"):
            _step_identifier(state)
        self.assertEqual(state.identifier, "mememage-0000000000000000")

    def test_pin_skips_collision_reroll(self):
        # Even if the slot reads as TAKEN on every surface, a pinned genesis
        # does NOT re-roll — it's a deliberate self-overwrite. _identifier_taken
        # must never be consulted on the pinned path.
        from mememage.core import _step_identifier
        state = self._genesis_state()
        with patch("mememage.chains.get_genesis_identifier",
                   return_value="mememage-0000000000000000"), \
             patch("mememage.chains.get_identifier_prefix", return_value="mememage"), \
             patch("mememage.core._identifier_taken", return_value=True) as taken, \
             patch("mememage.core.genesis_identifier") as roll:
            _step_identifier(state)
        self.assertEqual(state.identifier, "mememage-0000000000000000")
        taken.assert_not_called()
        roll.assert_not_called()

    def test_unpinned_falls_back_to_roll(self):
        from mememage.core import _step_identifier
        state = self._genesis_state()
        with patch("mememage.chains.get_genesis_identifier", return_value=None), \
             patch("mememage.core._identifier_taken", return_value=False), \
             patch("mememage.chains.get_identifier_prefix", return_value="mememage"):
            _step_identifier(state)
        self.assertRegex(state.identifier, r"^mememage-[0-9a-f]{16}$")
        self.assertNotEqual(state.identifier, "mememage-0000000000000000")

    def test_pinned_genesis_packs_into_bar_payload(self):
        # All-zeros suffix must survive the packed binary payload round-trip
        # (8 zero bytes for the id) so the bar can carry it.
        from mememage.bar import _pack_payload, _parse_payload
        ident = "mememage-0000000000000000"
        chash = "47f11bad5dcc9ad2"
        packed = _pack_payload(ident, chash)
        ident2, chash2 = _parse_payload(packed)
        self.assertEqual(ident2, ident)
        self.assertEqual(chash2, chash)


class TestIdentifierTaken(unittest.TestCase):
    """The multi-surface probe. A slot held on ANY enabled exists-capable
    channel counts as taken → the identifier re-rolls. This closes the
    flip scenario: free on IA but already minted on a self-hosted box no
    longer silently overwrites the older soul."""

    def _fake(self, taken):
        ch = MagicMock()
        ch.id = "fake"
        ch.exists.return_value = taken
        return ch

    def test_taken_if_any_surface_holds_it(self):
        from mememage.core import _identifier_taken
        free, held = self._fake(False), self._fake(True)
        self.assertTrue(_identifier_taken("mememage-x", channels=[free, held]))

    def test_free_if_all_surfaces_free(self):
        from mememage.core import _identifier_taken
        self.assertFalse(_identifier_taken(
            "mememage-x", channels=[self._fake(False), self._fake(False)]))

    def test_propagates_probe_errors(self):
        # A probe that can't answer fails the conception loudly rather
        # than risk overwriting a real soul.
        from mememage.core import _identifier_taken
        boom = MagicMock()
        boom.id = "boom"
        boom.exists.side_effect = RuntimeError("network down")
        with self.assertRaises(RuntimeError):
            _identifier_taken("mememage-x", channels=[boom])


class TestDarkMatterRoundTripVerifies(unittest.TestCase):
    """Regression: a dark_matter record, after encryption + save, must
    recompute its own content_hash.

    The bug: ``_step_content_hash`` used to run BEFORE ``_step_encrypt``.
    For light chains nothing broke (encryption only adds gps_password_locked
    on light; the protected fields stay), but on dark_matter the encrypt
    step DELETES origin/birth/width/height/etc. and replaces them with a
    single ``encrypted_fields`` blob. The hash was computed over the
    plaintext fields; the saved soul didn't have them; recomputing
    produced a different hash; every dark-matter record verified as
    tampered. Nobody noticed because the test suite never round-tripped
    a dark-matter mint through encrypt → save → load → verify.

    Fix: hash runs AFTER encrypt, and the V1 inclusion set includes
    encrypted_fields / encrypted_chunks / gps_password_locked so the
    ciphertext blobs are tamper-evident in their own right.
    """

    def _build_record(self):
        return {
            "identifier": "mememage-dkround00001",
            "hash_version": 1,
            "parent_id": None,
            "conceived": "2026-05-28T03:00:00Z",
            "rendered": "2026-05-28T02:55:00Z",
            "age": 1,
            "width": 1024,
            "height": 1024,
            "origin": {"prompt": "round trip", "seed": 42},
            "birth": {"sun": "Aries 24\u00b0"},
            "birth_traits": [2, 4],
            "rarity": {"celestial": [], "machine": [], "entropy": [], "sigil": []},
            "gps_time_locked": {"ct": "x", "N": "y", "T": 10**18, "e": 3},
            "constellation_hash": "1234567890abcdef",
            "constellation_name": "Hutifumul",
            "constellation_index": 0,
            "heart_star_id": "mememage-dkround00001",
            "machine_fingerprint": "53834153",
            "public_key": "0123456789abcdef" * 4,
            "key_fingerprint": "abcd:ef01:2345:6789",
            "chain_visibility": 1,  # dark_matter int code
            "outer_position": 0,
            "outer_total": 1,
        }

    def test_dark_matter_post_encrypt_hash_verifies(self):
        """The end-to-end invariant: encrypt → hash → save → load →
        recompute → matches."""
        from mememage.access import apply_encryption
        from mememage.core import compute_content_hash, verify_metadata
        record = self._build_record()
        # Encrypt FIRST — pipeline order. apply_encryption mutates in place.
        apply_encryption(record, gps=(45.0, -122.0),
                         password="darkpass", chain_visibility="dark_matter")
        # Sanity: encrypt deleted the plaintext, added the ciphertext.
        self.assertNotIn("origin", record)
        self.assertNotIn("birth", record)
        self.assertIn("encrypted_fields", record)
        self.assertIn("gps_password_locked", record)
        # Hash over the POST-encrypt record (what gets saved).
        record["content_hash"] = compute_content_hash(record)
        # Round-trip: serialize + deserialize the saved soul, recompute.
        import json
        reloaded = json.loads(json.dumps(record))
        self.assertTrue(verify_metadata(reloaded),
                        "dark_matter record must verify against its own hash after save+load")

    def test_light_chain_round_trip_still_verifies(self):
        """Light chain regression guard — same flow, light visibility.
        Encryption only adds gps_password_locked; protected fields stay."""
        from mememage.access import apply_encryption
        from mememage.core import compute_content_hash, verify_metadata
        record = self._build_record()
        record["chain_visibility"] = 0  # light_energy
        apply_encryption(record, gps=(45.0, -122.0),
                         password="lightpass", chain_visibility="light_energy")
        # Light keeps plaintext; only GPS gets the password envelope.
        self.assertIn("origin", record)
        self.assertIn("birth", record)
        self.assertIn("gps_password_locked", record)
        record["content_hash"] = compute_content_hash(record)
        import json
        reloaded = json.loads(json.dumps(record))
        self.assertTrue(verify_metadata(reloaded),
                        "light chain record must still verify after the reorder")

    def test_encrypted_fields_tamper_breaks_witnessed(self):
        """Modifying the encrypted_fields blob must invalidate the hash."""
        from mememage.access import apply_encryption
        from mememage.core import compute_content_hash, verify_metadata
        record = self._build_record()
        apply_encryption(record, gps=(45.0, -122.0),
                         password="darkpass", chain_visibility="dark_matter")
        record["content_hash"] = compute_content_hash(record)
        # Flip a byte in the ciphertext.
        ct = record["encrypted_fields"]["ct"]
        record["encrypted_fields"]["ct"] = (("f" if ct[0] != "f" else "0") + ct[1:])
        self.assertFalse(verify_metadata(record),
                         "Tampering with encrypted_fields must break WITNESSED")


class TestDarkMatterSignatureVerifies(unittest.TestCase):
    """Regression: a dark_matter record's signature must verify
    without requiring the verifier to hold the chain password.

    Previously, mint.py signed over sha256(plaintext_thumbnail_bytes),
    but dark_matter stores the thumbnail as an encrypted envelope dict.
    Verifiers without the password couldn't reproduce the plaintext
    hash, so AUTHENTICATED came back FORGED on every dark record.

    Fix: sign over the STORED form (canonical-JSON of the encrypted
    envelope for dark, plaintext string for light). Signature swap
    defense still holds — the attacker can't substitute a different
    encrypted dict without breaking the signature, and they can't
    re-encrypt the original plaintext without the password.
    """

    def _thumbnail_hash_for_record(self, record):
        """Mirror the verify-side computation in verify.js. Used by
        the test to confirm the hash matches what was signed."""
        import hashlib, json
        thumb = record.get("thumbnail")
        if not thumb:
            return ""
        if isinstance(thumb, str):
            return hashlib.sha256(thumb.encode("utf-8")).hexdigest()
        canonical = json.dumps(
            thumb, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _try_sign(self, identifier, content_hash, thumbnail_hash):
        """signing.sign() needs an actual key on disk. Skip the test
        if cryptography isn't installed or no key is present."""
        try:
            from mememage.signing import sign, verify, is_signing_available
        except ImportError:
            return None
        if not is_signing_available():
            return None
        result = sign(identifier, content_hash, thumbnail_hash)
        if result is None:
            return None
        sig_hex, pub_hex, _fp, _name = result
        return sig_hex, pub_hex, verify

    def test_dark_signature_verifies_without_password(self):
        """End-to-end: encrypt a thumbnail, sign over the encrypted
        dict's canonical hash, verify with only the public key (no
        password) — must succeed."""
        from mememage.access import encrypt_field
        identifier = "mememage-sigverify001"
        content_hash = "abcd1234deadbeef"
        plaintext_thumb = "iVBORw0KGgo" * 20  # dummy base64-like blob
        encrypted = encrypt_field(plaintext_thumb, "darkpass")
        # Sign-side hash uses canonical JSON of the encrypted dict.
        record = {"thumbnail": encrypted}
        sign_hash = self._thumbnail_hash_for_record(record)
        signed = self._try_sign(identifier, content_hash, sign_hash)
        if signed is None:
            self.skipTest("signing not available (no cryptography or no key)")
        sig_hex, pub_hex, verify = signed
        # Verifier (no password) computes the same hash over the
        # stored dict and confirms the signature.
        verify_hash = self._thumbnail_hash_for_record(record)
        self.assertEqual(sign_hash, verify_hash,
                         "stored-form hashes must match sign+verify sides")
        self.assertTrue(verify(identifier, content_hash, sig_hex, pub_hex, verify_hash),
                        "dark-chain signature must verify without password")

    def test_light_signature_unchanged(self):
        """Regression guard: plaintext-string thumbnails still hash
        and sign exactly as before."""
        identifier = "mememage-sigverify002"
        content_hash = "1234567890abcdef"
        plaintext_thumb = "iVBORw0KGgo" * 20
        record = {"thumbnail": plaintext_thumb}
        sign_hash = self._thumbnail_hash_for_record(record)
        signed = self._try_sign(identifier, content_hash, sign_hash)
        if signed is None:
            self.skipTest("signing not available (no cryptography or no key)")
        sig_hex, pub_hex, verify = signed
        verify_hash = self._thumbnail_hash_for_record(record)
        self.assertEqual(sign_hash, verify_hash)
        self.assertTrue(verify(identifier, content_hash, sig_hex, pub_hex, verify_hash))

    def test_dark_thumbnail_swap_breaks_signature(self):
        """Tamper resistance: substituting a different encrypted
        envelope must invalidate the signature, even though the
        verifier can't see the plaintext."""
        from mememage.access import encrypt_field
        identifier = "mememage-sigverify003"
        content_hash = "abcd1234deadbeef"
        plaintext_a = "iVBORw0KGgo" * 20
        plaintext_b = "iVBORw0KGgo" * 20 + "different"
        envelope_a = encrypt_field(plaintext_a, "darkpass")
        envelope_b = encrypt_field(plaintext_b, "darkpass")
        sign_hash = self._thumbnail_hash_for_record({"thumbnail": envelope_a})
        signed = self._try_sign(identifier, content_hash, sign_hash)
        if signed is None:
            self.skipTest("signing not available")
        sig_hex, pub_hex, verify = signed
        # Swap the envelope; verify hash differs → signature fails.
        swapped_hash = self._thumbnail_hash_for_record({"thumbnail": envelope_b})
        self.assertNotEqual(sign_hash, swapped_hash)
        self.assertFalse(verify(identifier, content_hash, sig_hex, pub_hex, swapped_hash),
                         "swapped encrypted thumbnail must break signature")


class TestDarkChainEncryptRetry(unittest.TestCase):
    """Regression: namespace-blocked retry on a dark_matter chain.

    Before the fix, _step_upload's retry called _step_encrypt a second
    time on an already-encrypted record. Plaintext soul fields were
    gone, so the second encrypt produced garbage and the mint failed
    with "channel blocked the identifier mid-mint on a dark_matter
    chain — cannot regenerate". The fix stashes a deep-copy of the
    pre-encryption record on state._pre_encrypt_record; the retry
    restores from it before regenerating the identifier and replaying.
    """

    def _state_with_dark_record(self):
        from mememage.core import ConceptionState
        state = ConceptionState(metadata={}, gps=(45.5, -122.6))
        state.password = "darkpass"
        state.chain_visibility = "dark_matter"
        state.identifier = "mememage-original0"
        state.record = {
            "identifier": "mememage-original0",
            "content_hash": "abcd1234abcd1234",
            "origin": {"prompt": "test", "seed": 7},
            "width": 1024,
            "height": 1024,
            "birth": {"sun": "Aries 24°"},
            "gps_time_locked": {"ct": "x", "N": "y", "T": 10**18, "e": 3},
            "rarity": {"celestial": [], "machine": [], "entropy": [], "sigil": []},
            "birth_traits": [2, 4],
            "constellation_hash": "1234567890abcdef",
            "machine_fingerprint": "53834153",
        }
        return state

    def test_encrypt_snapshots_pre_state(self):
        from mememage.core import _step_encrypt
        state = self._state_with_dark_record()
        _step_encrypt(state)
        # Encryption deletes plaintext soul fields
        self.assertIn("encrypted_fields", state.record)
        self.assertNotIn("origin", state.record)
        # But the snapshot still has them
        self.assertIsNotNone(getattr(state, "_pre_encrypt_record", None))
        self.assertIn("origin", state._pre_encrypt_record)
        self.assertEqual(state._pre_encrypt_record["origin"]["prompt"], "test")

    def test_retry_replay_restores_and_reencrypts(self):
        """Simulates the _step_upload retry path end-to-end."""
        import copy
        from mememage.core import _step_encrypt
        state = self._state_with_dark_record()
        # First encrypt — the path that previously poisoned the retry
        _step_encrypt(state)
        first_encrypted_blob = state.record["encrypted_fields"]
        # Simulate NamespaceBlocked retry: restore from snapshot, change
        # identifier, re-encrypt. Mirrors core._step_upload's retry loop.
        state.record = copy.deepcopy(state._pre_encrypt_record)
        self.assertIn("origin", state.record)  # plaintext is back
        state.identifier = "mememage-retry00001"
        state.record["identifier"] = state.identifier
        _step_encrypt(state)
        # Fresh encryption succeeded — plaintext gone, new ciphertext
        self.assertIn("encrypted_fields", state.record)
        self.assertNotIn("origin", state.record)
        # Different identifier → different AAD → different ciphertext
        self.assertNotEqual(state.record["encrypted_fields"], first_encrypted_blob)
        # Snapshot still has plaintext for any further retries
        self.assertIn("origin", state._pre_encrypt_record)


if __name__ == "__main__":
    unittest.main()
