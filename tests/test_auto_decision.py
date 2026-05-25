"""
Tests for `_compute_auto_decision` — the rule engine behind every K/M/X
press and every Auto-cull run.

Six independent code paths; each verified here in isolation. Mocks out
`_personal_model.info()` so the threshold-fallback path runs deterministically
without needing a trained pickle on disk.
"""
from unittest.mock import patch

import pytest

from backend.routers.decisions import (
    _all_faces_eyes_closed,
    _compute_auto_decision,
    _parse_shutter_seconds,
)


# Threshold set matching production defaults but with every instant-reject
# rule explicitly named so a future default flip won't silently rewrite test
# semantics. Tests opt rules in/out by overriding individual keys.
DEFAULT_T = {
    "keep_threshold":              80.0,
    "maybe_threshold":             60.0,
    "fallback_keep":                75.0,
    "fallback_maybe":               55.0,
    "fallback_sharpness_floor":     40.0,
    "face_sharpness_floor":         40.0,
    "reject_soft_face":             False,
    "reject_blurry_frame":          False,
    "reject_closed_eyes":           False,
    "reject_closed_eyes_all_faces": False,
    "reject_reciprocal_rule":       False,
    "reject_above_iso_ceiling":     False,
    "iso_ceiling":                  0.0,
    "reject_high_background_distraction": False,
    "background_distraction_ceiling":     0.65,
    "auto_cull_uncertain_to_maybe":       False,  # opt-in per test
    "uncertainty_threshold":              8.0,
}


@pytest.fixture(autouse=True)
def _model_not_ready():
    """Force the threshold-fallback path. `_personal_model.info()` is a method
    on a module-level singleton in backend.state; patching it here keeps every
    test below in pure-function territory."""
    with patch(
        "backend.routers.decisions._personal_model.info",
        return_value={"model_status": "untrained"},
    ):
        yield


def _row(**overrides) -> dict:
    """Build a minimum row dict; overrides win. Defaults represent a clean
    medium-quality landscape (no face, no EXIF surprises)."""
    base = {
        "sharpness_score": 70,
        "overall_score": 70,
        "face_detected": False,
        "face_count": 0,
        "eyes_open": None,
        "face_sharpness_score": 0,
        "iso": None,
        "focal_length_mm": None,
        "shutter_speed": None,
        "background_distraction_score": None,
        "faces_eyes_open_json": None,
        "personal_score": None,
    }
    base.update(overrides)
    return base


def _t(**overrides) -> dict:
    out = dict(DEFAULT_T)
    out.update(overrides)
    return out


# ---------------------------------------------------------------------------
# shutter parser — pure function, fast
# ---------------------------------------------------------------------------


def test_parse_shutter_fraction():
    assert _parse_shutter_seconds("1/500") == pytest.approx(1 / 500)


def test_parse_shutter_decimal():
    assert _parse_shutter_seconds("0.004") == pytest.approx(0.004)


def test_parse_shutter_integer_string():
    assert _parse_shutter_seconds("2") == 2.0


def test_parse_shutter_empty_string_is_none():
    assert _parse_shutter_seconds("") is None


def test_parse_shutter_none_is_none():
    assert _parse_shutter_seconds(None) is None


def test_parse_shutter_divide_by_zero_is_none():
    assert _parse_shutter_seconds("1/0") is None


def test_parse_shutter_garbage_is_none():
    assert _parse_shutter_seconds("not-a-number") is None


# ---------------------------------------------------------------------------
# closed-eyes branches
# ---------------------------------------------------------------------------


def test_closed_eyes_single_face_rejects():
    row = _row(face_detected=True, face_count=1, eyes_open=0)
    assert _compute_auto_decision(row, _t(reject_closed_eyes=True)) == (
        "reject", "closed_eyes",
    )


def test_closed_eyes_group_shot_one_blinker_does_not_reject_when_all_faces_mode_on():
    # 3-person group, only one blinked. faces_eyes_open_json indicates not all closed.
    row = _row(
        face_detected=True,
        face_count=3,
        eyes_open=0,
        faces_eyes_open_json='[false, true, true]',
    )
    t = _t(reject_closed_eyes=True, reject_closed_eyes_all_faces=True)
    decision, _ = _compute_auto_decision(row, t)
    assert decision != "reject"


