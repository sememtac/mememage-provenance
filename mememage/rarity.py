"""Rarity system — emergent collectibility from celestial, machine, and entropy data.

Three dice, three faces each, one sigil:

    Celestial (moment beyond cause):
        Phase        — moon illumination extremes
        Alignment    — bodies converging or opposing, weighted by orbital independence
        Distribution — angular spread of all bodies on the ecliptic

    Machine (moment with cause):
        Speculation  — kernel page prefetch activity (speculative pages)
        Sacrifice    — memory the system is willing to discard (purgeable pages)
        Pulse        — instantaneous disk bus activity (I/O rate)

    Entropy (moment without cause):
        Repetition   — consecutive identical or sequential bytes
        Symmetry     — mirrored byte patterns
        Extremes     — magnitude bias or spikes

    Sigil: the bar's magic bytes (0xAD4E) appearing in random entropy —
          the image's identity radiating unbidden in pure noise
"""

import hashlib
import re

from mememage.zodiac import ZODIAC

# ---------------------------------------------------------------------------
# Per-mint trait gate
# ---------------------------------------------------------------------------
#
# Sky and machine conditions persist across mints — a new-moon night
# lasts hours, an outer-planet conjunction lasts weeks. Without
# randomness, every mint in those windows scores the same bonus and
# users can predict "today is Legendary Day." That defeats the
# "discovery on each conception" feel.
#
# Solution: each candidate trait runs through a deterministic-but-
# unpredictable per-mint gate. The condition makes the trait
# *eligible*; the gate decides whether this particular conception
# actually catches it. Probability is inversely scaled by the trait's
# value — higher reward, rarer gate.
#
# Seed: the 32 random bytes the kernel provides per mint
# (born.machine.entropy). Different every mint, replayable for
# verifiers, never predictable in advance.
#
# Entropy-die traits and the sigil are NOT gated — those already
# derive from the per-mint entropy bytes and have natural per-roll
# uniqueness baked in.

_GATE_BY_VALUE = {
    5:  0.80,
    8:  0.65,
    10: 0.55,
    15: 0.45,
    20: 0.35,
    25: 0.25,
    30: 0.18,
    35: 0.12,
}


def _gate(entropy_hex: str, trait_name: str, value: int) -> bool:
    """Deterministic per-mint coin flip. True = trait fires."""
    if not entropy_hex:
        # Legacy / missing entropy: ungated. Preserves verifiability
        # of pre-gate records and pre-gate test fixtures.
        return True
    target = _GATE_BY_VALUE.get(value, 0.25)
    seed = hashlib.sha256(f"{entropy_hex}:{trait_name}".encode()).digest()
    r = int.from_bytes(seed[:4], "big") / (2 ** 32)
    return r < target


def _apply_gate(candidates, entropy_hex):
    return [(name, value) for (name, value) in candidates if _gate(entropy_hex, name, value)]

# Rarity tiers — Age of Aries thresholds (rarity v2, luck-backbone).
# The score is DERIVED, never stored, so changing this rescoring is forward-only:
# old records keep their stored dice dict (hash intact) and simply re-tier.
# Distribution on an idle box / calm sky (the common case): the entropy-luck
# backbone alone gives ~20% Uncommon+, ~5% Rare+; the top tiers require a real
# event (machine vigor, an unusual sky, an entropy pattern, or the sigil floor).
TIERS = [
    (0, "Common", "#a0a0a0"),
    (25, "Uncommon", "#4a9e4a"),
    (40, "Rare", "#4a7abe"),
    (55, "Very Rare", "#8a4abe"),
    (72, "Epic", "#be8a1a"),
    (88, "Legendary", "#be2a2a"),
]

