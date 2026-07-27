"""Tests for the burst multi-keep post-pass in /auto-cull.

_compute_auto_decision scores every photo independently, so a run of
near-identical frames in one burst (same pose, seconds apart) can each
individually clear the Keep cutoff and all land in Keep — the grouping/dedup
step users expect from a "burst" never fires for auto-cull without this pass.

The pass (`_cap_burst_keeps`) keeps the best-by-priority member of each burst,
lets a few additional GENUINELY-STRONG frames also keep (up to max_burst_keeps),
and demotes the rest — graded to Maybe (near-alternates, reason
"burst_keep_cap") or Reject (clearly-weaker duplicates, reason
"burst_keep_reject"). Two gates decide whether an extra frame survives as Keep:

  • margin:    its score ≥ its mode's Keep cutoff + burst_keep_margin
  • closeness: winner_score − its score ≤ burst_keep_winner_delta

A non-survivor rejects (instead of Maybe) when it's more than
burst_keep_reject_delta below the winner OR below its mode's Maybe cutoff.

These tests call the actual production helper `_cap_burst_keeps` directly and
pass an explicit `thresholds` dict so behaviour is independent of DB state.
"""
from __future__ import annotations

from backend.routers.decisions import _cap_burst_keeps


# Explicit thresholds so the tests don't depend on the settings DB. Values
# mirror the shipped defaults (fallback_keep=60, fallback_maybe=40,
# keep_threshold=70, maybe_threshold=45, margin=8, winner_delta=6,
# reject_delta=20). Individual tests override single keys via _thr(...).
def _thr(**overrides) -> dict:
    base = {
        "keep_threshold": 70.0,
        "maybe_threshold": 45.0,
        "fallback_keep": 60.0,
        "fallback_maybe": 40.0,
        "max_burst_keeps": 2.0,
        "burst_keep_margin": 8.0,
        "burst_keep_winner_delta": 6.0,
        "burst_keep_reject_delta": 20.0,
        # Large default so tests not exercising the review budget behave as if
        # unlimited Maybes are allowed; budget tests override this explicitly.
        "max_burst_maybes": 99.0,
    }
    base.update(overrides)
    return base


def _row(id: int, scene: int = 0, **overrides) -> dict:
    """Build a fake image row. By default every row lands in ONE shared visual
    scene (scene 0, a distinct per-id variant) so a burst of _row()s is a single
    sub-cluster — the common case the budget/gate tests exercise. Pass scene=N
    to place a frame in a different sub-scene (distinct POV). Pass embedding=...
    explicitly to control the vector directly (near-dup tests). id drives the
    per-frame variant so no two same-scene rows are identical."""
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
        "embedding": _scene(scene, variant=id),
    }
    base.update(overrides)
    return base


def _emb_json(vec) -> str:
    """Serialize a raw vector to the JSON string form stored in images.embedding,
    L2-normalised (the pipeline always stores unit vectors, so dot == cosine)."""
    import json
    import numpy as np
    v = np.asarray(vec, dtype=np.float32)
    n = np.linalg.norm(v)
    if n > 0:
        v = v / n
    return json.dumps(v.tolist())


def _scene(scene_id: int, variant: int = 0, dims: int = 32) -> str:
    """Embedding for a frame in a distinct visual scene, with a per-frame
    variation so same-scene frames land in ONE sub-cluster (pairwise cosine
    ~0.96 ≥ the 0.94 scene-split) but are NOT near-duplicates (< the 0.97
    collapse) — the state real burst frames are in, and what the budget tests
    need to exercise Stage B without tripping Stage A.

    Same scene_id, different variant → cosine ~0.96 (same sub-cluster, distinct
    frame — no member is the pure scene axis, so none is a near-dup of another).
    Different scene_id → cosine ~0 (separate sub-clusters, each with its own keep
    budget). Scene axes live in 0..7; per-variant perturbation axes in 8..27,
    disjoint, so scenes never bleed into each other. For a true near-duplicate
    pair, pass an explicit near-identical embedding via _emb_json (Stage-A tests)."""
    v = [0.0] * dims
    v[scene_id % 8] = 1.0
    # Always perturb (even variant 0) so no frame sits on the pure scene axis —
    # that keeps every same-scene pair below the 0.97 near-dup line.
    v[8 + (variant % 20)] = 0.20
    return _emb_json(v)


# ── Cap-at-one behaviour (max_burst_keeps=1 reproduces the original pass) ──

