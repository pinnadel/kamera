"""
Priority 4 — Personal model invariants.

Tests verify training guards, prediction range, save/load round-trip,
and schema-mismatch detection. All tests use synthetic in-memory data;
no real photo files or the production DB are touched.
"""
import pickle
from pathlib import Path

import numpy as np
import pytest
from sklearn.pipeline import Pipeline

from phase3_learning.personal_model import MIN_DECISIONS, PersonalModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(overall_score: float = 70.0, **kwargs) -> dict:
    """Return a minimal but complete images-table row dict."""
    base = {
        "sharpness_score":       75.0,
        "exposure_score":        68.0,
        "overall_score":         overall_score,
        "iqa_score":             0.60,
        "aesthetic_score":       0.50,
        "highlight_clip_pct":    1.0,
        "shadow_clip_pct":       0.5,
        "shake_detected":        0,
        "face_detected":         1,
        "face_count":            1,
        "face_sharpness_score":  80.0,
        "eyes_open":             1,
        "eye_openness_ratio":    0.80,
        "face_size_ratio":       0.10,
        "focal_length_mm":       35.0,
        "aperture":              2.8,
        "iso":                   400,
    }
    base.update(kwargs)
    return base


def _make_training_data(n: int = MIN_DECISIONS) -> tuple[list[dict], list[str]]:
    """
    Build n synthetic rows with alternating keep/maybe/reject labels
    so the GBR has something meaningful to learn without diverging.
    """
    decision_cycle = ["keep", "maybe", "reject"]
    rows = []
    decisions = []
    rng = np.random.default_rng(seed=0)
    for i in range(n):
        score = float(rng.uniform(20, 95))
        rows.append(_make_row(overall_score=score))
        decisions.append(decision_cycle[i % 3])
    return rows, decisions


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_model_not_ready_initially() -> None:
    """A freshly created PersonalModel has ready=False."""
    model = PersonalModel()
    assert model.ready is False


def test_model_training_size_zero_initially() -> None:
    """A freshly created PersonalModel reports training_size=0."""
    model = PersonalModel()
    assert model.training_size == 0


def test_model_predict_returns_none_when_not_ready() -> None:
    """predict_personal_score() returns None when the model hasn't been trained."""
    model = PersonalModel()
    result = model.predict_personal_score(_make_row())
    assert result is None


def test_model_train_below_min_raises() -> None:
    """train() with fewer than MIN_DECISIONS samples raises ValueError."""
    model = PersonalModel()
    rows, decisions = _make_training_data(MIN_DECISIONS - 1)
    with pytest.raises(ValueError, match=str(MIN_DECISIONS)):
        model.train(rows, decisions)


def test_model_train_mismatched_lengths_raises() -> None:
    """train() raises ValueError when rows and decisions have different lengths."""
    model = PersonalModel()
    rows, decisions = _make_training_data(MIN_DECISIONS)
    with pytest.raises(ValueError):
        model.train(rows, decisions[:-1])  # one fewer decision than rows


def test_model_train_and_predict() -> None:
    """
    train() with MIN_DECISIONS synthetic rows sets ready=True.
    predict_personal_score() returns a float in [0, 100].
    """
    model = PersonalModel()
    rows, decisions = _make_training_data(MIN_DECISIONS)
    meta = model.train(rows, decisions)

    assert model.ready is True
    assert model.training_size == MIN_DECISIONS
    assert "trained_at" in meta
    assert "feature_importances" in meta

    score = model.predict_personal_score(_make_row(overall_score=75.0))
    assert score is not None
    assert isinstance(score, float)
    assert 0.0 <= score <= 100.0, f"Score out of range: {score}"


def test_model_predict_returns_none_for_none_overall_score() -> None:
    """predict_personal_score() returns None when overall_score is None."""
    model = PersonalModel()
    rows, decisions = _make_training_data(MIN_DECISIONS)
    model.train(rows, decisions)

    row = _make_row(overall_score=None)
    result = model.predict_personal_score(row)
    assert result is None


def test_model_predict_clamps_to_0_100() -> None:
    """The clamping logic keeps predictions within [0, 100] even at extremes."""
    model = PersonalModel()
    rows, decisions = _make_training_data(30)
    model.train(rows, decisions)

    # Try boundary overall_scores — the delta should not push results out of range.
    for overall in [0.0, 1.0, 99.0, 100.0]:
        score = model.predict_personal_score(_make_row(overall_score=overall))
        if score is not None:
            assert 0.0 <= score <= 100.0, f"Score {score} out of [0,100] for overall={overall}"


