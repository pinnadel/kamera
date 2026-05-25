"""
Priority 1 — Database invariants.

Tests verify that:
  1. create_tables() builds all expected tables on a brand-new DB.
  2. Calling create_tables() a second time is safe and idempotent.
  3. All migration columns are present after schema creation.
  4. get_setting / set_setting round-trip correctly.
  5. get_folder_overrides / set_folder_override round-trip correctly.
  6. set_folder_override with dest_folder=None deletes the row.

None of these tests import from backend/main.py.
"""
import sqlite3
from pathlib import Path

import pytest

from backend.database import (
    create_tables,
    get_db,
    get_folder_overrides,
    get_schema_version,
    get_setting,
    set_folder_override,
    set_setting,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _table_names(db_path: Path) -> set[str]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    return {r["name"] for r in rows}


def _column_names(db_path: Path, table: str) -> set[str]:
    with get_db(db_path) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_create_tables_fresh_db(tmp_path: Path) -> None:
    """create_tables() on a temp DB creates all expected tables."""
    db_path = tmp_path / "fresh.db"
    create_tables(db_path)

    tables = _table_names(db_path)
    expected = {"images", "bursts", "burst_members", "decisions", "settings", "folder_settings", "training_samples", "shooting_log"}
    assert expected.issubset(tables), (
        f"Missing tables: {expected - tables}"
    )


def test_create_tables_idempotent(tmp_path: Path) -> None:
    """Calling create_tables() twice doesn't raise and doesn't destroy data."""
    db_path = tmp_path / "idempotent.db"
    create_tables(db_path)

    # Write a row of data before the second call.
    with get_db(db_path) as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('test_key', 'test_value')"
        )

    # Second call — must not raise.
    create_tables(db_path)

    # The row should still be there.
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'test_key'"
        ).fetchone()
    assert row is not None
    assert row["value"] == "test_value"


def test_migrations_add_columns(tmp_db: Path) -> None:
    """After create_tables(), the images table has all expected columns."""
    columns = _column_names(tmp_db, "images")

    required = {
        # Base schema columns
        "id", "file_path", "filename", "preview_path",
        "sharpness_score", "exposure_score", "overall_score",
        # Migration columns
        "uuid",
        "face_detected",
        "iqa_score",
        "aesthetic_score",
        "embedding",
        "source_folder",
        "highlight_clip_pct",
        "shadow_clip_pct",
    }
    missing = required - columns
    assert not missing, f"Missing columns in images table: {missing}"


def test_get_set_setting(tmp_db: Path) -> None:
    """Round-trip a key/value through get_setting() / set_setting()."""
    set_setting("sharpness_weight", "0.75", db_path=tmp_db)
    value = get_setting("sharpness_weight", db_path=tmp_db)
    assert value == "0.75"


def test_get_setting_missing_key_returns_default(tmp_db: Path) -> None:
    """get_setting() returns the provided default when the key doesn't exist."""
    value = get_setting("nonexistent_key", default="fallback", db_path=tmp_db)
    assert value == "fallback"


def test_get_setting_missing_key_returns_none_by_default(tmp_db: Path) -> None:
    """get_setting() returns None when the key doesn't exist and no default given."""
    value = get_setting("nonexistent_key", db_path=tmp_db)
    assert value is None


def test_folder_overrides_roundtrip(tmp_path: Path, tmp_db: Path) -> None:
    """set_folder_override() + get_folder_overrides() returns expected structure."""
    source = str(Path.home() / "Pictures" / "RAW temporary Folder")
    keeps  = str(tmp_path / "NAS" / "Keeps")
    maybes = str(tmp_path / "NAS" / "Maybes")
    trash  = str(tmp_path / "NAS" / "Trash")

    set_folder_override(source, "keep",   keeps,  db_path=tmp_db)
    set_folder_override(source, "maybe",  maybes, db_path=tmp_db)
    set_folder_override(source, "reject", trash,  db_path=tmp_db)

    overrides = get_folder_overrides(source, db_path=tmp_db)

    # get_folder_overrides translates decision names to the legacy key names
    # that resolve_dest_folder expects.
    assert overrides.get("keeps_folder")  == keeps
    assert overrides.get("maybes_folder") == maybes
    assert overrides.get("trash_folder")  == trash


