"""Zenodo mirror — upload metadata records for geographic/institutional redundancy.

Zenodo is hosted by CERN and backed by the European Commission. Mirroring
Mememage records there provides a second permanent store independent of the
Internet Archive.

The Zenodo upload is always non-fatal: callers should catch exceptions and
log warnings rather than aborting the primary IA upload path.
"""

import json
import logging
import urllib.error
import urllib.request

from mememage.config import get_zenodo_config
from mememage.net import urlopen_with_retry

log = logging.getLogger(__name__)


def upload_to_zenodo(identifier: str, record: dict) -> str | None:
    """Upload a metadata record to Zenodo as a mirror.

    Returns the DOI string on success, or None if Zenodo is not configured
    (no ZENODO_ACCESS_TOKEN set).

    Raises RuntimeError on permanent API failure.
    """
    api_url, token = get_zenodo_config()
    if api_url is None:
        return None

    # 1. Create empty deposition
    deposition_id, bucket_url = _create_deposition(api_url, token)

    # 2. Upload metadata.json into the deposition's file bucket
    payload = json.dumps(record, indent=2).encode("utf-8")
    _upload_file(bucket_url, token, "metadata.json", payload)

    # 3. Set deposition metadata and publish
    doi = _set_metadata_and_publish(api_url, token, deposition_id, identifier)
    return doi


def _create_deposition(api_url: str, token: str) -> tuple[int, str]:
    """Create an empty Zenodo deposition. Returns (deposition_id, bucket_url)."""
    url = f"{api_url}/api/deposit/depositions"
    req = urllib.request.Request(
        url,
        data=b"{}",
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    body = urlopen_with_retry(req)
    resp = json.loads(body.decode("utf-8"))
    return resp["id"], resp["links"]["bucket"]


def _upload_file(bucket_url: str, token: str, filename: str, data: bytes):
    """Upload a file to a deposition's bucket."""
    url = f"{bucket_url}/{filename}"
    req = urllib.request.Request(
        url,
        data=data,
        method="PUT",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/octet-stream",
        },
    )
    urlopen_with_retry(req)


def _set_metadata_and_publish(
    api_url: str, token: str, deposition_id: int, identifier: str
) -> str:
    """Set deposition metadata and publish. Returns the DOI."""
    # Set metadata
    meta_payload = json.dumps({
        "metadata": {
            "title": f"Mememage metadata for {identifier}",
            "upload_type": "dataset",
            # Published verbatim on Zenodo. Mememage is provenance for ANY
            # image — a render, a photo, a screenshot, a scan — so this text
            # names no source. It also names no "primary" surface: the soul
            # carries the identifier + content hash and verifies by hash from
            # wherever a reader found it (Zenodo included).
            "description": (
                f"Provenance record for image {identifier}. "
                f"Part of the Mememage format — a steganographic provenance "
                f"system that encodes an identifier and a content hash into "
                f"an image's pixels. The record is tamper-evident: recomputing "
                f"its hash and comparing against the hash in the pixels proves "
                f"the two belong together, from any source."
            ),
            "creators": [{"name": "Mememage"}],
            "keywords": ["mememage", "image", "provenance", "tamper-evident",
                         "content-hash", identifier],
        }
    }).encode("utf-8")

    meta_url = f"{api_url}/api/deposit/depositions/{deposition_id}"
    req = urllib.request.Request(
        meta_url,
        data=meta_payload,
        method="PUT",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    urlopen_with_retry(req)

    # Publish
    publish_url = f"{api_url}/api/deposit/depositions/{deposition_id}/actions/publish"
    req = urllib.request.Request(
        publish_url,
        data=b"",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
    )
    body = urlopen_with_retry(req)
    resp = json.loads(body.decode("utf-8"))
    return resp.get("doi", resp.get("conceptdoi", f"zenodo-{deposition_id}"))
