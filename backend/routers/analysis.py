"""
Analysis endpoints: single-photo analysis, folder batch analysis,
progress polling, stop, and preview/histogram/clipping-mask serving.
"""

import io
import json
import logging
import threading
import time
import uuid as _uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as _np
import rawpy
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response
from PIL import Image as PilImage
from pydantic import BaseModel

from phase1_technical.exposure import compute_histogram as _compute_histogram

from backend.database import get_db, get_setting, write_shooting_log
from backend.state import (
    PREVIEW_CACHE_DIR,
    _personal_model,
    _progress,
    _record_error,
    _stop_event,
    watcher,
)
from phase1_technical.exif_parser import extract_exif
from phase1_technical.quality_analyzer import StopRequested, analyze_photo_quality
from phase2_quality.similarity_scorer import embedding_to_json as _embedding_to_json
from phase2_quality.face_identity import face_embedding_to_json as _face_embedding_to_json

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Re-exported from backend.constants so callers that already import these
# names from analysis.py keep working. Cheap importers (file_watcher, the
# /folders/unfinished endpoint, burst_ranker's RAW fallback) should import
# directly from backend.constants to avoid pulling in this module's heavy
# transitive deps (rawpy, pyiqa, torch).
from backend.constants import SUPPORTED_EXTENSIONS, RAW_FORMATS
# .hif is intentionally excluded from SUPPORTED_EXTENSIONS — Fuji writes it
# next to every .RAF in RAW+HIF mode. We treat HIF as a sidecar of the RAF
# (move-only, never analyzed on its own).

# Sidecar preview extensions — companions written next to a RAW that hold the
# camera's baked rendition of the same shot. Order matters: HIF is checked
# before JPG because Fuji writes both a .HIF and a small .JPG in some modes,
# and we prefer the larger HEIF.
_SIDECAR_PREVIEW_EXTS: tuple[str, ...] = (".HIF", ".hif", ".JPG", ".jpg", ".JPEG", ".jpeg")

PREVIEW_SIZE = (1600, 1600)   # max px on either side, aspect ratio preserved
PREVIEW_QUALITY = 88          # JPEG quality for cached previews

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class AnalyzeRequest(BaseModel):
    """Body expected by POST /analyze."""
    file_path: str  # absolute path to a RAW or JPEG photo


class SharpnessDetail(BaseModel):
    sharpness_score: float
    sharpness_label: str        # 'Sharp' | 'Borderline' | 'Blurry'
    laplacian_variance: float   # raw OpenCV value before normalization


class ExposureDetail(BaseModel):
    exposure_score: float
    mean_brightness: float
    std_brightness: float
    highlight_clip_pct: float
    shadow_clip_pct: float
    exposure_warning: str       # human-readable warning, e.g. 'Overexposed'
    is_likely_intentional: bool # True when low-key/high-key looks deliberate


class FaceDetail(BaseModel):
    face_detected: bool
    face_count: int
    face_sharpness_score: float | None
    eyes_open: bool | None
    eye_openness_ratio: float | None
    face_size_ratio: float | None
    face_center_offset_x: float | None
    face_center_offset_y: float | None
    smile_score: float | None = None
    mouth_open_score: float | None = None


class AnalyzeResponse(BaseModel):
    """Body returned by POST /analyze."""
    image_id: int
    file_path: str
    overall_quality_score: float
    sharpness: SharpnessDetail
    exposure: ExposureDetail
    face: FaceDetail
    explanation: str | None = None


class AnalyzeFolderRequest(BaseModel):
    folder_path: str
    # When True, walk the folder tree recursively. Default False keeps the
    # original single-folder behaviour. Decisions on photos in subfolders
    # land in `<photo_parent>/_Keeps/` etc. — mirroring the subfolder shape.
    include_subfolders: bool = False


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _find_sidecar_preview(raw_path: Path) -> Path | None:
    """
    Return the path of a camera-baked sidecar (.HIF/.JPG) sitting next to a
    RAW file, or None. Match is case-insensitive on extension; the first
    extension in `_SIDECAR_PREVIEW_EXTS` that exists wins.
    """
    for ext in _SIDECAR_PREVIEW_EXTS:
        candidate = raw_path.with_suffix(ext)
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


# Module-level cache for the sidecar-preview preference. Without this,
# every preview generation opens a fresh DB connection just to read this
# one bool. Settings router invalidates by calling _reset_sidecar_pref_cache().
_sidecar_pref_cache: bool | None = None


