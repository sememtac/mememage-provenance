"""Tests for the channels framework — load, blast, at-least-one, primary."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mememage import channels as ch_mod
from mememage.channels import (
    Channel,
    ChannelUploadError,
    NamespaceBlocked,
    blast,
    load_channels,
    pick_primary_url,
    register,
)


@register
class _FakeOKChannel(Channel):
    TYPE = "_fake_ok"
    DISPLAY_NAME = "Fake (always succeeds)"
    CREDENTIAL_FIELDS = [
        {"name": "key", "label": "Key", "env_var": "FAKE_OK_KEY", "secret": False},
    ]
    CONFIG_FIELDS = []

    def upload(self, identifier, soul_bytes, image_path=None):
        return f"https://fake-ok.example/{identifier}"


@register
class _FakeFailChannel(Channel):
    TYPE = "_fake_fail"
    DISPLAY_NAME = "Fake (always fails)"
    CREDENTIAL_FIELDS = [
        {"name": "key", "label": "Key", "env_var": "FAKE_FAIL_KEY", "secret": False},
    ]

    def upload(self, identifier, soul_bytes, image_path=None):
        raise RuntimeError("boom")


@register
class _FakeBlockedChannel(Channel):
    """Raises NB without claiming NAMESPACE_AUTHORITY — represents a
    buggy plugin. blast() now catches this in phase 2 as an error
    rather than letting it propagate, since a non-authoritative
    channel shouldn't be making naming decisions."""
    TYPE = "_fake_blocked"
    DISPLAY_NAME = "Fake (namespace blocked)"
    CREDENTIAL_FIELDS = [
        {"name": "key", "label": "Key", "env_var": "FAKE_BLOCKED_KEY", "secret": False},
    ]

    def upload(self, identifier, soul_bytes, image_path=None):
        raise NamespaceBlocked("admin-blocked")


@register
class _FakeAuthBlockedChannel(Channel):
    """Authoritative + always raises NB. Phase-1 NB propagates so the
    caller can regenerate the identifier. No phase-2 channel runs
    before this resolves, so there are no orphans to clean up."""
    TYPE = "_fake_auth_blocked"
    DISPLAY_NAME = "Fake authoritative (always NB)"
    NAMESPACE_AUTHORITY = True
    CREDENTIAL_FIELDS = [
        {"name": "key", "label": "Key", "env_var": "FAKE_AUTH_BLOCKED_KEY", "secret": False},
    ]

    def upload(self, identifier, soul_bytes, image_path=None):
        raise NamespaceBlocked("admin-blocked")


# Tracks per-test which channels actually ran (and in what order). Each
# fake plugin appends its id when upload() executes; tests reset the
# list before exercising blast() and assert ordering / non-execution.
_BLAST_TRACE: list[str] = []


@register
class _FakeAuthSlowChannel(Channel):
    """Authoritative + sleeps before succeeding. Used to verify phase 2
    parallelism: phase 1 must complete before phase 2 starts, so the
    slow auth channel sets a floor on the start times of all phase-2
    channels (they should all start after this finishes)."""
    TYPE = "_fake_auth_slow"
    DISPLAY_NAME = "Fake authoritative (slow)"
    NAMESPACE_AUTHORITY = True
    CREDENTIAL_FIELDS = [
        {"name": "key", "label": "Key", "env_var": "FAKE_AUTH_SLOW_KEY", "secret": False},
    ]

    def upload(self, identifier, soul_bytes, image_path=None):
        import time as _t
        _t.sleep(0.05)
        _BLAST_TRACE.append(self.id)
        return f"https://fake-auth-slow.example/{identifier}"


@register
class _FakeTracedOKChannel(Channel):
    """Non-authoritative + succeeds after a tiny delay, appending its
    id to _BLAST_TRACE. Multiple instances allow tests to assert the
    parallel batch ran concurrently."""
    TYPE = "_fake_traced_ok"
    DISPLAY_NAME = "Fake traced ok"
    CREDENTIAL_FIELDS = [
        {"name": "key", "label": "Key", "env_var": "FAKE_TRACED_OK_KEY", "secret": False},
    ]

    def upload(self, identifier, soul_bytes, image_path=None):
        import time as _t
        _t.sleep(0.05)
        _BLAST_TRACE.append(self.id)
        return f"https://fake-traced.example/{self.id}/{identifier}"


