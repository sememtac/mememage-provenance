"""Zenodo channel — institutional mirror.

Thin wrapper around :func:`mememage.zenodo.upload_to_zenodo`, which
holds the deposition / file-upload / publish dance for Zenodo's REST
API. The wrapper exists so the channels framework has a uniform
entry point.

Dormant by default in ``channels.json`` (``enabled: false``). Flip
the toggle and configure ``ZENODO_ACCESS_TOKEN`` to activate.
"""

from __future__ import annotations

import json
import logging

from mememage.channels import Channel, register

log = logging.getLogger(__name__)


@register
class ZenodoChannel(Channel):
    TYPE = "zenodo"
    DISPLAY_NAME = "Zenodo"
    CREDENTIAL_FIELDS = [
        {
            "name": "access_token",
            "label": "Access token",
            "env_var": "ZENODO_ACCESS_TOKEN",
            "secret": True,
            "help": "Personal access token from https://zenodo.org/account/settings/applications/",
        },
    ]
    CONFIG_FIELDS = [
        {
            "name": "sandbox",
            "label": "Use sandbox",
            "default": False,
            "help": "Route uploads through sandbox.zenodo.org for testing.",
        },
    ]

    def upload(self, identifier: str, soul_bytes: bytes,
               image_path: str | None = None) -> str:
        # Zenodo's existing helper takes a record dict (it constructs
        # the metadata + file deposition itself). Re-parse the soul
        # bytes so callers don't have to pass both forms.
        from mememage.zenodo import upload_to_zenodo
        record = json.loads(soul_bytes.decode("utf-8"))
        doi = upload_to_zenodo(identifier, record)
        if not doi:
            # Treat "no token configured" as a soft skip — caller
            # already filtered on is_configured(), so reaching here
            # without a DOI is a real failure (API error, network).
            raise RuntimeError("Zenodo upload returned no DOI")
        # Zenodo's canonical URL is the DOI resolver — works
        # forever regardless of which Zenodo instance served it.
        if doi.startswith("10."):
            return f"https://doi.org/{doi}"
        # Fallback for sandbox-style or non-DOI returns
        return doi