def _is_sidecar_preview_preferred() -> bool:
    """Read the current value of the prefer_sidecar_preview toggle.

    Caches the answer at module level. The settings router calls
    _reset_sidecar_pref_cache() on every POST /settings so a toggle flip
    takes effect immediately for new previews. The previous stateless
    implementation re-opened SQLite for every preview generation —
    ~5–15ms × 741 RAWs = ~5–10s wasted on first analysis.
    """
    global _sidecar_pref_cache
    if _sidecar_pref_cache is not None:
        return _sidecar_pref_cache
    raw = get_setting("prefer_sidecar_preview")
    val = raw is not None and raw.strip().lower() in ("1", "true", "yes", "on")
    _sidecar_pref_cache = val
    return val


def _reset_sidecar_pref_cache() -> None:
    """Clear the cached sidecar-preview pref so the next read re-checks SQLite.
    Called from the settings router after any POST /settings."""
    global _sidecar_pref_cache
    _sidecar_pref_cache = None


def _raw_to_pil(file_path: Path) -> PilImage.Image:
    """
    Convert a RAW file to a PIL Image, with EXIF orientation applied.

    Tries full demosaic at half resolution first (accurate colors, real pixel
    data). Falls back to the embedded JPEG thumbnail via a fresh file open if
    the format isn't supported for demosaicing (e.g. Nikon's newer NEFs).

    rawpy.postprocess() never auto-rotates, so we read raw.flip and rotate
    manually (0=none, 3=180°, 5=90°CCW, 6=90°CW).  The extract_thumb fallback
    returns a real JPEG whose EXIF Orientation tag is applied via exif_transpose.
    """
    from PIL import ImageOps
    # LibRaw flip codes: 0=none, 3=180°, 5=90°CCW, 6=90°CW
    _FLIP_TO_DEGREES = {3: 180, 5: 90, 6: 270}
    try:
        with rawpy.imread(str(file_path)) as raw:
            flip = getattr(raw, 'flip', 0)
            rgb = raw.postprocess(use_camera_wb=True, half_size=True, output_bps=8)
        img = PilImage.fromarray(rgb)
        if flip in _FLIP_TO_DEGREES:
            img = img.rotate(_FLIP_TO_DEGREES[flip], expand=True)
        return img
    except Exception:
        with rawpy.imread(str(file_path)) as raw:
            thumb = raw.extract_thumb()
        return ImageOps.exif_transpose(PilImage.open(io.BytesIO(thumb.data)))


def _generate_preview(file_path: Path, fmt: str) -> bytes:
    """
    Render a high-quality JPEG preview from a photo file.

    RAW: demosaiced at half resolution (or embedded thumbnail as fallback).
    JPEG/PNG: opened directly with Pillow, EXIF orientation applied.

    If the user has enabled "Prefer camera JPEG/HIF preview" and the RAW has
    a sibling .HIF/.JPG, the sidecar is rendered instead.
    """
    from PIL import ImageOps

    img = None
    if fmt in RAW_FORMATS and _is_sidecar_preview_preferred():
        sidecar = _find_sidecar_preview(file_path)
        if sidecar is not None:
            try:
                img = PilImage.open(str(sidecar))
                img = ImageOps.exif_transpose(img)
            except Exception:
                logger.exception("Sidecar preview failed for %s — falling back to RAW", sidecar)
                img = None

    if img is None:
        if fmt in RAW_FORMATS:
            img = _raw_to_pil(file_path)
        else:
            img = PilImage.open(str(file_path))
            img = ImageOps.exif_transpose(img)

    img.thumbnail(PREVIEW_SIZE, PilImage.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=PREVIEW_QUALITY)
    return buf.getvalue()


