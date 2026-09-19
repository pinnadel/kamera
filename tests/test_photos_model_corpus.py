"""
Two personal models, one per training corpus.

Covers the seam added when the Photos corpus got its own model: per-corpus
pickles, per-corpus training floors, the "absolute" scoring mode that does not
consult overall_score, corpus-scoped settings lookup, and the corpus filters
that keep camera and iPhone data from leaking into each other's training set.

The leak tests matter most: training_samples has a `corpus` column but
pairwise_comparisons does not, so nothing in the schema stops a camera A/B
comparison from being folded into the Photos fit.
"""
from pathlib import Path
from typing import Iterator

import numpy as np
import pytest

from backend.database import get_db
from phase1_technical import quality_analyzer as qa
from phase3_learning import auto_trainer
from phase3_learning.feature_extractor import (
    FEATURE_SCHEMA_VERSION,
    feature_names,
    serialize_features,
)
from phase3_learning.personal_model import (
    MIN_DECISIONS,
    PersonalModel,
    min_decisions_for,
    model_path_for,
)


@pytest.fixture(autouse=True)
def _patch_db(monkeypatch: pytest.MonkeyPatch, tmp_db: Path) -> Iterator[None]:
    from backend import database as _db_mod

    def _scoped_get_db(db_path: Path = tmp_db):
        return _db_mod.get_db(db_path)

    monkeypatch.setattr(auto_trainer, "get_db", _scoped_get_db)
    auto_trainer._running = {}
    auto_trainer._last_auto_train_at = {}
    yield


def _seed_samples(db_path: Path, n: int, corpus: str, *, prefix: str = "s") -> None:
    rng = np.random.default_rng(seed=0)
    cycle = ["keep", "maybe", "reject"]
    rows = []
    for i in range(n):
        feature_row = {
            col: float(rng.uniform(0, 100))
            for col in feature_names() if col != "face_present"
        }
        feature_row["face_detected"] = int(rng.integers(0, 2))
        rows.append((
            f"{corpus}-{prefix}-{i:04d}",
            cycle[i % 3],
            serialize_features(feature_row),
            float(rng.uniform(20, 95)),
            FEATURE_SCHEMA_VERSION,
            corpus,
        ))
    with get_db(db_path) as conn:
        conn.executemany(
            """INSERT INTO training_samples
                 (sample_uuid, decision, features_json, overall_score,
                  schema_version, corpus, decided_at)
               VALUES (?, ?, ?, ?, ?, ?, DATETIME('now'))""",
            rows,
        )


def _seed_image(db_path: Path, image_id: int, source_type: str) -> None:
    with get_db(db_path) as conn:
        conn.execute(
            """INSERT INTO images
                 (id, file_path, filename, source_type, source_folder,
                  sharpness_score, exposure_score, overall_score, analysis_status)
               VALUES (?, ?, ?, ?, ?, 50.0, 80.0, 60.0, 'done')""",
            (image_id, f"/tmp/{source_type}-{image_id}.raf",
             f"{source_type}-{image_id}.raf", source_type, f"/tmp/{source_type}"),
        )


# ---------------------------------------------------------------------------
# Identity: each corpus is its own model
# ---------------------------------------------------------------------------

def test_folder_keeps_the_original_pickle_path() -> None:
    """Renaming folder's pickle would orphan every existing installation."""
    assert model_path_for("folder").name == "personal_model.pkl"
    assert model_path_for("photos").name == "personal_model_photos.pkl"
    assert model_path_for("folder") != model_path_for("photos")


def test_photos_floor_is_higher_than_folder() -> None:
    """A fit at 30 iPhone samples would be noise; the floor has to say so."""
    assert min_decisions_for("folder") == MIN_DECISIONS
    assert min_decisions_for("photos") > MIN_DECISIONS


def test_unknown_corpus_falls_back_to_offset_and_the_folder_floor() -> None:
    """An unregistered corpus must not land in a score space whose cutoffs
    were never calibrated, so the fallback stays the old offset formula."""
    model = PersonalModel("something-new")
    assert model._scoring == "offset"
    assert model._min_decisions == MIN_DECISIONS


# ---------------------------------------------------------------------------
# Scoring modes
# ---------------------------------------------------------------------------

def _offset_model() -> PersonalModel:
    """Offset mode survives only as the fallback for unregistered corpora."""
    return PersonalModel("legacy-corpus")


