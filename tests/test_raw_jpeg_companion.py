"""
RAW+JPEG companion pairing — display-mode filtering + decision/undo mirroring.

When a shot was captured RAW+JPEG, both files land as independent `images`
rows (own id, own scores, own decisions — see CLAUDE.md). The
`raw_jpeg_display_mode` setting ('raw' | 'processed' | 'both') controls which
one shows up as a grid/group tile:

  - GET /images (backend/routers/images.py::_filter_raw_jpeg_pairs) hides one
    side of a detected pair when mode isn't 'both'. Orphans (no same-stem
    sibling) are always shown regardless of mode.
  - _apply_decision (backend/routers/decisions.py) mirrors a K/M/R decision
    onto the hidden companion so both files move together, but NEVER writes
    a training_samples row for the companion — RAW and processed renditions
    of the same shot score very differently on sharpness (e.g. an
    undemosaiced RAF ~46 vs. its camera JPEG ~99), so mirroring that pair
    into the corpus would teach the model contradictory sharpness labels for
    what is compositionally one photo.
  - _apply_undo mirrors symmetrically: undoing the visible photo's decision
    also undoes the companion's mirrored decision.

These tests drive _apply_decision / _apply_undo / list_images directly
against an isolated tmp DB, with the filesystem move and the fire-and-forget
retrain stubbed out (same strategy as test_training_sample_provenance.py).
"""
from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

import backend.routers.decisions as decisions_mod
import backend.sources.folder as folder_sink_mod
import backend.routers.images as images_mod
import backend.routers.settings as settings_mod
from backend.database import get_db


def _insert_image(
    db_path: Path,
    *,
    image_id: int,
    uuid: str,
    filename: str,
    fmt: str,
    source_folder: str = "/fake/source",
    overall_score: float = 80.0,
    sharpness_score: float = 80.0,
) -> None:
    """Minimal analyzed image row sufficient for _apply_decision / list_images."""
    with get_db(db_path) as conn:
        conn.execute(
            """
            INSERT INTO images (id, file_path, filename, uuid, source_folder,
                                format, overall_score, sharpness_score, analysis_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'done')
            """,
            (image_id, f"{source_folder}/{filename}", filename, uuid,
             source_folder, fmt, overall_score, sharpness_score),
        )


