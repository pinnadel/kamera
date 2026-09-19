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
    # Normalise before de-duplicating: the directory scan and Spotlight can
    # return the same library spelled differently (a trailing slash, a symlink
    # through /Users vs /System/Volumes/Data), and a set of raw strings then
    # lists it twice in the picker.
    candidates = {
        str(Path(p).resolve()): p
        for p in (set(found) | ({open_library} if open_library else set()))
    }
    for path in sorted(candidates.values(), key=lambda p: p.lower()):
        resolved = str(Path(path).resolve())
        if not _is_user_library(path) or resolved in seen:
            continue
        seen.add(resolved)
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


class PhotosLibraryMissing(Exception):
    """The library package is not at that path any more.

    Kept distinct from PhotosAccessError on purpose. On macOS a permission
    denial and a deleted file are indistinguishable at the syscall level, and
    conflating them is what sent a whole debugging session in the wrong
    direction (docs/Learning-Journal.md, 2026-09-18): "not found" was reported
    where "not allowed" was meant. Here the cause IS absence, and the user
    needs a different instruction — pick another library, not grant a
    permission — so it gets its own type and its own message.
    """


def _library_is_really_absent(library_path: str) -> bool:
    """True only when we can actually SEE that the library is not there.

    `Path.exists()` cannot answer this. Under a TCC denial it returns False for
    a package that exists and is perfectly readable by another process, which
    is the exact confusion that cost a debugging session on 2026-09-18 —
    "not found" reported where "not allowed" was meant.

    Listing the PARENT directory can answer it: if the parent enumerates and
    the package is not among its entries, it is genuinely gone. If the parent
    itself is unreadable we have learned nothing, and must not claim absence —
    so the ambiguous case returns False and the caller reports an access
    problem instead, which is the one that has an action attached to it.
    """
    path = Path(library_path)
    try:
        names = {entry.name for entry in path.parent.iterdir()}
    except OSError:
        return False
    return path.name not in names


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
            except FileNotFoundError as exc:
                # osxphotos decides this with its own exists() check, so it
                # inherits the same ambiguity. Only claim "gone" when the
                # parent directory proves it.
                if _library_is_really_absent(library_path):
                    raise PhotosLibraryMissing(library_path) from exc
                raise PhotosAccessError(str(exc)) from exc
            except PermissionError as exc:
                raise PhotosAccessError(str(exc)) from exc
            except OSError as exc:
                # TCC denials surface as EPERM/EACCES from open()/scandir().
                if exc.errno in (1, 13):
                    raise PhotosAccessError(str(exc)) from exc
                raise
            _db_cache[library_path] = db
        return db


# Photos stores a user-created Smart Album with this kind. Apple's own
# built-ins (Favourites, Recents, Screenshots, …) use the 1500–1650 range too
# but carry no stored title — Photos localises those at runtime — so requiring
# a title is what separates "the user made this" from "the app ships this".
_SMART_ALBUM_KIND = 1507


class SmartAlbumUnavailable(Exception):
    """Photos could not be asked for a Smart Album's members.

    Smart Albums are the one thing here that cannot be answered from the
    catalogue, so this failure is structural rather than incidental — see
    smart_album_member_uuids().
    """


def _osascript(script: str, timeout: int = 120) -> str:
    """Run one AppleScript and return its stdout, or raise SmartAlbumUnavailable."""
    import subprocess

    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError as exc:            # not macOS
        raise SmartAlbumUnavailable("osascript is not available") from exc
    except subprocess.TimeoutExpired as exc:
        raise SmartAlbumUnavailable("Photos did not answer in time") from exc
    if proc.returncode != 0:
        raise SmartAlbumUnavailable((proc.stderr or "Photos refused the request").strip())
    return proc.stdout


def smart_albums(library_path: str, refresh: bool = False) -> dict[str, str]:
    """uuid → title for every user-created Smart Album in the library."""
    db = _db_for(library_path, refresh=refresh)
    return {
        uuid: detail["title"]
        for uuid, detail in db._dbalbum_details.items()
        if detail.get("kind") == _SMART_ALBUM_KIND
        and detail.get("title")
        and not detail.get("intrash")
    }


def smart_album_member_uuids(album_uuid: str) -> list[str]:
    """The assets currently matching a Smart Album, via AppleScript.

    **This cannot come from the catalogue.** A Smart Album is a stored query,
    not a stored list: its rows in the album↔asset join table are empty
    (verified 2026-09-19 — 0 rows for a Smart Album holding 2114 photos) and
    Photos evaluates the predicate live. osxphotos therefore does not expose
    Smart Albums at all, and asking AppleScript is the only route.

    Addressed by `album id`, never by name. Album names are not unique in
    Photos, and `album "Foo"` silently resolves to whichever one comes first —
    a wrong-album read that looks like a correct one.

    Measured at ~0.5s for 2114 members, so this is cheap enough to call per
    analysis run. It does require Photos.app to be running with this library
    open, which every write already requires.
    """
    raw = _osascript(
        f'tell application "Photos" to get id of media items in album id "{album_uuid}"'
    )
    # Photos returns "<uuid>/L0/001, <uuid>/L0/001, …"; KaMeRa keys on the
    # bare UUID, which is what osxphotos uses too.
    return [
        part.strip().split("/")[0]
        for part in raw.split(",")
        if part.strip()
    ]


