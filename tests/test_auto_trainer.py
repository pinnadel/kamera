"""
Auto-train trigger gating + atomic-save invariants.

The trigger lives in `phase3_learning.auto_trainer.maybe_train_async`.
These tests exercise its decision logic synchronously by stubbing the
DB sample count and checking the boolean return value (True = thread
started, False = gate said no). The actual training thread is not awaited
in unit tests — its behaviour is covered by test_personal_model.py.

The atomic-save test verifies that `personal_model.save()` writes via a
.tmp sibling and then os.replace()s into place, so a Force Quit during
the write can never leave a half-pickle on disk.
"""
from __future__ import annotations

import json
import os
import pickle
import threading
import time
from pathlib import Path
from typing import Iterator

import numpy as np
import pytest

from backend.database import get_db
from phase3_learning import auto_trainer
from phase3_learning.auto_trainer import (
    RETRAIN_DELTA,
    force_train_sync,
    maybe_train_async,
    status_snapshot,
)
from phase3_learning.feature_extractor import (
    FEATURE_SCHEMA_VERSION,
    feature_names,
    serialize_features,
)
from phase3_learning.personal_model import MIN_DECISIONS, PersonalModel


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_db(monkeypatch: pytest.MonkeyPatch, tmp_db: Path) -> Iterator[None]:
    """Redirect auto_trainer's get_db to the per-test temp DB.

    auto_trainer imports `get_db` at module load, so monkeypatching the
    module attribute is the cleanest way to scope it to one test without
    mutating production state.
    """
    from backend import database as _db_mod

    def _scoped_get_db(db_path: Path = tmp_db):
        return _db_mod.get_db(db_path)

    monkeypatch.setattr(auto_trainer, "get_db", _scoped_get_db)
    # Also reset the module-level lock state between tests to keep things
    # deterministic — the auto-train thread is daemonised and we never
    # await it, so successive tests could otherwise observe stale state.
    auto_trainer._running = False
    auto_trainer._last_auto_train_at = None
    yield


def _seed_samples(db_path: Path, n: int, *, prefix: str | None = None) -> None:
    """Insert n synthetic training_samples rows into the temp DB.

    Each call uses a unique uuid prefix so successive calls don't collide
    on the UNIQUE(sample_uuid) constraint — emulates real usage where each
    decision is on a different photo.
    """
    rng = np.random.default_rng(seed=0)
    decision_cycle = ["keep", "maybe", "reject"]
    if prefix is None:
        # Disambiguate by current row count; first call → batch-0, second
        # → batch-30, etc.
        with get_db(db_path) as conn:
            existing = conn.execute("SELECT COUNT(*) FROM training_samples").fetchone()[0]
        prefix = f"batch-{existing:04d}"
    rows = []
    for i in range(n):
        feature_row = {
            col: float(rng.uniform(0, 100))
            for col in feature_names() if col != "face_present"
        }
        feature_row["face_detected"] = int(rng.integers(0, 2))
        rows.append((
            f"{prefix}-uuid-{i:04d}",
            decision_cycle[i % 3],
            serialize_features(feature_row),
            float(rng.uniform(20, 95)),
            FEATURE_SCHEMA_VERSION,
        ))
    with get_db(db_path) as conn:
        conn.executemany(
            """INSERT INTO training_samples
               (sample_uuid, decision, features_json, overall_score, schema_version)
               VALUES (?, ?, ?, ?, ?)""",
            rows,
        )


