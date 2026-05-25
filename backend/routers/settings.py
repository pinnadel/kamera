"""
Settings and per-folder destination configuration endpoints.
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.database import get_db, get_folder_overrides, set_folder_override
from backend.state import PREVIEW_CACHE_DIR, _personal_model

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
    reject_soft_face:         bool  | None = None
    reject_blurry_frame:      bool  | None = None
    reject_closed_eyes:           bool  | None = None
    reject_closed_eyes_all_faces: bool  | None = None
    prefer_sidecar_preview:     bool  | None = None
    reject_to_system_trash:     bool  | None = None
    reject_reciprocal_rule:     bool  | None = None
    reject_above_iso_ceiling:   bool  | None = None
    iso_ceiling:                float | None = None


class FolderSettingsRequest(BaseModel):
    source_folder: str
    keeps_folder:  str | None = None
    maybes_folder: str | None = None
    trash_folder:  str | None = None


# ---------------------------------------------------------------------------
# Setting specs — populated once at module import
# ---------------------------------------------------------------------------

# Numeric setting key → (default, lo, hi).
_NUMERIC_SETTINGS: dict[str, tuple[float, float, float]] = {}
_BOOL_SETTINGS:    dict[str, bool] = {}


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
        DEFAULT_REJECT_SOFT_FACE,
        DEFAULT_SHARPNESS_WEIGHT,
        DEFAULT_ISO_CEILING,
        DEFAULT_BACKGROUND_DISTRACTION_CEILING,
        DEFAULT_REJECT_HIGH_BACKGROUND_DISTRACTION,
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
        # SigLIP zero-shot content axis ceiling (0.0–1.0). Fires only when the
        # toggle below is on AND the photo has a detected face.
        "background_distraction_ceiling": (DEFAULT_BACKGROUND_DISTRACTION_CEILING, 0.5, 0.99),
    })
    _BOOL_SETTINGS.update({
        "reject_soft_face":       DEFAULT_REJECT_SOFT_FACE,
        "reject_blurry_frame":    DEFAULT_REJECT_BLURRY_FRAME,
        "reject_closed_eyes":     DEFAULT_REJECT_CLOSED_EYES,
        "reject_closed_eyes_all_faces": DEFAULT_REJECT_CLOSED_EYES_ALL_FACES,
        "prefer_sidecar_preview": False,
        # Rejected photos go to the OS Trash bin instead of the per-folder
        # _Trash/ subfolder. Off by default to preserve current behaviour.
        "reject_to_system_trash":     False,
        # EXIF-based instant rejects — off by default (opt-in).
        "reject_reciprocal_rule":     False,
        "reject_above_iso_ceiling":   False,
        # SigLIP content axis: only fires when a face is detected AND
        # background_distraction_score >= background_distraction_ceiling.
        "reject_high_background_distraction": DEFAULT_REJECT_HIGH_BACKGROUND_DISTRACTION,
    })


_init_setting_specs()


# ---------------------------------------------------------------------------
# Helper: read the sidecar-preview preference
# ---------------------------------------------------------------------------


def _is_sidecar_preview_preferred() -> bool:
    """Read the current value of the prefer_sidecar_preview toggle."""
    from backend.database import get_setting
    raw = get_setting("prefer_sidecar_preview")
    if raw is None:
        return False
    return raw.strip().lower() in ("1", "true", "yes", "on")


# RAW_FORMATS needed for the sidecar-pref invalidation logic.
# Source of truth: backend.constants.RAW_FORMATS (zero-dep module).
from backend.constants import RAW_FORMATS as _RAW_FORMATS


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/settings")
def get_settings():
    """
    Return current scoring weight + decision threshold settings with defaults
    resolved.
    """
    all_keys = tuple(_NUMERIC_SETTINGS.keys()) + tuple(_BOOL_SETTINGS.keys())
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
        _personal_model.clear_cache()

    if sidecar_pref_changed:
        with get_db() as conn:
            raw_format_list = ",".join("?" * len(_RAW_FORMATS))
            conn.execute(
                f"UPDATE images SET preview_path = NULL WHERE UPPER(format) IN ({raw_format_list})",
                tuple(_RAW_FORMATS),
            )
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
        "keeps_folder":      overrides.get("keeps_folder")  or f"{src}/_Keeps",
        "maybes_folder":     overrides.get("maybes_folder") or f"{src}/_Maybes",
        "trash_folder":      overrides.get("trash_folder")  or f"{src}/_Trash",
        "keeps_is_default":  "keeps_folder"  not in overrides,
        "maybes_is_default": "maybes_folder" not in overrides,
        "trash_is_default":  "trash_folder"  not in overrides,
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

    fields_set = request.model_fields_set
    decision_for_field = {
        "keeps_folder":  "keep",
        "maybes_folder": "maybe",
        "trash_folder":  "reject",
    }
    for field, decision in decision_for_field.items():
        if field not in fields_set:
            continue
        set_folder_override(src, decision, getattr(request, field))

    return {"status": "ok", **get_folder_settings(src)}
