"""Setting a dark chain's password must HOLD the runtime password, not just
write the on-disk verifier — otherwise the freshly password-set chain is
gated-but-locked and the next conception demands the same password again.
"""
import json
import unittest
from unittest.mock import patch, MagicMock

import mememage.server as srv


class ChainPasswordHold(unittest.TestCase):
    def setUp(self):
        srv._runtime_pw.clear()

    def _post(self, body):
        h = MagicMock()
        h._read_body.return_value = json.dumps(body)
        with patch.object(srv, "_check_auth", return_value=True), \
             patch("mememage.chains.set_password",
                   return_value={"password_set": bool(body.get("password"))}):
            srv.MintHandler._chain_password(h)
        return h

    def test_setting_password_holds_runtime(self):
        self._post({"chain_id": "darkchain", "password": "hunter2"})
        self.assertEqual(srv._runtime_pw.get("darkchain"), "hunter2")  # unlocked, conceivable

    def test_clearing_password_drops_held(self):
        srv._runtime_pw["darkchain"] = "hunter2"
        self._post({"chain_id": "darkchain", "password": ""})
        self.assertNotIn("darkchain", srv._runtime_pw)


if __name__ == "__main__":
    unittest.main()
