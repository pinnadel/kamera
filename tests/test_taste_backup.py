"""
Backup / export / import of the personal taste model.

Verifies the load-bearing claims of the Backup & Restore feature:
  - Export serialises the durable state (training_samples + pairwise +
    tunable settings) into a portable JSON envelope.
  - Import restores it — merge upserts by sample_uuid, replace snapshots.
  - The corpus survives a full export → import round-trip byte-for-byte.
  - A backup made on a different feature schema still imports (name-keyed
    features re-align; import warns but never blocks).
  - Replace-import that shrinks the corpus below the on-disk fit still
    persists the smaller model (reset() bypasses save()'s shrink guard).

Strategy mirrors test_dashboard / test_auto_trainer: monkeypatch get_db on
every module the endpoints resolve through, call the endpoint functions
directly, and redirect the model pickle to a temp path. The import endpoint
is `async` and reads `await request.body()`, so we build a tiny mock Request
and drive it with asyncio.run().
"""
from __future__ import annotations

import asyncio
import json
import pickle
from pathlib import Path
from typing import Iterator

import numpy as np
import pytest

from backend import database as db_mod
from backend.database import create_tables, get_db
from backend.routers import model as model_mod
from backend.routers import settings as settings_mod
from phase3_learning import auto_trainer, personal_model
from phase3_learning.feature_extractor import (
    FEATURE_SCHEMA_VERSION,
    feature_names,
    serialize_features,
)
from phase3_learning.personal_model import MIN_DECISIONS, PersonalModel


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def backup_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Fresh tmp DB + tmp model pickle, with get_db rewired everywhere the
    export/import endpoints (and the retrain they trigger) read it."""
    db_path = tmp_path / "backup_test.db"
    create_tables(db_path)

    real_get_db = db_mod.get_db

    def _scoped_get_db(db_path_arg: Path = db_path):
        return real_get_db(db_path_arg)

    # The endpoints call get_db directly (model_mod); the retrain reads the
    # corpus through auto_trainer.get_db. Patch both.
    monkeypatch.setattr(db_mod, "get_db", _scoped_get_db)
    monkeypatch.setattr(model_mod, "get_db", _scoped_get_db)
    monkeypatch.setattr(auto_trainer, "get_db", _scoped_get_db)

    # Redirect the pickle so a retrain never touches data/models/. save(),
    # load() and reset() default path=_MODEL_PATH, a default bound at function
    # definition time — and force_train_sync calls model.save() with NO path
    # argument. So patching the module constant alone is not enough; we bind the
    # temp path into the three methods on our fresh instance.
    pkl_path = tmp_path / "personal_model.pkl"
    monkeypatch.setattr(personal_model, "_MODEL_PATH", pkl_path)

    fresh = PersonalModel()
    _orig_save, _orig_load, _orig_reset = fresh.save, fresh.load, fresh.reset
    monkeypatch.setattr(fresh, "save",
                        lambda path=pkl_path, **kw: _orig_save(path, **kw))
    monkeypatch.setattr(fresh, "load", lambda path=pkl_path: _orig_load(path))
    monkeypatch.setattr(fresh, "reset", lambda path=pkl_path: _orig_reset(path))
    monkeypatch.setattr(model_mod, "_personal_model", fresh)
    # The import path resets every corpus's model, so the registry it loops
    # over must point at the same throwaway instance the assertions inspect.
    monkeypatch.setattr(model_mod, "_personal_models", {"folder": fresh})

    auto_trainer._running = {}
    auto_trainer._last_auto_train_at = {}
    yield db_path


def _seed_samples(db_path: Path, n: int, *, prefix: str = "s") -> list[dict]:
    """Insert n synthetic training_samples rows; return the rows as dicts."""
    rng = np.random.default_rng(seed=0)
    decisions = ["keep", "maybe", "reject"]
    rows = []
    for i in range(n):
        feature_row = {
            col: float(rng.uniform(0, 100))
            for col in feature_names() if col != "face_present"
        }
        feature_row["face_detected"] = int(rng.integers(0, 2))
        rows.append({
            "sample_uuid":    f"{prefix}-uuid-{i:04d}",
            "decision":       decisions[i % 3],
            "features_json":  serialize_features(feature_row),
            "overall_score":  float(rng.uniform(20, 95)),
            "schema_version": FEATURE_SCHEMA_VERSION,
            "decided_at":     f"2026-05-{(i % 28) + 1:02d}T10:00:00",
        })
    with get_db(db_path) as conn:
        conn.executemany(
            """INSERT INTO training_samples
               (sample_uuid, decision, features_json, overall_score, schema_version, decided_at)
               VALUES (:sample_uuid, :decision, :features_json, :overall_score,
                       :schema_version, :decided_at)""",
            rows,
        )
    return rows


def _run_import(body: dict | str | bytes, mode: str = "merge") -> dict:
    """Invoke the async import endpoint with a mock Request carrying `body`."""
    if isinstance(body, dict):
        raw = json.dumps(body).encode()
    elif isinstance(body, str):
        raw = body.encode()
    else:
        raw = body

    class _Req:
        async def body(self_inner):
            return raw

    return asyncio.run(model_mod.import_taste_model(_Req(), mode=mode))


def _count(db_path: Path, table: str) -> int:
    with get_db(db_path) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def test_export_empty_corpus_is_valid(backup_env: Path) -> None:
    """Exporting with no samples returns a 200-shape envelope, never 404."""
    resp = model_mod.export_taste_model()
    payload = json.loads(resp.body)
    assert payload["format"] == "kamera-taste-model"
    # Compare to the constant, not a literal — this assertion is about the
    # envelope being well-formed, not about pinning a particular version.
    assert payload["format_version"] == model_mod._BACKUP_FORMAT_VERSION
    assert payload["training_samples"] == []
    assert payload["settings"] == {}
    assert payload["app"]["feature_schema_version"] == FEATURE_SCHEMA_VERSION
    assert "0-samples" in resp.headers["content-disposition"]


def test_export_includes_samples_pairwise_and_settings(backup_env: Path) -> None:
    _seed_samples(backup_env, 3)
    with get_db(backup_env) as conn:
        conn.execute(
            "INSERT INTO pairwise_comparisons (winner_id, loser_id, source_folder, decided_at) "
            "VALUES (?, ?, ?, ?)",
            (11, 22, "/src", "2026-05-01T09:00:00"),
        )
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("keep_threshold", "72.0000"))
        # A key NOT in the tunable registry must not be exported.
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("some_other_key", "x"))

    payload = json.loads(model_mod.export_taste_model().body)
    assert len(payload["training_samples"]) == 3
    assert len(payload["pairwise_comparisons"]) == 1
    assert payload["settings"] == {"keep_threshold": "72.0000"}


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_round_trip_replace_is_byte_identical(backup_env: Path, tmp_path: Path) -> None:
    """Export a corpus, wipe, replace-import → rows identical by uuid."""
    _seed_samples(backup_env, MIN_DECISIONS)
    with get_db(backup_env) as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("maybe_threshold", "40.0000"))
    backup = json.loads(model_mod.export_taste_model().body)

    # Wipe the corpus, then restore from the backup.
    with get_db(backup_env) as conn:
        conn.execute("DELETE FROM training_samples")
    assert _count(backup_env, "training_samples") == 0

    result = _run_import(backup, mode="replace")
    assert result["status"] == "imported"
    assert result["samples_imported"] == MIN_DECISIONS
    assert result["settings_applied"] == 1

    restored = json.loads(model_mod.export_taste_model().body)
    by_uuid = {s["sample_uuid"]: s for s in restored["training_samples"]}
    for orig in backup["training_samples"]:
        got = by_uuid[orig["sample_uuid"]]
        assert got["decision"] == orig["decision"]
        assert got["features_json"] == orig["features_json"]
        assert got["overall_score"] == orig["overall_score"]
        assert got["schema_version"] == orig["schema_version"]
        assert got["decided_at"] == orig["decided_at"]
    assert restored["settings"]["maybe_threshold"] == "40.0000"


# ---------------------------------------------------------------------------
# Merge vs Replace
# ---------------------------------------------------------------------------


def test_merge_upserts_overlap_and_keeps_others(backup_env: Path) -> None:
    _seed_samples(backup_env, 3, prefix="pre")  # pre-uuid-0000..0002 in DB

    # Backup overlaps one uuid (with a changed decision) + adds one new uuid.
    overlap = {
        "sample_uuid": "pre-uuid-0000", "decision": "reject",
        "features_json": serialize_features({"sharpness_score": 10}),
        "overall_score": 12.0, "schema_version": FEATURE_SCHEMA_VERSION,
        "decided_at": "2026-06-01T00:00:00",
    }
    fresh = {
        "sample_uuid": "new-uuid-9999", "decision": "keep",
        "features_json": serialize_features({"sharpness_score": 90}),
        "overall_score": 88.0, "schema_version": FEATURE_SCHEMA_VERSION,
        "decided_at": "2026-06-02T00:00:00",
    }
    backup = _envelope([overlap, fresh])

    result = _run_import(backup, mode="merge")
    assert result["samples_imported"] == 2

    with get_db(backup_env) as conn:
        rows = {r["sample_uuid"]: r["decision"]
                for r in conn.execute("SELECT sample_uuid, decision FROM training_samples")}
    # Overlap updated in place, the two other pre rows preserved, new row added.
    assert rows["pre-uuid-0000"] == "reject"      # updated
    assert "pre-uuid-0001" in rows                # preserved
    assert "pre-uuid-0002" in rows                # preserved
    assert rows["new-uuid-9999"] == "keep"        # added
    assert len(rows) == 4


def test_replace_leaves_only_backup_rows(backup_env: Path) -> None:
    _seed_samples(backup_env, 5, prefix="old")
    backup = _envelope([{
        "sample_uuid": "only-uuid", "decision": "keep",
        "features_json": serialize_features({"sharpness_score": 50}),
        "overall_score": 55.0, "schema_version": FEATURE_SCHEMA_VERSION,
        "decided_at": "2026-06-01T00:00:00",
    }])

    _run_import(backup, mode="replace")
    with get_db(backup_env) as conn:
        rows = [r["sample_uuid"] for r in conn.execute("SELECT sample_uuid FROM training_samples")]
    assert rows == ["only-uuid"]


def test_merge_pairwise_is_idempotent(backup_env: Path) -> None:
    pw = {"winner_id": 1, "loser_id": 2, "source_folder": "/s", "decided_at": "2026-05-01T00:00:00"}
    backup = _envelope([], pairwise=[pw])
    _run_import(backup, mode="merge")
    _run_import(backup, mode="merge")  # second merge must not duplicate
    assert _count(backup_env, "pairwise_comparisons") == 1


# ---------------------------------------------------------------------------
# Schema-mismatch tolerance
# ---------------------------------------------------------------------------


def test_schema_mismatch_imports_with_warning(backup_env: Path) -> None:
    """A backup made on an older feature schema still imports; import warns
    and a subsequent retrain does not raise (features re-align by name)."""
    rows = _seed_samples(backup_env, MIN_DECISIONS)
    backup = _envelope(rows)
    backup["app"]["feature_schema_version"] = FEATURE_SCHEMA_VERSION - 1
    with get_db(backup_env) as conn:
        conn.execute("DELETE FROM training_samples")

    result = _run_import(backup, mode="replace")
    assert result["schema_warning"] is not None
    assert result["samples_imported"] == MIN_DECISIONS
    # Corpus is at/above the floor so a retrain happened without raising.
    assert result["retrained"] is True


# ---------------------------------------------------------------------------
# Shrink-after-replace (the shrink-guard trap)
# ---------------------------------------------------------------------------


def test_replace_with_smaller_corpus_persists_smaller_model(backup_env: Path) -> None:
    """Train a large model (pickle on disk), then replace-import a smaller
    corpus. The on-disk pickle must reflect the SMALLER fit — proving reset()
    bypassed save()'s shrink guard."""
    big = MIN_DECISIONS + 15
    _seed_samples(backup_env, big, prefix="big")
    from phase3_learning.auto_trainer import force_train_sync
    force_train_sync(model_mod._personal_model)
    assert model_mod._personal_model.training_size == big

    small = MIN_DECISIONS + 1
    backup = _envelope(_synthetic_rows(small))
    result = _run_import(backup, mode="replace")
    assert result["retrained"] is True
    assert result["samples_imported"] == small

    # On-disk pickle must be the smaller fit, not the stale bigger one.
    with open(personal_model._MODEL_PATH, "rb") as f:
        saved = pickle.load(f)
    assert saved["meta"]["training_size"] == small


