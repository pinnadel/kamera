"""
analysis_mode='jpeg_priority' — skip-scoring a RAW with a processed sibling,
and the companion-mirror gap it exposed.

A RAW file whose same-stem JPEG/HIF sibling exists gets a lightweight
`images` row (analysis_status='skipped', every score column NULL) instead of
full scoring — see backend/routers/settings.py::analysis_mode and
backend/constants.py::has_processed_sibling.

The existing companion-mirror in _apply_decision / _apply_undo only fired
when raw_jpeg_display_mode hid one side of a pair. A 'skipped' row is a
DIFFERENT reason to mirror — it's unconditional on display mode, since a
skipped RAW is never independently reviewable regardless of whether
raw_jpeg_display_mode is 'both'. This file exercises that gap: mode='both'
(today's default) + a skipped companion must still mirror, and GET /images
must hide the skipped row unconditionally.

Reuses the fixture/helper pattern from test_raw_jpeg_companion.py.
"""
from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

import backend.routers.decisions as decisions_mod
import backend.sources.folder as folder_sink_mod
import backend.routers.images as images_mod
from backend.database import get_db


def _insert_image(
    db_path: Path,
    *,
    image_id: int,
    uuid: str,
    filename: str,
    fmt: str,
    source_folder: str = "/fake/source",
    analysis_status: str = "done",
    overall_score: float | None = 80.0,
    sharpness_score: float | None = 80.0,
) -> None:
    with get_db(db_path) as conn:
        conn.execute(
            """
            INSERT INTO images (id, file_path, filename, uuid, source_folder,
                                format, overall_score, sharpness_score, analysis_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (image_id, f"{source_folder}/{filename}", filename, uuid,
             source_folder, fmt, overall_score, sharpness_score, analysis_status),
        )


@pytest.fixture()
def patched(tmp_db: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    @contextlib.contextmanager
    def _get_db(*_args, **_kwargs):
        with get_db(tmp_db) as conn:
            yield conn

    monkeypatch.setattr(decisions_mod, "get_db", _get_db)
    monkeypatch.setattr(images_mod, "get_db", _get_db)
    monkeypatch.setattr(folder_sink_mod, "move_photo",
                        lambda current, dest: str(current))
    monkeypatch.setattr(decisions_mod, "maybe_train_async", lambda *_a, **_k: None)
    monkeypatch.setattr(images_mod._personal_model, "_pipeline", None, raising=False)

    # raw_jpeg_display_mode stays at its real default ('both') for these
    # tests — the whole point is proving the mirror fires WITHOUT the display
    # setting hiding anything, purely because the companion is 'skipped'.
    monkeypatch.setattr(decisions_mod, "get_raw_jpeg_display_mode", lambda: "both")
    monkeypatch.setattr(images_mod, "get_raw_jpeg_display_mode", lambda: "both")

    return tmp_db


def _decision(db_path: Path, image_id: int) -> str | None:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT decision FROM decisions WHERE image_id = ?", (image_id,)
        ).fetchone()
        return row[0] if row else None


def test_decision_mirrors_to_skipped_companion_even_with_mode_both(
    patched: Path,
) -> None:
    """The gap this feature exposed: display mode='both' used to mean 'never
    mirror'. A 'skipped' companion must still get the decision mirrored onto
    it — it's the ONLY way it ever moves, since it has no scores to be
    independently judged by."""
    _insert_image(patched, image_id=1, uuid="jpg-1", filename="DSCF1000.JPG", fmt="JPG")
    _insert_image(patched, image_id=2, uuid="raw-1", filename="DSCF1000.RAF",
                  fmt="RAF", analysis_status="skipped",
                  overall_score=None, sharpness_score=None)

    decisions_mod._apply_decision(1, "keep")

    assert _decision(patched, 1) == "keep"
    assert _decision(patched, 2) == "keep"


def test_undo_mirrors_to_skipped_companion_even_with_mode_both(
    patched: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Undo's Path.exists() checks are real filesystem checks — use genuine
    files + the real move_photo (same workaround as
    test_raw_jpeg_companion.py::test_undo_mirrors_to_companion)."""
    from backend.file_mover import move_photo as real_move_photo
    monkeypatch.setattr(folder_sink_mod, "move_photo", real_move_photo)

    source_folder = tmp_path / "source"
    source_folder.mkdir()
    jpg_path = source_folder / "DSCF2000.JPG"
    raw_path = source_folder / "DSCF2000.RAF"
    jpg_path.write_bytes(b"jpg")
    raw_path.write_bytes(b"raw")

    _insert_image(patched, image_id=1, uuid="jpg-2", filename="DSCF2000.JPG",
                  fmt="JPG", source_folder=str(source_folder))
    _insert_image(patched, image_id=2, uuid="raw-2", filename="DSCF2000.RAF",
                  fmt="RAF", source_folder=str(source_folder), analysis_status="skipped",
                  overall_score=None, sharpness_score=None)

    decisions_mod._apply_decision(1, "keep")
    assert _decision(patched, 2) == "keep"
    # Manual keep (is_auto=False, the default) routes straight to
    # _Keeps_reviewed — see docs/PLAN-keeps-reviewed.md.
    assert (source_folder / "_Keeps" / "_Keeps_reviewed" / "DSCF2000.JPG").exists()
    assert (source_folder / "_Keeps" / "_Keeps_reviewed" / "DSCF2000.RAF").exists()

    decisions_mod._apply_undo(1, str(jpg_path))

    assert _decision(patched, 1) is None
    assert _decision(patched, 2) is None
    assert jpg_path.exists()
    assert raw_path.exists()


def test_skipped_row_never_trains(patched: Path) -> None:
    _insert_image(patched, image_id=1, uuid="jpg-3", filename="DSCF3000.JPG", fmt="JPG")
    _insert_image(patched, image_id=2, uuid="raw-3", filename="DSCF3000.RAF",
                  fmt="RAF", analysis_status="skipped",
                  overall_score=None, sharpness_score=None)

    decisions_mod._apply_decision(1, "keep")

    with get_db(patched) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM training_samples WHERE sample_uuid = ?", ("raw-3",)
        ).fetchone()[0]
    assert count == 0


