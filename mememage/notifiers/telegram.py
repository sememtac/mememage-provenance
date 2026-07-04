"""Telegram notifier — Bot API (sendMessage / sendPhoto / sendDocument).

Telegram doesn't use a per-target incoming-webhook URL like Discord/Slack.
Instead a bot token authenticates (it lives in the path:
``api.telegram.org/bot<token>/<method>``) and a ``chat_id`` names the
destination. So the framework's URL is the constant ``https://api.telegram.org``
(routing identity, no secret), and the token + chat id are per-hook config:
``telegram_bot_token`` (secret) + ``telegram_chat_id``.

Body: the rendered template is JSON like the others — the Telegram preset is
``{"text": "…"}`` — and this adapter pulls ``text`` out and injects ``chat_id``.
With no template it falls back to the event's summary + action_url.

Attachments ride Telegram's multipart methods: the image goes via
``sendPhoto`` (with the message as the caption), the .soul via
``sendDocument``.
"""
from __future__ import annotations

import json
import re
import secrets
import urllib.request

from mememage.notifiers import Notifier, log, register

_TELEGRAM_HOST = "api.telegram.org"


@register
class TelegramNotifier(Notifier):
    TYPE = "telegram"
    DISPLAY_NAME = "Telegram"
    SUPPORTS_ATTACHMENTS = True

    @classmethod
    def matches(cls, url: str) -> bool:
        return _TELEGRAM_HOST in (url or "")

    def deliver(self, body: bytes, files: list, event: str, payload: dict) -> None:
        chat_id = (self.hook.get("telegram_chat_id") or "").strip()
        if not chat_id:
            raise RuntimeError("Telegram: telegram_chat_id is required")
        text, extra = self._message_text(body, payload)

        if files:
            self._send_files(chat_id, text, files, extra)
        else:
            self._call("sendMessage", dict(extra, chat_id=chat_id, text=text))

    # ----- message text -----------------------------------------------------

    def _message_text(self, body: bytes, payload: dict):
        """Pull the message text out of the rendered body. The Telegram preset
        is ``{"text": "…"}``; parse it and keep any extra Telegram params
        (parse_mode, disable_web_page_preview, …). Fall back to summary +
        action_url when there's no usable text."""
        text, extra = "", {}
        try:
            obj = json.loads(body.decode("utf-8"))
            if isinstance(obj, dict):
                text = obj.get("text") or obj.get("content") or ""
                extra = {k: v for k, v in obj.items()
                         if k not in ("text", "content", "chat_id")}
            else:
                text = body.decode("utf-8")
        except Exception:
            text = body.decode("utf-8", errors="replace")
        if not text:
            summary = (payload.get("summary") or "").strip()
            url = (payload.get("action_url") or "").strip()
            text = (summary + ("\n" + url if url else "")).strip()
        return text, extra

    # ----- transport --------------------------------------------------------

    def _base(self) -> str:
        token = (self.hook.get("telegram_bot_token") or "").strip()
        if token:
            return f"https://{_TELEGRAM_HOST}/bot{token}"
        # Fall back to a token embedded in the URL (.../bot<token>/sendMessage).
        url = (self.hook.get("url") or "").rstrip("/")
        m = re.match(rf"(https://{re.escape(_TELEGRAM_HOST)}/bot[^/]+)", url)
        if m:
            return m.group(1)
        raise RuntimeError("Telegram: no bot token (set telegram_bot_token)")

    def _call(self, method: str, obj: dict) -> dict:
        body = json.dumps(obj).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base()}/{method}", data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            parsed = json.loads(resp.read().decode("utf-8"))
        if not parsed.get("ok"):
            raise RuntimeError(
                f"Telegram {method} failed: "
                f"{parsed.get('description', 'unknown error')}"
            )
        return parsed

    def _send_files(self, chat_id: str, caption: str, files: list, extra: dict) -> None:
        """Image → sendPhoto (carries the message as its caption); soul →
        sendDocument. One unreadable file is logged, not fatal — as long as
        something sends."""
        sent = False
        for fa in files:
            try:
                blob = fa.read()
            except Exception as e:
                log.warning("Telegram: could not read %s: %s", fa.filename, e)
                continue
            if fa.role == "image":
                self._call_multipart(
                    "sendPhoto",
                    dict(extra, chat_id=chat_id, caption=caption),
                    "photo", fa.filename, fa.content_type, blob,
                )
            else:
                self._call_multipart(
                    "sendDocument",
                    {"chat_id": chat_id},
                    "document", fa.filename, fa.content_type, blob,
                )
            sent = True
        if not sent:
            # Nothing uploadable — at least deliver the text.
            self._call("sendMessage", dict(extra, chat_id=chat_id, text=caption))

    def _call_multipart(self, method: str, fields: dict, file_field: str,
                        filename: str, content_type: str, blob: bytes) -> None:
        boundary = "----mememage" + secrets.token_hex(12)
        crlf = b"\r\n"
        parts: list[bytes] = []
        for k, v in fields.items():
            parts.append(("--" + boundary).encode())
            parts.append(
                (f'Content-Disposition: form-data; name="{k}"').encode())
            parts.append(b"")
            parts.append(str(v).encode("utf-8"))
        parts.append(("--" + boundary).encode())
        parts.append((
            f'Content-Disposition: form-data; name="{file_field}"; '
            f'filename="{filename}"').encode())
        parts.append(("Content-Type: " + content_type).encode())
        parts.append(b"")
        parts.append(blob)
        parts.append(("--" + boundary + "--").encode())
        data = crlf.join(parts) + crlf
        req = urllib.request.Request(
            f"{self._base()}/{method}", data=data, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            parsed = json.loads(resp.read().decode("utf-8"))
        if not parsed.get("ok"):
            raise RuntimeError(
                f"Telegram {method} failed: "
                f"{parsed.get('description', 'unknown error')}"
            )
