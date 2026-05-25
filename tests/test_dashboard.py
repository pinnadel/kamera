"""
Tests for backend/routers/dashboard.py.

Strategy: monkeypatch DEFAULT_DB_PATH so get_db() reads from a tmp DB, then
call the endpoint functions directly (they're plain Python functions —
@router.get is just decoration). Avoids spinning up FastAPI TestClient which
would require importing backend.main (loads the personal model on import).
"""
import json
from pathlib import Path

import pytest

from backend import database as db_mod
from backend.database import create_tables, get_db, write_shooting_log
from backend.routers import dashboard as dash_mod
from phase3_learning.feature_extractor import FEATURE_SCHEMA_VERSION, feature_names


@pytest.fixture()
def dash_db(tmp_path: Path, monkeypatch):
    """Build a tmp DB and rewire get_db() to read it.

    Monkeypatching db_mod.DEFAULT_DB_PATH alone doesn't work because get_db's
    default-arg is bound at function definition time. Instead we wrap get_db
    with a closure that supplies the tmp path when no caller passes one.
    """
    p = tmp_path / "dash_test.db"
    create_tables(p)

    real_get_db = db_mod.get_db

    def _patched_get_db(db_path: Path = p):
        return real_get_db(db_path)

    # Patch the symbol in every module the dashboard router resolves through.
    monkeypatch.setattr(db_mod, "get_db", _patched_get_db)
    monkeypatch.setattr(dash_mod, "get_db", _patched_get_db)
    return p


# ---------------------------------------------------------------------------
# Empty DB → all endpoints return 200-shape with no rows
# ---------------------------------------------------------------------------


def test_decisions_timeline_empty(dash_db):
    out = dash_mod.decisions_timeline(bucket="week")
    assert out["bucket"] == "week"
    assert out["rows"] == []


def test_feature_deltas_empty(dash_db):
    out = dash_mod.decisions_feature_deltas()
    assert "features" in out
    assert len(out["features"]) == len(feature_names())
    for f in out["features"]:
        assert f["kept_median"] is None
        assert f["rejected_median"] is None
        assert f["n_kept"] == 0
        assert f["n_rejected"] == 0


def test_shooting_cameras_empty(dash_db):
    out = dash_mod.shooting_cameras()
    assert out == {"cameras": []}


def test_shooting_distributions_empty(dash_db):
    out = dash_mod.shooting_distributions()
    for key in ("focal_length", "aperture", "iso", "film_simulation", "lens_model"):
        assert key in out
        # Empty corpus → no rows; the COALESCE'd 'Unknown' bucket only appears
        # when at least one row exists.
        assert out[key] == [] or all(r["count"] >= 0 for r in out[key])


def test_shooting_timeline_empty(dash_db):
    out = dash_mod.shooting_timeline(bucket="month")
    assert out["bucket"] == "month"
    assert out["rows"] == []


# ---------------------------------------------------------------------------
# Seeded data → timelines + distributions populate correctly
# ---------------------------------------------------------------------------


def _seed_shooting_log(db_path: Path, rows: list[dict]) -> None:
    with get_db(db_path) as conn:
        for r in rows:
            write_shooting_log(conn, **r)


