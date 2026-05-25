"""
Tests for GET /folders/unfinished — the "resume after interruption" endpoint.

The endpoint walks the folder on disk, intersects with the
`images` rows whose analysis_status='done' on that source_folder,
and returns the delta. The UI uses this delta to surface a "Resume
analysis" banner so a user whose batch was interrupted (laptop
closed, app crash) can continue with one click.

Strategy mirrors test_dashboard.py: monkeypatch get_db to read a
tmp DB, then call the endpoint function directly.
"""
import pytest
from pathlib import Path

from backend import database as db_mod
from backend.database import create_tables
from backend.routers import images as images_mod


@pytest.fixture()
def patched_db(tmp_path: Path, monkeypatch):
    p = tmp_path / "test.db"
    create_tables(p)
    real_get_db = db_mod.get_db

    def _patched(db_path: Path = p):
        return real_get_db(db_path)

    monkeypatch.setattr(db_mod, "get_db", _patched)
    monkeypatch.setattr(images_mod, "get_db", _patched)
    return p


def _seed_done(db_path: Path, file_paths: list[str], source_folder: str) -> None:
    """Insert one analysis_status='done' row per file_path."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    for fp in file_paths:
        conn.execute(
            "INSERT INTO images (filename, file_path, source_folder, analysis_status) "
            "VALUES (?, ?, ?, 'done')",
            (Path(fp).name, fp, source_folder),
        )
    conn.commit()
    conn.close()


def test_unfinished_when_all_files_done(patched_db, tmp_path):
    """All supported files already analyzed → unfinished == 0."""
    folder = tmp_path / "shoot"
    folder.mkdir()
    (folder / "A.jpg").touch()
    (folder / "B.jpg").touch()
    _seed_done(patched_db, [str(folder / "A.jpg"), str(folder / "B.jpg")], str(folder))

    out = images_mod.folder_unfinished(folder_path=str(folder))
    assert out["total_on_disk"] == 2
    assert out["done_count"] == 2
    assert out["unfinished"] == 0


def test_unfinished_when_batch_interrupted(patched_db, tmp_path):
    """Partial done set → unfinished == total - done_count.

    Models the 'laptop closed mid-batch' case: 5 files on disk, 2 got
    their analysis_status='done' row before the interruption, 3 didn't.
    """
    folder = tmp_path / "interrupted"
    folder.mkdir()
    for name in ["A.jpg", "B.jpg", "C.jpg", "D.RAF", "E.NEF"]:
        (folder / name).touch()
    _seed_done(patched_db, [str(folder / "A.jpg"), str(folder / "B.jpg")], str(folder))

    out = images_mod.folder_unfinished(folder_path=str(folder))
    assert out["total_on_disk"] == 5
    assert out["done_count"] == 2
    assert out["unfinished"] == 3


def test_unfinished_skips_decision_subfolders(patched_db, tmp_path):
    """_Keeps/_Maybes/_Trash files must not count — they're moved
    photos, already analyzed pre-move. Without this skip, a folder
    finishes analysis and then permanently flags itself as 'has
    unfinished work' because the moved files now live in subdirs."""
    folder = tmp_path / "with_decisions"
    folder.mkdir()
    (folder / "A.jpg").touch()
    keeps = folder / "_Keeps"; keeps.mkdir()
    (keeps / "old_kept.jpg").touch()
    trash = folder / "_Trash"; trash.mkdir()
    (trash / "old_rejected.RAF").touch()
    _seed_done(patched_db, [str(folder / "A.jpg")], str(folder))

    # Non-recursive: only A.jpg in scope; skip dirs don't apply yet.
    out = images_mod.folder_unfinished(folder_path=str(folder))
    assert out["total_on_disk"] == 1
    assert out["unfinished"] == 0

    # Recursive: walks subdirs but the _Keeps/_Trash filter excludes them.
    out_rec = images_mod.folder_unfinished(folder_path=str(folder), include_subfolders=True)
    assert out_rec["total_on_disk"] == 1
    assert out_rec["unfinished"] == 0


def test_unfinished_ignores_unsupported_files(patched_db, tmp_path):
    """Non-photo files (e.g. .DS_Store, .txt, .mov) must not count."""
    folder = tmp_path / "mixed"
    folder.mkdir()
    (folder / "real.jpg").touch()
    (folder / ".DS_Store").touch()
    (folder / "notes.txt").touch()
    (folder / "video.mov").touch()

    out = images_mod.folder_unfinished(folder_path=str(folder))
    assert out["total_on_disk"] == 1
    assert out["unfinished"] == 1


def test_unfinished_returns_404_for_missing_folder(patched_db, tmp_path):
    """Folder doesn't exist on disk → 404."""
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        images_mod.folder_unfinished(folder_path=str(tmp_path / "ghost"))
    assert ei.value.status_code == 404


def test_unfinished_returns_zero_for_empty_folder(patched_db, tmp_path):
    """Empty folder → no work, no banner. Short-circuits before the DB query."""
    folder = tmp_path / "empty"
    folder.mkdir()
    out = images_mod.folder_unfinished(folder_path=str(folder))
    assert out["total_on_disk"] == 0
    assert out["done_count"] == 0
    assert out["unfinished"] == 0
