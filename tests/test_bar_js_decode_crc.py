"""JS decoder must reject frames whose CRC fails after RS decode.

Regression for the false-positive-identifier bug: codec.js decodeFrame skipped
the post-RS CRC re-validation that bar.py _try_decode_frame does, so a wrong
bit-read during the threshold/offset sweep could RS-"correct" into a magic-
prefixed garbage frame, be accepted, and hand the browser a bogus identifier
(reported to the user as "could not find soul" for a valid mint). Drives the
node harness bar_decode_crc.cjs. Skips cleanly if Node is unavailable.
"""
import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, "bar_decode_crc.cjs")
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "Node.js required for JS decode parity")
class TestBarJsDecodeCrc(unittest.TestCase):
    def test_decode_frame_crc_revalidation(self):
        r = subprocess.run([NODE, HARNESS], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("DECODE-CRC TESTS PASSED", r.stdout)


if __name__ == "__main__":
    unittest.main()
