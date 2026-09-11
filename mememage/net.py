"""Shared network helpers — retry with exponential backoff, plus a robust
default TLS context so HTTPS validation doesn't depend on a stale OS trust store."""

import json
import logging
import ssl
import time
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

# Transient HTTP codes worth retrying
_RETRYABLE = {429, 500, 502, 503, 504}

# Default retry config
MAX_RETRIES = 3
BASE_DELAY = 1.0  # seconds
BACKOFF_FACTOR = 2.0

_HTTPS_CTX = None


def default_https_context():
    """A TLS context that validates HTTPS against certifi's current CA bundle.

    Python validates HTTPS against the OS trust store; on Windows especially, a
    fresh or un-updated machine has a stale store, and OpenSSL path-builds
    through an old expired root — so a perfectly valid Let's Encrypt cert fails
    as "certificate has expired" and every mint blast / record fetch dies with a
    cryptic error the user can't fix without terminal surgery. certifi ships
    Mozilla's current roots (a dependency of the mint/desktop extras), so
    validating against it works out of the box on any machine, no env var.

    Verification is never disabled here — a self-signed peer passes its own
    ``CERT_NONE`` context explicitly and never routes through this. Falls back to
    urllib's default (return ``None``) if certifi somehow isn't importable, so
    there's no regression. Cached: built once, reused."""
    global _HTTPS_CTX
    if _HTTPS_CTX is None:
        try:
            import certifi
            _HTTPS_CTX = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            _HTTPS_CTX = False   # sentinel → fall back to urllib's default context
    return _HTTPS_CTX or None


def _log_retry(req, attempt, max_retries, reason):
    """Record that a request is about to be re-sent.

    Retries used to be silent, which hid a real failure mode: a PUT that
    SUCCEEDS server-side but reports a timeout or a 503 to the client gets
    re-sent, and the second attempt can fail for a reason that only makes sense
    if you know the first one happened. On the Internet Archive that surfaces as
    409 BucketAlreadyExists — the retry colliding with the item its own first
    attempt created. Log the method and URL so the sequence is readable
    afterward. WARNING level, because a retried write is not routine.
    """
    try:
        log.warning("Retry %d/%d for %s %s after %s",
                    attempt + 1, max_retries, req.get_method(),
                    req.full_url, reason)
    except Exception:
        pass   # logging must never break a request


def urlopen_with_retry(req, *, max_retries=MAX_RETRIES, base_delay=BASE_DELAY,
                        timeout=60, context=None):
    """Execute a urllib Request with exponential backoff on transient failures.

    Args:
        context: Optional ``ssl.SSLContext``. When set, used for HTTPS
            verification — needed by channels pushing to self-signed
            peers (the http_push channel passes a no-verify context
            when its ``verify_tls`` config is False). When ``None`` (the
            default), HTTPS validates against certifi's current roots via
            :func:`default_https_context` rather than the (possibly stale)
            OS trust store.

    Returns the response body as bytes.
    Raises RuntimeError on permanent failure.
    """
    if context is None:
        context = default_https_context()
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            kwargs = {"timeout": timeout}
            if context is not None:
                kwargs["context"] = context
            with urllib.request.urlopen(req, **kwargs) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code in _RETRYABLE and attempt < max_retries:
                last_exc = e
                _log_retry(req, attempt, max_retries, f"HTTP {e.code}")
                time.sleep(base_delay * (BACKOFF_FACTOR ** attempt))
                continue
            raise
        except (urllib.error.URLError, OSError) as e:
            # Network-level failures (DNS, connection refused, timeout)
            if attempt < max_retries:
                last_exc = e
                _log_retry(req, attempt, max_retries, str(e) or type(e).__name__)
                time.sleep(base_delay * (BACKOFF_FACTOR ** attempt))
                continue
            raise RuntimeError(
                f"Network request failed after {max_retries + 1} attempts: {e}"
            ) from e
    # Should not reach here, but just in case
    raise RuntimeError(f"Request failed after {max_retries + 1} attempts") from last_exc


def fetch_json(url, *, max_retries=MAX_RETRIES, timeout=30):
    """GET a URL, parse JSON, return dict. Returns None on 404."""
    req = urllib.request.Request(url)
    try:
        body = urlopen_with_retry(req, max_retries=max_retries, timeout=timeout)
        return json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise RuntimeError(
            f"HTTP {e.code} fetching {url}"
        ) from e
