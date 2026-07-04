"""Channel framework — pluggable soul distribution.

Every conception *blasts* the soul to one or more channels. A channel
is a destination (Internet Archive, Zenodo, a self-hosted HTTP
endpoint, …) with its own credential schema and uploader. The
content_hash is the authority; any channel that holds the soul is a
valid source of truth. This module exposes:

- ``Channel``                 — the base class plugins extend.
- ``register(cls)``           — decorator a plugin uses to announce itself.
- ``load_channels()``         — read ``~/.mememage/channels.json`` and
                                instantiate each configured channel
                                against its registered type. Auto-
                                migrates legacy installs (no
                                channels.json) by writing a default
                                Internet Archive channel pointed at
                                ``IA_ACCESS_KEY``/``IA_SECRET_KEY``.
- ``blast(channels, ...)``    — fire all enabled channels for one
                                soul, collect ``{channel_id: url}``,
                                return the dict. Succeeds when at
                                least one channel succeeds.
- ``NamespaceBlocked``        — raised by a channel that wants the
                                caller to regenerate the identifier
                                (currently only IA does this when an
                                admin has blocked a specific id).
- ``ChannelUploadError``      — raised when every enabled channel
                                failed.

The dashboard reads each channel type's ``CREDENTIAL_FIELDS`` and
``CONFIG_FIELDS`` to render its config form generically — same
pattern as GoDaddy's "plug in your Cloudflare key" page.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path
from typing import Any, Type

log = logging.getLogger(__name__)

MEMEMAGE_ROOT = Path(os.path.expanduser("~/.mememage"))

# Channels are per-profile: each profile owns its own channels.json under
# ~/.mememage/profiles/<id>/. A profile carries its whole blast setup, so
# switching profiles switches the surfaces (and their credentials) without
# reconfiguring — the modularity users expect from the multi-key model.
#
# CHANNELS_PATH is an OVERRIDE HOOK, not the live path. When None (the
# production default) the path resolves to the ACTIVE profile's dir at call
# time via _channels_path(). Tests set it to a concrete path to isolate the
# home dir; that override wins. The pre-per-profile global file at
# ~/.mememage/channels.json is migrated into the first profile that loads.
CHANNELS_PATH = None
LEGACY_CHANNELS_PATH = MEMEMAGE_ROOT / "channels.json"


def _channels_path() -> Path:
    """Resolve the channels.json path. Honors the CHANNELS_PATH override
    (tests pin it); otherwise the ACTIVE profile's directory."""
    if CHANNELS_PATH is not None:
        return CHANNELS_PATH
    try:
        from mememage import profiles
        return profiles.profile_dir() / "channels.json"
    except Exception:
        # Profiles subsystem unavailable — fall back to the legacy global so
        # a degraded environment still blasts somewhere sane.
        return LEGACY_CHANNELS_PATH


class ChannelUploadError(RuntimeError):
    """All enabled channels failed to upload."""


class NamespaceBlocked(RuntimeError):
    """The channel refused this specific identifier (admin block,
    namespace squat, etc.). Caller should regenerate the identifier
    and replay the affected pipeline steps."""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, Type["Channel"]] = {}


def register(cls: Type["Channel"]) -> Type["Channel"]:
    """Class decorator a channel plugin uses to announce its type."""
    if not cls.TYPE:
        raise ValueError(f"Channel {cls.__name__} must set TYPE")
    _REGISTRY[cls.TYPE] = cls
    return cls


def get_type(type_name: str) -> Type["Channel"] | None:
    return _REGISTRY.get(type_name)