def test_folder_override_delete(tmp_path: Path, tmp_db: Path) -> None:
    """set_folder_override with dest_folder=None deletes the row."""
    source = str(Path.home() / "Pictures" / "Session2")
    custom_keeps = str(tmp_path / "custom" / "Keeps")

    set_folder_override(source, "keep", custom_keeps, db_path=tmp_db)
    # Confirm it was written.
    overrides = get_folder_overrides(source, db_path=tmp_db)
    assert "keeps_folder" in overrides

    # Now delete by passing None.
    set_folder_override(source, "keep", None, db_path=tmp_db)
    overrides_after = get_folder_overrides(source, db_path=tmp_db)
    assert "keeps_folder" not in overrides_after


def test_set_folder_override_invalid_decision_raises(tmp_db: Path) -> None:
    """set_folder_override raises ValueError for unknown decision strings."""
    with pytest.raises(ValueError, match="decision must be one of"):
        set_folder_override("/some/folder", "discard", "/dest", db_path=tmp_db)


# ---------------------------------------------------------------------------
# Schema versioning tests
# ---------------------------------------------------------------------------

def test_schema_version_table_exists(tmp_path: Path) -> None:
    """After create_tables(), the schema_version table is queryable."""
    db_path = tmp_path / "sv_exists.db"
    create_tables(db_path)

    with get_db(db_path) as conn:
        # Must not raise — if the table is missing this returns an error
        rows = conn.execute("SELECT * FROM schema_version").fetchall()
    # Fresh DB should have at least one row (all migrations applied)
    assert len(rows) > 0


def test_schema_version_populated(tmp_path: Path) -> None:
    """get_schema_version() returns the latest applied migration version."""
    db_path = tmp_path / "sv_populated.db"
    create_tables(db_path)

    version = get_schema_version(db_path)
    # Each new migration in _MIGRATIONS must keep this lower bound truthful;
    # bump alongside the migration registry, not separately.
    assert version >= 22, f"Expected schema version >= 22, got {version}"


def test_schema_version_idempotent(tmp_path: Path) -> None:
    """Calling create_tables() twice does not add duplicate rows to schema_version."""
    db_path = tmp_path / "sv_idempotent.db"
    create_tables(db_path)

    with get_db(db_path) as conn:
        count_after_first = conn.execute(
            "SELECT COUNT(*) AS n FROM schema_version"
        ).fetchone()["n"]

    # Second call — must not raise and must not duplicate rows
    create_tables(db_path)

    with get_db(db_path) as conn:
        count_after_second = conn.execute(
            "SELECT COUNT(*) AS n FROM schema_version"
        ).fetchone()["n"]

    assert count_after_first == count_after_second, (
        f"schema_version grew from {count_after_first} to {count_after_second} rows "
        "after a second create_tables() call — migration steps are not idempotent"
    )


def test_get_schema_version_function(tmp_path: Path) -> None:
    """get_schema_version() matches a direct MAX(version) query on the same DB."""
    db_path = tmp_path / "sv_function.db"
    create_tables(db_path)

    reported = get_schema_version(db_path)

    with get_db(db_path) as conn:
        direct = conn.execute(
            "SELECT MAX(version) AS v FROM schema_version"
        ).fetchone()["v"]

    assert reported == direct, (
        f"get_schema_version() returned {reported} but direct MAX(version) is {direct}"
    )


# ---------------------------------------------------------------------------
# shooting_log — durable corpus for the Dashboard
# ---------------------------------------------------------------------------


def test_shooting_log_columns(tmp_db: Path) -> None:
    """shooting_log has the expected columns + a unique index on sample_uuid."""
    cols = _column_names(tmp_db, "shooting_log")
    required = {
        "id", "sample_uuid", "analyzed_at", "shot_at", "camera",
        "lens_model", "film_simulation", "format", "focal_length_mm",
        "aperture", "shutter_speed", "iso", "overall_score",
    }
    missing = required - cols
    assert not missing, f"Missing columns in shooting_log: {missing}"


