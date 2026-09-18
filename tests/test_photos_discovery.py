"""
Library discovery must not depend on Spotlight.

mdfind results are TCC-filtered per *calling* process. The detached backend
(uvicorn → python → bash → launchd, no app ancestor) gets an empty list back
even for libraries it can open and read perfectly — observed 2026-09-18, which
is what made the picker say "No Photos library found on this Mac" while an
explicit path worked fine.

So the directory scan is the basis and Spotlight is only a bonus.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import backend.photos_library as pl


@pytest.fixture()
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "Pictures").mkdir()
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "Pictures" / "Photos Library.photoslibrary").mkdir()
    (tmp_path / "Downloads" / "KaMeRa-Test.photoslibrary").mkdir()
    (tmp_path / "Downloads" / "not-a-library").mkdir()
    (tmp_path / "Loose.photoslibrary").mkdir()          # straight in home
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path


def test_scan_finds_libraries_without_spotlight(fake_home: Path) -> None:
    found = {Path(p).name for p in pl._scan_for_libraries()}
    assert found == {
        "Photos Library.photoslibrary",
        "KaMeRa-Test.photoslibrary",
        "Loose.photoslibrary",
    }


def test_scan_survives_an_unreadable_root(fake_home: Path, monkeypatch) -> None:
    """A permission-denied folder means "none here", never a crash."""
    real_iterdir = Path.iterdir

    def _iterdir(self):
        if self.name == "Downloads":
            raise PermissionError("Operation not permitted")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", _iterdir)
    found = {Path(p).name for p in pl._scan_for_libraries()}
    assert "Photos Library.photoslibrary" in found
    assert "KaMeRa-Test.photoslibrary" not in found


def test_list_libraries_works_when_spotlight_returns_nothing(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact failure the user hit: Spotlight blind, libraries still listed."""
    monkeypatch.setattr(pl, "is_available", lambda: True)
    monkeypatch.setattr(pl, "detected_library_path", lambda: None)
    # tmp_path lives under /private/var/folders, which _is_user_library rightly
    # rejects in production — neutralise just that rule for this test.
    monkeypatch.setattr(pl, "_SCRATCH_PREFIXES", ())
    import sys, types
    fake_utils = types.ModuleType("osxphotos.utils")
    fake_utils.list_photo_libraries = lambda: []
    monkeypatch.setitem(sys.modules, "osxphotos.utils", fake_utils)

    names = {lib["name"] for lib in pl.list_libraries()}
    assert "KaMeRa-Test" in names and "Photos Library" in names


def test_internal_libraries_are_excluded() -> None:
    """macOS keeps its own libraries; they are never what the user means."""
    assert not pl._is_user_library(
        "/Users/x/Library/Photos/Libraries/Syndication.photoslibrary"
    )
    assert not pl._is_user_library(
        "/Users/x/Library/Containers/com.apple.Photos/Data/tmp/file:/x.photoslibrary"
    )
    assert not pl._is_user_library("/private/var/folders/ab/T/x.photoslibrary")
    assert pl._is_user_library("/Users/x/Downloads/KaMeRa-Test.photoslibrary")
