"""Desktop app entry point.

This is what the packaged double-click binary (``Mememage.app`` on macOS,
``Mememage.exe`` on Windows) runs. It launches the mint server in local
desktop mode — loopback bind, plain HTTP (localhost is a secure context,
so GPS capture still works), a free local port — and opens the dashboard
in the default browser. No domain, no TLS, no token: the OS user account
is the security boundary for a single-user desktop app.

Run directly during development with::

    python -m mememage.desktop
"""

from __future__ import annotations

import argparse

# ---------------------------------------------------------------------------
# PyInstaller bundling — DO NOT REMOVE.
#
# server.py, the channel/notifier plugins, and most of the pipeline are
# imported INSIDE functions (lazy), and ``mememage.server`` is only ever
# reached lazily from cmd_serve. PyInstaller's static analysis can't see
# imports done inside functions, so the frozen app crashed with
# ``ModuleNotFoundError: No module named 'mememage.server'`` — and the
# spec's collect_submodules / source-walk hidden-imports don't reliably
# resolve against a PEP 660 *editable* install on Windows.
#
# Importing every submodule here, statically, makes them hard
# dependencies that PyInstaller's static analysis always bundles —
# independent of how mememage was installed. PyInstaller analyzes these
# ``import`` statements even though they sit inside try/except; the guard
# only keeps a missing optional dep from breaking app startup. Keep this
# list in sync when a new top-level module is added (one line in CI/tests
# could assert parity, see tests/test_desktop_bundle.py).
try:  # noqa: SIM105
    import mememage.access            # noqa: F401
    import mememage.api               # noqa: F401
    import mememage.bar               # noqa: F401
    import mememage.crypto            # noqa: F401
    import mememage.celestial         # noqa: F401
    import mememage.chain_config      # noqa: F401
    import mememage.chains            # noqa: F401
    import mememage.config            # noqa: F401
    import mememage.constellation     # noqa: F401
    import mememage.core              # noqa: F401
    import mememage.embodiment        # noqa: F401
    import mememage.ephemeris         # noqa: F401
    import mememage.exif              # noqa: F401
    import mememage.forecast          # noqa: F401
    import mememage.gps               # noqa: F401
    import mememage.hashing           # noqa: F401
    import mememage.ia_admin          # noqa: F401
    import mememage.install           # noqa: F401
    import mememage.lineage           # noqa: F401
    import mememage.mint              # noqa: F401
    import mememage.net               # noqa: F401
    import mememage.parsing           # noqa: F401
    import mememage.payload           # noqa: F401
    import mememage.personality       # noqa: F401
    import mememage.profiles          # noqa: F401
    import mememage.rarity            # noqa: F401
    import mememage.rs                # noqa: F401
    import mememage.server            # noqa: F401
    import mememage.signing           # noqa: F401
    import mememage.site_embed        # noqa: F401
    import mememage.site_pack         # noqa: F401
    import mememage.song              # noqa: F401
    import mememage.temperament       # noqa: F401
    import mememage.thumbnail         # noqa: F401
    import mememage.timelock          # noqa: F401
    import mememage.tls               # noqa: F401
    import mememage.tokens            # noqa: F401
    import mememage.tray              # noqa: F401
    import mememage.vitals            # noqa: F401
    import mememage.watermark         # noqa: F401
    import mememage.wordlist          # noqa: F401
    import mememage.zenodo            # noqa: F401
    import mememage.zodiac            # noqa: F401
    # plugin packages — registered lazily via _ensure_plugins_loaded()
    import mememage.channels                    # noqa: F401
    import mememage.channels.http_push          # noqa: F401
    import mememage.channels.internet_archive   # noqa: F401
    import mememage.channels.zenodo             # noqa: F401
    import mememage.notifiers                   # noqa: F401
    import mememage.notifiers.discord           # noqa: F401
    import mememage.notifiers.slack             # noqa: F401
    import mememage.notifiers.telegram          # noqa: F401
    import mememage.notifiers.webhook           # noqa: F401
except Exception:
    pass
# ---------------------------------------------------------------------------


def _serve_foreground() -> None:
    # Plain blocking serve — the original desktop behaviour. Reuses the same
    # code path as ``mememage app`` so the binary and the CLI never drift.
    from mememage.__main__ import cmd_app

    args = argparse.Namespace(
        port=None,        # free local port (8765+) picked at startup
        host="127.0.0.1",
        cert=None,
        key=None,
        no_tls=True,
        force_open=True,  # loopback bind — the public-domain warning is moot
        local=True,
    )
    cmd_app(args)


def main() -> None:
    # Default: live in the tray / menu bar (server on a background thread,
    # a menu to open feed / dashboard / decoder / validator). Fall back to a
    # foreground serve with --no-tray, or automatically if the GUI deps
    # (pystray / pyobjc) are missing or the tray fails to start — so a
    # headless or stripped environment still runs the server.
    parser = argparse.ArgumentParser(prog="mememage-desktop")
    parser.add_argument(
        "--no-tray", action="store_true",
        help="run the server in the foreground without the tray/menu-bar icon")
    args, _ = parser.parse_known_args()

    if not args.no_tray:
        try:
            from mememage.tray import run_tray
            run_tray()
            return
        except Exception as exc:
            print(f"Tray unavailable ({exc}); running in the foreground. "
                  f"Pass --no-tray to skip the tray and silence this.")

    _serve_foreground()


if __name__ == "__main__":
    main()
