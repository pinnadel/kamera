"""Tests for the preview disk cache in backend.routers.analysis.

Regression pin for the 2026-07-25 "HIF thumbnails re-decode on every reload"
report: GET /previews/{id} used to cache RAW formats only, re-decoding every
non-RAW file (crucially HIF, whose pillow-heif decode is as expensive as a
RAW demosaic) on every single request. The fix caches ALL formats to
data/previews/ and populates preview_path so subsequent loads — including
after an app reload — read straight from disk.

These call the endpoint function directly with a monkeypatched get_db +
PREVIEW_CACHE_DIR, so no FastAPI app or ML models are loaded.
"""
from __future__ import annotations

import io
import sqlite3

import pytest
from PIL import Image as PilImage

from backend.routers import analysis as analysis_router


def _open_conn(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _insert_image(conn, *, image_id: int, file_path: str, fmt: str) -> None:
    conn.execute(
        "INSERT INTO images (id, filename, file_path, format, preview_path) "
        "VALUES (?, ?, ?, ?, NULL)",
        (image_id, f"img{image_id}.{fmt.lower()}", file_path, fmt),
    )
    conn.commit()


def _write_jpeg(path, size=(64, 48)) -> None:
    img = PilImage.new("RGB", size, (120, 90, 30))
    img.save(str(path), format="JPEG", quality=90)


@pytest.fixture()
def patched(tmp_db, tmp_path, monkeypatch):
    """Point analysis.get_db at tmp_db and analysis.PREVIEW_CACHE_DIR at a
    temp dir. Yields (db_path, cache_dir)."""
    class _CtxConn:
        def __init__(self, path):
            self.path = path
            self._conn = None
        def __enter__(self):
            self._conn = sqlite3.connect(self.path)
            self._conn.row_factory = sqlite3.Row
            return self._conn
        def __exit__(self, *args):
            if self._conn is not None:
                self._conn.commit()
                self._conn.close()

    cache_dir = tmp_path / "previews"
    monkeypatch.setattr(analysis_router, "get_db", lambda: _CtxConn(tmp_db))
    monkeypatch.setattr(analysis_router, "PREVIEW_CACHE_DIR", cache_dir)
    return tmp_db, cache_dir


def test_non_raw_preview_is_cached_and_persisted(patched, tmp_path):
    """A non-RAW (JPEG-here, HIF-in-prod) preview must be written to the
    cache dir AND have its preview_path persisted on first request."""
    db_path, cache_dir = patched
    src = tmp_path / "IMG0001.jpg"
    _write_jpeg(src)

    conn = _open_conn(db_path)
    _insert_image(conn, image_id=1, file_path=str(src), fmt="JPEG")
    conn.close()

    resp = analysis_router.get_preview(1)
    assert resp.media_type == "image/jpeg"
    assert len(resp.body) > 0

    # Cache file written to disk...
    cache_file = cache_dir / "1.jpg"
    assert cache_file.exists()

    # ...and preview_path persisted in the DB.
    conn = _open_conn(db_path)
    pv = conn.execute("SELECT preview_path FROM images WHERE id = 1").fetchone()["preview_path"]
    conn.close()
    assert pv == str(cache_file)


def test_second_request_serves_from_cache_not_source(patched, tmp_path):
    """After the first request populates the cache, deleting the SOURCE file
    must not break the second request — it reads from the cached JPEG. This
    is exactly the reload path: the grid re-requests every thumbnail and
    should never re-decode the original."""
    db_path, cache_dir = patched
    src = tmp_path / "IMG0002.jpg"
    _write_jpeg(src)

    conn = _open_conn(db_path)
    _insert_image(conn, image_id=2, file_path=str(src), fmt="JPEG")
    conn.close()

    first = analysis_router.get_preview(2)
    first_bytes = bytes(first.body)

    # The cache is now warm. Prove the second request doesn't touch the
    # source by making the source unreadable-but-present (get_preview's
    # existence check passes, but any decode of it would produce different
    # bytes). Simplest: overwrite the source with a different image and
    # confirm we still get the ORIGINAL cached bytes back.
    _write_jpeg(src, size=(200, 150))  # different dimensions → different bytes

    second = analysis_router.get_preview(2)
    assert bytes(second.body) == first_bytes  # served from cache, not re-decoded


def test_missing_source_file_404s(patched, tmp_path):
    """If the source file is gone AND no cache exists yet, 404."""
    from fastapi import HTTPException
    db_path, _ = patched
    conn = _open_conn(db_path)
    _insert_image(conn, image_id=3, file_path=str(tmp_path / "gone.jpg"), fmt="JPEG")
    conn.close()

    with pytest.raises(HTTPException) as exc:
        analysis_router.get_preview(3)
    assert exc.value.status_code == 404
