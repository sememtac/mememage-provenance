"""Tests for mememage rarity system — 3 dice × 3 faces + sigil."""

import unittest

from mememage.rarity import compute_rarity, get_rarity_tier


class TestRarityTier(unittest.TestCase):
    def test_common(self):
        assert get_rarity_tier(0) == ("Common", "#a0a0a0")
        assert get_rarity_tier(24) == ("Common", "#a0a0a0")

    def test_uncommon(self):
        assert get_rarity_tier(25) == ("Uncommon", "#4a9e4a")
        assert get_rarity_tier(39) == ("Uncommon", "#4a9e4a")

    def test_rare(self):
        assert get_rarity_tier(40) == ("Rare", "#4a7abe")
        assert get_rarity_tier(54) == ("Rare", "#4a7abe")

    def test_very_rare(self):
        assert get_rarity_tier(55) == ("Very Rare", "#8a4abe")
        assert get_rarity_tier(71) == ("Very Rare", "#8a4abe")

    def test_epic(self):
        assert get_rarity_tier(72) == ("Epic", "#be8a1a")
        assert get_rarity_tier(87) == ("Epic", "#be8a1a")

    def test_legendary(self):
        assert get_rarity_tier(88) == ("Legendary", "#be2a2a")
        assert get_rarity_tier(255) == ("Legendary", "#be2a2a")


class TestRarityStructure(unittest.TestCase):
    def test_returns_dict_with_required_keys(self):
        result = compute_rarity({})
        assert "score" in result
        assert "celestial" in result
        assert "machine" in result
        assert "entropy" in result
        assert "sigil" in result

    def test_empty_born_returns_zero(self):
        result = compute_rarity({})
        assert result["score"] == 0
        assert result["celestial"] == []
        assert result["machine"] == []
        assert result["entropy"] == []
        assert result["sigil"] is None


# ---------------------------------------------------------------------------
# Celestial die
# ---------------------------------------------------------------------------

class TestCelestialPhase(unittest.TestCase):
    def test_full_moon(self):
        born = {"moon_phase": "Full Moon (99.8%)"}
        result = compute_rarity(born)
        traits = [t["trait"] for t in result["celestial"]]
        assert any("Full Moon" in t for t in traits)
        assert result["score"] >= 15

    def test_new_moon(self):
        born = {"moon_phase": "New Moon (0.5%)"}
        result = compute_rarity(born)
        traits = [t["trait"] for t in result["celestial"]]
        assert any("New Moon" in t for t in traits)

    def test_normal_phase_no_trait(self):
        born = {"moon_phase": "Waxing Gibbous (75%)"}
        result = compute_rarity(born)
        traits = [t["trait"] for t in result["celestial"]]
        assert not any("Moon" in t for t in traits)


class TestCelestialAlignment(unittest.TestCase):
    def test_inner_conjunction_low_points(self):
        born = {"sun": "Aries 12.6°", "mercury": "Aries 20.0°"}
        result = compute_rarity(born)
        traits = [t for t in result["celestial"] if "Conjunction" in t["trait"] or "Cluster" in t["trait"]]
        assert len(traits) >= 1
        assert traits[0]["points"] == 5  # inner-inner = low

    def test_outer_conjunction_high_points(self):
        born = {"mars": "Taurus 10.0°", "jupiter": "Taurus 15.0°"}
        result = compute_rarity(born)
        traits = [t for t in result["celestial"] if "Outer Conjunction" in t["trait"]]
        assert len(traits) == 1
        assert traits[0]["points"] == 25

    def test_cross_conjunction_medium_points(self):
        born = {"sun": "Aries 12.6°", "saturn": "Aries 4.7°"}
        result = compute_rarity(born)
        traits = [t for t in result["celestial"] if "Cross Conjunction" in t["trait"]]
        assert len(traits) == 1
        assert traits[0]["points"] == 15

    def test_grand_conjunction(self):
        born = {
            "sun": "Aries 12.6°",
            "saturn": "Aries 4.7°",
            "mercury": "Aries 20.0°",
        }
        result = compute_rarity(born)
        traits = [t for t in result["celestial"] if "Grand Conjunction" in t["trait"]]
        assert len(traits) == 1
        assert traits[0]["points"] == 35

    def test_opposition(self):
        born = {"sun": "Aries 12.6°", "moon": "Libra 17.0°"}
        result = compute_rarity(born)
        traits = [t["trait"] for t in result["celestial"]]
        assert any("Opposition" in t for t in traits)


