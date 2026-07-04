"""Birth temperament — reading the machine's state at the moment of creation.

Each image gets a temperament based on what was physically true at birth.
No rolling averages, no history, no state files. Pure function: vitals in,
reading out. Like the celestial data reads the sky, this reads the machine.

Scope: temperament reads the machine (load, memory, power, time of day).
Entropy anomalies and celestial events are handled by the rarity system.
Three lenses on the same moment, no overlap:
  - Celestial = what the sky looked like (positions)
  - Rarity = what was anomalous in sky + entropy + machine stress (score)
  - Temperament = what the machine felt like (narrative)
"""

import re

from mememage.parsing import parse_gb, parse_load, parse_mb


# ---------------------------------------------------------------------------
# Stable trait code list — APPEND ONLY.
#
# Soul records store birth_traits as integer codes (the index into this
# list) rather than the string names. Stable positional IDs keep the
# canonical JSON compact and uniform with other coded fields
# (constellation_index, age). Trait names live only in code, never in
# the soul.
#
# Two iron rules:
#   - APPEND ONLY. Never reorder, never remove. Old records reference
#     codes by position, so a rename or reorder mutes them.
#   - Add new traits at the END. The position becomes their permanent ID.
# ---------------------------------------------------------------------------

BIRTH_TRAIT_CODES = [
    "contested",       # 0
    "yielding",        # 1
    "uncontested",     # 2
    "stumbling",       # 3
    "sure_footed",     # 4
    "reaching",        # 5
    "speculative",     # 6
    "cautious",        # 7
    "restless",        # 8
    "loosening_grip",  # 9
    "holding_tight",   # 10
    "in_flux",         # 11
    "entangled",       # 12
    "unraveled",       # 13
    "forged_in_fire",  # 14
    "under_pressure",  # 15
    "in_silence",      # 16
    "last_light",      # 17
    "untethered",      # 18
    "night_owl",       # 19
    "dawn",            # 20
]

TRAIT_NAME_TO_CODE = {name: i for i, name in enumerate(BIRTH_TRAIT_CODES)}


def trait_code(name: str) -> int:
    """trait_name → integer code. Raises if unknown (catches typos)."""
    return TRAIT_NAME_TO_CODE[name]


def trait_name(code: int) -> str | None:
    """integer code → trait_name (None if out of range)."""
    if 0 <= code < len(BIRTH_TRAIT_CODES):
        return BIRTH_TRAIT_CODES[code]
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _battery_pct(power) -> int | None:
    """Extract battery percentage from V1 dict or legacy string.

    V1: {"src": 1, "pct": 75} → 75 (None on AC).
    Legacy: "Battery 75%" → 75; "AC" → None.
    """
    if isinstance(power, dict):
        if power.get("src") == 1:  # POWER_BATTERY
            return power.get("pct")
        return None
    if not power or "Battery" not in str(power):
        return None
    m = re.search(r"(\d+)", str(power))
    return int(m.group(1)) if m else None


def _on_battery(power) -> bool:
    """True if power source is battery."""
    if isinstance(power, dict):
        return power.get("src") == 1
    return "Battery" in str(power or "")


def _parse_load(load_val) -> float:
    """V1 list [f, f, f] → 1m avg. Legacy string falls through to parse_load."""
    if isinstance(load_val, list) and load_val:
        try:
            return float(load_val[0])
        except (ValueError, TypeError):
            return 0.0
    return parse_load(load_val or "0")


def _local_hour(vitals: dict) -> int | None:
    """Get local hour if provided. Caller injects 'local_hour' into vitals."""
    return vitals.get("local_hour")


def _parse_ctx(s) -> tuple[int, int]:
    """V1 dict {"vol":int,"invol":int} → (vol, invol). Legacy string fallback."""
    if isinstance(s, dict):
        return int(s.get("vol", 0) or 0), int(s.get("invol", 0) or 0)
    m = re.findall(r"(\d+)", str(s or ""))
    if len(m) >= 2:
        return int(m[0]), int(m[1])
    return 0, 0


def _parse_faults(s) -> tuple[int, int]:
    """V1 dict {"soft":int,"hard":int} → (soft, hard). Legacy string fallback."""
    if isinstance(s, dict):
        return int(s.get("soft", 0) or 0), int(s.get("hard", 0) or 0)
    m = re.findall(r"(\d+)", str(s or ""))
    if len(m) >= 2:
        return int(m[0]), int(m[1])
    return 0, 0