def _load_preview_pil(image_id: int) -> PilImage.Image:
    """
    Resolve image_id → an RGB PIL image of the displayed preview.

    Reaches for the on-disk cached preview first (instant), falls back to
    generating one for RAW formats, or thumbnailing the source for JPEG/PNG.
    Used by both the histogram endpoint and the clipping-mask endpoint so the
    pixels they analyze are exactly the pixels DetailView shows.

    Raises HTTPException(404) if the image_id is unknown or the source file
    is missing from disk.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT file_path, format, preview_path FROM images WHERE id = ?",
            (image_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Image not found")

    file_path = Path(row["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File missing from disk: {file_path}")

    cached_path = Path(row["preview_path"]) if row["preview_path"] else None
    if cached_path and cached_path.exists():
        return PilImage.open(str(cached_path)).convert("RGB")

    fmt = (row["format"] or "").upper()
    if fmt in RAW_FORMATS:
        jpeg_bytes = _generate_preview(file_path, fmt)
        return PilImage.open(io.BytesIO(jpeg_bytes)).convert("RGB")

    from PIL import ImageOps
    img = PilImage.open(str(file_path))
    img = ImageOps.exif_transpose(img)
    img.thumbnail(PREVIEW_SIZE, PilImage.LANCZOS)
    return img.convert("RGB")


def _warm_models() -> None:
    """Load the 3 PyTorch ML models into RAM in ONE background thread, sequentially.

    Models are module-level singletons — once loaded they stay in RAM until
    the server stops. Calling this at startup means analysis requests never
    pay the disk→RAM load cost (~5s for the pyiqa models combined).

    Sequential, not parallel: torch 2.11 + transformers 4.57 use accelerate's
    meta-device weight initialisation, and concurrent model.to(device) calls
    race on shared accelerate global state. The first model that loses the
    race fails with `Cannot copy out of meta tensor`, and once one fails the
    broken init path corrupts subsequent loads in the same process. Live-batch
    evidence: previous parallel implementation made aesthetic_scorer fail
    every session and TOPIQ retry-fail 200+ times per batch (logged 2026-05-05).
    Sequential load adds ~5s startup overhead but loads ALL three reliably.
    """
    from phase2_quality.aesthetic_scorer import _get_metric as _get_aes
    from phase2_quality.iqa_scorer import _get_metric
    from phase2_quality.similarity_scorer import _get_siglip

    def _warm_sequentially():
        # TOPIQ-NR first → fastest, lets any quick smoke-test see something ready
        # early. Each loader catches its own exceptions and sets _load_failed;
        # we wrap every call so one failure doesn't abort the others.
        for name, target in (("TOPIQ-NR", _get_metric), ("TOPIQ-IAA", _get_aes), ("SigLIP", _get_siglip)):
            try:
                target()
            except Exception:
                logger.exception("Warm-load failed for %s — falling back to lazy load", name)

    threading.Thread(target=_warm_sequentially, daemon=True, name="warm-models").start()


def _backfill_histograms() -> None:
    """Background thread: compute histogram_json for any images that don't have
    it yet (photos analyzed before histogram_json was added to the schema).

    Runs at ~50ms/photo from the cached preview JPEG so 634 backlog rows finish
    in ~30s without blocking the API. Rate-limited to one photo every 100ms so
    it doesn't compete with an active analysis batch. Safe to call multiple
    times — skips rows that already have histogram_json.
    """
    def _run():
        try:
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT id FROM images WHERE histogram_json IS NULL AND preview_path IS NOT NULL"
                ).fetchall()
            if not rows:
                return
            logger.info("Histogram backfill: %d images to process", len(rows))
            done = 0
            for row in rows:
                try:
                    img = _load_preview_pil(row["id"])
                    result = _compute_histogram(_np.asarray(img))
                    with get_db() as conn:
                        conn.execute(
                            "UPDATE images SET histogram_json = ? WHERE id = ?",
                            (json.dumps(result, separators=(",", ":")), row["id"]),
                        )
                    done += 1
                    time.sleep(0.1)  # yield to avoid starving analysis batches
                except Exception:
                    logger.exception("Histogram backfill failed for image_id=%s", row["id"])
            logger.info("Histogram backfill complete: %d/%d processed", done, len(rows))
        except Exception:
            logger.exception("Histogram backfill thread crashed")

    threading.Thread(target=_run, daemon=True, name="histogram-backfill").start()


# Shared INSERT SQL used by both /analyze and /analyze-folder.
#
# `uuid` is a stable identifier used by training_samples + shooting_log to
# survive analysis re-runs and Clear Analysis. We generate one on first
# insert and KEEP IT on conflict (COALESCE — never overwrite a uuid that
# already anchors a training_sample). RETURNING id, uuid lets the caller
# pass the uuid to write_shooting_log without an extra SELECT.
_INSERT_SQL = """
    INSERT INTO images (
        file_path, filename, format, source_folder, uuid,
        camera, shot_at, focal_length_mm, aperture, shutter_speed, iso,
        sharpness_score, exposure_score, overall_score,
        highlight_clip_pct, shadow_clip_pct,
        face_detected, face_count, face_sharpness_score,
        eyes_open, eye_openness_ratio, face_size_ratio,
        face_center_offset_x, face_center_offset_y,
        smile_score, mouth_open_score, faces_eyes_open_json,
        face_embedding,
        iqa_score, aesthetic_score, embedding, histogram_json,
        scene, scene_confidence,
        subject_prominence_score, background_distraction_score,
        eye_contact_score, decisive_moment_score,
        analysis_status, imported_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'done', DATETIME('now'))
    ON CONFLICT(file_path) DO UPDATE SET
        uuid                 = COALESCE(images.uuid, excluded.uuid),
        camera               = excluded.camera,
        shot_at              = excluded.shot_at,
        focal_length_mm      = excluded.focal_length_mm,
        aperture             = excluded.aperture,
        shutter_speed        = excluded.shutter_speed,
        iso                  = excluded.iso,
        sharpness_score      = excluded.sharpness_score,
        exposure_score       = excluded.exposure_score,
        overall_score        = excluded.overall_score,
        highlight_clip_pct   = excluded.highlight_clip_pct,
        shadow_clip_pct      = excluded.shadow_clip_pct,
        face_detected        = excluded.face_detected,
        face_count           = excluded.face_count,
        face_sharpness_score = excluded.face_sharpness_score,
        eyes_open            = excluded.eyes_open,
        eye_openness_ratio   = excluded.eye_openness_ratio,
        face_size_ratio      = excluded.face_size_ratio,
        face_center_offset_x = excluded.face_center_offset_x,
        face_center_offset_y = excluded.face_center_offset_y,
        smile_score          = excluded.smile_score,
        mouth_open_score     = excluded.mouth_open_score,
        faces_eyes_open_json = excluded.faces_eyes_open_json,
        face_embedding       = excluded.face_embedding,
        iqa_score            = excluded.iqa_score,
        aesthetic_score      = excluded.aesthetic_score,
        embedding            = excluded.embedding,
        histogram_json       = excluded.histogram_json,
        scene                = excluded.scene,
        scene_confidence     = excluded.scene_confidence,
        subject_prominence_score    = excluded.subject_prominence_score,
        background_distraction_score = excluded.background_distraction_score,
        eye_contact_score           = excluded.eye_contact_score,
        decisive_moment_score       = excluded.decisive_moment_score,
        analysis_status      = 'done'
    RETURNING id, uuid
