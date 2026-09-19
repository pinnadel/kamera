"""
Apple Photos album endpoints.

Reading is backend/photos_library.py, writing is backend/sources/photos.py;
this module is only the HTTP surface over them. Everything 501s off macOS
rather than 500ing, so a non-Darwin install degrades to "this source type does
not exist here" instead of looking broken.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend import photos_library
from backend.photos_library import (
    PhotosAccessError,
    PhotosLibraryMissing,
    SmartAlbumUnavailable,
)
from backend.database import get_db
from backend.routers.analysis import _run_batch
from backend.state import _progress, _stop_event, watcher

logger = logging.getLogger(__name__)
router = APIRouter()


class AnalyzePhotosAlbumRequest(BaseModel):
    library_path: str
    album_uuid: str


def _require_photos() -> None:
    if not photos_library.is_available():
        raise HTTPException(
            status_code=501,
            detail="Apple Photos albums are only available on macOS with "
                   "osxphotos installed",
        )


def _require_open_library(library_path: str) -> None:
    """Refuse to work against a library Photos.app does not currently have open.

    Reads would actually succeed (osxphotos parses the catalogue directly), but
    every subsequent WRITE goes through AppleScript, which only ever addresses
    the open library. Indexing an album KaMeRa could never write back to is a
    trap, so it is refused at the door.
    """
    current = photos_library.current_library_path()
    if current and Path(current) != Path(library_path):
        raise HTTPException(
            status_code=409,
            detail=f"Photos has a different library open ({Path(current).stem}). "
                   f"Open {Path(library_path).stem} first.",
        )


_PREF_ACCESS_HELP = (
    "KaMeRa may not read which Photos library is open. The backend runs "
    "detached from the app, so macOS treats it as its own identity: turn on "
    "System Settings \u2192 Privacy & Security \u2192 Files & Folders \u2192 "
    "python3.14 \u2192 Photos, then restart KaMeRa."
)

_ACCESS_HELP = (
    "KaMeRa cannot read your Photos library. Grant Full Disk Access to the app "
    "in System Settings \u2192 Privacy & Security, then restart it. Note macOS "
    "attributes this to the process that actually reads \u2014 a separate "
    "\u201cpython\u201d entry may need it too."
)




def _smart_album_unreachable(exc: Exception) -> HTTPException:
    """Photos would not answer for a Smart Album's contents.

    A Smart Album is a stored query, so its members exist only inside Photos —
    unlike a regular album, whose membership KaMeRa reads straight from the
    catalogue. That makes a running Photos.app a hard requirement here and only
    here, which is worth saying plainly rather than reporting a generic error.
    """
    return HTTPException(
        status_code=503,
        detail=f"Photos could not list this Smart Album's photos ({exc}). A Smart "
               f"Album is a live query, so Photos has to be running with this "
               f"library open to answer it — regular albums do not need that.",
    )

def _missing_library(library_path: str) -> HTTPException:
    """A gone library is a 404 with an instruction, not a 403 or a 500.

    Distinct from the access-denied path on purpose: nothing about granting a
    permission helps here, and telling the user to open System Settings for a
    library that no longer exists sends them somewhere useless.
    """
    name = Path(library_path).stem
    return HTTPException(
        status_code=404,
        detail=f"The Photos library “{name}” is no longer at {library_path}. "
               f"It was moved or deleted — pick another library in the album picker.",
    )

class SelectLibraryRequest(BaseModel):
    library_path: str | None = None


@router.get("/photos-libraries")
def photos_libraries():
    """Photos libraries on this Mac, for the picker's library step.

    Found via Spotlight, which needs no special permission — unlike Photos'
    own "which library is open" preference, whose Files & Folders grant cannot
    always be given to a detached backend process. This is why choosing a
    library explicitly is the reliable path and auto-detection is only a hint.
    """
    _require_photos()
    return {
        "libraries": photos_library.list_libraries(),
        "selected": photos_library.selected_library_path(),
        "detected": photos_library.detected_library_path(),
    }


@router.post("/photos-library")
def select_photos_library(request: SelectLibraryRequest):
    """Remember which library to use. Pass null to fall back to auto-detection."""
    _require_photos()
    # The picker lets the user type a path, and people type "~".
    path = str(Path((request.library_path or "").strip()).expanduser()).rstrip("/") \
        if (request.library_path or "").strip() else ""
    if path:
        if not Path(path).is_dir() or not path.endswith(".photoslibrary"):
            raise HTTPException(
                status_code=400,
                detail=f"Not a Photos library: {path}",
            )
        if not os.access(path, os.R_OK):
            raise HTTPException(status_code=403, detail=_ACCESS_HELP)
    photos_library.set_selected_library(path or None)
    return {"status": "ok", "selected": photos_library.selected_library_path()}


@router.get("/photos-albums")
def photos_albums(refresh: bool = False, library_path: str | None = None):
    """Every album in the currently open Photos library.

    First call parses the library catalogue, which is slow on a large library —
    this is a sync `def`, so FastAPI runs it on a worker thread and the event
    loop stays responsive. The UI should show an indeterminate spinner rather
    than assume it is instant.
    """
    _require_photos()
    if library_path:
        library_path = str(Path(library_path).expanduser()).rstrip("/")
    library = library_path or photos_library.current_library_path()
    if not library:
        # A TCC denial makes the preference read as absent, so "no library" and
        # "not allowed to look" are the same symptom. Naming the right one is
        # the difference between a two-second fix and a dead end.
        raise HTTPException(
            status_code=404,
            detail=(
                "Could not tell which Photos library is open"
                if not photos_library.photos_pref_readable()
                else "No Photos library found"
            ),
        )
    try:
        albums = photos_library.list_albums(library, refresh=refresh)
    except PhotosLibraryMissing:
        raise _missing_library(library)
    except PhotosAccessError as exc:
        logger.warning("Photos library not readable: %s", exc)
        raise HTTPException(status_code=403, detail=_ACCESS_HELP)
    return {
        "library_path": library,
        "library_name": Path(library).stem,
        "albums": albums,
    }


@router.get("/photos-library-status")
def photos_library_status(source_folder: str):
    """Is the library this tab belongs to the one Photos currently has open?

    Drives the warning strip on a Photos tab. Decisions stay enabled when this
    reports not-ok: the 409 the user then gets names the library they need to
    open, which is more useful than a silently dead keyboard.
    """
    if not photos_library.is_available():
        return {"ok": False, "reason": "unavailable",
                "expected_library": None, "current_library": None}

    with get_db() as conn:
        row = conn.execute(
            "SELECT library_path, album_name FROM photos_albums WHERE source_folder = ?",
            (source_folder,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Not a known Photos album tab")

    expected = row["library_path"]
    current = photos_library.current_library_path()
    ok = bool(current) and Path(current) == Path(expected)
    return {
        "ok": ok,
        "reason": None if ok else "wrong_library",
        "album_name": row["album_name"],
        "expected_library": expected,
        "expected_library_name": Path(expected).stem,
        "current_library": current,
        "current_library_name": Path(current).stem if current else None,
    }


@router.post("/analyze-photos-album")
def analyze_photos_album(request: AnalyzePhotosAlbumRequest):
    """Analyze every supported asset in one Photos album, in place.

    Mirrors POST /analyze-folder: same dedup on file_path, same _progress slot,
    same daemon thread, same per-photo commit — so the existing progress poll
    and grid need no special case. The differences are that candidates come
    from album membership rather than a directory walk, and that the rows are
    stamped source_type='photos' so their decisions become keywords instead of
    file moves.
    """
    _require_photos()
    _require_open_library(request.library_path)

    if _progress.get("running"):
        raise HTTPException(status_code=409, detail="Analysis already running")

    try:
        assets, skipped_counts = photos_library.album_assets(
            request.library_path, request.album_uuid
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except SmartAlbumUnavailable as exc:
        raise _smart_album_unreachable(exc)
    except PhotosLibraryMissing:
        raise _missing_library(request.library_path)
    except PhotosAccessError as exc:
        logger.warning("Photos library not readable: %s", exc)
        raise HTTPException(status_code=403, detail=_ACCESS_HELP)

    source_folder = photos_library.photos_source_folder(
        request.library_path, request.album_uuid
    )
    album_name = photos_library.album_name(
        request.library_path, request.album_uuid
    ) or "Photos album"

    # Remember the album's identity before anything else: sink preflight and
    # the library-mismatch banner both read this row, and a tab must never
    # exist without one.
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO photos_albums
                (source_folder, library_path, album_uuid, album_name, album_path, last_synced_at)
            VALUES (?, ?, ?, ?, ?, DATETIME('now'))
            ON CONFLICT(source_folder) DO UPDATE SET
                library_path   = excluded.library_path,
                album_name     = excluded.album_name,
                album_path     = excluded.album_path,
                last_synced_at = excluded.last_synced_at
            """,
            (source_folder, request.library_path, request.album_uuid,
             album_name, album_name),
        )

    common = {
        "status": "done",
        "source_folder": source_folder,
        "album_name": album_name,
        "missing_originals": skipped_counts["missing_originals"],
        "skipped_videos": skipped_counts["movies"],
        "skipped_unsupported": skipped_counts["unsupported"],
    }

    if not assets:
        _progress.update({"analyzed_count": 0, "error_count": 0})
        return {**common, "analyzed": 0, "skipped": [], "errors": [], "total_found": 0}

    with get_db() as conn:
        done_paths = {
            r[0] for r in conn.execute(
                "SELECT file_path FROM images WHERE analysis_status = 'done'"
            ).fetchall()
        }

    to_analyze = [Path(a.path) for a in assets if a.path not in done_paths]
    skipped = [a.original_filename for a in assets if a.path in done_paths]
    filename_overrides = {a.path: a.original_filename for a in assets}
    photos_uuid_overrides = {a.path: a.uuid for a in assets}

    _stop_event.clear()
    _progress.update({
        "running": True,
        "total": len(to_analyze),
        "done": 0,
        "current_file": None,
        "started_at": time.monotonic(),
        "stop_requested": False,
        "source_folder": source_folder,
        "analyzed_count": 0,
        "error_count": 0,
        "elapsed_seconds": None,
    })

    paused_watch_path = watcher.watching
    if paused_watch_path:
        watcher.stop()

    threading.Thread(
        target=_run_batch,
        args=(None, to_analyze, skipped, len(assets), paused_watch_path, False,
              source_folder, filename_overrides, photos_uuid_overrides),
        daemon=True,
        name="batch-analysis",
    ).start()

    return {
        **common,
        "status": "started",
        "total": len(to_analyze),
        "skipped": skipped,
        "total_found": len(assets),
    }