def test_cap_at_one_demotes_all_but_the_best():
    """max_burst_keeps=1 is the legacy behaviour: only the hero keeps."""
    burst = [
        _row(1, overall_score=80),
        _row(2, overall_score=95),  # winner
        _row(3, overall_score=82),
        _row(4, overall_score=78),
    ]
    decisions = {1: "keep", 2: "keep", 3: "keep", 4: "keep"}
    reasons   = {i: "threshold_keep" for i in (1, 2, 3, 4)}
    demotions = _cap_burst_keeps([burst], decisions, reasons, _thr(max_burst_keeps=1))

    assert decisions[2] == "keep"
    # 1, 3, 4 all demote. None is >20 below the winner (95) and all ≥40, so
    # they land in Maybe, not Reject.
    assert decisions[1] == decisions[3] == decisions[4] == "maybe"
    assert reasons[1] == reasons[3] == reasons[4] == "burst_keep_cap"
    assert reasons[2] == "threshold_keep"  # winner untouched
    assert {d["image_id"] for d in demotions} == {1, 3, 4}
    assert all(d["to"] == "maybe" for d in demotions)
    assert all(d["burst_size"] == 4 for d in demotions)


# ── Multi-keep: strong alternates survive ──

def test_second_strong_frame_survives_as_keep():
    """A frame that clears the cutoff by the margin AND is close to the winner
    keeps too, up to max_burst_keeps (default 2)."""
    burst = [
        _row(1, overall_score=95),  # winner
        _row(2, overall_score=92),  # margin (>=68) ✓, within 6 of winner ✓ → keep
        _row(3, overall_score=85),  # 3rd — hits max_burst_keeps=2 → demote to maybe
    ]
    decisions = {1: "keep", 2: "keep", 3: "keep"}
    reasons   = {i: "threshold_keep" for i in (1, 2, 3)}
    demotions = _cap_burst_keeps([burst], decisions, reasons, _thr())

    assert decisions[1] == "keep"
    assert decisions[2] == "keep"          # survived — genuine strong alternate
    assert reasons[2] == "threshold_keep"  # untouched
    assert decisions[3] == "maybe"         # over the cap, but strong → maybe
    assert [d["image_id"] for d in demotions] == [3]
    assert demotions[0]["to"] == "maybe"


def test_raising_max_lets_more_survive():
    burst = [
        _row(1, overall_score=95),
        _row(2, overall_score=93),
        _row(3, overall_score=91),
    ]
    decisions = {1: "keep", 2: "keep", 3: "keep"}
    reasons   = {i: "threshold_keep" for i in (1, 2, 3)}
    _cap_burst_keeps([burst], decisions, reasons, _thr(max_burst_keeps=3))
    assert decisions[1] == decisions[2] == decisions[3] == "keep"


def test_near_miss_close_to_winner_goes_to_maybe():
    """Fails the margin gate but is close to the winner and above the Maybe
    cutoff → Maybe, not Reject."""
    burst = [
        _row(1, overall_score=67),  # winner (low-ish burst)
        _row(2, overall_score=64),  # <68 margin fail; within 20 of winner; ≥40 → maybe
    ]
    decisions = {1: "keep", 2: "keep"}
    reasons   = {1: "threshold_keep", 2: "threshold_keep"}
    demotions = _cap_burst_keeps([burst], decisions, reasons, _thr())
    assert decisions[1] == "keep"
    assert decisions[2] == "maybe"
    assert reasons[2] == "burst_keep_cap"
    assert demotions[0]["to"] == "maybe"


# ── Graded demotion: clearly-weak frames reject ──

def test_clearly_weak_frame_rejects_when_far_below_winner():
    """A non-surviving keep more than reject_delta below the winner is the
    clearly-weak duplicate → Reject."""
    burst = [
        _row(1, overall_score=95),  # winner
        _row(2, overall_score=70),  # 25 below winner (>20) → reject
    ]
    decisions = {1: "keep", 2: "keep"}
    reasons   = {1: "threshold_keep", 2: "threshold_keep"}
    demotions = _cap_burst_keeps([burst], decisions, reasons, _thr())
    assert decisions[1] == "keep"
    assert decisions[2] == "reject"
    assert reasons[2] == "burst_keep_reject"
    assert demotions[0]["to"] == "reject"


