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

from backend import provenance_link
from backend.database import get_bool_setting, get_db, get_setting, write_shooting_log
from backend.state import (
    PREVIEW_CACHE_DIR,
    _progress,
    _record_error,
    _stop_event,
    invalidate_all,
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
from backend.constants import (
    SUPPORTED_EXTENSIONS,
    RAW_FORMATS,
    DECISION_SUBFOLDERS,
    is_standalone_hif,
    is_raw_format,
    has_processed_sibling,
)
from backend.routers.settings import get_analysis_mode
# .hif is in SUPPORTED_EXTENSIONS but only analyzed standalone (X100VI/X Half
# HIF-only shooting). When a same-stem .RAF exists, the .hif is a RAW+HIF
# sidecar preview — move-only, never analyzed on its own. See
# is_standalone_hif() and backend/file_mover.py.

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
    val = get_bool_setting("prefer_sidecar_preview", False)
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

    Reaches for the on-disk cached preview first (instant); on a miss,
    generates one via _generate_preview (RAW demosaic or plain decode
    depending on format) AND persists it to data/previews/, same as
    GET /previews/{id} — every format is cached unconditionally now (see
    that endpoint's docstring for why HIF needed this too). Used by both
    the histogram endpoint and the clipping-mask endpoint so the pixels
    they analyze are exactly the pixels DetailView shows.

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
    jpeg_bytes = _generate_preview(file_path, fmt)

    PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = PREVIEW_CACHE_DIR / f"{image_id}.jpg"
    cache_file.write_bytes(jpeg_bytes)
    with get_db() as conn:
        conn.execute(
            "UPDATE images SET preview_path = ? WHERE id = ?",
            (str(cache_file), image_id),
        )

    return PilImage.open(io.BytesIO(jpeg_bytes)).convert("RGB")


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

    Since 2026-08-28 the actual correctness guarantee is the process-wide
    MODEL_LOAD_LOCK held by each loader (phase2_quality/model_loading.py) —
    warming sequentially here could never be one, because it only orders the
    loads *this* thread performs. If a user clicked Analyze before warm-up
    finished, the six-worker scorer pool called the same loaders from other
    threads and raced anyway, silently blanking IQA + Aesthetic for the whole
    batch. This function stays sequential regardless: it avoids pointless lock
    contention and keeps the startup toasts in a predictable order.
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
        file_path, filename, format, source_folder, source_type, photos_uuid, uuid,
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
        edge_width_px, face_edge_width_px,
        analysis_status, imported_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'done', DATETIME('now'))
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
        edge_width_px        = excluded.edge_width_px,
        face_edge_width_px   = excluded.face_edge_width_px,
        analysis_status      = 'done'
    RETURNING id, uuid
"""


# Lightweight insert for a RAW file skipped under analysis_mode=jpeg_priority
# (see backend/routers/settings.py::analysis_mode). "jpeg_priority" is the
# setting's stored value, but the skip fires for ANY processed sibling —
# .jpg/.jpeg/.hif/.png/.tif/.tiff, see has_processed_sibling. No scorers run
# — every score column stays NULL and analysis_status is 'skipped', not
# 'done', so the row is excluded from every "done"-gated query (auto-cull,
# pairwise, undecided counters, /folders/unfinished) and from the grid (see
# _filter_raw_jpeg_pairs in routers/images.py). It still gets a uuid and
# file_path so _find_companion_image_id can mirror K/M/R decisions onto it
# and move it alongside its sibling. EXIF is cheap and kept for camera/shot_at
# metadata (burst grouping, shooting log) even though nothing is scored.
_INSERT_SKIPPED_SQL = """
    INSERT INTO images (
        file_path, filename, format, source_folder, uuid,
        camera, shot_at, focal_length_mm, aperture, shutter_speed, iso,
        analysis_status, imported_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'skipped', DATETIME('now'))
    ON CONFLICT(file_path) DO UPDATE SET
        uuid            = COALESCE(images.uuid, excluded.uuid),
        camera          = excluded.camera,
        shot_at         = excluded.shot_at,
        focal_length_mm = excluded.focal_length_mm,
        aperture        = excluded.aperture,
        shutter_speed   = excluded.shutter_speed,
        iso             = excluded.iso,
        analysis_status = CASE images.analysis_status WHEN 'done' THEN 'done' ELSE 'skipped' END
    RETURNING id, uuid
"""



def _stamp_provenance_key(conn, image_id: int, path) -> None:
    """Record Provenance's stable per-photo key on a freshly ingested row.

    **Has to happen at ingest.** The join is on the staged path in
    temp_import, and the first K/M/R moves the file into _Keeps/_Maybes/
    _Rejects — after which the lookup returns None forever. A silent no-op
    when Provenance isn't installed, or when the photo didn't come through it.

    Guarded by `if key`, so a re-analysis of an already-moved photo leaves the
    key it was stamped with rather than clobbering it with NULL.
    """
    key = provenance_link.key_for_path(path)
    if key:
        conn.execute("UPDATE images SET provenance_key = ? WHERE id = ?",
                     (key, image_id))


def _build_skipped_insert_params(path: Path, exif: dict) -> tuple:
    """Pack EXIF into the positional tuple for _INSERT_SKIPPED_SQL."""
    return (
        str(path), path.name,
        path.suffix.lstrip(".").upper(),
        str(path.parent),
        str(_uuid.uuid4()),
        exif["camera"], exif["shot_at"],
        exif["focal_length_mm"], exif["aperture"],
        exif["shutter_speed"], exif["iso"],
    )


def _build_insert_params(
    path: Path,
    result: dict,
    exif: dict,
    *,
    source_folder: str | None = None,
    filename: str | None = None,
    source_type: str = "folder",
    photos_uuid: str | None = None,
) -> tuple:
    """Pack analysis + EXIF into the positional tuple for _INSERT_SQL.

    The keyword overrides exist for non-filesystem sources. An Apple Photos
    asset lives at `originals/<hex>/<UUID>.heic`, so its real parent directory
    is useless as a tab key and its real filename is a UUID — the album URI and
    the asset's `original_filename` are passed in instead. The defaults
    reproduce today's folder behaviour exactly, so that path is unaffected.
    """
    sharpness  = result["sharpness"]
    exposure   = result["exposure"]
    face       = result["face"]
    iqa        = result["iqa"]
    aesthetic  = result["aesthetic"]
    similarity = result["similarity"]
    histogram  = result.get("histogram")
    return (
        str(path), filename or path.name,
        path.suffix.lstrip(".").upper(),
        source_folder if source_folder is not None else str(path.parent),
        source_type,
        photos_uuid,
        # KaMeRa's own durable id, never the Photos asset UUID: this column is
        # UNIQUE and joins training_samples / auto_decision_log, and one asset
        # can legitimately appear in two album tabs.
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
        # Edge-width sharpness in px at a fixed 1600 px long side — recorded for
        # every corpus, scored on only by Photos. NULL on a featureless frame.
        (result.get("edge") or {}).get("frame_width_px"),
        (result.get("edge") or {}).get("face_width_px"),
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
        _stamp_provenance_key(conn, image_id, path)
        write_shooting_log(conn, **_shooting_log_kwargs(path, result, exif, sample_uuid))

    # Re-analysis can change features → drop the stale cached score for this id.
    invalidate_all([image_id])

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


def _is_analyzable(f: Path) -> bool:
    if f.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return False
    if f.suffix.lower() == ".hif" and not is_standalone_hif(f):
        return False  # RAW+HIF sidecar — moves with its RAF, never analyzed alone
    return True


def _scan_candidates(folder: Path, include_subfolders: bool) -> list[Path]:
    """Walk `folder` for analyzable files. Shared by the initial scan and the
    mid-batch rescans that pick up files a live import (e.g. Provenance) drops
    in while a batch is already running."""
    if include_subfolders:
        # Recursive walk. Skip the conventional decision subfolders so a
        # re-analysis doesn't suck in already-decided photos as fresh inputs.
        skip_dirs = DECISION_SUBFOLDERS
        return sorted(
            f for f in folder.rglob("*")
            if f.is_file()
            and _is_analyzable(f)
            and not any(part in skip_dirs for part in f.relative_to(folder).parts)
        )
    return sorted(
        f for f in folder.iterdir()
        if f.is_file() and _is_analyzable(f)
    )


# How often _run_batch rescans the source folder for new arrivals. A live
# Provenance import can drop hundreds of files after the batch's initial
# snapshot; without a rescan they'd sit undiscovered until the batch ends
# and the (paused-for-the-duration) watcher resumes. Time-based rather than
# every N files so it stays cheap on fast (JPG-heavy) batches too.
RESCAN_INTERVAL_SECONDS = 10.0


def _run_batch(
    folder: Path | None,
    to_analyze: list[Path],
    skipped: list[str],
    total_candidates: int,
    paused_watch_path: Any,
    include_subfolders: bool = False,
    source_folder_override: str | None = None,
    filename_overrides: dict[str, str] | None = None,
    photos_uuid_overrides: dict[str, str] | None = None,
) -> None:
    """
    Run the batch analysis loop in a daemon thread.

    Extracted from analyze_folder so that the HTTP request worker thread is
    released immediately — uvicorn can then serve /analyze-progress and
    /stop-analysis without queuing behind the (potentially minutes-long) batch.

    _progress is pre-initialised by analyze_folder before this thread starts.
    On completion (normal, stop, or error) _progress["running"] is set to False
    and the final counts are written so the last poll sees the totals.

    to_analyze is a live list (not copied) — mid-batch rescans append to it
    in place and bump _progress["total"] so a batch started against a partial
    import (Provenance still copying files off a card) keeps absorbing new
    arrivals instead of finishing early and leaving them undiscovered until
    the watcher resumes.
    """
    analyzed, errors = 0, []
    # `folder is None` for a Photos album, and there `skipped` holds original
    # filenames rather than paths — so the join below is both impossible and
    # meaningless. Only the folder rescan reads `queued`, and that is already
    # gated on `folder is not None`, so an album needs no entries at all.
    # This stayed invisible until the first re-run of an album: on a fresh run
    # `skipped` is empty, and an empty comprehension never evaluates its body.
    queued = {str(f) for f in to_analyze}
    if folder is not None:
        queued |= {str(folder / name) for name in skipped}
    last_rescan = time.monotonic()

    # Read settings ONCE before the loop. Settings can't change mid-batch,
    # so the previous "fresh DB connection per photo per setting" pattern
    # was pure overhead (~10ms × 741 = ~7s on a typical batch).
    from phase1_technical.quality_analyzer import get_sharpness_weight as _get_sharpness_weight
    from phase1_technical.quality_analyzer import decode_raw as _decode_raw, RAW_DECODE_SUFFIXES
    from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor
    sharpness_weight = _get_sharpness_weight()
    jpeg_priority = get_analysis_mode() == "jpeg_priority"

    def _needs_full_raw_decode(p: Path) -> bool:
        # Mirrors the skip_scoring gate below: only a RAW file that will
        # actually reach analyze_photo_quality's expensive decode qualifies
        # for prefetch. Paired RAWs under jpeg_priority take the lightweight
        # EXIF-only path and never decode at all.
        if p.suffix.lower() not in RAW_DECODE_SUFFIXES:
            return False
        if jpeg_priority and is_raw_format(p.suffix.lstrip(".").upper()) and has_processed_sibling(p):
            return False
        return True

    # One-ahead RAW decode prefetch: while photo N's scorers are running,
    # decode photo N+1's RAW data on this single background worker so the
    # ~17s rawpy.postprocess() cost (GIL-releasing, CPU-bound) overlaps with
    # scoring instead of stalling the loop. Bounded to exactly one photo
    # ahead — each decoded RAF is tens of MB, and decode already outlasts
    # scoring per photo, so a deeper queue would only add memory pressure
    # without shortening the batch further. Scoped to orphan RAWs only
    # (jpeg_priority already skips paired ones before they'd ever decode).
    _prefetch_pool = _ThreadPoolExecutor(max_workers=1)
    _prefetch: dict[str, Any] = {"path": None, "future": None}

    def _submit_prefetch(p: Path) -> None:
        if _needs_full_raw_decode(p):
            _prefetch["path"] = str(p)
            _prefetch["future"] = _prefetch_pool.submit(_decode_raw, str(p))
        else:
            _prefetch["path"] = None
            _prefetch["future"] = None

    def _take_predecoded(path_str: str):
        if _prefetch["path"] == path_str and _prefetch["future"] is not None:
            fut = _prefetch["future"]
            _prefetch["path"] = None
            _prefetch["future"] = None
            return fut.result()
        return None

    if to_analyze:
        _submit_prefetch(to_analyze[0])

    try:
        with get_db() as conn:
            i = 0
            while i < len(to_analyze):
                file_path = to_analyze[i]
                i += 1
                # Top-of-loop stop check — catches a Stop pressed between files.
                if _progress.get("stop_requested") or _stop_event.is_set():
                    break

                # Claim this photo's predecoded RAW data (if any) BEFORE
                # submitting the next photo's prefetch — _submit_prefetch
                # overwrites the single prefetch slot, so reading it first
                # avoids losing the current photo's already-finished decode.
                predecoded = _take_predecoded(str(file_path))

                # Kick off next photo's RAW decode now, before this photo's
                # scoring starts, so the two overlap on separate threads.
                if i < len(to_analyze):
                    _submit_prefetch(to_analyze[i])

                # Periodically rescan the folder for files that arrived after
                # the batch started (or after the last rescan) — e.g. a card
                # import still in progress. New files are appended to the live
                # to_analyze list and total ticks up so the progress bar keeps
                # reflecting reality instead of freezing at the original count.
                # `folder is None` for a Photos album: membership is fixed
                # when the batch starts and there is no directory to re-walk.
                now = time.monotonic()
                if folder is not None and now - last_rescan >= RESCAN_INTERVAL_SECONDS:
                    last_rescan = now
                    try:
                        found = _scan_candidates(folder, include_subfolders)
                        new_files = [f for f in found if str(f) not in queued]
                    except Exception:
                        logger.exception("Batch rescan failed for %s", folder)
                        new_files = []
                    if new_files:
                        queued.update(str(f) for f in new_files)
                        to_analyze.extend(new_files)
                        _progress["total"] = len(to_analyze)
                        logger.info(
                            "analyze — rescan picked up %d new file(s) in %s (total now %d)",
                            len(new_files), folder, len(to_analyze),
                        )

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

                    # jpeg_priority: a RAW with a same-stem processed sibling
                    # (.jpg/.jpeg/.hif/.png/.tif/.tiff) gets a lightweight row
                    # (EXIF only, no scorers) instead of full analysis — the
                    # sibling is what gets scored and culled; this row exists
                    # only so K/M/R on the sibling can mirror onto it and move
                    # it. Orphan RAWs (no sibling) always get full analysis
                    # regardless of the setting.
                    skip_scoring = (
                        jpeg_priority
                        and is_raw_format(file_path.suffix.lstrip(".").upper())
                        and has_processed_sibling(file_path)
                    )

                    if skip_scoring:
                        _progress["_current_step"] = "exif"
                        exif = extract_exif(path_str)
                        _progress["_current_step"] = "database"
                        conn.execute(
                            _INSERT_SKIPPED_SQL, _build_skipped_insert_params(file_path, exif)
                        )
                    else:
                        _progress["_current_step"] = "analysis"
                        result = analyze_photo_quality(
                            path_str,
                            stop_event=_stop_event,
                            sharpness_weight=sharpness_weight,
                            predecoded=predecoded,
                            corpus="photos" if source_folder_override else "folder",
                        )
                        _progress["_current_step"] = "exif"
                        exif   = extract_exif(path_str)
                        _progress["_current_step"] = "database"

                        # RETURNING id, uuid avoids a separate SELECT round-trip after
                        # the upsert. Saves ~0.5–1ms × 741 photos = ~0.5–1s/batch.
                        inserted = conn.execute(
                            _INSERT_SQL,
                            _build_insert_params(
                                file_path, result, exif,
                                source_folder=source_folder_override,
                                filename=(filename_overrides or {}).get(str(file_path)),
                                source_type="photos" if source_folder_override else "folder",
                                photos_uuid=(photos_uuid_overrides or {}).get(str(file_path)),
                            ),
                        ).fetchone()
                        if inserted is not None:
                            invalidate_all([inserted["id"]])
                            _stamp_provenance_key(conn, inserted["id"], file_path)
                            # The shooting log answers "what are my camera
                            # habits" — focal lengths, apertures, bodies. iPhone
                            # shots out of a Photos album would skew every
                            # Dashboard statistic it feeds, so they stay out.
                            if source_folder_override is None:
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
                    # If the file vanished mid-batch (moved/renamed/archived out
                    # from under us — e.g. Provenance archiving a keeper, or a
                    # K/M/R decision), that's an expected race, not a failure.
                    # Skip it silently: no error row, no /debug/last-errors entry,
                    # no "[Errno 2] No such file or directory" toast. (Mirrors the
                    # same guard in file_watcher._analyze_and_store.)
                    if isinstance(e, FileNotFoundError) or not file_path.exists():
                        logger.info("Batch skipped vanished file (moved/renamed): %s",
                                    file_path.name)
                        _progress["done"] += 1
                        continue
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
        # Drop any in-flight prefetch immediately on Stop/error rather than
        # blocking batch teardown on a decode whose result nothing will use.
        _prefetch_pool.shutdown(wait=False, cancel_futures=True)

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
    skip_dirs = DECISION_SUBFOLDERS
    count = 0
    for sub in folder.iterdir():
        if not sub.is_dir() or sub.name in skip_dirs:
            continue
        # Cheap check: does this subdir (or any descendant) contain a photo?
        for path in sub.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            if path.suffix.lower() == ".hif" and not is_standalone_hif(path):
                continue  # RAW+HIF sidecar, not an independently analyzable photo
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

    candidates = _scan_candidates(folder, request.include_subfolders)
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
        skipped_raw_paths = {
            row[0] for row in conn.execute(
                "SELECT file_path FROM images WHERE analysis_status = 'skipped'"
            ).fetchall()
        }

    # A 'skipped' RAW is only left out of the re-queue while it still has a
    # processed sibling — if that sibling was since deleted (orphaned RAW),
    # fall through to a full re-analysis instead of leaving it unscored
    # forever. Cheap: has_processed_sibling only runs for the small set of
    # rows already marked 'skipped', not every candidate.
    still_skippable = {
        p for p in skipped_raw_paths if has_processed_sibling(Path(p))
    }
    resolved_paths = done_paths | still_skippable

    to_analyze = [f for f in candidates if str(f) not in resolved_paths]
    skipped = [f.name for f in candidates if str(f) in resolved_paths]

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
        args=(folder, to_analyze, skipped, len(candidates), paused_watch_path,
              request.include_subfolders),
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

    Every format is demosaiced/decoded on first request and cached to
    data/previews/ so every subsequent load — including after an app
    reload — is instant. This used to be RAW-only (JPEG/PNG were re-decoded
    on every request on the assumption that "already compressed" meant
    "cheap to decode"), but .HIF (HEIF) breaks that assumption: pillow-heif
    decode is comparably expensive to a RAW demosaic, so a folder full of
    HIF-only shots (X100VI/X Half) re-paid that cost on every single reload.
    Caching is unconditional now — cheap formats just get a near-instant
    first write, expensive ones get the same speedup RAW already had.

    Cache lifecycle: cleared alongside the DB row by clear-folder (tab
    close), clear-analysis, and sync-folder (file moved/deleted outside the
    app) — see _delete_image_rows(). A same-content move (K/M/R decision)
    leaves preview_path untouched since the cached bytes are still valid.

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

    cached_path = Path(row["preview_path"]) if row["preview_path"] else None
    if cached_path and cached_path.exists():
        return Response(content=cached_path.read_bytes(), media_type="image/jpeg")

    # Cache miss — generate and persist.
    fmt = (row["format"] or "").upper()
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