def all_types() -> dict[str, Type["Channel"]]:
    """Return a copy of the registry — used by the dashboard to render
    the "+ Add channel" picker with each type's display name and
    credential schema."""
    return dict(_REGISTRY)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class Channel:
    """Override per channel type.

    Plugins set ``TYPE`` (a stable string id), ``DISPLAY_NAME``
    (rendered in the dashboard), and the two field schemas:

    - ``CREDENTIAL_FIELDS``: list of ``{name, label, env_var, secret}``
      describing each piece of auth the channel needs. ``env_var`` is
      the default ``.env`` key name; the per-channel config can remap
      it (e.g. ``IA_PROD_ACCESS_KEY`` for a second IA account).
    - ``CONFIG_FIELDS``: non-secret config (URL templates, region,
      bucket, etc.). Stored inline in ``channels.json``.

    Then implement ``upload(identifier, soul_bytes, image_path) ->
    url``. Raise :class:`NamespaceBlocked` to ask the caller to
    regenerate the identifier; raise anything else for a hard fail
    (caller will record + skip).
    """

    TYPE: str = ""
    DISPLAY_NAME: str = ""
    CREDENTIAL_FIELDS: list[dict] = []
    CONFIG_FIELDS: list[dict] = []
    # True when this channel can raise NamespaceBlocked on upload(),
    # i.e. it owns part of the mememage identifier namespace and the
    # mint can be asked to pick a different name. ``blast()`` runs all
    # authoritative channels sequentially BEFORE the parallel batch of
    # non-authoritative channels so that an NB never strands committed
    # uploads under an about-to-be-abandoned identifier. Today only
    # InternetArchive sets this True.
    NAMESPACE_AUTHORITY: bool = False

    def __init__(self, channel_config: dict):
        self.id: str = channel_config["id"]
        self.name: str = channel_config.get("name") or self.DISPLAY_NAME or self.id
        self.enabled: bool = bool(channel_config.get("enabled", True))
        self.primary: bool = bool(channel_config.get("primary", False))
        # ``credentials`` maps logical field name → env var name.
        # Empty dict = use the default ``env_var`` from the schema.
        self.credentials: dict = channel_config.get("credentials") or {}
        # ``config`` holds non-secret settings (URLs, regions, etc.).
        self.config: dict = channel_config.get("config") or {}

    # --- credential lookup -------------------------------------------------

    def _env_var_for(self, field_name: str) -> str:
        """Resolve a credential field to its actual env var name.

        Priority: explicit per-channel mapping (``self.credentials``)
        > schema default (``env_var``). The field must be declared in
        ``CREDENTIAL_FIELDS`` or we raise — typo-resistance.
        """
        for f in self.CREDENTIAL_FIELDS:
            if f["name"] == field_name:
                return self.credentials.get(field_name) or f["env_var"]
        raise KeyError(f"{self.TYPE!r} has no credential field {field_name!r}")

    def _read_credential(self, field_name: str) -> str | None:
        # Lazy import to avoid hard-binding channels to config.py
        try:
            from mememage.config import _load_dotenv
            _load_dotenv()
        except Exception:
            pass
        return os.environ.get(self._env_var_for(field_name))

    def is_configured(self) -> bool:
        """True iff every CREDENTIAL_FIELD resolves to a non-empty env
        value. The dashboard uses this to render a "needs creds" dot;
        :func:`blast` uses it to skip silently."""
        for f in self.CREDENTIAL_FIELDS:
            if not self._read_credential(f["name"]):
                return False
        return True

    # --- override these ----------------------------------------------------

    def upload(self, identifier: str, soul_bytes: bytes,
               image_path: str | None = None) -> str:
        raise NotImplementedError

    # --- optional namespace-collision probe ------------------------------
    #
    # Consulted at identifier ASSIGNMENT time only (first mint of a
    # record / genesis roll) — NEVER at the post-mint patch reblast,
    # which legitimately re-PUTs the same identifier to attach the
    # signature + thumbnail. The pre-flight in core loops every enabled
    # channel that implements this and re-rolls the identifier on any
    # "taken", so a soul never silently overwrites a *different* soul on
    # any surface (the multi-surface divergent-namespace case: free on
    # IA, already held on a self-hosted box).
    #
    # Semantics are channel-specific and that's intentional — each
    # channel encodes its own permanence model. IA reports darkened /
    # tombstoned slots as taken (held forever); a self-hosted surface
    # with no tombstones reports only live slots. Channels that can't
    # answer leave the NotImplementedError default and the probe skips
    # them (advertised via ``capabilities()['exists']``).

    def exists(self, identifier: str) -> bool:
        """Report whether ``identifier`` is already taken on this surface."""
        raise NotImplementedError(f"{self.TYPE} does not implement exists()")

    # --- optional cleanup surface ----------------------------------------
    #
    # Channels that support pre-genesis maintenance (or general
    # housekeeping) implement these three methods. Each is OPTIONAL —
    # the default raises NotImplementedError, and ``capabilities()``
    # introspects what's actually wired so the dashboard can grey out
    # buttons for channels that can't do a given operation.
    #
    # IA today implements all three. Zenodo could implement search +
    # purge (no noindex equivalent). http_push could implement purge
    # (DELETE the resource). Plugins ship what they can support.

    def search(self, *, pattern: str = "mememage-*", limit: int = 200,
               **filters) -> list[dict]:
        """List items on this channel matching ``pattern``.

        ``filters`` is channel-specific extra criteria (IA uses
        ``uploader``/``collection``; Zenodo would use ``community``).
        Each result dict has at least ``identifier`` and ``url``;
        most channels can also fill ``date`` and ``size``.

        Returns an empty list if the channel has nothing matching;
        raises NotImplementedError if the channel doesn't support
        search at all.
        """
        raise NotImplementedError(f"{self.TYPE} does not implement search()")

    def hide(self, identifier: str) -> dict:
        """Make ``identifier`` invisible to public discovery on this
        channel without removing its content. Channel-specific:

        * IA: PATCH metadata to set ``noindex:true`` (drops from search).
        * Zenodo: no exact equivalent — would raise NotImplementedError.
        * http_push: typically no-op or NotImplementedError.

        Returns ``{"ok": bool, "error": str}``.
        """
        raise NotImplementedError(f"{self.TYPE} does not implement hide()")

    def purge(self, identifier: str) -> dict:
        """Remove the content of ``identifier`` (best-effort). The
        item's NAMESPACE may still survive — IA tombstones every
        identifier forever, for instance — but the files/data inside
        are deleted.

        Returns ``{"ok": bool, "deleted": int, "failed": int,
        "files": int, "errors": list[str]}`` (or a subset that makes
        sense for the channel). ``files`` is the count discovered so
        the dashboard can say "deleted 3 of 3".
        """
        raise NotImplementedError(f"{self.TYPE} does not implement purge()")

    def test(self) -> dict:
        """Live probe: is this surface reachable and are its credentials
        accepted, WITHOUT writing anything? Returns ``{"ok": bool, "detail":
        str}``. Optional — the dashboard shows a Test button only for channels
        that override this (advertised via ``capabilities()['test']``).
        """
        raise NotImplementedError(f"{self.TYPE} does not implement test()")

    def display_surface(self) -> str:
        """Human 'where the soul lands' label for the conception page's
        Target-surfaces strip. Defaults to the channel's friendly name;
        channels with a meaningful public address (http_push) override
        this to show their host/domain instead of an internal slug.
        """
        return self.name or self.DISPLAY_NAME or self.id

    def capabilities(self) -> dict:
        """Report which cleanup operations this channel actually
        implements. Used by the dashboard to render the cleanup UI
        with the right buttons enabled / disabled per channel.

        Detection is by method-identity comparison against the base
        class — if a subclass overrides ``search``, that's True; if
        it inherits the NotImplementedError-raising default, that's
        False.
        """
        cls = type(self)
        return {
            "search": cls.search is not Channel.search,
            "hide":   cls.hide   is not Channel.hide,
            "purge":  cls.purge  is not Channel.purge,
            "exists": cls.exists is not Channel.exists,
            "test":   cls.test   is not Channel.test,
        }

    def upload_keychain(self, chain_id: str, filename: str,
                        record_bytes: bytes) -> str:
        """Upload a keychain record (succession / revocation / alias).

        Keychain records use a different URL shape than souls — IA
        groups them under their own item (``mememage-keychain-<fp>``)
        rather than a per-record item. Most channels can implement
        this naturally; channels that don't fit the model (Zenodo's
        deposition shape) should leave the default ``NotImplementedError``
        and ``blast_keychain`` will silently skip them.

        Returns the public URL where the keychain record now lives.
        """
        raise NotImplementedError

    # --- introspection (for dashboard) -------------------------------------

    @classmethod
    def describe(cls) -> dict:
        """Schema dump for the dashboard's "+ Add channel" form."""
        return {
            "type": cls.TYPE,
            "display_name": cls.DISPLAY_NAME,
            "credential_fields": cls.CREDENTIAL_FIELDS,
            "config_fields": cls.CONFIG_FIELDS,
        }


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


