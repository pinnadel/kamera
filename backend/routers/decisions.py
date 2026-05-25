"""
Decision and auto-cull endpoints.
"""

import json
import logging
import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.database import get_db, get_folder_overrides, get_setting
from backend.file_mover import move_photo, resolve_dest_folder, trash_photo
from backend.state import _personal_model
from phase3_learning.auto_trainer import maybe_train_async
from phase3_learning.feature_extractor import FEATURE_SCHEMA_VERSION, serialize_features

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class DecisionRequest(BaseModel):
    """Body expected by POST /decision."""
    image_id: int
    decision: str  # 'keep' | 'reject' | 'maybe'


class BulkDecisionRequest(BaseModel):
    image_ids: list[int]
    decision: str


class UndoDecisionRequest(BaseModel):
    """Body expected by POST /undo-decision.

    `previous_path` is optional. When supplied, the backend uses it
    verbatim (caller knows the exact origin — handles folder overrides
    and subfolder structure). When omitted, the backend reconstructs it
    from `images.source_folder / images.filename` so per-photo undo can
    work without the frontend remembering pre-decision paths.
    """
    image_id: int
    previous_path: str | None = None


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _parse_shutter_seconds(shutter_str) -> float | None:
    """Parse '1/500', '0.004', '2' etc. → float seconds. Returns None on failure."""
    if not shutter_str:
        return None
    s = str(shutter_str).strip()
    try:
        if '/' in s:
            num, den = s.split('/', 1)
            den_f = float(den)
            return float(num) / den_f if den_f != 0 else None
        return float(s)
    except (ValueError, ZeroDivisionError):
        return None


def _all_faces_eyes_closed(row: dict, face_count: int) -> bool:
    """
    True when every detected face has closed eyes.

    Reads `faces_eyes_open_json` (per-face list of bools, mirrors face_count
    length) when available. Falls back to the conservative interpretation
    when per-face data is missing:
      - `face_count <= 1` → treat as "all closed" (single face whose
        primary `eyes_open` already triggered the caller).
      - `face_count > 1` AND no JSON → treat as "not all closed" so a group
        shot doesn't get killed on the strength of the primary face alone.
        This matches the behaviour of v36-and-older rows analysed before
        per-face data was captured.
    """
    raw = row.get("faces_eyes_open_json")
    if raw:
        try:
            faces = json.loads(raw)
            if isinstance(faces, list) and faces:
                return all(not bool(eo) for eo in faces)
        except (ValueError, TypeError):
            pass  # malformed JSON — fall through to count-based fallback
    return face_count <= 1


