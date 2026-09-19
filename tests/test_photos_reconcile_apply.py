"""
Reconcile's write side: does each action do what its name says, and only that?

The classification is covered in test_photos_reconcile.py. This file drives
the endpoint with a recording sink and a real temp DB, because the failure
that matters here is not a wrong label but a wrong *write* — a dropped row, a
keyword overwritten in the direction the user did not pick, or a "skip" that
turns out to act.
"""
from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

import backend.routers.photos as photos_router
import backend.sources as sources_mod
from backend.database import get_db

KEEP = "KaMeRa:Keep"
REJECT = "KaMeRa:Reject"
SOURCE = "photos://lib/album"


class _RecordingSink:
    """Records keyword writes instead of talking to Photos."""

    source_type = "photos"
    supports_bulk_trash = False
    supports_mark_reviewed = False
    supports_auto_cull = False
    supports_companion_mirror = False
    supports_watch = False
    supports_folder_overrides = False
    supports_path_reaper = False

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def preflight(self) -> None:
        pass

    def apply(self, image_row, decision, *, is_auto):
        self.calls.append(("apply", image_row["id"], decision))
        return image_row["file_path"]

    def undo(self, image_row, previous_path):
        self.calls.append(("undo", image_row["id"]))
        return image_row["file_path"]


@pytest.fixture()
def env(tmp_db: Path, monkeypatch: pytest.MonkeyPatch):
    """Two decided photos in one Photos tab, with a controllable library view."""
    import backend.routers.decisions as decisions_mod
    import backend.routers.images as images_mod

    @contextlib.contextmanager
    def _get_db(*_a, **_k):
        with get_db(tmp_db) as conn:
            yield conn

    for mod in (photos_router, decisions_mod, images_mod):
        monkeypatch.setattr(mod, "get_db", _get_db)
    monkeypatch.setattr(decisions_mod, "maybe_train_async", lambda *_a, **_k: None)

    sink = _RecordingSink()
    monkeypatch.setitem(sources_mod._SINKS, "photos", sink)

    # Neither gate is what these tests are about; both are covered elsewhere.
    monkeypatch.setattr(photos_router, "_require_photos", lambda: None)
    monkeypatch.setattr(photos_router, "_require_open_library", lambda _p: None)

    with get_db(tmp_db) as conn:
        conn.execute(
            "INSERT INTO photos_albums (source_folder, library_path, album_uuid, album_name) "
            "VALUES (?, '/lib/Test.photoslibrary', 'ALB', 'KaMeRa')",
            (SOURCE,),
        )
        for image_id, uuid in ((1, "u1"), (2, "u2")):
            conn.execute(
                """
                INSERT INTO images (id, file_path, filename, uuid, source_folder,
                                    source_type, photos_uuid, overall_score, analysis_status)
                VALUES (?, ?, ?, ?, ?, 'photos', ?, 80.0, 'done')
                """,
                (image_id, f"/lib/originals/{uuid}.heic", f"IMG_{image_id}.HEIC",
                 f"kamera-{uuid}", SOURCE, uuid),
            )

    def set_library(
        in_album: dict[str, list[str]],
        library_uuids: set[str] | None = None,
        members: dict[str, dict] | None = None,
    ):
        uuids = library_uuids if library_uuids is not None else set(in_album)
        # Default every member to an ordinary, locally-available photo: these
        # tests are about keyword drift, and only the not_analyzed ones care
        # what kind of asset it is.
        meta = members if members is not None else {
            uuid: {"filename": f"{uuid}.HEIC", "is_movie": False,
                   "has_original": True, "supported": True}
            for uuid in in_album
        }
        monkeypatch.setattr(
            photos_router.photos_library, "album_keyword_state",
            lambda _lib, _alb: (in_album, uuids, meta),
        )

    def decide(image_id: int, decision: str):
        with get_db(tmp_db) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO decisions (image_id, decision, reason) "
                "VALUES (?, ?, 'manual')",
                (image_id, decision),
            )

    return tmp_db, sink, set_library, decide