# ---------------------------------------------------------------------------
# Below-floor
# ---------------------------------------------------------------------------


def test_below_floor_skips_retrain(backup_env: Path) -> None:
    backup = _envelope(_synthetic_rows(10))
    result = _run_import(backup, mode="replace")
    assert result["retrained"] is False
    assert result["retrain_skipped_reason"] == "below_min_decisions"
    assert result["samples_imported"] == 10


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_non_json_body_is_400(backup_env: Path) -> None:
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        _run_import(b"this is not json", mode="merge")
    assert exc.value.status_code == 400


def test_wrong_format_is_400(backup_env: Path) -> None:
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        _run_import({"format": "something-else", "format_version": 1}, mode="merge")
    assert exc.value.status_code == 400


def test_bad_mode_is_400(backup_env: Path) -> None:
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        _run_import(_envelope([]), mode="wipe-everything")
    assert exc.value.status_code == 400


def test_unknown_settings_key_is_dropped(backup_env: Path) -> None:
    backup = _envelope([], settings={"keep_threshold": "70.0000", "evil_key": "boom"})
    result = _run_import(backup, mode="merge")
    assert result["settings_applied"] == 1
    with get_db(backup_env) as conn:
        keys = [r["key"] for r in conn.execute("SELECT key FROM settings")]
    assert "keep_threshold" in keys
    assert "evil_key" not in keys