# --- Rarity v2 knobs ---------------------------------------------------------
# Entropy luck is the distribution backbone: the 32 kernel-random bytes per mint
# are the fairest source (fresh every conception, unfarmable, no spike-day). A
# skewed map (u**EXP) keeps most mints low while leaving a real tail.
_LUCK_MAX = 45
_LUCK_EXP = 2.6
# Machine vigor: a continuous read of how alive the machine was at conception,
# so the machine always contributes proportionally — not only at extremes.
_VIGOR_MAX = 25
# Celestial is capped so a persistent conjunction can't spike every mint that
# day; the sky nudges, it never jackpots.
_CELESTIAL_CAP = 15
# A sigil (~1 in 1000) floors the tier to at least Rare — the rarest event in
# the system should change your tier.
_RARE_FLOOR = 40

ZODIAC_NAMES = [name for name, _ in ZODIAC]

# Orbital classification — inner bodies are tethered to the sun,
# outer bodies move independently on long timescales
INNER_BODIES = {"sun", "moon", "mercury", "venus"}
OUTER_BODIES = {"mars", "jupiter", "saturn"}


def _parse_position(pos):
    """Resolve a position record to ``(sign_index, deg_in_sign)``.

    Accepts V1 dict shape ``{"sign": int, "deg": float}`` or legacy
    string ``"Aries 12.6°"``. Returns ``(None, None)`` on garbage so
    callers can skip cleanly.
    """
    if not pos:
        return None, None
    if isinstance(pos, dict):
        sign = pos.get("sign")
        deg = pos.get("deg")
        if isinstance(sign, int) and isinstance(deg, (int, float)) and 0 <= sign < len(ZODIAC_NAMES):
            return sign, float(deg)
        return None, None
    if isinstance(pos, str):
        parts = pos.replace("\u00b0", "").split()
        if len(parts) < 2:
            return None, None
        sign_name = parts[0]
        try:
            deg = float(parts[1])
        except ValueError:
            return None, None
        idx = ZODIAC_NAMES.index(sign_name) if sign_name in ZODIAC_NAMES else -1
        return idx, deg
    return None, None


def _ecliptic_lon(pos):
    """Resolve a position record to its ecliptic longitude (0-360°)."""
    idx, deg = _parse_position(pos)
    if idx is None or idx < 0:
        return None
    return idx * 30 + deg


# ---------------------------------------------------------------------------
# Celestial die
# ---------------------------------------------------------------------------

def _check_celestial(born):
    """Check celestial data for rare conditions.

    Three faces: Phase, Alignment, Distribution.
    """
    traits = []
    bodies = ["sun", "moon", "mercury", "venus", "mars", "jupiter", "saturn"]

    # --- Phase: moon illumination extremes ---
    # V1 dict shape: {"phase": int(0..7), "illum": float(0..1)}.
    # Legacy string shape: "Full Moon (98.4%)".
    phase = born.get("moon_phase", "")
    phase_name = ""
    illum_pct = 0.0
    if isinstance(phase, dict):
        from mememage.zodiac import moon_phase_name_for_code
        p_code = phase.get("phase")
        if isinstance(p_code, int):
            phase_name = moon_phase_name_for_code(p_code)
        illum = phase.get("illum")
        if isinstance(illum, (int, float)):
            illum_pct = float(illum) * 100.0
    elif isinstance(phase, str) and phase:
        phase_name = re.sub(r"\s*\([^)]*\)\s*$", "", phase).strip()
        m = re.search(r"([\d.]+)%", phase)
        if m:
            try:
                illum_pct = float(m.group(1))
            except ValueError:
                pass
    if phase_name == "Full Moon" and illum_pct > 98:
        traits.append(("Full Moon (>98%)", 15))
    if phase_name == "New Moon" and illum_pct < 2:
        traits.append(("New Moon (<2%)", 15))

    # --- Alignment: conjunctions weighted by orbital independence ---
    signs = {}
    for body in bodies:
        pos = born.get(body, "")
        idx, deg = _parse_position(pos)
        if idx is not None and idx >= 0:
            signs[body] = idx

    # Group bodies by sign
    sign_groups = {}
    for body, sign_idx in signs.items():
        sign_groups.setdefault(sign_idx, []).append(body)

    for sign_idx, group in sign_groups.items():
        if len(group) < 2:
            continue
        group_set = set(group)
        has_outer = bool(group_set & OUTER_BODIES)
        outer_count = len(group_set & OUTER_BODIES)
        sign_name = ZODIAC_NAMES[sign_idx]
        names = "+".join(b.capitalize() for b in group)

        if len(group) >= 3 and has_outer:
            traits.append((f"Grand Conjunction ({names} in {sign_name})", 35))
        elif outer_count >= 2:
            traits.append((f"Outer Conjunction ({names} in {sign_name})", 25))
        elif has_outer:
            traits.append((f"Cross Conjunction ({names} in {sign_name})", 15))
        elif len(group) >= 3:
            traits.append((f"Inner Cluster ({names} in {sign_name})", 8))
        else:
            traits.append((f"Inner Conjunction ({names} in {sign_name})", 5))

    # Opposition: sun and moon in opposite signs (±1 sign)
    if "sun" in signs and "moon" in signs:
        diff = abs(signs["sun"] - signs["moon"])
        if diff == 6 or diff == 5 or diff == 7:
            traits.append(("Sun-Moon Opposition", 10))

    # --- Distribution: angular spread ---
    spread = born.get("angular_spread")
    if spread is not None:
        if spread < 60:
            traits.append((f"Stellar Convergence ({spread}\u00b0 spread)", 30))
        elif spread < 90:
            traits.append((f"Tight Cluster ({spread}\u00b0 spread)", 20))
        elif spread < 120:
            traits.append((f"Hemispherical Lean ({spread}\u00b0 spread)", 10))

    return traits


