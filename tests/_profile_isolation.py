"""Shared helper for redirecting mememage's profile-resolved paths
into a tmp directory during unit tests.

The signing module's path "constants" (PRIVATE_KEY_PATH etc.) are now
PEP 562 lazy attributes resolved through ``mememage.profiles``, so the
old ``patch("mememage.signing.PRIVATE_KEY_PATH", ...)`` pattern no
longer works — the dynamic getattr bypasses any module-dict patches.

This helper patches the underlying ``profiles`` roots instead. Both
``signing.PRIVATE_KEY_PATH`` and direct ``profiles.private_key_path()``
calls then resolve to ``<tmp>/profiles/default/private.key``.

Usage from a ``unittest.TestCase``:

    from tests._profile_isolation import isolate_profiles

    def setUp(self):
        self.key_dir = tempfile.mkdtemp(...)
        self.profile_dir = isolate_profiles(self, self.key_dir)
        keygen(name="Test")   # writes into self.profile_dir
"""

from pathlib import Path
from unittest.mock import patch


def isolate_profiles(test_case, key_dir):
    """Point profile resolution at ``key_dir``. Returns the path of
    the default profile directory (``<key_dir>/profiles/default``)
    so tests can locate files keygen will write."""
    key_dir = Path(key_dir)
    from mememage import profiles, signing
    patches = [
        patch.object(profiles, "ROOT", key_dir),
        patch.object(profiles, "PROFILES_DIR", key_dir / "profiles"),
        patch.object(profiles, "ACTIVE_FILE", key_dir / "active_profile"),
        patch.object(signing, "KEY_DIR", key_dir),
    ]
    for p in patches:
        p.start()
    test_case.addCleanup(lambda: [p.stop() for p in patches])
    return key_dir / "profiles" / "default"
