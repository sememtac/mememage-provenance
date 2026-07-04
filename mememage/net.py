"""Shared network helpers — retry with exponential backoff."""

import json
import time
import urllib.error
import urllib.request

# Transient HTTP codes worth retrying
_RETRYABLE = {429, 500, 502, 503, 504}

# Default retry config
MAX_RETRIES = 3
BASE_DELAY = 1.0  # seconds
BACKOFF_FACTOR = 2.0


def urlopen_with_retry(req, *, max_retries=MAX_RETRIES, base_delay=BASE_DELAY,
                        timeout=60, context=None):
    """Execute a urllib Request with exponential backoff on transient failures.

    Args:
        context: Optional ``ssl.SSLContext``. When set, used for HTTPS
            verification — needed by channels pushing to self-signed
            peers (the http_push channel passes a no-verify context
            when its ``verify_tls`` config is False).

    Returns the response body as bytes.
    Raises RuntimeError on permanent failure.
    """
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
                time.sleep(base_delay * (BACKOFF_FACTOR ** attempt))
                continue
            raise
        except (urllib.error.URLError, OSError) as e:
            # Network-level failures (DNS, connection refused, timeout)
            if attempt < max_retries:
                last_exc = e
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
