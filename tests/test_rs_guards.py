"""Reed-Solomon refuses a request it cannot honour.

Core audit round 5 (2026-07-28). The maths held up under 400 random payloads —
every round-trip exact, every error inside capacity corrected, none returning
wrong data — but the entry points accepted three requests they could not honour:

  * `nsym <= 0` returned the data with NO parity bytes, and `rs_decode` accepted
    it back. A caller who passed a bad count got zero error correction and no
    hint. A codec that believes it is protected but is not is worse than one
    that stops.
  * an EMPTY codeword raised `IndexError: list index out of range` out of the
    syndrome loop — an internal leak, not an answer.
  * a codeword at or under nsym (say 2 bytes with nsym=6) returned `b""`, which
    reads like a successful decode of an empty payload. That is the dangerous
    one: it is silence dressed as success.

`rs.py` ships in the core package, so `from mememage.rs import rs_encode,
rs_decode` is reachable by anyone. The JS mirror carries the same guards.
"""
import random
import unittest

from mememage import bar
from mememage.rs import rs_decode, rs_encode

NSYM = bar._RS_NSYM


class TestRsRefusesImpossibleRequests(unittest.TestCase):

    def test_non_positive_nsym_is_refused(self):
        """Silently returning unprotected data is the failure being prevented."""
        for nsym in (0, -1, -6):
            with self.subTest(nsym=nsym):
                with self.assertRaises(ValueError):
                    rs_encode(b"abc", nsym)
                with self.assertRaises(ValueError):
                    rs_decode(b"abcdefghij", nsym)

    def test_codeword_shorter_than_its_parity_is_refused(self):
        for length in (0, 1, 2, NSYM - 1):
            with self.subTest(codeword_len=length):
                with self.assertRaises(ValueError):
                    rs_decode(b"\x01" * length, NSYM)

    def test_an_empty_payload_round_trips(self):
        """len(codeword) == nsym is a VALID empty payload, not a short codeword.
        The existing suite (test_rs.py::test_empty_data) caught a first guard
        that refused it."""
        self.assertEqual(rs_decode(rs_encode(b"", NSYM), NSYM), b"")

    def test_the_shortest_valid_codeword_still_decodes(self):
        """One payload byte plus parity — the boundary the guard must not eat."""
        cw = rs_encode(b"A", NSYM)
        self.assertEqual(len(cw), 1 + NSYM)
        self.assertEqual(rs_decode(cw, NSYM), b"A")

    def test_truncation_never_returns_wrong_data(self):
        cw = rs_encode(b"hello world", NSYM)
        for cut in range(NSYM + 1, len(cw)):
            with self.subTest(truncated_to=cut):
                try:
                    self.assertEqual(rs_decode(cw[:cut], NSYM), b"hello world")
                except ValueError:
                    pass          # refusing is the other acceptable answer


class TestRsMathHolds(unittest.TestCase):
    """The properties the guards must not break."""

    def test_round_trip_over_random_payloads(self):
        rnd = random.Random(5)
        for _ in range(120):
            payload = bytes(rnd.randrange(256) for _ in range(rnd.randrange(1, 60)))
            self.assertEqual(rs_decode(rs_encode(payload, NSYM), NSYM), payload)

    def test_errors_within_capacity_are_corrected(self):
        rnd = random.Random(7)
        for _ in range(120):
            payload = bytes(rnd.randrange(256) for _ in range(rnd.randrange(8, 40)))
            cw = bytearray(rs_encode(payload, NSYM))
            for pos in rnd.sample(range(len(cw)), rnd.randrange(1, NSYM // 2 + 1)):
                cw[pos] ^= rnd.randrange(1, 256)
            self.assertEqual(rs_decode(bytes(cw), NSYM), payload)

    def test_uniform_payloads(self):
        for payload in (b"\x00" * 40, b"\xff" * 40):
            with self.subTest(payload=payload[:1]):
                self.assertEqual(rs_decode(rs_encode(payload, NSYM), NSYM), payload)

    def test_gf_limit_is_enforced(self):
        with self.assertRaises(ValueError):
            rs_encode(bytes(250), NSYM)              # 250 + 6 > 255
        self.assertEqual(len(rs_encode(bytes(249), NSYM)), 255)


if __name__ == "__main__":
    unittest.main()