def test_offset_mode_adds_the_prediction_to_overall_score() -> None:
    model = _offset_model()
    assert model._to_score(1.0, 60.0) == 85.0     # +DELTA_SCALE
    assert model._to_score(-1.0, 60.0) == 35.0    # -DELTA_SCALE
    assert model._to_score(0.0, 60.0) == 60.0


def test_both_shipped_corpora_score_absolute() -> None:
    """Measured 2026-09-19: pure GBR beats the offset formula on the folder
    corpus too (0.741 vs 0.713 ordinal accuracy)."""
    assert PersonalModel("folder")._scoring == "absolute"
    assert PersonalModel("photos")._scoring == "absolute"


def test_absolute_mode_ignores_overall_score_entirely() -> None:
    model = PersonalModel("photos")
    assert model._to_score(0.0, 50.0) == 50.0
    assert model._to_score(1.0, 50.0) == 100.0
    assert model._to_score(-0.5, 50.0) == 25.0
    # Same raw prediction, wildly different bases → identical score.
    assert model._to_score(0.2, 10.0) == model._to_score(0.2, 99.0)


def test_no_mode_scores_an_unanalyzed_row() -> None:
    """A missing overall_score means "not analyzed yet". Absolute mode does not
    read the value, but scoring such a row would run the imputer over an
    all-NaN vector and emit a confident-looking number for a photo nobody has
    looked at."""
    assert PersonalModel("photos")._to_score(0.5, None) is None
    assert _offset_model()._to_score(0.5, None) is None


def test_scoring_mode_is_reported_in_info() -> None:
    assert PersonalModel("photos").info()["scoring"] == "absolute"
    assert _offset_model().info()["scoring"] == "offset"
    assert PersonalModel("photos").info()["corpus"] == "photos"


# ---------------------------------------------------------------------------
# Corpus isolation in the training data
# ---------------------------------------------------------------------------

def test_count_samples_is_scoped_to_one_corpus(tmp_db: Path) -> None:
    _seed_samples(tmp_db, 12, "folder")
    _seed_samples(tmp_db, 5, "photos")
    assert auto_trainer._count_samples("folder") == 12
    assert auto_trainer._count_samples("photos") == 5


def test_load_corpus_only_reads_its_own_rows(tmp_db: Path) -> None:
    """Folder must not be pushed over its floor by Photos decisions."""
    _seed_samples(tmp_db, MIN_DECISIONS, "folder")
    _seed_samples(tmp_db, MIN_DECISIONS, "photos")

    loaded = auto_trainer._load_corpus("folder")
    assert loaded is not None
    assert loaded[0].shape[0] == MIN_DECISIONS

    # Photos has MIN_DECISIONS rows but its own floor is higher → still None.
    assert auto_trainer._load_corpus("photos") is None


def test_photos_corpus_loads_once_it_clears_its_own_floor(tmp_db: Path) -> None:
    _seed_samples(tmp_db, min_decisions_for("photos"), "photos")
    loaded = auto_trainer._load_corpus("photos")
    assert loaded is not None
    assert loaded[0].shape[0] == min_decisions_for("photos")


def test_pairwise_comparisons_do_not_cross_corpora(tmp_db: Path) -> None:
    """pairwise_comparisons has no corpus column — only the images join stops
    a camera A/B pair from becoming a Photos training sample."""
    _seed_image(tmp_db, 1, "folder")
    _seed_image(tmp_db, 2, "folder")
    _seed_image(tmp_db, 3, "photos")
    _seed_image(tmp_db, 4, "photos")
    with get_db(tmp_db) as conn:
        conn.executemany(
            "INSERT INTO pairwise_comparisons (winner_id, loser_id) VALUES (?, ?)",
            [(1, 2), (3, 4)],
        )

    folder_vecs, _, _, _ = auto_trainer._pairwise_synthetic_samples("folder")
    photos_vecs, _, _, _ = auto_trainer._pairwise_synthetic_samples("photos")
    assert folder_vecs.shape[0] == 2   # images 1 + 2 only
    assert photos_vecs.shape[0] == 2   # images 3 + 4 only


# ---------------------------------------------------------------------------
# Route wiring
# ---------------------------------------------------------------------------

def test_auto_cull_routes_still_point_at_their_handlers() -> None:
    """Adding a helper directly above a decorated endpoint silently rebinds the
    route to the helper — the decorator attaches to whatever `def` follows it.
    Every unit test still passed while GET /auto-cull/preview returned the
    string "photos" with a 200. Nothing else in the suite reads the routes.
    """
    from backend.routers import decisions as decisions_mod

    bound = {
        r.path: r.endpoint.__name__
        for r in decisions_mod.router.routes
        if hasattr(r, "endpoint")
    }
    assert bound["/auto-cull/preview"] == "auto_cull_preview"
    assert bound["/auto-cull"] == "run_auto_cull"
    assert bound["/decision"] == "save_decision"
    assert bound["/undo-decision"] == "undo_decision"


