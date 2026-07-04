"""Tests for song naming system."""
import pytest
from mememage.song import name_from_hash, WORD_A, WORD_B, WORD_C


class TestSongNaming:
    """Song name generation from content hash."""

    def test_deterministic(self):
        """Same hash always produces the same song name."""
        h = "a7f39c2d8b04e51a"
        assert name_from_hash(h) == name_from_hash(h)

    def test_different_hashes_different_names(self):
        """Different hashes produce different names (at scale)."""
        names = set()
        for i in range(100):
            names.add(name_from_hash(f"{i:016x}"))
        # With 32 × 24 × 16 = 12288 possible names, 100 should all be unique
        assert len(names) >= 95  # allow tiny collision margin

    def test_returns_string(self):
        """Song name is a non-empty string."""
        name = name_from_hash("0000000000000000")
        assert isinstance(name, str)
        assert len(name) > 0

    def test_format_two_words(self):
        """Song names are at least two words."""
        name = name_from_hash("abcdef1234567890")
        words = name.split(" ")
        assert len(words) >= 2

    def test_first_word_from_pool(self):
        """First word comes from WORD_A pool."""
        for i in range(50):
            name = name_from_hash(f"{i:016x}")
            first_word = name.split(" ")[0]
            assert first_word in WORD_A, f"'{first_word}' not in WORD_A"

    def test_second_word_from_pool(self):
        """Second word comes from WORD_B pool."""
        for i in range(50):
            name = name_from_hash(f"{i:016x}")
            second_word = name.split(" ")[1]
            assert second_word in WORD_B, f"'{second_word}' not in WORD_B"

    def test_optional_qualifier(self):
        """Some names have a third qualifier, some don't."""
        has_qualifier = 0
        no_qualifier = 0
        for i in range(200):
            name = name_from_hash(f"{i:016x}")
            words = name.split(" ")
            if len(words) > 2:
                has_qualifier += 1
            else:
                no_qualifier += 1
        # Both should occur (qualifiers have ~69% chance of being empty)
        assert has_qualifier > 0, "No names with qualifiers found"
        assert no_qualifier > 0, "No names without qualifiers found"

    def test_hash_salt_independence(self):
        """Song names don't correlate with constellation names from same hash."""
        # The song uses ":song" salt, constellation uses the raw hash.
        # Same input hash should produce different naming outputs.
        from mememage.constellation import name_from_hash as constellation_name
        h = "a7f39c2d8b04e51a"
        song = name_from_hash(h)
        constellation = constellation_name(h)
        # They should be completely different strings
        assert song != constellation
