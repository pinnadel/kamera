"""Tests for manual group composition.

Two surfaces:
  1. `phase2_quality.similarity_scorer.group_by_similarity` — must respect
     non-NULL manual_group_id as an inviolable anchor bucket that overrides
     time-segment + cosine clustering.
  2. `POST /set-manual-group` (backend.routers.model.set_manual_group) — the
     four modes (new_group, singletons, join_group, clear) and their
     transactional behaviour against the `images` table.

These tests deliberately avoid spinning up the full FastAPI app — the
endpoint function is called directly with a synthetic `SetManualGroupRequest`,
and a monkeypatched `get_db` points it at the test database.
"""
from __future__ import annotations

import json
import sqlite3

import numpy as np
import pytest

from phase2_quality.similarity_scorer import group_by_similarity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _l2(v):
    a = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(a))
    return (a / n).tolist() if n > 0 else a.tolist()


def _insert_image(conn: sqlite3.Connection, image_id: int, *, manual_group_id=None) -> None:
    """Insert a minimal `images` row sufficient for /set-manual-group tests.

    Only a small subset of NOT NULL columns is required by the schema, but to
    stay future-proof we always populate the same fields:
      - id, filename, file_path (NOT NULL on schema_version ≥ 1)
      - analysis_status defaults to 'pending'
      - manual_group_id is the field under test
    """
    conn.execute(
        "INSERT INTO images (id, filename, file_path, format, manual_group_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (image_id, f"img{image_id}.jpg", f"/fake/img{image_id}.jpg", "JPEG", manual_group_id),
    )
    conn.commit()


def _open_conn(tmp_db) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# group_by_similarity — manual-anchor reconciliation
# ---------------------------------------------------------------------------


def test_manual_anchor_bucket_overrides_cosine():
    """Two photos at cos ≈ 0.99 with DIFFERENT manual_group_id values must
    NOT end up in the same group — the manual anchor wins."""
    v = _l2([1.0, 0.0, 0.0, 0.0])
    v2 = _l2([0.99, 0.01, 0.0, 0.0])
    items = [
        (1, v,  1000.0, "anchor-A"),
        (2, v2, 1001.0, "anchor-B"),  # different anchor → must split
    ]
    groups = group_by_similarity(items, threshold=0.90, time_gap_seconds=60.0)
    # Both anchored singletons (size 1) → drop. Auto-clustering didn't run
    # for these (they have anchors), so result is empty.
    assert groups == []


def test_manual_anchor_forces_group_against_cosine():
    """Two photos at cos ≈ 0.10 (semantically unrelated) with the SAME
    manual_group_id must end up in one group, regardless of cosine."""
    v1 = _l2([1.0, 0.0, 0.0, 0.0])
    v2 = _l2([0.0, 1.0, 0.0, 0.0])  # orthogonal to v1
    items = [
        (1, v1, 1000.0, "forced-together"),
        (2, v2, 9999.0, "forced-together"),  # also way outside the time gap
    ]
    groups = group_by_similarity(items, threshold=0.90, time_gap_seconds=60.0)
    assert groups == [[1, 2]]


def test_manual_anchor_singleton_drops_from_results():
    """A photo alone in its manual_group_id bucket does NOT appear in
    /similarity-groups output (the ≥ 2 rule still applies)."""
    v1 = _l2([1.0, 0.0])
    v2 = _l2([0.9999, 0.01])
    items = [
        (1, v1, 1000.0, "alone"),       # singleton anchored bucket → drop
        (2, v2, 1001.0, None),          # auto-cluster, but also alone (no peer)
    ]
    groups = group_by_similarity(items, threshold=0.90, time_gap_seconds=60.0)
    assert groups == []


def test_manual_anchor_coexists_with_auto_cluster():
    """An anchored bucket and an unrelated auto-cluster both appear in the
    output. Anchored photos never bridge into the auto-cluster, even if
    cosine would suggest it."""
    # IDs 1+2 anchored together (orthogonal embeddings — wouldn't auto-cluster).
    # IDs 3+4 NOT anchored, near-identical embeddings — should auto-cluster.
    anchored_v1 = _l2([1.0, 0.0, 0.0, 0.0])
    anchored_v2 = _l2([0.0, 1.0, 0.0, 0.0])
    auto_v1     = _l2([0.0, 0.0, 1.0, 0.0])
    auto_v2     = _l2([0.0, 0.0, 0.99, 0.01])
    items = [
        (1, anchored_v1, 1000.0, "manual-bucket"),
        (2, anchored_v2, 1001.0, "manual-bucket"),
        (3, auto_v1,     1002.0, None),
        (4, auto_v2,     1003.0, None),
    ]
    groups = group_by_similarity(items, threshold=0.90, time_gap_seconds=60.0)
    # Order: anchored first (insertion order in the source), then auto.
    assert sorted([sorted(g) for g in groups]) == [[1, 2], [3, 4]]


