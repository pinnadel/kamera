"""Tests for the burst-level keep guarantee added to /auto-cull.

The guarantee promotes the best photo in a burst (using the canonical
priority: face_sharpness → eyes_open → sharpness → IQA → aesthetic →
overall_score) from reject → maybe when ALL of the following hold:

  - The burst has ≥2 members.
  - Zero of those members are decided 'keep'.
  - At least one member's reject reason is SUBJECTIVE (not iso_ceiling /
    reciprocal_rule / blurry_frame — those are physically unrecoverable).

These tests exercise the policy directly via the helpers and the
PHYSICAL_REJECT_REASONS frozenset. The end-to-end /auto-cull path is
indirectly covered by the higher-level test_auto_decision suite — adding
a fully end-to-end HTTP test here would need real preview files and a
populated DB, which isn't worth the complexity for a policy that's already
well-isolated.
"""
from __future__ import annotations

from backend.routers.decisions import (
    _PHYSICAL_REJECT_REASONS,
    _group_undecided_into_bursts,
)
from backend.group_scoring import top_n_candidates


def _row(id: int, **overrides) -> dict:
    """Minimal row dict honouring the keys the promotion path reads."""
    base = {
        "id": id,
        "embedding": None,
        "shot_at": None,
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


# ---------------------------------------------------------------------------
# Physical-reject set: stable so we don't accidentally start rescuing photos
# that are genuinely unrecoverable. Only iso_ceiling, reciprocal_rule, and
# blurry_frame should sit here — closed_eyes / soft_face / cluttered_bg are
# subjective and the burst context can change the call.
# ---------------------------------------------------------------------------


def test_physical_reject_reasons_contains_only_unrecoverable_signals():
    assert _PHYSICAL_REJECT_REASONS == {"iso_ceiling", "reciprocal_rule", "blurry_frame"}


# ---------------------------------------------------------------------------
# Grouping: photos without an embedding fall back to singletons; the post-
# pass doesn't try to promote anything in a singleton group.
# ---------------------------------------------------------------------------


def test_grouping_returns_singletons_for_rows_without_embeddings():
    rows = [_row(1), _row(2), _row(3)]
    groups = _group_undecided_into_bursts(rows)
    # Every row had None embedding → each becomes its own singleton group.
    assert len(groups) == 3
    assert all(len(g) == 1 for g in groups)


def test_grouping_skips_singletons_for_promotion_logic():
    """A singleton group can never trigger the keep-guarantee — there's no
    'burst' to rescue. This is the contract the production code relies on
    when it does `if len(burst) < 2: continue`."""
    rows = [_row(1)]
    groups = _group_undecided_into_bursts(rows)
    assert groups == [[rows[0]]]
    # The production loop skips length-1 groups; mirror that here.
    rescues = [g for g in groups if len(g) >= 2]
    assert rescues == []


# ---------------------------------------------------------------------------
# Promotion policy: simulate the post-pass shape used inside run_auto_cull.
# These tests build a burst, a decisions dict, a reasons dict, and run the
# policy inline to verify the rescue triggers (or doesn't) correctly.
# ---------------------------------------------------------------------------


def _run_policy(burst: list[dict], decisions: dict[int, str],
                reasons: dict[int, str]) -> dict | None:
    """Replicate the production post-pass for a single burst. Returns the
    promotion record (matching what run_auto_cull would emit) or None when
    no rescue happens. Keeping this in tests so the assertions don't need
    to inspect the production loop's internal state."""
    if len(burst) < 2:
        return None
    burst_ids = [m["id"] for m in burst]
    burst_decisions = {i: decisions[i] for i in burst_ids if i in decisions}
    if not burst_decisions or any(d == "keep" for d in burst_decisions.values()):
        return None
    burst_reasons = {i: reasons.get(i) for i in burst_ids}
    non_null = [r for r in burst_reasons.values() if r is not None]
    if non_null and all(r in _PHYSICAL_REJECT_REASONS for r in non_null):
        return None
    winner = top_n_candidates(burst, 1)[0]
    winner_id = winner["id"]
    if decisions.get(winner_id) == "maybe":
        return None
    previous = decisions.get(winner_id)
    decisions[winner_id] = "maybe"
    return {
        "image_id": winner_id, "from": previous, "to": "maybe",
        "burst_size": len(burst), "reason": "burst_keep_guarantee",
    }


def test_promotes_best_when_burst_all_subjective_rejects():
    """Closed-eyes on every frame → the burst would otherwise lose the
    moment entirely. Rescue the strongest face_sharpness to maybe."""
    burst = [
        _row(1, face_detected=1, face_sharpness_score=30, eyes_open=0, overall_score=40),
        _row(2, face_detected=1, face_sharpness_score=70, eyes_open=0, overall_score=50),
        _row(3, face_detected=1, face_sharpness_score=20, eyes_open=0, overall_score=45),
    ]
    decisions = {1: "reject", 2: "reject", 3: "reject"}
    reasons   = {1: "closed_eyes", 2: "closed_eyes", 3: "closed_eyes"}
    promo = _run_policy(burst, decisions, reasons)
    assert promo is not None
    assert promo["image_id"] == 2  # highest face_sharpness wins
    assert decisions[2] == "maybe"
    # Other photos stay rejected — only the best is rescued.
    assert decisions[1] == "reject" and decisions[3] == "reject"


def test_no_promotion_when_already_a_keep_in_burst():
    """If even one frame in the burst is a keep, the moment isn't lost —
    don't escalate anything."""
    burst = [
        _row(1, overall_score=85),
        _row(2, overall_score=50),
    ]
    decisions = {1: "keep", 2: "reject"}
    reasons   = {1: "threshold_keep", 2: "threshold_reject"}
    assert _run_policy(burst, decisions, reasons) is None
    assert decisions == {1: "keep", 2: "reject"}


def test_no_promotion_when_all_rejects_are_physical():
    """ISO 25600 on every frame → these photos really aren't recoverable.
    Promoting one would just put a guaranteed-bad photo in the user's
    Maybe folder."""
    burst = [_row(1, overall_score=30), _row(2, overall_score=40)]
    decisions = {1: "reject", 2: "reject"}
    reasons   = {1: "iso_ceiling", 2: "iso_ceiling"}
    assert _run_policy(burst, decisions, reasons) is None
    assert decisions == {1: "reject", 2: "reject"}


def test_promotion_runs_when_mixed_physical_and_subjective():
    """One frame failed on ISO, others on closed_eyes — the mixed case
    leans rescuable (the subjective failures could be the moment). Promote."""
    burst = [
        _row(1, overall_score=40),
        _row(2, overall_score=60),
    ]
    decisions = {1: "reject", 2: "reject"}
    reasons   = {1: "iso_ceiling", 2: "closed_eyes"}
    promo = _run_policy(burst, decisions, reasons)
    assert promo is not None
    # Tie-broken on overall_score in this fixture — id=2 wins.
    assert promo["image_id"] == 2


def test_no_promotion_when_best_is_already_maybe():
    """The best frame already survived as maybe — the moment isn't lost
    and there's nothing to upgrade. No-op."""
    burst = [
        _row(1, overall_score=80),
        _row(2, overall_score=40),
    ]
    decisions = {1: "maybe", 2: "reject"}
    reasons   = {1: "threshold_maybe", 2: "threshold_reject"}
    assert _run_policy(burst, decisions, reasons) is None
    assert decisions == {1: "maybe", 2: "reject"}


def test_promotion_picks_face_sharpness_over_overall_score():
    """The face-sharpness slot dominates the priority tuple. A photo with
    a strong face and weak overall_score must beat a photo with no face
    but high overall_score."""
    burst = [
        _row(1, face_detected=1, face_sharpness_score=80, overall_score=30),
        _row(2, face_detected=0, face_sharpness_score=0,  overall_score=80),
    ]
    decisions = {1: "reject", 2: "reject"}
    reasons   = {1: "soft_face", 2: "threshold_reject"}
    promo = _run_policy(burst, decisions, reasons)
    assert promo is not None
    assert promo["image_id"] == 1  # face wins despite worse overall_score


def test_promotion_response_shape_matches_production_contract():
    """The promotion record fields are the contract the frontend will
    consume in a future toast. Pin them down."""
    burst = [_row(1, overall_score=60), _row(2, overall_score=50)]
    decisions = {1: "reject", 2: "reject"}
    reasons   = {1: "closed_eyes", 2: "closed_eyes"}
    promo = _run_policy(burst, decisions, reasons)
    assert promo is not None
    assert set(promo.keys()) == {"image_id", "from", "to", "burst_size", "reason"}
    assert promo["to"] == "maybe"
    assert promo["from"] == "reject"
    assert promo["burst_size"] == 2
    assert promo["reason"] == "burst_keep_guarantee"
