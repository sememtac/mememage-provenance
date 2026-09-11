"""The serve path must survive a tailnet rename.

A tailnet rename changes the MagicDNS suffix. server.json then names a host
that no longer resolves, _external_host keeps handing that name out, and the
phone's GPS-capture link dead-ends. Happened 2026-09-11: tail327313 ->
tail4ee084, and the cert on disk had also quietly expired a month earlier.

The desktop capture path never had this problem, because _tailscale_https_fqdn
reads Self.DNSName live. selfheal_tailscale_domain gives the serve path the
same property.

The most important test here is test_non_tailscale_domain_is_untouched: the VPS
runs this same code on mint.mememage.art with certbot certs, and hijacking that
would take production down.
"""

import json
import unittest
from unittest.mock import patch

from mememage import server as S

OLD = "andys-mac-mini.tail327313.ts.net"
NEW = "andys-mac-mini.tail4ee084.ts.net"


class _Cfg:
    """Redirects server.json at a temp file for one test."""

    def __init__(self, case, data):
        import tempfile, pathlib
        self.case = case
        self.path = pathlib.Path(tempfile.mkdtemp()) / "server.json"
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def __enter__(self):
        self._p = patch.object(S, "SERVER_CONFIG_FILE", self.path)
        self._p.start()
        S._server_config = None            # drop the process-wide cache
        return self

    def __exit__(self, *a):
        self._p.stop()
        S._server_config = None
        return False

    def read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))


class TestSelfHeal(unittest.TestCase):

    def test_non_tailscale_domain_is_untouched(self):
        # THE GUARD. The VPS serves mint.mememage.art with certbot certs and
        # runs this same code. Rewriting its domain would break production.
        cfg = {"domain": "mint.mememage.art",
               "cert": "/etc/letsencrypt/live/mememage/fullchain.pem",
               "key": "/etc/letsencrypt/live/mememage/privkey.pem",
               "webhooks": [{"url": "https://example.invalid"}]}
        with _Cfg(self, cfg) as c:
            with patch.object(S, "_tailscale_https_fqdn", return_value=NEW) as fqdn, \
                 patch.object(S, "_provision_tailscale_cert") as prov:
                self.assertIsNone(S.selfheal_tailscale_domain())
                fqdn.assert_not_called()   # never even asks Tailscale
                prov.assert_not_called()
            self.assertEqual(c.read(), cfg, "config must be byte-for-byte unchanged")

    def test_empty_domain_is_not_filled_in(self):
        # Repairs an existing Tailscale identity; never chooses one.
        with _Cfg(self, {"port": 8443}) as c:
            with patch.object(S, "_tailscale_https_fqdn", return_value=NEW):
                self.assertIsNone(S.selfheal_tailscale_domain())
            self.assertNotIn("domain", c.read())

    def test_rename_repoints_domain_cert_and_key(self):
        # The 2026-09-11 case.
        cfg = {"domain": OLD, "cert": f"/c/{OLD}.crt", "key": f"/c/{OLD}.key",
               "webhooks": [{"url": "https://example.invalid"}], "port": 8443}
        with _Cfg(self, cfg) as c:
            with patch.object(S, "_tailscale_https_fqdn", return_value=NEW), \
                 patch.object(S, "_provision_tailscale_cert",
                              return_value=(f"/c/{NEW}.crt", f"/c/{NEW}.key")):
                got = S.selfheal_tailscale_domain()
            self.assertEqual(got, (f"/c/{NEW}.crt", f"/c/{NEW}.key"))
            d = c.read()
            self.assertEqual(d["domain"], NEW)
            self.assertEqual(d["cert"], f"/c/{NEW}.crt")
            self.assertEqual(d["key"], f"/c/{NEW}.key")
            self.assertEqual(d["webhooks"], cfg["webhooks"], "other keys preserved")
            self.assertEqual(d["port"], 8443)

    def test_failed_provision_leaves_config_alone(self):
        # Never advertise a name we have no certificate for.
        cfg = {"domain": OLD, "cert": f"/c/{OLD}.crt", "key": f"/c/{OLD}.key"}
        with _Cfg(self, cfg) as c:
            with patch.object(S, "_tailscale_https_fqdn", return_value=NEW), \
                 patch.object(S, "_provision_tailscale_cert", return_value=None):
                self.assertIsNone(S.selfheal_tailscale_domain())
            self.assertEqual(c.read(), cfg)

    def test_tailscale_unavailable_is_a_noop(self):
        # Tailscale absent, logged out, or HTTPS certificates disabled — the
        # exact state found on 2026-09-11 before the admin console toggle.
        cfg = {"domain": OLD, "cert": f"/c/{OLD}.crt", "key": f"/c/{OLD}.key"}
        with _Cfg(self, cfg) as c:
            with patch.object(S, "_tailscale_https_fqdn", return_value=None), \
                 patch.object(S, "_provision_tailscale_cert") as prov:
                self.assertIsNone(S.selfheal_tailscale_domain())
                prov.assert_not_called()
            self.assertEqual(c.read(), cfg)

    def test_same_name_still_renews_an_expired_cert(self):
        # The silent half of the incident: the name was fine for weeks while
        # the cert was expired. Provisioning is idempotent, so always call it.
        cfg = {"domain": NEW, "cert": f"/c/{NEW}.crt", "key": f"/c/{NEW}.key"}
        with _Cfg(self, cfg):
            with patch.object(S, "_tailscale_https_fqdn", return_value=NEW), \
                 patch.object(S, "_provision_tailscale_cert",
                              return_value=(f"/c/{NEW}.crt", f"/c/{NEW}.key")) as prov:
                got = S.selfheal_tailscale_domain()
                prov.assert_called_once_with(NEW)
        self.assertEqual(got, (f"/c/{NEW}.crt", f"/c/{NEW}.key"))

    def test_cache_is_dropped_so_links_use_the_new_name(self):
        # _external_host reads _get_server_config() per request. A stale cache
        # would keep advertising the dead hostname for the life of the process,
        # which is the whole bug.
        cfg = {"domain": OLD, "cert": f"/c/{OLD}.crt", "key": f"/c/{OLD}.key"}
        with _Cfg(self, cfg):
            S._get_server_config()                      # prime the cache with OLD
            self.assertEqual(S._get_server_config()["domain"], OLD)
            with patch.object(S, "_tailscale_https_fqdn", return_value=NEW), \
                 patch.object(S, "_provision_tailscale_cert",
                              return_value=(f"/c/{NEW}.crt", f"/c/{NEW}.key")):
                S.selfheal_tailscale_domain()
            self.assertEqual(S._get_server_config()["domain"], NEW)

    def test_trailing_dot_in_config_is_not_a_false_rename(self):
        # tailscale reports Self.DNSName with a trailing dot, so a config
        # written from it can carry one. A naive compare would call that a
        # rename on every boot and rewrite the file forever.
        cfg = {"domain": NEW + ".", "cert": f"/c/{NEW}.crt", "key": f"/c/{NEW}.key"}
        with _Cfg(self, cfg):
            with patch.object(S, "_tailscale_https_fqdn", return_value=NEW), \
                 patch.object(S, "_provision_tailscale_cert",
                              return_value=(f"/c/{NEW}.crt", f"/c/{NEW}.key")), \
                 patch.object(S, "_persist_server_identity") as persist:
                S.selfheal_tailscale_domain()
                persist.assert_not_called()   # same name + same paths → no write


if __name__ == "__main__":
    unittest.main()
