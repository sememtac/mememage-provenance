"""HTTP push channel — generic PUT to any server you control.

The escape hatch from third-party hosts. Configure a base URL + a
bearer token (or another auth header), and every soul is PUT to
``{base_url}/{identifier}.soul``. Pair it with nginx + WebDAV,
Caddy with file_server, an S3-compatible bucket, or anything that
accepts ``PUT`` with ``Authorization``.

If the receiving server stores the file at the same URL pattern,
the returned URL is fetchable by the decoder. CORS is the host's
responsibility — for browser fetch, the server must send
``Access-Control-Allow-Origin``. The decoder degrades gracefully:
if the body is unreachable but the bar carries identifier +
content_hash, the user can still verify By Soul (drop a local
``.soul`` file alongside the image).
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from mememage.channels import Channel, register
from mememage.net import urlopen_with_retry

log = logging.getLogger(__name__)


@register
class HttpPushChannel(Channel):
    TYPE = "http_push"
    DISPLAY_NAME = "HTTP push (self-hosted)"
    CREDENTIAL_FIELDS = [
        {
            "name": "bearer_token",
            "label": "Bearer token",
            "env_var": "HTTP_PUSH_TOKEN",
            "secret": True,
            "help": "Sent as Authorization: Bearer <token>. Leave empty for unauthenticated PUT (not recommended).",
        },
    ]
    CONFIG_FIELDS = [
        {
            "name": "base_url",
            "label": "Base URL",
            "default": "",
            "help": "PUT target: souls land at {base_url}/{identifier}.soul. Another mememage server accepts pushes at https://<host>:<port>/api/souls",
        },
        {
            "name": "public_url",
            "label": "Public URL (read face)",
            "default": "",
            "help": "Optional. The clean public address souls are READ from — e.g. https://souls.example.com (no /api/souls path; nginx maps it). When set, soul links use this instead of base_url. Leave empty to fall back to the souls domain (self-push) or base_url.",
        },
        {
            "name": "content_type",
            "label": "Content-Type",
            "default": "application/json",
            "help": "Override only if your server is fussy about MIME.",
        },
        {
            "name": "extra_header_name",
            "label": "Extra header (name)",
            "default": "",
            "help": "Optional. Useful for custom auth schemes (e.g. X-Api-Key).",
        },
        {
            "name": "extra_header_env",
            "label": "Extra header (env var for value)",
            "default": "",
            "help": "Reads the value from this .env key so the secret stays out of channels.json.",
        },
        {
            "name": "accept_self_signed",
            "label": "Accept self-signed cert",
            "default": False,
            "help": "Check this when pushing to another mememage server that uses the bundled tls helper (or any peer with a self-signed cert). Authentication still rides the bearer token; this only relaxes cert-chain verification.",
        },
        {
            "name": "push_image",
            "label": "Blast the image too (show in this surface's feed)",
            "default": False,
            "help": "Also send the full conceived image to this surface, so it appears in that surface's public feed at full quality — not just the tiny soul thumbnail. Use it to make a chosen host (e.g. your public site) a gallery of what you mint elsewhere. Bounded by the surface's catalog limit, like its own mints. Leave off for soul-only distribution.",
        },
    ]

    def is_configured(self) -> bool:
        # Bearer token is technically optional (some self-hosted setups
        # are LAN-only behind a firewall), so override the default
        # check: a base_url is the actual requirement.
        if not self.config.get("base_url"):
            return False
        return True

    def _resolve_bearer(self) -> str | None:
        """Bearer-token resolution chain:

          1. ``channels.credentials.bearer_token`` (per-channel override) —
             always wins, used when this channel needs a specific token
             different from any env default.
          2. Self-push detection: if the channel's ``base_url`` host
             matches the running server's bound host (from
             ``server.json``), use ``MINT_API_TOKEN``. Rotating the
             dashboard token then "just works" for self-publishing
             without a second token to maintain.
          3. ``HTTP_PUSH_TOKEN`` env var — deployment-level fallback
             for peer pushes where every http_push channel hits the
             same peer surface.
          4. ``MINT_API_TOKEN`` env var — last-resort fallback.

        Returns None when nothing resolves; the upload then fires
        unauthenticated, fine for LAN-only deployments behind a
        firewall but will 401 against any mememage peer with
        ``MINT_API_TOKEN`` configured.
        """
        # 1. Explicit per-channel credential (channels.credentials.bearer_token)
        explicit = self.credentials.get("bearer_token")
        if explicit:
            return explicit

        import os as _os
        try:
            from mememage.config import _load_dotenv
            _load_dotenv()
        except Exception:
            pass

        # 2. Self-push: base_url host matches our server's bound host.
        if self._is_self_push():
            mint_token = _os.environ.get("MINT_API_TOKEN")
            if mint_token:
                return mint_token

        # 3. HTTP_PUSH_TOKEN (peer-push fallback)
        legacy = _os.environ.get("HTTP_PUSH_TOKEN")
        if legacy:
            return legacy

        # 4. MINT_API_TOKEN (last resort — most peers share auth model)
        return _os.environ.get("MINT_API_TOKEN")

    def _is_self_push(self) -> bool:
        """True when ``base_url``'s host matches our own server's bound
        host. Checks three signals (any is sufficient):

          1. ``MEMEMAGE_SELF_HOST`` env var — set by the server on
             startup to the host:port it's bound on (or outbound IP
             via socket trick). Most reliable.
          2. Localhost forms (``localhost``, ``127.0.0.1``, ``::1``).
          3. ``server.json``'s ``domain`` field — last resort.

        Keeps the channel layer pure: doesn't import the server module.
        """
        from urllib.parse import urlsplit
        import os as _os
        try:
            target = urlsplit(self.config.get("base_url") or "").hostname or ""
        except Exception:
            return False
        if not target:
            return False
        target_lower = target.lower()
        # 1. Server-set self-host env var (comma-separated list of
        # host[:port] entries the server bound on).
        self_hosts = (_os.environ.get("MEMEMAGE_SELF_HOST") or "").strip().lower()
        if self_hosts:
            for entry in self_hosts.split(","):
                entry = entry.strip().split(":")[0]
                if entry and entry == target_lower:
                    return True
        # 2. Localhost forms.
        if target_lower in ("localhost", "127.0.0.1", "::1"):
            return True
        # 3. server.json domain field.
        try:
            import json as _json
            from pathlib import Path as _Path
            server_json = _Path("~/.mememage/server.json").expanduser()
            if server_json.exists():
                cfg = _json.loads(server_json.read_text(encoding="utf-8"))
                bound = (cfg.get("domain") or "").strip().lower()
                if bound and bound.split(":")[0] == target_lower:
                    return True
        except Exception:
            pass
        return False

    def _read_base(self) -> str:
        """Base URL for the soul's PUBLIC read / canonical link.

        PUT always targets ``base_url`` (the write face). The read link the
        viewer follows — and what the conception strip displays — prefers a
        cleaner address, in order:

          1. ``config.public_url`` — explicit clean read face (any peer).
          2. ``server.json:souls_domain`` — for self-push only, the box's
             clean souls face that ``vps-setup --souls`` stood up. Auto-wired,
             no per-channel config needed.
          3. ``server.json:domain`` — for self-push only, when there's no
             dedicated souls face but the box advertises a domain (it reverse-
             proxies ``/api/souls``). Surfaces the domain instead of a raw
             ``IP:port`` so "users with a domain" see a clean link.
          4. ``base_url`` — fallback (the raw host the soul was PUT to).

        Trailing slash stripped.
        """
        explicit = (self.config.get("public_url") or "").strip().rstrip("/")
        if explicit:
            return explicit
        base = (self.config.get("base_url") or "").rstrip("/")
        if self._is_self_push():
            souls = self._server_souls_domain()
            if souls:
                return f"https://{souls}".rstrip("/")
            # No dedicated souls face — but if the box has a domain and the
            # write target is a bare IP, advertise the domain (it proxies the
            # same /api/souls path) rather than the IP:port.
            dom = self._server_field("domain")
            if dom:
                from urllib.parse import urlsplit, urlunsplit
                import re as _re
                sp = urlsplit(base)
                if sp.hostname and _re.match(r"^\d{1,3}(\.\d{1,3}){3}$", sp.hostname):
                    return urlunsplit(("https", dom, sp.path, "", "")).rstrip("/")
        return base

    @staticmethod
    def _server_field(key: str) -> str:
        """Read a top-level string field from ~/.mememage/server.json (e.g.
        ``souls_domain`` or ``domain``). Empty string when absent/unreadable."""
        try:
            import json as _json
            from pathlib import Path as _Path
            p = _Path("~/.mememage/server.json").expanduser()
            if p.exists():
                return (_json.loads(p.read_text(encoding="utf-8")).get(key) or "").strip()
        except Exception:
            pass
        return ""

    @staticmethod
    def _server_souls_domain() -> str:
        """The box's public souls domain from server.json, if set (vps-setup
        --souls writes it). Empty string when absent/unreadable."""
        return HttpPushChannel._server_field("souls_domain")

    def display_surface(self) -> str:
        """Show the host the soul is reachable at, not the internal slug.
        ``localhost``/loopback render as ``localhost``; a bare IP falls back
        to the friendly name (we don't surface raw IPs); a domain shows
        verbatim."""
        from urllib.parse import urlsplit
        host = (urlsplit(self._read_base()).hostname or "").lower()
        if host in ("localhost", "127.0.0.1", "::1"):
            return "localhost"
        if not host:
            return self.name or self.id
        import ipaddress
        try:
            ipaddress.ip_address(host)
            return self.name or self.id  # bare IP — don't surface it
        except ValueError:
            return host  # a real domain

    @staticmethod
    def _local_store_dir():
        """The flat soul store this server reads/serves from. Kept inline (no
        core import) so the channel layer stays pure."""
        import os
        from pathlib import Path
        return Path(os.path.expanduser("~/.mememage/received"))

    def upload(self, identifier: str, soul_bytes: bytes,
               image_path: str | None = None) -> str:
        base_url = (self.config.get("base_url") or "").rstrip("/")
        if not base_url:
            raise RuntimeError("http_push channel needs a base_url in config")

        # Self-push: the soul is already on disk in the local flat store (the
        # mint pipeline writes ~/.mememage/received directly), which is the
        # same dir this server serves at the souls face. Don't HTTP-PUT to
        # ourselves — that loopback was the source of every self-push wedge.
        # Just hand back the canonical read URL.
        if self._is_self_push():
            return f"{self._read_base()}/{identifier}.soul"

        # PUT to the write face (base_url) — self-push detection, keychain
        # derivation, and peer auth all key off it, so it stays put.
        put_url = f"{base_url}/{identifier}.soul"
        content_type = self.config.get("content_type") or "application/json"

        req = urllib.request.Request(put_url, data=soul_bytes, method="PUT")
        req.add_header("Content-Type", content_type)
        self._apply_auth(req)

        # Self-signed peers are a first-class deployment pattern
        # (mememage's own ``mememage tls --self-signed`` helper ships
        # one). When the user has opted in via ``accept_self_signed``
        # we build a no-verify SSL context and pass it down to urlopen.
        # Authentication still rides the bearer token; we only relax
        # the cert chain check.
        #
        # Legacy migration: older configs stored the inverse flag
        # ``verify_tls`` (default True). ``verify_tls=False`` means
        # the same as ``accept_self_signed=True``. Read both so old
        # channels.json files keep working without manual edits.
        ctx = self._ssl_context()

        try:
            urlopen_with_retry(req, context=ctx)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"HTTP push upload failed (HTTP {e.code}): {body[:500]}"
            ) from e

        # Optionally blast the full conceived image too, so a surface that
        # opts in (push_image) shows this conception in its public feed at full
        # quality — not just the 80px soul thumbnail. Best-effort: the soul has
        # already landed (the carrier), and the feed image is a product nicety,
        # so a failure here (size cap, transient network) must never fail the
        # mint — log and move on. Self-push returned far above, so a host never
        # blasts an image to itself (its own session already feeds the image).
        if self.config.get("push_image") and image_path:
            try:
                self._put_image(base_url, identifier, image_path, ctx)
            except Exception as e:
                log.warning("http_push: image blast for %s failed "
                            "(soul landed fine): %s", identifier, e)

        # Canonical link the viewer follows — the clean read face, not the
        # raw PUT host. (e.g. https://souls.example.com/<id>.soul instead of
        # http://10.0.0.5:8443/api/souls/<id>.soul)
        return f"{self._read_base()}/{identifier}.soul"

    def _apply_auth(self, req):
        """Apply the bearer + optional extra header to an outbound request.

        Bearer rides ``Authorization: Bearer <token>``; the optional second
        header (X-Api-Key etc.) takes its name from plaintext config and its
        value from a .env key so secrets never land in channels.json. Shared by
        the soul PUT and the image PUT."""
        bearer = self._resolve_bearer()
        if bearer:
            req.add_header("Authorization", f"Bearer {bearer}")
        extra_name = (self.config.get("extra_header_name") or "").strip()
        extra_env = (self.config.get("extra_header_env") or "").strip()
        if extra_name and extra_env:
            import os as _os
            extra_val = _os.environ.get(extra_env)
            if extra_val:
                req.add_header(extra_name, extra_val)

    def _put_image(self, base_url, identifier, image_path, ctx):
        """PUT the full conceived image to the surface's feed-image endpoint
        ({base_url}/{identifier}.png). The receiving server stores it and shows
        the conception in its public feed at full quality. Same auth + TLS
        posture as the soul PUT."""
        with open(image_path, "rb") as f:
            data = f.read()
        put_url = f"{base_url}/{identifier}.png"
        req = urllib.request.Request(put_url, data=data, method="PUT")
        req.add_header("Content-Type", "image/png")
        self._apply_auth(req)
        try:
            urlopen_with_retry(req, context=ctx)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"image PUT failed (HTTP {e.code}): {body[:300]}") from e

    def _ssl_context(self):
        """Build the urllib SSL context for this peer.

        Returns ``None`` for normal verified TLS, or a no-verify
        context when the channel opted into ``accept_self_signed``
        (legacy inverse: ``verify_tls=False``). Authentication always
        rides the bearer token; only the cert-chain check is relaxed.
        Shared by ``upload()`` (PUT) and ``exists()`` (HEAD probe).
        """
        accept_self_signed = self.config.get("accept_self_signed")
        if accept_self_signed is None:
            legacy = self.config.get("verify_tls")
            accept_self_signed = (legacy is False)
        if not accept_self_signed:
            return None
        import ssl as _ssl
        ctx = _ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
        log.warning(
            "http_push channel %s: TLS verification relaxed for "
            "self-signed peer — transport is opaque to MITM. "
            "Authentication still rides the bearer token.", self.id,
        )
        return ctx

    def exists(self, identifier: str) -> bool:
        """Probe the read face for an existing soul at ``identifier``.

        ``GET {read_base}/{identifier}.soul`` — 2xx = taken, 404 = free.
        GET (not HEAD) on purpose: a read face may be anything the user
        controls (mememage's own stdlib server has no ``do_HEAD`` and
        501s; nginx file_server, S3, Caddy all vary), but GET is
        universal, and souls are tiny so the wasted body is negligible.
        No tombstones on a server you control: a deleted soul genuinely
        frees the slot (unlike IA), so this reports only live slots.
        Consulted at identifier assignment only — the overwrite-on-PUT
        patch reblast is never gated by it. A bearer token rides along
        if configured (read faces may be auth-gated too).
        """
        # Self-push: check the local store directly — no loopback GET.
        if self._is_self_push():
            return (self._local_store_dir() / f"{identifier}.soul").exists()
        read_url = f"{self._read_base()}/{identifier}.soul"
        req = urllib.request.Request(read_url, method="GET")
        bearer = self._resolve_bearer()
        if bearer:
            req.add_header("Authorization", f"Bearer {bearer}")
        try:
            urlopen_with_retry(req, context=self._ssl_context())
            return True  # 2xx → a soul already lives here
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False  # never minted here → slot free
            raise

    def test(self) -> dict:
        """Live reachability + auth probe for the dashboard Test button. GETs the
        write face (``base_url``, the soul-receive endpoint on a mememage peer),
        writing nothing. Returns ``{ok, detail}``. Self-push short-circuits.

        The probe runs the **unauthenticated** request FIRST, on purpose: just
        seeing a 200 with the token proves nothing, because a misconfigured
        ``base_url`` (e.g. the apex ``https://host`` instead of
        ``https://host/api/souls``) hits a PUBLIC page that answers 200 for
        everyone — the token is never checked. A real soul endpoint rejects the
        no-token GET (401/403); the token is then proven by a second GET that
        succeeds *with* it. So "token accepted" is only ever reported when the
        endpoint actually required the token.
        """
        if self._is_self_push():
            return {"ok": True, "detail": "Self-push — souls land in the local store directly (no network hop)."}
        base = (self.config.get("base_url") or "").rstrip("/")
        if not base:
            return {"ok": False, "detail": "No base URL configured."}
        bearer = self._resolve_bearer()

        # 1. Unauthenticated probe — is auth actually enforced here?
        try:
            anon_code, anon_body = self._probe_get(base, None)
        except Exception as e:
            return {"ok": False, "detail": f"Could not reach {base}: {e}"}

        if anon_code == 404:
            return {"ok": False, "detail": f"Reached the host, but {base} 404'd — the base URL probably needs the soul path (…/api/souls)."}

        if anon_code in (401, 403):
            # Good: auth is enforced. Prove our token is the right one.
            if not bearer:
                return {"ok": False, "detail": f"{base} requires a token, but none is configured — set the bearer token."}
            try:
                code, body = self._probe_get(base, bearer)
            except Exception as e:
                return {"ok": False, "detail": f"Could not reach {base}: {e}"}
            if code in (401, 403):
                return {"ok": False, "detail": f"Auth is enforced but the token was rejected ({code}) — check the bearer token."}
            if 200 <= code < 300:
                if self._looks_like_soul_list(body):
                    return {"ok": True, "detail": f"Confirmed — {base} is a mememage soul endpoint and the token was accepted."}
                return {"ok": True, "detail": f"{base} answered and accepted the token."}
            return {"ok": code < 500, "detail": f"Reached the host (HTTP {code}) with the token."}

        if 200 <= anon_code < 300:
            # The endpoint answered an UNAUTHENTICATED GET — the token was never
            # checked. Either it's an open server, or (far more often) base_url
            # points at a public page, not the soul endpoint.
            if self._looks_like_soul_list(anon_body):
                return {"ok": True, "detail": f"Reachable, but {base} isn't requiring a token — anyone can push here. Fine behind a firewall; set a token on the receiver for a public host."}
            return {"ok": False, "detail": f"Reached the host, but {base} isn't a mememage soul endpoint — it answered without auth and returned no soul list. If this is a mememage server, the base URL probably needs the /api/souls path."}

        return {"ok": anon_code < 500, "detail": f"Reached the host (HTTP {anon_code})."}

    def _probe_get(self, url: str, bearer: str | None):
        """GET ``url`` (optionally with a bearer), returning ``(status, body)``.

        ``urlopen_with_retry`` returns the response **body bytes** on any 2xx
        and raises ``HTTPError`` otherwise, so a clean read maps to ``(200,
        text)`` (the exact 2xx sub-code doesn't matter here) and an HTTP error
        maps to ``(code, "")``. Connection-level failures propagate. The body
        is decoded so the caller can sniff the soul-list JSON shape."""
        req = urllib.request.Request(url, method="GET")
        if bearer:
            req.add_header("Authorization", f"Bearer {bearer}")
        try:
            raw = urlopen_with_retry(req, context=self._ssl_context())
        except urllib.error.HTTPError as e:
            return e.code, ""
        if isinstance(raw, (bytes, bytearray)):
            return 200, raw.decode("utf-8", "replace")
        return 200, raw if isinstance(raw, str) else ""

    @staticmethod
    def _looks_like_soul_list(body: str) -> bool:
        """True when ``body`` parses as the soul-receive list JSON
        (``{"items": [...], "count": N}``) — the tell that we reached an
        actual mememage soul endpoint and not some public HTML page."""
        if not body:
            return False
        try:
            data = json.loads(body)
        except Exception:
            return False
        return isinstance(data, dict) and ("items" in data or "count" in data)

    def upload_keychain(self, chain_id: str, filename: str,
                        record_bytes: bytes) -> str:
        """Mirror a keychain record to the peer.

        Derives the keychain URL from the soul base_url: if it ends in
        ``/api/souls`` (the convention for mememage's own receive
        endpoint), swap to ``/api/keychain`` and append
        ``{chain_id}/{filename}``. Otherwise, fall back to writing under
        ``{base_url}/../keychain/{chain_id}/{filename}`` which works for
        any peer that mirrors the layout.
        """
        base = (self.config.get("base_url") or "").rstrip("/")
        if not base:
            raise RuntimeError("http_push channel needs a base_url in config")
        if base.endswith("/api/souls"):
            keychain_base = base[: -len("/api/souls")] + "/api/keychain"
        else:
            # Best-effort: assume base_url is the souls dir and keychain
            # is a sibling. User can override by writing channels.json.
            keychain_base = base.rsplit("/", 1)[0] + "/keychain"
        keychain_url = f"{keychain_base}/{chain_id}/{filename}"

        content_type = self.config.get("content_type") or "application/json"
        req = urllib.request.Request(keychain_url, data=record_bytes, method="PUT")
        req.add_header("Content-Type", content_type)

        bearer = self._resolve_bearer()
        if bearer:
            req.add_header("Authorization", f"Bearer {bearer}")

        extra_name = (self.config.get("extra_header_name") or "").strip()
        extra_env = (self.config.get("extra_header_env") or "").strip()
        if extra_name and extra_env:
            import os as _os
            extra_val = _os.environ.get(extra_env)
            if extra_val:
                req.add_header(extra_name, extra_val)

        accept_self_signed = self.config.get("accept_self_signed")
        if accept_self_signed is None:
            legacy = self.config.get("verify_tls")
            accept_self_signed = (legacy is False)
        ctx = None
        if accept_self_signed:
            import ssl as _ssl
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE

        try:
            urlopen_with_retry(req, context=ctx)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"HTTP push keychain upload failed (HTTP {e.code}): {body[:500]}"
            ) from e

        return keychain_url

    # ---- cleanup surface --------------------------------------------------
    # http_push targets a server you control. When that server is a
    # mememage mint server it exposes /api/souls/ (GET = list, DELETE
    # per-file). search() and purge() use those endpoints. No hide() —
    # plain HTTP storage has no noindex equivalent; calling hide on
    # this channel returns NotImplementedError via the base class.

    def _http_ctx_and_headers(self):
        """Return (ssl_context, request_headers) shared by search/purge.
        Mirrors upload()'s TLS + auth handling so the cleanup surface
        works against the same self-signed peer + bearer config that
        already works for uploads."""
        headers = {}
        bearer = self._resolve_bearer()
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        extra_name = (self.config.get("extra_header_name") or "").strip()
        extra_env = (self.config.get("extra_header_env") or "").strip()
        if extra_name and extra_env:
            import os as _os
            v = _os.environ.get(extra_env)
            if v:
                headers[extra_name] = v
        accept_self_signed = self.config.get("accept_self_signed")
        if accept_self_signed is None:
            accept_self_signed = (self.config.get("verify_tls") is False)
        ctx = None
        if accept_self_signed:
            import ssl as _ssl
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
        return ctx, headers

    def search(self, *, pattern: str = "mememage-*", limit: int = 200,
               **_filters) -> list[dict]:
        """List souls the peer has received. Calls GET ``{base_url}/``
        with ``?pattern=...&limit=...``. The receiving server (mememage's
        own /api/souls/) returns ``{items: [{identifier, url, size, date}, ...]}``.
        Non-mememage receivers without a listing endpoint return 404 →
        we surface it as an empty list with a clear log line so the
        operator knows the channel doesn't support listing.
        """
        base_url = (self.config.get("base_url") or "").rstrip("/")
        if not base_url:
            raise RuntimeError("http_push channel needs a base_url in config")
        import urllib.parse
        list_url = (
            f"{base_url}/?"
            + urllib.parse.urlencode({"pattern": pattern, "limit": int(limit)})
        )
        ctx, headers = self._http_ctx_and_headers()
        req = urllib.request.Request(list_url, method="GET")
        for k, v in headers.items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                import json as _json
                data = _json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                log.warning(
                    "http_push channel %s: peer at %s has no listing "
                    "endpoint (404). Cleanup search disabled for this peer.",
                    self.id, base_url,
                )
                return []
            body = e.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(
                f"HTTP push listing failed (HTTP {e.code}): {body}"
            ) from e
        return list(data.get("items") or [])

    def purge(self, identifier: str) -> dict:
        """DELETE ``{base_url}/{identifier}.soul`` and ``.json`` (mirror).
        Returns the standard purge shape so the dashboard surfaces
        per-item progress consistently with IA's purge."""
        base_url = (self.config.get("base_url") or "").rstrip("/")
        if not base_url:
            return {"ok": False, "error": "no base_url configured",
                    "deleted": 0, "failed": 0, "files": 0, "errors": []}
        # Self-push: delete from the local store directly — no loopback DELETE.
        if self._is_self_push():
            store = self._local_store_dir()
            deleted, errors = 0, []
            for p in list(store.glob(f"{identifier}.soul")) + list(store.glob(f"{identifier}.*.soul")):
                try:
                    p.unlink()
                    deleted += 1
                except OSError as e:
                    errors.append(f"{p.name}: {e}")
            return {"ok": not errors, "deleted": deleted, "failed": len(errors),
                    "files": deleted, "errors": errors}
        ctx, headers = self._http_ctx_and_headers()
        deleted = 0
        failed = 0
        errors = []
        # Try both extensions — mirrors the receive PUT, which can land
        # under either. .json is a CORS-friendly mirror IA-style flows
        # use; the server cleans up both on a single DELETE call, but
        # if a peer's implementation differs we attack each individually.
        for ext in ("soul", "json"):
            url = f"{base_url}/{identifier}.{ext}"
            req = urllib.request.Request(url, method="DELETE")
            for k, v in headers.items():
                req.add_header(k, v)
            try:
                urllib.request.urlopen(req, timeout=30, context=ctx)
                deleted += 1
            except urllib.error.HTTPError as e:
                # 404 is benign — the mirror may not have existed.
                if e.code == 404:
                    continue
                body = e.read().decode("utf-8", errors="replace")[:200]
                failed += 1
                errors.append(f"{identifier}.{ext}: HTTP {e.code} — {body}")
            except Exception as e:
                failed += 1
                errors.append(f"{identifier}.{ext}: {e}")
        return {
            "ok": failed == 0,
            "deleted": deleted,
            "failed": failed,
            "files": deleted + failed,
            "errors": errors,
        }
