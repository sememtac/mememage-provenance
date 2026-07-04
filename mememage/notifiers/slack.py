"""Slack notifier — text via Incoming Webhook, files via files.uploadV2.

Slack's reality, contained in one adapter:

- An **Incoming Webhook** URL (``hooks.slack.com/services/…``) accepts only a
  JSON body (``text``/``blocks``). It physically cannot carry files. That's the
  default text path and needs no extra config.
- To attach **file bytes** (parity with Discord), Slack requires its Web API
  ``files.uploadV2`` flow, which needs a **bot token** (``xoxb-…`` with the
  ``files:write`` scope) and a **channel id** — exactly the way Discord
  attachments lean on a bot token. Configured per-hook as ``slack_bot_token``
  + ``slack_channel``.

So: ``conceived`` + ``attach_files`` + bot-token + channel → upload the image +
soul into the channel via files.uploadV2 (the rendered summary becomes the
message's ``initial_comment``). Otherwise → plain text post to the Incoming
Webhook URL. If attachments are requested but the bot token / channel aren't
configured, we log a clear note and fall back to the text post rather than
breaking the send.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

from mememage.notifiers import Notifier, base_headers, log, register

_SLACK_API = "https://slack.com/api/"


@register
class SlackNotifier(Notifier):
    TYPE = "slack"
    DISPLAY_NAME = "Slack"
    SUPPORTS_ATTACHMENTS = True  # via files.uploadV2 when bot token + channel set

    @classmethod
    def matches(cls, url: str) -> bool:
        return "hooks.slack.com" in (url or "")

    def deliver(self, body: bytes, files: list, event: str, payload: dict) -> None:
        token = (self.hook.get("slack_bot_token") or "").strip()
        channel = (self.hook.get("slack_channel") or "").strip()

        if files and token and channel:
            try:
                self._upload_files(files, token, channel, payload)
                return
            except Exception as e:
                # Don't lose the whole notification over an attachment
                # problem (missing files:write scope, bot not in the channel,
                # bad channel id, …). Log the cause and fall back to the text
                # post so the conception still notifies — same graceful
                # degradation as the no-token case below.
                log.warning(
                    "Slack file upload failed (%s) — falling back to a text "
                    "post for %s", e, self.hook.get("url", ""),
                )
        elif files:
            log.info(
                "Slack attachments need slack_bot_token + slack_channel "
                "(files.uploadV2) — posting text only to %s",
                self.hook.get("url", ""),
            )
        # Text path: the Incoming Webhook URL takes the rendered JSON body.
        self._post(self.hook["url"], base_headers(self.hook), body)

    # ----- files.uploadV2 (3-step) ------------------------------------------

    def _upload_files(self, files: list, token: str, channel: str,
                      payload: dict) -> None:
        """Upload each file's bytes, then complete the upload into the channel
        with an initial comment. Raises on any Slack ``ok: false`` so the
        caller's error handler logs the reason."""
        comment = (payload.get("summary") or "").strip()
        action_url = (payload.get("action_url") or "").strip()
        if action_url:
            comment = (comment + "\n" + action_url).strip()

        completed = []
        for fa in files:
            try:
                blob = fa.read()
            except Exception as e:
                log.warning("Slack: could not read %s: %s", fa.filename, e)
                continue
            # 1) reserve an upload URL for this file
            up = self._api_form("files.getUploadURLExternal", token, {
                "filename": fa.filename,
                "length": str(len(blob)),
            })
            upload_url = up.get("upload_url")
            file_id = up.get("file_id")
            if not upload_url or not file_id:
                raise RuntimeError("Slack getUploadURLExternal: no upload_url/file_id")
            # 2) PUT/POST the raw bytes to the one-time upload URL
            req = urllib.request.Request(
                upload_url, data=blob,
                headers={"Content-Type": "application/octet-stream"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=30)
            completed.append({"id": file_id, "title": fa.filename})

        if not completed:
            raise RuntimeError("Slack: no files were uploadable")

        # 3) complete — attaches the files to the channel with the comment
        self._api_json("files.completeUploadExternal", token, {
            "files": completed,
            "channel_id": channel,
            "initial_comment": comment,
        })

    # ----- Slack Web API helpers --------------------------------------------

    def _api_form(self, method: str, token: str, fields: dict) -> dict:
        body = urllib.parse.urlencode(fields).encode("utf-8")
        return self._api_call(method, token, body,
                              "application/x-www-form-urlencoded")

    def _api_json(self, method: str, token: str, obj: dict) -> dict:
        return self._api_call(method, token, json.dumps(obj).encode("utf-8"),
                              "application/json; charset=utf-8")

    def _api_call(self, method: str, token: str, body: bytes,
                  content_type: str) -> dict:
        req = urllib.request.Request(
            _SLACK_API + method, data=body, method="POST",
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": content_type,
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            parsed = json.loads(resp.read().decode("utf-8"))
        if not parsed.get("ok"):
            # Surface needed/provided on scope errors — the single most useful
            # debugging signal (shows what the *live token* actually carries,
            # which is often stale relative to the app's configured scopes
            # until the app is reinstalled).
            detail = ""
            if parsed.get("needed") or parsed.get("provided"):
                detail = (f" (needed: {parsed.get('needed', '?')}; "
                          f"provided: {parsed.get('provided', '?')})")
            raise RuntimeError(
                f"Slack {method} failed: "
                f"{parsed.get('error', 'unknown error')}{detail}"
            )
        return parsed
