"""Mememage mint server.

A lightweight HTTP server that serves the mint UI and handles GPS callbacks.
Provides two entry points:

    /mint/new              — Manual upload: drag image, fill metadata, capture GPS, mint
    POST /api/mint/session — Programmatic: client sends image_path + metadata, gets token URL
    /mint/<token>          — GPS capture page (for both paths)
    POST /api/mint/<token> — GPS callback: receives coordinates, triggers mint

Run as:
    python -m mememage.server --port 8443

Session tokens persist to ~/.mememage/sessions.json (surviving restarts) and
expire after 7 days; pending sessions are consumed when their conception
completes or is cancelled.
"""

import hmac
import json
import logging
import os
import re
import sys
import secrets
import threading
import time
from collections import OrderedDict
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# Soul-store paths resolve through chains (never a hardcoded expanduser) so a
# redirected MEMEMAGE_ROOT — tests, a sandbox — cannot write into the real store.
from mememage import chains as _chains

from mememage.mint import mint
from mememage import access as _access

log = logging.getLogger(__name__)


def _generate_qr_data_uri(url):
    """Generate a QR code PNG as a base64 data URI.

    Optional — returns an empty string when ``qrcode`` isn't installed
    so the mint page still renders (the URL itself is enough to hand
    off to a phone). Crashing here would take out the whole page since
    it's the GPS-capture landing the Discord webhook points at.

    The three finder patterns at the QR's corners are colorized to
    M / Y / C — same palette the bar uses. Top-left magenta, top-right
    yellow, bottom-left cyan; reads M→Y→C clockwise from the upper-
    left, mirroring the bar's [M][Y][C] inward pattern. Decoders still
    read the QR fine because the finder structure (concentric square
    + center block) is preserved — only the hue of the foreground
    modules changes.
    """
    # Wrap the WHOLE body: ``qrcode``'s default image factory imports PIL
    # lazily inside ``make_image()`` — so a bundle with qrcode but no Pillow
    # (e.g. a Windows build that missed the [mint] extra) used to raise
    # ModuleNotFoundError here and kill the entire upload request thread
    # (no response → the browser sees "Failed to fetch"). The QR is purely
    # optional — the URL alone hands off to a phone — so never let it crash.
    try:
        import base64
        import io
        import qrcode

        qr = qrcode.QRCode(box_size=6, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        fg = (0xc0, 0xc0, 0xd0)
        img = qr.make_image(fill_color="#c0c0d0", back_color="#16161e").convert("RGB")

        # Recolor the three 7x7-module finder patterns. Module → pixel:
        # ``(border + module_idx) * box_size``. We replace only the fg
        # pixels inside each finder's bounding rect so the back color
        # stays untouched (no halo around the squares).
        modules = qr.modules_count
        box = qr.box_size
        border = qr.border
        finders = [
            ((0, 0),                  (0xdc, 0x50, 0xdc)),  # TL — Magenta
            ((modules - 7, 0),        (0xdc, 0xc8, 0x3c)),  # TR — Yellow
            ((0, modules - 7),        (0x3c, 0xc8, 0xdc)),  # BL — Cyan
        ]
        pix = img.load()
        for (mx, my), target in finders:
            x0 = (border + mx) * box
            y0 = (border + my) * box
            x1 = x0 + 7 * box
            y1 = y0 + 7 * box
            for y in range(y0, y1):
                for x in range(x0, x1):
                    if pix[x, y] == fg:
                        pix[x, y] = target

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception as e:
        log.info("QR generation unavailable (%s) — mint page will show URL only", e)
        return ""


def _extract_png_metadata(image_path):
    """Extract generation_params from PNG text chunks. Returns dict or {}."""
    try:
        from PIL import Image
        # Context-managed so the file handle is released promptly — on Windows
        # an open handle blocks the later unlink of the staged upload.
        with Image.open(image_path) as img:
            if hasattr(img, 'text') and 'generation_params' in img.text:
                return json.loads(img.text['generation_params'])
    except Exception:
        pass
    return {}


def _extract_image_metadata(image_path):
    """All prefill metadata an uploaded image carries: PNG text
    ``generation_params`` (AI gens / the param-packing plugin) PLUS readable
    EXIF (photos — camera, lens, exposure, date, GPS, description). EXIF must
    be read from the ORIGINAL file: _ensure_png_upload re-saves as PNG and
    strips it. PNG generation_params win over EXIF on the rare key clash.
    Best-effort — never raises."""
    meta = {}
    try:
        from mememage import exif as _exif
        meta.update(_exif.extract_origin_fields(image_path))
    except Exception as e:
        log.debug("EXIF extract failed for %s: %s", image_path, e)
    try:
        png = _extract_png_metadata(image_path)
        if png:
            meta.update(png)
    except Exception:
        pass
    return meta

UPLOAD_DIR = Path.home() / ".mememage" / "uploads"

# Default cap for an accepted soul body on the /api/souls receive face. The
# body is STREAMED to disk (never held whole in RAM), so this bounds DISK, not
# memory — the guard is against an authed peer filling your disk, not an OOM.
# Generous so large payload souls just work; override per-host via
# server.json: max_soul_bytes (see _soul_max_bytes()).
SOUL_MAX_BYTES = 512 * 1024 * 1024  # 512 MiB


def _stream_body_to_file(reader, length, dest, chunk_size=262144):
    """Stream exactly ``length`` bytes from ``reader`` (file-like with .read)
    into ``dest`` (a Path), in bounded chunks — the whole body is NEVER held in
    memory, so hundreds-of-MB souls don't OOM the box.

    Returns ``(bytes_written, head_ok)``. ``head_ok`` is True if the first
    non-whitespace byte is ``{`` or ``[`` (a cheap "looks like JSON" check — the
    soul's own content_hash is the real integrity check at read time), False if
    it clearly isn't JSON, None if the body was empty. Raises ValueError on a
    short read (fewer bytes arrived than Content-Length promised).
    """
    written = 0
    head_ok = None
    with open(dest, "wb") as out:
        remaining = length
        while remaining > 0:
            buf = reader.read(min(chunk_size, remaining))
            if not buf:
                break
            if head_ok is None:
                stripped = buf.lstrip()
                if stripped:
                    head_ok = stripped[:1] in (b"{", b"[")
            out.write(buf)
            written += len(buf)
            remaining -= len(buf)
    if written != length:
        raise ValueError(f"short body: {written}/{length} bytes")
    return written, head_ok


def _soul_max_bytes():
    """The /api/souls body cap: server.json ``max_soul_bytes`` if set, else the
    SOUL_MAX_BYTES default. The body streams to disk, so this bounds disk."""
    try:
        v = _get_server_config().get("max_soul_bytes")
        if isinstance(v, int) and v > 0:
            return v
    except Exception:
        pass
    return SOUL_MAX_BYTES


# Default cap for a conceived IMAGE accepted on the /api/souls/<id>.png receive
# face (the http_push channel's push_image feature). Streamed to disk like the
# soul, so this bounds disk. A conceived PNG is at most a handful of MB; 20 MiB
# is generous headroom. Override per-host via server.json: max_image_bytes.
IMAGE_MAX_BYTES = 20 * 1024 * 1024  # 20 MiB


def _image_max_bytes():
    """The /api/souls/<id>.png body cap: server.json ``max_image_bytes`` if set,
    else IMAGE_MAX_BYTES. The body streams to disk, so this bounds disk."""
    try:
        v = _get_server_config().get("max_image_bytes")
        if isinstance(v, int) and v > 0:
            return v
    except Exception:
        pass
    return IMAGE_MAX_BYTES


# Default cap for a payload SOURCE file uploaded via the dashboard (audio,
# video, big images that get chunked into the payload). Streamed to disk, so
# this bounds disk, not memory. Override via server.json: max_payload_bytes.
PAYLOAD_MAX_BYTES = 512 * 1024 * 1024  # 512 MiB


def _payload_max_bytes():
    try:
        v = _get_server_config().get("max_payload_bytes")
        if isinstance(v, int) and v > 0:
            return v
    except Exception:
        pass
    return PAYLOAD_MAX_BYTES


def _ensure_png_upload(path):
    """Normalize an uploaded image to a lossless PNG at the conception door.

    The bar codec embeds into PNG only — re-saving the barred pixels as JPEG
    (or any lossy/again-compressed format) would destroy the brightness
    modulation, so ``bar.embed_bar`` refuses any non-``.png`` path. The
    programmatic path already hands over PNG, but a manual drag-upload
    of a JPG/HEIC/WebP would otherwise reach ``embed_bar`` as-is and fail
    conception ("Bar encoding requires PNG format").

    Convert any non-PNG upload to a lossless ``.png`` sibling here so the whole
    downstream pipeline always sees a PNG. JPEG's prior compression is already
    baked in; converting to PNG from this point on is lossless. Returns the
    path to use (the ``.png`` on success, else the original untouched so the
    existing error still surfaces rather than silently swallowing a bad file).
    """
    path = Path(path)
    if path.suffix.lower() == ".png":
        return path
    try:
        from PIL import Image
        try:  # HEIC/HEIF need the optional opener registered before open()
            import pillow_heif
            pillow_heif.register_heif_opener()
        except Exception:
            pass
        png_path = path.with_suffix(".png")
        with Image.open(path) as im:
            im.convert("RGB").save(png_path, format="PNG")
        try:
            path.unlink()
        except OSError:
            pass
        log.info("Normalized upload %s -> %s for bar encoding", path.name, png_path.name)
        return png_path
    except Exception as e:
        log.warning("Upload PNG-normalize failed for %s: %s", path, e)
        return path
SERVER_CONFIG_FILE = Path.home() / ".mememage" / "server.json"


def _load_server_config():
    """Load server config from ~/.mememage/server.json.

    Optional keys:
        domain: str — public domain for mint URLs (auto-detected if absent)
        cert: str — TLS certificate path
        key: str — TLS key path
        webhooks: list[{url, headers?, template?}] — called on conception events
    """
    if not SERVER_CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(SERVER_CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


_server_config = None

def _get_server_config():
    global _server_config
    if _server_config is None:
        _server_config = _load_server_config()
    return _server_config


def _external_scheme():
    """``http`` or ``https`` for the URLs this server advertises about
    itself (mint links, souls base, download URLs).

    ``run_server`` stashes the actual bind scheme in ``MEMEMAGE_SCHEME``
    (https when started with a cert, http for a local/desktop bind). A
    public souls_domain is always behind nginx TLS, so those URLs stay
    https regardless — only the bare-host fallbacks use this. Defaults to
    https so anything reading it before the server boots stays safe.
    """
    return os.environ.get("MEMEMAGE_SCHEME", "https")


def _load_mint_token():
    """Load the mint API bearer token from .env. Returns None if not set."""
    from mememage.config import _load_dotenv
    _load_dotenv()
    return os.environ.get("MINT_API_TOKEN")


# Global env keys the dashboard owns regardless of channel config.
# Channel-specific keys (IA_*, ZENODO_*, etc.) are appended dynamically
# from each registered channel's CREDENTIAL_FIELDS schema.
_GLOBAL_ENV_KEYS = ("MINT_API_TOKEN", "MEMEMAGE_PASSWORD")


def _dashboard_env_keys() -> tuple[str, ...]:
    return tuple(k for k, _ in _dashboard_env_meta())


def _dashboard_env_meta() -> list[tuple[str, str]]:
    """Ordered (env_var_name, owner_label) pairs. Owner labels make
    the Credentials section honest about which channel each secret
    serves (e.g. ``IA_ACCESS_KEY`` → ``Internet Archive``). Globals
    get an owner label of ``"global"``."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    try:
        from mememage import channels as _ch
        _ch._ensure_plugins_loaded()
        for cls in _ch.all_types().values():
            for field in getattr(cls, "CREDENTIAL_FIELDS", []):
                ev = field.get("env_var")
                if ev and ev not in seen:
                    seen.add(ev)
                    out.append((ev, cls.DISPLAY_NAME or cls.TYPE))
    except Exception:
        pass
    for k in _GLOBAL_ENV_KEYS:
        if k not in seen:
            seen.add(k)
            out.append((k, "global"))
    return out


def _scrub_chain_password(info: dict) -> dict:
    """Strip the stored chain password from a metadata dict before
    returning it to a client. Adds a ``password_set`` boolean so the
    dashboard can show a presence dot, and a ``gps_source`` default
    so the client never has to fall back on its own. The actual
    password value never leaves the server — same pattern as env-var
    presence reporting.
    """
    if not isinstance(info, dict):
        return info
    out = {k: v for k, v in info.items() if k not in ("password", "password_verifier")}
    out["password_set"] = bool(info.get("password_verifier") or info.get("password"))
    # Default ``phone`` for legacy chains so the dashboard can render
    # the radio without ever seeing an undefined value.
    from mememage.gps import (GPS_SOURCES, DEFAULT_GPS_SOURCE,
                              GPS_VISIBILITIES, DEFAULT_GPS_VISIBILITY)
    src = info.get("gps_source")
    out["gps_source"] = src if src in GPS_SOURCES else DEFAULT_GPS_SOURCE
    vis = info.get("gps_visibility")
    out["gps_visibility"] = vis if vis in GPS_VISIBILITIES else DEFAULT_GPS_VISIBILITY
    # Constellation size (1..12) — clamp to the default for legacy chains so
    # the dashboard selector always has a valid value to render.
    from mememage.chains import (CONSTELLATION_SIZE_MIN, CONSTELLATION_SIZE_MAX,
                                 DEFAULT_CONSTELLATION_SIZE)
    cs = info.get("constellation_size")
    out["constellation_size"] = (
        cs if isinstance(cs, int) and CONSTELLATION_SIZE_MIN <= cs <= CONSTELLATION_SIZE_MAX
        else DEFAULT_CONSTELLATION_SIZE
    )
    # Watermark — a live per-chain on/off image setting edited in Config, so the
    # dashboard needs its current value here. Default is ON: only an explicit
    # {'preset':'off'} reads as off; absent (unset) or any non-off preset -> "on".
    wm = info.get("watermark")
    preset = wm.get("preset") if isinstance(wm, dict) else None
    out["watermark"] = "off" if preset == "off" else "on"
    return out


def _check_auth(handler):
    """Verify bearer token on API requests. Returns True if authorized."""
    token = _load_mint_token()
    if not token:
        return True  # no token configured = no auth required
    auth = handler.headers.get("Authorization", "")
    if hmac.compare_digest(auth, f"Bearer {token}"):
        return True
    handler._send_json({"error": "Unauthorized"}, 401)
    return False


# Sentinel string substituted in for webhook secrets when the URL
# travels back from the server to a client. The same string is
# recognized by ``_resolve_masked_webhook_url`` on the return trip so
# a "no-op edit" (user clicks Save without touching the URL) preserves
# the original token rather than clobbering it.
_WEBHOOK_MASK = "***"


def _mask_webhook_url(url: str) -> str:
    """Mask the trailing token segment in a webhook URL.

    Discord (``/api/webhooks/{id}/{token}``), Slack
    (``/services/T.../B.../{token}``), and most webhook providers embed
    the bearer-style secret as the final path segment. The dashboard
    returns webhook URLs to anyone with the ``MINT_API_TOKEN``, which
    is fine for the dashboard's own user but a leak if the token is
    snooped or the response is cached.

    Heuristic: replace the last path segment with ``***`` when (a) the
    URL has 3+ path segments and (b) that segment is at least 20 chars
    (typical webhook tokens are 60-80). Short trailing segments
    (``/notify``, ``/hook``) stay visible — they're path components,
    not secrets.
    """
    try:
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(url)
        path = parts.path or ""
        segments = path.split("/")
        # segments[0] is "" when path starts with /; meaningful count
        # excludes that leading empty.
        meaningful = [s for s in segments if s]
        if len(meaningful) < 3:
            return url
        last = segments[-1]
        if len(last) < 20:
            return url
        segments[-1] = _WEBHOOK_MASK
        return urlunsplit((parts.scheme, parts.netloc, "/".join(segments),
                           parts.query, parts.fragment))
    except Exception:
        return url


_SECRET_HEADER_KEYWORDS = ("authorization", "token", "secret", "key", "bearer", "api-key", "auth")


def _is_secret_header(name: str) -> bool:
    lower = (name or "").lower()
    return any(kw in lower for kw in _SECRET_HEADER_KEYWORDS)


def _mask_webhook_headers(headers: dict) -> dict:
    """Mask values of secret-bearing headers (Authorization, anything
    containing token/secret/key/bearer). Non-secret headers like
    ``Content-Type`` pass through unchanged.
    """
    out = {}
    for k, v in (headers or {}).items():
        if _is_secret_header(k) and v:
            out[k] = _WEBHOOK_MASK
        else:
            out[k] = v
    return out


def _resolve_masked_webhook_headers(incoming: dict, existing: dict) -> dict:
    """Restore secret-bearing header values that came back as the
    mask sentinel. Matches by header name in the corresponding
    existing webhook (caller is responsible for picking the right
    existing entry — usually by URL prefix).
    """
    out = dict(incoming or {})
    for k, v in list(out.items()):
        if v == _WEBHOOK_MASK and _is_secret_header(k):
            real = (existing or {}).get(k)
            if real:
                out[k] = real
    return out


def _resolve_masked_webhook_url(incoming_url: str, existing_urls: list) -> str:
    """Restore the original token if the incoming URL ends in the
    mask sentinel and the prefix matches a known on-disk webhook.

    Lets the dashboard send the displayed (masked) URL back unchanged
    on a no-op edit without clobbering the real secret. A user who
    genuinely wants to rotate the token pastes a fresh full URL — the
    sentinel won't be present, so the new URL flows through verbatim.
    """
    try:
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(incoming_url)
        segments = (parts.path or "").split("/")
        if not segments or segments[-1] != _WEBHOOK_MASK:
            return incoming_url
        prefix_path = "/".join(segments[:-1])
        prefix = urlunsplit((parts.scheme, parts.netloc, prefix_path, "", ""))
        for existing in existing_urls:
            ep = urlsplit(existing)
            existing_prefix_path = "/".join((ep.path or "").split("/")[:-1])
            existing_prefix = urlunsplit(
                (ep.scheme, ep.netloc, existing_prefix_path, "", "")
            )
            if existing_prefix == prefix:
                return existing
        # No match — leave the mask in place. User will see *** on
        # the next load, prompting them to paste the real URL.
        return incoming_url
    except Exception:
        return incoming_url


# Bayer designations — display-side mapping for the integer
# constellation_index that records carry (ASCII-safe storage; Greek
# letter at the webhook / cert surface).
# Full 24-letter Greek alphabet (\u03b1..\u03c9, no final sigma) \u2014 the Bayer
# designation space; caps constellation_size at 24. Keep in sync with
# core._BAYER_LETTERS and the JS tables.
_BAYER_LETTERS = ("\u03b1\u03b2\u03b3\u03b4\u03b5\u03b6\u03b7\u03b8\u03b9\u03ba\u03bb\u03bc"
                  "\u03bd\u03be\u03bf\u03c0\u03c1\u03c3\u03c4\u03c5\u03c6\u03c7\u03c8\u03c9")


def _bayer_letter(index) -> str:
    if not isinstance(index, int):
        return ""
    if 0 <= index < len(_BAYER_LETTERS):
        return _BAYER_LETTERS[index]
    return ""


def _fire_webhooks(event: str, data: dict):
    """Fire all configured webhooks for an event.

    Reads webhooks from ~/.mememage/server.json:
        "webhooks": [
            {"url": "https://...", "headers": {"Authorization": "Bot ..."}, "events": ["conceived", "ready"]}
        ]

    Events:
        "conceived" — image conceived, data has identifier, url, content_hash, image_path
        "ready" — mint session created, data has mint_url, image_name, gps_source
    """
    config = _get_server_config()
    webhooks = config.get("webhooks", [])
    if not webhooks:
        return

    import urllib.request
    # Compose the template substitution dict. We add two derived fields
    # so a single template renders cleanly for both events without the
    # user having to author conditionals (which the templater doesn't
    # support):
    #   action_url — the URL the recipient should click (for "ready"
    #                this is the GPS-capture mint URL; for "conceived"
    #                it's the IA record URL)
    #   summary    — short human description tailored to the event
    raw_payload_dict = {"event": event, **data}
    if event == "ready":
        raw_payload_dict.setdefault("action_url", data.get("mint_url", ""))
        # Only the `phone` GPS source involves an actual capture step on the
        # device; machine/none chains just need the creator to confirm. Don't
        # say "GPS capture" when the chain isn't capturing GPS on the phone.
        img = data.get("image_name", "image")
        summary = (f"GPS capture ready for {img}"
                   if data.get("gps_source") == "phone"
                   else f"Conception ready for {img}")
        raw_payload_dict.setdefault("summary", summary)
    elif event == "conceived":
        raw_payload_dict.setdefault("action_url", data.get("url", ""))
        raw_payload_dict.setdefault(
            "summary",
            f"Soul conceived: {data.get('identifier', '?')}",
        )
    raw_payload = json.dumps(raw_payload_dict).encode("utf-8")

    # Resolve the image + soul attachments once (generic descriptors); each
    # hook's adapter decides whether/how to use them. Only conceived events
    # carry files.
    from mememage import notifiers
    all_files = notifiers.build_attachments(data) if event == "conceived" else []

    for hook in webhooks:
        events = hook.get("events", ["conceived", "ready"])
        if event not in events:
            continue
        try:
            # Template rendering: if the hook defines a `template` string,
            # substitute {{key}} placeholders with JSON-escaped values from
            # the event payload and send the rendered string. This is how
            # Discord (expects {"content": ...}), Slack (expects {"text":
            # ...}), etc. get their platform-shaped BODY without us shipping
            # a per-platform formatter. The DELIVERY (plain POST vs multipart
            # vs files.uploadV2) is the adapter's job, below.
            tmpl = hook.get("template")
            if tmpl and isinstance(tmpl, str):
                payload = _render_webhook_template(tmpl, raw_payload_dict).encode("utf-8")
            else:
                payload = raw_payload

            # attach_files is a generic "the recipient wants the image + soul"
            # flag; each adapter attaches them its own way (Discord multipart,
            # Slack files.uploadV2, generic = links in body). Gated on the
            # conceived event since that's when the files exist.
            want_attach = bool(hook.get("attach_files")) and event == "conceived"
            files = all_files if want_attach else []

            notifier = notifiers.resolve(hook)
            notifier.deliver(payload, files, event, raw_payload_dict)
            log.info("Webhook fired: %s → %s via %s%s",
                     event, hook["url"], notifier.TYPE,
                     " (with attachments)" if files else "")
        except urllib.error.HTTPError as e:
            # Capture the response body so the log shows WHY the
            # destination rejected us — Discord's error codes (50007
            # "Cannot send messages to this user", 50013 "Missing
            # Permissions", 10003 "Unknown Channel", etc.) are the
            # actionable signal, not the bare HTTP status.
            try:
                body = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                body = "(could not read response body)"
            log.warning("Webhook failed (%s → %s): HTTP %s — %s",
                        event, hook["url"], e.code, body)
        except Exception as e:
            log.warning("Webhook failed (%s → %s): %s", event, hook["url"], e)


def _render_webhook_template(template: str, data: dict) -> str:
    """Substitute ``{{key}}`` placeholders in a webhook template string
    with JSON-escaped values from the event payload.

    The substitution outputs the raw escaped string (no surrounding
    quotes), which is the right shape for inserting into a JSON string
    context like ``{"content": "{{identifier}}"}``. Place placeholders
    INSIDE string values, not as bare JSON tokens.

    Unknown keys render as empty string rather than raising — webhook
    firing is best-effort and should never block conception.
    """
    import re

    def _replace(m: "re.Match") -> str:
        key = m.group(1).strip()
        val = data.get(key, "")
        # json.dumps gives a quoted JSON string with all chars properly
        # escaped (\, ", control chars, unicode). Strip the outer quotes
        # so the result is safe to drop inside an existing string context
        # in the template.
        return json.dumps(str(val))[1:-1]

    return re.sub(r"\{\{\s*(\w+)\s*\}\}", _replace, template)


def _notify_conceived(result):
    """Notify webhooks that an image was conceived."""
    from mememage import chains
    from mememage.core import soul_store_dir
    records_root = soul_store_dir()
    soul_path = records_root / f"{result.identifier}.soul"
    # Render distribution as a multiline "label: url" block so Discord
    # templates can drop ``{{distribution}}`` into a message and the
    # creator sees every surface the soul landed on. Single-channel
    # mints still get a single line — natural degradation.
    dist = result.distribution or {}
    distribution_block = "\n".join(f"{k}: {v}" for k, v in dist.items()) \
        if dist else (result.url or "")
    # Read the just-minted soul off disk so templates can reference any
    # field from the record (creator, constellation, rarity, etc.).
    # Best-effort: missing soul file = empty context, never crashes
    # webhook firing.
    record = {}
    if soul_path.exists():
        try:
            record = json.loads(soul_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    # V1: rarity_score is derived from the rarity dict, not persisted.
    # Compute here for webhook templates (still surfaced via {{rarity_score}}).
    rarity_score = record.get("rarity_score")  # back-compat for legacy souls
    if rarity_score is None and isinstance(record.get("rarity"), dict):
        r = record["rarity"]
        try:
            s = 0
            for g in ("celestial", "machine", "entropy"):
                for t in (r.get(g) or []):
                    if isinstance(t, dict) and isinstance(t.get("points"), (int, float)):
                        s += t["points"]
            if isinstance(r.get("machine_signature"), (int, float)):
                s += r["machine_signature"]
            sigil = r.get("sigil")
            if isinstance(sigil, dict) and isinstance(sigil.get("points"), (int, float)):
                s += sigil["points"]
            rarity_score = max(0, min(255, int(s)))
        except Exception:
            rarity_score = None
    rarity_tier = ""
    if isinstance(rarity_score, (int, float)):
        try:
            from mememage.rarity import get_rarity_tier
            rarity_tier, _ = get_rarity_tier(int(rarity_score))
        except Exception:
            pass
    _fire_webhooks("conceived", {
        "identifier": result.identifier,
        "content_hash": result.content_hash,
        "url": result.url,
        "distribution": distribution_block,
        "image_path": result.image_path,
        "soul_path": str(soul_path) if soul_path.exists() else None,
        # Rich-context fields — templates can drop these in for
        # narrative Discord/Slack messages without parsing the soul.
        "chain_id": str(chains.current()),
        # Soul stores chain_visibility as an int (0=light_energy, 1=dark_matter).
        # Webhook templates expect a human-readable string for {{chain_visibility}}.
        "chain_visibility": _access.visibility_name(record.get("chain_visibility")) if record.get("chain_visibility") is not None else "",
        "creator_name": record.get("creator_name", ""),
        "key_fingerprint": record.get("key_fingerprint", ""),
        "constellation": record.get("constellation_name", ""),
        # Webhook templates receive {{constellation_star}} as a Greek
        # letter for readable Discord/Slack messages. Records carry an
        # integer constellation_index (ASCII-safe); map at the surface.
        "constellation_star": _bayer_letter(record.get("constellation_index")),
        "rarity_score": rarity_score if rarity_score is not None else "",
        "rarity_tier": rarity_tier,
        # gps_source is a chain property (not stamped on the record).
        # Read from chain config so templates can render it directly.
        "gps_source": chains.get_gps_source(chains.current()),
    })


def _notify_ready(mint_url, image_name="image", gps_source=None):
    """Notify webhooks that a mint session is ready for confirmation.

    gps_source (the bound chain's setting) shapes the summary: only the
    ``phone`` source involves an actual GPS capture step on the device;
    ``machine``/``none`` chains just need the creator to confirm. Passed
    through so templates can also reference ``{{gps_source}}``.
    """
    _fire_webhooks("ready", {
        "mint_url": mint_url,
        "image_name": image_name,
        "gps_source": gps_source or "",
    })


SESSIONS_FILE = Path.home() / ".mememage" / "sessions.json"
TOKEN_EXPIRY_SECONDS = 7 * 24 * 60 * 60  # 7 days — no rush, mint when ready

# Static assets for the in-server dashboard. The dashboard page lives in
# docs/ alongside decoder/validator and reuses the same CSS/JS bundle.
# Only directory prefixes listed here are served; everything else 404s.
#
# Frozen (PyInstaller desktop bundle): docs/ is shipped as bundled data,
# so resolve it under sys._MEIPASS instead of next to the source tree.
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    DOCS_DIR = Path(sys._MEIPASS) / "docs"
else:
    DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
STATIC_PREFIXES = ("/css/", "/js/", "/planets/", "/img/", "/samples/", "/textures/")


# Minimal login page served when /dashboard is hit without a valid
# MINT_API_TOKEN. Plain HTML + tiny inline CSS so it works even before
# the docs/ static prefix is reachable. The form GETs back to
# /dashboard with the token in the query string — same handler then
# matches and serves the real dashboard.
_DASHBOARD_LOGIN_HTML = """\
<!doctype html>
<meta charset="utf-8">
<title>Mememage \u2014 sign in</title>
<style>
  html, body { margin: 0; padding: 0; background: #1c1c20; color: #d0d0d8;
    font-family: ui-monospace, "SF Mono", Menlo, monospace; }
  .wrap { max-width: 28rem; margin: 4rem auto; padding: 2rem 2.5rem;
    background: #2a2a30; border-radius: 12px;
    border: 1px solid rgba(255,255,255,0.06); }
  h1 { font-size: 1.1rem; letter-spacing: 0.08em; margin: 0 0 0.4rem;
    color: #e6e6ec; }
  p { font-size: 0.78rem; line-height: 1.55; color: #a0a0a8; margin: 0.5rem 0; }
  label { display: block; font-size: 0.65rem; letter-spacing: 0.1em;
    text-transform: uppercase; color: #8888a0; margin: 1rem 0 0.3rem; }
  input { width: 100%; box-sizing: border-box;
    background: #181820; color: #e6e6ec; font-family: inherit;
    font-size: 0.85rem; padding: 0.55rem 0.7rem;
    border: 1px solid rgba(255,255,255,0.1); border-radius: 6px; }
  input:focus { outline: none; border-color: #3cc8dc; }
  button { width: 100%; margin-top: 1rem; padding: 0.65rem;
    font-family: inherit; font-size: 0.8rem; font-weight: 700;
    letter-spacing: 0.05em; text-transform: uppercase;
    background: linear-gradient(180deg, #4a3c18 0%, #181410 55%, #14100a 100%);
    color: #d4b87b; border: 1px solid rgba(200,170,80,0.5); border-radius: 6px;
    text-shadow: 0 1px 1px rgba(0,0,0,0.5); cursor: pointer;
    transition: color 0.25s ease, border-color 0.25s ease, box-shadow 0.25s ease; }
  button:hover { color: #e0c070; border-color: rgba(224,192,112,0.75);
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.18), 0 0 12px rgba(220,190,80,0.18); }
  code { background: #181820; padding: 0.1rem 0.3rem; border-radius: 3px;
    color: #c8c8d4; font-size: 0.72rem; }
</style>
<div class="wrap">
  <h1>MEMEMAGE \u2014 SIGN IN</h1>
  <p>This mint server requires a token (<code>MINT_API_TOKEN</code>). Paste it
    below \u2014 you'll land on your clean dashboard URL,
    <code>this-host/&lt;token&gt;</code>, which you can bookmark to skip this
    screen next time.</p>
  <form id="signin">
    <label for="t">MINT_API_TOKEN</label>
    <input id="t" type="password" autocomplete="off" autofocus>
    <button type="submit">Unlock dashboard</button>
  </form>
</div>
<script>
  // Navigate to the clean path form (/<token>) so the address bar ends up
  // tidy and bookmarkable. Falls back to nothing on an empty token.
  document.getElementById('signin').addEventListener('submit', function(e) {
    e.preventDefault();
    var t = document.getElementById('t').value.trim();
    if (t) location.href = '/' + encodeURIComponent(t);
  });
</script>
"""

# Token store: token → {image_path, metadata, created, status, result}
# Persisted to disk so sessions survive server restarts.
_sessions = {}
_mint_lock = threading.Lock()  # Serialize mint execution to protect lineage chain


# Runtime chain-password hold (rung-1 finish). After migration a gated chain
# stores only a PBKDF2 verifier, never the password value. The server holds
# the active chain's password IN MEMORY ONLY for the life of the process,
# entered once via /api/chain/unlock (validated against the verifier), reused
# for every mint into that chain, and CLEARED on chain switch or /api/chain/lock.
# Never written to disk. Keyed by chain id so a stale password can never apply
# to the wrong chain.
_runtime_pw = {}


def _hold_password(chain_id, pw):
    _runtime_pw[chain_id] = pw


def _held_password(chain_id=None):
    from mememage import chains
    cid = chain_id or chains.current()
    return _runtime_pw.get(cid)


def _clear_held(chain_id=None):
    if chain_id is None:
        _runtime_pw.clear()
    else:
        _runtime_pw.pop(chain_id, None)


# Machine-GPS preview cache. The host's IP geolocation is stable, so cache the
# lookup briefly — repeated conception-page loads must not hammer ip-api.com.
_machine_gps_cache = {"ts": 0.0, "coords": None}


def _cached_machine_gps(ttl: float = 300.0):
    """The host's IP-geolocated (lat, lon), cached ``ttl`` seconds. None on miss."""
    now = time.time()
    cached = _machine_gps_cache["coords"]
    if cached and (now - _machine_gps_cache["ts"]) < ttl:
        return cached
    from mememage.gps import fetch_machine_gps
    coords = fetch_machine_gps()
    if coords:
        _machine_gps_cache.update(ts=now, coords=coords)
    return coords


# Public catalog feed cache. Recent conceptions for the souls-face landing page
# — the list only changes when a new soul lands, so a short TTL is plenty and
# keeps a hot page from re-parsing souls on every visit.
_FEED_MAX = 200
# Thumbnails of the ACTUAL conceived image, generated on demand from the minted
# file. Two tiers so a hot catalog never re-resizes AND a restart / local-image
# cull never forces a full-resolution re-download from the Internet Archive:
#   • in-memory LRU (_feed_thumb_cache) — fastest, bounded, lost on restart
#   • on-disk cache (_feed_thumb_dir)   — survives restart + volume cull, so an
#     IA-backed wall pays the multi-MB IA PNG download at most once per tile
#
# The memory cap sits ABOVE the default catalog_limit (500) so a full wall stays
# resident. The old ceiling was 400 (< 500) and cleared WHOLESALE on overflow,
# so any wall past the limit re-generated every tile on the next scroll — the
# thrash this replaces. All memory mutations go through the locked helpers below
# (the server is threaded; bare OrderedDict method calls can race).
_FEED_THUMB_MEM_MAX = 1024
# On-disk tiles are tiny (~20KB JPEG); cap the directory generously so scrolling
# well past the wall stays IA-free while disk stays bounded (~80MB at the cap).
_FEED_THUMB_DISK_MAX = 4096
_feed_thumb_cache = OrderedDict()
_feed_thumb_lock = threading.Lock()


def _feed_thumb_dir():
    """On-disk thumbnail cache dir (``<MEMEMAGE_ROOT>/feed_thumbs``), resolved at
    call time so a test-patched root stays isolated. Created lazily on write."""
    return _chains.MEMEMAGE_ROOT / "feed_thumbs"


def _feed_thumb_path(identifier):
    """Disk-cache path for one tile, or None if ``identifier`` isn't a safe
    single path segment. Identifiers are validated ``<prefix>-<hex>`` upstream,
    but this cache is driven by a public endpoint so never build a path from an
    unvetted string (path-traversal defense)."""
    if (not identifier or "/" in identifier or "\\" in identifier
            or identifier in (".", "..")):
        return None
    return _feed_thumb_dir() / (identifier + ".jpg")


def _feed_thumb_mem_get(identifier):
    """Read the in-memory LRU, refreshing recency on a hit."""
    with _feed_thumb_lock:
        data = _feed_thumb_cache.get(identifier)
        if data is not None:
            _feed_thumb_cache.move_to_end(identifier)
        return data


def _feed_thumb_mem_put(identifier, data):
    """Insert into the in-memory LRU, evicting the oldest entries one at a time
    past the cap (never the wholesale clear that made an over-cap wall re-fetch
    everything)."""
    with _feed_thumb_lock:
        _feed_thumb_cache[identifier] = data
        _feed_thumb_cache.move_to_end(identifier)
        while len(_feed_thumb_cache) > _FEED_THUMB_MEM_MAX:
            _feed_thumb_cache.popitem(last=False)


def _feed_thumb_mem_pop(identifier):
    """Drop one tile from the in-memory LRU only (disk copy retained)."""
    with _feed_thumb_lock:
        _feed_thumb_cache.pop(identifier, None)


def _feed_thumb_disk_get(identifier):
    """Read a cached tile off disk, or None. Warms nothing — the caller decides
    whether to also populate the memory tier."""
    p = _feed_thumb_path(identifier)
    if p is None:
        return None
    try:
        return p.read_bytes()
    except OSError:
        return None


def _feed_thumb_disk_put(identifier, data):
    """Persist a tile to disk, best-effort — a full/unwritable disk must never
    break tile serving. Atomic replace so a concurrent reader never sees a
    half-written JPEG."""
    p = _feed_thumb_path(identifier)
    if p is None:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".jpg.tmp")
        tmp.write_bytes(data)
        tmp.replace(p)
    except OSError as e:
        log.debug("feed thumb disk write failed for %s: %s", identifier, e)


def _feed_thumb_forget(identifier):
    """Drop a tile from BOTH tiers — used when the source image is REPLACED or
    fully WITHDRAWN, so the next request regenerates it. NOT used on a volume
    cull: a culled image still wants its disk thumbnail (that copy is exactly
    what spares the IA re-fetch)."""
    _feed_thumb_mem_pop(identifier)
    p = _feed_thumb_path(identifier)
    if p is not None:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


def _feed_thumb_disk_cull():
    """Bound the on-disk thumbnail cache to the newest ``_FEED_THUMB_DISK_MAX``
    tiles (by write time), unlinking the rest. Best-effort; called from the
    normal cleanup pass. Ordered by mtime, which is set at cache-write time, so
    the most-recently-generated tiles survive — on an IA wall that tracks what's
    been scrolled recently."""
    try:
        d = _feed_thumb_dir()
        if not d.is_dir():
            return
        thumbs = sorted(d.glob("*.jpg"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
        for p in thumbs[_FEED_THUMB_DISK_MAX:]:
            p.unlink(missing_ok=True)
    except OSError as e:
        log.debug("feed thumb disk cull failed: %s", e)


def _soul_on_surface(identifier):
    """True if this conception's soul is still present in the local store.
    Removing the soul (surface cleanup) therefore withdraws it from the catalog
    too, even inside the 7-day window."""
    try:
        from mememage import core
        return (core.soul_store_dir() / (identifier + ".soul")).exists()
    except Exception:
        return False


def _catalog_eligible(s):
    """The minted-image path if this session belongs in the public catalog, else
    None. Qualifies when: completed; the minted image is still on disk (inside
    the cull window); AND its soul is still present (a removed soul withdraws
    it). BOTH light and dark conceptions surface — the conceived image is
    plaintext (only a dark soul's metadata is sealed); the catalog just shows
    everything conceived."""
    if s.get("status") != "completed":
        return None
    r = s.get("result") or {}
    ident = r.get("identifier")
    ip = r.get("image_path") or s.get("image_path")
    if not ident or not ip or not os.path.exists(ip):
        return None
    if not _soul_on_surface(ident):
        return None
    return ip


# ---------------------------------------------------------------------------
# Feed source — generalized (local, ephemeral) vs. IA-backed (permanent wall).
#
# The default feed is the local sessions + blasted images, culled with the
# image (see _public_feed). That's the right generalized behaviour: a surface
# shows what it recently conceived and cycles itself, so it never grows without
# bound and needs no external dependency.
#
# But a creator who anchors their canonical chain on the Internet Archive has
# a permanent, public copy of every light-energy image that never goes away.
# For that surface, `server.json` may set:
#
#     "feed": { "source": "ia", "prefix": "mememage" }
#
# and the feed becomes a permanent wall of that chain: enumerated from IA
# (so it survives a local cull), filtered to the operator's OWN living chain
# (so the namespace's dev/test husks don't leak onto the wall), images served
# straight from IA. `prefix` defaults to the active chain's identifier prefix.
# ---------------------------------------------------------------------------

_IA_DOWNLOAD = "https://archive.org/download"
_ia_feed_cache = {"at": 0.0, "items": None}   # (timestamp, [{identifier, position}])
# The wall is enumerated from the LOCAL living chain (below), which has a new
# star the instant its soul is blasted in — so a fresh mint appears right away
# rather than waiting on IA's search index. The cache is a short load-shield
# only (the chain walk parses every soul), and it's cleared outright the moment
# a new image lands (see _receive_image), so "instant" holds even under it.
_IA_FEED_TTL = 30  # seconds


def _feed_source():
    """Return (source, prefix). source is "ia" only when server.json opts in;
    otherwise "local" (the default ephemeral feed)."""
    cfg = _get_server_config().get("feed") or {}
    if not isinstance(cfg, dict) or cfg.get("source") != "ia":
        return ("local", None)
    prefix = cfg.get("prefix")
    if not prefix:
        try:
            from mememage import chains
            prefix = chains.get_identifier_prefix()
        except Exception:
            prefix = "mememage"
    return ("ia", prefix)


def _invalidate_ia_feed():
    """Drop the wall cache so the next request re-walks the chain. Called when a
    new image lands (a fresh conception blasted in) so the operator's own new
    star shows on the wall immediately, not after the cache TTL."""
    _ia_feed_cache["items"] = None


def _living_chain_positions():
    """Map ``{identifier: outer_position}`` for the operator's OWN light-energy
    chain. Two jobs: the membership filter that keeps the namespace's dev/test
    husks off the wall (dark-matter is excluded here too — its image is never
    on IA), AND the authoritative ORDER. outer_position is the lineage sequence
    (genesis = 0), so sorting by it is correct by construction — unlike IA's
    addeddate, which only *happens* to match while uploads stay sequential and
    would misplace a tile if an item were ever deleted-and-recreated on IA."""
    try:
        from mememage import site_embed
        return {
            r["identifier"]: int(r.get("outer_position") or 0)
            for r in site_embed.walk_living_chain()
            if int(r.get("chain_visibility", 0) or 0) == 0
        }
    except Exception as e:
        log.warning("living-chain walk for feed failed (%s): %s", type(e).__name__, e)
        return {}


def _ia_feed_items(prefix=None):
    """The permanent IA wall, newest-first.

    Enumerated from the operator's OWN living chain (walk_living_chain), NOT
    from IA's search index: the local walk has a new star the instant its soul
    is blasted in, so a fresh mint appears immediately, whereas IA's index lags
    a conception by minutes. Husk-free by construction (the walk follows the
    lineage) and dark-matter-free (light-energy filter in _living_chain_
    positions). Images still come from IA — permanent — via the thumb/full
    endpoints. Ordered by chain position (outer_position desc, genesis last),
    which is the authoritative sequence. Short-cached; cleared on a new image
    (see _receive_image / _invalidate_ia_feed)."""
    now = time.time()
    if _ia_feed_cache["items"] is not None and (now - _ia_feed_cache["at"]) < _IA_FEED_TTL:
        return _ia_feed_cache["items"]
    positions = _living_chain_positions()
    items = [{"identifier": ident, "position": pos} for ident, pos in positions.items()]
    items.sort(key=lambda x: x["position"], reverse=True)
    _ia_feed_cache["at"] = now
    _ia_feed_cache["items"] = items
    return items


def _ia_feed_member(identifier):
    """True if ``identifier`` is in the current IA wall. The thumb/full
    endpoints serve/redirect by identifier, so this gates which identifiers are
    allowed — only the operator's own chain members, never an arbitrary
    archive.org item passed in the URL."""
    _s, prefix = _feed_source()
    return any(it["identifier"] == identifier for it in _ia_feed_items(prefix))


def _ia_feed_thumb_bytes(identifier):
    """A CRISP 460px tile for the IA wall (not IA's tiny auto-thumbnail).

    Prefers the local image the VPS already holds (received/<id>.png) so the
    tile is the same quality the feed always produced; if that image is gone,
    fetches the permanent IA copy once and thumbnails that. Cached by
    identifier inside _feed_thumb_bytes. Returns None on failure (graceful
    tile degradation)."""
    ip = _feed_image_path(identifier)
    if ip:
        return _feed_thumb_bytes(identifier, ip)
    # Local image culled — serve a cached tile if either tier has one before
    # paying for the full-resolution IA download. The disk tier is what makes
    # this cheap across restarts and cache evictions on the permanent wall.
    cached = _feed_thumb_mem_get(identifier)
    if cached is not None:
        return cached
    disk = _feed_thumb_disk_get(identifier)
    if disk is not None:
        _feed_thumb_mem_put(identifier, disk)
        return disk
    # Not cached anywhere — fetch the permanent IA PNG once; _feed_thumb_bytes
    # then persists the resulting tile to disk so this download never repeats.
    import tempfile
    import urllib.request
    url = f"{_IA_DOWNLOAD}/{identifier}/{identifier}.png"
    tmp = None
    try:
        from mememage import net
        with urllib.request.urlopen(url, timeout=20, context=net.default_https_context()) as resp:
            blob = resp.read()
        fd, tmp = tempfile.mkstemp(suffix=".png")
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
        return _feed_thumb_bytes(identifier, tmp)
    except Exception as e:
        log.warning("IA thumb fetch failed for %s (%s): %s",
                    identifier, type(e).__name__, e)
        return None
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _public_feed():
    """Recent public conceptions for the catalog — newest-first, identifier ONLY
    (the session token never leaves).

    Two sources (see _feed_source): the IA-backed permanent wall when the
    surface opts in, else the default local + blasted feed described below,
    which is inherently live + ephemeral (drops off when the image culls or
    the soul is removed)."""
    source, prefix = _feed_source()
    if source == "ia":
        return _ia_feed_items(prefix)
    items = []
    seen = set()
    for _tok, s in list(_sessions.items()):
        if _catalog_eligible(s) is None:
            continue
        ident = (s.get("result") or {}).get("identifier")
        items.append({"identifier": ident, "created": s.get("created", 0)})
        seen.add(ident)
    # Conceptions blasted in from another machine (the http_push channel's
    # push_image option) have no local session, but their full image landed in
    # the received store next to the soul. Merge them so a surface shows what it
    # was sent, not only what it minted here — e.g. a public site that's a
    # gallery of what you conceive on your laptop. Dedup against local sessions
    # so a self-targeted blast still shows once.
    items.extend(_received_feed_items(seen))
    items.sort(key=lambda x: x.get("created", 0), reverse=True)
    return items


def _received_image_path(identifier):
    """Path to a blasted-in feed image (``<received>/<id>.png``), or None.
    Gated on the soul still being present — same withdraw-on-removal rule as
    session tiles."""
    try:
        from mememage import core
        img = core.soul_store_dir() / (identifier + ".png")
        if img.exists() and _soul_on_surface(identifier):
            return str(img)
    except Exception:
        pass
    return None


def _received_feed_items(seen):
    """Feed entries for conceptions blasted in with their image — a received
    ``<id>.png`` whose soul is present and which isn't already shown via a local
    session. ``created`` is the image's mtime (when it landed here); the caller
    sorts newest-first."""
    items = []
    try:
        from mememage import core
        rdir = core.soul_store_dir()
    except Exception:
        return items
    if not rdir.is_dir():
        return items
    for img in rdir.glob("*.png"):
        ident = img.stem
        if ident in seen or not _soul_on_surface(ident):
            continue
        try:
            items.append({"identifier": ident, "created": img.stat().st_mtime})
        except OSError:
            continue
    return items


def _feed_image_path(identifier):
    """Image path for a feed identifier, else None. Prefers a catalog-eligible
    local session (the full staged mint image), then falls back to a blasted-in
    received image. Same public/light, soul-present gate as the listing, so a
    dark or withdrawn conception can't be fetched either way."""
    for _tok, s in list(_sessions.items()):
        if (s.get("result") or {}).get("identifier") == identifier:
            ip = _catalog_eligible(s)
            if ip:
                return ip
    return _received_image_path(identifier)


_BAR_ROWS = 2  # the steganographic bar is exactly 2 rows tall (non-negotiable)


def _resample_lanczos():
    """LANCZOS across Pillow versions (Resampling enum on 9.1+, attr before)."""
    from PIL import Image
    res = getattr(Image, "Resampling", None)
    return getattr(res, "LANCZOS", None) if res else getattr(Image, "LANCZOS", 1)


def _feed_thumb_bytes(identifier, image_path, size=460):
    """A square JPEG thumbnail of the conceived image, cached by identifier. The
    bar is cropped off first (it must never show in a tile), then a centered
    square is taken. None on any failure (missing Pillow, unreadable image) so
    the tile degrades gracefully."""
    cached = _feed_thumb_mem_get(identifier)
    if cached is not None:
        return cached
    # A prior run (or this run, pre-eviction) may already hold the tile on disk —
    # skip the resize entirely, and on an IA wall skip re-downloading the source.
    # Keyed by identifier alone, which is safe because every caller uses the
    # default size (460px); revisit if a second size is ever requested.
    disk = _feed_thumb_disk_get(identifier)
    if disk is not None:
        _feed_thumb_mem_put(identifier, disk)
        return disk
    try:
        import io
        from PIL import Image
        img = Image.open(image_path).convert("RGB")
        w, h = img.size
        # 1. Drop the steganographic bar (bottom rows) so it never appears.
        if h > _BAR_ROWS + 4:
            img = img.crop((0, 0, w, h - _BAR_ROWS))
            w, h = img.size
        # 2. Centered square crop of what remains.
        side = min(w, h)
        l, t = (w - side) // 2, (h - side) // 2
        sq = img.crop((l, t, l + side, t + side)).resize((size, size), _resample_lanczos())
        buf = io.BytesIO()
        sq.save(buf, format="JPEG", quality=82)
        data = buf.getvalue()
    except Exception:
        return None
    _feed_thumb_mem_put(identifier, data)
    _feed_thumb_disk_put(identifier, data)
    return data


def _withdraw_conception_image(identifier):
    """Deleting a soul also reclaims its minted image + drops the session, so the
    conception is fully withdrawn (disk freed), not merely hidden by the catalog's
    soul-present gate. Returns (images_unlinked, sessions_dropped). Persists the
    session change so it survives a restart, and drops any cached thumbnail."""
    img_n = 0
    drop = []
    for tok, s in list(_sessions.items()):
        if (s.get("result") or {}).get("identifier") != identifier:
            continue
        ip = (s.get("result") or {}).get("image_path") or s.get("image_path")
        if ip:
            try:
                if os.path.isfile(ip):
                    os.unlink(ip)
                    img_n += 1
            except OSError:
                pass
        drop.append(tok)
    for tok in drop:
        _sessions.pop(tok, None)
    # Also reclaim a blasted-in feed image (push_image) if one landed here.
    try:
        from mememage import core
        rimg = core.soul_store_dir() / (identifier + ".png")
        if rimg.is_file():
            rimg.unlink()
            img_n += 1
    except Exception:
        pass
    _feed_thumb_forget(identifier)
    if drop:
        _save_sessions()
    return img_n, len(drop)


def _active_password_status(cid=None):
    """Resolve a chain's gating posture for the dashboard (active by default).

    gated         — chain has a verifier (or lingering legacy plaintext)
    unlocked      — server currently holds the runtime password for it
    needs_password— gated AND the password isn't resolvable (no held value,
                    no MEMEMAGE_PASSWORD env, no legacy plaintext). When true
                    a dark chain can't mint until the user unlocks.
    """
    from mememage import chains
    cid = cid or chains.current()
    info = chains.info(cid)
    has_verifier = isinstance(info.get("password_verifier"), dict)
    has_legacy = bool(info.get("password"))
    gated = has_verifier or has_legacy
    held = _runtime_pw.get(cid) is not None
    env = bool(os.environ.get("MEMEMAGE_PASSWORD"))
    resolvable = held or env or has_legacy
    return {
        "password_gated": gated,
        "password_unlocked": held,
        "password_needs_unlock": gated and not resolvable,
    }


# Chain readiness — one semantic state used by the chain badge everywhere
# (dashboard + the conception page). Precedence high->low: notready beats
# pending beats nopayload beats ready, so the dot always shows the most
# important thing. Cheap + per-chain (no network).
#   notready  — can't conceive: dark chain with no resolvable password, OR
#               the chain has no sealed Age yet.
#   pending   — payload draft differs from the built/applied manifest (a
#               change is staged for the next Age but not rebuilt/applied).
#   nopayload — provenance works, but no payload distribution configured
#               (no layers or no entry sources). Perfectly valid, just flagged.
#   ready     — sealed, conceivable, payload clean.
_READINESS_WORD = {
    "ready": "Ready",
    "nopayload": "No payload",
    "pending": "Update pending",
    "notready": "Not ready",
}


def _chain_readiness(cid=None):
    from mememage import chains, chain_config, payload as _payload
    cid = cid or chains.current()
    info = chains.info(cid)
    # --- notready: gated-but-unresolvable ---
    pw = _active_password_status(cid)
    if pw.get("password_needs_unlock"):
        return "notready"
    try:
        cfg = chain_config.load(cid)
        has_payload = cfg.has_payload()
    except Exception:
        has_payload = False
    # --- provenance-only chains are always conceivable (sealed or not) ---
    # No payload to carry → no seal required (mirrors _require_chain_sealed).
    # "No payload" here means "provenance works, nothing to distribute" —
    # a valid, flagged state, not an error.
    if not has_payload:
        return "nopayload"
    # --- payload-carrying chains must seal before they can conceive ---
    # A seal must EXIST and PARSE (dict with the core Age fields) — not just
    # be present. Catches a truncated/empty/corrupt sealed_chunks.json that
    # would otherwise show "ready" then fail at mint when the seal is read.
    try:
        _seal_path = chains.path("records", cid).parent / "sealed_chunks.json"
        _seal = json.loads(_seal_path.read_text(encoding="utf-8")) if _seal_path.exists() else None
        sealed = isinstance(_seal, dict) and "age" in _seal
    except Exception:
        sealed = False
    if not sealed:
        return "notready"
    # --- pending: a BUILT payload drifted from its current sources ---
    # "Update pending" means the payload was built before AND a source has
    # changed since (genuine drift). NOT pending: a missing manifest (configured
    # but never built — nothing changed, so the build is just an optional step).
    #
    # payload.status() reads source-vs-built drift but only for the ACTIVE
    # chain (its readers load chain_config.load() / payload_dir() without a cid).
    # So we only assert drift for the active chain; for others we can't verify
    # it, and we don't claim "pending" we can't confirm.
    try:
        if cid == chains.current():
            st = _payload.status() or {}
            if not st.get("manifest_missing"):
                for entry in (st.get("statuses") or {}).values():
                    state = entry.get("status") if isinstance(entry, dict) else entry
                    if state in ("drifted", "missing_payload", "missing_source"):
                        return "pending"
    except Exception:
        pass
    return "ready"


def _chain_name(cid):
    if not cid:
        return None
    try:
        from mememage import chains
        n = chains.info(cid).get("name")
        return n if (n and n != cid) else None
    except Exception:
        return None


def _chain_visibility(cid):
    if not cid:
        return None
    try:
        from mememage import chains
        return chains.info(cid).get("visibility")
    except Exception:
        return None


def _chain_badge_html(cid=None):
    """Server-rendered chain badge — the PILL, matching dashboard.js
    ChainBadge.compact(). Used by the conception page so the creator always
    sees which chain they're on. Friendly name only (id lives in Config)."""
    from mememage import chains
    import html as _html
    cid = cid or chains.current()
    info = chains.info(cid)
    name = info.get("name") or ""
    primary = _html.escape(str(name if (name and name != cid) else cid))
    tip = _html.escape(str(cid) + (" · " + name if (name and name != cid) else ""))
    vis = "dark" if info.get("visibility") == "dark_matter" else "light"
    state = _chain_readiness(cid)
    word = _READINESS_WORD.get(state, state)
    # NOTE: the inner row MUST be wrapped in .chain-badge-head. The compact
    # badge is flex-direction:column; the single-line layout lives entirely
    # in .chain-badge-head (a flex row). Without that wrapper the dot / name /
    # vis / chip become column children of the pill and stack vertically,
    # ballooning the badge (regression seen on the conception page). Mirrors
    # dashboard.js ChainBadge.compact() exactly.
    return (
        '<span class="chain-badge compact" title="' + tip + '">'
        '<span class="chain-badge-head">'
        '<span class="chain-dot" data-state="' + state + '"></span>'
        '<span class="chain-badge-body">'
        '<span class="chain-badge-official">' + primary + '</span>'
        '</span>'
        '<span class="chain-vis">' + vis + '</span>'
        '<span class="chain-state-chip" data-state="' + state + '">' + word + '</span>'
        '</span>'
        '</span>'
    )


def _conception_channels():
    """The channels a conception will actually blast to, in blast() order.

    Mirrors channels.blast()'s fireable filter exactly: every enabled AND
    configured channel in the active profile's channels.json. Returns a list
    of dicts {id, name, type, primary}. The id is the stable slug (what
    appears on viewer certificates); name is the local friendly label. Empty
    list = nothing will publish.
    """
    try:
        from mememage.channels import load_channels
    except Exception:
        return []
    out = []
    for ch in load_channels():
        try:
            if not (ch.enabled and ch.is_configured()):
                continue
            out.append({
                "id": ch.id,
                "name": ch.name or ch.DISPLAY_NAME or ch.id,
                # Human "where the soul lands" — the host/domain for
                # http_push (e.g. souls.example.com / localhost), the
                # service name for IA/Zenodo. Not the internal slug.
                "surface": ch.display_surface(),
                "type": ch.TYPE,
                "primary": bool(ch.primary),
            })
        except Exception:
            continue
    # Primary first, then by id, so the canonical surface leads.
    out.sort(key=lambda c: (not c["primary"], c["id"]))
    return out


def _conception_channels_html():
    """Server-rendered 'Blasting to' strip for the conception page. Lists the
    enabled+configured channels (id leads, friendly name follows). One chip
    per channel; the primary surface is flagged. Empty => a soft warning row."""
    import html as _html
    chans = _conception_channels()
    if not chans:
        return (
            '<div class="conception-channels conception-channels-empty">'
            '<span class="conception-channels-label">Target surfaces</span>'
            '<span class="conception-channels-none">no surface configured — '
            'this conception cannot publish</span>'
            '</div>'
        )
    chips = []
    for c in chans:
        cid = _html.escape(str(c["id"]))
        # Show the human destination (domain / localhost / service name),
        # not the internal slug. The slug stays as the hover title since it
        # is what appears on the viewer's certificate.
        surface = _html.escape(str(c.get("surface") or c["id"]))
        prim = ('<span class="conception-channel-primary" title="Primary surface '
                '— its URL becomes the soul’s canonical link">primary</span>'
                if c["primary"] else '')
        chips.append(
            '<span class="conception-channel" title="' + cid + '">'
            '<span class="conception-channel-id">' + surface + '</span>'
            + prim +
            '</span>'
        )
    return (
        '<div class="conception-channels">'
        '<span class="conception-channels-label">Target surfaces</span>'
        '<span class="conception-channels-list">' + ''.join(chips) + '</span>'
        '</div>'
    )


def _doctor_check(label, status, detail):
    """One diagnostic row: status is 'ok' | 'warn' | 'fail'."""
    return {"label": label, "status": status, "detail": detail}


def _proxy_body_limit_mib(backend_port):
    """Smallest ``client_max_body_size`` (in MiB) among nginx vhosts that proxy
    to our backend (``127.0.0.1:<backend_port>``). Returns None if no readable
    nginx config proxies to us — a BYO/remote/Cloudflare proxy we can't inspect.
    A vhost that proxies to us with NO directive uses nginx's 1 MiB default.
    """
    import glob
    files = glob.glob("/etc/nginx/sites-enabled/*")
    size_re = re.compile(r"client_max_body_size\s+(\d+)\s*([kKmMgG]?)\s*;")
    proxy_re = re.compile(r"proxy_pass[^;]*127\.0\.0\.1:%d" % int(backend_port))
    limits = []
    for f in files:
        try:
            txt = open(f, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        if not proxy_re.search(txt):
            continue  # this vhost doesn't proxy to our backend
        m = size_re.search(txt)
        if m:
            num, unit = int(m.group(1)), m.group(2).lower()
            limits.append({"": num / (1024 * 1024), "k": num / 1024,
                           "m": float(num), "g": num * 1024.0}[unit])
        else:
            limits.append(1.0)  # no directive → nginx's 1 MiB default
    return min(limits) if limits else None


def _run_doctor():
    """Deployment preflight — surfaces the traps that make a public mint
    server unreachable/untrusted (bare-IP domain, unresolved DNS, self-signed
    or name-mismatch cert, open admin API on a public host, a proxy body cap
    that 413s large uploads). Read-only probes, safe to run anytime. Returns
    {checks: [...], summary: worst-status}."""
    import socket
    import ssl as _ssl

    checks = []
    config = _get_server_config()
    domain = (config.get("domain") or "").strip()
    port = config.get("port") or 8443
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 8443
    host = domain
    # Split an optional :port WITHOUT mangling a bare IPv6 literal (which has
    # multiple colons). Bracketed form [::1]:8444 is parsed; a bare IPv6 like
    # 2001:db8::1 (>1 colon, no brackets) is left intact for _is_ip to flag.
    if domain.startswith("["):
        _h, _, _rest = domain[1:].partition("]")
        host = _h
        if _rest.startswith(":"):
            try:
                port = int(_rest[1:])
            except ValueError:
                pass
    elif domain.count(":") == 1:  # host:port
        host, _, p = domain.rpartition(":")
        try:
            port = int(p)
        except ValueError:
            host = domain

    def _is_ip(s):
        # ipaddress handles IPv4 AND IPv6 literals (inet_aton was v4-only,
        # so a bare IPv6 domain slipped through as 'ok').
        import ipaddress
        try:
            ipaddress.ip_address(s)
            return True
        except ValueError:
            return False

    # 1. Domain
    if not domain:
        checks.append(_doctor_check(
            "Domain", "warn",
            "No domain set (auto-detect). A real domain is required for a "
            "publicly-trusted certificate."))
    elif _is_ip(host):
        checks.append(_doctor_check(
            "Domain", "fail",
            f"'{host}' is a bare IP. No CA — Let's Encrypt included — "
            "issues trusted certs for IP addresses. Point a domain at this host."))
    else:
        checks.append(_doctor_check("Domain", "ok", host))

    # 2. DNS resolution
    resolved = []
    if host and not _is_ip(host):
        try:
            resolved = sorted({ai[4][0] for ai in
                               socket.getaddrinfo(host, None, socket.AF_INET)})
            checks.append(_doctor_check(
                "DNS", "ok", f"{host} → {', '.join(resolved)}"))
        except OSError as e:
            checks.append(_doctor_check(
                "DNS", "fail",
                f"{host} does not resolve ({e}). Add an A record pointing to "
                "this host's public IP."))

    # 3. Trusted TLS — connect exactly the way a browser / iPhone would
    #    (full chain + hostname verification against the system trust store).
    if host and not _is_ip(host) and resolved:
        try:
            ctx = _ssl.create_default_context()
            with socket.create_connection((host, port), timeout=8) as sock:
                with ctx.wrap_socket(sock, server_hostname=host) as ss:
                    peer = ss.getpeercert()
            exp = peer.get("notAfter", "")
            checks.append(_doctor_check(
                "TLS trusted", "ok",
                f"browser-trusted cert on {host}:{port}"
                + (f" (expires {exp})" if exp else "")))
        except _ssl.SSLCertVerificationError as e:
            checks.append(_doctor_check(
                "TLS trusted", "fail",
                f"certificate NOT trusted on {host}:{port} — "
                f"{getattr(e, 'reason', None) or e}. Self-signed or "
                "name-mismatch certs fail on iOS Safari. Get a Let's Encrypt "
                "cert for this domain."))
        except Exception as e:
            checks.append(_doctor_check(
                "TLS trusted", "warn",
                f"couldn't reach {host}:{port} to check the cert ({e})."))

    # 4. Admin auth exposure on a public host
    try:
        from mememage.config import _load_dotenv
        _load_dotenv()
    except Exception:
        pass
    if os.environ.get("MINT_API_TOKEN"):
        checks.append(_doctor_check("Admin auth", "ok", "MINT_API_TOKEN is set"))
    elif domain and not _is_ip(host):
        checks.append(_doctor_check(
            "Admin auth", "fail",
            "No MINT_API_TOKEN on a public domain — the dashboard/config "
            "API is open to anyone who finds it. Set MINT_API_TOKEN."))
    else:
        checks.append(_doctor_check(
            "Admin auth", "warn",
            "No MINT_API_TOKEN (acceptable for local/private use only)."))

    # 5. Surfaces — at least one enabled + set-up, or conceptions can't publish
    try:
        from mememage import channels as _ch
        _live = [c for c in _ch.load_channels() if c.enabled and c.is_configured()]
    except Exception as e:
        _live = None
    if _live is None:
        checks.append(_doctor_check("Surfaces", "warn", "couldn't read surface config."))
    elif _live:
        checks.append(_doctor_check(
            "Surfaces", "ok",
            f"{len(_live)} enabled + set up ({', '.join(c.id for c in _live)})"))
    else:
        checks.append(_doctor_check(
            "Surfaces", "fail",
            "No enabled + set-up surface — conceptions have nowhere to publish "
            "and will fail at mint. Enable one in Config \u2192 Surfaces."))

    # Proxy upload limit — a reverse proxy (nginx, from the bundled vps-setup)
    # caps request bodies; below the backend's payload cap, large uploads 413
    # at the proxy before reaching us. Read nginx's limit on vhosts proxying to
    # the backend. Silent for non-nginx proxies (can't inspect Caddy/Cloudflare).
    try:
        _pl = _proxy_body_limit_mib(port)
    except Exception:
        _pl = None
    if _pl is not None:
        if _pl < 100:
            checks.append(_doctor_check(
                "Proxy upload limit", "warn",
                f"nginx caps request bodies at ~{_pl:.0f} MiB — large payload uploads "
                f"will fail with HTTP 413 before reaching the mint server. Raise "
                f"client_max_body_size to ≥ 600m on the vhost(s) proxying to "
                f"127.0.0.1:{port}, then `nginx -t && systemctl reload nginx`."))
        else:
            checks.append(_doctor_check(
                "Proxy upload limit", "ok",
                f"nginx allows ~{_pl:.0f} MiB request bodies"))

    summary = "ok"
    for c in checks:
        if c["status"] == "fail":
            summary = "fail"
            break
        if c["status"] == "warn":
            summary = "warn"
    return {"checks": checks, "summary": summary}



def _scrub_completed_gps(sessions):
    """Drop raw coordinates from COMPLETED sessions before persisting.

    A session's lat/lon is needed only while the mint is in flight. Once it
    completes, the coordinates are already sealed into the soul record (time-
    locked, and password-locked on gated chains) -- the copy sitting in
    sessions.json is just exhaust, and it's the very data the access layer
    exists to protect. We keep a small ``gps_recorded`` boolean so the
    dashboard can still show "GPS captured" without holding the coordinates.
    Returns a deep-ish copy safe to serialize; the in-memory session keeps
    its values for the life of the process.
    """
    import copy
    out = {}
    for tok, sess in sessions.items():
        if isinstance(sess, dict) and sess.get("status") == "completed" and "gps" in sess:
            sess = copy.copy(sess)
            g = sess.get("gps")
            had_coords = isinstance(g, dict) and ("lat" in g or "lon" in g)
            sess.pop("gps", None)
            sess["gps_recorded"] = bool(had_coords)
        out[tok] = sess
    return out


def _save_sessions():
    """Persist sessions to disk (owner-only; completed-session GPS scrubbed)."""
    try:
        SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SESSIONS_FILE.write_text(json.dumps(_scrub_completed_gps(_sessions), indent=2))
        try:
            os.chmod(str(SESSIONS_FILE), 0o600)  # raw GPS + metadata: owner-only
        except OSError:
            pass
    except Exception as e:
        log.warning("Failed to save sessions: %s", e)


def _load_sessions():
    """Load sessions from disk on startup."""
    global _sessions
    try:
        if SESSIONS_FILE.exists():
            _sessions = json.loads(SESSIONS_FILE.read_text())
            log.info("Loaded %d sessions from disk", len(_sessions))
    except Exception as e:
        log.warning("Failed to load sessions: %s", e)
        _sessions = {}


# The public catalog ("wall of art") is culled by VOLUME, not time: keep the
# newest `catalog_limit` conceptions and evict the oldest beyond it, so the wall
# holds a bounded, ever-fresh set instead of aging everything out on a clock
# (which "didn't land right" — Andy wanted the wall to persist). 0 = unlimited
# (disk then bounded only by what you mint). server.json: ``catalog_limit``.
# Drafts (pending/failed) are NOT the wall — they still age out by TTL.
CATALOG_LIMIT_DEFAULT = 500


def _catalog_limit():
    """Max completed conceptions retained. 0 = unlimited."""
    try:
        return max(0, int(_get_server_config().get("catalog_limit", CATALOG_LIMIT_DEFAULT)))
    except (TypeError, ValueError):
        return CATALOG_LIMIT_DEFAULT


def _cleanup_expired():
    """Reap pending/failed drafts by TTL, and cull COMPLETED conceptions by
    VOLUME (keep the newest ``catalog_limit``, evict the oldest).

    Drafts are transient working state, so they still age out on a 7-day clock;
    the catalog is the wall of art, so it's bounded by COUNT, not time — at any
    moment it holds at most ``catalog_limit`` conceptions, the freshest ones.
    Each evicted session has its staged image unlinked, so disk stays bounded by
    the limit (plus drafts within the window).

    Event-driven (startup + request handlers), so eviction happens on the next
    server activity. Snapshot-iterated — the threaded server mutates _sessions
    from other request threads.
    """
    now = time.time()
    snapshot = list(_sessions.items())
    remove = set()
    # 1. Pending / failed drafts expire by time — they're not part of the wall.
    for t, s in snapshot:
        if s.get("status") != "completed" and now - s.get("created", 0) > TOKEN_EXPIRY_SECONDS:
            remove.add(t)
    # 2. Completed conceptions cull by volume — only the newest `limit` survive.
    limit = _catalog_limit()
    if limit > 0:
        completed = sorted(
            (kv for kv in snapshot if kv[1].get("status") == "completed"),
            key=lambda kv: kv[1].get("created", 0), reverse=True)
        for t, _s in completed[limit:]:
            remove.add(t)
    if remove:
        for t in remove:
            img = _sessions.get(t, {}).get("image_path")
            if img:
                try:
                    Path(img).unlink(missing_ok=True)
                except Exception as e:
                    log.warning("Cleanup: failed to remove %s: %s", img, e)
            _sessions.pop(t, None)
        _save_sessions()

    # 3. Blasted-in feed images (push_image) cull by volume too — keep the
    #    newest `limit` received <id>.png, unlink the rest. The SOUL stays (the
    #    permanent, verifiable record); only the bounded feed-display image is
    #    evicted, so a receiving surface's wall stays bounded like its own mints.
    if limit > 0:
        try:
            from mememage import core
            rdir = core.soul_store_dir()
            if rdir.is_dir():
                imgs = sorted(rdir.glob("*.png"),
                              key=lambda p: p.stat().st_mtime, reverse=True)
                for p in imgs[limit:]:
                    p.unlink(missing_ok=True)
                    # Keep the disk thumbnail — a culled full image is exactly
                    # when the cached tile spares an IA re-download. Only evict
                    # the in-memory copy to reclaim RAM.
                    _feed_thumb_mem_pop(p.stem)
        except Exception as e:
            log.warning("Cleanup: received-image cull failed: %s", e)
    # Bound the on-disk thumbnail cache independently of the image cull.
    _feed_thumb_disk_cull()


def _cleanup_orphan_uploads():
    """Sweep ``UPLOAD_DIR`` for files that no live session references.

    Defense against crashes / race conditions where a session record
    is dropped before its image is unlinked. Runs once at startup so
    cold-boot reclaims any stragglers without making every request
    pay the disk-walk cost.
    """
    if not UPLOAD_DIR.is_dir():
        return
    referenced = {Path(s.get("image_path") or "").resolve()
                  for s in list(_sessions.values())
                  if s.get("image_path")}
    referenced.discard(Path(""))
    removed = 0
    for p in UPLOAD_DIR.iterdir():
        if not p.is_file():
            continue
        try:
            if p.resolve() in referenced:
                continue
            p.unlink(missing_ok=True)
            removed += 1
        except Exception as e:
            log.warning("Orphan upload sweep: %s failed: %s", p, e)
    if removed:
        log.info("Cleaned %d orphan upload(s) from %s", removed, UPLOAD_DIR)


def _migrate_records_to_store(chains_root=None, store=None):
    """One-time: copy legacy per-chain souls (``chains/*/records/*.soul``) into
    the flat store (``~/.mememage/received``) so collapsing to a single store
    loses nothing. Idempotent — skips souls already present; preserves any
    subdir nesting. Old records/ dirs are left in place (harmless).
    """
    try:
        if chains_root is not None:
            root = Path(chains_root)
        else:
            from mememage import chains as _chains
            root = _chains.CHAINS_ROOT
        if not root.is_dir():
            return
    except Exception:
        return
    import shutil as _shutil
    store = Path(store) if store else _chains.received_dir()
    store.mkdir(parents=True, exist_ok=True)
    migrated = 0
    for cdir in root.iterdir():
        recs = cdir / "records"
        if not recs.is_dir():
            continue
        for soul in recs.rglob("*.soul"):
            try:
                dest = store / soul.relative_to(recs)  # keeps any subdir nesting
                if dest.exists():
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                _shutil.copy2(str(soul), str(dest))
                migrated += 1
            except Exception as e:
                log.warning("records->store migration: %s failed: %s", soul, e)
    if migrated:
        log.info("Migrated %d legacy soul(s) from records/ into the flat store", migrated)


def _cleanup_orphan_payload_uploads(chains_root=None):
    """Sweep each chain's ``uploads/`` for payload source files that no chain
    entry references. The dashboard deletes orphans client-side on ×/re-upload,
    but a crash or an off-draft removal can leave one behind — and payload
    sources can be large, so reclaim them at startup.

    References are gathered across ALL chains (a source path could be shared),
    so a file still in use anywhere is never removed. A chain.json we can't
    parse is treated conservatively: its own uploads are kept. ``.part`` temp
    files are left to ``_cleanup_stale_part_files`` (which age-gates them).
    """
    try:
        from mememage import chains as _chains
    except Exception:
        return
    root = Path(chains_root) if chains_root else _chains.CHAINS_ROOT
    if not root.is_dir():
        return
    referenced = set()
    chain_dirs = [d for d in root.iterdir() if d.is_dir()]
    for cdir in chain_dirs:
        cj = cdir / "chain.json"
        up = cdir / "uploads"
        try:
            data = json.loads(cj.read_text()) if cj.is_file() else {}
        except Exception:
            # Unparseable chain.json — keep all of its uploads (don't risk it).
            if up.is_dir():
                for f in up.iterdir():
                    try:
                        referenced.add(f.resolve())
                    except Exception:
                        pass
            continue
        for ent in (data.get("entries") or {}).values():
            for src in (ent.get("sources") or []):
                if src:
                    try:
                        referenced.add(Path(src).resolve())
                    except Exception:
                        pass
    removed, freed = 0, 0
    for cdir in chain_dirs:
        up = cdir / "uploads"
        if not up.is_dir():
            continue
        for f in up.iterdir():
            if not f.is_file() or f.suffix == ".part":
                continue
            try:
                if f.resolve() in referenced:
                    continue
                sz = f.stat().st_size
                f.unlink()
                removed += 1
                freed += sz
            except Exception as e:
                log.warning("Orphan payload sweep: %s failed: %s", f, e)
    if removed:
        log.info("Cleaned %d orphan payload upload(s), freed %.1f MiB",
                 removed, freed / (1024 * 1024))


def _cleanup_stale_part_files(roots=None):
    """Reap leftover ``.part`` stream temp files (soul-receive + payload
    upload). Normal aborts unlink their own temp; only a hard process kill
    mid-stream leaks one. Remove .part files older than an hour — a live
    upload's temp is young, so the age gate never races an in-flight stream.
    """
    if roots is None:
        from mememage import chains as _chains
        roots = [_chains.received_dir()]
        try:
            if _chains.CHAINS_ROOT.is_dir():
                roots += [d / "uploads" for d in _chains.CHAINS_ROOT.iterdir() if d.is_dir()]
        except Exception:
            pass
    cutoff = time.time() - 3600
    removed = 0
    for r in roots:
        r = Path(r)
        if not r.is_dir():
            continue
        for p in r.glob("*.part"):
            try:
                if p.is_file() and p.stat().st_mtime < cutoff:
                    p.unlink()
                    removed += 1
            except Exception as e:
                log.warning("Stale .part sweep: %s failed: %s", p, e)
    if removed:
        log.info("Cleaned %d stale .part file(s)", removed)


def _self_pointing_http_push(base_url, own_hosts):
    """True if ``base_url``'s host is one of THIS box's own addresses.

    A self-push http_push channel targets this very server; its host may be
    loopback or any public name/IP the box answers on (``own_hosts``). A channel
    pointing at a DIFFERENT peer (a friend's mememage host) must NOT match — we
    only ever rewrite the box's own self-pointing targets to loopback.
    """
    if not base_url:
        return False
    try:
        host = (urlparse(base_url).hostname or "").strip().lower()
    except Exception:
        return False
    return bool(host) and host in own_hosts


def _reconcile_self_push_channels(chans, self_put, own_hosts):
    """Force every SELF-POINTING http_push channel's base_url to loopback:port.

    Self-pointing = id ``self`` (the seeded default) OR a base_url whose host is
    one of the box's own addresses. The PUT target MUST be loopback at the
    server's real scheme+port: a cloud box can't reach its own public IP/domain
    from inside (no NAT hairpin → the PUT wedges), and loopback is the only
    address whose port we control. Channels pointing at a real peer are left
    untouched. Mutates ``chans`` in place; returns True if anything changed.

    This is the self-heal for the genesis-blocking wedge where a self-push
    surface kept a public-IP / drifted-port base_url that no boot ever fixed
    (the old reconcile only matched id ``self``).
    """
    changed = False
    for c in chans:
        if c.get("type") != "http_push":
            continue
        cfg = c.setdefault("config", {})
        if c.get("id") == "self" or _self_pointing_http_push(cfg.get("base_url"), own_hosts):
            if cfg.get("base_url") != self_put or not cfg.get("accept_self_signed"):
                cfg["base_url"] = self_put
                cfg["accept_self_signed"] = True
                changed = True
    return changed


def _seed_first_run_defaults(port: int, scheme: str = "https") -> None:
    """Seed minimum config so a fresh-install dashboard lands mostly-green.

    Each step is idempotent — already-configured installs are no-ops.
    Failures are logged but never block startup; the dashboard's
    welcome checklist surfaces anything still missing.

    Steps:
      1. Default Ed25519 profile if none exist (creator name pulled
         from $USER, falling back to "default"). Signing-step ✓.
      2. Default chain (``aries``) via chains.create() if no chains
         exist on disk. Chain-step ✓.
      3. Self-pointing http_push channel if no http_push channel is
         configured. Auth resolves via MINT_API_TOKEN through the
         self-push detection added in the channels framework, so
         this "just works" on localhost. Distribution-step ✓.
    """
    # --- Profile ---
    try:
        from mememage import profiles, signing
        if signing.is_signing_available():
            existing = [p for p in profiles.list_profiles() if p.get("has_private_key")]
            if not existing:
                creator = (os.environ.get("USER") or os.environ.get("USERNAME") or "default").strip()
                # USER might be a system account name like "andy" — that's
                # fine as a placeholder, the dashboard makes it editable.
                try:
                    info = profiles.create("default", name=creator)
                    log.info("First-run: seeded default profile (creator=%s, fp=%s)",
                             creator, info.get("fingerprint"))
                except FileExistsError:
                    pass  # raced with another startup
                except Exception as e:
                    log.warning("First-run: profile seed failed: %s", e)
    except Exception as e:
        log.warning("First-run: profile section failed: %s", e)

    # --- Chain ---
    try:
        from mememage import chains
        if not chains.list_chains():
            try:
                chains.create(chains.DEFAULT_CHAIN_ID, name="Aries")
                log.info("First-run: seeded default chain %r", chains.DEFAULT_CHAIN_ID)
            except FileExistsError:
                pass
            except Exception as e:
                log.warning("First-run: chain seed failed: %s", e)
    except Exception as e:
        log.warning("First-run: chain section failed: %s", e)

    # --- Channel (self-pointing http_push) ---
    # The self-push channel PUTs souls to THIS server, so its target must be
    # LOOPBACK at the server's CURRENT scheme + port. Two failure modes this
    # avoids:
    #   • Public host: a cloud box can't reach its own public IP/domain from
    #     inside (no NAT hairpin) — the PUT hangs, cascading into a wedge.
    #   • Stale scheme: the first-run seed happens BEFORE vps-setup installs
    #     the cert, so it freezes http:// even though the server later runs
    #     https — plain HTTP to a TLS port hangs.
    # So we seed on first run AND reconcile on every boot (self-healing).
    try:
        from mememage import channels as _ch
        self_put = f"{scheme}://127.0.0.1:{port}/api/souls"
        self_host = (os.environ.get("MEMEMAGE_SELF_HOST") or "").strip()
        # The box's own addresses — a self-push channel may point at any of
        # them (loopback, the public domain/IP from MEMEMAGE_SELF_HOST="dom,ip",
        # or the souls host). All get rewritten to loopback; a real peer host
        # does not match and is left alone.
        own_hosts = {"127.0.0.1", "localhost", "::1"}
        for h in self_host.split(","):
            h = h.strip().lower()
            if h:
                own_hosts.add(h)
        try:
            _scfg = _get_server_config()
            for _k in ("domain", "souls_domain"):
                _v = (_scfg.get(_k) or "").strip().lower()
                if _v:
                    own_hosts.add(_v)
        except Exception:
            pass

        raw = _ch._load_raw()
        chans = raw.get("channels", [])
        if _reconcile_self_push_channels(chans, self_put, own_hosts):
            _ch.save_raw(raw)
            log.info("Reconciled self-pointing http_push base_url(s) -> %s", self_put)
        elif self_host and not any(c.get("type") == "http_push" for c in chans):
            # First run in a SERVER context (MEMEMAGE_SELF_HOST is set by the
            # server at startup — a clean proxy for "we're a live server, not
            # a CLI/library call"), no http_push yet: seed self as primary
            # so the bar reference + notifications prefer the self-hosted URL.
            # IA flips to primary as soon as the user adds creds and enables it.
            # (If the user already configured a custom http_push peer, we leave
            # their setup alone and don't inject a competing self channel.)
            for c in chans:
                c["primary"] = False
            chans.append({
                "id": "self",
                "type": "http_push",
                "name": "This server (self-push)",
                "enabled": True,
                "primary": True,
                "credentials": {},
                "config": {
                    "base_url": self_put,
                    "accept_self_signed": True,
                    "content_type": "application/json",
                },
            })
            raw["channels"] = chans
            _ch.save_raw(raw)
            log.info("First-run: seeded self-push channel @ %s", self_put)
    except Exception as e:
        log.warning("First-run: channel section failed: %s", e)


def _ticket(token: str) -> str:
    """Short human-friendly handle for a session.

    First 8 chars of the 32-char token, uppercased. No separator —
    one less symbol for the user to track when typing or comparing.
    The resolver still accepts a dash for back-compat with legacy
    tickets that may have been copied with the old 4-4 grouping.
    Used by the dashboard's "resume" flow — the user can come back
    hours later with just this string and pull their pending upload
    back up. 16M combinations is plenty for the handful of concurrent
    pending sessions a single host sees.
    """
    return token[:8].upper()


def _resolve_ticket(ticket: str) -> str | None:
    """Find a session token whose short ticket matches.

    Case-insensitive, hyphens optional. Returns the full token or
    ``None``. Refuses ambiguous prefixes (returns None) — first 8 hex
    chars should be unique across <16M sessions, but be defensive in
    case someone runs a very large server.
    """
    norm = ticket.strip().replace("-", "").replace(" ", "").lower()
    if len(norm) < 6:
        return None
    matches = [t for t in _sessions if t.lower().startswith(norm)]
    return matches[0] if len(matches) == 1 else None


def create_session(image_path, metadata):
    """Create a mint session and return (token, url_path).

    Args:
        image_path: Absolute path to the image file on disk.
        metadata: Dict of generation parameters.

    Returns:
        (token, url_path) where url_path is /mint/<token>
    """
    _cleanup_expired()
    token = secrets.token_hex(16)
    from mememage import chains as _chains_bind
    _sessions[token] = {
        "image_path": str(image_path),
        "metadata": metadata,
        "created": time.time(),
        # Bind the conception to the chain active at creation. The GPS
        # callback resolves against THIS, not whatever is active later,
        # so switching chains in the dashboard never redirects a pending
        # conception to a different chain.
        "chain": _chains_bind.current(),
        "status": "pending",
    }
    _save_sessions()
    return token, f"/mint/{token}"


class MintHandler(BaseHTTPRequestHandler):
    server_version = "Mememage/1.0"

    # Per-connection socket timeout (seconds). The server is threaded
    # (ThreadingHTTPServer), so a slow/hung client only ties up its own
    # daemon thread — but with NO timeout those threads block forever on a
    # stalled socket read and pile up. A public box gets scanned constantly
    # (slowloris-style probes, half-open connections); enough accumulated
    # never-dying threads exhausts the process and stalls the accept loop
    # (observed: listen backlog overflowed, every proxied request timed out).
    # A finite timeout makes a stalled read/write raise socket.timeout, the
    # handler returns, and the thread dies — bounding thread accumulation.
    # 120s is generous: it kills idle/slowloris connections while leaving
    # plenty of room for a legitimate mint or upload to complete (those block
    # on OUTBOUND calls inside the handler, not on this inbound socket).
    timeout = 120

    def log_message(self, format, *args):
        log.info(format, *args)

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html, status=200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def _external_host(self):
        """Compute the host[:port] the mint server should advertise in
        outbound URLs (Discord/Slack notifications, mint page links,
        QR codes). Honors the user's configured ``domain`` but splices
        the inbound request's port through when the config is
        port-less and the server is bound on a non-default port —
        otherwise links dead-end on whatever else is listening on :443.

        Precedence:
          1. ``domain`` from server.json, with port from Host header if
             domain is port-less and inbound port != 443
          2. Raw Host header
          3. ``localhost:8443`` fallback
        """
        host_header = self.headers.get("Host", "")
        # Desktop/local mode: advertise exactly what the browser connected
        # to (127.0.0.1:port), never a stale configured public domain.
        if os.environ.get("MEMEMAGE_LOCAL") == "1" and host_header:
            return host_header
        cfg_domain = _get_server_config().get("domain")
        if cfg_domain:
            if ":" not in cfg_domain and ":" in host_header:
                inbound_port = host_header.rsplit(":", 1)[-1]
                if inbound_port.isdigit() and inbound_port != "443":
                    return f"{cfg_domain}:{inbound_port}"
            return cfg_domain
        return host_header or "localhost:8443"

    def _souls_read_base(self):
        """Absolute base URL where this deployment's souls are readable,
        ending in ``/``. Injected into the self-hosted decoder/validator
        so they default their record Source to the local surface instead
        of the Internet Archive.

        Prefers the clean ``souls_domain`` (``souls.<domain>/``, nginx-
        mapped to ``/api/souls/``) when configured; otherwise the mint
        origin's ``/api/souls/`` path, which this process always serves.
        ``fetchFromSource`` in the decoder appends ``<id>.soul`` to it.
        """
        souls_domain = (_get_server_config().get("souls_domain") or "").strip()
        if souls_domain:
            return f"https://{souls_domain}/"
        return f"{_external_scheme()}://{self._external_host()}/api/souls/"

    def _capture_base(self):
        """``scheme://host[:port]`` prefix for the PHONE-capture mint URL/QR.

        Deliberately decoupled from ``_external_host``/``_souls_read_base``:
        those advertise loopback in desktop mode so the LOCAL browser fetches
        records over localhost. The phone, by contrast, needs a host it can
        actually route to — on desktop that's the Tailscale HTTPS endpoint
        stashed in ``MEMEMAGE_CAPTURE_BASE`` at bind time. A VPS has no such
        env and falls back to its normal external host (the public domain).
        """
        base = os.environ.get("MEMEMAGE_CAPTURE_BASE")
        if base:
            return base
        return f"{_external_scheme()}://{self._external_host()}"

    def _phone_reachable(self):
        """True when this server can hand a phone a URL it can actually load.

        Desktop: a Tailscale HTTPS socket was bound (``MEMEMAGE_CAPTURE_BASE``
        set). Loopback-only desktop (``MEMEMAGE_LOCAL=1``, no capture base) is
        NOT phone-reachable → callers fall back to machine GPS. A public/VPS
        install advertises a real domain and is reachable by definition.
        """
        if os.environ.get("MEMEMAGE_CAPTURE_BASE"):
            return True
        if os.environ.get("MEMEMAGE_LOCAL") == "1":
            return False
        bare = self._external_host().rsplit(":", 1)[0]
        return bare not in ("localhost", "127.0.0.1", "::1", "")

    def _effective_gps_source(self, chain_gps_source):
        """The GPS source actually used, given reachability.

        A chain set to ``phone`` gracefully falls back to ``machine`` when no
        phone can reach this server (loopback-only desktop, no Tailscale) —
        better an approximate fix than a QR that dead-ends on localhost.
        ``machine``/``none`` are honored as configured. The distinction is
        surfaced to the dashboard (``gps_source_configured`` + ``phone_reachable``)
        so the fallback is never a mystery.
        """
        from mememage.gps import GPS_SOURCE_PHONE, GPS_SOURCE_MACHINE
        if chain_gps_source == GPS_SOURCE_PHONE and not self._phone_reachable():
            return GPS_SOURCE_MACHINE
        return chain_gps_source

    def _is_souls_face(self):
        """True when this request arrived on the public souls host
        (server.json ``souls_domain``).

        The souls host is the *public decode face* — decoder, validator,
        raw souls — and exposes NOTHING admin (no dashboard, mint flow,
        config, or profile API). The admin mint host, and single-domain
        installs with no souls_domain, route normally.
        """
        souls_domain = (_get_server_config().get("souls_domain") or "").strip().lower()
        if not souls_domain:
            return False
        host = (self.headers.get("Host", "").split(":")[0]).strip().lower()
        return host == souls_domain

    def _is_local_request(self):
        """True when the request arrived on a loopback / local-only host —
        127.x, localhost, ::1, ``*.local``, or a Tailscale ``*.ts.net`` name.

        These are the self-contained desktop / LAN installs, where there is NO
        separate public souls host to defer the decoder/validator to. The
        ``souls_domain`` deferral (admin host stays admin-only, decode face
        lives on souls.<domain>) is a PUBLIC-deployment concern; locally the
        one server is everything, so it must serve the decoder inline even when
        a souls_domain is configured (e.g. a desktop pointing its read-base at a
        remote souls host). Without this, the desktop tray's Open Decoder /
        Open Validator 404."""
        host = (self.headers.get("Host", "").split(":")[0]).strip().lower()
        return (host in ("localhost", "127.0.0.1", "::1", "[::1]")
                or host.startswith("127.")
                or host.endswith(".local")
                or host.endswith(".ts.net"))

    def _route_souls_face(self, path, parsed):
        """Router for the public decode face (souls.<domain>).

        Serves the decoder + validator + their static assets + raw souls
        and keychain records — and 404s everything admin. The decoder's
        injected souls base points back at this same host, so By Word /
        By Sight fetch from ``/`` by default. ``path`` is already
        rstripped of a trailing slash (root → "").
        """
        if path in ("", "/decoder", "/index.html"):
            return self._serve_decoder_html("index.html")
        if path in ("/validator", "/validator.html"):
            return self._serve_decoder_html("validator.html")
        if path in ("/feed", "/feed.html"):
            return self._serve_decoder_html("feed.html")
        if path == "/api/feed":
            return self._feed(parsed.query)
        if path.startswith("/api/feed/thumb/"):
            return self._feed_thumb(path[len("/api/feed/thumb/"):])
        if path.startswith("/api/feed/full/"):
            return self._feed_full(path[len("/api/feed/full/"):])
        if path == "/health":
            return self._send_json({"status": "ok"})
        # Raw souls at root — keeps existing souls.<domain>/<id>.soul URLs
        # valid now that nginx full-proxies (no /api/souls/ rewrite).
        tail = path.lstrip("/")
        if self._SOUL_NAME_RE.match(tail):
            return self._serve_received_soul(tail)
        # Static assets the decoder/validator load.
        if any(path.startswith(p) for p in STATIC_PREFIXES):
            return self._serve_docs_static(path)
        # Read-open API paths the decoder may use directly.
        if path == "/api/souls":
            return self._list_received_souls(parsed.query)
        if path.startswith("/api/souls/"):
            return self._serve_received_soul(path[len("/api/souls/"):])
        if path.startswith("/api/keychain/"):
            return self._serve_received_keychain(path[len("/api/keychain/"):])
        # Everything else on the public face is hidden.
        self.send_error(404)

    # ----- Routes -----

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        # Public decode face (souls.<domain>) gets its own limited router —
        # decoder/validator/souls only, no admin surface. The admin mint
        # host and single-domain installs fall through to normal routing.
        if self._is_souls_face():
            return self._route_souls_face(path, parsed)

        if path == "/health":
            self._send_json({"status": "ok"})
        elif path == "":
            # Root → the public catalog (a wall of recently-conceived image
            # thumbnails). The dashboard login moved to /dashboard. Public, no
            # auth — this is the front door.
            self._serve_decoder_html("feed.html")
        elif path == "/api/feed":
            self._feed(parsed.query)
        elif path.startswith("/api/feed/thumb/"):
            self._feed_thumb(path[len("/api/feed/thumb/"):])
        elif path.startswith("/api/feed/full/"):
            self._feed_full(path[len("/api/feed/full/"):])
        elif path == "/mint/new":
            self._serve_upload_page()
        elif path == "/dashboard":
            self._serve_dashboard(parsed.query)
        elif path in ("/decoder", "/index.html", "/validator", "/validator.html"):
            # The decode face belongs on the PUBLIC souls host, not the
            # admin mint host. When a souls_domain is configured the mint
            # host stays admin-only and these are simply not paths here
            # (404). A single-domain self-host (no souls_domain) — AND any
            # local/loopback request (desktop, LAN, Tailscale) where there's
            # no separate souls host to reach — serves the decoder inline.
            souls_domain = (_get_server_config().get("souls_domain") or "").strip()
            if souls_domain and not self._is_local_request():
                self.send_error(404)
            else:
                self._serve_decoder_html(
                    "validator.html" if "validator" in path else "index.html")
        elif path == "/soul-fields":
            # Internal audit doc — open during pre-launch schema work.
            # Not in the public-sync allow-list; localhost / VPS only.
            self._serve_docs_html("soul-fields.html")
        elif path == "/bar-evolution":
            # Internal: visual history of the bar codec across generations.
            # Mockups live under docs/img/bar-evolution/ and ride the
            # existing static-asset route. Localhost / VPS only — not in
            # the public-sync allow-list (older bar formats are history,
            # not part of the live decoder).
            self._serve_docs_html("bar-evolution.html")
        elif path.startswith("/mint/") and len(path.split("/")) == 3:
            token = path.split("/")[2]
            self._serve_mint_page(token)
        elif path == "/api/mint/sessions":
            self._list_sessions()
        elif path.startswith("/api/mint/resume/") and len(path.split("/")) == 5:
            ticket = path.split("/")[4]
            self._mint_resume(ticket)
        elif path.startswith("/api/mint/") and path.endswith("/status") and len(path.split("/")) == 5:
            token = path.split("/")[3]
            self._mint_status(token)
        elif path.startswith("/api/mint/") and path.endswith("/machine-gps") and len(path.split("/")) == 5:
            token = path.split("/")[3]
            self._mint_machine_gps(token)
        elif path.startswith("/api/mint/") and path.endswith("/image") and len(path.split("/")) == 5:
            token = path.split("/")[3]
            self._serve_minted_image(token)
        elif path.startswith("/api/mint/") and path.endswith("/soul") and len(path.split("/")) == 5:
            token = path.split("/")[3]
            self._serve_minted_soul(token)
        elif path == "/api/payload/status":
            self._payload_status()
        elif path.startswith("/api/payload/inspect/"):
            from urllib.parse import unquote
            name = unquote(path[len("/api/payload/inspect/"):])
            self._payload_inspect(name)
        elif path == "/api/payload/presets":
            self._preset_list()
        elif path.startswith("/api/payload/presets/"):
            from urllib.parse import unquote
            name = unquote(path[len("/api/payload/presets/"):])
            self._preset_get(name)
        elif path == "/api/site-pack/status":
            self._site_pack_status()
        elif path == "/api/forecast":
            self._forecast(parsed.query)
        elif path == "/api/onboarding/status":
            self._onboarding_status()
        elif path == "/api/config":
            self._config_get()
        elif path == "/api/doctor":
            self._doctor()
        elif path == "/api/profiles":
            self._profiles_list()
        elif path == "/api/chain/current":
            self._chain_current()
        elif path == "/api/chain/list":
            self._chain_list()
        elif path == "/api/chain/config":
            self._chain_config_get()
        elif path == "/api/fs/pick/available":
            self._fs_pick_available()
        elif path.startswith("/preview/") and len(path.split("/")) == 3:
            # /preview/<name> → docs/<name>-preview.html. Self-contained
            # design preview pages — no auth gate, no token injection.
            # Used to iterate on page styling next to the production
            # version (which loads via /mint/<token> with substitutions).
            name = path.split("/")[2]
            # Defensive: only allow alnum + hyphen names to prevent
            # path traversal into docs/.
            if name and all(c.isalnum() or c == "-" for c in name):
                self._serve_docs_html(f"{name}-preview.html")
            else:
                self.send_error(404)
        elif path == "/api/souls" or path == "/api/souls/":
            # Listing endpoint — supports the http_push channel's
            # cleanup search() method. Query string carries pattern + limit.
            self._list_received_souls(parsed.query)
        elif path.startswith("/api/souls/"):
            tail = path[len("/api/souls/"):]
            self._serve_received_soul(tail)
        elif path.startswith("/api/keychain/"):
            tail = path[len("/api/keychain/"):]
            self._serve_received_keychain(tail)
        elif path == "/api/channels":
            self._channels_list()
        elif path == "/api/channels/raw":
            self._channels_raw()
        elif path == "/api/channels/types":
            self._channels_types()
        elif path == "/api/channels/capabilities":
            self._channels_capabilities()
        elif any(path.startswith(p) for p in STATIC_PREFIXES):
            self._serve_docs_static(path)
        elif self._dashboard_path_token(path):
            # Clean dashboard URL: /<MINT_API_TOKEN>. The token IS the auth,
            # so this serves the dashboard directly (the token gets injected
            # into the page by _serve_docs_html). The recommended self-host
            # bookmark — e.g. https://mint.example.com/<token>.
            self._serve_docs_html("dashboard.html")
        else:
            self.send_error(404)

    def _dashboard_path_token(self, path):
        """True when `path` is the single-segment clean dashboard URL
        (``/<MINT_API_TOKEN>``). The token is a 12-word concatenation, so a
        full-segment match never collides with a real route. Returns False
        when no token is configured (open dev mode uses /dashboard)."""
        expected = _load_mint_token()
        if not expected:
            return False
        seg = path.lstrip("/")
        return bool(seg) and "/" not in seg and hmac.compare_digest(seg, expected)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/mint/session":
            self._create_session_api()
        elif path == "/api/mint/upload":
            self._upload_and_create_session()
        elif path.startswith("/api/mint/") and path.endswith("/metadata") and len(path.split("/")) == 5:
            # POST /api/mint/<token>/metadata — sync edits from the dashboard
            # Origin-fields editor back into the session before the phone
            # confirms GPS. Mint runs server-side from session metadata,
            # so without this the user's edits would be discarded.
            token = path.split("/")[3]
            self._update_session_metadata(token)
        elif path.startswith("/api/mint/") and len(path.split("/")) == 4:
            token = path.split("/")[3]
            self._confirm_mint(token)
        elif path == "/api/payload/build":
            self._payload_build()
        elif path == "/api/payload/upload":
            self._payload_upload()
        elif path == "/api/payload/upload/delete":
            self._payload_upload_delete()
        elif path == "/api/payload/presets":
            self._preset_save()
        elif path.startswith("/api/payload/presets/") and path.endswith("/delete"):
            from urllib.parse import unquote
            name = unquote(path[len("/api/payload/presets/"):-len("/delete")])
            self._preset_delete(name)
        elif path == "/api/site-pack/seal":
            self._site_pack_seal()
        elif path.startswith("/api/channel/") and path.endswith("/scan"):
            from urllib.parse import unquote
            cid = unquote(path[len("/api/channel/"):-len("/scan")])
            self._channel_scan(cid)
        elif path.startswith("/api/channel/") and path.endswith("/hide"):
            from urllib.parse import unquote
            cid = unquote(path[len("/api/channel/"):-len("/hide")])
            self._channel_hide(cid)
        elif path.startswith("/api/channel/") and path.endswith("/purge"):
            from urllib.parse import unquote
            cid = unquote(path[len("/api/channel/"):-len("/purge")])
            self._channel_purge(cid)
        elif path.startswith("/api/channel/") and path.endswith("/test"):
            from urllib.parse import unquote
            cid = unquote(path[len("/api/channel/"):-len("/test")])
            self._channel_test(cid)
        elif path == "/api/config/creator":
            self._config_set_creator()
        elif path == "/api/config/server":
            self._config_set_server()
        elif path == "/api/config/env":
            self._config_set_env()
        elif path == "/api/config/token/generate":
            self._config_token_generate()
        elif path == "/api/config/webhooks":
            self._config_set_webhooks()
        elif path == "/api/identity/install-signing":
            self._identity_install_signing()
        elif path == "/api/identity/keygen":
            self._identity_keygen()
        elif path == "/api/identity/rotate":
            self._identity_rotate()
        elif path == "/api/identity/revoke":
            self._identity_revoke()
        elif path == "/api/profiles":
            self._profiles_new()
        elif path == "/api/profiles/import":
            self._profiles_import()
        elif path == "/api/profiles/active":
            self._profiles_active()
        elif path == "/api/profiles/alias":
            self._profiles_alias()
        elif path == "/api/profiles/remove":
            self._profiles_remove()
        elif path == "/api/profiles/pair":
            self._profiles_pair_inbound()
        elif path == "/api/profiles/pair-call":
            self._profiles_pair_call()
        elif path == "/api/chain/unlock":
            self._chain_unlock()
        elif path == "/api/chain/lock":
            self._chain_lock()
        elif path == "/api/chain/switch":
            self._chain_switch()
        elif path == "/api/chain/new":
            self._chain_new()
        elif path == "/api/chain/remove":
            self._chain_remove()
        elif path == "/api/chain/rename":
            self._chain_rename()
        elif path == "/api/chain/password":
            self._chain_password()
        elif path == "/api/chain/gps-source":
            self._chain_gps_source()
        elif path == "/api/chain/gps-visibility":
            self._chain_gps_visibility()
        elif path == "/api/chain/watermark":
            self._chain_watermark()
        elif path == "/api/channels":
            self._channels_save()
        elif path == "/api/sync/accept":
            self._sync_accept()
        elif path == "/api/sync/call":
            self._sync_call()
        elif path == "/api/sync/export":
            self._sync_export()
        elif path == "/api/chain/migrate":
            self._chain_migrate()
        elif path == "/api/chain/config":
            self._chain_config_set()
        elif path == "/api/fs/pick":
            self._fs_pick()
        else:
            self.send_error(404)

    def do_DELETE(self):
        """DELETE /api/mint/<token> — drop a pending mint session.
        DELETE /api/souls/<filename>.{soul,json} — purge a received soul.

        Lets the dashboard release sessions before the 7-day TTL kicks
        in (stale draft, wrong image staged, etc). Idempotent: a 404
        on a missing token is fine — the caller's goal is "this token
        does not exist anymore", which is true either way.

        Refuses to delete sessions that are actively minting (in
        flight) — wait for them to complete or fail. Completed sessions
        can be deleted; users can revisit the /mint/<token> page
        afterward but the underlying record is already on IA / peer
        mirrors, so it isn't lost.
        """
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        # Souls purge route — used by the http_push channel cleanup
        # surface. Auth-gated; filename validated against _SOUL_NAME_RE.
        if path.startswith("/api/souls/"):
            tail = path[len("/api/souls/"):]
            self._delete_received_soul(tail)
            return
        m = re.match(r"^/api/mint/([A-Za-z0-9_-]+)$", path)
        if not m:
            self.send_error(404)
            return
        if not _check_auth(self): return
        raw = m.group(1)
        # Accept either a full token OR a short ticket prefix (the
        # dashboard's Delete-by-ticket flow uses the same format the
        # Resume button does). Token lookup is direct; ticket goes
        # through the resolver.
        token = raw if raw in _sessions else (_resolve_ticket(raw) or raw)
        session = _sessions.get(token)
        if not session:
            self._send_json({"ok": True, "existed": False})
            return
        if session.get("status") == "minting":
            self._send_json(
                {"error": "Session is actively minting; wait for it to finish or fail."},
                409,
            )
            return
        # A COMPLETED conception is KEPT here, not deleted. Its staged image +
        # session are what the public catalog renders, and the "Conceive
        # another" / reset flow fires this same DELETE — so dropping it would
        # withdraw a just-conceived image from the feed (the bug: mint two, only
        # the newest showed, because conceiving the 2nd deleted the 1st's
        # session+image). Completed conceptions persist until the 7-day cull or
        # until their soul is removed via surface cleanup
        # (_withdraw_conception_image). An explicit ?purge=1 still discards one
        # deliberately. Pending/failed drafts fall through and are cleaned up.
        _purge = parse_qs(parsed.query).get("purge", ["0"])[0].lower() in ("1", "true", "yes")
        if session.get("status") == "completed" and not _purge:
            self._send_json({"ok": True, "existed": True, "kept": True,
                             "reason": "completed conception kept for the catalog"})
            return
        # Best-effort: scrub the staged image file too. Soul records
        # on IA / peer mirrors are unaffected — they're the canonical
        # post-mint state, not session-scoped.
        img = session.get("image_path")
        if img:
            try:
                Path(img).unlink(missing_ok=True)
            except Exception as e:
                log.warning("Session delete: failed to remove %s: %s", img, e)
        _sessions.pop(token, None)
        _save_sessions()
        log.info("Session %s deleted (%s)", token[:8], session.get("status"))
        self._send_json({"ok": True, "existed": True})

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path
        # /api/souls/<identifier>.{soul,json} — peer push endpoint.
        # Lets one mememage server mirror souls into another via the
        # http_push channel. The receiving server stores them under
        # ~/.mememage/received/ and serves them back via GET.
        if path.startswith("/api/souls/"):
            tail = path[len("/api/souls/"):]
            # The optional feed image (push_image) rides the same face as the
            # soul, distinguished by extension.
            if tail.endswith(".png"):
                self._receive_image(tail)
            else:
                self._receive_soul(tail)
        # /api/keychain/<chain_id>/<filename> — peer push endpoint for
        # keychain records (succession / revocation / alias). Same
        # idea as souls but the URL shape mirrors IA's keychain item
        # layout (chain_id is the item, filename is the record).
        elif path.startswith("/api/keychain/"):
            tail = path[len("/api/keychain/"):]
            self._receive_keychain(tail)
        else:
            self.send_error(404)

    # ----- Peer-receive endpoint -----

    # Identifier prefix is per-chain and case-preserving (mememage-, dark-,
    # MeMeMaGe-, …) — NOT hardcoded to "mememage". Accept any leading-letter,
    # filename-safe prefix, the <16-hex> id hash, and an optional interposed
    # content hash (<identifier>.<hash>.soul). The ^…$ anchor plus the
    # [A-Za-z0-9_-]/hex-only classes forbid '/' and '..', so path traversal
    # stays impossible without depending on the prefix literal.
    _SOUL_NAME_RE = re.compile(
        r"^([A-Za-z][A-Za-z0-9_-]*-[0-9a-f]{12,16})(?:\.[0-9a-f]{12,16})?\.(soul|json)$"
    )
    # The optional feed image that rides alongside a pushed soul (push_image):
    # <identifier>.png. Same identifier grammar as the soul; PNG only (the
    # conceived image is always PNG — the bar is embedded losslessly). The
    # ^…$ anchor + restricted classes forbid '/' and '..' (no path traversal).
    _IMAGE_NAME_RE = re.compile(
        r"^([A-Za-z][A-Za-z0-9_-]*-[0-9a-f]{12,16})\.png$"
    )
    # Keychain tail = "<chain_id>/<filename>". chain_id follows the same
    # mememage-keychain-<fingerprint> shape as IA. Filenames we accept:
    # succession.json, revocation.json, alias-<fp>.json (fp is 16 hex
    # chars, no colons — the dashed form).
    _KEYCHAIN_TAIL_RE = re.compile(
        r"^(mememage-keychain-[0-9a-f]{16,32})/"
        r"(succession\.json|revocation\.json|alias-[0-9a-f]{16,32}\.json)$"
    )

    def _receive_soul(self, filename):
        """PUT /api/souls/<identifier>.{soul,json} — accept a soul from
        another mememage instance running the http_push channel.

        Stored at ``~/.mememage/received/<filename>``. The identifier
        is validated against the canonical regex so we can't be tricked
        into writing arbitrary paths. Reuses ``_check_auth`` so the
        sender must present the same bearer token the receiving
        server's dashboard uses — if no token is set, the endpoint is
        open (same posture as the rest of the server).
        """
        if not _check_auth(self): return
        m = self._SOUL_NAME_RE.match(filename)
        if not m:
            self._send_json({"error": "Filename must be <prefix>-<hex>[.<hash>].soul or .json"}, 400)
            return
        # Body is STREAMED to disk (never held whole in RAM) so large payload
        # souls — multi-MB chunks, even big embedded files — can't OOM the box.
        # The cap (configurable: server.json max_soul_bytes) now bounds DISK — a
        # guard against an authed peer filling the box, not memory. Reject AFTER
        # draining the body so the pushing side gets a clean 400, not a TLS EOF
        # from an early connection close.
        cap = _soul_max_bytes()
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > cap:
            log.warning("soul-receive reject %s: Content-Length=%r (cap %d MiB) %s",
                        filename, self.headers.get("Content-Length"),
                        cap // (1024 * 1024), self.request_version)
            self._send_json({"error": f"Content-Length missing or over the {cap // (1024*1024)} MiB cap"}, 400)
            return
        target_dir = _chains.received_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        tmp = target.with_name(target.name + ".part")
        # Cheap "looks like JSON" check (first byte) instead of a full parse —
        # full-parsing would defeat the streaming, and the soul's content_hash
        # is the real integrity check at read time.
        try:
            written, head_ok = _stream_body_to_file(self.rfile, length, tmp)
            if head_ok is False:
                raise ValueError("body does not look like JSON (first byte not '{' or '[')")
        except Exception as e:
            try:
                tmp.unlink()
            except OSError:
                pass
            log.warning("soul-receive reject %s: %s", filename, e)
            self._send_json({"error": f"Body rejected: {e}"}, 400)
            return
        tmp.replace(target)
        log.info("Received soul %s (%d bytes, streamed)", filename, written)
        self._send_json({"ok": True, "stored_at": str(target)})

    def _receive_image(self, filename):
        """PUT /api/souls/<identifier>.png — accept the full conceived image
        that rides alongside a pushed soul (the http_push channel's push_image
        feature), so this surface shows the conception in its public feed at
        full quality. Stored next to the soul in ``~/.mememage/received/``.

        Same auth + streaming + path-safety posture as ``_receive_soul``; only
        the name shape (``.png``) and the size cap (``max_image_bytes``) differ.
        We accept the image regardless of whether the soul has arrived yet (push
        order isn't guaranteed) — the feed only surfaces it once the soul is
        also present (``_soul_on_surface``)."""
        if not _check_auth(self): return
        m = self._IMAGE_NAME_RE.match(filename)
        if not m:
            self._send_json({"error": "Filename must be <prefix>-<hex>.png"}, 400)
            return
        cap = _image_max_bytes()
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > cap:
            log.warning("image-receive reject %s: Content-Length=%r (cap %d MiB)",
                        filename, self.headers.get("Content-Length"),
                        cap // (1024 * 1024))
            self._send_json({"error": f"Content-Length missing or over the {cap // (1024*1024)} MiB cap"}, 400)
            return
        target_dir = _chains.received_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        tmp = target.with_name(target.name + ".part")
        try:
            written, _ = _stream_body_to_file(self.rfile, length, tmp)
        except Exception as e:
            try:
                tmp.unlink()
            except OSError:
                pass
            log.warning("image-receive reject %s: %s", filename, e)
            self._send_json({"error": f"Body rejected: {e}"}, 400)
            return
        tmp.replace(target)
        # New image → its feed thumbnail must be recomputed, not served stale
        # (drop the disk copy too, else the old tile would be served back).
        _feed_thumb_forget(m.group(1))
        # …and the wall must re-enumerate so this fresh conception shows now,
        # not after the cache TTL (IA-backed feed). No-op for the local feed.
        _invalidate_ia_feed()
        log.info("Received feed image %s (%d bytes, streamed)", filename, written)
        self._send_json({"ok": True, "stored_at": str(target)})

    def _receive_keychain(self, tail):
        """PUT /api/keychain/<chain_id>/<filename> — accept a keychain
        record from a peer (the http_push channel's keychain mirror).
        Stored at ``~/.mememage/received/keychain/<chain_id>/<filename>``.

        Same auth + size posture as ``_receive_soul``; only the
        validated URL shape differs (a chain dir + a known filename
        instead of an identifier-prefixed filename).
        """
        if not _check_auth(self): return
        m = self._KEYCHAIN_TAIL_RE.match(tail)
        if not m:
            self._send_json({"error": "Path must be /<chain_id>/<filename>; chain_id like mememage-keychain-<hex>, filename succession.json | revocation.json | alias-<hex>.json"}, 400)
            return
        chain_id, filename = m.group(1), m.group(2)
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 64 * 1024:  # 64 KiB — keychain records are tiny
            self._send_json({"error": "Content-Length missing or out of range (max 64 KiB)"}, 400)
            return
        body = self.rfile.read(length)
        try:
            json.loads(body.decode("utf-8"))
        except Exception:
            self._send_json({"error": "Body must be UTF-8 JSON"}, 400)
            return
        target_dir = _chains.keychain_dir() / chain_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        target.write_bytes(body)
        log.info("Received keychain %s/%s (%d bytes)", chain_id, filename, len(body))
        self._send_json({"ok": True, "stored_at": str(target)})

    # Chain-only path (no filename) — used for the listing endpoint
    # so verifiers can walk a peer's keychain the same way they walk
    # IA's /metadata/<chain_id> response.
    _KEYCHAIN_CHAIN_RE = re.compile(r"^mememage-keychain-[0-9a-f]{16,32}$")

    def _serve_received_keychain(self, tail):
        """GET /api/keychain/<tail> — three shapes:

          a) tail = ``mememage-keychain-<fp>/succession.json`` (etc.)
             → serve the specific keychain record file
          b) tail = ``mememage-keychain-<fp>``
             → list every keychain file the peer has stored for that
               chain, as ``{"files": [...]}``. Mirrors IA's metadata
               file listing so verify.js's discoverAliases can find
               alias-*.json files on a peer just like it does on IA.

        Read-open + CORS so browser verifiers can fetch without auth.
        Matches IA's public-read posture.
        """
        # Listing case — bare chain_id, no filename
        m_chain = self._KEYCHAIN_CHAIN_RE.match(tail)
        if m_chain:
            chain_dir = _chains.keychain_dir() / tail
            files = []
            if chain_dir.is_dir():
                for f in sorted(chain_dir.iterdir()):
                    if f.is_file() and not f.name.startswith("."):
                        files.append(f.name)
            body = json.dumps({"chain_id": tail, "files": files}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "public, max-age=60")
            self.end_headers()
            try:
                self.wfile.write(body)
            except Exception:
                pass
            return

        # Single-record case — chain/filename
        m = self._KEYCHAIN_TAIL_RE.match(tail)
        if not m:
            self.send_error(404)
            return
        chain_id, filename = m.group(1), m.group(2)
        path = _chains.keychain_dir() / chain_id / filename
        if not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _feed(self, query):
        """GET /api/feed?offset=N&limit=M — public catalog page, newest-first.

        Infinite-scroll friendly: pass the previous response's ``next_offset``
        to fetch the next page; keep going until ``has_more`` is false. Each
        entry is just {identifier}; the tile image comes from
        GET /api/feed/thumb/<identifier> (a thumbnail of the actual conceived
        image). No auth, no token — the public front door. ``limit`` is capped
        at _FEED_MAX per request, but ``offset`` pages through ALL eligible
        conceptions, so the wall is unbounded — what you scroll is what you get.
        """
        qs = parse_qs(query or "")

        def _int(name, default):
            try:
                return int((qs.get(name) or [str(default)])[0])
            except (TypeError, ValueError):
                return default

        limit = max(1, min(_FEED_MAX, _int("limit", 60)))
        offset = max(0, _int("offset", 0))
        full = _public_feed()
        page = full[offset:offset + limit]
        self._send_json({
            "feed": page,
            "offset": offset,
            "next_offset": offset + len(page),
            "has_more": (offset + len(page)) < len(full),
            "total": len(full),
        })

    def _redirect(self, url, code=302):
        """A bare 302 to ``url`` — used to hand feed image requests off to IA's
        permanent copies instead of serving bytes from this box."""
        self.send_response(code)
        self.send_header("Location", url)
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()

    def _feed_thumb(self, identifier):
        """GET /api/feed/thumb/<identifier> — crisp JPEG thumbnail of the
        conceived image. Public; resolves identifier→minted-image internally so
        the token is never exposed. 404 once the conception is culled (image
        gone).

        On an IA-backed feed the tile is still generated at full crispness
        (460px LANCZOS), NOT IA's tiny ~180px auto-thumbnail — from the local
        image the VPS already holds, falling back to a one-time fetch of the
        permanent IA copy if the local image is gone. Only the full-size
        lightbox image (_feed_full) is handed off to IA."""
        if _feed_source()[0] == "ia":
            if not _ia_feed_member(identifier):
                return self.send_error(404)
            data = _ia_feed_thumb_bytes(identifier)
            if not data:
                return self.send_error(404)
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        ip = _feed_image_path(identifier)
        data = _feed_thumb_bytes(identifier, ip) if ip else None
        if not data:
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _feed_full(self, identifier):
        """GET /api/feed/full/<identifier> — the full-resolution conceived image,
        for the catalog lightbox. Same public/light, soul-present gate as the
        thumbnail. The minted image is PNG (bar embedded in place).

        IA-backed feed: redirect to the permanent PNG on the Archive."""
        if _feed_source()[0] == "ia" and _ia_feed_member(identifier):
            return self._redirect(f"{_IA_DOWNLOAD}/{identifier}/{identifier}.png")
        ip = _feed_image_path(identifier)
        if not ip:
            return self.send_error(404)
        try:
            with open(ip, "rb") as f:
                data = f.read()
        except OSError:
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _list_received_souls(self, query_string):
        """GET /api/souls/?pattern=mememage-*&limit=N — list received souls.

        Used by the http_push channel's cleanup surface (search method).
        Returns ``{items: [{identifier, url, size, date}, ...]}``. Each
        item maps one ``.soul`` file in ``~/.mememage/received/``; the
        ``.json`` mirror is ignored (it's the same record under a
        different extension, same identifier).

        Authenticated — this is a maintenance surface, not a public
        listing of every soul we've received. CORS-friendly so the
        dashboard's fetch path works.
        """
        if not _check_auth(self): return
        from urllib.parse import parse_qs
        params = parse_qs(query_string or "")
        pattern = (params.get("pattern") or ["mememage-*"])[0]
        try:
            limit = int((params.get("limit") or ["500"])[0])
        except ValueError:
            limit = 500
        # Glob translation — only "*" wildcards matter for our identifiers.
        import fnmatch
        received = _chains.received_dir()
        items = []
        if received.is_dir():
            for entry in sorted(received.iterdir()):
                if not entry.is_file() or not entry.name.endswith(".soul"):
                    continue
                stem = entry.name[:-len(".soul")]
                if not fnmatch.fnmatchcase(stem, pattern):
                    continue
                try:
                    st = entry.stat()
                except OSError:
                    continue
                import datetime as _dt
                items.append({
                    "identifier": stem,
                    "url": f"{_external_scheme()}://{self._external_host()}/api/souls/{entry.name}",
                    "size": st.st_size,
                    "date": _dt.datetime.fromtimestamp(
                        st.st_mtime, tz=_dt.timezone.utc
                    ).isoformat(timespec="seconds"),
                })
                if len(items) >= limit:
                    break
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        body = json.dumps({"items": items, "count": len(items)}).encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _delete_received_soul(self, filename):
        """DELETE /api/souls/<identifier>.{soul,json} — unlink a received
        soul file. Used by the http_push channel's purge() method. Also
        unlinks the sibling ``.json`` mirror when present so the cleanup
        leaves no half-removed pair behind.

        Auth-gated. Containment via _SOUL_NAME_RE — the filename must
        match ``mememage-<hex>.{soul,json}`` so a malicious client can't
        traverse out of the received directory.
        """
        if not _check_auth(self): return
        m = self._SOUL_NAME_RE.match(filename)
        if not m:
            self._send_json({"error": "Invalid filename"}, 400)
            return
        identifier = m.group(1)
        received = _chains.received_dir()
        deleted = 0
        # Both .soul and .json (the CORS-friendly mirror IA-style flows
        # use) are scrubbed in one call.
        for ext in ("soul", "json"):
            target = received / f"{identifier}.{ext}"
            try:
                if target.is_file():
                    target.unlink()
                    deleted += 1
            except OSError as e:
                log.warning("Soul purge: %s failed: %s", target, e)
        # Removing a soul is a surface withdrawal: also reclaim the conceived
        # image + drop its session, so it leaves the catalog AND frees the disk
        # immediately rather than lingering (invisibly) until the 7-day reaper.
        img_n, sess_n = _withdraw_conception_image(identifier)
        self._send_json({
            "ok": True, "identifier": identifier,
            "files_deleted": deleted,
            "image_unlinked": img_n,
            "session_dropped": sess_n,
        })

    def _serve_received_soul(self, filename):
        """GET /api/souls/<identifier>.{soul,json} — serve a soul we
        previously received via PUT. Read-open so decoders can fetch
        without auth (matches IA's behavior on the public side).
        CORS-friendly so browser By Word verification works.
        """
        m = self._SOUL_NAME_RE.match(filename)
        if not m:
            self.send_error(404)
            return
        path = _chains.received_dir() / filename
        if not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    # ----- Session management -----

    def _require_chain_sealed(self):
        """Gate conception on the chain being conceivable, return True if so.

        Two conceivable states:
          1. The chain has a sealed Age — records get Age number + chunk
             assignments as usual.
          2. The chain is *provenance-only* (no payload configured) — a
             sealed Age isn't needed. The pipeline omits the payload fields
             (age / decoder_hash / chunks / outer_position); what remains is
             a fully well-formed, WITNESSED-valid soul. This matches the
             chain badge's "No payload — provenance still works" state.

        Only a chain that *carries a payload* but hasn't sealed is refused
        (412): its records are supposed to carry chunks, so minting before
        the seal would silently drop them. The refusal happens before a
        session token is issued, not mid-mint. Used by both mint-session
        entry points (/api/mint/session and /api/mint/upload).
        """
        from mememage.site_embed import get_current_age_info
        from mememage import chain_config
        unsealed = get_current_age_info() is None
        try:
            carries_payload = chain_config.load().has_payload()
        except Exception:
            carries_payload = True  # unreadable config -> safe refusal
        # Provenance-only chains conceive freely whether sealed or not — the
        # pipeline omits the payload fields and the soul is still well-formed
        # and WITNESSED-valid. Only a payload-carrying chain must seal first,
        # else its records silently lose the chunks they're meant to carry.
        if unsealed and carries_payload:
            self._send_json({
                "error": "Chain has no sealed Age yet — open the dashboard's "
                         "Payload tab and click \u201cSeal Age\u201d before minting. "
                         "Without a sealed Age the record would have no Age "
                         "number, no decoder_hash, and no chunks."
            }, 412)
            return False
        return True

    def _create_session_api(self):
        """POST /api/mint/session — create session from local image path + metadata."""
        if not _check_auth(self): return
        if not self._require_chain_sealed(): return
        try:
            data = json.loads(self._read_body())
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return

        image_path = data.get("image_path")
        metadata = data.get("metadata")

        if not image_path or not os.path.isfile(image_path):
            self._send_json({"error": "image_path missing or file not found"}, 400)
            return

        # Auto-extract metadata from PNG text chunks if not provided
        if not metadata or not isinstance(metadata, dict):
            metadata = {}
        extracted = _extract_image_metadata(image_path)
        if extracted:
            merged = dict(extracted)
            merged.update({k: v for k, v in metadata.items() if v})
            metadata = merged

        # Auto-derive dimensions from the image when not supplied. Non-AI
        # uploads (photos, screenshots) legitimately have no metadata
        # other than what we can read from pixels.
        if "width" not in metadata or "height" not in metadata:
            try:
                from PIL import Image as _Image
                with _Image.open(image_path) as _img:
                    metadata.setdefault("width", _img.size[0])
                    metadata.setdefault("height", _img.size[1])
            except Exception as e:
                log.warning("Could not derive dimensions from %s: %s", image_path, e)

        token, url_path = create_session(image_path, metadata)
        self._send_json({"token": token, "mint_url": url_path})

    def _upload_and_create_session(self):
        """POST /api/mint/upload — upload image + metadata via multipart-like JSON."""
        if not _check_auth(self): return
        if not self._require_chain_sealed(): return
        try:
            data = json.loads(self._read_body())
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return

        metadata = data.get("metadata", {})
        image_b64 = data.get("image_data")
        filename = data.get("filename", "upload.png")

        if not image_b64:
            self._send_json({"error": "image_data (base64) required"}, 400)
            return

        import base64
        try:
            image_bytes = base64.b64decode(image_b64)
        except Exception:
            self._send_json({"error": "Invalid base64 image data"}, 400)
            return

        # Save uploaded image to staging
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        dest = UPLOAD_DIR / f"{secrets.token_hex(8)}_{filename}"
        dest.write_bytes(image_bytes)
        # Extract prefill metadata from the ORIGINAL upload BEFORE PNG
        # normalization — _ensure_png_upload re-saves as PNG and strips EXIF.
        extracted = _extract_image_metadata(str(dest))
        # Bar encoding needs lossless PNG — normalize jpg/heic/webp uploads now.
        dest = _ensure_png_upload(dest)
        if extracted:
            merged = dict(extracted)
            merged.update({k: v for k, v in metadata.items() if v})
            metadata = merged

        # Auto-derive width/height from the image file if the caller didn't
        # supply them. Non-AI-gen uploads (photos, screenshots, drawings)
        # have no PNG-text generation_params and no client-provided dims,
        # but they DO have intrinsic pixel dimensions. The bar embedding
        # step needs these to scale correctly; deriving them server-side
        # lets any image mint without the client needing to know its
        # own dimensions.
        if "width" not in metadata or "height" not in metadata:
            try:
                from PIL import Image as _Image
                with _Image.open(str(dest)) as _img:
                    metadata.setdefault("width", _img.size[0])
                    metadata.setdefault("height", _img.size[1])
            except Exception as e:
                log.warning("Could not derive dimensions from %s: %s", dest, e)

        token, url_path = create_session(str(dest), metadata)
        # Capture base (phone-reachable), NOT the loopback decoder host.
        mint_url = f"{self._capture_base()}{url_path}"

        # Always fire the ready webhook — it's a host-awareness signal,
        # not a phone-only one. Hosts running a public mint surface want
        # to know "someone just dropped an image on my server" regardless
        # of GPS mode. The conception page itself adapts to the chain's
        # gps_source (phone watchPosition / machine fetch / none).
        try:
            from mememage import chains
            from mememage.gps import GPS_SOURCE_PHONE
            chain_gps_source = chains.get_gps_source(chains.current())
        except Exception:
            chain_gps_source = GPS_SOURCE_PHONE
        # Effective source: phone falls back to machine when no phone can
        # reach this host. Report both so the dashboard explains the choice.
        eff_gps_source = self._effective_gps_source(chain_gps_source)
        _notify_ready(mint_url, filename, eff_gps_source)

        self._send_json({
            "token": token,
            "ticket": _ticket(token),
            "mint_url": url_path,
            "mint_url_full": mint_url,
            "qr_data_uri": _generate_qr_data_uri(mint_url),
            "metadata": metadata,
            "gps_source": eff_gps_source,
            "gps_source_configured": chain_gps_source,
            "phone_reachable": self._phone_reachable(),
            "chain": _sessions.get(token, {}).get("chain"),
        })

    def _update_session_metadata(self, token):
        """POST /api/mint/<token>/metadata — replace session metadata.

        Fed by the dashboard's Origin-fields editor on each edit. The
        mint pipeline reads session["metadata"] when it runs, so this
        keeps server and client in sync. width/height from the original
        upload are preserved server-side — the editor never sends them
        and we don't want a stray edit clobbering image dimensions
        needed for bar embedding.
        """
        if not _check_auth(self): return
        session = _sessions.get(token)
        if not session:
            self._send_json({"error": "Invalid or expired token"}, 404)
            return
        if session["status"] != "pending":
            self._send_json({"error": "Session already started or finalized"}, 400)
            return
        try:
            data = json.loads(self._read_body())
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        new_meta = data.get("metadata")
        if not isinstance(new_meta, dict):
            self._send_json({"error": "metadata must be an object"}, 400)
            return
        old = session.get("metadata") or {}
        merged = dict(new_meta)
        for k in ("width", "height"):
            if k in old:
                merged[k] = old[k]
        session["metadata"] = merged
        _save_sessions()
        self._send_json({"ok": True, "metadata": merged})

    def _mint_status(self, token):
        """GET /api/mint/<token>/status — poll mint progress.

        On completion, includes a ``download_url`` that uses the
        externally-reachable host (via ``_external_host()``) instead of
        the bare ``localhost`` the dashboard's relative href would
        otherwise resolve to. This lets the user share the download
        link or open it from another device on the same tailnet.
        """
        session = _sessions.get(token)
        if not session:
            self._send_json({"error": "Invalid or expired token"}, 404)
            return
        resp = {"status": session["status"]}
        if session["status"] == "completed":
            resp.update(session.get("result", {}))
            # Image download URL: same scheme/host as the user's inbound
            # request so it's shareable. Relative href would resolve to
            # whatever the dashboard page is loaded at (localhost on the
            # host machine), which doesn't work for cross-device shares.
            host = self._external_host()
            _sch = _external_scheme()
            resp["download_url"] = f"{_sch}://{host}/api/mint/{token}/image"
            resp["download_soul_url"] = f"{_sch}://{host}/api/mint/{token}/soul"
        elif session["status"] == "failed":
            resp["error"] = session.get("error", "Unknown error")
        self._send_json(resp)

    def _mint_machine_gps(self, token):
        """GET /api/mint/<token>/machine-gps — preview the IP geolocation a
        machine-GPS conception would use, so the page shows real coordinates
        instead of "will fetch on conceive".

        Same `gps.fetch_machine_gps` the mint runs, cached briefly (the host's
        IP geo is stable) to avoid hitting ip-api.com on every page load. The
        mint still re-fetches fresh at conceive — same IP, so it matches. On a
        geo miss returns 200 + {"error": ...} so the page degrades gracefully.
        """
        if not _sessions.get(token):
            self._send_json({"error": "Invalid or expired token"}, 404)
            return
        coords = _cached_machine_gps()
        if not coords:
            self._send_json({"error": "IP geolocation unavailable — will retry on conceive"})
            return
        self._send_json({"lat": coords[0], "lon": coords[1]})

    def _mint_resume(self, ticket):
        """GET /api/mint/resume/<ticket> — rehydrate a pending session.

        Returns the same shape as the upload response so the dashboard
        can drop straight back into the reviewing state: image path
        (for the thumbnail preview), metadata (for the Origin-fields
        editor), mint URL + QR (for the conception handoff), and the
        chain's current gps_source.

        Refuses to resume sessions that aren't ``pending`` — completed
        mints are immutable, ``minting`` is in flight, ``failed`` may
        be retryable but the user should drop a fresh image.
        """
        if not _check_auth(self): return
        _cleanup_expired()
        token = _resolve_ticket(ticket)
        if not token:
            self._send_json({"error": "No pending mint matches that ticket. It may have expired (7-day TTL) or already been conceived."}, 404)
            return
        session = _sessions.get(token)
        if not session:
            self._send_json({"error": "Session not found"}, 404)
            return
        if session["status"] != "pending":
            self._send_json({
                "error": f"That ticket is {session['status']}, not pending. Drop a fresh image to start a new mint.",
                "status": session["status"],
            }, 409)
            return

        # Build the rehydration response. Mirrors the upload response
        # so dashboard.js can use the same handler for both paths.
        try:
            from mememage import chains
            from mememage.gps import GPS_SOURCE_PHONE
            _bound = session.get("chain") or chains.current()
            chain_gps_source = chains.get_gps_source(_bound)
        except Exception:
            chain_gps_source = GPS_SOURCE_PHONE
        eff_gps_source = self._effective_gps_source(chain_gps_source)
        mint_url = f"{self._capture_base()}/mint/{token}"
        # Thumbnail data URI so the dashboard can show the image preview
        # without serving it through a separate endpoint. Capped to a
        # reasonable size to avoid huge JSON responses on big uploads.
        thumb_uri = ""
        try:
            from PIL import Image as _Image
            import base64 as _b64
            import io as _io
            with _Image.open(session["image_path"]) as _img:
                _img.thumbnail((256, 256))
                _buf = _io.BytesIO()
                _img.convert("RGB").save(_buf, format="JPEG", quality=78)
                thumb_uri = "data:image/jpeg;base64," + _b64.b64encode(_buf.getvalue()).decode()
        except Exception as e:
            log.warning("Could not build resume thumbnail: %s", e)

        import os as _os
        self._send_json({
            "token": token,
            "ticket": _ticket(token),
            "mint_url": f"/mint/{token}",
            "mint_url_full": mint_url,
            "qr_data_uri": _generate_qr_data_uri(mint_url),
            "metadata": session.get("metadata") or {},
            "gps_source": eff_gps_source,
            "gps_source_configured": chain_gps_source,
            "phone_reachable": self._phone_reachable(),
            "chain": session.get("chain"),
            "filename": _os.path.basename(session["image_path"]),
            "thumb_data_uri": thumb_uri,
            "created": session.get("created"),
        })

    def _list_sessions(self):
        """GET /api/mint/sessions — list active sessions.

        Query params:
          ``status=pending|completed|minting|failed`` — filter by status
          ``limit=N`` — cap to most recent N (default 50)

        Each entry includes ``ticket`` (the short prefix the resume/
        delete flows use) but NOT the full token — that stays
        server-side so listing a session can't leak the bearer URL.

        Auth-gated like the rest of /api/* so a public bind doesn't
        leak the list of pending mints to anyone.
        """
        if not _check_auth(self): return
        _cleanup_expired()
        from urllib.parse import urlparse as _up, parse_qs
        qs = parse_qs(_up(self.path).query or "")
        status_filter = (qs.get("status") or [None])[0]
        try:
            limit = max(1, min(100, int((qs.get("limit") or ["50"])[0])))
        except (TypeError, ValueError):
            limit = 50

        rows = []
        for token, s in list(_sessions.items()):  # snapshot — see _cleanup_expired
            if status_filter and s.get("status") != status_filter:
                continue
            rows.append({
                "ticket": token[:8].upper(),
                "token": token,  # full token for the per-ticket image thumbnail
                "status": s.get("status"),
                "age_seconds": round(time.time() - s.get("created", time.time())),
                "image": os.path.basename(s.get("image_path") or ""),
                "created": s.get("created"),
                "chain": s.get("chain"),
                "chain_name": _chain_name(s.get("chain")),
                "chain_visibility": _chain_visibility(s.get("chain")),
                "chain_readiness": _chain_readiness(s.get("chain")) if s.get("chain") else None,
                # Completed conceptions carry their identifier so the dashboard's
                # "Recently conceived" gallery can label + name downloads without
                # needing the ticket. (Image/soul still served by token.)
                "identifier": (s.get("result") or {}).get("identifier"),
            })
        rows.sort(key=lambda r: r.get("created") or 0, reverse=True)
        self._send_json({"sessions": rows[:limit], "total": len(rows)})

    def _serve_minted_image(self, token):
        """GET /api/mint/<token>/image — download the minted image.

        Streams the file in 64 KiB chunks with explicit flushes between
        each. Reading the whole file into memory + a single ``write()``
        used to drop chunks on the floor over slower / TLS-terminated
        links (Tailscale, etc.): the browser would see HTTP 200 +
        headers but the body would arrive partial, triggering Chrome's
        "Check internet connection" error mid-download.

        We also use the minted-record's identifier as the Content-
        Disposition filename so the saved file is self-identifying
        (was: the staging-token-prefixed UPLOAD_DIR name).
        """
        session = _sessions.get(token)
        if not session:
            self._send_json({"error": "Invalid or expired token"}, 404)
            return
        # Allow pending + minting in addition to completed so the
        # conception page (and dashboard handoff card) can render the
        # staged image as a thumbnail before conception. Failed
        # sessions still refuse — the image may have been scrubbed.
        if session["status"] not in ("pending", "minting", "completed"):
            self._send_json({"error": "Image not available for this session state"}, 400)
            return

        image_path = session.get("image_path")
        if not image_path or not os.path.isfile(image_path):
            self._send_json({"error": "Image file not found"}, 404)
            return

        # Prefer the minted identifier for the download filename — users
        # save these for later inspection and "abc123_paragraph.png"
        # (the staging upload name) is meaningless to them. Falls back
        # to the staging basename when no identifier is recorded.
        result = session.get("result") or {}
        identifier = result.get("identifier")
        if identifier:
            filename = f"{identifier}.png"
        else:
            filename = os.path.basename(image_path)
        size = os.path.getsize(image_path)

        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", size)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        try:
            with open(image_path, "rb") as f:
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            # Client closed the connection mid-stream (cancel, network
            # hiccup, browser back-button). Silent — nothing useful to
            # log and no recovery to attempt.
            log.info("Client closed connection mid-image-download for %s", token)

    def _serve_minted_soul(self, token):
        """GET /api/mint/<token>/soul — download the local .soul file.

        Same shape as ``_serve_minted_image``: streams from the local store.
        Lets the dashboard offer a "Download soul" action — the IA URL can't
        be linked with download= cross-origin, so we serve the local backup
        instead.
        """
        session = _sessions.get(token)
        if not session:
            self._send_json({"error": "Invalid or expired token"}, 404)
            return
        if session["status"] != "completed":
            self._send_json({"error": "Conception not completed"}, 400)
            return
        result = session.get("result") or {}
        identifier = result.get("identifier")
        if not identifier:
            self._send_json({"error": "Identifier missing on completed session"}, 500)
            return
        from mememage.core import soul_store_dir
        records_dir = soul_store_dir()
        soul_path = records_dir / f"{identifier}.soul"
        if not soul_path.is_file():
            self._send_json({"error": "Soul file not found on disk"}, 404)
            return

        size = soul_path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", size)
        self.send_header("Content-Disposition", f'attachment; filename="{identifier}.soul"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            with open(soul_path, "rb") as f:
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            log.info("Client closed connection mid-soul-download for %s", token)

    # ----- Mint execution -----

    def _confirm_mint(self, token):
        """POST /api/mint/<token> — receive GPS, execute mint."""
        _cleanup_expired()

        session = _sessions.get(token)
        if not session:
            self._send_json({"error": "Invalid or expired token"}, 404)
            return
        if session["status"] == "completed":
            self._send_json({"error": "Already conceived"}, 409)
            return
        if session["status"] not in ("pending", "failed"):
            self._send_json({"error": "Conception in progress"}, 409)
            return
        # Atomically claim the session to prevent duplicate mint threads
        if not session.pop("_mintable", True):
            self._send_json({"error": "Conception in progress"}, 409)
            return
        session["_mintable"] = False

        try:
            data = json.loads(self._read_body())
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return

        # GPS resolution.
        #
        # The chain's ``gps_source`` decides where coordinates come from:
        #
        #   - ``phone``   : phone capture page (today's flow); ``lat``/
        #                   ``lon`` MUST be present in the body.
        #   - ``machine`` : server-side IP geolocation; body's coords
        #                   ignored. Fails closed if the lookup errors —
        #                   we don't silently degrade to ``none`` because
        #                   the user picked a different mode.
        #   - ``none``    : record gets no ``gps_time_locked``; coords stay
        #                   ``None`` through the pipeline.
        #
        # A client CAN still send explicit lat/lon and override the
        # chain's mode (CLI / scripts / debugging). The dashboard
        # doesn't do this — it sends an empty body and lets the server
        # resolve from chain config.
        from mememage import chains
        from mememage.gps import (
            fetch_machine_gps,
            GPS_SOURCE_PHONE, GPS_SOURCE_MACHINE, GPS_SOURCE_NONE,
        )
        # Bind to the chain stamped on the session at creation — a chain
        # switch in the dashboard must NOT redirect this conception.
        bound_chain = session.get("chain") or chains.current()
        try:
            chain_info = chains.info(bound_chain)
        except Exception:
            chain_info = {}
        chain_gps_source = chains.get_gps_source(bound_chain) \
            if chain_info else GPS_SOURCE_PHONE
        # Reachability-aware: a 'phone' chain falls back to 'machine' when no
        # phone can reach this host (loopback-only desktop, no Tailscale). The
        # capture page rendered the machine flow in that case, so the body
        # carries no coords — resolve to machine here too rather than erroring.
        resolved_source = self._effective_gps_source(chain_gps_source)

        body_lat = data.get("lat")
        body_lon = data.get("lon")
        gps_tuple: tuple[float, float] | None
        if body_lat is not None and body_lon is not None:
            try:
                gps_tuple = (float(body_lat), float(body_lon))
            except (ValueError, TypeError):
                self._send_json({"error": "lat and lon must be numbers"}, 400)
                return
            effective_source = GPS_SOURCE_PHONE
        elif resolved_source == GPS_SOURCE_PHONE:
            self._send_json({"error": "lat and lon required (chain gps_source is 'phone')"}, 400)
            return
        elif resolved_source == GPS_SOURCE_MACHINE:
            fetched = fetch_machine_gps()
            if fetched is None:
                self._send_json({
                    "error": "Machine GPS lookup failed. Check network connectivity, "
                             "or switch the chain's GPS source to 'none' in the Config tab."
                }, 502)
                return
            gps_tuple = fetched
            effective_source = GPS_SOURCE_MACHINE
        else:  # GPS_SOURCE_NONE
            gps_tuple = None
            effective_source = GPS_SOURCE_NONE

        # Access-layer resolution.
        #
        # Both password and visibility are now CHAIN properties — set
        # once via the Config tab, consulted at every mint. The client
        # CAN still pass values for programmatic / CLI / override use
        # (e.g. a user who wants to seal a single mint with a different
        # password than the chain's stored one), but the dashboard
        # doesn't send either anymore. Resolution order (canonical, via
        # chains.resolve_password — see that for the full precedence):
        #
        #   1. Explicit client value
        #   2. Active chain's chain.json
        #   3. MEMEMAGE_PASSWORD env var (paranoid path)
        #   4. None (light_energy stays public, dark_matter fails clearly)
        chain_visibility = data.get("chain_visibility") or None
        if chain_visibility and chain_visibility not in ("light_energy", "dark_matter"):
            self._send_json({"error": "chain_visibility must be light_energy or dark_matter"}, 400)
            return
        if not chain_visibility:
            v = chain_info.get("visibility")
            if v in ("light_energy", "dark_matter"):
                chain_visibility = v
        from mememage import chains as _chains
        # Per-mint client value wins; otherwise the active chain's held
        # runtime password (entered once via /api/chain/unlock).
        password = _chains.resolve_password(
            chain_id=bound_chain,
            override=(data.get("password") or _held_password(bound_chain) or None))
        # Dark chains require a key. Fail early with a useful message
        # rather than letting mint() proceed and leave the soul fields
        # unencrypted (which would violate the chain's contract).
        if chain_visibility == "dark_matter" and not password:
            self._send_json({
                "error": "Dark chain requires a password. Configure it in the dashboard's "
                         "Config → Chains section, or set MEMEMAGE_PASSWORD in the environment."
            }, 400)
            return
        # Reject a wrong password against the chain verifier before we
        # encrypt anything (a mismatched key seals an unrecoverable record).
        if password and _chains.verify_password(password) is False:
            self._send_json({
                "error": "Password does not match the chain seal."
            }, 400)
            return

        session["status"] = "minting"
        session["gps"] = ({"lat": gps_tuple[0], "lon": gps_tuple[1]}
                          if gps_tuple is not None
                          else {"source": effective_source})
        session["gps_source"] = effective_source

        def _do_mint():
            with _mint_lock:  # Serialize to protect lineage chain
                try:
                    # mint() resolves lineage / chunks / payload via
                    # chains.current(), so the bound chain must be ACTIVE
                    # for the call. Switch under the mint lock, restore in
                    # finally so a completed conception cannot hijack the
                    # dashboard's active chain.
                    _prev_chain = chains.current()
                    if bound_chain and bound_chain != _prev_chain:
                        chains.switch(bound_chain)
                    try:
                        result = mint(
                            metadata=session["metadata"],
                            gps=gps_tuple,
                            image_path=session["image_path"],
                            password=password,
                            chain_visibility=chain_visibility,
                        )
                    finally:
                        if bound_chain and bound_chain != _prev_chain:
                            chains.switch(_prev_chain)
                    session["status"] = "completed"
                    dist = result.distribution or {}
                    session["result"] = {
                        "identifier": result.identifier,
                        "content_hash": result.content_hash,
                        "url": result.url,
                        "image_path": result.image_path,
                        "distribution": dict(dist),
                        # Per-channel failures from the blast — channels
                        # that errored while others succeeded. Empty {}
                        # when every enabled channel landed cleanly.
                        # Conception page renders this alongside the
                        # success list so users can spot partial drops
                        # (IA timeout while MememageTest succeeded, etc.)
                        # without scraping logs.
                        "distribution_errors": getattr(dist, "errors", {}),
                        "gps": ({"lat": gps_tuple[0], "lon": gps_tuple[1]}
                                if gps_tuple is not None
                                else {"source": effective_source}),
                        "gps_source": effective_source,
                    }
                    _save_sessions()
                    _notify_conceived(result)
                except Exception as e:
                    session["status"] = "failed"
                    session["error"] = str(e)
                    _save_sessions()
                    log.exception("Mint failed")

        threading.Thread(target=_do_mint, daemon=True).start()
        self._send_json({"status": "minting"})

    # ----- HTML pages -----

    def _serve_upload_page(self):
        """GET /mint/new — manual upload page (injects API token for auth)."""
        token = _load_mint_token() or ''
        html = _UPLOAD_PAGE_HTML.replace('{{API_TOKEN}}', token)
        self._send_html(html)

    def _serve_docs_html(self, filename):
        """Serve a file from docs/ as text/html.

        Substitutes {{MINT_API_TOKEN}} with the configured token (or empty
        string) so the page can authenticate against /api/* without the
        user having to paste a token. Callers are responsible for
        gating auth themselves — this raw serve embeds the token in
        plaintext HTML, which is fine on localhost but a token-leak
        on a public bind. Use ``_serve_dashboard`` for the
        public-deploy-safe version.
        """
        p = DOCS_DIR / filename
        if not p.exists() or not p.is_file():
            self.send_error(404)
            return
        html = p.read_text(encoding="utf-8")
        html = html.replace("{{MINT_API_TOKEN}}", _load_mint_token() or "")
        # Decoder/Validator links adapt to the deployment shape: a public
        # souls host when one is configured, else the locally-served
        # decoder (single-domain / desktop install — the common case).
        decoder_url, validator_url = self._decoder_urls()
        html = html.replace("{{DECODER_URL}}", decoder_url) \
                   .replace("{{VALIDATOR_URL}}", validator_url)
        self._send_html(html)

    def _decoder_urls(self):
        """(decoder_url, validator_url) for the dashboard's portal links.

        A configured souls_domain is the public decode face
        (``https://souls.<d>/``) — but only for a PUBLIC admin request. A
        local/desktop/LAN request links straight to ``/decoder`` on this same
        host (which serves the decoder inline), so the dashboard opens the
        LOCAL decoder rather than bouncing to a remote souls host the user may
        only have set as their read-base. Single-domain installs always link
        local.
        """
        souls_domain = (_get_server_config().get("souls_domain") or "").strip()
        if souls_domain and not self._is_local_request():
            return f"https://{souls_domain}/", f"https://{souls_domain}/validator"
        return "/decoder", "/validator"

    def _serve_decoder_html(self, filename):
        """Serve the public decoder/validator from docs/ with the local
        souls base injected so a self-hosted copy defaults its record
        Source to this deployment's surface instead of the Internet
        Archive.

        Replaces the ``<!--MEMEMAGE_SOULS_BASE-->`` marker comment with a
        ``<script>`` setting ``window.MEMEMAGE_SOULS_BASE`` (consumed by
        docs/js/data.js's ``SOURCE_DEFAULT``). On GitHub Pages — where no
        server touches the file — the comment stays inert and the IA
        default applies, so the same build serves both surfaces.

        Unlike ``_serve_docs_html`` this does NOT inject the
        MINT_API_TOKEN: the decoder/validator are public pages and the
        admin token has no business in them.
        """
        p = DOCS_DIR / filename
        if not p.exists() or not p.is_file():
            self.send_error(404)
            return
        html = p.read_text(encoding="utf-8")
        inject = "<script>window.MEMEMAGE_SOULS_BASE=" + \
            json.dumps(self._souls_read_base()) + ";</script>"
        html = html.replace("<!--MEMEMAGE_SOULS_BASE-->", inject)
        self._send_html(html)

    def _serve_dashboard(self, query_string):
        """Token-gated dashboard serve.

        Behavior:
          - If MINT_API_TOKEN is NOT configured (open-localhost dev mode)
            → serve dashboard.html as-is, no gate. Same UX as before.
          - If MINT_API_TOKEN IS configured → require it via either
            ``?token=<value>`` query string OR ``Authorization: Bearer
            <value>`` header. On match: serve the dashboard with the
            token substituted in. On mismatch: serve a minimal login
            page that posts a form back to ``/dashboard?token=...``.

        Closes the token-leak gap on public deployments: a scanner
        hitting ``/dashboard`` without auth gets a login form, not the
        bearer token in plaintext JS.
        """
        expected = _load_mint_token()
        if not expected:
            # No token configured — treat as open dev mode, serve directly.
            self._serve_docs_html("dashboard.html")
            return

        # Token is required. Accept it via query string OR Authorization header.
        supplied = None
        if query_string:
            from urllib.parse import parse_qs
            qs = parse_qs(query_string)
            if "token" in qs and qs["token"]:
                supplied = qs["token"][0]
        if not supplied:
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                supplied = auth[len("Bearer "):].strip()

        if supplied == expected:
            self._serve_docs_html("dashboard.html")
            return

        # No / wrong token — serve a minimal login form. Never echoes
        # back what was supplied; doesn't reveal whether the token was
        # absent vs incorrect (same response for both).
        self._send_html(_DASHBOARD_LOGIN_HTML, status=401)

    def _serve_docs_static(self, path):
        """Serve a static asset from docs/ — CSS, JS, images, samples.

        Restricted to the prefixes in STATIC_PREFIXES and to files that
        actually resolve inside DOCS_DIR (defends against path traversal).
        """
        import mimetypes
        rel = path.lstrip("/")
        full = DOCS_DIR / rel
        try:
            full.resolve().relative_to(DOCS_DIR.resolve())
        except ValueError:
            self.send_error(403)
            return
        if not full.exists() or not full.is_file():
            self.send_error(404)
            return
        ctype, _ = mimetypes.guess_type(str(full))
        if not ctype:
            ctype = "application/octet-stream"
        data = full.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    # ----- Dashboard: Payload tab -----

    def _payload_status(self):
        """GET /api/payload/status — artifact currency vs sources, plus whether
        the active chain's seal is stale (payload changed since it was sealed,
        so changes won't reach conceptions until a re-seal)."""
        if not _check_auth(self): return
        from mememage import payload, site_pack
        st = payload.status() or {}
        try:
            drift = site_pack.seal_drift()
        except Exception:
            drift = []
        st["seal_stale"] = bool(drift)
        st["seal_drift_layers"] = drift
        self._send_json(st)

    def _payload_build(self):
        """POST /api/payload/build — regenerate Payload/ from active sources."""
        if not _check_auth(self): return
        from mememage import payload
        try:
            manifest = payload.build()
            self._send_json({"ok": True, "manifest": manifest})
        except Exception as e:
            log.exception("payload build failed")
            self._send_json({"ok": False, "error": str(e)}, 500)

    def _payload_upload(self):
        """POST /api/payload/upload — accept a payload SOURCE file and save it
        under the active chain's uploads/ folder, returning the absolute path.

        The file is the RAW request body, STREAMED to disk — never held whole in
        memory or base64-inflated — so large sources (audio, video, big images)
        upload fine. The filename rides in the ``X-Payload-Filename`` header,
        URL-encoded. Files land at ``<chain_dir>/uploads/<sanitized>`` so they
        round-trip with the chain; same-name files are overwritten (the
        dashboard prompts on collision first). The path is suitable as a source
        value in payload.entries[*].sources.
        """
        if not _check_auth(self): return
        from urllib.parse import unquote
        import os as _os
        filename = unquote((self.headers.get("X-Payload-Filename") or "").strip())
        if not filename:
            self._send_json({"error": "X-Payload-Filename header required"}, 400)
            return
        # Sanitize: keep only the basename so a malicious name can't escape the
        # uploads directory.
        safe_name = _os.path.basename(filename)
        if safe_name in ("", ".", "..") or "\0" in safe_name:
            self._send_json({"error": "Invalid filename"}, 400)
            return
        cap = _payload_max_bytes()
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > cap:
            self._send_json({"error": f"Content-Length missing or over the {cap // (1024*1024)} MiB cap"}, 400)
            return

        from mememage import chains as _chains
        chain_id = _chains.current()
        uploads = _chains.chain_dir(chain_id) / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        dest = uploads / safe_name
        tmp = dest.with_name(dest.name + ".part")
        try:
            written, _ = _stream_body_to_file(self.rfile, length, tmp)
        except Exception as e:
            try:
                tmp.unlink()
            except OSError:
                pass
            self._send_json({"error": f"Upload failed: {e}"}, 400)
            return
        tmp.replace(dest)
        self._send_json({
            "ok": True,
            "path": str(dest),
            "filename": safe_name,
            "size": written,
        })

    def _payload_upload_delete(self):
        """POST /api/payload/upload/delete — unlink a file from the active
        chain's uploads/ folder. Idempotent (missing file → ok, no-op).

        Body (JSON):
            { "path": "<absolute path returned by /api/payload/upload>" }

        Containment: the resolved path must live under <chain_dir>/uploads/
        on the active chain. Anything outside that scope is refused so a
        malicious or buggy client can't unlink arbitrary files.

        Used by the dashboard to clean up after: (a) the user clicks the
        × on a source row, (b) the user removes an entry that owned
        uploads, (c) the user re-uploads a different file into the same
        source slot. The client only fires this when no other in-draft
        entry/source slot still references the path.
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        raw = (data.get("path") or "").strip()
        if not raw:
            self._send_json({"error": "path required"}, 400)
            return

        from mememage import chains as _chains
        chain_id = _chains.current()
        uploads = (_chains.chain_dir(chain_id) / "uploads").resolve()
        try:
            target = Path(raw).resolve()
        except Exception:
            self._send_json({"error": "Invalid path"}, 400)
            return

        # Containment — never unlink anything outside the uploads dir.
        try:
            target.relative_to(uploads)
        except ValueError:
            self._send_json({"error": "path outside chain uploads dir"}, 400)
            return

        if not target.exists():
            # Idempotent — already gone or never existed.
            self._send_json({"ok": True, "deleted": False, "path": str(target)})
            return

        if not target.is_file():
            self._send_json({"error": "path is not a regular file"}, 400)
            return

        try:
            target.unlink()
        except Exception as e:
            self._send_json({"error": f"unlink failed: {e}"}, 500)
            return
        self._send_json({"ok": True, "deleted": True, "path": str(target)})

    def _payload_inspect(self, name):
        """GET /api/payload/inspect/<name> — preview an artifact."""
        if not _check_auth(self): return
        from mememage import payload
        # Defend against traversal: only allow names from the manifest.
        try:
            data = payload.inspect_data(name)
        except FileNotFoundError as e:
            self._send_json({"error": str(e)}, 404)
            return
        except KeyError:
            self._send_json({"error": f"No such artifact: {name}"}, 404)
            return
        self._send_json(data)

    # ----- Dashboard: payload presets -----
    #
    # Layout (B — self-contained, dir-per-preset):
    #
    #   ~/.mememage/payload_presets/<name>/
    #     preset.json                            # portable config, relative paths
    #     files/<entry_name>/<basename>          # copies of the source files
    #
    # Mirrors ``chains/<id>/{config.json, uploads/...}`` so the mental
    # model is symmetric. Save copies files in; load resolves relative
    # paths to absolute; chain-config save copies preset-resident files
    # into the chain's uploads/ so the chain stops depending on the
    # preset after Apply. Deleting a preset is a single rmtree.

    def _preset_root(self):
        """Return ``~/.mememage/payload_presets/`` (the root above all preset dirs)."""
        from pathlib import Path
        d = Path.home() / ".mememage" / "payload_presets"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _preset_dir(self, name=None):
        """Return the per-preset directory (creating root if absent).

        With ``name`` returns ``<root>/<name>/`` (does NOT create the
        per-preset dir — callers do that explicitly). Without it,
        returns the root for back-compat with older callers.
        """
        if name is None:
            return self._preset_root()
        return self._preset_root() / name

    @staticmethod
    def _sanitize_entry_dir(entry_name):
        """Filesystem-safe dirname for an entry (used inside preset/files/)."""
        import os as _os, re as _re
        safe = _re.sub(r"[^A-Za-z0-9_.-]", "_", entry_name or "_unnamed")
        if safe in ("", ".", ".."):
            safe = "_unnamed"
        return safe

    def _preset_inline_files(self, cfg, preset_dir):
        """Copy every source file referenced by cfg into preset_dir/files/...

        Mutates ``cfg`` in place: rewrites each entry's sources to
        relative paths of the form ``files/<entry>/<basename>``. Returns
        a list of (entry_name, basename) tuples for files that couldn't
        be copied (path didn't exist, etc) — caller decides whether to
        fail or warn.
        """
        import os as _os, shutil as _shutil
        from pathlib import Path
        missing = []
        files_root = preset_dir / "files"
        entries = cfg.get("entries") or {}
        for entry_name, entry in entries.items():
            srcs = entry.get("sources")
            if not srcs and entry.get("source"):
                srcs = [entry["source"]]
            if not srcs:
                continue
            safe_dir = self._sanitize_entry_dir(entry_name)
            dest_dir = files_root / safe_dir
            new_sources = []
            for src in srcs:
                src_path = Path(src) if not isinstance(src, Path) else src
                basename = src_path.name or "file"
                rel = f"files/{safe_dir}/{basename}"
                # If already a relative preset-local path (idempotent save
                # — preset hydrated by GET, re-saved), resolve to its
                # absolute form before copy. Otherwise treat as absolute.
                if not src_path.is_absolute():
                    src_abs = preset_dir / src_path
                else:
                    src_abs = src_path
                if not src_abs.exists():
                    missing.append((entry_name, str(src_abs)))
                    # Skip rewriting — keep the original path so the user
                    # can see what was missing instead of a silent rename.
                    new_sources.append(src)
                    continue
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / basename
                # Re-save case: source for an untouched entry is already
                # the preset-resident file (resolved by _preset_resolve_paths
                # on GET). Skip the copy — it would raise SameFileError —
                # but still rewrite to the relative form so the preset stays
                # portable across machines.
                if dest.exists() and src_abs.samefile(dest):
                    new_sources.append(rel)
                    continue
                try:
                    _shutil.copy2(str(src_abs), str(dest))
                except Exception as e:
                    log.warning("preset_save: copy %s → %s failed: %s", src_abs, dest, e)
                    missing.append((entry_name, str(src_abs)))
                    new_sources.append(src)
                    continue
                new_sources.append(rel)
            if "sources" in entry:
                entry["sources"] = new_sources
            elif "source" in entry and new_sources:
                entry["source"] = new_sources[0]
        return missing

    def _prune_preset_orphans(self, cfg, preset_dir):
        """Remove files under <preset_dir>/files/ not referenced by cfg.

        Called after ``_preset_inline_files`` on every preset save. The
        inline step writes new files for entries the user changed and
        rewrites every entry's sources to point at the preset-resident
        copy; this sweep drops anything left behind from a prior save
        (source replaced with a different file, source removed entirely,
        entry deleted). Files for entries the user *didn't* touch stay
        — they're still in the referenced set.

        We use Path.resolve() for the comparison so symlink/normalization
        differences don't cause a referenced file to be misidentified as
        an orphan.
        """
        from pathlib import Path
        files_root = preset_dir / "files"
        if not files_root.is_dir():
            return
        referenced = set()
        entries = cfg.get("entries") or {}
        for entry in entries.values():
            for key in ("sources", "source"):
                if key not in entry:
                    continue
                val = entry[key]
                paths = val if isinstance(val, list) else [val]
                for p in paths:
                    if not isinstance(p, str) or not p:
                        continue
                    pp = Path(p)
                    if not pp.is_absolute():
                        pp = preset_dir / pp
                    try:
                        referenced.add(pp.resolve())
                    except OSError:
                        continue
        # Sweep files first
        for f in list(files_root.rglob("*")):
            if not f.is_file():
                continue
            try:
                if f.resolve() in referenced:
                    continue
                f.unlink()
            except OSError as e:
                log.warning("preset prune: unlink %s failed: %s", f, e)
        # Then empty directories (deepest first so parents are eligible)
        dirs = sorted(
            [d for d in files_root.rglob("*") if d.is_dir()],
            key=lambda p: -len(p.parts),
        )
        for d in dirs:
            try:
                d.rmdir()
            except OSError:
                # Directory not empty or permission issue — leave it.
                pass

    def _preset_resolve_paths(self, cfg, preset_dir):
        """Resolve relative ``files/...`` paths in cfg to absolute paths.

        Mutates cfg in place. Called on GET so the dashboard always
        sees absolute paths it can pass straight to the file picker
        + chain config save.
        """
        from pathlib import Path
        entries = cfg.get("entries") or {}
        for entry_name, entry in entries.items():
            for key in ("sources", "source"):
                if key not in entry:
                    continue
                val = entry[key]
                if isinstance(val, list):
                    entry[key] = [
                        str((preset_dir / p).resolve()) if isinstance(p, str) and p.startswith("files/")
                        else p
                        for p in val
                    ]
                elif isinstance(val, str) and val.startswith("files/"):
                    entry[key] = str((preset_dir / val).resolve())

    @staticmethod
    def _sanitize_preset_name(name):
        """Strip whitespace, refuse traversal/path separators, refuse empty.
        Returns the cleaned name or raises ValueError.
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("Preset name is required.")
        if any(c in name for c in ("/", "\\", "\0")) or name in (".", ".."):
            raise ValueError("Preset name must not contain path separators.")
        # Keep filesystem-safe: letters, digits, hyphen, underscore, dot, space.
        bad = [c for c in name if not (c.isalnum() or c in "-_. ")]
        if bad:
            raise ValueError(f"Preset name has invalid characters: {''.join(set(bad))!r}")
        return name

    def _preset_list(self):
        """GET /api/payload/presets — list saved presets (name + mtime)."""
        if not _check_auth(self): return
        import datetime
        root = self._preset_root()
        items = []
        # New layout: <root>/<name>/preset.json. Scan directories.
        for d in sorted(root.iterdir() if root.exists() else []):
            if not d.is_dir():
                continue
            p = d / "preset.json"
            if not p.exists():
                continue
            try:
                mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
            except OSError:
                mtime = None
            items.append({"name": d.name, "modified": mtime})
        self._send_json({"presets": items})

    def _preset_get(self, name):
        """GET /api/payload/presets/<name> — return preset body.

        Resolves relative ``files/...`` source paths inside the preset's
        own files/ dir to absolute paths so the dashboard can use them
        directly (file picker / source field / chain-config Apply).
        """
        if not _check_auth(self): return
        try:
            clean = self._sanitize_preset_name(name)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
            return
        preset_dir = self._preset_dir(clean)
        path = preset_dir / "preset.json"
        if not path.exists():
            self._send_json({"error": f"Preset {clean!r} not found"}, 404)
            return
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            self._send_json({"error": f"Preset unreadable: {e}"}, 500)
            return
        self._preset_resolve_paths(body, preset_dir)
        self._send_json({"name": clean, "config": body})

    def _preset_save(self):
        """POST /api/payload/presets — save a preset.

        Body: {"name": "...", "config": {...}}

        ``config`` is the payload portion of a chain config — entries,
        layers, pinned, M, schema_version. Per-chain identity (id, name,
        visibility) is stripped before storing so the preset is portable.
        If ``config`` is omitted, snapshots the current chain's config.
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        try:
            name = self._sanitize_preset_name(data.get("name"))
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
            return
        cfg = data.get("config")
        if cfg is None:
            from mememage import chain_config
            try:
                cfg = chain_config.load().to_dict()
            except (RuntimeError, ValueError) as e:
                self._send_json({"error": f"Could not snapshot current config: {e}"}, 500)
                return
        if not isinstance(cfg, dict):
            self._send_json({"error": "'config' must be a dict"}, 400)
            return
        # Strip per-chain identity — presets are portable.
        portable = {k: v for k, v in cfg.items() if k not in ("id", "name", "visibility")}
        # Copy every source file into the preset's own files/ subdir
        # and rewrite source paths to ``files/<entry>/<basename>``. The
        # preset becomes self-contained — survives chain deletion,
        # zip-and-ship to another machine, etc.
        preset_dir = self._preset_dir(name)
        preset_dir.mkdir(parents=True, exist_ok=True)
        missing = self._preset_inline_files(portable, preset_dir)
        # Drop files the new config no longer references. Entries the user
        # didn't touch stay because their paths are still in the set.
        self._prune_preset_orphans(portable, preset_dir)
        path = preset_dir / "preset.json"
        path.write_text(json.dumps(portable, indent=2), encoding="utf-8")
        resp = {"ok": True, "name": name, "path": str(path)}
        if missing:
            # Soft warning — the preset is still saved, but flag which
            # source files couldn't be copied (paths didn't exist, etc).
            resp["missing"] = [{"entry": e, "source": s} for (e, s) in missing]
        self._send_json(resp)

    def _preset_delete(self, name):
        """POST /api/payload/presets/<name>/delete — remove a preset.

        Removes the entire preset directory (preset.json + files/...).
        """
        if not _check_auth(self): return
        import shutil
        try:
            clean = self._sanitize_preset_name(name)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
            return
        preset_dir = self._preset_dir(clean)
        if not preset_dir.exists():
            self._send_json({"error": f"Preset {clean!r} not found"}, 404)
            return
        try:
            shutil.rmtree(preset_dir)
        except OSError as e:
            self._send_json({"error": f"Could not delete: {e}"}, 500)
            return
        self._send_json({"ok": True, "name": clean})

    # ----- Dashboard: site_pack (Age seal) -----

    # Cache the forecast briefly — Monte-Carlo over 2000 samples is
    # cheap-ish but enough to dominate a dashboard refresh if every
    # tab visit triggers a fresh run. 30s feels right: sky moves
    # imperceptibly at that cadence, machine vitals barely shift.
    _FORECAST_CACHE_TTL = 30.0
    _forecast_cache = {"ts": 0.0, "n": 0, "report": None}

    def _forecast(self, query_string):
        """GET /api/forecast?n=2000 — Monte-Carlo rarity forecast for
        the next mint against current conditions.

        Mirrors the CLI ``mememage forecast`` but reduced N (2000 by
        default, capped 10000) for snappy widget refresh. The full
        forecast dict round-trips as JSON — see mememage/forecast.py
        for the shape (tier_pct, candidates_*, fire_rate_*, etc.).
        """
        if not _check_auth(self): return
        n = 2000
        if query_string:
            from urllib.parse import parse_qs
            qs = parse_qs(query_string)
            try:
                n = int((qs.get("n") or ["2000"])[0])
            except (TypeError, ValueError):
                n = 2000
        n = max(500, min(10000, n))

        cache = MintHandler._forecast_cache
        now = time.time()
        if (cache.get("report") and cache.get("n") == n
                and now - cache.get("ts", 0) < MintHandler._FORECAST_CACHE_TTL):
            self._send_json(cache["report"])
            return

        try:
            from mememage.forecast import forecast as _run
            report = _run(n=n)
        except Exception as e:
            log.warning("Forecast failed: %s", e)
            self._send_json({"error": f"Forecast failed: {e}"}, 500)
            return
        MintHandler._forecast_cache = {"ts": now, "n": n, "report": report}
        self._send_json(report)

    def _onboarding_status(self):
        """GET /api/onboarding/status — first-run checklist for the
        welcome card.

        Returns four steps in order, each with done/pending state +
        a short detail string. The dashboard renders the card when
        any step is pending; the card auto-hides when everything's
        green. Pure read — no state stored.

        Steps:
          1. identity        — at least one profile with a signing key
          2. distribution    — at least one enabled+configured channel
          3. chain           — at least one chain on disk
          4. first_conception — at least one record minted on this host
        """
        if not _check_auth(self): return

        steps = []

        # --- Identity ---
        from mememage import profiles
        signed_profiles = [
            p for p in profiles.list_profiles() if p.get("has_private_key")
        ]
        if signed_profiles:
            active = next((p for p in signed_profiles if p.get("is_active")),
                          signed_profiles[0])
            name = active.get("name") or active.get("id")
            fp = (active.get("fingerprint") or "")[:9]
            steps.append({
                "id": "identity", "label": "Identity", "done": True,
                "detail": f"{name} ({fp}\u2026)",
                "tab": "tab-config", "anchor": "configProfiles",
            })
        else:
            steps.append({
                "id": "identity", "label": "Identity", "done": False,
                "detail": "Optional — add a signing key to prove authorship (AUTHENTICATED)",
                "tab": "tab-config", "anchor": "configIdentity",
            })

        # --- Distribution ---
        from mememage import channels as _ch
        live = [c for c in _ch.load_channels() if c.enabled and c.is_configured()]
        if live:
            ids = [c.id for c in live]
            # NB: keep the ellipsis OUT of the f-string expression \u2014 a
            # backslash inside f-string {} is a SyntaxError before Python
            # 3.12, and we target 3.10+ (this exact line broke the 3.11
            # Windows desktop build).
            _ell = "\u2026" if len(ids) > 2 else ""
            label = ids[0] if len(ids) == 1 else f"{len(ids)} channels ({', '.join(ids[:2])}{_ell})"
            steps.append({
                "id": "distribution", "label": "Distribution", "done": True,
                "detail": label,
                "tab": "tab-config", "anchor": "configChannels",
            })
        else:
            steps.append({
                "id": "distribution", "label": "Distribution", "done": False,
                "detail": "Configure at least one channel so your souls have a home",
                "tab": "tab-config", "anchor": "configChannels",
            })

        # --- Chain ---
        from mememage import chains
        chain_list = chains.list_chains()
        if chain_list:
            active_cid = chains.current()
            label = active_cid if len(chain_list) == 1 else f"{active_cid} (of {len(chain_list)})"
            steps.append({
                "id": "chain", "label": "Chain", "done": True,
                "detail": label,
                "tab": "tab-config", "anchor": "configChains",
            })
        else:
            steps.append({
                "id": "chain", "label": "Chain", "done": False,
                "detail": "Create a chain — the universe your conceptions populate",
                "tab": "tab-config", "anchor": "configChains",
            })

        # --- First conception ---
        # Any soul in the flat store = the user has conceived at least once
        # (any chain). The store is shared across chains now, so one glob
        # replaces the per-chain records/ walk.
        first_done = False
        try:
            from mememage.core import soul_store_dir
            store = soul_store_dir()
            first_done = store.is_dir() and any(store.glob("*.soul"))
        except Exception:
            pass
        if first_done:
            steps.append({
                "id": "first_conception", "label": "First conception", "done": True,
                "detail": "You\u2019ve walked the path",
                "tab": "tab-mint",
            })
        else:
            steps.append({
                "id": "first_conception", "label": "First conception", "done": False,
                "detail": "Drop an image in the Mint tab to bring it across",
                "tab": "tab-mint",
            })

        complete = all(s["done"] for s in steps)
        self._send_json({"complete": complete, "steps": steps})

    def _site_pack_status(self):
        """GET /api/site-pack/status — current Age + cycle position.

        Also reports ``has_payload`` so the front-end mint guardrail can
        mirror _require_chain_sealed: a provenance-only chain (no payload)
        is conceivable even while unsealed, so the drop zone must NOT block
        on ``sealed === false`` alone.
        """
        if not _check_auth(self): return
        from mememage.site_embed import get_current_age_info
        from mememage import chain_config
        try:
            has_payload = chain_config.load().has_payload()
        except Exception:
            has_payload = False
        info = get_current_age_info()
        if info is None:
            self._send_json({"sealed": False, "has_payload": has_payload})
        else:
            info["sealed"] = True
            info["has_payload"] = has_payload
            self._send_json(info)

    def _site_pack_seal(self):
        """POST /api/site-pack/seal — begin a new Age. Confirmation required.

        Body: {"confirm": "SEAL"}  — exact string, prevents accidental triggers.
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        if data.get("confirm") != "SEAL":
            self._send_json({"error": "Body must include {\"confirm\": \"SEAL\"}"}, 400)
            return
        from mememage.site_pack import seal
        try:
            info = seal()
            self._send_json({"ok": True, "info": info})
        except (RuntimeError, ValueError) as e:
            self._send_json({"ok": False, "error": str(e)}, 400)
        except Exception as e:
            log.exception("seal failed")
            self._send_json({"ok": False, "error": str(e)}, 500)

    # ----- Dashboard: channel cleanup (Config tab) -----
    #
    # Channel-keyed maintenance: scan / hide / purge run through the
    # active Channel plugin's cleanup methods. IA implements all three;
    # other channels declare what they can do via ``capabilities()``.
    # The dashboard's IA-cleanup section renders buttons based on
    # capabilities so unsupported operations are visibly disabled.
    # Destructive endpoints require a typed confirmation token in the
    # body (HIDE / PURGE) — stray clicks can't damage anything.

    def _find_channel(self, channel_id: str):
        """Return the loaded Channel instance with this id, or None."""
        from mememage.channels import load_channels
        for ch in load_channels():
            if ch.id == channel_id:
                return ch
        return None

    def _channels_capabilities(self):
        """GET /api/channels/capabilities — report each channel's
        cleanup operations. The dashboard uses this to enable / disable
        Scan, Hide, Purge buttons per channel.
        """
        if not _check_auth(self): return
        from mememage.channels import load_channels
        out = []
        for ch in load_channels():
            try:
                caps = ch.capabilities()
            except Exception as e:
                caps = {"search": False, "hide": False, "purge": False,
                        "error": str(e)}
            out.append({
                "id": ch.id,
                "type": ch.TYPE,
                "name": ch.name,
                "enabled": ch.enabled,
                "configured": ch.is_configured(),
                "capabilities": caps,
            })
        self._send_json({"channels": out})

    def _channel_scan(self, channel_id: str):
        """POST /api/channel/<id>/scan — list items via the channel's
        search() method. Body forwards any extra filters (uploader,
        collection, etc.) as kwargs.
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        ch = self._find_channel(channel_id)
        if ch is None:
            self._send_json({"error": f"Unknown channel: {channel_id}"}, 404)
            return
        # Strip non-filter keys so they don't poison the channel's
        # signature. ``pattern`` and ``limit`` are first-class; the
        # rest pass through as **filters.
        pattern = data.get("pattern") or "mememage-*"
        try:
            limit = int(data.get("limit") or 200)
        except (TypeError, ValueError):
            limit = 200
        extra = {k: v for k, v in data.items()
                 if k not in ("pattern", "limit") and v not in (None, "")}
        try:
            items = ch.search(pattern=pattern, limit=limit, **extra)
        except NotImplementedError as e:
            self._send_json({"error": str(e)}, 400)
            return
        except Exception as e:
            log.exception("Channel scan failed: %s", channel_id)
            self._send_json({"error": str(e)}, 502)
            return
        self._send_json({"items": items, "count": len(items),
                         "channel": channel_id})

    def _channel_test(self, channel_id: str):
        """POST /api/channel/<id>/test — live reachability + auth probe via
        the channel's test() method. Writes nothing; returns ``{ok, detail}``.
        """
        if not _check_auth(self): return
        ch = self._find_channel(channel_id)
        if ch is None:
            self._send_json({"error": f"Unknown channel: {channel_id}"}, 404)
            return
        try:
            result = ch.test()
        except NotImplementedError as e:
            self._send_json({"error": str(e)}, 400)
            return
        except Exception as e:
            log.exception("Channel test failed: %s", channel_id)
            self._send_json({"ok": False, "detail": str(e),
                             "channel": channel_id})
            return
        out = dict(result or {})
        out["channel"] = channel_id
        self._send_json(out)

    def _channel_hide(self, channel_id: str):
        """POST /api/channel/<id>/hide — make selected items invisible
        to public discovery (channel-specific semantics; IA: noindex).

        Body: ``{"identifiers": [...], "confirm": "HIDE"}``.
        """
        if not _check_auth(self): return
        self._channel_destructive(channel_id, "hide", "HIDE",
                                  result_summary_keys=("succeeded",))

    def _channel_purge(self, channel_id: str):
        """POST /api/channel/<id>/purge — delete selected items'
        content (identifier may still survive as tombstone).

        Body: ``{"identifiers": [...], "confirm": "PURGE"}``.
        """
        if not _check_auth(self): return
        self._channel_destructive(channel_id, "purge", "PURGE",
                                  result_summary_keys=("files_deleted", "files_failed"))

    def _channel_destructive(self, channel_id: str, method_name: str,
                             confirm_token: str, *,
                             result_summary_keys: tuple = ()):
        """Shared body for hide/purge. Validates auth + confirmation
        token, looks up the channel, iterates the identifiers list,
        invokes the per-item method, aggregates results."""
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        if data.get("confirm") != confirm_token:
            self._send_json({"error": f'Body must include {{"confirm": "{confirm_token}"}}'}, 400)
            return
        identifiers = data.get("identifiers") or []
        if not isinstance(identifiers, list) or not identifiers:
            self._send_json({"error": "identifiers (list) required"}, 400)
            return
        ch = self._find_channel(channel_id)
        if ch is None:
            self._send_json({"error": f"Unknown channel: {channel_id}"}, 404)
            return
        method = getattr(ch, method_name, None)
        if method is None:
            self._send_json({"error": f"Channel does not implement {method_name}()"}, 400)
            return
        results = []
        ok_count = 0
        files_deleted = 0
        files_failed = 0
        for ident in identifiers:
            if not isinstance(ident, str) or not re.match(r"[A-Za-z][A-Za-z0-9_-]*-[0-9a-f]{12,16}$", ident):
                results.append({"identifier": str(ident), "ok": False,
                                "error": "identifier must look like <prefix>-<hex> (e.g. mememage-… or dark-…)"})
                continue
            try:
                r = method(ident)
            except NotImplementedError as e:
                self._send_json({"error": str(e)}, 400)
                return
            except Exception as e:
                r = {"ok": False, "error": str(e)}
            results.append({"identifier": ident, **r})
            if r.get("ok"):
                ok_count += 1
            files_deleted += int(r.get("deleted") or 0)
            files_failed += int(r.get("failed") or 0)
        summary = {
            "ok": ok_count == len(identifiers),
            "processed": len(identifiers),
            "succeeded": ok_count,
            "files_deleted": files_deleted,
            "files_failed": files_failed,
            "results": results,
            "channel": channel_id,
        }
        # Surface only the relevant summary keys so each operation's
        # response shape matches what the caller would expect.
        # ('succeeded' for hide; 'files_deleted'+'files_failed' for purge)
        # but we keep all keys present for diagnostic friendliness.
        self._send_json(summary)

    # ----- Dashboard: Config tab -----

    def _config_get(self):
        """GET /api/config — scrubbed config view.

        Never returns secret values. Env keys are reported as presence-only
        booleans. Identity reports fingerprint + public key (both safe to
        expose); private key is never returned.
        """
        if not _check_auth(self): return
        from mememage import signing
        config = _get_server_config()

        # Identity
        identity = {
            "name": signing.get_creator_name(),
            "fingerprint": None,
            "public_key": None,
            "has_private_key": signing.PRIVATE_KEY_PATH.exists(),
            "has_revocation_cert": signing.REVOCATION_PATH.exists(),
            "signing_available": signing.is_signing_available(),
        }
        if identity["has_private_key"]:
            try:
                identity["fingerprint"] = signing.get_fingerprint()
                if signing.PUBLIC_KEY_PATH.exists():
                    identity["public_key"] = signing.PUBLIC_KEY_PATH.read_text(encoding="utf-8").strip()
            except Exception:
                pass

        # Env presence (no values). The list is global keys + every
        # ``env_var`` declared by every registered channel type. New
        # channel plugins surface their secrets here automatically.
        env_keys = list(_dashboard_env_keys())
        env_path = Path(__file__).resolve().parent.parent / ".env"
        env_file = {}
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                env_file[k.strip()] = v.strip()
        env_presence = {}
        for k in env_keys:
            v = os.environ.get(k) or env_file.get(k) or ""
            env_presence[k] = bool(v)
        # Owner labels — "Internet Archive" / "Zenodo" / "global" so
        # the Credentials section can show which destination uses each
        # secret. Same order as env_keys.
        env_owners = dict(_dashboard_env_meta())

        # Server config (no secrets — domain/cert/key paths only, no key bytes).
        # Webhooks include headers when the response is going to the
        # dashboard so the editor can show/preserve auth headers across
        # round-trips. They're still not "secret" in the API-key sense
        # (they're Discord bot tokens or similar), but treat them with
        # the same care as the cert paths.
        webhooks = config.get("webhooks", []) or []
        # The "domain" in server.json is what the USER explicitly set;
        # when empty, the running server still has a resolved host
        # (MEMEMAGE_SELF_HOST — first entry of the comma-separated list
        # the startup seeder computed). Surface both so the dashboard
        # can show what the server is actually using even when the
        # user hasn't picked a value yet.
        resolved_domain = ""
        self_host_env = (os.environ.get("MEMEMAGE_SELF_HOST") or "").strip()
        if self_host_env:
            resolved_domain = self_host_env.split(",")[0]
        server_view = {
            "domain": config.get("domain"),
            "domain_resolved": resolved_domain,
            "cert": config.get("cert"),
            "key": config.get("key"),
            "port": config.get("port") or 8443,
            "catalog_limit": _catalog_limit(),
            "webhooks_count": len(webhooks),
            "webhooks": [
                # Real URLs + header values — past the MINT_API_TOKEN
                # gate the caller already has full server access, so
                # the prior masking was courtesy, not a defense. The
                # dashboard handles its own display-side masking
                # (type=password + eyeball) for secret-bearing headers.
                # Save-side _resolve_masked_* helpers stay in place to
                # handle older dashboard payloads that still echo "***".
                {
                    "url": w.get("url") or "",
                    "events": w.get("events", []),
                    "headers": dict(w.get("headers") or {}),
                    "template": w.get("template", "") or "",
                    "attach_files": bool(w.get("attach_files")),
                    # Notifier adapter: explicit override, else "" (auto-detect
                    # from URL on send). Slack file-upload config rides along —
                    # bot token returned real like the Authorization header
                    # above (dashboard masks display-side).
                    "kind": w.get("kind", "") or "",
                    "slack_bot_token": w.get("slack_bot_token", "") or "",
                    "slack_channel": w.get("slack_channel", "") or "",
                    "telegram_bot_token": w.get("telegram_bot_token", "") or "",
                    "telegram_chat_id": w.get("telegram_chat_id", "") or "",
                }
                for w in webhooks
            ],
        }

        # Easter egg lives per-chain now (chain.json) — no longer a
        # global ~/.mememage setting, so it's not part of /api/config.

        self._send_json({
            "identity": identity,
            "server": server_view,
            "env": env_presence,
            "env_owners": env_owners,
        })

    def _doctor(self):
        """GET /api/doctor — deployment preflight checklist."""
        if not _check_auth(self): return
        try:
            self._send_json(_run_doctor())
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _config_set_creator(self):
        """POST /api/config/creator — update ~/.mememage/creator.txt.

        Body: {"name": "Display Name"}
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        name = (data.get("name") or "").strip()
        if not name:
            self._send_json({"error": "name required"}, 400)
            return
        if len(name) > 200:
            self._send_json({"error": "name too long (max 200)"}, 400)
            return
        from mememage.signing import CREATOR_PATH
        CREATOR_PATH.parent.mkdir(parents=True, exist_ok=True)
        CREATOR_PATH.write_text(name)
        self._send_json({"ok": True, "name": name})

    def _config_set_server(self):
        """POST /api/config/server — update domain / cert / key paths in
        ``~/.mememage/server.json``.

        Body: ``{"domain": "...", "cert": "/path/...", "key": "/path/..."}``

        Each field is optional. Empty string clears the field (server
        falls back to auto-detection at startup — see
        ``mememage.__main__:cmd_serve``). Path existence isn't enforced
        on save (the user may stage paths for files they're about to
        create), but absolute paths are normalized via ``Path``.

        Preserves all other ``server.json`` keys (webhooks etc.) and
        invalidates ``_server_config`` cache so subsequent reads pick
        up changes. The actual TLS socket binds at server startup — a
        restart is still required for cert/key changes to take effect,
        and the response includes that hint.
        """
        global _server_config
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        allowed = {"domain", "cert", "key"}
        # `port` + `catalog_limit` are ints, handled separately. Port needs a
        # restart to rebind; catalog_limit takes effect live (the reaper reads
        # it on the next request).
        has_port = "port" in data
        has_catalog = "catalog_limit" in data
        if not any(k in data for k in allowed) and not has_port and not has_catalog:
            self._send_json({"error": "Provide at least one of: domain, cert, key, port, catalog_limit"}, 400)
            return
        for k in allowed:
            if k in data and data[k] is not None and not isinstance(data[k], str):
                self._send_json({"error": f"{k} must be a string"}, 400)
                return
        new_port = None
        if has_port:
            try:
                new_port = int(data["port"])
            except (TypeError, ValueError):
                self._send_json({"error": "port must be an integer"}, 400)
                return
            if not (1 <= new_port <= 65535):
                self._send_json({"error": "port must be between 1 and 65535"}, 400)
                return
        new_catalog = None
        if has_catalog:
            try:
                new_catalog = int(data["catalog_limit"])
            except (TypeError, ValueError):
                self._send_json({"error": "catalog_limit must be an integer"}, 400)
                return
            if new_catalog < 0:
                self._send_json({"error": "catalog_limit must be 0 or greater (0 = unlimited)"}, 400)
                return

        SERVER_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        on_disk = {}
        if SERVER_CONFIG_FILE.exists():
            try:
                on_disk = json.loads(SERVER_CONFIG_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                on_disk = {}
        restart_needed = False
        for k in allowed:
            if k not in data:
                continue
            new_val = (data[k] or "").strip()
            old_val = on_disk.get(k, "")
            if new_val != old_val:
                if k in ("cert", "key"):
                    restart_needed = True
                if new_val:
                    on_disk[k] = new_val
                elif k in on_disk:
                    del on_disk[k]
        if has_port and on_disk.get("port") != new_port:
            on_disk["port"] = new_port
            restart_needed = True
        catalog_changed = has_catalog and on_disk.get("catalog_limit") != new_catalog
        if has_catalog:
            on_disk["catalog_limit"] = new_catalog  # live — no restart
        SERVER_CONFIG_FILE.write_text(
            json.dumps(on_disk, indent=2), encoding="utf-8"
        )
        try:
            os.chmod(str(SERVER_CONFIG_FILE), 0o600)  # webhook tokens / TLS paths: owner-only
        except OSError:
            pass
        _server_config = None
        # Apply a tightened catalog limit immediately so the wall reflects the
        # new cap right away instead of waiting for the next mint's reaper pass.
        if catalog_changed:
            _cleanup_expired()
        self._send_json({
            "ok": True,
            "restart_needed": restart_needed,
        })

    def _config_set_env(self):
        """POST /api/config/env — set / clear values in the project root
        ``.env`` file.

        Body: ``{"KEY_NAME": "value", ...}``. Each key is set to the
        given value; empty-string value removes the key entirely.
        Other lines (other keys, comments, blank lines) are preserved
        verbatim so the user can keep their own annotations.

        Only an allow-list of known keys is writable from the dashboard
        to prevent the API from being used to inject arbitrary
        environment via .env. Adding new keys requires editing the
        allow-list here.
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        ALLOWED_ENV_KEYS = set(_dashboard_env_keys())
        for k, v in data.items():
            if k not in ALLOWED_ENV_KEYS:
                self._send_json({"error": f"env key {k!r} is not on the dashboard allow-list"}, 400)
                return
            if v is not None and not isinstance(v, str):
                self._send_json({"error": f"{k} value must be string"}, 400)
                return
            # Reject newlines so a malicious value can't inject a second
            # KEY=VALUE line into .env.
            if v and ("\n" in v or "\r" in v):
                self._send_json({"error": f"{k} value must not contain newlines"}, 400)
                return

        env_path = Path(__file__).resolve().parent.parent / ".env"
        # Read existing .env (or empty if missing) and walk line-by-line
        # so comments, blanks, and other vars are preserved as-is.
        lines = []
        if env_path.exists():
            lines = env_path.read_text(encoding="utf-8").splitlines()
        new_lines = []
        seen = set()
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                new_lines.append(line)
                continue
            k, _, _ = stripped.partition("=")
            k = k.strip()
            if k in data:
                seen.add(k)
                v = data[k]
                if v == "" or v is None:
                    # Empty value → drop the line entirely.
                    continue
                new_lines.append(f"{k}={v}")
            else:
                new_lines.append(line)
        # Append new keys that weren't already in the file.
        for k, v in data.items():
            if k in seen:
                continue
            if v == "" or v is None:
                continue
            new_lines.append(f"{k}={v}")

        env_path.write_text(
            "\n".join(new_lines) + ("\n" if new_lines else ""),
            encoding="utf-8",
        )
        # Mirror writes into the in-process environment so the running
        # server picks up the new values for any subsequent ops that
        # consult os.environ (IA uploads, etc.). Future-load via
        # _load_dotenv() still works for fields we touched.
        for k, v in data.items():
            if v == "" or v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

        self._send_json({"ok": True, "updated": sorted(data.keys())})

    def _config_token_generate(self):
        """POST /api/config/token/generate — return a fresh word-phrase
        token without writing it. The dashboard pre-fills the API token
        input with the response so the user can review before committing
        through the existing /api/config/env path.

        Body (optional): ``{"words": 12}``. Default 12 = ~108 bits of
        entropy from the 512-word list.

        Doesn't mutate state — explicit "Update" still required to
        persist. That keeps the regenerate flow from kicking the user
        out of their own session mid-edit.
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            data = {}
        words = int(data.get("words") or 12)
        words = max(4, min(32, words))
        from mememage.tokens import generate_word_token
        token = generate_word_token(words)
        self._send_json({"token": token, "words": words})

    def _config_set_webhooks(self):
        """POST /api/config/webhooks — replace the webhooks list in
        ``~/.mememage/server.json``.

        Body: ``{"webhooks": [{"url": "...", "events": ["conceived",
        "ready"], "headers": {...}}]}``

        Each webhook entry:
          - ``url``       (required) — must be http:// or https://
          - ``events``    (optional) — list of event names; empty/missing
                          means "all events" (the firing loop treats it
                          as ``["conceived", "ready"]``)
          - ``headers``   (optional) — dict of custom request headers
                          (e.g. Discord bot ``Authorization``)

        Preserves all other server.json keys (domain, cert, key, …) and
        invalidates the in-memory cache so the next ``_fire_webhooks``
        call picks up the new list without requiring a server restart.
        """
        global _server_config
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        hooks_in = data.get("webhooks")
        if not isinstance(hooks_in, list):
            self._send_json({"error": "webhooks must be a list"}, 400)
            return
        # Read existing webhooks from disk so masked URLs and masked
        # secret headers can be restored to their original values.
        # Sending the displayed mask back unchanged is a no-op edit,
        # not a token reset.
        existing_urls = []
        existing_by_url_prefix = {}  # url_prefix -> dict (the full existing entry)
        if SERVER_CONFIG_FILE.exists():
            try:
                existing = json.loads(SERVER_CONFIG_FILE.read_text(encoding="utf-8"))
                from urllib.parse import urlsplit, urlunsplit
                for w in (existing.get("webhooks") or []):
                    u = (w.get("url") or "").strip()
                    if u:
                        existing_urls.append(u)
                        try:
                            ep = urlsplit(u)
                            prefix_path = "/".join((ep.path or "").split("/")[:-1])
                            prefix = urlunsplit(
                                (ep.scheme, ep.netloc, prefix_path, "", "")
                            )
                            existing_by_url_prefix[prefix] = w
                        except Exception:
                            pass
            except (json.JSONDecodeError, OSError):
                pass
        cleaned = []
        for i, w in enumerate(hooks_in):
            if not isinstance(w, dict):
                self._send_json({"error": f"webhook[{i}] must be an object"}, 400)
                return
            url = (w.get("url") or "").strip()
            url = _resolve_masked_webhook_url(url, existing_urls)
            if not url.startswith(("http://", "https://")):
                self._send_json({"error": f"webhook[{i}].url must be http(s)://"}, 400)
                return
            events = w.get("events") or []
            if not isinstance(events, list) or not all(isinstance(e, str) for e in events):
                self._send_json({"error": f"webhook[{i}].events must be a list of strings"}, 400)
                return
            headers = w.get("headers") or {}
            if not isinstance(headers, dict):
                self._send_json({"error": f"webhook[{i}].headers must be an object"}, 400)
                return
            # Restore any masked header values by matching the URL
            # prefix against the on-disk webhook. New webhooks (no
            # match) keep their incoming values verbatim — caller
            # must supply real secrets on first add.
            try:
                from urllib.parse import urlsplit, urlunsplit
                ep = urlsplit(url)
                prefix_path = "/".join((ep.path or "").split("/")[:-1])
                url_prefix = urlunsplit((ep.scheme, ep.netloc, prefix_path, "", ""))
            except Exception:
                url_prefix = ""
            existing_entry = existing_by_url_prefix.get(url_prefix) or {}
            headers = _resolve_masked_webhook_headers(
                headers, existing_entry.get("headers") or {}
            )
            template = w.get("template", "")
            if template is None:
                template = ""
            if not isinstance(template, str):
                self._send_json({"error": f"webhook[{i}].template must be a string"}, 400)
                return
            template = template.strip()
            if len(template) > 8000:
                self._send_json({"error": f"webhook[{i}].template too long (max 8000)"}, 400)
                return
            attach_files = bool(w.get("attach_files"))
            # Notifier adapter override + Slack file-upload config. kind is
            # validated against the registry (empty = auto-detect). The bot
            # token is a secret but round-trips like the header secrets above
            # (GET returns it real, dashboard masks display-side) — if the
            # caller sends the mask sentinel back, restore from the prior
            # on-disk entry so a no-op edit doesn't wipe the token.
            kind = (w.get("kind") or "").strip().lower()
            if kind:
                from mememage import notifiers as _ntf
                _ntf._ensure_plugins_loaded()
                if kind not in _ntf._REGISTRY:
                    self._send_json(
                        {"error": f"webhook[{i}].kind '{kind}' is not a known "
                                  f"notifier type"}, 400)
                    return
            slack_bot_token = (w.get("slack_bot_token") or "").strip()
            if slack_bot_token == _WEBHOOK_MASK:
                slack_bot_token = (existing_entry.get("slack_bot_token") or "").strip()
            slack_channel = (w.get("slack_channel") or "").strip()
            telegram_bot_token = (w.get("telegram_bot_token") or "").strip()
            if telegram_bot_token == _WEBHOOK_MASK:
                telegram_bot_token = (existing_entry.get("telegram_bot_token") or "").strip()
            telegram_chat_id = (w.get("telegram_chat_id") or "").strip()
            entry = {"url": url}
            if events: entry["events"] = events
            if headers: entry["headers"] = headers
            if template: entry["template"] = template
            if attach_files: entry["attach_files"] = True
            if kind: entry["kind"] = kind
            if slack_bot_token: entry["slack_bot_token"] = slack_bot_token
            if slack_channel: entry["slack_channel"] = slack_channel
            if telegram_bot_token: entry["telegram_bot_token"] = telegram_bot_token
            if telegram_chat_id: entry["telegram_chat_id"] = telegram_chat_id
            cleaned.append(entry)

        # Merge into existing server.json. Read fresh from disk to avoid
        # clobbering any keys that aren't reflected in the cached view.
        SERVER_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        on_disk = {}
        if SERVER_CONFIG_FILE.exists():
            try:
                on_disk = json.loads(SERVER_CONFIG_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                on_disk = {}
        on_disk["webhooks"] = cleaned
        SERVER_CONFIG_FILE.write_text(
            json.dumps(on_disk, indent=2), encoding="utf-8"
        )
        try:
            os.chmod(str(SERVER_CONFIG_FILE), 0o600)  # webhook tokens / TLS paths: owner-only
        except OSError:
            pass
        # Invalidate cache so the next _fire_webhooks call sees the
        # update without a server restart.
        _server_config = None
        self._send_json({"ok": True, "webhooks_count": len(cleaned)})

    # ----- Dashboard: native OS file picker (for entry source picker) -----

    def _fs_pick_available(self):
        """GET /api/fs/pick/available — does the server have a working
        native picker?

        Returns ``{available: bool, reason: str?}``. Used by the
        dashboard to decide whether to show the "Browse…" button (the
        picker requires a desktop session — zenity / kdialog on Linux,
        osascript on macOS — and headless VPS deployments have neither
        the tool installed nor a DISPLAY to render against). When
        unavailable, the dashboard foregrounds manual-path entry.
        """
        if not _check_auth(self): return
        import platform
        import shutil as _shutil
        system = platform.system()
        ok = False
        reason = ""
        if system == "Darwin":
            ok = _shutil.which("osascript") is not None
            if not ok:
                reason = "osascript not found"
        elif system == "Linux":
            has_tool = bool(
                _shutil.which("zenity") or _shutil.which("kdialog")
            )
            has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
            ok = has_tool and has_display
            if not has_tool:
                reason = "no zenity/kdialog installed"
            elif not has_display:
                reason = "no DISPLAY (headless server)"
        else:
            reason = f"unsupported platform {system}"
        self._send_json({"available": ok, "reason": reason})

    def _fs_pick(self):
        """POST /api/fs/pick — pop the native OS file picker, return the path.

        Body (all optional): {
          "type":     "file" | "folder"  (default: "file")
          "init_dir": "/absolute/dir"     (optional starting directory)
        }

        Works because the mint server runs on the user's own machine, so
        ``osascript`` / ``zenity`` / etc. display the OS-native dialog on
        the user's screen with their permissions.

        Returns {"path": "..."} on success, {"cancelled": true} if the
        user dismissed the dialog, or an {"error": "..."} on failure.

        Path normalization: if the picked path lives inside the project
        root or ``~/.mememage/``, we return a relative or ``~``-prefixed
        path so the chain config stays portable. Otherwise we return the
        absolute path.

        NOTE: this endpoint blocks until the user picks or cancels, but the
        server is threaded (ThreadingHTTPServer), so it only ties up its own
        worker thread — other requests are unaffected. The dashboard is the
        only client and the user is actively interacting with the picker.
        """
        if not _check_auth(self): return
        import platform
        import subprocess

        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            data = {}
        pick_type = data.get("type", "file")
        if pick_type not in ("file", "folder"):
            self._send_json({"error": "type must be 'file' or 'folder'"}, 400)
            return
        init_dir = data.get("init_dir") or ""
        # Expand ~ and resolve to an absolute path. osascript's
        # ``POSIX file`` (and zenity/kdialog) need a literal filesystem
        # path — a bare ``~`` is not interpreted by the shell here
        # because we shell out via subprocess args, not a shell string.
        # If the directory doesn't exist (e.g. ~/.mememage/certs before
        # the user generates any certs), drop the init_dir entirely so
        # the picker starts at the OS default instead of failing.
        if init_dir:
            try:
                expanded = Path(init_dir).expanduser()
                if expanded.is_dir():
                    init_dir = str(expanded)
                else:
                    init_dir = ""
            except (OSError, ValueError):
                init_dir = ""

        system = platform.system()
        picked = None
        cancelled = False
        err = None

        if system == "Darwin":
            # AppleScript via osascript.
            if pick_type == "folder":
                script = 'POSIX path of (choose folder'
            else:
                script = 'POSIX path of (choose file'
            if init_dir:
                # Escape any embedded quotes in the path.
                safe = init_dir.replace('"', '\\"')
                script += f' default location POSIX file "{safe}"'
            script += ')'
            try:
                result = subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True, text=True, timeout=900,
                )
                if result.returncode == 0:
                    picked = result.stdout.strip()
                elif "User canceled" in result.stderr or result.returncode == 1:
                    cancelled = True
                else:
                    err = result.stderr.strip() or f"osascript exit {result.returncode}"
            except subprocess.TimeoutExpired:
                err = "Picker timed out (15 minutes)."
            except FileNotFoundError:
                err = "osascript not found — is this macOS?"
        elif system == "Linux":
            # zenity first, kdialog as a fallback.
            for cmd_prefix in (["zenity", "--file-selection"], ["kdialog", "--getopenfilename"]):
                cmd = list(cmd_prefix)
                if pick_type == "folder":
                    if cmd_prefix[0] == "zenity":
                        cmd.append("--directory")
                    else:
                        cmd[1] = "--getexistingdirectory"
                if init_dir:
                    if cmd_prefix[0] == "zenity":
                        cmd += ["--filename", init_dir + "/"]
                    else:
                        cmd.append(init_dir)
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
                    if result.returncode == 0:
                        picked = result.stdout.strip()
                    else:
                        cancelled = True
                    break
                except FileNotFoundError:
                    continue
            else:
                err = "Neither zenity nor kdialog found. Install one, or type the path manually."
        else:
            err = (f"Native picker not implemented on {system}. "
                   f"Type the path manually for now.")

        if err is not None:
            self._send_json({"error": err}, 500)
            return
        if cancelled or not picked:
            self._send_json({"cancelled": True})
            return

        # Normalize to the chain-config path conventions.
        project_root = Path(__file__).resolve().parent.parent.resolve()
        home_mememage = Path("~/.mememage").expanduser().resolve()
        try:
            abs_picked = Path(picked).resolve()
        except Exception:
            self._send_json({"path": picked})
            return
        try:
            rel = abs_picked.relative_to(project_root)
            self._send_json({"path": str(rel)})
            return
        except ValueError:
            pass
        try:
            rel = abs_picked.relative_to(home_mememage)
            self._send_json({"path": "~/.mememage/" + str(rel) if str(rel) != "." else "~/.mememage/"})
            return
        except ValueError:
            pass
        # Absolute path outside known roots — still valid, just not portable.
        self._send_json({"path": str(abs_picked)})

    def _identity_install_signing(self):
        """POST /api/identity/install-signing — install the optional
        `cryptography` library into the running server's environment so signing
        works, with no terminal access required.

        cryptography ships manylinux/macOS/Windows wheels, so this is a download
        (no compiler). signing.py imports it lazily and Python doesn't cache
        failed imports, so it becomes usable immediately — no restart. Returns
        {ok, available, message|error, log} (always HTTP 200 so the dashboard
        can show the structured result)."""
        if not _check_auth(self): return
        from mememage import signing
        if signing.is_signing_available():
            self._send_json({"ok": True, "available": True,
                             "message": "Signing support is already installed."})
            return
        import subprocess
        import sys as _sys
        try:
            proc = subprocess.run(
                [_sys.executable, "-m", "pip", "install", "cryptography>=41.0"],
                capture_output=True, text=True, timeout=300,
            )
        except subprocess.TimeoutExpired:
            self._send_json({"ok": False, "available": False,
                             "error": "pip install timed out (5 min)."})
            return
        except Exception as e:
            self._send_json({"ok": False, "available": False,
                             "error": f"Could not run pip: {e}"})
            return
        tail = (proc.stdout + "\n" + proc.stderr).strip()[-1500:]
        available = signing.is_signing_available()  # lazy re-import, no restart
        if proc.returncode == 0 and available:
            self._send_json({"ok": True, "available": True,
                             "message": "Signing support installed."})
            return
        # Most likely failure on a non-venv host: PEP 668 externally-managed.
        hint = ""
        if "externally-managed-environment" in tail:
            hint = (" The server's Python is externally managed — run the mint "
                    "server inside a virtualenv (the recommended setup) so it "
                    "can install its own optional dependencies.")
        self._send_json({"ok": False, "available": available,
                         "error": f"pip install failed (exit {proc.returncode}).{hint}",
                         "log": tail})

    def _identity_keygen(self):
        """POST /api/identity/keygen — generate Ed25519 key pair.

        Body: {"name": "Display Name", "force": false}

        Force is required if a key already exists. force=true ARCHIVES the
        existing key into ~/.mememage/keychain/ before generating; old
        signed records remain verifiable against the archived public key.
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        name = (data.get("name") or "").strip() or None
        force = bool(data.get("force"))

        from mememage import signing
        if not signing.is_signing_available():
            self._send_json({
                "error": "Signing requires the cryptography library. "
                         "Install with: pip install mememage[sign]"
            }, 400)
            return
        if signing.PRIVATE_KEY_PATH.exists() and not force:
            self._send_json({
                "error": "Key already exists. Pass force=true to replace "
                         "(old records won't verify under the new key)."
            }, 409)
            return
        try:
            fingerprint, public_hex, _path = signing.keygen(force=force, name=name)
            self._send_json({
                "ok": True,
                "fingerprint": fingerprint,
                "public_key": public_hex,
            })
        except Exception as e:
            log.exception("keygen failed")
            self._send_json({"error": str(e)}, 500)

    def _identity_rotate(self):
        """POST /api/identity/rotate — generate a new Ed25519 key, sign a
        succession record with the OLD key, archive the old key, and
        upload the succession record to the Internet Archive.

        Body: ``{"name": "Display Name", "confirm": "ROTATE"}``

        The ``confirm`` field must equal the literal string ``ROTATE``.
        Without it the request is rejected — rotation is irreversible
        for the records signed under the old key (they still verify,
        but the keychain trail is permanent on IA).

        IA upload is best-effort: a failure to publish the succession
        does NOT roll back the local rotation. The dashboard surfaces
        the upload error so the user can retry manually with the CLI
        if their network was flaky.
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        if (data.get("confirm") or "").strip() != "ROTATE":
            self._send_json({"error": "Type 'ROTATE' in the confirm field to proceed."}, 400)
            return
        name = (data.get("name") or "").strip() or None

        from mememage import signing
        if not signing.is_signing_available():
            self._send_json({"error": "Signing unavailable (cryptography not installed)."}, 400)
            return
        if not signing.PRIVATE_KEY_PATH.exists():
            self._send_json({"error": "No existing key to rotate from."}, 400)
            return
        try:
            new_fp, succession, chain_id = signing.rotate(name=name)
        except Exception as e:
            log.exception("rotate failed")
            self._send_json({"error": "Rotate failed: " + str(e)}, 500)
            return

        # Publish the succession record. Failures here leave the local
        # rotation intact — return the new fingerprint either way so the
        # user can recover by retrying the upload via CLI.
        upload_error = None
        try:
            signing.upload_keychain_record(succession, chain_id, "succession.json")
        except Exception as e:
            log.warning("Succession upload failed: %s", e)
            upload_error = str(e)

        self._send_json({
            "ok": True,
            "fingerprint": new_fp,
            "keychain_id": chain_id,
            "succession_uploaded": upload_error is None,
            "upload_error": upload_error,
        })

    # ----- Dashboard: Profile management -----

    def _profiles_list(self):
        """GET /api/profiles — every profile on disk + active marker
        + alias relationships from the local peer keychain.

        For each profile, walks ``~/.mememage/received/keychain/
        mememage-keychain-<fp>/`` for ``alias-<otherFp>.json`` files
        and reports each as a sibling. If the named other fingerprint
        matches another profile on this host, the alias links the two
        rows directly (``other_id`` populated). Bidirectional flag is
        set when the reverse alias file exists on the peer keychain
        side too.

        IA-only chains that don't have aliases in the peer keychain
        won't be discovered here — that's the same limitation as the
        rest of the dashboard's peer-keychain awareness; a future pass
        could probe IA's metadata API server-side, but most flows
        publish to the peer alongside IA so this covers the common
        case.
        """
        if not _check_auth(self): return
        from mememage import profiles
        profile_list = profiles.list_profiles()
        active = profiles.active_id()

        # Map fingerprint-clean (no colons) → profile id so cross-
        # references in alias records resolve to local profile names.
        fp_to_id = {}
        for p in profile_list:
            fp = p.get("fingerprint")
            if fp:
                fp_to_id[fp.replace(":", "")] = p["id"]

        keychain_root = _chains.keychain_dir()
        alias_pat = re.compile(r"^alias-([0-9a-f]{16})\.json$")
        for p in profile_list:
            p["aliases"] = []
            fp = p.get("fingerprint")
            if not fp:
                continue
            fp_clean = fp.replace(":", "")
            chain_dir = keychain_root / f"mememage-keychain-{fp_clean}"
            if not chain_dir.is_dir():
                continue
            for f in sorted(chain_dir.iterdir()):
                m = alias_pat.match(f.name)
                if not m:
                    continue
                other_fp_clean = m.group(1)
                try:
                    rec = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    continue
                # Reverse alias: does the OTHER side's keychain on
                # this peer hold the matching alias-<self>.json?
                reverse_dir = keychain_root / f"mememage-keychain-{other_fp_clean}"
                reverse_file = reverse_dir / f"alias-{fp_clean}.json"
                p["aliases"].append({
                    "other_fingerprint_clean": other_fp_clean,
                    "other_id": fp_to_id.get(other_fp_clean),
                    "creator_name": rec.get("creator_name") or "",
                    "timestamp": rec.get("timestamp") or "",
                    "bidirectional": reverse_file.is_file(),
                })

        self._send_json({"active": active, "profiles": profile_list})

    def _profiles_new(self):
        """POST /api/profiles — generate a fresh keypair under a new id.

        Body: ``{"id": "vps-prod", "name": "Production VPS"}``

        Side effects: writes private.key + public.key + creator.txt +
        revocation.cert under ``~/.mememage/profiles/<id>/``, AND
        switches the active profile to the new one (so the next mint
        signs with this key — same behavior as ``profiles.create()``
        and the CLI).
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        pid = (data.get("id") or "").strip()
        name = (data.get("name") or "").strip() or None
        from mememage import profiles
        try:
            info = profiles.create(pid, name=name)
        except FileExistsError as e:
            self._send_json({"error": str(e)}, 409)
            return
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
            return
        except RuntimeError as e:
            self._send_json({"error": str(e)}, 500)
            return
        self._send_json({"ok": True, "profile": info})

    def _profiles_import(self):
        """POST /api/profiles/import — import an existing Ed25519
        private key (PEM / OpenSSH) as a new profile.

        Body: ``{"id": "...", "name": "...", "key_path": "/abs/path"}``

        The file is read on the server side (the mint server runs on
        the user's own machine, so reading from their disk is
        legitimate). Useful in combination with ``/api/fs/pick`` so the
        dashboard can offer "Import key…" → file picker → import.

        Does NOT switch the active profile — importing a key is a
        staging action; the user explicitly switches when they're
        ready to start signing with it.
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        pid = (data.get("id") or "").strip()
        name = (data.get("name") or "").strip() or None
        key_path = (data.get("key_path") or "").strip()
        if not key_path:
            self._send_json({"error": "key_path required"}, 400)
            return
        # Expand ~ here too — the path may come back from the picker
        # with a ~/.ssh prefix.
        path = Path(key_path).expanduser()
        if not path.is_file():
            self._send_json({"error": f"Not a file: {path}"}, 400)
            return
        try:
            pem_bytes = path.read_bytes()
        except OSError as e:
            self._send_json({"error": f"Could not read key file: {e}"}, 500)
            return
        from mememage import profiles
        try:
            info = profiles.import_key(pid, name, pem_bytes)
        except FileExistsError as e:
            self._send_json({"error": str(e)}, 409)
            return
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
            return
        except RuntimeError as e:
            self._send_json({"error": str(e)}, 500)
            return
        self._send_json({"ok": True, "profile": info})

    def _profiles_active(self):
        """POST /api/profiles/active — switch the active profile.

        Body: ``{"id": "vps-prod"}``
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        pid = (data.get("id") or "").strip()
        from mememage import profiles
        try:
            profiles.set_active(pid)
        except FileNotFoundError as e:
            self._send_json({"error": str(e)}, 404)
            return
        self._send_json({"ok": True, "active": pid})

    def _profiles_alias(self):
        """POST /api/profiles/alias — active profile signs an alias
        record naming ``other_id`` and publishes it to IA.

        Body: ``{"other_id": "vps-prod", "confirm": "ALIAS"}``

        The active profile is the SIGNER; the other profile is the
        target. To establish a bidirectional alias (the strongest
        verifier signal), the user runs this from both profiles' active
        contexts. IA upload is best-effort — a network failure leaves
        the local state untouched and the dashboard surfaces the error
        for retry.
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        if (data.get("confirm") or "").strip() != "ALIAS":
            self._send_json({"error": "Type 'ALIAS' in the confirm field to proceed."}, 400)
            return
        other_id = (data.get("other_id") or "").strip()
        from mememage import profiles, signing
        try:
            record = profiles.sign_alias(other_id)
        except (FileNotFoundError, ValueError) as e:
            self._send_json({"error": str(e)}, 400)
            return
        except RuntimeError as e:
            self._send_json({"error": str(e)}, 500)
            return
        chain_id = signing.keychain_identifier(record["signer_fingerprint"])
        clean = record["alias_fingerprint"].replace(":", "")
        filename = f"alias-{clean}.json"
        upload_error = None
        try:
            signing.upload_keychain_record(record, chain_id, filename)
        except Exception as e:
            log.warning("Alias upload failed: %s", e)
            upload_error = str(e)
        self._send_json({
            "ok": True,
            "record": record,
            "keychain_id": chain_id,
            "filename": filename,
            "uploaded": upload_error is None,
            "upload_error": upload_error,
        })

    def _profiles_pair_inbound(self):
        """POST /api/profiles/pair — inbound side of a pairing call.

        Body: ``{profile_id, public_key, creator_name}`` — the caller's
        identity. Side effects on this host:
          1. Save the caller's pubkey as a public-only peer profile
          2. Sign an alias from THIS host's active profile to that peer
          3. Publish the alias via channels (so both peers + IA mirror)

        Response: this host's active-profile identity, so the caller
        can do the same on its side and the link becomes bidirectional
        in one round-trip.
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        peer_id = (data.get("profile_id") or "").strip()
        peer_pubkey = (data.get("public_key") or "").strip()
        peer_name = (data.get("creator_name") or "").strip() or None
        if not peer_id or not peer_pubkey:
            self._send_json({"error": "profile_id and public_key required"}, 400)
            return

        from mememage import profiles, signing
        # If we already have a profile with that id, accept idempotently
        # if the pubkey matches; otherwise refuse — we never overwrite.
        existing = profiles.profile_info(peer_id) if (profiles.PROFILES_DIR / peer_id).exists() else None
        if existing and existing.get("public_key") and existing["public_key"] != peer_pubkey.lower():
            self._send_json({
                "error": f"Profile id {peer_id!r} already exists with a different key"
            }, 409)
            return
        if not existing:
            try:
                profiles.add_peer(peer_id, peer_pubkey, peer_name)
            except ValueError as e:
                self._send_json({"error": str(e)}, 400)
                return

        # Sign + publish our active profile's alias to the peer.
        try:
            record = profiles.sign_alias(peer_id)
            chain_id = "mememage-keychain-" + record["signer_fingerprint"].replace(":", "")
            filename = "alias-" + record["alias_fingerprint"].replace(":", "") + ".json"
            signing.upload_keychain_record(record, chain_id, filename)
        except Exception as e:
            log.warning("Pair: alias publish failed for %s: %s", peer_id, e)
            self._send_json({"error": f"Alias publish failed: {e}"}, 500)
            return

        # Return our active-profile identity so the caller can save it
        # and sign its own reverse alias.
        active = profiles.profile_info(profiles.active_id())
        self._send_json({
            "ok": True,
            "profile_id": active["id"],
            "public_key": active["public_key"],
            "creator_name": active.get("name") or "",
            "fingerprint": active["fingerprint"],
        })

    def _profiles_pair_call(self):
        """POST /api/profiles/pair-call — outbound side of a pairing
        call. The dashboard's "Pair with another mememage" submit
        target.

        Body: ``{peer_url, peer_token, [peer_id_override]}``.

        Orchestration:
          1. Read local active profile
          2. POST to ``<peer_url>/api/profiles/pair`` with peer_token,
             carrying our identity
          3. Receive peer's identity in the response
          4. Save peer's identity as a local public-only profile
          5. Sign + publish our own alias to the peer

        After both sides complete, each has the other's pubkey on disk
        and a signed alias on its keychain — bidirectional in one click.
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        peer_url = (data.get("peer_url") or "").strip().rstrip("/")
        peer_token = (data.get("peer_token") or "").strip()
        peer_id_override = (data.get("peer_id") or "").strip() or None
        accept_self_signed = bool(data.get("accept_self_signed"))
        if not peer_url:
            self._send_json({"error": "peer_url required"}, 400)
            return

        from mememage import profiles, signing
        active = profiles.profile_info(profiles.active_id())
        if not active.get("fingerprint"):
            self._send_json({"error": "Active profile has no key"}, 400)
            return

        body = json.dumps({
            "profile_id": active["id"],
            "public_key": active["public_key"],
            "creator_name": active.get("name") or "",
        }).encode("utf-8")

        import urllib.request, urllib.error
        req = urllib.request.Request(
            peer_url + "/api/profiles/pair",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {peer_token}" if peer_token else "",
            },
        )
        from mememage import net
        ctx = net.default_https_context()      # certifi roots, not the stale OS store
        if accept_self_signed:
            import ssl as _ssl
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
        try:
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                peer_resp = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            self._send_json({"error": f"Peer rejected (HTTP {e.code}): {err_body[:300]}"}, 502)
            return
        except urllib.error.URLError as e:
            # Network-level failure — usually means the peer is unreachable.
            # NAT-behind-Tailscale and similar setups can't be reached from
            # the public internet; in those cases the user must initiate
            # the pair flow from the NAT-d host's own dashboard (only
            # outbound reach is required).
            reason = str(e.reason) if hasattr(e, 'reason') else str(e)
            self._send_json({
                "error": f"Peer unreachable: {reason}",
                "hint": "If the peer is behind NAT (Tailscale, residential router, etc.), "
                        "the public internet can't reach it. Initiate the pair flow from "
                        "the peer's own dashboard instead — the pairing handshake works "
                        "in one direction; only the initiator needs outbound reach.",
                "network_error": True,
            }, 502)
            return
        except (TimeoutError, OSError) as e:
            self._send_json({
                "error": f"Peer call timed out: {e}",
                "hint": "Same as above — if the peer is behind NAT it can't be reached. "
                        "Initiate pairing from the peer's side instead.",
                "network_error": True,
            }, 502)
            return
        except Exception as e:
            self._send_json({"error": f"Peer call failed: {e}"}, 502)
            return

        peer_pid = peer_id_override or peer_resp.get("profile_id")
        peer_pubkey = peer_resp.get("public_key")
        peer_name = peer_resp.get("creator_name") or None
        if not peer_pid or not peer_pubkey:
            self._send_json({"error": "Peer response missing profile_id/public_key"}, 502)
            return

        # Save peer's identity as a public-only profile locally
        existing = profiles.profile_info(peer_pid) if (profiles.PROFILES_DIR / peer_pid).exists() else None
        if existing and existing.get("public_key") and existing["public_key"] != peer_pubkey.lower():
            self._send_json({
                "error": f"A local profile named {peer_pid!r} already exists with a different key. "
                         f"Pass a different peer_id (or remove the conflicting profile first)."
            }, 409)
            return
        if not existing:
            try:
                profiles.add_peer(peer_pid, peer_pubkey, peer_name)
            except ValueError as e:
                self._send_json({"error": str(e)}, 400)
                return

        # Sign + publish our alias to the peer
        try:
            record = profiles.sign_alias(peer_pid)
            chain_id = "mememage-keychain-" + record["signer_fingerprint"].replace(":", "")
            filename = "alias-" + record["alias_fingerprint"].replace(":", "") + ".json"
            signing.upload_keychain_record(record, chain_id, filename)
        except Exception as e:
            log.warning("Pair-call: local alias publish failed: %s", e)
            self._send_json({
                "error": f"Peer accepted but local alias publish failed: {e}",
                "peer_profile_id": peer_pid,
            }, 500)
            return

        self._send_json({
            "ok": True,
            "peer_profile_id": peer_pid,
            "peer_fingerprint": peer_resp.get("fingerprint"),
            "peer_creator_name": peer_name or "",
        })

    # ----- Config sync (push from one host to a peer) -----
    #
    # Mirrors the pair flow shape: dashboard initiates from the source
    # host, calls the peer's /api/sync/accept, peer applies additively.
    # No private keys, no MINT_API_TOKEN, no channel credentials, no
    # sessions, no records. Three categories the sender can push:
    #
    #   - chains:    chain.json shape minus password (each host gates
    #                its own dark chains via the existing Set Password
    #                flow)
    #   - channels:  channels.json shape minus credentials (env vars
    #                stay host-local)
    #   - webhooks:  full webhook entries (URL + headers + template +
    #                events). Includes Discord/Slack tokens embedded
    #                in URLs/headers — opt-in only with explicit
    #                consent on the sender side.
    #
    # Receiver applies additively: matches by id (chains/channels) or
    # by URL (webhooks). Existing entries are skipped, never updated
    # or merged. Response summarizes created vs. skipped for each
    # category so the sender can see the outcome at a glance.

    def _sync_accept(self):
        """POST /api/sync/accept — inbound side of a config push.

        Body shape::
            {
              "chains":   [{id, name?, visibility?, gps_source?}, ...],
              "channels": [{id, type, name?, enabled?, primary?, config?}, ...],
              "webhooks": [{url, events?, headers?, template?, attach_files?}, ...]
            }

        Every category is optional. Missing/empty arrays mean "don't
        touch this category on the receiver." Applies additively;
        existing rows are skipped (logged in the response summary).
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return

        from mememage import chains as _chains
        from mememage import channels as _ch

        summary = {
            "chains":   {"created": [], "skipped": []},
            "channels": {"created": [], "skipped": []},
            "webhooks": {"created": 0, "skipped": 0},
        }

        # --- Chains ---
        incoming_chains = data.get("chains") or []
        if isinstance(incoming_chains, list):
            existing_ids = {c.get("id") for c in _chains.list_chains() if c.get("id")}
            for entry in incoming_chains:
                if not isinstance(entry, dict): continue
                cid = (entry.get("id") or "").strip()
                if not cid: continue
                if cid in existing_ids:
                    summary["chains"]["skipped"].append(cid)
                    continue
                try:
                    _chains.create(
                        cid,
                        visibility=entry.get("visibility") or "light_energy",
                        name=entry.get("name"),
                    )
                    # gps_source rides separately — apply it now if present.
                    gps = entry.get("gps_source")
                    if gps in ("phone", "machine", "none"):
                        _chains.set_gps_source(cid, gps)
                    # constellation_size rides separately too (clamped in the
                    # setter); apply when the source explicitly set it.
                    csize = entry.get("constellation_size")
                    if isinstance(csize, int):
                        _chains.set_constellation_size(cid, csize)
                    summary["chains"]["created"].append(cid)
                except Exception as e:
                    log.warning("Sync: chain %s create failed: %s", cid, e)
                    summary["chains"]["skipped"].append(cid)

        # --- Channels ---
        incoming_channels = data.get("channels") or []
        if isinstance(incoming_channels, list):
            current = _ch.load_channels()
            existing_ids = {c.id for c in current}
            # Build a fresh wholesale list: existing untouched, new
            # entries appended. credentials stay empty on the new
            # ones (env vars hold the real values per-host).
            new_chans_raw = [
                # Re-serialize existing channels back to channels.json
                # shape so save_raw gets a clean list.
                {
                    "id": c.id, "type": c.TYPE, "name": c.name,
                    "enabled": c.enabled, "primary": c.primary,
                    "credentials": dict(c.credentials or {}),
                    "config": dict(c.config or {}),
                }
                for c in current
            ]
            had_primary = any(c.get("primary") for c in new_chans_raw)
            for entry in incoming_channels:
                if not isinstance(entry, dict): continue
                cid = (entry.get("id") or "").strip()
                ctype = (entry.get("type") or "").strip()
                if not cid or not ctype:
                    continue
                if cid in existing_ids:
                    summary["channels"]["skipped"].append(cid)
                    continue
                # Honor primary only if the receiver doesn't already
                # have one (can't have two primaries). New imports
                # land non-primary by default; user promotes manually.
                want_primary = bool(entry.get("primary"))
                if want_primary and had_primary:
                    want_primary = False
                elif want_primary:
                    had_primary = True
                new_chans_raw.append({
                    "id": cid,
                    "type": ctype,
                    "name": entry.get("name") or cid,
                    "enabled": bool(entry.get("enabled", True)),
                    "primary": want_primary,
                    "credentials": {},  # never accept credentials over the wire
                    "config": dict(entry.get("config") or {}),
                })
                summary["channels"]["created"].append(cid)
            if summary["channels"]["created"]:
                _ch.save_raw({"channels": new_chans_raw})

        # --- Webhooks ---
        incoming_webhooks = data.get("webhooks") or []
        if isinstance(incoming_webhooks, list) and incoming_webhooks:
            cfg = _get_server_config()
            existing_hooks = list(cfg.get("webhooks") or [])
            existing_urls = {(h.get("url") or "").strip() for h in existing_hooks}
            for entry in incoming_webhooks:
                if not isinstance(entry, dict): continue
                url = (entry.get("url") or "").strip()
                if not url or not url.startswith(("http://", "https://")):
                    continue
                if url in existing_urls:
                    summary["webhooks"]["skipped"] += 1
                    continue
                hook = {"url": url}
                events = entry.get("events") or []
                if isinstance(events, list):
                    hook["events"] = [e for e in events if isinstance(e, str)]
                headers = entry.get("headers") or {}
                if isinstance(headers, dict):
                    hook["headers"] = {k: v for k, v in headers.items()
                                       if isinstance(k, str) and isinstance(v, str)}
                template = entry.get("template")
                if isinstance(template, str) and template:
                    hook["template"] = template
                if entry.get("attach_files"):
                    hook["attach_files"] = True
                existing_hooks.append(hook)
                summary["webhooks"]["created"] += 1
            if summary["webhooks"]["created"]:
                # Read fresh from disk + merge to preserve any other
                # server.json keys (cert/key paths, domain).
                SERVER_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
                on_disk = {}
                if SERVER_CONFIG_FILE.exists():
                    try:
                        on_disk = json.loads(SERVER_CONFIG_FILE.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError):
                        on_disk = {}
                on_disk["webhooks"] = existing_hooks
                SERVER_CONFIG_FILE.write_text(
                    json.dumps(on_disk, indent=2), encoding="utf-8"
                )
                try:
                    os.chmod(str(SERVER_CONFIG_FILE), 0o600)  # webhook tokens / TLS paths: owner-only
                except OSError:
                    pass
                global _server_config
                _server_config = None  # invalidate cache

        self._send_json({"ok": True, "summary": summary})

    def _sync_export(self):
        """POST /api/sync/export — return the host's chains + channels
        (+ optional webhooks) as a downloadable JSON file.

        Body (optional): ``{"include": {chains, channels, webhooks}}``.
        Same shape ``_sync_accept`` consumes — so the export file can
        be uploaded directly to a peer's accept endpoint (or re-imported
        on the same host to restore a saved snapshot).

        Returns the payload wrapped in a small envelope so future
        readers can detect the format:

            {
              "mememage_config_export": 1,
              "exported_at": "2026-05-21T...",
              "host": "<self-host>",
              "chains": [...], "channels": [...], "webhooks": [...]
            }

        Auth-gated like every other ``/api/*`` route.
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            data = {}
        include = data.get("include") or {}
        from mememage import chains as _chains
        from mememage import channels as _ch

        payload = {
            "mememage_config_export": 1,
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "host": (os.environ.get("MEMEMAGE_SELF_HOST") or "").split(",")[0],
        }
        if include.get("chains", True):
            payload["chains"] = [
                {
                    "id": c.get("id"),
                    "name": c.get("name"),
                    "visibility": c.get("visibility") or "light_energy",
                    "gps_source": c.get("gps_source"),
                }
                for c in _chains.list_chains()
                if c.get("id")
            ]
        if include.get("channels", True):
            payload["channels"] = [
                {
                    "id": c.id, "type": c.TYPE, "name": c.name,
                    "enabled": c.enabled, "primary": c.primary,
                    "config": dict(c.config or {}),
                }
                for c in _ch.load_channels()
            ]
        if include.get("webhooks", False):
            cfg = _get_server_config()
            payload["webhooks"] = [
                {
                    "url": h.get("url"),
                    "events": h.get("events") or [],
                    "headers": h.get("headers") or {},
                    "template": h.get("template", ""),
                    "attach_files": bool(h.get("attach_files")),
                }
                for h in (cfg.get("webhooks") or [])
                if h.get("url")
            ]
        self._send_json(payload)

    def _sync_call(self):
        """POST /api/sync/call — outbound side of a config push.

        Body: ``{peer_url, peer_token, accept_self_signed?, include}``
        where ``include`` is a dict with boolean keys ``chains``,
        ``channels``, ``webhooks`` (default true / true / false).

        Reads this host's config, strips secrets per category, POSTs
        to ``<peer_url>/api/sync/accept`` with the peer's bearer.
        Returns the peer's summary unchanged so the dashboard can
        render which entries landed vs. were skipped.
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        peer_url = (data.get("peer_url") or "").strip().rstrip("/")
        peer_token = (data.get("peer_token") or "").strip()
        accept_self_signed = bool(data.get("accept_self_signed"))
        include = data.get("include") or {}
        if not peer_url:
            self._send_json({"error": "peer_url required"}, 400)
            return

        from mememage import chains as _chains
        from mememage import channels as _ch

        payload = {}

        if include.get("chains", True):
            payload["chains"] = [
                {
                    "id": c.get("id"),
                    "name": c.get("name"),
                    "visibility": c.get("visibility") or "light_energy",
                    # gps_source ships if explicitly set (so receiver
                    # mirrors the source's GPS contract).
                    "gps_source": c.get("gps_source"),
                    # constellation_size ships if explicitly set so the
                    # receiver mirrors the source's constellation cadence.
                    "constellation_size": c.get("constellation_size"),
                }
                for c in _chains.list_chains()
                if c.get("id")
            ]

        if include.get("channels", True):
            payload["channels"] = [
                {
                    "id": c.id, "type": c.TYPE, "name": c.name,
                    "enabled": c.enabled, "primary": c.primary,
                    "config": dict(c.config or {}),
                    # credentials excluded — env vars stay per-host
                }
                for c in _ch.load_channels()
            ]

        if include.get("webhooks", False):
            cfg = _get_server_config()
            payload["webhooks"] = [
                # Send verbatim — including the embedded bearer in
                # URL / headers. User opted in explicitly.
                {
                    "url": h.get("url"),
                    "events": h.get("events") or [],
                    "headers": h.get("headers") or {},
                    "template": h.get("template", ""),
                    "attach_files": bool(h.get("attach_files")),
                }
                for h in (cfg.get("webhooks") or [])
                if h.get("url")
            ]

        body = json.dumps(payload).encode("utf-8")

        import urllib.request, urllib.error
        req = urllib.request.Request(
            peer_url + "/api/sync/accept",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {peer_token}" if peer_token else "",
            },
        )
        from mememage import net
        ctx = net.default_https_context()      # certifi roots, not the stale OS store
        if accept_self_signed:
            import ssl as _ssl
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE

        try:
            with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
                peer_resp = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            self._send_json({"error": f"Peer rejected (HTTP {e.code}): {err_body[:300]}"}, 502)
            return
        except urllib.error.URLError as e:
            reason = str(e.reason) if hasattr(e, 'reason') else str(e)
            self._send_json({
                "error": f"Peer unreachable: {reason}",
                "hint": "Same NAT/Tailscale caveat as the pair flow — initiate from "
                        "whichever side can reach the other.",
                "network_error": True,
            }, 502)
            return
        except Exception as e:
            self._send_json({"error": f"Peer call failed: {e}"}, 502)
            return

        self._send_json({"ok": True, "peer_summary": peer_resp.get("summary") or {}})

    def _profiles_remove(self):
        """POST /api/profiles/remove — archive a non-active profile.

        Body: ``{"id": "...", "confirm": "REMOVE"}``

        Archive (not delete): old records signed by this key still need
        to verify, and the archived files let the user re-import if they
        change their mind. Refuses to remove the currently-active
        profile.
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        if (data.get("confirm") or "").strip() != "REMOVE":
            self._send_json({"error": "Type 'REMOVE' in the confirm field to proceed."}, 400)
            return
        pid = (data.get("id") or "").strip()
        from mememage import profiles
        try:
            res = profiles.remove(pid)
        except (FileNotFoundError, ValueError) as e:
            self._send_json({"error": str(e)}, 400)
            return
        self._send_json({"ok": True, "archived": res.get("archived")})

    def _identity_revoke(self):
        """POST /api/identity/revoke — publish the pre-signed revocation
        certificate to the Internet Archive.

        Body: ``{"confirm": "REVOKE"}``

        Revocation is the nuclear option: every record signed by this
        key will display a revocation warning after publication. The
        cert was pre-signed at keygen time so an attacker who steals
        the key cannot forge a revocation — but the same property means
        the user can't UN-revoke. Triple-check the confirmation string.
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        if (data.get("confirm") or "").strip() != "REVOKE":
            self._send_json({"error": "Type 'REVOKE' in the confirm field to proceed."}, 400)
            return

        from mememage import signing
        if not signing.is_signing_available():
            self._send_json({"error": "Signing unavailable (cryptography not installed)."}, 400)
            return
        cert = signing.get_revocation()
        if not cert:
            self._send_json({
                "error": "No revocation cert at ~/.mememage/revocation.cert. "
                         "Regenerate via: mememage keygen --force"
            }, 400)
            return
        ok = signing.verify_keychain_record(cert)
        if ok is not True:
            self._send_json({"error": "Revocation cert is invalid or corrupted."}, 500)
            return
        fp = cert.get("key_fingerprint") or signing.get_fingerprint()
        chain_id = signing.keychain_identifier(fp)
        try:
            signing.upload_keychain_record(cert, chain_id, "revocation.json")
        except Exception as e:
            log.exception("Revocation upload failed")
            self._send_json({"error": "Upload failed: " + str(e)}, 502)
            return
        self._send_json({
            "ok": True,
            "fingerprint": fp,
            "keychain_id": chain_id,
        })

    # ----- Dashboard: Chain management + payload config -----


    def _chain_current(self):
        """GET /api/chain/current — active chain + its metadata.

        Sealing password is scrubbed to a presence boolean — never
        round-tripped over the API. The dashboard renders it as a dot,
        not a value.
        """
        if not _check_auth(self): return
        from mememage import chains
        from urllib.parse import urlparse as _up, parse_qs as _pq
        # ?chain=<id> describes a SPECIFIC chain (e.g. a ticket's bound
        # chain) instead of the live active one — so the conception banner
        # can show where THIS ticket lands, not whatever's active now.
        _q = _pq(_up(self.path).query or "")
        _req = (_q.get("chain") or [None])[0]
        cid = _req if (_req and (chains.CHAINS_ROOT / _req).is_dir()) else chains.current()
        info = _scrub_chain_password(chains.info(cid))
        info.update(_active_password_status(cid))  # gated / unlocked / needs_unlock
        info["readiness"] = _chain_readiness(cid)  # ready/nopayload/pending/notready
        self._send_json({"id": cid, "info": info})

    def _chain_list(self):
        """GET /api/chain/list — all chains on disk + active marker.

        Also reports whether legacy state at ~/.mememage/ root needs to be
        migrated into chains/<id>/. The dashboard uses this to surface a
        "Migrate legacy state" banner when the chain list is empty but
        legacy state exists.
        """
        if not _check_auth(self): return
        from mememage import chains
        self._send_json({
            "current": chains.current(),
            "chains": [
                dict(_scrub_chain_password(c), readiness=_chain_readiness(c.get("id")))
                for c in chains.list_chains()
            ],
            "needs_migration": chains.needs_migration(),
        })

    def _chain_unlock(self):
        """POST /api/chain/unlock — hold the active chain's password in memory.

        Body: {"password": "..."}. Validated against the chain's verifier; the
        value is held in process memory only (never written to disk) and used
        to seal every mint into the active chain until a chain switch or lock.
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        pw = data.get("password")
        if not pw or not isinstance(pw, str):
            self._send_json({"error": "password required"}, 400)
            return
        from mememage import chains
        active = chains.current()
        verdict = chains.verify_password(pw, active)
        if verdict is False:
            self._send_json({"error": "Password does not match this chain's seal."}, 400)
            return
        if verdict is None:
            # No verifier to check against — either ungated or still on a
            # legacy plaintext (which already works without unlock). Holding an
            # unverifiable password could silently seal records with a wrong
            # key, so refuse rather than risk corruption.
            info = chains.info(active)
            if info.get("password"):
                self._send_json({
                    "error": "This chain still uses a legacy stored password and "
                             "works without unlocking. Migrate it to a verifier first."
                }, 409)
            else:
                self._send_json({"error": "This chain is not password-gated."}, 400)
            return
        _hold_password(active, pw)
        self._send_json({"ok": True, "unlocked": True, "chain": active})

    def _chain_lock(self):
        """POST /api/chain/lock — forget the held password for the active chain."""
        if not _check_auth(self): return
        from mememage import chains
        _clear_held(chains.current())
        self._send_json({"ok": True, "unlocked": False})

    def _chain_switch(self):
        """POST /api/chain/switch — set the active chain.

        Body: {"chain_id": "name"}

        site_embed.seal_file() / chunk_state_file(), payload.payload_dir(),
        and lineage's _db_path() all resolve at call time, so mints and
        seals after a switch land in the new chain immediately.
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        chain_id = (data.get("chain_id") or "").strip()
        if not chain_id:
            self._send_json({"error": "chain_id required"}, 400)
            return
        from mememage import chains
        try:
            chains.switch(chain_id)
            _clear_held()  # rung-1: new active chain starts locked
            self._send_json({
                "ok": True,
                "current": chain_id,
            })
        except FileNotFoundError as e:
            self._send_json({"error": str(e)}, 404)

    def _chain_new(self):
        """POST /api/chain/new — create a new chain.

        Body: {"chain_id": "name", "visibility": "light_energy"|"dark_matter",
               "name": "Display name", "identifier_prefix": "phoenix"}

        ``identifier_prefix`` is optional; if omitted the chain uses the
        default ``mememage`` prefix. Once set it cannot be changed.
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        chain_id = (data.get("chain_id") or "").strip()
        if not chain_id:
            self._send_json({"error": "chain_id required"}, 400)
            return
        visibility = data.get("visibility") or "light_energy"
        display_name = data.get("name") or chain_id
        # Empty string from the dashboard form means "use default" — let
        # chains.create() treat None as "no override" and skip writing
        # the field to disk.
        raw_prefix = data.get("identifier_prefix")
        identifier_prefix = (raw_prefix or "").strip() or None
        from mememage import chains
        try:
            meta = chains.create(
                chain_id, visibility=visibility, name=display_name,
                identifier_prefix=identifier_prefix,
            )
            self._send_json({"ok": True, "meta": meta})
        except (FileExistsError, ValueError) as e:
            self._send_json({"error": str(e)}, 400)

    def _chain_remove(self):
        """POST /api/chain/remove — permanently delete a chain and free its disk.

        Body: {"chain_id": "name"}

        Deletes the chain dir (uploads, sealed_chunks, lineage) + its Payload
        staging. Not recoverable — the dashboard confirms first. Refuses the
        active chain. Returns bytes freed.
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        chain_id = (data.get("chain_id") or "").strip()
        if not chain_id:
            self._send_json({"error": "chain_id required"}, 400)
            return

        from mememage import chains
        try:
            freed = chains.remove(chain_id)
        except FileNotFoundError as e:
            self._send_json({"error": str(e)}, 404)
            return
        except RuntimeError as e:
            self._send_json({"error": str(e)}, 409)
            return
        self._send_json({"ok": True, "freed_bytes": freed})

    def _chain_rename(self):
        """POST /api/chain/rename — update a chain's display name.

        Body: {"chain_id": "name", "name": "New display name"}

        Only the display name changes — chain id and visibility are
        locked at creation. Returns the updated chain.json metadata.
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        chain_id = (data.get("chain_id") or "").strip()
        new_name = (data.get("name") or "").strip()
        if not chain_id:
            self._send_json({"error": "chain_id required"}, 400)
            return
        if not new_name:
            self._send_json({"error": "name required"}, 400)
            return
        from mememage import chains
        try:
            meta = chains.rename(chain_id, new_name)
            self._send_json({"ok": True, "meta": meta})
        except FileNotFoundError as e:
            self._send_json({"error": str(e)}, 404)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)

    def _chain_password(self):
        """POST /api/chain/password — set or clear the chain's stored
        sealing password.

        Body: ``{"chain_id": "aries", "password": "..."}``. Empty string
        clears the stored password. Never echoed back in any GET — the
        dashboard sees only a boolean ``password_set`` presence flag via
        ``/api/chain/list`` and ``/api/chain/current``.
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        chain_id = (data.get("chain_id") or "").strip()
        password = data.get("password")
        if not chain_id:
            self._send_json({"error": "chain_id required"}, 400)
            return
        if password is not None and not isinstance(password, str):
            self._send_json({"error": "password must be a string"}, 400)
            return
        from mememage import chains
        try:
            result = chains.set_password(chain_id, password)
        except FileNotFoundError as e:
            self._send_json({"error": str(e)}, 404)
            return
        # Hold the runtime password too. The user just typed it (at chain
        # creation or an edit), so the chain is immediately conceivable instead
        # of the gated-but-locked state that demanded the SAME password again at
        # the next mint. Clearing the password drops the held value. (Held in
        # memory only — a restart still needs an explicit unlock, by design.)
        if password:
            _hold_password(chain_id, password)
        else:
            _clear_held(chain_id)
        self._send_json({"ok": True, **result})

    # ----- Channels (soul distribution) -----

    def _channels_list(self):
        """GET /api/channels — list configured channels with state.

        Returns the raw channels.json plus per-channel ``configured``
        booleans (do all credential env vars resolve to non-empty?)
        so the dashboard can render a "needs creds" badge without
        ever seeing the secret values themselves.
        """
        if not _check_auth(self): return
        from mememage import channels as _ch
        raw = _ch._load_raw()
        channels = _ch.load_channels()
        # Map channel_id → configured boolean. Channels in the JSON
        # but with unregistered types will be missing from this map;
        # the dashboard treats those as "unknown_type".
        configured_map = {c.id: c.is_configured() for c in channels}
        type_known = {c.id: True for c in channels}
        caps_map = {c.id: c.capabilities() for c in channels}
        out = []
        for entry in raw.get("channels", []):
            cid = entry.get("id")
            out.append({
                **entry,
                "configured": configured_map.get(cid, False),
                "type_known": bool(type_known.get(cid, False)),
                "capabilities": caps_map.get(cid, {}),
            })
        self._send_json({"channels": out})

    def _channels_raw(self):
        """GET /api/channels/raw — raw ``channels.json`` file contents
        as text/plain. Powers the dashboard's "View raw JSON" modal
        so users can spot legacy keys, hand-edits, and other config
        that doesn't surface through the structured editor.

        Auth-gated like every /api/* route. Returns "{}" if the file
        doesn't yet exist (fresh install before first save).
        """
        if not _check_auth(self): return
        from mememage import channels as _ch
        path = _ch._channels_path()  # active profile's channels.json
        try:
            content = path.read_text(encoding="utf-8") if path.exists() else "{}"
        except OSError as e:
            self._send_json({"error": f"Could not read {path}: {e}"}, 500)
            return
        body = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _channels_types(self):
        """GET /api/channels/types — schema for every registered
        channel type, so the dashboard's "+ Add channel" picker can
        render the credential/config form generically.
        """
        if not _check_auth(self): return
        from mememage import channels as _ch
        _ch._ensure_plugins_loaded()
        types = [cls.describe() for cls in _ch.all_types().values()]
        # Sort: built-ins first by display name. Stable for the UI.
        types.sort(key=lambda t: t["display_name"])
        self._send_json({"types": types})

    def _channels_save(self):
        """POST /api/channels — replace channels.json wholesale.

        The dashboard sends the full ``{"channels": [...]}`` list. We
        validate each entry has the required fields and that no two
        channels claim ``primary: true`` (the bar can only point at
        one canonical URL).
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        chans = data.get("channels")
        if not isinstance(chans, list):
            self._send_json({"error": "channels must be a list"}, 400)
            return
        seen_ids = set()
        primary_count = 0
        for i, c in enumerate(chans):
            if not isinstance(c, dict):
                self._send_json({"error": f"channels[{i}] must be an object"}, 400)
                return
            cid = c.get("id")
            ctype = c.get("type")
            if not cid or not isinstance(cid, str):
                self._send_json({"error": f"channels[{i}].id required (string)"}, 400)
                return
            if not ctype or not isinstance(ctype, str):
                self._send_json({"error": f"channels[{i}].type required (string)"}, 400)
                return
            if cid in seen_ids:
                self._send_json({"error": f"Duplicate channel id: {cid!r}"}, 400)
                return
            seen_ids.add(cid)
            if c.get("primary"):
                primary_count += 1
        if primary_count > 1:
            self._send_json({"error": "Only one channel may be marked primary"}, 400)
            return

        from mememage import channels as _ch
        _ch.save_raw({"channels": chans})
        self._send_json({"ok": True, "count": len(chans)})

    def _chain_gps_source(self):
        """POST /api/chain/gps-source — set the chain's GPS capture mode.

        Body: ``{"chain_id": "aries", "gps_source": "phone"|"machine"|"none"}``.
        Persists to ``chain.json``; the mint flow consults this on the
        next conception. Existing records are unaffected.
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        chain_id = (data.get("chain_id") or "").strip()
        gps_source = data.get("gps_source")
        if not chain_id:
            self._send_json({"error": "chain_id required"}, 400)
            return
        from mememage import chains
        try:
            result = chains.set_gps_source(chain_id, gps_source)
        except FileNotFoundError as e:
            self._send_json({"error": str(e)}, 404)
            return
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
            return
        self._send_json({"ok": True, **result})

    def _chain_gps_visibility(self):
        """POST /api/chain/gps-visibility — set how captured GPS is shown.

        Body: ``{"chain_id": "aries", "gps_visibility": "time_locked"|"public"}``.
        ``time_locked`` (default) seals coordinates in the RSA puzzle;
        ``public`` ALSO stores plaintext so the cert shows the location now.
        Persists to ``chain.json``; only affects FUTURE conceptions (you can't
        un-time-lock an already-minted record).
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        chain_id = (data.get("chain_id") or "").strip()
        gps_visibility = data.get("gps_visibility")
        if not chain_id:
            self._send_json({"error": "chain_id required"}, 400)
            return
        from mememage import chains
        try:
            result = chains.set_gps_visibility(chain_id, gps_visibility)
        except FileNotFoundError as e:
            self._send_json({"error": str(e)}, 404)
            return
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
            return
        self._send_json({"ok": True, **result})

    def _chain_watermark(self):
        """POST /api/chain/watermark — set the chain's watermark preset.

        Body: ``{"chain_id": "aries", "watermark": "off"|"on"}``.
        A live per-chain image setting (read at mint time, like gps_source) —
        persists to ``chain.json`` immediately, no seal needed; toggle anytime.
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        chain_id = (data.get("chain_id") or "").strip()
        preset = data.get("watermark")
        if not chain_id:
            self._send_json({"error": "chain_id required"}, 400)
            return
        from mememage import chains
        try:
            result = chains.set_watermark(chain_id, preset)
        except FileNotFoundError as e:
            self._send_json({"error": str(e)}, 404)
            return
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
            return
        self._send_json({"ok": True, **result})

    def _chain_migrate(self):
        """POST /api/chain/migrate — move legacy state at ~/.mememage/ root
        into chains/<chain_id>/.

        Body: {"chain_id": "aries" (optional), "name": "Display name" (optional),
               "visibility": "light_energy"|"dark_matter" (optional)}

        Idempotent: if the target chain directory already exists, returns
        a 409 so the dashboard can show a clear error.
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            data = {}
        from mememage import chains
        kwargs = {}
        if data.get("chain_id"): kwargs["chain_id"] = data["chain_id"].strip()
        if data.get("name"):     kwargs["chain_name"] = data["name"].strip()
        if data.get("visibility"): kwargs["visibility"] = data["visibility"]
        try:
            result = chains.migrate(**kwargs)
            self._send_json({"ok": True, "result": result})
        except FileExistsError as e:
            self._send_json({"error": str(e)}, 409)
        except (RuntimeError, ValueError) as e:
            self._send_json({"error": str(e)}, 500)

    def _chain_config_get(self):
        """GET /api/chain/config — return the active chain's payload config.

        Always returns a valid config; falls back to the default for chains
        without an authored chain.json.
        """
        if not _check_auth(self): return
        from mememage import chain_config
        try:
            cfg = chain_config.load()
            out = cfg.to_dict()
            # Tell the dashboard whether constellation_size is frozen, so it
            # can disable the input. True only for a provenance-only chain
            # that has already minted (sealed chains stage it per Age).
            out["constellation_size_locked"] = self._provenance_minted()
            # Provenance-only = no entry resolves to any source (the blank()
            # fallback, or a payload whose sources were all cleared). The
            # dashboard renders an empty "no payload configured" state instead
            # of the phantom decoder scaffold when this is true.
            out["provenance_only"] = not cfg.has_payload()
            self._send_json(out)
        except (RuntimeError, ValueError) as e:
            self._send_json({"error": str(e)}, 500)

    def _chain_config_set(self):
        """POST /api/chain/config — replace the active chain's payload config.

        Body: the full ChainConfig dict (id/name/visibility/M/layers/pinned/entries).
        Validates strictly before writing; rejects with 400 on any schema
        violation (M-too-small, missing entry refs, pinned-out-of-range, etc.).

        Always allowed, even while an Age is in progress: the running Age's
        chunk distribution is driven by the SEAL (sealed_chunks.json), which
        is immutable for the Age — nothing mid-Age reads chain.json for chunk
        or cadence decisions. So an Apply mid-Age can't corrupt the current
        Age; it simply STAGES the config for the next seal. (site_pack.seal()
        still refuses to start a new Age until the current one completes.)
        """
        if not _check_auth(self): return
        try:
            data = json.loads(self._read_body() or "{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON"}, 400)
            return

        # Hydrate preset-resident source paths into the active chain's
        # uploads/ before validating. If the user loaded a preset and is
        # Applying it to the chain, the entry sources still point at
        # ~/.mememage/payload_presets/<name>/files/... — copy those into
        # the chain's uploads/ so the chain stops depending on the preset
        # (the user can then delete the preset without breaking the chain).
        self._hydrate_preset_paths_into_chain(data)

        from mememage import chain_config
        try:
            cfg = chain_config.ChainConfig.from_dict(data)
        except ValueError as e:
            self._send_json({"error": f"Invalid config: {e}"}, 400)
            return
        except Exception as e:
            self._send_json({"error": f"Could not parse config: {e}"}, 400)
            return

        # The decoder layer's chunk count IS the constellation size (one
        # decoder chunk per star). Keep them aligned on every write so the
        # stored config never drifts — seal() rebinds it too, but this keeps
        # chain.json honest in the meantime.
        for _ly in cfg.layers:
            if _ly.name == "decoder":
                _ly.K = cfg.constellation_size
                break

        # Provenance lock: a provenance-only (never-sealed) chain freezes its
        # constellation_size once it has conceived a star — changing the
        # rhythm mid-stream would straddle a constellation. Sealed chains are
        # exempt: they snapshot constellation_size per Age, so a change just
        # stages for the next seal.
        if cfg.constellation_size != self._chain_constellation_size_now(cfg.id) \
                and self._provenance_minted():
            self._send_json({
                "error": "Constellation size is locked: this chain has already "
                         "conceived stars and never seals, so its constellation "
                         "rhythm is fixed. Sealed chains can change it per Age."
            }, 409)
            return

        try:
            written = chain_config.save(cfg)
            self._send_json({"ok": True, "path": str(written), "config": cfg.to_dict()})
        except Exception as e:
            log.exception("chain_config save failed")
            self._send_json({"error": str(e)}, 500)

    def _chain_constellation_size_now(self, chain_id):
        """The chain's currently-stored constellation_size (best-effort)."""
        from mememage import chains
        try:
            return chains.get_constellation_size(chain_id)
        except Exception:
            return chains.DEFAULT_CONSTELLATION_SIZE

    def _provenance_minted(self):
        """True if the ACTIVE chain is provenance-only (never sealed) AND has
        already conceived a star — the point past which its constellation
        rhythm is locked. Sealed chains return False (they snapshot
        constellation_size per Age, so changes just stage for the next seal).

        "Has minted" = the active chain's lineage parent_id is set, or the
        outer position has advanced. parent_id is written on every successful
        mint (per active chain) and is non-null the moment a chain has
        conceived once — the reliable per-chain signal now that souls live in
        the shared flat store rather than a per-chain records/ dir."""
        from mememage.site_embed import _load_seal, current_outer_position
        try:
            if _load_seal() is not None:
                return False
        except Exception:
            return False
        try:
            from mememage.lineage import get_parent_id
            if get_parent_id() is not None:
                return True
        except Exception:
            pass
        try:
            return current_outer_position() > 0
        except Exception:
            return False

    def _hydrate_preset_paths_into_chain(self, data):
        """Copy preset-resident source files into the active chain's uploads/.

        Mutates ``data['entries']`` in place: any source path that lives
        under the preset root gets copied into the chain's uploads dir
        and the path rewritten to the chain-local absolute path.
        Idempotent: already-chain-local paths are untouched.

        Best-effort: copy failures fall through (the chain config is
        still saved with the original path; build will fail audibly
        when the preset is later deleted).
        """
        import os as _os, shutil as _shutil
        from pathlib import Path
        from mememage import chains as _chains
        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, dict):
            return
        try:
            chain_id = _chains.current()
        except Exception:
            return
        uploads = _chains.chain_dir(chain_id) / "uploads"
        preset_root = self._preset_root().resolve()

        def relocate(src):
            if not isinstance(src, str) or not src:
                return src
            try:
                src_path = Path(src).resolve()
            except OSError:
                return src
            try:
                src_path.relative_to(preset_root)
            except ValueError:
                return src  # not preset-resident; leave alone
            if not src_path.exists():
                return src  # dead reference; let build fail audibly
            uploads.mkdir(parents=True, exist_ok=True)
            dest = uploads / src_path.name
            try:
                _shutil.copy2(str(src_path), str(dest))
            except Exception as e:
                log.warning("hydrate: copy %s → %s failed: %s", src_path, dest, e)
                return src
            return str(dest)

        for entry in entries.values():
            if not isinstance(entry, dict):
                continue
            if isinstance(entry.get("sources"), list):
                entry["sources"] = [relocate(s) for s in entry["sources"]]
            elif "source" in entry:
                entry["source"] = relocate(entry["source"])

    def _serve_mint_page(self, token):
        """GET /mint/<token> — conception page (adapts to chain's GPS source).

        Three branches, decided by the active chain's ``gps_source`` at
        session-creation time:

        - ``phone``   : ``navigator.geolocation.watchPosition`` until
                        precise fix, then the Conceive button enables.
        - ``machine`` : no client-side GPS; server fetches IP geolocation
                        when the user hits Conceive. Button enabled
                        immediately.
        - ``none``    : no GPS at all. Button enabled immediately. The
                        record will have no ``gps_time_locked`` field and the
                        cert renders the "BIRTHPLACE — NOT RECORDED"
                        placeholder.

        QR-for-cross-device-handoff lives on the dashboard's Mint tab
        now, not here — by the time the user is on this page, they've
        already picked their device.
        """
        _cleanup_expired()
        session = _sessions.get(token)
        if not session:
            self._send_html("<h1>Token expired or invalid</h1>", 404)
            return
        # Serve the page for completed/minting sessions too — the
        # client-side JS polls /api/mint/<token>/status and switches
        # into the result view, so revisiting a completed conception
        # URL surfaces the minted image + per-channel links instead
        # of bouncing with a 409. Useful for sharing the URL after
        # the fact, or coming back to copy a surface link.

        try:
            from mememage import chains
            from mememage.gps import GPS_SOURCE_PHONE
            # Bind to the session's chain (same one the mint uses), and resolve
            # the EFFECTIVE source so the page renders the machine flow instead
            # of a dead phone-capture UI when no phone can reach this host.
            _bound_src = session.get("chain") or chains.current()
            gps_source = self._effective_gps_source(chains.get_gps_source(_bound_src))
        except Exception:
            gps_source = GPS_SOURCE_PHONE

        # Conception page lives in docs/ now (was inline in this module).
        # Server reads + token-substitutes once per request. Same pattern
        # as the dashboard — keeps HTML iterable as a standalone file.
        template = (DOCS_DIR / "conception.html").read_text(encoding="utf-8")
        image_name_safe = json.dumps(os.path.basename(session["image_path"]))[1:-1]
        metadata_json = json.dumps(session["metadata"])
        # Chain badge — pin to the session's BOUND chain (same chain the
        # mint will use), not whatever is active now.
        _bound = session.get("chain") or chains.current()
        try:
            chain_badge = _chain_badge_html(_bound)
        except Exception:
            chain_badge = ""
        # Channels strip — the surfaces this conception will blast to (enabled
        # + configured, narrowed by the active profile's scope). Shown so the
        # creator sees exactly where the soul lands before confirming.
        try:
            channels_html = _conception_channels_html()
        except Exception:
            channels_html = ""
        html = template.replace("{{TOKEN}}", token)
        html = html.replace("{{IMAGE_NAME}}", image_name_safe)
        html = html.replace("{{GPS_SOURCE}}", gps_source)
        html = html.replace("{{METADATA_JSON}}", metadata_json)
        html = html.replace("{{CHAIN_BADGE}}", chain_badge)
        html = html.replace("{{CHANNELS}}", channels_html)
        # Cache-bust the page's static assets so a deploy invalidates stale
        # browser copies — the conception /js + /css have no other version
        # query, so an old cached asset can render the page blank (or run an
        # outdated loop, e.g. starfield.js) after an update. Stamp every
        # /js/*.js and /css/*.css ref with the NEWEST mtime across all of them,
        # so a change to ANY asset busts the cache — not just conception.js.
        # Idempotent (anchors on the closing quote, won't double-stamp).
        try:
            _assets = (list((DOCS_DIR / "js").glob("*.js"))
                       + list((DOCS_DIR / "css").glob("*.css")))
            _v = int(max(p.stat().st_mtime for p in _assets)) if _assets else 0
        except OSError:
            _v = 0
        html = re.sub(r'(/(?:js|css)/[\w.\-]+\.(?:js|css))"', r'\1?v=%d"' % _v, html)
        self._send_html(html)


# ---------------------------------------------------------------------------
# HTML templates
# ---------------------------------------------------------------------------

_UPLOAD_PAGE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mememage — Conceive</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace;
    background: #0a0a0f;
    color: #e0e0e0;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 2rem;
  }
  h1 { font-size: 1.4rem; margin-bottom: 1.5rem; color: #c0c0d0; }
  .upload-zone {
    width: 100%;
    max-width: 480px;
    border: 2px dashed #333;
    border-radius: 12px;
    padding: 3rem 1.5rem;
    text-align: center;
    cursor: pointer;
    transition: border-color 0.2s;
    margin-bottom: 1.5rem;
  }
  .upload-zone:hover, .upload-zone.dragover { border-color: #7070a0; }
  .upload-zone img {
    max-width: 100%;
    max-height: 300px;
    border-radius: 8px;
    margin-top: 1rem;
  }
  input[type="file"] { display: none; }
  .fields {
    width: 100%;
    max-width: 480px;
  }
  .field { margin-bottom: 0.75rem; }
  .field label {
    display: block;
    font-size: 0.75rem;
    color: #888;
    margin-bottom: 0.25rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  .field input, .field textarea {
    width: 100%;
    padding: 0.5rem;
    background: #16161e;
    border: 1px solid #333;
    border-radius: 6px;
    color: #e0e0e0;
    font-family: inherit;
    font-size: 0.9rem;
  }
  .field textarea { min-height: 80px; resize: vertical; }
  button.mint-btn {
    width: 100%;
    max-width: 480px;
    padding: 0.75rem;
    margin-top: 1rem;
    background: #2a2a3a;
    border: 1px solid #444;
    border-radius: 8px;
    color: #e0e0e0;
    font-size: 1rem;
    cursor: pointer;
    transition: background 0.2s;
  }
  button.mint-btn:hover { background: #3a3a4a; }
  button.mint-btn:disabled { opacity: 0.4; cursor: not-allowed; }
  .status {
    margin-top: 1rem;
    font-size: 0.85rem;
    color: #888;
    text-align: center;
    max-width: 480px;
  }
  .status.error { color: #cc4444; }
  .status.success { color: #44aa44; }
</style>
</head>
<body>
<h1>Mememage</h1>

<div class="upload-zone" id="dropZone" onclick="fileInput.click()">
  <p>Drop, paste, or tap to select</p>
  <input type="file" id="fileInput" accept="image/*">
  <img id="preview" style="display:none">
</div>

<div class="fields">
  <div class="field">
    <label>Prompt</label>
    <textarea id="prompt" placeholder="Generation prompt..."></textarea>
  </div>
  <div class="field">
    <label>Seed</label>
    <input type="number" id="seed" placeholder="e.g. 42">
  </div>
  <div class="field">
    <label>Width</label>
    <input type="number" id="width" placeholder="e.g. 1024">
  </div>
  <div class="field">
    <label>Height</label>
    <input type="number" id="height" placeholder="e.g. 1024">
  </div>
  <div class="field">
    <label>Model / UNet</label>
    <input type="text" id="unet" placeholder="e.g. flux1-dev">
  </div>
  <div class="field">
    <label>Sampler</label>
    <input type="text" id="sampler" placeholder="e.g. euler">
  </div>
  <div class="field">
    <label>Scheduler</label>
    <input type="text" id="scheduler" placeholder="e.g. normal">
  </div>
</div>

<button class="mint-btn" id="mintBtn" disabled>Upload &amp; Conceive</button>
<div class="status" id="status"></div>

<script>
const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');
const preview = document.getElementById('preview');
const mintBtn = document.getElementById('mintBtn');
const status = document.getElementById('status');
let selectedFile = null;
const API_TOKEN = '{{API_TOKEN}}';
const authHeaders = API_TOKEN ? {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + API_TOKEN} : {'Content-Type': 'application/json'};

dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener('change', () => { if (fileInput.files.length) handleFile(fileInput.files[0]); });
// Clipboard paste — parity with the decoder/validator/dashboard. Only an image
// paste is consumed; a text paste falls through to the prompt/seed fields.
document.addEventListener('paste', e => {
  if (!e.clipboardData) return;
  const files = Array.prototype.slice.call(e.clipboardData.files || []);
  let img = files.find(f => f.type && f.type.indexOf('image/') === 0);
  if (!img) {
    const it = Array.prototype.slice.call(e.clipboardData.items || []).find(i => i.type && i.type.indexOf('image/') === 0);
    if (it) img = it.getAsFile();
  }
  if (!img) return;
  if (!img.name) { try { img = new File([img], 'pasted.png', {type: img.type || 'image/png'}); } catch (_) {} }
  e.preventDefault();
  handleFile(img);
});

function handleFile(file) {
  selectedFile = file;
  const reader = new FileReader();
  reader.onload = e => {
    preview.src = e.target.result;
    preview.style.display = 'block';
    dropZone.querySelector('p').style.display = 'none';
    status.textContent = 'Checking for embedded metadata...';
    status.className = 'status';

    // Upload immediately to let server extract PNG text chunks
    uploadAndCheck(e.target.result.split(',')[1], file.name);
  };
  reader.readAsDataURL(file);
}

function fillForm(meta) {
  if (meta.prompt) document.getElementById('prompt').value = meta.prompt;
  if (meta.seed) document.getElementById('seed').value = meta.seed;
  if (meta.width) document.getElementById('width').value = meta.width;
  if (meta.height) document.getElementById('height').value = meta.height;
  if (meta.unet) document.getElementById('unet').value = meta.unet;
  if (meta.sampler) document.getElementById('sampler').value = meta.sampler;
  if (meta.scheduler) document.getElementById('scheduler').value = meta.scheduler;
}

async function uploadAndCheck(b64, filename) {
  try {
    const resp = await fetch('/api/mint/upload', {
      method: 'POST',
      headers: authHeaders,
      body: JSON.stringify({ image_data: b64, filename, metadata: {} }),
    });
    const data = await resp.json();
    if (data.error) {
      status.textContent = data.error;
      status.className = 'status error';
      mintBtn.disabled = false;
      return;
    }
    if (data.metadata && data.metadata.prompt) {
      // PNG had embedded metadata — show extracted data and mint link
      fillForm(data.metadata);
      status.textContent = 'Metadata extracted. Conception link ready.';
      status.className = 'status success';
      if (data.mint_url_full) {
        var linkDiv = document.createElement('div');
        linkDiv.style.cssText = 'margin-top:1rem;text-align:center;';
        linkDiv.innerHTML = '<a href="' + data.mint_url_full + '" style="color:#7070a0;font-size:0.85rem;word-break:break-all;">' + data.mint_url_full + '</a>';
        status.parentNode.insertBefore(linkDiv, status.nextSibling);
      }
    } else {
      // No embedded metadata — show form for manual entry
      status.textContent = 'No embedded metadata found. Fill in the fields below.';
      status.className = 'status';
      mintBtn.disabled = false;
      mintBtn.dataset.mintUrl = data.mint_url;
      mintBtn.dataset.token = data.token || '';
    }
  } catch (err) {
    status.textContent = 'Upload failed: ' + err.message;
    status.className = 'status error';
    mintBtn.disabled = false;
  }
}

mintBtn.addEventListener('click', async () => {
  if (!selectedFile) return;

  // If we already have a mint URL from the initial upload, redirect
  if (mintBtn.dataset.mintUrl) {
    window.location.href = mintBtn.dataset.mintUrl;
    return;
  }

  mintBtn.disabled = true;
  status.textContent = 'Uploading...';
  status.className = 'status';

  const reader = new FileReader();
  reader.onload = async e => {
    const b64 = e.target.result.split(',')[1];
    const metadata = {};
    const prompt = document.getElementById('prompt').value.trim();
    const seed = document.getElementById('seed').value.trim();
    const width = document.getElementById('width').value.trim();
    const height = document.getElementById('height').value.trim();
    const unet = document.getElementById('unet').value.trim();
    const sampler = document.getElementById('sampler').value.trim();
    const scheduler = document.getElementById('scheduler').value.trim();

    if (prompt) metadata.prompt = prompt;
    if (seed) metadata.seed = parseInt(seed, 10);
    if (width) metadata.width = parseInt(width, 10);
    if (height) metadata.height = parseInt(height, 10);
    if (unet) metadata.unet = unet;
    if (sampler) metadata.sampler = sampler;
    if (scheduler) metadata.scheduler = scheduler;

    try {
      const resp = await fetch('/api/mint/upload', {
        method: 'POST',
        headers: authHeaders,
        body: JSON.stringify({
          image_data: b64,
          filename: selectedFile.name,
          metadata: metadata,
        }),
      });
      const data = await resp.json();
      if (data.error) {
        status.textContent = data.error;
        status.className = 'status error';
        mintBtn.disabled = false;
        return;
      }
      window.location.href = data.mint_url;
    } catch (err) {
      status.textContent = 'Upload failed: ' + err.message;
      status.className = 'status error';
      mintBtn.disabled = false;
    }
  };
  reader.readAsDataURL(selectedFile);
});
</script>
</body>
</html>
"""



_DESKTOP_LOCK = Path.home() / ".mememage" / "desktop.lock"


def _desktop_already_running():
    """If a desktop Mememage instance is already up, return its dashboard
    URL; otherwise None (clearing a stale lock on the way out).

    Single-instance guard for the double-click app: re-launching (or
    double-clicking again after closing the browser) should just focus the
    running server, never start a second one on a new free port. Liveness
    is decided by an actual ``/health`` probe, so a lock left behind by a
    hard-killed process (closed console window, force-quit, crash) self-
    heals — we don't trust the file's mere existence.
    """
    try:
        info = json.loads(_DESKTOP_LOCK.read_text(encoding="utf-8"))
        port = int(info["port"])
        scheme = info.get("scheme", "http")
    except Exception:
        return None
    base = f"{scheme}://127.0.0.1:{port}"
    try:
        import urllib.request  # server.py is http.server-based; not at module top
        ctx = None
        if scheme == "https":
            import ssl as _ssl
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
        with urllib.request.urlopen(base + "/health", timeout=2, context=ctx) as r:
            if r.status == 200:
                token = _load_mint_token()
                return base + (f"/{token}" if token else "/dashboard")
    except Exception:
        pass
    # Recorded server isn't answering — stale lock. Clear it and proceed.
    try:
        _DESKTOP_LOCK.unlink()
    except OSError:
        pass
    return None


def _write_desktop_lock(port: int, scheme: str) -> None:
    try:
        _DESKTOP_LOCK.parent.mkdir(parents=True, exist_ok=True)
        _DESKTOP_LOCK.write_text(
            json.dumps({"port": port, "scheme": scheme, "pid": os.getpid()}),
            encoding="utf-8",
        )
    except OSError:
        pass


def _clear_desktop_lock() -> None:
    try:
        _DESKTOP_LOCK.unlink()
    except OSError:
        pass


def _find_free_port(host="127.0.0.1", preferred=8765):
    """Return a bindable port at/after ``preferred`` on ``host``.

    Desktop mode picks a free local port so a second launch (or any other
    service on the default) doesn't fail to bind. Falls back to
    ``preferred`` if the small scan finds nothing (the bind will then
    raise a clear error rather than guess wildly).
    """
    import socket as _s
    for p in range(preferred, preferred + 50):
        sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
        try:
            sock.bind((host, p))
            return p
        except OSError:
            continue
        finally:
            sock.close()
    return preferred


# --- Tailscale phone-capture HTTPS ----------------------------------------
#
# The desktop app binds loopback over plain HTTP — fine for the local
# browser (localhost is a secure context) but unreachable by a phone, and
# a phone needs a SECURE context for geolocation anyway. The one desktop
# interface that yields a phone-trusted cert is Tailscale's ``*.ts.net``
# MagicDNS name: ``tailscale cert`` issues a real Let's Encrypt cert for
# it. So when a Tailscale node has HTTPS enabled, we provision that cert
# and serve a SECOND, HTTPS socket on the tailnet IP (loopback stays HTTP).
# A phone on the same tailnet loads ``https://<node>.<tailnet>.ts.net`` —
# trusted cert, secure context, geolocation + fetch both work.
#
# Everything here is best-effort: no Tailscale, HTTPS not enabled, or a
# cert that won't issue → we simply don't bind the second socket and the
# GPS source falls back to ``machine`` (approximate IP geolocation).

_TS_CERT_DIR = Path.home() / ".mememage" / "certs"


def _tailscale_cmd():
    """Path to the ``tailscale`` CLI, or ``None`` if not installed."""
    import shutil
    candidates = (
        "tailscale",
        "/opt/homebrew/bin/tailscale",
        "/usr/bin/tailscale",
        "/usr/local/bin/tailscale",
        "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
        r"C:\Program Files\Tailscale\tailscale.exe",
    )
    for c in candidates:
        found = shutil.which(c) if "/" not in c and "\\" not in c else (
            c if os.path.exists(c) else None)
        if found:
            return found
    return None


def _tailscale_https_fqdn():
    """This node's MagicDNS FQDN (no trailing dot) when the tailnet has
    HTTPS certs enabled, else ``None``.

    Reads ``tailscale status --json``: ``Self.DNSName`` is the node name,
    and a non-empty ``CertDomains`` means the admin console has HTTPS
    certificates turned on (a prerequisite for ``tailscale cert``).
    """
    cmd = _tailscale_cmd()
    if not cmd:
        return None
    try:
        import subprocess
        out = subprocess.run([cmd, "status", "--json"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode != 0:
            return None
        st = json.loads(out.stdout)
    except Exception as e:
        log.debug("tailscale status failed: %s", e)
        return None
    fqdn = ((st.get("Self") or {}).get("DNSName") or "").rstrip(".")
    cert_domains = st.get("CertDomains") or []
    if fqdn and cert_domains:
        return fqdn
    return None


def _provision_tailscale_cert(fqdn, timeout=90):
    """Ensure a valid Tailscale (Let's Encrypt) cert for ``fqdn`` lives in
    ``_TS_CERT_DIR``. Returns ``(certfile, keyfile)`` or ``None``.

    Always runs ``tailscale cert`` — it is the authoritative, idempotent
    source of truth: ~0.5s when the cert is already valid in Tailscale's own
    store (it just writes the cached copy), longer only when a reissue is
    genuinely needed. A file-mtime cache was tried and removed — mtime does
    NOT track the cert's ``notAfter``, so it happily served an expired cert.
    Best-effort: any failure returns ``None`` and the caller degrades to
    machine GPS.
    """
    cmd = _tailscale_cmd()
    if not cmd:
        return None
    certfile = _TS_CERT_DIR / f"{fqdn}.crt"
    keyfile = _TS_CERT_DIR / f"{fqdn}.key"
    _TS_CERT_DIR.mkdir(parents=True, exist_ok=True)
    log.info("Provisioning Tailscale HTTPS cert for %s…", fqdn)
    try:
        import subprocess
        out = subprocess.run(
            [cmd, "cert", "--cert-file", str(certfile), "--key-file", str(keyfile), fqdn],
            capture_output=True, text=True, timeout=timeout)
        if out.returncode != 0:
            log.warning("tailscale cert failed (%s): %s", fqdn, out.stderr.strip())
            # Fall back to an existing on-disk cert only if it's still valid —
            # never serve an expired one (that breaks HTTPS on the phone).
            if certfile.exists() and keyfile.exists() and _cert_not_expired(certfile):
                return str(certfile), str(keyfile)
            return None
    except Exception as e:
        log.warning("tailscale cert error: %s", e)
        if certfile.exists() and keyfile.exists() and _cert_not_expired(certfile):
            return str(certfile), str(keyfile)
        return None
    if certfile.exists() and keyfile.exists():
        return str(certfile), str(keyfile)
    return None


def _cert_not_expired(certfile, margin_days=1):
    """True when the PEM cert at ``certfile`` is still valid (notAfter is at
    least ``margin_days`` in the future). Uses ``cryptography`` when present;
    if it isn't installed we can't parse the date, so return ``False`` (treat
    as unusable rather than risk serving an expired cert)."""
    try:
        from cryptography import x509
        from datetime import datetime, timezone, timedelta
        data = Path(certfile).read_bytes()
        cert = x509.load_pem_x509_certificate(data)
        return cert.not_valid_after_utc > datetime.now(timezone.utc) + timedelta(days=margin_days)
    except Exception:
        return False


def _resolve_phone_capture(port):
    """Resolve the phone-reachable HTTPS capture endpoint for desktop mode.

    Returns ``(ts_ip, certfile, keyfile, capture_base)`` when a Tailscale
    node with HTTPS is available and its cert provisions, else ``None``.
    The caller binds a second HTTPS socket on ``ts_ip`` and advertises
    ``capture_base`` (``https://<fqdn>:<port>``) in the mint URL/QR.
    """
    fqdn = _tailscale_https_fqdn()
    if not fqdn:
        return None
    try:
        from mememage.gps import tailscale_ip
        ts_ip = tailscale_ip()
    except Exception:
        ts_ip = None
    if not ts_ip:
        return None
    prov = _provision_tailscale_cert(fqdn)
    if not prov:
        return None
    certfile, keyfile = prov
    return ts_ip, certfile, keyfile, f"https://{fqdn}:{port}"


def run_server(host="0.0.0.0", port=8443, certfile=None, keyfile=None,
               open_browser=False, on_ready=None):
    """Start the mint server.

    Args:
        host: Bind address.
        port: Port number.
        certfile: Path to TLS certificate (PEM). Required for HTTPS.
        keyfile: Path to TLS private key (PEM). Required for HTTPS.
        open_browser: When True (desktop/local mode), open the dashboard
            in the default browser once the server is listening.
        on_ready: Optional callback invoked with the live server instance
            just before serve_forever() blocks. The tray app uses it to
            grab the handle so its Quit item can call server.shutdown()
            from another thread. Default None → behaviour unchanged.
    """
    # Single-instance guard (desktop/local mode only): if an instance is
    # already running, focus it (open its dashboard) and bail rather than
    # start a second server on a new port. VPS/foreground server runs
    # aren't gated — they manage their own lifecycle (systemd, etc.).
    if open_browser:
        existing = _desktop_already_running()
        if existing:
            print(f"Mememage is already running — opening {existing}")
            try:
                import webbrowser as _wb
                _wb.open(existing)
            except Exception:
                pass
            return

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    _load_sessions()
    # Expire stale sessions + reclaim their staged images, then sweep
    # any orphan upload files that no session still references.
    _cleanup_expired()
    _migrate_records_to_store()
    _cleanup_orphan_uploads()
    _cleanup_orphan_payload_uploads()
    _cleanup_stale_part_files()

    # Threaded server so the handler can call back into itself without
    # deadlocking. The pair flow exposed this: an inbound /api/profiles/pair
    # handler signs an alias and asks the http_push channel to publish it,
    # which PUTs to /api/keychain/... on the SAME process — single-threaded
    # HTTPServer would block waiting for its own request handler. Threaded
    # serve also helps any other future self-call patterns (e.g. _patch_record's
    # re-blast) and lets a slow IA upload not block dashboard responsiveness.
    class ReusableThreadingHTTPServer(ThreadingHTTPServer):
        allow_reuse_address = True
        allow_reuse_port = True
        # Daemon worker threads so the server can shut down promptly
        # without waiting for in-flight uploads to finish (sessions
        # persist to disk; threads dying mid-PUT is recoverable).
        daemon_threads = True

    server = ReusableThreadingHTTPServer((host, port), MintHandler)

    # Stash own bind info so channels that point back at us (self-push)
    # can detect themselves and resolve auth via MINT_API_TOKEN instead
    # of requiring a separate HTTP_PUSH_TOKEN that drifts on rotation.
    # Format: comma-separated host[:port] — channels compare against
    # base_url's host. Multiple values cover the case where the
    # configured domain, the outbound public IP, and the loopback
    # all reach the same process.
    self_hosts = []
    cfg_domain = (_get_server_config().get("domain") or "").strip()
    if cfg_domain:
        self_hosts.append(cfg_domain.split(":")[0])
    try:
        import socket as _sock
        s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            outbound = s.getsockname()[0]
            if outbound and outbound not in self_hosts:
                self_hosts.append(outbound)
        finally:
            s.close()
    except Exception:
        pass
    # Always include the listen-address host if it's a concrete IP
    # (not 0.0.0.0). Lets users bind to a specific interface and have
    # self-push detect it.
    if host and host not in ("0.0.0.0", "::", ""):
        if host not in self_hosts:
            self_hosts.append(host)
    if self_hosts:
        os.environ["MEMEMAGE_SELF_HOST"] = ",".join(self_hosts)
        log.info("MEMEMAGE_SELF_HOST=%s (port %d)", os.environ["MEMEMAGE_SELF_HOST"], port)

    # Seed first-run defaults so a fresh-install user lands on a
    # dashboard with 3 of 4 onboarding steps already green. Each
    # seeder is idempotent — already-configured installs are
    # unaffected. Runs AFTER MEMEMAGE_SELF_HOST is computed because
    # the http_push channel seeder needs that to point at itself.
    _seed_first_run_defaults(port, scheme="https" if (certfile and keyfile) else "http")

    scheme = "http"
    if certfile and keyfile:
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile, keyfile)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    # Advertise the right scheme in self-referential URLs (mint links,
    # souls base, downloads). A local/desktop bind is http — and on
    # localhost that's a secure context, so geolocation still works.
    os.environ["MEMEMAGE_SCHEME"] = scheme
    # Desktop/local mode advertises loopback (whatever the browser
    # connected to) for the DECODER/souls base — the local browser fetches
    # records over localhost. The PHONE-capture URL is resolved separately
    # (see below) because a phone can't reach loopback.
    if open_browser:
        os.environ["MEMEMAGE_LOCAL"] = "1"
    else:
        os.environ.pop("MEMEMAGE_LOCAL", None)

    # Phone-capture HTTPS socket (desktop only). The loopback server above
    # is HTTP — unreachable by a phone, and a phone needs a secure context
    # for geolocation regardless. When this node is on a Tailscale tailnet
    # with HTTPS enabled, bind a SECOND server on the tailnet IP wrapped in
    # a Tailscale-issued (Let's Encrypt) cert, and advertise its ts.net URL
    # in the capture QR. Best-effort: any failure leaves the capture base
    # unset, so the GPS source falls back to machine (approximate). The
    # LAN/loopback interfaces are never exposed — only the tailnet IP.
    extra_servers = []
    os.environ.pop("MEMEMAGE_CAPTURE_BASE", None)
    if open_browser:
        try:
            cap = _resolve_phone_capture(port)
        except Exception as e:
            log.debug("Phone-capture resolution failed: %s", e)
            cap = None
        if cap:
            ts_ip, ts_cert, ts_key, capture_base = cap
            try:
                import ssl as _ssl
                ts_srv = ReusableThreadingHTTPServer((ts_ip, port), MintHandler)
                tctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
                tctx.load_cert_chain(ts_cert, ts_key)
                ts_srv.socket = tctx.wrap_socket(ts_srv.socket, server_side=True)
                extra_servers.append(ts_srv)
                os.environ["MEMEMAGE_CAPTURE_BASE"] = capture_base
                log.info("Phone GPS capture: %s (Tailscale HTTPS)", capture_base)
            except OSError as e:
                log.warning("Could not bind Tailscale HTTPS capture socket "
                            "(%s) — phone GPS will use machine fallback", e)
        else:
            log.info("No Tailscale HTTPS endpoint — phone GPS uses machine "
                     "fallback (approximate IP geolocation)")

    log.info("Mememage mint server running on %s://%s:%d", scheme, host, port)
    base = f"{scheme}://localhost:{port}"
    bar = "=" * 56
    print(bar)
    print(f"  MEMEMAGE MINT SERVER  ({scheme.upper()} on {host}:{port})")
    print(bar)
    print(f"  Dashboard:   {base}/dashboard")
    print(f"  Manual mint: {base}/mint/new")
    print(f"  Health:      {base}/health")
    print(f"  API:         POST {base}/api/mint/session")
    _cap_base = os.environ.get("MEMEMAGE_CAPTURE_BASE")
    if _cap_base:
        print(f"  Phone GPS:   {_cap_base}/mint/<token>  (Tailscale)")
    print(bar)
    print("  Press Ctrl+C to stop.")

    if open_browser:
        # Record the live instance so the next launch focuses it instead
        # of starting a second server (see _desktop_already_running).
        _write_desktop_lock(port, scheme)
        # Desktop/local mode — pop the dashboard once the socket is
        # accepting. A short delay lets serve_forever() get into its
        # accept loop first (the thread is daemon so it never blocks exit).
        open_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
        token = _load_mint_token()
        dash_path = f"/{token}" if token else "/dashboard"
        dash_url = f"{scheme}://{open_host}:{port}{dash_path}"
        print(f"  Opening {dash_url} ...")

        def _open_dash():
            import time as _t
            import webbrowser as _wb
            _t.sleep(1.0)
            try:
                _wb.open(dash_url)
            except Exception:
                pass
        import threading as _thr
        _thr.Thread(target=_open_dash, daemon=True).start()

    # Serve any extra (Tailscale HTTPS) sockets in daemon threads; the main
    # loopback server stays on the main thread so Ctrl+C lands here.
    for _es in extra_servers:
        import threading as _thr_es
        _thr_es.Thread(target=_es.serve_forever, daemon=True).start()

    # Hand the live server to a caller that wants to drive its lifecycle
    # (the tray app's Quit calls server.shutdown() from the main thread
    # while serve_forever runs here on a background thread).
    if on_ready is not None:
        try:
            on_ready(server)
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        try:
            server.server_close()
        except Exception:
            pass
        for _es in extra_servers:
            try:
                _es.shutdown()
                _es.server_close()
            except Exception:
                pass
        if open_browser:
            _clear_desktop_lock()


# Default TLS cert paths — configurable via ~/.mememage/server.json or --cert/--key
_CERT_DIR = Path.home() / ".mememage" / "certs"


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Mememage mint server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=None,
                        help="Listen port. Overrides server.json 'port'; "
                             "default 8443 if neither is set.")
    parser.add_argument("--cert", default=None, help="TLS cert path (default: Tailscale certs)")
    parser.add_argument("--key", default=None, help="TLS key path (default: Tailscale certs)")
    parser.add_argument("--no-tls", action="store_true", help="Disable TLS (HTTP only)")
    parser.add_argument("--local", action="store_true",
                        help="Desktop mode: bind 127.0.0.1 over HTTP (localhost is a "
                             "secure context so GPS still works) and open the dashboard.")
    args = parser.parse_args()

    if args.local:
        args.host = "127.0.0.1"
        args.no_tls = True
        if args.port is None:
            args.port = _find_free_port("127.0.0.1", 8765)

    config = _get_server_config()
    # Port resolution: explicit --port wins (ad-hoc runs); else server.json
    # 'port' (the dashboard-editable source of truth); else 8443.
    port = args.port if args.port is not None else config.get("port")
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 8443
    if not (1 <= port <= 65535):
        port = 8443

    certfile = args.cert
    keyfile = args.key

    if not args.no_tls and not certfile:
        # Auto-detect certs: config → ~/.mememage/certs/ → HTTP fallback
        config_cert = config.get("cert")
        config_key = config.get("key")
        if config_cert and Path(config_cert).exists():
            certfile = config_cert
            keyfile = config_key
            print(f"Using TLS certs from server.json")
        else:
            # Look for any .crt/.key pair in ~/.mememage/certs/
            certs = list(_CERT_DIR.glob("*.crt")) if _CERT_DIR.exists() else []
            if certs:
                certfile = str(certs[0])
                keyfile = str(certs[0].with_suffix(".key"))
                if Path(keyfile).exists():
                    print(f"Using TLS certs from {_CERT_DIR}")
                else:
                    certfile = None
                    keyfile = None
            if not certfile:
                print("No TLS certs found. Running HTTP only (geolocation won't work on phone).")
                print(f"Place certs in: {_CERT_DIR}/")

    run_server(args.host, port, certfile, keyfile, open_browser=args.local)
