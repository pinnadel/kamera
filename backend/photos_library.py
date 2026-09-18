"""
Read-only access to the Apple Photos library.

KaMeRa can analyse Photos assets *in place*: `originals/<hex>/<UUID>.ext` are
real files with real absolute paths, and rawpy / pillow-heif decode them
directly (measured: RAF 1.3s half-size, HEIC 0.2s). Nothing is exported and
nothing is copied.

What this module does NOT do is write. Decisions go back through
backend/sources/photos.py as keywords; this module never mutates the library
and never touches backend/file_mover.

## Why osxphotos rather than reading Photos.sqlite directly

The catalogue schema changes with every macOS release (this library reports
DB version 5001). osxphotos tracks that drift; a hand-rolled query would break
on the next upgrade. AppleScript is not an alternative for bulk reads either —
`get name of every album` timed out at 60s against a 360 MB catalogue, which is
exactly why osxphotos parses the sqlite itself.

## Platform

macOS-only by necessity. Every import is lazy and every entry point is gated on
is_available(), so a Linux/Windows install imports this module fine and simply
has no Photos source type. See the rule-4 exception note in CLAUDE.md.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from backend.constants import PHOTOS_SOURCE_PREFIX, SUPPORTED_EXTENSIONS

logger = logging.getLogger(__name__)

# PhotosDB construction copies and parses the library catalogue — tens of
# seconds on a large library, so it is cached per library path. Guarded because
# two requests (the album picker and an analyze call) can race on first use.
_db_cache: dict[str, object] = {}
_db_lock = threading.Lock()


@dataclass(frozen=True)
class PhotoAsset:
    """One analysable asset, decoupled from osxphotos' PhotoInfo.

    Nothing downstream should depend on osxphotos types — this crosses the
    boundary instead, so the rest of the backend stays importable without it.
    """

    uuid: str                 # the Photos asset UUID
    original_filename: str    # 'DSCF1234.RAF' — the on-disk name is a UUID
    path: str                 # absolute path to the original inside the library
    format: str               # 'RAF' / 'HEIC' — derived from the real suffix
    date: datetime | None


def is_available() -> bool:
    """True when this machine can talk to a Photos library at all."""
    if platform.system() != "Darwin":
        return False
    try:
        import osxphotos  # noqa: F401
        import photoscript  # noqa: F401
    except Exception as exc:  # pragma: no cover - depends on the install
        logger.debug("Photos support unavailable: %s", exc)
        return False
    return True


#: Where Photos records which library it last opened. osxphotos reads this to
#: answer current_library_path(). It lives in Photos' own container, so it is
#: guarded by Settings -> Privacy & Security -> Files & Folders -> <process> ->
#: Photos — a DIFFERENT permission from the Full Disk Access that covers the
#: library package itself.
_PHOTOS_PREF_PLIST = (
    Path.home()
    / "Library/Containers/com.apple.Photos/Data/Library/Preferences/com.apple.Photos.plist"
)


def photos_pref_readable() -> bool:
    """Can this process read the preference naming the open library?

    Under a TCC denial the file reads as simply absent — `is_file()` is False
    and `open()` raises — which is indistinguishable from "Photos was never
    opened" unless you ask the question separately. Hence this helper: it turns
    a misleading "no library found" into "not allowed to look".
    """
    try:
        with open(_PHOTOS_PREF_PLIST, "rb") as fp:
            fp.read(1)
        return True
    except (PermissionError, OSError):
        return False


#: Libraries macOS keeps for its own features (Generative Playground, Visual
#: Intelligence, Syndication). Real, but never what the user means.
_INTERNAL_LIBRARY_MARKERS = (
    "/Library/Photos/Libraries/",   # Generative Playground, Visual Intelligence, Syndication
    "/Library/Containers/",         # Sidecar temp paths that Spotlight also reports
)

#: Where the user's chosen library is remembered. A plain settings row, NOT a
#: tunable setting — it is machine-specific and has no business in a taste-model
#: backup.
_SELECTED_LIBRARY_KEY = "photos_library_path"

#: Scratch space that can hold real .photoslibrary dirs but never a library the
#: user means — temp dirs, and pytest's tmp_path, which lives under the first.
_SCRATCH_PREFIXES = ("/private/var/folders/", "/tmp/", "/var/folders/")


def _is_user_library(path: str) -> bool:
    if not path.startswith("/"):
        return False
    if any(m in path for m in _INTERNAL_LIBRARY_MARKERS):
        return False
    if any(path.startswith(prefix) for prefix in _SCRATCH_PREFIXES):
        return False
    return True


#: Where people actually keep Photos libraries. Scanned one level deep only —
#: a library sits directly in one of these, never buried.
_LIBRARY_SEARCH_ROOTS = ("Pictures", "Downloads", "Documents", "Desktop", "Movies")


def _scan_for_libraries() -> list[str]:
    """Find .photoslibrary packages by looking, not by asking Spotlight."""
    roots = [Path.home()] + [Path.home() / r for r in _LIBRARY_SEARCH_ROOTS]
    volumes = Path("/Volumes")
    try:
        roots += [v for v in volumes.iterdir() if v.is_dir()]
    except OSError:
        pass  # /Volumes unreadable or a mount is hanging — skip it

    found: list[str] = []
    for root in roots:
        try:
            for entry in root.iterdir():
                if entry.name.lower().endswith(".photoslibrary") and entry.is_dir():
                    found.append(str(entry))
        except OSError:
            continue  # permission denied or gone — just means "none here"
    return found


def list_libraries() -> list[dict]:
    """Every Photos library on this Mac that the user might mean.

    Uses Spotlight (`mdfind`) rather than Photos' own preference file. That
    matters: the preference lives in Photos' container and is gated behind a
    Files & Folders permission that cannot always be granted to a detached
    process — see docs/PLAN-photos-source.md. Spotlight needs no such grant, so
    library discovery keeps working where auto-detection does not.
    """
    if not is_available():
        return []

    found: set[str] = set(_scan_for_libraries())

    # Spotlight as a bonus, not the basis: mdfind results are TCC-filtered per
    # CALLING process, and the detached backend gets an empty list even for
    # libraries it can open and read perfectly well (observed 2026-09-18). The
    # directory scan above needs no Spotlight and no extra permission beyond
    # being able to read the folder.
    try:
        from osxphotos.utils import list_photo_libraries

        found.update(list_photo_libraries())
    except Exception as exc:
        logger.debug("Spotlight library discovery unavailable: %s", exc)

    open_library = detected_library_path()
    seen, libraries = set(), []
    for path in sorted(set(found) | ({open_library} if open_library else set())):
        if not _is_user_library(path) or path in seen:
            continue
        seen.add(path)
        libraries.append({
            "path": path,
            "name": Path(path).stem,
            "is_open": open_library is not None and path == open_library,
            "readable": os.access(path, os.R_OK),
        })
    return libraries


def selected_library_path() -> str | None:
    """The library the user explicitly chose, if any."""
    from backend.database import get_setting

    return get_setting(_SELECTED_LIBRARY_KEY) or None


def set_selected_library(path: str | None) -> None:
    from backend.database import set_setting

    set_setting(_SELECTED_LIBRARY_KEY, path or "")


def detected_library_path() -> str | None:
    """Auto-detected open library — best effort, may be permission-blocked."""
    if not is_available():
        return None
    try:
        from osxphotos.utils import get_last_library_path

        return get_last_library_path()
    except Exception as exc:
        logger.warning("Could not determine the open Photos library: %s", exc)
        return None


def current_library_path() -> str | None:
    """The library Photos.app currently has open, or None.

    This is the ONLY handle on library identity: photoscript's
    PhotosLibrary().name returns "Photos" (the application), not the library.
    It matters because AppleScript writes always land in whatever library is
    open — so a decision made against the wrong one must be refused, not
    written. See backend/sources/photos.py.

    Returns None both when no library has ever been opened AND when this
    process may not read the preference. Callers that report to the user
    should ask photos_pref_readable() to tell those apart.
    """
    chosen = selected_library_path()
    if chosen:
        return chosen
    return detected_library_path()


def library_id(library_path: str) -> str:
    """Short stable id for a library path, used in the source_folder key."""
    return hashlib.sha1(str(library_path).encode("utf-8")).hexdigest()[:12]


def photos_source_folder(library_path: str, album_uuid: str) -> str:
    """The opaque tab key for one album. See constants.PHOTOS_SOURCE_PREFIX."""
    return f"{PHOTOS_SOURCE_PREFIX}{library_id(library_path)}/{album_uuid}"


class PhotosAccessError(Exception):
    """The library exists but this process is not allowed to read it.

    macOS guards a .photoslibrary package behind Full Disk Access, and TCC
    attributes that permission to the *responsible* process — which is not
    necessarily the binary doing the reading. A python spawned detached from an
    app bundle can end up as its own TCC identity and need its own grant, so
    "KaMeRa.app has Full Disk Access" does not by itself mean the backend does.

    Raised so callers can say that, instead of surfacing a bare OSError.
    """


def _db_for(library_path: str, refresh: bool = False):
    from osxphotos import PhotosDB

    with _db_lock:
        if refresh:
            _db_cache.pop(library_path, None)
        db = _db_cache.get(library_path)
        if db is None:
            logger.info("Reading Photos library catalogue: %s", library_path)
            try:
                db = PhotosDB(dbfile=library_path)
            except PermissionError as exc:
                raise PhotosAccessError(str(exc)) from exc
            except OSError as exc:
                # TCC denials surface as EPERM/EACCES from open()/scandir().
                if exc.errno in (1, 13):
                    raise PhotosAccessError(str(exc)) from exc
                raise
            _db_cache[library_path] = db
        return db


def list_albums(library_path: str, refresh: bool = False) -> list[dict]:
    """Every album in the library, with its folder path and asset count."""
    db = _db_for(library_path, refresh=refresh)
    albums = []
    for album in db.album_info:
        folders = list(album.folder_names or [])
        albums.append({
            "uuid": album.uuid,
            "name": album.title,
            "path": "/".join(folders + [album.title]),
            "folders": folders,
            "count": len(album),
        })
    albums.sort(key=lambda a: a["path"].lower())
    return albums


def album_assets(library_path: str, album_uuid: str, refresh: bool = False) -> tuple[list[PhotoAsset], dict]:
    """Analysable assets in one album, plus counts of what was left out.

    Three kinds of asset are dropped, each for a different reason:

      - movies: KaMeRa scores photos.
      - originals that are not on this Mac ("Optimize Mac Storage" evicts them;
        `path` is None). They are NOT substituted with a derivative: those are
        768×1024, and a sharpness score computed on one would be meaningless
        and would then enter training_samples as if it were real.
      - anything whose suffix KaMeRa cannot decode.
    """
    db = _db_for(library_path, refresh=refresh)
    album = next((a for a in db.album_info if a.uuid == album_uuid), None)
    if album is None:
        raise KeyError(f"Album {album_uuid} not found in {library_path}")

    assets: list[PhotoAsset] = []
    skipped = {"movies": 0, "missing_originals": 0, "unsupported": 0}

    for photo in album.photos:
        if photo.ismovie:
            skipped["movies"] += 1
            continue
        path = photo.path
        if not path or photo.ismissing:
            skipped["missing_originals"] += 1
            continue
        suffix = Path(path).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            skipped["unsupported"] += 1
            continue
        assets.append(PhotoAsset(
            uuid=photo.uuid,
            original_filename=photo.original_filename or Path(path).name,
            path=path,
            format=Path(path).suffix.lstrip(".").upper(),
            date=photo.date,
        ))

    return assets, skipped


def album_name(library_path: str, album_uuid: str) -> str | None:
    db = _db_for(library_path)
    album = next((a for a in db.album_info if a.uuid == album_uuid), None)
    return album.title if album else None
