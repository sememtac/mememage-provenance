"""Generic webhook notifier — the fallback for any unrecognized destination.

Plain JSON ``POST`` of the rendered body. A generic endpoint has no file API
we can know about, so ``attach_files`` can't ship bytes here — the body should
carry links instead (``{{url}}`` for the soul, ``{{distribution}}`` for every
surface). We log once when files were requested so the operator understands
why nothing was uploaded, rather than failing silently.
"""
from __future__ import annotations

from mememage.notifiers import Notifier, base_headers, log, register


@register
class GenericNotifier(Notifier):
    TYPE = "generic"
    DISPLAY_NAME = "Generic webhook"
    SUPPORTS_ATTACHMENTS = False

    def deliver(self, body: bytes, files: list, event: str, payload: dict) -> None:
        if files:
            log.info(
                "Generic notifier (%s) can't upload file bytes — posting body "
                "only. Put {{url}} / {{distribution}} in the template for links.",
                self.hook.get("url", ""),
            )
        self._post(self.hook["url"], base_headers(self.hook), body)