# Third-party surfaces are OPT-IN. IA and Zenodo are pre-listed (enabling
# one is a single toggle) but ship DISABLED + non-primary — a fresh user
# should never eat a credential failure (e.g. "IA upload failed: no
# IA_ACCESS_KEY") for a surface they never chose. Anyone who enables IA /
# Zenodo is opting in and is expected to know it needs API keys + secrets.
#
# The blast surface that's ALWAYS on by default is the local self-pointing
# http_push ("this server") — it's seeded as enabled + PRIMARY at server
# boot by server._seed_first_run_defaults (it needs the live scheme/port,
# so it can't be a static entry here) and needs no third-party
# credentials. That's what carries minting + the "View certificate" loop
# out of the box.
_DEFAULT_CONFIG = {
    "channels": [
        {
            "id": "ia",
            "type": "internet_archive",
            "name": "Internet Archive",
            "enabled": False,  # opt-in — needs IA_ACCESS_KEY / IA_SECRET_KEY
            "primary": False,  # the local self-push surface is primary
            "credentials": {},  # use IA_ACCESS_KEY / IA_SECRET_KEY defaults
            "config": {},
        },
        # Zenodo — opt-in, same one-click toggle, ZENODO_ACCESS_TOKEN.
        {
            "id": "zenodo",
            "type": "zenodo",
            "name": "Zenodo",
            "enabled": False,
            "primary": False,
            "credentials": {},
            "config": {},
        },
    ],
}