def test_write_shooting_log_inserts_and_upserts(tmp_db: Path) -> None:
    """write_shooting_log inserts a new row, then on a second call with the
    same sample_uuid replaces it (re-analysis semantics)."""
    from backend.database import write_shooting_log

    with get_db(tmp_db) as conn:
        write_shooting_log(
            conn,
            sample_uuid="uuid-A",
            shot_at="2026-04-10 12:00:00",
            camera="X100VI",
            lens_model="Fujifilm 23mm f/2",
            film_simulation="Velvia",
            format="RAF",
            focal_length_mm=23.0,
            aperture=2.0,
            shutter_speed=1 / 250,
            iso=400,
            overall_score=72.0,
        )
        write_shooting_log(
            conn,
            sample_uuid="uuid-A",
            shot_at="2026-04-10 12:00:00",
            camera="X100VI",
            lens_model="Fujifilm 23mm f/2",
            film_simulation="Acros",  # changed
            format="RAF",
            focal_length_mm=23.0,
            aperture=2.0,
            shutter_speed=1 / 250,
            iso=400,
            overall_score=80.0,  # changed
        )
        rows = conn.execute("SELECT film_simulation, overall_score FROM shooting_log WHERE sample_uuid = ?", ("uuid-A",)).fetchall()

    assert len(rows) == 1, "Re-write should replace, not insert a second row"
    assert rows[0]["film_simulation"] == "Acros"
    assert rows[0]["overall_score"] == 80.0


def test_write_shooting_log_skips_when_uuid_missing(tmp_db: Path) -> None:
    """No uuid → no row written. Same forgiving behaviour as training_samples."""
    from backend.database import write_shooting_log

    with get_db(tmp_db) as conn:
        write_shooting_log(
            conn,
            sample_uuid=None,
            shot_at=None, camera="X100VI", lens_model=None, film_simulation=None,
            format="RAF", focal_length_mm=None, aperture=None, shutter_speed=None,
            iso=None, overall_score=None,
        )
        count = conn.execute("SELECT COUNT(*) FROM shooting_log").fetchone()[0]

    assert count == 0


def test_v42_null_aesthetic_in_training_samples(tmp_db: Path) -> None:
    """PR1 migration: NULL aesthetic_score in training_samples.features_json.

    Old-scale aesthetic values would otherwise mix with new-scale TOPIQ-IAA
    values when the model retrains. Other features must survive intact.
    """
    import json
    from backend.database import _step_v42_null_aesthetic_in_training_samples

    with get_db(tmp_db) as conn:
        # Seed three rows: one with aesthetic_score + other features, one
        # without aesthetic_score (sparse legacy row), and one with malformed
        # JSON (defensive — should be skipped, not crash).
        conn.execute(
            """INSERT INTO training_samples (sample_uuid, decision, features_json,
               overall_score, schema_version)
               VALUES (?, 'keep', ?, 72.0, 3)""",
            ("uuid-A", json.dumps({"sharpness_score": 65.0, "aesthetic_score": 78.0, "iqa_score": 72.0})),
        )
        conn.execute(
            """INSERT INTO training_samples (sample_uuid, decision, features_json,
               overall_score, schema_version)
               VALUES (?, 'reject', ?, 30.0, 3)""",
            ("uuid-B", json.dumps({"sharpness_score": 28.0, "iqa_score": 31.0})),
        )
        conn.execute(
            """INSERT INTO training_samples (sample_uuid, decision, features_json,
               overall_score, schema_version)
               VALUES (?, 'maybe', '{not valid json', 50.0, 3)""",
            ("uuid-C",),
        )

        _step_v42_null_aesthetic_in_training_samples(conn)

        rows = {
            r["sample_uuid"]: r
            for r in conn.execute(
                "SELECT sample_uuid, features_json, schema_version FROM training_samples"
            ).fetchall()
        }

    # Row A: aesthetic nulled, other features preserved, schema_version bumped.
    a_features = json.loads(rows["uuid-A"]["features_json"])
    assert a_features["aesthetic_score"] is None
    assert a_features["sharpness_score"] == 65.0
    assert a_features["iqa_score"] == 72.0
    assert rows["uuid-A"]["schema_version"] == 5

    # Row B: no aesthetic_score key — untouched.
    b_features = json.loads(rows["uuid-B"]["features_json"])
    assert "aesthetic_score" not in b_features
    assert b_features["sharpness_score"] == 28.0
    assert rows["uuid-B"]["schema_version"] == 3  # unchanged

    # Row C: malformed JSON — silently skipped, no crash.
    assert rows["uuid-C"]["features_json"] == "{not valid json"
    assert rows["uuid-C"]["schema_version"] == 3


