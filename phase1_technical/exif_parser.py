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
import os
import shutil
from pathlib import Path
import re
from datetime import datetime
from typing import Optional
import exifread

from .camera_shake import _parse_fraction, _get_camera_name, canonical_camera_name

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


# Resolved once. A LaunchAgent inherits launchd's PATH
# (/usr/bin:/bin:/usr/sbin:/sbin), not the login shell's, so `exiftool` from
# Homebrew is invisible to the backend even though it is plainly installed.
# That cost every RAF and HIF its entire EXIF block — silently, because the
# photo still scored and the row was written with every metadata column NULL.
# 1 530 rows of shooting_log before it was caught on 2026-09-20.
#
# The LaunchAgent now declares a PATH (launcher/install_agent.sh); this is the
# second line of defence, so a wrong PATH degrades to "works" rather than to
# "quietly loses data".
_EXIFTOOL_FALLBACKS = (
    "/opt/homebrew/bin/exiftool",   # Apple Silicon Homebrew
    "/usr/local/bin/exiftool",      # Intel Homebrew
    "/usr/bin/exiftool",            # distro packages
)

_exiftool_path: Optional[str] = None
_exiftool_warned = False


def _find_exiftool() -> Optional[str]:
    """Locate the exiftool binary, PATH first, then the usual install roots."""
    global _exiftool_path
    if _exiftool_path is not None:
        return _exiftool_path
    found = shutil.which("exiftool")
    if not found:
        for candidate in _EXIFTOOL_FALLBACKS:
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                found = candidate
                break
    _exiftool_path = found or ""
    return _exiftool_path or None


def _warn_missing_exiftool_once() -> None:
    """Warn loudly, once per process.

    This failure used to be logged at debug level to keep a big batch from
    flooding the log. That was the right instinct for a per-photo error and
    the wrong one for a missing dependency: the condition is constant for the
    whole run, so one warning says everything and nothing is lost by it.
    """
    global _exiftool_warned
    if _exiftool_warned:
        return
    _exiftool_warned = True
    logger.warning(
        "exiftool not found (PATH=%s). RAF/HIF files will be analysed with NO "
        "EXIF at all — no camera, date, lens, focal length, aperture or ISO. "
        "Install it (brew install exiftool) or re-run launcher/install_agent.sh "
        "so the backend's PATH includes it.",
        os.environ.get("PATH", ""),
    )


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

    executable = _find_exiftool()
    if executable is None:
        _warn_missing_exiftool_once()
        return None

    try:
        # common_args=["-G"] drops pyexiftool's default "-n".
        #
        # "-n" disables exiftool's print conversion, which is what we want for
        # FNumber/ISO but silently destroys every enum-backed tag: FilmMode
        # came back as 2816 instead of "Reala ACE", so the dashboard's film
        # simulation histogram was a list of raw Fuji opcodes. Keeping print
        # conversion on means the numeric tags arrive formatted instead
        # ("23.0 mm", "1/140"), which _to_float below is built to handle —
        # a parsing job we control, versus an enum table we would have to
        # chase every time Fujifilm ships a new simulation.
        with ExifToolHelper(executable=executable, common_args=["-G"]) as et:
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

    # Route through the same normaliser exifread's path uses. This branch used
    # to build "Make Model" itself, which is why RAF/HIF files logged
    # "FUJIFILM X100VI" while the JPEGs off the same body logged "X100VI" —
    # one camera, two rows, in every camera statistic on the dashboard.
    camera: Optional[str] = canonical_camera_name(_g("Model"))

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
        """Parse exiftool's printed numeric forms.

        With print conversion on, exiftool formats numbers for humans:
        "23.0 mm", "f/2.8", "1/140". Bare floats still arrive for tags it
        doesn't decorate, so the plain path stays first.
        """
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            pass
        # "f/2.8" → 2.8
        m = re.match(r"^f/\s*([0-9]*\.?[0-9]+)", s, re.IGNORECASE)
        if m:
            return float(m.group(1))
        # "1/140" → 0.00714  (shutter speeds)
        m = re.match(r"^([0-9]*\.?[0-9]+)\s*/\s*([0-9]*\.?[0-9]+)$", s)
        if m and float(m.group(2)) != 0:
            return float(m.group(1)) / float(m.group(2))
        # "23.0 mm" → 23.0  (leading number, unit dropped)
        m = re.match(r"^([-+]?[0-9]*\.?[0-9]+)", s)
        if m:
            return float(m.group(1))
        return None

    def _to_int(v):
        f = _to_float(v)
        return None if f is None else int(f)

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
    # Guard against a regression to raw opcodes: a bare integer here means
    # print conversion was off again, and a histogram of "2816" is worse than
    # an honest blank.
    if film_simulation is not None and re.fullmatch(r"-?\d+", film_simulation):
        logger.debug("Dropping unresolved numeric FilmMode %r", film_simulation)
        film_simulation = None

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