def test_list_images_hides_skipped_row_unconditionally(patched: Path) -> None:
    """A 'skipped' row never renders as a tile, regardless of
    raw_jpeg_display_mode='both' — it exists purely for mirror bookkeeping."""
    _insert_image(patched, image_id=1, uuid="jpg-4", filename="DSCF4000.JPG", fmt="JPG")
    _insert_image(patched, image_id=2, uuid="raw-4", filename="DSCF4000.RAF",
                  fmt="RAF", analysis_status="skipped",
                  overall_score=None, sharpness_score=None)

    out = images_mod.list_images(source_folder="/fake/source")

    ids = {row["id"] for row in out}
    assert ids == {1}


def test_orphan_raw_still_scores_in_full_when_no_sibling_on_disk(tmp_path: Path) -> None:
    """has_processed_sibling requires a REAL sibling file on disk (not a DB
    row) — an orphan RAW (no JPEG next to it) is never skippable."""
    from backend.constants import has_processed_sibling

    folder = tmp_path / "source"
    folder.mkdir()
    raw_path = folder / "DSCF5000.RAF"
    raw_path.write_bytes(b"raw")

    assert has_processed_sibling(raw_path) is False


def test_raw_with_jpeg_sibling_on_disk_is_skippable(tmp_path: Path) -> None:
    from backend.constants import has_processed_sibling

    folder = tmp_path / "source"
    folder.mkdir()
    raw_path = folder / "DSCF6000.RAF"
    jpg_path = folder / "DSCF6000.JPG"
    raw_path.write_bytes(b"raw")
    jpg_path.write_bytes(b"jpg")

    assert has_processed_sibling(raw_path) is True


def test_non_raw_file_is_never_skippable(tmp_path: Path) -> None:
    """A JPEG itself is never a skip candidate — only RAW formats are."""
    from backend.constants import has_processed_sibling

    folder = tmp_path / "source"
    folder.mkdir()
    jpg_path = folder / "DSCF7000.JPG"
    raw_path = folder / "DSCF7000.RAF"
    jpg_path.write_bytes(b"jpg")
    raw_path.write_bytes(b"raw")

    assert has_processed_sibling(jpg_path) is False
