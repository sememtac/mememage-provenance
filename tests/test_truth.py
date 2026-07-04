"""Tests for constellation pole special-casing (kept from former truth tests)."""

import pytest


class TestConstellationPoles:
    """Verify Maat and Isfet special-casing."""

    def test_maat(self):
        from mememage.constellation import name_from_hash
        assert name_from_hash('0000000000000000') == 'Maat'

    def test_isfet(self):
        from mememage.constellation import name_from_hash
        assert name_from_hash('ffffffffffffffff') == 'Isfet'

    def test_maat_ignores_age(self):
        from mememage.constellation import name_from_hash
        assert name_from_hash('0000000000000000', age=5) == 'Maat'

    def test_isfet_ignores_age(self):
        from mememage.constellation import name_from_hash
        assert name_from_hash('ffffffffffffffff', age=12) == 'Isfet'

    def test_near_poles_are_not_special(self):
        from mememage.constellation import name_from_hash
        assert name_from_hash('0000000000000001') != 'Maat'
        assert name_from_hash('fffffffffffffffe') != 'Isfet'
