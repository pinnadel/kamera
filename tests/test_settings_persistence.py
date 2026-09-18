"""
Settings persistence round-trip — specifically that every field the frontend
sends is actually saved and read back.

Regression guard for the PR3 boundary-routing settings
(auto_cull_uncertain_to_maybe / uncertainty_threshold): they used to be
POSTed by the UI but dropped by FastAPI because they were absent from
SettingsRequest and the tunable registries, so they never persisted and
always fell back to defaults. This test asserts they survive a POST → GET
round-trip and land in tunable_setting_keys() (so they're also backed up).

Strategy mirrors test_dashboard: monkeypatch get_db on the settings module
so the endpoint functions read/write a tmp DB, then call them directly.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest

from backend import database as db_mod
from backend.database import create_tables, get_db
from backend.routers import settings as settings_mod
from backend.routers.settings import (
    SettingsRequest,
    get_raw_jpeg_display_mode,
    get_settings,
    tunable_setting_keys,
    update_settings,
)


@pytest.fixture()
def settings_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    p = tmp_path / "settings_test.db"
    create_tables(p)
    real_get_db = db_mod.get_db

    # Ignore whatever db_path the caller passes (including database.py's
    # get_setting(), which always passes its DEFAULT_DB_PATH default
    # explicitly) and redirect everything to the tmp DB. A bare `db_path: Path
    # = p` default here would NOT catch that case, since get_setting() never
    # omits the argument for us to fall back on.
    def _scoped_get_db(*_args, **_kwargs):
        return real_get_db(p)

    monkeypatch.setattr(db_mod, "get_db", _scoped_get_db)
    monkeypatch.setattr(settings_mod, "get_db", _scoped_get_db)
    yield p


# ---------------------------------------------------------------------------
# The PR3 boundary-routing keys are wired end to end
# ---------------------------------------------------------------------------


def test_uncertainty_keys_are_tunable() -> None:
    keys = tunable_setting_keys()
    assert "auto_cull_uncertain_to_maybe" in keys
    assert "uncertainty_threshold" in keys


def test_uncertainty_settings_round_trip(settings_db: Path) -> None:
    """POST non-default values → GET returns exactly those, not the defaults."""
    # Defaults are True / 8.0; write the opposite so a fallback would be visible.
    update_settings(SettingsRequest(
        auto_cull_uncertain_to_maybe=False,
        uncertainty_threshold=12.5,
    ))

    out = get_settings()
    assert out["auto_cull_uncertain_to_maybe"] is False
    assert out["uncertainty_threshold"] == 12.5
    # And they are no longer flagged as defaults.
    assert out["auto_cull_uncertain_to_maybe_is_default"] is False
    assert out["uncertainty_threshold_is_default"] is False

    # The raw rows actually exist in the settings table.
    with get_db(settings_db) as conn:
        rows = {r["key"]: r["value"] for r in conn.execute(
            "SELECT key, value FROM settings WHERE key IN "
            "('auto_cull_uncertain_to_maybe', 'uncertainty_threshold')"
        )}
    assert rows["auto_cull_uncertain_to_maybe"] == "0"
    assert rows["uncertainty_threshold"] == "12.5000"


def test_uncertainty_defaults_when_unset(settings_db: Path) -> None:
    """With nothing written, GET resolves the documented defaults."""
    out = get_settings()
    assert out["auto_cull_uncertain_to_maybe"] is True
    assert out["uncertainty_threshold"] == 8.0
    assert out["auto_cull_uncertain_to_maybe_is_default"] is True
    assert out["uncertainty_threshold_is_default"] is True


def test_null_reverts_uncertainty_threshold_to_default(settings_db: Path) -> None:
    """Passing null for a previously-set key clears the row (revert to default)."""
    update_settings(SettingsRequest(uncertainty_threshold=20.0))
    assert get_settings()["uncertainty_threshold"] == 20.0

    update_settings(SettingsRequest(uncertainty_threshold=None))
    out = get_settings()
    assert out["uncertainty_threshold"] == 8.0
    assert out["uncertainty_threshold_is_default"] is True


# ---------------------------------------------------------------------------
# raw_jpeg_display_mode — the first string/enum setting (previously the
# registries only held numeric + bool). Exercises the new _STRING_SETTINGS
# path end to end.
# ---------------------------------------------------------------------------


def test_raw_jpeg_display_mode_is_tunable() -> None:
    assert "raw_jpeg_display_mode" in tunable_setting_keys()


def test_raw_jpeg_display_mode_defaults_to_both(settings_db: Path) -> None:
    out = get_settings()
    assert out["raw_jpeg_display_mode"] == "both"
    assert out["raw_jpeg_display_mode_is_default"] is True
    assert get_raw_jpeg_display_mode() == "both"


def test_raw_jpeg_display_mode_round_trip(settings_db: Path) -> None:
    update_settings(SettingsRequest(raw_jpeg_display_mode="raw"))

    out = get_settings()
    assert out["raw_jpeg_display_mode"] == "raw"
    assert out["raw_jpeg_display_mode_is_default"] is False
    assert get_raw_jpeg_display_mode() == "raw"

    with get_db(settings_db) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'raw_jpeg_display_mode'"
        ).fetchone()
    assert row["value"] == "raw"


def test_raw_jpeg_display_mode_rejects_invalid_value(settings_db: Path) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        update_settings(SettingsRequest(raw_jpeg_display_mode="jpeg_only"))
    assert exc_info.value.status_code == 400


def test_null_reverts_raw_jpeg_display_mode_to_default(settings_db: Path) -> None:
    update_settings(SettingsRequest(raw_jpeg_display_mode="processed"))
    assert get_settings()["raw_jpeg_display_mode"] == "processed"

    update_settings(SettingsRequest(raw_jpeg_display_mode=None))
    out = get_settings()
    assert out["raw_jpeg_display_mode"] == "both"
    assert out["raw_jpeg_display_mode_is_default"] is True