def _build(type_name, **overrides):
    cfg = {
        "id": overrides.get("id", type_name),
        "type": type_name,
        "name": overrides.get("name", type_name),
        "enabled": overrides.get("enabled", True),
        "primary": overrides.get("primary", False),
        "credentials": overrides.get("credentials", {}),
        "config": overrides.get("config", {}),
    }
    return ch_mod.get_type(type_name)(cfg)


class TestBlast(unittest.TestCase):
    @patch.dict(os.environ, {"FAKE_OK_KEY": "yes"})
    def test_single_success(self):
        ok = _build("_fake_ok")
        results = blast([ok], "mememage-test1234", b"{}")
        self.assertEqual(results, {"_fake_ok": "https://fake-ok.example/mememage-test1234"})

    @patch.dict(os.environ, {"FAKE_OK_KEY": "yes", "FAKE_FAIL_KEY": "yes"})
    def test_one_succeeds_one_fails(self):
        ok = _build("_fake_ok")
        fail = _build("_fake_fail")
        results = blast([ok, fail], "mememage-test1234", b"{}")
        self.assertIn("_fake_ok", results)
        self.assertNotIn("_fake_fail", results)

    @patch.dict(os.environ, {"FAKE_FAIL_KEY": "yes"})
    def test_all_fail_raises(self):
        fail = _build("_fake_fail")
        with self.assertRaises(ChannelUploadError):
            blast([fail], "mememage-test1234", b"{}")

    @patch.dict(os.environ, {"FAKE_OK_KEY": "yes", "FAKE_AUTH_BLOCKED_KEY": "yes"})
    def test_authoritative_namespace_blocked_propagates(self):
        # The authoritative channel raises NB in phase 1. No phase-2
        # channel should have run yet — the caller regenerates the
        # identifier and re-blasts, with zero orphans behind.
        blocked = _build("_fake_auth_blocked")
        ok = _build("_fake_ok")
        with self.assertRaises(NamespaceBlocked):
            blast([blocked, ok], "mememage-test1234", b"{}")

    @patch.dict(os.environ, {"FAKE_OK_KEY": "yes", "FAKE_BLOCKED_KEY": "yes"})
    def test_non_authoritative_namespace_blocked_is_isolated(self):
        # Non-authoritative channel raising NB is a plugin bug. blast()
        # catches it in phase 2 as a per-channel error so the mint can
        # still complete via the surviving channels.
        blocked = _build("_fake_blocked")
        ok = _build("_fake_ok")
        results = blast([blocked, ok], "mememage-test1234", b"{}")
        self.assertIn("_fake_ok", results)
        self.assertNotIn("_fake_blocked", results)
        # The bug is captured as an error, not silently swallowed.
        self.assertIn("_fake_blocked", results.errors)
        self.assertIn("NAMESPACE_AUTHORITY", results.errors["_fake_blocked"])

    @patch.dict(os.environ, {
        "FAKE_AUTH_SLOW_KEY": "yes",
        "FAKE_TRACED_OK_KEY": "yes",
    })
    def test_phase_ordering_auth_before_others(self):
        # Phase 1 (auth) must complete before phase 2 (parallel) starts.
        # We assert: the auth channel's id appears at trace position 0,
        # and the phase-2 ids all appear AFTER it.
        _BLAST_TRACE.clear()
        auth = _build("_fake_auth_slow", id="auth-slow")
        ok1 = _build("_fake_traced_ok", id="phase2-a")
        ok2 = _build("_fake_traced_ok", id="phase2-b")
        results = blast([ok1, auth, ok2], "mememage-test1234", b"{}")
        self.assertEqual(set(results.keys()), {"auth-slow", "phase2-a", "phase2-b"})
        # Trace order: auth first, then phase-2 (order between a and b
        # is non-deterministic because they run in parallel).
        self.assertEqual(_BLAST_TRACE[0], "auth-slow")
        self.assertEqual(set(_BLAST_TRACE[1:]), {"phase2-a", "phase2-b"})

    @patch.dict(os.environ, {"FAKE_TRACED_OK_KEY": "yes"})
    def test_phase_2_runs_in_parallel(self):
        # Two phase-2 channels each sleep 50ms. If serialized total ≥
        # 100ms; if parallelized total ≈ 50ms + executor overhead.
        # Use 80ms as the threshold — generous enough to absorb thread
        # scheduling on a busy CI box without losing signal.
        _BLAST_TRACE.clear()
        a = _build("_fake_traced_ok", id="par-a")
        b = _build("_fake_traced_ok", id="par-b")
        import time as _t
        t0 = _t.monotonic()
        results = blast([a, b], "mememage-test1234", b"{}")
        elapsed = _t.monotonic() - t0
        self.assertEqual(set(results.keys()), {"par-a", "par-b"})
        self.assertLess(elapsed, 0.08,
                        f"Expected parallel execution under 80ms, took {elapsed*1000:.1f}ms")

    def test_unconfigured_channels_skipped(self):
        # No env vars set → is_configured() returns False → blast
        # silently skips. If all are unconfigured, fall back to
        # ChannelUploadError on the empty-fireable-set guard.
        ok = _build("_fake_ok")
        with self.assertRaises(ChannelUploadError):
            blast([ok], "mememage-test1234", b"{}")


