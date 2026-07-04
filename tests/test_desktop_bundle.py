"""Guard: the desktop entry must statically import every mememage submodule.

PyInstaller can't see imports done inside functions (the whole pipeline +
server.py are lazy-imported), and collect_submodules / source-walk hidden
imports don't reliably resolve against a PEP 660 editable install on
Windows. The reliable fix is static imports in mememage/desktop.py — but
that list can silently fall out of sync when a new module is added, which
would drop it from the frozen bundle. This test fails loudly if so.
"""

import ast
import os
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _source_modules():
    mods = set()
    pkg = os.path.join(REPO, "mememage")
    for dp, dn, fn in os.walk(pkg):
        dn[:] = [d for d in dn if not d.startswith((".", "__pycache__"))]
        for f in fn:
            if f.endswith(".py") and f != "__init__.py":
                rel = os.path.relpath(os.path.join(dp, f), REPO)[:-3]
                mods.add(rel.replace(os.sep, "."))
    return mods


def _desktop_static_imports():
    src = open(os.path.join(REPO, "mememage", "desktop.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("mememage"):
                    names.add(alias.name)
    return names


class TestDesktopBundleParity(unittest.TestCase):
    def test_every_submodule_is_statically_imported(self):
        source = _source_modules()
        imported = _desktop_static_imports()
        # The entry itself, and __main__ (pulled in via main()'s ImportFrom),
        # don't need to be in the static block.
        ignore = {"mememage.desktop", "mememage.__main__"}
        missing = (source - imported) - ignore
        self.assertFalse(
            missing,
            "mememage/desktop.py must statically import these so the PyInstaller "
            "bundle includes them: " + ", ".join(sorted(missing)),
        )


if __name__ == "__main__":
    unittest.main()
