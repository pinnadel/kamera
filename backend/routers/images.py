"""
Image listing and folder management endpoints.
"""

import logging
import platform
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.constants import (
    SUPPORTED_EXTENSIONS, DECISION_SUBFOLDERS, is_standalone_hif,
    is_raw_format, companion_stem_matches, has_processed_sibling,
    is_photos_source_folder,
)
from backend.database import get_db
from backend.routers.settings import get_raw_jpeg_display_mode
from backend.state import (
    PREVIEW_CACHE_DIR, _decision_move_lock, _progress,
    clear_all_caches, invalidate_all, model_for,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _filter_raw_jpeg_pairs(rows: list[dict]) -> list[dict]:
    """
    Hide one member of each detected RAW+JPEG (or RAW+standalone-HIF) pair
    per the raw_jpeg_display_mode setting. Mode 'both' is a no-op (today's
    behaviour). A "pair" is two rows in the same source_folder whose
    filenames share a stem and whose formats are on opposite sides of the
    RAW/processed split.

    Orphans (a RAW or JPEG with no same-stem sibling in this result set) are
    always kept regardless of mode — the filter only ever hides one half of
    an actual detected pair, never a photo the user has no other copy of.

    A row with analysis_status='skipped' (analysis_mode=jpeg_priority — see
    settings.py) is ALWAYS hidden here, unconditionally on raw_jpeg_display_mode.
    It exists only so K/M/R decisions can mirror onto it and move it with its
    processed sibling; it has no scores and was never meant to render as a
    tile.
    """
    rows = [r for r in rows if r.get("analysis_status") != "skipped"]

    mode = get_raw_jpeg_display_mode()
    if mode == "both":
        return rows

    # Group candidate rows by (source_folder, stem) to find pairs cheaply.
    #
    # Photos rows are never grouped, so they can never land in hide_ids and are
    # always returned. Pairing is a folder concept — two files sharing a stem in
    # one directory. Hiding one half of a "pair" inside an album would also
    # leave it unmirrored (the decision mirror is gated on the same capability),
    # i.e. permanently undecided and invisible.
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        if row.get("source_type") == "photos":
            continue
        key = (row.get("source_folder"), Path(row["filename"]).stem.lower())
        groups.setdefault(key, []).append(row)

    hide_ids: set[int] = set()
    for members in groups.values():
        if len(members) < 2:
            continue  # orphan — no companion in this result set, always shown
        raw_members = [r for r in members if is_raw_format(r.get("format"))]
        processed_members = [r for r in members if not is_raw_format(r.get("format"))]
        if not raw_members or not processed_members:
            continue  # same-stem rows that aren't actually a RAW/processed pair
        losers = processed_members if mode == "raw" else raw_members
        hide_ids.update(r["id"] for r in losers)

    return [r for r in rows if r["id"] not in hide_ids]


def _delete_image_rows(ids: list[int]) -> int:
    """
    Cascade-delete the given image IDs: decisions, burst membership, bursts
    whose hero is removed, then the images rows themselves. Also invalidates
    the personal-score cache and removes preview JPEGs. Returns the count of
    preview files removed.
    """
    if not ids:
        return 0
    with get_db() as conn:
        placeholders = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM decisions      WHERE image_id IN ({placeholders})", ids)
        conn.execute(f"DELETE FROM burst_members  WHERE image_id IN ({placeholders})", ids)
        conn.execute(f"DELETE FROM bursts         WHERE hero_image_id IN ({placeholders})", ids)
        conn.execute(f"DELETE FROM images         WHERE id IN ({placeholders})", ids)

    invalidate_all(ids)

    previews_removed = 0
    if PREVIEW_CACHE_DIR.exists():
        for image_id in ids:
            preview_file = PREVIEW_CACHE_DIR / f"{image_id}.jpg"
            try:
                preview_file.unlink()
                previews_removed += 1
            except FileNotFoundError:
                pass
            except OSError:
                pass
    return previews_removed


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ClearFolderRequest(BaseModel):
    source_folder: str


class RevealRequest(BaseModel):
    image_id: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/images")
def list_images(source_folder: str | None = None):
    """
    Return images with their analysis scores.

    Ordered by imported_at descending (newest first).
    Images still pending analysis will have null scores.

    If source_folder is provided, only images whose source_folder matches
    are returned — used by the multi-tab UI to scope the grid to one tab.
    """
    base_sql = """
        SELECT
            i.id, i.file_path, i.filename, i.camera, i.format,
            i.shot_at, i.preview_path, i.source_folder, i.source_type,
            i.focal_length_mm, i.aperture, i.shutter_speed, i.iso,
            i.sharpness_score, i.exposure_score, i.overall_score,
            i.highlight_clip_pct, i.shadow_clip_pct,
            i.shake_detected, i.analysis_status, i.imported_at,
            i.face_detected, i.face_count, i.face_sharpness_score,
            i.eyes_open, i.eye_openness_ratio, i.face_size_ratio,
            i.face_center_offset_x, i.face_center_offset_y,
            i.smile_score, i.mouth_open_score,
            i.scene, i.scene_confidence,
            i.subject_prominence_score, i.background_distraction_score,
            i.eye_contact_score, i.decisive_moment_score,
            i.iqa_score, i.aesthetic_score,
            i.manual_group_id,
            i.explanation,
            d.decision, d.reason
        FROM images i
        LEFT JOIN decisions d ON d.image_id = i.id
    """
    with get_db() as conn:
        if source_folder is not None:
            rows = conn.execute(
                base_sql + " WHERE i.source_folder = ? ORDER BY i.imported_at DESC",
                (source_folder,),
            ).fetchall()
        else:
            rows = conn.execute(base_sql + " ORDER BY i.imported_at DESC").fetchall()

    rows_dicts = [dict(row) for row in rows]
    rows_dicts = _filter_raw_jpeg_pairs(rows_dicts)

    # Annotate with personal_score, each source_type scored by the model
    # trained on its own corpus. Running the camera model over iPhone HEICs
    # would emit a confident number from a model that has never seen that
    # distribution, so rows whose corpus has no trained model stay None.
    # predict_batch() calls pipeline.predict() once per corpus — still fast.
    rows_by_corpus: dict[str, list[int]] = {}
    for idx, img in enumerate(rows_dicts):
        img["personal_score"] = None
        rows_by_corpus.setdefault(img.get("source_type") or "folder", []).append(idx)

    for corpus, indices in rows_by_corpus.items():
        model = model_for(corpus)
        if not model.ready:
            continue
        scores = model.predict_batch([rows_dicts[i] for i in indices])
        for idx, ps in zip(indices, scores):
            rows_dicts[idx]["personal_score"] = ps

    return rows_dicts


@router.post("/reveal-in-finder")
def reveal_in_finder(request: RevealRequest):
    """Open the OS file browser with the photo selected.

    macOS: `open -R <path>` (Finder).
    Windows: `explorer /select,<path>`.
    Linux: best-effort xdg-open on the parent dir (selection isn't standard).
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT file_path FROM images WHERE id = ?", (request.image_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"image_id {request.image_id} not found")
    path = Path(row["file_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"File missing on disk: {path}")

    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["open", "-R", str(path)])
        elif system == "Windows":
            subprocess.Popen(["explorer", f"/select,{path}"])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except Exception as exc:
        logger.exception("reveal-in-finder failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Reveal failed: {exc}")
    return {"status": "ok"}


@router.get("/folders")
def list_folders():
    """
    Return distinct source folders that currently have analyzed images.

    Used by the multi-tab UI on launch to rebuild one tab per folder.

    `source_type` tells the UI which kind of tab to rebuild. `label` and
    `library_path` are only populated for Photos albums, whose source_folder is
    an opaque photos:// URI — without the label the tab bar would show a raw
    album UUID, since it otherwise renders the last path segment.
    """
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                i.source_folder,
                COUNT(*)              AS image_count,
                MAX(i.imported_at)    AS last_imported_at,
                MAX(i.source_type)    AS source_type,
                a.album_name          AS label,
                a.library_path        AS library_path
            FROM images i
            LEFT JOIN photos_albums a ON a.source_folder = i.source_folder
            WHERE i.source_folder IS NOT NULL
            GROUP BY i.source_folder
            ORDER BY MAX(i.imported_at) DESC
            """
        ).fetchall()
    return {"folders": [dict(r) for r in rows]}