def _parse_int(s) -> int:
    """Parse a string or int to int, default 0."""
    try:
        return int(str(s).strip())
    except (ValueError, TypeError):
        return 0


# ---------------------------------------------------------------------------
# Condition definitions
#
# (trait_name, category, check_fn, reading)
# Categories prevent stacking within the same domain.
# Only the first match per category fires.
# ---------------------------------------------------------------------------

BIRTH_CONDITIONS = [
    # All volatile dimensions use modular residue (low-order bits) of
    # accumulative counters. The magnitude is predictable; the exact
    # value at the instant of birth is not. Each condition fires ~1/N
    # of the time, creating genuine per-mint variance.

    # --- Context switches — last 2 digits of involuntary count ---
    (
        "contested", "ctx",
        lambda v: v.get("ctx_switches") and _parse_ctx(v["ctx_switches"])[1] > 0 and _parse_ctx(v["ctx_switches"])[1] % 100 > 75,
        "The CPU was contested — threads jostling at the moment of birth",
    ),
    (
        "yielding", "ctx",
        lambda v: v.get("ctx_switches") and _parse_ctx(v["ctx_switches"])[1] > 0 and _parse_ctx(v["ctx_switches"])[1] % 100 < 15,
        "The CPU was yielding — a brief window of cooperation",
    ),
    (
        "uncontested", "ctx",
        lambda v: v.get("ctx_switches") and _parse_ctx(v["ctx_switches"])[1] > 0 and 30 <= _parse_ctx(v["ctx_switches"])[1] % 100 <= 50,
        "The CPU was at ease — a calm moment between storms",
    ),

    # --- Page faults — parity and residue of soft faults ---
    (
        "stumbling", "faults",
        lambda v: v.get("page_faults") and _parse_faults(v["page_faults"])[0] > 0 and _parse_faults(v["page_faults"])[0] % 7 == 0,
        "The machine stumbled — a page fault at the exact moment of conception",
    ),
    (
        "sure_footed", "faults",
        lambda v: v.get("page_faults") and _parse_faults(v["page_faults"])[0] > 0 and _parse_faults(v["page_faults"])[0] % 7 in (3, 4),
        "Sure-footed — the memory was aligned",
    ),
    (
        "reaching", "hard_faults",
        lambda v: _parse_faults(v.get("page_faults", ""))[1] % 3 == 0 and _parse_faults(v.get("page_faults", ""))[1] > 0,
        "The machine was reaching — hard faults echoed through the birth",
    ),

    # --- Speculative pages — volatile, flips between states ---
    (
        "speculative", "speculation",
        lambda v: _parse_int(v.get("speculative_pages", 0)) > 2000,
        "Speculative surge — the OS was racing ahead of the program",
    ),
    (
        "cautious", "speculation",
        lambda v: v.get("speculative_pages") is not None and _parse_int(v["speculative_pages"]) < 300,
        "The OS was cautious — taking no risks with memory",
    ),
    (
        "restless", "speculation",
        lambda v: 500 <= _parse_int(v.get("speculative_pages", 0)) <= 1200,
        "The OS was restless — speculating but unsure",
    ),

    # --- Purgeable pages — chaotic, swings wildly ---
    (
        "loosening_grip", "purgeable",
        lambda v: _parse_int(v.get("purgeable_pages", 0)) > 4000,
        "Loosening grip — the machine was letting go of memory",
    ),
    (
        "holding_tight", "purgeable",
        lambda v: v.get("purgeable_pages") is not None and _parse_int(v["purgeable_pages"]) < 300,
        "Holding tight — every page was precious, nothing to spare",
    ),
    (
        "in_flux", "purgeable",
        lambda v: 1000 <= _parse_int(v.get("purgeable_pages", 0)) <= 3000,
        "Memory in flux — the machine was deciding what to keep",
    ),

    # --- Open file descriptors — residue for variance ---
    (
        "entangled", "fds",
        lambda v: v.get("open_fds") and _parse_int(v["open_fds"]) > 0 and _parse_int(v["open_fds"]) % 10 in (0, 1),
        "Entangled — the file descriptors aligned at a round number",
    ),
    (
        "unraveled", "fds",
        lambda v: v.get("open_fds") and _parse_int(v["open_fds"]) > 0 and _parse_int(v["open_fds"]) % 10 in (7, 8, 9),
        "Unraveled — the connections were fraying at the edges",
    ),

    # --- Load (tighter thresholds) ---
    (
        "forged_in_fire", "pressure",
        lambda v: _parse_load(v.get("load")) > 5.0,
        "Forged in fire — the machine was overwhelmed",
    ),
    (
        "under_pressure", "pressure",
        lambda v: _parse_load(v.get("load")) > 4.0,
        "Born under pressure — the system was straining",
    ),
    (
        "in_silence", "pressure",
        lambda v: _parse_load(v.get("load")) < 0.5 and v.get("load") is not None,
        "Born in silence — the machine was barely conscious",
    ),

    # --- Power (laptops only) ---
    (
        "last_light", "power",
        lambda v: (_battery_pct(v.get("power")) or 100) < 5,
        "Born in the last light — power was fading",
    ),
    (
        "untethered", "power",
        lambda v: _on_battery(v.get("power")),
        "Born untethered — free from the wall",
    ),

    # --- Time of day ---
    (
        "night_owl", "time",
        lambda v: _local_hour(v) is not None and 0 <= _local_hour(v) < 5,
        "Born in the small hours — the world was asleep",
    ),
    (
        "dawn", "time",
        lambda v: _local_hour(v) is not None and 5 <= _local_hour(v) < 7,
        "Born at first light",
    ),
]


