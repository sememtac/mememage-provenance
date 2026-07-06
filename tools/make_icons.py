#!/usr/bin/env python3
"""Generate the Mememage desktop icons from the canonical favicon design.

The Polaroid icon: a white card framing a black photo (thin border + a gap above
the bar) with the M/Y/C bar along the bottom 20% — the icon IS a Mememage-stamped
image. It's a few solid rectangles, so we draw it straight with Pillow (no SVG
rasterizer needed) and emit both platform formats:

    tools/Mememage.icns   — macOS app bundle icon (built via `iconutil`)
    tools/Mememage.ico    — Windows .exe icon (multi-size, Pillow-native)

Run on a Mac to refresh both (iconutil is macOS-only); on other platforms it
still writes the .ico and a master PNG. The .icns/.ico are committed assets —
tools/mememage_app.spec points at them — so a normal build doesn't run this;
re-run it only when the icon design changes.

    python3 tools/make_icons.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))

# The brand's slightly-muted M/Y/C — identical to the favicon's hex.
M = (0xDC, 0x50, 0xDC)
Y = (0xDC, 0xC8, 0x3C)
C = (0x3C, 0xC8, 0xDC)


def render(size: int) -> Image.Image:
    """Draw the Polaroid icon at `size`x`size`: a WHITE card framing a black
    photo (thin border top/left/right + a white gap above the bar), with the
    M/Y/C bar full-width along the bottom 20%. On light surfaces the white card
    recedes; on dark it frames the photo, so one icon works on either."""
    img = Image.new("RGBA", (size, size), (255, 255, 255, 255))   # white card
    d = ImageDraw.Draw(img)
    b = round(size * 0.031)                     # frame border (~32/1024)
    bar_top = size - round(size * 0.20)         # M/Y/C bar = bottom 20%
    gap = round(size * 0.032)                   # white gap above the bar
    photo_bottom = bar_top - gap
    d.rectangle([b, b, size - b - 1, photo_bottom - 1], fill=(0, 0, 0, 255))
    xs = [round(size * i / 3) for i in range(4)]            # 3 equal columns
    cols = [M, Y, C]
    for col in range(3):
        d.rectangle([xs[col], bar_top, xs[col + 1] - 1, size - 1], fill=cols[col])
    return img


def make_ico(path: str) -> None:
    # Windows .ico — multi-resolution so Explorer/taskbar/alt-tab each pick a
    # crisp size. Pillow writes all sizes into one file from the largest image.
    sizes = [16, 24, 32, 48, 64, 128, 256]
    base = render(256)
    base.save(path, format="ICO", sizes=[(s, s) for s in sizes])
    print("wrote", os.path.relpath(path))


def make_icns(path: str) -> bool:
    # macOS .icns via the system `iconutil` (the reliable path; Pillow's ICNS
    # writer is finicky). Build a .iconset of the named sizes Apple expects,
    # then compile. Returns False (without raising) off macOS / without iconutil
    # so the generator still produces the .ico everywhere.
    if sys.platform != "darwin" or shutil.which("iconutil") is None:
        print("skip .icns (needs macOS + iconutil)")
        return False
    specs = [
        ("icon_16x16.png", 16), ("icon_16x16@2x.png", 32),
        ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
        ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256),
        ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512),
        ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        iconset = os.path.join(tmp, "Mememage.iconset")
        os.makedirs(iconset)
        for name, sz in specs:
            render(sz).save(os.path.join(iconset, name))
        subprocess.check_call(["iconutil", "-c", "icns", iconset, "-o", path])
    print("wrote", os.path.relpath(path))
    return True


def main() -> int:
    # A master PNG too — handy for Linux .desktop entries / docs / stores.
    render(1024).save(os.path.join(HERE, "Mememage.png"))
    print("wrote", os.path.relpath(os.path.join(HERE, "Mememage.png")))
    make_ico(os.path.join(HERE, "Mememage.ico"))
    make_icns(os.path.join(HERE, "Mememage.icns"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