@pytest.fixture()
def patched(tmp_db: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Point decisions.py + images.py at the tmp DB and neutralise side effects
    (file moves, retrain trigger) that don't belong in a unit test.
    """
    @contextlib.contextmanager
    def _get_db(*_args, **_kwargs):
        with get_db(tmp_db) as conn:
            yield conn

    monkeypatch.setattr(decisions_mod, "get_db", _get_db)
    monkeypatch.setattr(images_mod, "get_db", _get_db)
    monkeypatch.setattr(folder_sink_mod, "move_photo",
                        lambda current, dest: str(current))
    monkeypatch.setattr(decisions_mod, "maybe_train_async", lambda *_a, **_k: None)

    # personal_score annotation touches a real model singleton — keep it inert.
    # `ready` is a read-only property derived from `_pipeline is not None`.
    monkeypatch.setattr(images_mod._personal_model, "_pipeline", None, raising=False)

    return tmp_db


def _set_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    """raw_jpeg_display_mode is read via get_setting(DEFAULT_DB_PATH) — same
    unbounded-DB pattern as every other single-setting reader in this codebase
    (_reject_to_system_trash_enabled, _is_sidecar_preview_preferred). Tests
    stub the resolved value directly rather than writing through a real DB."""
    monkeypatch.setattr(decisions_mod, "get_raw_jpeg_display_mode", lambda: mode)
    monkeypatch.setattr(images_mod, "get_raw_jpeg_display_mode", lambda: mode)


def _decision(db_path: Path, image_id: int) -> str | None:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT decision FROM decisions WHERE image_id = ?", (image_id,)
        ).fetchone()
        return row[0] if row else None


def _sample_count(db_path: Path, uuid: str) -> int:
    with get_db(db_path) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM training_samples WHERE sample_uuid = ?",
            (uuid,),
        ).fetchone()[0]


# ---------------------------------------------------------------------------
# Decision mirroring
# ---------------------------------------------------------------------------


def test_decision_mirrors_to_companion_when_mode_hides_it(
    patched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_mode(monkeypatch, "raw")
    _insert_image(patched, image_id=1, uuid="raw-1", filename="DSCF1041.RAF", fmt="RAF")
    _insert_image(patched, image_id=2, uuid="jpg-1", filename="DSCF1041.JPG", fmt="JPG")

    decisions_mod._apply_decision(1, "keep")

    assert _decision(patched, 1) == "keep"
    assert _decision(patched, 2) == "keep"


def test_decision_does_not_mirror_when_mode_is_both(
    patched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_mode(monkeypatch, "both")
    _insert_image(patched, image_id=1, uuid="raw-1", filename="DSCF1041.RAF", fmt="RAF")
    _insert_image(patched, image_id=2, uuid="jpg-1", filename="DSCF1041.JPG", fmt="JPG")

    decisions_mod._apply_decision(1, "keep")

    assert _decision(patched, 1) == "keep"
    assert _decision(patched, 2) is None


def test_decision_does_not_mirror_when_no_companion_exists(
    patched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Orphan RAW (no same-stem JPEG) — decision applies only to itself."""
    _set_mode(monkeypatch, "raw")
    _insert_image(patched, image_id=1, uuid="raw-only", filename="DSCF9999.RAF", fmt="RAF")

    result = decisions_mod._apply_decision(1, "keep")

    assert result["image_id"] == 1
    assert _decision(patched, 1) == "keep"


def test_mirrored_companion_never_writes_training_sample(
    patched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The core requirement from this feature: even a MANUAL decision on the
    visible photo must not train the model on the companion's mismatched
    (RAW vs. processed) feature vector.
    """
    _set_mode(monkeypatch, "processed")
    _insert_image(patched, image_id=1, uuid="raw-2", filename="DSCF2000.RAF",
                  fmt="RAF", overall_score=46.0)
    _insert_image(patched, image_id=2, uuid="jpg-2", filename="DSCF2000.JPG",
                  fmt="JPG", overall_score=99.0)

    # Visible photo (JPG, since mode='processed') is manually kept.
    decisions_mod._apply_decision(2, "keep")

    # The visible photo trains normally...
    assert _sample_count(patched, "jpg-2") == 1
    # ...but the mirrored RAW companion must NOT.
    assert _sample_count(patched, "raw-2") == 0
    assert _decision(patched, 1) == "keep"


def test_auto_cull_mirror_also_skips_training(
    patched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An auto-cull decision on the visible photo still mirrors the file
    move/decision to the companion, and (as with any auto-cull) neither side
    writes a training sample."""
    _set_mode(monkeypatch, "raw")
    _insert_image(patched, image_id=1, uuid="raw-3", filename="DSCF3000.RAF", fmt="RAF")
    _insert_image(patched, image_id=2, uuid="jpg-3", filename="DSCF3000.JPG", fmt="JPG")

    decisions_mod._apply_decision(1, "reject", is_auto=True)

    assert _decision(patched, 1) == "reject"
    assert _decision(patched, 2) == "reject"
    assert _sample_count(patched, "raw-3") == 0
    assert _sample_count(patched, "jpg-3") == 0


def test_auto_cull_scores_each_side_of_a_pair_independently(
    patched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a bug where POST /auto-cull, run while
    raw_jpeg_display_mode hid one side of a RAW+JPEG pair, mirrored
    _apply_decision's companion write onto BOTH sides after each had already
    been independently scored — so a camera JPEG correctly decided 'keep'
    got silently overwritten with its much-lower-scoring RAW twin's 'reject'
    (or vice versa, depending on loop order). Real-world case: a JPEG with
    overall_score=99.7 landed as 'maybe' because its RAF sibling scored 53.2.

    run_auto_cull must call _apply_decision with _mirror=False so each row's
    own _compute_auto_decision verdict sticks, regardless of display mode."""
    _set_mode(monkeypatch, "raw")
    _insert_image(patched, image_id=1, uuid="raw-5", filename="DSCF5000.RAF",
                  fmt="RAF", overall_score=30.0, sharpness_score=30.0)  # → reject
    _insert_image(patched, image_id=2, uuid="jpg-5", filename="DSCF5000.JPG",
                  fmt="JPG", overall_score=99.0, sharpness_score=99.0)  # → keep

    decisions_mod.run_auto_cull()

    assert _decision(patched, 1) == "reject"
    assert _decision(patched, 2) == "keep"


def test_auto_cull_still_mirrors_to_skipped_jpeg_priority_stub(
    patched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a bug where the fix above (_mirror=False in
    run_auto_cull) was too blunt: it also silenced mirroring onto
    analysis_mode=jpeg_priority's unscored 'skipped' RAW stub rows (see
    test_analysis_mode_jpeg_priority.py). Those rows never reach
    analysis_status='done', so run_auto_cull's own SELECT never picks them up
    and they never get an entry in `decisions`/`reasons` — mirroring is the
    ONLY way they're ever decided and moved. Real-world case: a whole batch of
    JPEGs got auto-culled and moved while their RAF twins sat undecided in the
    source folder, because the skipped-stub mirror path was collateral damage
    of the independent-scoring fix above."""
    _set_mode(monkeypatch, "both")
    _insert_image(patched, image_id=1, uuid="jpg-6", filename="DSCF6000.JPG",
                  fmt="JPG", overall_score=99.0, sharpness_score=99.0)  # → keep
    with get_db(patched) as conn:
        conn.execute(
            """
            INSERT INTO images (id, file_path, filename, uuid, source_folder,
                                format, overall_score, sharpness_score, analysis_status)
            VALUES (2, '/fake/source/DSCF6000.RAF', 'DSCF6000.RAF', 'raw-6',
                    '/fake/source', 'RAF', NULL, NULL, 'skipped')
            """
        )

    decisions_mod.run_auto_cull()

    assert _decision(patched, 1) == "keep"
    assert _decision(patched, 2) == "keep"


def test_mirror_only_pairs_within_same_source_folder(
    patched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same stem, different folders — NOT a pair. No mirroring across folders."""
    _set_mode(monkeypatch, "raw")
    _insert_image(patched, image_id=1, uuid="raw-4", filename="DSCF4000.RAF",
                  fmt="RAF", source_folder="/fake/folderA")
    _insert_image(patched, image_id=2, uuid="jpg-4", filename="DSCF4000.JPG",
                  fmt="JPG", source_folder="/fake/folderB")

    decisions_mod._apply_decision(1, "keep")

    assert _decision(patched, 1) == "keep"
    assert _decision(patched, 2) is None


# ---------------------------------------------------------------------------
# Undo mirroring
# ---------------------------------------------------------------------------


def test_undo_mirrors_to_companion(
    patched: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    Undo's existence checks (Path.exists()) are real filesystem checks, so
    this test uses genuine files under tmp_path and the real move_photo
    (re-patched back from the `patched` fixture's no-op stub) instead of the
    fake /fake/source paths the other tests use — otherwise the companion's
    post-mirror "keep" move would have nowhere real to move to/from and its
    undo would 409 on a file that was never actually moved.
    """
    from backend.file_mover import move_photo as real_move_photo
    monkeypatch.setattr(folder_sink_mod, "move_photo", real_move_photo)

    source_folder = tmp_path / "source"
    source_folder.mkdir()
    raw_path = source_folder / "DSCF5000.RAF"
    jpg_path = source_folder / "DSCF5000.JPG"
    raw_path.write_bytes(b"raw")
    jpg_path.write_bytes(b"jpg")

    _set_mode(monkeypatch, "raw")
    _insert_image(patched, image_id=1, uuid="raw-5", filename="DSCF5000.RAF",
                  fmt="RAF", source_folder=str(source_folder))
    _insert_image(patched, image_id=2, uuid="jpg-5", filename="DSCF5000.JPG",
                  fmt="JPG", source_folder=str(source_folder))
    # _insert_image sets file_path = f"{source_folder}/{filename}" already
    # matching raw_path / jpg_path above.

    decisions_mod._apply_decision(1, "keep")
    assert _decision(patched, 1) == "keep"
    assert _decision(patched, 2) == "keep"
    # Manual keep (is_auto=False, the default) routes straight to
    # _Keeps_reviewed — see docs/PLAN-keeps-reviewed.md.
    assert (source_folder / "_Keeps" / "_Keeps_reviewed" / "DSCF5000.RAF").exists()
    assert (source_folder / "_Keeps" / "_Keeps_reviewed" / "DSCF5000.JPG").exists()

    decisions_mod._apply_undo(1, str(raw_path))

    assert _decision(patched, 1) is None
    assert _decision(patched, 2) is None
    assert raw_path.exists()
    assert jpg_path.exists()


def test_undo_mirror_missing_companion_decision_does_not_fail(
    patched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the companion somehow has no decision to undo, the primary undo
    must still succeed (best-effort mirroring, 409 on the companion side is
    swallowed)."""
    _set_mode(monkeypatch, "raw")
    _insert_image(patched, image_id=1, uuid="raw-6", filename="DSCF6000.RAF", fmt="RAF")
    _insert_image(patched, image_id=2, uuid="jpg-6", filename="DSCF6000.JPG", fmt="JPG")

    # Decide only the RAW directly (bypassing mirroring) to leave the
    # companion undecided, simulating drift.
    with get_db(patched) as conn:
        conn.execute(
            "INSERT INTO decisions (image_id, decision) VALUES (1, 'keep')"
        )

    result = decisions_mod._apply_undo(1, "/fake/source/DSCF6000.RAF")

    assert result["image_id"] == 1
    assert _decision(patched, 1) is None


# ---------------------------------------------------------------------------
# GET /images filtering
# ---------------------------------------------------------------------------


def test_list_images_hides_processed_when_mode_is_raw(
    patched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_mode(monkeypatch, "raw")
    _insert_image(patched, image_id=1, uuid="raw-7", filename="DSCF7000.RAF", fmt="RAF")
    _insert_image(patched, image_id=2, uuid="jpg-7", filename="DSCF7000.JPG", fmt="JPG")

    out = images_mod.list_images(source_folder="/fake/source")

    ids = {row["id"] for row in out}
    assert ids == {1}


def test_list_images_hides_raw_when_mode_is_processed(
    patched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_mode(monkeypatch, "processed")
    _insert_image(patched, image_id=1, uuid="raw-8", filename="DSCF8000.RAF", fmt="RAF")
    _insert_image(patched, image_id=2, uuid="jpg-8", filename="DSCF8000.JPG", fmt="JPG")

    out = images_mod.list_images(source_folder="/fake/source")

    ids = {row["id"] for row in out}
    assert ids == {2}


def test_list_images_shows_both_when_mode_is_both(
    patched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_mode(monkeypatch, "both")
    _insert_image(patched, image_id=1, uuid="raw-9", filename="DSCF9001.RAF", fmt="RAF")
    _insert_image(patched, image_id=2, uuid="jpg-9", filename="DSCF9001.JPG", fmt="JPG")

    out = images_mod.list_images(source_folder="/fake/source")

    ids = {row["id"] for row in out}
    assert ids == {1, 2}


def test_list_images_always_shows_orphans(
    patched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lone RAW with no JPEG companion is never hidden, even in
    'processed'-only mode — the filter only hides one half of an actual pair."""
    _set_mode(monkeypatch, "processed")
    _insert_image(patched, image_id=1, uuid="raw-only-2", filename="DSCF9500.RAF", fmt="RAF")

    out = images_mod.list_images(source_folder="/fake/source")

    ids = {row["id"] for row in out}
    assert ids == {1}
