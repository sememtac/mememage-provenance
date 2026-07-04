"""On-disk .soul layout invariants.

The .soul file is reordered for human readability by
``_canonicalize_for_disk`` (cosmetic — hashing always sort_keys first, so
disk order can't change the content_hash). The load-bearing rule:

  **`about` (the Rosetta Stone) is ALWAYS the last field**, below every
  other key, no matter how the structure evolves.

This regressed once — new top-level fields not in the positional layout
list (e.g. ``constellation_size``, ``outer_position``, ``outer_total``)
were appended alphabetically AFTER ``about``, so a reader hit the legend
before the fields it explains.
"""

import json
import unittest

from mememage.core import _canonicalize_for_disk


class AboutAlwaysLastTests(unittest.TestCase):
    def test_about_is_last_with_unlisted_fields(self):
        # A future top-level field not yet in the positional layout must
        # still sort ABOVE `about`, not after it (this regressed once).
        record = {
            "identifier": "mememage-deadbeefdeadbeef",
            "content_hash": "abc123",
            "about": "this explains the format",
            "some_future_field": "x",
            "rarity": 70,
        }
        keys = list(_canonicalize_for_disk(record).keys())
        self.assertEqual(keys[-1], "about",
                         f"about must be last, got order: {keys}")

    def test_about_is_last_even_against_alphabetically_later_keys(self):
        # A field whose name sorts AFTER "about" alphabetically ("zzz")
        # must still land above it.
        record = {"identifier": "x", "about": "legend", "zzz_future_field": 1}
        keys = list(_canonicalize_for_disk(record).keys())
        self.assertEqual(keys[-1], "about")
        self.assertIn("zzz_future_field", keys[:-1])

    def test_about_last_among_listed_blobs(self):
        # Listed opaque blobs (signature/thumbnail/chunks) sit just above
        # `about`, never below.
        record = {
            "identifier": "x",
            "about": "legend",
            "signature": "sig",
            "thumbnail": "data:…",
            "chunks": {"a": 1},
        }
        keys = list(_canonicalize_for_disk(record).keys())
        self.assertEqual(keys[-1], "about")

    def test_newer_fields_are_grouped_not_dangling(self):
        # constellation_size / outer_position / outer_total / gps were in the
        # hash set but not the disk layout, so they floated in the alphabetical
        # tail just above `about`. Now they sit with their kin.
        record = {
            "identifier": "x", "content_hash": "h", "creator_name": "C",
            "age": 5, "outer_position": 3, "outer_total": 365, "width": 10, "height": 10,
            "constellation_index": 2, "constellation_size": 12, "heart_star_id": "hs",
            "birth": {}, "gps": [1, 2], "gps_time_locked": {}, "about": "legend",
        }
        keys = list(_canonicalize_for_disk(record).keys())
        self.assertEqual(keys[keys.index("age") + 1], "outer_position")
        self.assertEqual(keys[keys.index("outer_position") + 1], "outer_total")
        self.assertEqual(keys[keys.index("constellation_index") + 1], "constellation_size")
        self.assertEqual(keys[keys.index("birth") + 1], "gps")
        self.assertEqual(keys[-1], "about")

    def test_no_about_field_is_fine(self):
        # Records without `about` (pre-_step_about) just don't have it —
        # no crash, no phantom key.
        record = {"identifier": "x", "rarity": 10}
        out = _canonicalize_for_disk(record)
        self.assertNotIn("about", out)
        self.assertEqual(set(out), {"identifier", "rarity"})

    def test_canonicalize_preserves_content(self):
        # Reordering is cosmetic: same keys, same values, and the
        # sort_keys serialization (what the hash sees) is identical.
        record = {
            "identifier": "x", "about": "legend", "creator_name": "C",
            "rarity": 5, "origin": {"prompt": "p"},
        }
        out = _canonicalize_for_disk(record)
        self.assertEqual(out, record)  # dict equality ignores order
        self.assertEqual(
            json.dumps(out, sort_keys=True),
            json.dumps(record, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