def smart_album_counts(album_uuids: list[str]) -> dict[str, int]:
    """Member counts for several Smart Albums in ONE osascript call.

    Batched because the cost here is process spawn plus Photos IPC, not the
    counting — asking separately would make the album picker wait half a
    second per Smart Album. Best-effort by design: the picker must still open
    when Photos is closed, so a failure yields no counts rather than an error.
    """
    if not album_uuids:
        return {}
    lines = "\n".join(
        f'set out to out & "{u}=" & (count of media items in album id "{u}") & linefeed'
        for u in album_uuids
    )
    script = f'tell application "Photos"\nset out to ""\n{lines}\nreturn out\nend tell'
    try:
        raw = _osascript(script, timeout=60)
    except SmartAlbumUnavailable as exc:
        logger.debug("Smart Album counts unavailable: %s", exc)
        return {}

    counts: dict[str, int] = {}
    for line in raw.splitlines():
        uuid, _, value = line.partition("=")
        try:
            counts[uuid.strip()] = int(value.strip())
        except ValueError:
            continue
    return counts


def list_albums(library_path: str, refresh: bool = False) -> list[dict]:
    """Every album in the library — regular and Smart — with its count.

    Smart Albums carry `smart: True` and may carry `count: None`. Their counts
    come from Photos over AppleScript (the catalogue does not store Smart Album
    membership at all), batched into one call and treated as best-effort: the
    picker has to open even when Photos is closed, so a missing count is a
    missing number, never an error.
    """
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
            "smart": False,
        })

    smart = smart_albums(library_path, refresh=refresh)
    counts = smart_album_counts(list(smart)) if smart else {}
    for uuid, title in smart.items():
        albums.append({
            "uuid": uuid,
            "name": title,
            # Photos shows Smart Albums at the top level, not inside the folder
            # tree the catalogue records for regular albums.
            "path": title,
            "folders": [],
            "count": counts.get(uuid),
            "smart": True,
        })

    albums.sort(key=lambda a: a["path"].lower())
    return albums


def _album_photos(db, library_path: str, album_uuid: str) -> list:
    """Every PhotoInfo in an album — regular or Smart.

    One resolver for both kinds so nothing downstream has to know the
    difference. A regular album's membership comes from the catalogue; a Smart
    Album's can only come from Photos itself (see smart_album_member_uuids),
    and the UUIDs it returns are then looked back up in the catalogue so both
    paths hand back the same objects.
    """
    album = next((a for a in db.album_info if a.uuid == album_uuid), None)
    if album is not None:
        return list(album.photos)

    smart = smart_albums(library_path)
    if album_uuid not in smart:
        raise KeyError(f"Album {album_uuid} not found in {library_path}")

    member_uuids = smart_album_member_uuids(album_uuid)
    if not member_uuids:
        return []
    # Order follows Photos' own, not the lookup's, so the grid's initial
    # ordering matches what the user sees in the album.
    by_uuid = {p.uuid: p for p in db.photos(uuid=member_uuids, movies=True)}
    return [by_uuid[u] for u in member_uuids if u in by_uuid]


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
    photos = _album_photos(db, library_path, album_uuid)

    assets: list[PhotoAsset] = []
    skipped = {"movies": 0, "missing_originals": 0, "unsupported": 0}

    for photo in photos:
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


def album_keyword_state(
    library_path: str, album_uuid: str, refresh: bool = True
) -> tuple[dict[str, list[str]], set[str], dict[str, dict]]:
    """Read every album asset's keywords, plus the library's whole UUID set.

    Returns `(in_album, library_uuids, members)`:
      - `in_album` maps asset UUID → its keywords, for EVERY photo in the
        album. Deliberately unfiltered, unlike album_assets(): a movie, an
        unsupported format or an original evicted by "Optimize Mac Storage" is
        still *in the album*, and treating it as absent would misreport it as
        `not_in_album` and invite dropping a perfectly good row.
      - `library_uuids` is every asset UUID in the library, which is what
        separates "this photo left the album" from "this photo is gone".
      - `members` maps the same UUIDs to {filename, is_movie, has_original,
        supported}, mirroring album_assets()'s three drop reasons. Without it
        a reconcile cannot tell a photo KaMeRa has simply never analysed from
        a movie it is never going to analyse, and every album's 200-odd videos
        would be reported as drift. `supported` falls back to the original
        filename's suffix when the original is not on this Mac, so an evicted
        HEIC still reads as a photo KaMeRa would take.

    `refresh=True` by default: the catalogue is cached per library path, and a
    reconcile's entire job is to see keywords the user changed in Photos since
    that cache was built. Reading a stale copy would report drift that is not
    there and miss the drift that is. Costs one catalogue re-parse, which is
    the price of an answer that is true right now.
    """
    db = _db_for(library_path, refresh=refresh)
    # Smart Albums go through the same resolver, which means a reconcile on one
    # compares against its membership AS IT IS RIGHT NOW. That is the honest
    # reading: the album is a live query, so "in the album" has no other
    # meaning. It does mean a Smart Album built on KaMeRa keywords will show
    # members coming and going as you cull — expected, and the reason
    # `not_in_album` still defaults to leaving rows alone.
    photos = _album_photos(db, library_path, album_uuid)
    in_album = {photo.uuid: list(photo.keywords or []) for photo in photos}

    members: dict[str, dict] = {}
    for photo in photos:
        path = photo.path
        name = photo.original_filename or (Path(path).name if path else photo.uuid)
        suffix = Path(path).suffix.lower() if path else Path(name).suffix.lower()
        members[photo.uuid] = {
            "filename": name,
            "is_movie": bool(photo.ismovie),
            "has_original": bool(path) and not photo.ismissing,
            "supported": suffix in SUPPORTED_EXTENSIONS,
        }

    library_uuids = {photo.uuid for photo in db.photos()}
    return in_album, library_uuids, members


def album_name(library_path: str, album_uuid: str) -> str | None:
    db = _db_for(library_path)
    album = next((a for a in db.album_info if a.uuid == album_uuid), None)
    if album is not None:
        return album.title
    return smart_albums(library_path).get(album_uuid)
