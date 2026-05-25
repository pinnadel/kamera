"""
EXIF metadata extractor for KaMeRa.

Two-tier strategy:
  1. exifread — fast, pure-Python, handles JPEG and NEF cleanly.
  2. exiftool — Perl tool installed at /opt/homebrew/bin/exiftool, called
     via pyexiftool. Required for RAF (Fuji) — exifread emits
     "File format not recognized" on every RAF and returns no tags,
     silently dropping camera / shutter / aperture / iso for every
     Fuji photo.

Per-call exiftool subprocess costs ~150ms (one-shot mode). On a 700-photo
batch with ~140 RAFs, that's ~21s — dwarfed by the ~15s/photo analysis
cost, and only triggered when exifread's tag dict is empty for fields we
care about.
"""

import logging
from pathlib import Path
from datetime import datetime
from typing import Optional
import exifread

from .camera_shake import _parse_fraction, _get_camera_name

logger = logging.getLogger(__name__)

# exifread cannot parse RAF and emits "File format not recognized" once per
# call. The fallback path here handles those files cleanly via exiftool, so
# the warning is just noise that flooded data/app.log (one line per RAF on
# every batch). Silence it.
logging.getLogger("exifread").setLevel(logging.ERROR)


def extract_exif(image_path: str) -> dict:
    """
    Extract the metadata columns we store in the images table.

    Returns a dict with keys:
        camera, shot_at, focal_length_mm, aperture, shutter_speed, iso,
        lens_model, film_simulation

    The first six map 1:1 to images table columns. The last two are extras
    used by shooting_log (Dashboard) — they're returned even when the column
    isn't reachable (e.g. exifread can't read maker notes), in which case
    they're None and the caller stores NULL.

    Tries exifread first; if the result is mostly empty (the common case
    for RAF), falls back to exiftool which handles every camera-RAW format.
    """
    result = _extract_via_exifread(image_path)

    # If exifread got nothing useful, try exiftool. We treat "useful" as
    # "at least one of camera/shot_at/iso is populated" — if all three are
    # None, exifread almost certainly couldn't parse the format.
    if result["camera"] is None and result["shot_at"] is None and result["iso"] is None:
        fallback = _extract_via_exiftool(image_path)
        if fallback is not None:
            return fallback

    return result


def _extract_via_exifread(image_path: str) -> dict:
    """Original exifread-based extractor. Fast, but doesn't handle RAF."""
    with open(image_path, "rb") as f:
        tags = exifread.process_file(f, stop_tag="EXIF ISOSpeedRatings")

    camera: Optional[str] = _get_camera_name(tags)

    shot_at: Optional[str] = None
    date_tag = tags.get("EXIF DateTimeOriginal") or tags.get("Image DateTime")
    if date_tag:
        try:
            dt = datetime.strptime(str(date_tag), "%Y:%m:%d %H:%M:%S")
            shot_at = dt.isoformat(sep=" ")
        except ValueError:
            pass

    focal_tag = tags.get("EXIF FocalLength")
    focal_length_mm: Optional[float] = _parse_fraction(focal_tag) if focal_tag else None

    aperture_tag = tags.get("EXIF FNumber")
    aperture: Optional[float] = _parse_fraction(aperture_tag) if aperture_tag else None

    shutter_tag = tags.get("EXIF ExposureTime")
    shutter_speed: Optional[float] = _parse_fraction(shutter_tag) if shutter_tag else None

    iso_tag = tags.get("EXIF ISOSpeedRatings")
    iso: Optional[int] = None
    if iso_tag:
        try:
            iso = int(str(iso_tag))
        except ValueError:
            pass

    # exifread doesn't reliably surface Fuji film mode or lens model from
    # maker notes, so the exifread tier returns None for both. The exiftool
    # fallback fills them in for RAFs (and JPEGs that fall through).
    lens_tag = tags.get("EXIF LensModel") or tags.get("MakerNote LensModel")
    lens_model: Optional[str] = str(lens_tag).strip() if lens_tag else None

    return {
        "camera": camera,
        "shot_at": shot_at,
        "focal_length_mm": focal_length_mm,
        "aperture": aperture,
        "shutter_speed": shutter_speed,
        "iso": iso,
        "lens_model": lens_model,
        "film_simulation": None,
    }