@router.get("/folders/unfinished")
def folder_unfinished(folder_path: str, include_subfolders: bool = False):
    """
    Return the count of supported files in `folder_path` that have NOT been
    analyzed yet — i.e. files on disk whose path doesn't have an
    `images` row with analysis_status='done'.

    Used by the UI to surface a "Resume analysis" banner when a prior batch
    was interrupted (laptop lid closed, app crash, force-quit). Cheap:
    one directory walk + one indexed SQL query against an in-memory set.

    Returns:
      {
        "folder_path":    "<absolute path>",
        "total_on_disk":  int   # supported files found
        "done_count":     int   # files already analysis_status='done'
        "unfinished":     int   # total_on_disk - done_count
      }

    Skips conventional decision subfolders (_Keeps / _Maybes / _Rejects) to
    match analyze_folder's walk semantics — otherwise a freshly-moved photo
    would count as "unfinished" forever.
    """
    # An album has no directory to walk, so the denominator comes from the
    # asset_count the last analyze run recorded. Re-enumerating here instead
    # would cost an osxphotos read on every tab focus — and for a Smart Album
    # an AppleScript round-trip that fails outright unless Photos.app is open.
    # Staleness is bounded and matches what the banner claims: it reports on
    # the run that was interrupted, not on the album's live membership. Drift
    # against Photos is what /photos-album/diff is for.
    if is_photos_source_folder(folder_path):
        with get_db() as conn:
            album = conn.execute(
                "SELECT asset_count FROM photos_albums WHERE source_folder = ?",
                (folder_path,),
            ).fetchone()
            done_count = conn.execute(
                "SELECT COUNT(*) FROM images "
                "WHERE source_folder = ? AND analysis_status = 'done'",
                (folder_path,),
            ).fetchone()[0]
        total = (album["asset_count"] if album else None) or 0
        return {
            "folder_path":   folder_path,
            "total_on_disk": total,
            "done_count":    done_count,
            # An album that grew since the last run would go negative.
            "unfinished":    max(0, total - done_count),
        }

    folder = Path(folder_path)
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=404, detail=f"Folder not found: {folder_path}")

    def _is_analyzable(f: Path) -> bool:
        if f.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return False
        if f.suffix.lower() == ".hif" and not is_standalone_hif(f):
            return False  # RAW+HIF sidecar — moves with its RAF, never analyzed alone
        return True

    if include_subfolders:
        skip_dirs = DECISION_SUBFOLDERS
        candidates = [
            f for f in folder.rglob("*")
            if f.is_file()
            and _is_analyzable(f)
            and not any(part in skip_dirs for part in f.relative_to(folder).parts)
        ]
    else:
        candidates = [
            f for f in folder.iterdir()
            if f.is_file() and _is_analyzable(f)
        ]

    total_on_disk = len(candidates)
    if total_on_disk == 0:
        return {
            "folder_path": str(folder),
            "total_on_disk": 0,
            "done_count": 0,
            "unfinished": 0,
        }

    with get_db() as conn:
        done_paths = {
            row[0] for row in conn.execute(
                "SELECT file_path FROM images WHERE analysis_status = 'done' AND source_folder = ?",
                (str(folder),),
            ).fetchall()
        }
        skipped_raw_paths = {
            row[0] for row in conn.execute(
                "SELECT file_path FROM images WHERE analysis_status = 'skipped' AND source_folder = ?",
                (str(folder),),
            ).fetchall()
        }

    # A 'skipped' RAW (jpeg_priority analysis mode) still counts as "finished"
    # here as long as its processed sibling is still on disk — otherwise the
    # Resume-analysis banner would nag forever about a RAW that was
    # intentionally left unscored. See analyze_folder's identical guard.
    still_skippable = {p for p in skipped_raw_paths if has_processed_sibling(Path(p))}
    resolved_paths = done_paths | still_skippable

    done_count = sum(1 for f in candidates if str(f) in resolved_paths)
    return {
        "folder_path": str(folder),
        "total_on_disk": total_on_disk,
        "done_count": done_count,
        "unfinished": total_on_disk - done_count,
    }