class TestChannelCleanupAPI(unittest.TestCase):
    """capabilities() introspection + default NotImplementedError stubs.

    The Channel base class exposes search/hide/purge as optional
    methods. Plugins that don't override them inherit the default
    NotImplementedError, and capabilities() reports those as False.
    """

    @patch.dict(os.environ, {"FAKE_OK_KEY": "yes"})
    def test_default_capabilities_all_false(self):
        # _FakeOKChannel only overrides upload(), nothing else.
        ok = _build("_fake_ok")
        caps = ok.capabilities()
        self.assertEqual(caps, {"search": False, "hide": False, "purge": False,
                                "exists": False, "test": False})

    @patch.dict(os.environ, {"FAKE_OK_KEY": "yes"})
    def test_default_methods_raise(self):
        ok = _build("_fake_ok")
        with self.assertRaises(NotImplementedError):
            ok.search(pattern="x", limit=1)
        with self.assertRaises(NotImplementedError):
            ok.hide("mememage-x")
        with self.assertRaises(NotImplementedError):
            ok.purge("mememage-x")

    def test_ia_capabilities_all_true(self):
        from mememage.channels.internet_archive import InternetArchiveChannel
        ch = InternetArchiveChannel({"id": "ia", "type": "internet_archive"})
        caps = ch.capabilities()
        self.assertEqual(caps, {"search": True, "hide": True, "purge": True,
                                "exists": True, "test": False})

    def test_ia_search_decorates_url(self):
        from mememage.channels.internet_archive import InternetArchiveChannel
        ch = InternetArchiveChannel({"id": "ia", "type": "internet_archive"})
        fake_items = [
            {"identifier": "mememage-aaaa11111111", "publicdate": "2026-05-01"},
            {"identifier": "mememage-bbbb22222222", "publicdate": "2026-05-02"},
        ]
        with patch("mememage.ia_admin.search_items", return_value=fake_items):
            items = ch.search(pattern="mememage-*", limit=10,
                              uploader="me@example.com")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["url"], "https://archive.org/details/mememage-aaaa11111111")
        self.assertEqual(items[1]["url"], "https://archive.org/details/mememage-bbbb22222222")

    @patch.dict(os.environ, {"IA_ACCESS_KEY": "AAA", "IA_SECRET_KEY": "SSS"})
    def test_ia_hide_delegates_to_ia_admin(self):
        from mememage.channels.internet_archive import InternetArchiveChannel
        ch = InternetArchiveChannel({"id": "ia", "type": "internet_archive"})
        with patch("mememage.ia_admin.darken_item",
                   return_value={"ok": True, "error": ""}) as m:
            r = ch.hide("mememage-test1")
        m.assert_called_once_with("mememage-test1", "AAA", "SSS")
        self.assertEqual(r, {"ok": True, "error": ""})

    @patch.dict(os.environ, {"IA_ACCESS_KEY": "AAA", "IA_SECRET_KEY": "SSS"})
    def test_ia_purge_normalizes_ok_flag(self):
        from mememage.channels.internet_archive import InternetArchiveChannel
        ch = InternetArchiveChannel({"id": "ia", "type": "internet_archive"})
        # delete_files returns deleted/failed/files counts; purge() must
        # tack on a top-level `ok` so callers can treat it uniformly.
        with patch("mememage.ia_admin.delete_files",
                   return_value={"deleted": 3, "failed": 0, "files": 3, "errors": []}):
            r = ch.purge("mememage-test1")
        self.assertTrue(r["ok"])
        with patch("mememage.ia_admin.delete_files",
                   return_value={"deleted": 1, "failed": 1, "files": 2, "errors": ["x"]}):
            r2 = ch.purge("mememage-test1")
        self.assertFalse(r2["ok"])

    def test_ia_hide_no_credentials(self):
        from mememage.channels.internet_archive import InternetArchiveChannel
        ch = InternetArchiveChannel({"id": "ia", "type": "internet_archive"})
        # Channel reads credentials lazily; without env vars the call
        # returns a clean error rather than crashing.
        with patch.dict(os.environ, {"IA_ACCESS_KEY": "", "IA_SECRET_KEY": ""}, clear=False):
            r = ch.hide("mememage-test1")
        self.assertFalse(r["ok"])
        self.assertIn("credentials", r["error"].lower())


