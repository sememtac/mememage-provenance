#!/usr/bin/env python3
"""Build the Mememage desktop app — one command, cross-platform.

    python3 tools/build_app.py     # macOS / Linux
    python  tools/build_app.py     # Windows (no `python3` alias there)

Produces (under ``dist/``):

    macOS    →  dist/Mememage.app            (double-click in Finder)
    Windows  →  dist/Mememage/Mememage.exe
    Linux    →  dist/Mememage/Mememage

Re-run after ANY code or docs change to rebuild the bundle — that's the
whole point: one command, every time. Run it from the project's venv so
the bundle picks up the same mememage + Pillow/numpy/cryptography you
test with.

PyInstaller can't cross-compile, so build the macOS app on a Mac and the
Windows .exe on Windows (run this same script on each).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SPEC = REPO / "tools" / "mememage_app.spec"


def _pip_install(args) -> None:
    """``pip install <args>`` into the current env, with the PEP 668
    override fallback for externally-managed Pythons (Homebrew, etc.)."""
    base = [sys.executable, "-m", "pip", "install"]
    try:
        subprocess.check_call(base + args)
    except subprocess.CalledProcessError:
        subprocess.check_call(base + ["--break-system-packages"] + args)


def _ensure_build_deps() -> None:
    """Ensure EVERYTHING the bundled app needs at runtime is in this env
    BEFORE PyInstaller snapshots it.

    PyInstaller bundles only what the build venv has installed — a venv
    missing Pillow/numpy/cryptography/qrcode silently ships a broken app
    that crashes on first upload (``No module named 'PIL'``). The
    ``desktop`` extra is the single source of truth for that runtime set
    (Pillow + numpy + qrcode + cryptography + pyinstaller); install it
    here so ``python tools/build_app.py`` always produces a working
    bundle, even on a fresh machine where the user never ran
    ``pip install .[desktop]``. Idempotent — pip skips satisfied deps.
    """
    # Fast path: if the heavy runtime deps already import, skip the
    # (slowish) pip resolve entirely. PyInstaller is checked separately
    # because it's a build-time, not runtime, dependency.
    have_runtime = True
    for mod in ("PIL", "numpy", "qrcode", "cryptography", "pillow_heif", "pystray"):
        try:
            __import__(mod)
        except ImportError:
            have_runtime = False
            break
    try:
        import PyInstaller  # noqa: F401
        have_pyinstaller = True
    except ImportError:
        have_pyinstaller = False

    if have_runtime and have_pyinstaller:
        return

    print("Ensuring build + runtime deps "
          "(Pillow, numpy, qrcode, cryptography, pillow-heif, pystray, pyinstaller)…")
    # Install the dependency PACKAGES directly — NOT `pip install -e .[desktop]`.
    # PyInstaller builds mememage from the source tree (pathex + the source-walk
    # hiddenimports in the spec), so the package itself needn't be installed.
    # Reinstalling the editable package triggers pip's uninstall step, which on
    # Windows fails outright when a prior install left the console script
    # (mememage.exe) half-removed — blocking an otherwise-fine build. Keep this
    # list in sync with the [desktop] extra in pyproject.toml.
    _pip_install(["Pillow>=10.0", "numpy>=1.24", "qrcode>=7.4",
                  "cryptography>=41.0", "pystray>=0.19", "pyinstaller>=6.0"])
    # HEIC (iPhone) — best-effort: a missing native wheel must never break the
    # build; JPEG/PNG are unaffected without it.
    try:
        _pip_install(["pillow-heif>=0.16"])
    except Exception:
        print("  (pillow-heif unavailable here — HEIC images won't read EXIF "
              "in this build; JPEG/PNG are unaffected.)")


def _clean() -> None:
    # Wipe prior artifacts so a stale bundle can never ship.
    for name in ("build", "dist"):
        path = REPO / name
        if path.exists():
            print(f"Removing {name}/ …")
            shutil.rmtree(path)


def main() -> int:
    if not SPEC.exists():
        print(f"Spec not found: {SPEC}", file=sys.stderr)
        return 1

    _ensure_build_deps()
    _clean()

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        str(SPEC),
    ]
    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(REPO))
    if result.returncode != 0:
        print("\nBuild FAILED — see PyInstaller output above.", file=sys.stderr)
        return result.returncode

    dist = REPO / "dist"
    print("\n" + "=" * 64)
    print("  BUILD COMPLETE")
    print("=" * 64)
    if sys.platform == "darwin" and (dist / "Mememage.app").exists():
        print("  App:    dist/Mememage.app")
        print("  Launch: open dist/Mememage.app    (or double-click in Finder)")
        print("  Debug:  ./dist/Mememage.app/Contents/MacOS/Mememage")
    else:
        # Windows / Linux build onefile — a single self-contained binary.
        exe = "Mememage.exe" if os.name == "nt" else "Mememage"
        print(f"  App:    dist/{exe}")
        print(f"  Launch: dist/{exe}    (or double-click)")
        print(f"  Share:  dist/{exe} is self-contained — distribute this one file.")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
