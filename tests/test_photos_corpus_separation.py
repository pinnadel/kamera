"""
Photos-album decisions must never reach the camera taste model.

An iPhone HEIC and a camera RAF score on entirely different sharpness,
aperture and ISO distributions. Folding both into one GBR teaches it
contradictory labels — the same failure mode the RAW+JPEG companion mirror
avoids with `_train=False`. The separation is a `corpus` column on
training_samples plus a WHERE clause everywhere that reads it.

These drive `_apply_decision` against an isolated tmp DB with the filesystem
move and the retrain trigger stubbed out.
"""
from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

import backend.routers.decisions as decisions_mod
import backend.sources as sources_mod
import backend.sources.folder as folder_sink_mod
import phase3_learning.auto_trainer as trainer_mod
from backend.database import get_db


def _insert_image(db_path: Path, *, image_id: int, uuid: str, source_type: str) -> None:
    folder = "photos://lib/album" if source_type == "photos" else "/fake/source"
    with get_db(db_path) as conn:
        conn.execute(
            """
            INSERT INTO images (id, file_path, filename, uuid, source_folder,
                                source_type, overall_score, analysis_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'done')
            """,
            (image_id, f"/fake/source/{uuid}.RAF", f"{uuid}.RAF", uuid,
             folder, source_type, 80.0),
        )


@pytest.fixture()
def patched(tmp_db: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    @contextlib.contextmanager
    def _get_db(*_args, **_kwargs):
        with get_db(tmp_db) as conn:
            yield conn

    monkeypatch.setattr(decisions_mod, "get_db", _get_db)
    monkeypatch.setattr(folder_sink_mod, "move_photo", lambda current, dest: str(current))
    monkeypatch.setattr(decisions_mod, "maybe_train_async", lambda *_a, **_k: None)
    monkeypatch.setattr(trainer_mod, "get_db", _get_db)

    # PhotosSink itself is exercised in test_photos_sink.py; here we only need
    # *a* sink registered under 'photos' so dispatch resolves. It records
    # nothing and moves nothing — which is the point: a Photos decision must
    # reach the training corpus without touching the filesystem.
    class _StubPhotosSink:
        source_type = "photos"
        supports_bulk_trash = False
        supports_mark_reviewed = False
        supports_auto_cull = False
        supports_companion_mirror = False
        supports_watch = False
        supports_folder_overrides = False
        supports_path_reaper = False

        def preflight(self) -> None:
            pass

        def apply(self, image_row, decision, *, is_auto):
            return image_row["file_path"]

        def undo(self, image_row, previous_path):
            return image_row["file_path"]

        def mark_reviewed(self, image_row):
            raise AssertionError("not supported")

        def bulk_trash(self, image_row):
            raise AssertionError("not supported")

    monkeypatch.setitem(sources_mod._SINKS, "photos", _StubPhotosSink())
    return tmp_db


def _corpus_of(db_path: Path, uuid: str) -> str | None:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT corpus FROM training_samples WHERE sample_uuid = ?", (uuid,)
        ).fetchone()
    return row["corpus"] if row else None


def test_folder_decision_lands_in_folder_corpus(patched: Path) -> None:
    _insert_image(patched, image_id=1, uuid="u-folder", source_type="folder")
    decisions_mod._apply_decision(1, "keep")
    assert _corpus_of(patched, "u-folder") == "folder"


def test_photos_decision_lands_in_photos_corpus(patched: Path) -> None:
    _insert_image(patched, image_id=2, uuid="u-photos", source_type="photos")
    decisions_mod._apply_decision(2, "keep")
    assert _corpus_of(patched, "u-photos") == "photos"


def test_trainer_never_sees_photos_samples(patched: Path) -> None:
    """
    _count_samples drives the retrain freshness gate. If it counted Photos
    rows, a run of Photos decisions would trigger retrains on a folder corpus
    that had not changed at all.
    """
    for i in range(5):
        _insert_image(patched, image_id=10 + i, uuid=f"u-f{i}", source_type="folder")
        decisions_mod._apply_decision(10 + i, "keep")
    for i in range(7):
        _insert_image(patched, image_id=20 + i, uuid=f"u-p{i}", source_type="photos")
        decisions_mod._apply_decision(20 + i, "reject")

    with get_db(patched) as conn:
        total = conn.execute("SELECT COUNT(*) FROM training_samples").fetchone()[0]
    assert total == 12, "both corpora are stored"
    assert trainer_mod._count_samples() == 5, "but the trainer only counts folder rows"


def test_redeciding_a_photos_photo_keeps_its_corpus(patched: Path) -> None:
    """The ON CONFLICT path must carry corpus too, or a re-decision relabels it."""
    _insert_image(patched, image_id=3, uuid="u-re", source_type="photos")
    decisions_mod._apply_decision(3, "keep")
    decisions_mod._apply_decision(3, "reject")
    assert _corpus_of(patched, "u-re") == "photos"
    with get_db(patched) as conn:
        row = conn.execute(
            "SELECT decision FROM training_samples WHERE sample_uuid = 'u-re'"
        ).fetchone()
    assert row["decision"] == "reject"
