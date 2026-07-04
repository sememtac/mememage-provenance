"""Time-lock puzzle for GPS coordinates.

Implements Rivest-Shamir-Wagner time-lock encryption (1996).
The encryptor uses an RSA trapdoor to quickly compute a key that
would take ~years of sequential computation to derive without it.

The puzzle: compute 2^(2^t) mod N by repeated squaring.
With the trapdoor (p, q): compute e = 2^t mod phi(N), then 2^e mod N. Instant.
Without the trapdoor: must perform t sequential squarings. No parallelism possible.
"""

import hashlib
import os
import secrets


def _generate_prime(bits):
    """Generate a probable prime of the given bit length.

    Uses Miller-Rabin via Python's built-in pow() with modular exponentiation.
    """
    while True:
        # Generate odd random number of correct bit length
        n = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if _is_probable_prime(n):
            return n


def _is_probable_prime(n, rounds=20):
    """Miller-Rabin primality test."""
    if n < 2:
        return False
    if n == 2 or n == 3:
        return True
    if n % 2 == 0:
        return False

    # Write n-1 as 2^r * d
    r, d = 0, n - 1
    while d % 2 == 0:
        r += 1
        d //= 2

    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def lock_gps(lat, lon, t_squarings):
    """Encrypt GPS coordinates behind a time-lock puzzle.

    Args:
        lat: latitude (float)
        lon: longitude (float)
        t_squarings: number of sequential squarings required to unlock.
            ~10^9 squarings/sec on modern hardware for 1024-bit modulus.
            10^18 ≈ ~10 years (accounting for hardware acceleration).

    Returns:
        dict with puzzle parameters (N, t, ciphertext). The trapdoor
        (p, q) is discarded — the only path to the plaintext is
        sequential computation.
    """
    # Generate RSA modulus (1024-bit primes → 2048-bit modulus)
    p = _generate_prime(1024)
    q = _generate_prime(1024)
    N = p * q
    phi_N = (p - 1) * (q - 1)

    # The time-lock: key = 2^(2^t) mod N
    # With trapdoor: e = 2^t mod phi(N), key = 2^e mod N
    e = pow(2, t_squarings, phi_N)
    key_int = pow(2, e, N)

    # Derive a symmetric key from the puzzle solution
    key_bytes = hashlib.sha256(key_int.to_bytes((N.bit_length() + 7) // 8, "big")).digest()

    # Encrypt GPS coordinates (XOR — plaintext is tiny, key is 32 bytes)
    # Salt with 8 random bytes so identical coordinates produce different ciphertexts.
    # Cracking one image's puzzle reveals only that image's coordinates —
    # no correlation possible across images even from the same location.
    salt = os.urandom(8)
    plaintext = salt + f"{lat:.6f},{lon:.6f}".encode("utf-8")
    # Pad plaintext to 32 bytes for clean XOR
    padded = plaintext.ljust(32, b"\x00")
    ciphertext = bytes(a ^ b for a, b in zip(padded, key_bytes))

    # Discard the trapdoor. p and q die here.
    # From this point, the only way to recover key_int is
    # t sequential squarings: start with 2, square mod N, repeat t times.

    return {
        "N": hex(N),
        "t": t_squarings,
        "ct": ciphertext.hex(),
        "salt_len": 8,
        "len": len(plaintext),
    }


def unlock_gps(puzzle: dict) -> str:
    """Unlock a time-locked GPS puzzle by brute-force sequential squaring.

    This takes ~10-15 years on current hardware. Provided for completeness
    so the decryption protocol is not lost.

    Args:
        puzzle: dict with keys N (hex), t (int), ct (hex), salt_len (int), len (int)

    Returns:
        GPS string like "-45.123456,170.123456"
    """
    N = int(puzzle["N"], 16)
    t = puzzle["t"]
    ct = bytes.fromhex(puzzle["ct"])
    salt_len = puzzle["salt_len"]
    plaintext_len = puzzle["len"]

    # Sequential squaring: start with 2, square mod N, repeat t times
    val = 2
    for i in range(t):
        val = pow(val, 2, N)
        if i % 10_000_000 == 0 and i > 0:
            print(f"  Progress: {i:,} / {t:,} squarings ({100*i/t:.6f}%)")

    key_bytes = hashlib.sha256(val.to_bytes((N.bit_length() + 7) // 8, "big")).digest()

    # XOR decrypt, strip padding and salt
    padded = bytes(a ^ b for a, b in zip(ct, key_bytes))
    plaintext = padded[:plaintext_len]
    gps_str = plaintext[salt_len:].decode("utf-8")
    return gps_str