def test_rows_without_uuid_are_skipped(backup_env: Path) -> None:
    good = {
        "sample_uuid": "ok", "decision": "keep",
        "features_json": serialize_features({"sharpness_score": 1}),
        "overall_score": 1.0, "schema_version": FEATURE_SCHEMA_VERSION,
        "decided_at": "2026-06-01T00:00:00",
    }
    bad = {**good, "sample_uuid": ""}
    backup = _envelope([good, bad])
    result = _run_import(backup, mode="merge")
    assert result["samples_imported"] == 1
    assert result["samples_skipped"] == 1


# ---------------------------------------------------------------------------
# Small envelope helpers
# ---------------------------------------------------------------------------


def _envelope(samples: list[dict], *, pairwise: list | None = None,
              settings: dict | None = None) -> dict:
    return {
        "format": "kamera-taste-model",
        "format_version": 1,
        "exported_at": "2026-07-02T00:00:00",
        "app": {"feature_schema_version": FEATURE_SCHEMA_VERSION, "min_decisions": MIN_DECISIONS},
        "training_samples": samples,
        "pairwise_comparisons": pairwise or [],
        "settings": settings or {},
    }


def _synthetic_rows(n: int) -> list[dict]:
    rng = np.random.default_rng(seed=1)
    decisions = ["keep", "maybe", "reject"]
    out = []
    for i in range(n):
        feature_row = {
            col: float(rng.uniform(0, 100))
            for col in feature_names() if col != "face_present"
        }
        feature_row["face_detected"] = int(rng.integers(0, 2))
        out.append({
            "sample_uuid": f"syn-{i:04d}",
            "decision": decisions[i % 3],
            "features_json": serialize_features(feature_row),
            "overall_score": float(rng.uniform(20, 95)),
            "schema_version": FEATURE_SCHEMA_VERSION,
            "decided_at": f"2026-05-{(i % 28) + 1:02d}T10:00:00",
        })
    return out
