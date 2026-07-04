"""Tests for the pluggable notifier framework (generalized webhook delivery).

Covers adapter resolution (auto-detect + explicit kind), attachment
descriptor building, and each adapter's deliver() HTTP shape: Discord plain +
multipart, generic plain, Slack text + files.uploadV2 (3-step) + graceful
fallback when the bot token/channel aren't configured.
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from mememage import notifiers
from mememage.notifiers import resolve, build_attachments, FileAttachment


class _Resp:
    """Minimal urlopen response: context-manager + read()."""
    def __init__(self, body=b""):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Recorder:
    """Stand-in for urllib.request.urlopen. Records every Request and returns
    a body chosen by URL substring match."""
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def __call__(self, req, timeout=None):
        self.calls.append(req)
        url = getattr(req, "full_url", req)
        for sub, body in self.responses.items():
            if sub in url:
                return _Resp(body)
        return _Resp(b"")

    def urls(self):
        return [getattr(r, "full_url", r) for r in self.calls]


def _hook(**kw):
    kw.setdefault("url", "https://example.com/hook")
    return kw


class TestResolve(unittest.TestCase):
    def test_autodetect_discord(self):
        n = resolve(_hook(url="https://discord.com/api/webhooks/1/abc"))
        self.assertEqual(n.TYPE, "discord")

    def test_autodetect_slack(self):
        n = resolve(_hook(url="https://hooks.slack.com/services/T/B/x"))
        self.assertEqual(n.TYPE, "slack")

    def test_unknown_falls_back_to_generic(self):
        n = resolve(_hook(url="https://my-server.example/notify"))
        self.assertEqual(n.TYPE, "generic")

    def test_explicit_kind_overrides_url(self):
        # A Discord URL but kind=slack → Slack adapter (operator's call wins).
        n = resolve(_hook(url="https://discord.com/api/x", kind="slack"))
        self.assertEqual(n.TYPE, "slack")

    def test_unknown_kind_ignored_falls_to_autodetect(self):
        n = resolve(_hook(url="https://discord.com/api/x", kind="nope"))
        self.assertEqual(n.TYPE, "discord")


class TestBuildAttachments(unittest.TestCase):
    def test_image_and_soul(self):
        with tempfile.TemporaryDirectory() as d:
            img = os.path.join(d, "i.png"); open(img, "wb").write(b"\x89PNG")
            soul = os.path.join(d, "s.soul"); open(soul, "wb").write(b"{}")
            files = build_attachments({
                "identifier": "mememage-abc", "image_path": img, "soul_path": soul,
            })
            roles = {f.role: f for f in files}
            self.assertIn("image", roles)
            self.assertIn("soul", roles)
            self.assertEqual(roles["image"].filename, "mememage-abc.png")
            self.assertEqual(roles["soul"].filename, "mememage-abc.soul")
            self.assertEqual(roles["image"].read(), b"\x89PNG")

    def test_missing_files_skipped(self):
        files = build_attachments({
            "identifier": "x", "image_path": "/nope/missing.png",
        })
        self.assertEqual(files, [])


class TestDiscord(unittest.TestCase):
    def test_plain_post_no_files(self):
        rec = _Recorder()
        with patch("urllib.request.urlopen", rec):
            resolve(_hook(url="https://discord.com/api/x")).deliver(
                b'{"content":"hi"}', [], "ready", {})
        self.assertEqual(len(rec.calls), 1)
        req = rec.calls[0]
        self.assertEqual(req.data, b'{"content":"hi"}')
        self.assertIn("application/json", req.headers.get("Content-type", ""))

    def test_multipart_with_files(self):
        rec = _Recorder()
        with tempfile.TemporaryDirectory() as d:
            img = os.path.join(d, "i.png"); open(img, "wb").write(b"PNGDATA")
            files = build_attachments({"identifier": "id1", "image_path": img})
            with patch("urllib.request.urlopen", rec):
                resolve(_hook(url="https://discord.com/api/x")).deliver(
                    b'{"content":"hi"}', files, "conceived", {})
        self.assertEqual(len(rec.calls), 1)
        req = rec.calls[0]
        self.assertIn("multipart/form-data", req.headers.get("Content-type", ""))
        self.assertIn(b'name="payload_json"', req.data)
        self.assertIn(b'filename="id1.png"', req.data)
        self.assertIn(b"PNGDATA", req.data)


class TestGeneric(unittest.TestCase):
    def test_plain_post(self):
        rec = _Recorder()
        with patch("urllib.request.urlopen", rec):
            resolve(_hook(url="https://x.example/n")).deliver(
                b'{"a":1}', [], "ready", {})
        self.assertEqual(len(rec.calls), 1)
        self.assertEqual(rec.calls[0].data, b'{"a":1}')

    def test_files_ignored_still_posts_body(self):
        rec = _Recorder()
        with tempfile.TemporaryDirectory() as d:
            img = os.path.join(d, "i.png"); open(img, "wb").write(b"x")
            files = build_attachments({"identifier": "id", "image_path": img})
            with patch("urllib.request.urlopen", rec):
                resolve(_hook(url="https://x.example/n")).deliver(
                    b'{"a":1}', files, "conceived", {})
        self.assertEqual(len(rec.calls), 1)  # one plain POST, no upload attempts


class TestSlack(unittest.TestCase):
    SLACK_URL = "https://hooks.slack.com/services/T/B/x"

    def test_text_post_when_no_token(self):
        rec = _Recorder({"hooks.slack.com": b"ok"})
        with patch("urllib.request.urlopen", rec):
            resolve(_hook(url=self.SLACK_URL)).deliver(
                b'{"text":"hi"}', [], "ready", {})
        self.assertEqual(len(rec.calls), 1)
        self.assertIn("hooks.slack.com", rec.urls()[0])

    def test_attach_without_token_falls_back_to_text(self):
        rec = _Recorder({"hooks.slack.com": b"ok"})
        with tempfile.TemporaryDirectory() as d:
            img = os.path.join(d, "i.png"); open(img, "wb").write(b"x")
            files = build_attachments({"identifier": "id", "image_path": img})
            with patch("urllib.request.urlopen", rec):
                resolve(_hook(url=self.SLACK_URL)).deliver(
                    b'{"text":"hi"}', files, "conceived", {})
        # No bot token → no uploadV2 calls, just the text post.
        self.assertEqual(len(rec.calls), 1)
        self.assertIn("hooks.slack.com", rec.urls()[0])

    def test_files_upload_v2_flow(self):
        rec = _Recorder({
            "files.getUploadURLExternal": json.dumps({
                "ok": True, "upload_url": "https://files.slack.com/upload/Z",
                "file_id": "F123",
            }).encode(),
            "files.slack.com/upload": b"",
            "files.completeUploadExternal": b'{"ok": true}',
        })
        with tempfile.TemporaryDirectory() as d:
            img = os.path.join(d, "i.png"); open(img, "wb").write(b"PNGBYTES")
            files = build_attachments({"identifier": "id1", "image_path": img})
            hook = _hook(url=self.SLACK_URL,
                         slack_bot_token="xoxb-test", slack_channel="C0ABC")
            with patch("urllib.request.urlopen", rec):
                resolve(hook).deliver(
                    b'{"text":"hi"}', files, "conceived",
                    {"summary": "Soul conceived: id1", "action_url": "https://ia/x"})
        urls = rec.urls()
        # 3-step flow: reserve URL → upload bytes → complete.
        self.assertTrue(any("files.getUploadURLExternal" in u for u in urls))
        self.assertTrue(any("files.slack.com/upload" in u for u in urls))
        complete = [r for r in rec.calls
                    if "files.completeUploadExternal" in getattr(r, "full_url", "")]
        self.assertEqual(len(complete), 1)
        body = json.loads(complete[0].data.decode())
        self.assertEqual(body["channel_id"], "C0ABC")
        self.assertIn("Soul conceived: id1", body["initial_comment"])
        self.assertEqual(body["files"][0]["id"], "F123")
        # bot token rode in the Authorization header.
        self.assertEqual(complete[0].headers.get("Authorization"), "Bearer xoxb-test")

    def test_slack_upload_error_falls_back_to_text(self):
        # An upload failure (missing scope, bad channel, …) must NOT lose the
        # notification — it degrades to the text post on the incoming webhook.
        rec = _Recorder({
            "files.getUploadURLExternal": b'{"ok": false, "error": "missing_scope"}',
            "hooks.slack.com": b"ok",
        })
        with tempfile.TemporaryDirectory() as d:
            img = os.path.join(d, "i.png"); open(img, "wb").write(b"x")
            files = build_attachments({"identifier": "id", "image_path": img})
            hook = _hook(url=self.SLACK_URL,
                         slack_bot_token="xoxb-bad", slack_channel="C0")
            with patch("urllib.request.urlopen", rec):
                resolve(hook).deliver(b'{"text":"hi"}', files, "conceived", {})
        # Tried the upload, then fell back to a text post on the webhook URL.
        urls = rec.urls()
        self.assertTrue(any("files.getUploadURLExternal" in u for u in urls))
        self.assertTrue(any("hooks.slack.com/services" in u for u in urls))


class TestTelegram(unittest.TestCase):
    OK = b'{"ok": true, "result": {}}'

    def _hook(self, **kw):
        return _hook(url="https://api.telegram.org",
                     telegram_bot_token="123:ABC", telegram_chat_id="@chan", **kw)

    def test_autodetect_and_kind(self):
        self.assertEqual(resolve(_hook(url="https://api.telegram.org/botX/x")).TYPE,
                         "telegram")
        self.assertEqual(resolve(_hook(url="https://other", kind="telegram")).TYPE,
                         "telegram")

    def test_text_sendmessage(self):
        rec = _Recorder({"api.telegram.org": self.OK})
        with patch("urllib.request.urlopen", rec):
            resolve(self._hook()).deliver(
                json.dumps({"text": "hi there\nhttps://x"}).encode(),
                [], "ready", {})
        self.assertEqual(len(rec.calls), 1)
        req = rec.calls[0]
        self.assertIn("/bot123:ABC/sendMessage", req.full_url)
        body = json.loads(req.data.decode())
        self.assertEqual(body["chat_id"], "@chan")
        self.assertEqual(body["text"], "hi there\nhttps://x")

    def test_text_fallback_to_summary(self):
        # No usable "text" in the body → build from summary + action_url.
        rec = _Recorder({"api.telegram.org": self.OK})
        with patch("urllib.request.urlopen", rec):
            resolve(self._hook()).deliver(
                json.dumps({"event": "ready"}).encode(), [], "ready",
                {"summary": "Conception ready for x.png",
                 "action_url": "https://mint/abc"})
        body = json.loads(rec.calls[0].data.decode())
        self.assertEqual(body["text"], "Conception ready for x.png\nhttps://mint/abc")

    def test_files_send_photo_and_document(self):
        rec = _Recorder({"api.telegram.org": self.OK})
        with tempfile.TemporaryDirectory() as d:
            img = os.path.join(d, "i.png"); open(img, "wb").write(b"PNG")
            soul = os.path.join(d, "s.soul"); open(soul, "wb").write(b"{}")
            files = build_attachments({
                "identifier": "id1", "image_path": img, "soul_path": soul})
            with patch("urllib.request.urlopen", rec):
                resolve(self._hook()).deliver(
                    json.dumps({"text": "caption!"}).encode(), files,
                    "conceived", {})
        urls = rec.urls()
        self.assertTrue(any("/sendPhoto" in u for u in urls))
        self.assertTrue(any("/sendDocument" in u for u in urls))
        # sendPhoto carries the message as the caption (multipart).
        photo = [r for r in rec.calls if "/sendPhoto" in r.full_url][0]
        self.assertIn(b'name="caption"', photo.data)
        self.assertIn(b"caption!", photo.data)
        self.assertIn(b'filename="id1.png"', photo.data)

    def test_missing_chat_id_raises(self):
        rec = _Recorder({"api.telegram.org": self.OK})
        hook = _hook(url="https://api.telegram.org", telegram_bot_token="123:ABC")
        with patch("urllib.request.urlopen", rec):
            with self.assertRaises(Exception):
                resolve(hook).deliver(b'{"text":"x"}', [], "ready", {})


if __name__ == "__main__":
    unittest.main()
