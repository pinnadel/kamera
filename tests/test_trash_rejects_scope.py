"""
`POST /trash-rejects` must only ever touch the tab it was asked about.

The endpoint used to take no body and sweep every reject in the database,
while the button that calls it lives in a bar labelled with the ACTIVE tab's
reject count. Pressing it in folder B therefore trashed folder A's rejects
under a number that described neither — a data-loss-adjacent mismatch between
what the UI promised and what the server did.

This is a containment property, so it is asserted directly: with rejects in
two folders, a scoped sweep must leave the other folder untouched, files and
rows alike.
"""
from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

import backend.file_mover as file_mover_mod
import backend.routers.decisions as decisions_mod
import backend.sources.folder as folder_sink_mod
from backend.database import get_db


FOLDER_A = "/tmp/shoot-a"
FOLDER_B = "/tmp/shoot-b"


@pytest.fixture()
def two_folders(tmp_db: Path, monkeypatch: pytest.MonkeyPatch):
    """Two folder tabs, one reject in each, with send2trash recorded not run."""

    @contextlib.contextmanager
    def _get_db(*_args, **_kwargs):
        with get_db(tmp_db) as conn:
            yield conn

    monkeypatch.setattr(decisions_mod, "get_db", _get_db)
    monkeypatch.setattr(decisions_mod, "maybe_train_async", lambda *_a, **_k: None)

    trashed: list[str] = []

    def _record_trash(path, *_a, **_k):
        trashed.append(str(path))
        return str(path)

    # Record instead of touching the disk. Patched on the sink module because
    # that is the name sources/folder.py actually calls (see the note in
    # test_source_sink_dispatch.py).
    monkeypatch.setattr(folder_sink_mod, "trash_photo", _record_trash)
    monkeypatch.setattr(file_mover_mod, "trash_photo", _record_trash)

    with get_db(tmp_db) as conn:
        for image_id, folder in ((1, FOLDER_A), (2, FOLDER_B)):
            conn.execute(
                """
                INSERT INTO images (id, file_path, filename, uuid, source_folder,
                                    source_type, overall_score, analysis_status)
                VALUES (?, ?, ?, ?, ?, 'folder', 42.0, 'done')
                """,
                (image_id, f"{folder}/_Rejects/IMG_{image_id}.RAF",
                 f"IMG_{image_id}.RAF", f"uuid-{image_id}", folder),
            )
            conn.execute(
                "INSERT INTO decisions (image_id, decision, reason) VALUES (?, 'reject', 'manual')",
                (image_id,),
            )

    return tmp_db, trashed


def test_sweep_touches_only_the_named_folder(two_folders) -> None:
    db_path, trashed = two_folders

    result = decisions_mod.trash_rejects(
        decisions_mod.TrashRejectsRequest(source_folder=FOLDER_A)
    )

    assert result["trashed"] == 1
    assert trashed == [f"{FOLDER_A}/_Rejects/IMG_1.RAF"], (
        "the other folder's file must never reach send2trash"
    )


def test_the_other_folders_rows_survive(two_folders) -> None:
    db_path, _trashed = two_folders

    decisions_mod.trash_rejects(
        decisions_mod.TrashRejectsRequest(source_folder=FOLDER_A)
    )

    with get_db(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM images WHERE id = 1").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM images WHERE id = 2").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE image_id = 2"
        ).fetchone()[0] == 1


def test_an_unknown_folder_is_a_no_op(two_folders) -> None:
    # Not an error: a tab whose rejects were already swept is a normal state.
    # It must simply do nothing rather than fall back to a wider sweep.
    db_path, trashed = two_folders

    result = decisions_mod.trash_rejects(
        decisions_mod.TrashRejectsRequest(source_folder="/tmp/never-analyzed")
    )

    assert result["trashed"] == 0
    assert trashed == []
    with get_db(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 2


def test_source_folder_is_required() -> None:
    """Omitting the scope must fail loudly, not sweep everything."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        decisions_mod.TrashRejectsRequest()
