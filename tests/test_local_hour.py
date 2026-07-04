"""Regression: temperament's local_hour must be the machine's LOCAL hour,
not UTC.

state._now is UTC (it stamps the conceived timestamp). Reading its .hour for
"local_hour" mislabeled a 5 PM PDT conception (00:54Z) as hour 0 -> the
night_owl ("nocturnal birth") trait fired in broad daylight. The fix reads
datetime.now() (system-local). This pins it so nobody reverts to the UTC hour.
"""

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from mememage import core


class LocalHourIsLocal(unittest.TestCase):
    def test_local_hour_uses_local_not_utc(self):
        state = core.ConceptionState(metadata={}, gps=None)
        state.birth = {"machine": {}}
        # UTC hour 2 (would be night_owl); local hour 14 (afternoon).
        state._now = datetime(2026, 6, 7, 2, 0, 0, tzinfo=timezone.utc)

        class _FakeDT:
            @staticmethod
            def now(tz=None):
                if tz is not None:
                    return datetime(2026, 6, 7, 2, 0, 0, tzinfo=tz)   # UTC
                return datetime(2026, 6, 6, 14, 0, 0)                 # local

        with patch.object(core, "datetime", _FakeDT), \
             patch.object(core, "compute_machine_fingerprint", lambda m: "fp"), \
             patch.object(core, "update_personality", lambda m: {}), \
             patch.object(core, "read_birth_temperament", lambda m: {}):
            core._step_identity(state)

        self.assertEqual(state.machine["local_hour"], 14)   # local, not UTC's 2


if __name__ == "__main__":
    unittest.main()
