"""Read an image's EXIF metadata into origin fields for the mint editor.

The on-ramp for existing images: a photographer drops a JPEG and its camera,
lens, exposure, date, GPS, and description prefill the soul's origin fields —
no retyping. The creator reviews and removes anything they don't want to
attest, right there in the Conceive tab.

Philosophy (Andy, 2026-06-06): take ALL the readable data in, make no special
rules about which tags matter — including GPS. We don't decide what's
meaningful; the creator does, at mint. Mememage's own location feature
(capture + RSA time-lock on the certificate) is a separate, system-controlled
concern; the EXIF GPS here is just data the file already carries.

Best-effort throughout: returns ``{}`` if Pillow is absent, the image has no
EXIF, or anything fails. Image-focused — nothing here is AI-specific.

Common photo tags get clean, formatted field names (``camera``, ``lens``,
``aperture`` = "f/2.8", ``shutter`` = "1/250", ``iso``, ``focal_length`` =
"50mm", ``captured``, ``creator``, ``copyright``, ``software``,
``description``). Every other readable tag comes in under its snake_cased
EXIF name. GPS is converted to decimal ``gps_latitude`` / ``gps_longitude``
(+ ``gps_altitude`` when present). Opaque binary tags (MakerNote, version
bytes, etc.) are skipped — they aren't human-reviewable "data", just file
internals; that's a render rule, not a per-field exclusion.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

_EXIF_IFD = 0x8769   # Exif sub-IFD (camera/exposure tags)
_GPS_IFD = 0x8825    # GPS sub-IFD

# EXIF tag names consumed by the clean-name mapping below — excluded from the
# long-tail pass so we don't emit both "camera" and "Make"/"Model".
_CONSUMED = {
    "Make", "Model", "LensModel", "FNumber", "ExposureTime",
    "ISOSpeedRatings", "PhotographicSensitivity", "FocalLength",
    "DateTimeOriginal", "DateTime", "Artist", "Copyright", "Software",
    "ImageDescription",
}

# IFD pointer tags + binary internals — byte offsets / file structure, not
# data anyone would attest. Skipped from the long-tail pass (their numeric
# offsets would otherwise leak in as e.g. "gps_info": "298").
_SKIP_TAGS = {
    "GPSInfo", "ExifOffset", "ExifIFD", "InteroperabilityIFD", "InteropOffset",
    "MakerNote", "PrintImageMatching", "UserComment",
}


def _clean(v):
    """Coerce an EXIF value to a trimmed, human-readable string, or ``None``
    if it's missing/opaque binary (so we skip it rather than emit garbage)."""
    if v is None:
        return None
    if isinstance(v, bytes):
        try:
            t = v.decode("ascii")
        except Exception:
            return None
        t = t.replace("\x00", "").strip()
        # Only keep it if it's actually printable text, not binary noise.
        if not t or any(ord(c) < 32 or ord(c) > 126 for c in t):
            return None
        return t
    if isinstance(v, (tuple, list)):
        parts = [p for p in (_clean(x) for x in v) if p]
        return ", ".join(parts) if parts else None
    s = str(v).replace("\x00", "").strip()
    return s or None


def _snake(name) -> str:
    """CamelCase EXIF tag name -> snake_case field key."""
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", str(name))
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.lower()


def _num(v):
    """Best-effort float from a Rational/number; None on failure."""
    try:
        return float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _gps_decimal(dms, ref) -> str | None:
    """(deg, min, sec) + N/S/E/W ref -> signed decimal-degrees string."""
    try:
        d, m, s = (_num(x) or 0.0 for x in dms)
        val = d + m / 60.0 + s / 3600.0
        if str(ref).strip().upper() in ("S", "W"):
            val = -val
        return f"{val:.6f}"
    except Exception:
        return None


def extract_origin_fields(path) -> dict:
    """Return ``{field: str}`` from the image's EXIF, or ``{}``."""
    try:
        from PIL import Image, ExifTags
    except Exception:
        return {}
    try:
        try:  # HEIC/HEIF need the opener registered
            import pillow_heif
            pillow_heif.register_heif_opener()
        except Exception:
            pass
        with Image.open(path) as im:
            exif = im.getexif()
        if not exif:
            return {}
    except Exception as e:
        log.debug("EXIF read failed for %s: %s", path, e)
        return {}

    # Named tag dicts for IFD0 + the Exif sub-IFD (camera/exposure live there).
    ifd0 = {ExifTags.TAGS.get(k, str(k)): v for k, v in exif.items()}
    sub = {}
    try:
        sub = {ExifTags.TAGS.get(k, str(k)): v
               for k, v in exif.get_ifd(_EXIF_IFD).items()}
    except Exception:
        pass
    merged = dict(ifd0)
    merged.update(sub)  # sub-IFD values win on the rare name clash

    out: dict = {}

    # --- clean, formatted common photo fields ---
    make = _clean(merged.get("Make")) or ""
    model = _clean(merged.get("Model")) or ""
    if make or model:
        cam = model if (make and make.lower() in model.lower()) else f"{make} {model}".strip()
        if cam:
            out["camera"] = cam
    for tag, field in (("LensModel", "lens"), ("Software", "software"),
                       ("Artist", "creator"), ("Copyright", "copyright"),
                       ("ImageDescription", "description")):
        val = _clean(merged.get(tag))
        if val:
            out[field] = val
    captured = _clean(merged.get("DateTimeOriginal")) or _clean(merged.get("DateTime"))
    if captured:
        out["captured"] = captured
    fn = _num(merged.get("FNumber"))
    if fn:
        out["aperture"] = f"f/{fn:g}"
    et = _num(merged.get("ExposureTime"))
    if et and et > 0:
        out["shutter"] = f"1/{round(1/et)}" if et < 1 else f"{et:g}s"
    iso = merged.get("ISOSpeedRatings")
    if iso is None:
        iso = merged.get("PhotographicSensitivity")
    if isinstance(iso, (tuple, list)) and iso:
        iso = iso[0]
    iso = _num(iso)
    if iso:
        out["iso"] = str(int(iso))
    fl = _num(merged.get("FocalLength"))
    if fl:
        out["focal_length"] = f"{round(fl)}mm"

    # --- long tail: every other readable tag, under its snake_cased name ---
    for name, v in merged.items():
        if name in _CONSUMED or name in _SKIP_TAGS:
            continue
        cleaned = _clean(v)
        if cleaned is None:
            continue
        key = _snake(name)
        out.setdefault(key, cleaned)

    # --- GPS: decimal lat/lon (+ altitude), the data the file carries ---
    try:
        from PIL import ExifTags as _ET
        gps_raw = exif.get_ifd(_GPS_IFD)
        gps = {_ET.GPSTAGS.get(k, str(k)): v for k, v in gps_raw.items()}
        lat = _gps_decimal(gps.get("GPSLatitude"), gps.get("GPSLatitudeRef"))
        lon = _gps_decimal(gps.get("GPSLongitude"), gps.get("GPSLongitudeRef"))
        if lat:
            out["gps_latitude"] = lat
        if lon:
            out["gps_longitude"] = lon
        alt = _num(gps.get("GPSAltitude"))
        if alt:
            out["gps_altitude"] = f"{alt:g}m"
    except Exception:
        pass

    return out
