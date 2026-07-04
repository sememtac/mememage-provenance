"""The self-push reconcile rewrites any SELF-POINTING http_push surface's
base_url to loopback:port — matched by id 'self' OR by the box's own
host/IP — while leaving real peers alone.

Regression for the genesis-blocking wedge: a primary surface stuck on
``https://<public-ip>:<port>/api/souls`` that no boot ever healed because the
old reconcile only matched id 'self'. A cloud box can't reach its own public
IP from inside (no NAT hairpin), so the PUT failed every mint.
"""

import unittest

from mememage.server import (
    _reconcile_self_push_channels,
    _self_pointing_http_push,
)

LOOPBACK = "https://127.0.0.1:8444/api/souls"
OWN = {"127.0.0.1", "localhost", "::1",
       "mint.example.com", "203.0.113.5", "souls.example.com"}


class SelfPushReconcile(unittest.TestCase):
    def test_public_ip_rewritten(self):
        # The exact wedge: public IP + drifted port, id is NOT 'self'.
        ch = {"id": "Mememage Mint Server", "type": "http_push",
              "config": {"base_url": "https://203.0.113.5:8444/api/souls"}}
        self.assertTrue(_reconcile_self_push_channels([ch], LOOPBACK, OWN))
        self.assertEqual(ch["config"]["base_url"], LOOPBACK)
        self.assertTrue(ch["config"]["accept_self_signed"])

    def test_public_domain_rewritten(self):
        ch = {"id": "d", "type": "http_push",
              "config": {"base_url": "https://mint.example.com/api/souls"}}
        self.assertTrue(_reconcile_self_push_channels([ch], LOOPBACK, OWN))
        self.assertEqual(ch["config"]["base_url"], LOOPBACK)

    def test_souls_host_rewritten(self):
        ch = {"id": "s", "type": "http_push",
              "config": {"base_url": "https://souls.example.com/api/souls"}}
        self.assertTrue(_reconcile_self_push_channels([ch], LOOPBACK, OWN))
        self.assertEqual(ch["config"]["base_url"], LOOPBACK)

    def test_id_self_always_rewritten(self):
        # id 'self' forced to loopback even if its host drifted off own_hosts.
        ch = {"id": "self", "type": "http_push",
              "config": {"base_url": "https://stale.host:9/api/souls"}}
        self.assertTrue(_reconcile_self_push_channels([ch], LOOPBACK, OWN))
        self.assertEqual(ch["config"]["base_url"], LOOPBACK)

    def test_real_peer_left_alone(self):
        # A friend's mememage host — NOT our address — must be untouched.
        ch = {"id": "friend", "type": "http_push",
              "config": {"base_url": "https://friend.example.org/api/souls",
                         "accept_self_signed": False}}
        self.assertFalse(_reconcile_self_push_channels([ch], LOOPBACK, OWN))
        self.assertEqual(ch["config"]["base_url"], "https://friend.example.org/api/souls")

    def test_already_loopback_noop(self):
        ch = {"id": "self", "type": "http_push",
              "config": {"base_url": LOOPBACK, "accept_self_signed": True}}
        self.assertFalse(_reconcile_self_push_channels([ch], LOOPBACK, OWN))

    def test_loopback_but_self_signed_off_gets_fixed(self):
        ch = {"id": "self", "type": "http_push",
              "config": {"base_url": LOOPBACK, "accept_self_signed": False}}
        self.assertTrue(_reconcile_self_push_channels([ch], LOOPBACK, OWN))
        self.assertTrue(ch["config"]["accept_self_signed"])

    def test_non_http_push_ignored(self):
        ch = {"id": "ia", "type": "internet_archive", "config": {}}
        self.assertFalse(_reconcile_self_push_channels([ch], LOOPBACK, OWN))

    def test_mixed_set(self):
        own_pin = {"id": "Mememage Mint Server", "type": "http_push",
                   "config": {"base_url": "https://203.0.113.5:8444/api/souls"}}
        peer = {"id": "friend", "type": "http_push",
                "config": {"base_url": "https://other.example.net/api/souls"}}
        ia = {"id": "ia", "type": "internet_archive", "config": {}}
        chans = [own_pin, peer, ia]
        self.assertTrue(_reconcile_self_push_channels(chans, LOOPBACK, OWN))
        self.assertEqual(own_pin["config"]["base_url"], LOOPBACK)         # fixed
        self.assertEqual(peer["config"]["base_url"],
                         "https://other.example.net/api/souls")            # untouched

    def test_self_pointing_helper(self):
        self.assertTrue(_self_pointing_http_push("https://203.0.113.5:8444/api/souls", OWN))
        self.assertTrue(_self_pointing_http_push("https://127.0.0.1/x", OWN))
        self.assertFalse(_self_pointing_http_push("https://friend.example.org/x", OWN))
        self.assertFalse(_self_pointing_http_push("", OWN))
        self.assertFalse(_self_pointing_http_push(None, OWN))


if __name__ == "__main__":
    unittest.main()