def test_model_save_load_roundtrip(tmp_path: Path) -> None:
    """
    save() to a temp file, then load() into a fresh PersonalModel.
    The loaded model should be ready and produce the same prediction.
    """
    model_a = PersonalModel()
    rows, decisions = _make_training_data(MIN_DECISIONS)
    model_a.train(rows, decisions)

    pkl_path = tmp_path / "personal_model.pkl"
    model_a.save(pkl_path)

    model_b = PersonalModel()
    assert model_b.ready is False

    success = model_b.load(pkl_path)
    assert success is True
    assert model_b.ready is True
    assert model_b.training_size == MIN_DECISIONS

    # Both models should agree on the same prediction (same pipeline weights).
    test_row = _make_row(overall_score=65.0)
    score_a = model_a.predict_personal_score(test_row)
    score_b = model_b.predict_personal_score(test_row)
    assert score_a is not None and score_b is not None
    assert abs(score_a - score_b) < 1e-4, (
        f"Score mismatch after round-trip: {score_a} vs {score_b}"
    )


def test_model_load_returns_false_for_missing_file(tmp_path: Path) -> None:
    """load() returns False (not raises) when the pickle file doesn't exist."""
    model = PersonalModel()
    result = model.load(tmp_path / "nonexistent.pkl")
    assert result is False
    assert model.ready is False


def test_model_load_wrong_feature_count(tmp_path: Path) -> None:
    """
    A pickle whose pipeline was trained on a different number of features
    causes load() to return False without crashing.

    Current schema: 17 features (overall_score replaced by face_present).
    Any pipeline trained on a different count (e.g. 5) must be rejected.
    """
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler

    # Build a pipeline trained on only 5 features — clearly wrong schema.
    wrong_pipeline = Pipeline([
        ("imputer", SimpleImputer(strategy="mean")),
        ("scaler",  StandardScaler()),
        ("gbr",     GradientBoostingRegressor(n_estimators=10, random_state=42)),
    ])
    X_wrong = np.random.default_rng(1).random((25, 5)).astype(np.float32)
    y_wrong = np.random.default_rng(1).uniform(-1, 1, 25).astype(np.float32)
    wrong_pipeline.fit(X_wrong, y_wrong)

    pkl_path = tmp_path / "stale_model.pkl"
    with open(pkl_path, "wb") as f:
        pickle.dump({"pipeline": wrong_pipeline, "meta": {}}, f)

    model = PersonalModel()
    result = model.load(pkl_path)
    assert result is False, (
        "load() should return False for a pipeline with wrong n_features_in_"
    )
    assert model.ready is False


def test_model_load_wrong_feature_schema_version(tmp_path: Path) -> None:
    """
    A pickle whose meta.feature_schema_version is older than the current
    constant causes load() to return False without crashing.

    Catches scorer-swap scenarios (e.g. PR1 LAION → TOPIQ-IAA, schema 4 → 5)
    where the feature *count* is unchanged but the underlying score
    distribution has changed.
    """
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler
    from phase3_learning.feature_extractor import FEATURE_SCHEMA_VERSION, feature_names

    n_features = len(feature_names())
    pipeline = Pipeline([
        ("imputer", SimpleImputer(strategy="mean")),
        ("scaler",  StandardScaler()),
        ("gbr",     GradientBoostingRegressor(n_estimators=10, random_state=42)),
    ])
    X = np.random.default_rng(1).random((25, n_features)).astype(np.float32)
    y = np.random.default_rng(1).uniform(-1, 1, 25).astype(np.float32)
    pipeline.fit(X, y)

    pkl_path = tmp_path / "old_schema_model.pkl"
    with open(pkl_path, "wb") as f:
        pickle.dump(
            {"pipeline": pipeline, "meta": {"feature_schema_version": FEATURE_SCHEMA_VERSION - 1}},
            f,
        )

    model = PersonalModel()
    result = model.load(pkl_path)
    assert result is False, (
        "load() should return False for a pickle with stale feature_schema_version"
    )
    assert model.ready is False


