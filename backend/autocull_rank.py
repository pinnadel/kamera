"""AI burst ranking for auto-cull.

Auto-cull scores every photo independently and then runs two burst post-passes
that need one thing the per-photo scores can't give them: *which frame of this
group is the best one*. Until now that answer came from the score heuristic in
`backend.group_scoring` (face_sharpness → eyes_open → sharpness → IQA →
aesthetic → overall). GroupLoupe and the grid have always used a better answer
for the same question — the local vision LLM in `phase2_quality.burst_ranker`,
which sees all the frames in one context and compares them (peak smile vs. 80 ms
before, eyes fully open vs. mid-blink). This module is the seam that lets
auto-cull use it too.

Two things keep this affordable. A fresh rank costs ~60 s of Ollama time, so:

  * **Only consequential groups are ranked.** The winner only decides an outcome
    in two cases: the group has ≥2 Keeps (the keep-cap pass picks which survive)
    or 0 Keeps (the keep-guarantee pass promotes one to Maybe). A group with
    exactly one Keep has already been decided by the per-photo scores and an LLM
    call there changes nothing. Plus the same ≥3-member floor the prerank worker
    uses — on a pair the LLM rarely beats the score pick and still costs a full
    call.
  * **The cache is shared with the prerank worker.** `rank_burst` is keyed on the
    membership hash, so a group the user already opened in GroupLoupe (or that
    the background worker drained) is free here.

Reach is deliberately narrow: the LLM's rank-1 becomes the group's winner, and
nothing else changes. The keep-budget gates, the near-duplicate rep choice and
the demotion order all still run on scores. When the rank is unavailable — no
vision model, Ollama error, or the near-duplicate outcome where no per-photo
rank was meaningful — the group silently falls back to the score pick, and the
caller reports how many groups that happened to.
"""

from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)

# Mirror phase2_quality.prerank._MIN_GROUP_SIZE. Below this the LLM wouldn't
# meaningfully beat the score pick and still costs a full model call.
MIN_GROUP_SIZE = 3

# Rough wall-clock cost of one fresh rank, used only to tell the user how long
# a run will take before they start it. Measured ~55-60 s/group after the
# 2026-05-17 latency work (512 px previews, _MAX_MEMBERS 8, intra-burst dedup).
SECONDS_PER_RANK = 60


def consequential_bursts(
    bursts: list[list[dict]],
    decisions: dict[int, str],
    *,
    min_size: int = MIN_GROUP_SIZE,
) -> list[list[dict]]:
    """The bursts where an AI winner would actually change auto-cull's output.

    A burst qualifies when it has ≥ `min_size` members AND either:
      * ≥2 Keeps — `_cap_burst_keeps` will choose which of them survives, and
        the winner is the one photo guaranteed to stay Keep; or
      * 0 Keeps — the keep-guarantee floor pass will promote the winner to
        Maybe, so the winner is the moment the user gets to rescue.

    Exactly one Keep means the per-photo scores already settled it: neither
    post-pass fires, and a rank would be paid for and then ignored.
    """
    out: list[list[dict]] = []
    for burst in bursts:
        if len(burst) < min_size:
            continue
        keeps = sum(1 for m in burst if decisions.get(m["id"]) == "keep")
        if keeps >= 2 or keeps == 0:
            out.append(burst)
    return out


def _rank_one_winner(member_ids: list[int], conn, *, cached_only: bool) -> tuple[int | None, str]:
    """Rank one burst and return (winner_image_id, status).

    `winner_image_id` is None on every status except "ranked" — the caller then
    keeps the score-based pick. Never raises: a ranker failure is a fallback,
    not a failed auto-cull.
    """
    from phase2_quality.burst_ranker import rank_burst

    try:
        result = rank_burst(member_ids, conn, cached_only=cached_only)
    except Exception:
        logger.exception("autocull_rank: rank_burst raised for %d members", len(member_ids))
        return None, "error"

    status = result.get("status") or "error"
    if status != "ranked":
        return None, status

    top = next((r for r in result.get("rankings") or [] if r.get("rank") == 1), None)
    if top is None:
        return None, "error"
    winner = top.get("image_id")
    # Defensive: a 'ranked' row is always built from this group's own members,
    # but a stale cache row keyed on a hash collision would be worse than a
    # fallback. Membership is the contract the rest of the pass relies on.
    if winner not in set(member_ids):
        logger.warning("autocull_rank: rank-1 %r is not a member — falling back", winner)
        return None, "error"
    return int(winner), "ranked"