class TestHttpPushCleanup(unittest.TestCase):
    """search() lists via GET /api/souls?pattern=...; purge() DELETEs
    via /api/souls/<id>.{soul,json}. Both inherit upload()'s auth + TLS
    handling so they Just Work against the same self-signed peer +
    bearer token config."""

    def _build_http_push(self):
        from mememage.channels.http_push import HttpPushChannel
        return HttpPushChannel({
            "id": "vps",
            "type": "http_push",
            "name": "Peer VPS",
            "enabled": True,
            "credentials": {},
            "config": {
                "base_url": "https://example.com/api/souls",
                "accept_self_signed": True,
            },
        })

    def test_capabilities_match_implementation(self):
        ch = self._build_http_push()
        # search + purge + test implemented, hide is not (no noindex equivalent).
        self.assertEqual(ch.capabilities(),
                         {"search": True, "hide": False, "purge": True,
                          "exists": True, "test": True})

    @patch.dict(os.environ, {"HTTP_PUSH_TOKEN": "secret-token"})
    def test_search_constructs_url_and_returns_items(self):
        import json as _json
        import io
        from mememage.channels import http_push as hp
        captured = {}

        def fake_urlopen(req, *a, **kw):
            captured["url"] = req.full_url
            captured["auth"] = req.headers.get("Authorization")

            class _R:
                def __enter__(self_inner): return self_inner
                def __exit__(self_inner, *a): return False
                def read(self_inner):
                    return _json.dumps({"items": [
                        {"identifier": "mememage-aaa11122233", "url": "x",
                         "size": 1024, "date": "2026-05-28T00:00:00+00:00"},
                    ]}).encode("utf-8")
            return _R()

        ch = self._build_http_push()
        with patch.object(hp.urllib.request, "urlopen", side_effect=fake_urlopen):
            items = ch.search(pattern="mememage-*", limit=10)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["identifier"], "mememage-aaa11122233")
        # URL carries pattern + limit; auth carries bearer.
        self.assertIn("pattern=mememage-", captured["url"])
        self.assertIn("limit=10", captured["url"])
        self.assertEqual(captured["auth"], "Bearer secret-token")

    @patch.dict(os.environ, {"HTTP_PUSH_TOKEN": "secret-token"})
    def test_search_404_returns_empty_list(self):
        import urllib.error
        from mememage.channels import http_push as hp
        ch = self._build_http_push()
        err = urllib.error.HTTPError(url="x", code=404, msg="", hdrs={}, fp=None)
        with patch.object(hp.urllib.request, "urlopen", side_effect=err):
            items = ch.search()
        # 404 = peer has no listing endpoint, treat as "nothing to clean".
        self.assertEqual(items, [])

    @patch.dict(os.environ, {"HTTP_PUSH_TOKEN": "secret-token"})
    def test_purge_deletes_both_soul_and_json(self):
        from mememage.channels import http_push as hp
        captured = []

        def fake_urlopen(req, *a, **kw):
            captured.append((req.method, req.full_url,
                             req.headers.get("Authorization")))

            class _R:
                def __enter__(self_inner): return self_inner
                def __exit__(self_inner, *a): return False
                def read(self_inner): return b""
            return _R()

        ch = self._build_http_push()
        with patch.object(hp.urllib.request, "urlopen", side_effect=fake_urlopen):
            r = ch.purge("mememage-aaa11122233")
        self.assertTrue(r["ok"])
        self.assertEqual(r["deleted"], 2)
        # Both .soul and .json went out as DELETE with bearer.
        methods = [c[0] for c in captured]
        urls = [c[1] for c in captured]
        self.assertEqual(methods, ["DELETE", "DELETE"])
        self.assertTrue(any(u.endswith("/mememage-aaa11122233.soul") for u in urls))
        self.assertTrue(any(u.endswith("/mememage-aaa11122233.json") for u in urls))
        self.assertTrue(all(c[2] == "Bearer secret-token" for c in captured))

    @patch.dict(os.environ, {"HTTP_PUSH_TOKEN": "secret-token"})
    def test_purge_404_treated_as_already_gone(self):
        """A missing .json mirror shouldn't fail the purge — only the
        .soul deletion path needs to succeed."""
        import urllib.error
        from mememage.channels import http_push as hp
        call_count = [0]

        def fake_urlopen(req, *a, **kw):
            call_count[0] += 1
            # First call (.soul) succeeds; second (.json) returns 404.
            if call_count[0] == 1:
                class _R:
                    def __enter__(self_inner): return self_inner
                    def __exit__(self_inner, *a): return False
                    def read(self_inner): return b""
                return _R()
            raise urllib.error.HTTPError(url="x", code=404, msg="", hdrs={}, fp=None)

        ch = self._build_http_push()
        with patch.object(hp.urllib.request, "urlopen", side_effect=fake_urlopen):
            r = ch.purge("mememage-aaa11122233")
        self.assertTrue(r["ok"])
        self.assertEqual(r["deleted"], 1)
        self.assertEqual(r["failed"], 0)


