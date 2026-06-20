"""
Training-corpus provenance: only MANUAL decisions feed the personal model.

The personal model must learn from the user's own judgments, never from its
own auto-cull output (that would be a self-reinforcement loop). The gate lives
in `_apply_decision(..., is_auto=...)`:

  - POST /auto-cull calls it with is_auto=True  -> NO training_samples row.
  - POST /decision / /bulk-decision use the default is_auto=False -> row written.
  - A later MANUAL decision on an auto-culled photo writes the sample then.

These tests drive `_apply_decision` directly against an isolated tmp DB, with
the filesystem move and the fire-and-forget retrain stubbed out, so they assert
the exact corpus side-effect without touching real files or models.
"""
from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

import backend.routers.decisions as decisions_mod
from backend.database import get_db


def _insert_image(db_path: Path, *, image_id: int, uuid: str) -> None:
    """Minimal analyzed image row sufficient for _apply_decision."""
    with get_db(db_path) as conn:
        conn.execute(
            """
            INSERT INTO images (id, file_path, filename, uuid, source_folder,
                                overall_score, analysis_status)
            VALUES (?, ?, ?, ?, ?, ?, 'done')
            """,
            (image_id, f"/fake/source/{uuid}.RAF", f"{uuid}.RAF", uuid,
             "/fake/source", 80.0),
        )


@pytest.fixture()
def patched(tmp_db: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Point _apply_decision at the tmp DB and neutralise its side effects:
    file moves become no-ops and the retrain trigger is swallowed.
    """
    @contextlib.contextmanager
    def _get_db(*_args, **_kwargs):
        with get_db(tmp_db) as conn:
            yield conn

    monkeypatch.setattr(decisions_mod, "get_db", _get_db)
    monkeypatch.setattr(decisions_mod, "move_photo",
                        lambda current, dest: str(current))
    monkeypatch.setattr(decisions_mod, "maybe_train_async", lambda *_a, **_k: None)
    return tmp_db


def _sample_count(db_path: Path, uuid: str) -> int:
    with get_db(db_path) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM training_samples WHERE sample_uuid = ?",
            (uuid,),
        ).fetchone()[0]


def _sample_decision(db_path: Path, uuid: str) -> str | None:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT decision FROM training_samples WHERE sample_uuid = ?",
            (uuid,),
        ).fetchone()
        return row[0] if row else None


def test_auto_cull_writes_no_training_sample(patched: Path) -> None:
    """An auto-cull decision must NOT land in the training corpus."""
    _insert_image(patched, image_id=1, uuid="auto-only")

    decisions_mod._apply_decision(1, "reject", is_auto=True)

    assert _sample_count(patched, "auto-only") == 0


def test_manual_decision_writes_training_sample(patched: Path) -> None:
    """A manual decision (default is_auto=False) writes the sample as before."""
    _insert_image(patched, image_id=2, uuid="manual")

    decisions_mod._apply_decision(2, "keep")

    assert _sample_decision(patched, "manual") == "keep"


def test_manual_override_of_auto_cull_trains(patched: Path) -> None:
    """
    The core requirement: when the user manually changes an auto-cull
    decision, that manual judgment IS used for training.
    """
    _insert_image(patched, image_id=3, uuid="overridden")

    # System auto-rejects -> nothing in the corpus.
    decisions_mod._apply_decision(3, "reject", is_auto=True)
    assert _sample_count(patched, "overridden") == 0

    # User disagrees and manually keeps it -> now it trains, as a keep.
    decisions_mod._apply_decision(3, "keep")
    assert _sample_decision(patched, "overridden") == "keep"


def test_manual_then_auto_does_not_erase_sample(patched: Path) -> None:
    """
    Defensive: a manual decision followed by an auto-cull pass over the same
    photo must not clobber the manual training row. (Auto-cull only targets
    undecided photos in production, but the gate must hold regardless.)
    """
    _insert_image(patched, image_id=4, uuid="manual-first")

    decisions_mod._apply_decision(4, "keep")
    decisions_mod._apply_decision(4, "reject", is_auto=True)

    # Manual keep survives; the auto pass left the corpus untouched.
    assert _sample_decision(patched, "manual-first") == "keep"