def test_manual_anchor_does_not_merge_with_auto_cluster_even_if_cosine_bridges():
    """Critical firewall: if a manual_group_id bucket has a photo whose
    embedding is near-identical to an unanchored peer, the unanchored peer
    must NOT be pulled into the anchored bucket. The anchored bucket
    stays at exactly its anchored members; the unanchored peer goes
    through auto-cluster on its own."""
    shared = _l2([1.0, 0.0, 0.0, 0.0])
    items = [
        (1, shared, 1000.0, "manual"),  # anchored
        (2, shared, 1001.0, None),       # auto, same embedding → would have unioned with 1
    ]
    groups = group_by_similarity(items, threshold=0.90, time_gap_seconds=60.0)
    # Anchored singleton drops (size 1); auto-singleton also drops. Result: empty.
    assert groups == []


def test_legacy_3tuple_shape_still_works():
    """The pre-anchor 3-tuple shape (image_id, embedding, shot_at) still
    runs the legacy code path with no manual-anchor phase."""
    v1 = _l2([1.0, 0.0])
    v2 = _l2([0.99, 0.01])
    items = [
        (1, v1, 1000.0),
        (2, v2, 1001.0),
    ]
    groups = group_by_similarity(items, threshold=0.90, time_gap_seconds=60.0)
    assert groups == [[1, 2]]


# ---------------------------------------------------------------------------
# POST /set-manual-group — endpoint
# ---------------------------------------------------------------------------


@pytest.fixture()
def patched_db(tmp_db, monkeypatch):
    """Point `backend.routers.model.get_db` at the test database for the
    duration of one test. Yields the path so the test can also open its
    own read-only connection to verify state."""
    from backend.database import get_db as _real_get_db  # noqa: F401
    from backend.routers import model as model_router

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
                self._conn.close()

    monkeypatch.setattr(model_router, "get_db", lambda: _CtxConn(tmp_db))
    return tmp_db


def _make_request(**kwargs):
    """Build a SetManualGroupRequest without importing the class symbol
    twice (it lives on the router module)."""
    from backend.routers.model import SetManualGroupRequest
    return SetManualGroupRequest(**kwargs)


def test_set_manual_group_new_group_assigns_shared_uuid(patched_db):
    """mode='new_group' → one fresh uuid assigned to every image_id."""
    from backend.routers.model import set_manual_group
    conn = _open_conn(patched_db)
    for i in (1, 2, 3):
        _insert_image(conn, i)
    conn.close()

    resp = set_manual_group(_make_request(image_ids=[1, 2, 3], mode="new_group"))
    assert resp["updated"] == 3
    anchor = resp["manual_group_id"]
    assert isinstance(anchor, str) and len(anchor) >= 16

    conn = _open_conn(patched_db)
    rows = conn.execute(
        "SELECT id, manual_group_id FROM images WHERE id IN (1, 2, 3) ORDER BY id",
    ).fetchall()
    assert all(r["manual_group_id"] == anchor for r in rows)
    conn.close()


def test_set_manual_group_singletons_distinct_uuids(patched_db):
    """mode='singletons' → each image_id gets its own fresh uuid."""
    from backend.routers.model import set_manual_group
    conn = _open_conn(patched_db)
    for i in (1, 2, 3):
        _insert_image(conn, i)
    conn.close()

    resp = set_manual_group(_make_request(image_ids=[1, 2, 3], mode="singletons"))
    assert resp["updated"] == 3
    assert resp["manual_group_id"] is None  # no single anchor returned

    conn = _open_conn(patched_db)
    rows = conn.execute(
        "SELECT id, manual_group_id FROM images WHERE id IN (1, 2, 3) ORDER BY id",
    ).fetchall()
    anchors = [r["manual_group_id"] for r in rows]
    # All non-null and all distinct.
    assert all(a is not None for a in anchors)
    assert len(set(anchors)) == 3
    conn.close()


