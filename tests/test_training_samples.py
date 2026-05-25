"""
Durable training_samples corpus.

These tests verify the load-bearing claim: decisions captured into
training_samples survive Clear Analysis (DELETE FROM images / decisions)
and the photo file leaving the project. The personal model can be
retrained from training_samples alone, even after the source images
have been moved or deleted.
"""
import json
from pathlib import Path

import numpy as np

from backend.database import create_tables, get_db, get_schema_version
from phase3_learning.feature_extractor import (
    FEATURE_SCHEMA_VERSION,
    feature_names,
    features_from_json,
    serialize_features,
)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_training_samples_table_exists(tmp_db: Path) -> None:
    """The training_samples table is present after migrations run."""
    with get_db(tmp_db) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='training_samples'"
        ).fetchall()
    assert len(rows) == 1


def test_training_samples_uuid_unique(tmp_db: Path) -> None:
    """sample_uuid is UNIQUE — second insert with same uuid replaces, not duplicates."""
    payload = serialize_features({"sharpness_score": 50, "exposure_score": 60})
    with get_db(tmp_db) as conn:
        conn.execute(
            """INSERT INTO training_samples
               (sample_uuid, decision, features_json, overall_score, schema_version)
               VALUES (?, ?, ?, ?, ?)""",
            ("uuid-A", "keep", payload, 70.0, FEATURE_SCHEMA_VERSION),
        )
        # Same uuid, different decision → use ON CONFLICT to update.
        conn.execute(
            """INSERT INTO training_samples
               (sample_uuid, decision, features_json, overall_score, schema_version)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(sample_uuid) DO UPDATE SET decision = excluded.decision""",
            ("uuid-A", "reject", payload, 70.0, FEATURE_SCHEMA_VERSION),
        )
        rows = conn.execute(
            "SELECT decision FROM training_samples WHERE sample_uuid = ?",
            ("uuid-A",),
        ).fetchall()
    assert len(rows) == 1, "ON CONFLICT replace should keep one row, not two"
    assert rows[0]["decision"] == "reject"


def test_schema_version_includes_training_samples(tmp_db: Path) -> None:
    """Schema migration registry includes the training_samples migration (v21)."""
    version = get_schema_version(tmp_db)
    assert version >= 21


# ---------------------------------------------------------------------------
# Feature serialization
# ---------------------------------------------------------------------------

def test_serialize_features_roundtrip() -> None:
    """A row → JSON → array roundtrip preserves the feature vector."""
    row = {
        "sharpness_score": 80.0, "exposure_score": 70.0,
        "iqa_score": 0.5, "aesthetic_score": 0.4,
        "highlight_clip_pct": 1.2, "shadow_clip_pct": 0.3,
        "shake_detected": 0, "face_detected": 1, "face_count": 1,
        "face_sharpness_score": 75.0, "eyes_open": 1, "eye_openness_ratio": 0.85,
        "face_size_ratio": 0.12, "focal_length_mm": 50.0, "aperture": 2.0, "iso": 200,
    }
    payload = serialize_features(row)
    arr = features_from_json(payload)
    assert arr.shape == (len(feature_names()),)
    # The 'face_present' synthetic feature should be 1.0 because face_detected=1.
    idx = feature_names().index("face_present")
    assert arr[idx] == 1.0


def test_serialize_features_handles_none() -> None:
    """None values become null in JSON and NaN in the rebuilt array."""
    row = {"sharpness_score": None, "exposure_score": 50.0}
    payload = serialize_features(row)
    data = json.loads(payload)
    assert data["sharpness_score"] is None
    arr = features_from_json(payload)
    idx = feature_names().index("sharpness_score")
    assert np.isnan(arr[idx])


def test_features_from_json_pads_missing_columns() -> None:
    """
    If a saved sample is missing a column (e.g. it was captured before that
    column existed in _COLUMNS), features_from_json fills it with NaN so the
    SimpleImputer can take over at fit time.
    """
    # Hand-craft a JSON payload that's missing several current columns.
    legacy_payload = json.dumps({
        "sharpness_score": 70.0,
        "exposure_score": 65.0,
        # everything else absent
    })
    arr = features_from_json(legacy_payload)
    assert arr.shape == (len(feature_names()),)

    # The two present columns should be exact; everything else NaN.
    idx_sharp = feature_names().index("sharpness_score")
    idx_exp = feature_names().index("exposure_score")
    assert arr[idx_sharp] == 70.0
    assert arr[idx_exp] == 65.0

    # Spot-check that a non-present column came back as NaN.
    idx_iqa = feature_names().index("iqa_score")
    assert np.isnan(arr[idx_iqa])


def test_features_from_json_ignores_unknown_keys() -> None:
    """Unknown keys (features removed from a newer schema) are silently dropped."""
    payload = json.dumps({
        "sharpness_score": 60.0,
        "long_dead_feature_xyz": 999.0,  # not in current _COLUMNS
    })
    arr = features_from_json(payload)
    assert arr.shape == (len(feature_names()),)
    # No exception raised; the bogus key is just ignored.


# ---------------------------------------------------------------------------
# Train from samples (durability claim)
# ---------------------------------------------------------------------------

def test_train_from_samples_works_with_padded_legacy_rows() -> None:
    """
    The personal model can train on a mix of full-schema and legacy-schema
    rows — proving that feature-schema bumps don't invalidate past labels.
    """
    from phase3_learning.personal_model import MIN_DECISIONS, PersonalModel

    rng = np.random.default_rng(42)
    decisions = ["keep", "maybe", "reject"] * (MIN_DECISIONS // 3 + 1)
    decisions = decisions[:MIN_DECISIONS]

    # Half the rows have all features; half have a stripped-down legacy payload.
    payloads = []
    for i in range(MIN_DECISIONS):
        if i % 2 == 0:
            row = {col: float(rng.uniform(0, 100)) for col in feature_names() if col != "face_present"}
            row["face_detected"] = int(rng.integers(0, 2))
            payloads.append(serialize_features(row))
        else:
            payloads.append(json.dumps({
                "sharpness_score": float(rng.uniform(40, 90)),
                "exposure_score":  float(rng.uniform(40, 90)),
            }))

    feature_vectors = np.vstack([features_from_json(p) for p in payloads])
    overall_scores = [float(rng.uniform(20, 90)) for _ in range(MIN_DECISIONS)]

    model = PersonalModel()
    meta = model.train_from_samples(feature_vectors, decisions, overall_scores)
    assert model.ready
    assert meta["training_size"] == MIN_DECISIONS