"""


def _build_insert_params(path: Path, result: dict, exif: dict) -> tuple:
    """Pack analysis + EXIF into the positional tuple for _INSERT_SQL."""
    sharpness  = result["sharpness"]
    exposure   = result["exposure"]
    face       = result["face"]
    iqa        = result["iqa"]
    aesthetic  = result["aesthetic"]
    similarity = result["similarity"]
    histogram  = result.get("histogram")
    return (
        str(path), path.name,
        path.suffix.lstrip(".").upper(),
        str(path.parent),
        str(_uuid.uuid4()),
        exif["camera"], exif["shot_at"],
        exif["focal_length_mm"], exif["aperture"],
        exif["shutter_speed"], exif["iso"],
        sharpness["sharpness_score"],
        exposure["exposure_score"],
        result["overall_quality_score"],
        exposure["highlight_clip_pct"],
        exposure["shadow_clip_pct"],
        int(face["face_detected"]),
        face["face_count"],
        face["face_sharpness_score"],
        int(face["eyes_open"]) if face["eyes_open"] is not None else None,
        face["eye_openness_ratio"],
        face["face_size_ratio"],
        face["face_center_offset_x"],
        face["face_center_offset_y"],
        face.get("smile_score"),
        face.get("mouth_open_score"),
        # Per-face eye state — JSON list of bools, mirrors face_count length.
        # NULL when only BlazeFace fired (no blendshapes available).
        json.dumps(face.get("faces_eyes_open"), separators=(",", ":"))
            if face.get("faces_eyes_open") is not None else None,
        # FaceNet 512-dim identity embedding — NULL when no face detected.
        _face_embedding_to_json(face.get("face_embedding")),
        iqa["iqa_score"],
        aesthetic["aesthetic_score"],
        _embedding_to_json(similarity["embedding"]),
        json.dumps(histogram, separators=(",", ":")) if histogram else None,
        result.get("scene", {}).get("scene"),
        result.get("scene", {}).get("scene_confidence"),
        # SigLIP zero-shot content axes (v0.13). Each is 0.0–1.0 or None;
        # eye_contact is None for non-portrait shots so the personal-model
        # imputer fills the NaN at train time.
        result.get("concepts", {}).get("subject_prominence"),
        result.get("concepts", {}).get("background_distraction"),
        result.get("concepts", {}).get("eye_contact"),
        result.get("concepts", {}).get("decisive_moment"),
    )


def _shooting_log_kwargs(path: Path, result: dict, exif: dict, sample_uuid: str | None) -> dict:
    """Pack the keyword args for write_shooting_log from analyze results."""
    return {
        "sample_uuid": sample_uuid,
        "shot_at": exif["shot_at"],
        "camera": exif["camera"],
        "lens_model": exif.get("lens_model"),
        "film_simulation": exif.get("film_simulation"),
        "format": path.suffix.lstrip(".").upper(),
        "focal_length_mm": exif["focal_length_mm"],
        "aperture": exif["aperture"],
        "shutter_speed": exif["shutter_speed"],
        "iso": exif["iso"],
        "overall_score": result["overall_quality_score"],
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest):
    """
    Analyze a single photo and store the result.

    Steps:
      1. Validate the file exists on disk.
      2. Run Phase 1 quality analysis (sharpness + exposure).
      3. Upsert a row in the images table (insert or update if already analyzed).
      4. Return the full score breakdown.

    Raises 404 if the file doesn't exist.
    Raises 500 if analysis itself fails (corrupt file, unsupported format, etc.).
    """
    path = Path(request.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {request.file_path}")

    try:
        result = analyze_photo_quality(str(path))
        exif   = extract_exif(str(path))
    except Exception as e:
        logger.exception("Single-file analysis failed: %s", path)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}")

    sharpness  = result["sharpness"]
    exposure   = result["exposure"]
    face       = result["face"]

    with get_db() as conn:
        inserted = conn.execute(
            _INSERT_SQL, _build_insert_params(path, result, exif)
        ).fetchone()
        sample_uuid = inserted["uuid"] if inserted is not None else None
        row = conn.execute(
            "SELECT id, preview_path FROM images WHERE file_path = ?", (str(path),)
        ).fetchone()
        image_id = row["id"]
        write_shooting_log(conn, **_shooting_log_kwargs(path, result, exif, sample_uuid))

    # Re-analysis can change features → drop the stale cached score for this id.
    _personal_model.invalidate(image_id)

    return AnalyzeResponse(
        image_id=image_id,
        file_path=str(path),
        overall_quality_score=result["overall_quality_score"],
        explanation=None,
        sharpness=SharpnessDetail(
            sharpness_score=sharpness["sharpness_score"],
            sharpness_label=sharpness["sharpness_label"],
            laplacian_variance=sharpness["laplacian_variance"],
        ),
        exposure=ExposureDetail(
            exposure_score=exposure["exposure_score"],
            mean_brightness=exposure["mean_brightness"],
            std_brightness=exposure["std_brightness"],
            highlight_clip_pct=exposure["highlight_clip_pct"],
            shadow_clip_pct=exposure["shadow_clip_pct"],
            exposure_warning=exposure["exposure_warning"],
            is_likely_intentional=exposure["is_likely_intentional"],
        ),
        face=FaceDetail(
            face_detected=face["face_detected"],
            face_count=face["face_count"],
            face_sharpness_score=face["face_sharpness_score"],
            eyes_open=face["eyes_open"],
            eye_openness_ratio=face["eye_openness_ratio"],
            face_size_ratio=face["face_size_ratio"],
            face_center_offset_x=face["face_center_offset_x"],
            face_center_offset_y=face["face_center_offset_y"],
        ),
    )


def _run_batch(
    folder: Path,
    to_analyze: list[Path],
    skipped: list[str],
    total_candidates: int,
    paused_watch_path: Any,
) -> None:
    """
    Run the batch analysis loop in a daemon thread.

    Extracted from analyze_folder so that the HTTP request worker thread is
    released immediately — uvicorn can then serve /analyze-progress and
    /stop-analysis without queuing behind the (potentially minutes-long) batch.

    _progress is pre-initialised by analyze_folder before this thread starts.
    On completion (normal, stop, or error) _progress["running"] is set to False
    and the final counts are written so the last poll sees the totals.
    """
    analyzed, errors = 0, []

    # Read settings ONCE before the loop. Settings can't change mid-batch,
    # so the previous "fresh DB connection per photo per setting" pattern
    # was pure overhead (~10ms × 741 = ~7s on a typical batch).
    from phase1_technical.quality_analyzer import get_sharpness_weight as _get_sharpness_weight
    sharpness_weight = _get_sharpness_weight()

    try:
        with get_db() as conn:
            for file_path in to_analyze:
                # Top-of-loop stop check — catches a Stop pressed between files.
                if _progress.get("stop_requested") or _stop_event.is_set():
                    break
                path_str = str(file_path)
                _progress["current_file"] = file_path.name
                _photo_t0 = time.monotonic()

                try:
                    # Second stop check — catches a Stop pressed after
                    # current_file was written but before analysis started.
                    # Costs nothing; eliminates a whole photo's decode latency
                    # in the common "stop pressed right as the next file starts"
                    # race.
                    if _progress.get("stop_requested") or _stop_event.is_set():
                        break

                    _progress["_current_step"] = "analysis"
                    result = analyze_photo_quality(
                        path_str,
                        stop_event=_stop_event,
                        sharpness_weight=sharpness_weight,
                    )
                    _progress["_current_step"] = "exif"
                    exif   = extract_exif(path_str)
                    _progress["_current_step"] = "database"

                    # RETURNING id, uuid avoids a separate SELECT round-trip after
                    # the upsert. Saves ~0.5–1ms × 741 photos = ~0.5–1s/batch.
                    inserted = conn.execute(
                        _INSERT_SQL, _build_insert_params(file_path, result, exif)
                    ).fetchone()
                    if inserted is not None:
                        _personal_model.invalidate(inserted["id"])
                        write_shooting_log(
                            conn,
                            **_shooting_log_kwargs(file_path, result, exif, inserted["uuid"]),
                        )

                    # Commit per-photo so the frontend's /images poll can see
                    # rows as they finish. Without this, SQLite holds the whole
                    # batch in one transaction and nothing is visible to other
                    # connections until the loop ends.
                    # WAL + synchronous=NORMAL (set in get_db) keeps each
                    # commit cheap (~0.5ms instead of ~7ms).
                    conn.commit()

                    analyzed += 1
                    _progress["_current_step"] = None
                    logger.info(
                        "analyze — [%d/%d] %s done in %.1fs",
                        _progress["done"] + 1,
                        _progress["total"],
                        file_path.name,
                        time.monotonic() - _photo_t0,
                    )

                except StopRequested:
                    # User pressed Stop mid-photo. Don't record as error, don't
                    # increment done (this file didn't finish). Outer loop check
                    # will see stop_requested and break on the next iteration.
                    break
                except Exception as e:
                    step = _progress.get("_current_step") or "unknown"
                    logger.exception("Batch fail — %s [step: %s]", file_path.name, step)
                    errors.append({"file": file_path.name, "error": str(e), "step": step})
                    _record_error({
                        "file": file_path.name,
                        "error": str(e),
                        "step": step,
                        "ts": datetime.now().isoformat(),
                    })

                _progress["done"] += 1

    finally:
        # Write final counts so the last /analyze-progress poll can report totals.
        # elapsed is computed from the monotonic started_at stamp so it is
        # immune to wall-clock drift / NTP corrections during long batches.
        started = _progress.get("started_at")
        elapsed = (time.monotonic() - started) if started is not None else None
        _stop_event.clear()
        _progress.update({
            "running": False,
            "current_file": None,
            "stop_requested": False,
            "source_folder": None,
            "analyzed_count": analyzed,
            "error_count": len(errors),
            "elapsed_seconds": round(elapsed, 1) if elapsed is not None else None,
        })
        # Resume the watcher we paused on entry. Best-effort: if the path is gone
        # (folder deleted/unmounted mid-batch) just log and move on.
        if paused_watch_path:
            try:
                watcher.start(paused_watch_path)
            except Exception:
                logger.exception("Could not resume watcher on %s", paused_watch_path)


@router.get("/has-subfolders")
def has_subfolders(folder_path: str):
    """
    Quick scan: does the folder contain subfolders with supported photos?

    Used by the frontend right after the user picks a folder. When this
    returns count > 0, the UI prompts whether to analyze just the root or
    walk the tree recursively. Skips the decision subfolders (_Keeps etc.)
    so re-opening an already-culled folder doesn't trigger a false prompt.

    Returns {has_subfolders: bool, count: int} where count is the number of
    immediate subdirectories that contain at least one supported photo.
    """
    folder = Path(folder_path)
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=404, detail=f"Folder not found: {folder_path}")
    skip_dirs = {"_Keeps", "_Maybes", "_Trash"}
    count = 0
    for sub in folder.iterdir():
        if not sub.is_dir() or sub.name in skip_dirs:
            continue
        # Cheap check: does this subdir (or any descendant) contain a photo?
        for path in sub.rglob("*"):
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
                count += 1
                break
    return {"has_subfolders": count > 0, "count": count}


@router.post("/analyze-folder")
def analyze_folder(request: AnalyzeFolderRequest):
    """
    Analyze every supported photo in a folder and store results in the DB.

    Walks the folder (non-recursive), skips files already in the DB with
    analysis_status='done' so re-running is safe and fast.

    The heavy batch loop runs in a daemon thread so this endpoint returns
    immediately with {"status": "started", ...}.  The frontend already polls
    GET /analyze-progress for live status — no frontend change is needed.

    Returns 409 if another analysis is already running.
    """
    folder = Path(request.folder_path)
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=404, detail=f"Folder not found: {request.folder_path}")

    if _progress.get("running"):
        raise HTTPException(status_code=409, detail="Analysis already running")

    if request.include_subfolders:
        # Recursive walk. Skip the conventional decision subfolders so a
        # re-analysis doesn't suck in already-decided photos as fresh inputs.
        skip_dirs = {"_Keeps", "_Maybes", "_Trash"}
        candidates = sorted(
            f for f in folder.rglob("*")
            if f.is_file()
            and f.suffix.lower() in SUPPORTED_EXTENSIONS
            and not any(part in skip_dirs for part in f.relative_to(folder).parts)
        )
    else:
        candidates = sorted(
            f for f in folder.iterdir()
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
        )
    if not candidates:
        # Nothing to analyze — return immediately without touching _progress.
        _progress.update({
            "analyzed_count": 0,
            "error_count": 0,
        })
        return {"status": "done", "analyzed": 0, "skipped": [], "errors": [], "total_found": 0}

    with get_db() as conn:
        done_paths = {
            row[0] for row in conn.execute(
                "SELECT file_path FROM images WHERE analysis_status = 'done'"
            ).fetchall()
        }

    to_analyze = [f for f in candidates if str(f) not in done_paths]
    skipped = [f.name for f in candidates if str(f) in done_paths]

    # Initialise progress before starting the thread so the very first poll
    # already sees the correct total.  Reset the stop flag so a previous stop
    # doesn't block the new run.
    _stop_event.clear()
    _progress.update({
        "running": True,
        "total": len(to_analyze),
        "done": 0,
        "current_file": None,
        "started_at": time.monotonic(),
        "stop_requested": False,
        "source_folder": str(folder),
        "analyzed_count": 0,
        "error_count": 0,
        "elapsed_seconds": None,
    })

    # Pause any active watcher for the duration of the batch run, then resume
    # inside _run_batch's finally block — keeps the single _progress slot
    # uncontested.
    paused_watch_path = watcher.watching
    if paused_watch_path:
        watcher.stop()

    t = threading.Thread(
        target=_run_batch,
        args=(folder, to_analyze, skipped, len(candidates), paused_watch_path),
        daemon=True,
        name="batch-analysis",
    )
    t.start()

    return {
        "status": "started",
        "total": len(to_analyze),
        "skipped": skipped,
        "total_found": len(candidates),
    }


@router.get("/analyze-progress")
def analyze_progress():
    """
    Return current batch analysis progress so the frontend can show a live bar.

    Fields:
      running          — True while a batch is in flight
      total            — files to analyze (skipped files excluded)
      done             — files finished so far
      pct              — 0-100 integer percentage
      current_file     — filename being analyzed right now (or None)
      eta_seconds      — estimated seconds remaining (None until at least 1 file done)
      elapsed_seconds  — live wall-clock seconds since the batch started while
                         running; final frozen total after the batch finishes;
                         None when no batch has run this session. Reset to None
                         on every new analyze-folder call so a stale value from
                         the previous batch never leaks into the new run. Use
                         `running` (not this field) as the done-detection sentinel.
    """
    p = _progress
    pct = round(p["done"] / p["total"] * 100) if p["total"] else 0
    eta = None
    live_elapsed = None
    if p["running"] and p["started_at"] is not None:
        live_elapsed = round(time.monotonic() - p["started_at"], 1)
        if p["done"] > 0:
            rate = live_elapsed / p["done"]      # seconds per file
            remaining = p["total"] - p["done"]
            eta = round(rate * remaining)
    return {
        "running":         p["running"],
        "total":           p["total"],
        "done":            p["done"],
        "pct":             pct,
        "current_file":    p["current_file"],
        "eta_seconds":     eta,
        "source_folder":   p.get("source_folder"),
        "elapsed_seconds": live_elapsed if p["running"] else p.get("elapsed_seconds"),
    }


@router.post("/stop-analysis")
def stop_analysis():
    """
    Signal the running batch analysis to stop after the current file finishes.

    Sets a flag that the analyze-folder loop checks between files. The current
    file completes normally (no mid-file abort), then the loop exits cleanly.
    Safe to call when nothing is running — returns 'not_running' in that case.
    """
    if not _progress.get("running"):
        return {"status": "not_running"}
    _progress["stop_requested"] = True
    _stop_event.set()
    return {"status": "stopping"}


@router.get("/previews/{image_id}")
def get_preview(image_id: int):
    """
    Serve a high-quality preview for the UI.

    RAW files are demosaiced at half-resolution on first request and cached
    to data/previews/ so every subsequent load is instant. JPEG/PNG files
    are served directly without caching (they're already compressed).

    Raises 404 if the image_id doesn't exist or the file has been moved.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT file_path, format, preview_path FROM images WHERE id = ?",
            (image_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Image not found")

    file_path = Path(row["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File missing from disk: {file_path}")

    fmt = (row["format"] or "").upper()

    # Serve non-RAW files directly — no cache needed.
    if fmt not in RAW_FORMATS:
        from PIL import ImageOps
        img = PilImage.open(str(file_path))
        img = ImageOps.exif_transpose(img)
        img.thumbnail(PREVIEW_SIZE, PilImage.LANCZOS)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=PREVIEW_QUALITY)
        return Response(content=buf.getvalue(), media_type="image/jpeg")

    # RAW: check the disk cache first.
    cached_path = Path(row["preview_path"]) if row["preview_path"] else None
    if cached_path and cached_path.exists():
        return Response(content=cached_path.read_bytes(), media_type="image/jpeg")

    # Cache miss — generate and persist.
    jpeg_bytes = _generate_preview(file_path, fmt)

    PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = PREVIEW_CACHE_DIR / f"{image_id}.jpg"
    cache_file.write_bytes(jpeg_bytes)

    with get_db() as conn:
        conn.execute(
            "UPDATE images SET preview_path = ? WHERE id = ?",
            (str(cache_file), image_id),
        )

    return Response(content=jpeg_bytes, media_type="image/jpeg")


@router.get("/histogram/{image_id}")
def get_histogram(image_id: int):
    """
    Return a 256-bin R/G/B/luminance histogram for an image.

    Served from `images.histogram_json` (computed once at analyze time on the
    full-resolution decoded RGB so it matches the stored clip percentages).
    For rows analyzed before histogram_json existed the column is NULL — we
    compute on-demand from the cached preview JPEG and persist back so the
    next open is instant.

    Cache-Control: immutable tells the browser to never re-fetch for this
    image_id — the data is permanent once written to histogram_json.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT histogram_json FROM images WHERE id = ?", (image_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Image not found")

    _CACHE_HEADERS = {"Cache-Control": "private, max-age=31536000, immutable"}

    if row["histogram_json"]:
        return JSONResponse(content=json.loads(row["histogram_json"]), headers=_CACHE_HEADERS)

    # Backfill path — old row without histogram_json. Compute from cached
    # preview, persist for next time, then return.
    try:
        img = _load_preview_pil(image_id)
        result = _compute_histogram(_np.asarray(img))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Histogram computation failed for image_id=%s", image_id)
        raise HTTPException(status_code=500, detail=f"Histogram failed: {exc}")

    try:
        with get_db() as conn:
            conn.execute(
                "UPDATE images SET histogram_json = ? WHERE id = ?",
                (json.dumps(result, separators=(",", ":")), image_id),
            )
    except Exception:
        logger.exception("Failed to persist backfilled histogram for image_id=%s", image_id)
    return JSONResponse(content=result, headers=_CACHE_HEADERS)


@router.get("/clipping-mask/{image_id}")
def get_clipping_mask(image_id: int, mode: str = "highlights"):
    """
    Return a transparent PNG that tints clipped pixels in the preview.

    `mode=highlights` (default) — pixels where any channel == 255, tinted amber.
    `mode=shadows`              — pixels where any channel == 0,   tinted cyan.

    Cache-Control: immutable — the preview pixels don't change, so the mask
    is stable. Browser caches it for the session, eliminating repeat re-decodes
    when the user toggles shadows/highlights off and back on.
    """
    if mode not in ("highlights", "shadows"):
        raise HTTPException(
            status_code=400,
            detail=f"mode must be 'highlights' or 'shadows', got {mode!r}",
        )

    img = _load_preview_pil(image_id)
    try:
        rgb = _np.asarray(img)
        if mode == "highlights":
            mask = (rgb >= 253).all(axis=2)
            tint = (232, 184, 74)   # design system Warm Amber (#E8B84A)
        else:
            mask = (rgb <= 2).all(axis=2)
            tint = (91, 184, 212)   # design system Cool Cyan (#5BB8D4)

        h, w = mask.shape
        rgba = _np.zeros((h, w, 4), dtype=_np.uint8)
        rgba[mask, 0] = tint[0]
        rgba[mask, 1] = tint[1]
        rgba[mask, 2] = tint[2]
        rgba[mask, 3] = 230  # ~90% alpha

        out = PilImage.fromarray(rgba, mode="RGBA")
        buf = io.BytesIO()
        out.save(buf, format="PNG", optimize=True)
        return Response(
            content=buf.getvalue(),
            media_type="image/png",
            headers={"Cache-Control": "private, max-age=31536000, immutable"},
        )
    except Exception as exc:
        logger.exception("Clipping mask failed for image_id=%s mode=%s", image_id, mode)
        raise HTTPException(status_code=500, detail=f"Clipping mask failed: {exc}")
