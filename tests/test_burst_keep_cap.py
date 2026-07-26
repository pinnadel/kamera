"""Tests for the burst keep-cap post-pass added to /auto-cull.

_compute_auto_decision scores every photo independently, so a run of
near-identical frames in one burst (same pose, seconds apart) can each
individually clear the Keep cutoff and all land in Keep — the grouping/dedup
step users expect from a "burst" never fires for auto-cull without this pass.

The cap caps each similarity burst at exactly one auto-cull Keep: the single
best-by-priority member (top_n_candidates — same priority as the group hero
selector) stays "keep"; every other member that scored "keep" demotes to
"maybe" with reason "burst_keep_cap". Non-keep decisions are untouched.

Unlike test_burst_keep_guarantee.py (which replicates the promotion policy
inline), these tests call the actual production helper `_cap_burst_keeps`
directly since it was extracted as an importable function.
"""
from __future__ import annotations

from backend.routers.decisions import _cap_burst_keeps


def _row(id: int, **overrides) -> dict:
    base = {
        "id": id,
        "face_detected": 0,
        "face_sharpness_score": None,
        "eyes_open": None,
        "sharpness_score": None,
        "iqa_score": None,
        "aesthetic_score": None,
        "overall_score": None,
    }
    base.update(overrides)
    return base


def test_demotes_all_but_the_best_when_every_member_scored_keep():
    """The exact screenshot scenario: 8 near-identical burst frames each
    independently cleared the Keep cutoff."""
    burst = [
        _row(1, overall_score=80),
        _row(2, overall_score=95),  # winner
        _row(3, overall_score=82),
        _row(4, overall_score=78),
    ]
    decisions = {1: "keep", 2: "keep", 3: "keep", 4: "keep"}
    reasons   = {1: "threshold_keep", 2: "threshold_keep",
                 3: "threshold_keep", 4: "threshold_keep"}
    demotions = _cap_burst_keeps([burst], decisions, reasons)

    assert decisions[2] == "keep"
    assert decisions[1] == decisions[3] == decisions[4] == "maybe"
    assert reasons[1] == reasons[3] == reasons[4] == "burst_keep_cap"
    # The winner's reason is untouched — it was never demoted.
    assert reasons[2] == "threshold_keep"
    assert {d["image_id"] for d in demotions} == {1, 3, 4}
    assert all(d["burst_size"] == 4 for d in demotions)


def test_noop_when_burst_has_only_one_keep():
    """A burst with a single Keep and the rest Maybe/Reject is already
    correct — nothing to cap."""
    burst = [_row(1, overall_score=90), _row(2, overall_score=50)]
    decisions = {1: "keep", 2: "maybe"}
    reasons   = {1: "threshold_keep", 2: "threshold_maybe"}
    demotions = _cap_burst_keeps([burst], decisions, reasons)
    assert demotions == []
    assert decisions == {1: "keep", 2: "maybe"}


def test_noop_for_singleton_burst():
    """A group of 1 can't have redundant keeps by definition."""
    burst = [_row(1, overall_score=90)]
    decisions = {1: "keep"}
    reasons = {1: "threshold_keep"}
    demotions = _cap_burst_keeps([burst], decisions, reasons)
    assert demotions == []


def test_face_sharpness_wins_over_overall_score():
    """Priority mirrors top_n_candidates: a strong face beats a higher
    overall_score without a face."""
    burst = [
        _row(1, face_detected=1, face_sharpness_score=90, overall_score=40),
        _row(2, face_detected=0, face_sharpness_score=0, overall_score=95),
    ]
    decisions = {1: "keep", 2: "keep"}
    reasons   = {1: "personal_keep", 2: "personal_keep"}
    _cap_burst_keeps([burst], decisions, reasons)
    assert decisions[1] == "keep"
    assert decisions[2] == "maybe"


def test_non_keep_decisions_in_the_burst_are_left_alone():
    """Only "keep" decisions are candidates for demotion — a burst mixing
    keep/maybe/reject shouldn't touch the maybe/reject members."""
    burst = [
        _row(1, overall_score=90),
        _row(2, overall_score=85),
        _row(3, overall_score=30),
    ]
    decisions = {1: "keep", 2: "keep", 3: "reject"}
    reasons   = {1: "threshold_keep", 2: "threshold_keep", 3: "threshold_reject"}
    demotions = _cap_burst_keeps([burst], decisions, reasons)
    assert decisions[1] == "keep"
    assert decisions[2] == "maybe"
    assert decisions[3] == "reject"  # untouched
    assert reasons[3] == "threshold_reject"  # untouched
    assert len(demotions) == 1 and demotions[0]["image_id"] == 2


def test_multiple_bursts_are_capped_independently():
    burst_a = [_row(1, overall_score=90), _row(2, overall_score=95)]
    burst_b = [_row(3, overall_score=60), _row(4, overall_score=99)]
    decisions = {1: "keep", 2: "keep", 3: "keep", 4: "keep"}
    reasons   = {i: "threshold_keep" for i in (1, 2, 3, 4)}
    demotions = _cap_burst_keeps([burst_a, burst_b], decisions, reasons)
    assert decisions[2] == "keep" and decisions[1] == "maybe"
    assert decisions[4] == "keep" and decisions[3] == "maybe"
    assert {d["image_id"] for d in demotions} == {1, 3}


def test_returns_empty_list_when_no_bursts_have_redundant_keeps():
    bursts = [
        [_row(1, overall_score=90), _row(2, overall_score=30)],
        [_row(3, overall_score=85)],
    ]
    decisions = {1: "keep", 2: "reject", 3: "keep"}
    reasons   = {1: "threshold_keep", 2: "threshold_reject", 3: "threshold_keep"}
    assert _cap_burst_keeps(bursts, decisions, reasons) == []