# ---------------------------------------------------------------------------
# Temperament combinations — checked in order, first match wins
# ---------------------------------------------------------------------------

TEMPERAMENT_COMBOS = [
    # --- Turbulent ---
    (
        {"contested", "stumbling", "speculative"},
        "A violent birth",
        "CPU contested, memory stumbling, OS speculating — chaos at every level",
    ),
    (
        {"contested", "stumbling"},
        "A turbulent birth",
        "The CPU fought for time while the machine stumbled on its memory",
    ),
    (
        {"contested", "speculative"},
        "A reckless birth",
        "The CPU was contested and the OS was gambling on every page",
    ),
    (
        {"stumbling", "loosening_grip"},
        "A birth in collapse",
        "Memory was faulting and the OS was letting go — the floor giving way",
    ),
    (
        {"forged_in_fire", "contested"},
        "Born in the furnace",
        "Load was crushing and every thread fighting for survival",
    ),
    (
        {"speculative", "loosening_grip"},
        "An unstable birth",
        "The OS was speculating while releasing memory — a system on the edge",
    ),
    (
        {"reaching", "in_flux"},
        "A grasping birth",
        "Hard faults and shifting memory — the machine was reaching for something",
    ),
    (
        {"under_pressure", "holding_tight"},
        "A clenched birth",
        "Under pressure but refusing to release — every page held in a fist",
    ),
    (
        {"contested", "reaching"},
        "A strained birth",
        "CPU contested and hard faults rippling — two pressures at once",
    ),

    # --- Calm ---
    (
        {"uncontested", "sure_footed", "cautious"},
        "A perfect birth",
        "CPU clear, memory solid, OS confident — everything aligned",
    ),
    (
        {"uncontested", "sure_footed"},
        "A clean birth",
        "No contention, no faults — the machine was certain",
    ),
    (
        {"uncontested", "cautious"},
        "A deliberate birth",
        "CPU clear and OS cautious — nothing left to chance",
    ),
    (
        {"sure_footed", "holding_tight"},
        "A steadfast birth",
        "Memory aligned and nothing released — the machine was solid",
    ),
    (
        {"in_silence", "sure_footed"},
        "A meditative birth",
        "The machine was barely awake and every page was ready",
    ),
    (
        {"yielding", "restless"},
        "A restless birth",
        "The CPU was yielding but the OS was restless — a quiet tension",
    ),
    (
        {"yielding", "cautious"},
        "A patient birth",
        "The CPU yielded and the OS held back — both waiting for the right moment",
    ),

    # --- Night ---
    (
        {"night_owl", "contested"},
        "A fever dream",
        "The small hours with the CPU contested — a restless machine at night",
    ),
    (
        {"night_owl", "uncontested"},
        "A lucid dream",
        "The small hours with a clear CPU — a dreaming machine at peace",
    ),
    (
        {"night_owl", "stumbling"},
        "A nightmare",
        "The small hours with the machine stumbling — tossing in its sleep",
    ),
    (
        {"dawn", "sure_footed"},
        "A first breath",
        "Born at dawn with every page in place — the day's first creation",
    ),
    (
        {"dawn", "speculative"},
        "An eager dawn",
        "Born at first light with the OS already racing ahead",
    ),

    # --- Frequent real-world pairs ---
    (
        {"contested", "restless"},
        "An agitated birth",
        "CPU contested and OS restless — the machine couldn't settle",
    ),
    (
        {"contested", "unraveled"},
        "A fraying birth",
        "CPU contested and connections fraying — pressure from all sides",
    ),
    (
        {"sure_footed", "yielding"},
        "A graceful birth",
        "Memory solid and CPU yielding — a moment of elegant coordination",
    ),
    (
        {"in_flux", "restless"},
        "A shifting birth",
        "Memory in flux and OS restless — nothing was fixed in place",
    ),
    (
        {"reaching", "restless"},
        "A searching birth",
        "Hard faults and a restless OS — the machine was reaching for something",
    ),
    (
        {"contested", "in_flux"},
        "A roiling birth",
        "CPU contested while memory shifted beneath — turbulence at every layer",
    ),
    (
        {"sure_footed", "restless"},
        "A paradox birth",
        "Memory was solid but the OS was restless — certainty and unease at once",
    ),
    (
        {"uncontested", "restless"},
        "A watchful birth",
        "CPU clear but the OS restless — calm on the surface, stirring below",
    ),

    # --- Entanglement ---
    (
        {"entangled", "contested"},
        "A knotted birth",
        "Connections aligned and threads contested — deeply tangled",
    ),
    (
        {"unraveled", "loosening_grip"},
        "An unraveling birth",
        "Connections fraying and memory releasing — the machine was coming apart",
    ),
    (
        {"entangled", "holding_tight"},
        "A taut birth",
        "Everything connected and nothing released — a system wound tight",
    ),
    (
        {"unraveled", "restless"},
        "A dissolving birth",
        "Connections fraying and OS restless — the machine was losing its shape",
    ),
]

