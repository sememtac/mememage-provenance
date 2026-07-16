"""Conception orchestrator — the single entry point for conceiving an image.

Conception is a conscious act: creator provides GPS, approves the image,
and the system produces a provenance-sealed artifact with a bar pointing
to its permanent metadata record on the Internet Archive.

Usage:
    from mememage.mint import mint

    result = mint(
        metadata={"prompt": "...", "seed": 42, "width": 1024, "height": 1024, ...},
        gps=(37.7749, -122.4194),
        image_path="/path/to/rendered.png",
    )
    # result.identifier  — "mememage-abc12345"
    # result.content_hash — "a1b2c3d4e5f6g7h8"
    # result.url — "https://archive.org/download/mememage-abc12345/metadata.json"
    # result.image_path — path to the bar-encoded image
"""

import logging
import os
from dataclasses import dataclass

import json
import urllib.request

from mememage.bar import embed_bar, extract_bar
from mememage.config import IA_DOWNLOAD_URL
from mememage.core import upload_metadata, _save_local_backup, soul_store_dir
from mememage.thumbnail import generate_thumbnail
from mememage.watermark import embed_watermark, PAYLOAD_BITS

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MintResult:
    identifier: str
    content_hash: str
    url: str
    image_path: str
    # {channel_id: url} from the channels blast — every enabled+
    # configured destination that successfully held the soul. The
    # ``url`` field above is the primary channel's URL (canonical
    # link in the bar / Discord); ``distribution`` is the full set
    # for surfaces that want to show every mirror.
    distribution: dict = None


def _patch_record(identifier, content_hash, patch):
    """Patch fields into an existing record + reblast through channels.

    Used by the post-mint thumbnail injection (and any future
    deferred patches). Loads the local backup, applies the patch,
    saves the local backup, then reblasts the updated soul through
    every enabled channel so all peer mirrors stay in sync.

    Local backup save happens FIRST and unconditionally — any
    channel-side failure (IA down, peer offline, CORS, etc.) still
    leaves the thumbnail recorded locally. Without this ordering,
    a single channel hiccup loses the thumbnail entirely.

    The ``content_hash`` arg is retained for backward compatibility
    with older callers that included a hash in the filename; modern
    filenames are just ``{identifier}.soul``.
    """
    import os
    from mememage import chains, channels as _channels

    soul_filename = f"{identifier}.soul"

    # 1. Find the record locally first (most reliable source) — the flat store.
    records_dir = soul_store_dir()
    record = None
    backup_paths = [
        records_dir / soul_filename,
    ]
    if content_hash:
        backup_paths.insert(1, records_dir / f"{identifier}.{content_hash}.soul")
    for bp in backup_paths:
        if bp.is_file():
            try:
                with open(bp) as f:
                    record = json.load(f)
                break
            except Exception as e:
                log.warning("Patch: failed to load %s: %s", bp, e)
    if record is None:
        # Last-resort: fetch from IA in case local backup was pruned.
        for ext in ("json", "soul"):
            url = f"{IA_DOWNLOAD_URL}/{identifier}/{identifier}.{ext}"
            try:
                from mememage import net
                resp = urllib.request.urlopen(url, context=net.default_https_context())
                record = json.loads(resp.read().decode("utf-8"))
                break
            except Exception:
                continue
    if record is None:
        log.warning("Cannot patch record %s — not found locally or on IA", identifier)
        return

    # 2. Apply patch + persist locally. Local save must succeed before
    # we try any network — otherwise a channel failure loses the patch.
    record.update(patch)
    _save_local_backup(identifier, record)

    # 3. Reblast through channels so peer mirrors pick up the patched
    # bytes. Use the same channels list the original mint used; if
    # nothing is enabled or configured, blast() raises and we log it
    # — local backup is already saved, so the patch isn't lost.
    from mememage.core import _canonicalize_for_disk
    payload = json.dumps(_canonicalize_for_disk(record), indent=2,
                         ensure_ascii=False).encode("utf-8")
    try:
        channels = _channels.load_channels()
        results = _channels.blast(channels, identifier, payload)
        log.info("Patched %s reblasted to %s", identifier, list(results.keys()))
    except _channels.ChannelUploadError as e:
        log.warning("Patch reblast failed for %s (local backup still updated): %s", identifier, e)
    except _channels.NamespaceBlocked as e:
        # Rare: a channel rejected the identifier mid-patch. Since the
        # identifier was already accepted at mint time, this shouldn't
        # really happen, but log clearly if it does.
        log.warning("Patch reblast namespace-blocked for %s: %s", identifier, e)