@router.post("/sync-folder")
def sync_folder(request: ClearFolderRequest):
    """
    Drop rows whose file_path no longer exists on disk for one source_folder.

    Used by batch tabs on focus to keep the grid in sync with the file system
    when the user deletes files outside the app.

    Refuses while a batch analysis is in flight on the same folder.
    """
    if _progress.get("running") and _progress.get("source_folder") == request.source_folder:
        raise HTTPException(
            status_code=409,
            detail="Analysis is running for this folder — wait or stop it first.",
        )

    # "The file is gone, so drop the row" is wrong in BOTH directions for a
    # Photos album. Removing an asset from the album leaves the original file
    # sitting in the library (stale row survives), and an iCloud offload of a
    # still-perfectly-fine photo makes its original vanish from disk (row and
    # decision deleted for no reason). Album membership, not file existence,
    # is the right predicate there — deferred, so for now simply never reap.
    if is_photos_source_folder(request.source_folder):
        return {"status": "skipped", "images_removed": 0, "previews_removed": 0}

    # Read + existence-check under _decision_move_lock so this can't observe a
    # photo mid-move (file already relocated on disk, file_path not updated
    # yet) and wrongly conclude the row is orphaned. See the incident note on
    # _decision_move_lock in backend/state.py.
    with _decision_move_lock:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, file_path FROM images WHERE source_folder = ?",
                (request.source_folder,),
            ).fetchall()

        missing_ids = [r["id"] for r in rows if not Path(r["file_path"]).exists()]
        previews_removed = _delete_image_rows(missing_ids)

    return {
        "status": "synced",
        "images_removed": len(missing_ids),
        "previews_removed": previews_removed,
    }


