"""Tests for backend/group_scoring.py — the shared priority helper used by
the similarity-group hero picker AND the burst-rank top-N pre-filter.

The priority is the canonical "best of group" order: face_sharpness (when a
face is detected) → eyes_open → frame sharpness → IQA → aesthetic →
overall_score. These tests pin that order down so a future refactor can't
silently change which photo wins.
"""

from backend.group_scoring import (
    compute_best_reason,
    score_candidate,
    top_n_candidates,
)


def _img(**overrides):
    """Build a defaulted image dict, override the fields a test cares about."""
    base = {
        "id": 1,
        "face_detected": 0,
        "face_sharpness_score": None,
        "eyes_open": None,
        "sharpness_score": None,
        "iqa_score": None,
        "aesthetic_score": None,
        "overall_score": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# compute_best_reason
# ---------------------------------------------------------------------------


def test_compute_best_reason_face_sharpness_wins():
    """When the hero has the sharpest face among face-detected photos, the
    reason should call that out — face quality dominates the priority order."""
    best = _img(id=1, face_detected=1, face_sharpness_score=80, overall_score=70)
    others = [
        _img(id=2, face_detected=1, face_sharpness_score=40, overall_score=72),
        _img(id=3, face_detected=1, face_sharpness_score=50, overall_score=68),
    ]
    reason = compute_best_reason(best, [best] + others, user_override=False)
    assert "Sharpest face" in reason


def test_compute_best_reason_user_override_short_circuits():
    """A user override always returns the same short copy regardless of scores."""
    best = _img(id=1, overall_score=10)
    others = [_img(id=2, overall_score=99)]
    assert compute_best_reason(best, [best] + others, user_override=True) == "Your pick"


def test_compute_best_reason_only_photo_in_group():
    best = _img(id=1)
    assert compute_best_reason(best, [best], user_override=False) == "Only photo in group"


def test_compute_best_reason_falls_through_to_overall():
    """With no distinguishing face/sharpness/IQA/aesthetic signal, the
    explanation falls through to 'Highest overall score'."""
    best = _img(id=1, overall_score=80)
    others = [_img(id=2, overall_score=60)]
    assert compute_best_reason(best, [best] + others, user_override=False) == "Highest overall score"


# ---------------------------------------------------------------------------
# score_candidate
# ---------------------------------------------------------------------------


def test_score_candidate_face_dominates_overall():
    """Face sharpness sits at tuple slot 0 — it must beat a higher overall_score."""
    high_face = _img(id=1, face_detected=1, face_sharpness_score=80, overall_score=20)
    high_overall = _img(id=2, face_detected=1, face_sharpness_score=10, overall_score=95)
    assert score_candidate(high_face) > score_candidate(high_overall)


def test_score_candidate_eyes_open_beats_sharpness_when_face_ties():
    """With identical face sharpness, eyes_open is the next tiebreaker."""
    eyes_open = _img(id=1, face_detected=1, face_sharpness_score=50,
                     eyes_open=1, sharpness_score=10)
    eyes_closed = _img(id=2, face_detected=1, face_sharpness_score=50,
                       eyes_open=0, sharpness_score=90)
    assert score_candidate(eyes_open) > score_candidate(eyes_closed)


def test_score_candidate_face_only_counts_when_face_detected():
    """face_sharpness_score is suppressed to 0 unless face_detected=1, so a
    photo with no detected face can't ride a stale face score to the top."""
    no_face = _img(id=1, face_detected=0, face_sharpness_score=99,
                   sharpness_score=10)
    has_face = _img(id=2, face_detected=1, face_sharpness_score=50,
                    sharpness_score=10)
    assert score_candidate(has_face) > score_candidate(no_face)


def test_score_candidate_handles_none_without_raising():
    """NULL scores (from SQLite or freshly-analyzed rows with partial data)
    must coerce to 0 without raising — pre-filter runs on whatever the DB
    has, including partially-analysed bursts."""
    empty = _img(id=1)  # everything None
    assert score_candidate(empty) == (0.0, 0, 0.0, 0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# top_n_candidates
# ---------------------------------------------------------------------------


def test_top_n_returns_full_list_when_n_exceeds_input():
    images = [_img(id=i, overall_score=i) for i in range(3)]
    assert len(top_n_candidates(images, 10)) == 3


def test_top_n_picks_highest_by_priority():
    images = [
        _img(id=1, overall_score=10),
        _img(id=2, face_detected=1, face_sharpness_score=80, overall_score=5),  # winner
        _img(id=3, overall_score=90),  # high overall, but no face → loses to id=2
    ]
    top = top_n_candidates(images, 1)
    assert [im["id"] for im in top] == [2]


def test_top_n_stable_on_exact_ties():
    """sorted() is stable, so two photos with identical score tuples preserve
    input order. Important so cache keys stay deterministic across runs."""
    a = _img(id=1, overall_score=50)
    b = _img(id=2, overall_score=50)
    c = _img(id=3, overall_score=50)
    top = top_n_candidates([a, b, c], 2)
    assert [im["id"] for im in top] == [1, 2]


def test_top_n_handles_partial_nulls():
    """A photo with NULL scores sorts to the bottom but doesn't crash the call."""
    populated = _img(id=1, face_detected=1, face_sharpness_score=70)
    empty = _img(id=2)
    top = top_n_candidates([empty, populated], 1)
    assert top[0]["id"] == 1
