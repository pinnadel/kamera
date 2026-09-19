"""
Resuming an interrupted Photos-album analysis.

Both halves of the Resume banner used to be folder-only: /folders/unfinished
400'd for an album because it walks a directory, and the banner's button
re-POSTed /analyze-folder, which rejects a photos:// key outright.

Two things make the album path work without a directory:
  - the denominator is the asset_count the last analyze run recorded, so
    drawing the banner costs one indexed SQL query rather than an osxphotos
    read (or, for a Smart Album, AppleScript against a running Photos.app);
  - the album is addressable by source_folder alone, because album_uuid was
    never stored on a tab and so is missing from every tab restored out of
    localStorage — which is exactly the tab a resume happens on.
"""
import contextlib
from pathlib import Path

import pytest
from fastapi import HTTPException

import backend.routers.images as images_mod
import backend.routers.photos as photos_router
from backend.database import get_db

SOURCE = "photos://abc123/ALBUM-UUID"
LIBRARY = "/Users/someone/Pictures/Photos Library.photoslibrary"


@pytest.fixture()
def env(tmp_db: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    @contextlib.contextmanager
    def _get_db(*_a, **_k):
        with get_db(tmp_db) as conn:
            yield conn

    for mod in (photos_router, images_mod):
        monkeypatch.setattr(mod, "get_db", _get_db)
    return tmp_db


def _seed_album(db: Path, *, asset_count: int | None) -> None:
    with get_db(db) as conn:
        conn.execute(
            "INSERT INTO photos_albums "
            "(source_folder, library_path, album_uuid, album_name, asset_count) "
            "VALUES (?, ?, 'ALBUM-UUID', '2026', ?)",
            (SOURCE, LIBRARY, asset_count),
        )


def _seed_analyzed(db: Path, n: int, *, status: str = "done") -> None:
    """file_path is UNIQUE, so each call needs its own namespace."""
    with get_db(db) as conn:
        conn.executemany(
            "INSERT INTO images (file_path, filename, source_type, source_folder, analysis_status) "
            "VALUES (?, ?, 'photos', ?, ?)",
            [(f"/orig/{status}-{i}.heic", f"{i}.heic", SOURCE, status) for i in range(n)],
        )


# ---------------------------------------------------------------------------
# The count behind the banner
# ---------------------------------------------------------------------------

def test_unfinished_counts_an_album_against_its_recorded_asset_count(env: Path) -> None:
    _seed_album(env, asset_count=1868)
    _seed_analyzed(env, 1752)

    result = images_mod.folder_unfinished(SOURCE)
    assert result["total_on_disk"] == 1868
    assert result["done_count"] == 1752
    assert result["unfinished"] == 116


def test_a_finished_album_shows_no_banner(env: Path) -> None:
    _seed_album(env, asset_count=10)
    _seed_analyzed(env, 10)
    assert images_mod.folder_unfinished(SOURCE)["unfinished"] == 0


def test_only_done_rows_count_as_finished(env: Path) -> None:
    """A row that exists but never finished is precisely what resume is for."""
    _seed_album(env, asset_count=10)
    _seed_analyzed(env, 6)
    _seed_analyzed(env, 2, status="error")
    assert images_mod.folder_unfinished(SOURCE)["unfinished"] == 4


def test_an_album_that_grew_since_the_last_run_never_goes_negative(env: Path) -> None:
    """asset_count is a snapshot of the interrupted run, not live membership —
    photos added to the album afterwards would otherwise drive it below zero."""
    _seed_album(env, asset_count=5)
    _seed_analyzed(env, 9)
    assert images_mod.folder_unfinished(SOURCE)["unfinished"] == 0


def test_an_album_analyzed_before_asset_count_existed_reports_nothing(env: Path) -> None:
    """Pre-v53 rows have a NULL asset_count. No denominator means no claim —
    the banner stays hidden rather than guessing."""
    _seed_album(env, asset_count=None)
    _seed_analyzed(env, 40)
    assert images_mod.folder_unfinished(SOURCE)["unfinished"] == 0


def test_an_unknown_album_reports_nothing_rather_than_raising(env: Path) -> None:
    assert images_mod.folder_unfinished(SOURCE)["unfinished"] == 0


def test_done_count_is_scoped_to_this_album(env: Path) -> None:
    """Two Photos tabs share the images table; a global count would report the
    other album's progress against this one's total."""
    _seed_album(env, asset_count=10)
    _seed_analyzed(env, 3)
    with get_db(env) as conn:
        conn.executemany(
            "INSERT INTO images (file_path, filename, source_type, source_folder, analysis_status) "
            "VALUES (?, ?, 'photos', 'photos://other/ALB2', 'done')",
            [(f"/other/{i}.heic", f"{i}.heic") for i in range(50)],
        )
    assert images_mod.folder_unfinished(SOURCE)["unfinished"] == 7


# ---------------------------------------------------------------------------
# The batch loop's folder assumption
# ---------------------------------------------------------------------------

def test_resuming_an_album_does_not_crash_the_batch_thread(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_run_batch built `queued` as `folder / name` over `skipped`. For an
    album `folder` is None — but a *fresh* album run has an empty `skipped`,
    and an empty comprehension never evaluates its body, so this only blew up
    on the re-run, which is precisely what a resume is. It raised before
    _run_batch's try block, so the `finally` that clears _progress never ran:
    the UI sat on an analysis reporting 0 of N forever, with nothing to click.
    """
    import contextlib as _contextlib

    import backend.routers.analysis as analysis_mod
    from backend.state import _progress

    @_contextlib.contextmanager
    def _get_db(*_a, **_k):
        with get_db(env) as conn:
            yield conn

    monkeypatch.setattr(analysis_mod, "get_db", _get_db)
    _progress.update({"running": True, "total": 0, "done": 0})

    # Empty to_analyze: the loop body needs the whole scorer stack and is not
    # what regressed. The crash was in the setup lines above it.
    analysis_mod._run_batch(
        None, [], ["already-done-1.heic", "already-done-2.heic"], 2, None,
        source_folder_override=SOURCE,
    )

    assert _progress["running"] is False


# ---------------------------------------------------------------------------
# Addressing the album for the re-POST
# ---------------------------------------------------------------------------

def _request(**kw) -> photos_router.AnalyzePhotosAlbumRequest:
    return photos_router.AnalyzePhotosAlbumRequest(**kw)


def test_source_folder_alone_resolves_to_library_and_uuid(env: Path) -> None:
    """The resume case: a restored tab knows its source_folder and nothing else."""
    _seed_album(env, asset_count=10)
    assert photos_router._resolve_album_target(
        _request(source_folder=SOURCE)
    ) == (LIBRARY, "ALBUM-UUID")


def test_explicit_library_and_uuid_still_win(env: Path) -> None:
    """The picker's fresh-analyze path passes both and must not hit the DB —
    a brand-new album has no photos_albums row yet."""
    assert photos_router._resolve_album_target(
        _request(library_path="/other/Lib.photoslibrary", album_uuid="OTHER")
    ) == ("/other/Lib.photoslibrary", "OTHER")


def test_resuming_an_album_kamera_never_analyzed_is_a_404(env: Path) -> None:
    with pytest.raises(HTTPException) as exc:
        photos_router._resolve_album_target(_request(source_folder=SOURCE))
    assert exc.value.status_code == 404


def test_a_request_naming_nothing_is_a_422(env: Path) -> None:
    with pytest.raises(HTTPException) as exc:
        photos_router._resolve_album_target(_request())
    assert exc.value.status_code == 422


def test_a_half_specified_request_falls_back_to_source_folder(env: Path) -> None:
    """library_path without album_uuid is not enough to address an album."""
    _seed_album(env, asset_count=10)
    assert photos_router._resolve_album_target(
        _request(library_path=LIBRARY, source_folder=SOURCE)
    ) == (LIBRARY, "ALBUM-UUID")
