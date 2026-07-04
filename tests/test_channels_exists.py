"""Channel exists() namespace-collision probe.

The pre-flight in core loops every enabled channel that implements
exists() and re-rolls the identifier on any "taken", so a soul never
silently overwrites a *different* soul on any surface. Each channel
encodes its own permanence model:

  * IA reports darkened/tombstoned slots as taken (delegates to the
    canonical metadata-API parse in core._identifier_exists).
  * http_push GET-probes its read face — 2xx taken, 404 free, no
    tombstones (a deleted soul genuinely frees the slot). GET not HEAD:
    read faces vary (mememage's stdlib server has no do_HEAD), GET is
    universal and souls are tiny.
  * Zenodo declares no support (DOI namespace, different model).
"""

import unittest
import urllib.error
from unittest.mock import patch, MagicMock


class TestCapabilitiesExists(unittest.TestCase):
    def test_ia_advertises_exists(self):
        from mememage.channels.internet_archive import InternetArchiveChannel
        ch = InternetArchiveChannel({"id": "ia", "type": "internet_archive"})
        self.assertTrue(ch.capabilities()["exists"])

    def test_http_push_advertises_exists(self):
        from mememage.channels.http_push import HttpPushChannel
        ch = HttpPushChannel({"id": "vps", "type": "http_push",
                              "config": {"base_url": "https://x/api/souls"}})
        self.assertTrue(ch.capabilities()["exists"])

    def test_zenodo_does_not_advertise_exists(self):
        from mememage.channels.zenodo import ZenodoChannel
        ch = ZenodoChannel({"id": "zen", "type": "zenodo"})
        self.assertFalse(ch.capabilities()["exists"])


class TestIAExists(unittest.TestCase):
    """IA delegates to the single canonical metadata probe in core, so
    the tombstone-aware three-state logic lives in exactly one place."""

    def test_delegates_to_core_probe(self):
        from mememage.channels.internet_archive import InternetArchiveChannel
        ch = InternetArchiveChannel({"id": "ia", "type": "internet_archive"})
        with patch("mememage.core._identifier_exists",
                   return_value=True) as probe:
            self.assertTrue(ch.exists("mememage-darkened0000"))
        probe.assert_called_once_with("mememage-darkened0000")

    def test_free_slot_reports_false(self):
        from mememage.channels.internet_archive import InternetArchiveChannel
        ch = InternetArchiveChannel({"id": "ia", "type": "internet_archive"})
        with patch("mememage.core._identifier_exists", return_value=False):
            self.assertFalse(ch.exists("mememage-freeslot0000"))


class TestHttpPushExists(unittest.TestCase):
    def _build(self):
        from mememage.channels.http_push import HttpPushChannel
        return HttpPushChannel({
            "id": "vps", "type": "http_push", "name": "Peer",
            "enabled": True, "credentials": {},
            "config": {"base_url": "https://example.com/api/souls",
                       "accept_self_signed": True},
        })

    def _http_error(self, code):
        return urllib.error.HTTPError(
            url="https://example.com", code=code, msg="x", hdrs=None, fp=None)

    def test_2xx_means_taken(self):
        ch = self._build()
        with patch("mememage.channels.http_push.urlopen_with_retry",
                   return_value=MagicMock()):
            self.assertTrue(ch.exists("mememage-livesoul0000"))

    def test_404_means_free(self):
        ch = self._build()
        with patch("mememage.channels.http_push.urlopen_with_retry",
                   side_effect=self._http_error(404)):
            self.assertFalse(ch.exists("mememage-neverminted0"))

    def test_other_http_error_propagates(self):
        # A 500 from the read face is not a clean "free" signal — fail
        # closed (propagate) rather than risk overwriting a real soul.
        ch = self._build()
        with patch("mememage.channels.http_push.urlopen_with_retry",
                   side_effect=self._http_error(500)):
            with self.assertRaises(urllib.error.HTTPError):
                ch.exists("mememage-servererr00")


if __name__ == "__main__":
    unittest.main()