def test_v44_null_siglip_features_in_training_samples(tmp_db: Path) -> None:
    """PR2 migration v44: NULL the four SigLIP content-axis keys in every
    training_samples.features_json row when SigLIP-1 → SigLIP-2.

    Other features (aesthetic, sharpness, scene_is_*) must survive intact.
    Malformed JSON and rows without any SigLIP keys are skipped.
    """
    import json
    from backend.database import _step_v44_null_siglip_features_in_training_samples

    with get_db(tmp_db) as conn:
        # Row with all four SigLIP axes + a few unrelated features.
        conn.execute(
            """INSERT INTO training_samples (sample_uuid, decision, features_json,
               overall_score, schema_version) VALUES (?, 'keep', ?, 72.0, 5)""",
            ("uuid-A", json.dumps({
                "sharpness_score": 65.0,
                "aesthetic_score": 43.5,
                "scene_is_portrait": 1.0,
                "subject_prominence_score": 0.74,
                "background_distraction_score": 0.41,
                "eye_contact_score": 0.88,
                "decisive_moment_score": 0.62,
            })),
        )
        # Row without any SigLIP keys — should be untouched.
        conn.execute(
            """INSERT INTO training_samples (sample_uuid, decision, features_json,
               overall_score, schema_version) VALUES (?, 'reject', ?, 30.0, 5)""",
            ("uuid-B", json.dumps({"sharpness_score": 28.0, "iqa_score": 31.0})),
        )
        # Malformed JSON — defensive skip.
        conn.execute(
            """INSERT INTO training_samples (sample_uuid, decision, features_json,
               overall_score, schema_version) VALUES (?, 'maybe', '{garbage', 50.0, 5)""",
            ("uuid-C",),
        )

        _step_v44_null_siglip_features_in_training_samples(conn)

        rows = {
            r["sample_uuid"]: r
            for r in conn.execute(
                "SELECT sample_uuid, features_json, schema_version FROM training_samples"
            ).fetchall()
        }

    a = json.loads(rows["uuid-A"]["features_json"])
    assert a["subject_prominence_score"] is None
    assert a["background_distraction_score"] is None
    assert a["eye_contact_score"] is None
    assert a["decisive_moment_score"] is None
    # Other features survive.
    assert a["sharpness_score"] == 65.0
    assert a["aesthetic_score"] == 43.5
    assert a["scene_is_portrait"] == 1.0
    assert rows["uuid-A"]["schema_version"] == 6

    b = json.loads(rows["uuid-B"]["features_json"])
    assert "subject_prominence_score" not in b
    assert b["sharpness_score"] == 28.0
    assert rows["uuid-B"]["schema_version"] == 5  # untouched

    assert rows["uuid-C"]["features_json"] == "{garbage"  # malformed → skipped
    assert rows["uuid-C"]["schema_version"] == 5


def test_v45_null_siglip_columns_in_images(tmp_db: Path) -> None:
    """PR2 migration v45: NULL the SigLIP-derived columns in the live images
    table (embedding, 4 content axes, scene, scene_confidence) so re-analysis
    refills them with SigLIP-2-space values.
    """
    from backend.database import _migrate

    with get_db(tmp_db) as conn:
        conn.execute(
            """INSERT INTO images (file_path, filename, format, source_folder,
               embedding, subject_prominence_score, background_distraction_score,
               eye_contact_score, decisive_moment_score, scene, scene_confidence,
               sharpness_score, overall_score, analysis_status, imported_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'done', DATETIME('now'))""",
            ("/p/A.RAF", "A.RAF", "RAF", "/p",
             "[0.1, 0.2, 0.3]", 0.74, 0.41, 0.88, 0.62, "portrait", 0.93,
             65.0, 72.0),
        )

        # Roll back schema_version to before v45 so _migrate() reruns it.
        conn.execute("DELETE FROM schema_version WHERE version >= 45")
        _migrate(conn)

        row = conn.execute(
            """SELECT embedding, subject_prominence_score, background_distraction_score,
               eye_contact_score, decisive_moment_score, scene, scene_confidence,
               sharpness_score, overall_score FROM images WHERE filename = 'A.RAF'"""
        ).fetchone()

    # All SigLIP-derived columns NULLed.
    assert row["embedding"] is None
    assert row["subject_prominence_score"] is None
    assert row["background_distraction_score"] is None
    assert row["eye_contact_score"] is None
    assert row["decisive_moment_score"] is None
    assert row["scene"] is None
    assert row["scene_confidence"] is None
    # Non-SigLIP columns untouched.
    assert row["sharpness_score"] == 65.0
    assert row["overall_score"] == 72.0


