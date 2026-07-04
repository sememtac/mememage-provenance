"""Rarity forecast — Monte-Carlo distribution for the next mint.

Given the current sky + machine state, simulate N mints with fresh
entropy each and report the tier distribution. Lets a creator see
"if I conceive now, what should I expect?" without any guarantee
of the actual outcome (the gate is per-mint and unpredictable —
the forecast is a probability cloud).

The sky and machine conditions are fixed for the call; only the
entropy varies. That mirrors the real conception flow where the
candidate traits are determined by the moment, but the gate seeded
by the kernel's 32 random bytes decides which traits land.
"""

from __future__ import annotations

import os
from collections import Counter

from mememage.celestial import compute_birth_certificate
from mememage.rarity import (
    TIERS,
    _check_celestial,
    _check_entropy,
    _check_sigil,
    _check_machine,
    _GATE_BY_VALUE,
    compute_rarity,
)
from mememage.vitals import collect_vitals


def _tier_for(score: int) -> str:
    name = TIERS[0][1]
    for threshold, t_name, _ in TIERS:
        if score >= threshold:
            name = t_name
    return name


def forecast(n: int = 10000, born: dict | None = None) -> dict:
    """Sample N mints against the current (or provided) born dict.

    ``born`` defaults to ``compute_birth_certificate(None)`` — today's
    sky + the live machine snapshot. Pass an explicit ``born`` to
    forecast a scenario other than "right now."

    Returns a dict with tier percentages, score quantiles, the
    candidate traits the conditions allow, and the per-trait fire
    rate observed across the simulation.
    """
    if born is None:
        born = compute_birth_certificate(None)

    # Snapshot the candidate traits — these don't vary per mint
    # (entropy isn't part of the condition check, only the gate).
    candidates_celestial = _check_celestial(born)
    candidates_machine = _check_machine(born.get("machine", {}))

    machine = dict(born.get("machine", {}))
    sim_born = dict(born)
    sim_born["machine"] = machine

    tier_counts = Counter()
    scores = []
    celestial_fires = Counter()
    machine_fires = Counter()
    sigil_hits = 0
    entropy_fires = Counter()

    for _ in range(n):
        machine["entropy"] = os.urandom(32).hex()
        r = compute_rarity(sim_born)
        scores.append(r["score"])
        tier_counts[_tier_for(r["score"])] += 1
        for t in r.get("celestial") or []:
            celestial_fires[t["trait"]] += 1
        for t in r.get("machine") or []:
            machine_fires[t["trait"]] += 1
        for t in r.get("entropy") or []:
            entropy_fires[t["trait"]] += 1
        if r.get("sigil"):
            sigil_hits += 1

    scores.sort()

    return {
        "n": n,
        "mean": sum(scores) / n,
        "median": scores[n // 2],
        "p90": scores[int(n * 0.90)],
        "p99": scores[int(n * 0.99)],
        "max": scores[-1],
        "min": scores[0],
        "tier_pct": {name: round(100 * tier_counts.get(name, 0) / n, 2)
                     for _, name, _ in TIERS},
        "candidates_celestial": [{"trait": t, "value": v, "gate_pct": round(100 * _GATE_BY_VALUE.get(v, 0.25), 1)}
                                 for t, v in candidates_celestial],
        "candidates_machine": [{"trait": t, "value": v, "gate_pct": round(100 * _GATE_BY_VALUE.get(v, 0.25), 1)}
                               for t, v in candidates_machine],
        "fire_rate_celestial": {name: round(100 * c / n, 1) for name, c in celestial_fires.items()},
        "fire_rate_machine":   {name: round(100 * c / n, 1) for name, c in machine_fires.items()},
        "fire_rate_entropy":   {name: round(100 * c / n, 2) for name, c in entropy_fires.items()},
        "sigil_pct": round(100 * sigil_hits / n, 3),
    }


def print_forecast(report: dict) -> None:
    """Render a forecast dict to stdout. Used by the CLI + ad-hoc."""
    print(f"\n  Rarity forecast — {report['n']} simulated mints against current conditions")
    print(f"  {'='*72}")
    print(f"  Score range: {report['min']}–{report['max']}    "
          f"mean {report['mean']:.1f}    median {report['median']}    "
          f"p90 {report['p90']}    p99 {report['p99']}")
    print()
    print(f"  Tier distribution:")
    for _, name, _ in TIERS:
        pct = report["tier_pct"].get(name, 0)
        bar = "█" * int(pct / 2)
        print(f"    {name:12} {pct:6.2f}%  {bar}")
    print()
    print(f"  Sigil (0xAD4E in entropy): {report['sigil_pct']:.3f}% per mint")
    print()
    cels = report["candidates_celestial"]
    if cels:
        print(f"  Eligible celestial traits (sky conditions hold):")
        for c in cels:
            obs = report["fire_rate_celestial"].get(c["trait"], 0)
            print(f"    +{c['value']:2}  gate {c['gate_pct']:5.1f}%   observed {obs:5.1f}%   {c['trait']}")
        print()
    machs = report["candidates_machine"]
    if machs:
        print(f"  Eligible machine traits (live state):")
        for c in machs:
            obs = report["fire_rate_machine"].get(c["trait"], 0)
            print(f"    +{c['value']:2}  gate {c['gate_pct']:5.1f}%   observed {obs:5.1f}%   {c['trait']}")
        print()
    if not cels and not machs:
        print("  (no candidate traits — score will come from machine signature 1-5 + rare entropy hits)")
        print()


if __name__ == "__main__":
    print_forecast(forecast())