def test_closed_eyes_group_shot_all_closed_rejects():
    row = _row(
        face_detected=True,
        face_count=3,
        eyes_open=0,
        faces_eyes_open_json='[false, false, false]',
    )
    t = _t(reject_closed_eyes=True, reject_closed_eyes_all_faces=True)
    assert _compute_auto_decision(row, t) == ("reject", "closed_eyes")


def test_closed_eyes_disabled_does_not_reject():
    row = _row(face_detected=True, face_count=1, eyes_open=0)
    decision, _ = _compute_auto_decision(row, _t(reject_closed_eyes=False))
    assert decision != "reject"


# ---------------------------------------------------------------------------
# _all_faces_eyes_closed unit — malformed-JSON fallback
# ---------------------------------------------------------------------------


def test_all_faces_eyes_closed_malformed_json_single_face_falls_through_to_true():
    # Single face + caller-already-saw-eyes-closed → conservative "all closed".
    row = {"faces_eyes_open_json": "not-json"}
    assert _all_faces_eyes_closed(row, face_count=1) is True


def test_all_faces_eyes_closed_malformed_json_group_falls_through_to_false():
    # Group shot with malformed JSON → conservative "don't kill the group".
    row = {"faces_eyes_open_json": "not-json"}
    assert _all_faces_eyes_closed(row, face_count=3) is False


def test_all_faces_eyes_closed_empty_string_treated_as_missing():
    row = {"faces_eyes_open_json": ""}
    assert _all_faces_eyes_closed(row, face_count=3) is False


# ---------------------------------------------------------------------------
# soft face
# ---------------------------------------------------------------------------


def test_soft_face_rejects_when_frame_sharp_but_face_soft():
    row = _row(
        face_detected=True, face_count=1, eyes_open=1,
        sharpness_score=80, face_sharpness_score=20,
    )
    assert _compute_auto_decision(row, _t(reject_soft_face=True)) == (
        "reject", "soft_face",
    )


def test_soft_face_not_triggered_when_whole_frame_is_blurry():
    # If the whole frame is soft, blame the frame, not the face.
    row = _row(
        face_detected=True, face_count=1, eyes_open=1,
        sharpness_score=20, face_sharpness_score=20,
    )
    decision, reason = _compute_auto_decision(row, _t(reject_soft_face=True))
    assert reason != "soft_face"


# ---------------------------------------------------------------------------
# ISO ceiling — opt-in
# ---------------------------------------------------------------------------


def test_iso_above_ceiling_rejects():
    row = _row(iso=12800)
    t = _t(reject_above_iso_ceiling=True, iso_ceiling=6400)
    assert _compute_auto_decision(row, t) == ("reject", "iso_ceiling")


def test_iso_at_ceiling_does_not_reject():
    row = _row(iso=6400)
    t = _t(reject_above_iso_ceiling=True, iso_ceiling=6400)
    decision, _ = _compute_auto_decision(row, t)
    assert decision != "reject"


def test_iso_rule_off_does_not_reject_high_iso():
    row = _row(iso=25600)
    decision, _ = _compute_auto_decision(row, _t(reject_above_iso_ceiling=False))
    assert decision != "reject"


# ---------------------------------------------------------------------------
# reciprocal rule
# ---------------------------------------------------------------------------


def test_reciprocal_rule_rejects_when_shutter_slower_than_one_over_focal():
    # 50mm @ 1/30s — handholdable threshold is 1/50s, so 1/30 violates.
    row = _row(focal_length_mm=50.0, shutter_speed="1/30")
    assert _compute_auto_decision(row, _t(reject_reciprocal_rule=True)) == (
        "reject", "reciprocal_rule",
    )


def test_reciprocal_rule_passes_when_shutter_faster_than_one_over_focal():
    # 50mm @ 1/100s — comfortably within the rule.
    row = _row(focal_length_mm=50.0, shutter_speed="1/100")
    decision, _ = _compute_auto_decision(row, _t(reject_reciprocal_rule=True))
    assert decision != "reject"


def test_reciprocal_rule_silent_on_missing_exif():
    # No focal / no shutter → rule cannot fire; fall through to threshold logic.
    row = _row(focal_length_mm=None, shutter_speed=None, overall_score=80)
    decision, _ = _compute_auto_decision(row, _t(reject_reciprocal_rule=True))
    assert decision != "reject"


def test_reciprocal_rule_handles_unparseable_shutter_string():
    # Garbage shutter string must not crash the rule — silently skip.
    row = _row(focal_length_mm=50.0, shutter_speed="garbage", overall_score=80)
    decision, reason = _compute_auto_decision(row, _t(reject_reciprocal_rule=True))
    assert reason != "reciprocal_rule"