def test_v43_null_aesthetic_in_images(tmp_db: Path) -> None:
    """PR1.5 migration v43: NULL aesthetic_score in the live images table.

    Old-scale aesthetic values would mix with new-scale TOPIQ-IAA predictions
    if left in place. Other columns must survive untouched, and the migration
    must be idempotent on already-null rows.
    """
    from backend.database import _migrate

    with get_db(tmp_db) as conn:
        conn.execute(
            """INSERT INTO images (file_path, filename, format, source_folder,
               aesthetic_score, sharpness_score, overall_score, analysis_status,
               imported_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'done', DATETIME('now'))""",
            ("/p/A.RAF", "A.RAF", "RAF", "/p", 78.4, 65.0, 72.0),
        )
        conn.execute(
            """INSERT INTO images (file_path, filename, format, source_folder,
               aesthetic_score, sharpness_score, overall_score, analysis_status,
               imported_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'done', DATETIME('now'))""",
            ("/p/B.JPG", "B.JPG", "JPG", "/p", None, 40.0, 45.0),
        )

        # Roll back schema_version to just before v43 so _migrate() reruns it.
        conn.execute("DELETE FROM schema_version WHERE version >= 43")
        _migrate(conn)

        rows = {
            r["filename"]: r
            for r in conn.execute(
                "SELECT filename, aesthetic_score, sharpness_score, overall_score FROM images"
            ).fetchall()
        }

    # Both rows end up NULL on aesthetic_score; other columns untouched.
    assert rows["A.RAF"]["aesthetic_score"] is None
    assert rows["A.RAF"]["sharpness_score"] == 65.0
    assert rows["A.RAF"]["overall_score"] == 72.0
    assert rows["B.JPG"]["aesthetic_score"] is None  # was already null
    assert rows["B.JPG"]["sharpness_score"] == 40.0


def test_v25_backfill_idempotent(tmp_db: Path) -> None:
    """Running create_tables() (which triggers v25 backfill) twice doesn't
    duplicate rows. Seed images first, then re-run."""
    from backend.database import _step_v25_backfill_shooting_log

    with get_db(tmp_db) as conn:
        # Seed two image rows with uuids set (mimicking a populated images table).
        conn.execute(
            """INSERT INTO images (file_path, filename, format, source_folder, uuid,
               camera, shot_at, focal_length_mm, aperture, shutter_speed, iso,
               overall_score, analysis_status, imported_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'done', DATETIME('now'))""",
            ("/p/A.RAF", "A.RAF", "RAF", "/p", "uuid-1", "X100VI",
             "2026-04-10 12:00:00", 23.0, 2.0, 0.004, 400, 70.0),
        )
        conn.execute(
            """INSERT INTO images (file_path, filename, format, source_folder, uuid,
               camera, shot_at, focal_length_mm, aperture, shutter_speed, iso,
               overall_score, analysis_status, imported_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'done', DATETIME('now'))""",
            ("/p/B.NEF", "B.NEF", "NEF", "/p", "uuid-2", "Z6III",
             "2026-04-10 12:01:00", 50.0, 1.8, 0.002, 200, 75.0),
        )
        # First backfill — populates two rows.
        _step_v25_backfill_shooting_log(conn)
        first = conn.execute("SELECT COUNT(*) FROM shooting_log").fetchone()[0]
        # Second backfill — should not duplicate.
        _step_v25_backfill_shooting_log(conn)
        second = conn.execute("SELECT COUNT(*) FROM shooting_log").fetchone()[0]

    assert first == 2
    assert second == 2, f"Backfill duplicated rows: {first} → {second}"