def _load_raw() -> dict:
    """Read the active profile's channels.json, auto-creating it on first
    access.

    First-creation seeding, in order of preference:
      1. The pre-per-profile global ~/.mememage/channels.json, if present —
         the user's existing blast setup is adopted into the profile so the
         move to per-profile is invisible. The legacy file is then consumed
         (renamed .migrated) so it doesn't re-seed *other* profiles with a
         stale copy of the first profile's config.
      2. Otherwise the built-in default (single IA channel, Zenodo dormant),
         so a vanilla mememage / a fresh profile works exactly like before.
    """
    path = _channels_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("channels.json unreadable, falling back to default: %s", e)
            return copy.deepcopy(_DEFAULT_CONFIG)

    seed = None
    migrated_from_legacy = False
    # Only migrate the legacy global in production (override unset). Under a
    # test override CHANNELS_PATH is a pinned temp path — skip migration so the
    # test gets the clean default it expects.
    if (CHANNELS_PATH is None and LEGACY_CHANNELS_PATH.exists()
            and LEGACY_CHANNELS_PATH != path):
        try:
            seed = json.loads(LEGACY_CHANNELS_PATH.read_text(encoding="utf-8"))
            migrated_from_legacy = True
        except (json.JSONDecodeError, OSError):
            seed = None
    if seed is None:
        # deepcopy, not dict(): a shallow copy shares the nested ``channels``
        # list with the module-level default, so a caller appending (e.g. the
        # first-run self-push seed) would mutate _DEFAULT_CONFIG for the whole
        # process — every later _load_raw() would then carry the phantom entry.
        seed = copy.deepcopy(_DEFAULT_CONFIG)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(seed, indent=2), encoding="utf-8")
        try:
            os.chmod(str(path), 0o600)  # may hold channel creds: owner-only
        except OSError:
            pass
    except OSError:
        # Read-only disk — return the in-memory seed so uploads still work.
        return seed

    if migrated_from_legacy:
        try:
            LEGACY_CHANNELS_PATH.rename(
                LEGACY_CHANNELS_PATH.with_suffix(".json.migrated")
            )
        except OSError:
            pass
    return seed


def save_raw(data: dict) -> None:
    """Atomic-ish save to the active profile's channels.json — dashboard API
    uses this when the user edits."""
    path = _channels_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(str(tmp), 0o600)  # may hold channel credentials: owner-only
    except OSError:
        pass
    tmp.replace(path)