def test_set_manual_group_join_group_target_has_anchor(patched_db):
    """mode='join_group' when target already has an anchor → incoming
    photos inherit it."""
    from backend.routers.model import set_manual_group
    conn = _open_conn(patched_db)
    _insert_image(conn, 10, manual_group_id="existing-anchor")
    _insert_image(conn, 20)
    _insert_image(conn, 21)
    conn.close()

    resp = set_manual_group(_make_request(
        image_ids=[20, 21], mode="join_group", target_image_id=10,
    ))
    assert resp["manual_group_id"] == "existing-anchor"
    assert resp["updated"] == 2

    conn = _open_conn(patched_db)
    rows = conn.execute(
        "SELECT id, manual_group_id FROM images WHERE id IN (10, 20, 21) ORDER BY id",
    ).fetchall()
    assert all(r["manual_group_id"] == "existing-anchor" for r in rows)
    conn.close()


def test_set_manual_group_join_group_target_unanchored(patched_db):
    """mode='join_group' when target has NULL anchor → backend mints a new
    uuid and assigns it to BOTH target and incoming."""
    from backend.routers.model import set_manual_group
    conn = _open_conn(patched_db)
    _insert_image(conn, 10)  # unanchored target
    _insert_image(conn, 20)
    _insert_image(conn, 21)
    conn.close()

    resp = set_manual_group(_make_request(
        image_ids=[20, 21], mode="join_group", target_image_id=10,
    ))
    anchor = resp["manual_group_id"]
    assert isinstance(anchor, str)
    # Updated count includes the target since it had to be anchored too.
    assert resp["updated"] == 3

    conn = _open_conn(patched_db)
    rows = conn.execute(
        "SELECT id, manual_group_id FROM images WHERE id IN (10, 20, 21) ORDER BY id",
    ).fetchall()
    assert all(r["manual_group_id"] == anchor for r in rows)
    conn.close()


def test_set_manual_group_clear_resets_to_null(patched_db):
    """mode='clear' nulls out the anchor → photos go back through
    auto-clustering on the next /similarity-groups."""
    from backend.routers.model import set_manual_group
    conn = _open_conn(patched_db)
    _insert_image(conn, 1, manual_group_id="some-anchor")
    _insert_image(conn, 2, manual_group_id="some-anchor")
    conn.close()

    resp = set_manual_group(_make_request(image_ids=[1, 2], mode="clear"))
    assert resp["manual_group_id"] is None
    assert resp["updated"] == 2

    conn = _open_conn(patched_db)
    rows = conn.execute(
        "SELECT manual_group_id FROM images WHERE id IN (1, 2)",
    ).fetchall()
    assert all(r["manual_group_id"] is None for r in rows)
    conn.close()


def test_set_manual_group_rejects_empty_image_ids(patched_db):
    """Empty image_ids → 400."""
    from fastapi import HTTPException
    from backend.routers.model import set_manual_group
    with pytest.raises(HTTPException) as exc:
        set_manual_group(_make_request(image_ids=[], mode="new_group"))
    assert exc.value.status_code == 400


def test_set_manual_group_rejects_bad_mode(patched_db):
    """Unknown mode → 400."""
    from fastapi import HTTPException
    from backend.routers.model import set_manual_group
    with pytest.raises(HTTPException) as exc:
        set_manual_group(_make_request(image_ids=[1], mode="bogus"))
    assert exc.value.status_code == 400


def test_set_manual_group_rejects_join_without_target(patched_db):
    """join_group without target_image_id → 400."""
    from fastapi import HTTPException
    from backend.routers.model import set_manual_group
    with pytest.raises(HTTPException) as exc:
        set_manual_group(_make_request(image_ids=[1], mode="join_group"))
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# Stale singleton anchor reclaim — regression for "groupings lost after
# reload" (2026-07-25). Removing one photo from a 2-member anchored group
# used to leave the other member pinned to the old anchor forever: a size-1
# anchor bucket never renders (the ≥2 rule) AND can never rejoin
# auto-clustering (non-NULL manual_group_id always short-circuits into the
# anchor phase) — so the survivor silently vanished from every future
# /similarity-groups response, appearing as "the group disappeared on
# reload" even though nothing about reload itself was at fault.
# ---------------------------------------------------------------------------


