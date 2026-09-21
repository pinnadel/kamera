"""
Decision and auto-cull endpoints.
"""

import json
import logging
import os
import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.constants import is_raw_format, companion_stem_matches, shot_at_to_unix as _shot_at_to_unix
from backend.database import get_db, get_folder_overrides, get_setting
from backend.constants import is_photos_source_folder
from backend.sources import sink_for
from backend.sources.folder import _reject_to_system_trash_enabled
from backend.file_mover import _DECISION_FOLDERS, resolve_dest_folder
from backend.routers.settings import get_raw_jpeg_display_mode
from backend.state import _autocull_progress, _autocull_stop_event, _decision_move_lock, model_for
from phase3_learning.auto_trainer import _count_samples, maybe_train_async
from phase3_learning.personal_model import min_decisions_for
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


class MarkReviewedRequest(BaseModel):
    """Body expected by POST /mark-reviewed."""
    image_id: int


class BulkMarkReviewedRequest(BaseModel):
    """Body expected by POST /bulk-mark-reviewed."""
    image_ids: list[int]


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


def _compute_auto_decision(
    row: dict, thresholds: dict | None = None
) -> tuple[str, str]:
    """
    Map stored scores for one image → ('keep' | 'maybe' | 'reject', reason).

    `reason` names the rule that fired:
      closed_eyes · soft_face · low_iqa · iso_ceiling · reciprocal_rule ·
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
    corpus = row.get("source_type") or "folder"
    t = thresholds or get_decision_thresholds(corpus)

    sharpness  = row.get("sharpness_score") or 0
    face       = bool(row.get("face_detected"))
    eyes_open  = row.get("eyes_open")        # 0 = closed, 1 = open, None = unknown
    face_count = row.get("face_count") or 0
    face_sharp = row.get("face_sharpness_score") or 0

    # The soft-face rule asks "is the FRAME sharp while the face is soft?" — on
    # the Photos corpus `sharpness_score` can no longer answer that, because it
    # is min(frame, face) since the edge-width switch, so a soft face drags the
    # frame score down with it and the precondition below could never be true.
    # `edge_width_px` is the frame-only measurement, on the same 0-100 scale.
    # Folder is untouched: its face sharpness is still Laplacian variance, and
    # judging it against an edge-width frame score would mix two scales.
    frame_sharp = sharpness
    if corpus == "photos" and row.get("edge_width_px") is not None:
        from phase1_technical.edge_sharpness import width_to_score
        frame_sharp = width_to_score(row["edge_width_px"])

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
        if t["reject_soft_face"] and face_sharp < t["face_sharpness_floor"] and frame_sharp >= t["fallback_sharpness_floor"]:
            return "reject", "soft_face"

    # Low-IQA instant reject — catches the false-sharp failure mode the Phase-1
    # sharpness metric misses (a defocused frame full of fine micro-texture
    # scores high on sharpness but low on TOPIQ-NR perceptual quality). Fires in
    # both personal-model and threshold mode. Skipped when iqa_score is NULL
    # (older rows / analysis errors) so a missing signal never culls a photo.
    if t.get("reject_low_iqa"):
        iqa = row.get("iqa_score")
        if iqa is not None and iqa < t.get("iqa_floor", 30.0):
            return "reject", "low_iqa"

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
    model = model_for(corpus)
    info = model.info()
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
            ps_with_std = model.predict_with_uncertainty(row)
            if ps_with_std is not None:
                ps, std = ps_with_std
        elif ps is None:
            ps = model.predict_personal_score(row)

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


def _find_companion_image_id(image_row: dict) -> int | None:
    """
    Find the paired RAW/processed row for `image_row` (same source_folder,
    same filename stem, opposite side of the RAW/processed split).

    Used to mirror K/M/R decisions onto the hidden half of a pair when
    raw_jpeg_display_mode isn't 'both' — see _apply_decision. Returns None
    if no such row exists (the photo is an orphan, or has more than one
    same-stem candidate, which we don't try to disambiguate).
    """
    source_folder = image_row.get("source_folder")
    filename = image_row["filename"]
    want_raw = not is_raw_format(image_row.get("format"))  # companion is the OTHER side

    with get_db() as conn:
        candidates = conn.execute(
            "SELECT id, filename, format FROM images WHERE source_folder IS ? AND id != ?",
            (source_folder, image_row["id"]),
        ).fetchall()

    matches = [
        dict(c) for c in candidates
        if companion_stem_matches(filename, c["filename"])
        and is_raw_format(c["format"]) == want_raw
    ]
    if len(matches) != 1:
        return None
    return matches[0]["id"]


def _companion_is_skipped_stub(companion_id: int) -> bool:
    """
    True if `companion_id` is an unscored analysis_mode=jpeg_priority row
    (analysis_status='skipped') — a RAW whose processed sibling got the full
    analysis and whose own row exists only so a decision can mirror onto it
    and move it. It never reaches analysis_status='done', so it never gets an
    independent verdict of its own; mirroring is the only path to a decision.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT analysis_status FROM images WHERE id = ?", (companion_id,)
        ).fetchone()
    return row is not None and row["analysis_status"] == "skipped"


def _companion_is_hidden(companion_id: int) -> bool:
    """
    True if `companion_id` isn't independently visible/reviewable, so a
    decision/undo on its sibling should mirror onto it instead of leaving it
    untouched. Two cases: raw_jpeg_display_mode is hiding it from the grid,
    or it's a skipped analysis_mode=jpeg_priority stub (see
    _companion_is_skipped_stub) that can never be independently judged.
    """
    if get_raw_jpeg_display_mode() != "both":
        return True
    return _companion_is_skipped_stub(companion_id)


