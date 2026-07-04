# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Mememage desktop app.

Bundles the local mint server + the whole web UI (dashboard, decoder,
validator, assets) into a double-click app that runs ``mememage.desktop:main``
— loopback HTTP + auto-opened dashboard. Platform-aware:

  * macOS   → a windowed ``Mememage.app`` (debug by running the inner
              binary: ``./dist/Mememage.app/Contents/MacOS/Mememage``)
  * Windows → ``dist/Mememage/Mememage.exe`` (console, so logs + Ctrl-C work)
  * Linux   → ``dist/Mememage/Mememage``

Build with the cross-platform helper (recommended):

    python tools/build_app.py

or directly:

    pyinstaller tools/mememage_app.spec
"""
import os
import sys

from PyInstaller.utils.hooks import (
    collect_submodules, collect_dynamic_libs, collect_data_files)

REPO = os.path.abspath(os.path.join(SPECPATH, os.pardir))
# Entry lives OUTSIDE the package on purpose — a script inside mememage/
# makes PyInstaller resolve `import mememage.server` ambiguously and drop
# every submodule from the bundle. See tools/run_app.py.
ENTRY = os.path.join(REPO, "tools", "run_app.py")
DOCS = os.path.join(REPO, "docs")

# Desktop icons (committed assets; regenerate via tools/make_icons.py). The
# six-cell M/Y/C bar mark — same design as the site favicon. PyInstaller picks
# the right format per platform: .ico for the Windows .exe, .icns for the macOS
# .app. Guarded by existence so a checkout without them still builds.
ICON_ICO = os.path.join(SPECPATH, "Mememage.ico")
ICON_ICNS = os.path.join(SPECPATH, "Mememage.icns")
_ico = ICON_ICO if os.path.exists(ICON_ICO) else None
_icns = ICON_ICNS if os.path.exists(ICON_ICNS) else None

# Ship the entire web UI as bundled data; server.DOCS_DIR resolves it
# under sys._MEIPASS when frozen.
datas = [(DOCS, "docs")]

# mememage imports its plugins + the server module inside functions (lazy),
# so PyInstaller's static analysis never sees them. We enumerate every
# mememage submodule from the SOURCE TREE rather than collect_submodules():
# against a PEP 660 editable install (pip install -e) collect_submodules can
# return an incomplete list, silently dropping lazily-imported modules like
# mememage.server — which is exactly what broke the first Windows build.
# Walking the source is deterministic across platforms + install modes.
def _mememage_submodules():
    mods = []
    pkg_root = os.path.join(REPO, "mememage")
    for dirpath, dirnames, filenames in os.walk(pkg_root):
        dirnames[:] = [d for d in dirnames if not d.startswith((".", "__pycache__"))]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), REPO)[:-3]
            mod = rel.replace(os.sep, ".")
            if mod.endswith(".__init__"):
                mod = mod[: -len(".__init__")]
            mods.append(mod)
    return sorted(set(mods))

hiddenimports = _mememage_submodules()
# Optional native deps are lazy-imported too — collect so the frozen app
# can mint (Pillow/numpy bar embed + qrcode handoff) and sign (cryptography).
# These must also be INSTALLED in the build venv (tools/build_app.py installs
# the `.[desktop]` extra first) — collect_submodules finds nothing for an
# absent package, which is how the first Windows build shipped without PIL.
for _opt in ("PIL", "numpy", "cryptography", "qrcode"):
    try:
        hiddenimports += collect_submodules(_opt)
    except Exception:
        pass

# pillow-heif ships a native libheif — it needs its BINARIES + data bundled,
# not just Python submodules, or HEIC (iPhone) images won't open in the frozen
# app and their EXIF won't read. Best-effort: absent package -> empty lists.
binaries = []
try:
    hiddenimports += collect_submodules("pillow_heif")
    binaries += collect_dynamic_libs("pillow_heif")
    datas += collect_data_files("pillow_heif")
except Exception:
    pass

# Tray / menu-bar UI (pystray). The bundled hook-pystray collects pystray's
# backends but NOT the pyobjc frameworks the macOS backend (_darwin) imports at
# runtime — AppKit / Foundation / objc / PyObjCTools — nor their compiled
# extensions. Pull those (+ their dylibs/data) on macOS so the frozen .app can
# raise the menu-bar item. Windows' backend is pure ctypes (the hook suffices);
# Linux's GTK/AppIndicator backends are optional — the app falls back to a
# foreground serve when no backend is present (see mememage.desktop.main).
try:
    hiddenimports += collect_submodules("pystray")
except Exception:
    pass
if sys.platform == "darwin":
    hiddenimports += ["PyObjCTools.MachSignals"]
    for _fw in ("objc", "AppKit", "Foundation", "CoreFoundation", "PyObjCTools"):
        try:
            hiddenimports += collect_submodules(_fw)
            binaries += collect_dynamic_libs(_fw)
            datas += collect_data_files(_fw)
        except Exception:
            pass
elif sys.platform == "win32":
    hiddenimports += ["pystray._win32"]

IS_MAC = sys.platform == "darwin"
# macOS ships a windowed .app (onedir + BUNDLE). Windows/Linux ship a
# single self-contained executable (onefile) so distribution is "send
# one file" rather than "zip a folder".
ONEFILE = not IS_MAC

a = Analysis(
    [ENTRY],
    pathex=[REPO],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Trim test/dev-only heavyweight deps that would otherwise bloat the
    # bundle. None are needed at runtime by the desktop app.
    excludes=["tkinter", "matplotlib", "pytest", "playwright", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)

if ONEFILE:
    # Windows / Linux — one self-contained file: dist/Mememage(.exe).
    # Binaries + bundled docs ride inside the EXE (extracted to a temp
    # dir at launch). Console so a double-click shows logs and Ctrl-C /
    # closing the window stops the server.
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="Mememage",
        debug=False,
        strip=False,
        upx=False,
        console=True,
        disable_windowed_traceback=False,
        icon=_ico,  # Windows .exe icon (ignored on Linux)
    )
else:
    # macOS — onedir + windowed .app (the cool double-click).
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="Mememage",
        debug=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="Mememage",
    )
    app = BUNDLE(
        coll,
        name="Mememage.app",
        icon=_icns,  # six-cell M/Y/C bar mark (tools/Mememage.icns)
        bundle_identifier="art.mememage.desktop",
        info_plist={
            "CFBundleName": "Mememage",
            "CFBundleDisplayName": "Mememage",
            "CFBundleShortVersionString": "0.1.0",
            "NSHighResolutionCapable": True,
            # Menu-bar agent app: no Dock icon, no Cmd-Tab entry, no app menu —
            # its only presence is the menu-bar (tray) item. This is a pure tray
            # app (the UI is the browser pages it opens), so a windowless Dock
            # icon would just be dead weight. The menu-bar mark is the "server
            # is running / click to access" indicator; Quit lives in its menu.
            "LSUIElement": True,
        },
    )
