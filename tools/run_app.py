#!/usr/bin/env python3
"""PyInstaller entry for the Mememage desktop app.

This deliberately lives OUTSIDE the ``mememage`` package. PyInstaller
adds an entry script's own directory to the module search path — so when
the entry is ``mememage/desktop.py`` (inside the package), ``import
mememage.server`` resolves ambiguously and every ``mememage.*`` submodule
silently drops from the bundle (the frozen app then dies with
``ModuleNotFoundError: No module named 'mememage.server'``). An external
entry that simply imports the package resolves cleanly, so the whole
package gets bundled.

The actual app logic stays in ``mememage.desktop`` so the binary and the
``mememage app`` CLI share one code path.
"""
from mememage.desktop import main

if __name__ == "__main__":
    main()
