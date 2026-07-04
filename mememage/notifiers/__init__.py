"""Pluggable event notifiers — generalized webhook delivery.

Parallel to the channels framework, but for ephemeral EVENTS rather than the
durable, content-addressed soul. Each messaging platform (Discord, Slack, a
plain webhook, …) is one adapter file that owns *how it shapes the HTTP
request* — crucially including how (or whether) it attaches files.

The core (``server._fire_webhooks``) renders a platform-agnostic JSON body
from the hook's template, resolves the right adapter for the destination, and
hands off ``(body, files, event, payload)``. No core code names a platform;
adding one is a new file under this package plus ``@register``. If a platform
falls out of favour you delete its file — the framework doesn't bet on any
single messenger staying relevant.

Resolution: a hook may set an explicit ``kind`` (``"discord"``/``"slack"``/
``"generic"``); otherwise the adapter is auto-detected from the URL via each
adapter's ``matches()``. Unrecognized destinations fall back to the generic
plain-JSON notifier.
"""
from __future__ import annotations

import logging
import os
import urllib.request
from typing import Optional, Type

log = logging.getLogger(__name__)

# Default User-Agent. Python's stdlib urllib sends ``Python-urllib/3.x``,
# which Cloudflare (in front of Discord, among others) treats as a script
# signature and 403s before the request reaches the real API. The Discord-
# recommended bot UA shape sails through; per-hook ``headers`` can override.
DEFAULT_UA = "DiscordBot (https://github.com/sememtac/Mememage, 1.0)"

_REGISTRY: dict[str, Type["Notifier"]] = {}


def register(cls: Type["Notifier"]) -> Type["Notifier"]:
    """Class decorator: announce a notifier's ``TYPE`` to the registry."""
    if not cls.TYPE:
        raise ValueError(f"Notifier {cls.__name__} must set TYPE")
    _REGISTRY[cls.TYPE] = cls
    return cls


_plugins_loaded = False


def _ensure_plugins_loaded() -> None:
    """Import the built-in adapters so they register. Lazy + idempotent."""
    global _plugins_loaded
    if _plugins_loaded:
        return
    # Importing each module runs its @register decorator.
    from mememage.notifiers import discord, slack, telegram, webhook  # noqa: F401
    _plugins_loaded = True


def base_headers(hook: dict) -> dict:
    """Default request headers for a hook (UA + JSON content-type), with the
    hook's own ``headers`` layered on top so a destination-specific
    Authorization/User-Agent wins."""
    headers = {"Content-Type": "application/json", "User-Agent": DEFAULT_UA}
    headers.update(hook.get("headers") or {})
    return headers


class FileAttachment:
    """One file to attach to an event (the minted image or the .soul).

    Carries both a local ``path`` (for adapters that upload bytes, e.g.
    Discord multipart / Slack files.uploadV2) and a public ``url`` (for
    adapters that can only reference, not upload). Bytes are read lazily.
    """

    def __init__(self, role: str, filename: str, content_type: str,
                 path: Optional[str] = None, url: str = ""):
        self.role = role            # "image" | "soul"
        self.filename = filename
        self.content_type = content_type
        self.path = path
        self.url = url

    def read(self) -> bytes:
        with open(self.path, "rb") as f:
            return f.read()


def build_attachments(data: dict) -> list:
    """Resolve the image + soul attachments from event data. Generic — every
    adapter consumes the same descriptors and decides what to do with them.
    Only returns files that actually exist on disk."""
    out: list[FileAttachment] = []
    ident = data.get("identifier") or "mememage"
    image_path = data.get("image_path")
    if image_path and os.path.isfile(image_path):
        out.append(FileAttachment(
            "image", f"{ident}.png", "image/png",
            path=image_path, url=data.get("image_url", ""),
        ))
    soul_path = data.get("soul_path")
    if soul_path and os.path.isfile(soul_path):
        out.append(FileAttachment(
            "soul", f"{ident}.soul", "application/json",
            path=soul_path, url=data.get("url", ""),
        ))
    return out


class Notifier:
    """Base adapter. Subclasses set TYPE/DISPLAY_NAME, optionally override
    ``matches`` (URL auto-detect) and ``deliver`` (the actual request)."""

    TYPE: str = ""
    DISPLAY_NAME: str = ""
    SUPPORTS_ATTACHMENTS: bool = False

    def __init__(self, hook: dict):
        self.hook = hook or {}

    @classmethod
    def matches(cls, url: str) -> bool:
        """Auto-detect: does this adapter own the given destination URL?"""
        return False

    def deliver(self, body: bytes, files: list, event: str, payload: dict) -> None:
        """Send the event. ``body`` is the rendered JSON bytes; ``files`` is a
        (possibly empty) FileAttachment list already gated on the hook's
        ``attach_files`` + the event being ``conceived``. May raise on failure;
        the caller logs. Default: plain JSON POST, no attachments."""
        self._post(self.hook["url"], base_headers(self.hook), body)

    @staticmethod
    def _post(url: str, headers: dict, body: bytes, timeout: int = 15):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        return urllib.request.urlopen(req, timeout=timeout)


def resolve(hook: dict) -> "Notifier":
    """Pick the adapter for a hook: explicit ``kind`` wins, else URL
    auto-detect, else the generic plain-JSON notifier."""
    _ensure_plugins_loaded()
    kind = (hook.get("kind") or "").strip().lower()
    if kind and kind in _REGISTRY:
        return _REGISTRY[kind](hook)
    url = hook.get("url", "") or ""
    for type_name, cls in _REGISTRY.items():
        if type_name == "generic":
            continue
        try:
            if cls.matches(url):
                return cls(hook)
        except Exception:
            continue
    generic = _REGISTRY.get("generic", Notifier)
    return generic(hook)
