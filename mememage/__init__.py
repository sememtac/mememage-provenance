"""Mememage — a bar in an image's pixels points to a record you control.

The core API is three functions — ``encode`` (write a bar + build the record),
``decode`` (read the bar, optionally resolve + verify), ``verify`` (image vs
record). Everything else (``mint`` + the celestial/GPS/chain/IA machinery) is the
canonical-chain reference implementation — a provenance demo built on top.
"""

from mememage.core import (
    ConceptionState,
    compute_content_hash,
    compute_identifier,
    fetch_metadata,
    upload_metadata,
    verify_metadata,
)
from mememage.api import (
    Bar, Record, Verification, decode, encode, is_encrypted, unlock, verify,
)
from mememage.mint import MintResult, mint

__all__ = [
    # Core API
    "encode",
    "decode",
    "verify",
    "unlock",
    "is_encrypted",
    "Record",
    "Bar",
    "Verification",
    # Canonical-chain reference implementation + lower-level helpers
    "mint",
    "MintResult",
    "ConceptionState",
    "upload_metadata",
    "fetch_metadata",
    "compute_identifier",
    "compute_content_hash",
    "verify_metadata",
]
