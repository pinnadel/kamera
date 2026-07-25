"""
`_Keeps_reviewed` review gate (docs/PLAN-keeps-reviewed.md).

Piece 1 — manual keep (is_auto=False) routes straight to
_Keeps/_Keeps_reviewed; auto-cull keep (is_auto=True) still lands in plain
_Keeps, unchanged.

Piece 2 — POST /mark-reviewed (via _move_to_reviewed) releases an
auto-culled keeper from _Keeps into _Keeps/_Keeps_reviewed.

These tests drive `_apply_decision` / `_move_to_reviewed` directly against an
isolated tmp DB, with real filesystem moves (unlike
test_training_sample_provenance.py, which stubs move_photo entirely) so the
actual destination path is verified end-to-end.
"""
from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

import backend.routers.decisions as decisions_mod
from backend.database import get_db


def _insert_image(db_path: Path, *, image_id: int, uuid: str, file_path: str,
                   source_folder: str) -> None:
    with get_db(db_path) as conn:
        conn.execute(
            """
            INSERT INTO images (id, file_path, filename, uuid, source_folder,
                                overall_score, analysis_status)
            VALUES (?, ?, ?, ?, ?, ?, 'done')
            """,
            (image_id, file_path, Path(file_path).name, uuid, source_folder, 80.0),
        )


