"""Internet Archive S3 API configuration and shared constants."""

import os
from pathlib import Path

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

IA_S3_URL = "https://s3.us.archive.org"
IA_DOWNLOAD_URL = "https://archive.org/download"
# Metadata API — the authoritative existence/state check. Download URL
# alone returns 404 for darkened items, masking the tombstone. The
# metadata endpoint reports {} for never-existed identifiers and
# {"is_dark": true, ...} for ones held in dark/tombstone state. Always
# probe this before any PUT — see _identifier_exists in core.py.
IA_METADATA_URL = "https://archive.org/metadata"

# Built-in nested-cycle defaults — fallback cycle parameters for the reference
# implementation's seal/advance, used only when a seal predates the per-chain
# config. A chain's own chain.json drives its real shape (layer count, cycle
# lengths, pinned positions); these are just the demo values, derived so each
# cycle tiles the outer cycle with a whole-cycle remainder.
DECODER_CHUNKS = 12       # inner cycle: chunks per full inner cycle
DECODER_RANGE = 360       # inner cycle active for positions 0..359 (30 × 12)
TRUTH_CHUNKS = 365        # outer cycle: one text fragment per position
OUTER_CYCLE = 365         # outer cycle length
PROOF_CHUNKS = 6          # secondary cycle: 6 data chunks + 1 reserved slot
PROOF_CYCLE = 7           # secondary cycle length including the reserved slot
PROOF_RANGE = 364         # secondary cycle active for positions 0..363 (52 × 7)


def _load_dotenv():
    """Load key=value pairs from .env into os.environ (if not already set)."""
    if not _ENV_FILE.exists():
        return
    for line in _ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


def get_ia_keys() -> tuple[str, str]:
    """Return (access_key, secret_key) from .env file or environment variables.

    Get keys at: https://archive.org/account/s3.php
    """
    _load_dotenv()
    access = os.environ.get("IA_ACCESS_KEY", "")
    secret = os.environ.get("IA_SECRET_KEY", "")
    if not access or not secret:
        raise RuntimeError(
            "Missing Internet Archive API keys. "
            "Set IA_ACCESS_KEY and IA_SECRET_KEY environment variables. "
            "Register at https://archive.org/account/s3.php"
        )
    return access, secret


def get_zenodo_config() -> tuple[str | None, str | None]:
    """Return (api_url, access_token) for Zenodo, or (None, None) if not configured.

    Set ZENODO_ACCESS_TOKEN in .env or environment to enable mirroring.
    Set ZENODO_SANDBOX=true to use sandbox.zenodo.org for testing.
    Get a token at: https://zenodo.org/account/settings/applications/tokens/new/
    """
    _load_dotenv()
    token = os.environ.get("ZENODO_ACCESS_TOKEN")
    if not token:
        return None, None
    sandbox = os.environ.get("ZENODO_SANDBOX", "false").lower() == "true"
    api_url = "https://sandbox.zenodo.org" if sandbox else "https://zenodo.org"
    return api_url, token