def test_frame_below_maybe_cutoff_rejects():
    """Even when close to the winner, a frame below its mode's Maybe cutoff is
    clearly weak → Reject."""
    burst = [
        _row(1, overall_score=44),  # winner (whole burst is weak)
        _row(2, overall_score=38),  # <fallback_maybe(40) → reject; within 6 of winner
    ]
    decisions = {1: "keep", 2: "keep"}
    reasons   = {1: "threshold_keep", 2: "threshold_keep"}
    demotions = _cap_burst_keeps([burst], decisions, reasons, _thr())
    assert decisions[2] == "reject"
    assert reasons[2] == "burst_keep_reject"


def test_reject_delta_raise_routes_to_maybe_instead():
    """Raising burst_keep_reject_delta makes auto-reject rarer — a frame that
    would reject at the default routes to Maybe with a wider band."""
    burst = [
        _row(1, overall_score=95),
        _row(2, overall_score=70),  # 25 below winner
    ]
    decisions = {1: "keep", 2: "keep"}
    reasons   = {1: "threshold_keep", 2: "threshold_keep"}
    _cap_burst_keeps([burst], decisions, reasons, _thr(burst_keep_reject_delta=30))
    assert decisions[2] == "maybe"  # 25 < 30 now, and 70 ≥ 40 → maybe


# ── Score-field selection follows the decision mode ──

def test_personal_mode_reads_personal_score():
    """A personal_keep member is gated on personal_score against the personal
    cutoffs, not overall_score."""
    burst = [
        # winner: strong personal_score, weak overall.
        _row(1, personal_score=95, overall_score=10),
        # challenger: personal_score 90 ≥ 70+8 margin, within 6 of winner → keep.
        _row(2, personal_score=90, overall_score=10),
    ]
    decisions = {1: "keep", 2: "keep"}
    reasons   = {1: "personal_keep", 2: "personal_keep"}
    _cap_burst_keeps([burst], decisions, reasons, _thr())
    # top_n_candidates ranks by technical priority (overall_score tie here), so
    # whichever is winner, the other has personal_score within margin+closeness
    # → both survive as keep.
    assert decisions[1] == "keep"
    assert decisions[2] == "keep"


def test_missing_score_never_rejects_always_maybe():
    """A member whose gating score is missing can't be judged strong — it never
    survives, and it demotes to the safer Maybe, never Reject."""
    burst = [
        _row(1, overall_score=95),
        _row(2, overall_score=None),  # no score → can't qualify, can't be 'weak'
    ]
    decisions = {1: "keep", 2: "keep"}
    reasons   = {1: "threshold_keep", 2: "threshold_keep"}
    demotions = _cap_burst_keeps([burst], decisions, reasons, _thr())
    assert decisions[2] == "maybe"
    assert demotions[0]["to"] == "maybe"


# ── Unchanged invariants ──

def test_noop_when_burst_has_only_one_keep():
    burst = [_row(1, overall_score=90), _row(2, overall_score=50)]
    decisions = {1: "keep", 2: "maybe"}
    reasons   = {1: "threshold_keep", 2: "threshold_maybe"}
    demotions = _cap_burst_keeps([burst], decisions, reasons, _thr())
    assert demotions == []
    assert decisions == {1: "keep", 2: "maybe"}


def test_noop_for_singleton_burst():
    burst = [_row(1, overall_score=90)]
    decisions = {1: "keep"}
    reasons = {1: "threshold_keep"}
    assert _cap_burst_keeps([burst], decisions, reasons, _thr()) == []


def test_face_sharpness_wins_over_overall_score():
    """Priority mirrors top_n_candidates: a strong face beats a higher
    overall_score without a face. max_burst_keeps=1 so only the winner keeps."""
    burst = [
        _row(1, face_detected=1, face_sharpness_score=90, overall_score=40),
        _row(2, face_detected=0, face_sharpness_score=0, overall_score=95),
    ]
    decisions = {1: "keep", 2: "keep"}
    reasons   = {1: "personal_keep", 2: "personal_keep"}
    _cap_burst_keeps([burst], decisions, reasons, _thr(max_burst_keeps=1))
    assert decisions[1] == "keep"
    assert decisions[2] != "keep"


def test_non_keep_decisions_in_the_burst_are_left_alone():
    burst = [
        _row(1, overall_score=95),
        _row(2, overall_score=93),
        _row(3, overall_score=30),
    ]
    decisions = {1: "keep", 2: "keep", 3: "reject"}
    reasons   = {1: "threshold_keep", 2: "threshold_keep", 3: "threshold_reject"}
    _cap_burst_keeps([burst], decisions, reasons, _thr(max_burst_keeps=1))
    assert decisions[3] == "reject"          # untouched
    assert reasons[3] == "threshold_reject"  # untouched