# ---------------------------------------------------------------------------
# Machine die
# ---------------------------------------------------------------------------

def _parse_tps(disk_io):
    """Extract tps from V1 dict or legacy string. Returns None if absent."""
    if not disk_io:
        return None
    if isinstance(disk_io, dict):
        v = disk_io.get("tps")
        return float(v) if isinstance(v, (int, float)) else None
    m = re.search(r"([\d.]+)\s*tps", str(disk_io))
    if m:
        return float(m.group(1))
    return None


# Platform-tuned thresholds for the three machine-die faces. macOS
# was the reference implementation, Monte-Carlo'd against 100K rolls;
# Linux thresholds are first-pass estimates targeting ~5% trigger
# rate per trait against typical VPS + desktop /proc samples.
# Recalibrate when there's enough mint data.
#
# Each tuple: (frenzy, surge, lull, silence). The high two are
# "kernel doing a lot"; the low two are "kernel quiet". Unknown
# platforms get no machine traits — die stays silent rather than
# rolling phony scores.
_MACHINE_THRESHOLDS = {
    "darwin": {
        "speculative": (25000, 15000, 1500, 500),
        "purgeable":   (10000,  5000,  200,  10),
    },
    "linux": {
        # Inactive(file) / 4 — page cache the kernel holds
        # speculatively. A typical 1-2 GB VPS sees 50k-200k pages
        # routinely; the rare extremes are real outliers.
        "speculative": (800000, 400000, 5000, 500),
        # (Cached + SReclaimable) / 4 — reclaimable cache + slab.
        # A 1-2 GB VPS keeps 200k-300k pages cached as baseline;
        # genuinely rare states sit well outside that band.
        "purgeable":  (1500000, 700000, 5000, 500),
    },
}

# Pulse face — disk transfers/sec. Platform-specific because the
# baseline differs: macOS desktop has constant background I/O so
# "Silence" is meaningful at <5 tps. Linux VPS samples often read
# 0 tps in a brief window simply because the box was idle for
# 400ms — that's not "rare", it's the default state. Linux Silence
# therefore requires a measurable but minimal pulse (>0 and <2).
_PULSE_THRESHOLDS = {
    "darwin": {"storm": 5000, "rush": 2000, "whisper": 20, "silence": 5,
               "min_silence": 0},
    "linux":  {"storm": 5000, "rush": 1500, "whisper": 8, "silence": 3,
               "min_silence": 1},  # tps must be ≥ 1 to count as Silence
}