class TestCelestialDistribution(unittest.TestCase):
    def test_tight_spread(self):
        # All bodies within ~50 degrees
        born = {
            "sun": "Aries 10°", "moon": "Aries 20°",
            "mercury": "Aries 15°", "venus": "Taurus 5°",
            "mars": "Aries 25°", "jupiter": "Taurus 10°",
            "saturn": "Aries 5°",
            "angular_spread": 55.0,
        }
        result = compute_rarity(born)
        traits = [t["trait"] for t in result["celestial"]]
        assert any("Convergence" in t for t in traits)

    def test_moderate_spread(self):
        born = {"angular_spread": 85.0}
        result = compute_rarity(born)
        traits = [t["trait"] for t in result["celestial"]]
        assert any("Tight Cluster" in t for t in traits)

    def test_wide_spread_no_trait(self):
        born = {"angular_spread": 200.0}
        result = compute_rarity(born)
        traits = [t["trait"] for t in result["celestial"]]
        assert not any("spread" in t.lower() for t in traits)


# ---------------------------------------------------------------------------
# Machine die
# ---------------------------------------------------------------------------

class TestMachineSpeculation(unittest.TestCase):
    def test_speculative_frenzy(self):
        born = {"machine": {"speculative_pages": 28000}}
        result = compute_rarity(born)
        traits = [t["trait"] for t in result["machine"]]
        assert any("Frenzy" in t for t in traits)

    def test_speculative_silence(self):
        born = {"machine": {"speculative_pages": 300}}
        result = compute_rarity(born)
        traits = [t["trait"] for t in result["machine"]]
        assert any("Silence" in t for t in traits)

    def test_normal_speculation_no_trait(self):
        born = {"machine": {"speculative_pages": 5000}}
        result = compute_rarity(born)
        traits = [t["trait"] for t in result["machine"]]
        assert not any("Speculative" in t for t in traits)


class TestMachineSacrifice(unittest.TestCase):
    def test_ready_to_shed(self):
        born = {"machine": {"purgeable_pages": 12000}}
        result = compute_rarity(born)
        traits = [t["trait"] for t in result["machine"]]
        assert any("Shed" in t for t in traits)

    def test_holding_everything(self):
        born = {"machine": {"purgeable_pages": 5}}
        result = compute_rarity(born)
        traits = [t["trait"] for t in result["machine"]]
        assert any("Holding Everything" in t for t in traits)


class TestMachinePulse(unittest.TestCase):
    def test_bus_storm(self):
        born = {"machine": {"disk_io": "64 KB/t, 6000 tps, 375 MB/s"}}
        result = compute_rarity(born)
        traits = [t["trait"] for t in result["machine"]]
        assert any("Storm" in t for t in traits)

    def test_bus_silence(self):
        born = {"machine": {"disk_io": "4 KB/t, 2 tps, 0 MB/s"}}
        result = compute_rarity(born)
        traits = [t["trait"] for t in result["machine"]]
        assert any("Silence" in t for t in traits)


