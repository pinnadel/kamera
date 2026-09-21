"""Tests for the vision LLM picking auto-cull's group winner (2026-09-21).

Auto-cull used to choose the best frame of a burst with the score heuristic in
backend/group_scoring.py. Now the vision burst ranker gets that call, for the
groups where the winner actually decides an outcome. The three things worth
pinning, because each would fail silently:

  1. WHICH groups get ranked. A rank costs ~60s, so `consequential_bursts` has
     to skip small groups and groups whose winner changes nothing — and must NOT
     skip the 0-Keep case, which is exactly the burst the user is about to lose.
  2. That the AI winner REACHES both post-passes. The mechanism working in
     isolation proves nothing if the call sites pass no winners
     (feedback_verify_call_site_not_just_mechanism).
  3. That an unavailable ranker falls back to the score pick instead of raising,
     and that the fallback is COUNTED — a silent downgrade to the old behaviour
     is indistinguishable from the feature working.
"""
from __future__ import annotations

import json
import inspect

import numpy as np

from backend.autocull_rank import (
    consequential_bursts,
    promote_winner,
    resolve_cached,
    resolve_winners,
)
from backend.routers import decisions as decisions_mod
from backend.routers.decisions import _cap_burst_keeps, _guarantee_winner


def _emb(scene: int, variant: int, dims: int = 32) -> str:
    """Same construction as test_burst_keep_cap._scene: same-scene frames sit in
    one sub-cluster (~0.96) without tripping the 0.97 near-dup collapse."""
    v = [0.0] * dims
    v[scene % 8] = 1.0
    v[8 + (variant % 20)] = 0.20
    v = np.asarray(v, dtype=np.float32)
    return json.dumps((v / np.linalg.norm(v)).tolist())


def _row(id: int, scene: int = 0, **overrides) -> dict:
    base = {
        "id": id,
        "face_detected": 0,
        "face_sharpness_score": None,
        "eyes_open": None,
        "sharpness_score": None,
        "iqa_score": None,
        "aesthetic_score": None,
        "overall_score": None,
        "personal_score": None,
        "shot_at": None,
        "embedding": _emb(scene, id),
    }
    base.update(overrides)
    return base


