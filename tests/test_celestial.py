"""Tests for mememage.celestial — astronomical math and birth certificate."""

import unittest
from datetime import datetime, timezone

from mememage.celestial import (
    ZODIAC,
    _julian_date,
    _julian_centuries,
    _moon_phase_name,
    _sun_ecliptic_lon,
    _moon_ecliptic_lon,
    _to_zodiac,
)


class TestJulianDate(unittest.TestCase):
    def test_j2000_epoch(self):
        """J2000.0 epoch (2000-01-01T12:00:00Z) should give JD 2451545.0."""
        dt = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        jd = _julian_date(dt)
        self.assertAlmostEqual(jd, 2451545.0, places=3)

    def test_known_date(self):
        """2024-03-20T00:00:00Z should give approximately JD 2460389.5."""
        dt = datetime(2024, 3, 20, 0, 0, 0, tzinfo=timezone.utc)
        jd = _julian_date(dt)
        self.assertAlmostEqual(jd, 2460389.5, places=1)

    def test_julian_centuries_at_epoch(self):
        """At J2000.0, T should be 0."""
        T = _julian_centuries(2451545.0)
        self.assertAlmostEqual(T, 0.0, places=10)


class TestZodiac(unittest.TestCase):
    def test_zero_degrees_is_aries(self):
        sign, deg = _to_zodiac(0)
        self.assertEqual(sign, "Aries")
        self.assertAlmostEqual(deg, 0.0)

    def test_exactly_30_is_taurus(self):
        sign, deg = _to_zodiac(30.0)
        self.assertEqual(sign, "Taurus")
        self.assertAlmostEqual(deg, 0.0)

    def test_negative_wraps(self):
        sign, _ = _to_zodiac(-10)
        self.assertEqual(sign, "Pisces")


class TestMoonPhase(unittest.TestCase):
    def test_new_moon(self):
        self.assertEqual(_moon_phase_name(0), "New Moon")

    def test_full_moon(self):
        self.assertEqual(_moon_phase_name(180), "Full Moon")

    def test_first_quarter(self):
        self.assertEqual(_moon_phase_name(90), "First Quarter")

    def test_last_quarter(self):
        self.assertEqual(_moon_phase_name(270), "Last Quarter")

    def test_waxing_crescent(self):
        self.assertEqual(_moon_phase_name(60), "Waxing Crescent")


class TestSunPosition(unittest.TestCase):
    def test_vernal_equinox_2024(self):
        """Sun should be near 0 degrees (Aries) at vernal equinox ~March 20."""
        dt = datetime(2024, 3, 20, 3, 6, 0, tzinfo=timezone.utc)
        T = _julian_centuries(_julian_date(dt))
        lon = _sun_ecliptic_lon(T)
        # Should be very close to 0 (Aries 0)
        self.assertAlmostEqual(lon % 360, 0.0, delta=1.0)

    def test_summer_solstice_2024(self):
        """Sun should be near 90 degrees (Cancer 0) at summer solstice ~June 20."""
        dt = datetime(2024, 6, 20, 20, 51, 0, tzinfo=timezone.utc)
        T = _julian_centuries(_julian_date(dt))
        lon = _sun_ecliptic_lon(T)
        self.assertAlmostEqual(lon, 90.0, delta=1.0)


class TestMoonPosition(unittest.TestCase):
    def test_moon_in_valid_range(self):
        """Moon longitude should be between 0 and 360."""
        dt = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        T = _julian_centuries(_julian_date(dt))
        lon = _moon_ecliptic_lon(T)
        self.assertGreaterEqual(lon, 0)
        self.assertLess(lon, 360)


class TestBirthCertificateStructure(unittest.TestCase):
    def test_has_expected_keys(self):
        """compute_birth_certificate should return all expected keys."""
        from unittest.mock import patch
        # Mock machine vitals and time-lock (GPS is now caller-provided)
        with patch("mememage.celestial._machine_vitals", return_value={"cpu": "test", "entropy": "aa" * 32}), \
             patch("mememage.celestial.lock_gps", return_value={"N": "0x1", "t": 100, "ct": "0xaabb", "len": 17}):
            from mememage.celestial import compute_birth_certificate
            cert = compute_birth_certificate(
                gps=(37.7, -122.4),
                dt=datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
            )

        # GPS no longer lives inside birth — it's at top-level on the
        # record (gps_time_locked + gps_password_locked) so chains with
        # gps_source: "none" produce a symmetric birth dict.
        expected_keys = {"sun", "moon", "moon_phase", "mercury",
                         "venus", "mars", "jupiter", "saturn", "angular_spread",
                         "machine"}
        self.assertTrue(expected_keys.issubset(set(cert.keys())))
        self.assertNotIn("gps_time_locked", cert)
        self.assertNotIn("gps_locked", cert)
        # Planetary positions stored as {"sign": int(0-11), "deg": float}
        # dicts — display layers reconstruct "Aries 24.3°" via the
        # zodiac_name helper. Storing codes makes the record machine-
        # comparable and removes parse-fragility on the verify side.
        for k in ("sun", "moon", "mercury", "venus", "mars", "jupiter", "saturn"):
            self.assertIsInstance(cert[k], dict, f"{k} should be a coded dict")
            self.assertIn("sign", cert[k])
            self.assertIn("deg", cert[k])
            self.assertIsInstance(cert[k]["sign"], int)
            self.assertGreaterEqual(cert[k]["sign"], 0)
            self.assertLess(cert[k]["sign"], 12)
            self.assertGreaterEqual(cert[k]["deg"], 0.0)
            self.assertLess(cert[k]["deg"], 30.0)
        # Moon phase: {"phase": int(0-7), "illum": float(0-1)}
        self.assertIsInstance(cert["moon_phase"], dict)
        self.assertIn(cert["moon_phase"]["phase"], range(8))
        self.assertGreaterEqual(cert["moon_phase"]["illum"], 0.0)
        self.assertLessEqual(cert["moon_phase"]["illum"], 1.0)


if __name__ == "__main__":
    unittest.main()