# ---------------------------------------------------------------------------
# Reconcile — KaMeRa ↔ Photos drift
# ---------------------------------------------------------------------------


class ReconcileRequest(BaseModel):
    source_folder: str
    # Class name → action. Omitted classes fall back to DEFAULT_ACTIONS, which
    # leaves everything alone except keyword_missing. Validated strictly: this
    # endpoint deletes rows, so an unrecognised key is an error, not a no-op.
    actions: dict[str, str] = {}


def _photos_tab(source_folder: str) -> dict:
    """The photos_albums row for a tab, or 404."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM photos_albums WHERE source_folder = ?",
            (source_folder,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Not a known Photos album tab")
    return dict(row)


def _tab_rows(source_folder: str) -> list[dict]:
    """This tab's images joined to their decision, in a stable order."""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT i.id, i.filename, i.photos_uuid, i.source_folder,
                   i.file_path, i.source_type, d.decision
            FROM images i
            LEFT JOIN decisions d ON d.image_id = i.id
            WHERE i.source_folder = ?
            ORDER BY i.filename
            """,
            (source_folder,),
        ).fetchall()
    return [dict(r) for r in rows]


def _build_diff(source_folder: str):
    """Shared by the diff endpoint and reconcile, so both see one truth.

    Reconcile deliberately re-reads rather than trusting what the client was
    shown: the user may have changed something in Photos while the diff sat on
    screen, and acting on a stale picture is how a reconcile would destroy the
    very edit it was meant to adopt.
    """
    from backend import photos_reconcile

    _require_photos()
    tab = _photos_tab(source_folder)
    _require_open_library(tab["library_path"])

    try:
        in_album, library_uuids = photos_library.album_keyword_state(
            tab["library_path"], tab["album_uuid"]
        )
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Album “{tab['album_name']}” no longer exists in this library",
        )
    except SmartAlbumUnavailable as exc:
        raise _smart_album_unreachable(exc)
    except PhotosLibraryMissing:
        raise _missing_library(tab["library_path"])
    except PhotosAccessError:
        raise HTTPException(status_code=403, detail=_ACCESS_HELP)

    rows = _tab_rows(source_folder)
    diff = photos_reconcile.classify(rows, in_album, library_uuids)
    return tab, rows, diff


@router.get("/photos-album/diff")
def photos_album_diff(source_folder: str):
    """What disagrees between this tab and the Photos library, per photo.

    Read-only. Re-parses the library catalogue so the answer reflects keyword
    edits made in Photos since KaMeRa last looked — a cached read would report
    drift that is already gone and miss the drift that matters.
    """
    from backend import photos_reconcile

    tab, _rows, diff = _build_diff(source_folder)
    return {
        "source_folder": source_folder,
        "album_name": tab["album_name"],
        "library_path": tab["library_path"],
        "library_name": Path(tab["library_path"]).stem,
        "actions": photos_reconcile.ACTIONS,
        "default_actions": photos_reconcile.DEFAULT_ACTIONS,
        **diff.as_dict(),
    }


@router.post("/photos-album/reconcile")
def photos_album_reconcile(request: ReconcileRequest):
    """Apply a chosen direction per mismatch class.

    Every write goes through the same paths a normal decision uses — the sink
    for keywords, `_apply_decision` for decisions, `_delete_image_rows` for
    removals — so reconcile cannot develop its own idea of what a decision is.

    Failures are per-photo and never abort the run: a keyword write that 409s
    because the library changed under us leaves that one row untouched and
    reported, rather than half-applying the batch and claiming success.
    """
    from backend import photos_reconcile
    from backend.routers.decisions import _apply_decision
    from backend.routers.images import _delete_image_rows
    from backend.sources import sink_for

    try:
        actions = photos_reconcile.validate_actions(request.actions)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    tab, rows, diff = _build_diff(request.source_folder)
    by_id = {r["id"]: r for r in rows}
    sink = sink_for({"source_type": "photos"})

    applied: dict[str, int] = {name: 0 for name in photos_reconcile.CLASSES}
    errors: list[dict] = []
    drop_ids: list[int] = []

    def _fail(entry: dict, exc: Exception) -> None:
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        errors.append({
            "image_id": entry.get("image_id"),
            "filename": entry.get("filename"),
            "error": detail,
        })
        logger.warning("reconcile: %s failed: %s", entry.get("filename"), detail)

    for name in photos_reconcile.CLASSES:
        action = actions[name]
        if action == "skip":
            continue

        for entry in diff.classes[name]:
            image_id = entry.get("image_id")
            row = by_id.get(image_id)
            try:
                if row is None and action != "drop":
                    # The row vanished between the diff and now (a concurrent
                    # Clear Analysis, another tab). Say so rather than failing
                    # deeper with a confusing NoneType error.
                    raise ValueError("row no longer exists — re-run the diff")

                if entry.get("ambiguous") and action == "photos":
                    # Two KaMeRa keywords on one asset: there is no "what
                    # Photos says". Picking the first would be a coin flip
                    # recorded as a decision, so this one goes back to the user.
                    raise ValueError(
                        "carries more than one KaMeRa keyword — remove one in "
                        "Photos, then reconcile again"
                    )

                if action == "drop":
                    # Deferred: one batched delete after the loop, so a
                    # partial failure elsewhere cannot leave rows half-removed.
                    drop_ids.append(image_id)

                elif action == "apply":
                    # KaMeRa is right, the write simply never landed.
                    sink.apply(row, entry["decision"], is_auto=False)

                elif action == "kamera":
                    # Conflict resolved toward KaMeRa: overwrite the marker.
                    sink.apply(row, entry["decision"], is_auto=False)

                elif action in ("photos", "import"):
                    # Conflict resolved toward Photos, or a decision made
                    # there. _apply_decision re-writes the same keyword
                    # (idempotent) and records it as a manual decision — which
                    # it is: a human set that keyword by hand.
                    decision = photos_reconcile.KEYWORD_DECISIONS.get(entry.get("keyword"))
                    if decision is None:
                        raise ValueError(f"Unreadable keyword {entry.get('keyword')!r}")
                    _apply_decision(image_id, decision, reason="manual")

                elif action == "clear":
                    if name == "keyword_missing":
                        # Drop OUR decision — Photos is treated as the record.
                        with get_db() as conn:
                            conn.execute(
                                "DELETE FROM decisions WHERE image_id = ?", (image_id,)
                            )
                    else:
                        # Drop THEIR marker — KaMeRa has no decision to back it.
                        sink.undo(row, None)

                applied[name] += 1
            except Exception as exc:  # noqa: BLE001 — reported, never fatal
                _fail(entry, exc)

    dropped = 0
    if drop_ids:
        try:
            _delete_image_rows(drop_ids)
            dropped = len(drop_ids)
        except Exception as exc:  # noqa: BLE001
            # Report honestly instead of letting the successful keyword writes
            # above disappear behind a 500.
            errors.append({"image_id": None, "filename": None,
                           "error": f"could not drop {len(drop_ids)} row(s): {exc}"})
            for name in ("not_in_album", "asset_gone"):
                applied[name] = 0

    return {
        "applied": applied,
        "dropped_rows": dropped,
        "errors": errors,
        "actions": actions,
    }
