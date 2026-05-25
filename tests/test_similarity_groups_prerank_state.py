"""Tests for the per-group prerank_state annotation on /similarity-groups
and /face-groups responses.

The helper `backend.routers.model._annotate_prerank_state` is what writes
the field onto each group dict. Tests call it directly with synthetic
group lists + a real in-memory DB so we can seed burst_rankings rows and
assert state assignment without standing up the full HTTP router.
"""
from __future__ import annotations

import json
import sqlite3

from backend.database import create_tables
from backend.routers.model import _annotate_prerank_state
from phase2_quality.burst_ranker import _members_hash


def _open_conn(tmp_db):
    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    return conn


def _g(ids: list[int]) -> dict:
    """Build a group dict shaped like /similarity-groups produces, but
    only with the fields _annotate_prerank_state actually reads."""
    return {"images": [{"id": i} for i in ids], "size": len(ids)}


def _seed_burst_rankings(conn, member_ids: list[int]) -> str:
    """Insert a row whose members_hash matches `member_ids` so the
    annotator treats it as 'ready' (cache hit). Returns the hash."""
    h = _members_hash(member_ids)
    conn.execute(
        "INSERT INTO burst_rankings (members_hash, member_ids, rankings_json, model) "
        "VALUES (?, ?, ?, ?)",
        (h, json.dumps(sorted(member_ids)), "[]", "stub"),
    )
    conn.commit()
    return h


# ---------------------------------------------------------------------------
# State assignment: ready / pending / not_applicable
# ---------------------------------------------------------------------------


def test_group_with_cache_row_is_ready(tmp_db):
    """A group whose members hash matches a row in burst_rankings is
    'ready' — opening the loupe will be instant."""
    conn = _open_conn(tmp_db)
    _seed_burst_rankings(conn, [1, 2, 3])
    groups = [_g([1, 2, 3])]
    _annotate_prerank_state(groups, conn)
    assert groups[0]["prerank_state"] == "ready"
    conn.close()


def test_group_without_cache_row_is_pending(tmp_db):
    """An eligible group (>= _PRERANK_MIN_GROUP_SIZE) that has no cache
    row is 'pending' — the worker will get to it."""
    conn = _open_conn(tmp_db)
    groups = [_g([10, 11, 12])]
    _annotate_prerank_state(groups, conn)
    assert groups[0]["prerank_state"] == "pending"
    conn.close()


def test_too_small_group_is_not_applicable(tmp_db):
    """Groups under _PRERANK_MIN_GROUP_SIZE (currently 3) get
    'not_applicable' — the grid renders no marker."""
    conn = _open_conn(tmp_db)
    groups = [_g([20, 21])]  # 2 members, below the floor
    _annotate_prerank_state(groups, conn)
    assert groups[0]["prerank_state"] == "not_applicable"
    conn.close()


def test_mixed_states_resolved_in_one_pass(tmp_db):
    """When the response carries many groups, each gets its own state
    independently. Ready groups don't leak into pending neighbours."""
    conn = _open_conn(tmp_db)
    _seed_burst_rankings(conn, [1, 2, 3])
    groups = [
        _g([1, 2, 3]),     # cached → ready
        _g([4, 5, 6]),     # uncached, eligible → pending
        _g([7, 8]),        # too small → not_applicable
    ]
    _annotate_prerank_state(groups, conn)
    states = [g["prerank_state"] for g in groups]
    assert states == ["ready", "pending", "not_applicable"]
    conn.close()


# ---------------------------------------------------------------------------
# in_progress state — depends on prerank.snapshot()['current_job_hash']
# ---------------------------------------------------------------------------


