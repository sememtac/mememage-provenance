"""TLS certificate helpers.

For a fresh-server install the user needs a TLS cert *somewhere*, or
the phone-GPS flow doesn't work (Safari refuses geolocation over HTTP
on non-localhost) and the validator's Web Crypto calls degrade. Real
certs from Let's Encrypt need a domain name; for IP-only deployments
and quick test setups, a self-signed cert covers the secure-context
gate at the cost of a one-time browser warning.

Run via the CLI:

    mememage tls --self-signed                      # auto-detect
    mememage tls --self-signed --hostname my.box    # for a domain
    mememage tls --self-signed --ip 203.0.113.10 # for a raw IP

Writes a 10-year RSA-2048 cert + key to ``~/.mememage/certs/`` and
updates ``~/.mememage/server.json`` to point at them.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)


CERT_DIR = Path("~/.mememage/certs").expanduser()
SERVER_CONFIG = Path("~/.mememage/server.json").expanduser()


def _detect_host() -> tuple[str, bool]:
    """Best-guess hostname or IP for the current machine.

    Returns ``(value, is_ip)``. Prefers the hostname (so the cert
    Common Name reads cleanly) but falls back to the primary IP if
    the hostname resolves to nothing useful.
    """
    try:
        hostname = socket.gethostname()
        if hostname and hostname not in ("localhost", "localhost.localdomain"):
            return hostname, False
    except OSError:
        pass
    # IP fallback — connect to a public anycast to learn our outbound
    # interface address without actually sending traffic.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0], True
    except OSError:
        return "localhost", False


def generate_self_signed(
    hostname: str | None = None,
    ip_address: str | None = None,
    days_valid: int = 3650,
    cert_dir: Path | None = None,
) -> dict:
    """Generate a self-signed RSA-2048 cert for the given hostname / IP.

    The Subject Alternative Name extension covers both forms when both
    are supplied, so the cert is valid for ``https://my-host:8443/``
    AND ``https://10.0.0.5:8443/`` simultaneously. Modern browsers
    enforce the SAN, not the deprecated Common Name; we set both to
    keep older clients happy.

    Returns a dict with the cert + key paths and the fingerprint.
    Raises RuntimeError if the cryptography library is unavailable.
    """
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError as e:
        raise RuntimeError(
            "TLS cert generation requires the cryptography library. "
            "Install with: pip install mememage[sign]"
        ) from e

    # Auto-detect if nothing was passed
    if not hostname and not ip_address:
        detected, is_ip = _detect_host()
        if is_ip:
            ip_address = detected
        else:
            hostname = detected

    # Build the Subject Alternative Name list. Include both hostname
    # forms (with + without a leading wildcard) for the common cases.
    sans = []
    if hostname:
        sans.append(x509.DNSName(hostname))
        # `localhost` is the standard development name — always include
        # it so the same cert covers loopback access.
        if hostname != "localhost":
            sans.append(x509.DNSName("localhost"))
    if ip_address:
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(ip_address)))
        except ValueError as e:
            raise RuntimeError(f"Invalid IP address {ip_address!r}: {e}")
    # Always include the loopback IPs so curl on the host works too.
    sans.append(x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")))
    sans.append(x509.IPAddress(ipaddress.IPv6Address("::1")))

    # Common Name = first DNS name if present, else first IP
    if hostname:
        common_name = hostname
    elif ip_address:
        common_name = ip_address
    else:
        common_name = "localhost"

    # Generate key + cert
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    subject_issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Mememage (self-signed)"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject_issuer)
        .issuer_name(subject_issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))  # avoid clock-skew rejections
        .not_valid_after(now + timedelta(days=days_valid))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None), critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(private_key=key, algorithm=hashes.SHA256())
    )

    # Compute fingerprint for the user (lets them verify it later)
    fingerprint_bytes = cert.fingerprint(hashes.SHA256())
    fingerprint = ":".join(f"{b:02x}" for b in fingerprint_bytes)

    # Write files
    cert_dir = cert_dir or CERT_DIR
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert_path = cert_dir / "mememage.crt"
    key_path = cert_dir / "mememage.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    # 0600 perms on the key — same convention as Ed25519 private keys.
    os.chmod(str(key_path), 0o600)
    os.chmod(str(cert_path), 0o644)

    return {
        "cert": str(cert_path),
        "key": str(key_path),
        "fingerprint": fingerprint,
        "common_name": common_name,
        "alt_names": [n.value if hasattr(n, "value") else str(n) for n in sans],
        "expires": (now + timedelta(days=days_valid)).isoformat(),
    }


def update_server_config(cert_path: str, key_path: str,
                         domain: str | None = None) -> None:
    """Write the cert + key + optional domain into ``~/.mememage/server.json``.

    Preserves every other field in the config. Creates the file if
    missing. After this the mint server picks up the new cert paths on
    the next ``run_server()`` call — but since the TLS socket is bound
    at startup, an in-place cert change still needs a server restart.
    """
    SERVER_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    config = {}
    if SERVER_CONFIG.exists():
        try:
            config = json.loads(SERVER_CONFIG.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            config = {}
    config["cert"] = cert_path
    config["key"] = key_path
    if domain:
        config["domain"] = domain
    SERVER_CONFIG.write_text(json.dumps(config, indent=2), encoding="utf-8")
    os.chmod(str(SERVER_CONFIG), 0o644)