def load_channels() -> list[Channel]:
    """Instantiate every configured channel against the registry.

    Channels whose ``type`` isn't registered (typo, future plugin not
    installed) are logged and skipped — we don't crash the upload
    pipeline over a stale config entry.
    """
    # Ensure plugin modules have been imported so they registered.
    _ensure_plugins_loaded()
    raw = _load_raw()
    out: list[Channel] = []
    for entry in raw.get("channels", []):
        type_name = entry.get("type")
        cls = _REGISTRY.get(type_name)
        if cls is None:
            log.warning("Unknown channel type %r — skipping %s", type_name, entry.get("id"))
            continue
        try:
            out.append(cls(entry))
        except Exception as e:
            log.warning("Failed to instantiate channel %s: %s", entry.get("id"), e)
    return out


_plugins_loaded = False


def _ensure_plugins_loaded() -> None:
    """Import the bundled plugin modules so they register their types.

    Lazy to keep import-time side effects minimal — the registry is
    only populated when something actually asks for channels.
    """
    global _plugins_loaded
    if _plugins_loaded:
        return
    # Order doesn't matter, but importing each here is the canonical
    # place to wire built-in types. Third-party plugins can import
    # themselves before load_channels() is called.
    from mememage.channels import internet_archive as _ia  # noqa: F401
    from mememage.channels import zenodo as _zen  # noqa: F401
    from mememage.channels import http_push as _hp  # noqa: F401
    _plugins_loaded = True


# ---------------------------------------------------------------------------
# Blast
# ---------------------------------------------------------------------------


def blast(channels: list[Channel], identifier: str, soul_bytes: bytes,
          image_path: str | None = None) -> dict[str, str]:
    """Fire enabled channels for one soul. Return ``{channel_id: url}``.

    Two-phase dispatch to keep orphan uploads off remote servers when a
    namespace-authoritative channel says "pick a different name":

    1. **Authoritative phase (sequential).** Channels with
       ``NAMESPACE_AUTHORITY = True`` fire in declaration order. NB
       from any of them propagates BEFORE phase 2 starts, so no
       non-authoritative channel has committed to disk yet — the
       caller regenerates the identifier and we re-blast from scratch
       with no orphans on Zenodo / http_push / etc.
    2. **Permissive phase (parallel).** Remaining channels run
       concurrently via a ThreadPoolExecutor. These channels accept
       whatever identifier we hand them; they cannot raise NB. Wall
       time = max of their individual upload times instead of the sum.

    Args:
        channels: result of :func:`load_channels`.
        identifier: ``mememage-XXXXXXXXXXXX``.
        soul_bytes: serialized soul JSON.
        image_path: optional path to the minted image — channels that
            also want the body (IPFS, S3, etc.) read it from here.

    Raises:
        NamespaceBlocked: propagated from a phase 1 channel. Caller
            replays the affected pipeline steps with a fresh identifier.
        ChannelUploadError: every fireable channel failed.
    """
    fireable = [
        ch for ch in channels
        if ch.enabled and ch.is_configured()
    ]
    if not fireable:
        raise ChannelUploadError(
            "No enabled+configured surfaces to blast to. Configure at "
            "least one in the dashboard's Config → Surfaces section."
        )

    auth_channels = [c for c in fireable if c.NAMESPACE_AUTHORITY]
    other_channels = [c for c in fireable if not c.NAMESPACE_AUTHORITY]

    results: dict[str, str] = {}
    errors: dict[str, str] = {}

    # Phase 1 — sequential. NB here aborts the whole blast before phase
    # 2 even starts, so non-authoritative channels never get a chance to
    # leave orphans behind under a doomed identifier.
    for ch in auth_channels:
        try:
            url = ch.upload(identifier, soul_bytes, image_path)
            if url:
                results[ch.id] = url
        except NamespaceBlocked:
            raise
        except Exception as e:
            errors[ch.id] = str(e)
            log.warning("Channel %s failed: %s", ch.id, e)

    # Phase 2 — parallel. Channels here cannot raise NB; their failures
    # are isolated per-channel and captured in `errors`. If only one
    # other channel is configured the executor still works — single-
    # thread overhead is negligible vs the network call cost.
    if other_channels:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(
            max_workers=len(other_channels),
            thread_name_prefix="mememage-blast",
        ) as ex:
            future_to_ch = {
                ex.submit(ch.upload, identifier, soul_bytes, image_path): ch
                for ch in other_channels
            }
            for future in as_completed(future_to_ch):
                ch = future_to_ch[future]
                try:
                    url = future.result()
                    if url:
                        results[ch.id] = url
                except NamespaceBlocked as e:
                    # Defense in depth — a misconfigured plugin claiming
                    # NB without NAMESPACE_AUTHORITY would otherwise sneak
                    # past the contract. Log it as an error so the mint
                    # still completes via the authoritative channel, and
                    # surface the misconfiguration to the operator.
                    errors[ch.id] = (
                        f"plugin error: {ch.id} raised NamespaceBlocked but "
                        f"does not set NAMESPACE_AUTHORITY=True ({e})"
                    )
                    log.warning("Channel %s raised NB without authority flag: %s", ch.id, e)
                except Exception as e:
                    errors[ch.id] = str(e)
                    log.warning("Channel %s failed: %s", ch.id, e)

    if not results:
        raise ChannelUploadError(
            f"All channels failed: {errors}"
        )
    return BlastResult(results, errors)