def resolve_cached(
    bursts: list[list[dict]],
    *,
    conn_factory: Callable | None = None,
) -> tuple[dict[int, int], list[list[dict]], dict]:
    """Split `bursts` into the ones already answered and the ones that will cost
    an LLM call. Returns (winners_so_far, bursts_needing_a_fresh_rank, stats).

    Nothing here calls the LLM. Three outcomes resolve for free: a cached
    ranking, a near-duplicate group (the SigLIP collapse leaves nothing to
    compare, and that is numpy over embeddings we already have), and an error.
    Only `not_cached` goes in the pending list.

    This exists so ONE definition of "groups that cost time" feeds both the
    preview's estimate and the run's progress bar. They used to count different
    things — the bar counted every contested group, the estimate counted only
    uncached ones — and a run that promised 50 groups then showed 13/72 is a
    progress bar nobody can trust.
    """
    winners: dict[int, int] = {}
    pending: list[list[dict]] = []
    stats = {"ranked": 0, "free": 0, "fallback_reasons": {}}

    if conn_factory is None:
        from backend.database import get_db as conn_factory  # type: ignore[assignment]

    for burst in bursts:
        member_ids = [m["id"] for m in burst]
        with conn_factory() as conn:
            winner, status = _rank_one_winner(member_ids, conn, cached_only=True)
        if status == "not_cached":
            pending.append(burst)
            continue
        stats["free"] += 1
        if winner is not None:
            stats["ranked"] += 1
            for mid in member_ids:
                winners[mid] = winner
        else:
            stats["fallback_reasons"][status] = stats["fallback_reasons"].get(status, 0) + 1
    return winners, pending, stats


def resolve_winners(
    bursts: list[list[dict]],
    *,
    cached_only: bool = False,
    conn_factory: Callable | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[dict[int, int], dict]:
    """Rank `bursts` and return (winner_by_member_id, stats).

    `winner_by_member_id` maps EVERY member id of a successfully-ranked burst to
    that burst's winner id. `_group_undecided_into_bursts` partitions the rows,
    so a photo belongs to exactly one burst and the map is unambiguous — the
    burst passes look up `winners.get(any_member_id)`.

    `conn_factory` yields a short-lived DB connection per burst (the same
    one-connection-per-item discipline the prerank worker uses, so a ~60 s LLM
    call never holds a transaction open). Defaults to `backend.database.get_db`.

    `on_progress(done, total)` fires after each burst. `should_stop()` is polled
    between bursts — when it returns True the function returns what it has so
    far with `stats["stopped"] = True`; the caller decides what that means.
    """
    if conn_factory is None:
        from backend.database import get_db as conn_factory  # type: ignore[assignment]

    winners: dict[int, int] = {}
    stats = {
        "groups": len(bursts),
        "ranked": 0,
        "fell_back": 0,
        "fallback_reasons": {},
        "stopped": False,
    }

    for index, burst in enumerate(bursts):
        if should_stop is not None and should_stop():
            stats["stopped"] = True
            break

        member_ids = [m["id"] for m in burst]
        with conn_factory() as conn:
            winner, status = _rank_one_winner(member_ids, conn, cached_only=cached_only)

        if winner is not None:
            stats["ranked"] += 1
            for mid in member_ids:
                winners[mid] = winner
        else:
            stats["fell_back"] += 1
            stats["fallback_reasons"][status] = stats["fallback_reasons"].get(status, 0) + 1

        if on_progress is not None:
            on_progress(index + 1, len(bursts))

    return winners, stats


def promote_winner(ordered: list[dict], winner_id: int | None) -> list[dict]:
    """Move `winner_id` to the front of a priority-ordered member list.

    The burst passes treat index 0 as "the best photo of this group", so this is
    the whole of the AI rank's reach: it changes who index 0 is, and leaves the
    relative order of everyone else alone. A winner that isn't in the list (it
    lost its Keep to another pass, or isn't among this sub-set) is a no-op — the
    score order stands.
    """
    if winner_id is None:
        return ordered
    for i, member in enumerate(ordered):
        if member["id"] == winner_id:
            if i == 0:
                return ordered
            return [member] + ordered[:i] + ordered[i + 1:]
    return ordered
