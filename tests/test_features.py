"""
Priority 3 — Feature extractor invariants.

Tests verify the shape, NaN handling, and batch behavior of the
phase3_learning feature extractor without training any model or
touching the filesystem.
"""
import numpy as np
import pytest

from phase3_learning.feature_extractor import (
    extract,
    extract_batch,
    feature_names,
)

# The expected feature count as defined in feature_extractor._COLUMNS.
# 19 base + 8 binary scene labels + 4 SigLIP content axes (subject_prominence,
# background_distraction, eye_contact, decisive_moment — 2026-05-10) → 31.
EXPECTED_FEATURES = 31

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(**kwargs) -> dict:
    """Return a minimal images-table row dict with optional overrides."""
    base = {
        "sharpness_score":       75.0,
        "exposure_score":        68.0,
        "overall_score":         72.5,   # kept in row dict (used as delta base), but not a feature
        "iqa_score":             0.62,
        "aesthetic_score":       0.55,
        "highlight_clip_pct":    1.2,
        "shadow_clip_pct":       0.3,
        "shake_detected":        0,
        "face_detected":         1,
        "face_count":            1,
        "face_sharpness_score":  80.0,
        "eyes_open":             1,
        "eye_openness_ratio":    0.85,
        "face_size_ratio":       0.12,
        "focal_length_mm":       35.0,
        "aperture":              2.8,
        "iso":                   400,
    }
    base.update(kwargs)
    return base


def _make_rows(n: int) -> list[dict]:
    return [_make_row(sharpness_score=float(i)) for i in range(n)]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_extract_shape() -> None:
    """extract() returns a 1-D array of length EXPECTED_FEATURES."""
    row = _make_row()
    result = extract(row)
    assert result.ndim == 1
    assert len(result) == EXPECTED_FEATURES, (
        f"Expected {EXPECTED_FEATURES} features, got {len(result)}"
    )


def test_extract_dtype() -> None:
    """extract() returns a float32 array."""
    row = _make_row()
    result = extract(row)
    assert result.dtype == np.float32


def test_extract_nan_on_none() -> None:
    """Columns with None become np.nan — not 0, not some other sentinel."""
    row = _make_row(
        iqa_score=None,
        aesthetic_score=None,
        face_count=None,
    )
    result = extract(row)

    from phase3_learning.feature_extractor import feature_names as fn
    names = fn()
    iqa_idx       = names.index("iqa_score")
    aesthetic_idx = names.index("aesthetic_score")
    face_cnt_idx  = names.index("face_count")

    assert np.isnan(result[iqa_idx]),       "iqa_score=None should become NaN"
    assert np.isnan(result[aesthetic_idx]), "aesthetic_score=None should become NaN"
    assert np.isnan(result[face_cnt_idx]), "face_count=None should become NaN"


def test_extract_all_none_becomes_all_nan_except_computed_binaries() -> None:
    """
    A row where every column is None should produce an all-NaN vector
    except for computed binary features (face_present, scene_is_*) which are
    always 0.0 or 1.0 — they never propagate NaN so the imputer never has to
    fill them with group-mean values.
    """
    row = {k: None for k in feature_names()}
    result = extract(row)
    names = feature_names()
    for i, val in enumerate(result):
        is_computed_binary = names[i] == "face_present" or names[i].startswith("scene_is_")
        if is_computed_binary:
            assert not np.isnan(val), f"{names[i]} should never be NaN"
            assert val == 0.0, f"{names[i]} should be 0.0 when underlying column is None"
        else:
            assert np.isnan(val), f"Feature at index {i} ({names[i]}) should be NaN"


def test_extract_missing_keys_become_nan() -> None:
    """extract() treats missing dict keys the same as explicit None — NaN
    (except computed binary features: face_present + scene_is_*)."""
    result = extract({})
    names = feature_names()
    for i, val in enumerate(result):
        is_computed_binary = names[i] == "face_present" or names[i].startswith("scene_is_")
        if is_computed_binary:
            assert not np.isnan(val), f"{names[i]} should never be NaN"
        else:
            assert np.isnan(val), f"Index {i} ({names[i]}) should be NaN for missing key"


def test_extract_batch_shape() -> None:
    """extract_batch() of N rows returns shape (N, EXPECTED_FEATURES)."""
    rows = _make_rows(5)
    result = extract_batch(rows)
    assert result.shape == (5, EXPECTED_FEATURES), (
        f"Expected (5, {EXPECTED_FEATURES}), got {result.shape}"
    )


def test_extract_batch_empty() -> None:
    """extract_batch([]) returns shape (0, EXPECTED_FEATURES) — not an error."""
    result = extract_batch([])
    assert result.shape == (0, EXPECTED_FEATURES), (
        f"Expected (0, {EXPECTED_FEATURES}), got {result.shape}"
    )


def test_extract_batch_dtype() -> None:
    """extract_batch() returns a float32 array."""
    rows = _make_rows(3)
    result = extract_batch(rows)
    assert result.dtype == np.float32


def test_extract_batch_single_row() -> None:
    """extract_batch() with one row returns shape (1, 17)."""
    result = extract_batch([_make_row()])
    assert result.shape == (1, EXPECTED_FEATURES)


def test_feature_names_length() -> None:
    """feature_names() returns a list of exactly 17 strings."""
    names = feature_names()
    assert isinstance(names, list)
    assert len(names) == EXPECTED_FEATURES, (
        f"Expected {EXPECTED_FEATURES} feature names, got {len(names)}"
    )


def test_feature_names_all_strings() -> None:
    """Every element of feature_names() is a non-empty string."""
    for name in feature_names():
        assert isinstance(name, str) and name, f"Bad feature name: {name!r}"


def test_feature_names_no_duplicates() -> None:
    """feature_names() has no duplicate entries."""
    names = feature_names()
    assert len(names) == len(set(names)), "Duplicate feature names detected"


def test_extract_batch_values_match_extract() -> None:
    """Each row in extract_batch() matches the corresponding extract() call."""
    rows = _make_rows(4)
    batch = extract_batch(rows)
    for i, row in enumerate(rows):
        single = extract(row)
        np.testing.assert_array_equal(batch[i], single)


def test_overall_score_not_in_features() -> None:
    """overall_score must NOT appear in feature_names() — it's a linear combo
    of sharpness + exposure (both already present) and causes double-weighting
    of the technical axis."""
    assert "overall_score" not in feature_names(), (
        "overall_score is redundant (= sharpness*0.65 + exposure*0.35) and "
        "must not be a feature input to the GBR."
    )


def test_face_present_computed_from_face_detected() -> None:
    """face_present is 1.0 when face_detected==1, else 0.0 — never NaN."""
    names = feature_names()
    fp_idx = names.index("face_present")

    # face_detected = 1 → face_present = 1.0
    row_with_face = _make_row(face_detected=1)
    vec = extract(row_with_face)
    assert vec[fp_idx] == 1.0, "face_present should be 1.0 when face_detected=1"
    assert not np.isnan(vec[fp_idx])

    # face_detected = 0 → face_present = 0.0
    row_no_face = _make_row(face_detected=0)
    vec = extract(row_no_face)
    assert vec[fp_idx] == 0.0, "face_present should be 0.0 when face_detected=0"
    assert not np.isnan(vec[fp_idx])

    # face_detected = None → face_present = 0.0 (not NaN, not 1.0)
    row_null_face = _make_row(face_detected=None)
    vec = extract(row_null_face)
    assert vec[fp_idx] == 0.0, "face_present should be 0.0 when face_detected=None"
    assert not np.isnan(vec[fp_idx])
