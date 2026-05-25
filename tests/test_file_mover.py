"""
Priority 5 — File mover safety.

Tests cover resolve_dest_folder() path-resolution logic only — no actual
file moves are performed. move_photo() requires real files and is covered
by a single smoke test that creates a temp file to verify the move works
and sidecars follow, but the bulk of these tests exercise the pure logic.
"""
from pathlib import Path

import pytest

from backend.file_mover import resolve_dest_folder, move_photo

SOURCE_FOLDER = str(Path.home() / "Pictures" / "Session1")


# ---------------------------------------------------------------------------
# resolve_dest_folder — pure path resolution, no filesystem access
# ---------------------------------------------------------------------------

def test_resolve_dest_folder_default_keep() -> None:
    """With no overrides, keep destination ends in _Keeps."""
    result = resolve_dest_folder("keep", SOURCE_FOLDER, overrides=None)
    assert result == Path(SOURCE_FOLDER) / "_Keeps"
    assert result.name == "_Keeps"


def test_resolve_dest_folder_default_reject() -> None:
    """With no overrides, reject destination ends in _Trash."""
    result = resolve_dest_folder("reject", SOURCE_FOLDER, overrides=None)
    assert result == Path(SOURCE_FOLDER) / "_Trash"
    assert result.name == "_Trash"


def test_resolve_dest_folder_default_maybe() -> None:
    """With no overrides, maybe destination ends in _Maybes."""
    result = resolve_dest_folder("maybe", SOURCE_FOLDER, overrides=None)
    assert result == Path(SOURCE_FOLDER) / "_Maybes"
    assert result.name == "_Maybes"


def test_resolve_dest_folder_empty_overrides_uses_default() -> None:
    """An empty overrides dict behaves identically to None."""
    result_none  = resolve_dest_folder("keep", SOURCE_FOLDER, overrides=None)
    result_empty = resolve_dest_folder("keep", SOURCE_FOLDER, overrides={})
    assert result_none == result_empty


def test_resolve_dest_folder_override_keep(tmp_path: Path) -> None:
    """Override for keep returns the custom absolute path."""
    custom = str(tmp_path / "NAS" / "Keeps")
    overrides = {"keeps_folder": custom}
    result = resolve_dest_folder("keep", SOURCE_FOLDER, overrides=overrides)
    assert result == Path(custom)


def test_resolve_dest_folder_override_reject(tmp_path: Path) -> None:
    """Override for reject returns the custom absolute path."""
    custom = str(tmp_path / "NAS" / "Trash")
    overrides = {"trash_folder": custom}
    result = resolve_dest_folder("reject", SOURCE_FOLDER, overrides=overrides)
    assert result == Path(custom)


def test_resolve_dest_folder_override_maybe(tmp_path: Path) -> None:
    """Override for maybe returns the custom absolute path."""
    custom = str(tmp_path / "NAS" / "Maybes")
    overrides = {"maybes_folder": custom}
    result = resolve_dest_folder("maybe", SOURCE_FOLDER, overrides=overrides)
    assert result == Path(custom)


def test_resolve_dest_folder_partial_override_fallback(tmp_path: Path) -> None:
    """If only keep is overridden, reject still falls back to default subfolder."""
    overrides = {"keeps_folder": str(tmp_path / "NAS" / "Keeps")}
    result = resolve_dest_folder("reject", SOURCE_FOLDER, overrides=overrides)
    assert result == Path(SOURCE_FOLDER) / "_Trash"


def test_resolve_dest_folder_returns_path_type() -> None:
    """resolve_dest_folder() always returns a pathlib.Path, not a string."""
    result = resolve_dest_folder("keep", SOURCE_FOLDER)
    assert isinstance(result, Path)


def test_resolve_dest_folder_path_input() -> None:
    """source_folder can be a Path object as well as a string."""
    result_str  = resolve_dest_folder("keep", SOURCE_FOLDER)
    result_path = resolve_dest_folder("keep", Path(SOURCE_FOLDER))
    assert result_str == result_path


# ---------------------------------------------------------------------------
# move_photo — smoke test with a real temp file
# ---------------------------------------------------------------------------

def test_move_photo_basic(tmp_path: Path) -> None:
    """move_photo() moves a file to the destination and returns the new path."""
    src_dir = tmp_path / "source"
    dst_dir = tmp_path / "dest"
    src_dir.mkdir()

    photo = src_dir / "DSC_0001.jpg"
    photo.write_bytes(b"fake jpeg content")

    new_path = move_photo(str(photo), str(dst_dir))

    assert Path(new_path).exists(), "Moved file should exist at new path"
    assert not photo.exists(), "Original file should no longer exist at source"
    assert Path(new_path).parent.resolve() == dst_dir.resolve()