def _reject_to_system_trash_enabled() -> bool:
    """Read the `reject_to_system_trash` toggle. Default False — preserve the
    existing _Trash/ folder behaviour for users who want their rejects on
    disk. When True, rejected photos go to the OS system Trash via
    send2trash instead, and can be recovered from the Trash / Recycle Bin."""
    raw = get_setting("reject_to_system_trash")
    if raw is None:
        return False
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _compute_auto_decision(
    row: dict, thresholds: dict | None = None
) -> tuple[str, str]:
    """
    Map stored scores for one image → ('keep' | 'maybe' | 'reject', reason).

    `reason` names the rule that fired:
      closed_eyes · soft_face · iso_ceiling · reciprocal_rule ·
      cluttered_background · personal_keep · personal_maybe ·
      personal_reject · blurry_frame · threshold_keep ·
      threshold_maybe · threshold_reject

    Uses the personal model (GBR delta model) when trained; falls back to
    a threshold heuristic on sharpness/overall_score for untrained projects.
    All cutoffs are user-configurable via Settings → Model → Decision thresholds.

    `thresholds` may be passed in by callers iterating many rows so they only
    pay one SQL roundtrip; otherwise it's read fresh per call.
    """
    from phase1_technical.quality_analyzer import get_decision_thresholds
    t = thresholds or get_decision_thresholds()

    sharpness  = row.get("sharpness_score") or 0
    face       = bool(row.get("face_detected"))
    eyes_open  = row.get("eyes_open")        # 0 = closed, 1 = open, None = unknown
    face_count = row.get("face_count") or 0
    face_sharp = row.get("face_sharpness_score") or 0

    # Instant-reject rules apply in BOTH personal-model and threshold mode.
    if face:
        if t["reject_closed_eyes"] and eyes_open == 0:
            # `reject_closed_eyes_all_faces` only fires when EVERY detected
            # face has closed eyes — protects group shots where one person
            # blinked. Per-face data (faces_eyes_open_json) lands in v37+
            # rows; for older rows or BlazeFace-only detections we fall back
            # to the conservative single-face-only behaviour.
            if t.get("reject_closed_eyes_all_faces"):
                if _all_faces_eyes_closed(row, face_count):
                    return "reject", "closed_eyes"
                # else: at least one face has open eyes — let it through
            else:
                return "reject", "closed_eyes"
        if t["reject_soft_face"] and face_sharp < t["face_sharpness_floor"] and sharpness >= t["fallback_sharpness_floor"]:
            return "reject", "soft_face"

    # EXIF-based instant rejects — opt-in, off by default.
    if t.get("reject_above_iso_ceiling") and t.get("iso_ceiling", 0) > 0:
        iso = row.get("iso")
        if iso is not None and iso > t["iso_ceiling"]:
            return "reject", "iso_ceiling"

    if t.get("reject_reciprocal_rule"):
        focal = row.get("focal_length_mm")
        shutter_s = _parse_shutter_seconds(row.get("shutter_speed"))
        if focal and focal > 0 and shutter_s is not None and shutter_s > 1.0 / focal:
            return "reject", "reciprocal_rule"

    # SigLIP content-axis reject — only fires for face photos so we don't
    # cull clean-background landscapes that happen to score high on the
    # "busy" prompt. The signal is most reliable for subject-vs-background
    # portraits, which is also where a cluttered backdrop hurts most.
    if t.get("reject_high_background_distraction") and face:
        bd_score = row.get("background_distraction_score")
        if bd_score is not None and bd_score >= t.get("background_distraction_ceiling", 0.65):
            return "reject", "cluttered_background"

    # Use the personal model for auto-cull ONLY when it has earned the "ready"
    # tier: at least 50 training samples AND validated to beat the threshold
    # baseline by the bootstrap margin. Below that the model still renders
    # personal_score in the UI (so the user sees what it's learning) but the
    # automated K/M/X assignment falls back to quality thresholds.
    info = _personal_model.info()
    if info.get("model_status") == "ready":
        ps = row.get("personal_score")
        # PR3 boundary routing: when the toggle is on we always go through
        # predict_with_uncertainty so we get the std_dev — even if `ps` was
        # already stored on the row, we still need the per-photo variance.
        # When the toggle is off we keep the legacy single-prediction path.
        # Falls through to the hard-cut decision below when the model has no
        # ensemble (pre-PR3 pickle, std=0.0) or std_dev is below the threshold.
        std = None
        if t.get("auto_cull_uncertain_to_maybe"):
            ps_with_std = _personal_model.predict_with_uncertainty(row)
            if ps_with_std is not None:
                ps, std = ps_with_std
        elif ps is None:
            ps = _personal_model.predict_personal_score(row)

        if ps is not None:
            if std is not None and std >= t.get("uncertainty_threshold", 8.0):
                # Within ±std of either boundary → route to maybe.
                if abs(ps - t["keep_threshold"]) <= std or abs(ps - t["maybe_threshold"]) <= std:
                    return "maybe", "uncertain"
            if ps >= t["keep_threshold"]:
                return "keep", "personal_keep"
            if ps >= t["maybe_threshold"]:
                return "maybe", "personal_maybe"
            return "reject", "personal_reject"

    # threshold fallback — pure sharpness + overall_score logic
    overall = row.get("overall_score") or 0
    if t["reject_blurry_frame"] and sharpness < t["fallback_sharpness_floor"]:
        return "reject", "blurry_frame"
    if overall >= t["fallback_keep"]:
        return "keep", "threshold_keep"
    if overall >= t["fallback_maybe"] and sharpness >= 60:
        return "maybe", "threshold_maybe"
    return "reject", "threshold_reject"


