"""Machine personality — hardware fingerprint and behavioral trait tracking."""

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

from mememage.parsing import parse_gb, parse_load, parse_mb

PERSONALITY_FILE = Path("~/.mememage/personality.json").expanduser()

STABLE_KEYS = ("cpu", "cores", "gpu", "ram", "cache")


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def compute_machine_fingerprint(vitals: dict) -> str:
    """SHA-256 of sorted stable hardware traits, first 8 hex chars.

    Same machine always produces the same fingerprint.
    """
    parts = []
    for key in sorted(STABLE_KEYS):
        if key in vitals:
            parts.append(f"{key}={vitals[key]}")
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:8]


def update_personality(vitals: dict, personality_file: Path | None = None) -> dict:
    """Record this generation's vitals, update rolling trait percentages.

    Returns dict with: fingerprint, generations (count), traits (dict of
    percentages).  Persists to ~/.mememage/personality.json using atomic write.
    """
    path = personality_file if personality_file is not None else PERSONALITY_FILE

    # Load existing personality or start fresh
    _fresh = {
        "fingerprint": "",
        "generations": 0,
        "trait_counts": {},
        "traits": {},
    }
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                personality = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Personality file corrupt, starting fresh: %s", e)
            personality = _fresh
    else:
        personality = _fresh

    # Fingerprint
    personality["fingerprint"] = compute_machine_fingerprint(vitals)

    # Increment generation count
    personality["generations"] += 1
    total = personality["generations"]

    # Evaluate trait triggers for this generation
    # Key names match celestial.py _machine_vitals output
    # V1 storage: bytes (int) for mem/net, list[3] for load, dict for power.
    # Legacy strings still pass through parse_* helpers as fallback.
    def _bytes_or_legacy(val, legacy_parser, mult):
        if isinstance(val, (int, float)):
            return float(val) / mult
        return legacy_parser(val or ("0 MB" if mult == 1024 * 1024 else "0 GB"))

    def _load_avg(val):
        if isinstance(val, list) and val:
            try:
                return float(val[0])
            except (ValueError, TypeError):
                return 0.0
        return parse_load(val or "0")

    def _on_battery(val):
        if isinstance(val, dict):
            return val.get("src") == 1  # POWER_BATTERY
        return "Battery" in str(val or "")

    trait_triggers = {
        "high_load": _load_avg(vitals.get("load")) > 3.0,
        "low_memory": _bytes_or_legacy(vitals.get("mem_free"), parse_mb, 1024 * 1024) < 200,
        "on_battery": _on_battery(vitals.get("power")),
        "high_compression": _bytes_or_legacy(vitals.get("mem_compressed"), parse_gb, 1024 ** 3) > 4,
        "heavy_network": _bytes_or_legacy(vitals.get("net_rx"), parse_gb, 1024 ** 3) > 500,
    }

    counts = personality.get("trait_counts", {})
    for trait, active in trait_triggers.items():
        prev = counts.get(trait, 0)
        counts[trait] = prev + (1 if active else 0)
    personality["trait_counts"] = counts

    # Recompute percentages
    traits = {}
    for trait, count in counts.items():
        traits[trait] = count / total
    personality["traits"] = traits

    # Atomic write with file lock to prevent concurrent mint corruption
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(personality, f, indent=2)
        # Lock the target file during replace to serialize concurrent access
        try:
            import fcntl
            lock_fd = os.open(str(path), os.O_CREAT | os.O_RDWR)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                os.replace(tmp_path, path)
            finally:
                os.close(lock_fd)
        except (ImportError, OSError):
            # fcntl unavailable (Windows) or lock failed — fall back to bare replace
            os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    return personality
