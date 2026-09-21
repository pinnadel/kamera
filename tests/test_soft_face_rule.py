"""The soft-face instant reject must survive the edge-width switch.

It asks "is the frame sharp while the face is soft?" — and in personal-model
mode it is the ONLY sharpness guard, because `_compute_auto_decision` returns on
the model's verdict long before the threshold-fallback `reject_blurry_frame`
line. On the Photos corpus `sharpness_score` became min(frame, face), so the
precondition `sharpness >= floor` could never hold at the moment the rule was
meant to fire. `edge_width_px` (frame only) answers it instead.
"""
import pytest

from backend.routers.decisions import _compute_auto_decision
from phase1_technical.edge_sharpness import width_to_score

# A width that scores well above the blurry floor, and one well below it.
SHARP_FRAME_WIDTH = 1.20   # → 90
SOFT_FRAME_WIDTH = 2.40    # → ~11

THRESHOLDS = {
    "reject_closed_eyes": True,
    "reject_closed_eyes_all_faces": False,
    "reject_soft_face": True,
    "face_sharpness_floor": 20.0,
    "fallback_sharpness_floor": 40.0,
    "reject_blurry_frame": True,
    "reject_low_iqa": False,
    "fallback_keep": 60.0,
    "fallback_maybe": 40.0,
}


def _photos_row(**over):
    row = {
        "source_type": "photos",
        "face_detected": 1,
        "face_count": 1,
        "eyes_open": 1,
        "face_sharpness_score": 8,           # soft face, on the 0-100 edge scale
        "edge_width_px": SHARP_FRAME_WIDTH,  # frame itself is sharp
        "sharpness_score": 8,                # min(frame, face) — what the DB holds
        "overall_score": 50.0,
        "iqa_score": 60.0,
    }
    row.update(over)
    return row


def test_sharp_frame_with_soft_face_is_rejected_as_soft_face():
    assert width_to_score(SHARP_FRAME_WIDTH) >= THRESHOLDS["fallback_sharpness_floor"]
    decision, reason = _compute_auto_decision(_photos_row(), thresholds=THRESHOLDS)
    assert (decision, reason) == ("reject", "soft_face")


def test_rule_does_not_fire_when_the_whole_frame_is_soft():
    """Everything is soft — that is a blurry frame, not a missed focus plane."""
    row = _photos_row(edge_width_px=SOFT_FRAME_WIDTH)
    decision, reason = _compute_auto_decision(row, thresholds=THRESHOLDS)
    assert reason != "soft_face"


def test_sharp_face_is_never_soft_face():
    row = _photos_row(face_sharpness_score=85, sharpness_score=85)
    decision, reason = _compute_auto_decision(row, thresholds=THRESHOLDS)
    assert reason != "soft_face"


def test_photos_row_without_the_edge_measurement_falls_back_to_sharpness_score():
    """A row analysed before the switch has no edge_width_px — no crash, and the
    legacy precondition still applies."""
    row = _photos_row(edge_width_px=None, sharpness_score=90, face_sharpness_score=8)
    decision, reason = _compute_auto_decision(row, thresholds=THRESHOLDS)
    assert (decision, reason) == ("reject", "soft_face")


def test_folder_rows_keep_reading_sharpness_score():
    """Folder face sharpness is still Laplacian variance; judging it against an
    edge-width frame score would compare two different scales."""
    row = _photos_row(
        source_type="folder",
        face_sharpness_score=8,     # variance, below the floor
        sharpness_score=90,         # legacy frame score, above the floor
        edge_width_px=SOFT_FRAME_WIDTH,  # shadow measurement — must be ignored here
    )
    decision, reason = _compute_auto_decision(row, thresholds=THRESHOLDS)
    assert (decision, reason) == ("reject", "soft_face")


def test_auto_cull_queries_select_the_whole_image_row():
    """Call-site guard: the rule above only works if `edge_width_px` actually
    reaches it. Both auto-cull paths read `SELECT i.*`; a narrowed column list
    would silently return the rule to the state this file was written to fix.
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "backend/routers/decisions.py").read_text()
    # Every query feeding _compute_auto_decision joins decisions to find undecided rows.
    queries = [q for q in src.split("conn.execute(") if "LEFT JOIN decisions d ON d.image_id = i.id" in q[:600]]
    assert len(queries) >= 2, "auto-cull row queries not found"
    for q in queries:
        head = q[:600]
        assert "SELECT i.*" in head or "i.edge_width_px" in head, head[:200]
