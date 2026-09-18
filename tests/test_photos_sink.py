"""
PhotosSink: decisions as keywords, and the user's own keywords left alone.

Driven against a fake `photoscript` module so these run anywhere, with no
Photos.app and no library. The live round-trip is covered by the photos_live
tests in docs/PLAN-photos-source.md.

The keyword-preservation test is the important one. `photo.keywords = [...]`
is a whole-list assignment: the naive implementation clears the user's own
keywords as a side effect of recording a decision, and the user would only
notice long afterwards.
"""
from __future__ import annotations

import sys
import types

import pytest
from fastapi import HTTPException

import backend.sources.photos as photos_sink_mod
from backend.sources.photos import DECISION_KEYWORDS, PhotosSink


class _FakePhoto:
    def __init__(self, uuid: str, keywords: list[str] | None = None) -> None:
        self.uuid = uuid
        self.keywords = list(keywords or [])


class _FakeLibrary:
    """Stands in for photoscript.PhotosLibrary for one fixed library."""

    photos_by_uuid: dict[str, _FakePhoto] = {}

    def photos(self, uuid: list[str]):
        return [self.photos_by_uuid[u] for u in uuid if u in self.photos_by_uuid]


@pytest.fixture()
def sink(monkeypatch: pytest.MonkeyPatch):
    fake_module = types.ModuleType("photoscript")
    fake_module.PhotosLibrary = _FakeLibrary
    monkeypatch.setitem(sys.modules, "photoscript", fake_module)

    _FakeLibrary.photos_by_uuid = {}
    monkeypatch.setattr(photos_sink_mod.photos_library, "is_available", lambda: True)
    # No photos_albums row in these tests -> _expected_library returns None ->
    # the advisory library check is a no-op, which is what we want here.
    monkeypatch.setattr(PhotosSink, "_expected_library", lambda self, row: None)
    return PhotosSink()


def _row(uuid: str = "asset-1") -> dict:
    return {
        "id": 1,
        "photos_uuid": uuid,
        "file_path": "/lib/Test.photoslibrary/originals/0/AAA.heic",
        "source_folder": "photos://lib/album",
    }


def test_keep_writes_exactly_one_marker(sink) -> None:
    photo = _FakePhoto("asset-1")
    _FakeLibrary.photos_by_uuid = {"asset-1": photo}

    sink.apply(_row(), "keep", is_auto=False)

    assert photo.keywords == [DECISION_KEYWORDS["keep"]]


def test_redeciding_replaces_rather_than_accumulates(sink) -> None:
    """K then R must leave ONE marker, or a Smart Album shows the photo twice."""
    photo = _FakePhoto("asset-1")
    _FakeLibrary.photos_by_uuid = {"asset-1": photo}

    sink.apply(_row(), "keep", is_auto=False)
    sink.apply(_row(), "reject", is_auto=False)

    assert photo.keywords == [DECISION_KEYWORDS["reject"]]


def test_user_keywords_survive_a_decision(sink) -> None:
    photo = _FakePhoto("asset-1", ["Iceland", "Family"])
    _FakeLibrary.photos_by_uuid = {"asset-1": photo}

    sink.apply(_row(), "maybe", is_auto=False)

    assert "Iceland" in photo.keywords and "Family" in photo.keywords
    assert DECISION_KEYWORDS["maybe"] in photo.keywords


def test_undo_clears_only_kameras_keyword(sink) -> None:
    photo = _FakePhoto("asset-1", ["Iceland"])
    _FakeLibrary.photos_by_uuid = {"asset-1": photo}

    sink.apply(_row(), "keep", is_auto=False)
    sink.undo(_row(), None)

    assert photo.keywords == ["Iceland"], "undo must not take the user's keywords with it"


def test_undo_on_an_untouched_photo_is_a_no_op(sink) -> None:
    photo = _FakePhoto("asset-1", ["Iceland"])
    _FakeLibrary.photos_by_uuid = {"asset-1": photo}

    sink.undo(_row(), None)

    assert photo.keywords == ["Iceland"]


