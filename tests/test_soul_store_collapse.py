"""The soul-store collapse: one flat store (~/.mememage/received), the self-push
surface writes/reads/purges it DIRECTLY (no loopback HTTP), and legacy per-chain
records/ souls migrate into it.

This is the change that removed the self-push loopback — the mechanism behind a
whole run of EOF/port/cap wedges.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mememage import core
from mememage.channels.http_push import HttpPushChannel
from mememage.server import _migrate_records_to_store


def _self_ch():
    # base_url is loopback → _is_self_push() is True (localhost form).
    return HttpPushChannel({
        "id": "self", "type": "http_push", "enabled": True,
        "config": {"base_url": "https://127.0.0.1:8444/api/souls",
                   "accept_self_signed": True},
    })


class SelfPushNoLoopback(unittest.TestCase):
    def test_upload_returns_url_without_network(self):
        ch = _self_ch()
        with patch("mememage.channels.http_push.urlopen_with_retry") as net:
            url = ch.upload("juliet-aaaabbbbccccdddd", b'{"x":1}')
        net.assert_not_called()                      # no HTTP-to-self
        self.assertTrue(url.endswith("juliet-aaaabbbbccccdddd.soul"))

    def test_exists_checks_local_store(self):
        ch = _self_ch()
        d = Path(tempfile.mkdtemp())
        (d / "juliet-aaaabbbbccccdddd.soul").write_text("{}")
        with patch.object(HttpPushChannel, "_local_store_dir", return_value=d):
            self.assertTrue(ch.exists("juliet-aaaabbbbccccdddd"))
            self.assertFalse(ch.exists("juliet-ffffffffffffffff"))

    def test_purge_unlinks_from_store(self):
        ch = _self_ch()
        d = Path(tempfile.mkdtemp())
        f = d / "juliet-aaaabbbbccccdddd.soul"
        f.write_text("{}")
        hashed = d / "juliet-aaaabbbbccccdddd.deadbeefdeadbeef.soul"
        hashed.write_text("{}")
        with patch.object(HttpPushChannel, "_local_store_dir", return_value=d):
            res = ch.purge("juliet-aaaabbbbccccdddd")
        self.assertTrue(res["ok"])
        self.assertEqual(res["deleted"], 2)          # .soul + .<hash>.soul mirror
        self.assertFalse(f.exists())
        self.assertFalse(hashed.exists())


class SaveToStore(unittest.TestCase):
    def test_save_writes_to_flat_store(self):
        d = Path(tempfile.mkdtemp())
        with patch("mememage.core.soul_store_dir", return_value=d):
            path = core._save_local_backup(
                "juliet-aaaabbbbccccdddd", {"identifier": "juliet-aaaabbbbccccdddd"})
        self.assertIsNotNone(path)
        self.assertTrue((d / "juliet-aaaabbbbccccdddd.soul").is_file())


class RecordsMigration(unittest.TestCase):
    def test_copies_records_into_store_preserving_subdirs(self):
        # Migration preserves any subdir nesting under records/.
        root = Path(tempfile.mkdtemp())
        recs = root / "mychain" / "records"
        recs.mkdir(parents=True)
        (recs / "juliet-aaaabbbbccccdddd.soul").write_text('{"x":1}')
        (recs / "sub").mkdir()
        (recs / "sub" / "juliet-bbbbccccddddeeee.soul").write_text("{}")
        store = Path(tempfile.mkdtemp())
        _migrate_records_to_store(chains_root=root, store=store)
        self.assertTrue((store / "juliet-aaaabbbbccccdddd.soul").is_file())
        self.assertTrue((store / "sub" / "juliet-bbbbccccddddeeee.soul").is_file())

    def test_idempotent_does_not_clobber(self):
        root = Path(tempfile.mkdtemp())
        recs = root / "c" / "records"
        recs.mkdir(parents=True)
        (recs / "juliet-aaaabbbbccccdddd.soul").write_text('{"orig":1}')
        store = Path(tempfile.mkdtemp())
        _migrate_records_to_store(chains_root=root, store=store)
        # A later edit to the store copy must survive a re-run.
        (store / "juliet-aaaabbbbccccdddd.soul").write_text('{"edited":1}')
        _migrate_records_to_store(chains_root=root, store=store)
        self.assertIn("edited", (store / "juliet-aaaabbbbccccdddd.soul").read_text())


if __name__ == "__main__":
    unittest.main()
