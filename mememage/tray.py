"""System-tray / menu-bar presence for the desktop app.

One cross-platform surface for Windows (system tray), macOS (menu bar), and
Linux (app indicator), all from a single menu via ``pystray``. The mint server
runs on a background thread; the tray's event loop owns the main thread (macOS
requires the GUI run loop there). The menu opens the four web faces in the
default browser:

    Open Feed       → /            (the wall of recently-conceived images)
    Open Dashboard  → /dashboard
    Open Decoder    → /decoder
    Open Validator  → /validator
    Quit            → server.shutdown() + stop the tray

``pystray`` (and, on macOS, pyobjc) are imported lazily inside ``run_tray`` so
that merely importing this module — which ``mememage/desktop.py`` does at the
top for PyInstaller bundling — never fails when the GUI deps are absent. A
caller that can't start the tray should fall back to a plain foreground serve.
"""

from __future__ import annotations

import sys
import threading
import time
import urllib.request
import webbrowser

# The brand mark's M/Y/C (identical to the favicon / desktop icon).
_M = (0xDC, 0x50, 0xDC)
_Y = (0xDC, 0xC8, 0x3C)
_C = (0x3C, 0xC8, 0xDC)


def _icon_image(size: int = 128, pad_frac: float = 0.18):
    """The six-cell bar mark (M/Y/C over mirrored C/Y/M) as a PIL image — the
    same design as the site favicon, in full colour for the tray.

    macOS scales the image to the menu-bar height, so a full-bleed mark fills
    the whole bar and reads big. ``pad_frac`` insets the mark inside a
    transparent border (fraction of the canvas per side) so it renders a touch
    smaller with breathing room. Drawn at a high resolution and downscaled by
    the OS, so the inset edges stay crisp."""
    from PIL import Image, ImageDraw  # desktop dep; lazy so import is safe

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = round(size * pad_frac)
    inner = size - 2 * pad                       # the centered square the mark fills
    xs = [pad + round(inner * i / 3) for i in range(4)]   # 4 column edges
    ys = [pad + round(inner * j / 2) for j in range(3)]   # 3 row edges
    top, bot = [_M, _Y, _C], [_C, _Y, _M]
    for col in range(3):
        d.rectangle([xs[col], ys[0], xs[col + 1] - 1, ys[1] - 1], fill=top[col])
        d.rectangle([xs[col], ys[1], xs[col + 1] - 1, ys[2] - 1], fill=bot[col])
    return img


def _wait_health(base: str, timeout: float = 15.0) -> bool:
    """Poll /health until the background server is accepting, so the menu's
    first click lands on a live server. Returns True once up."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/health", timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def run_tray() -> None:
    """Launch the desktop app as a tray/menu-bar program. Blocks until Quit.

    Raises on missing GUI deps (pystray / pyobjc) or a failed start so the
    caller can fall back to a foreground serve.
    """
    from pystray import Icon, Menu, MenuItem  # raises if pystray absent
    from mememage import server as S

    # Single-instance: if a desktop server is already up, focus it and bail
    # rather than light a second tray icon.
    existing = S._desktop_already_running()
    if existing:
        webbrowser.open(existing)
        return

    port = S._find_free_port("127.0.0.1", 8765)
    base = f"http://127.0.0.1:{port}"

    # The live server handle, set via on_ready so Quit can shut it down.
    state: dict = {"server": None}

    def _serve():
        # open_browser=True writes the single-instance lock, pops the
        # dashboard once on launch (so you can see it started), and clears
        # the lock on shutdown. The tray adds the persistent menu on top.
        S.run_server(host="127.0.0.1", port=port, certfile=None, keyfile=None,
                     open_browser=True, on_ready=lambda srv: state.update(server=srv))

    threading.Thread(target=_serve, daemon=True, name="mememage-server").start()
    _wait_health(base)

    # The dashboard is the one gated face: when a MINT_API_TOKEN is configured
    # its clean URL is /<token> (the login otherwise 401s a direct hit). Mirror
    # run_server's own dash-path logic. Feed/decoder/validator are public.
    token = S._load_mint_token()
    dash_path = f"/{token}" if token else "/dashboard"

    def _open(path):
        return lambda icon, item: webbrowser.open(base + path)

    def _quit(icon, item):
        srv = state.get("server")
        if srv is not None:
            try:
                # shutdown() returns serve_forever() on the background thread,
                # whose finally clears the desktop lock + closes the socket.
                srv.shutdown()
            except Exception:
                pass
        icon.stop()

    menu = Menu(
        MenuItem("Open Feed", _open("/")),
        MenuItem("Open Dashboard", _open(dash_path)),
        MenuItem("Open Decoder", _open("/decoder")),
        MenuItem("Open Validator", _open("/validator")),
        Menu.SEPARATOR,
        MenuItem("Quit Mememage", _quit),
    )

    def _setup(icon):
        # pystray calls this once the backend is ready, before its run loop.
        icon.visible = True
        # macOS: make this a menu-bar *accessory* so there's no Dock icon /
        # Cmd-Tab entry — same as the frozen .app's LSUIElement, but applied at
        # runtime so it also holds when run from source (`python -m
        # mememage.desktop`), where the process would otherwise show as
        # "Python" in the Dock. The frozen app gets it both ways (belt + braces).
        if sys.platform == "darwin":
            try:
                from AppKit import (NSApplication,
                                    NSApplicationActivationPolicyAccessory)
                NSApplication.sharedApplication().setActivationPolicy_(
                    NSApplicationActivationPolicyAccessory)
            except Exception:
                pass

    icon = Icon("mememage", _icon_image(), "Mememage", menu=menu)
    icon.run(setup=_setup)  # blocks the main thread (required on macOS) until Quit


if __name__ == "__main__":
    run_tray()