class TestMachineWindowsMemory(unittest.TestCase):
    """Windows ('other', platform code 2) has no Mach/proc page counters or
    disk tps; the die reads physical-memory load instead. (No `entropy` in
    these dicts → the per-mint gate is bypassed, so the trait fires when
    eligible.) mem_active/mem_free are bytes; ratios are unit-free so small
    integers stand in for the percentage."""

    def _born(self, used, free):
        return {"machine": {"platform": 2, "mem_active": used, "mem_free": free}}

    def test_memory_saturated(self):
        r = compute_rarity(self._born(used=95, free=5))      # 95%
        assert any("Saturated" in t["trait"] for t in r["machine"])

    def test_memory_wide_open(self):
        r = compute_rarity(self._born(used=4, free=96))      # 4%
        assert any("Wide Open" in t["trait"] for t in r["machine"])

    def test_normal_band_no_trait(self):
        r = compute_rarity(self._born(used=50, free=50))     # 50% — unremarkable
        assert not any("Memory" in t["trait"] for t in r["machine"])

    def test_only_fires_on_other_platform(self):
        # Same memory shape on darwin must NOT fire the Windows memory face.
        born = {"machine": {"platform": 0, "mem_active": 95, "mem_free": 5}}
        r = compute_rarity(born)
        assert not any("Memory" in t["trait"] for t in r["machine"])

    def test_vigor_responds_to_windows_memory(self):
        from mememage.rarity import _machine_vigor
        # Windows has no load / disk tps — vigor must still move with memory.
        busy = {"platform": 2, "mem_active": 90, "mem_free": 10}
        idle = {"platform": 2, "mem_active": 10, "mem_free": 90}
        assert _machine_vigor(busy) > _machine_vigor(idle)


# ---------------------------------------------------------------------------
# Entropy die
# ---------------------------------------------------------------------------

class TestEntropyRepetition(unittest.TestCase):
    def test_triple_identical(self):
        # 3 identical bytes = 6 identical hex chars (ababab)
        born = {"machine": {"entropy": "abababab112233445566778899aabbccddeeff001122334455667788990011ab"}}
        result = compute_rarity(born)
        traits = [t["trait"] for t in result["entropy"]]
        assert any("Triple Echo" in t for t in traits)

    def test_ascending_run(self):
        # bytes 0x10, 0x11, 0x12 = ascending
        born = {"machine": {"entropy": "10111200445566778899aabbccddeeff0011223344556677889900112233abcd"}}
        result = compute_rarity(born)
        traits = [t["trait"] for t in result["entropy"]]
        assert any("Ascending" in t for t in traits)


class TestEntropySymmetry(unittest.TestCase):
    def test_deep_mirror(self):
        # First 2 bytes (ab cd) == last 2 bytes reversed (cd ab)
        born = {"machine": {"entropy": "abcd2233445566778899aabbccddeeff0011223344556677889900112233cdab"}}
        result = compute_rarity(born)
        traits = [t["trait"] for t in result["entropy"]]
        assert any("Deep Mirror" in t for t in traits)

    def test_bookend(self):
        # First byte == last byte, but second != second-to-last
        born = {"machine": {"entropy": "ab112233445566778899aabbccddeeff0011223344556677889900112233ffab"}}
        result = compute_rarity(born)
        traits = [t["trait"] for t in result["entropy"]]
        assert any("Bookend" in t for t in traits)


class TestEntropyExtremes(unittest.TestCase):
    def test_meteor_storm(self):
        born = {"machine": {"entropy": "fbfcfd00112233445566778899aabbccddeeff0011223344556677889900112233"}}
        result = compute_rarity(born)
        traits = [t["trait"] for t in result["entropy"]]
        assert any("Meteor Storm" in t for t in traits)

    def test_high_tide(self):
        # All bytes > ~170 average → mean > 170
        born = {"machine": {"entropy": "c0c1c2c3c4c5c6c7c8c9cacbcccdcecfd0d1d2d3d4d5d6d7d8d9dadbdcdddedf"}}
        result = compute_rarity(born)
        traits = [t["trait"] for t in result["entropy"]]
        assert any("High Tide" in t for t in traits)

    def test_normal_entropy_no_traits(self):
        born = {"machine": {"entropy": "4a7f3c92b1e8d0568f21c7a4e53906bd92d1f4a83b67c80ea5f21d9473b60e8c"}}
        result = compute_rarity(born)
        traits = [t["trait"] for t in result["entropy"]]
        assert len(traits) == 0


# ---------------------------------------------------------------------------
# Sigil
# ---------------------------------------------------------------------------

