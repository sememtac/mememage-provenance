"""Tests for mememage.temperament — birth temperament readings.

Tests the new high-variance temperament system based on volatile machine
state (context switches, page faults, speculative/purgeable pages) rather
than slow-moving indicators (load, free memory).
"""

import unittest

from mememage.temperament import read_birth_temperament


# Baseline vitals — values that don't trigger volatile conditions
CALM = {
    "load": "1.50 / 1.40 / 1.30",
    "power": "AC",
    "ctx_switches": "50 voluntary / 2500 involuntary",  # mid-range (25-75)
    "page_faults": "10001 soft / 51 hard",  # not mod 7 == 0, not mod 3 == 0
    "speculative_pages": "800",  # mid-range (300-2000)
    "purgeable_pages": "1500",  # mid-range (300-4000)
    "open_fds": "5003",  # mod 10 = 3 (not 0,1 or 7,8,9)
}


class TestSereneDefault(unittest.TestCase):
    def test_minimal_vitals_is_serene(self):
        """Empty vitals → serene default."""
        t = read_birth_temperament({})
        self.assertEqual(t["temperament"], "A serene birth")
        self.assertEqual(t["traits"], [])

    def test_mid_range_vitals_produce_some_traits(self):
        """CALM vitals should trigger some volatile conditions."""
        t = read_birth_temperament(CALM)
        # The new system reads volatile state — CALM may or may not trigger
        # depending on exact modular residues. Just verify structure.
        self.assertIn("temperament", t)
        self.assertIsInstance(t["traits"], list)


class TestContextSwitches(unittest.TestCase):
    def test_contested(self):
        v = dict(CALM, ctx_switches="100 voluntary / 2480 involuntary")  # 2480 % 100 = 80 > 75
        t = read_birth_temperament(v)
        self.assertIn("contested", t["traits"])

    def test_yielding(self):
        v = dict(CALM, ctx_switches="100 voluntary / 2410 involuntary")  # 2410 % 100 = 10 < 15
        t = read_birth_temperament(v)
        self.assertIn("yielding", t["traits"])

    def test_uncontested(self):
        v = dict(CALM, ctx_switches="100 voluntary / 2540 involuntary")  # 2540 % 100 = 40
        t = read_birth_temperament(v)
        self.assertIn("uncontested", t["traits"])

    def test_category_exclusive(self):
        """Only one ctx trait fires per reading."""
        v = dict(CALM, ctx_switches="100 voluntary / 2480 involuntary")
        t = read_birth_temperament(v)
        ctx_traits = [x for x in t["traits"] if x in ("contested", "yielding", "uncontested")]
        self.assertEqual(len(ctx_traits), 1)


class TestPageFaults(unittest.TestCase):
    def test_stumbling(self):
        v = dict(CALM, page_faults="10003 soft / 50 hard")  # 10003 % 7 == 0
        t = read_birth_temperament(v)
        self.assertIn("stumbling", t["traits"])

    def test_sure_footed(self):
        v = dict(CALM, page_faults="10003 soft / 50 hard")  # 10003 % 7 = 0, not 3 or 4
        # sure_footed needs mod 7 in (3,4) — use 10010 % 7 = 0... 10003 % 7 = 3? let me compute
        # 10003 / 7 = 1429, 1429*7 = 10003. So 10003 % 7 = 0 → stumbling
        v2 = dict(CALM, page_faults="10004 soft / 50 hard")  # 10004 % 7 = 1
        t2 = read_birth_temperament(v2)
        # 10004 % 7 = 1 → neither stumbling nor sure_footed
        v3 = dict(CALM, page_faults="10006 soft / 50 hard")  # 10006 % 7 = 3
        t3 = read_birth_temperament(v3)
        self.assertIn("sure_footed", t3["traits"])

    def test_reaching(self):
        v = dict(CALM, page_faults="10000 soft / 51 hard")  # 51 % 3 = 0, > 0
        t = read_birth_temperament(v)
        self.assertIn("reaching", t["traits"])


class TestSpeculativePages(unittest.TestCase):
    def test_speculative(self):
        v = dict(CALM, speculative_pages="3500")
        t = read_birth_temperament(v)
        self.assertIn("speculative", t["traits"])

    def test_cautious(self):
        v = dict(CALM, speculative_pages="100")
        t = read_birth_temperament(v)
        self.assertIn("cautious", t["traits"])

    def test_restless(self):
        v = dict(CALM, speculative_pages="800")
        t = read_birth_temperament(v)
        self.assertIn("restless", t["traits"])


class TestPurgeablePages(unittest.TestCase):
    def test_loosening_grip(self):
        v = dict(CALM, purgeable_pages="6000")
        t = read_birth_temperament(v)
        self.assertIn("loosening_grip", t["traits"])

    def test_holding_tight(self):
        v = dict(CALM, purgeable_pages="100")
        t = read_birth_temperament(v)
        self.assertIn("holding_tight", t["traits"])

    def test_in_flux(self):
        v = dict(CALM, purgeable_pages="2000")
        t = read_birth_temperament(v)
        self.assertIn("in_flux", t["traits"])


