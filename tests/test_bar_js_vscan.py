"""JS decoder vertical-scan must find a bar moved off the bottom.

The encoder always bottom-anchors the bar; the decoder (Python extract_bar and
its JS twin in image-decode.js:decodeImageBar) falls back to a vertical scan so
an image still reads if the bar was relocated or content was appended below it
after minting. Drives the node harness bar_vscan.cjs, which validates both the
sequential and even-fill layouts. Skips cleanly if Node is unavailable.
"""
import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, "bar_vscan.cjs")
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "Node.js required for JS decode parity")
class TestBarJsVscan(unittest.TestCase):
    def test_vertical_scan_finds_moved_bar(self):
        r = subprocess.run([NODE, HARNESS], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("VSCAN TESTS PASSED", r.stdout)


if __name__ == "__main__":
    unittest.main()
