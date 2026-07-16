"""Internet Archive cleanup operations — search, darken, purge.

Used by both the standalone CLI (``tools/purge-ia-test-items.py``)
and the dashboard's Config tab. Both surfaces import the same
functions so the behavior matches exactly.

Why "darken" vs "purge":
  * IA's namespace is permanent for users — once you've uploaded
    under ``mememage-XXXX`` the identifier is yours forever.
    Delete via the IA web UI removes public files but the slot
    stays tombstoned. Only IA staff can free a namespace.
  * The most we can do is hide test items so they don't pollute
    public listings. Two approaches:
      - **Darken**: PATCH metadata to add ``noindex:true``. Item
        vanishes from IA search but is still technically present.
        Recommended for tidiness.
      - **Purge**: DELETE every file in the item via S3 API. Bucket
        becomes empty (still tombstoned). More destructive but
        clears storage.
  * Both leave the identifier permanently tombstoned. Future mints
    compute fresh 12-hex identifiers and don't collide.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from mememage.net import default_https_context


log = logging.getLogger(__name__)

IA_SEARCH_URL = "https://archive.org/advancedsearch.php"
IA_METADATA_URL = "https://archive.org/metadata"
IA_S3_URL = "https://s3.us.archive.org"


def read_credentials() -> tuple[str, str]:
    """Read IA S3 keys from env (with .env fallback). Returns
    ``(access, secret)`` — either or both may be empty strings if
    not configured.
    """
    access = os.environ.get("IA_ACCESS_KEY", "")
    secret = os.environ.get("IA_SECRET_KEY", "")
    if access and secret:
        return access, secret
    try:
        from mememage.config import _load_dotenv
        _load_dotenv()
    except Exception:
        pass
    return os.environ.get("IA_ACCESS_KEY", ""), os.environ.get("IA_SECRET_KEY", "")


def search_items(uploader: str | None = None,
                 collection: str | None = None,
                 pattern: str = "mememage-*",
                 limit: int = 500,
                 page_size: int = 100) -> list[dict]:
    """Return matching items from IA's advanced search.

    Lucene-style query: identifier matches the pattern, optionally
    scoped to a collection and/or uploader. Pages until ``limit`` is
    reached or results exhaust. Each result dict carries at least
    ``identifier``, ``title``, ``uploader``, ``collection``,
    ``publicdate``, ``item_size`` (when IA returns them).
    """
    parts = [f"identifier:{pattern}"]
    if uploader:
        parts.append(f"uploader:{uploader}")
    if collection:
        parts.append(f"collection:{collection}")
    query = " AND ".join(parts)

    items: list[dict] = []
    page = 1
    while len(items) < limit:
        params = {
            "q": query,
            "fl[]": "identifier,title,uploader,collection,publicdate,item_size",
            "rows": page_size,
            "page": page,
            "output": "json",
        }
        url = f"{IA_SEARCH_URL}?{urllib.parse.urlencode(params, doseq=True)}"
        with urllib.request.urlopen(url, timeout=30, context=default_https_context()) as resp:
            data = json.load(resp)
        docs = (data.get("response") or {}).get("docs") or []
        if not docs:
            break
        items.extend(docs)
        if len(docs) < page_size:
            break
        page += 1
    return items[:limit]


def list_files(identifier: str) -> list[str]:
    """Return the names of "original" source files inside an IA item.

    Format-derived files (thumbnails, OCR, etc.) are excluded — they
    cascade-delete when the originals go. Returns an empty list if
    the item is tombstoned (403) or missing (404/503).
    """
    url = f"{IA_METADATA_URL}/{identifier}"
    try:
        with urllib.request.urlopen(url, timeout=30, context=default_https_context()) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code in (403, 404, 503):
            return []
        raise
    files = data.get("files") or []
    return [f["name"] for f in files if f.get("source") == "original"]


def darken_item(identifier: str, access: str, secret: str) -> dict:
    """PATCH an item's metadata to set ``noindex:true``. Returns
    ``{"ok": bool, "error": str}``. Idempotent: re-darkening a
    darkened item succeeds without effect.
    """
    target = f"{IA_METADATA_URL}/{identifier}"
    patch = json.dumps([
        {"op": "add", "path": "/noindex", "value": "true"},
    ])
    body = urllib.parse.urlencode({
        "-target": "metadata",
        "-patch": patch,
        "access": access,
        "secret": secret,
    }).encode("utf-8")
    req = urllib.request.Request(target, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=60, context=default_https_context()) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        if payload.get("success"):
            return {"ok": True, "error": ""}
        return {"ok": False, "error": str(payload)}
    except urllib.error.HTTPError as e:
        body_str = e.read().decode("utf-8", errors="replace")[:300]
        return {"ok": False, "error": f"HTTP {e.code} — {body_str}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def delete_files(identifier: str, access: str, secret: str,
                 *, throttle_sec: float = 0.1) -> dict:
    """DELETE every original file in an IA item via the S3 API.

    Returns ``{"deleted": int, "failed": int, "errors": list[str],
    "files": int}``. ``files`` is the count discovered (so the
    dashboard can show "deleted 3 of 3" or "0 of 0 — nothing to
    purge"). The item bucket itself remains as a tombstone.
    """
    files = list_files(identifier)
    out = {"files": len(files), "deleted": 0, "failed": 0, "errors": []}
    for name in files:
        url = f"{IA_S3_URL}/{identifier}/{urllib.parse.quote(name)}"
        req = urllib.request.Request(url, method="DELETE")
        req.add_header("Authorization", f"LOW {access}:{secret}")
        req.add_header("x-archive-cascade-delete", "1")
        try:
            urllib.request.urlopen(req, timeout=60, context=default_https_context())
            out["deleted"] += 1
        except urllib.error.HTTPError as e:
            body_str = e.read().decode("utf-8", errors="replace")[:200]
            out["failed"] += 1
            out["errors"].append(f"{name}: HTTP {e.code} — {body_str}")
        except Exception as e:
            out["failed"] += 1
            out["errors"].append(f"{name}: {e}")
        if throttle_sec > 0:
            time.sleep(throttle_sec)
    return out