class TestSigil(unittest.TestCase):
    def test_sigil_fires(self):
        born = {"machine": {"entropy": "0011ad4e33445566778899aabbccddeeff0011223344556677889900112233abcd"}}
        result = compute_rarity(born)
        assert result["sigil"] is not None
        assert result["sigil"]["found"] == "ad4e"
        assert result["sigil"]["points"] == 10

    def test_sigil_position(self):
        born = {"machine": {"entropy": "0011ad4e33445566778899aabbccddeeff0011223344556677889900112233abcd"}}
        result = compute_rarity(born)
        assert result["sigil"]["position"] == 4  # "0011" then "ad4e" at index 4

    def test_no_sigil(self):
        born = {"machine": {"entropy": "4a7f3c92b1e8d0568f21c7a4e53906bd92d1f4a83b67c80ea5f21d9473b60e8c"}}
        result = compute_rarity(born)
        assert result["sigil"] is None

    def test_sigil_contributes_to_score(self):
        born = {"machine": {"entropy": "ad4e0000000000000000000000000000000000000000000000000000000000000000"}}
        result = compute_rarity(born)
        assert result["sigil"] is not None
        assert result["score"] >= 10


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------

class TestCompositeRarity(unittest.TestCase):
    def test_high_tier_reachable_under_favorable_conditions(self):
        # Construct a maximally favorable cert: every condition that
        # could fire, fires. With the per-mint gate, no single record
        # is guaranteed to clip the max — but across enough entropy
        # variations, at least one should reach Epic. The test exists
        # to catch a regression where the gate becomes so strict that
        # high tiers are unreachable.
        def born(entropy):
            return {
                "moon_phase": "Full Moon (99.9%)",
                "sun": "Taurus 10°", "moon": "Scorpio 10°",
                "mars": "Taurus 12°", "jupiter": "Taurus 8°",
                "saturn": "Taurus 15°",
                "angular_spread": 45.0,
                "machine": {
                    "platform": "darwin",
                    "speculative_pages": 30000,
                    "purgeable_pages": 15000,
                    "disk_io": "64 KB/t, 6000 tps, 375 MB/s",
                    "entropy": entropy,
                }
            }
        import hashlib
        best = 0
        for i in range(400):
            # Varied entropy (the FIRST bytes drive luck, so sequential
            # f"{i:064x}" — all-zero high-order — would pin luck to 0).
            entropy = hashlib.sha256(str(i).encode()).hexdigest()
            result = compute_rarity(born(entropy))
            best = max(best, result["score"])
        # Epic threshold is 72 — reachable across 400 rolls under favorable
        # machine + sky + the luck jackpot.
        assert best >= 72, f"best score {best} below Epic threshold across 400 rolls"

    def test_score_never_exceeds_clamp(self):
        born = {
            "moon_phase": "Full Moon (99.9%)",
            "machine": {"entropy": "ad4eff" + "f0" * 30},
        }
        result = compute_rarity(born)
        assert 0 <= result["score"] <= 255

    def test_all_three_dice_contribute(self):
        born = {
            "moon_phase": "Full Moon (99.8%)",
            "angular_spread": 200.0,
            "machine": {
                "speculative_pages": 30000,
                "entropy": "4a7f3c92b1e8d0568f21c7a4e53906bd92d1f4a83b67c80ea5f21d9473b60e8c",
            }
        }
        result = compute_rarity(born)
        assert len(result["celestial"]) >= 1  # Full Moon
        assert len(result["machine"]) >= 1    # Speculative Frenzy
        # Entropy may or may not contribute

    def test_real_data(self):
        """Test with realistic birth certificate data."""
        born = {
            "sun": "Aries 12.6°",
            "moon": "Libra 17.0°",
            "moon_phase": "Full Moon (99.8%)",
            "mercury": "Sagittarius 11.4°",
            "venus": "Gemini 2.1°",
            "mars": "Pisces 10.5°",
            "jupiter": "Cancer 26.5°",
            "saturn": "Aries 4.7°",
            "angular_spread": 267.0,
            "machine": {
                "speculative_pages": 5000,
                "purgeable_pages": 2000,
                "disk_io": "128 KB/t, 45 tps, 12 MB/s",
                "entropy": "4a7f3c92b1e8d0568f21c7a4e53906bd92d1f4a83b67c80ea5f21d9473b60e8c",
            }
        }
        result = compute_rarity(born)
        traits = [t["trait"] for t in result["celestial"]]
        # Should detect: Full Moon, Sun+Saturn cross conjunction, Sun-Moon opposition
        assert any("Full Moon" in t for t in traits)
        assert any("Cross Conjunction" in t and "Saturn" in t for t in traits)
        assert any("Opposition" in t for t in traits)
        # v2: the sky NUDGES, it never jackpots — a celestial-heavy day is
        # capped, so it can't spike the tier. The traits are still recorded for
        # display; their score contribution is bounded.
        from mememage.rarity import _CELESTIAL_CAP
        cel_raw = sum(t["points"] for t in result["celestial"])
        assert cel_raw > _CELESTIAL_CAP  # this record really is celestial-heavy
        # ...yet its contribution to the score is clamped.
        assert get_rarity_tier(result["score"])[0] in (
            "Common", "Uncommon", "Rare", "Very Rare", "Epic", "Legendary")