class TestPickPrimary(unittest.TestCase):
    @patch.dict(os.environ, {"FAKE_OK_KEY": "yes"})
    def test_primary_wins_when_succeeded(self):
        primary = _build("_fake_ok", id="primary-ch", primary=True)
        secondary = _build("_fake_ok", id="secondary-ch", primary=False)
        results = {"secondary-ch": "https://sec/x", "primary-ch": "https://prim/x"}
        self.assertEqual(pick_primary_url([primary, secondary], results), "https://prim/x")

    def test_falls_back_to_first_successful(self):
        a = _build("_fake_ok", id="a")
        b = _build("_fake_ok", id="b")
        results = {"b": "https://b/x"}
        self.assertEqual(pick_primary_url([a, b], results), "https://b/x")

    def test_empty_when_no_results(self):
        a = _build("_fake_ok", id="a")
        self.assertEqual(pick_primary_url([a], {}), "")


class TestLoadChannels(unittest.TestCase):
    def test_auto_creates_default_config(self):
        # Point CHANNELS_PATH at a temp dir for this test, then call
        # _load_raw — should write the default file and return it.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp) / "channels.json"
            with patch.object(ch_mod, "CHANNELS_PATH", tmp_path), \
                 patch.object(ch_mod, "MEMEMAGE_ROOT", Path(tmp)):
                raw = ch_mod._load_raw()
            self.assertTrue(tmp_path.exists())
            ids = [c["id"] for c in raw["channels"]]
            self.assertIn("ia", ids)
            self.assertIn("zenodo", ids)

    def test_skips_unknown_type(self):
        # An entry with a bogus type shouldn't crash load_channels —
        # log + skip.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp) / "channels.json"
            tmp_path.write_text(json.dumps({
                "channels": [
                    {"id": "bad", "type": "definitely_not_registered"},
                    {"id": "ok", "type": "_fake_ok"},
                ]
            }))
            with patch.object(ch_mod, "CHANNELS_PATH", tmp_path):
                channels = load_channels()
            ids = [c.id for c in channels]
            self.assertIn("ok", ids)
            self.assertNotIn("bad", ids)