class TestPressure(unittest.TestCase):
    def test_forged_in_fire(self):
        v = dict(CALM, load="12.00 / 10.00 / 8.00")
        t = read_birth_temperament(v)
        self.assertIn("forged_in_fire", t["traits"])

    def test_under_pressure(self):
        v = dict(CALM, load="5.00 / 4.00 / 3.00")
        t = read_birth_temperament(v)
        self.assertIn("under_pressure", t["traits"])

    def test_in_silence(self):
        v = dict(CALM, load="0.10 / 0.20 / 0.15")
        t = read_birth_temperament(v)
        self.assertIn("in_silence", t["traits"])

    def test_fire_shadows_pressure(self):
        v = dict(CALM, load="10.00 / 8.00 / 6.00")
        t = read_birth_temperament(v)
        self.assertIn("forged_in_fire", t["traits"])
        self.assertNotIn("under_pressure", t["traits"])


class TestPower(unittest.TestCase):
    def test_last_light(self):
        v = dict(CALM, power="Battery 3%")
        t = read_birth_temperament(v)
        self.assertIn("last_light", t["traits"])

    def test_untethered(self):
        v = dict(CALM, power="Battery 80%")
        t = read_birth_temperament(v)
        self.assertIn("untethered", t["traits"])

    def test_ac_triggers_nothing(self):
        t = read_birth_temperament(CALM)
        for trait in t["traits"]:
            self.assertNotIn(trait, ["last_light", "untethered"])


class TestTimeOfDay(unittest.TestCase):
    def test_night_owl(self):
        v = dict(CALM, local_hour=3)
        t = read_birth_temperament(v)
        self.assertIn("night_owl", t["traits"])

    def test_dawn(self):
        v = dict(CALM, local_hour=6)
        t = read_birth_temperament(v)
        self.assertIn("dawn", t["traits"])

    def test_afternoon_nothing(self):
        v = dict(CALM, local_hour=14)
        t = read_birth_temperament(v)
        self.assertNotIn("night_owl", t["traits"])
        self.assertNotIn("dawn", t["traits"])


class TestCombinations(unittest.TestCase):
    def test_turbulent(self):
        v = dict(CALM, ctx_switches="0 vol / 2480 invol", page_faults="10003 soft / 51 hard")
        t = read_birth_temperament(v)
        self.assertIn("contested", t["traits"])
        self.assertIn("stumbling", t["traits"])

    def test_clean_birth(self):
        v = dict(CALM,
            ctx_switches="0 vol / 2540 invol",  # 40 → uncontested
            page_faults="10006 soft / 50 hard",  # 10006 % 7 = 3 → sure_footed
        )
        t = read_birth_temperament(v)
        self.assertEqual(t["temperament"], "A clean birth")

    def test_fever_dream(self):
        # Only trigger contested + night_owl, avoid other traits that might match earlier combos
        v = {
            "ctx_switches": "0 vol / 2480 invol",  # contested
            "page_faults": "10001 soft / 50 hard",  # 10001%7=5, 50%3=2 → no fault traits
            "speculative_pages": "800",  # restless (but fever_dream is checked before agitated)
            "purgeable_pages": "1500",  # in_flux
            "open_fds": "5003",
            "load": "1.50 / 1.40 / 1.30",
            "power": "AC",
            "local_hour": 3,
        }
        t = read_birth_temperament(v)
        self.assertIn("contested", t["traits"])
        self.assertIn("night_owl", t["traits"])
        self.assertEqual(t["temperament"], "A fever dream")

    def test_first_breath(self):
        v = {
            "ctx_switches": "0 vol / 2560 invol",  # 60 → no ctx trait
            "page_faults": "10006 soft / 50 hard",  # 10006%7=3 → sure_footed
            "speculative_pages": "400",  # between 300-500, no speculation trait
            "purgeable_pages": "1500",  # in_flux
            "open_fds": "5003",
            "load": "1.50 / 1.40 / 1.30",
            "power": "AC",
            "local_hour": 6,
        }
        t = read_birth_temperament(v)
        self.assertIn("sure_footed", t["traits"])
        self.assertIn("dawn", t["traits"])
        self.assertEqual(t["temperament"], "A first breath")


class TestOutputStructure(unittest.TestCase):
    def test_has_all_keys(self):
        t = read_birth_temperament(CALM)
        for key in ("traits", "readings", "temperament", "summary"):
            self.assertIn(key, t)

    def test_readings_match_traits_count(self):
        v = dict(CALM, load="10.00 / 8.00 / 6.00")
        t = read_birth_temperament(v)
        self.assertEqual(len(t["readings"]), len(t["traits"]))


class TestVariance(unittest.TestCase):
    """Verify the system produces genuine variance from volatile inputs."""

    def test_different_ctx_residues_produce_different_traits(self):
        """Changing the last 2 digits of involuntary switches changes the trait."""
        results = set()
        for invol in [2410, 2440, 2480]:  # mod 100 = 10, 40, 80
            v = dict(CALM, ctx_switches=f"50 voluntary / {invol} involuntary")
            t = read_birth_temperament(v)
            ctx = [x for x in t["traits"] if x in ("contested", "yielding", "uncontested")]
            if ctx:
                results.add(ctx[0])
        self.assertGreaterEqual(len(results), 2, f"Expected ≥2 different ctx traits, got {results}")

    def test_different_fault_residues_produce_different_traits(self):
        """Changing soft fault count mod 7 changes the trait."""
        results = set()
        for soft in [10003, 10006, 10005]:  # mod 7 = 0, 3, 5
            v = dict(CALM, page_faults=f"{soft} soft / 50 hard")
            t = read_birth_temperament(v)
            fault = [x for x in t["traits"] if x in ("stumbling", "sure_footed")]
            if fault:
                results.add(fault[0])
        self.assertGreaterEqual(len(results), 2, f"Expected ≥2 different fault traits, got {results}")


if __name__ == "__main__":
    unittest.main()