# ---------------------------------------------------------------------------
# default-value pins (caught when someone "rounds" a default by eye)
# ---------------------------------------------------------------------------


def test_background_distraction_ceiling_default_is_0_65():
    """Pinned 2026-05-12 after the SigLIP-2 60-photo distribution audit
    (p90=0.49, p99=0.61, max=0.75; 1.7% of photos trip 0.65). Changing this
    without re-measuring the SigLIP-* distribution will silently over- or
    under-reject face photos with cluttered backgrounds."""
    from phase1_technical.quality_analyzer import DEFAULT_BACKGROUND_DISTRACTION_CEILING
    assert DEFAULT_BACKGROUND_DISTRACTION_CEILING == 0.65


# ---------------------------------------------------------------------------
# background distraction (SigLIP) — face-photo-only
# ---------------------------------------------------------------------------


def test_background_distraction_rejects_face_photo_with_busy_bg():
    # Score 0.75 picked from the SigLIP-2 60-photo audit (2026-05-12) max=0.753 —
    # the upper outlier of the distribution. Trips the 0.65 default ceiling.
    row = _row(face_detected=True, face_count=1, background_distraction_score=0.75)
    t = _t(reject_high_background_distraction=True, background_distraction_ceiling=0.65)
    assert _compute_auto_decision(row, t) == ("reject", "cluttered_background")


def test_background_distraction_does_not_fire_on_landscape():
    # Same high BD score but no face — rule must NOT fire (landscape).
    row = _row(face_detected=False, background_distraction_score=0.75, overall_score=80)
    t = _t(reject_high_background_distraction=True, background_distraction_ceiling=0.65)
    decision, reason = _compute_auto_decision(row, t)
    assert reason != "cluttered_background"


# ---------------------------------------------------------------------------
# threshold fallback (no personal model)
# ---------------------------------------------------------------------------


def test_threshold_keep_at_high_overall():
    row = _row(overall_score=85, sharpness_score=80)
    assert _compute_auto_decision(row, _t()) == ("keep", "threshold_keep")


def test_threshold_maybe_in_band():
    row = _row(overall_score=60, sharpness_score=65)
    assert _compute_auto_decision(row, _t()) == ("maybe", "threshold_maybe")


def test_threshold_reject_below_band():
    row = _row(overall_score=40, sharpness_score=50)
    assert _compute_auto_decision(row, _t()) == ("reject", "threshold_reject")


def test_blurry_frame_rule_fires_before_threshold():
    # overall would be a "keep" but sharpness is below floor with the rule on.
    row = _row(overall_score=80, sharpness_score=30)
    t = _t(reject_blurry_frame=True, fallback_sharpness_floor=40)
    assert _compute_auto_decision(row, t) == ("reject", "blurry_frame")


# ---------------------------------------------------------------------------
# personal model path
# ---------------------------------------------------------------------------


def test_personal_model_keep():
    row = _row(personal_score=85, overall_score=40)  # overall low — model must win
    with patch(
        "backend.routers.decisions._personal_model.info",
        return_value={"model_status": "ready"},
    ):
        assert _compute_auto_decision(row, _t()) == ("keep", "personal_keep")


def test_personal_model_maybe():
    row = _row(personal_score=65, overall_score=40)
    with patch(
        "backend.routers.decisions._personal_model.info",
        return_value={"model_status": "ready"},
    ):
        assert _compute_auto_decision(row, _t()) == ("maybe", "personal_maybe")


def test_personal_model_reject():
    row = _row(personal_score=20, overall_score=90)  # overall high — model still wins
    with patch(
        "backend.routers.decisions._personal_model.info",
        return_value={"model_status": "ready"},
    ):
        assert _compute_auto_decision(row, _t()) == ("reject", "personal_reject")


def test_personal_model_instant_reject_wins_over_personal_score():
    # Closed eyes must always reject regardless of how well the model rates the photo.
    row = _row(
        face_detected=True, face_count=1, eyes_open=0, personal_score=95,
    )
    with patch(
        "backend.routers.decisions._personal_model.info",
        return_value={"model_status": "ready"},
    ):
        assert _compute_auto_decision(row, _t(reject_closed_eyes=True)) == (
            "reject", "closed_eyes",
        )