def _check_machine(machine):
    """Check machine vitals for rare conditions.

    Three faces: Speculation, Sacrifice, Pulse. Thresholds are
    platform-aware because Mach VM page counts (macOS) and Linux
    /proc/meminfo page counts measure different things at different
    scales. ``machine["platform"]`` is set by vitals.collect_vitals.
    """
    traits = []
    from mememage.vitals import platform_name
    plat = platform_name(machine.get("platform", "darwin"))
    thresh = _MACHINE_THRESHOLDS.get(plat)
    if thresh is None:
        # Unknown platform — die stays silent. Better than rolling
        # against thresholds that aren't calibrated to this system.
        # Pulse below still fires if disk_io reports a tps reading.
        thresh = {"speculative": None, "purgeable": None}

    # --- Speculation: kernel prefetch / inactive cache pages ---
    spec = machine.get("speculative_pages")
    if spec is not None and thresh["speculative"]:
        frenzy, surge, lull, silence = thresh["speculative"]
        if spec > frenzy:
            traits.append((f"Speculative Frenzy ({spec} pages)", 20))
        elif spec > surge:
            traits.append((f"Speculative Surge ({spec} pages)", 10))
        elif spec < silence:
            traits.append((f"Speculative Silence ({spec} pages)", 20))
        elif spec < lull:
            traits.append((f"Speculative Lull ({spec} pages)", 10))

    # --- Sacrifice: purgeable / reclaimable pages ---
    purg = machine.get("purgeable_pages")
    if purg is not None and thresh["purgeable"]:
        ready, loosen, tight, every = thresh["purgeable"]
        if purg > ready:
            traits.append((f"Ready to Shed ({purg} purgeable pages)", 20))
        elif purg > loosen:
            traits.append((f"Loosening Grip ({purg} purgeable pages)", 10))
        elif purg < every:
            traits.append((f"Holding Everything ({purg} purgeable pages)", 20))
        elif purg < tight:
            traits.append((f"Holding Tight ({purg} purgeable pages)", 10))

    # --- Pulse: disk I/O rate (tps). Platform-tuned because idle
    # state means different things — see _PULSE_THRESHOLDS comment.
    # Both Silence and Whisper require a non-zero measurement on
    # Linux: tps == 0 is the common case for an idle VPS in a brief
    # sample window and shouldn't fire a "rare" trait.
    pt = _PULSE_THRESHOLDS.get(plat)
    tps = _parse_tps(machine.get("disk_io", ""))
    if tps is not None and pt is not None:
        if tps > pt["storm"]:
            traits.append((f"Bus Storm ({tps:.0f} tps)", 20))
        elif tps > pt["rush"]:
            traits.append((f"Bus Rush ({tps:.0f} tps)", 10))
        elif pt["min_silence"] <= tps < pt["silence"]:
            traits.append((f"Bus Silence ({tps:.0f} tps)", 20))
        elif pt["min_silence"] <= tps < pt["whisper"]:
            traits.append((f"Bus Whisper ({tps:.0f} tps)", 10))

    # --- Other platforms (Windows): memory commit pressure. The page-count and
    # disk-tps faces above are macOS/Linux concepts; on "other" we read the one
    # universal signal we collect — how full physical RAM is (Windows reports
    # used/available via GlobalMemoryStatusEx). Percentage thresholds need no
    # distribution calibration: >92% is genuinely strained, <8% genuinely open.
    if plat == "other":
        act = machine.get("mem_active")
        free = machine.get("mem_free")
        if isinstance(act, int) and isinstance(free, int) and (act + free) > 0:
            load = 100.0 * act / (act + free)
            if load > 92:
                traits.append((f"Memory Saturated ({load:.0f}%)", 20))
            elif load > 80:
                traits.append((f"Memory Strained ({load:.0f}%)", 10))
            elif load < 8:
                traits.append((f"Memory Wide Open ({load:.0f}%)", 20))
            elif load < 20:
                traits.append((f"Memory Spacious ({load:.0f}%)", 10))

    return traits


