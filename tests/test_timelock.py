"""Tests for mememage.timelock — RSA time-lock puzzle."""

import hashlib
import unittest

from mememage.timelock import _is_probable_prime, lock_gps


class TestMillerRabin(unittest.TestCase):
    def test_small_primes(self):
        for p in [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31]:
            self.assertTrue(_is_probable_prime(p), f"{p} should be prime")

    def test_small_composites(self):
        for n in [0, 1, 4, 6, 8, 9, 10, 12, 15, 25, 100]:
            self.assertFalse(_is_probable_prime(n), f"{n} should not be prime")

    def test_known_large_prime(self):
        # Mersenne prime 2^61 - 1
        self.assertTrue(_is_probable_prime(2**61 - 1))

    def test_carmichael_number(self):
        # 561 is the smallest Carmichael number — composite but fools Fermat
        # Miller-Rabin should catch it
        self.assertFalse(_is_probable_prime(561))


class TestLockGps(unittest.TestCase):
    def test_output_structure(self):
        """lock_gps should return dict with N, t, ct, len keys."""
        result = lock_gps(37.7749, -122.4194, t_squarings=100)
        self.assertIn("N", result)
        self.assertIn("t", result)
        self.assertIn("ct", result)
        self.assertIn("len", result)

    def test_t_preserved(self):
        result = lock_gps(0.0, 0.0, t_squarings=42)
        self.assertEqual(result["t"], 42)

    def test_n_is_hex_string(self):
        result = lock_gps(37.7, -122.4, t_squarings=100)
        self.assertTrue(result["N"].startswith("0x"))
        # 2048-bit modulus → ~617 hex digits
        n_val = int(result["N"], 16)
        self.assertGreater(n_val.bit_length(), 2000)

    def test_ciphertext_is_hex(self):
        result = lock_gps(37.7, -122.4, t_squarings=100)
        # ct should be valid hex
        bytes.fromhex(result["ct"])

    def test_len_matches_plaintext(self):
        lat, lon = 37.774900, -122.419400
        result = lock_gps(lat, lon, t_squarings=100)
        expected_gps = f"{lat:.6f},{lon:.6f}"
        # len = salt (8 bytes) + GPS string
        self.assertEqual(result["len"], 8 + len(expected_gps.encode("utf-8")))
        self.assertEqual(result["salt_len"], 8)

    def test_small_t_roundtrip(self):
        """With a tiny t, verify the puzzle can be solved by brute-force squaring."""
        lat, lon = 12.345678, -98.765432
        t = 100  # tiny — solvable instantly

        result = lock_gps(lat, lon, t_squarings=t)
        N = int(result["N"], 16)
        ct = bytes.fromhex(result["ct"])
        plaintext_len = result["len"]

        # Solve: compute 2^(2^t) mod N by sequential squaring
        val = 2
        for _ in range(t):
            val = pow(val, 2, N)

        key_bytes = hashlib.sha256(val.to_bytes(256, "big")).digest()

        # Decrypt — skip salt prefix to get GPS coordinates
        padded = bytes(a ^ b for a, b in zip(ct, key_bytes))
        salt_len = result["salt_len"]
        plaintext = padded[salt_len:plaintext_len].decode("utf-8")

        expected = f"{lat:.6f},{lon:.6f}"
        self.assertEqual(plaintext, expected)


    def test_salt_prevents_correlation(self):
        """Same coordinates should produce different ciphertexts each time."""
        lat, lon = 37.7749, -122.4194
        r1 = lock_gps(lat, lon, t_squarings=100)
        r2 = lock_gps(lat, lon, t_squarings=100)
        # Different N (new RSA keypair each time)
        self.assertNotEqual(r1["N"], r2["N"])
        # Different ciphertext (salt ensures this even if N happened to match)
        self.assertNotEqual(r1["ct"], r2["ct"])


if __name__ == "__main__":
    unittest.main()
