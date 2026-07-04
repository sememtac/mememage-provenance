"""Astronomical position calculations.

Sun, Moon, and planetary positions from Jean Meeus' "Astronomical Algorithms".
All pure math — no external deps, no network calls.
"""

import math
from datetime import datetime


# ---------------------------------------------------------------------------
# Julian date
# ---------------------------------------------------------------------------

def julian_date(dt: datetime) -> float:
    """Convert datetime to Julian Date."""
    y, m = dt.year, dt.month
    if m <= 2:
        y -= 1
        m += 12
    A = y // 100
    B = 2 - A + A // 4
    day_frac = dt.day + (dt.hour + dt.minute / 60 + dt.second / 3600) / 24
    return int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + day_frac + B - 1524.5


def julian_centuries(jd: float) -> float:
    """Julian centuries since J2000.0."""
    return (jd - 2451545.0) / 36525.0


# ---------------------------------------------------------------------------
# Solar position (Meeus, Chapter 25 — ~0.01° accuracy)
# ---------------------------------------------------------------------------

def sun_ecliptic_lon(T: float) -> float:
    """Sun's ecliptic longitude in degrees (geometric, FK5)."""
    M = (357.52911 + T * (35999.05029 - 0.0001537 * T)) % 360
    Mr = math.radians(M)
    C = ((1.914602 - T * (0.004817 + 0.000014 * T)) * math.sin(Mr)
         + (0.019993 - 0.000101 * T) * math.sin(2 * Mr)
         + 0.000289 * math.sin(3 * Mr))
    L0 = (280.46646 + T * (36000.76983 + 0.0003032 * T)) % 360
    return (L0 + C) % 360


# ---------------------------------------------------------------------------
# Lunar position (Meeus Chapter 47, truncated series — ~0.5° accuracy)
# ---------------------------------------------------------------------------

def moon_ecliptic_lon(T: float) -> float:
    """Moon's ecliptic longitude in degrees."""
    Lp = (218.3165 + 481267.8813 * T) % 360
    M_moon = (134.9634 + 477198.8676 * T) % 360
    M_sun = (357.5291 + 35999.0503 * T) % 360
    D = (297.8502 + 445267.1115 * T) % 360
    F = (93.2720 + 483202.0175 * T) % 360

    Mr_m = math.radians(M_moon)
    Mr_s = math.radians(M_sun)
    Dr = math.radians(D)
    Fr = math.radians(F)

    lon = (Lp
           + 6.289 * math.sin(Mr_m)
           - 1.274 * math.sin(2 * Dr - Mr_m)
           + 0.658 * math.sin(2 * Dr)
           + 0.214 * math.sin(2 * Mr_m)
           - 0.186 * math.sin(Mr_s)
           - 0.114 * math.sin(2 * Fr)
           + 0.059 * math.sin(2 * Dr - 2 * Mr_m)
           + 0.057 * math.sin(2 * Dr - Mr_s - Mr_m)
           + 0.053 * math.sin(2 * Dr + Mr_m)
           + 0.046 * math.sin(2 * Dr - Mr_s))
    return lon % 360


# ---------------------------------------------------------------------------
# Planetary positions (simplified orbital elements — ~1° accuracy)
# Mean orbital elements from JPL — valid ~1800-2050
# ---------------------------------------------------------------------------

PLANETS = {
    "Mercury": {"L0": 252.2509, "L1": 149472.6746, "M0": 174.7948, "M1": 149472.5153, "e": 0.205630},
    "Venus":   {"L0": 181.9798, "L1": 58517.8157,  "M0": 50.4161,  "M1": 58517.8039,  "e": 0.006773},
    "Mars":    {"L0": 355.4330, "L1": 19140.2993,   "M0": 19.3730,  "M1": 19139.8585,  "e": 0.093405},
    "Jupiter": {"L0": 34.3515,  "L1": 3034.9057,    "M0": 20.0202,  "M1": 3034.6962,   "e": 0.048498},
    "Saturn":  {"L0": 50.0774,  "L1": 1222.1139,    "M0": 317.0207, "M1": 1222.1138,   "e": 0.054151},
}


def planet_ecliptic_lon(name: str, T: float) -> float:
    """Heliocentric ecliptic longitude of a planet (approximate).

    For birth certificate purposes we use heliocentric longitude directly.
    A proper geocentric correction would require computing the planet's distance
    and applying a parallax transform. Heliocentric longitude gives the correct
    zodiac sign ~95%+ for outer planets (Mars, Jupiter, Saturn) but only ~60-70%
    for inner planets (Mercury, Venus) near greatest elongation, where the
    heliocentric-geocentric difference can reach 28° (Mercury) or 47° (Venus).
    Sufficient for an art piece.
    """
    p = PLANETS[name]
    M = math.radians((p["M0"] + p["M1"] * T) % 360)
    e = p["e"]
    C = (2 * e - e**3 / 4) * math.sin(M) + (5 * e**2 / 4) * math.sin(2 * M)
    L = (p["L0"] + p["L1"] * T + math.degrees(C)) % 360
    return L