# ---------------------------------------------------------------------------
# PR3 — boundary routing via uncertainty ensemble
# ---------------------------------------------------------------------------


def test_uncertain_boundary_routes_to_maybe():
    """Score within ±std_dev of the maybe_threshold AND std_dev high enough →
    "maybe" with reason "uncertain"."""
    # keep_threshold=80, maybe_threshold=60 (per DEFAULT_T). Score 65 is 5 pts
    # above maybe_threshold; std=8 means the boundary is within range AND
    # crosses the uncertainty_threshold (8.0).
    row = _row(overall_score=40)
    t = _t(auto_cull_uncertain_to_maybe=True, uncertainty_threshold=8.0)
    with patch(
        "backend.routers.decisions._personal_model.info",
        return_value={"model_status": "ready"},
    ), patch(
        "backend.routers.decisions._personal_model.predict_with_uncertainty",
        return_value=(65.0, 8.0),
    ):
        assert _compute_auto_decision(row, t) == ("maybe", "uncertain")


def test_uncertain_disabled_skips_routing():
    """Same row, toggle off → falls through to the hard decision."""
    row = _row(overall_score=40)
    t = _t(auto_cull_uncertain_to_maybe=False, uncertainty_threshold=8.0)
    # When the toggle is off, the new code path consults predict_personal_score
    # (not predict_with_uncertainty), so we mock that one for this test.
    with patch(
        "backend.routers.decisions._personal_model.info",
        return_value={"model_status": "ready"},
    ), patch(
        "backend.routers.decisions._personal_model.predict_personal_score",
        return_value=65.0,
    ):
        # 65 is between maybe_threshold (60) and keep_threshold (80) → personal_maybe
        assert _compute_auto_decision(row, t) == ("maybe", "personal_maybe")


def test_uncertain_below_threshold_uses_hard_decision():
    """std_dev below uncertainty_threshold → original hard decision."""
    row = _row(overall_score=40)
    t = _t(auto_cull_uncertain_to_maybe=True, uncertainty_threshold=8.0)
    with patch(
        "backend.routers.decisions._personal_model.info",
        return_value={"model_status": "ready"},
    ), patch(
        "backend.routers.decisions._personal_model.predict_with_uncertainty",
        return_value=(65.0, 4.0),   # confident
    ):
        # 65 is between thresholds → personal_maybe (hard)
        assert _compute_auto_decision(row, t) == ("maybe", "personal_maybe")


def test_uncertain_does_not_override_instant_reject():
    """Closed-eyes still wins even with high uncertainty near boundary."""
    row = _row(face_detected=True, face_count=1, eyes_open=0, overall_score=40)
    t = _t(
        auto_cull_uncertain_to_maybe=True,
        uncertainty_threshold=8.0,
        reject_closed_eyes=True,
    )
    with patch(
        "backend.routers.decisions._personal_model.info",
        return_value={"model_status": "ready"},
    ), patch(
        "backend.routers.decisions._personal_model.predict_with_uncertainty",
        return_value=(65.0, 8.0),
    ):
        assert _compute_auto_decision(row, t) == ("reject", "closed_eyes")


def test_uncertain_routes_near_keep_threshold_too():
    """Boundary routing fires at the keep_threshold side as well."""
    row = _row(overall_score=40)
    t = _t(auto_cull_uncertain_to_maybe=True, uncertainty_threshold=8.0)
    # 78 is within 8 of keep_threshold=80 → route to maybe.
    with patch(
        "backend.routers.decisions._personal_model.info",
        return_value={"model_status": "ready"},
    ), patch(
        "backend.routers.decisions._personal_model.predict_with_uncertainty",
        return_value=(78.0, 8.0),
    ):
        assert _compute_auto_decision(row, t) == ("maybe", "uncertain")


def test_uncertain_far_from_boundary_passes_through():
    """High std_dev but well clear of both boundaries → hard decision."""
    row = _row(overall_score=40)
    t = _t(auto_cull_uncertain_to_maybe=True, uncertainty_threshold=8.0)
    # 95 is well above keep_threshold=80 and far from any boundary;
    # std=10 ≥ threshold but |95-80|=15 > 10 → keep wins.
    with patch(
        "backend.routers.decisions._personal_model.info",
        return_value={"model_status": "ready"},
    ), patch(
        "backend.routers.decisions._personal_model.predict_with_uncertainty",
        return_value=(95.0, 10.0),
    ):
        assert _compute_auto_decision(row, t) == ("keep", "personal_keep")