def test_model_load_missing_feature_schema_version(tmp_path: Path) -> None:
    """
    A legacy pickle that predates the feature_schema_version meta field is
    refused — absent field is treated identically to a stale version.
    """
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler
    from phase3_learning.feature_extractor import feature_names

    n_features = len(feature_names())
    pipeline = Pipeline([
        ("imputer", SimpleImputer(strategy="mean")),
        ("scaler",  StandardScaler()),
        ("gbr",     GradientBoostingRegressor(n_estimators=10, random_state=42)),
    ])
    X = np.random.default_rng(2).random((25, n_features)).astype(np.float32)
    y = np.random.default_rng(2).uniform(-1, 1, 25).astype(np.float32)
    pipeline.fit(X, y)

    pkl_path = tmp_path / "legacy_model.pkl"
    with open(pkl_path, "wb") as f:
        pickle.dump({"pipeline": pipeline, "meta": {}}, f)

    model = PersonalModel()
    assert model.load(pkl_path) is False
    assert model.ready is False


def test_model_cache_cleared_after_retrain() -> None:
    """The internal score cache is empty after train() is called."""
    model = PersonalModel()
    rows, decisions = _make_training_data(MIN_DECISIONS)
    model.train(rows, decisions)

    # Populate the cache by calling predict_batch with rows that have id keys.
    rows_with_ids = [dict(r, id=i) for i, r in enumerate(rows)]
    model.predict_batch(rows_with_ids)
    assert len(model._cache) > 0  # sanity-check: cache was populated

    # Retrain — cache must be cleared.
    model.train(rows, decisions)
    assert len(model._cache) == 0, "Cache should be empty after retrain"


def test_model_invalidate_removes_entry() -> None:
    """invalidate(image_id) removes that id from the cache."""
    model = PersonalModel()
    rows, decisions = _make_training_data(MIN_DECISIONS)
    model.train(rows, decisions)

    rows_with_ids = [dict(r, id=i) for i, r in enumerate(rows[:5])]
    model.predict_batch(rows_with_ids)

    # id=0 should now be cached.
    assert 0 in model._cache
    model.invalidate(0)
    assert 0 not in model._cache


# ---------------------------------------------------------------------------
# Validation gating tests
# ---------------------------------------------------------------------------

def make_row(i: int) -> dict:
    """Minimal row dict for validation tests (reuses _make_row internally)."""
    import numpy as _np
    rng = _np.random.default_rng(seed=i)
    score = float(rng.uniform(20, 95))
    return _make_row(overall_score=score)


def test_model_train_returns_validation() -> None:
    """train() metadata includes a 'validation' dict with expected keys."""
    model = PersonalModel()
    rows = [make_row(i) for i in range(MIN_DECISIONS)]
    decisions = ["keep"] * 10 + ["maybe"] * 10 + ["reject"] * 10
    meta = model.train(rows, decisions)

    assert "validation" in meta
    v = meta["validation"]
    assert "model_accuracy" in v
    assert "baseline_accuracy" in v
    assert "beats_baseline" in v
    assert "margin" in v
    assert 0.0 <= v["model_accuracy"] <= 1.0
    assert 0.0 <= v["baseline_accuracy"] <= 1.0


def test_model_status_learning_below_50() -> None:
    """model_status is 'learning' when training_size < 50."""
    model = PersonalModel()
    rows = [make_row(i) for i in range(MIN_DECISIONS)]
    decisions = ["keep"] * 10 + ["maybe"] * 10 + ["reject"] * 10
    model.train(rows, decisions)
    info = model.info()
    assert info["model_status"] == "learning"


def test_model_beats_baseline_property() -> None:
    """beats_baseline property is a bool."""
    model = PersonalModel()
    rows = [make_row(i) for i in range(MIN_DECISIONS)]
    decisions = ["keep"] * 10 + ["maybe"] * 10 + ["reject"] * 10
    model.train(rows, decisions)
    assert isinstance(model.beats_baseline, bool)


def test_model_info_includes_validation() -> None:
    """info() dict includes model_status, validation, and beats_baseline."""
    model = PersonalModel()
    rows = [make_row(i) for i in range(MIN_DECISIONS)]
    decisions = ["keep"] * 10 + ["maybe"] * 10 + ["reject"] * 10
    model.train(rows, decisions)
    info = model.info()
    assert "model_status" in info
    assert "validation" in info
    assert "beats_baseline" in info