def test_multiple_bursts_are_capped_independently():
    burst_a = [_row(1, overall_score=90), _row(2, overall_score=95)]
    burst_b = [_row(3, overall_score=60), _row(4, overall_score=99)]
    decisions = {1: "keep", 2: "keep", 3: "keep", 4: "keep"}
    reasons   = {i: "threshold_keep" for i in (1, 2, 3, 4)}
    _cap_burst_keeps([burst_a, burst_b], decisions, reasons, _thr(max_burst_keeps=1))
    assert decisions[2] == "keep" and decisions[1] != "keep"
    assert decisions[4] == "keep" and decisions[3] != "keep"


def test_returns_empty_list_when_no_bursts_have_redundant_keeps():
    bursts = [
        [_row(1, overall_score=90), _row(2, overall_score=30)],
        [_row(3, overall_score=85)],
    ]
    decisions = {1: "keep", 2: "reject", 3: "keep"}
    reasons   = {1: "threshold_keep", 2: "threshold_reject", 3: "threshold_keep"}
    assert _cap_burst_keeps(bursts, decisions, reasons, _thr()) == []


# ── Scene split: distinct sub-scenes within one time-gap burst ──

def test_two_scenes_each_get_their_own_keep_budget():
    """The headline case: one time-gap burst contains two visually distinct POVs
    (angle A + angle B, seconds apart). Each sub-scene keeps its own winner even
    at max_burst_keeps=1 — a second scene isn't budget-rejected as a redundant
    copy of the first."""
    burst = [
        _row(1, scene=0, overall_score=95),  # scene A winner
        _row(2, scene=0, overall_score=80),  # scene A alt
        _row(3, scene=5, overall_score=93),  # scene B winner (distinct POV)
        _row(4, scene=5, overall_score=78),  # scene B alt
    ]
    decisions = {i: "keep" for i in (1, 2, 3, 4)}
    reasons   = {i: "threshold_keep" for i in (1, 2, 3, 4)}
    _cap_burst_keeps([burst], decisions, reasons, _thr(max_burst_keeps=1))
    # Both scene winners keep — two keeps from one time-gap burst.
    assert decisions[1] == "keep"
    assert decisions[3] == "keep"
    # The alternates demote within their OWN scene's budget.
    assert decisions[2] == "maybe" and decisions[4] == "maybe"


def test_scene_split_does_not_inflate_a_single_scene_burst():
    """A burst that's really one scene still collapses to the per-burst budget —
    the split must not manufacture extra keeps out of one moment."""
    burst = [_row(i, scene=0, overall_score=95 - i) for i in range(1, 6)]
    decisions = {i: "keep" for i in range(1, 6)}
    reasons   = {i: "threshold_keep" for i in range(1, 6)}
    _cap_burst_keeps(
        [burst], decisions, reasons,
        _thr(max_burst_keeps=1, max_burst_maybes=2),
    )
    kept = sum(1 for i in range(1, 6) if decisions[i] == "keep")
    assert kept == 1  # one scene → one keep, exactly as before the split


def test_budget_is_per_scene_not_shared_across_scenes():
    """Spending scene A's Maybe budget must not starve scene B — each scene has
    its own fresh Keep+Maybe allowance."""
    burst = [
        # Scene A: winner + 3 close alternates (will spend A's 1 Maybe, reject 2).
        _row(1, scene=0, overall_score=95),
        _row(2, scene=0, overall_score=94),
        _row(3, scene=0, overall_score=93),
        _row(4, scene=0, overall_score=92),
        # Scene B: winner + 1 close alternate (should get B's own Maybe slot).
        _row(5, scene=5, overall_score=90),
        _row(6, scene=5, overall_score=89),
    ]
    decisions = {i: "keep" for i in range(1, 7)}
    reasons   = {i: "threshold_keep" for i in range(1, 7)}
    _cap_burst_keeps(
        [burst], decisions, reasons,
        _thr(max_burst_keeps=1, max_burst_maybes=1),
    )
    # Scene A: 1 keep, 1 maybe, 2 redundant-reject.
    assert decisions[1] == "keep"
    assert sum(1 for i in (2, 3, 4) if decisions[i] == "maybe") == 1
    assert sum(1 for i in (2, 3, 4) if decisions[i] == "reject") == 2
    # Scene B got its OWN budget: winner keeps, alt takes B's fresh Maybe slot.
    assert decisions[5] == "keep"
    assert decisions[6] == "maybe"