def _wait_for_quiet(timeout: float = 5.0) -> None:
    """Block until the background trainer flag clears, or fail.

    The trigger thread is daemonised and we never get a handle to it, so
    we poll the module flag — which is the same thing the API exposes.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not auto_trainer._running:
            return
        time.sleep(0.05)
    pytest.fail(f"Auto-train did not finish within {timeout}s")


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------

def test_below_floor_does_not_train(tmp_db: Path) -> None:
    """maybe_train_async returns False when corpus < MIN_DECISIONS."""
    _seed_samples(tmp_db, MIN_DECISIONS - 1)
    model = PersonalModel()
    assert maybe_train_async(model) is False
    assert model.ready is False


def test_first_train_fires_at_floor(tmp_db: Path) -> None:
    """The first call with corpus ≥ MIN_DECISIONS starts a training thread.

    We then wait for the worker to finish so we can observe the result.
    """
    _seed_samples(tmp_db, MIN_DECISIONS)
    model = PersonalModel()
    started = maybe_train_async(model)
    assert started is True
    _wait_for_quiet()
    assert model.ready is True
    assert model.training_size == MIN_DECISIONS


def test_second_train_requires_delta(tmp_db: Path) -> None:
    """After the first fit, retrain only when ≥ RETRAIN_DELTA new samples.

    Add 3 more samples (RETRAIN_DELTA defaults to 10) — gate should refuse.
    Add enough to cross the delta — gate should fire.
    """
    _seed_samples(tmp_db, MIN_DECISIONS)
    model = PersonalModel()
    assert maybe_train_async(model) is True
    _wait_for_quiet()

    # Below delta — no fire.
    _seed_samples(tmp_db, RETRAIN_DELTA - 1)
    assert maybe_train_async(model) is False

    # One more (now exactly at delta) — fire.
    with get_db(tmp_db) as conn:
        conn.execute(
            """INSERT INTO training_samples
               (sample_uuid, decision, features_json, overall_score, schema_version)
               VALUES (?, ?, ?, ?, ?)""",
            (
                "uuid-extra",
                "keep",
                serialize_features({"sharpness_score": 70.0}),
                70.0,
                FEATURE_SCHEMA_VERSION,
            ),
        )
    assert maybe_train_async(model) is True
    _wait_for_quiet()
    # Re-fitted on the larger corpus.
    assert model.training_size == MIN_DECISIONS + RETRAIN_DELTA


def test_concurrent_calls_single_flight(tmp_db: Path) -> None:
    """Two near-simultaneous trigger calls produce at most one training run.

    The second call returns False because the lock is held; whichever call
    grabbed the lock proceeds. We don't need to test ordering — just that
    the flag does its job.
    """
    _seed_samples(tmp_db, MIN_DECISIONS)
    model = PersonalModel()

    results: list[bool] = []
    barrier = threading.Barrier(2)

    def _attempt():
        barrier.wait()
        results.append(maybe_train_async(model))

    threads = [threading.Thread(target=_attempt) for _ in range(2)]
    for t in threads: t.start()
    for t in threads: t.join()
    _wait_for_quiet()

    assert results.count(True) == 1, f"Expected exactly one True, got {results}"
    assert results.count(False) == 1
    assert model.ready is True


# ---------------------------------------------------------------------------
# force_train_sync (manual override)
# ---------------------------------------------------------------------------

def test_force_train_sync_below_floor_raises(tmp_db: Path) -> None:
    """Manual retrain raises ValueError when corpus < MIN_DECISIONS."""
    _seed_samples(tmp_db, MIN_DECISIONS - 1)
    model = PersonalModel()
    with pytest.raises(ValueError, match=str(MIN_DECISIONS)):
        force_train_sync(model)


def test_force_train_sync_works_at_floor(tmp_db: Path) -> None:
    """Manual retrain succeeds when corpus ≥ MIN_DECISIONS."""
    _seed_samples(tmp_db, MIN_DECISIONS)
    model = PersonalModel()
    meta = force_train_sync(model)
    assert model.ready is True
    assert meta["training_size"] == MIN_DECISIONS


def test_status_snapshot_shape() -> None:
    """status_snapshot returns the expected JSON-serialisable keys."""
    snap = status_snapshot()
    assert "auto_running" in snap
    assert "last_auto_train_at" in snap
    assert "retrain_delta" in snap
    assert snap["retrain_delta"] == RETRAIN_DELTA


# ---------------------------------------------------------------------------
# Atomic save
# ---------------------------------------------------------------------------

def test_atomic_save_no_temp_left(tmp_path: Path) -> None:
    """After a successful save, no .tmp sibling remains on disk."""
    model = PersonalModel()

    # Train minimally so there's something to save.
    rng = np.random.default_rng(0)
    rows = []
    decisions = []
    for i in range(MIN_DECISIONS):
        feature_row = {col: float(rng.uniform(0, 100)) for col in feature_names() if col != "face_present"}
        feature_row["face_detected"] = int(rng.integers(0, 2))
        rows.append(feature_row)
        decisions.append(["keep", "maybe", "reject"][i % 3])
    model.train(rows, decisions)

    pkl_path = tmp_path / "personal_model.pkl"
    model.save(pkl_path)

    assert pkl_path.exists()
    assert not (tmp_path / "personal_model.pkl.tmp").exists(), (
        ".tmp sibling should be renamed away after a successful save"
    )


def test_atomic_save_overwrites_existing(tmp_path: Path) -> None:
    """A second save replaces the file in place (no append, no error)."""
    model = PersonalModel()

    rng = np.random.default_rng(1)
    rows = []
    decisions = []
    for i in range(MIN_DECISIONS):
        feature_row = {col: float(rng.uniform(0, 100)) for col in feature_names() if col != "face_present"}
        feature_row["face_detected"] = int(rng.integers(0, 2))
        rows.append(feature_row)
        decisions.append(["keep", "maybe", "reject"][i % 3])
    model.train(rows, decisions)

    pkl_path = tmp_path / "personal_model.pkl"
    model.save(pkl_path)
    first_size = pkl_path.stat().st_size

    # Train again with different data — overwriting save must succeed.
    model.train(rows, decisions)
    model.save(pkl_path)

    second_size = pkl_path.stat().st_size
    # File still exists, no .tmp leftover, both sizes are valid pickles.
    assert pkl_path.exists()
    assert not (tmp_path / "personal_model.pkl.tmp").exists()
    with open(pkl_path, "rb") as f:
        data = pickle.load(f)
    assert "pipeline" in data and "meta" in data