class TestRarityV2(unittest.TestCase):
    """Luck-backbone model: a real distribution from entropy, machine vigor
    that rewards a busy box, a capped sky, and a sigil that floors to Rare."""

    def _idle(self, entropy):
        return {"machine": {"entropy": entropy, "load": [0.03, 0.04, 0.04],
                            "cores": {"total": 1}, "disk_io": {"tps": 0},
                            "mem_active": 200_000_000, "mem_free": 1_500_000_000,
                            "mem_compressed": 0, "platform": 1}}

    def test_not_all_common(self):
        # The whole point: an idle box still produces a spread, not 100% Common.
        import hashlib
        from collections import Counter
        tiers = Counter()
        for i in range(4000):
            e = hashlib.sha256(b"dist-%d" % i).hexdigest()
            s = compute_rarity(self._idle(e))["score"]
            tiers[get_rarity_tier(s)[0]] += 1
        common_frac = tiers["Common"] / 4000.0
        assert 0.55 < common_frac < 0.88, common_frac      # mostly, not entirely
        assert tiers["Uncommon"] > 0 and tiers["Rare"] > 0  # a real tail exists

    def test_celestial_contribution_capped(self):
        from mememage.rarity import _score_from_dice, _CELESTIAL_CAP
        machine = {"entropy": ""}  # luck 0, isolate celestial
        dice = {"celestial": [{"trait": "X", "points": 35},
                              {"trait": "Y", "points": 30}],
                "machine": [], "machine_signature": 0, "entropy": [], "sigil": None}
        assert _score_from_dice(dice, machine) == _CELESTIAL_CAP  # 65 → capped 15

    def test_sigil_floors_to_rare(self):
        from mememage.rarity import _score_from_dice
        # A bone-dry record (no luck, no traits) still reaches Rare on a sigil.
        dice = {"celestial": [], "machine": [], "machine_signature": 0,
                "entropy": [], "sigil": {"found": "ad4e", "position": 0, "points": 10}}
        tier, _ = get_rarity_tier(_score_from_dice(dice, {"entropy": ""}))
        assert tier in ("Rare", "Very Rare", "Epic", "Legendary")

    def test_busy_machine_outscores_idle(self):
        # Same entropy, different machine state — the busy box scores higher.
        e = "ff00" * 16
        busy = {"machine": {"entropy": e, "load": [3.5, 3.0, 2.5],
                            "cores": {"total": 2}, "disk_io": {"tps": 700},
                            "mem_active": 1_200_000_000, "mem_free": 100_000_000,
                            "mem_compressed": 400_000_000, "platform": 1}}
        assert compute_rarity(busy)["score"] > compute_rarity(self._idle(e))["score"]


if __name__ == "__main__":
    unittest.main()