def _reconcile(actions: dict[str, str]):
    return photos_router.photos_album_reconcile(
        photos_router.ReconcileRequest(source_folder=SOURCE, actions=actions)
    )


def _rows(db: Path) -> set[int]:
    with get_db(db) as conn:
        return {r["id"] for r in conn.execute("SELECT id FROM images")}


def _decision(db: Path, image_id: int):
    with get_db(db) as conn:
        row = conn.execute(
            "SELECT decision FROM decisions WHERE image_id = ?", (image_id,)
        ).fetchone()
    return row["decision"] if row else None


# ── the default: fix only what is unambiguous ───────────────────────────────

def test_default_reapplies_a_missing_keyword(env) -> None:
    db, sink, set_library, decide = env
    decide(1, "keep")
    decide(2, "reject")
    set_library({"u1": [], "u2": [REJECT]})

    result = _reconcile({})

    assert result["applied"]["keyword_missing"] == 1
    assert ("apply", 1, "keep") in sink.calls
    assert not any(c[1] == 2 for c in sink.calls), "the in-sync photo must be left alone"


def test_default_leaves_a_conflict_alone(env) -> None:
    db, sink, set_library, decide = env
    decide(1, "keep")
    set_library({"u1": [REJECT], "u2": []})

    result = _reconcile({})

    assert result["applied"]["keyword_foreign"] == 0
    assert sink.calls == [], "a disagreement must never be resolved by default"
    assert _decision(db, 1) == "keep"


def test_default_never_drops_a_row(env) -> None:
    db, _sink, set_library, decide = env
    decide(1, "keep")
    set_library({"u2": []}, library_uuids=set())   # u1 deleted from the library

    result = _reconcile({})

    assert result["dropped_rows"] == 0
    assert _rows(db) == {1, 2}


# ── explicit directions ─────────────────────────────────────────────────────

def test_conflict_resolved_toward_kamera_rewrites_the_keyword(env) -> None:
    db, sink, set_library, decide = env
    decide(1, "keep")
    set_library({"u1": [REJECT], "u2": []})

    _reconcile({"keyword_foreign": "kamera"})

    assert ("apply", 1, "keep") in sink.calls
    assert _decision(db, 1) == "keep", "KaMeRa's decision must not change"


def test_conflict_resolved_toward_photos_rewrites_the_decision(env) -> None:
    db, _sink, set_library, decide = env
    decide(1, "keep")
    set_library({"u1": [REJECT], "u2": []})

    _reconcile({"keyword_foreign": "photos"})

    assert _decision(db, 1) == "reject", "the keyword the user set in Photos wins"


def test_a_decision_made_in_photos_can_be_imported(env) -> None:
    db, _sink, set_library, _decide = env
    set_library({"u1": [KEEP], "u2": []})

    result = _reconcile({"decision_missing": "import"})

    assert result["applied"]["decision_missing"] == 1
    assert _decision(db, 1) == "keep"


def test_clearing_a_stray_marker_calls_undo_not_apply(env) -> None:
    db, sink, set_library, _decide = env
    set_library({"u1": [KEEP], "u2": []})

    _reconcile({"decision_missing": "clear"})

    assert ("undo", 1) in sink.calls
    assert _decision(db, 1) is None


def test_clearing_a_missing_keyword_drops_our_decision_instead(env) -> None:
    """Same action name, opposite side — the class decides whose record goes."""
    db, sink, set_library, decide = env
    decide(1, "keep")
    set_library({"u1": [], "u2": []})

    _reconcile({"keyword_missing": "clear"})

    assert _decision(db, 1) is None
    assert sink.calls == [], "nothing should be written to Photos"


def test_dropping_removes_only_the_gone_row(env) -> None:
    db, _sink, set_library, decide = env
    decide(1, "keep")
    decide(2, "keep")
    set_library({"u2": [KEEP]}, library_uuids={"u2"})

    result = _reconcile({"asset_gone": "drop"})

    assert result["dropped_rows"] == 1
    assert _rows(db) == {2}


