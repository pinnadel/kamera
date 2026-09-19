"""
Settings and per-folder destination configuration endpoints.
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.constants import is_photos_source_folder
from backend.database import get_db, get_folder_overrides, set_folder_override
from backend.state import PREVIEW_CACHE_DIR, clear_all_caches

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class SettingsRequest(BaseModel):
    sharpness_weight:         float | None = None
    keep_threshold:           float | None = None
    maybe_threshold:          float | None = None
    fallback_keep:            float | None = None
    fallback_maybe:           float | None = None
    fallback_sharpness_floor: float | None = None
    face_sharpness_floor:     float | None = None
    iqa_floor:                float | None = None
    reject_soft_face:         bool  | None = None
    reject_blurry_frame:      bool  | None = None
    reject_low_iqa:           bool  | None = None
    reject_closed_eyes:           bool  | None = None
    reject_closed_eyes_all_faces: bool  | None = None
    prefer_sidecar_preview:     bool  | None = None
    reject_to_system_trash:     bool  | None = None
    reject_reciprocal_rule:     bool  | None = None
    reject_above_iso_ceiling:   bool  | None = None
    iso_ceiling:                float | None = None
    # PR3 boundary routing — high-variance near-boundary photos route to Maybe.
    auto_cull_uncertain_to_maybe: bool  | None = None
    uncertainty_threshold:        float | None = None
    # Burst keep-cap / multi-keep post-pass — the best photo per similarity
    # burst always keeps; extra strong frames may also keep (up to
    # max_burst_keeps), the rest demote graded to Maybe or Reject.
    auto_cull_cap_burst_keeps:    bool  | None = None
    max_burst_keeps:              float | None = None
    burst_keep_margin:            float | None = None
    burst_keep_winner_delta:      float | None = None
    burst_keep_reject_delta:      float | None = None
    max_burst_maybes:             float | None = None
    # RAW+JPEG pair visibility — 'raw' | 'processed' | 'both'.
    raw_jpeg_display_mode:        str   | None = None
    # Analysis scope — 'full' | 'jpeg_priority'.
    analysis_mode:                str   | None = None


class FolderSettingsRequest(BaseModel):
    source_folder: str
    keeps_folder:   str | None = None
    maybes_folder:  str | None = None
    rejects_folder: str | None = None


# ---------------------------------------------------------------------------
# Setting specs — populated once at module import
# ---------------------------------------------------------------------------

# Numeric setting key → (default, lo, hi).
_NUMERIC_SETTINGS: dict[str, tuple[float, float, float]] = {}
_BOOL_SETTINGS:    dict[str, bool] = {}
# String/enum setting key → (default, allowed values).
_STRING_SETTINGS:  dict[str, tuple[str, frozenset[str]]] = {
    # Which member(s) of a RAW+JPEG (or RAW+standalone-HIF) pair appear as
    # grid/group tiles. 'both' preserves today's behaviour (every analyzed
    # file is its own tile). Read live by GET /images and by _apply_decision
    # (routers/decisions.py) to decide whether a K/M/R on one member should
    # mirror onto its companion.
    "raw_jpeg_display_mode": ("both", frozenset({"raw", "processed", "both"})),
    # 'jpeg_priority' skips scoring a RAW file (RAF/NEF/3FR) when a same-stem
    # processed sibling exists in the same folder — any of .jpg/.jpeg/.hif/
    # .png/.tif/.tiff, not just JPEG (see constants.py::has_processed_sibling
    # / _PROCESSED_SIBLING_EXTS; the setting value keeps the 'jpeg_priority'
    # name for backward compatibility with already-saved settings rows). The
    # RAW still gets a lightweight images row (analysis_status='skipped') so
    # K/M/R decisions mirror onto it and it moves with its sibling, it just
    # never runs the scorers. A RAW with no sibling is always analyzed in
    # full regardless of this setting (nothing else to cull from). Read live
    # by _scan_candidates/_is_analyzable (routers/analysis.py) and
    # file_watcher.py.
    "analysis_mode": ("full", frozenset({"full", "jpeg_priority"})),
}


def _init_setting_specs() -> None:
    """Populate _NUMERIC_SETTINGS / _BOOL_SETTINGS from quality_analyzer defaults."""
    from phase1_technical.quality_analyzer import (
        DEFAULT_FALLBACK_KEEP,
        DEFAULT_FALLBACK_MAYBE,
        DEFAULT_FALLBACK_SHARPNESS_FLOOR,
        DEFAULT_FACE_SHARP_FLOOR,
        DEFAULT_KEEP_THRESHOLD,
        DEFAULT_MAYBE_THRESHOLD,
        DEFAULT_REJECT_BLURRY_FRAME,
        DEFAULT_REJECT_CLOSED_EYES,
        DEFAULT_REJECT_CLOSED_EYES_ALL_FACES,
        DEFAULT_REJECT_LOW_IQA,
        DEFAULT_IQA_FLOOR,
        DEFAULT_REJECT_SOFT_FACE,
        DEFAULT_SHARPNESS_WEIGHT,
        DEFAULT_ISO_CEILING,
        DEFAULT_BACKGROUND_DISTRACTION_CEILING,
        DEFAULT_REJECT_HIGH_BACKGROUND_DISTRACTION,
        DEFAULT_AUTO_CULL_UNCERTAIN_TO_MAYBE,
        DEFAULT_UNCERTAINTY_THRESHOLD,
        DEFAULT_AUTO_CULL_CAP_BURST_KEEPS,
        DEFAULT_MAX_BURST_KEEPS,
        DEFAULT_BURST_KEEP_MARGIN,
        DEFAULT_BURST_KEEP_WINNER_DELTA,
        DEFAULT_BURST_KEEP_REJECT_DELTA,
        DEFAULT_MAX_BURST_MAYBES,
    )
    _NUMERIC_SETTINGS.update({
        "sharpness_weight":         (DEFAULT_SHARPNESS_WEIGHT,         0.0, 1.0),
        "keep_threshold":           (DEFAULT_KEEP_THRESHOLD,           0.0, 100.0),
        "maybe_threshold":          (DEFAULT_MAYBE_THRESHOLD,          0.0, 100.0),
        "fallback_keep":            (DEFAULT_FALLBACK_KEEP,            0.0, 100.0),
        "fallback_maybe":           (DEFAULT_FALLBACK_MAYBE,           0.0, 100.0),
        "fallback_sharpness_floor": (DEFAULT_FALLBACK_SHARPNESS_FLOOR, 0.0, 100.0),
        "iso_ceiling":              (DEFAULT_ISO_CEILING,              0.0, 204800.0),
        "face_sharpness_floor":     (DEFAULT_FACE_SHARP_FLOOR,         0.0, 100.0),
        # TOPIQ-NR perceptual-quality floor (0-100). iqa_score below this →
        # instant reject (catches the false-sharp failure mode). Fires only
        # when reject_low_iqa is on.
        "iqa_floor":                (DEFAULT_IQA_FLOOR,                0.0, 100.0),
        # SigLIP zero-shot content axis ceiling (0.0–1.0). Fires only when the
        # toggle below is on AND the photo has a detected face.
        "background_distraction_ceiling": (DEFAULT_BACKGROUND_DISTRACTION_CEILING, 0.5, 0.99),
        # PR3 boundary routing — ensemble std_dev (in personal_score points)
        # above which a near-boundary photo is routed to Maybe. Range matches
        # get_thresholds()'s _read_float bounds in quality_analyzer.
        "uncertainty_threshold":    (DEFAULT_UNCERTAINTY_THRESHOLD,    0.0, 50.0),
        # Burst multi-keep tuning. Ranges mirror get_decision_thresholds()'s
        # _read_float bounds in quality_analyzer. max_burst_keeps=1 reproduces
        # the original cap-at-one behaviour.
        "max_burst_keeps":          (DEFAULT_MAX_BURST_KEEPS,          1.0, 10.0),
        "burst_keep_margin":        (DEFAULT_BURST_KEEP_MARGIN,        0.0, 50.0),
        "burst_keep_winner_delta":  (DEFAULT_BURST_KEEP_WINNER_DELTA,  0.0, 50.0),
        "burst_keep_reject_delta":  (DEFAULT_BURST_KEEP_REJECT_DELTA,  0.0, 100.0),
        # Review budget — max Maybe alternates kept per burst before further
        # redundant frames reject (reason burst_redundant).
        "max_burst_maybes":         (DEFAULT_MAX_BURST_MAYBES,         0.0, 10.0),
    })
    _BOOL_SETTINGS.update({
        "reject_soft_face":       DEFAULT_REJECT_SOFT_FACE,
        "reject_blurry_frame":    DEFAULT_REJECT_BLURRY_FRAME,
        # Low perceptual-quality (TOPIQ-NR) instant reject — primary safety net
        # for the false-sharp bug. On by default; floor is iqa_floor above.
        "reject_low_iqa":         DEFAULT_REJECT_LOW_IQA,
        "reject_closed_eyes":     DEFAULT_REJECT_CLOSED_EYES,
        "reject_closed_eyes_all_faces": DEFAULT_REJECT_CLOSED_EYES_ALL_FACES,
        "prefer_sidecar_preview": False,
        # Rejected photos go to the OS Trash bin instead of the per-folder
        # _Rejects/ subfolder. Off by default to preserve current behaviour.
        "reject_to_system_trash":     False,
        # EXIF-based instant rejects — off by default (opt-in).
        "reject_reciprocal_rule":     False,
        "reject_above_iso_ceiling":   False,
        # SigLIP content axis: only fires when a face is detected AND
        # background_distraction_score >= background_distraction_ceiling.
        "reject_high_background_distraction": DEFAULT_REJECT_HIGH_BACKGROUND_DISTRACTION,
        # PR3 boundary routing — master toggle for routing uncertain
        # near-boundary photos to Maybe instead of a hard auto-decision.
        "auto_cull_uncertain_to_maybe": DEFAULT_AUTO_CULL_UNCERTAIN_TO_MAYBE,
        # Burst keep-cap post-pass — caps auto-cull Keep at one photo per
        # similarity burst; the rest demote to Maybe.
        "auto_cull_cap_burst_keeps": DEFAULT_AUTO_CULL_CAP_BURST_KEEPS,
    })


_init_setting_specs()


def tunable_setting_keys() -> tuple[str, ...]:
    """All user-tunable setting keys (numeric + bool).

    Single source of truth for the taste-model backup export/import — the
    registries above are the superset that GET /settings resolves against.
    `SettingsRequest` is NOT authoritative (it omits background_distraction_ceiling
    and reject_high_background_distraction), so callers that need the full set
    of keys must go through here, not the Pydantic model.
    """
    return tuple(_NUMERIC_SETTINGS.keys()) + tuple(_BOOL_SETTINGS.keys()) + tuple(_STRING_SETTINGS.keys())


def get_raw_jpeg_display_mode() -> str:
    """Read the current value of raw_jpeg_display_mode ('raw' | 'processed' | 'both')."""
    from backend.database import get_setting
    default, allowed = _STRING_SETTINGS["raw_jpeg_display_mode"]
    raw = get_setting("raw_jpeg_display_mode")
    if raw is None or raw not in allowed:
        return default
    return raw


def get_analysis_mode() -> str:
    """Read the current value of analysis_mode ('full' | 'jpeg_priority')."""
    from backend.database import get_setting
    default, allowed = _STRING_SETTINGS["analysis_mode"]
    raw = get_setting("analysis_mode")
    if raw is None or raw not in allowed:
        return default
    return raw


# ---------------------------------------------------------------------------
# Helper: read the sidecar-preview preference
# ---------------------------------------------------------------------------


def _is_sidecar_preview_preferred() -> bool:
    """Read the current value of the prefer_sidecar_preview toggle."""
    from backend.database import get_bool_setting
    return get_bool_setting("prefer_sidecar_preview", False)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/settings")
def get_settings():
    """
    Return current scoring weight + decision threshold settings with defaults
    resolved.
    """
    all_keys = tuple(_NUMERIC_SETTINGS.keys()) + tuple(_BOOL_SETTINGS.keys()) + tuple(_STRING_SETTINGS.keys())
    placeholders = ",".join("?" * len(all_keys))

    with get_db() as conn:
        setting_rows = conn.execute(
            f"SELECT key, value FROM settings WHERE key IN ({placeholders})",
            all_keys,
        ).fetchall()
    overrides = {r["key"]: r["value"] for r in setting_rows}

    out: dict = {}

    for key, (default, _lo, _hi) in _NUMERIC_SETTINGS.items():
        raw = overrides.get(key)
        try:
            out[key] = float(raw) if raw is not None else float(default)
        except ValueError:
            out[key] = float(default)
        out[f"{key}_is_default"] = key not in overrides

    # Backwards-compat alias the frontend already uses.
    out["weight_is_default"] = out.pop("sharpness_weight_is_default")

    for key, default in _BOOL_SETTINGS.items():
        raw = overrides.get(key)
        out[key] = (raw.strip().lower() in ("1", "true", "yes", "on")) if raw is not None else default
        out[f"{key}_is_default"] = key not in overrides

    for key, (default, allowed) in _STRING_SETTINGS.items():
        raw = overrides.get(key)
        out[key] = raw if raw in allowed else default
        out[f"{key}_is_default"] = key not in overrides

    return out


@router.post("/settings")
def update_settings(request: SettingsRequest):
    """
    Persist folder paths, scoring weight, and all decision thresholds.
    Pass null/empty for any field to revert that single setting to its default.
    """
    from phase1_technical.quality_analyzer import DEFAULT_SHARPNESS_WEIGHT

    fields_set = request.model_fields_set
    for key, (_default, lo, hi) in _NUMERIC_SETTINGS.items():
        if key in fields_set:
            v = getattr(request, key)
            if v is not None and not (lo <= v <= hi):
                raise HTTPException(status_code=400, detail=f"{key} must be between {lo} and {hi}")

    for key, (_default, allowed) in _STRING_SETTINGS.items():
        if key in fields_set:
            v = getattr(request, key)
            if v is not None and v not in allowed:
                raise HTTPException(status_code=400, detail=f"{key} must be one of {sorted(allowed)}")

    def _effective(field: str) -> float:
        if field in fields_set and getattr(request, field) is not None:
            return float(getattr(request, field))
        from phase1_technical.quality_analyzer import _read_float
        default, lo, hi = _NUMERIC_SETTINGS[field]
        return _read_float(field, default, lo, hi)

    if _effective("maybe_threshold") >= _effective("keep_threshold"):
        raise HTTPException(status_code=400, detail="maybe_threshold must be lower than keep_threshold")
    if _effective("fallback_maybe") >= _effective("fallback_keep"):
        raise HTTPException(status_code=400, detail="fallback_maybe must be lower than fallback_keep")

    weight_changed = False
    new_weight: float | None = None
    if request.sharpness_weight is not None:
        new_weight = float(request.sharpness_weight)

    sidecar_pref_changed = False
    if "prefer_sidecar_preview" in fields_set:
        old_effective = _is_sidecar_preview_preferred()
        new_val = request.prefer_sidecar_preview
        new_effective = bool(new_val) if new_val is not None else _BOOL_SETTINGS["prefer_sidecar_preview"]
        sidecar_pref_changed = old_effective != new_effective

    with get_db() as conn:
        for key in _NUMERIC_SETTINGS:
            if key == "sharpness_weight" or key not in fields_set:
                continue
            v = getattr(request, key)
            if v is None:
                conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (key, f"{float(v):.4f}"),
                )

        for key in _BOOL_SETTINGS:
            if key not in fields_set:
                continue
            v = getattr(request, key)
            if v is None:
                conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (key, "1" if v else "0"),
                )

        for key in _STRING_SETTINGS:
            if key not in fields_set:
                continue
            v = getattr(request, key)
            if v is None:
                conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (key, v),
                )

        if "sharpness_weight" in fields_set:
            existing = conn.execute(
                "SELECT value FROM settings WHERE key = 'sharpness_weight'"
            ).fetchone()
            existing_val = float(existing["value"]) if existing else DEFAULT_SHARPNESS_WEIGHT

            if new_weight is None:
                conn.execute("DELETE FROM settings WHERE key = 'sharpness_weight'")
                effective_new = DEFAULT_SHARPNESS_WEIGHT
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    ("sharpness_weight", f"{new_weight:.4f}"),
                )
                effective_new = new_weight

            if abs(effective_new - existing_val) > 1e-6:
                weight_changed = True
                conn.execute(
                    """
                    UPDATE images
                    SET overall_score = ROUND(
                        sharpness_score * ? + exposure_score * ?, 1
                    )
                    WHERE sharpness_score IS NOT NULL
                      AND exposure_score  IS NOT NULL
                    """,
                    (effective_new, 1.0 - effective_new),
                )

    if weight_changed:
        clear_all_caches()

    if sidecar_pref_changed:
        # The toggle only changes how RAW files render (sidecar vs. demosaic),
        # so only RAW previews are semantically stale. But the on-disk wipe
        # below is a blanket glob("*.jpg") (simplest + safe), and now that
        # EVERY format is cached (not just RAW), nulling only RAW preview_paths
        # would leave non-RAW rows pointing at deleted cache files. Null all of
        # them to keep the DB and disk consistent — non-RAW rows just
        # regenerate an identical preview on next request.
        with get_db() as conn:
            conn.execute("UPDATE images SET preview_path = NULL")
        if PREVIEW_CACHE_DIR.exists():
            for f in PREVIEW_CACHE_DIR.glob("*.jpg"):
                try:
                    f.unlink()
                except OSError:
                    pass
        # Invalidate the analysis router's cached toggle so the next preview
        # generation re-reads the new value from SQLite.
        from backend.routers.analysis import _reset_sidecar_pref_cache
        _reset_sidecar_pref_cache()

    return {"status": "ok", "weight_changed": weight_changed, "sidecar_pref_changed": sidecar_pref_changed}


@router.get("/folder-settings")
def get_folder_settings(source_folder: str):
    """
    Resolved K/M/X destination paths for one source folder.

    Returns the absolute destination plus an `_is_default` flag per decision so
    the UI can show "Default: <source>/_Keeps" vs. the user's custom path.
    """
    src = source_folder.rstrip("/") or source_folder
    overrides = get_folder_overrides(src)
    return {
        "source_folder":     src,
        "keeps_folder":       overrides.get("keeps_folder")   or f"{src}/_Keeps",
        "maybes_folder":      overrides.get("maybes_folder")  or f"{src}/_Maybes",
        "rejects_folder":     overrides.get("rejects_folder") or f"{src}/_Rejects",
        "keeps_is_default":   "keeps_folder"   not in overrides,
        "maybes_is_default":  "maybes_folder"  not in overrides,
        "rejects_is_default": "rejects_folder" not in overrides,
    }


@router.post("/folder-settings")
def update_folder_settings(request: FolderSettingsRequest):
    """
    Persist per-folder destination overrides. Pass null/empty for any field to
    revert that single decision back to its default subfolder.
    """
    src = request.source_folder.strip()
    if not src:
        raise HTTPException(status_code=400, detail="source_folder is required")
    # An override is a destination folder, and resolve_dest_folder() turns it
    # into a real move target. Accepting one for a Photos album would be the
    # one route by which a library path could reach move_photo.
    if is_photos_source_folder(src):
        raise HTTPException(
            status_code=400,
            detail="Photos albums have no destination folders — decisions are "
                   "written as keywords and files are never moved",
        )

    fields_set = request.model_fields_set
    decision_for_field = {
        "keeps_folder":   "keep",
        "maybes_folder":  "maybe",
        "rejects_folder": "reject",
    }
    for field, decision in decision_for_field.items():
        if field not in fields_set:
            continue
        set_folder_override(src, decision, getattr(request, field))

    return {"status": "ok", **get_folder_settings(src)}