def test_singletons_mode_reclaims_stale_partner(patched_db):
    """Removing one photo from a 2-member anchored group ('Remove from
    group' in GroupLoupe) must clear the OTHER member's stale anchor too,
    not just mint a fresh anchor for the removed photo."""
    from backend.routers.model import set_manual_group
    conn = _open_conn(patched_db)
    _insert_image(conn, 1, manual_group_id="pair-anchor")
    _insert_image(conn, 2, manual_group_id="pair-anchor")
    conn.close()

    # Remove photo 1 only — mirrors GroupLoupe's "Remove from group" button,
    # which calls mode='singletons' with just the selected ids.
    resp = set_manual_group(_make_request(image_ids=[1], mode="singletons"))
    assert resp["updated"] == 1

    conn = _open_conn(patched_db)
    rows = {r["id"]: r["manual_group_id"] for r in conn.execute(
        "SELECT id, manual_group_id FROM images WHERE id IN (1, 2)"
    ).fetchall()}
    conn.close()
    # Photo 1 got its own fresh singleton anchor (unchanged behaviour).
    assert rows[1] is not None and rows[1] != "pair-anchor"
    # Photo 2 must be reclaimed back to NULL — it's alone in "pair-anchor"
    # now, which would otherwise never render again and could never
    # auto-cluster either.
    assert rows[2] is None


def test_singletons_mode_leaves_larger_bucket_intact(patched_db):
    """Removing one photo from a 3-member group must NOT touch the other
    two — they're still a valid ≥2 group and keep their shared anchor."""
    from backend.routers.model import set_manual_group
    conn = _open_conn(patched_db)
    _insert_image(conn, 1, manual_group_id="trio-anchor")
    _insert_image(conn, 2, manual_group_id="trio-anchor")
    _insert_image(conn, 3, manual_group_id="trio-anchor")
    conn.close()

    set_manual_group(_make_request(image_ids=[1], mode="singletons"))

    conn = _open_conn(patched_db)
    rows = {r["id"]: r["manual_group_id"] for r in conn.execute(
        "SELECT id, manual_group_id FROM images WHERE id IN (2, 3)"
    ).fetchall()}
    conn.close()
    assert rows[2] == "trio-anchor"
    assert rows[3] == "trio-anchor"


def test_join_group_reclaims_stale_donor_bucket(patched_db):
    """Pulling one photo out of a 2-member group to join a different group
    must reclaim the abandoned donor's lone remaining member."""
    from backend.routers.model import set_manual_group
    conn = _open_conn(patched_db)
    _insert_image(conn, 1, manual_group_id="donor-anchor")
    _insert_image(conn, 2, manual_group_id="donor-anchor")
    _insert_image(conn, 10, manual_group_id="target-anchor")
    conn.close()

    resp = set_manual_group(_make_request(
        image_ids=[1], mode="join_group", target_image_id=10,
    ))
    assert resp["manual_group_id"] == "target-anchor"

    conn = _open_conn(patched_db)
    rows = {r["id"]: r["manual_group_id"] for r in conn.execute(
        "SELECT id, manual_group_id FROM images WHERE id IN (1, 2, 10)"
    ).fetchall()}
    conn.close()
    assert rows[1] == "target-anchor"
    assert rows[10] == "target-anchor"
    # Photo 2 is now alone in "donor-anchor" — must be reclaimed.
    assert rows[2] is None


def test_clear_mode_reclaims_stale_partner(patched_db):
    """Clearing one photo out of a 2-member group must reclaim the other
    member left behind, not just the cleared photo."""
    from backend.routers.model import set_manual_group
    conn = _open_conn(patched_db)
    _insert_image(conn, 1, manual_group_id="pair-anchor")
    _insert_image(conn, 2, manual_group_id="pair-anchor")
    conn.close()

    set_manual_group(_make_request(image_ids=[1], mode="clear"))

    conn = _open_conn(patched_db)
    rows = {r["id"]: r["manual_group_id"] for r in conn.execute(
        "SELECT id, manual_group_id FROM images WHERE id IN (1, 2)"
    ).fetchall()}
    conn.close()
    assert rows[1] is None
    assert rows[2] is None


def test_new_group_reclaims_stale_donor_bucket(patched_db):
    """Pulling one photo into a brand-new group must reclaim its old
    2-member bucket's abandoned partner."""
    from backend.routers.model import set_manual_group
    conn = _open_conn(patched_db)
    _insert_image(conn, 1, manual_group_id="old-anchor")
    _insert_image(conn, 2, manual_group_id="old-anchor")
    _insert_image(conn, 3)
    conn.close()

    resp = set_manual_group(_make_request(image_ids=[1, 3], mode="new_group"))
    anchor = resp["manual_group_id"]

    conn = _open_conn(patched_db)
    rows = {r["id"]: r["manual_group_id"] for r in conn.execute(
        "SELECT id, manual_group_id FROM images WHERE id IN (1, 2, 3)"
    ).fetchall()}
    conn.close()
    assert rows[1] == anchor
    assert rows[3] == anchor
    assert rows[2] is None
