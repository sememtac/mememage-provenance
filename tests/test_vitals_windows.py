"""Vitals + forecast must not crash on Windows.

vitals.py has dedicated macOS (sysctl/vm_stat) and Linux (/proc) branches
and a generic catch-all for everything else. The cross-platform tail used
``os.getloadavg()`` — which doesn't EXIST on Windows (AttributeError, not
OSError), so catching only OSError let it crash the whole vitals snapshot,
which made the dashboard forecast "unavailable on Windows".

These tests simulate Windows on any host: platform.system() -> "Windows"
and the Unix-only os attributes removed.
"""

import os
import unittest
from unittest.mock import patch


class _SimulateWindows:
    """Context manager: platform=Windows + Unix-only os attrs gone."""

    _UNIX_ONLY = ("getloadavg", "uname")

    def __enter__(self):
        self._patch = patch("mememage.vitals.platform.system", return_value="Windows")
        self._patch.start()
        self._saved = {}
        for name in self._UNIX_ONLY:
            if hasattr(os, name):
                self._saved[name] = getattr(os, name)
                delattr(os, name)
        return self

    def __exit__(self, *exc):
        for name, fn in self._saved.items():
            setattr(os, name, fn)
        self._patch.stop()


class TestVitalsWindows(unittest.TestCase):
    def test_collect_vitals_does_not_raise(self):
        from mememage.vitals import collect_vitals, PLATFORM_OTHER
        with _SimulateWindows():
            vitals = collect_vitals()
        self.assertIsInstance(vitals, dict)
        self.assertEqual(vitals.get("platform"), PLATFORM_OTHER)
        self.assertIn("entropy", vitals)
        self.assertNotIn("load", vitals)  # no loadavg on Windows — skipped, not crashed

    def test_forecast_works(self):
        from mememage.forecast import forecast
        with _SimulateWindows():
            report = forecast(n=500)
        self.assertIn("tier_pct", report)
        self.assertIsInstance(report["tier_pct"], dict)
        # percentages sum to ~100 across the tiers
        self.assertAlmostEqual(sum(report["tier_pct"].values()), 100.0, delta=1.0)


if __name__ == "__main__":
    unittest.main()