# ---------------------------------------------------------------------------
# Corpus-scoped settings
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_settings(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    store: dict[str, str] = {}
    from backend import database as _db_mod

    monkeypatch.setattr(_db_mod, "get_setting",
                        lambda key, default=None, **kw: store.get(key, default))
    monkeypatch.setattr(_db_mod, "get_bool_setting",
                        lambda key, default=False, **kw: store.get(key, "").strip().lower()
                        in {"1", "true", "yes", "on"} if key in store else default)
    monkeypatch.setattr(_db_mod, "set_setting",
                        lambda key, value, **kw: store.__setitem__(key, value))
    return store


def test_scoped_key_wins_over_the_global_one(fake_settings: dict) -> None:
    fake_settings["keep_threshold"] = "70"
    fake_settings["photos:keep_threshold"] = "42"
    assert qa.get_decision_thresholds("photos")["keep_threshold"] == 42.0
    assert qa.get_decision_thresholds("folder")["keep_threshold"] == 70.0
    assert qa.get_decision_thresholds()["keep_threshold"] == 70.0


def test_unscoped_keys_still_reach_every_corpus(fake_settings: dict) -> None:
    """A corpus only overrides what it needs; everything else stays shared."""
    fake_settings["iqa_floor"] = "33"
    assert qa.get_decision_thresholds("photos")["iqa_floor"] == 33.0
    assert qa.get_decision_thresholds("folder")["iqa_floor"] == 33.0


def test_scoped_bool_wins_over_the_global_one(fake_settings: dict) -> None:
    fake_settings["reject_blurry_frame"] = "0"
    fake_settings["photos:reject_blurry_frame"] = "1"
    assert qa.get_decision_thresholds("photos")["reject_blurry_frame"] is True
    assert qa.get_decision_thresholds("folder")["reject_blurry_frame"] is False


def test_out_of_range_scoped_value_falls_through_to_global(fake_settings: dict) -> None:
    fake_settings["keep_threshold"] = "70"
    fake_settings["photos:keep_threshold"] = "5000"   # outside 0..100
    assert qa.get_decision_thresholds("photos")["keep_threshold"] == 70.0


# ---------------------------------------------------------------------------
# Cutoff calibration
# ---------------------------------------------------------------------------

def test_absolute_fit_suggests_cutoffs(tmp_db: Path) -> None:
    """An absolute score has no human-meaningful unit, so the fit has to say
    where keep starts — otherwise the shipped 70/45 apply to the wrong scale."""
    _seed_samples(tmp_db, min_decisions_for("photos"), "photos")
    loaded = auto_trainer._load_corpus("photos")
    assert loaded is not None
    X, decisions, overall, decided_at, weights = loaded

    model = PersonalModel("photos")
    meta = model.train_from_samples(X, decisions, overall,
                                    decided_at=decided_at, base_weights=weights)
    cutoffs = meta["suggested_cutoffs"]
    assert 0.0 <= cutoffs["maybe_threshold"] <= cutoffs["keep_threshold"] <= 100.0


def test_offset_fit_suggests_nothing(tmp_db: Path) -> None:
    """Offset-mode cutoffs live in quality-percentage space and are user-tuned
    — a retrain must never propose moving them."""
    _seed_samples(tmp_db, MIN_DECISIONS, "folder")
    loaded = auto_trainer._load_corpus("folder")
    assert loaded is not None
    X, decisions, overall, decided_at, weights = loaded

    meta = _offset_model().train_from_samples(
        X, decisions, overall, decided_at=decided_at, base_weights=weights)
    assert meta["suggested_cutoffs"] == {}


def test_calibration_does_not_overwrite_a_tuned_value(fake_settings: dict) -> None:
    fake_settings["photos:keep_threshold"] = "61"
    auto_trainer._calibrate_cutoffs(
        PersonalModel("photos"),
        {"suggested_cutoffs": {"keep_threshold": 12.0, "maybe_threshold": 3.0}},
    )
    assert fake_settings["photos:keep_threshold"] == "61"    # user's value kept
    assert fake_settings["photos:maybe_threshold"] == "3.0"  # absent one seeded
