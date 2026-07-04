"""Shared parsing helpers for machine vitals strings."""

import re


def parse_load(load_str: str) -> float:
    """Extract first number from '2.39 / 2.38 / 2.24'. Returns 0.0 on failure."""
    if not load_str:
        return 0.0
    m = re.search(r"[\d.]+", str(load_str))
    return float(m.group()) if m else 0.0


def parse_mb(mem_str: str) -> float:
    """Parse memory string to MB. '134 MB' → 134.0, '10.6 GB' → 10854.4.

    Returns 99999 on failure (safe default for < comparisons).
    """
    if not mem_str:
        return 99999.0
    val = str(mem_str).strip()
    m = re.search(r"([\d.]+)\s*(TB|GB|MB|KB|B)\b", val, re.IGNORECASE)
    if not m:
        # Try bare number
        try:
            return float(val)
        except ValueError:
            return 99999.0
    value = float(m.group(1))
    unit = m.group(2).upper()
    if unit == "TB":
        return value * 1024 * 1024
    if unit == "GB":
        return value * 1024
    if unit == "MB":
        return value
    if unit == "KB":
        return value / 1024
    return 0.0


def parse_gb(val_str: str) -> float:
    """Extract value as GB from '254.7 GB' or '1.2 TB'. Returns 0.0 on failure."""
    if not val_str:
        return 0.0
    m = re.search(r"([\d.]+)\s*(TB|GB|MB|KB|B)\b", str(val_str), re.IGNORECASE)
    if not m:
        return 0.0
    value = float(m.group(1))
    unit = m.group(2).upper()
    if unit == "TB":
        return value * 1024
    if unit == "GB":
        return value
    if unit == "MB":
        return value / 1024
    return 0.0