def test_near_duplicate_across_whole_burst_still_collapses_within_scene():
    """Stage A near-dup collapse runs burst-wide and still fires inside a scene:
    a true near-pixel-dup of a scene winner rejects regardless of the split."""
    dup = _emb_json([1.0, 0.0, 0.0, 0.0])
    burst = [
        _row(1, overall_score=95, embedding=dup),
        _row(2, overall_score=93, embedding=_emb_json([0.999, 0.001, 0.0, 0.0])),  # ~1.0 → dup of #1
    ]
    decisions = {1: "keep", 2: "keep"}
    reasons   = {1: "threshold_keep", 2: "threshold_keep"}
    _cap_burst_keeps([burst], decisions, reasons, _thr())
    assert decisions[2] == "reject" and reasons[2] == "burst_near_duplicate"


def test_frame_without_embedding_becomes_its_own_scene():
    """A frame we can't place visually (no embedding) gets its own singleton
    sub-scene and keeps — never budget-rejected against a scene it may not
    belong to (consistent with 'no auto-trash on missing data')."""
    burst = [
        _row(1, scene=0, overall_score=95),
        _row(2, scene=0, overall_score=94),
        _row(3, overall_score=60, embedding=None),  # unplaceable → own scene → keeps
    ]
    decisions = {1: "keep", 2: "keep", 3: "keep"}
    reasons   = {i: "threshold_keep" for i in (1, 2, 3)}
    _cap_burst_keeps(
        [burst], decisions, reasons,
        _thr(max_burst_keeps=1, max_burst_maybes=0),
    )
    assert decisions[3] == "keep"  # its own scene, its own winner


# ── Stage A: near-duplicate collapse ──

def test_near_duplicate_rejects_the_lower_priority_frame():
    """Two cosine-near-identical frames → the lower-priority one rejects with
    reason burst_near_duplicate, regardless of how close its score is."""
    dup = [1.0, 0.0, 0.0, 0.0]
    burst = [
        _row(1, overall_score=95, embedding=_emb_json(dup)),        # winner + rep
        _row(2, overall_score=93, embedding=_emb_json([0.999, 0.001, 0.0, 0.0])),  # ~1.0 cosine → dup
    ]
    decisions = {1: "keep", 2: "keep"}
    reasons   = {1: "threshold_keep", 2: "threshold_keep"}
    demotions = _cap_burst_keeps([burst], decisions, reasons, _thr())
    assert decisions[1] == "keep"
    assert decisions[2] == "reject"
    assert reasons[2] == "burst_near_duplicate"
    assert demotions[0]["to"] == "reject"


def test_distinct_frames_are_not_near_dup_collapsed():
    """Orthogonal embeddings (cosine 0) are visually distinct — no Stage A
    reject; they fall through to the normal score gates."""
    burst = [
        _row(1, overall_score=95, embedding=_emb_json([1.0, 0.0, 0.0, 0.0])),
        _row(2, overall_score=93, embedding=_emb_json([0.0, 1.0, 0.0, 0.0])),
    ]
    decisions = {1: "keep", 2: "keep"}
    reasons   = {1: "threshold_keep", 2: "threshold_keep"}
    _cap_burst_keeps([burst], decisions, reasons, _thr())
    # 93 clears 60+8 margin and is within 6 of the winner → survives as keep.
    assert decisions[1] == "keep" and decisions[2] == "keep"


def test_missing_embedding_never_near_dup_collapses():
    """A frame with no embedding can't be proven a duplicate — it must reach
    the score gates, not be Stage-A rejected."""
    burst = [
        _row(1, overall_score=95, embedding=_emb_json([1.0, 0.0])),
        _row(2, overall_score=93, embedding=None),
    ]
    decisions = {1: "keep", 2: "keep"}
    reasons   = {1: "threshold_keep", 2: "threshold_keep"}
    _cap_burst_keeps([burst], decisions, reasons, _thr())
    assert decisions[2] != "reject" or reasons[2] != "burst_near_duplicate"


def test_higher_priority_dup_survives_as_rep():
    """The BETTER of a near-dup pair is the surviving rep even when it's not the
    overall burst winner."""
    dup_a = _emb_json([0.0, 1.0, 0.0])
    dup_b = _emb_json([0.001, 0.999, 0.0])  # near-identical to dup_a
    burst = [
        _row(1, overall_score=99, embedding=_emb_json([1.0, 0.0, 0.0])),  # winner, distinct
        _row(2, overall_score=90, embedding=dup_a),  # rep of the dup pair (higher score)
        _row(3, overall_score=85, embedding=dup_b),  # dup of #2 → reject
    ]
    decisions = {1: "keep", 2: "keep", 3: "keep"}
    reasons   = {i: "threshold_keep" for i in (1, 2, 3)}
    _cap_burst_keeps([burst], decisions, reasons, _thr(max_burst_keeps=3))
    assert decisions[3] == "reject" and reasons[3] == "burst_near_duplicate"
    assert decisions[2] != "reject"  # the rep survived


