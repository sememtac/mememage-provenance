"""Zodiac definitions and mapping helpers.

Two complementary representations:
  - Display: human-readable names ("Aries", "First Quarter")
  - Storage: integer codes (0..11 for zodiac, 0..7 for moon phases)

Souls store the integer codes; render layers (Python cert HTML, JS
cert-renderer, validator) reconstruct the display strings via
``zodiac_name(code)`` / ``moon_phase_name_for_code(code)``. Storing
codes makes the record smaller, machine-comparable, and free of
brittle "Aries 24.3°" string-parsing on the verify side.
"""

ZODIAC = [
    ("Aries", 0), ("Taurus", 30), ("Gemini", 60), ("Cancer", 90),
    ("Leo", 120), ("Virgo", 150), ("Libra", 180), ("Scorpio", 210),
    ("Sagittarius", 240), ("Capricorn", 270), ("Aquarius", 300), ("Pisces", 330),
]

ZODIAC_NAMES = [name for name, _ in ZODIAC]

# Moon-phase codes. 8 phases, each spanning 45° of elongation.
# Code = elongation // 45 (mod 8). New Moon is 0, Full Moon is 4.
# Append-only: never reorder, soul records reference by position.
MOON_PHASE_NAMES = [
    "New Moon",          # 0
    "Waxing Crescent",   # 1
    "First Quarter",     # 2
    "Waxing Gibbous",    # 3
    "Full Moon",         # 4
    "Waning Gibbous",    # 5
    "Last Quarter",      # 6
    "Waning Crescent",   # 7
]

# Back-compat — earlier code referenced MOON_PHASES as a list of
# (boundary_deg, name) tuples. Kept here so any straggler import
# doesn't break; new code should use MOON_PHASE_NAMES + moon_phase_code.
MOON_PHASES = [
    (0, "New Moon"),
    (45, "Waxing Crescent"),
    (90, "First Quarter"),
    (135, "Waxing Gibbous"),
    (180, "Full Moon"),
    (225, "Waning Gibbous"),
    (270, "Last Quarter"),
    (315, "Waning Crescent"),
    (360, "New Moon"),
]


def to_zodiac(ecliptic_lon_deg: float) -> tuple[str, float]:
    """Map ecliptic longitude (0-360) to zodiac sign NAME and degree.

    Display-side helper. Stored records use ``to_zodiac_code`` for the
    integer-coded form; this string-name version stays for any consumer
    that wants the display string directly (CLI ``mememage validate``,
    etc.).
    """
    lon = ecliptic_lon_deg % 360
    for i in range(len(ZODIAC) - 1, -1, -1):
        if lon >= ZODIAC[i][1]:
            deg_in_sign = lon - ZODIAC[i][1]
            return ZODIAC[i][0], round(deg_in_sign, 1)
    return ZODIAC[0][0], round(lon, 1)


def to_zodiac_code(ecliptic_lon_deg: float) -> dict:
    """Map ecliptic longitude to {"sign": int(0-11), "deg": float}.

    This is the canonical storage form for sun/moon/planetary positions
    in V1 souls. Display layers use ``zodiac_name(sign)`` to recover
    "Aries"/"Taurus"/etc. Storing codes:
      - eliminates parsing fragility ("Aries 24.3°" → split on space,
        regex degree, look up sign)
      - keeps records compact + machine-comparable
      - aligns naturally with the 0-indexed ZODIAC list above
    """
    lon = ecliptic_lon_deg % 360
    sign_idx = int(lon // 30)
    if sign_idx >= len(ZODIAC):  # defensive: numerical edge near 360
        sign_idx = 0
    deg_in_sign = round(lon - ZODIAC[sign_idx][1], 1)
    return {"sign": sign_idx, "deg": deg_in_sign}


def zodiac_name(sign_code: int) -> str:
    """Look up a zodiac sign name by 0-indexed code. Out-of-range
    falls back to "Aries" rather than raise — display surfaces should
    never crash on a malformed record."""
    if 0 <= sign_code < len(ZODIAC_NAMES):
        return ZODIAC_NAMES[sign_code]
    return ZODIAC_NAMES[0]


def moon_phase_name(elongation_deg: float) -> str:
    """Get moon phase NAME from sun-moon elongation.

    Display helper. Stored records use ``moon_phase_code`` for the
    integer-coded form.
    """
    e = elongation_deg % 360
    for i in range(len(MOON_PHASES) - 1):
        lo = MOON_PHASES[i][0]
        hi = MOON_PHASES[i + 1][0]
        if lo <= e < hi:
            return MOON_PHASES[i][1]
    return "New Moon"


def moon_phase_code(elongation_deg: float) -> dict:
    """Map elongation to {"phase": int(0-7), "illum": float(0-1)}.

    ``phase`` is the 8-phase code (New Moon=0, Full Moon=4).
    ``illum`` is the illumination fraction 0.0–1.0 (full = 1.0).

    Round-trip back to "Waxing Crescent (37.4%)" is one helper call
    on either Python or JS side: ``f"{MOON_PHASE_NAMES[p]} ({illum*100:.1f}%)"``.
    """
    import math
    e = elongation_deg % 360
    phase_idx = int(e // 45) % 8
    illumination = round((1 - math.cos(math.radians(e))) / 2, 3)
    return {"phase": phase_idx, "illum": illumination}


def moon_phase_name_for_code(phase_code: int) -> str:
    """Look up a moon phase name by 0-indexed code. Out-of-range falls
    back to "New Moon" so display surfaces stay non-crashy on garbage
    records."""
    if 0 <= phase_code < len(MOON_PHASE_NAMES):
        return MOON_PHASE_NAMES[phase_code]
    return MOON_PHASE_NAMES[0]