class BlastResult(dict):
    """Channel-blast result with both successes and failures.

    Behaves like a ``{channel_id: url}`` dict for back-compat with
    existing callers that iterate the success map; also exposes
    ``.errors`` (``{channel_id: error_message}``) so newer callers
    can surface partial failures on the conception page / status
    endpoint.

    A typical mint with one success and one failure produces a
    BlastResult of length 1 (the success) plus a ``.errors`` dict
    of length 1 (the failure). Tests that previously asserted
    ``len(results) == n`` for successes-only flows still pass.
    """

    def __init__(self, urls: dict, errors: dict):
        super().__init__(urls)
        self.errors = dict(errors or {})


def blast_keychain(channels: list[Channel], chain_id: str, filename: str,
                   record_bytes: bytes) -> dict[str, str]:
    """Mirror a keychain record to every channel that supports it.

    Parallel to :func:`blast` but for keychain records (succession /
    revocation / alias). Channels that don't implement
    ``upload_keychain`` (e.g. Zenodo) are silently skipped — those
    surfaces don't fit the keychain model and that's fine, as long as
    at least one channel succeeds.

    Same at-least-one-succeeds contract as soul blast. ``NamespaceBlocked``
    isn't possible for keychain records (no collision retries) and would
    propagate if a channel raised it; in practice no channel does.
    """
    fireable = [
        ch for ch in channels
        if ch.enabled and ch.is_configured()
    ]
    if not fireable:
        raise ChannelUploadError(
            "No enabled+configured surfaces to mirror keychain record to. "
            "Configure at least one in the dashboard's Config → Surfaces "
            "section."
        )

    results: dict[str, str] = {}
    errors: dict[str, str] = {}
    skipped: list[str] = []
    for ch in fireable:
        try:
            url = ch.upload_keychain(chain_id, filename, record_bytes)
            if url:
                results[ch.id] = url
        except NotImplementedError:
            # Channel can't host keychain records — silently skip.
            # Zenodo is the canonical example.
            skipped.append(ch.id)
        except Exception as e:
            errors[ch.id] = str(e)
            log.warning("Keychain blast — channel %s failed: %s", ch.id, e)

    if not results:
        raise ChannelUploadError(
            f"Keychain blast failed: {errors} (skipped: {skipped})"
        )
    return results


def pick_primary_url(channels: list[Channel], results: dict[str, str]) -> str:
    """Choose the canonical record URL for the bar / Discord message.

    Priority: a channel marked ``primary`` that also succeeded; else
    the first successful channel in declaration order; else empty.
    """
    for ch in channels:
        if ch.primary and ch.id in results:
            return results[ch.id]
    for ch in channels:
        if ch.id in results:
            return results[ch.id]
    return ""
