"""
GET /images surfaces `decisions.reason`.

The grid's "only unconfirmed" filter (frontend/src/ui/format.js::isUnconfirmed)
answers "have I personally looked at this photo?" with `reason === 'manual'`.
The data has existed on the decisions row since migration v49, but the list
endpoint used to select `d.decision` alone, so the UI could only derive the
answer for Keeps, from the _Keeps_reviewed path. This guards the one column
that closes that gap for every bucket.
"""
from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

import backend.routers.images as images_mod
from backend.database import get_db


@pytest.fixture()
def patched(tmp_db: Path, monkeypatch: pytest.MonkeyPatch):
    @contextlib.contextmanager
    def _get_db(*_args, **_kwargs):
        with get_db(tmp_db) as conn:
            yield conn

    monkeypatch.setattr(images_mod, "get_db", _get_db)
    return tmp_db


def _insert(db_path: Path, image_id: int, decision: str | None, reason: str | None) -> None:
    with get_db(db_path) as conn:
        conn.execute(
            """
            INSERT INTO images (id, file_path, filename, uuid, source_folder,
                                overall_score, analysis_status)
            VALUES (?, ?, ?, ?, '/src', 80.0, 'done')
            """,
            (image_id, f"/src/{image_id}.RAF", f"{image_id}.RAF", f"u{image_id}"),
        )
        if decision is not None:
            conn.execute(
                "INSERT INTO decisions (image_id, decision, reason) VALUES (?, ?, ?)",
                (image_id, decision, reason),
            )


def test_list_images_exposes_reason(patched: Path) -> None:
    _insert(patched, 1, "keep", "manual")
    _insert(patched, 2, "reject", "closed_eyes")
    _insert(patched, 3, None, None)

    by_id = {img["id"]: img for img in images_mod.list_images()}

    assert by_id[1]["reason"] == "manual"
    assert by_id[2]["reason"] == "closed_eyes"
    # Undecided: no decisions row at all, so the LEFT JOIN yields NULL. The
    # frontend treats that as unconfirmed — open work belongs in the filter.
    assert by_id[3]["reason"] is None


def test_pre_v49_decision_without_reason_reads_as_unconfirmed(patched: Path) -> None:
    """A decision row predating migration v49 has no reason. It must come back
    as None (→ unconfirmed) rather than being silently treated as reviewed;
    showing a photo one extra time is the safe direction, hiding an unseen one
    from a bulk trash action is not."""
    _insert(patched, 4, "keep", None)

    by_id = {img["id"]: img for img in images_mod.list_images()}

    assert by_id[4]["reason"] is None
