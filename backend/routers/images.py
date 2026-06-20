"""
Image listing and folder management endpoints.
"""

import logging
import platform
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.constants import SUPPORTED_EXTENSIONS, DECISION_SUBFOLDERS
from backend.database import get_db
from backend.state import PREVIEW_CACHE_DIR, _personal_model, _progress

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


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

    _personal_model.invalidate_many(ids)

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
            i.shot_at, i.preview_path, i.source_folder,
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
            d.decision
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

    # Annotate with personal_score if the model has been trained.
    # predict_batch() calls pipeline.predict() once for all N rows — fast.
    if _personal_model.ready:
        personal_scores = _personal_model.predict_batch(rows_dicts)
        for img, ps in zip(rows_dicts, personal_scores):
            img["personal_score"] = ps
    else:
        for img in rows_dicts:
            img["personal_score"] = None

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
    """
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                source_folder,
                COUNT(*)            AS image_count,
                MAX(imported_at)    AS last_imported_at
            FROM images
            WHERE source_folder IS NOT NULL
            GROUP BY source_folder
            ORDER BY MAX(imported_at) DESC
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

    Skips conventional decision subfolders (_Keeps / _Maybes / _Trash) to
    match analyze_folder's walk semantics — otherwise a freshly-moved photo
    would count as "unfinished" forever.
    """
    folder = Path(folder_path)
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=404, detail=f"Folder not found: {folder_path}")

    if include_subfolders:
        skip_dirs = DECISION_SUBFOLDERS
        candidates = [
            f for f in folder.rglob("*")
            if f.is_file()
            and f.suffix.lower() in SUPPORTED_EXTENSIONS
            and not any(part in skip_dirs for part in f.relative_to(folder).parts)
        ]
    else:
        candidates = [
            f for f in folder.iterdir()
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
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

    done_count = sum(1 for f in candidates if str(f) in done_paths)
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
    _personal_model.clear_cache()

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