def mint(metadata: dict, gps: tuple | None, image_path: str,
         password: str = None, chain_visibility: str = None) -> MintResult:
    """Conceive an image: upload metadata to IA, encode bar into image.

    Args:
        metadata: Generation parameters (prompt, seed, dimensions, model, etc.)
        gps: (lat, lon) at conception, or ``None`` when the chain's
             ``gps_source`` is ``none``. Absent GPS is recorded
             honestly — the record carries no ``gps_time_locked`` field and
             the cert displays a "BIRTHPLACE — NOT RECORDED" placeholder.
        image_path: Path to the rendered image. Modified in place with bar.
        password: Optional creator password for GPS encryption and chain gating.
            If None, falls back to MEMEMAGE_PASSWORD from .env / environment.
        chain_visibility: "light_energy" (public) or "dark_matter" (private).
            If None and a password is in scope, defaults to "light_energy"
            (creator wants GPS sealed but soul public). Set to "dark_matter"
            explicitly to seal the entire soul + chunks.

    Returns:
        MintResult with identifier, content_hash, full IA URL, and image path.

    Raises:
        ValueError: If GPS is missing or image is too narrow for bar.
        RuntimeError: If IA upload fails.
    """
    # Canonical password resolution: explicit arg → chain.json → env →
    # None. Routes through chains.resolve_password so this path stays
    # in sync with the dashboard's mint handler (the two used to drift
    # — mint.py skipped chain.json, so CLI/programmatic calls into a
    # dark-matter chain configured via the dashboard would fail until
    # the user also mirrored MEMEMAGE_PASSWORD into env).
    from mememage import chains as _chains
    password = _chains.resolve_password(override=password)
    # Reject a wrong password against the chain verifier before encrypting:
    # a mismatched key seals a record the real chain password cannot unlock.
    if password and _chains.verify_password(password) is False:
        raise ValueError("Password does not match the chain seal.")

    # Guard against double conception — check if image already has a bar
    existing = extract_bar(image_path)
    if existing:
        raise ValueError(
            f"Image already conceived as {existing[0]}. "
            f"Remove the bar first or use a different image."
        )

    # Transactional image prep — runs INSIDE upload_metadata, immediately
    # before the soul is blasted (core._step_upload's prepare_image hook).
    # Embeds the bar (+ optional watermark), generates the thumbnail, and
    # signs — folding all three into the FIRST and only upload. If embed_bar
    # raises (non-PNG, image too narrow), the error propagates out before any
    # blast: the soul is never published and the chain never advances. No
    # image, no record. Replaces the old publish-then-_patch_record reblast,
    # which orphaned a thumbnail-less, unsigned soul whenever the bar embed
    # failed after the record was already uploaded.
    import hashlib as _hashlib
    import json as _json
    from mememage.signing import sign as _sign

    def _prepare_image(identifier, content_hash):
        # Optional perceptual-masked watermark — opt-in per chain. Deeper
        # backup than the bar: 16 hex chars of the hash (the full content hash) spread across the
        # whole image, surviving crops that destroy the bottom-row bar.
        # Watermark first (whole body), bar second (overwrites bottom 2
        # rows); _get_all_blocks excludes the bar margin so they don't
        # fight. blocks_used == 0 -> gate left too few blocks; silent skip.
        try:
            from mememage import chain_config as _chain_config
            _cfg = _chain_config.load()
            _wm_params = _cfg.watermark_params() if _cfg else None
        except Exception:
            _wm_params = None
        if _wm_params is not None:
            _strength, _variance_threshold = _wm_params
            _blocks_used = embed_watermark(
                image_path, content_hash,
                strength=_strength, variance_threshold=_variance_threshold,
            )
            if _blocks_used == 0:
                log.info(
                    "Watermark skipped: image too small or too flat for safe embed"
                )

        # Bar — the spirit in the lowest parts. The critical step: if this
        # raises, the error propagates out of _step_upload before any blast,
        # so nothing is published and no commitments run.
        embed_bar(image_path, identifier, content_hash)

        # Thumbnail from the MINTED image (bar + watermark in place) so the
        # dHash portrait comparison is apples-to-apples. Not in
        # _HASH_INCLUDED (content hash already computed). The signature
        # payload binds id + content_hash + sha256(thumbnail) so authorship
        # vouches for the portrait, closing the thumbnail-swap gap on
        # AUTHENTICATED.
        patch = {}
        thumbnail_for_sign = ""
        try:
            thumbnail = generate_thumbnail(image_path)
            if thumbnail:
                # Sign the STORED form of the thumbnail — plaintext bytes for
                # light chains, the encrypted-envelope dict's canonical JSON
                # for dark chains — so verifiers reproduce the hash without
                # the password. The swap defense holds either way.
                if chain_visibility == "dark_matter" and password:
                    from mememage.access import encrypt_field
                    stored = encrypt_field(thumbnail, password)
                    patch["thumbnail"] = stored
                    canonical = _json.dumps(
                        stored, sort_keys=True, separators=(",", ":"),
                        ensure_ascii=True,
                    )
                    thumbnail_for_sign = _hashlib.sha256(
                        canonical.encode("utf-8")
                    ).hexdigest()
                else:
                    patch["thumbnail"] = thumbnail
                    thumbnail_for_sign = _hashlib.sha256(
                        thumbnail.encode("utf-8")
                    ).hexdigest()
                log.info("Thumbnail generated for %s", identifier)
        except Exception as e:
            log.warning("Thumbnail generation failed (non-fatal): %s", e)

        sig_result = _sign(identifier, content_hash, thumbnail_for_sign)
        if sig_result:
            sig_hex, _pub_hex, _fp, _name = sig_result
            patch["signature"] = sig_hex
        return patch

    identifier, content_hash, distribution, primary_url = upload_metadata(
        metadata, gps, image_path,
        password=password, chain_visibility=chain_visibility,
        prepare_image=_prepare_image,
    )

    # Canonical URL = whatever the primary channel returned. Falls back
    # to the legacy IA pattern when no channel claimed primary.
    soul_filename = f"{identifier}.soul"
    url = primary_url or f"{IA_DOWNLOAD_URL}/{identifier}/{soul_filename}"

    # (image prep + thumbnail + signing now happen transactionally inside
    # upload_metadata via the _prepare_image hook above, before the blast.)

    return MintResult(
        identifier=identifier,
        content_hash=content_hash,
        url=url,
        image_path=image_path,
        distribution=distribution,
    )
