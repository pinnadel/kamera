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


def _g_scored(ids: list[int], score_hero: int, user_override: bool = False) -> dict:
    """Build a group dict with the full set of fields the real endpoint
    loop sets before calling _annotate_prerank_state — i.e. what a caller
    must provide for the AI-pick promotion path to have something to
    compare against / override."""
    return {
        "images": [{"id": i} for i in ids],
        "size": len(ids),
        "best_image_id": score_hero,
        "best_reason": "Sharpest frame",
        "best_is_ai_pick": False,
        "_user_override": user_override,
    }


def _seed_ranked(conn, member_ids: list[int], rankings: list[dict]) -> str:
    """Like _seed_burst_rankings, but with a real rankings payload so the
    AI-pick promotion path has something to read."""
    h = _members_hash(member_ids)
    conn.execute(
        "INSERT INTO burst_rankings (members_hash, member_ids, rankings_json, model) "
        "VALUES (?, ?, ?, ?)",
        (h, json.dumps(sorted(member_ids)), json.dumps(rankings), "stub"),
    )
    conn.commit()
    return h


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


# ---------------------------------------------------------------------------
# AI-pick promotion — a 'ready' group's best_image_id/best_reason get
# overwritten by the vision model's rank-1, unless a manual hero override
# is in play. See _promote_ai_pick.
# ---------------------------------------------------------------------------


def test_ready_group_promotes_llm_rank1_over_score_hero(tmp_db):
    """The score-based hero (100) loses to the LLM's rank-1 (101) once a
    'ranked' cache row exists — best_is_ai_pick flips to True and
    best_reason picks up the model's own reason text."""
    conn = _open_conn(tmp_db)
    _seed_ranked(conn, [100, 101, 102], [
        {"image_id": 101, "rank": 1, "reason": "Eyes open, best composition"},
        {"image_id": 100, "rank": 2, "reason": "Slightly soft focus"},
        {"image_id": 102, "rank": 3, "reason": "Subject looking away"},
    ])
    groups = [_g_scored([100, 101, 102], score_hero=100)]
    _annotate_prerank_state(groups, conn)
    g = groups[0]
    assert g["prerank_state"] == "ready"
    assert g["best_image_id"] == 101
    assert g["best_reason"] == "Eyes open, best composition"
    assert g["best_is_ai_pick"] is True
    conn.close()


def test_manual_override_beats_llm_rank1(tmp_db):
    """A user-pinned hero (Settings → hero override) always wins — the
    AI's rank-1 never displaces a decision the human already made."""
    conn = _open_conn(tmp_db)
    _seed_ranked(conn, [200, 201, 202], [
        {"image_id": 201, "rank": 1, "reason": "Best of the burst"},
    ])
    groups = [_g_scored([200, 201, 202], score_hero=200, user_override=True)]
    _annotate_prerank_state(groups, conn)
    g = groups[0]
    assert g["prerank_state"] == "ready"
    assert g["best_image_id"] == 200          # unchanged — the manual pin
    assert g["best_is_ai_pick"] is False
    conn.close()


def test_ready_with_empty_rankings_keeps_score_hero(tmp_db):
    """A 'ready' row with no rank-1 entry (e.g. the [] stub the other
    tests in this file seed) leaves the score-based pick untouched and
    best_is_ai_pick False — no crash, no false promotion."""
    conn = _open_conn(tmp_db)
    _seed_burst_rankings(conn, [300, 301, 302])  # rankings_json = "[]"
    groups = [_g_scored([300, 301, 302], score_hero=300)]
    _annotate_prerank_state(groups, conn)
    g = groups[0]
    assert g["prerank_state"] == "ready"
    assert g["best_image_id"] == 300
    assert g["best_is_ai_pick"] is False
    conn.close()


def test_pending_group_keeps_score_hero_and_no_leftover_keys(tmp_db):
    """A 'pending' group (no cache row) keeps its score-based pick, and
    the internal `_user_override` bookkeeping key never leaks into the
    group dict that would otherwise ship straight out over the API."""
    conn = _open_conn(tmp_db)
    groups = [_g_scored([400, 401, 402], score_hero=400)]
    _annotate_prerank_state(groups, conn)
    g = groups[0]
    assert g["prerank_state"] == "pending"
    assert g["best_image_id"] == 400
    assert g["best_is_ai_pick"] is False
    assert "_user_override" not in g
    conn.close()