# ── Stage B: review budget ──

def test_review_budget_rejects_the_overflow():
    """A big tight burst: winner + N alternates all score close together. Beyond
    max_burst_keeps Keeps + max_burst_maybes Maybes, the rest reject as
    burst_redundant rather than piling up as Maybe."""
    # 6 frames, all within the Maybe band, none near-dup (no embeddings), all
    # close enough to the winner (95..90) that none is 'clearly weak'.
    burst = [_row(i, overall_score=95 - i) for i in range(6)]  # 95,94,93,92,91,90
    decisions = {i: "keep" for i in range(6)}
    reasons   = {i: "threshold_keep" for i in range(6)}
    demotions = _cap_burst_keeps(
        [burst], decisions, reasons,
        _thr(max_burst_keeps=1, max_burst_maybes=2),
    )
    kept   = [i for i in range(6) if decisions[i] == "keep"]
    maybes = [i for i in range(6) if decisions[i] == "maybe"]
    rejects = [i for i in range(6) if decisions[i] == "reject"]
    assert len(kept) == 1      # winner only (cap=1)
    assert len(maybes) == 2    # budget
    assert len(rejects) == 3   # the redundant overflow
    assert all(reasons[i] == "burst_redundant" for i in rejects)
    assert all(d["to"] in ("maybe", "reject") for d in demotions)


def test_review_budget_zero_rejects_all_non_survivors():
    """max_burst_maybes=0 keeps no Maybe alternates — every non-surviving,
    non-clearly-weak frame rejects as redundant."""
    burst = [_row(i, overall_score=95 - i) for i in range(4)]  # 95,94,93,92
    decisions = {i: "keep" for i in range(4)}
    reasons   = {i: "threshold_keep" for i in range(4)}
    _cap_burst_keeps(
        [burst], decisions, reasons,
        _thr(max_burst_keeps=1, max_burst_maybes=0),
    )
    assert decisions[0] == "keep"
    assert all(decisions[i] == "reject" for i in (1, 2, 3))
    assert all(reasons[i] == "burst_redundant" for i in (1, 2, 3))


def test_clearly_weak_still_rejects_before_consuming_budget():
    """A clearly-weak frame rejects on its own merits (burst_keep_reject) and
    does NOT consume a Maybe budget slot — the budget is for genuine alternates."""
    burst = [
        _row(1, overall_score=95),
        _row(2, overall_score=70),  # 25 below winner → clearly weak → reject
        _row(3, overall_score=93),  # close alternate → should still get a Maybe slot
    ]
    decisions = {1: "keep", 2: "keep", 3: "keep"}
    reasons   = {i: "threshold_keep" for i in (1, 2, 3)}
    _cap_burst_keeps(
        [burst], decisions, reasons,
        _thr(max_burst_keeps=1, max_burst_maybes=1),
    )
    assert decisions[2] == "reject" and reasons[2] == "burst_keep_reject"
    # #3 got the single Maybe slot because #2's reject didn't consume it.
    assert decisions[3] == "maybe" and reasons[3] == "burst_keep_cap"


def test_missing_score_takes_maybe_slot_then_rejects_as_redundant():
    """A no-score frame can't be clearly-weak, so it takes a Maybe slot when one
    is free and otherwise rejects as redundant — never on the clearly-weak path."""
    burst = [
        _row(1, overall_score=95),
        _row(2, overall_score=None),  # no score
        _row(3, overall_score=None),  # no score
    ]
    decisions = {1: "keep", 2: "keep", 3: "keep"}
    reasons   = {i: "threshold_keep" for i in (1, 2, 3)}
    _cap_burst_keeps(
        [burst], decisions, reasons,
        _thr(max_burst_keeps=1, max_burst_maybes=1),
    )
    # One of the no-score frames takes the single Maybe slot; the other is
    # redundant-rejected. Neither is ever burst_keep_reject (clearly-weak path).
    outcomes = sorted([decisions[2], decisions[3]])
    assert outcomes == ["maybe", "reject"]
    assert "burst_keep_reject" not in (reasons[2], reasons[3])
