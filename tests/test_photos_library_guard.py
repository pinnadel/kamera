"""
The never-touch-a-Photos-library invariant.

file_mover is the only module in the repo that calls shutil.move or send2trash
on a photo, so these tests are the proof that no caller — present or future —
can corrupt an Apple Photos library by moving a file out of its package.

See backend/file_mover.py::_assert_outside_photos_library for why that is fatal.
"""

import pytest

from backend.file_mover import (
    _assert_outside_photos_library,
    move_photo,
    resolve_dest_folder,
    trash_photo,
)


@pytest.fixture
def library(tmp_path):
    """A fake .photoslibrary package with one original inside it."""
    originals = tmp_path / "Test.photoslibrary" / "originals" / "0"
    originals.mkdir(parents=True)
    asset = originals / "A1B2C3D4-0000-0000-0000-000000000000.RAF"
    asset.write_bytes(b"not really a raf")
    return asset


@pytest.fixture
def plain_photo(tmp_path):
    photo = tmp_path / "loose" / "DSCF1234.RAF"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"not really a raf")
    return photo


def test_move_refuses_source_inside_library(library, tmp_path):
    with pytest.raises(OSError, match="Photos library"):
        move_photo(library, tmp_path / "_Keeps")
    assert library.exists(), "the asset must still be where Photos left it"


def test_move_refuses_destination_inside_library(plain_photo, tmp_path):
    dest = tmp_path / "Test.photoslibrary" / "originals" / "0"
    with pytest.raises(OSError, match="Photos library"):
        move_photo(plain_photo, dest)
    assert plain_photo.exists()


def test_trash_refuses_file_inside_library(library):
    with pytest.raises(OSError, match="Photos library"):
        trash_photo(library)
    assert library.exists()


def test_symlink_pointing_into_a_library_is_refused(library, tmp_path):
    """
    The guard resolves before matching, so laundering the path through a
    symlink outside the package does not get you in.
    """
    link_dir = tmp_path / "loose"
    link_dir.mkdir()
    link = link_dir / "innocent.RAF"
    link.symlink_to(library)

    with pytest.raises(OSError, match="Photos library"):
        move_photo(link, tmp_path / "_Keeps")
    with pytest.raises(OSError, match="Photos library"):
        trash_photo(link)
    assert library.exists()


def test_resolve_dest_folder_refuses_override_into_a_library(tmp_path):
    """The poisoned-folder_settings path: an override aimed into a library."""
    into_library = tmp_path / "Test.photoslibrary" / "originals"
    with pytest.raises(OSError, match="Photos library"):
        resolve_dest_folder(
            "keep", tmp_path / "src", {"keeps_folder": str(into_library)}
        )


@pytest.mark.parametrize("name", ["My Photos.photoslibrary", "X.PHOTOSLIBRARY"])
def test_match_is_case_insensitive_and_name_agnostic(tmp_path, name):
    asset = tmp_path / name / "originals" / "a.RAF"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"x")
    with pytest.raises(OSError, match="Photos library"):
        _assert_outside_photos_library(asset)


def test_a_file_merely_named_like_a_library_is_allowed(tmp_path):
    """Only a path COMPONENT counts — not a filename that ends in the word."""
    photo = tmp_path / "holiday.photoslibrary.RAF"
    photo.write_bytes(b"x")
    _assert_outside_photos_library(photo)  # must not raise


def test_ordinary_move_still_works(plain_photo, tmp_path):
    """Regression guard: the common path is untouched by the new check."""
    dest = tmp_path / "_Keeps"
    new_path = move_photo(plain_photo, dest)
    assert new_path == str(dest / "DSCF1234.RAF")
    assert not plain_photo.exists()


def test_resolve_dest_folder_still_works_for_normal_paths(tmp_path):
    assert resolve_dest_folder("keep", tmp_path) == tmp_path / "_Keeps"
    assert resolve_dest_folder("keep", tmp_path, reviewed=True) == (
        tmp_path / "_Keeps" / "_Keeps_reviewed"
    )
    assert resolve_dest_folder("reject", tmp_path) == tmp_path / "_Rejects"