def test_group_matching_current_job_hash_is_in_progress(tmp_db, monkeypatch):
    """Exactly one group in the response can be 'in_progress' at a time —
    the one whose hash matches the worker's live job. Stubbed via the
    prerank snapshot."""
    conn = _open_conn(tmp_db)
    target_ids = [30, 31, 32]
    target_hash = _members_hash(target_ids)

    # Patch the snapshot to claim the worker is mid-call on target_hash.
    # Patched at the import site inside the router module.
    from backend.routers import model as _model_mod
    monkeypatch.setattr(
        _model_mod,
        "_annotate_prerank_state",
        _annotate_prerank_state,  # keep the same function; monkeypatch needed below
    )
    # Patch the prerank module's snapshot.
    import phase2_quality.prerank as _prerank
    monkeypatch.setattr(_prerank, "snapshot", lambda: {
        "running": True, "queued": 0, "completed": 0, "failed": 0,
        "skipped": 0, "total_queued": 1, "last_error": None,
        "current_job_hash": target_hash,
    })

    groups = [_g(target_ids), _g([40, 41, 42])]
    _annotate_prerank_state(groups, conn)
    assert groups[0]["prerank_state"] == "in_progress"
    # The other eligible group is pending, not in_progress — only one in_progress.
    assert groups[1]["prerank_state"] == "pending"
    conn.close()


def test_cached_takes_precedence_over_in_progress(tmp_db, monkeypatch):
    """Edge case: if a group has a cache row AND happens to match the
    worker's current_job_hash (e.g. the worker just finished but hasn't
    cleared the field yet), 'ready' wins. The cache is the source of
    truth for openability — in_progress is just informational decoration."""
    conn = _open_conn(tmp_db)
    target_ids = [50, 51, 52]
    _seed_burst_rankings(conn, target_ids)
    target_hash = _members_hash(target_ids)

    import phase2_quality.prerank as _prerank
    monkeypatch.setattr(_prerank, "snapshot", lambda: {
        "running": True, "queued": 0, "completed": 0, "failed": 0,
        "skipped": 0, "total_queued": 1, "last_error": None,
        "current_job_hash": target_hash,
    })

    groups = [_g(target_ids)]
    _annotate_prerank_state(groups, conn)
    assert groups[0]["prerank_state"] == "ready"
    conn.close()


# ---------------------------------------------------------------------------
# near_duplicates outcome (added 2026-05-17)
# ---------------------------------------------------------------------------


def _seed_near_duplicates(conn, member_ids: list[int]) -> str:
    """Insert a burst_rankings row with outcome='near_duplicates' so the
    annotator emits the dedicated state. Returns the hash."""
    h = _members_hash(member_ids)
    conn.execute(
        "INSERT INTO burst_rankings (members_hash, member_ids, rankings_json, model, outcome) "
        "VALUES (?, ?, '[]', NULL, 'near_duplicates')",
        (h, json.dumps(sorted(member_ids))),
    )
    conn.commit()
    return h


def test_group_with_near_duplicates_outcome(tmp_db):
    """A burst_rankings row with outcome='near_duplicates' surfaces as
    prerank_state='near_duplicates' — distinct from both 'ready' (true
    LLM rank) and 'pending' (no row yet). Lets the UI render a chip
    that explains why no AI rank ring is shown on this burst."""
    conn = _open_conn(tmp_db)
    _seed_near_duplicates(conn, [60, 61, 62, 63])
    groups = [_g([60, 61, 62, 63])]
    _annotate_prerank_state(groups, conn)
    assert groups[0]["prerank_state"] == "near_duplicates"
    conn.close()


def test_mixed_outcomes_resolved_independently(tmp_db):
    """Three eligible groups, three different outcomes — annotator must
    emit each correctly without cross-contamination."""
    conn = _open_conn(tmp_db)
    _seed_burst_rankings(conn, [70, 71, 72])     # ranked → ready
    _seed_near_duplicates(conn, [80, 81, 82])    # collapsed → near_duplicates
    groups = [
        _g([70, 71, 72]),
        _g([80, 81, 82]),
        _g([90, 91, 92]),  # no row at all → pending
    ]
    _annotate_prerank_state(groups, conn)
    states = [g["prerank_state"] for g in groups]
    assert states == ["ready", "near_duplicates", "pending"]
    conn.close()