def test_unknown_uuid_is_a_409_not_a_silent_miss(sink) -> None:
    """An empty lookup means the wrong library is open — never write blind."""
    _FakeLibrary.photos_by_uuid = {}

    with pytest.raises(HTTPException) as exc:
        sink.apply(_row("not-in-this-library"), "keep", is_auto=False)
    assert exc.value.status_code == 409


def test_row_without_photos_uuid_is_a_500(sink) -> None:
    with pytest.raises(HTTPException) as exc:
        sink.apply({"id": 7, "file_path": "/x", "photos_uuid": None}, "keep", is_auto=False)
    assert exc.value.status_code == 500


def test_write_that_does_not_stick_is_reported(sink, monkeypatch) -> None:
    """A silently-dropped keyword must not be recorded as a decision."""
    class _RefusingPhoto(_FakePhoto):
        @property
        def keywords(self):
            return self._kw
        @keywords.setter
        def keywords(self, value):
            self._kw = []  # Photos "accepts" and discards

    photo = _RefusingPhoto("asset-1")
    photo._kw = []
    _FakeLibrary.photos_by_uuid = {"asset-1": photo}

    with pytest.raises(HTTPException) as exc:
        sink.apply(_row(), "keep", is_auto=False)
    assert exc.value.status_code == 500


def test_file_path_is_returned_unchanged(sink) -> None:
    """The asset never moves; the caller writes this straight back to the DB."""
    photo = _FakePhoto("asset-1")
    _FakeLibrary.photos_by_uuid = {"asset-1": photo}

    row = _row()
    assert sink.apply(row, "keep", is_auto=False) == row["file_path"]
    assert sink.undo(row, None) == row["file_path"]


def test_capabilities_are_all_off(sink) -> None:
    """Every folder-shaped feature must be declined, not attempted."""
    assert not sink.supports_bulk_trash
    assert not sink.supports_mark_reviewed
    assert not sink.supports_auto_cull
    assert not sink.supports_companion_mirror
    assert not sink.supports_watch
    assert not sink.supports_folder_overrides
    assert not sink.supports_path_reaper


# ---------------------------------------------------------------------------
# Permission failures must be legible, not a bare 500
# ---------------------------------------------------------------------------

def test_access_error_becomes_an_actionable_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    A TCC denial surfaces as EPERM/EACCES from deep inside osxphotos. Left
    alone it reaches the user as "500 Internal Server Error", which says
    nothing about the one thing that fixes it.
    """
    import backend.photos_library as pl
    import backend.routers.photos as photos_router

    monkeypatch.setattr(pl, "is_available", lambda: True)
    monkeypatch.setattr(photos_router.photos_library, "is_available", lambda: True)
    monkeypatch.setattr(
        photos_router.photos_library, "current_library_path",
        lambda: "/Users/x/Pictures/Test.photoslibrary",
    )

    def _denied(*_a, **_k):
        raise pl.PhotosAccessError("[Errno 1] Operation not permitted")

    monkeypatch.setattr(photos_router.photos_library, "list_albums", _denied)

    with pytest.raises(HTTPException) as exc:
        photos_router.photos_albums()
    assert exc.value.status_code == 403
    assert "Full Disk Access" in exc.value.detail


def test_eperm_from_osxphotos_is_translated(monkeypatch: pytest.MonkeyPatch) -> None:
    """_db_for must recognise a TCC denial and not let a raw OSError escape."""
    import backend.photos_library as pl

    class _FakeOsxphotos:
        @staticmethod
        def PhotosDB(dbfile=None):
            raise PermissionError(1, "Operation not permitted")

    monkeypatch.setitem(sys.modules, "osxphotos",
                        type("m", (), {"PhotosDB": _FakeOsxphotos.PhotosDB}))
    monkeypatch.setattr(pl, "_db_cache", {})

    with pytest.raises(pl.PhotosAccessError):
        pl._db_for("/Users/x/Pictures/Test.photoslibrary")
