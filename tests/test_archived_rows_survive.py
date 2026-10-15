"""An archived photo keeps its row.

Provenance archives a released keeper by MOVING the file into the Luminar
library. `sync_folder` drops every row whose file_path is missing — which is
exactly what that move produces, seconds later. That single predicate is why
an archived photo used to disappear from KaMeRa entirely, taking its preview
with it: "the file is gone" and "the photo is gone" are not the same claim.

These tests pin both halves: an archived row survives the sweep, and a
genuinely deleted one still does not.
"""
from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

import pytest

import backend.routers.images as images_mod
from backend import provenance_link
from backend.database import get_db

_LEDGER_DDL = """
CREATE TABLE imports (
    id                 INTEGER PRIMARY KEY,
    exif_key           TEXT UNIQUE NOT NULL,
    temp_path          TEXT,
    archived_path      TEXT,
    status             TEXT DEFAULT 'imported',
    backed_up          INTEGER DEFAULT 0
);
"""


@pytest.fixture()
def env(tmp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    @contextlib.contextmanager
    def _get_db(*_a, **_k):
        with get_db(tmp_db) as conn:
            yield conn

    monkeypatch.setattr(images_mod, "get_db", _get_db)
    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setenv("KAMERA_PROVENANCE_DIR", str(shared))
    provenance_link.reset_cache()
    yield tmp_db, tmp_path, shared
    provenance_link.reset_cache()


def _ledger(shared: Path, rows) -> None:
    conn = sqlite3.connect(shared / "ledger.sqlite")
    conn.executescript(_LEDGER_DDL)
    for r in rows:
        conn.execute("INSERT INTO imports (exif_key, temp_path, archived_path, "
                     "status, backed_up) VALUES (?,?,?,?,?)", r)
    conn.commit()
    conn.close()


def _image(db: Path, image_id: int, path: Path, *, key: str | None,
           decision: str | None = "keep", folder: str = "/src") -> None:
    with get_db(db) as conn:
        conn.execute(
            "INSERT INTO images (id, file_path, filename, uuid, source_folder, "
            "provenance_key, overall_score, analysis_status) "
            "VALUES (?,?,?,?,?,?,80.0,'done')",
            (image_id, str(path), path.name, f"u{image_id}", folder, key),
        )
        if decision:
            conn.execute("INSERT INTO decisions (image_id, decision, reason) "
                         "VALUES (?,?,'manual')", (image_id, decision))
        conn.commit()


class _Req:
    def __init__(self, source_folder): self.source_folder = source_folder


def test_archived_row_survives_the_sweep(env):
    db, tmp, shared = env
    _ledger(shared, [("K1", "/src/A.RAF", "/lib/2026/2026 Bilder/A.RAF",
                      "archived", 0)])
    _image(db, 1, tmp / "A.RAF", key="K1")     # file deliberately absent

    out = images_mod.sync_folder(_Req("/src"))
    assert out["images_removed"] == 0
    with get_db(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 1


def test_a_genuinely_deleted_photo_is_still_reaped(env):
    """The guard must be narrow: deleting a file outside the app still has to
    clean up, or the grid fills with rows pointing at nothing."""
    db, tmp, shared = env
    _ledger(shared, [("K1", "/src/A.RAF", None, "imported", 0)])
    _image(db, 1, tmp / "A.RAF", key="K1")     # staged, not archived — gone

    out = images_mod.sync_folder(_Req("/src"))
    assert out["images_removed"] == 1


def test_photo_without_provenance_is_reaped_as_before(env):
    db, tmp, shared = env
    _ledger(shared, [])
    _image(db, 1, tmp / "A.RAF", key=None)

    assert images_mod.sync_folder(_Req("/src"))["images_removed"] == 1


def test_sweep_works_without_provenance_at_all(env):
    """No ledger on this machine: behaviour is exactly what it was before."""
    db, tmp, _shared = env
    _image(db, 1, tmp / "A.RAF", key=None)
    (tmp / "B.RAF").write_text("x")
    _image(db, 2, tmp / "B.RAF", key=None)

    out = images_mod.sync_folder(_Req("/src"))
    assert out["images_removed"] == 1          # only the missing one
    with get_db(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 1


def test_list_images_reports_lifecycle(env):
    db, tmp, shared = env
    _ledger(shared, [
        ("K1", "/src/A.RAF", "/lib/A.RAF", "archived", 1),
        ("K2", "/src/B.RAF", None, "imported", 0),
    ])
    (tmp / "A.RAF").write_text("x")
    (tmp / "B.RAF").write_text("x")
    _image(db, 1, tmp / "A.RAF", key="K1")
    _image(db, 2, tmp / "B.RAF", key="K2")

    rows = {r["filename"]: r for r in images_mod.list_images(source_folder="/src")}
    assert rows["A.RAF"]["lifecycle"] == "backed_up"
    assert rows["A.RAF"]["archived_path"] == "/lib/A.RAF"
    assert rows["B.RAF"]["lifecycle"] == "staged"