def _extract_via_exiftool(image_path: str) -> Optional[dict]:
    """Fallback for formats exifread can't read (RAF in particular).

    Returns a metadata dict on success, or None when exiftool is unavailable
    or fails — callers should treat None as "fall back to whatever exifread
    returned, even if mostly empty."
    """
    try:
        from exiftool import ExifToolHelper
    except ImportError:
        logger.warning("pyexiftool not installed; cannot read RAF EXIF.")
        return None

    try:
        with ExifToolHelper() as et:
            metadata_list = et.get_metadata(image_path)
        if not metadata_list:
            return None
        m = metadata_list[0]
    except Exception as exc:
        # exiftool binary missing, file unreadable, etc. — log once per photo
        # at debug level (warning would flood the log on a big batch).
        logger.debug("exiftool extract failed for %s: %s", image_path, exc)
        return None

    # exiftool tag names live in groups; the common ones we want sit under
    # EXIF: / MakerNotes: / Composite: / File:. We accept any group prefix.
    def _g(*keys):
        for k in keys:
            for prefix in ("EXIF:", "MakerNotes:", "Composite:", "File:", ""):
                full = prefix + k
                if full in m:
                    return m[full]
        return None

    make  = _g("Make")
    model = _g("Model")
    camera: Optional[str] = None
    if model:
        model_str = str(model).strip()
        # Match the format _get_camera_name uses: model only when it already
        # contains the make (e.g., "X100VI"), else "Make Model".
        if make and str(make).strip().lower() not in model_str.lower():
            camera = f"{str(make).strip()} {model_str}"
        else:
            camera = model_str

    shot_at: Optional[str] = None
    date_raw = _g("DateTimeOriginal", "CreateDate", "DateTime")
    if date_raw:
        # exiftool returns "2026:04:03 16:44:05" — same format exifread uses.
        try:
            dt = datetime.strptime(str(date_raw)[:19], "%Y:%m:%d %H:%M:%S")
            shot_at = dt.isoformat(sep=" ")
        except ValueError:
            pass

    def _to_float(v):
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _to_int(v):
        if v is None:
            return None
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    # Lens model: Fuji X100VI has a fixed lens written to EXIF:LensModel;
    # X-mount and Z6III bodies usually surface it via MakerNotes:LensID
    # (Nikon canonical) or Composite:LensID. Try the broadest set of keys.
    lens_raw = _g("LensModel", "Lens", "LensInfo", "LensID")
    lens_model: Optional[str] = str(lens_raw).strip() if lens_raw not in (None, "") else None

    # Film simulation / picture control:
    #   - Fuji X-series: MakerNotes:FilmMode resolves to "Velvia"/"Acros"/"Classic Chrome" etc.
    #   - Nikon Z-series: MakerNotes:PictureControlBase resolves to "STANDARD"/"VIVID"/"MONOCHROME" —
    #     not technically a "film simulation" but the closest analogue and worth surfacing
    #     in the same dashboard column.
    film_raw = _g("FilmMode", "PictureControlBase")
    film_simulation: Optional[str] = str(film_raw).strip() if film_raw not in (None, "") else None

    return {
        "camera":          camera,
        "shot_at":         shot_at,
        "focal_length_mm": _to_float(_g("FocalLength")),
        "aperture":        _to_float(_g("FNumber", "ApertureValue")),
        "shutter_speed":   _to_float(_g("ExposureTime")),
        "iso":             _to_int(_g("ISO", "ISOSpeedRatings")),
        "lens_model":      lens_model,
        "film_simulation": film_simulation,
    }