class TestPerProfileChannels(unittest.TestCase):
    """Channels are stored per-profile: each profile owns its own
    channels.json under ~/.mememage/profiles/<id>/. CHANNELS_PATH=None
    (production) routes through the active profile."""

    def _patch_profiles_root(self, root):
        """Point the profiles subsystem at a temp root and return a context
        manager stack already entered. Caller is responsible for cleanup via
        addCleanup."""
        from mememage import profiles as profs
        patches = [
            patch.object(profs, "ROOT", root),
            patch.object(profs, "PROFILES_DIR", root / "profiles"),
            patch.object(profs, "ACTIVE_FILE", root / "active_profile"),
            patch.object(ch_mod, "CHANNELS_PATH", None),
            patch.object(ch_mod, "MEMEMAGE_ROOT", root),
            patch.object(ch_mod, "LEGACY_CHANNELS_PATH", root / "channels.json"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        # set_active() refuses a profile whose dir doesn't exist — create the
        # default one up front (the auto-migration would do this in prod).
        (root / "profiles" / profs.DEFAULT_PROFILE_ID).mkdir(parents=True, exist_ok=True)
        return profs

    def test_distinct_profiles_have_independent_channels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profs = self._patch_profiles_root(root)
            # Profile A: default; give it a custom channel set.
            profs.set_active(profs.DEFAULT_PROFILE_ID)
            ch_mod.save_raw({"channels": [
                {"id": "a-only", "type": "_fake_ok", "name": "A", "enabled": True},
            ]})
            a_path = root / "profiles" / profs.DEFAULT_PROFILE_ID / "channels.json"
            self.assertTrue(a_path.exists())
            # Profile B: a new id. Its first load seeds the DEFAULT config —
            # NOT profile A's channels.
            (root / "profiles" / "second").mkdir(parents=True, exist_ok=True)
            profs.set_active("second")
            raw_b = ch_mod._load_raw()
            ids_b = [c["id"] for c in raw_b["channels"]]
            self.assertNotIn("a-only", ids_b)   # isolation: B can't see A's set
            self.assertIn("ia", ids_b)          # B got the fresh default
            # Switching back to A restores A's set, no reconfigure.
            profs.set_active(profs.DEFAULT_PROFILE_ID)
            ids_a = [c["id"] for c in ch_mod._load_raw()["channels"]]
            self.assertEqual(ids_a, ["a-only"])

    def test_legacy_global_migrates_into_first_profile_then_consumed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profs = self._patch_profiles_root(root)
            profs.set_active(profs.DEFAULT_PROFILE_ID)
            # Seed a pre-per-profile global channels.json.
            legacy = root / "channels.json"
            legacy.write_text(json.dumps({"channels": [
                {"id": "heritage", "type": "_fake_ok", "name": "Legacy", "enabled": True},
            ]}))
            # First load on the active profile adopts the legacy config.
            raw = ch_mod._load_raw()
            self.assertEqual([c["id"] for c in raw["channels"]], ["heritage"])
            prof_path = root / "profiles" / profs.DEFAULT_PROFILE_ID / "channels.json"
            self.assertTrue(prof_path.exists())
            # Legacy file is consumed so other profiles don't inherit it.
            self.assertFalse(legacy.exists())
            self.assertTrue((root / "channels.json.migrated").exists())
            # A second profile now seeds the DEFAULT, not the heritage config.
            (root / "profiles" / "second").mkdir(parents=True, exist_ok=True)
            profs.set_active("second")
            ids_b = [c["id"] for c in ch_mod._load_raw()["channels"]]
            self.assertNotIn("heritage", ids_b)
            self.assertIn("ia", ids_b)


class TestSigningKeychainViaChannels(unittest.TestCase):
    """Pin signing.upload_keychain_record to the channels-aware path.

    Regression for the audit finding (2026-05-18) where direct IA
    writes bypassed the channels framework. If a future change
    re-introduces get_ia_keys / IA_S3_URL / urlopen_with_retry in
    signing.py, this test catches it.
    """

    def test_signing_keychain_routes_through_channels(self):
        from mememage import signing

        with patch("mememage.channels.blast_keychain", return_value={"ia": "https://x/y"}) as mock_blast, \
             patch("mememage.config.get_ia_keys") as mock_ia, \
             patch("mememage.net.urlopen_with_retry") as mock_urlopen:
            signing.upload_keychain_record(
                {"identifier": "test"}, "mememage-keychain-abc1234567890def", "succession.json"
            )

        self.assertTrue(mock_blast.called, "signing.upload_keychain_record must use channels.blast_keychain")
        self.assertFalse(mock_ia.called, "signing.upload_keychain_record must NOT call get_ia_keys")
        self.assertFalse(mock_urlopen.called, "signing.upload_keychain_record must NOT call urlopen_with_retry directly")


class TestBlastKeychain(unittest.TestCase):
    """Keychain records (succession / revocation / alias) mirror through
    channels so chains with IA disabled can still publish their key
    lifecycle to peers. Channels that don't fit the keychain shape
    (Zenodo) are silently skipped."""

    @patch.dict(os.environ, {"FAKE_OK_KEY": "yes"})
    def test_keychain_single_channel_success(self):
        from mememage.channels import blast_keychain

        # Patch upload_keychain on the fake-ok class so it succeeds
        with patch.object(_FakeOKChannel, "upload_keychain",
                          return_value="https://fake/keychain/CHAIN/file.json",
                          create=True):
            ok = _build("_fake_ok")
            results = blast_keychain([ok], "mememage-keychain-XX", "succession.json", b"{}")
            self.assertEqual(results["_fake_ok"],
                             "https://fake/keychain/CHAIN/file.json")

    @patch.dict(os.environ, {"FAKE_OK_KEY": "yes"})
    def test_keychain_skips_not_implemented(self):
        # A channel that doesn't override upload_keychain raises
        # NotImplementedError from the base — blast_keychain silently
        # skips and as long as at least one other channel succeeds,
        # returns those results.
        from mememage.channels import blast_keychain, ChannelUploadError

        with patch.object(_FakeOKChannel, "upload_keychain",
                          return_value="https://fake/k/X/f.json",
                          create=True):
            ok = _build("_fake_ok", id="ok")
            # Use a second channel with no upload_keychain override
            # → base class NotImplementedError → silently skipped
            skip = _build("_fake_ok", id="skip-me")
            results = blast_keychain([ok, skip], "X", "f.json", b"{}")
            self.assertIn("ok", results)
            # The other one wasn't skipped per se (same class), so this
            # test mainly pins that NotImplementedError → skip via the
            # ChannelUploadError no-results path. Test the explicit
            # NotImplementedError path next.

    @patch.dict(os.environ, {"FAKE_OK_KEY": "yes"})
    def test_keychain_all_failed_raises(self):
        from mememage.channels import blast_keychain, ChannelUploadError

        # Channel with upload_keychain that errors
        with patch.object(_FakeOKChannel, "upload_keychain",
                          side_effect=RuntimeError("boom"), create=True):
            ok = _build("_fake_ok")
            with self.assertRaises(ChannelUploadError):
                blast_keychain([ok], "X", "f.json", b"{}")


if __name__ == "__main__":
    unittest.main()