@pytest.fixture()
def patched(tmp_db: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the decisions module at the tmp DB; leave move_photo real so
    filesystem destinations are actually exercised. Retrain is stubbed since
    it's fire-and-forget and irrelevant here."""
    @contextlib.contextmanager
    def _get_db(*_args, **_kwargs):
        with get_db(tmp_db) as conn:
            yield conn

    monkeypatch.setattr(decisions_mod, "get_db", _get_db)
    monkeypatch.setattr(decisions_mod, "maybe_train_async", lambda *_a, **_k: None)
    return tmp_db


def _make_photo(tmp_path: Path, source: str, name: str = "DSCF0001.RAF") -> Path:
    src_dir = tmp_path / source
    src_dir.mkdir(parents=True, exist_ok=True)
    photo = src_dir / name
    photo.write_bytes(b"fake raw")
    return photo


# ---------------------------------------------------------------------------
# Piece 1 — manual vs auto keep routing
# ---------------------------------------------------------------------------

def test_manual_keep_lands_in_keeps_reviewed(patched: Path, tmp_path: Path) -> None:
    source = str(tmp_path / "source")
    photo = _make_photo(tmp_path, "source")
    _insert_image(patched, image_id=1, uuid="manual-keep",
                  file_path=str(photo), source_folder=source)

    result = decisions_mod._apply_decision(1, "keep", is_auto=False)

    expected = Path(source) / "_Keeps" / "_Keeps_reviewed" / photo.name
    assert Path(result["new_path"]) == expected
    assert expected.exists()


def test_auto_cull_keep_lands_in_plain_keeps(patched: Path, tmp_path: Path) -> None:
    """Regression guard — this must NOT change."""
    source = str(tmp_path / "source")
    photo = _make_photo(tmp_path, "source")
    _insert_image(patched, image_id=2, uuid="auto-keep",
                  file_path=str(photo), source_folder=source)

    result = decisions_mod._apply_decision(2, "keep", is_auto=True)

    expected = Path(source) / "_Keeps" / photo.name
    assert Path(result["new_path"]) == expected
    assert expected.exists()
    assert not (Path(source) / "_Keeps" / "_Keeps_reviewed" / photo.name).exists()


def test_manual_keep_with_folder_override_nests_correctly(
    patched: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.database import get_folder_overrides, set_folder_override

    source = str(tmp_path / "source")
    photo = _make_photo(tmp_path, "source")
    custom_keeps = tmp_path / "NAS" / "MyKeeps"
    set_folder_override(source, "keep", str(custom_keeps), db_path=patched)
    _insert_image(patched, image_id=3, uuid="override-keep",
                  file_path=str(photo), source_folder=source)

    # get_folder_overrides defaults to the real data/pca.db — redirect it to
    # our tmp DB the same way the `patched` fixture redirects get_db.
    monkeypatch.setattr(
        decisions_mod, "get_folder_overrides",
        lambda sf: get_folder_overrides(sf, db_path=patched),
    )

    result = decisions_mod._apply_decision(3, "keep", is_auto=False)

    expected = custom_keeps / "_Keeps_reviewed" / photo.name
    assert Path(result["new_path"]) == expected
    assert expected.exists()


def test_manual_maybe_and_reject_unaffected(patched: Path, tmp_path: Path) -> None:
    """reviewed routing only applies to 'keep' — maybe/reject unchanged."""
    source = str(tmp_path / "source")
    photo_m = _make_photo(tmp_path, "source", "M.RAF")
    photo_r = _make_photo(tmp_path, "source", "R.RAF")
    _insert_image(patched, image_id=4, uuid="maybe-one",
                  file_path=str(photo_m), source_folder=source)
    _insert_image(patched, image_id=5, uuid="reject-one",
                  file_path=str(photo_r), source_folder=source)

    r_maybe = decisions_mod._apply_decision(4, "maybe", is_auto=False)
    r_reject = decisions_mod._apply_decision(5, "reject", is_auto=False)

    assert Path(r_maybe["new_path"]) == Path(source) / "_Maybes" / "M.RAF"
    assert Path(r_reject["new_path"]) == Path(source) / "_Trash" / "R.RAF"


# ---------------------------------------------------------------------------
# Piece 2 — mark-reviewed
# ---------------------------------------------------------------------------

def test_mark_reviewed_moves_auto_kept_photo(patched: Path, tmp_path: Path) -> None:
    source = str(tmp_path / "source")
    photo = _make_photo(tmp_path, "source")
    _insert_image(patched, image_id=6, uuid="to-review",
                  file_path=str(photo), source_folder=source)
    decisions_mod._apply_decision(6, "keep", is_auto=True)  # lands in plain _Keeps

    result = decisions_mod._move_to_reviewed(6)

    expected = Path(source) / "_Keeps" / "_Keeps_reviewed" / photo.name
    assert Path(result["new_path"]) == expected
    assert expected.exists()


def test_mark_reviewed_rejects_non_keep_decision(patched: Path, tmp_path: Path) -> None:
    source = str(tmp_path / "source")
    photo = _make_photo(tmp_path, "source")
    _insert_image(patched, image_id=7, uuid="not-kept",
                  file_path=str(photo), source_folder=source)
    decisions_mod._apply_decision(7, "reject", is_auto=True)

    with pytest.raises(Exception) as exc_info:
        decisions_mod._move_to_reviewed(7)
    assert exc_info.value.status_code == 400


def test_mark_reviewed_rejects_already_reviewed(patched: Path, tmp_path: Path) -> None:
    source = str(tmp_path / "source")
    photo = _make_photo(tmp_path, "source")
    _insert_image(patched, image_id=8, uuid="already-reviewed",
                  file_path=str(photo), source_folder=source)
    decisions_mod._apply_decision(8, "keep", is_auto=False)  # already _Keeps_reviewed

    with pytest.raises(Exception) as exc_info:
        decisions_mod._move_to_reviewed(8)
    assert exc_info.value.status_code == 400


def test_mark_reviewed_unknown_image_404(patched: Path) -> None:
    with pytest.raises(Exception) as exc_info:
        decisions_mod._move_to_reviewed(999)
    assert exc_info.value.status_code == 404


def test_undo_from_keeps_reviewed_goes_home(patched: Path, tmp_path: Path) -> None:
    """
    _apply_undo's "no previous_path supplied" reconstruction walks UP one
    level (parent.parent) to escape the K/M/R subfolder. A reviewed keep is
    nested two levels deep (_Keeps/_Keeps_reviewed/photo), not one — undo
    must strip both, landing the photo back in its true source folder
    rather than in plain _Keeps.
    """
    source = str(tmp_path / "source")
    photo = _make_photo(tmp_path, "source")
    _insert_image(patched, image_id=11, uuid="undo-reviewed",
                  file_path=str(photo), source_folder=source)

    decisions_mod._apply_decision(11, "keep", is_auto=False)
    reviewed_path = Path(source) / "_Keeps" / "_Keeps_reviewed" / photo.name
    assert reviewed_path.exists()

    result = decisions_mod._apply_undo(11, None)

    assert Path(result["new_path"]) == photo
    assert photo.exists()
    assert not reviewed_path.exists()


def test_bulk_mark_reviewed_skips_already_reviewed(patched: Path, tmp_path: Path) -> None:
    source = str(tmp_path / "source")
    photo_a = _make_photo(tmp_path, "source", "A.RAF")
    photo_b = _make_photo(tmp_path, "source", "B.RAF")
    _insert_image(patched, image_id=9, uuid="a",
                  file_path=str(photo_a), source_folder=source)
    _insert_image(patched, image_id=10, uuid="b",
                  file_path=str(photo_b), source_folder=source)
    decisions_mod._apply_decision(9, "keep", is_auto=True)   # plain _Keeps
    decisions_mod._apply_decision(10, "keep", is_auto=False)  # already reviewed

    req = decisions_mod.BulkMarkReviewedRequest(image_ids=[9, 10])
    result = decisions_mod.bulk_mark_reviewed(req)

    assert result["moved"] == 1
    assert result["skipped"] == 1
    assert result["errors"] == []