# Single-trait temperaments (fallback)
TEMPERAMENT_SINGLES = {
    "contested": "A contested birth",
    "yielding": "A yielding birth",
    "uncontested": "An uncontested birth",
    "stumbling": "A stumbling birth",
    "sure_footed": "A sure-footed birth",
    "reaching": "A reaching birth",
    "speculative": "A speculative birth",
    "cautious": "A cautious birth",
    "restless": "A restless birth",
    "loosening_grip": "A loosening birth",
    "holding_tight": "A clenched birth",
    "in_flux": "A birth in flux",
    "entangled": "An entangled birth",
    "unraveled": "An unraveled birth",
    "forged_in_fire": "A volatile birth",
    "under_pressure": "A strained birth",
    "in_silence": "A silent birth",
    "last_light": "A dying birth",
    "untethered": "An untethered birth",
    "night_owl": "A nocturnal birth",
    "dawn": "A dawn birth",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_birth_temperament(vitals: dict) -> dict:
    """Read the machine's temperament at this moment of creation.

    Returns:
        {
            "traits": ["forged_in_fire", "night_owl"],   # names (display + tests)
            "trait_codes": [14, 19],                     # int codes (soul record)
            "readings": ["Forged in fire — ...", "Born in the small hours — ..."],
            "temperament": "A desperate birth",
            "summary": "Born with nothing — ...",
        }
    """
    trait_names = []
    readings = []
    fired_categories = set()

    for name, category, check_fn, reading in BIRTH_CONDITIONS:
        if category in fired_categories:
            continue
        if check_fn(vitals):
            trait_names.append(name)
            fired_categories.add(category)

            if reading:
                readings.append(reading)

    # Determine temperament from combinations
    trait_set = set(trait_names)
    temperament = None
    summary = None

    for required, temp, summ in TEMPERAMENT_COMBOS:
        if required.issubset(trait_set):
            temperament = temp
            summary = summ
            break

    # Fallback to single-trait temperament
    if temperament is None and trait_names:
        temperament = TEMPERAMENT_SINGLES.get(trait_names[0])

    # Default: serene
    if temperament is None:
        temperament = "A serene birth"
        summary = "All was calm — the machine had nothing to prove"

    if summary is None:
        summary = readings[0] if readings else "All was calm — the machine had nothing to prove"

    # Names live in the engine + display layer; the soul record carries
    # the integer codes (see _step_build_record in core.py).
    trait_codes = [TRAIT_NAME_TO_CODE[n] for n in trait_names]

    return {
        "traits": trait_names,
        "trait_codes": trait_codes,
        "readings": readings,
        "temperament": temperament,
        "summary": summary,
    }
