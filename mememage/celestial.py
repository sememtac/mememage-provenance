"""Celestial birth certificate for AI-generated images.

Computes the exact state of the sky at the moment and place of creation:
moon phase, sun position, and planetary positions in the zodiac.
All math from Jean Meeus' "Astronomical Algorithms" — no external deps.

This module orchestrates the birth certificate from focused submodules:
- zodiac: sign definitions and mapping
- ephemeris: astronomical position calculations
- vitals: machine hardware snapshot
- timelock: RSA time-lock puzzle for GPS

GPS is caller-provided (captured from the creator's phone at mint time).
"""

import math
from datetime import datetime, timezone

from mememage.ephemeris import (
    PLANETS,
    julian_centuries,
    julian_date,
    moon_ecliptic_lon,
    planet_ecliptic_lon,
    sun_ecliptic_lon,
)
from mememage.timelock import lock_gps
from mememage.vitals import collect_vitals
from mememage.zodiac import (
    ZODIAC,
    moon_phase_code,
    moon_phase_name,
    to_zodiac,
    to_zodiac_code,
)

# ---------------------------------------------------------------------------
# Backward-compatible aliases (tests and rarity.py import these)
# ---------------------------------------------------------------------------
_julian_date = julian_date
_julian_centuries = julian_centuries
_sun_ecliptic_lon = sun_ecliptic_lon
_moon_ecliptic_lon = moon_ecliptic_lon
_to_zodiac = to_zodiac
_moon_phase_name = moon_phase_name
_machine_vitals = collect_vitals


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_birth_certificate(gps, dt=None):
    """Compute the celestial birth certificate for this moment.

    Args:
        gps: (lat, lon) tuple, or ``None`` if the chain's GPS source is
             ``none``. Celestial positions are geocentric and don't
             depend on observer location — only the time-lock puzzle
             needs GPS, so absent coordinates simply omit ``gps_time_locked``
             from the returned dict.
        dt: UTC datetime for the certificate. Defaults to now.

    Returns a dict with (optionally) time-locked GPS, moon phase, sun
    sign, planetary positions, and machine vitals.
    """
    if gps is not None:
        if len(gps) != 2:
            raise ValueError("GPS must be a (lat, lon) tuple or None")
        lat, lon = float(gps[0]), float(gps[1])
    else:
        lat = lon = None

    if dt is None:
        dt = datetime.now(timezone.utc)

    jd = julian_date(dt)
    T = julian_centuries(jd)

    # Sun
    sun_lon = sun_ecliptic_lon(T)

    # Moon
    moon_lon = moon_ecliptic_lon(T)
    elongation = (moon_lon - sun_lon) % 360

    # Planets — coded form keyed by lowercase name.
    planets = {}
    for name in PLANETS:
        plon = planet_ecliptic_lon(name, T)
        planets[name.lower()] = to_zodiac_code(plon)

    # Angular spread — minimum arc containing all bodies (for rarity)
    all_lons = [sun_lon, moon_lon]
    for name in PLANETS:
        all_lons.append(planet_ecliptic_lon(name, T))
    all_lons_sorted = sorted(l % 360 for l in all_lons)
    if len(all_lons_sorted) >= 2:
        gaps = []
        for i in range(len(all_lons_sorted) - 1):
            gaps.append(all_lons_sorted[i + 1] - all_lons_sorted[i])
        gaps.append(360 - all_lons_sorted[-1] + all_lons_sorted[0])
        angular_spread = round(360 - max(gaps), 1)
    else:
        angular_spread = 0.0

    # Machine vitals
    vitals = collect_vitals()

    # All celestial positions stored as {"sign": int(0-11), "deg":
    # float} dicts. moon_phase as {"phase": int(0-7), "illum":
    # float(0-1)}. Display layers (cert-renderer.js, validator.js,
    # Python validate.py) reconstruct human strings via the
    # zodiac.zodiac_name / moon_phase_name_for_code helpers.
    cert: dict = {
        "sun": to_zodiac_code(sun_lon),
        "moon": to_zodiac_code(moon_lon),
        "moon_phase": moon_phase_code(elongation),
        "mercury": planets["mercury"],
        "venus": planets["venus"],
        "mars": planets["mars"],
        "jupiter": planets["jupiter"],
        "saturn": planets["saturn"],
        "angular_spread": angular_spread,
        "machine": vitals,
    }
    # GPS time-lock is no longer stored inside the birth certificate
    # — it lives at the top-level `gps` namespace (set in core.py's
    # _step_build_record) so chains with gps_source: "none" produce
    # symmetrically-shaped birth dicts (no gps inside, no gps key on
    # the record at all).
    return cert
