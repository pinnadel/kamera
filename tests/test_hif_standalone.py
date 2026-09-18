"""
Tests for standalone .HIF support.

Fuji X100VI / X Half can shoot HIF-only (no RAW at all) — those files must
be analyzed exactly like a standalone JPEG. But a .HIF written next to a
.RAF in RAW+HIF mode is a camera-baked sidecar preview: move-only, never
analyzed on its own (file_mover.py already handles moving it alongside the
RAF; see test_file_mover.py::test_move_photo_moves_hif_companion).

is_standalone_hif() in backend/constants.py is the gate distinguishing the
two cases. These tests cover that gate plus the folder-walk call sites that
must apply it (analyze_folder, has_subfolders, folder_unfinished) so a
RAW+HIF pair is never double-counted or double-analyzed.
"""
from pathlib import Path

import pytest

from backend.constants import SUPPORTED_EXTENSIONS, is_standalone_hif


def test_hif_is_in_supported_extensions():
    assert ".hif" in SUPPORTED_EXTENSIONS


def test_standalone_hif_with_no_raw_sibling(tmp_path: Path):
    """HIF-only shooting mode (X100VI/X Half): no sibling RAF → standalone."""
    hif = tmp_path / "DSCF0864.HIF"
    hif.write_bytes(b"fake hif")
    assert is_standalone_hif(hif) is True


def test_hif_with_raf_sibling_is_not_standalone(tmp_path: Path):
    """RAW+HIF shooting mode: same-stem .RAF sibling → sidecar, not standalone."""
    hif = tmp_path / "DSCF1234.HIF"
    raf = tmp_path / "DSCF1234.RAF"
    hif.write_bytes(b"fake hif")
    raf.write_bytes(b"fake raw")
    assert is_standalone_hif(hif) is False


def test_hif_sibling_check_is_case_insensitive(tmp_path: Path):
    """APFS is case-insensitive; a lowercase .raf sibling must still pair."""
    hif = tmp_path / "DSCF5555.hif"
    raf = tmp_path / "DSCF5555.raf"
    hif.write_bytes(b"fake hif")
    raf.write_bytes(b"fake raw")
    assert is_standalone_hif(hif) is False


def test_hif_with_differently_stemmed_raf_is_standalone(tmp_path: Path):
    """A RAF elsewhere in the folder with a different stem must not pair."""
    hif = tmp_path / "DSCF0001.HIF"
    other_raf = tmp_path / "DSCF0002.RAF"
    hif.write_bytes(b"fake hif")
    other_raf.write_bytes(b"fake raw")
    assert is_standalone_hif(hif) is True


def test_non_hif_path_returns_false(tmp_path: Path):
    jpg = tmp_path / "DSCF0001.JPG"
    jpg.write_bytes(b"fake jpg")
    assert is_standalone_hif(jpg) is False


def test_nef_and_3fr_siblings_also_pair(tmp_path: Path):
    """Nikon/Hasselblad don't actually write HIF sidecars today, but the
    gate checks all RAW extensions generically — pin the behavior."""
    hif_nef = tmp_path / "A.HIF"
    (tmp_path / "A.NEF").write_bytes(b"fake nef")
    hif_nef.write_bytes(b"fake hif")
    assert is_standalone_hif(hif_nef) is False


# ---------------------------------------------------------------------------
# analyze_folder / has_subfolders / folder_unfinished candidate filtering
# ---------------------------------------------------------------------------


@pytest.fixture()
def patched_db(tmp_path: Path, monkeypatch):
    from backend import database as db_mod
    from backend.database import create_tables
    from backend.routers import images as images_mod

    p = tmp_path / "test.db"
    create_tables(p)
    real_get_db = db_mod.get_db

    def _patched(db_path: Path = p):
        return real_get_db(db_path)

    monkeypatch.setattr(db_mod, "get_db", _patched)
    monkeypatch.setattr(images_mod, "get_db", _patched)
    return p


def test_folder_unfinished_excludes_paired_hif_sidecar(patched_db, tmp_path: Path):
    """A RAW+HIF pair must count as ONE unfinished file (the RAF), not two."""
    from backend.routers import images as images_mod

    folder = tmp_path / "shoot"
    folder.mkdir()
    (folder / "DSCF0001.RAF").touch()
    (folder / "DSCF0001.HIF").touch()

    out = images_mod.folder_unfinished(folder_path=str(folder))
    assert out["total_on_disk"] == 1
    assert out["unfinished"] == 1


def test_folder_unfinished_counts_standalone_hif(patched_db, tmp_path: Path):
    """A HIF-only file (X100VI/X Half mode, no RAF) must count as unfinished."""
    from backend.routers import images as images_mod

    folder = tmp_path / "shoot"
    folder.mkdir()
    (folder / "DSCF0864.HIF").touch()

    out = images_mod.folder_unfinished(folder_path=str(folder))
    assert out["total_on_disk"] == 1
    assert out["unfinished"] == 1
