"""Machine-GPS preview endpoint: show the host's IP-geolocated coords on the
conception page (cached) instead of a vague "will fetch on conceive" promise.
"""
import unittest
from unittest.mock import patch, MagicMock

import mememage.server as srv


class MachineGpsPreview(unittest.TestCase):
    def setUp(self):
        srv._machine_gps_cache.update(ts=0.0, coords=None)

    def test_cache_avoids_refetch(self):
        with patch("mememage.gps.fetch_machine_gps", return_value=(45.5, -122.6)) as m:
            a = srv._cached_machine_gps()
            b = srv._cached_machine_gps()
        self.assertEqual(a, (45.5, -122.6))
        self.assertEqual(b, (45.5, -122.6))
        self.assertEqual(m.call_count, 1)   # second call served from cache

    def test_handler_returns_coords_for_valid_token(self):
        srv._sessions["tok_mgps"] = {"status": "pending"}
        try:
            h = MagicMock()
            with patch.object(srv, "_cached_machine_gps", return_value=(10.0, 20.0)):
                srv.MintHandler._mint_machine_gps(h, "tok_mgps")
            self.assertEqual(h._send_json.call_args[0][0], {"lat": 10.0, "lon": 20.0})
        finally:
            srv._sessions.pop("tok_mgps", None)

    def test_handler_404_for_invalid_token(self):
        h = MagicMock()
        srv.MintHandler._mint_machine_gps(h, "no_such_token_xyz")
        self.assertEqual(h._send_json.call_args[0][1], 404)

    def test_handler_graceful_on_geo_miss(self):
        srv._sessions["tok_mgps2"] = {"status": "pending"}
        try:
            h = MagicMock()
            with patch.object(srv, "_cached_machine_gps", return_value=None):
                srv.MintHandler._mint_machine_gps(h, "tok_mgps2")
            self.assertIn("error", h._send_json.call_args[0][0])   # 200 + error, not a crash
        finally:
            srv._sessions.pop("tok_mgps2", None)


if __name__ == "__main__":
    unittest.main()