def _apply_decision(image_id: int, decision: str) -> dict:
    """
    Move the file to its destination folder and record the decision in DB.
    Shared by POST /decision and POST /auto-cull so the behaviour is identical.
    Raises HTTPException on 404 / 409 / 500.
    """
    with get_db() as conn:
        image = conn.execute(
            "SELECT * FROM images WHERE id = ?",
            (image_id,),
        ).fetchone()
    if image is None:
        raise HTTPException(status_code=404, detail=f"Image {image_id} not found")

    image_row = dict(image)
    current_path  = image_row["file_path"]
    source_folder = image_row.get("source_folder")

    use_system_trash = decision == "reject" and _reject_to_system_trash_enabled()

    if use_system_trash:
        try:
            new_path = trash_photo(current_path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Trash failed: {exc}")
    else:
        # Default subfolder lives next to the photo at *ingest time* — mirrors
        # the subfolder shape from recursive analysis (a photo in /root/Italy/
        # goes to /root/Italy/_Keeps/ not /root/_Keeps/) while staying correct
        # on re-decisions. Using `Path(current_path).parent` here would nest
        # _Keeps/_Maybes/ when K→M because the photo has already been moved
        # into _Keeps. `images.source_folder` is the durable ingest parent,
        # captured at analysis time (analysis.py:424), so it always points to
        # the original location regardless of intermediate moves.
        photo_parent = source_folder or str(Path(current_path).parent)
        overrides = get_folder_overrides(photo_parent)
        dest_folder = resolve_dest_folder(decision, photo_parent, overrides)

        try:
            new_path = move_photo(current_path, dest_folder)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"File move failed: {exc}")

    with get_db() as conn:
        conn.execute(
            "UPDATE images SET file_path = ? WHERE id = ?",
            (new_path, image_id),
        )
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO decisions
                    (image_id, decision, decided_at)
                VALUES (?, ?, DATETIME('now'))
                """,
                (image_id, decision),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=422, detail="decision must be 'keep', 'reject', or 'maybe'")

        # Durable training corpus — survives Clear Analysis and folder moves.
        # Captured here so the photo's analyzed features are frozen at the
        # exact moment the user judges it. Re-decisions on the same photo
        # (same UUID) replace the old row so taste evolution wins.
        sample_uuid = image_row.get("uuid")
        if sample_uuid:
            try:
                conn.execute(
                    """
                    INSERT INTO training_samples
                        (sample_uuid, decision, features_json, overall_score, schema_version, decided_at)
                    VALUES (?, ?, ?, ?, ?, DATETIME('now'))
                    ON CONFLICT(sample_uuid) DO UPDATE SET
                        decision       = excluded.decision,
                        features_json  = excluded.features_json,
                        overall_score  = excluded.overall_score,
                        schema_version = excluded.schema_version,
                        decided_at     = excluded.decided_at
                    """,
                    (
                        sample_uuid,
                        decision,
                        serialize_features(image_row),
                        image_row.get("overall_score"),
                        FEATURE_SCHEMA_VERSION,
                    ),
                )
            except Exception as exc:
                # Never fail the user's decision over a training-corpus write.
                logger.exception("training_samples write failed for image %s: %s", image_id, exc)
        else:
            # No UUID means the photo was ingested before UUID assignment was
            # active. The decision still saves; the model just won't see this
            # photo as a training sample. Logged so we can spot drift.
            logger.warning(
                "Image %s has no UUID — skipping training_samples write", image_id
            )

    # Fire-and-forget auto-train. Internally gated by sample count and a
    # retrain-delta floor, so the daemon thread only actually runs when
    # there's meaningful new data. Never blocks the user.
    maybe_train_async(_personal_model)

    return {"image_id": image_id, "decision": decision, "new_path": new_path}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/decision")
def save_decision(request: DecisionRequest):
    """
    Record or update a keep/reject/maybe decision and immediately move the file.

    Raises 404 if image_id is unknown.
    Raises 409 if the file is missing from disk.
    Raises 500 for unexpected filesystem errors.
    """
    result = _apply_decision(request.image_id, request.decision)
    return {"status": "ok", **result}


@router.post("/undo-decision")
def undo_decision(request: UndoDecisionRequest):
    """
    Reverse a decision: move the file back to `previous_path`, clear the
    `decisions` row, and drop the durable training_samples row keyed by UUID.

    Errors:
      404 — image_id unknown.
      409 — no decision to undo, file missing on disk (e.g. system Trash),
            or destination parent folder no longer exists.
      500 — unexpected filesystem failure during move.
    """
    image_id      = request.image_id
    previous_path = request.previous_path

    with get_db() as conn:
        image = conn.execute(
            "SELECT * FROM images WHERE id = ?",
            (image_id,),
        ).fetchone()
        if image is None:
            raise HTTPException(status_code=404, detail=f"Image {image_id} not found")
        decision_row = conn.execute(
            "SELECT decision FROM decisions WHERE image_id = ?",
            (image_id,),
        ).fetchone()
        if decision_row is None:
            raise HTTPException(status_code=409, detail="No decision to undo")

    image_row     = dict(image)
    current_path  = image_row["file_path"]
    sample_uuid   = image_row.get("uuid")

    # Per-photo undo path: when the caller didn't supply a previous_path
    # (i.e. the undo isn't replaying a recent action, it's "send this
    # photo home"), move the file UP one level — out of the K/M/R
    # subfolder it was sorted into. This handles three cases correctly:
    #   - Default subfolders: .../source/_Keeps/photo.JPG → .../source/photo.JPG
    #   - Subfolder analysis: .../source/sub/_Keeps/photo.JPG → .../source/sub/photo.JPG
    #   - Custom override:    /any/where/MyKeeps/photo.JPG  → /any/where/photo.JPG
    # Using `images.source_folder + filename` would flatten subfolder structure
    # and break custom-override undo. Filename comes from the live file (not the
    # DB) so any de-dup suffix added by `_claim_path` during the original move
    # comes along for the round trip.
    if previous_path is None:
        filename = Path(current_path).name
        parent_parent = Path(current_path).parent.parent
        previous_path = str(parent_parent / filename)

    # Already at home (e.g. someone manually moved the file back, or path
    # normalization quirks): skip the move and just clear DB state.
    already_home = (
        os.path.normpath(current_path).lower()
        == os.path.normpath(previous_path).lower()
    )

    new_path = current_path
    if not already_home:
        if not Path(current_path).exists():
            raise HTTPException(
                status_code=409,
                detail="File missing — cannot undo (may be in system Trash)",
            )
        prev_parent = Path(previous_path).parent
        if not prev_parent.exists():
            raise HTTPException(
                status_code=409,
                detail=f"Source folder no longer exists: {prev_parent}",
            )
        try:
            new_path = move_photo(current_path, prev_parent)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"File move failed: {exc}")

    with get_db() as conn:
        conn.execute("DELETE FROM decisions WHERE image_id = ?", (image_id,))
        conn.execute(
            "UPDATE images SET file_path = ? WHERE id = ?",
            (new_path, image_id),
        )
        # Remove the durable training_samples row so the model doesn't keep
        # learning from a judgment the user took back.
        if sample_uuid:
            try:
                conn.execute(
                    "DELETE FROM training_samples WHERE sample_uuid = ?",
                    (sample_uuid,),
                )
            except Exception as exc:
                logger.exception(
                    "training_samples delete failed for image %s: %s",
                    image_id, exc,
                )

    # Retrain the personal model so it stops weighting the undone sample.
    # Same fire-and-forget pattern as _apply_decision — gated internally on
    # sample count + retrain-delta floor, never blocks the user.
    maybe_train_async(_personal_model)

    return {"status": "ok", "image_id": image_id, "decision": None, "new_path": new_path}


@router.post("/bulk-decision")
def bulk_decision(request: BulkDecisionRequest):
    """
    Apply one decision to a list of image IDs — batch version of POST /decision.

    Moves each file and records the decision. Errors on individual files are
    collected and returned rather than aborting the whole batch.
    Used by "Reject all remaining maybes" in the frontend.
    """
    moved, errors = 0, []
    for image_id in request.image_ids:
        try:
            _apply_decision(image_id, request.decision)
            moved += 1
        except HTTPException as exc:
            errors.append({"id": image_id, "error": exc.detail})
    return {"moved": moved, "errors": errors}


@router.post("/trash-rejects")
def trash_rejects():
    """
    Send all rejected files to the system Trash (recoverable via Trash bin).

    For each image with decision='reject':
      1. Moves the RAW file to the system Trash via send2trash.
      2. Deletes the DB row (file is gone from our managed set).

    Files that are already missing from disk are silently skipped.
    """
    from send2trash import send2trash as _send2trash

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT i.id, i.file_path
            FROM images i
            INNER JOIN decisions d ON d.image_id = i.id
            WHERE d.decision = 'reject'
            """
        ).fetchall()

    trashed: int = 0
    errors: list[dict] = []
    ids_to_delete: list[int] = []

    for row in rows:
        file_path = row["file_path"]
        try:
            if Path(file_path).exists():
                _send2trash(file_path)
            ids_to_delete.append(row["id"])
            trashed += 1
        except Exception as exc:
            errors.append({"file": file_path, "error": str(exc)})

    if ids_to_delete:
        placeholders = ",".join("?" * len(ids_to_delete))
        with get_db() as conn:
            conn.execute(
                f"DELETE FROM decisions WHERE image_id IN ({placeholders})",
                ids_to_delete,
            )
            conn.execute(
                f"DELETE FROM images WHERE id IN ({placeholders})",
                ids_to_delete,
            )

    return {"trashed": trashed, "errors": errors}