# ---------------------------------------------------------------------------
# Entropy die
# ---------------------------------------------------------------------------

def _check_entropy(entropy_hex):
    """Check kernel entropy for rare patterns.

    Three faces: Repetition, Symmetry, Extremes.
    """
    traits = []
    if not entropy_hex:
        return traits

    clean = entropy_hex.replace(" ", "").lower()
    if len(clean) < 8:
        return traits

    # Parse to bytes
    ebytes = []
    for i in range(0, len(clean) - 1, 2):
        try:
            ebytes.append(int(clean[i:i + 2], 16))
        except ValueError:
            pass

    if not ebytes:
        return traits

    # --- Repetition: consecutive identical or sequential bytes ---

    # 3+ consecutive identical bytes (~0.05% on 32 bytes)
    run = 1
    for i in range(1, len(ebytes)):
        if ebytes[i] == ebytes[i - 1]:
            run += 1
            if run >= 3:
                traits.append((f"Triple Echo (3+ identical bytes: 0x{ebytes[i]:02x})", 20))
                break
        else:
            run = 1

    # 3+ sequential ascending or descending (~0.05%)
    for i in range(len(ebytes) - 2):
        if ebytes[i + 1] == ebytes[i] + 1 and ebytes[i + 2] == ebytes[i] + 2:
            traits.append(("Ascending Run (3+ sequential)", 15))
            break
        if ebytes[i + 1] == ebytes[i] - 1 and ebytes[i + 2] == ebytes[i] - 2:
            traits.append(("Descending Run (3+ sequential)", 15))
            break

    # --- Symmetry: mirrored byte patterns ---

    # First 2 bytes == last 2 reversed (~0.0015%)
    if len(ebytes) >= 4:
        if ebytes[0] == ebytes[-1] and ebytes[1] == ebytes[-2]:
            traits.append(("Deep Mirror (first 2 = last 2 reversed)", 25))
        elif ebytes[0] == ebytes[-1]:
            # First byte == last byte (~0.39%)
            traits.append(("Bookend (first byte = last byte)", 10))

    # --- Extremes: magnitude bias or spikes ---

    # Mean bias — average of all bytes in extreme quartile (~0.06%)
    if ebytes:
        mean_val = sum(ebytes) / len(ebytes)
        if mean_val > 170:
            traits.append((f"High Tide (mean {mean_val:.1f})", 20))
        elif mean_val < 85:
            traits.append((f"Low Tide (mean {mean_val:.1f})", 20))

    # Meteor patterns — consecutive extreme bytes
    for i in range(len(ebytes) - 2):
        if ebytes[i] > 250 and ebytes[i + 1] > 250 and ebytes[i + 2] > 250:
            traits.append(("Meteor Storm (triple spike >250)", 25))
            break
    else:
        for i in range(len(ebytes) - 1):
            if ebytes[i] > 250 and ebytes[i + 1] > 250:
                traits.append(("Meteor Burst (double spike >250)", 10))
                break

    return traits


# ---------------------------------------------------------------------------
# Sigil — the bar's identity radiating in random noise
# ---------------------------------------------------------------------------

def _check_sigil(entropy_hex):
    """Check if the bar's magic bytes (0xAD4E) appear in the entropy.

    The bar writes AD4E deliberately. The entropy is random.
    When the random bytes contain the deliberate signature,
    the image wears a sigil — its identity found unbidden in noise.

    ~0.09% probability per mint (~1 in 1,075).
    """
    if not entropy_hex:
        return None
    clean = entropy_hex.replace(" ", "").lower()
    pos = clean.find("ad4e")
    if pos >= 0:
        return {"found": "ad4e", "position": pos, "points": 10}
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _machine_signature(entropy_hex: str, machine: dict) -> int:
    """Per-mint machine baseline: 1-5 pts, always non-zero, always
    varying. The machine's signature on this conception.

    Even when no extreme trait fires (idle VPS, calm desktop), the
    machine still contributes a small amount derived from the live
    state + entropy. Deterministic per record (verifiers replay it
    from the bytes already in the soul), unpredictable per attempt
    (entropy changes every mint).

    Returns 1-5 pts; returns 0 only when entropy is missing entirely
    (legacy records).
    """
    if not entropy_hex:
        return 0
    # Mix entropy + a few stable machine fields so the signature
    # reflects "this machine, this moment." Stable fields are
    # included so different machines with same entropy disagree
    # (extremely unlikely collision, but structurally honest).
    fp = str(machine.get("cpu", "")) + str(machine.get("ram", ""))
    seed = hashlib.sha256(f"machine-sig:{entropy_hex}:{fp}".encode()).digest()
    # 0-255 → 1-5
    return 1 + (seed[0] * 5) // 256