def _apply_decision(
    image_id: int,
    decision: str,
    is_auto: bool = False,
    reason: str | None = None,
    _mirror: bool = True,
    _train: bool = True,
) -> dict:
    """
    Move the file to its destination folder and record the decision in DB.
    Shared by POST /decision and POST /auto-cull so the file-move + decisions
    write behave identically.

    `is_auto` distinguishes a system-made auto-cull decision from a manual
    K/M/X by the user. Only MANUAL decisions feed the personal model's
    training corpus — auto-cull decisions are deliberately excluded so the
    model never learns from its own output (a self-reinforcement loop). When
    the user later manually changes (or confirms) an auto-cull decision, that
    manual call writes the training sample as normal.

    `reason` names why this decision was made (e.g. 'manual', 'personal_keep',
    'closed_eyes', 'burst_keep_guarantee', 'uncertain'). Written to
    decisions.reason (current-state, overwritten on re-decision, cleared on
    Clear Analysis) AND appended to auto_decision_log (durable, survives
    Clear Analysis — see database.py migration v50). Callers that don't know
    a specific rule name should still pass something meaningful ('manual' for
    plain user K/M/X) so the log stays queryable.

    `_mirror` / `_train` are internal-only (never set by callers outside this
    module). When raw_jpeg_display_mode hides one half of a RAW+JPEG pair,
    the visible photo's decision mirrors onto its companion so both files
    move together and the DB stays consistent — but the companion's own
    feature vector is never written to training_samples (RAW vs. processed
    sharpness/exposure differ systematically for the same shot, e.g. an
    unprocessed RAF scoring ~46 vs. its camera JPEG scoring ~99 — mirroring
    that pair into training would teach the model contradictory sharpness
    labels for what is compositionally one photo). `_mirror=False` on the
    recursive companion call prevents it from trying to mirror back;
    `_train=False` forces the training write off for that companion call
    regardless of is_auto.

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

    # Where this decision is committed depends on where the photo came from:
    # a folder move, or a keyword on an Apple Photos asset. See
    # backend/sources/base.py.
    sink = sink_for(image_row)
    sink.preflight()

    # Hold _decision_move_lock across "file physically moved" → "images.file_path
    # updated to match". POST /sync-folder checks Path(file_path).exists() to
    # find rows to drop; without this lock it can read the OLD path in that
    # exact window, see nothing there (the file's already at its new path),
    # and delete a row for a photo that's perfectly safe on disk. See the
    # incident note on _decision_move_lock in backend/state.py.
    with _decision_move_lock:
        new_path = sink.apply(image_row, decision, is_auto=is_auto)

        with get_db() as conn:
            conn.execute(
                "UPDATE images SET file_path = ? WHERE id = ?",
                (new_path, image_id),
            )
    # Lock released — file_path on disk and in the DB agree again, so
    # sync-folder can safely run its existence check from here on. The
    # remaining writes (decisions, training_samples) don't touch file_path
    # and don't need the lock.
    with get_db() as conn:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO decisions
                    (image_id, decision, reason, decided_at)
                VALUES (?, ?, ?, DATETIME('now'))
                """,
                (image_id, decision, reason),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=422, detail="decision must be 'keep', 'reject', or 'maybe'")

        # Durable audit log — never overwritten or deleted by Clear Analysis /
        # tab close (unlike the decisions row above). Answers "how often does
        # each auto-cull path fire" and "how often do I override an
        # auto-decision" (join on sample_uuid across is_auto=1 then is_auto=0
        # rows). Best-effort: never fail the user's decision over a log write.
        try:
            conn.execute(
                """
                INSERT INTO auto_decision_log
                    (sample_uuid, image_id, decision, reason, is_auto, decided_at)
                VALUES (?, ?, ?, ?, ?, DATETIME('now'))
                """,
                (image_row.get("uuid"), image_id, decision, reason, int(is_auto)),
            )
        except Exception as exc:
            logger.exception("auto_decision_log write failed for image %s: %s", image_id, exc)

        # Durable training corpus — survives Clear Analysis and folder moves.
        # Captured here so the photo's analyzed features are frozen at the
        # exact moment the user judges it. Re-decisions on the same photo
        # (same UUID) replace the old row so taste evolution wins.
        #
        # Auto-cull decisions are NEVER written here: the model must learn
        # only from the user's own judgments, not from its own predictions.
        # A later manual decision on the same photo (override or confirm)
        # comes through with is_auto=False and INSERTs the sample then.
        #
        # `corpus` partitions the training set by source_type. An iPhone HEIC
        # out of a Photos album and a RAF off the camera score on entirely
        # different sharpness / aperture / iso distributions, so folding them
        # into one GBR would teach it contradictory labels — the same reason
        # the RAW+JPEG companion mirror passes _train=False.
        sample_uuid = image_row.get("uuid")
        if is_auto or not _train:
            pass  # system decision, or a mirrored companion write — not a training sample
        elif sample_uuid:
            try:
                conn.execute(
                    """
                    INSERT INTO training_samples
                        (sample_uuid, decision, features_json, overall_score, schema_version, corpus, decided_at)
                    VALUES (?, ?, ?, ?, ?, ?, DATETIME('now'))
                    ON CONFLICT(sample_uuid) DO UPDATE SET
                        decision       = excluded.decision,
                        features_json  = excluded.features_json,
                        overall_score  = excluded.overall_score,
                        schema_version = excluded.schema_version,
                        corpus         = excluded.corpus,
                        decided_at     = excluded.decided_at
                    """,
                    (
                        sample_uuid,
                        decision,
                        serialize_features(image_row),
                        image_row.get("overall_score"),
                        FEATURE_SCHEMA_VERSION,
                        image_row.get("source_type") or "folder",
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

    # Mirror onto the paired RAW/processed companion when it's not being
    # independently reviewed — either the display setting is hiding one side,
    # or the companion is an unscored analysis_mode=jpeg_priority row (skipped
    # RAW rows can never be independently judged, so mirroring is the only
    # way they ever get moved; see settings.py::analysis_mode). The user only
    # saw and judged one tile, but both files represent the same shot and
    # should carry the same decision. Best-effort: a companion-move failure
    # must never fail the visible photo's own (already-committed) decision.
    # Gated on the sink: a RAW/JPEG pair in a FOLDER is two files that must move
    # together. In a Photos album there is nothing to keep in sync — and
    # _find_companion_image_id matches on source_folder + filename stem, so two
    # same-stem assets in one album would pair up and mirror a keyword onto a
    # photo the user never judged.
    if _mirror and sink.supports_companion_mirror:
        companion_id = _find_companion_image_id(image_row)
        if companion_id is not None and _companion_is_hidden(companion_id):
            try:
                _apply_decision(companion_id, decision, is_auto=is_auto, reason=reason, _mirror=False, _train=False)
            except HTTPException as exc:
                logger.warning(
                    "Companion mirror failed for image %s → %s: %s",
                    image_id, companion_id, exc.detail,
                )

    # Fire-and-forget auto-train, aimed at the corpus this photo belongs to.
    # Internally gated by sample count and a retrain-delta floor, so the
    # daemon thread only actually runs when there's meaningful new data.
    # Never blocks the user.
    maybe_train_async(model_for(image_row.get("source_type") or "folder"))

    return {"image_id": image_id, "decision": decision, "new_path": new_path}


def _move_to_reviewed(image_id: int, _mirror: bool = True) -> dict:
    """
    Move an already-kept photo from `_Keeps` into `_Keeps/_Keeps_reviewed`.

    The human counterpart to Piece 1's automatic routing: for photos that
    landed in `_Keeps` via auto-cull, this is the "I looked, this is
    correct" action that releases the photo to Provenance's archive watch.
    Requires the photo's current decision to be 'keep' and its current
    `file_path` to sit directly under a `_Keeps` folder (not already under
    `_Keeps_reviewed`) — both are 400s, not silent no-ops, since calling
    this on the wrong photo is a caller bug, not a race.

    Also stamps `decisions.reason = 'manual'` so the photo counts as
    human-confirmed everywhere the UI asks that question — no training
    sample is written (see the inline note below).

    Mirrors onto a hidden RAW/JPEG companion the same way _apply_decision
    does, so both files of a pair release to Provenance together.
    """
    with get_db() as conn:
        image = conn.execute(
            "SELECT * FROM images WHERE id = ?", (image_id,)
        ).fetchone()
    if image is None:
        raise HTTPException(status_code=404, detail=f"Image {image_id} not found")

    image_row = dict(image)

    sink = sink_for(image_row)
    if not sink.supports_mark_reviewed:
        # No _Keeps folder exists to promote out of. A Photos album records the
        # decision as a keyword; there is no second location to move it to.
        raise HTTPException(
            status_code=400,
            detail="Mark reviewed is not available for this source",
        )
    sink.preflight()

    with get_db() as conn:
        decision_row = conn.execute(
            "SELECT decision FROM decisions WHERE image_id = ?", (image_id,)
        ).fetchone()
    if decision_row is None or decision_row["decision"] != "keep":
        raise HTTPException(status_code=400, detail="Photo is not decided 'keep'")

    with _decision_move_lock:
        new_path = sink.mark_reviewed(image_row)

        with get_db() as conn:
            conn.execute(
                "UPDATE images SET file_path = ? WHERE id = ?",
                (new_path, image_id),
            )
            # Stamp the decision as human-seen. 'manual' is the frontend's
            # single source of truth for "I already looked at this photo"
            # (ui/format.js::isUnconfirmed), so the "only unconfirmed" grid
            # filter can hide it in every bucket without re-deriving the
            # answer from the file path. Deliberately does NOT write a
            # training sample: marking reviewed means "I looked", not "here
            # is a label" — a bulk review must never feed the model its own
            # auto-cull output (see _apply_decision's is_auto note). The
            # pre-review reason ('personal_keep', etc.) is not lost; it lives
            # on in the durable auto_decision_log row written back when the
            # auto-cull decided this photo.
            conn.execute(
                "UPDATE decisions SET reason = 'manual' WHERE image_id = ?",
                (image_id,),
            )

    # Gated on the sink: a RAW/JPEG pair in a FOLDER is two files that must move
    # together. In a Photos album there is nothing to keep in sync — and
    # _find_companion_image_id matches on source_folder + filename stem, so two
    # same-stem assets in one album would pair up and mirror a keyword onto a
    # photo the user never judged.
    if _mirror and sink.supports_companion_mirror:
        companion_id = _find_companion_image_id(image_row)
        if companion_id is not None and _companion_is_hidden(companion_id):
            try:
                _move_to_reviewed(companion_id, _mirror=False)
            except HTTPException as exc:
                logger.warning(
                    "Mark-reviewed companion mirror failed for image %s → %s: %s",
                    image_id, companion_id, exc.detail,
                )

    return {"image_id": image_id, "new_path": new_path}


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
    result = _apply_decision(request.image_id, request.decision, reason="manual")
    return {"status": "ok", **result}


@router.post("/mark-reviewed")
def mark_reviewed(request: MarkReviewedRequest):
    """
    Release a kept photo from `_Keeps` into `_Keeps/_Keeps_reviewed`.

    For photos that landed in `_Keeps` via auto-cull (a manual keep already
    goes straight to `_Keeps_reviewed` — see /decision), this is the "I
    looked, this is correct" action. Provenance watches `_Keeps_reviewed`
    and archives from there.

    Raises 400 if the photo isn't decided 'keep' or isn't currently under
    `_Keeps` (already reviewed, or was never kept).
    Raises 404 if image_id is unknown. 409 if the file is missing from disk.
    """
    result = _move_to_reviewed(request.image_id)
    return {"status": "ok", **result}


def _apply_undo(image_id: int, previous_path: str | None, _mirror: bool = True) -> dict:
    """
    Reverse a decision: move the file back to `previous_path`, clear the
    `decisions` row, and drop the durable training_samples row keyed by UUID.

    `_mirror` is internal-only: when raw_jpeg_display_mode hides one half of
    a RAW+JPEG pair, undoing the visible photo's decision also undoes its
    companion's mirrored decision, keeping the pair in sync (they were
    decided together by _apply_decision, so they're undone together too).
    The recursive companion call always passes previous_path=None — the
    caller's previous_path is scoped to the *visible* photo's filename and
    would be wrong for the companion, so we fall back to the same
    parent-parent reconstruction used for photos with no supplied path.

    Errors:
      404 — image_id unknown.
      409 — no decision to undo, file missing on disk (e.g. system Trash),
            or destination parent folder no longer exists.
      500 — unexpected filesystem failure during move.
    """
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

    sink = sink_for(image_row)
    sink.preflight()

    # Same move-then-update race as _apply_decision — see the incident note
    # on _decision_move_lock in backend/state.py.
    with _decision_move_lock:
        new_path = sink.undo(image_row, previous_path)

        with get_db() as conn:
            conn.execute(
                "UPDATE images SET file_path = ? WHERE id = ?",
                (new_path, image_id),
            )

    with get_db() as conn:
        conn.execute("DELETE FROM decisions WHERE image_id = ?", (image_id,))
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

    # Mirror the undo onto the paired RAW/processed companion, if any —
    # symmetric with the mirrored decision made in _apply_decision (same
    # companion-hidden test: display setting OR an unscored jpeg_priority
    # skip row). Best-effort: a companion with nothing to undo (409) or a
    # missing companion file is silently skipped rather than failing this undo.
    # Gated on the sink: a RAW/JPEG pair in a FOLDER is two files that must move
    # together. In a Photos album there is nothing to keep in sync — and
    # _find_companion_image_id matches on source_folder + filename stem, so two
    # same-stem assets in one album would pair up and mirror a keyword onto a
    # photo the user never judged.
    if _mirror and sink.supports_companion_mirror:
        companion_id = _find_companion_image_id(image_row)
        if companion_id is not None and _companion_is_hidden(companion_id):
            try:
                _apply_undo(companion_id, None, _mirror=False)
            except HTTPException as exc:
                logger.warning(
                    "Companion undo-mirror failed for image %s → %s: %s",
                    image_id, companion_id, exc.detail,
                )

    # Retrain the personal model so it stops weighting the undone sample.
    # Same fire-and-forget pattern as _apply_decision — gated internally on
    # sample count + retrain-delta floor, never blocks the user.
    maybe_train_async(model_for(image_row.get("source_type") or "folder"))

    return {"status": "ok", "image_id": image_id, "decision": None, "new_path": new_path}


@router.post("/undo-decision")
def undo_decision(request: UndoDecisionRequest):
    """
    Reverse a decision and immediately move the file back.

    See _apply_undo for the full contract (also mirrors onto a RAW/JPEG
    companion when raw_jpeg_display_mode hides one half of the pair).
    """
    return _apply_undo(request.image_id, request.previous_path)


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
            _apply_decision(image_id, request.decision, reason="manual")
            moved += 1
        except HTTPException as exc:
            errors.append({"id": image_id, "error": exc.detail})
    return {"moved": moved, "errors": errors}


@router.post("/bulk-mark-reviewed")
def bulk_mark_reviewed(request: BulkMarkReviewedRequest):
    """
    Release a batch of kept photos from `_Keeps` into `_Keeps/_Keeps_reviewed`
    — batch version of POST /mark-reviewed, for "mark all reviewed" over a
    filtered Keeps view.

    Idempotent: a photo already in `_Keeps_reviewed` is silently skipped
    (not counted as an error) so re-running over a view that mixes
    already-reviewed and not-yet-reviewed photos is safe. Photos that
    aren't decided 'keep' at all, or that fail to move, are reported in
    `errors`.
    """
    moved, skipped, errors = 0, 0, []
    for image_id in request.image_ids:
        try:
            _move_to_reviewed(image_id)
            moved += 1
        except HTTPException as exc:
            if exc.status_code == 400 and "already in _Keeps_reviewed" in exc.detail:
                skipped += 1
            else:
                errors.append({"id": image_id, "error": exc.detail})
    return {"moved": moved, "skipped": skipped, "errors": errors}


class TrashRejectsRequest(BaseModel):
    """Which tab's rejects to sweep.

    Required, and deliberately so. This endpoint used to take no body and
    swept every reject in the database, while the button that calls it sits in
    a bar labelled with the ACTIVE TAB's reject count — so pressing it in tab B
    trashed tab A's rejects under a number that described neither. Making the
    scope explicit means the accident cannot come back by omission: a caller
    that forgets it gets a 422, not a wider sweep.
    """

    source_folder: str


@router.post("/trash-rejects")
def trash_rejects(request: TrashRejectsRequest):
    """
    Send one tab's rejected files to the system Trash (recoverable via Trash bin).

    For each image with decision='reject', route through file_mover.trash_photo
    so the send2trash call — and its .HIF sidecar handling — is identical to a
    single per-photo R press with reject_to_system_trash on. The DB row is
    deleted ONLY when trash_photo confirms the file reached the Trash.

    Failure honesty (data-loss adjacent, so we never lie about success):
      - A file that trash_photo can't verifiably move to Trash (send2trash
        raised, permission denied, or the file already vanished from disk —
        e.g. a folder-watching app like Luminar grabbed it first) is reported
        as an error and its DB row is KEPT. The photo stays in your reject
        list so you can see it didn't make it, rather than silently
        disappearing from the app while still sitting on disk.

    Returns per-outcome counts so the UI can report the truth:
      - trashed:         verifiably sent to the system Trash
      - skipped_missing: the file was already gone from disk (row kept)
      - failed:          send2trash/OS error (row kept)
    """
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT i.id, i.file_path, i.source_type
            FROM images i
            INNER JOIN decisions d ON d.image_id = i.id
            WHERE d.decision = 'reject'
              AND i.source_folder = ?
            """,
            (request.source_folder,),
        ).fetchall()

    trashed: int = 0
    skipped_missing: int = 0
    skipped_unsupported: int = 0
    errors: list[dict] = []
    ids_to_delete: list[int] = []

    for row in rows:
        file_path = row["file_path"]
        # Scoped to one source_folder (see TrashRejectsRequest). A source that
        # cannot delete (Apple Photos: neither AppleScript nor, from this
        # process, PhotoKit can remove an asset) is skipped rather than
        # attempted, and its row is kept so the rejection survives.
        sink = sink_for(dict(row))
        if not sink.supports_bulk_trash:
            skipped_unsupported += 1
            continue
        try:
            sink.bulk_trash(dict(row))
            ids_to_delete.append(row["id"])
            trashed += 1
        except FileNotFoundError:
            # File already gone from disk — do NOT claim we trashed it and do
            # NOT delete the row. Surfaced so the user can investigate (a
            # folder-watching app may have moved it out from under us).
            skipped_missing += 1
            errors.append({"file": file_path, "error": "file already missing from disk"})
            logger.warning("trash-rejects: file already missing, keeping row: %s", file_path)
        except Exception as exc:
            errors.append({"file": file_path, "error": str(exc)})
            logger.warning("trash-rejects: send2trash failed, keeping row: %s (%s)", file_path, exc)

    # Only rows whose file verifiably reached the Trash are removed from the
    # managed set. Missing/failed rows stay so the app's view matches reality.
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

    return {
        "trashed": trashed,
        "skipped_missing": skipped_missing,
        "skipped_unsupported": skipped_unsupported,
        "failed": len(errors) - skipped_missing,
        "errors": errors,
    }


def _reject_destination(source_folder: str | None) -> dict:
    """Describe where auto-cull's rejects would actually land.

    Auto-cull commits through the same sink as a manual R press, so the
    `reject_to_system_trash` setting applies to it too — a fact the modal used
    to hide behind "Files move immediately". The UI needs the truth *before*
    the click, so the preview carries it:

      mode "trash"  — send2trash. Recoverable from the OS Trash, but NOT
                      undoable in-app: the file is no longer addressable.
      mode "folder" — moved into a subfolder of the source. Fully reversible
                      with U, and the file never leaves the shoot.

    `label` is what to show the user. With no source_folder the sweep spans
    every folder tab, whose per-folder overrides may differ, so we name the
    default rather than claim one path for all of them.
    """
    if _reject_to_system_trash_enabled():
        return {"mode": "trash", "label": "the system Trash", "undoable": False}
    if source_folder is None:
        return {"mode": "folder", "label": f"each folder's {_DECISION_FOLDERS['reject']}", "undoable": True}
    overrides = get_folder_overrides(source_folder)
    dest = resolve_dest_folder("reject", source_folder, overrides)
    return {"mode": "folder", "label": dest.name, "undoable": True}


def _corpus_for_source(source_folder: str | None) -> str:
    """Training corpus a tab belongs to. Mirrors images.source_type."""
    return "photos" if source_folder and is_photos_source_folder(source_folder) else "folder"


def _require_auto_cull_model(corpus: str) -> None:
    """Refuse auto-cull on a corpus whose model has not earned the ready tier.

    Folder is exempt: without a model it falls back to quality thresholds,
    which work on RAW. On the Photos corpus they do not — half an iPhone
    library scores >= 95 sharpness and overall_score's median is 93, so a
    threshold pass there stamps Keep on most of the album rather than culling
    it. Better to say "not yet" than to hand back a confident wrong answer.
    """
    if corpus == "folder":
        return
    if model_for(corpus).info().get("model_status") == "ready":
        return
    have = _count_samples(corpus)
    need = min_decisions_for(corpus)
    raise HTTPException(
        status_code=400,
        detail=(
            f"Auto-cull needs a trained {corpus} model — {have} of {need} "
            f"decisions so far. Keep culling by hand and it switches itself on."
        ),
    )


def _ai_rank_preview(bursts: list[list[dict]], decisions: dict[int, str],
                     *, enabled: bool) -> dict:
    """Cache-only AI winners for the preview, plus the cost of the missing ones.

    Returns the `ai_rank` block the modal renders, with the winner map under
    `_winners` for the caller to pop and feed to the burst passes.
    """
    from backend.autocull_rank import (
        SECONDS_PER_RANK, consequential_bursts, resolve_winners,
    )

    if not enabled:
        return {"_winners": {}, "enabled": False, "groups": 0, "cached": 0,
                "to_rank": 0, "estimated_seconds": 0}

    contested = consequential_bursts(bursts, decisions)
    winners, stats = resolve_winners(contested, cached_only=True)
    to_rank = stats["fallback_reasons"].get("not_cached", 0)
    return {
        "_winners": winners,
        "enabled": True,
        "groups": len(contested),
        "cached": stats["ranked"],
        "to_rank": to_rank,
        "estimated_seconds": to_rank * SECONDS_PER_RANK,
    }


@router.get("/auto-cull/preview")
def auto_cull_preview(
    source_folder: str | None = None,
    threshold: float = 0.90,
):
    """
    Dry-run: compute which K/M/X each undecided photo would receive.

    Uses the personal model when trained (≥20 decisions); falls back to
    the built-in threshold logic. No files are moved and no DB rows are written.

    `threshold` is the cosine-similarity cutoff the burst-dedup post-pass groups
    on — the Similarity knob (the frontend passes its current value). Auto-cull
    dedup deliberately does NOT use the grouping time-gap (see
    _group_undecided_into_bursts); grouping here is visual-similarity only.
    """
    corpus = _corpus_for_source(source_folder)
    _require_auto_cull_model(corpus)
    with get_db() as conn:
        if source_folder is not None:
            rows = conn.execute(
                """
                SELECT i.* FROM images i
                LEFT JOIN decisions d ON d.image_id = i.id
                WHERE i.analysis_status = 'done' AND d.decision IS NULL
                  -- Scoped to one corpus: each source_type is judged by the
                  -- model trained on its own distribution, and the thresholds
                  -- below are read for that corpus too.
                  AND i.source_type = ?
                  AND i.source_folder = ?
                """,
                (corpus, source_folder),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT i.* FROM images i
                LEFT JOIN decisions d ON d.image_id = i.id
                WHERE i.analysis_status = 'done' AND d.decision IS NULL
                  AND i.source_type = ?
                """,
                (corpus,),
            ).fetchall()

    rows_dicts = [dict(r) for r in rows]

    cull_model = model_for(corpus)
    if cull_model.ready and rows_dicts:
        personal_scores = cull_model.predict_batch(rows_dicts)
        for row, ps in zip(rows_dicts, personal_scores):
            row["personal_score"] = ps

    from phase1_technical.quality_analyzer import get_decision_thresholds
    thresholds = get_decision_thresholds(corpus)

    counts: dict[str, int] = {"keep": 0, "maybe": 0, "reject": 0}
    # Bucket every reject reason emitted by _compute_auto_decision into the
    # UI's coarser categories. iso_ceiling + reciprocal_rule + threshold_reject
    # + personal_reject all land in low_score so the modal copy stays simple.
    _REASON_BUCKET = {
        "closed_eyes":          "closed_eyes",
        "soft_face":            "soft_face",
        "blurry_frame":         "blurry_frame",
        # A low-IQA reject is a poor/soft frame — folds into the blurry bucket
        # so the preview's "blurry" count reflects it and the modal copy stays
        # simple (no separate category for what reads to the user as blur).
        "low_iqa":              "blurry_frame",
        "cluttered_background": "cluttered_background",
        "iso_ceiling":          "low_score",
        "reciprocal_rule":      "low_score",
        "personal_reject":      "low_score",
        "threshold_reject":     "low_score",
        # Burst-redundancy rejects — decent frames that lost to a clearly-better
        # sibling in the same burst (graded demotion), were near-pixel-identical
        # to a better frame (Stage A collapse), or overflowed the burst's Maybe
        # review budget (Stage B). Not low-quality photos — their own bucket so
        # the modal can say "redundant burst frame", not "low score".
        "burst_keep_reject":    "burst_duplicate",
        "burst_near_duplicate": "burst_duplicate",
        "burst_redundant":      "burst_duplicate",
    }
    rule_counts: dict[str, int] = {
        "closed_eyes": 0,
        "soft_face": 0,
        "blurry_frame": 0,
        "cluttered_background": 0,
        "low_score": 0,
        # Frames rejected only because a near-identical burst sibling scored
        # clearly better (multi-keep graded demotion).
        "burst_duplicate": 0,
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

    bursts_preview = _group_undecided_into_bursts(rows_dicts, threshold=threshold)

    # AI burst rank, CACHE-ONLY. The real run ranks every contested group, which
    # costs ~60s apiece; a preview that did the same would make the modal take
    # as long as the cull it is previewing (and this endpoint promises to write
    # nothing). So the preview uses the ranks that already exist — from
    # GroupLoupe or the prerank worker — and reports the rest as work the run
    # will have to do, with an estimate. Counts for already-ranked groups are
    # exact; a group ranked for the first time during the run can land on a
    # different winner than this preview showed, which is the honest cost of not
    # spending an hour inside a modal.
    ai_preview = _ai_rank_preview(bursts_preview, decisions,
                                  enabled=bool(thresholds.get("auto_cull_ai_rank")))
    ai_winners = ai_preview.pop("_winners")

    # Same multi-keep pass as run_auto_cull, ahead of the keep-guarantee pass
    # below, so the preview counts match what the real run would produce.
    demotions_count = 0
    reject_demotions_count = 0
    if thresholds.get("auto_cull_cap_burst_keeps"):
        burst_demotions = _cap_burst_keeps(
            bursts_preview, decisions, preview_reasons, thresholds, ai_winners)
        demotions_count = len(burst_demotions)
        reject_demotions_count = sum(1 for d in burst_demotions if d["to"] == "reject")

    promotions_count = 0
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
        winner = _guarantee_winner(burst, preview_reasons, ai_winners)
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

    info = cull_model.info()
    using_personal = info.get("model_status") == "ready"
    return {
        "scoring_mode": "personal" if using_personal else "threshold",
        "scoring_info": (
            f"trained on {cull_model.training_size} {corpus} decisions"
            if using_personal
            else (
                f"model still learning ({cull_model.training_size}/{info.get('min_decisions')} "
                f"samples) — using quality thresholds"
                if cull_model.ready
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
        # Count of extra burst keeps demoted by the multi-keep pass (to Maybe
        # or Reject). `reject_demotions` is the subset sent all the way to
        # Reject (clearly-weak near-duplicates); the rest went to Maybe.
        "demotions": demotions_count,
        "reject_demotions": reject_demotions_count,
        # Where the rejects actually go, resolved the same way the real run
        # will resolve it. The modal states this before the click — with
        # reject_to_system_trash on, auto-cull sends N files to the OS Trash,
        # which is a materially different promise from "files move".
        "reject_destination": _reject_destination(source_folder),
        # What the AI-rank phase of the real run will cost, and how much of
        # this preview's own numbers already reflect an AI pick.
        "ai_rank": ai_preview,
    }


# Reject reasons we treat as "the photo is physically unrecoverable" — when
# every member of a burst hits one of these, promoting the best one to Maybe
# would just put a guaranteed-bad photo in front of the user. Subjective
# reasons (closed_eyes, soft_face, cluttered_background) DO get rescued
# because they can be salvageable in context (one photo of the group might
# be the only frame the moment happened in). low_iqa joins this set: a frame
# TOPIQ-NR scores below the floor is out-of-focus / heavily degraded, and
# rescuing the "best" of an all-low-IQA burst just surfaces the least-bad blur.
_PHYSICAL_REJECT_REASONS = frozenset({"iso_ceiling", "reciprocal_rule", "blurry_frame", "low_iqa"})


# Auto-decision reasons that mean "the personal model made this call" — for
# those members the multi-keep gates read personal_score against the personal
# cutoffs; every other keep reason came from the threshold-fallback path and
# reads overall_score against the fallback cutoffs. Mirrors the two branches of
# _compute_auto_decision so the gates use the same number the Keep verdict did.
_PERSONAL_KEEP_REASONS = frozenset({"personal_keep"})


def _guarantee_winner(
    burst: list[dict],
    reasons: dict[int, str],
    ai_winners: dict[int, int] | None = None,
) -> dict:
    """The photo the keep-guarantee floor pass promotes to Maybe for a burst
    that auto-cull decided entirely against.

    Defaults to the vision LLM's rank-1 when we have one, else the score-based
    pick — with one guard. The point of this pass is to hand the user something
    rescuable, so a rank-1 whose own reject reason is physically unrecoverable
    (ISO ceiling / reciprocal rule / blurry frame / low IQA) loses to any member
    that is only subjectively rejected. The LLM ranks frames against each other;
    in an all-mediocre burst its #1 can still be the blurred one, and promoting
    that would be strictly worse than promoting the soft-smile frame next to it.
    """
    from backend.group_scoring import top_n_candidates

    ordered = top_n_candidates(burst, len(burst))
    winner_id = (ai_winners or {}).get(burst[0]["id"])
    if winner_id is not None:
        ai_pick = next((m for m in ordered if m["id"] == winner_id), None)
        if ai_pick is not None and (
            reasons.get(winner_id) not in _PHYSICAL_REJECT_REASONS
            or all(reasons.get(m["id"]) in _PHYSICAL_REJECT_REASONS for m in ordered)
        ):
            return ai_pick
    return ordered[0]


def _burst_member_score(row: dict, reason: str | None) -> float | None:
    """The score the auto-decision was actually made on for this member:
    personal_score for personal-mode keeps, overall_score otherwise. None when
    the relevant field is missing (older rows / analysis gaps) — the caller
    treats a None-scored extra keep as non-qualifying (never double-kept)."""
    if reason in _PERSONAL_KEEP_REASONS:
        return row.get("personal_score")
    return row.get("overall_score")


# Cosine threshold that separates distinct visual sub-scenes WITHIN one burst.
# Deliberately STRICTER than the 0.90 the grouping algorithm uses to FORM a
# burst: a burst is already a single 0.90-connected component (transitively —
# a chain A~B~C at 0.92 each is one burst even if A~C is only 0.85), so
# sub-clustering at 0.90 would just re-merge it and never fire. We need a higher
# bar to isolate a genuinely-different framing that got chained into the burst.
# 0.94 sits between burst formation (0.90) and near-dup collapse (0.97): shoot
# angle A, pan, shoot angle B seconds apart → two sub-scenes, each with its own
# Keep/Maybe budget, so a real second POV earns its own keeps instead of being
# budget-rejected as a redundant copy of angle A. A slight recompose of the same
# shot stays ≥ 0.94 and remains one scene. Tunable later if real bursts show
# 0.94 splits too eagerly or too rarely; kept a constant for now (no new knob).
_SCENE_SPLIT_COSINE = 0.94


def _scene_subclusters(ordered_members: list[dict]) -> list[list[dict]]:
    """Split a burst's members into distinct visual sub-scenes by cosine
    union-find at _SCENE_SPLIT_COSINE, preserving each member's priority order
    within its sub-cluster. Members without an embedding can't be clustered —
    each becomes its own singleton sub-cluster (conservative: a frame we can't
    place visually gets its own keep budget rather than being lumped in and
    possibly budget-rejected against an unrelated scene).

    Returns a list of sub-clusters; concatenated they contain every input member
    exactly once. When every member shares one scene this returns a single
    sub-cluster == the input, so the budget behaves exactly as the pre-sub-
    cluster code did for single-scene bursts.
    """
    import numpy as _np
    from phase2_quality.similarity_scorer import json_to_embedding as _emb

    vecs: dict[int, "_np.ndarray"] = {}
    for m in ordered_members:
        vec = _emb(m.get("embedding"))
        if vec is not None:
            vecs[m["id"]] = _np.asarray(vec, dtype=_np.float32)

    # Union-find over members that have embeddings.
    parent: dict[int, int] = {m["id"]: m["id"] for m in ordered_members}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    ids_with_vec = [m["id"] for m in ordered_members if m["id"] in vecs]
    for i, a in enumerate(ids_with_vec):
        for b in ids_with_vec[i + 1:]:
            if float(_np.dot(vecs[a], vecs[b])) >= _SCENE_SPLIT_COSINE:
                union(a, b)

    # Bucket members by root, preserving input (priority) order both across and
    # within sub-clusters: first-seen root ordering keeps the best sub-cluster
    # (the one containing the burst winner) first.
    order: list[int] = []
    buckets: dict[int, list[dict]] = {}
    for m in ordered_members:
        root = find(m["id"])
        if root not in buckets:
            buckets[root] = []
            order.append(root)
        buckets[root].append(m)
    return [buckets[r] for r in order]


def _near_duplicate_losers(ordered_members: list[dict]) -> set[int]:
    """Greedy near-duplicate collapse over an already-priority-ordered burst.

    Walks members best→worst; the higher-priority frame of each cosine-near-
    identical pair (cosine ≥ _NEAR_DUP_COSINE) is the surviving representative,
    and every lower-priority frame within that radius is a redundant duplicate.
    Returns the set of image_ids that are duplicates of some better frame — the
    caller rejects those as `burst_near_duplicate`.

    Same 0.97 threshold and greedy-rep logic as burst_ranker._collapse_near_
    duplicates, but operates purely on the embeddings already on the in-memory
    rows (no DB round-trip). Embeddings are L2-normalised, so dot product IS
    cosine. A frame with no embedding can never be collapsed (can't prove it's
    a dup) and never absorbs others — it falls through to the score gates.
    """
    import numpy as _np
    from phase2_quality.similarity_scorer import json_to_embedding as _emb
    from phase2_quality.burst_ranker import _NEAR_DUP_COSINE

    vecs: dict[int, "_np.ndarray"] = {}
    for m in ordered_members:
        vec = _emb(m.get("embedding"))
        if vec is not None:
            vecs[m["id"]] = _np.asarray(vec, dtype=_np.float32)
    if len(vecs) < 2:
        return set()

    losers: set[int] = set()
    ordered_ids = [m["id"] for m in ordered_members]
    for i, rep_id in enumerate(ordered_ids):
        if rep_id in losers or rep_id not in vecs:
            continue
        rep_vec = vecs[rep_id]
        # Only lower-priority frames can be absorbed by this rep.
        for other_id in ordered_ids[i + 1:]:
            if other_id in losers or other_id not in vecs:
                continue
            if float(_np.dot(rep_vec, vecs[other_id])) >= _NEAR_DUP_COSINE:
                losers.add(other_id)
    return losers


def _cap_burst_keeps(
    bursts: list[list[dict]],
    decisions: dict[int, str],
    reasons: dict[int, str],
    thresholds: dict | None = None,
    ai_winners: dict[int, int] | None = None,
) -> list[dict]:
    """Burst multi-keep post-pass: mutates `decisions`/`reasons` in place so a
    similarity burst auto-keeps its best-by-priority member plus any additional
    frames that are genuinely strong — the rest demote, graded to Maybe or
    Reject. Each burst member is scored independently by _compute_auto_decision,
    so a run of near-identical frames (same pose, seconds apart) can each clear
    the Keep cutoff; the grouping/dedup users expect never fires for auto-cull
    without this pass.

    For every burst with ≥2 members and ≥2 "keep" decisions:

      Stage A — near-duplicate collapse (burst-wide). The lower-priority frame
      of any pair that is cosine-near-identical (≥ 0.97, same threshold as the
      LLM burst ranker's dedup) is redundant regardless of score → "reject" with
      reason "burst_near_duplicate". The higher-priority frame survives as rep.

      Scene split. The burst is split into distinct visual sub-scenes by cosine
      union-find at _SCENE_SPLIT_COSINE (0.90, the same threshold the grouping
      algorithm uses to form bursts within a time segment). A 30s time-gap burst
      can contain several genuinely-different framings — shoot angle A, pan,
      shoot angle B seconds apart — and the time-gap glue makes them one burst.
      Each sub-scene gets its OWN Keep/Maybe budget below, so a real second POV
      earns its own keeps instead of being rejected as a redundant copy of the
      first. A single-scene burst yields one sub-cluster → identical to the
      per-burst budget.

      Stage B — score gates + review budget, applied PER SUB-SCENE (see
      _apply_burst_budget). The best member of each sub-scene always keeps; the
      rest stay "keep" only if BOTH gates hold and we're under max_burst_keeps
      (margin above the mode's Keep cutoff + closeness to the sub-scene winner),
      else demote: "burst_keep_reject" when clearly weak (far below the sub-scene
      winner or under the Maybe cutoff), "burst_keep_cap"→Maybe while that sub-
      scene's Maybe budget has room, "burst_redundant"→reject once it's spent. A
      scoreless frame never counts as clearly weak (no auto-trash on missing
      data) — Maybe slot if free, else redundant-reject.

    `ai_winners` maps a member id to the vision LLM's rank-1 for that burst (see
    backend/autocull_rank.py). When present, that photo is moved to the front of
    the priority order, which makes it the winner of its sub-scene — the one
    frame guaranteed to stay Keep. That is the AI rank's ENTIRE reach here: the
    keep/maybe gates, the near-dup rep choice for every other pair and the
    demotion order all still run on `top_n_candidates`' score priority.

    max_burst_keeps=1 + max_burst_maybes large reproduces the original cap-at-one
    behaviour PER SUB-SCENE; max_burst_maybes=0 rejects every non-surviving frame
    in each sub-scene. Non-keep decisions in the burst are left untouched. Runs
    BEFORE the burst_keep_guarantee floor pass so a burst always retains ≥1 keep
    here (each sub-scene keeps its winner) — it can never trigger the zero-keeps
    rescue the floor pass checks.

    Returns the list of demotions applied:
    [{"image_id", "burst_size", "to"}, ...] where "to" is "maybe" | "reject".
    """
    from backend.autocull_rank import promote_winner as _promote_winner
    from backend.group_scoring import top_n_candidates
    from phase1_technical.quality_analyzer import get_decision_thresholds

    t = thresholds or get_decision_thresholds()
    max_keeps = int(t.get("max_burst_keeps", 2))
    max_maybes = int(t.get("max_burst_maybes", 2))
    margin = t.get("burst_keep_margin", 8.0)
    winner_delta = t.get("burst_keep_winner_delta", 6.0)
    reject_delta = t.get("burst_keep_reject_delta", 20.0)

    budget = {
        "max_keeps": max_keeps, "max_maybes": max_maybes,
        "margin": margin, "winner_delta": winner_delta, "reject_delta": reject_delta,
    }

    demotions: list[dict] = []
    for burst in bursts:
        if len(burst) < 2:
            continue
        burst_ids = [m["id"] for m in burst]
        keep_ids = [i for i in burst_ids if decisions.get(i) == "keep"]
        if len(keep_ids) < 2:
            continue

        by_id = {m["id"]: m for m in burst}
        keep_members = [by_id[i] for i in keep_ids]
        # Priority order (best→worst) drives every stage: the winner, the
        # near-dup reps, and which frames fill the Keep/Maybe budgets.
        ordered = top_n_candidates(keep_members, len(keep_members))
        # The AI rank-1, when we have one and it is still a Keep, takes index 0
        # away from the score pick. A rank-1 that already lost its Keep to an
        # instant-reject rule isn't in `keep_members`, and promote_winner is a
        # no-op then — the rules that fire before the burst passes are not
        # something the LLM gets to overturn.
        ordered = _promote_winner(
            ordered, (ai_winners or {}).get(burst_ids[0]))

        # Stage A — near-duplicate collapse, applied ACROSS the whole burst: a
        # near-pixel-dup is redundant no matter which sub-scene it lands in
        # (and two ≥0.97 frames are always in the same ≥0.90 sub-cluster anyway,
        # so scoping this burst-wide changes nothing but is clearer).
        near_dup_losers = _near_duplicate_losers(ordered)

        # Split the burst into distinct visual sub-scenes. A 30s time-gap burst
        # can contain several genuinely-different framings (pan to a new POV
        # mid-window); each sub-scene gets its OWN Keep/Maybe budget so a real
        # second scene isn't budget-rejected as a redundant copy of the first.
        subclusters = _scene_subclusters(ordered)

        # Stage B — score gates + review budget, applied per sub-scene.
        for sub in subclusters:
            demotions.extend(
                _apply_burst_budget(
                    sub, near_dup_losers, decisions, reasons, t, budget,
                    burst_size=len(burst),
                )
            )
    return demotions


def _apply_burst_budget(
    ordered_sub: list[dict],
    near_dup_losers: set[int],
    decisions: dict[int, str],
    reasons: dict[int, str],
    t: dict,
    budget: dict,
    burst_size: int,
) -> list[dict]:
    """Apply the Keep/Maybe review budget to ONE visual sub-scene of a burst.

    `ordered_sub` is priority-ordered (best first). Its best member is the
    sub-scene winner and always keeps; the rest run the near-dup / score-gate /
    graded-demotion logic with a fresh Keep+Maybe budget scoped to this sub-
    scene. Mutates `decisions`/`reasons` in place; returns the demotions list.

    A single-scene burst produces one sub-cluster, so this reproduces the prior
    burst-wide budget exactly. `burst_size` is passed through unchanged for the
    demotion records (callers/telemetry still think in whole-burst sizes).
    """
    demotions: list[dict] = []
    winner_id = ordered_sub[0]["id"]
    winner_score = _burst_member_score(ordered_sub[0], reasons.get(winner_id))

    kept_count = 1   # the sub-scene winner always keeps
    maybe_count = 0
    for member in ordered_sub:
        image_id = member["id"]
        if image_id == winner_id:
            continue

        if image_id in near_dup_losers:
            decisions[image_id] = "reject"
            reasons[image_id] = "burst_near_duplicate"
            demotions.append(
                {"image_id": image_id, "burst_size": burst_size, "to": "reject"}
            )
            continue

        reason = reasons.get(image_id)
        score = _burst_member_score(member, reason)

        personal_mode = reason in _PERSONAL_KEEP_REASONS
        keep_cutoff = t["keep_threshold"] if personal_mode else t["fallback_keep"]
        maybe_cutoff = t["maybe_threshold"] if personal_mode else t["fallback_maybe"]

        qualifies = (
            kept_count < budget["max_keeps"]
            and score is not None
            and winner_score is not None
            and score >= keep_cutoff + budget["margin"]
            and (winner_score - score) <= budget["winner_delta"]
        )
        if qualifies:
            kept_count += 1
            continue  # survives as keep; decision/reason untouched

        # Graded demotion. Reject when clearly-weak (far below the sub-scene
        # winner, or under the mode's Maybe cutoff) OR when this sub-scene's
        # review budget is spent. A missing score can't be judged weak, so it
        # only rejects via the budget path, never the clearly-weak path.
        clearly_weak = score is not None and (
            (winner_score is not None and (winner_score - score) > budget["reject_delta"])
            or score < maybe_cutoff
        )
        if clearly_weak:
            target, reason_out = "reject", "burst_keep_reject"
        elif maybe_count < budget["max_maybes"]:
            target, reason_out = "maybe", "burst_keep_cap"
            maybe_count += 1
        else:
            target, reason_out = "reject", "burst_redundant"

        decisions[image_id] = target
        reasons[image_id] = reason_out
        demotions.append(
            {"image_id": image_id, "burst_size": burst_size, "to": target}
        )
    return demotions


def _group_undecided_into_bursts(rows: list[dict],
                                 time_gap_seconds: float | None = None,
                                 threshold: float = 0.90) -> list[list[dict]]:
    """Cluster the rows for the auto-cull burst post-passes.

    Auto-cull groups by VISUAL SIMILARITY only — `time_gap_seconds` defaults to
    None (time split disabled). Rationale: the grouping time-gap can only split
    bursts *coarser* than the camera's inter-frame spacing, and continuous
    bursts fire several frames within one EXIF second (second-resolution
    timestamps), so no gap can separate them — the slider was inert for
    auto-cull dedup on fast bursts. Cosine similarity *can* discriminate those
    frames. The risk of pure-similarity grouping (distinct moments minutes apart
    collapsing into one giant burst) is handled downstream by _cap_burst_keeps'
    per-scene split at _SCENE_SPLIT_COSINE, which re-separates them and gives
    each moment its own keep budget. So the real auto-cull dedup levers are the
    Similarity `threshold` + the keep/maybe budgets, NOT the time gap. Callers
    may still pass an explicit time_gap_seconds (tests do) to force a time split.

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


def _run_ai_ranking(
    bursts: list[list[dict]],
    decisions: dict[int, str],
    source_folder: str | None,
) -> tuple[dict[int, int], dict]:
    """Rank auto-cull's contested groups with the vision LLM, reporting progress.

    Publishes into `state._autocull_progress` so POST /auto-cull can stay a
    single blocking request while the modal polls GET /auto-cull/progress — the
    ranking phase can run for tens of minutes on a fresh import and a spinner
    with no numbers behind it is not an honest way to spend that.

    The prerank worker is cancelled first. It ranks the SAME groups through the
    same cache, but a second concurrent qwen runner OOMs the unified memory pool
    on Apple silicon (see phase2_quality/prerank.py). Its queue is warm-cache
    work; this is blocking a user who is waiting, so this wins. rank_burst's
    inflight registry covers the narrower race where both want one exact group.
    """
    from backend.autocull_rank import consequential_bursts, resolve_winners
    from phase2_quality import prerank

    to_rank = consequential_bursts(bursts, decisions)
    prerank.cancel()

    _autocull_stop_event.clear()
    _autocull_progress.update({
        "running": True,
        "phase": "ranking",
        "total": len(to_rank),
        "done": 0,
        "cached": 0,
        "stop_requested": False,
        "source_folder": source_folder,
        "started_at": time.time(),
    })
    try:
        winners, stats = resolve_winners(
            to_rank,
            on_progress=lambda done, total: _autocull_progress.update(
                {"done": done, "total": total}),
            should_stop=_autocull_stop_event.is_set,
        )
        if not stats.get("stopped"):
            _autocull_progress["phase"] = "applying"
        return winners, stats
    finally:
        _autocull_progress["running"] = False
        _autocull_progress["phase"] = None
        _autocull_stop_event.clear()


@router.get("/auto-cull/progress")
def auto_cull_progress():
    """Progress of a running auto-cull's AI-ranking phase.

    `total`/`done` count GROUPS, not photos — ranking is the only slow part and
    it is per-group. Safe to poll when nothing is running (`running: false`).
    """
    return dict(_autocull_progress)


@router.post("/auto-cull/stop")
def stop_auto_cull():
    """Abort a running auto-cull between two group ranks.

    Ranking happens before a single decision is applied, so stopping there
    leaves nothing behind: no files move, no decisions are written. Ranks that
    already completed stay in the burst_rankings cache, so a later run picks up
    where this one left off for free. Once the run has left the ranking phase
    there is nothing long-running left to interrupt, and this reports
    'not_running'.
    """
    if not _autocull_progress.get("running"):
        return {"status": "not_running"}
    _autocull_progress["stop_requested"] = True
    _autocull_stop_event.set()
    return {"status": "stopping"}


@router.post("/auto-cull")
def run_auto_cull(
    source_folder: str | None = None,
    threshold: float = 0.90,
):
    """
    Assign K/M/X to every undecided analyzed photo and move their files.

    Uses the same scoring logic as GET /auto-cull/preview, with two
    burst-aware post-passes over the similarity groups:

      1. Multi-keep cap (_cap_burst_keeps, opt-out): the best-by-priority
         member of a burst always keeps; additional in-burst Keeps survive
         only if genuinely strong (up to max_burst_keeps), and the rest demote
         graded to Maybe (near-alternates) or Reject (clearly-weak duplicates).
      2. Keep-guarantee floor: when an entire group ends up with zero Keeps AND
         none of the rejects were physical-unrecoverable (ISO ceiling /
         reciprocal-rule / blurry-frame / low-IQA), the best photo is promoted
         to Maybe so the user always has a chance to rescue a moment.

    Both passes rank with the same priority as the group hero selector
    (face_sharpness → eyes_open → sharpness → IQA → aesthetic → overall_score);
    see backend.group_scoring.top_n_candidates.

    `threshold` is the cosine-similarity cutoff the burst grouping uses. Auto-cull
    dedup groups by visual similarity ONLY, not the grouping time-gap (see
    _group_undecided_into_bursts for why the time-gap was inert on fast bursts).
    """
    corpus = _corpus_for_source(source_folder)
    _require_auto_cull_model(corpus)
    with get_db() as conn:
        if source_folder is not None:
            rows = conn.execute(
                """
                SELECT i.* FROM images i
                LEFT JOIN decisions d ON d.image_id = i.id
                WHERE i.analysis_status = 'done' AND d.decision IS NULL
                  -- Scoped to one corpus: each source_type is judged by the
                  -- model trained on its own distribution, and the thresholds
                  -- below are read for that corpus too.
                  AND i.source_type = ?
                  AND i.source_folder = ?
                """,
                (corpus, source_folder),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT i.* FROM images i
                LEFT JOIN decisions d ON d.image_id = i.id
                WHERE i.analysis_status = 'done' AND d.decision IS NULL
                  AND i.source_type = ?
                """,
                (corpus,),
            ).fetchall()

    rows_dicts = [dict(r) for r in rows]

    cull_model = model_for(corpus)
    if cull_model.ready and rows_dicts:
        personal_scores = cull_model.predict_batch(rows_dicts)
        for row, ps in zip(rows_dicts, personal_scores):
            row["personal_score"] = ps

    from phase1_technical.quality_analyzer import get_decision_thresholds
    thresholds = get_decision_thresholds(corpus)

    # Phase 1: compute decisions for every row WITHOUT applying them yet.
    # Capturing reasons here is what lets the burst post-pass know whether
    # each reject is physical (don't rescue) or subjective (do rescue).
    decisions: dict[int, str] = {}
    reasons:   dict[int, str] = {}
    for row in rows_dicts:
        decision, reason = _compute_auto_decision(row, thresholds)
        decisions[row["id"]] = decision
        reasons[row["id"]] = reason

    bursts = _group_undecided_into_bursts(rows_dicts, threshold=threshold)

    # Phase 1.4: AI burst rank. Ask the vision LLM which frame of each
    # CONTESTED group is the best one, so the two post-passes below pick their
    # winner by comparative judgement instead of the score heuristic. Only
    # groups where the winner decides something are ranked (see
    # autocull_rank.consequential_bursts) — everything else would pay ~60s for
    # an answer nobody reads. Failures fall back to the score pick and are
    # counted, never raised: an unreachable Ollama must not block a cull.
    ai_winners: dict[int, int] = {}
    rank_stats: dict = {"groups": 0, "ranked": 0, "fell_back": 0,
                        "fallback_reasons": {}, "stopped": False}
    if thresholds.get("auto_cull_ai_rank"):
        ai_winners, rank_stats = _run_ai_ranking(bursts, decisions, source_folder)
        if rank_stats.get("stopped"):
            # Stopped during ranking — nothing has been applied yet, so the
            # honest outcome is a no-op run, not a half-culled folder.
            return {
                "scoring_mode": "stopped",
                "counts": {"keep": 0, "maybe": 0, "reject": 0},
                "total": 0,
                "errors": [],
                "promotions": [],
                "demotions": [],
                "ai_rank": rank_stats,
                "stopped": True,
            }

    # Phase 1.5: burst keep-cap post-pass. Runs BEFORE the keep-guarantee
    # floor pass below — see _cap_burst_keeps docstring for why the ordering
    # is safe (a burst can never end up with zero keeps as a result of this
    # pass, since it always leaves exactly one). Opt-out via the
    # auto_cull_cap_burst_keeps setting.
    demotions: list[dict] = []
    if thresholds.get("auto_cull_cap_burst_keeps"):
        demotions = _cap_burst_keeps(bursts, decisions, reasons, thresholds, ai_winners)

    # Phase 2: burst keep-guarantee post-pass. Walks each similarity group;
    # if it has ≥2 members, zero Keeps, and at least one member has a
    # subjective reject reason (not iso_ceiling / reciprocal_rule /
    # blurry_frame), promote the best-by-priority member to Maybe. Tracks
    # the promotions so the response surfaces them for any future UI nudge.
    promotions: list[dict] = []
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
        # Pick the winner: the AI rank-1 when this group got one, else the
        # best member by the canonical score priority. See _guarantee_winner.
        winner = _guarantee_winner(burst, reasons, ai_winners)
        winner_id = winner["id"]
        current = decisions.get(winner_id)
        if current == "maybe":
            # Already maybe — no action needed, but still record so we can
            # report "this burst was almost lost" in the response.
            continue
        decisions[winner_id] = "maybe"
        # Overwrite the winner's pre-promotion reject reason so Phase 3 (and
        # the auto_decision_log audit trail) record the TRUE cause of the
        # final decision, not the reject rule that would have fired without
        # this post-pass.
        reasons[winner_id] = "burst_keep_guarantee"
        promotions.append({
            "image_id": winner_id,
            "from": current,
            "to": "maybe",
            "burst_size": len(burst),
            "reason": "burst_keep_guarantee",
        })

    # Phase 3: apply the decisions (now post-promotion).
    #
    # Mirroring is OFF by default here: every row (RAW and processed alike)
    # already went through _compute_auto_decision independently above, using
    # its own scores. Letting _apply_decision mirror one side's decision onto
    # its companion would silently overwrite that independent verdict with
    # whichever sibling happened to be applied second in this loop — e.g. a
    # camera JPEG scoring personal_score=100 ("keep") getting dragged down to
    # match its un-demosaiced RAW twin scoring ~35 ("maybe"), even though both
    # were correctly scored on their own. Manual K/M/X from the UI still
    # mirrors (see POST /decision) because there the user only ever judges
    # one visible tile and both files should follow that single verdict.
    #
    # Exception: analysis_mode=jpeg_priority gives a RAW-with-processed-sibling
    # an unscored analysis_status='skipped' stub row (see analyze_folder in
    # backend/routers/analysis.py) that never reaches this query's `WHERE
    # analysis_status = 'done'` filter and never gets its own entry in
    # `decisions`/`reasons`. That stub has no independent verdict to protect —
    # mirroring is the ONLY way it ever gets decided and moved. Skipping this
    # would leave every such RAW behind in the source folder while its JPEG
    # twin moves on its own (see _companion_is_hidden, which encodes this same
    # skipped-stub case for the manual K/M/X path).
    counts: dict[str, int] = {"keep": 0, "maybe": 0, "reject": 0}
    errors: list[dict] = []
    for row in rows_dicts:
        decision = decisions[row["id"]]
        companion_id = _find_companion_image_id(row)
        mirror_to_skipped_stub = companion_id is not None and _companion_is_skipped_stub(companion_id)
        try:
            _apply_decision(
                row["id"], decision, is_auto=True, reason=reasons.get(row["id"]),
                _mirror=mirror_to_skipped_stub,
            )
            counts[decision] += 1
        except HTTPException as exc:
            errors.append({"id": row["id"], "error": exc.detail})

    info = cull_model.info()
    using_personal = info.get("model_status") == "ready"
    return {
        "scoring_mode": "personal" if using_personal else "threshold",
        "counts": counts,
        "total": len(rows_dicts),
        "errors": errors,
        # New: surfaces how many bursts had their best photo rescued from
        # all-reject to maybe. Frontend can use this for a one-line toast.
        "promotions": promotions,
        # Surfaces how many extra burst keeps were demoted by the multi-keep
        # pass. Each entry carries "to": "maybe" | "reject" so the frontend can
        # split near-alternates (Maybe) from clearly-weak duplicates (Reject).
        "demotions": demotions,
        # How many contested groups had their winner picked by the vision LLM
        # vs. silently fell back to the score heuristic, and why. A fallback is
        # invisible in the decisions themselves, so it has to be reported here
        # or "the AI picked these" becomes a claim nobody can check.
        "ai_rank": rank_stats,
    }
