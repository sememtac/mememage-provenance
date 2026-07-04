"""Regression: manual non-PNG uploads must be normalized to PNG before the
bar codec sees them. A JPG drag-upload used to fail conception with
"Bar encoding requires PNG format" because the server saved the upload with
its original extension and handed that path straight to bar.embed_bar.

See mememage.server._ensure_png_upload.
"""
import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from mememage import server, bar  # noqa: E402


def _make(tmp_path, name, fmt, size=(1300, 420)):
    p = tmp_path / name
    img = Image.new("RGB", size, (110, 120, 130))
    img.save(p, format=fmt)
    return p


def test_jpg_upload_normalized_and_barrable(tmp_path):
    jpg = _make(tmp_path, "scenic.jpg", "JPEG")
    out = server._ensure_png_upload(jpg)

    # Converted to a .png sibling; original removed.
    assert out.suffix == ".png"
    assert out.exists()
    assert not jpg.exists()

    # The whole point: embed_bar must now accept it (no ValueError) and the
    # bar must round-trip.
    ident, chash = "dark-0000000000000000", "0123456789abcdef"
    bar.embed_bar(str(out), ident, chash)
    decoded = bar.extract_bar(str(out))
    assert decoded, "bar should decode after PNG normalization"
    assert ident in str(decoded)
    assert chash in str(decoded)


def test_png_upload_passes_through_untouched(tmp_path):
    png = _make(tmp_path, "render.png", "PNG")
    out = server._ensure_png_upload(png)
    assert out == png
    assert out.exists()


def test_webp_upload_normalized(tmp_path):
    try:
        src = _make(tmp_path, "shot.webp", "WEBP")
    except Exception:
        pytest.skip("WEBP encoder unavailable in this Pillow build")
    out = server._ensure_png_upload(src)
    assert out.suffix == ".png"
    assert out.exists()
    assert not src.exists()