def test_leaving_the_album_is_a_separate_decision_from_being_deleted(env) -> None:
    """Dropping asset_gone must not also drop a photo that merely left the album."""
    db, _sink, set_library, decide = env
    decide(1, "keep")
    decide(2, "keep")
    set_library({}, library_uuids={"u1", "u2"})   # both left the album, both alive

    result = _reconcile({"asset_gone": "drop"})

    assert result["dropped_rows"] == 0
    assert _rows(db) == {1, 2}


# ── refusals ────────────────────────────────────────────────────────────────

def test_an_ambiguous_asset_is_refused_not_guessed(env) -> None:
    db, _sink, set_library, decide = env
    decide(1, "keep")
    set_library({"u1": [KEEP, REJECT], "u2": []})

    result = _reconcile({"keyword_foreign": "photos"})

    assert result["applied"]["keyword_foreign"] == 0
    assert len(result["errors"]) == 1
    assert "more than one" in result["errors"][0]["error"]
    assert _decision(db, 1) == "keep", "the decision must survive a refusal"


def test_one_failure_does_not_abort_the_rest(env, monkeypatch) -> None:
    db, sink, set_library, decide = env
    decide(1, "keep")
    decide(2, "keep")
    set_library({"u1": [], "u2": []})

    def _explode(image_row, decision, *, is_auto):
        if image_row["id"] == 1:
            raise RuntimeError("Photos said no")
        sink.calls.append(("apply", image_row["id"], decision))
        return image_row["file_path"]

    monkeypatch.setattr(sink, "apply", _explode)

    result = _reconcile({})

    assert result["applied"]["keyword_missing"] == 1
    assert len(result["errors"]) == 1
    assert ("apply", 2, "keep") in sink.calls


def test_an_invalid_action_is_rejected_before_anything_is_written(env) -> None:
    from fastapi import HTTPException

    db, sink, set_library, decide = env
    decide(1, "keep")
    set_library({"u1": [], "u2": []})

    with pytest.raises(HTTPException) as exc:
        _reconcile({"keyword_missing": "drop"})

    assert exc.value.status_code == 422
    assert sink.calls == []
    assert _rows(db) == {1, 2}


# ── the diff endpoint ───────────────────────────────────────────────────────

def test_diff_is_read_only(env) -> None:
    db, sink, set_library, decide = env
    decide(1, "keep")
    set_library({"u1": [REJECT], "u2": []})

    payload = photos_router.photos_album_diff(SOURCE)

    assert payload["drift"] == 1
    assert payload["album_name"] == "KaMeRa"
    assert sink.calls == []
    assert _decision(db, 1) == "keep"


def test_diff_carries_the_action_vocabulary(env) -> None:
    """The UI builds its per-class controls from this, so it must ship."""
    db, _sink, set_library, _decide = env
    set_library({"u1": [], "u2": []})

    payload = photos_router.photos_album_diff(SOURCE)

    assert payload["default_actions"]["keyword_missing"] == "apply"
    assert "kamera" in payload["actions"]["keyword_foreign"]


# ── a library that is not there any more ────────────────────────────────────

def test_a_deleted_library_is_a_404_with_an_instruction(env, monkeypatch) -> None:
    """Absence and denial must not share a message.

    macOS makes a permission denial look exactly like a missing file, and this
    project has already lost a debugging session to that conflation
    (Learning-Journal 2026-09-18). Here the cause really is absence, so the
    user must be told to pick another library — not to grant a permission.
    """
    from fastapi import HTTPException
    from backend.photos_library import PhotosLibraryMissing

    _db, _sink, _set_library, _decide = env

    def _gone(_lib, _alb):
        raise PhotosLibraryMissing("/lib/Test.photoslibrary")

    monkeypatch.setattr(photos_router.photos_library, "album_keyword_state", _gone)

    with pytest.raises(HTTPException) as exc:
        photos_router.photos_album_diff(SOURCE)

    assert exc.value.status_code == 404
    assert "no longer at" in exc.value.detail
    assert "Privacy" not in exc.value.detail, "must not send the user to a permission pane"