def _seed_training_sample(db_path: Path, *, uuid: str, decision: str,
                          features: dict, decided_at: str | None = None) -> None:
    """Insert one training_samples row directly."""
    with get_db(db_path) as conn:
        if decided_at is None:
            conn.execute(
                """INSERT INTO training_samples
                   (sample_uuid, decision, features_json, overall_score, schema_version)
                   VALUES (?, ?, ?, ?, ?)""",
                (uuid, decision, json.dumps(features), 70.0, FEATURE_SCHEMA_VERSION),
            )
        else:
            conn.execute(
                """INSERT INTO training_samples
                   (sample_uuid, decision, features_json, overall_score, schema_version, decided_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (uuid, decision, json.dumps(features), 70.0, FEATURE_SCHEMA_VERSION, decided_at),
            )


def test_shooting_cameras_groups_and_orders(dash_db):
    _seed_shooting_log(dash_db, [
        dict(sample_uuid="u1", shot_at="2026-04-10 12:00:00", camera="X100VI",
             lens_model="Fuji 23mm", film_simulation="Velvia",
             format="RAF", focal_length_mm=23.0, aperture=2.0,
             shutter_speed=0.004, iso=400, overall_score=72.0),
        dict(sample_uuid="u2", shot_at="2026-04-11 14:00:00", camera="X100VI",
             lens_model="Fuji 23mm", film_simulation="Acros",
             format="RAF", focal_length_mm=23.0, aperture=4.0,
             shutter_speed=0.001, iso=200, overall_score=68.0),
        dict(sample_uuid="u3", shot_at="2026-04-12 09:00:00", camera="Z6III",
             lens_model="NIKKOR 50mm", film_simulation="STANDARD",
             format="NEF", focal_length_mm=50.0, aperture=1.8,
             shutter_speed=0.002, iso=200, overall_score=75.0),
    ])

    out = dash_mod.shooting_cameras()
    cams = out["cameras"]
    # Two distinct cameras, X100VI first because it has more shots.
    assert len(cams) == 2
    assert cams[0]["camera"] == "X100VI"
    assert cams[0]["count"] == 2
    assert cams[1]["camera"] == "Z6III"
    assert cams[1]["count"] == 1


def test_shooting_distributions_buckets_correctly(dash_db):
    _seed_shooting_log(dash_db, [
        dict(sample_uuid="u1", shot_at="2026-04-10 12:00:00", camera="X100VI",
             lens_model="Fuji 23mm", film_simulation="Velvia",
             format="RAF", focal_length_mm=23.0, aperture=2.0,
             shutter_speed=0.004, iso=400, overall_score=72.0),
        dict(sample_uuid="u2", shot_at="2026-04-10 13:00:00", camera="X100VI",
             lens_model="Fuji 23mm", film_simulation="Velvia",
             format="RAF", focal_length_mm=23.0, aperture=2.0,
             shutter_speed=0.004, iso=400, overall_score=70.0),
        dict(sample_uuid="u3", shot_at="2026-04-12 09:00:00", camera="Z6III",
             lens_model="NIKKOR 50mm", film_simulation="STANDARD",
             format="NEF", focal_length_mm=50.0, aperture=1.8,
             shutter_speed=0.002, iso=200, overall_score=75.0),
    ])

    out = dash_mod.shooting_distributions()
    # Focal-length buckets — 23mm falls into '21–27mm', 50mm falls into '50–69mm'.
    fl = {b["bucket"]: b["count"] for b in out["focal_length"]}
    assert fl.get("21–27mm") == 2
    assert fl.get("50–69mm") == 1
    # Aperture buckets — 2.0 → 'f/2', 1.8 → 'f/2'.
    ap = {b["bucket"]: b["count"] for b in out["aperture"]}
    assert ap.get("f/2") == 3, f"Expected 3 f/2 shots, got {ap}"
    # Film sim — 2 Velvia + 1 STANDARD.
    fs = {b["bucket"]: b["count"] for b in out["film_simulation"]}
    assert fs.get("Velvia") == 2
    assert fs.get("STANDARD") == 1


def test_decisions_timeline_pivots_kmx(dash_db):
    # Three decisions in the same week.
    _seed_training_sample(
        dash_db, uuid="u1", decision="keep",
        features={n: 0.5 for n in feature_names()},
        decided_at="2026-04-10 12:00:00",
    )
    _seed_training_sample(
        dash_db, uuid="u2", decision="keep",
        features={n: 0.5 for n in feature_names()},
        decided_at="2026-04-10 14:00:00",
    )
    _seed_training_sample(
        dash_db, uuid="u3", decision="reject",
        features={n: 0.5 for n in feature_names()},
        decided_at="2026-04-11 09:00:00",
    )

    out = dash_mod.decisions_timeline(bucket="week")
    assert len(out["rows"]) == 1
    row = out["rows"][0]
    assert row["keep"] == 2
    assert row["reject"] == 1
    assert row["maybe"] == 0


def test_feature_deltas_computes_medians(dash_db):
    """Two keeps with iso=200, two rejects with iso=6400 → kept_median=200,
    rejected_median=6400 for the iso feature."""
    base = {n: 0.5 for n in feature_names()}
    keep_features = {**base, "iso": 200.0}
    rej_features  = {**base, "iso": 6400.0}

    _seed_training_sample(dash_db, uuid="u1", decision="keep",   features=keep_features)
    _seed_training_sample(dash_db, uuid="u2", decision="keep",   features=keep_features)
    _seed_training_sample(dash_db, uuid="u3", decision="reject", features=rej_features)
    _seed_training_sample(dash_db, uuid="u4", decision="reject", features=rej_features)

    out = dash_mod.decisions_feature_deltas()
    iso_row = next(f for f in out["features"] if f["feature"] == "iso")
    assert iso_row["kept_median"] == 200.0
    assert iso_row["rejected_median"] == 6400.0
    assert iso_row["n_kept"] == 2
    assert iso_row["n_rejected"] == 2


def test_invalid_bucket_returns_error(dash_db):
    out = dash_mod.decisions_timeline(bucket="day")
    assert out["rows"] == []
    assert "error" in out


# ---------------------------------------------------------------------------
# `since` query param — windows decisions & shooting endpoints
# ---------------------------------------------------------------------------


def test_since_windows_feature_deltas(dash_db):
    """`since` filters training_samples by decided_at; samples older than the
    cutoff drop out of the median calculation."""
    base = {n: 0.5 for n in feature_names()}
    _seed_training_sample(dash_db, uuid="old1", decision="keep",
                          features={**base, "iso": 100.0},
                          decided_at="2026-01-15 12:00:00")
    _seed_training_sample(dash_db, uuid="old2", decision="reject",
                          features={**base, "iso": 100.0},
                          decided_at="2026-01-15 13:00:00")
    _seed_training_sample(dash_db, uuid="new1", decision="keep",
                          features={**base, "iso": 6400.0},
                          decided_at="2026-05-01 12:00:00")
    _seed_training_sample(dash_db, uuid="new2", decision="reject",
                          features={**base, "iso": 6400.0},
                          decided_at="2026-05-01 13:00:00")

    # All-time: 4 samples total.
    out_all = dash_mod.decisions_feature_deltas()
    iso_all = next(f for f in out_all["features"] if f["feature"] == "iso")
    assert iso_all["n_kept"] == 2 and iso_all["n_rejected"] == 2

    # Since 2026-04-01: only the 'new' pair remains.
    out_recent = dash_mod.decisions_feature_deltas(since="2026-04-01")
    iso_recent = next(f for f in out_recent["features"] if f["feature"] == "iso")
    assert iso_recent["n_kept"] == 1
    assert iso_recent["n_rejected"] == 1
    assert iso_recent["kept_median"] == 6400.0


def test_since_windows_shooting_cameras(dash_db):
    _seed_shooting_log(dash_db, [
        dict(sample_uuid="o1", shot_at="2026-01-10 12:00:00", camera="X100VI",
             lens_model=None, film_simulation=None,
             format="RAF", focal_length_mm=23.0, aperture=2.0,
             shutter_speed=0.004, iso=400, overall_score=72.0),
        dict(sample_uuid="n1", shot_at="2026-05-10 12:00:00", camera="Z6III",
             lens_model=None, film_simulation=None,
             format="NEF", focal_length_mm=50.0, aperture=2.8,
             shutter_speed=0.004, iso=400, overall_score=72.0),
    ])

    all_cams = dash_mod.shooting_cameras()["cameras"]
    assert {c["camera"] for c in all_cams} == {"X100VI", "Z6III"}

    recent = dash_mod.shooting_cameras(since="2026-04-01")["cameras"]
    assert {c["camera"] for c in recent} == {"Z6III"}


def test_since_invalid_returns_400(dash_db):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        dash_mod.decisions_feature_deltas(since="not-a-date")
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# Persistence — clearing images doesn't touch shooting_log or training_samples
# ---------------------------------------------------------------------------


def test_dashboard_data_survives_clear_images(dash_db):
    """Wipe the images table and confirm shooting_log + training_samples
    rows are still queryable."""
    _seed_shooting_log(dash_db, [
        dict(sample_uuid="u1", shot_at="2026-04-10 12:00:00", camera="X100VI",
             lens_model=None, film_simulation=None,
             format="RAF", focal_length_mm=23.0, aperture=2.0,
             shutter_speed=0.004, iso=400, overall_score=72.0),
    ])
    _seed_training_sample(
        dash_db, uuid="u1", decision="keep",
        features={n: 0.5 for n in feature_names()},
    )

    # Clear images (the simulated /clear path).
    with get_db(dash_db) as conn:
        conn.execute("DELETE FROM images")

    cams = dash_mod.shooting_cameras()["cameras"]
    timeline = dash_mod.decisions_timeline(bucket="week")["rows"]

    assert len(cams) == 1, "shooting_log should survive image deletion"
    assert len(timeline) == 1, "training_samples should survive image deletion"
