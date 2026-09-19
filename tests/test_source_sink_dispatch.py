"""
A Photos-sourced photo must never reach a filesystem mutation.

The sink registry is dispatch, not safety — dispatch is an if-statement, and
if-statements get forgotten. These tests assert the property directly: with
every filesystem-mutating function in file_mover replaced by an explosive
stub, a full round of decision operations over a Photos row must complete
without any of them firing.

The complementary guarantee (file_mover refusing regardless of caller) is
tested in test_photos_library_guard.py.
"""
from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

import backend.file_mover as file_mover_mod
import backend.routers.decisions as decisions_mod
import backend.sources as sources_mod
import backend.sources.folder as folder_sink_mod
from backend.database import get_db


class _RecordingPhotosSink:
    """Stands in for PhotosSink: records calls, touches nothing."""

    source_type = "photos"
    supports_bulk_trash = False
    supports_mark_reviewed = False
    supports_auto_cull = False
    supports_companion_mirror = False
    supports_watch = False
    supports_folder_overrides = False
    supports_path_reaper = False

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def preflight(self) -> None:
        self.calls.append(("preflight",))

    def apply(self, image_row, decision, *, is_auto):
        self.calls.append(("apply", decision, is_auto))
        return image_row["file_path"]

    def undo(self, image_row, previous_path):
        self.calls.append(("undo",))
        return image_row["file_path"]

    def mark_reviewed(self, image_row):
        raise AssertionError("mark_reviewed must be refused before reaching the sink")

    def bulk_trash(self, image_row):
        raise AssertionError("bulk_trash must be refused before reaching the sink")


@pytest.fixture()
def env(tmp_db: Path, monkeypatch: pytest.MonkeyPatch):
    @contextlib.contextmanager
    def _get_db(*_args, **_kwargs):
        with get_db(tmp_db) as conn:
            yield conn

    monkeypatch.setattr(decisions_mod, "get_db", _get_db)
    monkeypatch.setattr(decisions_mod, "maybe_train_async", lambda *_a, **_k: None)

    # Arm the filesystem. Any call is a failure, wherever it comes from.
    def _boom(*_a, **_k):
        raise AssertionError("a Photos row reached a filesystem mutation")

    for name in ("move_photo", "trash_photo"):
        monkeypatch.setattr(file_mover_mod, name, _boom)
        monkeypatch.setattr(folder_sink_mod, name, _boom)
    monkeypatch.setattr(file_mover_mod, "send2trash", _boom)

    sink = _RecordingPhotosSink()
    monkeypatch.setitem(sources_mod._SINKS, "photos", sink)

    with get_db(tmp_db) as conn:
        conn.execute(
            """
            INSERT INTO images (id, file_path, filename, uuid, source_folder,
                                source_type, photos_uuid, overall_score, analysis_status)
            VALUES (1, '/lib/Test.photoslibrary/originals/0/AAA.heic', 'IMG_1.HEIC',
                    'kamera-uuid', 'photos://lib/album', 'photos', 'photos-uuid',
                    80.0, 'done')
            """
        )
    return tmp_db, sink


def test_decision_on_a_photos_row_never_moves_a_file(env) -> None:
    _db, sink = env
    decisions_mod._apply_decision(1, "keep")
    assert ("apply", "keep", False) in sink.calls


def test_undo_on_a_photos_row_never_moves_a_file(env) -> None:
    _db, sink = env
    decisions_mod._apply_decision(1, "reject")
    decisions_mod._apply_undo(1, None)
    assert ("undo",) in sink.calls


def test_preflight_runs_before_every_commit(env) -> None:
    """The library-open check must gate the write, not merely accompany it."""
    _db, sink = env
    decisions_mod._apply_decision(1, "keep")
    assert sink.calls[0] == ("preflight",)
    assert sink.calls.index(("preflight",)) < sink.calls.index(("apply", "keep", False))


def test_mark_reviewed_is_refused_for_photos(env) -> None:
    from fastapi import HTTPException

    _db, _sink = env
    decisions_mod._apply_decision(1, "keep")
    with pytest.raises(HTTPException) as exc:
        decisions_mod._move_to_reviewed(1)
    assert exc.value.status_code == 400


def test_trash_rejects_skips_photos_and_keeps_the_row(env) -> None:
    db_path, _sink = env
    decisions_mod._apply_decision(1, "reject")

    result = decisions_mod.trash_rejects(
        decisions_mod.TrashRejectsRequest(source_folder="photos://lib/album")
    )

    assert result["trashed"] == 0
    assert result["skipped_unsupported"] == 1
    with get_db(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM images WHERE id = 1").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE image_id = 1"
        ).fetchone()[0] == 1, "the rejection must survive a sweep that could not act on it"


def test_auto_cull_never_sees_photos_rows(env) -> None:
    db_path, _sink = env
    preview = decisions_mod.auto_cull_preview()
    assert preview["total"] == 0, "an undecided Photos row must not be an auto-cull candidate"


def test_auto_cull_rejects_a_photos_source_folder(env) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        decisions_mod.run_auto_cull(source_folder="photos://lib/album")
    assert exc.value.status_code == 400