@router.get("/auto-cull/preview")
def auto_cull_preview(source_folder: str | None = None):
    """
    Dry-run: compute which K/M/X each undecided photo would receive.

    Uses the personal model when trained (≥20 decisions); falls back to
    the built-in threshold logic. No files are moved and no DB rows are written.
    """
    with get_db() as conn:
        if source_folder is not None:
            rows = conn.execute(
                """
                SELECT i.* FROM images i
                LEFT JOIN decisions d ON d.image_id = i.id
                WHERE i.analysis_status = 'done' AND d.decision IS NULL
                  AND i.source_folder = ?
                """,
                (source_folder,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT i.* FROM images i
                LEFT JOIN decisions d ON d.image_id = i.id
                WHERE i.analysis_status = 'done' AND d.decision IS NULL
                """
            ).fetchall()

    rows_dicts = [dict(r) for r in rows]

    if _personal_model.ready and rows_dicts:
        personal_scores = _personal_model.predict_batch(rows_dicts)
        for row, ps in zip(rows_dicts, personal_scores):
            row["personal_score"] = ps

    from phase1_technical.quality_analyzer import get_decision_thresholds
    thresholds = get_decision_thresholds()

    counts: dict[str, int] = {"keep": 0, "maybe": 0, "reject": 0}
    # Bucket every reject reason emitted by _compute_auto_decision into the
    # UI's coarser categories. iso_ceiling + reciprocal_rule + threshold_reject
    # + personal_reject all land in low_score so the modal copy stays simple.
    _REASON_BUCKET = {
        "closed_eyes":          "closed_eyes",
        "soft_face":            "soft_face",
        "blurry_frame":         "blurry_frame",
        "cluttered_background": "cluttered_background",
        "iso_ceiling":          "low_score",
        "reciprocal_rule":      "low_score",
        "personal_reject":      "low_score",
        "threshold_reject":     "low_score",
    }
    rule_counts: dict[str, int] = {
        "closed_eyes": 0,
        "soft_face": 0,
        "blurry_frame": 0,
        "cluttered_background": 0,
        "low_score": 0,
        # PR3: uncertain photos are routed to "maybe" (not rejected) so we
        # count them outside the reject-only bucket below.
        "uncertain": 0,
    }
    # Same pre-compute / post-pass shape as /auto-cull so preview counts
    # match what the actual run would do. See run_auto_cull for the full
    # rationale on the burst keep-guarantee policy.
    decisions: dict[int, str] = {}
    preview_reasons: dict[int, str] = {}
    for row in rows_dicts:
        decision, reason = _compute_auto_decision(row, thresholds)
        decisions[row["id"]] = decision
        preview_reasons[row["id"]] = reason

    from backend.group_scoring import top_n_candidates
    promotions_count = 0
    bursts_preview = _group_undecided_into_bursts(rows_dicts)
    for burst in bursts_preview:
        if len(burst) < 2:
            continue
        burst_ids = [m["id"] for m in burst]
        burst_decisions = {i: decisions[i] for i in burst_ids if i in decisions}
        if not burst_decisions or any(d == "keep" for d in burst_decisions.values()):
            continue
        burst_reasons = {i: preview_reasons.get(i) for i in burst_ids}
        if all(r in _PHYSICAL_REJECT_REASONS for r in burst_reasons.values() if r is not None):
            continue
        winner = top_n_candidates(burst, 1)[0]
        winner_id = winner["id"]
        if decisions.get(winner_id) == "maybe":
            continue
        decisions[winner_id] = "maybe"
        promotions_count += 1

    for row in rows_dicts:
        decision = decisions[row["id"]]
        # Reason still describes the original auto-decision logic — the
        # promotion overrides decision but doesn't change why the photo
        # was scored the way it was. Rule_counts bucket uses the original.
        original_reason = preview_reasons[row["id"]]
        counts[decision] += 1
        if original_reason == "uncertain":
            rule_counts["uncertain"] += 1
        elif decision == "reject":
            # After the post-pass, only photos that are STILL reject get
            # bucketed — the promoted winners count as maybe now.
            rule_counts[_REASON_BUCKET.get(original_reason, "low_score")] += 1

    info = _personal_model.info()
    using_personal = info.get("model_status") == "ready"
    return {
        "scoring_mode": "personal" if using_personal else "threshold",
        "scoring_info": (
            f"trained on {_personal_model.training_size} decisions"
            if using_personal
            else (
                f"model still learning ({_personal_model.training_size}/50 samples) — using quality thresholds"
                if _personal_model.ready
                else "quality thresholds"
            )
        ),
        "counts": counts,
        "total": len(rows_dicts),
        "rule_breakdown": rule_counts,
        # Count of bursts where the best member was rescued from reject →
        # maybe by the keep-guarantee post-pass. Surfaces "your burst would
        # otherwise have lost the moment" so the AutoCullModal can flag it.
        "promotions": promotions_count,
    }


# Reject reasons we treat as "the photo is physically unrecoverable" — when
# every member of a burst hits one of these, promoting the best one to Maybe
# would just put a guaranteed-bad photo in front of the user. Subjective
# reasons (closed_eyes, soft_face, cluttered_background) DO get rescued
# because they can be salvageable in context (one photo of the group might
# be the only frame the moment happened in).
_PHYSICAL_REJECT_REASONS = frozenset({"iso_ceiling", "reciprocal_rule", "blurry_frame"})


def _group_undecided_into_bursts(rows: list[dict], time_gap_seconds: float = 60.0,
                                 threshold: float = 0.90) -> list[list[dict]]:
    """Cluster the rows the same way /similarity-groups does so the burst
    keep-guarantee operates on the same groupings the user sees in the UI.

    Photos without an embedding (Phase 2 hasn't completed for them yet) are
    returned as singleton groups so the caller can still iterate uniformly.
    """
    from phase2_quality.similarity_scorer import (
        group_by_similarity as _group_by_similarity,
        json_to_embedding as _json_to_embedding,
    )

    by_id = {r["id"]: r for r in rows}
    items: list[tuple[int, list[float], float | None, str | None]] = []
    singletons: list[list[dict]] = []
    for row in rows:
        vec = _json_to_embedding(row.get("embedding"))
        if vec is None:
            singletons.append([row])
            continue
        items.append((
            row["id"], vec,
            _shot_at_to_unix(row.get("shot_at")),
            row.get("manual_group_id"),
        ))

    raw_groups = _group_by_similarity(
        items, threshold=threshold, time_gap_seconds=time_gap_seconds,
    ) if items else []

    grouped: list[list[dict]] = []
    for ids in raw_groups:
        members = [by_id[i] for i in ids if i in by_id]
        if members:
            grouped.append(members)
    return grouped + singletons


def _shot_at_to_unix(shot_at) -> float | None:
    """Tiny copy of the helper in routers/model.py so this module isn't
    forced into a circular import. Returns a unix timestamp or None when the
    field is missing / unparseable."""
    if shot_at is None:
        return None
    try:
        from datetime import datetime
        # Accept ISO 8601 with or without microseconds. SQLite stores
        # "YYYY-MM-DD HH:MM:SS" or "YYYY-MM-DDTHH:MM:SS"; both parse via
        # fromisoformat after a tiny tolerance fix.
        s = str(shot_at).strip()
        if " " in s and "T" not in s:
            s = s.replace(" ", "T", 1)
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


@router.post("/auto-cull")
def run_auto_cull(source_folder: str | None = None):
    """
    Assign K/M/X to every undecided analyzed photo and move their files.

    Uses the same scoring logic as GET /auto-cull/preview, with one
    burst-aware post-pass: when an entire similarity group ends up with
    zero Keeps AND none of the rejects were physical-unrecoverable
    (ISO ceiling / reciprocal-rule / blurry-frame), the best photo in
    the burst is promoted to Maybe so the user always has a chance to
    rescue a moment. Promotion uses the same priority as the group hero
    selector (face_sharpness → eyes_open → sharpness → IQA → aesthetic
    → overall_score). See backend.group_scoring.top_n_candidates.
    """
    with get_db() as conn:
        if source_folder is not None:
            rows = conn.execute(
                """
                SELECT i.* FROM images i
                LEFT JOIN decisions d ON d.image_id = i.id
                WHERE i.analysis_status = 'done' AND d.decision IS NULL
                  AND i.source_folder = ?
                """,
                (source_folder,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT i.* FROM images i
                LEFT JOIN decisions d ON d.image_id = i.id
                WHERE i.analysis_status = 'done' AND d.decision IS NULL
                """
            ).fetchall()

    rows_dicts = [dict(r) for r in rows]

    if _personal_model.ready and rows_dicts:
        personal_scores = _personal_model.predict_batch(rows_dicts)
        for row, ps in zip(rows_dicts, personal_scores):
            row["personal_score"] = ps

    from phase1_technical.quality_analyzer import get_decision_thresholds
    thresholds = get_decision_thresholds()

    # Phase 1: compute decisions for every row WITHOUT applying them yet.
    # Capturing reasons here is what lets the burst post-pass know whether
    # each reject is physical (don't rescue) or subjective (do rescue).
    decisions: dict[int, str] = {}
    reasons:   dict[int, str] = {}
    for row in rows_dicts:
        decision, reason = _compute_auto_decision(row, thresholds)
        decisions[row["id"]] = decision
        reasons[row["id"]] = reason

    # Phase 2: burst keep-guarantee post-pass. Walks each similarity group;
    # if it has ≥2 members, zero Keeps, and at least one member has a
    # subjective reject reason (not iso_ceiling / reciprocal_rule /
    # blurry_frame), promote the best-by-priority member to Maybe. Tracks
    # the promotions so the response surfaces them for any future UI nudge.
    from backend.group_scoring import top_n_candidates
    promotions: list[dict] = []
    bursts = _group_undecided_into_bursts(rows_dicts)
    for burst in bursts:
        if len(burst) < 2:
            continue
        burst_ids = [m["id"] for m in burst]
        burst_decisions = {i: decisions[i] for i in burst_ids if i in decisions}
        if not burst_decisions or any(d == "keep" for d in burst_decisions.values()):
            continue
        burst_reasons = {i: reasons.get(i) for i in burst_ids}
        # Skip when every reject is physical-unrecoverable — promoting one
        # would just hand the user a guaranteed-bad photo.
        if all(r in _PHYSICAL_REJECT_REASONS for r in burst_reasons.values() if r is not None):
            continue
        # Pick the best member by the canonical priority. top_n_candidates
        # returns descending, so [0] is the winner.
        winner = top_n_candidates(burst, 1)[0]
        winner_id = winner["id"]
        current = decisions.get(winner_id)
        if current == "maybe":
            # Already maybe — no action needed, but still record so we can
            # report "this burst was almost lost" in the response.
            continue
        decisions[winner_id] = "maybe"
        promotions.append({
            "image_id": winner_id,
            "from": current,
            "to": "maybe",
            "burst_size": len(burst),
            "reason": "burst_keep_guarantee",
        })

    # Phase 3: apply the decisions (now post-promotion).
    counts: dict[str, int] = {"keep": 0, "maybe": 0, "reject": 0}
    errors: list[dict] = []
    for row in rows_dicts:
        decision = decisions[row["id"]]
        try:
            _apply_decision(row["id"], decision)
            counts[decision] += 1
        except HTTPException as exc:
            errors.append({"id": row["id"], "error": exc.detail})

    info = _personal_model.info()
    using_personal = info.get("model_status") == "ready"
    return {
        "scoring_mode": "personal" if using_personal else "threshold",
        "counts": counts,
        "total": len(rows_dicts),
        "errors": errors,
        # New: surfaces how many bursts had their best photo rescued from
        # all-reject to maybe. Frontend can use this for a one-line toast.
        "promotions": promotions,
    }
