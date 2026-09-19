"""
A boolean setting must read back as what the settings endpoint wrote.

`reject_to_system_trash` was written as "1" by POST /settings and read with a
literal `== "true"` test in sources/folder.py, so it evaluated False no matter
how the user set it — the toggle did nothing from the day it shipped. It failed
in the safe direction (rejects kept landing in the recoverable _Rejects/ folder
instead of the OS Trash), which is why it went unnoticed, and unit tests never
caught it because they patch the reader rather than exercise the round trip.

So these tests deliberately go through the real writer and the real reader.
Anything that mocks either one would re-open the same hole.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.database import get_bool_setting, get_setting, set_setting
from backend.routers.settings import _BOOL_SETTINGS


# Every form the writer has used or might use, plus the casings a hand-edited
# DB could contain. All of them mean True.
@pytest.mark.parametrize("stored", ["1", "true", "True", "TRUE", "yes", "on", " 1 "])
def test_truthy_forms_all_read_as_true(tmp_db: Path, stored: str) -> None:
    set_setting("some_flag", stored, db_path=tmp_db)
    assert get_bool_setting("some_flag", False, db_path=tmp_db) is True


@pytest.mark.parametrize("stored", ["0", "false", "False", "no", "off", ""])
def test_falsy_forms_all_read_as_false(tmp_db: Path, stored: str) -> None:
    set_setting("some_flag", stored, db_path=tmp_db)
    assert get_bool_setting("some_flag", True, db_path=tmp_db) is False


def test_missing_key_returns_the_default(tmp_db: Path) -> None:
    assert get_bool_setting("never_written", True, db_path=tmp_db) is True
    assert get_bool_setting("never_written", False, db_path=tmp_db) is False


def test_the_writers_format_round_trips(tmp_db: Path) -> None:
    """Pin the exact contract: what routers/settings.py writes, this reads.

    If the write format ever changes, this fails here rather than silently
    disabling a toggle somewhere downstream.
    """
    # Mirrors the INSERT in update_settings' _BOOL_SETTINGS loop.
    for value, expected in ((True, True), (False, False)):
        set_setting("round_trip", "1" if value else "0", db_path=tmp_db)
        assert get_bool_setting("round_trip", not expected, db_path=tmp_db) is expected


def test_reject_to_system_trash_is_a_registered_bool() -> None:
    """The regression's subject, pinned by name.

    sources/folder.py reads this key; routers/settings.py owns its format.
    If it ever leaves the bool registry, the two sides have drifted again.
    """
    assert "reject_to_system_trash" in _BOOL_SETTINGS


def test_folder_sink_sees_the_setting_the_endpoint_wrote(tmp_db: Path, monkeypatch) -> None:
    """The actual bug, end to end: writer format in, sink's answer out."""
    import backend.database as db_mod
    import backend.sources.folder as folder_mod

    monkeypatch.setattr(
        db_mod, "DEFAULT_DB_PATH", tmp_db
    )
    monkeypatch.setattr(
        folder_mod, "get_bool_setting",
        lambda key, default=False: get_bool_setting(key, default, db_path=tmp_db),
    )

    set_setting("reject_to_system_trash", "1", db_path=tmp_db)
    assert folder_mod._reject_to_system_trash_enabled() is True, (
        "the toggle must fire when the endpoint's own format is in the DB"
    )

    set_setting("reject_to_system_trash", "0", db_path=tmp_db)
    assert folder_mod._reject_to_system_trash_enabled() is False


def test_get_setting_still_returns_the_raw_string(tmp_db: Path) -> None:
    """get_bool_setting is an addition, not a change to the string reader."""
    set_setting("some_flag", "1", db_path=tmp_db)
    assert get_setting("some_flag", db_path=tmp_db) == "1"