def _thr(**overrides) -> dict:
    base = {
        "keep_threshold": 70.0,
        "maybe_threshold": 45.0,
        "fallback_keep": 60.0,
        "fallback_maybe": 40.0,
        "max_burst_keeps": 1.0,
        "burst_keep_margin": 8.0,
        "burst_keep_winner_delta": 6.0,
        "burst_keep_reject_delta": 20.0,
        "max_burst_maybes": 99.0,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. Which groups are worth ~60s of Ollama time
# ---------------------------------------------------------------------------

def test_two_keeps_is_consequential():
    """≥2 Keeps → the keep-cap pass decides which survives, so the winner matters."""
    burst = [_row(1), _row(2), _row(3)]
    decisions = {1: "keep", 2: "keep", 3: "reject"}
    assert consequential_bursts([burst], decisions) == [burst]


def test_zero_keeps_is_consequential():
    """0 Keeps → the keep-guarantee pass promotes the winner. This is the burst
    the user is about to lose entirely; it is the LAST one to skip."""
    burst = [_row(1), _row(2), _row(3)]
    decisions = {1: "reject", 2: "maybe", 3: "reject"}
    assert consequential_bursts([burst], decisions) == [burst]


def test_single_keep_is_not_consequential():
    """Exactly one Keep: neither post-pass fires, the scores already settled it."""
    burst = [_row(1), _row(2), _row(3)]
    decisions = {1: "keep", 2: "reject", 3: "reject"}
    assert consequential_bursts([burst], decisions) == []


def test_pairs_are_below_the_size_floor():
    """Mirrors the prerank worker's _MIN_GROUP_SIZE — a pair isn't worth a call."""
    burst = [_row(1), _row(2)]
    decisions = {1: "keep", 2: "keep"}
    assert consequential_bursts([burst], decisions) == []


def test_size_floor_matches_the_prerank_worker():
    """If one of the two floors moves, the grid's 'pending' chip and auto-cull's
    spend stop agreeing about which groups the LLM ever looks at."""
    from backend.autocull_rank import MIN_GROUP_SIZE
    from phase2_quality.prerank import _MIN_GROUP_SIZE

    assert MIN_GROUP_SIZE == _MIN_GROUP_SIZE


# ---------------------------------------------------------------------------
# 2. The winner reaches both post-passes
# ---------------------------------------------------------------------------

def test_keep_cap_keeps_the_ai_winner_not_the_score_winner():
    """id 1 scores highest, id 3 is the LLM's pick — id 3 must be the survivor."""
    burst = [_row(1, overall_score=95), _row(2, overall_score=90), _row(3, overall_score=80)]
    decisions = {1: "keep", 2: "keep", 3: "keep"}
    reasons = {1: "threshold_keep", 2: "threshold_keep", 3: "threshold_keep"}

    _cap_burst_keeps([burst], decisions, reasons, _thr(),
                     {1: 3, 2: 3, 3: 3})

    assert decisions[3] == "keep"
    assert decisions[1] != "keep"
    assert decisions[2] != "keep"


def test_keep_cap_without_winners_still_uses_the_score_pick():
    """The AI rank is additive: with no winners the pass behaves exactly as before."""
    burst = [_row(1, overall_score=95), _row(2, overall_score=90), _row(3, overall_score=80)]
    decisions = {1: "keep", 2: "keep", 3: "keep"}
    reasons = {i: "threshold_keep" for i in (1, 2, 3)}

    _cap_burst_keeps([burst], decisions, reasons, _thr(), None)

    assert decisions[1] == "keep"


def test_guarantee_promotes_the_ai_winner():
    burst = [_row(1, overall_score=50), _row(2, overall_score=30), _row(3, overall_score=20)]
    reasons = {1: "soft_face", 2: "closed_eyes", 3: "cluttered_background"}

    assert _guarantee_winner(burst, reasons, {1: 3, 2: 3, 3: 3})["id"] == 3
    assert _guarantee_winner(burst, reasons, None)["id"] == 1


def test_guarantee_will_not_promote_a_physically_unrecoverable_ai_pick():
    """The pass exists to hand the user something rescuable. The LLM ranks frames
    against each other, so in an all-mediocre burst its #1 can be the blurred
    one — and a blurred frame is not a rescue."""
    burst = [_row(1, overall_score=50), _row(2, overall_score=30)]
    reasons = {1: "closed_eyes", 2: "blurry_frame"}

    assert _guarantee_winner(burst, reasons, {1: 2, 2: 2})["id"] == 1


def test_guarantee_accepts_an_unrecoverable_pick_when_nothing_else_is_better():
    """Guard only fires when a subjectively-rejected alternative exists — if the
    whole burst is physical, the caller has already decided to promote one."""
    burst = [_row(1, overall_score=50), _row(2, overall_score=30)]
    reasons = {1: "blurry_frame", 2: "iso_ceiling"}

    assert _guarantee_winner(burst, reasons, {1: 2, 2: 2})["id"] == 2


def test_auto_cull_call_sites_pass_the_winners_through():
    """Both endpoints must hand their winner map to both passes. A mechanism that
    works while every call site passes nothing is the failure mode from
    feedback_verify_call_site_not_just_mechanism."""
    run_src = inspect.getsource(decisions_mod.run_auto_cull)
    preview_src = inspect.getsource(decisions_mod.auto_cull_preview)

    assert "_cap_burst_keeps(bursts, decisions, reasons, thresholds, ai_winners)" in run_src
    assert "_guarantee_winner(burst, reasons, ai_winners)" in run_src
    assert "thresholds, ai_winners)" in preview_src
    assert "_guarantee_winner(burst, preview_reasons, ai_winners)" in preview_src


# ---------------------------------------------------------------------------
# 3. Fallback is silent in the decisions but loud in the report
# ---------------------------------------------------------------------------

class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _conn_factory():
    return _FakeConn()


def test_ranked_result_maps_every_member_to_the_winner(monkeypatch):
    monkeypatch.setattr(
        "phase2_quality.burst_ranker.rank_burst",
        lambda ids, conn, cached_only=False: {
            "status": "ranked",
            "rankings": [{"image_id": 7, "rank": 1}, {"image_id": 5, "rank": 2}],
        },
    )
    burst = [_row(5), _row(7), _row(9)]

    winners, stats = resolve_winners([burst], conn_factory=_conn_factory)

    assert winners == {5: 7, 7: 7, 9: 7}
    assert stats["ranked"] == 1 and stats["fell_back"] == 0


def test_missing_vision_model_falls_back_and_is_counted(monkeypatch):
    monkeypatch.setattr(
        "phase2_quality.burst_ranker.rank_burst",
        lambda ids, conn, cached_only=False: {"status": "no_vision_model", "rankings": []},
    )
    winners, stats = resolve_winners([[_row(1), _row(2), _row(3)]], conn_factory=_conn_factory)

    assert winners == {}
    assert stats["fell_back"] == 1
    assert stats["fallback_reasons"] == {"no_vision_model": 1}


def test_ranker_exception_does_not_abort_the_cull(monkeypatch):
    def _boom(ids, conn, cached_only=False):
        raise RuntimeError("ollama unreachable")

    monkeypatch.setattr("phase2_quality.burst_ranker.rank_burst", _boom)
    winners, stats = resolve_winners([[_row(1), _row(2), _row(3)]], conn_factory=_conn_factory)

    assert winners == {}
    assert stats["fallback_reasons"] == {"error": 1}


def test_rank_one_that_is_not_a_member_is_rejected(monkeypatch):
    """A rank-1 outside the group would silently hand the burst passes a winner
    they can't find — better to fall back than to trust it."""
    monkeypatch.setattr(
        "phase2_quality.burst_ranker.rank_burst",
        lambda ids, conn, cached_only=False: {
            "status": "ranked", "rankings": [{"image_id": 999, "rank": 1}],
        },
    )
    winners, stats = resolve_winners([[_row(1), _row(2), _row(3)]], conn_factory=_conn_factory)

    assert winners == {}
    assert stats["fell_back"] == 1


def test_stop_between_groups_keeps_what_was_ranked(monkeypatch):
    calls = {"n": 0}

    def _rank(ids, conn, cached_only=False):
        calls["n"] += 1
        return {"status": "ranked", "rankings": [{"image_id": ids[0], "rank": 1}]}

    monkeypatch.setattr("phase2_quality.burst_ranker.rank_burst", _rank)
    bursts = [[_row(i), _row(i + 1), _row(i + 2)] for i in (1, 10, 20)]

    winners, stats = resolve_winners(
        bursts, conn_factory=_conn_factory,
        should_stop=lambda: calls["n"] >= 1,
    )

    assert stats["stopped"] is True
    assert calls["n"] == 1
    assert winners == {1: 1, 2: 1, 3: 1}


def test_preview_never_fires_a_fresh_rank(monkeypatch):
    """The modal must not block on ~60s LLM calls — a preview that costs as much
    as the run it previews is not a preview."""
    seen = {}

    def _rank(ids, conn, cached_only=False):
        seen["cached_only"] = cached_only
        return {"status": "not_cached", "rankings": []}

    monkeypatch.setattr("phase2_quality.burst_ranker.rank_burst", _rank)
    winners, stats = resolve_winners(
        [[_row(1), _row(2), _row(3)]], cached_only=True, conn_factory=_conn_factory)

    assert seen["cached_only"] is True
    assert winners == {}
    assert stats["fallback_reasons"] == {"not_cached": 1}


def test_promote_winner_leaves_the_rest_of_the_order_alone():
    """The AI rank's whole reach is index 0. Everything downstream — budgets,
    near-dup reps, demotion order — still reads the score order."""
    ordered = [_row(1), _row(2), _row(3), _row(4)]

    moved = promote_winner(ordered, 3)

    assert [m["id"] for m in moved] == [3, 1, 2, 4]
    assert [m["id"] for m in promote_winner(ordered, None)] == [1, 2, 3, 4]
    assert [m["id"] for m in promote_winner(ordered, 99)] == [1, 2, 3, 4]


# ---------------------------------------------------------------------------
# 4. The preview's estimate and the run's progress bar must count the same thing
# ---------------------------------------------------------------------------

def _statuses(mapping):
    """rank_burst stub that returns a per-first-member status."""
    def _rank(ids, conn, cached_only=False):
        status = mapping[ids[0]]
        if status == "ranked":
            return {"status": "ranked", "rankings": [{"image_id": ids[0], "rank": 1}]}
        return {"status": status, "rankings": []}
    return _rank


def test_resolve_cached_only_pends_the_groups_that_cost_an_llm_call(monkeypatch):
    """A near-duplicate group resolves from embeddings alone and a cached one is
    free. Counting either as "needs ranking" is what made the modal promise 50
    groups and the progress bar then show 13/72."""
    monkeypatch.setattr("phase2_quality.burst_ranker.rank_burst", _statuses({
        1: "ranked", 10: "near_duplicates", 20: "not_cached", 30: "not_cached",
    }))
    bursts = [[_row(i), _row(i + 1), _row(i + 2)] for i in (1, 10, 20, 30)]

    winners, pending, stats = resolve_cached(bursts, conn_factory=_conn_factory)

    assert len(pending) == 2                      # only the two uncached ones
    assert stats["ranked"] == 1 and stats["free"] == 2
    assert stats["fallback_reasons"] == {"near_duplicates": 1}
    assert winners == {1: 1, 2: 1, 3: 1}


def test_preview_estimate_equals_the_runs_progress_total(monkeypatch):
    """The number the modal quotes before you start and the denominator of the
    bar you then watch come from one call, not two definitions."""
    monkeypatch.setattr("phase2_quality.burst_ranker.rank_burst", _statuses({
        1: "ranked", 10: "near_duplicates", 20: "not_cached", 30: "not_cached",
    }))
    bursts = [[_row(i), _row(i + 1), _row(i + 2)] for i in (1, 10, 20, 30)]
    decisions = {m["id"]: "keep" for b in bursts for m in b}

    preview = decisions_mod._ai_rank_preview(bursts, decisions, enabled=True)
    _, pending, _ = resolve_cached(
        consequential_bursts(bursts, decisions), conn_factory=_conn_factory)

    assert preview["to_rank"] == len(pending)
    assert preview["estimated_seconds"] == len(pending) * 60
    assert preview["groups"] == 4        # contested total, reported separately


def test_run_publishes_the_pending_count_as_its_progress_total(monkeypatch):
    """Call-site check: _run_ai_ranking must publish len(pending), not the
    contested count. Same failure shape as the winners-map call-site test."""
    src = inspect.getsource(decisions_mod._run_ai_ranking)
    assert '"total": len(pending)' in src
    assert "resolve_cached(contested)" in src
