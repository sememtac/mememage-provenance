"""Discord notifier — multipart file attachments.

Discord accepts attachments as a ``multipart/form-data`` body with a
``payload_json`` field carrying the rendered message + ``files[N]`` fields
carrying the bytes. This is the original (and still default for Discord)
attachment path, lifted out of server.py into an adapter.
"""
from __future__ import annotations

import secrets
import urllib.request

from mememage.notifiers import Notifier, base_headers, log, register


@register
class DiscordNotifier(Notifier):
    TYPE = "discord"
    DISPLAY_NAME = "Discord"
    SUPPORTS_ATTACHMENTS = True

    @classmethod
    def matches(cls, url: str) -> bool:
        return "discord.com" in (url or "") or "discordapp.com" in (url or "")

    def deliver(self, body: bytes, files: list, event: str, payload: dict) -> None:
        if files:
            self._post_multipart(body, files)
        else:
            self._post(self.hook["url"], base_headers(self.hook), body)

    def _post_multipart(self, json_body_bytes: bytes, files: list) -> None:
        """payload_json + files[N] multipart, Discord's expected shape."""
        boundary = "----mememage" + secrets.token_hex(12)
        crlf = b"\r\n"
        parts: list[bytes] = []

        def add_part(content_disp: str, content_type: str, body: bytes) -> None:
            parts.append(("--" + boundary).encode())
            parts.append(("Content-Disposition: " + content_disp).encode())
            if content_type:
                parts.append(("Content-Type: " + content_type).encode())
            parts.append(b"")
            parts.append(body)

        add_part('form-data; name="payload_json"', "application/json", json_body_bytes)
        for i, fa in enumerate(files):
            try:
                add_part(
                    f'form-data; name="files[{i}]"; filename="{fa.filename}"',
                    fa.content_type,
                    fa.read(),
                )
            except Exception as e:  # one unreadable file shouldn't sink the post
                log.warning("Discord: could not attach %s: %s", fa.filename, e)

        parts.append(("--" + boundary + "--").encode())
        body = crlf.join(parts) + crlf

        # Multipart needs its own Content-Type (with boundary). Replace the
        # default JSON content-type; preserve everything else (Authorization,
        # User-Agent, …).
        headers = {k: v for k, v in base_headers(self.hook).items()
                   if k.lower() != "content-type"}
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        req = urllib.request.Request(
            self.hook["url"], data=body, headers=headers, method="POST"
        )
        urllib.request.urlopen(req, timeout=30)
