"""Doctor's proxy-upload-limit check: read nginx's client_max_body_size on the
vhosts that proxy to the backend, so a low cap (the 413 trap that blocks large
payload uploads) is flagged before a user hits it on a real upload.
"""

import glob
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mememage.server import _proxy_body_limit_mib


class ProxyBodyLimit(unittest.TestCase):
    def _limit(self, files, port=8444):
        d = tempfile.mkdtemp()
        for name, txt in files.items():
            (Path(d) / name).write_text(txt)
        with patch("glob.glob", return_value=glob.glob(d + "/*")):
            return _proxy_body_limit_mib(port)

    def test_reports_lowest_proxying_vhost(self):
        # The smallest cap among vhosts proxying to us is what 413s.
        lim = self._limit({
            "mint": "server { client_max_body_size 50m; location / { proxy_pass https://127.0.0.1:8444; } }",
            "souls": "server { client_max_body_size 600m; location / { proxy_pass https://127.0.0.1:8444; } }",
        })
        self.assertEqual(lim, 50.0)

    def test_no_directive_is_nginx_default_1mib(self):
        lim = self._limit({"mint": "server { location / { proxy_pass https://127.0.0.1:8444; } }"})
        self.assertEqual(lim, 1.0)

    def test_ignores_non_proxying_vhosts(self):
        # A default vhost that doesn't proxy to us must not count.
        lim = self._limit({"default": "server { client_max_body_size 1m; root /var/www; }"})
        self.assertIsNone(lim)

    def test_units(self):
        self.assertEqual(self._limit(
            {"v": "server{client_max_body_size 600m; proxy_pass https://127.0.0.1:8444;}"}), 600.0)
        self.assertEqual(self._limit(
            {"v": "server{client_max_body_size 1g; proxy_pass https://127.0.0.1:8444;}"}), 1024.0)
        self.assertAlmostEqual(self._limit(
            {"v": "server{client_max_body_size 512k; proxy_pass https://127.0.0.1:8444;}"}), 0.5)

    def test_only_matches_our_backend_port(self):
        lim = self._limit(
            {"other": "server { client_max_body_size 1m; proxy_pass https://127.0.0.1:9999; }"},
            port=8444)
        self.assertIsNone(lim)


if __name__ == "__main__":
    unittest.main()
