"""Internet Archive channel — primary distribution for Mememage souls.

Free, permanent (since 1996), public, simple S3 API. Uploads the soul
twice under the same identifier: ``.soul`` for humans and ``.json``
for browser CORS fetches (IA sends ``Access-Control-Allow-Origin``
for JSON but not for arbitrary extensions).

Raises :class:`NamespaceBlocked` when IA returns 403 with "taken
offline" in the body — that's IA's signal that an admin has blocked
the specific identifier. The conception pipeline catches it,
regenerates the identifier with fresh entropy, and replays the
content_hash + signature + encryption steps before re-firing this
channel.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from mememage.channels import Channel, NamespaceBlocked, register
from mememage.config import IA_DOWNLOAD_URL, IA_S3_URL
from mememage.net import urlopen_with_retry

log = logging.getLogger(__name__)


@register
class InternetArchiveChannel(Channel):
    TYPE = "internet_archive"
    DISPLAY_NAME = "Internet Archive"
    # IA owns the mememage-XXXX bucket namespace. A blocked/taken-offline
    # identifier surfaces as 403 on PUT (NamespaceBlocked), at which point
    # blast() must regenerate before any other channel commits. The
    # two-phase dispatcher in channels/__init__.blast keys off this flag
    # to run IA first, alone, so phase-2 channels don't leave orphans.
    NAMESPACE_AUTHORITY = True
    CREDENTIAL_FIELDS = [
        {
            "name": "access_key",
            "label": "Access key",
            "env_var": "IA_ACCESS_KEY",
            "secret": False,
            "help": "Get it at https://archive.org/account/s3.php",
        },
        {
            "name": "secret_key",
            "label": "Secret key",
            "env_var": "IA_SECRET_KEY",
            "secret": True,
            "help": "Same page — keep this secret.",
        },
    ]
    CONFIG_FIELDS = [
        {
            "name": "collection",
            "label": "IA collection",
            "default": "opensource",
            "help": "Most users leave this on opensource.",
        },
    ]

    def upload(self, identifier: str, soul_bytes: bytes,
               image_path: str | None = None) -> str:
        access_key = self._read_credential("access_key")
        secret_key = self._read_credential("secret_key")
        collection = self.config.get("collection") or "opensource"

        soul_filename = f"{identifier}.soul"
        json_filename = f"{identifier}.json"
        url = f"{IA_S3_URL}/{identifier}/{soul_filename}"

        req = urllib.request.Request(url, data=soul_bytes, method="PUT")
        req.add_header("x-amz-auto-make-bucket", "1")
        req.add_header("x-archive-meta-mediatype", "data")
        req.add_header("x-archive-meta-collection", collection)
        req.add_header("x-archive-meta-title", f"Mememage soul for {identifier}")
        req.add_header("authorization", f"LOW {access_key}:{secret_key}")
        req.add_header("Content-Type", "application/json")

        try:
            urlopen_with_retry(req)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            # Admin-blocked identifier on IA: surface to caller so the
            # pipeline can regenerate the identifier and replay the
            # downstream hash/sign/encrypt steps. Without this signal
            # we'd fail-and-give-up on a fixable condition.
            if e.code == 403 and "taken offline" in body.lower():
                raise NamespaceBlocked(
                    f"IA blocked identifier {identifier}: {body[:200]}"
                ) from e
            raise RuntimeError(
                f"Internet Archive upload failed (HTTP {e.code}): {body[:500]}"
            ) from e

        # CORS-friendly .json mirror — non-fatal. The browser decoder
        # probes ``.soul`` first and falls back to ``.json`` because
        # IA only sends ``Access-Control-Allow-Origin`` for known
        # MIME-mapped extensions.
        json_url = f"{IA_S3_URL}/{identifier}/{json_filename}"
        json_req = urllib.request.Request(json_url, data=soul_bytes, method="PUT")
        json_req.add_header("authorization", f"LOW {access_key}:{secret_key}")
        json_req.add_header("Content-Type", "application/json")
        try:
            urlopen_with_retry(json_req)
        except Exception:
            log.warning("IA .json mirror upload failed (non-fatal) — .soul is primary")

        # Full minted image — LIGHT-ENERGY chains ONLY. IA is permanent + public
        # (an item can be darkened but never truly released), so a dark-matter
        # chain NEVER gets its image uploaded — that would publish the sealed
        # image irreversibly. Non-fatal: the soul is primary; the image is a bonus
        # that makes the conception publicly viewable + decodable straight from IA
        # (archive.org/download/<id>/<id>.png).
        if image_path:
            try:
                visibility = int(json.loads(soul_bytes).get("chain_visibility", 0))
            except Exception:
                visibility = 0
            if visibility == 0:   # 0 == light_energy (public); 1 == dark_matter
                try:
                    with open(image_path, "rb") as f:
                        img_bytes = f.read()
                    img_url = f"{IA_S3_URL}/{identifier}/{identifier}.png"
                    img_req = urllib.request.Request(img_url, data=img_bytes, method="PUT")
                    img_req.add_header("authorization", f"LOW {access_key}:{secret_key}")
                    img_req.add_header("Content-Type", "image/png")
                    img_req.add_header("x-archive-meta-mediatype", "image")
                    urlopen_with_retry(img_req)
                except Exception as e:
                    log.warning("IA image upload failed (non-fatal) — soul is primary: %s", e)

        # The canonical URL pattern that decoders fetch. Stable
        # across reupload + admin-side metadata edits.
        return f"{IA_DOWNLOAD_URL}/{identifier}/{soul_filename}"

    def exists(self, identifier: str) -> bool:
        """IA namespace probe — alive OR darkened/tombstoned = taken.

        Delegates to the canonical metadata-API parse in core so the
        tombstone-aware three-state logic ({} free / is_dark held /
        else alive) lives in exactly one place. IA tombstones an
        identifier forever, so a darkened slot still counts as taken —
        that's the case that bit us before the metadata-API switch.
        """
        from mememage.core import _identifier_exists
        return _identifier_exists(identifier)

    def upload_keychain(self, chain_id: str, filename: str,
                        record_bytes: bytes) -> str:
        """Upload a keychain record (succession / revocation / alias) to
        IA. The keychain is its own IA item, keyed by
        ``mememage-keychain-<fingerprint>``; each record (succession.json,
        revocation.json, alias-<fp>.json) lives as a file under that item.
        Same auth + collection metadata as soul uploads.
        """
        access_key = self._read_credential("access_key")
        secret_key = self._read_credential("secret_key")
        collection = self.config.get("collection") or "opensource"

        url = f"{IA_S3_URL}/{chain_id}/{filename}"
        req = urllib.request.Request(url, data=record_bytes, method="PUT")
        req.add_header("x-amz-auto-make-bucket", "1")
        req.add_header("x-archive-meta-mediatype", "data")
        req.add_header("x-archive-meta-collection", collection)
        req.add_header("x-archive-meta-title", f"Mememage keychain for {chain_id}")
        req.add_header("authorization", f"LOW {access_key}:{secret_key}")
        req.add_header("Content-Type", "application/json")

        try:
            urlopen_with_retry(req)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"IA keychain upload failed (HTTP {e.code}): {body[:500]}"
            ) from e

        return f"{IA_DOWNLOAD_URL}/{chain_id}/{filename}"

    # ---- cleanup surface --------------------------------------------------
    # IA implements all three operations defined on the Channel base.
    # Each delegates to mememage.ia_admin for the network primitives so
    # the standalone CLI (tools/channel-cleanup.py) and the dashboard
    # both hit the same code path.

    def search(self, *, pattern: str = "mememage-*", limit: int = 200,
               uploader: str | None = None, collection: str | None = None,
               **_filters) -> list[dict]:
        from mememage.ia_admin import search_items
        items = search_items(
            uploader=uploader,
            collection=(collection or self.config.get("collection") or None),
            pattern=pattern,
            limit=limit,
        )
        # Decorate with a clickable URL — the search API returns
        # identifier only; the dashboard wants a link.
        for it in items:
            ident = it.get("identifier") or ""
            it.setdefault("url", f"https://archive.org/details/{ident}")
        return items

    def hide(self, identifier: str) -> dict:
        from mememage.ia_admin import darken_item
        access = self._read_credential("access_key")
        secret = self._read_credential("secret_key")
        if not (access and secret):
            return {"ok": False, "error": "IA credentials not configured"}
        return darken_item(identifier, access, secret)

    def purge(self, identifier: str) -> dict:
        from mememage.ia_admin import delete_files
        access = self._read_credential("access_key")
        secret = self._read_credential("secret_key")
        if not (access and secret):
            return {"ok": False, "error": "IA credentials not configured",
                    "deleted": 0, "failed": 0, "files": 0, "errors": []}
        r = delete_files(identifier, access, secret)
        # Normalize: callers want a top-level ok bool. IA considers the
        # operation successful when no files failed (zero-file items
        # also count as successes — there was nothing to delete).
        r["ok"] = r["failed"] == 0
        return r