def test_an_access_denial_still_says_permission(env, monkeypatch) -> None:
    from fastapi import HTTPException
    from backend.photos_library import PhotosAccessError

    _db, _sink, _set_library, _decide = env

    def _denied(_lib, _alb):
        raise PhotosAccessError("EPERM")

    monkeypatch.setattr(photos_router.photos_library, "album_keyword_state", _denied)

    with pytest.raises(HTTPException) as exc:
        photos_router.photos_album_diff(SOURCE)

    assert exc.value.status_code == 403


def test_a_deleted_album_is_named_in_the_error(env, monkeypatch) -> None:
    from fastapi import HTTPException

    _db, _sink, _set_library, _decide = env

    def _no_album(_lib, _alb):
        raise KeyError("Album ALB not found")

    monkeypatch.setattr(photos_router.photos_library, "album_keyword_state", _no_album)

    with pytest.raises(HTTPException) as exc:
        photos_router.photos_album_diff(SOURCE)

    assert exc.value.status_code == 404
    assert "KaMeRa" in exc.value.detail


# ── not_analyzed ────────────────────────────────────────────────────────────
#
# The one class with nothing to write. Reconcile reports the count; the caller
# runs the ordinary analyze flow for the album afterwards, which re-enumerates
# it and skips what is already done.


def test_new_album_photos_are_reported_but_nothing_is_written(env) -> None:
    db, sink, set_library, decide = env
    decide(1, "keep")
    set_library(
        {"u1": [KEEP], "u2": [], "new-1": [], "new-2": []},
        members={
            "u1":    {"filename": "IMG_1.HEIC", "is_movie": False,
                      "has_original": True, "supported": True},
            "u2":    {"filename": "IMG_2.HEIC", "is_movie": False,
                      "has_original": True, "supported": True},
            "new-1": {"filename": "IMG_3951.HEIC", "is_movie": False,
                      "has_original": True, "supported": True},
            "new-2": {"filename": "IMG_3949.HEIC", "is_movie": False,
                      "has_original": False, "supported": True},
        },
    )

    result = _reconcile({"not_analyzed": "analyze"})

    assert result["applied"]["not_analyzed"] == 2
    assert result["analysis_requested"] == 2
    assert result["dropped_rows"] == 0
    assert result["errors"] == []
    # No row exists for these, so nothing may be written on their behalf.
    assert not any(c[1] in ("new-1", "new-2") for c in sink.calls)


def test_skipping_new_photos_requests_no_analysis(env) -> None:
    db, sink, set_library, decide = env
    decide(1, "keep")
    set_library(
        {"u1": [KEEP], "u2": [], "new-1": []},
        members={
            u: {"filename": f"{u}.HEIC", "is_movie": False,
                "has_original": True, "supported": True}
            for u in ("u1", "u2", "new-1")
        },
    )

    result = _reconcile({})   # default is skip

    assert result["analysis_requested"] == 0
    assert result["applied"]["not_analyzed"] == 0


def test_a_rowless_new_photo_never_reaches_the_row_lookup(env) -> None:
    """not_analyzed entries carry image_id=None. Falling through to the
    per-row loop would raise "row no longer exists" for every one of them and
    report the album's new photos as errors."""
    db, sink, set_library, decide = env
    decide(1, "keep")
    set_library(
        {"u1": [KEEP], "u2": [], "new-1": []},
        members={
            u: {"filename": f"{u}.HEIC", "is_movie": False,
                "has_original": True, "supported": True}
            for u in ("u1", "u2", "new-1")
        },
    )

    result = _reconcile({"not_analyzed": "analyze"})
    assert result["errors"] == []
