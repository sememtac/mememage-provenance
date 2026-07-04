"""Tests for mememage.personality — machine fingerprint."""

import unittest

from mememage.personality import compute_machine_fingerprint


SAMPLE_VITALS = {
    "cpu": "Apple M2 Max",
    "cores": "12 (8 performance and 4 efficiency)",
    "gpu": "38",
    "ram": "32 GB",
    "cache": "16 MB",
    "load": "2.39 / 2.38 / 2.24",
    "mem_free": "134 MB",
    "power": "Battery Power (72%)",
    "mem_compressed": "2.1 GB",
    "net_rx": "254.7 GB",
}


class TestFingerprint(unittest.TestCase):
    def test_deterministic(self):
        fp1 = compute_machine_fingerprint(SAMPLE_VITALS)
        fp2 = compute_machine_fingerprint(SAMPLE_VITALS)
        self.assertEqual(fp1, fp2)

    def test_length_is_8_hex_chars(self):
        fp = compute_machine_fingerprint(SAMPLE_VITALS)
        self.assertEqual(len(fp), 8)
        self.assertTrue(all(c in "0123456789abcdef" for c in fp))

    def test_differs_for_different_hardware(self):
        other = dict(SAMPLE_VITALS, cpu="Intel Core i9-13900K")
        self.assertNotEqual(
            compute_machine_fingerprint(SAMPLE_VITALS),
            compute_machine_fingerprint(other),
        )

    def test_ignores_volatile_fields(self):
        """Fingerprint only uses stable keys — load, memory, power don't affect it."""
        calm = dict(SAMPLE_VITALS, load="0.10 / 0.10 / 0.10", mem_free="8000 MB", power="AC")
        self.assertEqual(
            compute_machine_fingerprint(SAMPLE_VITALS),
            compute_machine_fingerprint(calm),
        )


if __name__ == "__main__":
    unittest.main()
