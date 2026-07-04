"""Py↔JS rarity-score parity.

The rarity score is DERIVED independently by mememage/rarity.py (mint side) and
docs/js/rarity-helpers.js (viewer side). If they drift, a genuine soul shows a
different tier in the viewer than the creator minted. This runs the actual JS
under node against the same records and asserts identical scores.
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from mememage.rarity import compute_rarity

_HELPERS = os.path.join(os.path.dirname(__file__), "..", "docs", "js", "rarity-helpers.js")

# Node harness: load the IIFE with `this` bound to a fresh root, then score
# each {rarity, machine} record via RarityScore.compute.
_NODE = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const root = {};
new Function(src).call(root);
const recs = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const out = recs.map(r => root.RarityScore.compute(r.rarity, r.machine));
process.stdout.write(JSON.stringify(out));
"""


def _machine(entropy, busy=False, kind="linux"):
    if kind == "windows":
        # No load / disk / compressor — just memory (used/available), platform 2.
        if busy:
            return {"entropy": entropy, "cores": {"total": 4}, "platform": 2,
                    "mem_active": 15_000_000_000, "mem_free": 1_000_000_000}
        return {"entropy": entropy, "cores": {"total": 4}, "platform": 2,
                "mem_active": 3_000_000_000, "mem_free": 13_000_000_000}
    if busy:
        return {"entropy": entropy, "load": [3.4, 3.0, 2.5], "cores": {"total": 2},
                "disk_io": {"tps": 650}, "mem_active": 1_200_000_000,
                "mem_free": 100_000_000, "mem_compressed": 400_000_000, "platform": 1}
    return {"entropy": entropy, "load": [0.05, 0.05, 0.05], "cores": {"total": 1},
            "disk_io": {"tps": 0}, "mem_active": 200_000_000,
            "mem_free": 1_500_000_000, "mem_compressed": 0, "platform": 1}


@unittest.skipUnless(shutil.which("node"), "node required for JS parity")
class RarityParity(unittest.TestCase):
    def test_scores_match(self):
        records = []   # what both sides score
        py_scores = []
        for i in range(300):
            entropy = hashlib.sha256(b"parity-%d" % i).hexdigest()
            busy = (i % 3 == 0)
            # Mix platforms so the vigor memory fallback (Windows: no compressor
            # stat) is exercised on both sides.
            kind = "windows" if (i % 4 == 0) else "linux"
            machine = _machine(entropy, busy=busy, kind=kind)
            # A few records carry a sigil / celestial so those paths are covered.
            born = {"machine": machine}
            if i % 50 == 0:
                # force a celestial trait + (occasionally) a sigil
                born.update({"moon_phase": "Full Moon (99.5%)",
                             "sun": "Aries 5°", "moon": "Libra 5°"})
                machine["entropy"] = "ad4e" + entropy[4:]  # sigil
            res = compute_rarity(born)
            dice = {k: res[k] for k in
                    ("celestial", "machine", "machine_signature", "entropy", "sigil")}
            records.append({"rarity": dice, "machine": machine})
            py_scores.append(res["score"])

        with tempfile.TemporaryDirectory() as d:
            harness = os.path.join(d, "h.js")
            recs = os.path.join(d, "r.json")
            with open(harness, "w") as f:
                f.write(_NODE)
            with open(recs, "w") as f:
                json.dump(records, f)
            proc = subprocess.run(
                ["node", harness, os.path.abspath(_HELPERS), recs],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            js_scores = json.loads(proc.stdout)

        self.assertEqual(len(js_scores), len(py_scores))
        mism = [(i, py_scores[i], js_scores[i])
                for i in range(len(py_scores)) if py_scores[i] != js_scores[i]]
        self.assertEqual(mism, [], f"Py↔JS score mismatch (first few): {mism[:5]}")


if __name__ == "__main__":
    unittest.main()