def test_move_photo_already_at_destination(tmp_path: Path) -> None:
    """move_photo() is a no-op when the file is already in the destination."""
    dst_dir = tmp_path / "dest"
    dst_dir.mkdir()

    photo = dst_dir / "DSC_0001.jpg"
    photo.write_bytes(b"fake jpeg content")

    returned_path = move_photo(str(photo), str(dst_dir))

    assert returned_path == str(photo), (
        "Should return the unchanged path when already at destination"
    )
    assert photo.exists()


def test_move_photo_missing_source_raises(tmp_path: Path) -> None:
    """move_photo() raises FileNotFoundError when the source does not exist."""
    with pytest.raises(FileNotFoundError):
        move_photo(str(tmp_path / "ghost.jpg"), str(tmp_path / "dest"))


def test_move_photo_collision_renames(tmp_path: Path) -> None:
    """move_photo() renames with _1 suffix on filename collision."""
    src_dir = tmp_path / "source"
    dst_dir = tmp_path / "dest"
    src_dir.mkdir()
    dst_dir.mkdir()

    # Pre-populate the destination with a file of the same name.
    (dst_dir / "DSC_0001.jpg").write_bytes(b"existing file")

    photo = src_dir / "DSC_0001.jpg"
    photo.write_bytes(b"new file")

    new_path = move_photo(str(photo), str(dst_dir))

    # The moved file should have the _1 suffix.
    assert Path(new_path).name == "DSC_0001_1.jpg", (
        f"Expected DSC_0001_1.jpg, got {Path(new_path).name}"
    )
    assert Path(new_path).exists()
    # The original destination file should be untouched.
    assert (dst_dir / "DSC_0001.jpg").read_bytes() == b"existing file"


def test_move_photo_moves_hif_companion(tmp_path: Path) -> None:
    """move_photo() moves a .HIF camera-baked HEIF companion alongside the RAF.

    Fuji X100VI / X Half write a .HIF next to every .RAF in RAW+HIF mode.
    Companion must follow the RAW or DetailView's sidecar-preview feature
    breaks after a decision.
    """
    src_dir = tmp_path / "source"
    dst_dir = tmp_path / "dest"
    src_dir.mkdir()

    photo = src_dir / "DSCF1234.RAF"
    hif = src_dir / "DSCF1234.HIF"
    photo.write_bytes(b"fake raw")
    hif.write_bytes(b"fake hif")

    new_path = move_photo(str(photo), str(dst_dir))

    moved_hif = Path(new_path).with_suffix(".HIF")
    assert moved_hif.exists(), "HIF companion should be moved alongside RAW"
    assert not hif.exists(), "Original HIF should no longer exist"


def test_move_photo_refuses_symlink(tmp_path: Path) -> None:
    """move_photo() refuses to follow a symlink — defends RAW originals
    outside the analysis tree from being silently moved."""
    real_dir = tmp_path / "real"
    link_dir = tmp_path / "watched"
    dst_dir = tmp_path / "dest"
    real_dir.mkdir()
    link_dir.mkdir()

    real_photo = real_dir / "IMG_0001.RAF"
    real_photo.write_bytes(b"the precious original")
    symlink = link_dir / "IMG_0001.RAF"
    symlink.symlink_to(real_photo)

    with pytest.raises(OSError, match="symlink"):
        move_photo(str(symlink), str(dst_dir))

    # The real file at the symlink target must be untouched.
    assert real_photo.exists()
    assert real_photo.read_bytes() == b"the precious original"


def test_move_photo_collision_with_concurrent_writer(tmp_path: Path) -> None:
    """Simulate the TOCTOU window: a parallel worker creates a file with the
    candidate name AFTER _claim_path picked it but BEFORE move. Our atomic
    O_CREAT|O_EXCL claim makes that impossible — the second mover gets _2."""
    src_dir = tmp_path / "source"
    dst_dir = tmp_path / "dest"
    src_dir.mkdir()
    dst_dir.mkdir()

    # _1 is already claimed (the "concurrent writer" placeholder).
    (dst_dir / "DSC_0001.jpg").write_bytes(b"already-here")
    (dst_dir / "DSC_0001_1.jpg").write_bytes(b"already-here-too")

    photo = src_dir / "DSC_0001.jpg"
    photo.write_bytes(b"new content")
    new_path = move_photo(str(photo), str(dst_dir))

    # The mover must have skipped both occupied names and landed at _2.
    assert Path(new_path).name == "DSC_0001_2.jpg"
    assert (dst_dir / "DSC_0001.jpg").read_bytes() == b"already-here"
    assert (dst_dir / "DSC_0001_1.jpg").read_bytes() == b"already-here-too"