# Luck jackpot — a rare independent roll that floors a conception into the top
# tiers, so the whole spectrum is reachable on pure luck (no special machine/sky
# needed) while the smooth backbone keeps the middle thin. (cutoff, floor):
# crossing a cutoff floors luck to at least that tier's base.
_LUCK_JACKPOT = [
    (0.9990, 90),   # ~0.10% — Legendary
    (0.9955, 73),   # ~0.45% — Epic
    (0.9750, 56),   # ~1.95% — Very Rare
]


def _entropy_luck(entropy_hex: str) -> int:
    """The luck backbone. A skewed map of the per-mint kernel entropy — fresh
    every conception, unfarmable, no spike-day — gives the distribution its
    shape (mostly low, thin middle). A second independent slice of the same
    bytes occasionally jackpots into the top tiers, so Legendary is reachable on
    any mint, barely. Read straight from the entropy bytes already in the soul
    (NO hashing) so the JS reader reproduces it bit-for-bit — the score is
    derived by readers, never stored, and must match across Python/JS."""
    clean = entropy_hex.replace(" ", "").lower() if entropy_hex else ""
    if len(clean) < 24:
        return 0
    try:
        u = int(clean[0:12], 16) / float(1 << 48)   # first 6 bytes  → [0, 1)
        j = int(clean[12:24], 16) / float(1 << 48)  # next 6 bytes   → [0, 1)
    except ValueError:
        return 0
    luck = int(_LUCK_MAX * (u ** _LUCK_EXP))
    for cutoff, floor in _LUCK_JACKPOT:
        if j > cutoff:
            return max(luck, floor)
    return luck


def _machine_vigor(machine: dict) -> int:
    """A continuous read of how alive the machine was at conception (0.._VIGOR_MAX):
    load per core + disk throughput + memory pressure, blended. Idle box → near
    zero (honest); busy box → more. Derived from vitals already in the soul, so
    it's recomputed by readers, never stored."""
    if not machine:
        return 0
    # Load per core (1-minute average).
    n_load = 0.0
    load = machine.get("load")
    if isinstance(load, (list, tuple)) and load:
        try:
            cores = (machine.get("cores") or {}).get("total") or 1
            n_load = min(1.0, float(load[0]) / max(1, cores))
        except (TypeError, ValueError):
            n_load = 0.0
    # Disk transfers/sec.
    tps = _parse_tps(machine.get("disk_io"))
    n_disk = min(1.0, tps / 400.0) if tps is not None else 0.0
    # Memory pressure. Compression is the macOS/Linux signal; Windows reports
    # used/available with no compressor stat, so fall back to the used fraction
    # there so a memory-loaded box still earns vigor.
    act = machine.get("mem_active") or 0
    comp = machine.get("mem_compressed") or 0
    free = machine.get("mem_free") or 0
    total = act + comp + free
    if total > 0:
        n_mem = min(1.0, (comp / total) if comp > 0 else (act / total))
    else:
        n_mem = 0.0
    vigor01 = 0.5 * n_load + 0.3 * n_disk + 0.2 * n_mem
    return int(round(_VIGOR_MAX * vigor01))


