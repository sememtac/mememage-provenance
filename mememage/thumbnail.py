"""Thumbnail generation for birth certificate embedding."""

import base64
import io


def _get_Image():
    """Lazy import Pillow so the package can be imported without it."""
    from PIL import Image
    return Image


def generate_thumbnail(image_path: str, size: int = 80, quality: int = 30) -> str:
    """Generate a tiny JPEG thumbnail and return as a base64 data URI.

    Args:
        image_path: Path to the source image
        size: Thumbnail dimension (square, will center-crop)
        quality: JPEG quality (lower = smaller, 30 is ~1-2KB)

    Returns:
        Data URI string: 'data:image/jpeg;base64,...'
    """
    img = _get_Image().open(image_path)
    if img.mode == "RGBA":
        img = img.convert("RGB")

    # Center crop to square
    w, h = img.size
    sq = min(w, h)
    left = (w - sq) // 2
    top = (h - sq) // 2
    img = img.crop((left, top, left + sq, top + sq))

    # Resize to thumbnail
    img = img.resize((size, size), _get_Image().LANCZOS)

    # Encode as JPEG
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    return f"data:image/jpeg;base64,{b64}"
