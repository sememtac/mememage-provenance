"""GPS source resolution for the conception pipeline.

A chain declares how it captures GPS coordinates:

- ``phone``      — Phone capture page (today's flow). Precise ±20m
                   via ``navigator.geolocation.watchPosition``. Requires
                   the creator to act on a second device.
- ``machine``    — Server-side IP geolocation. Approximate, city-level.
                   No phone loop; mint fires immediately on conceive.
                   Useful for batch / desktop flows where the creator
                   accepts coarser coordinates in exchange for speed.
- ``none``       — No GPS captured. Record carries no ``gps_time_locked``
                   field. The cert renders an honest "BIRTHPLACE — NOT
                   RECORDED" placeholder so the absence is visible.

The camera analogy from the design: phone is the precise sensor,
machine is "use the device's own best guess," none is "this camera
doesn't write location to EXIF." All three are first-class.

GPS itself remains mandatory in spirit (the celestial birth certificate
treats place as part of conception); ``none`` simply records the
absence explicitly rather than degrading silently.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
from typing import Optional, Tuple
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

GPS_SOURCE_PHONE = "phone"
GPS_SOURCE_MACHINE = "machine"
GPS_SOURCE_NONE = "none"

GPS_SOURCES = (GPS_SOURCE_PHONE, GPS_SOURCE_MACHINE, GPS_SOURCE_NONE)
DEFAULT_GPS_SOURCE = GPS_SOURCE_PHONE


def is_valid_source(value: object) -> bool:
    return isinstance(value, str) and value in GPS_SOURCES


# --- GPS visibility -------------------------------------------------------
#
# Orthogonal to the SOURCE (where the coordinates come from): the
# VISIBILITY decides how a captured location is shown.
#
# - ``time_locked`` (default) — the coordinates are sealed in an RSA
#   time-lock puzzle (``gps_time_locked``). Private now; anyone can
#   recover them in ~10 years of sequential squaring. Location privacy
#   independent of chain visibility — a public chain can publish its
#   prompt + sky while keeping its birthplace private-until-eventually.
# - ``public`` — the coordinates are ALSO stored in plaintext (``gps``
#   field) so the certificate shows them directly, right now. The
#   time-lock is kept too, so the location stays cryptographically
#   provable later. Opt-in, per chain: a deliberate, irreversible
#   (per record) choice to expose where each conception happened.
#
# There is no "decrypt a time-locked GPS" path — plaintext must be
# stored at conception, so this is a chain-level decision made before
# minting, not a viewer toggle.
GPS_VISIBILITY_TIME_LOCKED = "time_locked"
GPS_VISIBILITY_PUBLIC = "public"

GPS_VISIBILITIES = (GPS_VISIBILITY_TIME_LOCKED, GPS_VISIBILITY_PUBLIC)
DEFAULT_GPS_VISIBILITY = GPS_VISIBILITY_TIME_LOCKED


def is_valid_visibility(value: object) -> bool:
    return isinstance(value, str) and value in GPS_VISIBILITIES


# --- Reachability -------------------------------------------------------
#
# Whether the PHONE GPS source is usable on a given host comes down to
# one question: can a phone reach this server to load the capture page?
# That is orthogonal to the OS — a loopback-only desktop bind is
# unreachable; a Tailscale or LAN interface is reachable.
#
# These helpers detect the host's own reachable interface addresses with
# stdlib only (no netifaces/psutil). The trick: a connectionless UDP
# socket "connected" to a target picks the source IP the OS would route
# from — without sending a single packet. Same move the server already
# uses (8.8.8.8) to learn its outbound IP.
#
# The DECISION to advertise/bind a given interface (Tailscale always;
# LAN only on explicit opt-in) lives in the server; these functions only
# report what's available.

_CGNAT = ipaddress.ip_network("100.64.0.0/10")  # RFC 6598 — Tailscale's range


def _source_ip_for(target: str) -> Optional[str]:
    """The local source IPv4 the OS would use to reach ``target``.

    Uses a connectionless UDP socket — ``connect()`` only sets the route,
    no datagram is sent. Returns ``None`` if no route exists (e.g. asking
    for the Tailscale resolver when tailscaled isn't running).
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((target, 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def _in_cgnat(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip) in _CGNAT
    except ValueError:
        return False


def _is_private(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


def tailscale_ip() -> Optional[str]:
    """This host's Tailscale IPv4 (in ``100.64.0.0/10``), or ``None``.

    Detected by asking the OS which source address it would use to reach
    Tailscale's internal resolver (``100.100.100.100``). That route only
    exists when tailscaled is up, and the source address it returns is
    this node's own tailnet IP. The private, authenticated overlay makes
    it the preferred phone-capture interface on desktop.
    """
    ip = _source_ip_for("100.100.100.100")
    if ip and _in_cgnat(ip):
        return ip
    return None


def lan_ip() -> Optional[str]:
    """This host's primary private LAN IPv4 (RFC 1918), or ``None``.

    ``None`` when the outbound address is public (the host is directly
    internet-facing — advertise via its domain, not a bare LAN IP) or
    when it lands in the Tailscale CGNAT range. Exposing this interface
    for phone capture is opt-in (it puts the tokenless desktop dashboard
    on the local network); the server gates it behind ``expose_lan``.
    """
    ip = _source_ip_for("8.8.8.8")
    if ip and _is_private(ip) and not _in_cgnat(ip):
        return ip
    return None


def fetch_machine_gps(timeout: float = 3.0) -> Optional[Tuple[float, float]]:
    """Best-effort IP geolocation for the host running this process.

    Returns ``(lat, lon)`` on success, ``None`` on any failure. Uses
    ip-api.com's free JSON endpoint over HTTP. Accuracy is roughly
    city-level for residential IPs and datacenter-location for VPS
    hosts — that's the deal the user accepts when they pick the
    ``machine`` source.

    No retries: if the lookup fails the caller decides what to do
    (typically: surface the failure rather than fall through to
    ``none``, so the user knows their picked source didn't work).
    """
    try:
        req = Request(
            "http://ip-api.com/json/?fields=status,lat,lon",
            headers={"User-Agent": "mememage/1.0"},
        )
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("status") != "success":
            log.warning("ip-api.com returned non-success: %s", data)
            return None
        lat = float(data["lat"])
        lon = float(data["lon"])
        return (lat, lon)
    except Exception as e:
        log.warning("Machine GPS lookup failed: %s", e)
        return None