@router.post("/clear-folder")
def clear_folder(request: ClearFolderRequest):
    """
    Wipe analysis rows + cached previews for a single source_folder.

    Used when the user closes a tab or re-analyzes an already-open folder.
    Refuses to run while a batch analysis targeting this same folder is in flight.
    """
    if _progress.get("running") and _progress.get("source_folder") == request.source_folder:
        raise HTTPException(
            status_code=409,
            detail="Analysis is running for this folder — stop it before clearing.",
        )

    with get_db() as conn:
        ids = [row[0] for row in conn.execute(
            "SELECT id FROM images WHERE source_folder = ?", (request.source_folder,),
        ).fetchall()]

    previews_removed = _delete_image_rows(ids)

    return {
        "status": "cleared",
        "images_removed": len(ids),
        "previews_removed": previews_removed,
    }


@router.post("/clear")
def clear_analysis():
    """
    Wipe all analysis state so the next run starts from a clean slate.

    Removes: every row in images / decisions / bursts / burst_members, plus any
    cached preview JPEGs on disk. RAW files on disk are never touched.

    Refuses to run while a batch analysis is in flight.
    """
    from backend.state import watcher as _watcher

    if _progress.get("running"):
        raise HTTPException(status_code=409, detail="Analysis is running — stop it before clearing.")

    # Stop the folder watcher too: otherwise it could race-create new rows
    # between the DELETE and the caller's next action.
    if _watcher.watching:
        _watcher.stop()

    with get_db() as conn:
        images_before = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
        conn.execute("DELETE FROM decisions")
        conn.execute("DELETE FROM burst_members")
        conn.execute("DELETE FROM bursts")
        conn.execute("DELETE FROM images")
        conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('images','bursts','decisions')")

    # Every cached personal_score now refers to a deleted row.
    clear_all_caches()

    # Wipe preview cache so stale thumbnails can't outlive their DB row.
    previews_removed = 0
    if PREVIEW_CACHE_DIR.exists():
        for f in PREVIEW_CACHE_DIR.glob("*.jpg"):
            try:
                f.unlink()
                previews_removed += 1
            except OSError:
                pass

    return {
        "status": "cleared",
        "images_removed": images_before,
        "previews_removed": previews_removed,
    }