# ---------------------------------------------------------------------------
# PR3 — Uncertainty ensemble
# ---------------------------------------------------------------------------

def test_predict_with_uncertainty_returns_score_and_std() -> None:
    """After train(), predict_with_uncertainty returns (score, std≥0)."""
    model = PersonalModel()
    rows = [make_row(i) for i in range(MIN_DECISIONS)]
    decisions = ["keep"] * 10 + ["maybe"] * 10 + ["reject"] * 10
    model.train(rows, decisions)

    result = model.predict_with_uncertainty(_make_row(overall_score=70.0))
    assert result is not None
    score, std = result
    assert 0.0 <= score <= 100.0
    assert std >= 0.0


def test_predict_with_uncertainty_returns_none_when_not_ready() -> None:
    model = PersonalModel()
    assert model.predict_with_uncertainty(_make_row()) is None


def test_predict_with_uncertainty_returns_none_for_none_overall() -> None:
    model = PersonalModel()
    rows = [make_row(i) for i in range(MIN_DECISIONS)]
    decisions = ["keep"] * 10 + ["maybe"] * 10 + ["reject"] * 10
    model.train(rows, decisions)
    assert model.predict_with_uncertainty(_make_row(overall_score=None)) is None


def test_predict_batch_with_uncertainty_shape() -> None:
    """Batch path returns one tuple per row, None for rows without overall_score."""
    model = PersonalModel()
    rows = [make_row(i) for i in range(MIN_DECISIONS)]
    decisions = ["keep"] * 10 + ["maybe"] * 10 + ["reject"] * 10
    model.train(rows, decisions)

    test_rows = [
        _make_row(overall_score=70.0),
        _make_row(overall_score=None),
        _make_row(overall_score=85.0),
    ]
    out = model.predict_batch_with_uncertainty(test_rows)
    assert len(out) == 3
    assert out[0] is not None and len(out[0]) == 2
    assert out[1] is None
    assert out[2] is not None and len(out[2]) == 2


def test_save_load_roundtrip_with_ensemble(tmp_path: Path) -> None:
    """After save+load, predict_with_uncertainty matches the in-memory model."""
    model = PersonalModel()
    rows = [make_row(i) for i in range(MIN_DECISIONS)]
    decisions = ["keep"] * 10 + ["maybe"] * 10 + ["reject"] * 10
    model.train(rows, decisions)

    pkl = tmp_path / "model.pkl"
    model.save(pkl)

    loaded = PersonalModel()
    assert loaded.load(pkl) is True
    test_row = _make_row(overall_score=72.0)
    a = model.predict_with_uncertainty(test_row)
    b = loaded.predict_with_uncertainty(test_row)
    assert a is not None and b is not None
    assert abs(a[0] - b[0]) < 1e-3
    assert abs(a[1] - b[1]) < 1e-3


def test_load_pickle_without_ensemble_is_tolerant(tmp_path: Path) -> None:
    """A pre-PR3 pickle (no 'uncertainty_ensemble' key) loads, and
    predict_with_uncertainty returns (score, 0.0) — the boundary router
    treats 0.0 std as "no signal" and falls through to the hard decision."""
    from phase3_learning.feature_extractor import FEATURE_SCHEMA_VERSION

    # Train normally to get a fitted pipeline + meta, then save a pre-PR3
    # shape pickle (no ensemble key) by hand.
    model = PersonalModel()
    rows = [make_row(i) for i in range(MIN_DECISIONS)]
    decisions = ["keep"] * 10 + ["maybe"] * 10 + ["reject"] * 10
    model.train(rows, decisions)

    pkl = tmp_path / "old.pkl"
    with open(pkl, "wb") as f:
        pickle.dump({
            "pipeline": model._pipeline,
            "meta": {**model._meta, "feature_schema_version": FEATURE_SCHEMA_VERSION},
            # NB: intentionally no "uncertainty_ensemble" key
        }, f)

    loaded = PersonalModel()
    assert loaded.load(pkl) is True
    assert loaded._uncertainty_ensemble == []
    result = loaded.predict_with_uncertainty(_make_row(overall_score=70.0))
    assert result is not None
    score, std = result
    assert 0.0 <= score <= 100.0
    assert std == 0.0