def _score_from_dice(rarity: dict, machine: dict) -> int:
    """The single source of truth for the derived score — MUST mirror
    docs/js/rarity-helpers.js `compute`. luck + vigor + machine_signature +
    (celestial capped) + machine-extreme + entropy-pattern + sigil; a sigil
    floors the result to at least Rare. Clamped 0-255. ``machine`` carries the
    entropy (for luck) and the vitals (for vigor)."""
    machine = machine or {}
    s = _entropy_luck(machine.get("entropy", ""))
    s += _machine_vigor(machine)
    s += rarity.get("machine_signature", 0) or 0
    cel = sum((t.get("points", 0) or 0) for t in (rarity.get("celestial") or []))
    s += min(_CELESTIAL_CAP, cel)
    s += sum((t.get("points", 0) or 0) for t in (rarity.get("machine") or []))
    s += sum((t.get("points", 0) or 0) for t in (rarity.get("entropy") or []))
    sigil = rarity.get("sigil")
    if sigil:
        s += sigil.get("points", 0) or 0
        s = max(s, _RARE_FLOOR)
    return min(255, max(0, int(s)))


def compute_rarity(born):
    """Compute composite rarity from three dice + sigil + machine
    baseline.

    Pipeline:
      1. Build candidate trait lists from celestial / machine /
         entropy conditions.
      2. Pass celestial + machine candidates through the per-mint
         gate (see ``_gate``). The condition makes the trait
         *eligible*; the gate decides whether this conception
         catches it. Entropy traits and the sigil are inherently
         random per-mint (they read the kernel entropy bytes
         directly) — not re-gated.
      3. Add a small machine-baseline signature (1-5 pts) so every
         mint has the machine present in its score, not just the
         outliers.

    Returns:
        {
            "score": 0-255 (clamped),
            "celestial": [{"trait": "...", "points": N}, ...],
            "machine": [{"trait": "...", "points": N}, ...],
            "machine_signature": 1-5,
            "entropy": [{"trait": "...", "points": N}, ...],
            "sigil": {"found": "ad4e", "position": N, "points": 10} or None,
        }
    """
    entropy_hex = born.get("machine", {}).get("entropy", "")

    # Candidates: deterministic on the condition
    celestial_raw = _check_celestial(born)
    machine_raw = _check_machine(born.get("machine", {}))
    entropy_raw = _check_entropy(entropy_hex)
    sigil = _check_sigil(entropy_hex)

    # Per-mint gate — only the celestial and machine traits need it
    # (those conditions persist across mints). Entropy + sigil derive
    # from the per-mint entropy bytes and are already random per roll.
    celestial_raw = _apply_gate(celestial_raw, entropy_hex)
    machine_raw = _apply_gate(machine_raw, entropy_hex)

    # Machine baseline — small per-mint contribution, always present
    machine_sig = _machine_signature(entropy_hex, born.get("machine", {}))

    # Format as structured dicts
    celestial = [{"trait": name, "points": pts} for name, pts in celestial_raw]
    machine = [{"trait": name, "points": pts} for name, pts in machine_raw]
    entropy = [{"trait": name, "points": pts} for name, pts in entropy_raw]

    # The STORED dice dict — fields unchanged from v1 (luck + vigor are derived
    # from entropy + vitals already in the soul, never persisted, so the schema
    # and every record's content hash are untouched).
    dice = {
        "celestial": celestial,
        "machine": machine,
        "machine_signature": machine_sig,
        "entropy": entropy,
        "sigil": sigil,
    }
    machine_state = born.get("machine", {})
    # Score is derived (mirror in rarity-helpers.js). luck + vigor are returned
    # for callers/visibility but are NOT part of the stored dict above.
    return {
        **dice,
        "score": _score_from_dice(dice, machine_state),
        "luck": _entropy_luck(machine_state.get("entropy", "")),
        "vigor": _machine_vigor(machine_state),
    }


def get_rarity_tier(score):
    """Return (tier_name, hex_color) for a given rarity score."""
    result = TIERS[0]
    for threshold, name, color in TIERS:
        if score >= threshold:
            result = (name, color)
    return result
